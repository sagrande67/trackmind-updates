"""
Updater v2.0 - Sistema aggiornamento RetroDB/TrackMind
Supporta: USB (locale), GitHub (remoto via raw.githubusercontent.com)

Formato update (USB):
  update_vX.Y.zip
    ├── version.json    {"version":"05.04.1", "date":"2026-04-10", "note":"..."}
    └── *.py            Solo file codice applicazione

Formato update (GitHub):
  Repository: SAGRANDE67/trackmind-updates
    ├── version.json    {"version":"05.04.1", "date":"...", "note":"...", "files":{"retrodb.py":"root","crono.py":"addons"}}
    ├── retrodb.py      File aggiornati nella root del repo
    ├── addons/
    │   └── crono.py    File in sottocartelle corrispondenti
    └── core/
        └── auth.py

  In version.json, "files" mappa nome_file -> cartella_destinazione:
    "root"   = directory principale dell'app
    "addons" = sottocartella addons/
    "core"   = sottocartella core/

MAI inclusi: conf.dat, colori.cfg, dati/, backup/
Eccezione: tabelle/conf.def e' incluso (definizione configurazione sistema)
"""

from version import __version__

import os, sys, json, shutil, zipfile
import urllib.request
import ssl
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────
#  CONFIGURAZIONE GITHUB
# ─────────────────────────────────────────────────────────────────────
GITHUB_USER = "sagrande67"
GITHUB_REPO = "trackmind-updates"
GITHUB_BRANCH = "main"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/%s/%s/%s" % (
    GITHUB_USER, GITHUB_REPO, GITHUB_BRANCH)

# File che fanno parte dell'applicazione (aggiornabili)
# Esclusi: file che iniziano con _ (script temporanei), __pycache__
def _is_app_file(filename):
    """Verifica se un file .py fa parte dell'applicazione (distribuibile).

    Esclude:
      - _*.py          (utility private: _test_email.py, _setup_smtp.py, ...)
      - genera_*.py    (generatori licenze/segreti, mai distribuire)
      - test_*.py      (test manuali di sviluppo)
      - sync_*.py      (utility one-shot di sincronizzazione cataloghi)
      - '(copia)' nel nome (backup accidentali creati da Windows Explorer)
    """
    if not filename.endswith(".py"):
        return False
    nome_low = filename.lower()
    if nome_low.startswith("_"):
        return False
    if nome_low.startswith("genera_"):
        return False
    if nome_low.startswith("test_"):
        return False
    if nome_low.startswith("sync_"):
        return False
    if "(copia)" in nome_low or "(copy)" in nome_low:
        return False
    return True


def get_app_files(base_dir):
    """Lista dei file .py applicazione nella directory base (solo root)."""
    files = []
    for f in sorted(os.listdir(base_dir)):
        if _is_app_file(f):
            full = os.path.join(base_dir, f)
            if os.path.isfile(full):
                files.append(f)
    return files


# Cartelle dell'applicazione che fanno parte degli aggiornamenti.
# Stesso schema usato dal canale GitHub (version.json -> files: {nome: cartella}).
APP_FOLDERS = ["root", "addons", "core"]

# File extra non-.py da includere negli aggiornamenti.
# Chiave = cartella logica, valore = lista nomi file.
# - conf.def: solo questo viene distribuito, le altre .def sono
#   personalizzabili dal cliente
# - logo.png: logo di default di TrackMind spedito con l'app; i
#   rivenditori possono comunque sovrascriverlo localmente mettendo il
#   proprio PNG in dati/loghi/ (non toccato dagli aggiornamenti).
APP_EXTRA_FILES = {
    "tabelle": ["conf.def"],
    "loghi":   ["logo.png"],
}

# Tutte le cartelle valide: APP_FOLDERS + chiavi APP_EXTRA_FILES
APP_VALID_FOLDERS = set(APP_FOLDERS) | set(APP_EXTRA_FILES.keys())


