"""
CONF Manager - Gestione configurazione crittografata
Accessibile solo tramite Ctrl+Shift+F12
"""

import json
import os
import sys
import base64
import hashlib
import platform
import subprocess as _subprocess
from datetime import datetime, date, timedelta

# Chiave di offuscamento interna (non modificabile dall'utente)
_OBFUSCATION_KEY = b"Tr4ckM1nd_C0nf_K3y_2026!#"

# Struttura CONF di default
DEFAULT_CONF = {
    "nome_database": "TRACKMIND",
    "percorso_installazione": "",  # path completo, si auto-rileva
    "percorso_tabelle": "",        # path completo definizioni
    "percorso_dati": "",           # path completo dati
    "percorso_backup": "",         # path completo backup
    "percorso_core": "",            # path completo moduli core
    "percorso_addons": "",          # path completo moduli addons
    "larghezza_max": 900,
    "altezza_max": 700,
    "scala": 1.0,
    "fullscreen": 0,
    "cella_dimensione": 16,
    "cella_spaziatura": 1,
    "font_campi": 9,
    "font_label": 9,
    "stampante_bt": "",
    "data_installazione": "",
    "data_fine_licenza": "2099-12-31",
    "versione": "5.4",
    "codice_macchina": "",         # Hardware fingerprint (auto-generato)
    "chiave_attivazione": "",      # Chiave fornita dallo sviluppatore
    "licenza_revocata": "",        # Data revoca ISO se la licenza e' stata revocata dallo sviluppatore
    "motivo_revoca": "",           # Motivo revoca (informativo)
    "email_sviluppatore": "trackmind.support.gmail.com@gmail.com",
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "trackmind.support.gmail.com@gmail.com",
    "smtp_password": "",           # App password Gmail (da generare su myaccount.google.com)
    "anthropic_api_key": "",        # API key Anthropic per analisi IA (opzionale)
    "multiutente": "1",               # 0=illimitato, 1=monoutente, 2+=multi con limite
    "crediti_ia": 500,                # Crediti IA: scalano a ogni analisi, ricaricabili con codice
    "sd_tbw_gb": 30000,               # TBW dichiarato della micro-SD in GB (default 30 TB)
    "sd_vu_max_mbs": 20,              # Fondo scala VU meter I/O SD in MB/s
    "wifi_auto_attivo": 0,            # 0=disabilitato, 1=riconnessione Wi-Fi automatica
    "wifi_auto_ssid": "",             # SSID del profilo Wi-Fi preferito da riagganciare
    "wifi_auto_intervallo": 15,       # Secondi tra un controllo e l'altro (min 5)
}

# Chiave segreta per generare/verificare le chiavi di attivazione
# IMPORTANTE: questa chiave deve essere uguale in genera_licenza.py
_ACTIVATION_SECRET = b"TrKm1nd_4ct1v4t10n_S3cr3t_2026!!"


def _get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _get_conf_path():
    return os.path.join(_get_base_dir(), "conf.dat")


def _encrypt(data_str):
    """Offusca i dati: JSON -> bytes -> XOR -> base64."""
    data_bytes = data_str.encode("utf-8")
    key = _OBFUSCATION_KEY
    encrypted = bytes([b ^ key[i % len(key)] for i, b in enumerate(data_bytes)])
    return base64.b64encode(encrypted).decode("ascii")


def _decrypt(encoded_str):
    """De-offusca: base64 -> XOR -> bytes -> JSON string."""
    encrypted = base64.b64decode(encoded_str.encode("ascii"))
    key = _OBFUSCATION_KEY
    decrypted = bytes([b ^ key[i % len(key)] for i, b in enumerate(encrypted)])
    return decrypted.decode("utf-8")


def carica_conf():
    """Carica la configurazione dal file crittografato. Crea default se non esiste."""
    path = _get_conf_path()
    base = _get_base_dir()

    if not os.path.exists(path):
        # Prima installazione: crea conf con percorsi assoluti
        conf = DEFAULT_CONF.copy()
        conf["data_installazione"] = date.today().isoformat()
        conf = _assicura_percorsi_assoluti(conf, base)
        salva_conf(conf)
        return conf

    try:
        with open(path, "r", encoding="utf-8") as f:
            encoded = f.read().strip()
        json_str = _decrypt(encoded)
        conf = json.loads(json_str)

        # Aggiungi campi mancanti (per aggiornamenti)
        for k, v in DEFAULT_CONF.items():
            if k not in conf:
                conf[k] = v

        # Migrazione: converte percorsi relativi in assoluti
        conf = _assicura_percorsi_assoluti(conf, base)

        return conf

    except Exception as e:
        print("[ERRORE CONF] %s - Uso default" % e)
        conf = DEFAULT_CONF.copy()
        conf["data_installazione"] = date.today().isoformat()
        conf = _assicura_percorsi_assoluti(conf, base)
        return conf


