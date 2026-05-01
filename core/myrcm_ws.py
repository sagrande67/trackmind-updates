"""
TrackMind - MyRCM Live Timing WebSocket Client
================================================
Client WebSocket stdlib-only per ricevere i tempi LIVE da MyRCM.

MyRCM trasmette il timing via WebSocket sull'endpoint
`wss://www.myrcm.ch/myrcm/websocket`. Dopo l'handshake il client invia
un messaggio di subscribe con l'event_id e da li' in poi il server
manda messaggi JSON ad ogni cambio di stato (giro completato, cambio
batteria, fine sessione, ecc.).

Formato messaggio dati:
    {
      "EVENT": {
        "CONFIG": {RACEMODE, STARTTYPE, NROFBESTLAPS, ...},
        "METADATA": {NAME, SECTION, GROUP, GROUPKEY, RACETIME,
                     CURRENTTIME, REMAININGTIME, RACESTATE,
                     PERCENTAGE, ...},
        "DATA": [
            {INDEX, PILOT, PILOTNUMBER, TRANSPONDER, COUNTRY,
             LAPS, LAPTIME, BESTTIME, MEDIUMTIME, ABSOLUTTIME,
             DELAYTIMEFIRST, DELAYTIMEPREVIOUS, FORECAST,
             PITSTOPS, ...},
            ...
        ],
        "TIMESTAMP": ..., "KEY": event_id
      }
    }

Tutto su libreria standard (socket + ssl + struct + json + threading).
Niente dipendenze esterne (rispetta vincolo TrackMind).

Uso:
    from core.myrcm_ws import MyRcmWsClient
    cli = MyRcmWsClient(
        event_id="96297",
        on_event=lambda md, dt: print("ricevuto", md.get("GROUP")),
        on_status=lambda s: print("stato:", s),
    )
    cli.start()  # avvia thread, connette
    ...
    cli.stop()   # ferma e chiude
"""

import socket
import ssl
import os
import base64
import struct
import json
import threading
import time


_HOST = "www.myrcm.ch"
_PORT = 443
_PATH = "/myrcm/websocket"
_CONN_TIMEOUT = 10.0
_RECV_TIMEOUT = 30.0  # se nessun messaggio per 30s si riconnette
_RECONNECT_DELAY = 5.0


# =====================================================================
#  WebSocket low-level (RFC 6455) - solo lato CLIENT
# =====================================================================
def _ws_handshake(host, port, path, timeout=10):
    """Apre socket TLS e fa handshake WebSocket. Ritorna ssl-socket
    pronto a tx/rx frame, o None se l'handshake fallisce."""
    raw = socket.create_connection((host, port), timeout=timeout)
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        "GET %s HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Origin: https://%s\r\n"
        "User-Agent: TrackMind/1.0 (MyRCM Live)\r\n"
        "\r\n"
    ) % (path, host, key, host)
    sock.sendall(req.encode())
    # Leggi handshake fino a \r\n\r\n
    resp = b""
    deadline = time.time() + timeout
    while b"\r\n\r\n" not in resp:
        if time.time() > deadline:
            try: sock.close()
            except Exception: pass
            return None
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, ssl.SSLError):
            try: sock.close()
            except Exception: pass
            return None
        if not chunk:
            break
        resp += chunk
    first = resp.split(b"\r\n", 1)[0] if resp else b""
    if b"101" not in first:
        try: sock.close()
        except Exception: pass
        return None
    return sock


def _ws_send_text(sock, text):
    """Invia un frame TEXT (opcode 0x1) con masking client->server."""
    payload = text.encode("utf-8")
    pl = len(payload)
    mask = os.urandom(4)
    header = bytes([0x81])  # FIN + TEXT
    if pl < 126:
        header += bytes([0x80 | pl])
    elif pl < 65536:
        header += bytes([0x80 | 126]) + struct.pack(">H", pl)
    else:
        header += bytes([0x80 | 127]) + struct.pack(">Q", pl)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + masked)


def _ws_send_pong(sock, payload=b""):
    """Risponde a un PING server con un PONG (opcode 0xA)."""
    pl = len(payload)
    mask = os.urandom(4)
    header = bytes([0x8A])  # FIN + PONG
    if pl < 126:
        header += bytes([0x80 | pl])
    else:
        header += bytes([0x80 | 126]) + struct.pack(">H", pl)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    try:
        sock.sendall(header + masked)
    except Exception:
        pass


def _ws_recv_frame(sock, timeout=30):
    """Riceve un frame WebSocket. Ritorna (opcode, bytes) o None su
    timeout / errore. Gestisce frame estesi fino a 2^63 byte."""
    sock.settimeout(timeout)
    try:
        h = _recv_exact(sock, 2)
        if h is None:
            return None
        b1, b2 = h[0], h[1]
        opcode = b1 & 0x0F
        masked = (b2 & 0x80) != 0
        plen = b2 & 0x7F
        if plen == 126:
            ext = _recv_exact(sock, 2)
            if ext is None: return None
            plen = struct.unpack(">H", ext)[0]
        elif plen == 127:
            ext = _recv_exact(sock, 8)
            if ext is None: return None
            plen = struct.unpack(">Q", ext)[0]
        mask_key = None
        if masked:
            mask_key = _recv_exact(sock, 4)
            if mask_key is None: return None
        data = _recv_exact(sock, plen) if plen > 0 else b""
        if data is None:
            return None
        if mask_key is not None:
            data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
        return opcode, data
    except (socket.timeout, ssl.SSLError, OSError):
        return None