def _scan_folder(base_dir, folder):
    """Lista .py applicazione di una cartella logica.

    folder: "root" -> base_dir, "addons" -> base_dir/addons, "core" -> base_dir/core
    Ritorna lista di nomi file (senza percorso).
    """
    if folder == "root":
        target = base_dir
    else:
        target = os.path.join(base_dir, folder)
    if not os.path.isdir(target):
        return []
    out = []
    try:
        for f in sorted(os.listdir(target)):
            if not _is_app_file(f):
                continue
            full = os.path.join(target, f)
            if os.path.isfile(full):
                out.append(f)
    except OSError:
        pass
    return out


def get_app_files_full(base_dir):
    """Lista completa file aggiornabili come dict {nome_file: cartella_logica}.

    Esempio: {"retrodb.py": "root", "crono.py": "addons", "conf.def": "tabelle"}
    Stesso formato usato in version.json del canale GitHub.
    Include anche i file extra definiti in APP_EXTRA_FILES (es. conf.def).
    """
    mappa = {}
    for cart in APP_FOLDERS:
        for nome in _scan_folder(base_dir, cart):
            # Se per qualche motivo lo stesso nome esistesse in piu' cartelle,
            # vince root > addons > core (improbabile, ma deterministico).
            if nome not in mappa:
                mappa[nome] = cart
    # File extra non-.py (conf.def ecc.)
    for cart, nomi in APP_EXTRA_FILES.items():
        cart_path = os.path.join(base_dir, cart) if cart != "root" else base_dir
        for nome in nomi:
            full = os.path.join(cart_path, nome)
            if os.path.isfile(full) and nome not in mappa:
                mappa[nome] = cart
    return mappa


def _path_in_zip(nome, cart):
    """Percorso interno allo zip per un file della cartella logica indicata."""
    if cart == "root":
        return nome
    return "%s/%s" % (cart, nome)


def _path_in_fs(base_dir, nome, cart):
    """Percorso filesystem reale per un file della cartella logica indicata."""
    if cart == "root":
        return os.path.join(base_dir, nome)
    return os.path.join(base_dir, cart, nome)


# ─────────────────────────────────────────────────────────────────────
#  PREPARA AGGIORNAMENTO (lato sviluppatore)
# ─────────────────────────────────────────────────────────────────────