def _assicura_percorsi_assoluti(conf, base):
    """Converte percorsi vuoti o relativi in percorsi assoluti."""
    # Percorso installazione
    if not conf.get("percorso_installazione") or not os.path.isabs(conf["percorso_installazione"]):
        conf["percorso_installazione"] = base

    # Percorsi dati: se vuoti, relativi, o solo nome cartella -> converti
    mapping = {
        "percorso_tabelle": "tabelle",
        "percorso_dati": "dati",
        "percorso_backup": "backup",
        "percorso_core": "core",
        "percorso_addons": "addons",
    }
    for chiave, default_nome in mapping.items():
        val = conf.get(chiave, "").strip()
        if not val or not os.path.isabs(val):
            nome = val if val else default_nome
            conf[chiave] = os.path.join(base, nome)

    return conf


def salva_conf(conf):
    """Salva la configurazione crittografata."""
    path = _get_conf_path()
    json_str = json.dumps(conf, ensure_ascii=False, indent=2)
    encoded = _encrypt(json_str)
    with open(path, "w", encoding="utf-8") as f:
        f.write(encoded)


def verifica_licenza(conf):
    """
    Verifica se la licenza e' valida.
    Ritorna (valida: bool, messaggio: str, giorni_rimasti: int)
    """
    try:
        data_str = conf.get("data_fine_licenza", "2099-12-31")
        data_fine = _parse_data(data_str)
        oggi = date.today()
        giorni = (data_fine - oggi).days

        if giorni < 0:
            return False, "Licenza scaduta il %s" % data_fine.isoformat(), giorni
        elif giorni <= 30:
            return True, "Licenza in scadenza tra %d giorni" % giorni, giorni
        else:
            return True, "Licenza valida fino al %s" % data_fine.isoformat(), giorni

    except Exception as e:
        return True, "Errore verifica licenza: %s" % e, 999


def _parse_data(data_str):
    """Parse data in qualsiasi formato: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY."""
    data_str = str(data_str).strip()
    if not data_str:
        return date(2099, 12, 31)

    try:
        # ISO: 2026-02-10 o 2099-12-31
        if len(data_str) == 10 and data_str[4] == "-":
            return date(int(data_str[:4]), int(data_str[5:7]), int(data_str[8:10]))
        # Europeo con /: 10/02/2026
        if "/" in data_str:
            parti = data_str.split("/")
            if len(parti) == 3:
                return date(int(parti[2]), int(parti[1]), int(parti[0]))
        # Europeo con -: 10-02-2026
        if "-" in data_str and len(data_str) == 10 and data_str[2] == "-":
            parti = data_str.split("-")
            if len(parti) == 3:
                return date(int(parti[2]), int(parti[1]), int(parti[0]))
    except (ValueError, IndexError):
        pass

    return date(2099, 12, 31)


def _percorso_valido(percorso_conf, fallback):
    """Usa il percorso da conf solo se compatibile col SO corrente,
    altrimenti ritorna il fallback (percorso locale calcolato).
    Rileva anche percorsi corrotti (mix Linux+Windows concatenati)."""
    if not percorso_conf:
        return fallback
    # Cerca pattern drive Windows (X:\ o X:/) ovunque nella stringa
    # Cattura sia percorsi puri "D:\..." sia corrotti "/linux/D:/..."
    import re
    if os.sep == '/' and re.search(r'[A-Za-z]:[/\\]', percorso_conf):
        return fallback
    # Percorso Linux (es. /home/...) su Windows? Ignora e usa fallback
    if os.sep == '\\' and percorso_conf.startswith('/'):
        return fallback
    return percorso_conf


