# -*- coding: utf-8 -*-
"""
core/scouting_paths.py
======================

Gestione centralizzata della struttura della cartella ``dati/scouting/``.

Storicamente i file dei tempi (``lap_*.json``) erano salvati TUTTI piatti
dentro ``dati/scouting/`` (e in alcuni casi nella cartella ``scouting/``
nella root del progetto). Da TrackMind 5.4.x si introduce una gerarchia:

    dati/scouting/
    ├── 2026/
    │   ├── Cremona_GT8_Gara2/
    │   │   ├── lap_lapmonitor_20260502_091756_0.json
    │   │   └── lap_myrcm_live_20260502_112229_4.json
    │   └── Mycandy_Arena/
    │       └── lap_speedhive_09052026_063354_s10.json
    └── _senza_pista/        # fallback per file senza metadati pista

Questo modulo offre un'API piccola, retrocompatibile, da usare in tutti
i punti del codice che oggi fanno ``os.listdir(scouting_dir)`` o
``os.path.join(scouting_dir, fname)``.

API principale
--------------

* :func:`elenca_lap_files` — itera ricorsivamente i file ``lap_*.json``,
  ritornando per ognuno (basename, percorso_completo). Sostituisce
  ``[f for f in os.listdir(scouting_dir) if f.startswith('lap_') and f.endswith('.json')]``.

* :func:`risolvi_path` — dato un basename (es. ``lap_myrcm_live_...json``),
  trova il path effettivo nel nuovo o vecchio layout. Sostituisce
  ``os.path.join(scouting_dir, fname)`` per la lettura.

* :func:`cartella_destinazione_per_meta` — per la SCRITTURA di un nuovo
  file, calcola in che sotto-cartella deve andare leggendo i metadati
  (``pista``, ``data``, ``setup``).

* :func:`path_destinazione` — wrapper che ritorna direttamente il path
  completo dove salvare un nuovo lap_*.json, creando le cartelle se
  servono.

Compatibilità
-------------

Le funzioni accettano sempre ``scouting_dir`` come parametro (lo stesso
che oggi viene calcolato nei vari moduli) e gestiscono in modo
trasparente sia la struttura piatta legacy sia quella nuova ad albero.
"""

from __future__ import absolute_import
import os
import re
import json


# ---------------------------------------------------------------------------
# Sanitizzazione nomi cartella
# ---------------------------------------------------------------------------

# Caratteri non ammessi nei nomi di cartella su Windows/Linux/macOS
_RE_CHAR_INVALIDI = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RE_SPAZI_MULTIPLI = re.compile(r'\s+')


def sanitizza_nome_cartella(nome):
    """Rende ``nome`` utilizzabile come nome di cartella su qualunque OS.

    - Rimpiazza i caratteri non validi con ``_``
    - Compatta gli spazi in ``_``
    - Tronca a 80 caratteri
    - Strippa punti e spazi finali (problematici su Windows)
    - Se diventa vuoto, ritorna ``"_senza_nome"``
    """
    if not nome:
        return "_senza_nome"
    s = str(nome).strip()
    s = _RE_CHAR_INVALIDI.sub('_', s)
    s = _RE_SPAZI_MULTIPLI.sub('_', s)
    s = s.strip(' ._')
    if len(s) > 80:
        s = s[:80].rstrip('_')
    return s or "_senza_nome"


# ---------------------------------------------------------------------------
# Estrazione anno e pista dai metadati / dal nome file
# ---------------------------------------------------------------------------

# Date come 20260502 oppure 09052026 nei nomi file
_RE_DATA_YMD = re.compile(r'(\d{8})')
_RE_DATA_DMY_SLASH = re.compile(r'(\d{2})/(\d{2})/(\d{4})')
_RE_DATA_YMD_DASH = re.compile(r'(\d{4})-(\d{2})-(\d{2})')


def _estrai_anno_da_data(data_str):
    """Ritorna l'anno (stringa di 4 cifre) o None."""
    if not data_str:
        return None
    s = str(data_str).strip()
    m = _RE_DATA_YMD_DASH.search(s)
    if m:
        return m.group(1)
    m = _RE_DATA_DMY_SLASH.search(s)
    if m:
        return m.group(3)
    m = _RE_DATA_YMD.search(s)
    if m:
        token = m.group(1)
        # Se inizia con 19/20/21 lo trattiamo come YYYYMMDD;
        # altrimenti DDMMYYYY (caso speedhive).
        if token[:2] in ('19', '20', '21'):
            return token[:4]
        return token[4:8]
    return None


