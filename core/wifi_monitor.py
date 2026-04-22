# -*- coding: utf-8 -*-
"""
Wi-Fi Auto-Reconnect - monitor cross-platform della connessione Wi-Fi.

Problema risolto:
    Quando un hotspot del telefono (o un Wi-Fi qualsiasi) va giu' e poi torna,
    Windows spesso NON si ricollega da solo e chiede di nuovo la password,
    anche se il profilo e' stato salvato con "connetti automaticamente".
    Il risultato in pista e' che TrackMind perde la connessione di rete (e
    quindi SpeedHive, invio email, analisi IA, aggiornamenti) e l'utente
    non se ne accorge finche' non prova a fare qualcosa.

Cosa fa questo modulo (v05.05.30 - elenco reti):
    - Controlla ciclicamente se la macchina e' collegata a un Wi-Fi.
    - Se cade, scansiona le reti Wi-Fi a portata e confronta gli SSID
      visibili con quelli presenti in dati/wifi.json (tabella wifi).
      Per il primo SSID conosciuto in range tenta la connessione con
      la password memorizzata in elenco (crea il profilo al volo).
    - Logga ogni caduta e ogni riconnessione in dati/wifi_log.txt.

Differenza con le versioni precedenti:
    Prima c'era UN solo SSID salvato in CONFI (`wifi_auto_ssid`).
    Adesso si appoggia a un elenco di reti con password: cosi' il
    software gira su piu' installazioni senza SSID personali hardcoded
    nel default config, e si aggancia in automatico a qualsiasi rete
    gia' vista in passato.

Dipendenze:
    ZERO - solo stdlib Python + comandi di sistema gia' presenti
    (netsh, nmcli). Nessuna libreria esterna.
"""

import os
import sys
import json
import time
import threading
import subprocess
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────
#  LETTURA STATO
# ─────────────────────────────────────────────────────────────────────

def _flag_no_console():
    """Evita che spawnare netsh/nmcli apra una finestra cmd nera su Windows."""
    if sys.platform == "win32":
        return 0x08000000  # CREATE_NO_WINDOW
    return 0


def stato_wifi():
    """Ritorna (connesso, ssid_corrente).

    Funziona su:
        - Windows (netsh wlan show interfaces)
        - Linux / uConsole (nmcli)
    In caso di errore o comando mancante ritorna (False, "").
    """
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, timeout=5,
                creationflags=_flag_no_console(),
            )
            ssid = ""
            stato = ""
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("SSID") and "BSSID" not in line:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        ssid = parts[1].strip()
                elif line.startswith(("Stato", "State")):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        stato = parts[1].strip().lower()
            connesso = ("conness" in stato or "connected" in stato) and bool(ssid)
            return (connesso, ssid)
        else:
            r = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                parti = line.split(":")
                if len(parti) >= 2 and "wireless" in parti[1].lower():
                    return (True, parti[0])
            return (False, "")
    except Exception:
        return (False, "")


def profili_salvati():
    """Ritorna la lista degli SSID/profili Wi-Fi gia' memorizzati nel sistema.

    Utile per diagnostica. Su Windows legge 'netsh wlan show profiles',
    su Linux legge 'nmcli connection show' filtrando i wireless.
    """
    nomi = []
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["netsh", "wlan", "show", "profiles"],
                capture_output=True, text=True, timeout=5,
                creationflags=_flag_no_console(),
            )
            for line in r.stdout.splitlines():
                # Italiano: "Profilo utente    : NomeRete"
                # Inglese:  "All User Profile : NomeRete"
                if ":" in line and ("profilo" in line.lower() or "profile" in line.lower()):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        n = parts[1].strip()
                        if n and n not in nomi:
                            nomi.append(n)
        else:
            r = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                import re as _re
                parti = _re.split(r'(?<!\\):', line)
                parti = [p.replace('\\:', ':') for p in parti]
                if len(parti) >= 2 and "wireless" in parti[1].lower():
                    if parti[0] and parti[0] not in nomi:
                        nomi.append(parti[0])
    except Exception:
        pass
    return nomi