def get_percorsi(conf):
    """Restituisce i percorsi assoluti dalla configurazione.
    Ignora automaticamente percorsi salvati per un SO diverso."""
    base = _get_base_dir()
    return {
        "installazione": _percorso_valido(conf.get("percorso_installazione"), base),
        "definizioni": _percorso_valido(conf.get("percorso_tabelle"), os.path.join(base, "tabelle")),
        "dati": _percorso_valido(conf.get("percorso_dati"), os.path.join(base, "dati")),
        "backup": _percorso_valido(conf.get("percorso_backup"), os.path.join(base, "backup")),
        "core": _percorso_valido(conf.get("percorso_core"), os.path.join(base, "core")),
        "addons": _percorso_valido(conf.get("percorso_addons"), os.path.join(base, "addons")),
    }


# ─────────────────────────────────────────────────────────────────────
#  HARDWARE FINGERPRINT
# ─────────────────────────────────────────────────────────────────────

def _get_nome_pc():
    """Nome del computer."""
    try:
        return platform.node().strip().upper()
    except Exception:
        return "UNKNOWN"


def _get_mac_address():
    """MAC address della prima interfaccia di rete attiva."""
    try:
        import uuid as _uuid
        mac = _uuid.getnode()
        if (mac >> 40) & 1:
            return "NO-MAC"
        return ':'.join(('%012X' % mac)[i:i+2] for i in range(0, 12, 2))
    except Exception:
        return "NO-MAC"


def _get_soc_serial():
    """Seriale del SoC (Raspberry Pi / uConsole / ARM boards).
    Valore inciso nel silicio: immutabile anche dopo reinstallazioni,
    cambio hostname, MAC randomization, formattazione SD.
    Ritorna None se non disponibile (es. PC x86 desktop)."""
    if sys.platform == "win32":
        return None
    # Prova 1: device-tree (Raspberry Pi OS moderno)
    try:
        dt_path = "/sys/firmware/devicetree/base/serial-number"
        if os.path.exists(dt_path):
            with open(dt_path, "rb") as f:
                raw = f.read().rstrip(b"\x00").strip()
                s = raw.decode("ascii", errors="ignore").strip()
                if s and s != "0" * len(s):
                    return s.upper()
    except Exception:
        pass
    # Prova 2: /proc/cpuinfo -> riga "Serial : ..."
    try:
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.lower().startswith("serial"):
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            s = parts[1].strip()
                            if s and s != "0" * len(s):
                                return s.upper()
    except Exception:
        pass
    return None


def _get_disco_serial():
    """Numero seriale del volume C: (Windows) o root disk (Linux/Mac)."""
    try:
        if sys.platform == "win32":
            result = _subprocess.run(
                ["cmd", "/c", "vol", "C:"],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000
            )
            for line in result.stdout.split('\n'):
                line = line.strip()
                if '-' in line and len(line) >= 9:
                    parti = line.split()
                    for p in parti:
                        if len(p) == 9 and p[4] == '-':
                            return p.upper()
                lower = line.lower()
                if "serie" in lower or "serial" in lower:
                    token = line.split()[-1] if line.split() else ""
                    if token and len(token) >= 4:
                        return token.upper()
        else:
            if os.path.exists("/etc/machine-id"):
                with open("/etc/machine-id", "r") as f:
                    return f.read().strip()[:16].upper()
    except Exception:
        pass
    return "NO-DISK"


def get_hardware_id():
    """
    Genera un fingerprint hardware univoco.

    Strategia:
    - Se il sistema espone un seriale SoC stabile (uConsole / Raspberry Pi /
      board ARM con device-tree) lo usa da solo: e' inciso nel silicio e non
      cambia mai -> codice macchina davvero immutabile.
    - Altrimenti (tipicamente Windows/PC x86) usa il vecchio mix nome PC +
      seriale disco + MAC, che resta stabile abbastanza nella pratica.

    Ritorna una stringa hash esadecimale (SHA-256).
    """
    soc = _get_soc_serial()
    if soc:
        raw = "SOC|%s" % soc
    else:
        nome = _get_nome_pc()
        disco = _get_disco_serial()
        mac = _get_mac_address()
        raw = "%s|%s|%s" % (nome, disco, mac)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_codice_macchina():
    """
    Genera il codice macchina in formato leggibile: TM-XXXX-XXXX-XXXX
    Questo e' il codice che l'utente comunica allo sviluppatore.
    """
    hw_hash = get_hardware_id()
    short = hw_hash[:12].upper()
    return "TM-%s-%s-%s" % (short[0:4], short[4:8], short[8:12])