def prepara_aggiornamento(dest_dir, app_version, base_dir, note=""):
    """
    Crea zip di aggiornamento con i file .py di root + addons/ + core/ + version.json.

    Lo zip e' organizzato per cartelle logiche (stesso schema del canale GitHub):
      update_vX.Y.zip
        version.json   {"files": {"retrodb.py":"root", "crono.py":"addons", ...}}
        retrodb.py             <- root
        addons/crono.py
        core/auth.py

    Args:
        dest_dir: cartella di destinazione (USB o locale)
        app_version: versione corrente (es. "5.4")
        base_dir: directory sorgente del progetto
        note: note di rilascio opzionali

    Returns:
        (successo: bool, messaggio: str, percorso_zip: str)
    """
    try:
        files_map = get_app_files_full(base_dir)
        if not files_map:
            return False, "Nessun file .py trovato in %s" % base_dir, ""

        zip_name = "update_v%s.zip" % app_version
        zip_path = os.path.join(dest_dir, zip_name)

        # Leggi la lista hash delle licenze revocate dal registro locale
        # (best-effort: se il modulo non e' disponibile si pubblica senza revoche)
        revoche_list = []
        try:
            import sys as _sys
            dev_dir = os.path.join(base_dir, "dev")
            if os.path.isdir(dev_dir) and dev_dir not in _sys.path:
                _sys.path.insert(0, dev_dir)
            from genera_licenza import lista_hash_revocate  # type: ignore
            revoche_list = lista_hash_revocate() or []
        except Exception:
            revoche_list = []

        version_info = {
            "version": app_version,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M:%S"),
            "note": note,
            "files": files_map,
            "revoche": revoche_list,
        }

        os.makedirs(dest_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # version.json
            zf.writestr("version.json",
                        json.dumps(version_info, indent=2, ensure_ascii=False))
            # File .py: percorso nello zip rispecchia la cartella logica
            for nome, cart in files_map.items():
                src = _path_in_fs(base_dir, nome, cart)
                if os.path.isfile(src):
                    zf.write(src, _path_in_zip(nome, cart))

        size_kb = os.path.getsize(zip_path) // 1024
        # Riepilogo: numero file totale + breakdown per cartella
        per_cart = {}
        for c in files_map.values():
            per_cart[c] = per_cart.get(c, 0) + 1
        # Ordine: prima APP_FOLDERS, poi le extra (tabelle ecc.)
        ordine = list(APP_FOLDERS) + sorted(k for k in per_cart if k not in APP_FOLDERS)
        breakdown = ", ".join("%s:%d" % (k, per_cart[k])
                              for k in ordine if k in per_cart)
        return (True,
                "%d file (%s) -> %s (%d KB)" % (len(files_map), breakdown,
                                                zip_name, size_kb),
                zip_path)

    except Exception as e:
        return False, "Errore: %s" % e, ""


# ─────────────────────────────────────────────────────────────────────
#  PREPARA AGGIORNAMENTO PER REPOSITORY GITHUB (lato sviluppatore)
# ─────────────────────────────────────────────────────────────────────

def prepara_aggiornamento_github(dest_dir, app_version, base_dir, note=""):
    """
    Prepara i file per il repository GitHub (niente zip).

    Scrive direttamente dentro dest_dir la stessa struttura che il canale
    GitHub si aspetta:
      dest_dir/
        version.json          {"files": {"retrodb.py":"root", ...}}
        retrodb.py            <- root
        addons/crono.py
        core/auth.py
        tabelle/conf.def

    Dopo l'esecuzione basta fare 'git add . && git commit && git push' dalla
    cartella dest_dir per pubblicare l'aggiornamento.

    I file che nel repo non sono piu' presenti nella app sorgente NON vengono
    cancellati automaticamente (lo lasciamo fare a git, cosi' se l'utente si
    accorge puo' scegliere se mantenerli o no).

    Args:
        dest_dir: cartella locale del repo GitHub (es. C:\\dev\\trackmind-updates)
        app_version: versione corrente (da version.py)
        base_dir: directory sorgente del progetto
        note: note di rilascio opzionali

    Returns:
        (successo: bool, messaggio: str, percorso_dest: str)
    """
    try:
        files_map = get_app_files_full(base_dir)
        if not files_map:
            return False, "Nessun file .py trovato in %s" % base_dir, ""

        if not os.path.isdir(dest_dir):
            return False, "Cartella destinazione non esiste: %s" % dest_dir, ""

        # Leggi la lista hash delle licenze revocate dal registro locale
        # (best-effort: se il modulo non e' disponibile si pubblica senza revoche)
        revoche_list = []
        try:
            import sys as _sys
            dev_dir = os.path.join(base_dir, "dev")
            if os.path.isdir(dev_dir) and dev_dir not in _sys.path:
                _sys.path.insert(0, dev_dir)
            from genera_licenza import lista_hash_revocate  # type: ignore
            revoche_list = lista_hash_revocate() or []
        except Exception:
            revoche_list = []

        version_info = {
            "version": app_version,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M:%S"),
            "note": note,
            "files": files_map,
            "revoche": revoche_list,
        }

        # 1. Scrivi version.json nella root del repo (scrittura atomica)
        version_path = os.path.join(dest_dir, "version.json")
        tmp_vpath = version_path + ".tmp"
        with open(tmp_vpath, "w", encoding="utf-8") as f:
            json.dump(version_info, f, indent=2, ensure_ascii=False)
        os.replace(tmp_vpath, version_path)

        # 2. Copia ogni file nella struttura attesa dal repo
        copiati = 0
        errori = []
        for nome, cart in files_map.items():
            src = _path_in_fs(base_dir, nome, cart)
            if not os.path.isfile(src):
                continue
            # Destinazione nel repo: root/ o sottocartella
            if cart == "root":
                dst_dir = dest_dir
            else:
                dst_dir = os.path.join(dest_dir, cart)
                os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, nome)
            tmp_dst = dst + ".tmp"
            try:
                shutil.copyfile(src, tmp_dst)
                os.replace(tmp_dst, dst)
                copiati += 1
            except Exception as e:
                errori.append("%s: %s" % (nome, e))
                try:
                    os.remove(tmp_dst)
                except OSError:
                    pass

        # Riepilogo per cartella
        per_cart = {}
        for c in files_map.values():
            per_cart[c] = per_cart.get(c, 0) + 1
        ordine = list(APP_FOLDERS) + sorted(k for k in per_cart if k not in APP_FOLDERS)
        breakdown = ", ".join("%s:%d" % (k, per_cart[k])
                              for k in ordine if k in per_cart)

        msg = "%d file copiati (%s) in %s" % (copiati, breakdown, dest_dir)
        if errori:
            msg += " - %d errori: %s" % (len(errori), ", ".join(errori[:3]))
        return True, msg, dest_dir

    except Exception as e:
        return False, "Errore: %s" % e, ""