def reti_visibili():
    """Scansiona le reti Wi-Fi a portata e ritorna la lista degli SSID visibili.

    Su Windows: `netsh wlan show networks` (non serve ri-scansione attiva,
    la card aggiorna la cache da sola).
    Su Linux: `nmcli -t -f SSID dev wifi list` (nmcli fa rescan se serve).
    Ritorna lista di stringhe SSID univoche. Se il comando fallisce, [].
    """
    ssid_set = []
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["netsh", "wlan", "show", "networks"],
                capture_output=True, text=True, timeout=10,
                creationflags=_flag_no_console(),
            )
            # Output tipico (IT):
            #   SSID 1 : NomeRete
            # Output tipico (EN):
            #   SSID 1 : NetworkName
            for line in r.stdout.splitlines():
                line = line.strip()
                low = line.lower()
                if low.startswith("ssid ") and "bssid" not in low and ":" in line:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        nome = parts[1].strip()
                        if nome and nome not in ssid_set:
                            ssid_set.append(nome)
        else:
            # Linux: prova rescan, poi lista
            try:
                subprocess.run(
                    ["nmcli", "device", "wifi", "rescan"],
                    capture_output=True, text=True, timeout=10,
                )
            except Exception:
                pass
            r = subprocess.run(
                ["nmcli", "-t", "-f", "SSID", "device", "wifi", "list"],
                capture_output=True, text=True, timeout=10,
            )
            import re as _re
            for line in r.stdout.splitlines():
                # nmcli escapes ':' in SSID as '\:'
                nome = line.replace('\\:', ':').strip()
                if nome and nome not in ssid_set:
                    ssid_set.append(nome)
    except Exception:
        pass
    return ssid_set


# ─────────────────────────────────────────────────────────────────────
#  RICONNESSIONE
# ─────────────────────────────────────────────────────────────────────

def riconnetti(ssid):
    """Prova a riconnettersi al profilo GIA' salvato per lo SSID indicato.

    Fast path: se il SO ha gia' il profilo con password memorizzata,
    questa chiamata riagisce senza dover ricreare nulla.
    Ritorna True se il comando ha risposto "ok".
    """
    ssid = (ssid or "").strip()
    if not ssid:
        return False
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["netsh", "wlan", "connect", "name=%s" % ssid, "ssid=%s" % ssid],
                capture_output=True, text=True, timeout=10,
                creationflags=_flag_no_console(),
            )
            out = (r.stdout or "") + (r.stderr or "")
            out_l = out.lower()
            if r.returncode == 0 and ("complet" in out_l or "successfully" in out_l):
                return True
            return False
        else:
            r = subprocess.run(
                ["nmcli", "connection", "up", ssid],
                capture_output=True, text=True, timeout=15,
            )
            return r.returncode == 0
    except Exception:
        return False


def riconnetti_con_password(ssid, password):
    """Crea (o ricrea) il profilo Wi-Fi per `ssid` con la password fornita
    e si connette. Usato come fallback quando `riconnetti(ssid)` fallisce
    perche' il profilo del SO non esiste o ha una password errata.

    Ritorna True se il comando di connessione e' andato a buon fine.
    NB: non garantisce che la rete sia effettivamente raggiungibile,
        va ricontrollato con stato_wifi() qualche secondo dopo.
    """
    ssid = (ssid or "").strip()
    password = password or ""
    if not ssid:
        return False
    try:
        if sys.platform == "win32":
            # Profilo XML temporaneo (WPA2PSK se c'e' password, open altrimenti)
            if password:
                xml = ('<?xml version="1.0"?>'
                       '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
                       '<name>%s</name><SSIDConfig><SSID><name>%s</name></SSID></SSIDConfig>'
                       '<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>'
                       '<MSM><security><authEncryption>'
                       '<authentication>WPA2PSK</authentication><encryption>AES</encryption>'
                       '<useOneX>false</useOneX></authEncryption>'
                       '<sharedKey><keyType>passPhrase</keyType><protected>false</protected>'
                       '<keyMaterial>%s</keyMaterial></sharedKey>'
                       '</security></MSM></WLANProfile>' % (ssid, ssid, password))
            else:
                xml = ('<?xml version="1.0"?>'
                       '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
                       '<name>%s</name><SSIDConfig><SSID><name>%s</name></SSID></SSIDConfig>'
                       '<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>'
                       '<MSM><security><authEncryption>'
                       '<authentication>open</authentication><encryption>none</encryption>'
                       '<useOneX>false</useOneX></authEncryption>'
                       '</security></MSM></WLANProfile>' % (ssid, ssid))
            tmp = os.path.join(os.environ.get("TEMP", "."), "_tm_wifi_profile.xml")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(xml)
            try:
                subprocess.run(
                    ["netsh", "wlan", "add", "profile", "filename=%s" % tmp],
                    capture_output=True, text=True, timeout=10,
                    creationflags=_flag_no_console(),
                )
            finally:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            r = subprocess.run(
                ["netsh", "wlan", "connect", "name=%s" % ssid, "ssid=%s" % ssid],
                capture_output=True, text=True, timeout=15,
                creationflags=_flag_no_console(),
            )
            return r.returncode == 0
        else:
            # Linux: cancella eventuale profilo vecchio per evitare che
            # nmcli riusi credenziali stale (problema noto con SSID generici
            # tipo iPhone/AndroidAP), poi riconnetti pulito.
            try:
                subprocess.run(
                    ["nmcli", "connection", "delete", "id", ssid],
                    capture_output=True, text=True, timeout=10,
                )
            except Exception:
                pass
            cmd = ["nmcli", "device", "wifi", "connect", ssid]
            if password:
                cmd += ["password", password]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and password:
                # Promuovi a system-owned per evitare popup keyring al boot
                try:
                    subprocess.run(
                        ["nmcli", "connection", "modify", ssid,
                         "802-11-wireless-security.psk-flags", "0"],
                        capture_output=True, text=True, timeout=5)
                except Exception:
                    pass
            return r.returncode == 0
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────
#  LETTURA ELENCO Wi-Fi (dati/wifi.json)
# ─────────────────────────────────────────────────────────────────────