# ─────────────────────────────────────────────────────────────────────
#  ATTIVAZIONE LICENZA
# ─────────────────────────────────────────────────────────────────────

def genera_chiave(codice_macchina, data_scadenza="2099-12-31"):
    """
    Genera chiave di attivazione: XXXX-XXXX-XXXX-XXXX-XXXX
    Blocchi 1-4: hash (dipende da macchina + data)
    Blocco 5: data scadenza codificata (l'utente NON la vede)

    L'utente inserisce SOLO la chiave. La data e' nascosta dentro.
    """
    data_norm = _normalizza_data_iso(data_scadenza)

    # Hash con data inclusa (blocchi 1-4 cambiano se cambia la data)
    payload = "%s|%s" % (codice_macchina.upper().strip(), data_norm)
    raw = hashlib.sha256(
        _ACTIVATION_SECRET + payload.encode("utf-8")
    ).hexdigest()

    short = raw[:16].upper()
    date_block = _encode_date_block(data_norm, codice_macchina)

    return "%s-%s-%s-%s-%s" % (short[0:4], short[4:8], short[8:12], short[12:16], date_block)


# ── Codifica/decodifica data nel blocco 5 ──

_DATE_EPOCH = date(2025, 1, 1)
_UNLIMITED_DAYS = 0xFFFF   # Sentinella per licenza illimitata

def _encode_date_block(data_iso, codice_macchina):
    """Codifica una data ISO in un blocco hex di 4 caratteri."""
    d = _parse_data(data_iso)
    if d >= date(2099, 1, 1):
        days = _UNLIMITED_DAYS
    else:
        days = (d - _DATE_EPOCH).days
        days = max(0, min(days, 0xFFFE))  # 0xFFFF riservato

    # Chiave XOR derivata dal codice macchina
    date_key = hashlib.sha256(
        _ACTIVATION_SECRET + b"DATE" + codice_macchina.upper().strip().encode("utf-8")
    ).digest()[:2]

    encoded = days ^ (date_key[0] << 8 | date_key[1])
    return "%04X" % (encoded & 0xFFFF)


def _decode_date_block(block5, codice_macchina):
    """Decodifica un blocco hex di 4 caratteri in data ISO."""
    encoded = int(block5, 16)

    date_key = hashlib.sha256(
        _ACTIVATION_SECRET + b"DATE" + codice_macchina.upper().strip().encode("utf-8")
    ).digest()[:2]

    days = (encoded ^ (date_key[0] << 8 | date_key[1])) & 0xFFFF

    if days == _UNLIMITED_DAYS:
        return "2099-12-31"

    return (_DATE_EPOCH + timedelta(days=days)).isoformat()


def _normalizza_data_iso(data_str):
    """
    Normalizza qualsiasi formato data in ISO YYYY-MM-DD.
    Fondamentale: genera_chiave e verifica usano lo stesso formato.
    """
    data_str = str(data_str).strip()
    if not data_str:
        return "2099-12-31"
    try:
        d = _parse_data(data_str)
        return d.isoformat()  # Sempre YYYY-MM-DD
    except Exception:
        return "2099-12-31"


def verifica_attivazione(conf):
    """
    Verifica se il software e' attivato su questa macchina.
    Decodifica la data direttamente dalla chiave salvata.

    Ritorna:
        (attivato: bool, codice_macchina: str, messaggio: str)
    """
    codice_macchina = get_codice_macchina()

    # Aggiorna il codice macchina nella conf
    if conf.get("codice_macchina") != codice_macchina:
        conf["codice_macchina"] = codice_macchina
        salva_conf(conf)

    # ─ Licenza revocata dallo sviluppatore: blocco totale, prima di tutto ─
    if conf.get("licenza_revocata"):
        return False, codice_macchina, "LICENZA REVOCATA - Contattare il rivenditore"

    chiave_salvata = conf.get("chiave_attivazione", "").strip().upper()

    if not chiave_salvata:
        return False, codice_macchina, "Software non attivato"

    # Estrai blocchi (5 standard, 6 con opzioni)
    parti = chiave_salvata.split("-")
    if len(parti) not in (5, 6):
        # Retrocompatibilita': vecchio formato a 4 blocchi
        return _verifica_attivazione_legacy(conf, codice_macchina, chiave_salvata)

    # Decodifica data dal blocco 5
    try:
        data_decodificata = _decode_date_block(parti[4], codice_macchina)
    except Exception:
        return False, codice_macchina, "Chiave non valida"

    # Rigenera la chiave attesa e confronta
    chiave_attesa = genera_chiave(codice_macchina, data_decodificata)

    # Per chiavi a 6 blocchi, confronta con chiave completa
    valida = False
    if len(parti) == 6:
        try:
            opzioni = _decode_options_block(parti[5], codice_macchina)
            chiave_attesa_6 = genera_chiave_con_opzioni(codice_macchina, data_decodificata, opzioni)
            valida = (chiave_salvata == chiave_attesa_6.upper())
        except Exception:
            pass
    else:
        valida = (chiave_salvata == chiave_attesa.upper())

    if valida:
        # Sincronizza data_fine_licenza con quella nella chiave
        if conf.get("data_fine_licenza") != data_decodificata:
            conf["data_fine_licenza"] = data_decodificata
            salva_conf(conf)
        return True, codice_macchina, "Software attivato"

    return False, codice_macchina, "Chiave di attivazione non valida"