# ─────────────────────────────────────────────────────────────────────
#  CERCA AGGIORNAMENTO SU USB
# ─────────────────────────────────────────────────────────────────────

def _trova_unita_usb():
    """Trova unita' rimovibili (USB) disponibili."""
    unita = []
    if sys.platform == "win32":
        # Windows: cerca drive rimovibili
        try:
            import ctypes
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for i in range(26):
                if bitmask & (1 << i):
                    lettera = chr(ord('A') + i)
                    path = "%s:\\" % lettera
                    # 2 = DRIVE_REMOVABLE
                    tipo = ctypes.windll.kernel32.GetDriveTypeW(path)
                    if tipo == 2:  # Rimovibile (USB)
                        try:
                            label = ""
                            buf = ctypes.create_unicode_buffer(256)
                            if ctypes.windll.kernel32.GetVolumeInformationW(
                                    path, buf, 256, None, None, None, None, 0):
                                label = buf.value
                            unita.append({
                                "path": path,
                                "label": label or "USB (%s:)" % lettera,
                                "lettera": lettera,
                            })
                        except:
                            unita.append({"path": path, "label": "USB (%s:)" % lettera, "lettera": lettera})
        except:
            pass
    else:
        # Linux: cerca in /media/$USER/ e /mnt/
        username = os.environ.get("USER", "")
        search_dirs = []
        if username:
            search_dirs.append("/media/%s" % username)
        search_dirs.extend(["/media", "/mnt"])

        for search in search_dirs:
            if os.path.isdir(search):
                try:
                    for d in os.listdir(search):
                        full = os.path.join(search, d)
                        if os.path.isdir(full) and os.path.ismount(full):
                            unita.append({
                                "path": full,
                                "label": d,
                            })
                except:
                    pass
    return unita


def cerca_aggiornamento_usb():
    """
    Cerca file update_v*.zip su tutte le unita' USB.

    Returns:
        lista di dict: [{path, version, date, note, usb_label}, ...]
    """
    risultati = []
    unita = _trova_unita_usb()

    for usb in unita:
        usb_path = usb["path"]
        # Cerca nella root e in sottocartelle comuni
        search_paths = [usb_path]
        for sub in ["TrackMind", "RetroDB", "update", "aggiornamento"]:
            sub_path = os.path.join(usb_path, sub)
            if os.path.isdir(sub_path):
                search_paths.append(sub_path)

        for sp in search_paths:
            try:
                for f in os.listdir(sp):
                    if f.startswith("update_v") and f.endswith(".zip"):
                        zip_path = os.path.join(sp, f)
                        info = _leggi_version_json(zip_path)
                        if info:
                            info["zip_path"] = zip_path
                            info["usb_label"] = usb.get("label", "USB")
                            risultati.append(info)
            except:
                pass

    # Ordina per versione decrescente
    risultati.sort(key=lambda x: x.get("version", "0"), reverse=True)
    return risultati