def estrai_anno_e_pista(meta, basename=None):
    """Da un dict di metadati (e opzionalmente dal nome file) ricava
    una tupla (anno, pista) sanitizzata, pronta per costruire il path.

    Cerca, nell'ordine:
      * ``meta['pista']``
      * ``meta['setup']`` (es. "MyRCM Live - Cremona") — prende dopo " - "
      * ``meta['Codice_Pista']`` (solo come prefisso codice)
    Per l'anno guarda ``meta['data']``, poi le cifre nel basename.
    Se proprio non trova nulla, ritorna (None, None).
    """
    pista_raw = None
    if isinstance(meta, dict):
        for chiave in ('pista', 'Pista', 'nome_pista'):
            v = meta.get(chiave)
            if v and str(v).strip():
                pista_raw = str(v).strip()
                break
        if not pista_raw:
            setup = meta.get('setup') or meta.get('Setup')
            if setup and ' - ' in str(setup):
                # Es. "MyRCM Live - Cremona" -> "Cremona"
                pista_raw = str(setup).split(' - ', 1)[1].strip()
            elif setup:
                pista_raw = str(setup).strip()

    anno = None
    if isinstance(meta, dict):
        anno = _estrai_anno_da_data(
            meta.get('data') or meta.get('Data') or meta.get('_timestamp')
        )
    if not anno and basename:
        anno = _estrai_anno_da_data(basename)

    pista = sanitizza_nome_cartella(pista_raw) if pista_raw else None
    return (anno, pista)


# ---------------------------------------------------------------------------
# Calcolo cartella di destinazione
# ---------------------------------------------------------------------------

def cartella_destinazione_per_meta(scouting_dir, meta, basename=None,
                                   crea=False):
    """Ritorna la sotto-cartella di ``scouting_dir`` dove andrebbe salvato
    un file con i metadati indicati.

    Se ``crea=True`` la cartella viene creata (con ``os.makedirs``).
    Se mancano sia anno che pista, ritorna ``scouting_dir/_senza_pista``.
    """
    anno, pista = estrai_anno_e_pista(meta, basename=basename)
    if anno and pista:
        path = os.path.join(scouting_dir, anno, pista)
    elif pista:
        path = os.path.join(scouting_dir, '_senza_anno', pista)
    elif anno:
        path = os.path.join(scouting_dir, anno, '_senza_pista')
    else:
        path = os.path.join(scouting_dir, '_senza_pista')
    if crea:
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass
    return path


def path_destinazione(scouting_dir, basename, meta=None, crea=True):
    """Path completo dove SCRIVERE un nuovo file ``basename`` in base
    ai suoi metadati. Se ``meta`` è None, prova a leggerlo dal file
    esistente con quel basename (raro: usato solo nei migrate).
    """
    if meta is None:
        # Cerca in tutto l'albero un file gia' esistente con questo nome
        # (poco probabile in fase di scrittura, ma copre casi di rename)
        existing = risolvi_path(scouting_dir, basename)
        if existing and os.path.isfile(existing):
            try:
                with open(existing, 'r', encoding='utf-8') as fh:
                    meta = json.load(fh)
            except Exception:
                meta = {}
        else:
            meta = {}
    cartella = cartella_destinazione_per_meta(
        scouting_dir, meta, basename=basename, crea=crea)
    return os.path.join(cartella, basename)


# ---------------------------------------------------------------------------
# Scrittura unificata
# ---------------------------------------------------------------------------

def salva_sessione(sess, scouting_dir, basename, indent=2):
    """Salva un dict-sessione lap_*.json nella sotto-cartella
    ``<anno>/<pista>/`` di ``scouting_dir``.

    Questa è l'API unica usata da TUTTI i flussi di salvataggio
    (manuale, LapMonitor live, MyRCM live, MyRCM import, SpeedHive).
    Sostituisce il pattern ricorrente:

        path = os.path.join(scouting_dir, basename)   # piatto
        # oppure path = scouting_paths.path_destinazione(...)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sess, f, ensure_ascii=False, indent=2)
        scouting_paths.invalida_cache(scouting_dir)

    Il filename viene determinato dalla callsite (la naming
    convention varia per fonte: speedhive deve avere chip+sid nel
    nome per la deduplica, lapmonitor usa timestamp+pnum, ecc.).

    Args:
        sess: dict con i dati della sessione (contiene 'pista', 'data', ...)
        scouting_dir: cartella radice di scouting
        basename: nome file ``lap_*.json`` (calcolato dal chiamante)
        indent: indentazione JSON (default 2)

    Returns:
        Path completo del file scritto, o None in caso di errore.
    """
    try:
        path = path_destinazione(scouting_dir, basename, meta=sess,
                                  crea=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sess, f, ensure_ascii=False, indent=indent)
        invalida_cache(scouting_dir)
        return path
    except Exception as e:
        print("[scouting_paths] errore salvataggio %s: %s"
              % (basename, e))
        return None