def _verifica_attivazione_legacy(conf, codice_macchina, chiave_salvata):
    """Retrocompatibilita' con chiavi vecchio formato (4 blocchi)."""
    data_scadenza = conf.get("data_fine_licenza", "2099-12-31")
    data_norm = _normalizza_data_iso(data_scadenza)
    payload = "%s|%s" % (codice_macchina.upper().strip(), data_norm)
    raw = hashlib.sha256(
        _ACTIVATION_SECRET + payload.encode("utf-8")
    ).hexdigest()
    short = raw[:16].upper()
    chiave_old = "%s-%s-%s-%s" % (short[0:4], short[4:8], short[8:12], short[12:16])
    if chiave_salvata == chiave_old.upper():
        return True, codice_macchina, "Software attivato (legacy)"
    # Prova illimitata
    if data_norm != "2099-12-31":
        payload2 = "%s|%s" % (codice_macchina.upper().strip(), "2099-12-31")
        raw2 = hashlib.sha256(_ACTIVATION_SECRET + payload2.encode("utf-8")).hexdigest()
        short2 = raw2[:16].upper()
        chiave_old2 = "%s-%s-%s-%s" % (short2[0:4], short2[4:8], short2[8:12], short2[12:16])
        if chiave_salvata == chiave_old2.upper():
            conf["data_fine_licenza"] = "2099-12-31"
            salva_conf(conf)
            return True, codice_macchina, "Software attivato (legacy)"
    return False, codice_macchina, "Chiave non valida"


def attiva_licenza(conf, chiave_inserita):
    """
    Attiva con la sola chiave. La data e' codificata DENTRO la chiave.
    L'utente non puo' manipolare la scadenza.

    Ritorna:
        (successo: bool, messaggio: str)
    """
    codice_macchina = get_codice_macchina()
    chiave_inserita = chiave_inserita.upper().strip()

    if not chiave_inserita:
        return False, "Inserisci una chiave di attivazione"

    # Controlla formato (5 o 6 blocchi)
    parti = chiave_inserita.split("-")
    if len(parti) not in (5, 6):
        return False, "Formato chiave non valido"

    # Decodifica data dal blocco 5
    try:
        data_decodificata = _decode_date_block(parti[4], codice_macchina)
    except Exception:
        return False, "Chiave non valida"

    # Rigenera e confronta (solo i primi 5 blocchi)
    chiave_attesa = genera_chiave(codice_macchina, data_decodificata)

    # Per chiavi a 6 blocchi, verifica anche il blocco opzioni
    if len(parti) == 6:
        try:
            opzioni = _decode_options_block(parti[5], codice_macchina)
            chiave_attesa_6 = genera_chiave_con_opzioni(codice_macchina, data_decodificata, opzioni)
            if chiave_inserita != chiave_attesa_6.upper():
                return False, "Chiave non valida per questa macchina"
        except Exception:
            return False, "Chiave non valida"
    elif chiave_inserita != chiave_attesa.upper():
        return False, "Chiave non valida per questa macchina"

    # Chiave valida - salva e attiva
    conf["chiave_attivazione"] = chiave_inserita
    conf["codice_macchina"] = codice_macchina
    conf["data_fine_licenza"] = data_decodificata
    salva_conf(conf)
    if data_decodificata == "2099-12-31":
        return True, "Attivazione completata! Licenza illimitata"
    return True, "Attivazione completata! Scadenza: %s" % data_decodificata


