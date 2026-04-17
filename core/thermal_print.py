"""
TrackMind - Stampa Termica (58mm / 42 char Font B)
Modulo per stampare schede gara su stampante termica portatile.

Protocollo: ESC/POS via Bluetooth RFCOMM
Su uConsole/Linux: auto-discovery stampante BT + bind rfcomm automatico.
Su Windows: win32print RAW o porta COM.

L'operatore accende la stampante, TrackMind fa tutto il resto.
"""

import socket
import os
import sys
import subprocess
import time
import threading
from datetime import datetime

# Larghezza carta 58mm = ~42 caratteri con Font B (piccolo)
W = 42

# --- Comandi ESC/POS ---
ESC = b'\x1b'
GS  = b'\x1d'

CMD_INIT        = ESC + b'\x40'           # Reset stampante
CMD_FONT_B      = ESC + b'\x4d\x01'      # Font B (piccolo, 9x17)
CMD_FONT_A      = ESC + b'\x4d\x00'      # Font A (standard, 12x24)
CMD_BOLD_ON     = ESC + b'\x45\x01'
CMD_BOLD_OFF    = ESC + b'\x45\x00'
CMD_DOUBLE      = ESC + b'\x21\x31'       # Doppia h+w + Font B
CMD_DOUBLE_H    = ESC + b'\x21\x11'       # Doppia altezza + Font B
CMD_NORMAL      = ESC + b'\x21\x01'       # Font B normale
CMD_CENTER      = ESC + b'\x61\x01'
CMD_LEFT        = ESC + b'\x61\x00'
CMD_CUT         = GS  + b'\x56\x01'       # Taglio parziale
CMD_FEED        = b'\n\n\n\n'             # Avanzamento per staccare


# --- Utilita' formattazione ---

def _linea(car="="):
    return car * W

def _centra(testo):
    return testo.center(W)

def _riga(label, valore, larg_label=20):
    """Riga allineata: label a sinistra, valore a destra. Tronca se necessario."""
    val_str = str(valore)
    max_val = W - larg_label - 1  # almeno 1 spazio
    if len(val_str) > max_val:
        val_str = val_str[:max_val]
    spazi = W - larg_label - len(val_str)
    if spazi < 1: spazi = 1
    return "%s%s%s" % (label.ljust(larg_label), " " * spazi, val_str)

def _min_to_ms(minuti):
    if not minuti or minuti <= 0:
        return "--:--"
    m = int(minuti)
    s = int((minuti - m) * 60)
    return "%d:%02d" % (m, s)

def _fmt_tempo(sec):
    if not sec or sec <= 0:
        return "--:--.--"
    m = int(sec) // 60
    s = sec - m * 60
    return "%02d:%05.2f" % (m, s)


# =================================================================
#  GENERAZIONE TESTO SCHEDA GARA
# =================================================================

