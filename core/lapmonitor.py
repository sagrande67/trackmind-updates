"""
LapMonitor - Client Bluetooth LE per ricevitore di cronometraggio RC.

Wrapper stdlib-friendly attorno al modulo bleak (import opzionale):
- _HAS_BLEAK dice se la libreria e' disponibile
- scan_devices_async() scansiona i dispositivi BLE filtrando per nome
- LapMonitorClient gira in un thread dedicato con il suo event loop
  asyncio, cosi' il mainloop tkinter non viene toccato. Ogni passaggio
  trasponder viene consegnato al main thread tramite root.after(0, ...),
  che e' l'unico modo sicuro di aggiornare widget Tk da thread non-main.

Protocollo BLE (reverse-engineering dal modulo laptimer_REAL.py fornito
dal LapMonitor):
  - Servizio: Nordic UART Service
  - RX (notify, device->host): 6e400003-b5a3-f393-e0a9-e50e24dcca9e
  - TX (write, host->device):  6e400002-b5a3-f393-e0a9-e50e24dcca9e
  - Pacchetto START (host->dev, 19 byte): 0x23 0x53 0x01 00 00 <uuid10>
    <duration_hi> <duration_lo> <extra_hi> <extra_lo>
  - Pacchetto LAP (dev->host, 13 byte): 0x23 0x6C <sid> <...> <cnt>
    <...> <pilot> <...> 0xA5   -> estraiamo pilot (byte 7) e cnt (byte 5)

Filtri lato client:
  - dump-skip (default 3.0s): i primi pacchetti dopo START sono lo
    storico del dispositivo, li scartiamo (potrebbero riferirsi a
    sessioni vecchie)
  - debounce (default 2.0s): se lo stesso trasponder passa due volte
    entro N secondi, scartiamo il secondo (raffica/falso positivo)
"""

import asyncio
import threading
import os
from datetime import datetime

# Import opzionale di bleak: se manca il modulo continua a girare ma
# tutte le chiamate ritornano errore "bleak non disponibile".
try:
    from bleak import BleakClient, BleakScanner
    _HAS_BLEAK = True
except Exception:
    _HAS_BLEAK = False
    BleakClient = None
    BleakScanner = None


# UUID del Nordic UART Service (lo stesso usato da Adafruit/nRF Connect)
_RX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # device -> host (notify)
_TX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # host   -> device (write)


def _build_start_packet(duration=64800, extra=0):
    """Costruisce il pacchetto START da inviare al LapMonitor.
    duration = durata sessione in secondi (default 18 ore = sempre)."""
    uuid = os.urandom(10)
    pkt = bytearray(19)
    pkt[0] = 0x23
    pkt[1] = 0x53
    pkt[2] = 0x01
    pkt[3] = 0
    pkt[4] = 0
    pkt[5:15] = uuid
    pkt[15] = (duration >> 8) & 0xFF
    pkt[16] = duration & 0xFF
    pkt[17] = (extra >> 8) & 0xFF
    pkt[18] = extra & 0xFF
    return bytes(pkt)


def _parse_lap_packet(data):
    """Estrae pilot number e lap counter dal pacchetto LAP.
    Ritorna None se il pacchetto non e' valido."""
    if len(data) != 13 or data[0] != 0x23 or data[1] != 0x6C or data[-1] != 0xA5:
        return None
    return {"sid": data[3], "cnt": data[5], "pilot": data[7]}


# ============================================================
#  SCANNER
# ============================================================
def scan_devices_sync(prefix="LapM", timeout=5.0):
    """Scansiona i dispositivi BLE nelle vicinanze e ritorna una
    lista di tuple (name, address) filtrate per prefisso del nome.

    Esegue la scan asincrona in un event loop temporaneo. Chiamabile
    sia dal main thread che da un worker, ma NON deve essere chiamato
    dal mainloop tkinter (bloccante fino a timeout secondi).

    Ritorna [] se bleak non e' disponibile o se lo scan fallisce.
    """
    if not _HAS_BLEAK:
        return []

    async def _scan():
        devs = await BleakScanner.discover(timeout=timeout)
        found = []
        for d in devs:
            name = getattr(d, "name", None) or ""
            if name.lower().startswith(prefix.lower()):
                found.append((name, d.address))
        return found

    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_scan())
        finally:
            loop.close()
    except Exception:
        return []


def scan_devices_async(prefix, timeout, on_done, tk_root):
    """Variante non bloccante: lancia lo scan in un thread daemon e
    chiama on_done(lista) nel main thread tkinter via after().

    on_done riceve la lista [(name, address), ...] oppure None se
    bleak non e' disponibile."""
    if not _HAS_BLEAK:
        if tk_root is not None:
            tk_root.after(0, lambda: on_done(None))
        else:
            on_done(None)
        return

    def _worker():
        try:
            result = scan_devices_sync(prefix=prefix, timeout=timeout)
        except Exception:
            result = []
        # Consegna al main thread tkinter
        if tk_root is not None:
            try:
                tk_root.after(0, lambda: on_done(result))
            except Exception:
                pass
        else:
            on_done(result)

    threading.Thread(target=_worker, daemon=True).start()