def carica_elenco_wifi(path_json):
    """Legge l'elenco Wi-Fi conosciute dalla tabella wifi (JSON TrackMind).

    Ritorna una lista di dict [{"ssid": ..., "password": ..., "note": ...}, ...].
    I record con SSID vuoto vengono ignorati. Se il file manca o e' corrotto,
    ritorna lista vuota (best-effort: nessuna eccezione verso il chiamante).
    """
    out = []
    try:
        if not path_json or not os.path.exists(path_json):
            return out
        with open(path_json, "r", encoding="utf-8") as f:
            contenuto = json.load(f)
        for rec in contenuto.get("records", []) or []:
            # Il motore TrackMind conserva le chiavi con la case originale
            # del .def (SSID, Password, Note); normalizziamo.
            ssid = ""
            pwd = ""
            note = ""
            for k, v in rec.items():
                kl = k.lower()
                if kl == "ssid":
                    ssid = str(v or "").strip()
                elif kl == "password":
                    pwd = str(v or "")
                elif kl == "note":
                    note = str(v or "")
            if ssid:
                out.append({"ssid": ssid, "password": pwd, "note": note})
    except Exception:
        pass
    return out


# ─────────────────────────────────────────────────────────────────────
#  THREAD DI MONITORAGGIO
# ─────────────────────────────────────────────────────────────────────