def genera_scheda_gara(sessione):
    """
    Genera il testo della scheda gara da una sessione.
    Ritorna lista di righe di testo (plain text, 42 char Font B).
    """
    righe = []
    r = righe.append

    pilota = sessione.get("pilota", "?")
    setup = sessione.get("setup", "?")
    data = sessione.get("data", "?")
    ora = sessione.get("ora", "?")[:5]
    serb = sessione.get("serbatoio_cc", 0)
    best = sessione.get("miglior_tempo", 0)
    media = sessione.get("media", 0)
    n_giri = sessione.get("num_giri", 0)
    consumo = sessione.get("consumo_cc_min", 0)
    autonomia = sessione.get("autonomia_min", 0)
    tipo = sessione.get("tipo", "laptimer")
    strat = sessione.get("strategia", {})

    r(_linea("="))
    r(_centra("SCHEDA GARA"))
    r(_centra("TRACKMIND"))
    r(_linea("="))
    r("")
    r(_riga("Pilota:", pilota))
    r(_riga("Setup:", setup))
    r(_riga("Data:", "%s  %s" % (data, ora)))
    r(_riga("Fonte:", "SpeedHive" if tipo == "speedhive" else "LapTimer"))
    r("")
    r(_linea("-"))
    r(_centra("TEMPI"))
    r(_linea("-"))
    r("")
    r(_riga("BEST LAP:", _fmt_tempo(best)))
    r(_riga("MEDIA:", _fmt_tempo(media)))
    r(_riga("GIRI:", "%d" % n_giri))
    r("")
    r(_linea("-"))
    r(_centra("CARBURANTE"))
    r(_linea("-"))
    r("")
    r(_riga("Serbatoio:", "%dcc" % serb))
    r(_riga("Consumo:", "%.1f cc/min" % consumo))
    r(_riga("Autonomia:", _min_to_ms(autonomia)))
    r("")

    # Strategia per ogni durata gara
    for chiave in sorted(strat.keys()):
        v = strat[chiave]
        dur = chiave.replace("gara_", "").replace("_min", "")
        pit = v.get("pit_stop", 0)
        rientro = v.get("rientro_min", 0)

        r(_linea("="))
        r(_centra("*** GARA %s MINUTI ***" % dur))
        r(_linea("="))
        r("")
        r(_riga("PIT STOP:", "%d" % pit))
        r(_riga("DURATA STINT:", _min_to_ms(autonomia)))
        r("")
        r(_centra(">>> CHIAMATA: %s <<<" % _min_to_ms(rientro)))
        r("")

    r(_linea("="))
    r(_centra("Stampato: %s" % datetime.now().strftime("%d/%m/%Y %H:%M")))
    r(_linea("="))
    r("")

    return righe


def genera_scheda_completa(sessioni, best_assoluto=None):
    """
    Genera scheda con TUTTE le sessioni + confronto.
    Usa l'ultima sessione per la strategia principale.
    """
    if not sessioni:
        return ["Nessuna sessione disponibile."]

    # Usa la sessione piu' recente per la scheda
    ultima = sessioni[-1]
    righe = genera_scheda_gara(ultima)

    # Se ci sono piu' sessioni, aggiungi confronto
    if len(sessioni) > 1 and best_assoluto:
        righe.append("")
        righe.append(_linea("-"))
        righe.append(_centra("STORICO (%d sessioni)" % len(sessioni)))
        righe.append(_linea("-"))
        righe.append("")
        righe.append(_riga("BEST ASSOLUTO:", _fmt_tempo(best_assoluto)))
        righe.append("")
        for i, s in enumerate(reversed(sessioni)):
            data = s.get("data", "?")[-5:]  # solo MM-DD
            best = s.get("miglior_tempo", 0)
            media = s.get("media", 0)
            n = s.get("num_giri", 0)
            # Formato compatto: #1 03-29 B:18.54 M:19.02 24g
            b_str = _fmt_tempo(best)[-5:]   # solo SS.CC
            m_str = _fmt_tempo(media)[-5:]   # solo SS.CC
            righe.append("#%d %s B:%s M:%s %dg" % (
                len(sessioni) - i, data, b_str, m_str, n))
        righe.append("")
        righe.append(_linea("="))

    return righe


# =================================================================
#  SALVATAGGIO FILE TESTO
# =================================================================

def salva_scheda_txt(righe, percorso_dati, prefisso="scheda_gara"):
    """Salva la scheda come file .txt. Ritorna il path o None."""
    try:
        os.makedirs(percorso_dati, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome = "%s_%s.txt" % (prefisso, ts)
        path = os.path.join(percorso_dati, nome)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(righe))
        return path
    except Exception as e:
        print("[STAMPA] Errore salvataggio: %s" % e)
        return None


# =================================================================
#  AUTO-DISCOVERY E AUTO-BIND BLUETOOTH (Linux/uConsole)
# =================================================================

# Nomi noti di stampanti termiche BT (lowercase per confronto)
_NOMI_STAMPANTE = ["printer", "netum", "pos-58", "pos58", "thermal",
                   "receipt", "nt-1809", "yichip", "bluet"]

# Cache MAC trovato in sessione (evita scan ripetuti)
_mac_cache = None