# ─────────────────────────────────────────────────────────────────────
#  OPZIONI LICENZA (blocco 6 - opzionale)
# ─────────────────────────────────────────────────────────────────────
# Bit flags per opzioni
OPT_LAPTIMER = 0x01

def _encode_options_block(opzioni, codice_macchina):
    """Codifica flags opzioni in un blocco hex di 4 caratteri."""
    opt_key = hashlib.sha256(
        _ACTIVATION_SECRET + b"OPTS" + codice_macchina.upper().strip().encode("utf-8")
    ).digest()[:2]
    encoded = opzioni ^ (opt_key[0] << 8 | opt_key[1])
    return "%04X" % (encoded & 0xFFFF)


def _decode_options_block(block6, codice_macchina):
    """Decodifica un blocco hex di 4 caratteri in flags opzioni."""
    encoded = int(block6, 16)
    opt_key = hashlib.sha256(
        _ACTIVATION_SECRET + b"OPTS" + codice_macchina.upper().strip().encode("utf-8")
    ).digest()[:2]
    return (encoded ^ (opt_key[0] << 8 | opt_key[1])) & 0xFFFF


def genera_chiave_con_opzioni(codice_macchina, data_scadenza="2099-12-31", opzioni=0):
    """
    Genera chiave a 6 blocchi: XXXX-XXXX-XXXX-XXXX-XXXX-XXXX
    Blocchi 1-5: come prima (macchina + data)
    Blocco 6: opzioni codificate (solo se opzioni > 0)
    """
    chiave_base = genera_chiave(codice_macchina, data_scadenza)
    if opzioni == 0:
        return chiave_base  # 5 blocchi standard
    opt_block = _encode_options_block(opzioni, codice_macchina)
    return "%s-%s" % (chiave_base, opt_block)


def hash_chiave_attivazione(chiave):
    """
    Hash SHA-256 di una chiave di attivazione, usato per confronti anonimi
    (es. lista revoche pubblica). Normalizza maiuscolo/spazi prima.
    """
    norm = (chiave or "").strip().upper()
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def applica_revoca(conf, motivo=""):
    """
    Applica il flag di revoca alla conf e svuota la chiave di attivazione,
    in modo che il prossimo login mostri "LICENZA REVOCATA".
    Idempotente: se gia' revocata non rifa' niente.
    """
    if conf.get("licenza_revocata"):
        return False
    conf["licenza_revocata"] = date.today().isoformat()
    conf["motivo_revoca"] = motivo or "Licenza revocata"
    conf["chiave_attivazione"] = ""
    salva_conf(conf)
    return True


def ha_opzione_laptimer(conf):
    """Verifica se la licenza include l'opzione LapTimer."""
    codice_macchina = get_codice_macchina()
    chiave = conf.get("chiave_attivazione", "").strip().upper()
    if not chiave:
        return False

    parti = chiave.split("-")
    if len(parti) != 6:
        return False  # chiave a 5 blocchi = no opzioni

    # Verifica che la chiave base sia valida
    attivato, _, _ = verifica_attivazione(conf)
    if not attivato:
        return False

    # Decodifica opzioni dal blocco 6
    try:
        opzioni = _decode_options_block(parti[5], codice_macchina)
        return bool(opzioni & OPT_LAPTIMER)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────
#  TOKEN LAPTIMER (anti-lancio standalone)
# ─────────────────────────────────────────────────────────────────────
_LAPTIMER_TOKEN_SECRET = b"TrKm1nd_L4pT1m3r_T0k3n!!"