def _recv_exact(sock, n):
    """Riceve esattamente n byte. None se la connessione si chiude."""
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (socket.timeout, ssl.SSLError, OSError):
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


# =====================================================================
#  Client di alto livello: thread + callback + reconnect
# =====================================================================
class MyRcmWsClient(object):
    """Client WebSocket persistente per MyRCM live timing.

    Gira in un thread daemon. Si riconnette automaticamente se la
    connessione cade. Smista i messaggi ricevuti ai callback registrati
    nel thread main UI con root.after se passato in `tk_root`, oppure
    li chiama dal thread WS (caso uso headless / test)."""

    def __init__(self, event_id, on_event=None, on_status=None,
                 on_error=None, tk_root=None, language="-",
                 fmt="JSON"):
        self.event_id = str(event_id)
        self._on_event = on_event
        self._on_status = on_status
        self._on_error = on_error
        self._tk_root = tk_root
        self._language = language
        self._fmt = fmt
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self._connected = False
        self._last_event_ts = 0  # timestamp ultimo EVENT ricevuto
        self._n_events = 0
        self._lock = threading.Lock()

    # ---- API pubblica ----
    def start(self):
        """Avvia il thread di ascolto WebSocket. Idempotente."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="MyRcmWsClient-%s" % self.event_id,
            daemon=True)
        self._thread.start()

    def stop(self):
        """Ferma il client e chiude la connessione. Bloccante max 3s."""
        self._stop.set()
        try:
            if self._sock is not None:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._connected = False

    def is_connected(self):
        return self._connected

    def stats(self):
        """Statistiche di sessione: messaggi ricevuti + secondi
        dall'ultimo. Utile per indicatore "live attivo" nella UI."""
        return {
            "connected": self._connected,
            "n_events": self._n_events,
            "secs_since_last": (time.time() - self._last_event_ts
                                if self._last_event_ts > 0 else None),
        }

    # ---- Thread principale ----
    def _run_loop(self):
        backoff = _RECONNECT_DELAY
        while not self._stop.is_set():
            try:
                self._notify_status("Connessione a MyRCM live...")
                sock = _ws_handshake(_HOST, _PORT, _PATH,
                                      timeout=_CONN_TIMEOUT)
                if sock is None:
                    self._notify_error("Handshake WebSocket fallito")
                    if self._stop.wait(backoff):
                        return
                    backoff = min(60.0, backoff * 1.5)
                    continue
                self._sock = sock
                self._connected = True
                # Subscribe
                init = json.dumps({
                    "EventKey": self.event_id,
                    "Language": self._language,
                    "Format": self._fmt,
                })
                try:
                    _ws_send_text(sock, init)
                except Exception as e:
                    self._notify_error("Subscribe fallito: %s" % e)
                    self._safe_close()
                    if self._stop.wait(backoff):
                        return
                    continue
                self._notify_status("Connesso a evento %s, in ascolto..."
                                     % self.event_id)
                backoff = _RECONNECT_DELAY  # reset al successo
                # Loop ricezione
                while not self._stop.is_set():
                    frame = _ws_recv_frame(sock, timeout=_RECV_TIMEOUT)
                    if frame is None:
                        # Timeout o connessione persa
                        if self._stop.is_set():
                            break
                        self._notify_status(
                            "Connessione persa, riconnessione...")
                        break
                    op, data = frame
                    if op == 0x8:  # CLOSE
                        break
                    if op == 0x9:  # PING -> rispondi con PONG
                        _ws_send_pong(sock, data)
                        continue
                    if op == 0xA:  # PONG dal server, ignora
                        continue
                    if op != 0x1:  # Solo TEXT ci interessa
                        continue
                    try:
                        text = data.decode("utf-8")
                    except Exception:
                        continue
                    self._handle_message(text)
                self._safe_close()
            except Exception as e:
                self._notify_error("Errore loop WS: %s" % e)
                self._safe_close()
            if not self._stop.is_set():
                if self._stop.wait(backoff):
                    return
                backoff = min(60.0, backoff * 1.5)

    def _safe_close(self):
        try:
            if self._sock is not None:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._connected = False

    def _handle_message(self, text):
        """Parse JSON e dispatch al callback on_event."""
        try:
            obj = json.loads(text)
        except Exception:
            return
        # Welcome / control message del framework jWebSocket
        if "EVENT" not in obj:
            return
        ev = obj["EVENT"]
        meta = ev.get("METADATA") or {}
        data = ev.get("DATA") or []
        self._last_event_ts = time.time()
        self._n_events += 1
        self._dispatch(self._on_event, meta, data)

    def _dispatch(self, cb, *args):
        """Smista il callback al thread Tk se disponibile."""
        if cb is None:
            return
        if self._tk_root is not None:
            try:
                self._tk_root.after(0, lambda: cb(*args))
                return
            except Exception:
                pass
        try:
            cb(*args)
        except Exception:
            pass

    def _notify_status(self, msg):
        self._dispatch(self._on_status, msg)

    def _notify_error(self, msg):
        self._dispatch(self._on_error, msg)