def copia_in_scouting(src_path, scouting_dir, dest_basename=None,
                      sovrascrivi=False):
    """Copia un file ``lap_*.json`` da ``src_path`` dentro
    ``scouting_dir``, posizionandolo nella sotto-cartella corretta
    ``<anno>/<pista>/`` in base ai metadati letti dal file stesso.

    Args:
        src_path: file sorgente da copiare
        scouting_dir: cartella radice di scouting di destinazione
        dest_basename: nome file di destinazione; se None usa il
            basename di src_path
        sovrascrivi: se False (default), salta se il file di
            destinazione esiste già

    Returns:
        Path della destinazione (anche se non scritta perché
        già esistente), oppure None in caso di errore.
    """
    if not os.path.isfile(src_path):
        return None
    if dest_basename is None:
        dest_basename = os.path.basename(src_path)
    try:
        with open(src_path, "r", encoding="utf-8") as f:
            contenuto = f.read()
        try:
            meta = json.loads(contenuto)
        except Exception:
            meta = {}
        dest = path_destinazione(scouting_dir, dest_basename,
                                  meta=meta, crea=True)
        if os.path.exists(dest) and not sovrascrivi:
            return dest
        with open(dest, "w", encoding="utf-8") as f:
            f.write(contenuto)
        invalida_cache(scouting_dir)
        return dest
    except Exception as e:
        print("[scouting_paths] errore copia %s -> %s: %s"
              % (src_path, scouting_dir, e))
        return None


# ---------------------------------------------------------------------------
# Lettura: enumerazione e risoluzione path
# ---------------------------------------------------------------------------

def _is_lap_file(name):
    return name.startswith('lap_') and name.endswith('.json')


def elenca_lap_files(scouting_dir, prefisso=None):
    """Itera ricorsivamente tutti i file ``lap_*.json`` in
    ``scouting_dir`` e nelle sue sotto-cartelle.

    Ritorna una lista di tuple ``(basename, fullpath)``.

    Se ``prefisso`` è specificato (es. ``"lap_myrcm_"``), filtra i nomi.
    Funziona sia con la struttura legacy piatta sia con quella ad albero.
    """
    risultati = []
    if not scouting_dir or not os.path.isdir(scouting_dir):
        return risultati
    pref = prefisso or 'lap_'
    for root, _dirs, files in os.walk(scouting_dir):
        for f in files:
            if not _is_lap_file(f):
                continue
            if not f.startswith(pref):
                continue
            risultati.append((f, os.path.join(root, f)))
    return risultati


def elenca_basenames(scouting_dir, prefisso=None):
    """Compat-shim: come :func:`elenca_lap_files` ma ritorna solo i
    basename (per minimizzare le modifiche dove il vecchio codice
    faceva ``os.listdir`` e poi ``os.path.join``).
    Da usare insieme a :func:`risolvi_path`.
    """
    return [b for b, _p in elenca_lap_files(scouting_dir, prefisso=prefisso)]


# Cache per risolvi_path - viene invalidata se cambia il mtime della
# directory radice. Evita walk ripetuti durante operazioni batch.
_cache_index = {}  # scouting_dir -> {'mtime': ..., 'map': {basename: path}}


def _aggiorna_index(scouting_dir):
    try:
        mtime = os.path.getmtime(scouting_dir)
    except Exception:
        mtime = 0
    cache = _cache_index.get(scouting_dir)
    if cache and cache.get('mtime') == mtime:
        return cache['map']
    mappa = {b: p for b, p in elenca_lap_files(scouting_dir)}
    _cache_index[scouting_dir] = {'mtime': mtime, 'map': mappa}
    return mappa


def invalida_cache(scouting_dir=None):
    """Da chiamare dopo un'operazione di scrittura per forzare il
    refresh dell'indice. Se ``scouting_dir`` è None, svuota tutto.
    """
    if scouting_dir is None:
        _cache_index.clear()
    else:
        _cache_index.pop(scouting_dir, None)


def risolvi_path(scouting_dir, basename):
    """Dato un basename ``lap_*.json``, ritorna il path completo dove
    si trova nel filesystem.

    - Prima prova il path piatto ``scouting_dir/basename`` (legacy).
    - Poi consulta l'indice della struttura ad albero.
    - Se non trovato, ritorna comunque il path piatto come fallback
      (così il chiamante puo' decidere se trattarlo come "non esiste").
    """
    if not scouting_dir or not basename:
        return os.path.join(scouting_dir or '', basename or '')
    flat = os.path.join(scouting_dir, basename)
    if os.path.isfile(flat):
        return flat
    mappa = _aggiorna_index(scouting_dir)
    if basename in mappa:
        return mappa[basename]
    return flat  # default: path piatto (anche se non esiste)


# ---------------------------------------------------------------------------
# Helper di alto livello per il bootstrap (usato da myrcm_import.py)
# ---------------------------------------------------------------------------

def itera_lap_con_meta(scouting_dir, prefisso=None):
    """Generatore che ritorna (basename, fullpath, meta_dict) per ogni
    file lap_*.json. Salta i file non leggibili come JSON.
    """
    for basename, full in elenca_lap_files(scouting_dir, prefisso=prefisso):
        try:
            with open(full, 'r', encoding='utf-8') as fh:
                meta = json.load(fh)
        except Exception:
            continue
        yield basename, full, meta