def genera_token_laptimer():
    """Genera un token temporaneo per il lancio del laptimer."""
    import time
    minuto = str(int(time.time()) // 60)
    raw = hashlib.sha256(
        _LAPTIMER_TOKEN_SECRET + minuto.encode("utf-8")
    ).hexdigest()
    return raw[:16].upper()


def verifica_token_laptimer(token):
    """Verifica che il token sia valido (finestra di 2 minuti)."""
    import time
    token = token.strip().upper()
    now_min = int(time.time()) // 60
    for offset in (0, -1):
        minuto = str(now_min + offset)
        raw = hashlib.sha256(
            _LAPTIMER_TOKEN_SECRET + minuto.encode("utf-8")
        ).hexdigest()
        if token == raw[:16].upper():
            return True
    return False


# ─────────────────────────────────────────────────────────────────────
#  CREDITI IA
# ─────────────────────────────────────────────────────────────────────
# Segreto per la firma dei codici di ricarica IA
_RICARICA_SECRET = b"TrKm1nd_R1c4r1c4_1A_2026!!"


def crediti_ia_rimasti(conf):
    """Ritorna il numero di crediti IA rimasti."""
    try:
        return int(conf.get("crediti_ia", 500))
    except (ValueError, TypeError):
        return 500


def usa_credito_ia(conf):
    """Scala 1 credito IA. Ritorna (ok: bool, rimasti: int).
    Se crediti <= 0, ritorna (False, 0) senza scalare."""
    rimasti = crediti_ia_rimasti(conf)
    if rimasti <= 0:
        return False, 0
    rimasti -= 1
    conf["crediti_ia"] = rimasti
    salva_conf(conf)
    return True, rimasti


def genera_codice_ricarica(codice_macchina, crediti):
    """
    Genera un codice di ricarica IA firmato.
    Formato: RIA-XXXX-XXXX-CCCC
      RIA = prefisso fisso (Ricarica IA)
      XXXX-XXXX = hash legato a macchina + crediti
      CCCC = crediti codificati (XOR con chiave derivata)
    """
    crediti = max(1, min(crediti, 9999))
    cm = codice_macchina.upper().strip()

    # Hash: macchina + crediti + segreto
    payload = "%s|%d" % (cm, crediti)
    raw = hashlib.sha256(
        _RICARICA_SECRET + payload.encode("utf-8")
    ).hexdigest()
    short = raw[:8].upper()

    # Crediti codificati con XOR
    ric_key = hashlib.sha256(
        _RICARICA_SECRET + b"CRED" + cm.encode("utf-8")
    ).digest()[:2]
    cred_enc = crediti ^ (ric_key[0] << 8 | ric_key[1])

    return "RIA-%s-%s-%04X" % (short[0:4], short[4:8], cred_enc & 0xFFFF)


def applica_ricarica_ia(conf, codice_ricarica):
    """
    Verifica e applica un codice di ricarica IA.
    Ritorna (successo: bool, messaggio: str, crediti_aggiunti: int)
    """
    codice_ricarica = codice_ricarica.upper().strip()

    if not codice_ricarica.startswith("RIA-"):
        return False, "Codice non valido", 0

    parti = codice_ricarica.split("-")
    if len(parti) != 4:
        return False, "Formato codice non valido", 0

    codice_macchina = get_codice_macchina()
    cm = codice_macchina.upper().strip()

    # Decodifica crediti dal blocco 4
    try:
        cred_enc = int(parti[3], 16)
        ric_key = hashlib.sha256(
            _RICARICA_SECRET + b"CRED" + cm.encode("utf-8")
        ).digest()[:2]
        crediti = (cred_enc ^ (ric_key[0] << 8 | ric_key[1])) & 0xFFFF
    except Exception:
        return False, "Codice non valido", 0

    if crediti < 1 or crediti > 9999:
        return False, "Codice non valido per questa macchina", 0

    # Verifica hash (macchina + crediti)
    payload = "%s|%d" % (cm, crediti)
    raw = hashlib.sha256(
        _RICARICA_SECRET + payload.encode("utf-8")
    ).hexdigest()
    short = raw[:8].upper()
    hash_atteso = "%s-%s" % (short[0:4], short[4:8])
    hash_ricevuto = "%s-%s" % (parti[1], parti[2])

    if hash_ricevuto != hash_atteso:
        return False, "Codice non valido per questa macchina", 0

    # Controlla se il codice e' gia' stato usato
    usati = conf.get("_ricariche_usate", [])
    if codice_ricarica in usati:
        return False, "Codice gia' utilizzato", 0

    # Applica ricarica
    rimasti = crediti_ia_rimasti(conf)
    conf["crediti_ia"] = rimasti + crediti
    # Salva codice come usato (max ultimi 50)
    usati.append(codice_ricarica)
    conf["_ricariche_usate"] = usati[-50:]
    salva_conf(conf)

    return True, "Ricarica completata! +%d crediti (%d totali)" % (crediti, rimasti + crediti), crediti
