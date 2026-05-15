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


def reti_visibili(rescan=True):
    """Scansiona le reti Wi-Fi a portata e ritorna lista di tuple
    (ssid, signal_pct) con signal in percentuale 0-100.

    v05.06.53: estratto anche il signal strength per scegliere
    sempre la rete piu' forte in auto-riconnect (prima si fermava
    al primo SSID conosciuto in elenco, ignorando il segnale).

    v05.06.99: parametro `rescan` (solo Linux). IMPORTANTE per non
    staccare la connessione attiva:
      - rescan=True (default): forza un rescan attivo
        (`nmcli device wifi rescan`) prima di leggere la lista.
        Dati freschi, MA su molti chipset il rescan fa saltare la
        radio di canale e provoca un micro-distacco dalla rete
        connessa. Da usare SOLO quando si e' gia' offline (es.
        auto-riconnessione): li' il distacco e' irrilevante.
      - rescan=False: NON forza il rescan, legge la lista che
        NetworkManager ha gia' in cache (`--rescan no`). Dati
        eventualmente un po' vecchi ma ZERO impatto sulla
        connessione attiva. Da usare quando si e' connessi (es.
        valutazione roaming).

    Su Windows: `netsh wlan show networks mode=bssid` (il modo
    bssid include il "Segnale" o "Signal" per ogni rete). Il
    parametro `rescan` viene ignorato: netsh legge i risultati di
    scansione gia' raccolti dal SO, non forza un nuovo scan.

    Ritorna lista [(ssid, signal_pct), ...]. Per SSID duplicati
    tiene il signal massimo. Lista ordinata per signal decrescente
    (la piu' forte per prima). Se il comando fallisce, [].
    """
    reti = {}  # ssid -> max_signal
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["netsh", "wlan", "show", "networks", "mode=bssid"],
                capture_output=True, text=True, timeout=10,
                creationflags=_flag_no_console(),
            )
            # Output tipico:
            #   SSID 1 : NomeRete
            #     ...
            #     BSSID 1 : aa:bb:cc:dd:ee:ff
            #          Signal             : 85%
            #   ...
            cur_ssid = None
            for line in r.stdout.splitlines():
                line = line.rstrip()
                low = line.lower().strip()
                # Riga SSID (NON BSSID)
                if (low.startswith("ssid ")
                        and "bssid" not in low and ":" in line):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        nome = parts[1].strip()
                        if nome:
                            cur_ssid = nome
                            if nome not in reti:
                                reti[nome] = 0
                # Riga Signal/Segnale
                elif (cur_ssid and ":" in line and
                      ("signal" in low or "segnale" in low)):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        s = parts[1].strip().rstrip("%").strip()
                        try:
                            sig = int(float(s))
                            if sig > reti.get(cur_ssid, 0):
                                reti[cur_ssid] = sig
                        except (ValueError, TypeError):
                            pass
        else:
            # Linux
            if rescan:
                # Rescan attivo: dati freschi ma puo' causare un
                # micro-distacco (la radio salta canale). Da usare
                # solo se gia' offline.
                try:
                    subprocess.run(
                        ["nmcli", "device", "wifi", "rescan"],
                        capture_output=True, text=True, timeout=10,
                    )
                except Exception:
                    pass
                lista_cmd = ["nmcli", "-t", "-f", "SSID,SIGNAL",
                             "device", "wifi", "list"]
            else:
                # Nessun rescan: usa la cache di NetworkManager.
                # `--rescan no` garantisce che nmcli NON inneschi
                # comunque una scansione anche se i dati sono vecchi
                # -> zero impatto sulla connessione attiva.
                lista_cmd = ["nmcli", "-t", "-f", "SSID,SIGNAL",
                             "device", "wifi", "list", "--rescan", "no"]
            r = subprocess.run(
                lista_cmd,
                capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.splitlines():
                # Formato: "SSID:signal" (es. "Galaxy:78")
                # SSID escapato con \: per i due-punti interni
                # Splitto sull'ULTIMO ':' non escapato per separare
                # SSID dal signal.
                parts = line.rsplit(":", 1)
                if len(parts) != 2:
                    continue
                nome = parts[0].replace('\\:', ':').strip()
                try:
                    sig = int(float(parts[1].strip()))
                except (ValueError, TypeError):
                    sig = 0
                if not nome:
                    continue
                if sig > reti.get(nome, 0):
                    reti[nome] = sig
    except Exception:
        pass
    # Ordina per signal decrescente (piu' forte per prima)
    return sorted(reti.items(), key=lambda x: -x[1])


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
#  ROAMING - parametri isteresi (anti-flapping)
# ─────────────────────────────────────────────────────────────────────
# v05.06.98: da CONNESSO il monitor valuta periodicamente se esiste
# una rete CONOSCIUTA (password gia' in elenco) col segnale piu'
# forte e, se conviene, ci si sposta. Per non rimbalzare avanti e
# indietro tra reti di potenza simile:
#   - SOGLIA_ROAMING_PCT: la candidata deve battere la rete attuale
#     di almeno tot punti percentuali di segnale. Sotto questa
#     differenza si resta dove si e'.
#   - ROAMING_SEGNALE_COMODO: se il segnale attuale e' gia' >= di
#     questo valore non si fa nulla — sei gia' messo bene e non
#     vale la micro-interruzione che comporta lo switch.
SOGLIA_ROAMING_PCT = 25
ROAMING_SEGNALE_COMODO = 70


# ─────────────────────────────────────────────────────────────────────
#  THREAD DI MONITORAGGIO
# ─────────────────────────────────────────────────────────────────────

class AutoRiconnettore(object):
    """Thread daemon che monitora il Wi-Fi e tenta la riconnessione.

    Logica (v05.05.30):
        - Ogni `intervallo_sec` controlla lo stato.
        - Se e' offline:
            1. Scansiona le reti Wi-Fi visibili.
            2. Legge l'elenco delle Wi-Fi conosciute da `wifi_json_path`.
            3. Per il primo SSID conosciuto che risulta a portata:
               a) prova `riconnetti(ssid)` (profilo SO gia' salvato)
               b) se fallisce, prova `riconnetti_con_password(ssid, pwd)`
                  ricreando il profilo al volo con la password in elenco.
            4. Cooldown anti-spam tra tentativi consecutivi.
        - Se e' connesso (v05.06.98): valuta il ROAMING — controlla
          se a portata c'e' una rete conosciuta col segnale
          significativamente piu' forte e in tal caso ci si sposta.
          Isteresi anti-flapping (vedi SOGLIA_ROAMING_PCT e
          ROAMING_SEGNALE_COMODO sopra) + cooldown dedicato.

    Parametri:
        wifi_json_path: path al file dati/wifi.json (tabella wifi TrackMind).
        intervallo_sec: ogni quanti secondi fa il check (default 15).
        log_path: file di testo dove registrare gli eventi. None = no log.
        cooldown_sec: tempo minimo tra due tentativi consecutivi (default 20).
        callback_stato: funzione opzionale (connesso, ssid, evento).
            evento: None/"caduta"/"ripristino"/"tentativo".
        cooldown_roaming_sec: tempo minimo tra due valutazioni di
            roaming quando si e' connessi (default 150).

    Metodi:
        start(): avvia il thread.
        stop(): ferma il thread al prossimo ciclo.
    """

    def __init__(self, wifi_json_path="", intervallo_sec=15,
                 log_path=None, cooldown_sec=20, callback_stato=None,
                 cooldown_roaming_sec=150):
        self._wifi_json = wifi_json_path or ""
        self._intervallo = max(5, int(intervallo_sec))
        self._log_path = log_path
        self._cooldown = max(5, int(cooldown_sec))
        self._cb = callback_stato
        self._stop_event = threading.Event()
        self._thread = None
        self._ultimo_tentativo = 0
        self._era_connesso = None   # None = primo giro, poi True/False
        # Roaming (da connesso): cooldown dedicato per non valutare
        # — e soprattutto non switchare — troppo spesso.
        self._cooldown_roaming = max(30, int(cooldown_roaming_sec))
        self._ultimo_roaming = 0

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
                else:
                    # Connesso: valuta il roaming verso una rete
                    # conosciuta significativamente piu' forte.
                    # Protetto da un cooldown dedicato per evitare
                    # scan e switch troppo frequenti (anti-flapping).
                    ora = time.time()
                    if (ora - self._ultimo_roaming) >= self._cooldown_roaming:
                        self._ultimo_roaming = ora
                        self._valuta_roaming(ssid_attuale)

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

        # Siamo OFFLINE: rescan attivo OK, non c'e' connessione da
        # disturbare e vogliamo i dati piu' freschi possibile.
        visibili = reti_visibili(rescan=True)  # ord. per signal decr.
        if not visibili:
            self._log("Scan reti vuoto (adattatore spento o area senza Wi-Fi)")
            return

        # v05.06.53: scegliamo la rete CONOSCIUTA col SEGNALE PIU'
        # FORTE, non la prima dell'elenco. Costruiamo una mappa
        # ssid_lowercase -> (ssid_originale_visibile, signal).
        # Poi per ogni rete in elenco controlliamo se e' visibile,
        # e tra le visibili scegliamo quella con signal massimo.
        visibili_map = {ssid.lower(): (ssid, sig)
                         for ssid, sig in visibili}
        candidate = []  # [(signal, ssid_elenco, password)]
        for rete in elenco:
            ssid_e = rete["ssid"]
            v = visibili_map.get(ssid_e.lower())
            if v is None:
                continue
            sig = v[1]
            candidate.append((sig, ssid_e, rete["password"] or ""))
        if not candidate:
            self._log("Nessuna Wi-Fi conosciuta a portata "
                      "(%d visibili, %d in elenco)"
                      % (len(visibili), len(elenco)))
            return
        # Ordina per signal DESC -> tentiamo prima la piu' forte.
        # Se quella fallisce (es. password sbagliata, BSSID
        # cambiato), proviamo la prossima nell'ordine.
        candidate.sort(key=lambda x: -x[0])
        self._log("Reti conosciute a portata (per segnale): %s"
                  % ", ".join("%s(%d%%)" % (s, sig)
                                for sig, s, _ in candidate))
        for sig, ssid, pwd in candidate:
            self._log("Tentativo riconnessione a '%s' "
                      "(segnale %d%%)..." % (ssid, sig))
            # Fast path: profilo gia' salvato nel SO
            ok = riconnetti(ssid)
            if ok:
                self._log("  -> OK (profilo SO)")
                return
            # Fallback: ricrea profilo al volo con la password in elenco
            if pwd:
                self._log("  -> profilo SO assente/stale, ricreo con "
                          "password salvata")
                ok = riconnetti_con_password(ssid, pwd)
                self._log("  -> %s" % ("OK" if ok else "fallito"))
                if ok:
                    return
            else:
                self._log("  -> fallito (password non in elenco "
                          "per '%s')" % ssid)
        self._log("Tutti i tentativi falliti su %d reti candidate"
                  % len(candidate))

    # ── roaming (da connesso) ──
    def _valuta_roaming(self, ssid_attuale):
        """Da CONNESSO: controlla se a portata c'e' una rete
        CONOSCIUTA (password gia' in elenco) col segnale
        significativamente piu' forte di quella attuale, e in tal
        caso ci si sposta.

        Isteresi anti-flapping:
          - la candidata deve superare l'attuale di almeno
            SOGLIA_ROAMING_PCT punti percentuali di segnale;
          - se il segnale attuale e' gia' >= ROAMING_SEGNALE_COMODO
            non si fa nulla (sei gia' messo bene, non vale la
            micro-interruzione dello switch);
          - lo switch e' comunque protetto dal cooldown del loop.

        Non fa nulla (silenziosamente) se non c'e' un elenco reti,
        se lo scan e' vuoto, o se la rete attuale non compare nello
        scan (in quel caso non e' possibile un confronto affidabile).
        """
        ssid_attuale = (ssid_attuale or "").strip()
        if not ssid_attuale:
            return
        elenco = carica_elenco_wifi(self._wifi_json)
        if not elenco:
            return
        # Siamo CONNESSI: NIENTE rescan attivo, altrimenti la radio
        # salta canale e ci stacca dalla rete. Leggiamo la cache di
        # NetworkManager: per decidere un roaming va piu' che bene.
        visibili = reti_visibili(rescan=False)
        if not visibili:
            return
        visibili_map = {ssid.lower(): (ssid, sig)
                        for ssid, sig in visibili}
        # Segnale della rete a cui siamo collegati ORA.
        v_att = visibili_map.get(ssid_attuale.lower())
        if v_att is None:
            # La rete attuale non compare nello scan: confronto non
            # affidabile, salto questo giro.
            return
        sig_attuale = v_att[1]
        # Se siamo gia' messi bene, non vale la pena rischiare una
        # micro-interruzione: niente roaming.
        if sig_attuale >= ROAMING_SEGNALE_COMODO:
            return
        # Cerca la migliore rete CONOSCIUTA a portata, diversa da
        # quella attuale e con password (serve per ricreare il
        # profilo se quello del SO non c'e' o e' stale).
        migliore = None  # (signal, ssid, password)
        for rete in elenco:
            ssid_e = rete["ssid"]
            if ssid_e.lower() == ssid_attuale.lower():
                continue
            pwd = rete["password"] or ""
            if not pwd:
                continue
            v = visibili_map.get(ssid_e.lower())
            if v is None:
                continue
            sig = v[1]
            if migliore is None or sig > migliore[0]:
                migliore = (sig, ssid_e, pwd)
        if migliore is None:
            return
        sig_migliore, ssid_migliore, pwd_migliore = migliore
        guadagno = sig_migliore - sig_attuale
        if guadagno < SOGLIA_ROAMING_PCT:
            # Differenza non sufficiente: restiamo dove siamo.
            return
        self._log("Roaming: '%s' (%d%%) -> '%s' (%d%%), "
                  "guadagno +%d punti"
                  % (ssid_attuale, sig_attuale,
                     ssid_migliore, sig_migliore, guadagno))
        # Prova prima il profilo SO gia' salvato, poi ricrea con la
        # password in elenco.
        ok = riconnetti(ssid_migliore)
        if not ok:
            ok = riconnetti_con_password(ssid_migliore, pwd_migliore)
        if ok:
            self._log("Roaming completato su '%s'" % ssid_migliore)
        else:
            self._log("Roaming FALLITO su '%s', resto su '%s'"
                      % (ssid_migliore, ssid_attuale))

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
    print("Reti visibili (ordinate per segnale decrescente):")
    for ssid, sig in reti_visibili():
        print("  - %s  (%d%%)" % (ssid, sig))
