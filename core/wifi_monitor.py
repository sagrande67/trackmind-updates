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

Cosa fa questo modulo:
    - Controlla ciclicamente se la macchina e' collegata a un Wi-Fi
    - Se cade, e se l'utente ha abilitato la riconnessione automatica e
      configurato un SSID di riferimento, prova a richiamare il profilo
      gia' salvato nel sistema operativo (netsh wlan connect su Windows,
      nmcli connection up su Linux/uConsole).
    - Logga ogni caduta e ogni riconnessione in dati/wifi_log.txt.

Dipendenze:
    ZERO - solo stdlib Python + comandi di sistema gia' presenti
    (netsh, nmcli). Nessuna libreria esterna.

Uso tipico (thread in background):

    from core.wifi_monitor import AutoRiconnettore
    r = AutoRiconnettore(
        ssid_preferito="HotspotSandro",
        intervallo_sec=15,
        log_path="dati/wifi_log.txt",
    )
    r.start()
    ...
    r.stop()   # all'uscita
"""

import os
import sys
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

    Utile per mostrare all'utente un menu "scegli rete da riconnettere
    automaticamente". Su Windows legge 'netsh wlan show profiles', su Linux
    legge 'nmcli connection show' filtrando i wireless.
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


# ─────────────────────────────────────────────────────────────────────
#  RICONNESSIONE
# ─────────────────────────────────────────────────────────────────────

def riconnetti(ssid):
    """Prova a riconnettersi al profilo gia' salvato per lo SSID indicato.

    Il profilo DEVE essere stato salvato una volta nel sistema con la password
    (cioe' l'utente si e' connesso almeno una volta a quella rete e il SO ha
    memorizzato le credenziali). Se il profilo non esiste, il comando fallisce
    silenziosamente e torna False: la riconnessione senza credenziali salvate
    richiede input utente e non puo' essere fatta a sua insaputa.

    Ritorna True se il comando ha risposto "ok" (non garantisce che la rete
    sia effettivamente su - per quello bisogna ricontrollare stato_wifi()
    qualche secondo dopo).
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
            # "Richiesta di connessione... completata correttamente" / "completed successfully"
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


# ─────────────────────────────────────────────────────────────────────
#  THREAD DI MONITORAGGIO
# ─────────────────────────────────────────────────────────────────────

class AutoRiconnettore(object):
    """Thread daemon che monitora il Wi-Fi e tenta la riconnessione.

    Parametri:
        ssid_preferito: SSID da riattivare quando la connessione cade.
                        Se vuoto, il thread si limita a loggare senza agire.
        intervallo_sec: ogni quanti secondi fa il check (default 15).
        log_path: file di testo dove registrare gli eventi. None = no log.
        cooldown_sec: tempo minimo tra due tentativi consecutivi di
                      riconnessione per non martellare il sistema.
                      Default 20 sec.
        callback_stato: funzione opzionale chiamata ad ogni check con
                        (connesso: bool, ssid: str, evento: str|None).
                        'evento' e' uno tra None/"caduta"/"ripristino"/"tentativo".
                        Utile per aggiornare la UI. La callback e' invocata
                        dal thread worker: il chiamante deve garantire la
                        thread-safety (es. root.after(0, ...) con Tkinter).

    Metodi:
        start(): avvia il thread.
        stop(): ferma il thread al prossimo ciclo.
        set_ssid(nuovo): cambia al volo lo SSID preferito (per aggiornamenti
                         da CONFI).
    """

    def __init__(self, ssid_preferito="", intervallo_sec=15,
                 log_path=None, cooldown_sec=20, callback_stato=None):
        self._ssid = (ssid_preferito or "").strip()
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

    def set_ssid(self, nuovo):
        self._ssid = (nuovo or "").strip()

    # ── loop ──
    def _loop(self):
        while not self._stop_event.is_set():
            try:
                connesso, ssid_attuale = stato_wifi()
                evento = None

                # Rileva transizioni
                if self._era_connesso is None:
                    # Primo giro, nessuna transizione da segnare
                    pass
                elif self._era_connesso and not connesso:
                    evento = "caduta"
                    self._log("Wi-Fi caduto (era '%s')" % (ssid_attuale or "?"))
                elif (not self._era_connesso) and connesso:
                    evento = "ripristino"
                    self._log("Wi-Fi ripristinato su '%s'" % (ssid_attuale or "?"))

                self._era_connesso = connesso

                # Tentativo di riconnessione se offline e abbiamo uno SSID
                if (not connesso) and self._ssid:
                    ora = time.time()
                    if (ora - self._ultimo_tentativo) >= self._cooldown:
                        self._ultimo_tentativo = ora
                        self._log("Tentativo riconnessione a '%s'..." % self._ssid)
                        ok = riconnetti(self._ssid)
                        self._log("  -> %s" % ("OK" if ok else "fallito"))
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
    # Diagnostica: mostra stato e profili salvati
    connesso, ssid = stato_wifi()
    print("Connesso:", connesso)
    print("SSID corrente:", ssid)
    print("Profili salvati:")
    for n in profili_salvati():
        print("  -", n)