# Lock per evitare accessi BT simultanei (monitor + stampa)
_bt_lock = threading.Lock()


def _is_linux():
    """Ritorna True se siamo su Linux/uConsole."""
    return sys.platform.startswith("linux")


def _bt_scan_stampante(timeout=12):
    """
    Scansiona dispositivi Bluetooth cercando una stampante termica.
    Usa hcitool scan (piu' affidabile su uConsole) con fallback bluetoothctl.
    Ritorna MAC address o None.
    """
    global _mac_cache
    if _mac_cache:
        return _mac_cache

    # Metodo 1: hcitool scan --flush (forza nuova scansione radio)
    try:
        r = subprocess.run(["hcitool", "scan", "--flush"],
                           capture_output=True, text=True, timeout=timeout + 8)
        if r.returncode == 0:
            for linea in r.stdout.strip().split("\n"):
                linea = linea.strip()
                if not linea or linea.startswith("Scanning"):
                    continue
                # Formato: "XX:XX:XX:XX:XX:XX    Nome Dispositivo"
                parti = linea.split("\t")
                if len(parti) >= 2:
                    mac = parti[0].strip()
                    nome = parti[1].strip().lower()
                    if any(k in nome for k in _NOMI_STAMPANTE):
                        _mac_cache = mac
                        return mac
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass

    # Metodo 2: bluetoothctl (fallback)
    try:
        # Avvia scan per qualche secondo
        subprocess.run(["bluetoothctl", "scan", "on"],
                       capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        r = subprocess.run(["bluetoothctl", "devices"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for linea in r.stdout.strip().split("\n"):
                # Formato: "Device XX:XX:XX:XX:XX:XX Nome"
                linea = linea.strip()
                if not linea.startswith("Device"):
                    continue
                parti = linea.split(" ", 2)
                if len(parti) >= 3:
                    mac = parti[1].strip()
                    nome = parti[2].strip().lower()
                    if any(k in nome for k in _NOMI_STAMPANTE):
                        _mac_cache = mac
                        return mac
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass

    return None


def _bt_bind_rfcomm(mac, canale=1, dev_num=0):
    """
    Crea /dev/rfcommN collegato al MAC via rfcomm bind.
    Se il device esiste gia', controlla che sia collegato allo stesso MAC.
    Ritorna (device_path, errore) - errore e' None se ok.
    """
    dev_path = "/dev/rfcomm%d" % dev_num

    # Se esiste gia', verifica che sia il MAC giusto
    if os.path.exists(dev_path):
        try:
            r = subprocess.run(["rfcomm", "show", str(dev_num)],
                               capture_output=True, text=True, timeout=5)
            if mac.upper() in r.stdout.upper():
                return dev_path, None  # Gia' bindato correttamente
            # MAC diverso: rilascia e ri-binda
            subprocess.run(["rfcomm", "release", str(dev_num)],
                           capture_output=True, timeout=5)
        except Exception:
            pass

    # Bind nuovo
    try:
        r = subprocess.run(["rfcomm", "bind", str(dev_num), mac, str(canale)],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            # Prova con sudo (uConsole potrebbe avere sudoers configurato)
            r = subprocess.run(["sudo", "-n", "rfcomm", "bind",
                                str(dev_num), mac, str(canale)],
                               capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return None, "rfcomm bind fallito: %s" % r.stderr.strip()

        # Aspetta che il device appaia
        for _ in range(10):
            if os.path.exists(dev_path):
                return dev_path, None
            time.sleep(0.3)

        return None, "Device %s non creato dopo bind" % dev_path

    except subprocess.TimeoutExpired:
        return None, "Timeout rfcomm bind"
    except FileNotFoundError:
        return None, "Comando rfcomm non trovato"
    except Exception as e:
        return None, "Errore bind: %s" % e


def _bt_auto_setup(mac_configurato=""):
    """
    Setup stampante BT su Linux/uConsole.
    1. Se c'e' gia' /dev/rfcomm0 funzionante, usa quello
    2. Se c'e' un MAC in conf.dat, usa SOLO quello (no scan)
    3. Solo alla prima installazione (MAC vuoto): scan per trovarla

    IMPORTANTE: lo scan avviene SOLO se il MAC non e' configurato.
    In pista ci possono essere piu' terminali — ogni uno deve usare
    la SUA stampante, quella abbinata alla consegna.

    Ritorna (device_path, mac, errore) - errore e' None se ok.
    """
    global _mac_cache
    # Gia' pronto?
    if os.path.exists("/dev/rfcomm0"):
        return "/dev/rfcomm0", mac_configurato or "rfcomm0", None

    # MAC configurato in conf.dat? Usa SOLO quello.
    mac = mac_configurato if (mac_configurato and ":" in mac_configurato
                              and len(mac_configurato) == 17) else None

    # Solo prima installazione: scan per trovare la stampante
    if not mac:
        mac = _bt_scan_stampante()

    if not mac:
        return None, None, "Stampante non configurata"

    # Prova bind rfcomm
    dev, err = _bt_bind_rfcomm(mac)
    if not err and dev:
        return dev, mac, None

    # Bind fallito (serve sudo). Verifica che la stampante sia ACCESA
    # usando hcitool name (non apre socket dati, non causa "device busy").
    try:
        r = subprocess.run(["hcitool", "name", mac],
                           capture_output=True, text=True, timeout=6)
        nome = r.stdout.strip()
        if nome:
            # Stampante accesa e raggiungibile!
            _mac_cache = mac
            return "socket", mac, None
        else:
            return None, mac, "Stampante spenta"
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None, mac, "Stampante non raggiungibile"


def bt_reset_cache():
    """Resetta la cache MAC (utile se si cambia stampante)."""
    global _mac_cache
    _mac_cache = None


def stampante_disponibile(mac_configurato="auto"):
    """
    Verifica se una stampante termica e' effettivamente raggiungibile.
    Ritorna True/False. Timeout breve (~4s) per non bloccare la UI.
    Usata per decidere se abilitare il bottone STAMPA.
    """
    # USB diretto: se il device esiste, la stampante e' collegata
    for usb_dev in ("/dev/usb/lp0", "/dev/usb/lp1"):
        if os.path.exists(usb_dev):
            return True

    # Linux: verifica BT con hcitool name (veloce, non apre socket)
    if _is_linux():
        if os.path.exists("/dev/rfcomm0"):
            return True
        # Determina MAC da usare
        mac = None
        if mac_configurato and ":" in mac_configurato and len(mac_configurato) == 17:
            mac = mac_configurato
        elif _mac_cache:
            mac = _mac_cache
        if mac:
            try:
                r = subprocess.run(["hcitool", "name", mac],
                                   capture_output=True, text=True, timeout=4)
                return bool(r.stdout.strip())
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                return False
        return False

    # Windows: cerca stampante termica tra quelle installate
    if sys.platform == "win32":
        try:
            import win32print
            stampanti = win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)
            for flags, desc, name, comment in stampanti:
                nl = name.lower()
                if mac_configurato and mac_configurato.lower() in nl:
                    return True
                if any(k in nl for k in ["netum", "generic", "text only",
                       "yichip", "pos-58", "pos", "thermal", "receipt"]):
                    return True
        except (ImportError, Exception):
            pass
        # Fallback: COM configurata o MAC BT → assume disponibile
        if mac_configurato and (mac_configurato.upper().startswith("COM")
                                or ":" in mac_configurato):
            return True
        return False

    return False


# =================================================================
#  STAMPA BLUETOOTH ESC/POS
# =================================================================

def stampa_bluetooth(righe, mac_address, porta=1):
    """
    Invia la scheda gara alla stampante termica via Bluetooth.
    Su Linux/uConsole: usa /dev/rfcomm0 (seriale BT) se disponibile,
    altrimenti socket RFCOMM diretto.
    Su Windows: usa porta COM seriale.

    Args:
        righe:       lista di stringhe (testo da stampare)
        mac_address: MAC della stampante o porta COM (es. "COM4", "00:11:22:33:44:55")
        porta:       canale RFCOMM (default 1)

    Ritorna: (successo: bool, messaggio: str)
    """
    if not mac_address:
        mac_address = "auto"

    try:
        dati = _prepara_dati_escpos(righe)

        # Metodo 0: USB diretto (Linux - /dev/usb/lp0)
        for usb_dev in ("/dev/usb/lp0", "/dev/usb/lp1"):
            if os.path.exists(usb_dev):
                with open(usb_dev, "wb") as f:
                    f.write(dati)
                return True, "Scheda stampata (USB)!"

        # Metodo 1: Auto-setup BT su Linux/uConsole
        # Cerca stampante, binda rfcomm, stampa - tutto automatico
        if _is_linux():
            # 1a: Se /dev/rfcomm0 esiste gia', stampa subito
            if os.path.exists("/dev/rfcomm0"):
                try:
                    with open("/dev/rfcomm0", "wb") as f:
                        f.write(dati)
                    return True, "Scheda stampata (BT)!"
                except PermissionError:
                    return False, "Permesso negato su rfcomm0 (aggiungere utente a gruppo dialout)"
                except Exception:
                    pass  # Device morto, prova auto-setup

            # 1b: Auto-discovery + bind + stampa
            dev, mac_trovato, err = _bt_auto_setup(mac_address)
            if err:
                return False, str(err)

            # 1c: Stampa via socket diretto (rfcomm bind non riuscito)
            if dev == "socket" and mac_trovato:
                with _bt_lock:
                    try:
                        sock = socket.socket(socket.AF_BLUETOOTH,
                                             socket.SOCK_STREAM,
                                             socket.BTPROTO_RFCOMM)
                        sock.settimeout(10)
                        sock.connect((mac_trovato, porta))
                        sock.send(dati)
                        sock.close()
                        return True, "Scheda stampata (BT)!"
                    except Exception as e:
                        return False, "Errore socket BT: %s" % e

            # 1d: Stampa su device rfcomm
            try:
                with open(dev, "wb") as f:
                    f.write(dati)
                return True, "Scheda stampata (BT)!"
            except PermissionError:
                return False, "Permesso negato su %s (aggiungere utente a gruppo dialout)" % dev
            except Exception as e:
                return False, "Errore scrittura %s: %s" % (dev, e)

        # Metodo 2a: stampante Windows (win32print RAW)
        if sys.platform == "win32":
            try:
                import win32print
                # Cerca stampante per nome configurato o generic/pos
                stampanti = win32print.EnumPrinters(
                    win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)
                target = None
                for flags, desc, name, comment in stampanti:
                    nl = name.lower()
                    # Se mac_address contiene un nome stampante specifico
                    if mac_address.lower() in nl:
                        target = name; break
                    if any(k in nl for k in ["netum", "generic", "text only", "yichip", "pos-58", "pos", "thermal", "receipt"]):
                        target = name
                if target:
                    hprinter = win32print.OpenPrinter(target)
                    win32print.StartDocPrinter(hprinter, 1, ("TrackMind", None, "RAW"))
                    win32print.StartPagePrinter(hprinter)
                    win32print.WritePrinter(hprinter, dati)
                    win32print.EndPagePrinter(hprinter)
                    win32print.EndDocPrinter(hprinter)
                    win32print.ClosePrinter(hprinter)
                    return True, "Scheda stampata (USB)!"
            except ImportError:
                pass  # win32print non disponibile, prova COM
            except Exception as e:
                return False, "Errore stampante: %s" % e

        # Metodo 2b: porta COM (Windows BT seriale)
        if mac_address.upper().startswith("COM"):
            try:
                import serial
                ser = serial.Serial(mac_address, 9600, timeout=3, dsrdtr=True)
                time.sleep(0.5)
                ser.write(dati)
                ser.flush()
                time.sleep(1)
                ser.close()
                return True, "Scheda stampata (COM)!"
            except ImportError:
                return False, "pyserial non installato!"
            except Exception as e:
                return False, "Errore COM: %s" % e

        return False, "Stampante non trovata!"

    except Exception as e:
        return False, "Errore stampa: %s" % e


def _prepara_dati_escpos(righe):
    """Converte le righe in dati binari ESC/POS con formattazione."""
    dati = bytearray()
    dati.extend(CMD_INIT)
    dati.extend(CMD_FONT_B)  # Font piccolo per risparmiare carta

    for riga in righe:
        if riga.startswith("===") or riga.startswith("---"):
            dati.extend(CMD_BOLD_ON)
            dati.extend((riga + "\n").encode("ascii", errors="replace"))
            dati.extend(CMD_BOLD_OFF)
        elif ">>>" in riga and "CHIAMATA" in riga:
            dati.extend(CMD_CENTER)
            dati.extend(CMD_DOUBLE_H)
            dati.extend(CMD_BOLD_ON)
            testo = riga.strip().replace(">>>", "").replace("<<<", "").strip()
            dati.extend((testo + "\n").encode("ascii", errors="replace"))
            dati.extend(CMD_NORMAL)
            dati.extend(CMD_BOLD_OFF)
            dati.extend(CMD_LEFT)
        elif "***" in riga:
            dati.extend(CMD_CENTER)
            dati.extend(CMD_DOUBLE_H)
            dati.extend((riga.strip() + "\n").encode("ascii", errors="replace"))
            dati.extend(CMD_NORMAL)
            dati.extend(CMD_LEFT)
        elif "SCHEDA GARA" in riga or "TRACKMIND" in riga:
            dati.extend(CMD_CENTER)
            dati.extend(CMD_DOUBLE)
            dati.extend((riga.strip() + "\n").encode("ascii", errors="replace"))
            dati.extend(CMD_NORMAL)
            dati.extend(CMD_LEFT)
        else:
            dati.extend((riga + "\n").encode("ascii", errors="replace"))

    dati.extend(CMD_FEED)
    dati.extend(CMD_CUT)
    return bytes(dati)


# =================================================================
#  TEST STANDALONE
# =================================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TrackMind - Test stampa termica")
    parser.add_argument("--scan", action="store_true",
                        help="Scansiona e mostra stampanti BT trovate")
    parser.add_argument("--stampa", action="store_true",
                        help="Stampa scheda test sulla stampante")
    parser.add_argument("--mac", default="",
                        help="MAC address stampante (opzionale, auto-detect)")
    args = parser.parse_args()

    if args.scan:
        print("[SCAN] Ricerca stampante termica BT...")
        mac = _bt_scan_stampante(timeout=10)
        if mac:
            print("[SCAN] Trovata: %s" % mac)
        else:
            print("[SCAN] Nessuna stampante trovata. Accesa e in range?")
        sys.exit(0)

    # Dati test
    sessione_test = {
        "pilota": "Sandro Grandesso",
        "setup": "Test Setup",
        "data": "2026-03-31",
        "ora": "14:30:00",
        "serbatoio_cc": 150,
        "miglior_tempo": 17.997,
        "media": 18.978,
        "num_giri": 24,
        "consumo_cc_min": 19.34,
        "autonomia_min": 7.76,
        "tipo": "laptimer",
        "strategia": {
            "gara_30_min": {"pit_stop": 3, "giri_per_stint": 24,
                            "giri_sicuri": 23, "rientro_min": 7.44},
            "gara_45_min": {"pit_stop": 5, "giri_per_stint": 24,
                            "giri_sicuri": 23, "rientro_min": 7.44},
        }
    }

    righe = genera_scheda_gara(sessione_test)

    if args.stampa:
        print("[STAMPA] Auto-setup e stampa scheda test...")
        ok, msg = stampa_bluetooth(righe, args.mac or "auto")
        print("[STAMPA] %s - %s" % ("OK" if ok else "ERRORE", msg))
    else:
        print("\n".join(righe))
        print("\n--- Usa --stampa per stampare, --scan per cercare ---")