class AutoRiconnettore(object):
    """Thread daemon che monitora il Wi-Fi e tenta la riconnessione.

    Logica (v05.05.30):
        - Ogni `intervallo_sec` controlla lo stato.
        - Se e' connesso, non fa nulla.
        - Se e' offline:
            1. Scansiona le reti Wi-Fi visibili.
            2. Legge l'elenco delle Wi-Fi conosciute da `wifi_json_path`.
            3. Per il primo SSID conosciuto che risulta a portata:
               a) prova `riconnetti(ssid)` (profilo SO gia' salvato)
               b) se fallisce, prova `riconnetti_con_password(ssid, pwd)`
                  ricreando il profilo al volo con la password in elenco.
            4. Cooldown anti-spam tra tentativi consecutivi.

    Parametri:
        wifi_json_path: path al file dati/wifi.json (tabella wifi TrackMind).
        intervallo_sec: ogni quanti secondi fa il check (default 15).
        log_path: file di testo dove registrare gli eventi. None = no log.
        cooldown_sec: tempo minimo tra due tentativi consecutivi (default 20).
        callback_stato: funzione opzionale (connesso, ssid, evento).
            evento: None/"caduta"/"ripristino"/"tentativo".

    Metodi:
        start(): avvia il thread.
        stop(): ferma il thread al prossimo ciclo.
    """

    def __init__(self, wifi_json_path="", intervallo_sec=15,
                 log_path=None, cooldown_sec=20, callback_stato=None):
        self._wifi_json = wifi_json_path or ""
        self._intervallo = max(5, int(intervallo_sec))
        self._log_path = log_path
        self._cooldown = max(5, int(cooldown_sec))
        self._cb = callback_stato
        self._stop_event = threading.Event()
        self._thread = None
        self._ultimo_tentativo = 0
        self._era_connesso = None   # None = primo giro, poi True/False

    # ── controllo ciclo vita ──
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="wifi_auto_reconnect", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    # ── loop ──
    def _loop(self):
        while not self._stop_event.is_set():
            try:
                connesso, ssid_attuale = stato_wifi()
                evento = None

                # Rileva transizioni
                if self._era_connesso is None:
                    pass  # primo giro
                elif self._era_connesso and not connesso:
                    evento = "caduta"
                    self._log("Wi-Fi caduto")
                elif (not self._era_connesso) and connesso:
                    evento = "ripristino"
                    self._log("Wi-Fi ripristinato su '%s'" % (ssid_attuale or "?"))

                self._era_connesso = connesso

                # Tentativo di riconnessione se offline
                if (not connesso):
                    ora = time.time()
                    if (ora - self._ultimo_tentativo) >= self._cooldown:
                        self._ultimo_tentativo = ora
                        self._tenta_riconnessione()
                        evento = "tentativo"

                # Notifica callback UI (se presente)
                if self._cb:
                    try:
                        self._cb(connesso, ssid_attuale, evento)
                    except Exception:
                        pass

            except Exception as e:
                self._log("Errore loop: %s" % e)

            # Attesa interrompibile
            self._stop_event.wait(self._intervallo)

    # ── tentativo di riconnessione ──
    def _tenta_riconnessione(self):
        """Scansiona, incrocia con l'elenco, prova a connettersi."""
        elenco = carica_elenco_wifi(self._wifi_json)
        if not elenco:
            # Nessuna rete memorizzata -> niente da fare (silenzio,
            # non spammiamo il log ad ogni ciclo).
            return

        visibili = reti_visibili()
        if not visibili:
            self._log("Scan reti vuoto (adattatore spento o area senza Wi-Fi)")
            return

        # Trova il primo SSID conosciuto che e' anche visibile.
        # Match case-insensitive ma preserviamo la case originale
        # dell'elenco (e' quella che il SO usera' come profile name).
        visibili_low = {v.lower() for v in visibili}
        for rete in elenco:
            ssid = rete["ssid"]
            if ssid.lower() not in visibili_low:
                continue
            pwd = rete["password"] or ""
            self._log("Tentativo riconnessione a '%s'..." % ssid)
            # Fast path: profilo gia' salvato nel SO
            ok = riconnetti(ssid)
            if ok:
                self._log("  -> OK (profilo SO)")
                return
            # Fallback: ricrea profilo al volo con la password in elenco
            if pwd:
                self._log("  -> profilo SO assente/stale, ricreo con password salvata")
                ok = riconnetti_con_password(ssid, pwd)
                self._log("  -> %s" % ("OK" if ok else "fallito"))
                if ok:
                    return
            else:
                self._log("  -> fallito (password non in elenco per '%s')" % ssid)
        # Nessuna rete conosciuta in range
        self._log("Nessuna Wi-Fi conosciuta a portata (%d visibili, %d in elenco)"
                 % (len(visibili), len(elenco)))

    # ── log su file ──
    def _log(self, messaggio):
        if not self._log_path:
            return
        try:
            os.makedirs(os.path.dirname(self._log_path) or ".", exist_ok=True)
            riga = "[%s] %s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                messaggio,
            )
            # Rotazione grezza: se il file supera 200 KB, lo tronca.
            try:
                if os.path.exists(self._log_path) and \
                   os.path.getsize(self._log_path) > 200 * 1024:
                    with open(self._log_path, "w", encoding="utf-8") as f:
                        f.write(riga)
                    return
            except Exception:
                pass
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(riga)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
#  TEST MANUALE (eseguibile standalone)
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Diagnostica: mostra stato, profili salvati e reti visibili
    connesso, ssid = stato_wifi()
    print("Connesso:", connesso)
    print("SSID corrente:", ssid)
    print("Profili salvati:")
    for n in profili_salvati():
        print("  -", n)
    print("Reti visibili:")
    for n in reti_visibili():
        print("  -", n)