# ============================================================
#  CLIENT (thread dedicato con event loop asyncio proprio)
# ============================================================
class LapMonitorClient:
    """Client BLE che gira in un thread separato.

    Uso:
        cli = LapMonitorClient(address, tk_root=root,
                               on_lap=cb_lap, on_status=cb_status,
                               on_connected=cb_conn)
        cli.start()
        ...
        cli.stop()

    Callback (tutti chiamati nel main thread tkinter via after):
      - on_lap(pilot_num, lap_count_device, delta_sec, timestamp, raw_hex)
        delta_sec = None per il primissimo giro di ogni trasponder
      - on_status(msg, livello)  livello in {"info","ok","errore","avviso"}
      - on_connected(bool)       True quando connesso, False allo stop
    """

    def __init__(self, address, tk_root=None,
                 on_lap=None, on_status=None, on_connected=None,
                 duration=64800, dump_skip=3.0, debounce=2.0):
        self.address = address
        self.tk_root = tk_root
        self.on_lap = on_lap
        self.on_status = on_status
        self.on_connected = on_connected
        self.duration = duration
        self.dump_skip = float(dump_skip)
        self.debounce = float(debounce)

        self._thread = None
        self._loop = None
        self._stop_evt = None  # asyncio.Event creato nel loop del thread
        self._started = False
        # stato per debounce per-pilota
        self._pilot_state = {}   # pilot_num -> {"count": int, "last_t": datetime}
        self._start_time = None
        self._bursts = 0         # contatore pacchetti scartati per debounce
        self._history = 0        # contatore pacchetti scartati per dump-skip

    # --- helpers per dispatch callback nel main thread ---
    def _emit(self, fn, *args):
        """Esegue fn(*args) nel main thread tkinter se disponibile,
        altrimenti diretto (utile per test headless)."""
        if fn is None:
            return
        if self.tk_root is not None:
            try:
                self.tk_root.after(0, lambda: fn(*args))
            except Exception:
                pass
        else:
            try:
                fn(*args)
            except Exception:
                pass

    def _status(self, msg, livello="info"):
        self._emit(self.on_status, msg, livello)

    # --- API pubblica ---
    def is_running(self):
        return self._started and self._thread is not None and self._thread.is_alive()

    def start(self):
        """Avvia il thread BLE. Ritorna False se bleak non disponibile."""
        if not _HAS_BLEAK:
            self._status("bleak non installato - impossibile connettere", "errore")
            return False
        if self._started:
            return True
        self._started = True
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """Richiede lo stop al thread BLE. Non blocca: il thread si
        chiude da solo dopo aver disconnesso. Idempotente."""
        if not self._started:
            return
        self._started = False
        # Scheduliamo il set() dell'evento stop nel loop del thread
        if self._loop is not None and self._stop_evt is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop_evt.set)
            except Exception:
                pass

    # --- implementazione nel thread ---
    def _run_thread(self):
        """Entry point del thread BLE. Crea il proprio event loop
        asyncio e esegue _run_async fino a stop."""
        self._loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._run_async())
        except Exception as e:
            self._status("errore BLE: %s" % e, "errore")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None
            self._emit(self.on_connected, False)

    async def _run_async(self):
        """Loop principale BLE: connetti, invia START, ascolta notify,
        esci quando stop_evt viene settato."""
        self._stop_evt = asyncio.Event()
        self._status("connessione a %s..." % self.address, "info")

        # Pulizia preventiva BlueZ: un BleakClient con timeout molto
        # breve che chiude subito, per forzare la liberazione di
        # eventuali connessioni "in progress" lasciate da sessioni
        # precedenti. Best-effort: se fallisce lo ignoriamo.
        try:
            try:
                cleanup = BleakClient(self.address, timeout=1.0)
                await cleanup.disconnect()
            except Exception:
                pass
            # Breve pausa per dare tempo a BlueZ di completare la pulizia
            await asyncio.sleep(0.5)
        except Exception:
            pass

        try:
            async with BleakClient(self.address) as client:
                if not client.is_connected:
                    self._status("connessione fallita", "errore")
                    return

                self._emit(self.on_connected, True)
                self._status("connesso, invio START", "ok")

                # Callback per ogni notify BLE
                def _handle_notify(_char, data):
                    try:
                        self._process_packet(bytes(data))
                    except Exception as e:
                        self._status("parse err: %s" % e, "errore")

                await client.start_notify(_RX_UUID, _handle_notify)

                # Invia il pacchetto START
                start_pkt = _build_start_packet(self.duration)
                await client.write_gatt_char(_TX_UUID, start_pkt, response=False)
                self._start_time = datetime.now()
                self._status("in ascolto...", "ok")

                # Aspetta stop request, controllando anche la connessione
                while not self._stop_evt.is_set() and client.is_connected:
                    try:
                        await asyncio.wait_for(self._stop_evt.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass

                # Pulizia: prova a fermare le notify (best-effort)
                try:
                    await client.stop_notify(_RX_UUID)
                except Exception:
                    pass
                self._status("disconnessione", "info")
        except Exception as e:
            self._status("errore connessione: %s" % e, "errore")

    def _process_packet(self, data):
        """Parse + filtri (dump-skip + debounce) + dispatch on_lap."""
        now = datetime.now()
        p = _parse_lap_packet(data)
        if not p:
            # Pacchetto non-lap (info/heartbeat): ignorato silenziosamente
            return

        # Dump-skip: i primi N secondi scartiamo lo storico
        if self._start_time is not None:
            elapsed = (now - self._start_time).total_seconds()
            if elapsed < self.dump_skip:
                self._history += 1
                return

        pid = p["pilot"]
        st = self._pilot_state.get(pid)
        delta = (now - st["last_t"]).total_seconds() if st else None

        # Debounce per-pilota
        if delta is not None and delta < self.debounce:
            self._bursts += 1
            return

        # Nuovo giro valido
        my_n = 1 if st is None else st["count"] + 1
        self._pilot_state[pid] = {"count": my_n, "last_t": now}

        # Dispatch al main thread
        self._emit(self.on_lap, pid, p["cnt"], delta, now, data.hex(" "))

    # --- utilita' diagnostica ---
    def stats(self):
        """Restituisce un dict con contatori per diagnostica."""
        return {
            "bursts_filtered": self._bursts,
            "history_skipped": self._history,
            "pilots_seen": len(self._pilot_state),
        }
