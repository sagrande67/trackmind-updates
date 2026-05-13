# web_sync.py — TrackMind 5.4
# Sincronizzazione automatica PISTE via SpeedHive API.
# Tutte le operazioni di rete girano in thread daemon separati.
# La data ultimo sync viene scritta nel .def come !sync_date;ISO8601
# API pubblica richiesta da retrodb.py:
#   sync_tabella_background(nome, table_def, db, callback=None)
#   carica_ultimo_sync(nome) -> dict | None
#   ha_cambiamenti(nome) -> bool

import os
import json
import threading
import logging
import urllib.request
import urllib.error
from datetime import datetime

# ---------------------------------------------------------------------------
# Percorsi (risale da addons/ alla root del progetto)
# ---------------------------------------------------------------------------

_ADDONS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_ADDONS_DIR)
SYNC_STATE_DIR = os.path.join(_PROJECT_DIR, "dati", "sync_log")

os.makedirs(SYNC_STATE_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_log_file = os.path.join(SYNC_STATE_DIR, "web_sync.log")
logging.basicConfig(
    filename=_log_file,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ---------------------------------------------------------------------------
# Scrittura !sync_date nel file .def
# ---------------------------------------------------------------------------

def _aggiorna_sync_date_def(def_path):
    """Scrive/aggiorna !sync_date;ISO8601 nel file .def."""
    try:
        now = datetime.now().isoformat(timespec="seconds")
        if not os.path.exists(def_path):
            return

        with open(def_path, "r", encoding="utf-8") as f:
            righe = f.readlines()

        trovato = False
        for i, riga in enumerate(righe):
            if riga.strip().startswith("!sync_date;"):
                righe[i] = "!sync_date;%s\n" % now
                trovato = True
                break

        if not trovato:
            # Inserisci dopo l'ultima direttiva ! o all'inizio
            pos = 0
            for i, riga in enumerate(righe):
                s = riga.strip()
                if s.startswith("!") or s.startswith("#") or not s:
                    pos = i + 1
                else:
                    break
            righe.insert(pos, "!sync_date;%s\n" % now)

        with open(def_path, "w", encoding="utf-8") as f:
            f.writelines(righe)

    except Exception as e:
        logging.error("[sync_date] %s -> %s" % (def_path, e))

# ---------------------------------------------------------------------------
# Stato sync persistente (file JSON per ha_cambiamenti)
# ---------------------------------------------------------------------------

def _state_path(nome_tabella):
    return os.path.join(SYNC_STATE_DIR, "%s_sync.json" % nome_tabella)


def carica_ultimo_sync(nome_tabella):
    """Carica stato ultimo sync. Ritorna dict {'data': ISO, 'cambiamenti': bool} o None."""
    p = _state_path(nome_tabella)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _salva_stato_sync(nome_tabella, aggiunti, aggiornati):
    """Salva stato sync con timestamp e flag cambiamenti."""
    stato = {
        "data": datetime.now().isoformat(timespec="seconds"),
        "aggiunti": aggiunti,
        "aggiornati": aggiornati,
        "cambiamenti": (aggiunti + aggiornati) > 0,
    }
    try:
        with open(_state_path(nome_tabella), "w", encoding="utf-8") as f:
            json.dump(stato, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error("[%s] Salvataggio stato sync: %s" % (nome_tabella, e))


def ha_cambiamenti(nome_tabella):
    """True se l'ultimo sync ha prodotto cambiamenti non ancora visti."""
    stato = carica_ultimo_sync(nome_tabella)
    if not stato:
        return False
    return stato.get("cambiamenti", False)


def azzera_cambiamenti(nome_tabella):
    """Resetta il flag cambiamenti (dopo che l'utente ha aperto la tabella)."""
    stato = carica_ultimo_sync(nome_tabella)
    if stato and stato.get("cambiamenti"):
        stato["cambiamenti"] = False
        try:
            with open(_state_path(nome_tabella), "w", encoding="utf-8") as f:
                json.dump(stato, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Merge record nuovi con esistenti
# ---------------------------------------------------------------------------

def _merge_records(esistenti, nuovi, campo_chiave):
    """Unisce nuovi con esistenti. Ritorna (lista_aggiornata, n_aggiunti, n_modificati)."""
    risultato = [r.copy() for r in esistenti]
    n_aggiunti = 0
    n_modificati = 0

    for nuovo in nuovi:
        chiave_nuovo = str(nuovo.get(campo_chiave, "")).strip().lower()
        if not chiave_nuovo:
            continue

        trovato = False
        for i, es in enumerate(risultato):
            chiave_es = str(es.get(campo_chiave, "")).strip().lower()
            if chiave_es == chiave_nuovo:
                diff = False
                for k, v in nuovo.items():
                    if es.get(k) != v:
                        diff = True
                        break
                if diff:
                    risultato[i].update(nuovo)
                    n_modificati += 1
                trovato = True
                break

        if not trovato:
            risultato.append(nuovo)
            n_aggiunti += 1

    return risultato, n_aggiunti, n_modificati


def _completa_record_sync(records, table_def):
    """Assegna _id e campo chiave primario a record importati da sync.

    I record creati da _merge_records saltano db.inserisci(), quindi possono
    mancare _id (hex UUID) e il campo chiave K del .def (es. Codice_Pista).
    Questa funzione li completa dopo il merge, prima del salvataggio.
    """
    import uuid
    campo_k = table_def.get_campo_chiave()
    nome_k = campo_k["nome"] if campo_k else None

    # Trova il prossimo valore numerico per il campo chiave
    max_k = 0
    if nome_k:
        for r in records:
            val = r.get(nome_k, "")
            try:
                max_k = max(max_k, int(val))
            except (ValueError, TypeError):
                pass

    modificati = 0
    for r in records:
        # Assegna _id se mancante
        if not r.get("_id"):
            r["_id"] = str(uuid.uuid4())[:8]
            modificati += 1
        # Assegna campo chiave numerico se mancante
        if nome_k and not r.get(nome_k):
            max_k += 1
            r[nome_k] = str(max_k)
        # Assegna _timestamp se mancante
        if not r.get("_timestamp"):
            r["_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if modificati > 0:
        logging.info("[sync] Completati %d record con _id/chiave mancanti" % modificati)
    return records


# ---------------------------------------------------------------------------
# Sync PISTE via SpeedHive API (senza IA, dati strutturati)
# ---------------------------------------------------------------------------

_SPEEDHIVE_API = "https://practice-api.speedhive.com/api/v1/locations"
_SPEEDHIVE_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "TrackMind/1.0",
    "Origin": "https://speedhive.mylaps.com",
    "Referer": "https://speedhive.mylaps.com/",
}

# Mappa codici paese -> nome
_PAESI = {
    'it':'Italia','de':'Germania','fr':'Francia','es':'Spagna','nl':'Paesi Bassi',
    'be':'Belgio','at':'Austria','ch':'Svizzera','gb':'Regno Unito','us':'USA',
    'se':'Svezia','dk':'Danimarca','no':'Norvegia','fi':'Finlandia','pt':'Portogallo',
    'pl':'Polonia','cz':'Rep. Ceca','sk':'Slovacchia','hu':'Ungheria','hr':'Croazia',
    'si':'Slovenia','ro':'Romania','bg':'Bulgaria','gr':'Grecia','ie':'Irlanda',
    'lu':'Lussemburgo','ar':'Argentina','br':'Brasile','au':'Australia','nz':'Nuova Zelanda',
    'jp':'Giappone','cn':'Cina','kr':'Corea del Sud','tw':'Taiwan','hk':'Hong Kong',
    'sg':'Singapore','my':'Malesia','th':'Thailandia','id':'Indonesia','ph':'Filippine',
    'za':'Sudafrica','mx':'Messico','cl':'Cile','co':'Colombia','ca':'Canada',
    'ru':'Russia','ua':'Ucraina','tr':'Turchia','il':'Israele','ae':'Emirati Arabi',
    'sa':'Arabia Saudita','in':'India',
}


def _sync_piste_speedhive():
    """Scarica tutte le piste RC da SpeedHive API. Ritorna lista di dict o []."""
    try:
        url = "%s?count=5000" % _SPEEDHIVE_API
        req = urllib.request.Request(url, headers=_SPEEDHIVE_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        locations = data.get("locations", [])
        rc_tracks = [l for l in locations if l.get("sport") == "RC"]
        logging.info("[piste] SpeedHive API: %d RC tracks su %d totali" % (len(rc_tracks), len(locations)))

        risultato = []
        for p in rc_tracks:
            nome = (p.get("name") or "").strip().strip('"').strip()
            if not nome:
                continue

            lunghezza = p.get("trackLength", 0)
            if isinstance(lunghezza, (int, float)) and lunghezza > 10000:
                lunghezza = int(lunghezza / 1000)

            cc = (p.get("country") or "").lower()
            nazione = _PAESI.get(cc, cc.upper() if cc else "")

            nome_lower = nome.lower()
            if "indoor" in nome_lower or "carpet" in nome_lower:
                io = "Indoor"
            elif "outdoor" in nome_lower or "off road" in nome_lower or "offroad" in nome_lower:
                io = "Outdoor"
            else:
                io = ""

            status = p.get("status", "")

            risultato.append({
                "Nome_Pista": nome,
                "Nazione": nazione,
                "Lunghezza_mt": str(lunghezza) if lunghezza else "",
                "Indoor_Outdoor": io,
                "SpeedHive_ID": str(p.get("id", "")),
                "Sito_Web": p.get("url") or "",
                "Note": status if status != "OFFLINE" else "",
            })

        return risultato

    except Exception as e:
        logging.error("[piste] SpeedHive API: %s" % e)
        return []


# ---------------------------------------------------------------------------
# Worker sync (gira in thread daemon)
# ---------------------------------------------------------------------------

def _sync_worker(nome_tabella, table_def, db, callback):
    """
    Thread worker: fetch -> merge -> save -> aggiorna !sync_date nel .def.
    Supporta SOLO la tabella 'piste' via SpeedHive API.
    Mai blocca il main thread di tkinter.
    """
    logging.info("[%s] Sync avviata (thread: %s)" % (nome_tabella, threading.current_thread().name))

    # ---- Piste: sync diretto via SpeedHive API ----
    if nome_tabella == "piste":
        prodotti = _sync_piste_speedhive()
        if prodotti:
            aggiornati, n_aggiunti, n_modificati = _merge_records(
                db.records, prodotti, "SpeedHive_ID")
            aggiornati = _completa_record_sync(aggiornati, table_def)
            if n_aggiunti > 0 or n_modificati > 0:
                db.records = aggiornati
                db._salva_dati()
                logging.info("[piste] Salvati: +%d nuovi, ~%d mod" % (n_aggiunti, n_modificati))
            else:
                logging.info("[piste] Nessuna variazione")
            _aggiorna_sync_date_def(table_def.def_path)
            _salva_stato_sync(nome_tabella, n_aggiunti, n_modificati)
            if callback:
                callback({"aggiunti": n_aggiunti, "aggiornati": n_modificati, "errori": []})
        else:
            logging.error("[piste] SpeedHive API: nessun dato")
            _salva_stato_sync(nome_tabella, 0, 0)
            _aggiorna_sync_date_def(table_def.def_path)
            if callback:
                callback({"aggiunti": 0, "aggiornati": 0, "errori": ["SpeedHive API non disponibile"]})
        return

    # ---- Altre tabelle: nessun sync automatico ----
    logging.info("[%s] Sync non disponibile per questa tabella" % nome_tabella)
    _aggiorna_sync_date_def(table_def.def_path)
    _salva_stato_sync(nome_tabella, 0, 0)
    if callback:
        callback({"aggiunti": 0, "aggiornati": 0, "errori": []})


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------

# Protezione contro sync duplicati sulla stessa tabella
_sync_in_corso = set()
_sync_lock = threading.Lock()


def sync_tabella_background(nome_tabella, table_def, db, callback=None):
    """
    Lancia la sync di una tabella in un thread daemon separato.
    Non blocca MAI il main thread (tkinter).
    Ignora richieste duplicate se la stessa tabella e' gia' in sync.

    Attualmente supporta SOLO la tabella 'piste' (via SpeedHive API).
    Le altre tabelle (gomme, miscela, motori, ecc.) sono compilate
    manualmente dagli utenti con i propri componenti.

    Parametri:
        nome_tabella : str — nome tabella (es. "piste")
        table_def    : TableDef — definizione tabella (con .links, .campi, .def_path)
        db           : RetroDB — istanza database
        callback     : callable(dict) — opzionale, riceve:
                       {'aggiunti': int, 'aggiornati': int, 'errori': list}
                       Usare root.after(0, ...) per aggiornare la UI dal callback.
    """
    # Solo piste ha la sync automatica
    if nome_tabella != "piste":
        logging.info("[%s] Sync non supportata (solo piste), skip" % nome_tabella)
        if callback:
            callback({"aggiunti": 0, "aggiornati": 0, "errori": []})
        return

    if not table_def.links:
        logging.info("[%s] Nessun link, skip" % nome_tabella)
        if callback:
            callback({"aggiunti": 0, "aggiornati": 0, "errori": ["Nessun link"]})
        return

    # Check rapido connessione internet
    import socket as _sock
    try:
        _sock.create_connection(("8.8.8.8", 53), timeout=3).close()
    except (OSError, _sock.timeout):
        logging.info("[%s] Nessuna connessione internet, skip sync" % nome_tabella)
        if callback:
            callback({"aggiunti": 0, "aggiornati": 0, "errori": ["Nessuna connessione internet"]})
        return

    with _sync_lock:
        if nome_tabella in _sync_in_corso:
            logging.info("[%s] Sync gia' in corso, skip" % nome_tabella)
            return
        _sync_in_corso.add(nome_tabella)

    def _worker_wrapper():
        try:
            _sync_worker(nome_tabella, table_def, db, callback)
        finally:
            with _sync_lock:
                _sync_in_corso.discard(nome_tabella)

    t = threading.Thread(
        target=_worker_wrapper,
        args=(),
        daemon=True,
        name="sync_%s" % nome_tabella,
    )
    t.start()
    logging.info("[%s] Thread sync avviato: %s" % (nome_tabella, t.name))