def _leggi_version_json(zip_path):
    """Legge version.json da un file zip. Ritorna dict o None."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if "version.json" in zf.namelist():
                data = json.loads(zf.read("version.json"))
                return {
                    "version": data.get("version", "?"),
                    "date": data.get("date", "?"),
                    "note": data.get("note", ""),
                    "files": data.get("files", []),
                }
    except:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────
#  VERIFICA E APPLICA AGGIORNAMENTO
# ─────────────────────────────────────────────────────────────────────

def _confronta_versioni(v_locale, v_remota):
    """Confronta versioni (es. '5.4' vs '5.5'). Ritorna True se remota > locale.

    Se remota == locale NON propone l'aggiornamento: per ripubblicare
    un hotfix e' comunque necessario bumpare il numero di versione.
    """
    try:
        def _parse(v):
            return [int(x) for x in str(v).split(".")]
        return _parse(v_remota) > _parse(v_locale)
    except:
        return str(v_remota) > str(v_locale)


def verifica_aggiornamento(zip_path, app_version):
    """
    Verifica se lo zip contiene un aggiornamento valido.

    Returns:
        (valido: bool, info: dict, messaggio: str)
    """
    info = _leggi_version_json(zip_path)
    if not info:
        return False, {}, "File zip non valido o version.json mancante"

    if not _confronta_versioni(app_version, info["version"]):
        return False, info, "Versione %s anteriore a %s" % (info["version"], app_version)

    # Verifica che contenga file aggiornabili (.py o file extra)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            app_files = [f for f in zf.namelist()
                         if f != "version.json"]
            if not app_files:
                return False, info, "Nessun file aggiornabile nello zip"
            info["files"] = app_files
    except:
        return False, info, "Errore lettura zip"

    return True, info, "Aggiornamento v%s disponibile (%d file)" % (info["version"], len(info["files"]))


def applica_aggiornamento(zip_path, base_dir, backup_dir=None):
    """
    Applica l'aggiornamento: backup vecchi .py, estrai nuovi.

    Supporta due formati di zip:
      - NUOVO (multi-cartella): version.json -> "files": {"nome.py": "root|addons|core"}
        i file dentro lo zip sono organizzati come "addons/crono.py" ecc.
      - VECCHIO (flat): version.json -> "files": ["a.py","b.py", ...]
        tutti i file .py al primo livello dello zip, finiscono in root.

    Args:
        zip_path: percorso dello zip aggiornamento
        base_dir: directory dell'applicazione
        backup_dir: directory backup (default: base_dir/backup)

    Returns:
        (successo: bool, messaggio: str, file_aggiornati: list)
    """
    if not backup_dir:
        backup_dir = os.path.join(base_dir, "backup")

    try:
        # 1. Leggi info
        info = _leggi_version_json(zip_path)
        if not info:
            return False, "version.json mancante", []

        # 2. Backup dei .py correnti di TUTTE le cartelle gestite
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bk_name = "pre_update_%s_%s.zip" % (info["version"], ts)
        bk_path = os.path.join(backup_dir, bk_name)
        os.makedirs(backup_dir, exist_ok=True)

        with zipfile.ZipFile(bk_path, "w", zipfile.ZIP_DEFLATED) as bk:
            # Backup .py di root, addons, core
            for cart in APP_FOLDERS:
                cart_path = base_dir if cart == "root" else os.path.join(base_dir, cart)
                if not os.path.isdir(cart_path):
                    continue
                try:
                    for f in os.listdir(cart_path):
                        if not f.endswith(".py"):
                            continue
                        full = os.path.join(cart_path, f)
                        if os.path.isfile(full):
                            bk.write(full, _path_in_zip(f, cart))
                except OSError:
                    pass
            # Backup file extra (conf.def ecc.)
            for cart, nomi in APP_EXTRA_FILES.items():
                cart_path = base_dir if cart == "root" else os.path.join(base_dir, cart)
                for nome in nomi:
                    full = os.path.join(cart_path, nome)
                    if os.path.isfile(full):
                        try:
                            bk.write(full, _path_in_zip(nome, cart))
                        except OSError:
                            pass

        # 3. Determina la mappa file -> cartella
        files_field = info.get("files", None)
        files_map = {}
        if isinstance(files_field, dict):
            # Formato nuovo: la mappa e' gia' nel version.json
            files_map = dict(files_field)
        else:
            # Formato vecchio (lista o assente): scansiona lo zip e tratta
            # i file in root come "root", quelli in addons/<x>.py come "addons", ecc.
            with zipfile.ZipFile(zip_path, "r") as zf_scan:
                for nome in zf_scan.namelist():
                    if nome == "version.json":
                        continue
                    if "/" not in nome:
                        files_map[nome] = "root"
                    else:
                        cart, sub = nome.split("/", 1)
                        if cart in APP_VALID_FOLDERS and "/" not in sub:
                            files_map[sub] = cart

        # 4. Estrai i nuovi .py rispettando la mappa (scrittura atomica)
        aggiornati = []
        with zipfile.ZipFile(zip_path, "r") as zf:
            nomi_zip = set(zf.namelist())
            for nome, cart in files_map.items():
                if cart not in APP_VALID_FOLDERS:
                    continue
                arc = _path_in_zip(nome, cart)
                if arc not in nomi_zip:
                    # Tollerante: se il file non c'e' nello zip lo salto.
                    continue
                dest = _path_in_fs(base_dir, nome, cart)
                dest_parent = os.path.dirname(dest)
                if dest_parent:
                    os.makedirs(dest_parent, exist_ok=True)
                tmp = dest + ".tmp"
                try:
                    with zf.open(arc) as src, open(tmp, "wb") as dst:
                        dst.write(src.read())
                    os.replace(tmp, dest)
                    aggiornati.append(arc)
                except Exception:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

        msg = "Aggiornato a v%s: %d file (%s)" % (
            info["version"], len(aggiornati), ", ".join(aggiornati[:5]))
        if len(aggiornati) > 5:
            msg += " +%d altri" % (len(aggiornati) - 5)
        msg += "\nBackup: %s" % bk_name

        return True, msg, aggiornati

    except Exception as e:
        return False, "Errore aggiornamento: %s" % e, []


def riavvia_app():
    """Riavvia l'applicazione corrente."""
    python = sys.executable
    script = os.path.abspath(sys.argv[0])
    os.execv(python, [python, script])


# ─────────────────────────────────────────────────────────────────────
#  AGGIORNAMENTO VIA GITHUB (remoto)
# ─────────────────────────────────────────────────────────────────────

def _github_url(path):
    """Costruisce URL completo per un file nel repo GitHub."""
    return "%s/%s" % (GITHUB_RAW_BASE, path)


def _scarica_url(url, timeout=15):
    """Scarica contenuto da URL. Ritorna bytes o None."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={
            "User-Agent": "TrackMind/%s" % __version__,
        })
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        return resp.read()
    except Exception:
        return None


def _applica_revoche_da_info(info):
    """
    Se il version.json remoto contiene un campo 'revoche' (lista di SHA-256
    delle chiavi revocate dallo sviluppatore), confronta con l'hash della
    chiave di attivazione locale. Se c'e' match, segna la licenza come
    revocata nel conf.dat: al prossimo avvio il login mostra
    'LICENZA REVOCATA'.

    Ritorna True se la revoca e' stata applicata in questo giro.
    Best-effort: qualsiasi errore non blocca il controllo aggiornamenti.
    """
    try:
        revoche = info.get("revoche") or []
        if not revoche:
            return False
        # Import locale per evitare import circolari a livello di modulo
        import sys as _sys
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if base not in _sys.path:
            _sys.path.insert(0, base)
        from conf_manager import carica_conf, hash_chiave_attivazione, applica_revoca
        conf = carica_conf()
        chiave = conf.get("chiave_attivazione", "")
        if not chiave:
            return False
        h_local = hash_chiave_attivazione(chiave)
        revoche_norm = {str(x).strip().lower() for x in revoche}
        if h_local.lower() in revoche_norm:
            applica_revoca(conf, motivo="Revoca via aggiornamento")
            return True
    except Exception:
        pass
    return False


def controlla_aggiornamento_github(app_version):
    """
    Controlla se esiste un aggiornamento su GitHub.

    Effetto collaterale: se il version.json remoto contiene un elenco di
    hash di chiavi revocate e la chiave locale ne fa parte, la licenza
    viene segnata come revocata nel conf.dat (blocco al prossimo avvio).

    Args:
        app_version: versione corrente (es. "05.04.5")

    Returns:
        (disponibile: bool, info: dict, messaggio: str)
        info contiene: version, date, note, files (dict nome->cartella)
    """
    url = _github_url("version.json")
    data = _scarica_url(url)
    if not data:
        return False, {}, "Impossibile contattare il server aggiornamenti"

    try:
        info = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False, {}, "version.json non valido"

    # Check revoche PRIMA del confronto versione: cosi' anche se non c'e'
    # un update disponibile la revoca scatta comunque.
    _applica_revoche_da_info(info)

    v_remota = info.get("version", "0")
    if not _confronta_versioni(app_version, v_remota):
        return False, info, "Versione corrente (%s) gia' aggiornata" % app_version

    n_files = len(info.get("files", {}))
    return True, info, "Aggiornamento v%s disponibile (%d file)" % (v_remota, n_files)


def scarica_aggiornamento_github(info, base_dir, backup_dir=None, callback=None):
    """
    Scarica e applica aggiornamento da GitHub.

    Args:
        info: dict da controlla_aggiornamento_github (con "files" e "version")
        base_dir: directory principale dell'app
        backup_dir: directory backup (default: base_dir/backup)
        callback: funzione(messaggio) per aggiornare UI durante download

    Returns:
        (successo: bool, messaggio: str, file_aggiornati: list)
    """
    if not backup_dir:
        backup_dir = os.path.join(base_dir, "backup")

    files = info.get("files", {})
    if not files:
        return False, "Nessun file da aggiornare", []

    versione = info.get("version", "?")

    # Mappa cartelle: nome logico -> percorso reale
    cartelle = {
        "root": base_dir,
        "addons": os.path.join(base_dir, "addons"),
        "core": os.path.join(base_dir, "core"),
        "dev": os.path.join(base_dir, "dev"),
        "tabelle": os.path.join(base_dir, "tabelle"),
        "loghi": os.path.join(base_dir, "loghi"),
    }

    try:
        # 1. Backup dei .py correnti
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bk_name = "pre_update_%s_%s.zip" % (versione, ts)
        bk_path = os.path.join(backup_dir, bk_name)
        os.makedirs(backup_dir, exist_ok=True)

        with zipfile.ZipFile(bk_path, "w", zipfile.ZIP_DEFLATED) as bk:
            for cart_nome, cart_path in cartelle.items():
                if not os.path.isdir(cart_path):
                    continue
                for f in os.listdir(cart_path):
                    if f.endswith(".py"):
                        full = os.path.join(cart_path, f)
                        arc_name = f if cart_nome == "root" else "%s/%s" % (cart_nome, f)
                        bk.write(full, arc_name)
            # Backup file extra (conf.def ecc.)
            for cart, nomi in APP_EXTRA_FILES.items():
                cart_path = cartelle.get(cart)
                if not cart_path:
                    continue
                for nome in nomi:
                    full = os.path.join(cart_path, nome)
                    if os.path.isfile(full):
                        arc_name = "%s/%s" % (cart, nome)
                        try:
                            bk.write(full, arc_name)
                        except OSError:
                            pass

        if callback:
            callback("Backup creato: %s" % bk_name)

        # 2. Scarica ogni file dal repo GitHub
        aggiornati = []
        errori = []
        totale = len(files)

        for i, (nome_file, cartella) in enumerate(files.items(), 1):
            if callback:
                callback("Scaricando %d/%d: %s" % (i, totale, nome_file))

            # Costruisci URL: se cartella e' "root", il file e' nella root del repo
            if cartella == "root":
                url_file = _github_url(nome_file)
            else:
                url_file = _github_url("%s/%s" % (cartella, nome_file))

            contenuto = _scarica_url(url_file)
            if not contenuto:
                errori.append(nome_file)
                continue

            # Scrivi il file nella cartella corretta (scrittura atomica)
            dest_dir = cartelle.get(cartella, base_dir)
            dest_path = os.path.join(dest_dir, nome_file)
            tmp_path = dest_path + ".tmp"

            try:
                os.makedirs(dest_dir, exist_ok=True)
                with open(tmp_path, "wb") as f:
                    f.write(contenuto)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, dest_path)
                aggiornati.append(nome_file)
            except Exception as e:
                errori.append(nome_file)
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        # 3. Risultato
        if not aggiornati:
            return False, "Download fallito per tutti i file", []

        msg = "Aggiornato a v%s: %d/%d file" % (versione, len(aggiornati), totale)
        if errori:
            msg += "\nErrori su: %s" % ", ".join(errori)
        msg += "\nBackup: %s" % bk_name

        if callback:
            callback(msg)

        return True, msg, aggiornati

    except Exception as e:
        return False, "Errore aggiornamento: %s" % e, []
