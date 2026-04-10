
"""
RetroDB v5.4 - MOTORE GESTIONALE GENERICO
- Login utente con username + password
- Dati filtrati per utente
- Ruolo admin per gestione completa
- Tabelle composite con selezione a lista
- Vista ELENCO (griglia tabellare con selezione record)
- ID auto-generato, backup, scala DPI

Applicazioni basate su RetroDB:
  - TrackMind (RC car setup management)
"""

APP_VERSION = "5.4"
__version__ = '05.04.2'

import re as _re
def _nome_base(nome_db):
    """Rimuove eventuale suffisso versione dal nome database (es. 'TrackMind 5.4' -> 'TrackMind')."""
    return _re.sub(r'\s+\d+(\.\d+)*\s*$', '', nome_db)

import sys
sys.dont_write_bytecode = True  # No .pyc cache

import tkinter as tk
from tkinter import font as tkfont, ttk, filedialog as _filedialog
import json, os, sys, uuid, shutil, zipfile, subprocess, threading
from datetime import datetime, date

# Aggiungi core/ e addons/ a sys.path (da CONF o default)
_base_dir = os.path.dirname(os.path.abspath(__file__))
from conf_manager import carica_conf as _cc, get_percorsi as _gp
_conf_tmp = _cc()
_percorsi_tmp = _gp(_conf_tmp)
for _sub_key in ("core", "addons"):
    _sub_dir = _percorsi_tmp.get(_sub_key, os.path.join(_base_dir, _sub_key))
    if os.path.isdir(_sub_dir) and _sub_dir not in sys.path:
        sys.path.insert(0, _sub_dir)
del _cc, _gp, _conf_tmp, _percorsi_tmp

from tm_field import RetroField, set_scala, get_scala, set_cell_params
from config_colori import carica_colori, salva_colori, DEFAULT_COLORS, NIGHT_COLORS
from conf_manager import (carica_conf, salva_conf, verifica_licenza, get_percorsi,
                          verifica_attivazione, attiva_licenza, get_codice_macchina,
                          ha_opzione_laptimer, genera_token_laptimer)
from auth import (carica_utenti, salva_utenti, verifica_login,
                  get_utente, is_admin, get_display_name,
                  username_esiste, cripta_password, decripta_password,
                  _verifica_accesso_speciale)
# splash.py non piu' usato: la splash e' integrata in _schermata_splash()

# Stampa termica (opzionale)
try:
    from thermal_print import (genera_scheda_gara, genera_scheda_completa,
                               salva_scheda_txt, stampa_bluetooth,
                               _bt_auto_setup, _bt_scan_stampante, _is_linux)
    _HAS_THERMAL = True
except ImportError:
    _HAS_THERMAL = False

# SpeedHive: gestito dal modulo Crono

# Editor Tabelle (embedded mode)
try:
    from editor_tabelle import EditorTabelle
    _HAS_EDITOR = True
except ImportError:
    _HAS_EDITOR = False

# LapTimer (usato dal modulo Crono)
try:
    from laptimer import LapTimer, classifica_giri
    _HAS_LAPTIMER = True
except ImportError:
    _HAS_LAPTIMER = False
    classifica_giri = None

# Crono Hub (add-on TrackMind - gestisce cronometraggi)
try:
    from crono import Crono
    _HAS_CRONO = True
except ImportError:
    _HAS_CRONO = False

# Meteo automatico (add-on TrackMind)
try:
    from meteo import meteo_da_indirizzo
    _HAS_METEO = True
except ImportError:
    _HAS_METEO = False

# Analizza Tempi: gestito dal modulo Crono

# Updater (aggiornamento software)
try:
    from updater import (prepara_aggiornamento, cerca_aggiornamento_usb,
                         verifica_aggiornamento, applica_aggiornamento,
                         riavvia_app, get_app_files, _trova_unita_usb,
                         controlla_aggiornamento_github,
                         scarica_aggiornamento_github)
    _HAS_UPDATER = True
except ImportError:
    _HAS_UPDATER = False

# Web Sync (aggiornamento cataloghi da web)
try:
    from web_sync import sync_tabella_background, ha_cambiamenti, carica_ultimo_sync
    _HAS_WEBSYNC = True
except ImportError:
    _HAS_WEBSYNC = False

MAX_VISIBLE_FIELDS = 15

def _S(val):
    return max(1, int(val * get_scala()))

COLOR_DESCRIPTIONS = {
    "sfondo": "Sfondo applicazione", "dati": "Dati inseriti",
    "label": "Nome campi (label)", "puntini": "Puntini celle vuote",
    "bordo_vuote": "Bordo celle vuote", "sfondo_celle": "Sfondo celle vuote",
    "sfondo_celle_piene": "Sfondo celle con dati", "separatori": "Separatori (/ e :)",
    "cursore": "Cursore", "testo_cursore": "Testo su cursore",
    "pulsanti_sfondo": "Pulsanti sfondo", "pulsanti_testo": "Pulsanti testo",
    "cancella_sfondo": "Cancella sfondo", "cancella_testo": "Cancella testo",
    "cerca_sfondo": "Cerca sfondo", "cerca_testo": "Cerca testo",
    "stato_ok": "Stato OK", "stato_avviso": "Stato avviso",
    "stato_errore": "Stato errore", "testo_dim": "Testo secondario", "linee": "Linee separatrici",
}


# =============================================================================
#  PARSER DEFINIZIONI
# =============================================================================
class TableDef:
    # Operazioni configurabili da file .def
    # Sintassi: !operazione;vero/falso
    OPERAZIONI_DEFAULT = {
        "nuovo":    False,  # Inserimento nuovi record
        "salva":    False,  # Modifica record esistenti
        "cancella": False,  # Cancellazione record
        "cerca":    False,  # Ricerca nei record
        "naviga":   False,  # Navigazione avanti/indietro
        "elenca":   False,  # Mostra lista/storico
        "laptimer": False,  # Bottone PISTA + TEMPI (attivare nel .def)
        "crono": False,     # Bottone CRONO scouting (attivare nel .def)
        "speedhive": False, # Bottone SPEEDHIVE import (attivare nel .def)
        "stampa": False,    # Bottone STAMPA assetto (attivare nel .def)
    }

    def __init__(self, def_path):
        self.def_path = def_path
        self.campi = []
        self.riferimenti = []
        self.is_composite = False
        self.accesso = "tutti"  # "admin", "utente", "tutti"
        self.condiviso = False  # Se True, dati visibili a tutti (no filtro utente)
        self.storico = False    # Se True, mostra storico dopo primo riferimento
        self.sezioni = {}       # {nome_campo: titolo_sezione} per separatori form
        self.links = []         # Lista URL per auto-sync catalogo
        self.descrizione = ""   # Descrizione tabella per IA (!descrizione nel .def)
        self.sync_date = None   # datetime ultimo sync web (!sync_date nel .def)
        self.operazioni = dict(self.OPERAZIONI_DEFAULT)
        self._parse()

    def _parse(self):
        _sezione_pending = None  # Sezione in attesa di essere assegnata al prossimo campo
        with open(self.def_path, "r", encoding="utf-8") as f:
            for riga in f:
                riga = riga.strip()
                if not riga or riga.startswith("#"): continue
                if riga.startswith("!"):
                    parti = riga[1:].split(";")
                    chiave = parti[0].strip().lower()
                    if chiave == "accesso" and len(parti) >= 2:
                        self.accesso = parti[1].strip().lower()
                    elif chiave == "condiviso" and len(parti) >= 2:
                        val = parti[1].strip().lower()
                        self.condiviso = val in ("vero", "true", "si", "1")
                    elif chiave == "storico" and len(parti) >= 2:
                        val = parti[1].strip().lower()
                        self.storico = val in ("vero", "true", "si", "1")
                    elif chiave == "sezione" and len(parti) >= 2:
                        _sezione_pending = parti[1].strip()
                    elif chiave in self.OPERAZIONI_DEFAULT and len(parti) >= 2:
                        val = parti[1].strip().lower()
                        self.operazioni[chiave] = val in ("vero", "true", "si", "1")
                    elif chiave == "link" and len(parti) >= 2:
                        url = parti[1].strip()
                        if url:
                            self.links.append(url)
                    elif chiave == "sync_date" and len(parti) >= 2:
                        try:
                            self.sync_date = datetime.fromisoformat(parti[1].strip())
                        except (ValueError, TypeError):
                            pass
                    elif chiave == "descrizione" and len(parti) >= 2:
                        self.descrizione = parti[1].strip()
                    continue
                if riga.startswith("@"):
                    parti = riga[1:].split(";")
                    if len(parti) >= 2:
                        tab = parti[0].strip()
                        chiave = parti[1].strip()
                        alias = parti[2].strip() if len(parti) >= 3 else tab
                        campo_rec = alias if len(parti) >= 3 else chiave
                        self.riferimenti.append({
                            "tabella": tab, "campo_chiave": chiave,
                            "alias": alias, "campo_record": campo_rec
                        })
                        self.is_composite = True
                    continue
                parti = riga.split(";")
                if len(parti) >= 3:
                    nome_campo = parti[0].strip()
                    self.campi.append({
                        "nome": nome_campo, "lunghezza": int(parti[1].strip()),
                        "tipo": parti[2].strip().upper(),
                        "chiave": len(parti) >= 4 and parti[3].strip().upper() == "K",
                    })
                    # Assegna sezione pendente al primo campo dopo la direttiva
                    if _sezione_pending:
                        self.sezioni[nome_campo] = _sezione_pending
                        _sezione_pending = None

    def puo(self, operazione):
        """Verifica se un'operazione e' abilitata per questa tabella."""
        return self.operazioni.get(operazione, True)

    def get_campo_chiave(self):
        for c in self.campi:
            if c.get("chiave"): return c
        return None

    def get_campi_non_chiave(self):
        return [c for c in self.campi if not c.get("chiave")]

    def get_schema_hash(self):
        import hashlib
        s = json.dumps(self.campi, sort_keys=True) + json.dumps(self.riferimenti, sort_keys=True)
        return hashlib.md5(s.encode()).hexdigest()

    def utente_autorizzato(self, sessione):
        """Verifica se l'utente ha accesso a questa tabella.
        accesso=tutti -> chiunque loggato
        accesso=admin -> solo admin
        accesso=utente -> solo utenti (non admin, utile per tabelle personali)
        """
        if not sessione: return False
        ruolo = sessione.get("ruolo", "utente")
        if self.accesso == "tutti": return True
        if self.accesso == "admin": return ruolo in ("admin", "sviluppatore")
        if self.accesso == "utente": return ruolo == "utente"
        return True


# =============================================================================
#  MOTORE DATABASE
# =============================================================================
class RetroDB:
    def __init__(self, nome, percorsi, table_def):
        self.nome = nome
        self.table_def = table_def
        self.data_path = os.path.join(percorsi["dati"], "%s.json" % nome)
        self.backup_dir = percorsi["backup"]
        self.records = []
        self._carica_dati()

    def _carica_dati(self):
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    contenuto = json.load(f)
            except (json.JSONDecodeError, ValueError):
                # File corrotto: prova il .tmp di backup (scrittura atomica interrotta)
                tmp_path = self.data_path + ".tmp"
                if os.path.exists(tmp_path):
                    try:
                        with open(tmp_path, "r", encoding="utf-8") as f:
                            contenuto = json.load(f)
                    except Exception:
                        contenuto = {}
                else:
                    contenuto = {}
            # Formato nuovo: dict con _meta + records
            if isinstance(contenuto, dict) and "records" in contenuto:
                self.records = contenuto["records"]
            # Formato vecchio: lista piatta (retrocompatibile)
            elif isinstance(contenuto, list):
                self.records = contenuto
            else:
                self.records = []
        else:
            self.records = []

    def _salva_dati(self):
        """Salva dati con scrittura atomica (temp + rename) per evitare corruzione."""
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        contenuto = {
            "_meta": {
                "tabella": self.nome,
                "accesso": self.table_def.accesso,
                "versione": APP_VERSION,
            },
            "records": self.records,
        }
        # Scrittura atomica: scrivi su file .tmp, poi rinomina
        tmp_path = self.data_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(contenuto, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            # os.replace() e' atomico sullo stesso filesystem
            os.replace(tmp_path, self.data_path)
        except Exception:
            # Se fallisce il rename, prova a rimuovere il .tmp
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def backup(self):
        if not os.path.exists(self.data_path): return
        os.makedirs(self.backup_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(self.backup_dir, "%s_%s.json" % (self.nome, ts))
        shutil.copy2(self.data_path, dest)
        backups = sorted([f for f in os.listdir(self.backup_dir)
                          if f.startswith(self.nome + "_") and f.endswith(".json")])
        while len(backups) > 2:
            os.remove(os.path.join(self.backup_dir, backups.pop(0)))

    def conteggio(self, filtro_utente=None):
        if filtro_utente:
            return len(self.get_records_filtrati(filtro_utente))
        return len(self.records)

    def get_records_filtrati(self, filtro_utente=None):
        """Ritorna indici dei record visibili per questo utente."""
        if not filtro_utente:
            return list(range(len(self.records)))
        try: fp = str(int(filtro_utente))
        except: fp = str(filtro_utente).strip()
        risultati = []
        for i, r in enumerate(self.records):
            try: rp = str(int(r.get("_utente_id", "")))
            except: rp = str(r.get("_utente_id", "")).strip()
            if rp == fp: risultati.append(i)
        return risultati

    def leggi(self, idx):
        return self.records[idx].copy() if 0 <= idx < len(self.records) else None

    def prossimo_id(self):
        campo_k = self.table_def.get_campo_chiave()
        if not campo_k: return None
        nome_k = campo_k["nome"]
        max_id = 0
        for rec in self.records:
            try:
                val = int(rec.get(nome_k, 0))
                if val > max_id: max_id = val
            except: pass
        return max_id + 1

    def inserisci(self, dati, utente_id=None):
        record = {"_id": str(uuid.uuid4())[:8]}
        if utente_id: record["_utente_id"] = str(utente_id)
        campo_k = self.table_def.get_campo_chiave()
        if campo_k:
            nome_k = campo_k["nome"]
            if not dati.get(nome_k): dati[nome_k] = str(self.prossimo_id())
        for rif in self.table_def.riferimenti:
            cr = rif.get("campo_record", rif["campo_chiave"])
            record[cr] = dati.get(cr, "")
        for c in self.table_def.campi:
            record[c["nome"]] = dati.get(c["nome"], "")
        record["_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.backup(); self.records.append(record); self._salva_dati()
        return len(self.records) - 1

    def aggiorna(self, idx, dati):
        if 0 <= idx < len(self.records):
            self.backup()
            for rif in self.table_def.riferimenti:
                k = rif.get("campo_record", rif["campo_chiave"])
                if k in dati: self.records[idx][k] = dati[k]
            for c in self.table_def.campi:
                if c["nome"] in dati and not c.get("chiave"):
                    self.records[idx][c["nome"]] = dati[c["nome"]]
            self.records[idx]["_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._salva_dati(); return True
        return False

    def cancella(self, idx):
        if 0 <= idx < len(self.records):
            self.backup(); self.records.pop(idx); self._salva_dati(); return True
        return False

    def cerca(self, filtri, filtro_utente=None):
        indici_visibili = self.get_records_filtrati(filtro_utente)
        risultati = []
        for i in indici_visibili:
            rec = self.records[i]; trovato = True
            for campo, val in filtri.items():
                if not val: continue
                if val.lower() not in str(rec.get(campo, "")).lower(): trovato = False; break
            if trovato: risultati.append(i)
        return risultati

    def get_descrizione_record(self, idx):
        rec = self.leggi(idx)
        if not rec: return ""
        campo_k = self.table_def.get_campo_chiave()
        parti = []
        if campo_k: parti.append("[%s]" % rec.get(campo_k["nome"], "?"))
        for c in self.table_def.get_campi_non_chiave():
            val = rec.get(c["nome"], "")
            if val: parti.append(str(val))
        return " - ".join(parti) if parti else "(vuoto)"

    def verifica_schema(self):
        meta_path = self.data_path + ".meta"
        current_hash = self.table_def.get_schema_hash()
        if os.path.exists(meta_path):
            with open(meta_path, "r") as f: saved_hash = f.read().strip()
            if saved_hash != current_hash:
                with open(meta_path, "w") as f: f.write(current_hash)
                return True
        else:
            with open(meta_path, "w") as f: f.write(current_hash)
        return False


# =============================================================================
#  APP PRINCIPALE
# =============================================================================
class RetroDBApp:

    def __init__(self, root):
        self.root = root
        self.conf = carica_conf()
        self.percorsi = get_percorsi(self.conf)

        scala = 1.0
        try: scala = float(self.conf.get("scala", 1.0))
        except: pass
        set_scala(scala)
        set_cell_params(
            size=self.conf.get("cella_dimensione", 16),
            pad=self.conf.get("cella_spaziatura", 1),
            font_cell=self.conf.get("font_campi", 9),
            font_label=self.conf.get("font_label", 9),
        )

        c = carica_colori()
        self.root.title(_nome_base(self.conf.get("nome_database", "RetroDB")) + "  v" + __version__)
        self.root.configure(bg=c["sfondo"])
        self.root.minsize(_S(500), _S(300))

        if self.conf.get("fullscreen", 0) in (1, "1", True, "True"):
            self.root.attributes("-fullscreen", True)
            self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))

        # Numero utenze licenza: 0=illimitato(multi), 1=mono, 2+=multi con limite
        self._max_utenti = self._parse_max_utenti()
        self._applica_limite_utenze()  # Verifica coerenza utenti/licenza

        # Sessione
        self.sessione = None   # {"codice": .., "nome": .., "ruolo": ..}
        self.db = None
        self.table_def = None
        self.ref_defs = {}
        self.ref_dbs = {}
        self.fields = {}
        self.ref_selectors = {}
        self.indice_corrente = -1
        self._indici_visibili = []
        self._pos_visibile = -1
        self.risultati_ricerca = []
        self.indice_ricerca = -1
        self.modo_ricerca = False
        self.modo_nuovo = False
        self._nome_tabella = ""
        self._wifi_online = True
        self._bt_stampante_ok = False   # Stato stampante BT
        self._bt_stampante_nome = ""    # Nome/MAC stampante trovata

        # Monitor WiFi ogni 30 secondi
        self._wifi_monitor()

        # Ricerca stampante BT in background (solo Linux/uConsole)
        if _HAS_THERMAL and _is_linux():
            self._bt_printer_monitor()

        # Frame BASE FISSO: non viene MAI distrutto.
        # Evita il flash del desktop durante il cambio schermata.
        self._base = tk.Frame(self.root, bg=c["sfondo"])
        self._base.pack(fill="both", expand=True)
        self._vista = None  # Frame schermata corrente (sopra _base)

        # Geometria impostata UNA VOLTA QUI, mai piu' durante le transizioni
        self.root.geometry("%dx%d" % (int(self.conf.get("larghezza_max", 900)),
                                       int(self.conf.get("altezza_max", 700))))

        self._f_title  = tkfont.Font(family="Consolas", size=_S(11), weight="bold")
        self._f_label  = tkfont.Font(family="Consolas", size=_S(9))
        self._f_btn    = tkfont.Font(family="Consolas", size=_S(8), weight="bold")
        self._f_small  = tkfont.Font(family="Consolas", size=_S(8))
        self._f_status = tkfont.Font(family="Consolas", size=_S(8))
        self._f_nav    = tkfont.Font(family="Consolas", size=_S(10), weight="bold")
        self._f_list   = tkfont.Font(family="Consolas", size=_S(8))
        self._f_login  = tkfont.Font(family="Consolas", size=_S(14), weight="bold")

        # Dimensione finestra dalla configurazione
        self._win_w = int(self.conf.get("larghezza_max", 900))
        self._win_h = int(self.conf.get("altezza_max", 700))

        # Suono tasti (macchina da scrivere)

        # Navigazione tastiera globale: Enter invoca qualsiasi bottone focalizzato
        def _safe_invoke(e):
            try:
                if str(e.widget.cget('state')) != 'disabled':
                    e.widget.invoke()
            except Exception:
                pass
        self.root.bind_class("Button", "<Return>", _safe_invoke)

        # Flash visivo su OGNI bottone premuto (feedback tattile stile terminale)
        self.root.bind_class("Button", "<ButtonRelease-1>",
            lambda e: self._btn_flash(e.widget), add="+")

        # Focus visivo globale: su Linux non si vede il focus ring nativo come su Windows
        # Inversione colori su OGNI bottone quando riceve/perde focus
        self.root.bind_class("Button", "<FocusIn>",
            lambda e: self._kb_focus_evidenzia(e.widget, True), add="+")
        self.root.bind_class("Button", "<FocusOut>",
            lambda e: self._kb_focus_evidenzia(e.widget, False), add="+")

        # Binding globali rimossi (CONF accessibile solo da login)
        self.root.bind("<Control-q>", lambda e: self.root.destroy())  # Ctrl+Q = ESCI da qualsiasi schermata

        valida, msg, giorni = verifica_licenza(self.conf)
        if not valida:
            self._schermata_licenza_scaduta(msg)
        else:
            # Verifica attivazione hardware
            attivato, codice_hw, msg_att = verifica_attivazione(self.conf)
            if not attivato:
                self._schermata_attivazione(codice_hw, msg_att)
            else:
                pass  # Licenza in scadenza: verra' mostrato nel menu
                self._schermata_login()  # Splash disabilitata (da rifare come immagine statica)

    def _parse_max_utenti(self):
        """Legge il numero max utenti dalla conf.
        0 = illimitato (multi), 1 = mono, 2+ = multi con limite."""
        raw = self.conf.get("multiutente", "1")
        # Retrocompatibilita': vecchi valori booleani
        if raw in ("X", "vero", "True", "true", "0"):
            return 0  # illimitato
        if raw in ("", "falso", "False", "false"):
            return 1  # monoutente
        try:
            n = int(raw)
            return max(n, 0)
        except (ValueError, TypeError):
            return 1  # default monoutente

    def _is_monoutente(self):
        """True se la licenza e' per un solo utente."""
        return self._max_utenti == 1

    def _utenti_validi(self):
        """Ritorna la lista degli utenti validi (con Username e Password compilati).
        Scorre tutti i record in ordine e prende solo quelli compilati."""
        utenti = carica_utenti()
        validi = []
        for u in utenti:
            nome = u.get("Username", "").strip()
            pwd = u.get("Password", "").strip()
            if nome and pwd:
                validi.append(u)
        return validi

    def _applica_limite_utenze(self):
        """Verifica coerenza utenti con il limite licenza.
        Con il nuovo modello semplificato, il limite impedisce la CREAZIONE
        di nuovi record oltre N. Non serve piu' il flag Attivo."""
        pass  # Il controllo avviene in _nuovo() e _utente_abilitato()

    def _utente_abilitato(self, username):
        """Verifica se un username puo' accedere.
        Con il modello semplificato, se l'utente esiste tra i validi puo' entrare:
        il limite agisce solo sulla creazione di nuovi record, non sul login."""
        if self._max_utenti == 0:
            return True  # illimitato
        validi = self._utenti_validi()
        username_low = username.strip().lower()
        return any(u.get("Username", "").strip().lower() == username_low for u in validi)

    def filtro_utente(self):
        """Ritorna il codice utente per filtrare, o None se condiviso."""
        if not self.sessione: return None
        # Solo sviluppatore (manutenzione) vede tutto
        if self.sessione.get("ruolo") == "sviluppatore": return None
        # Tabelle condivise: tutti vedono tutto (piste, utenti...)
        if hasattr(self, 'table_def') and self.table_def and self.table_def.condiviso:
            return None
        # Admin e utenti normali: vedono solo i propri dati
        return self.sessione.get("codice")

    def _imposta_geometria(self):
        """Imposta dimensione finestra dalla configurazione. Solo se cambiata."""
        target = "%dx%d" % (self._win_w, self._win_h)
        current = self.root.geometry().split("+")[0]  # "WxH" senza posizione
        if current != target:
            self.root.geometry(target)
        self.root.resizable(True, True)

    # =========================================================================
    #  NAVIGAZIONE DA TASTIERA (per uConsole / no mouse)
    # =========================================================================
    def _kb_enter_invoca(self, event):
        """Enter su un Button lo invoca (tkinter usa solo Space di default)."""
        w = event.widget
        if isinstance(w, tk.Button) and str(w["state"]) != "disabled":
            w.invoke()
            return "break"

    def _btn_flash(self, widget):
        """Flash visivo su bottone premuto: brevissima inversione colori."""
        try:
            if str(widget["state"]) == "disabled":
                return
        except (tk.TclError, AttributeError, TypeError):
            return
        try:
            c = carica_colori()
            orig_bg = widget.cget("bg")
            orig_fg = widget.cget("fg")
            # Flash: inversione colori
            widget.config(bg=c["dati"], fg=c["sfondo"])
            def _reset():
                try:
                    widget.config(bg=orig_bg, fg=orig_fg)
                except (tk.TclError, AttributeError):
                    pass
            widget.after(200, _reset)
        except (tk.TclError, AttributeError):
            pass

    _kb_original_colors = {}  # {widget_id: (bg, fg)}

    def _kb_focus_evidenzia(self, widget, on=True):
        """Evidenzia focus: sfondo chiaro + testo scuro (con focus),
        sfondo scuro + testo chiaro (senza focus)."""
        try:
            if str(widget.cget("state")) == "disabled":
                return
        except (tk.TclError, AttributeError):
            return
        wid = id(widget)
        try:
            if on:
                # Salva colori originali
                if wid not in self._kb_original_colors:
                    self._kb_original_colors[wid] = (
                        widget.cget("bg"), widget.cget("fg"),
                        widget.cget("relief"), str(widget.cget("bd")),
                    )
                orig_bg, orig_fg = self._kb_original_colors[wid][:2]
                # Focus: sfondo CHIARO (era il colore del testo) + testo SCURO (era lo sfondo)
                widget.config(bg=orig_fg, fg=orig_bg,
                              activebackground=orig_fg, activeforeground=orig_bg,
                              relief="solid", bd=2)
            else:
                # Senza focus: ripristina sfondo scuro + testo chiaro
                if wid in self._kb_original_colors:
                    orig_bg, orig_fg, orig_rel, orig_bd = self._kb_original_colors[wid]
                    widget.config(bg=orig_bg, fg=orig_fg,
                                  activebackground=orig_bg, activeforeground=orig_fg,
                                  relief=orig_rel, bd=int(orig_bd))
        except (tk.TclError, AttributeError):
            pass

    def _kb_setup_bottoni(self, bottoni, orizzontale=True):
        """Configura navigazione frecce + Enter + focus visivo su lista bottoni.
        orizzontale=True: Left/Right navigano, orizzontale=False: Up/Down navigano."""
        attivi = [b for b in bottoni if str(b["state"]) != "disabled"]
        if not attivi: return

        for btn in attivi:
            # Enter invoca
            btn.bind("<Return>", self._kb_enter_invoca)
            # Focus visivo
            btn.bind("<FocusIn>", lambda e, b=btn: self._kb_focus_evidenzia(b, True))
            btn.bind("<FocusOut>", lambda e, b=btn: self._kb_focus_evidenzia(b, False))

        # Frecce per spostarsi
        tasto_avanti = "<Right>" if orizzontale else "<Down>"
        tasto_indietro = "<Left>" if orizzontale else "<Up>"

        for i, btn in enumerate(attivi):
            if i < len(attivi) - 1:
                next_btn = attivi[i + 1]
                btn.bind(tasto_avanti, lambda e, b=next_btn: (b.focus_set(), "break")[-1])
            if i > 0:
                prev_btn = attivi[i - 1]
                btn.bind(tasto_indietro, lambda e, b=prev_btn: (b.focus_set(), "break")[-1])

    def _kb_setup_griglia(self, bottoni, colonne):
        """Configura navigazione frecce 4 direzioni su griglia di bottoni.
        bottoni = lista piatta, colonne = numero colonne della griglia."""
        attivi = [(i, b) for i, b in enumerate(bottoni) if str(b["state"]) != "disabled"]
        if not attivi: return

        # Mappa indice_griglia -> bottone attivo
        idx_map = {i: b for i, b in attivi}
        righe_tot = (len(bottoni) + colonne - 1) // colonne

        for grid_idx, btn in attivi:
            btn.bind("<Return>", self._kb_enter_invoca)
            btn.bind("<FocusIn>", lambda e, b=btn: self._kb_focus_evidenzia(b, True))
            btn.bind("<FocusOut>", lambda e, b=btn: self._kb_focus_evidenzia(b, False))

            riga = grid_idx // colonne
            col = grid_idx % colonne

            # Right
            for dc in range(1, colonne):
                next_c = col + dc
                next_idx = riga * colonne + next_c
                if next_c < colonne and next_idx in idx_map:
                    btn.bind("<Right>", lambda e, b=idx_map[next_idx]: (b.focus_set(), "break")[-1])
                    break
            # Left
            for dc in range(1, colonne):
                prev_c = col - dc
                prev_idx = riga * colonne + prev_c
                if prev_c >= 0 and prev_idx in idx_map:
                    btn.bind("<Left>", lambda e, b=idx_map[prev_idx]: (b.focus_set(), "break")[-1])
                    break
            # Down
            for dr in range(1, righe_tot):
                next_r = riga + dr
                next_idx = next_r * colonne + col
                if next_r < righe_tot and next_idx in idx_map:
                    btn.bind("<Down>", lambda e, b=idx_map[next_idx]: (b.focus_set(), "break")[-1])
                    break
            # Up
            for dr in range(1, righe_tot):
                prev_r = riga - dr
                prev_idx = prev_r * colonne + col
                if prev_r >= 0 and prev_idx in idx_map:
                    btn.bind("<Up>", lambda e, b=idx_map[prev_idx]: (b.focus_set(), "break")[-1])
                    break

        # Focus iniziale sul primo attivo
        attivi[0][1].focus_set()

    def _kb_setup_listbox(self, listbox, on_enter=None):
        """Configura Enter su Listbox per invocare un'azione."""
        if on_enter:
            listbox.bind("<Return>", lambda e: on_enter())

    # =========================================================================
    #  BARRA HELP TASTIERA
    # =========================================================================
    def _help_bar(self, parent, testo):
        """Aggiunge una riga di help tastiera stile DOS in fondo alla schermata."""
        c = carica_colori()
        bar = tk.Frame(parent, bg=c["sfondo"])
        bar.pack(fill="x", side="bottom", padx=_S(10), pady=(_S(2), _S(4)))
        tk.Label(bar, text=testo, bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small, anchor="center").pack(fill="x")
        return bar

    # =========================================================================
    #  SPLASH SCREEN (Matrix Rain nella stessa finestra)
    # =========================================================================
    def _schermata_splash(self):
        import random
        self._pulisci(); c = carica_colori()

        BG    = c["sfondo"]
        GREEN = c["dati"]
        DIM   = c["testo_dim"]
        LABEL = c["label"]
        AMBER = c.get("stato_avviso", "#ffaa00")
        LINE  = c.get("linee", "#1a5a0a")

        # In fullscreen usa le dimensioni reali dello schermo
        self.root.update_idletasks()
        if self.conf.get("fullscreen", 0) in (1, "1", True, "True"):
            W = self.root.winfo_screenwidth()
            H = self.root.winfo_screenheight()
        else:
            W = self._win_w
            H = self._win_h

        canvas = tk.Canvas(self._vista, width=W, height=H, bg=BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        # ─── Font ───
        F_TINY  = ("Consolas", _S(7))
        F_SMALL = ("Consolas", _S(9))
        F_MED   = ("Consolas", _S(11))
        F_BIG   = ("Consolas", _S(14), "bold")
        F_TITLE = ("Consolas", _S(18), "bold")

        # ─── Pioggia Matrix ───
        GLYPHS = "01234567890ABCDEF<>{}[]|/\\:;+=*#@$%&!?~^"
        COL_W = _S(14)
        COLS = W // COL_W

        # Sfumature verdi per la coda (no stipple — causa pixel bianchi su Windows)
        DIM2 = c.get("linee", "#144a14")  # verde ancora piu' scuro

        drops = []
        drop_items = []
        for col in range(COLS):
            x = col * COL_W + COL_W // 2
            y = random.randint(-H * 2, -10)
            speed = random.uniform(3.0, 10.0)
            trail_len = random.randint(6, 20)
            items = []
            for t in range(trail_len):
                if t == 0:       fill = GREEN  # testa brillante
                elif t < 3:      fill = LABEL  # verde medio
                elif t < trail_len // 2: fill = DIM   # verde scuro
                else:            fill = DIM2   # verde scurissimo
                item = canvas.create_text(x, y - t * 15, text=random.choice(GLYPHS),
                                           fill=fill, font=F_TINY, anchor="center")
                items.append(item)
            drops.append({"x": x, "y": y, "speed": speed, "trail": trail_len})
            drop_items.append(items)

        # ─── Logo (nascosto) ───
        logo_group = []
        phase = [0]
        frame_count = [0]
        _done = [False]

        bx, by, bw, bh = W // 2 - _S(220), H // 2 - _S(130), _S(440), _S(260)
        pad = _S(8)
        logo_group.append(canvas.create_rectangle(bx, by, bx + bw, by + bh, fill=BG, outline=LINE, width=1))
        logo_group.append(canvas.create_rectangle(bx + pad, by + pad, bx + bw - pad, by + bh - pad,
                                                    fill="", outline=DIM, width=1, dash=(3, 3)))
        logo_group.append(canvas.create_text(W // 2, by + _S(30),
            text="╔══════════════════════════════════════╗", fill=DIM, font=F_SMALL))
        logo_group.append(canvas.create_text(W // 2, by + _S(58),
            text="T R A C K M I N D", fill=GREEN, font=F_TITLE))
        logo_group.append(canvas.create_text(W // 2, by + _S(85),
            text="╚══════════════════════════════════════╝", fill=DIM, font=F_SMALL))
        logo_group.append(canvas.create_text(W // 2, by + _S(110),
            text="━" * 34, fill=DIM, font=F_SMALL))
        logo_group.append(canvas.create_text(W // 2, by + _S(135),
            text="version %s" % APP_VERSION, fill=AMBER, font=F_BIG))
        logo_group.append(canvas.create_text(W // 2, by + _S(165),
            text="RC Car Setup Database", fill=LABEL, font=F_MED))
        logo_group.append(canvas.create_text(W // 2, by + _S(190),
            text="Track · Setup · Perform", fill=DIM, font=F_SMALL))
        logo_group.append(canvas.create_text(W // 2, by + _S(215),
            text="━" * 34, fill=DIM, font=F_SMALL))
        logo_group.append(canvas.create_text(W // 2, by + _S(238),
            text="[ press any key ]", fill=DIM, font=F_SMALL))
        for item in logo_group:
            canvas.itemconfig(item, state="hidden")

        # ─── Reveal progressivo ───
        reveal_idx = [0]
        def _reveal_next():
            if _done[0]: return
            idx = reveal_idx[0]
            if idx < len(logo_group):
                canvas.itemconfig(logo_group[idx], state="normal")
                canvas.tag_raise(logo_group[idx])
                reveal_idx[0] += 1
                self.root.after(80, _reveal_next)
            else:
                _blink_hint()

        def _blink_hint():
            if _done[0] or not logo_group: return
            hint = logo_group[-1]
            vis = [True]
            def _toggle():
                if _done[0]: return
                vis[0] = not vis[0]
                canvas.itemconfig(hint, fill=DIM if vis[0] else BG)
                canvas.tag_raise(hint)
                self.root.after(500, _toggle)
            _toggle()

        # ─── Animazione ───
        def _animate():
            if _done[0]: return
            frame_count[0] += 1
            for i, (drop, items) in enumerate(zip(drops, drop_items)):
                drop["y"] += drop["speed"]
                x = drop["x"]
                for t, item in enumerate(items):
                    canvas.coords(item, x, drop["y"] - t * 15)
                    if random.random() < 0.12:
                        canvas.itemconfig(item, text=random.choice(GLYPHS))
                total_len = drop["trail"] * 15
                if drop["y"] - total_len > H:
                    drop["y"] = random.randint(-120, -30)
                    drop["speed"] = random.uniform(3.0, 10.0)
            if phase[0] == 0 and frame_count[0] > 60:
                phase[0] = 1
                _reveal_next()
            self.root.after(33, _animate)

        # Scanlines CRT (linee sottili scure, no stipple)
        for sy in range(0, H, 4):
            canvas.create_line(0, sy, W, sy, fill="#050505")

        # ─── Chiusura → vai al login ───
        def _vai_login(e=None):
            if _done[0]: return
            _done[0] = True
            self._schermata_login()

        self.root.bind("<Button-1>", _vai_login)
        self.root.bind("<Key>", _vai_login)
        self.root.after(100, _animate)
        self.root.after(10000, _vai_login)
        self._rimuovi_coperta()

    # =========================================================================
    #  LOGIN
    # =========================================================================
    def _schermata_login(self):
        self._pulisci(); c = carica_colori()


        self.sessione = None

        nome_db = _nome_base(self.conf.get("nome_database", "RetroDB"))
        tk.Label(self._vista, text=nome_db + "  v" + __version__, bg=c["sfondo"], fg=c["dati"],
                 font=self._f_login).pack(pady=(_S(20), _S(15)))

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(30))

        form = tk.Frame(self._vista, bg=c["sfondo"])
        form.pack(pady=_S(15))

        tk.Label(form, text="LOGIN", bg=c["sfondo"], fg=c["stato_avviso"],
                 font=self._f_btn).grid(row=0, column=0, columnspan=2, pady=(_S(0), _S(10)))

        tk.Label(form, text="Utente:", bg=c["sfondo"], fg=c["label"],
                 font=self._f_label).grid(row=1, column=0, sticky="e", padx=(_S(0), _S(5)))
        self._login_user = RetroField(form, label="", tipo="S", lunghezza=15, label_width=0)
        self._login_user.grid(row=1, column=1, pady=_S(2))

        tk.Label(form, text="Password:", bg=c["sfondo"], fg=c["label"],
                 font=self._f_label).grid(row=2, column=0, sticky="e", padx=(_S(0), _S(5)))
        self._login_pwd = RetroField(form, label="", tipo="P", lunghezza=9, label_width=0)
        self._login_pwd.grid(row=2, column=1, pady=_S(2), sticky="w")

        btn_frame = tk.Frame(self._vista, bg=c["sfondo"])
        btn_frame.pack(pady=_S(10))

        tk.Button(btn_frame, text="ACCEDI", font=self._f_btn, width=_S(12),
                  bg=c["pulsanti_sfondo"], fg=c["dati"],
                  activebackground=c["pulsanti_sfondo"], activeforeground=c["dati"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._esegui_login).pack(pady=_S(5))

        self._login_status = tk.Label(self._vista, text="Default: admin / 000000",
                                       bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small)
        self._login_status.pack()

        # Stato Wi-Fi
        connesso, ssid_wifi = self._wifi_stato()
        if connesso:
            wifi_txt = "Wi-Fi: %s" % ssid_wifi
            wifi_fg = c["stato_ok"]
        else:
            wifi_txt = "Wi-Fi: non connesso"
            wifi_fg = c["stato_errore"]
        tk.Label(self._vista, text=wifi_txt, bg=c["sfondo"], fg=wifi_fg,
                 font=self._f_small).pack(pady=(_S(4), 0))

        # Stato Stampante BT (solo Linux/uConsole)
        if _HAS_THERMAL and _is_linux():
            if self._bt_stampante_ok:
                bt_txt = "Stampante: ON"
                bt_fg = c["stato_ok"]
            else:
                bt_txt = "Stampante: OFF"
                bt_fg = c["stato_errore"]
            self._lbl_stampante = tk.Label(self._vista, text=bt_txt, bg=c["sfondo"],
                                           fg=bt_fg, font=self._f_small)
            self._lbl_stampante.pack(pady=(_S(2), 0))

        # Help tastiera + barra inferiore
        self._help_bar(self._vista, "Tab = Prossimo campo  |  Enter = Accedi  |  SPEGNI = Arresta sistema")
        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(20), side="bottom")
        bottom = tk.Frame(self._vista, bg=c["sfondo"])
        bottom.pack(fill="x", side="bottom", padx=_S(20), pady=(_S(6), _S(8)))

        btn_spegni = tk.Button(bottom, text="SPEGNI", font=self._f_small, width=_S(8),
                  bg=c["pulsanti_sfondo"], fg=c["stato_errore"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._spegni_console)
        btn_spegni.pack(side="right", padx=_S(3))
        self._kb_setup_bottoni([btn_spegni], orizzontale=True)

        # Keyboard: Enter ovunque -> accedi
        self.root.bind("<Return>", lambda e: self._esegui_login())
        self._rimuovi_coperta()

        # Monoutente: auto-compila username e focus su password
        if self._is_monoutente():
            utenti = carica_utenti()
            if utenti:
                nome = utenti[0].get("Username", "").strip()
                if nome:
                    self._login_user.set(nome)
                    self.root.after(100, self._login_pwd.set_focus)
                else:
                    self.root.after(100, self._login_user.set_focus)
            else:
                self.root.after(100, self._login_user.set_focus)
        else:
            self.root.after(100, self._login_user.set_focus)

    def _spegni_console(self):
        """Spegne la console (Linux/uConsole). Doppia pressione per conferma."""
        import time
        c = carica_colori()
        now = time.time()
        if not hasattr(self, '_spegni_ts') or now - self._spegni_ts > 3:
            self._spegni_ts = now
            if hasattr(self, '_login_status'):
                self._login_status.config(
                    text="Premi SPEGNI di nuovo per confermare!",
                    fg=c["stato_errore"])
            return
        # Confermato: backup + spegni
        del self._spegni_ts
        if hasattr(self, '_login_status'):
            self._login_status.config(text="Backup in corso...", fg=c["stato_avviso"])
            self.root.update()
        # Backup automatico pre-spegnimento
        try:
            self._esegui_backup(force=True)
        except Exception:
            pass  # Se il backup fallisce, spegni comunque
        if sys.platform == "win32":
            self.root.destroy()  # Su Windows: solo esci
        else:
            self.root.destroy()
            os.system("sudo shutdown -h now")  # Linux/uConsole

    def _esegui_login(self):
        c = carica_colori()
        username = self._login_user.get().strip()
        password = self._login_pwd.get_raw().strip()

        # Accesso sviluppatore
        if username == "CoNfI":
            self._login_user.clear()
            self._login_pwd.clear()
            self._schermata_conf()
            return

        # Monoutente: forza login solo con il primo utente valido
        # (bypass per accesso manutenzione)
        if self._is_monoutente() and not _verifica_accesso_speciale(username, password):
            validi = self._utenti_validi()
            if validi:
                username = validi[0].get("Username", "").strip()

        if not username or not password:
            try:
                self._login_status.config(text="Inserisci utente e password!", fg=c["stato_errore"])
            except (tk.TclError, AttributeError):
                pass
            return

        ok, utente = verifica_login(username, password)
        if ok:
            # Accesso manutenzione: bypassa controllo limite utenze
            if utente.get("ruolo") != "sviluppatore":
                # Controllo limite utenze: verifica che l'utente sia tra i primi N validi
                if not self._utente_abilitato(username):
                    try:
                        self._login_status.config(
                            text="Utente o password errati!",
                            fg=c["stato_errore"])
                    except (tk.TclError, AttributeError):
                        pass
                    self._login_pwd.clear()
                    return
            self.sessione = utente
            self._notifica_connessione()  # Notifica anti-copia
            self._schermata_menu()
            # Check aggiornamenti automatico in background (random 5-30s dopo login)
            if _HAS_UPDATER:
                import random
                delay = random.randint(5000, 30000)
                self.root.after(delay, self._auto_check_aggiornamenti)
        else:
            try:
                self._login_status.config(text="Utente o password errati!", fg=c["stato_errore"])
            except (tk.TclError, AttributeError):
                pass
            self._login_pwd.clear()

    # =========================================================================
    #  MENU PRINCIPALE
    # =========================================================================
    def _schermata_menu(self):
        self._pulisci(); c = carica_colori()

        # â”€â”€ HEADER â”€â”€
        header = tk.Frame(self._vista, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(20), pady=(_S(15), _S(5)))

        nome_db = _nome_base(self.conf.get("nome_database", "RetroDB"))
        tk.Label(header, text=nome_db + "  v" + __version__, bg=c["sfondo"], fg=c["dati"],
                 font=self._f_login).pack()

        ruolo_txt = "ADMIN" if is_admin(self.sessione) else "UTENTE"
        ruolo_fg = c["stato_avviso"] if is_admin(self.sessione) else c["dati"]
        info_line = tk.Frame(header, bg=c["sfondo"])
        info_line.pack(pady=(_S(2), 0))
        tk.Label(info_line, text="%s  |  %s" % (get_display_name(self.sessione), ruolo_txt),
                 bg=c["sfondo"], fg=ruolo_fg, font=self._f_small).pack(side="left")

        # Indicatore Wi-Fi
        connesso, ssid_wifi = self._wifi_stato()
        if connesso:
            wifi_txt = "  |  Wi-Fi: %s" % ssid_wifi
            wifi_fg = c["stato_ok"]
        else:
            wifi_txt = "  |  Wi-Fi: offline"
            wifi_fg = c["stato_errore"]
        tk.Label(info_line, text=wifi_txt, bg=c["sfondo"], fg=wifi_fg,
                 font=self._f_small).pack(side="left")

        # Indicatore Stampante BT (solo Linux/uConsole)
        if _HAS_THERMAL and _is_linux():
            if self._bt_stampante_ok:
                bt_txt = "  |  Stampante: ON"
                bt_fg = c["stato_ok"]
            else:
                bt_txt = "  |  Stampante: OFF"
                bt_fg = c["stato_errore"]
            self._lbl_stampante = tk.Label(info_line, text=bt_txt, bg=c["sfondo"],
                                           fg=bt_fg, font=self._f_small)
            self._lbl_stampante.pack(side="left")
        else:
            self._lbl_stampante = None

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(20), pady=(_S(8), 0))

        # â”€â”€ AREA CENTRALE (espandibile) â”€â”€
        centro = tk.Frame(self._vista, bg=c["sfondo"])
        centro.pack(fill="both", expand=True, padx=_S(20), pady=(_S(5), _S(5)))

        # Etichetta sezione
        tk.Label(centro, text="T A B E L L E", bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(pady=(_S(8), _S(6)))

        # Griglia tabelle con cornice decorativa
        grid_border = tk.Frame(centro, bg=c["linee"], bd=0)
        grid_border.pack(pady=(_S(0), _S(10)))
        grid_inner = tk.Frame(grid_border, bg=c["sfondo"], padx=_S(12), pady=_S(10))
        grid_inner.pack(padx=1, pady=1)  # bordo 1px

        # Linea decorativa sopra griglia
        deco_top = tk.Frame(grid_inner, bg=c["sfondo"])
        deco_top.pack(fill="x", pady=(0, _S(6)))
        tk.Frame(deco_top, bg=c["cerca_sfondo"], height=2, width=_S(40)).pack(side="left")
        tk.Frame(deco_top, bg=c["sfondo"], width=_S(6)).pack(side="left")
        tk.Frame(deco_top, bg=c["cerca_sfondo"], height=2).pack(side="left", fill="x", expand=True)

        # Bottoni tabelle
        def_dir = self.percorsi["definizioni"]
        os.makedirs(def_dir, exist_ok=True)
        tabelle = [f[:-4] for f in sorted(os.listdir(def_dir)) if f.endswith(".def")]

        COLONNE = 3
        btn_w = _S(16)

        grid_frame = tk.Frame(grid_inner, bg=c["sfondo"])
        grid_frame.pack()

        menu_btns = []  # lista bottoni per navigazione tastiera
        self._menu_tab_btns = {}  # {nome_tabella: widget} per aggiornamento colore sync
        for i, tab in enumerate(tabelle):
            def_path = os.path.join(def_dir, "%s.def" % tab)
            td = TableDef(def_path)
            autorizzato = td.utente_autorizzato(self.sessione)
            nome_btn = tab.upper().replace("_", " ")
            riga = i // COLONNE
            col = i % COLONNE

            if autorizzato:
                # Colore giallo se ci sono aggiornamenti dal web sync
                btn_fg = c["pulsanti_testo"]
                if _HAS_WEBSYNC and td.links and ha_cambiamenti(tab):
                    btn_fg = c["stato_avviso"]  # GIALLO
                btn = tk.Button(grid_frame, text=" %s " % nome_btn, font=self._f_btn, width=btn_w,
                                bg=c["pulsanti_sfondo"], fg=btn_fg,
                                activebackground=c["cerca_sfondo"], activeforeground=c["pulsanti_testo"],
                                relief="ridge", bd=1, cursor="hand2")
                btn.config(command=self._flash_btn(btn, lambda t=tab: self._apri_tabella(t)))
            else:
                btn = tk.Button(grid_frame, text=" %s " % nome_btn, font=self._f_btn, width=btn_w,
                                bg=c["sfondo"], fg=c["testo_dim"],
                                relief="flat", bd=1, state="disabled",
                                disabledforeground=c["testo_dim"])
            btn.grid(row=riga, column=col, padx=_S(4), pady=_S(4))
            menu_btns.append(btn)
            if autorizzato:
                self._menu_tab_btns[tab] = btn

        # Navigazione tastiera sulla griglia
        if menu_btns:
            self._kb_setup_griglia(menu_btns, COLONNE)

        # Linea decorativa sotto griglia
        deco_bot = tk.Frame(grid_inner, bg=c["sfondo"])
        deco_bot.pack(fill="x", pady=(_S(6), 0))
        tk.Frame(deco_bot, bg=c["cerca_sfondo"], height=2).pack(side="left", fill="x", expand=True)
        tk.Frame(deco_bot, bg=c["sfondo"], width=_S(6)).pack(side="left")
        tk.Frame(deco_bot, bg=c["cerca_sfondo"], height=2, width=_S(40)).pack(side="left")

        # Versione centrata
        ver = APP_VERSION
        tk.Label(centro, text="v%s" % ver, bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(pady=(_S(4), 0))

        # Codice macchina (per acquisto estensioni)
        cod_macchina = get_codice_macchina()
        tk.Label(centro, text=cod_macchina, bg=c["sfondo"], fg=c["label"],
                 font=self._f_label).pack(pady=(_S(2), 0))

        # Status menu (backup/ripristino)
        self._menu_status = tk.Label(centro, text="", bg=c["sfondo"], fg=c["testo_dim"],
                                      font=self._f_small)
        self._menu_status.pack(pady=(_S(4), 0))

        # Status sync web
        self._sync_status = tk.Label(centro, text="", bg=c["sfondo"], fg=c["testo_dim"],
                                      font=self._f_small)
        self._sync_status.pack(pady=(0, 0))

        # Help tastiera
        tk.Label(centro, text="Frecce = Naviga  |  Enter = Apri  |  Tab = Bottoni  |  Esc = Logout",
                 bg=c["sfondo"], fg=c["puntini"], font=self._f_small).pack(pady=(_S(6), 0))


        # â”€â”€ BARRA INFERIORE (sempre in fondo) â”€â”€
        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(20), side="bottom")

        bottom_bar = tk.Frame(self._vista, bg=c["sfondo"])
        bottom_bar.pack(fill="x", side="bottom", padx=_S(20), pady=(_S(6), _S(8)))

        # Licenza a sinistra
        _, lic_msg, _ = verifica_licenza(self.conf)
        tk.Label(bottom_bar, text=lic_msg, bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small, anchor="w").pack(side="left")

        # Bottoni funzione a destra
        _bb = []  # bottoni bottom bar (ordine visivo: sinistra -> destra)

        if is_admin(self.sessione):
            self._btn_backup = tk.Button(bottom_bar, text="BACKUP", font=self._f_small, width=_S(8),
                      bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=self._esegui_backup)
            _bb.append(self._btn_backup)

            btn_ripr = tk.Button(bottom_bar, text="RIPRISTINA", font=self._f_small, width=_S(10),
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=self._esegui_ripristino)
            _bb.append(btn_ripr)

            btn_edtab = tk.Button(bottom_bar, text="EDITA TAB", font=self._f_small, width=_S(10),
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=self._lancia_editor_tabelle)
            _bb.append(btn_edtab)

            btn_attiva = tk.Button(bottom_bar, text="ATTIVA", font=self._f_small, width=_S(8),
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=lambda: self._schermata_attivazione(
                          get_codice_macchina(), "Inserisci nuova chiave di attivazione"))
            _bb.append(btn_attiva)

            btn_hotspot = tk.Button(bottom_bar, text="HOTSPOT", font=self._f_small, width=_S(8),
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=self._schermata_hotspot)
            _bb.append(btn_hotspot)

            if _HAS_UPDATER:
                btn_agg = tk.Button(bottom_bar, text="AGGIORNA", font=self._f_small, width=_S(9),
                          bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                          relief="ridge", bd=1, cursor="hand2",
                          command=self._schermata_aggiorna)
                _bb.append(btn_agg)

                btn_prep = tk.Button(bottom_bar, text="PREPARA", font=self._f_small, width=_S(8),
                          bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                          relief="ridge", bd=1, cursor="hand2",
                          command=self._schermata_prepara_aggiornamento)
                _bb.append(btn_prep)


        # Bottone CRONO scouting (solo se almeno un .def ha !crono;vero)
        _ha_crono = False
        if _HAS_CRONO:
            def_dir = self.percorsi.get("definizioni", "")
            if def_dir and os.path.isdir(def_dir):
                for fn in os.listdir(def_dir):
                    if fn.endswith(".def"):
                        try:
                            with open(os.path.join(def_dir, fn), "r", encoding="utf-8") as _df:
                                if "!crono;vero" in _df.read():
                                    _ha_crono = True; break
                        except: pass
        if _ha_crono:
            btn_crono = tk.Button(bottom_bar, text="CRONO", font=self._f_small, width=_S(7),
                      bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=lambda: self._lancia_crono_scouting())
            _bb.append(btn_crono)

        night_label = "GIORNO" if self._is_night_mode() else "NOTTE"
        night_fg = "#39ff14" if self._is_night_mode() else "#ff3333"
        btn_night = tk.Button(bottom_bar, text=night_label, font=self._f_small, width=_S(7),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._toggle_night_mode)
        _bb.append(btn_night)

        btn_colori = tk.Button(bottom_bar, text="COLORI", font=self._f_small, width=_S(7),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._schermata_setup)
        _bb.append(btn_colori)

        btn_esci = tk.Button(bottom_bar, text="ESCI", font=self._f_small, width=_S(6),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._schermata_login)
        _bb.append(btn_esci)

        # Pack in ordine inverso (side=right)
        for b in reversed(_bb):
            b.pack(side="right", padx=_S(3))

        # Navigazione tastiera barra inferiore
        self._kb_setup_bottoni(_bb, orizzontale=True)

        # Escape -> logout
        self.root.bind("<Escape>", lambda e: self._schermata_login())
        self._rimuovi_coperta()

        # Auto-sync web cataloghi (controlla !sync_date, agisce solo se > 24h)
        if _HAS_WEBSYNC:
            self.root.after(2000, self._auto_sync_cataloghi)

    # =========================================================================
    #  MODALITÀ NOTTURNA
    # =========================================================================
    def _is_night_mode(self):
        """Controlla se i colori correnti sono quelli notturni."""
        c = carica_colori()
        return c.get("dati") == NIGHT_COLORS["dati"]

    def _toggle_night_mode(self):
        """Alterna tra tema verde (giorno) e tema rosso (notte)."""
        if self._is_night_mode():
            salva_colori(DEFAULT_COLORS.copy())
        else:
            salva_colori(NIGHT_COLORS.copy())
        carica_colori(force=True)
        self._schermata_menu()

    # =========================================================================
    #  NOTIFICA ANTI-COPIA (email allo sviluppatore)
    # =========================================================================
    _notifica_inviata = False  # Una sola notifica per sessione

    def _notifica_connessione(self):
        """Invia email allo sviluppatore con dati macchina quando c'e' connessione.
        Esecuzione in background, silente, una volta per sessione."""
        if self._notifica_inviata:
            return
        email_dev = self.conf.get("email_sviluppatore", "").strip()
        smtp_srv = self.conf.get("smtp_server", "").strip()
        smtp_usr = self.conf.get("smtp_user", "").strip()
        smtp_pwd = self.conf.get("smtp_password", "").strip()
        if not email_dev or not smtp_srv or not smtp_usr or not smtp_pwd:
            return
        # Verifica connessione WiFi
        connesso, ssid = self._wifi_stato()
        if not connesso:
            return
        self._notifica_inviata = True

        codice = get_codice_macchina()
        ora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        utente = get_display_name(self.sessione) if self.sessione else "(nessuno)"
        nome_db = self.conf.get("nome_database", "RetroDB")
        smtp_port = int(self.conf.get("smtp_port", 587))

        def _invia():
            try:
                import smtplib
                from email.mime.text import MIMEText
                corpo = (
                    "NOTIFICA AVVIO\n"
                    "===================\n"
                    "Applicazione: %s v%s\n"
                    "Codice macchina: %s\n"
                    "Data/Ora: %s\n"
                    "Utente: %s\n"
                    "WiFi: %s\n"
                    "Piattaforma: %s\n"
                ) % (nome_db, __version__, codice, ora, utente, ssid,
                     sys.platform)
                msg = MIMEText(corpo, "plain", "utf-8")
                msg["Subject"] = "[%s] Avvio - %s - %s" % (nome_db, codice, ora)
                msg["From"] = smtp_usr
                msg["To"] = email_dev
                with smtplib.SMTP(smtp_srv, smtp_port, timeout=15) as s:
                    s.starttls()
                    s.login(smtp_usr, smtp_pwd)
                    s.send_message(msg)
            except Exception:
                pass  # Silente: non disturbare l'utente

        threading.Thread(target=_invia, daemon=True).start()

    # =========================================================================
    #  CONNESSIONE HOTSPOT Wi-Fi (admin)
    # =========================================================================
    def _wifi_force_rescan(self):
        """Forza una nuova scansione radio Wi-Fi (Windows API / Linux nmcli)."""
        import time
        if sys.platform != "win32":
            # Linux (uConsole / Raspberry Pi)
            try:
                subprocess.run(["nmcli", "device", "wifi", "rescan"],
                               capture_output=True, timeout=10)
                time.sleep(3)  # RPi ha hardware WiFi lento
            except Exception:
                pass
            return
        try:
            import ctypes
            wlanapi = ctypes.windll.wlanapi

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                            ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

            class WLAN_INTERFACE_INFO(ctypes.Structure):
                _fields_ = [("InterfaceGuid", GUID),
                            ("strInterfaceDescription", ctypes.c_wchar * 256),
                            ("isState", ctypes.c_uint)]

            class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
                _fields_ = [("dwNumberOfItems", ctypes.c_uint),
                            ("dwIndex", ctypes.c_uint),
                            ("InterfaceInfo", WLAN_INTERFACE_INFO * 1)]

            hClient = ctypes.c_void_p()
            negotiated = ctypes.c_uint()
            ret = wlanapi.WlanOpenHandle(2, None, ctypes.byref(negotiated), ctypes.byref(hClient))
            if ret != 0:
                return

            pIfList = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
            ret = wlanapi.WlanEnumInterfaces(hClient, None, ctypes.byref(pIfList))
            if ret == 0 and pIfList.contents.dwNumberOfItems > 0:
                guid = pIfList.contents.InterfaceInfo[0].InterfaceGuid
                wlanapi.WlanScan(hClient, ctypes.byref(guid), None, None, None)

            wlanapi.WlanFreeMemory(pIfList)
            wlanapi.WlanCloseHandle(hClient, None)

            import time
            time.sleep(6)  # Windows tiene cache WiFi, serve piu' tempo
        except Exception:
            pass  # Se l'API nativa fallisce, prosegui con i dati cache

    def _wifi_scan(self):
        """Scansiona reti Wi-Fi disponibili. Ritorna (lista, errore).
        lista = [{ssid, signal, security}, ...]
        errore = stringa errore o None
        """
        reti = []
        try:
            if sys.platform == "win32":
                # Forza rescan radio prima di leggere
                self._wifi_force_rescan()
                r = subprocess.run(["netsh", "wlan", "show", "networks", "mode=Bssid"],
                                   capture_output=True, timeout=15,
                                   encoding="cp850", errors="replace",
                                   creationflags=0x08000000)  # CREATE_NO_WINDOW
                if r.returncode != 0:
                    err = r.stderr.strip() if r.stderr else "netsh errore %d" % r.returncode
                    return [], err
                ssid = signal = security = ""
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if not line: continue
                    if line.startswith("SSID") and "BSSID" not in line:
                        if ssid:
                            reti.append({"ssid": ssid, "signal": signal, "security": security})
                        parts = line.split(":", 1)
                        ssid = parts[1].strip() if len(parts) > 1 else ""
                        signal = security = ""
                    elif any(k in line for k in ("Autenticazione", "Authentication", "autenticazione")):
                        parts = line.split(":", 1)
                        security = parts[1].strip() if len(parts) > 1 else ""
                    elif any(k in line for k in ("Segnale", "Signal", "segnale")):
                        parts = line.split(":", 1)
                        signal = parts[1].strip().replace("%", "").strip() if len(parts) > 1 else "0"
                if ssid:
                    reti.append({"ssid": ssid, "signal": signal, "security": security})
            else:
                # Linux (uConsole / Raspberry Pi) — rescan gia' fatto in _wifi_force_rescan
                self._wifi_force_rescan()
                r = subprocess.run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
                                    "device", "wifi", "list", "--rescan", "no"],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode != 0:
                    return [], r.stderr.strip() or "nmcli errore %d" % r.returncode
                import re
                for line in r.stdout.splitlines():
                    if not line.strip(): continue
                    # nmcli -t usa : come separatore, ma escapa \: nei valori
                    parti = re.split(r'(?<!\\):', line)
                    parti = [p.replace('\\:', ':') for p in parti]
                    if len(parti) >= 3 and parti[0]:
                        reti.append({"ssid": parti[0], "signal": parti[1], "security": parti[2]})
        except subprocess.TimeoutExpired:
            return [], "Timeout scansione (15s)"
        except FileNotFoundError:
            return [], "Comando non trovato (netsh/nmcli)"
        except Exception as e:
            return [], str(e)
        # Rimuovi duplicati, ordina per segnale decrescente
        visti = set()
        uniche = []
        for r in reti:
            if r["ssid"] and r["ssid"] not in visti:
                visti.add(r["ssid"])
                uniche.append(r)
        uniche.sort(key=lambda x: int(x.get("signal", "0") or "0"), reverse=True)
        return uniche, None

    def _wifi_connect(self, ssid, password=None):
        """Connetti a rete Wi-Fi. Ritorna (ok, messaggio)."""
        try:
            if sys.platform == "win32":
                # Crea profilo XML temporaneo
                if password:
                    auth = "WPA2PSK"
                    encr = "AES"
                    xml = ('<?xml version="1.0"?>'
                           '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
                           '<name>%s</name><SSIDConfig><SSID><name>%s</name></SSID></SSIDConfig>'
                           '<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>'
                           '<MSM><security><authEncryption>'
                           '<authentication>%s</authentication><encryption>%s</encryption>'
                           '<useOneX>false</useOneX></authEncryption>'
                           '<sharedKey><keyType>passPhrase</keyType><protected>false</protected>'
                           '<keyMaterial>%s</keyMaterial></sharedKey>'
                           '</security></MSM></WLANProfile>' % (ssid, ssid, auth, encr, password))
                else:
                    xml = ('<?xml version="1.0"?>'
                           '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
                           '<name>%s</name><SSIDConfig><SSID><name>%s</name></SSID></SSIDConfig>'
                           '<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>'
                           '<MSM><security><authEncryption>'
                           '<authentication>open</authentication><encryption>none</encryption>'
                           '<useOneX>false</useOneX></authEncryption>'
                           '</security></MSM></WLANProfile>' % (ssid, ssid))
                # Salva profilo temporaneo
                tmp = os.path.join(os.environ.get("TEMP", "."), "_wifi_profile.xml")
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(xml)
                subprocess.run(["netsh", "wlan", "add", "profile", "filename=%s" % tmp],
                               capture_output=True, timeout=10)
                os.remove(tmp)
                r = subprocess.run(["netsh", "wlan", "connect", "name=%s" % ssid],
                                   capture_output=True, text=True, timeout=15)
                if r.returncode == 0:
                    return True, "Connesso a %s" % ssid
                return False, r.stdout.strip() or r.stderr.strip() or "Connessione fallita"
            else:
                # Linux (nmcli)
                cmd = ["nmcli", "device", "wifi", "connect", ssid]
                if password:
                    cmd += ["password", password]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                if r.returncode == 0:
                    return True, "Connesso a %s" % ssid
                return False, r.stderr.strip() or r.stdout.strip() or "Connessione fallita"
        except subprocess.TimeoutExpired:
            return False, "Timeout connessione"
        except Exception as e:
            return False, str(e)

    def _wifi_stato(self):
        """Ritorna (connesso, ssid_corrente)."""
        try:
            if sys.platform == "win32":
                r = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                                   capture_output=True, text=True, timeout=5)
                ssid = ""
                stato = ""
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("SSID") and "BSSID" not in line:
                        ssid = line.split(":", 1)[1].strip()
                    elif "Stato" in line or "State" in line:
                        stato = line.split(":", 1)[1].strip().lower()
                return ("conness" in stato or "connected" in stato, ssid)
            else:
                r = subprocess.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
                                   capture_output=True, text=True, timeout=5)
                for line in r.stdout.splitlines():
                    parti = line.split(":")
                    if len(parti) >= 2 and "wireless" in parti[1].lower():
                        return (True, parti[0])
                return (False, "")
        except Exception:
            return (False, "")

    def _wifi_monitor(self):
        """Controlla WiFi ogni 30 sec. Se offline, avvisa nel titolo."""
        try:
            connesso, ssid = self._wifi_stato()
            titolo_base = _nome_base(self.conf.get("nome_database", "RetroDB")) + "  v" + __version__
            if not connesso and self._wifi_online:
                # Appena caduta
                self._wifi_online = False
                self.root.title("[OFFLINE] " + titolo_base)
            elif connesso and not self._wifi_online:
                # Appena ripristinata
                self._wifi_online = True
                self.root.title(titolo_base)
        except Exception:
            pass
        self.root.after(30000, self._wifi_monitor)

    def _bt_printer_monitor(self):
        """Verifica stampante BT in background. Non blocca la UI.
        Se il MAC e' in conf.dat, usa solo quello (no scan).
        Lo scan avviene solo alla prima installazione (MAC vuoto)."""
        import threading

        def _cerca():
            mac_conf = self.conf.get("stampante_bt", "").strip()
            dev, mac, err = _bt_auto_setup(mac_conf)
            if dev and not err:
                self._bt_stampante_ok = True
                self._bt_stampante_nome = mac or "rfcomm0"
                # Prima installazione: salva MAC trovato in conf.dat
                if mac and not mac_conf and ":" in mac:
                    try:
                        self.conf["stampante_bt"] = mac
                        salva_conf(self.conf)
                    except Exception:
                        pass
            else:
                self._bt_stampante_ok = False
                self._bt_stampante_nome = ""
            self.root.after(0, self._aggiorna_label_stampante)

        t = threading.Thread(target=_cerca, daemon=True)
        t.start()
        # Se MAC configurato: check veloce ogni 15s (no scan, istantaneo)
        # Se MAC non configurato: scan ogni 10s (prima installazione)
        mac_conf = self.conf.get("stampante_bt", "").strip()
        intervallo = 15000 if mac_conf else 10000
        self.root.after(intervallo, self._bt_printer_monitor)

    def _aggiorna_label_stampante(self):
        """Aggiorna la label dello stato stampante se presente e ancora valida."""
        if hasattr(self, '_lbl_stampante') and self._lbl_stampante:
            try:
                self._lbl_stampante.winfo_exists()  # Verifica che il widget esista ancora
            except Exception:
                self._lbl_stampante = None
                return
            if not self._lbl_stampante.winfo_exists():
                self._lbl_stampante = None
                return
            c = carica_colori()
            if self._bt_stampante_ok:
                self._lbl_stampante.config(text="  |  Stampante: ON",
                                           fg=c["stato_ok"])
            else:
                self._lbl_stampante.config(text="  |  Stampante: OFF",
                                           fg=c["stato_errore"])

    def _schermata_hotspot(self):
        """Schermata connessione hotspot Wi-Fi."""
        self._pulisci(); c = carica_colori()

        # Header
        header = tk.Frame(self._vista, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))
        tk.Button(header, text="< MENU", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._wifi_esci).pack(side="left")
        tk.Label(header, text="  CONNESSIONE HOTSPOT", bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(_S(8), 0))

        # Stato attuale
        connesso, ssid_attuale = self._wifi_stato()
        stato_txt = "Connesso a: %s" % ssid_attuale if connesso else "Non connesso"
        stato_fg = c["stato_ok"] if connesso else c["stato_errore"]
        self._wifi_stato_label = tk.Label(header, text=stato_txt, bg=c["sfondo"], fg=stato_fg,
                                           font=self._f_small)
        self._wifi_stato_label.pack(side="right")

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(2)))

        # Status scan
        self._wifi_status = tk.Label(self._vista, text="Scansione reti in corso...",
                                      bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small)
        self._wifi_status.pack(pady=(_S(4), _S(2)))

        # Lista reti
        lista_frame = tk.Frame(self._vista, bg=c["sfondo"])
        lista_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(2), _S(4)))

        self._wifi_listbox = tk.Listbox(lista_frame, font=self._f_list,
                                         bg=c["sfondo_celle"], fg=c["dati"],
                                         selectbackground=c["cursore"], selectforeground=c["testo_cursore"],
                                         highlightthickness=1, highlightbackground=c["bordo_vuote"],
                                         relief="flat", exportselection=False)
        self._wifi_listbox.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(lista_frame, orient="vertical", command=self._wifi_listbox.yview)
        sb.pack(side="right", fill="y")
        self._wifi_listbox.configure(yscrollcommand=sb.set)

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))

        # Etichetta rete selezionata
        sel_bar = tk.Frame(self._vista, bg=c["sfondo"])
        sel_bar.pack(fill="x", padx=_S(10), pady=(_S(2), _S(0)))
        tk.Label(sel_bar, text="RETE:", bg=c["sfondo"], fg=c["label"],
                 font=self._f_btn).pack(side="left")
        self._wifi_sel_label = tk.Label(sel_bar, text="(nessuna selezionata)",
                                         bg=c["sfondo"], fg=c["testo_dim"], font=self._f_btn)
        self._wifi_sel_label.pack(side="left", padx=(_S(4), 0))

        # Bind selezione listbox
        self._wifi_listbox.bind("<<ListboxSelect>>", self._wifi_on_select)
        self._wifi_listbox.bind("<Double-Button-1>", lambda e: self._wifi_pwd_entry.focus_set())
        self._wifi_listbox.bind("<Return>", lambda e: self._wifi_pwd_entry.focus_set())

        # Barra password + connetti
        conn_bar = tk.Frame(self._vista, bg=c["sfondo"])
        conn_bar.pack(fill="x", padx=_S(10), pady=(_S(2), _S(4)))

        tk.Label(conn_bar, text="Password:", bg=c["sfondo"], fg=c["label"],
                 font=self._f_label).pack(side="left")
        self._wifi_pwd_entry = tk.Entry(conn_bar, font=self._f_label, width=20, show="*",
                                         bg=c["sfondo_celle"], fg=c["dati"],
                                         insertbackground=c["dati"], relief="flat",
                                         highlightthickness=1, highlightbackground=c["bordo_vuote"])
        self._wifi_pwd_entry.pack(side="left", padx=(_S(4), _S(8)))

        tk.Button(conn_bar, text="CONNETTI", font=self._f_btn, width=_S(10),
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._wifi_connetti).pack(side="left", padx=_S(4))

        tk.Button(conn_bar, text="AGGIORNA", font=self._f_btn, width=_S(10),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._wifi_aggiorna).pack(side="left", padx=_S(4))

        # Bind Enter e password
        self._wifi_pwd_entry.bind("<Return>", lambda e: self._wifi_connetti())

        self.root.bind("<Escape>", lambda e: self._wifi_esci())

        # Avvia scan + auto-refresh ogni 15 sec
        self._wifi_reti = []
        self._wifi_auto_refresh = True
        self.root.after(100, self._wifi_aggiorna)
        self._rimuovi_coperta()

    def _wifi_esci(self):
        """Esce dalla schermata HOTSPOT e ferma auto-refresh."""
        self._wifi_auto_refresh = False
        self._schermata_menu()

    def _wifi_on_select(self, event=None):
        """Aggiorna etichetta rete selezionata."""
        c = carica_colori()
        sel = self._wifi_listbox.curselection()
        if sel and self._wifi_reti:
            rete = self._wifi_reti[sel[0]]
            self._wifi_sel_label.config(text=rete["ssid"], fg=c["dati"])
        else:
            self._wifi_sel_label.config(text="(nessuna selezionata)", fg=c["testo_dim"])

    def _wifi_aggiorna(self):
        """Avvia scansione reti in background (non blocca la UI)."""
        try:
            self._wifi_listbox.winfo_exists()
        except (tk.TclError, AttributeError):
            return
        # Evita scan sovrapposti
        if getattr(self, '_wifi_scanning', False):
            return
        c = carica_colori()
        self._wifi_status.config(text="Scansione reti in corso...", fg=c["testo_dim"])
        self._wifi_scan_dots = 0

        # Animazione puntini durante la scan
        def _anima():
            try:
                if not self._wifi_listbox.winfo_exists(): return
            except (tk.TclError, AttributeError):
                return
            if hasattr(self, '_wifi_scanning') and self._wifi_scanning:
                self._wifi_scan_dots = (self._wifi_scan_dots + 1) % 4
                dots = "." * self._wifi_scan_dots
                self._wifi_status.config(text="Scansione reti in corso%s" % dots)
                self.root.after(400, _anima)

        # Scan in background
        self._wifi_scanning = True
        self._wifi_scan_result = None
        _anima()

        def _do_scan():
            self._wifi_scan_result = self._wifi_scan()
            self._wifi_scanning = False

        threading.Thread(target=_do_scan, daemon=True).start()

        # Poll risultati
        def _check_result():
            if self._wifi_scanning:
                self.root.after(200, _check_result)
                return
            try:
                self._wifi_listbox.winfo_exists()
            except (tk.TclError, AttributeError):
                return
            reti, errore = self._wifi_scan_result or ([], "Errore sconosciuto")
            # Ricorda selezione attuale
            old_ssid = ""
            old_sel = self._wifi_listbox.curselection()
            if old_sel and self._wifi_reti:
                old_ssid = self._wifi_reti[old_sel[0]].get("ssid", "")
            self._wifi_reti = reti
            try:
                self._wifi_listbox.delete(0, "end")
            except (tk.TclError, AttributeError):
                return
            if errore:
                self._wifi_status.config(text="Errore: %s" % errore, fg=c["stato_errore"])
            elif self._wifi_reti:
                for r in self._wifi_reti:
                    sec = " [%s]" % r["security"] if r["security"] else " [Aperta]"
                    self._wifi_listbox.insert("end", "  %s%%  %s%s" % (
                        r["signal"].rjust(3), r["ssid"], sec))
                self._wifi_status.config(text="%d reti trovate" % len(self._wifi_reti), fg=c["stato_ok"])
                # Ripristina selezione se SSID ancora presente
                sel_idx = 0
                if old_ssid:
                    for i, r in enumerate(self._wifi_reti):
                        if r["ssid"] == old_ssid:
                            sel_idx = i
                            break
                self._wifi_listbox.selection_set(sel_idx)
                self._wifi_listbox.activate(sel_idx)
                self._wifi_listbox.see(sel_idx)
                self._wifi_on_select()
            else:
                self._wifi_status.config(text="Nessuna rete trovata - premi AGGIORNA",
                                          fg=c["stato_avviso"])
            # Aggiorna stato connessione
            connesso, ssid_attuale = self._wifi_stato()
            stato_txt = "Connesso a: %s" % ssid_attuale if connesso else "Non connesso"
            stato_fg = c["stato_ok"] if connesso else c["stato_errore"]
            self._wifi_stato_label.config(text=stato_txt, fg=stato_fg)

            # Auto-refresh ogni 20 secondi
            if getattr(self, '_wifi_auto_refresh', False):
                self.root.after(20000, self._wifi_aggiorna)

        self.root.after(300, _check_result)

    def _wifi_connetti(self):
        """Connetti alla rete selezionata."""
        c = carica_colori()
        sel = self._wifi_listbox.curselection()
        if not sel or not self._wifi_reti:
            self._wifi_status.config(text="Seleziona una rete!", fg=c["stato_avviso"])
            return
        rete = self._wifi_reti[sel[0]]
        pwd = self._wifi_pwd_entry.get().strip()
        self._wifi_status.config(text="Connessione a %s..." % rete["ssid"], fg=c["testo_dim"])
        self.root.update()
        ok, msg = self._wifi_connect(rete["ssid"], pwd if pwd else None)
        if ok:
            self._wifi_status.config(text=msg, fg=c["stato_ok"])
            self._wifi_stato_label.config(text="Connesso a: %s" % rete["ssid"], fg=c["stato_ok"])
            self._notifica_connessione()  # Notifica anti-copia
        else:
            self._wifi_status.config(text=msg, fg=c["stato_errore"])

    # =========================================================================
    #  AGGIORNAMENTO AUTOMATICO (background)
    # =========================================================================
    def _auto_check_aggiornamenti(self):
        """Check automatico aggiornamenti da GitHub in background.
        Se trova una versione nuova, scarica e propone riavvio."""
        # Se gia' scaricato in questa sessione, non ricontrollare
        if getattr(self, '_aggiornamento_scaricato', False):
            return
        def _worker():
            try:
                disp, info, msg = controlla_aggiornamento_github(APP_VERSION)
                if disp:
                    # Aggiornamento trovato: scarica in background
                    base = self._get_base()
                    ok, risultato, files = scarica_aggiornamento_github(
                        info, base, self.percorsi.get("backup"))
                    if ok:
                        self._aggiornamento_scaricato = True
                        # Torna al thread principale per mostrare popup riavvio
                        self.root.after(0, lambda: self._popup_riavvio(info))
            except Exception:
                pass  # Silenzioso: se la rete non c'e', riprova dopo

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _popup_riavvio(self, info):
        """Mostra popup aggiornamento con conto alla rovescia e riavvio automatico."""
        c = carica_colori()
        versione = info.get("version", "?")
        nota = info.get("note", "")

        # Crea finestra popup sopra tutto
        popup = tk.Toplevel(self.root)
        popup.title("Aggiornamento")
        popup.configure(bg=c["sfondo"])
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()

        # Centra rispetto alla finestra principale
        popup.update_idletasks()
        pw, ph = 380, 200
        rx = self.root.winfo_x() + (self.root.winfo_width() - pw) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - ph) // 2
        popup.geometry("%dx%d+%d+%d" % (pw, ph, rx, ry))

        tk.Label(popup, text="AGGIORNAMENTO INSTALLATO",
                 bg=c["sfondo"], fg=c["stato_ok"], font=self._f_title).pack(pady=(_S(15), _S(5)))
        tk.Label(popup, text="Versione %s scaricata" % versione,
                 bg=c["sfondo"], fg=c["dati"], font=self._f_label).pack()
        if nota:
            tk.Label(popup, text=nota, bg=c["sfondo"], fg=c["testo_dim"],
                     font=self._f_small).pack(pady=(_S(2), _S(8)))

        # Conto alla rovescia per riavvio automatico
        lbl_count = tk.Label(popup, text="Riavvio automatico tra 5...",
                             bg=c["sfondo"], fg=c["stato_avviso"], font=self._f_label)
        lbl_count.pack(pady=(_S(5), _S(8)))

        self._countdown_annullato = False

        def _countdown(sec):
            if self._countdown_annullato:
                return
            if sec <= 0:
                riavvia_app()
                return
            lbl_count.config(text="Riavvio automatico tra %d..." % sec)
            popup.after(1000, lambda: _countdown(sec - 1))

        bar = tk.Frame(popup, bg=c["sfondo"])
        bar.pack(pady=_S(5))
        tk.Button(bar, text="RIAVVIA ORA", font=self._f_btn, width=14,
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=riavvia_app).pack(side="left", padx=_S(4))

        def _annulla():
            self._countdown_annullato = True
            lbl_count.config(text="Riavvio rimandato. Riavvia manualmente.")
            popup.after(3000, popup.destroy)

        tk.Button(bar, text="DOPO", font=self._f_btn, width=10,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=_annulla).pack(side="left", padx=_S(4))

        # Avvia conto alla rovescia da 5 secondi
        _countdown(5)

    # =========================================================================
    #  AGGIORNAMENTO SOFTWARE (USB)
    # =========================================================================
    def _schermata_aggiorna(self):
        """Cerca aggiornamenti su USB e li applica."""
        self._pulisci(); c = carica_colori()

        header = tk.Frame(self._vista, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))
        tk.Button(header, text="< MENU", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._schermata_menu).pack(side="left")
        tk.Label(header, text="  AGGIORNAMENTO SOFTWARE", bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(_S(8), 0))
        tk.Label(header, text="v%s" % APP_VERSION, bg=c["sfondo"], fg=c["stato_avviso"],
                 font=self._f_small).pack(side="right")

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(4)))

        self._upd_status = tk.Label(self._vista, text="Ricerca aggiornamenti su USB...",
                                     bg=c["sfondo"], fg=c["testo_dim"], font=self._f_label)
        self._upd_status.pack(pady=(_S(8), _S(4)))

        # Lista aggiornamenti trovati
        lista_frame = tk.Frame(self._vista, bg=c["sfondo"])
        lista_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(2), _S(4)))

        self._upd_listbox = tk.Listbox(lista_frame, font=self._f_list,
                                        bg=c["sfondo_celle"], fg=c["dati"],
                                        selectbackground=c["cursore"], selectforeground=c["testo_cursore"],
                                        highlightthickness=1, highlightbackground=c["bordo_vuote"],
                                        relief="flat", exportselection=False, height=8)
        self._upd_listbox.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(lista_frame, orient="vertical", command=self._upd_listbox.yview)
        sb.pack(side="right", fill="y")
        self._upd_listbox.configure(yscrollcommand=sb.set)

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))

        # Bottoni
        bar = tk.Frame(self._vista, bg=c["sfondo"])
        bar.pack(pady=(_S(4), _S(4)))
        _btns = []

        btn_applica = tk.Button(bar, text="APPLICA", font=self._f_btn, width=_S(12),
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._applica_aggiornamento)
        btn_applica.pack(side="left", padx=_S(3))
        _btns.append(btn_applica)

        btn_ricarica = tk.Button(bar, text="RICARICA", font=self._f_btn, width=_S(12),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._cerca_aggiornamenti)
        btn_ricarica.pack(side="left", padx=_S(3))
        _btns.append(btn_ricarica)

        self._kb_setup_bottoni(_btns, orizzontale=True)
        self.root.bind("<Escape>", lambda e: self._schermata_menu())

        # Avvia ricerca
        self._upd_risultati = []
        self.root.after(200, self._cerca_aggiornamenti)
        self._rimuovi_coperta()

    def _cerca_aggiornamenti(self):
        """Cerca aggiornamenti: prima GitHub (rete), poi USB."""
        c = carica_colori()
        try:
            self._upd_listbox.winfo_exists()
        except (tk.TclError, AttributeError):
            return
        self._upd_status.config(text="Ricerca aggiornamenti online...", fg=c["testo_dim"])
        self.root.update()

        self._upd_risultati = []
        self._upd_github_info = None
        self._upd_listbox.delete(0, "end")

        # 1. Cerca su GitHub
        try:
            disp, info, msg = controlla_aggiornamento_github(APP_VERSION)
            if disp:
                self._upd_github_info = info
                nota = "  (%s)" % info.get("note", "") if info.get("note") else ""
                n_files = len(info.get("files", {}))
                self._upd_listbox.insert("end",
                    "  [ONLINE] v%s  |  %s  |  %d file  |  NUOVO%s" % (
                    info["version"], info.get("date", "?"), n_files, nota))
        except Exception:
            pass

        # 2. Cerca su USB
        self._upd_status.config(text="Ricerca USB...", fg=c["testo_dim"])
        self.root.update()

        self._upd_risultati = cerca_aggiornamento_usb()
        for r in self._upd_risultati:
            valido, _, _ = verifica_aggiornamento(r["zip_path"], APP_VERSION)
            stato = "NUOVO" if valido else "gia' installata"
            nota = "  (%s)" % r["note"] if r.get("note") else ""
            self._upd_listbox.insert("end", "  [USB] v%s  |  %s  |  %s  |  %s%s" % (
                r["version"], r["date"], r["usb_label"], stato, nota))

        # 3. Risultato finale
        tot = self._upd_listbox.size()
        if tot > 0:
            self._upd_status.config(
                text="%d aggiornamenti trovati - seleziona e premi APPLICA" % tot,
                fg=c["stato_ok"])
            self._upd_listbox.selection_set(0)
            self._upd_listbox.focus_set()
        else:
            self._upd_status.config(
                text="Nessun aggiornamento disponibile (v%s e' corrente)" % APP_VERSION,
                fg=c["stato_avviso"])

    def _applica_aggiornamento(self):
        """Applica l'aggiornamento selezionato (GitHub o USB)."""
        c = carica_colori()
        sel = self._upd_listbox.curselection()
        if not sel:
            self._upd_status.config(text="Seleziona un aggiornamento!", fg=c["stato_avviso"])
            return

        # Identifica se e' GitHub (indice 0 e github_info presente) o USB
        idx = sel[0]
        is_github = (idx == 0 and self._upd_github_info is not None)

        if is_github:
            versione = self._upd_github_info.get("version", "?")
        else:
            usb_idx = idx - (1 if self._upd_github_info else 0)
            if usb_idx < 0 or usb_idx >= len(self._upd_risultati):
                self._upd_status.config(text="Selezione non valida!", fg=c["stato_errore"])
                return
            info_usb = self._upd_risultati[usb_idx]
            valido, vinfo, msg = verifica_aggiornamento(info_usb["zip_path"], APP_VERSION)
            if not valido:
                self._upd_status.config(text=msg, fg=c["stato_errore"])
                return
            versione = vinfo.get("version", "?")

        # Doppia pressione per conferma
        import time
        now = time.time()
        if not hasattr(self, '_upd_conferma') or now - self._upd_conferma > 4:
            self._upd_conferma = now
            fonte = "ONLINE" if is_github else "USB"
            self._upd_status.config(
                text="Aggiornare a v%s (%s)? Premi APPLICA di nuovo" % (versione, fonte),
                fg=c["stato_avviso"])
            return
        del self._upd_conferma

        # Applica
        self._upd_status.config(text="Aggiornamento in corso...", fg=c["testo_dim"])
        self.root.update()

        base = self._get_base()

        if is_github:
            # Aggiornamento da GitHub con callback per UI
            def _callback(msg):
                try:
                    self._upd_status.config(text=msg, fg=c["testo_dim"])
                    self.root.update()
                except (tk.TclError, AttributeError):
                    pass
            ok, msg, files = scarica_aggiornamento_github(
                self._upd_github_info, base, self.percorsi.get("backup"), _callback)
        else:
            # Aggiornamento da USB
            ok, msg, files = applica_aggiornamento(
                info_usb["zip_path"], base, self.percorsi.get("backup"))

        if ok:
            self._upd_status.config(text="%s - RIAVVIO..." % msg, fg=c["stato_ok"])
            self.root.update()
            self.root.after(2000, riavvia_app)
        else:
            self._upd_status.config(text=msg, fg=c["stato_errore"])

    def _schermata_prepara_aggiornamento(self):
        """Prepara zip aggiornamento su USB (lato sviluppatore)."""
        self._pulisci(); c = carica_colori()

        header = tk.Frame(self._vista, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))
        tk.Button(header, text="< MENU", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._schermata_menu).pack(side="left")
        tk.Label(header, text="  PREPARA AGGIORNAMENTO v%s" % APP_VERSION, bg=c["sfondo"],
                 fg=c["dati"], font=self._f_title).pack(side="left", padx=(_S(8), 0))

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(4)))

        # File che verranno inclusi
        base = self._get_base()
        files = get_app_files(base)

        tk.Label(self._vista, text="File inclusi nello zip:", bg=c["sfondo"],
                 fg=c["label"], font=self._f_label).pack(anchor="w", padx=_S(10), pady=(_S(4), _S(2)))

        file_frame = tk.Frame(self._vista, bg=c["sfondo"])
        file_frame.pack(fill="x", padx=_S(10))
        file_list = tk.Listbox(file_frame, font=self._f_list, height=6,
                                bg=c["sfondo_celle"], fg=c["dati"],
                                highlightthickness=1, highlightbackground=c["bordo_vuote"],
                                relief="flat")
        file_list.pack(side="left", fill="x", expand=True)
        for f in files:
            size = os.path.getsize(os.path.join(base, f)) // 1024
            file_list.insert("end", "  %s  (%d KB)" % (f, size))

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(4)))

        # Destinazione: USB trovate
        tk.Label(self._vista, text="Destinazione:", bg=c["sfondo"],
                 fg=c["label"], font=self._f_label).pack(anchor="w", padx=_S(10), pady=(_S(4), _S(2)))

        dest_frame = tk.Frame(self._vista, bg=c["sfondo"])
        dest_frame.pack(fill="x", padx=_S(10))
        self._prep_dest_lb = tk.Listbox(dest_frame, font=self._f_list, height=4,
                                         bg=c["sfondo_celle"], fg=c["dati"],
                                         selectbackground=c["cursore"], selectforeground=c["testo_cursore"],
                                         highlightthickness=1, highlightbackground=c["bordo_vuote"],
                                         relief="flat", exportselection=False)
        self._prep_dest_lb.pack(side="left", fill="x", expand=True)

        self._prep_unita = _trova_unita_usb()
        # Aggiungi anche la cartella locale come opzione
        self._prep_unita.append({"path": base, "label": "LOCALE (cartella progetto)"})
        for u in self._prep_unita:
            self._prep_dest_lb.insert("end", "  %s  [%s]" % (u["label"], u["path"]))
        if self._prep_unita:
            self._prep_dest_lb.selection_set(0)

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(4)))

        self._prep_status = tk.Label(self._vista, text="%d file pronti - seleziona destinazione e premi CREA" % len(files),
                                      bg=c["sfondo"], fg=c["testo_dim"], font=self._f_label)
        self._prep_status.pack(pady=(_S(4), _S(4)))

        # Bottone
        bar = tk.Frame(self._vista, bg=c["sfondo"])
        bar.pack(pady=(_S(4), _S(4)))

        btn_crea = tk.Button(bar, text="CREA ZIP", font=self._f_btn, width=_S(14),
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._esegui_prepara_aggiornamento)
        btn_crea.pack(side="left", padx=_S(3))
        self._kb_setup_bottoni([btn_crea], orizzontale=True)

        self.root.bind("<Escape>", lambda e: self._schermata_menu())
        self._rimuovi_coperta()

    def _esegui_prepara_aggiornamento(self):
        """Crea lo zip di aggiornamento."""
        c = carica_colori()
        sel = self._prep_dest_lb.curselection()
        if not sel or not self._prep_unita:
            self._prep_status.config(text="Seleziona una destinazione!", fg=c["stato_avviso"])
            return

        dest = self._prep_unita[sel[0]]["path"]
        base = self._get_base()

        self._prep_status.config(text="Creazione zip...", fg=c["testo_dim"])
        self.root.update()

        ok, msg, zip_path = prepara_aggiornamento(dest, APP_VERSION, base)
        if ok:
            self._prep_status.config(text="Creato: %s" % msg, fg=c["stato_ok"])
        else:
            self._prep_status.config(text=msg, fg=c["stato_errore"])

    # =========================================================================
    #  BACKUP E RIPRISTINO
    # =========================================================================
    def _esegui_backup(self, force=False):
        """Backup completo: dati + definizioni + utenti -> file ZIP. Solo admin (o force per shutdown)."""
        if not force and not is_admin(self.sessione):
            return

        c = carica_colori()
        # Feedback: bottone rosso durante operazione
        if hasattr(self, '_btn_backup'):
            try:
                self._btn_backup.config(bg=c["stato_errore"], fg="#ffffff", text="BACKUP...")
                self.root.update()
            except: pass

        base = self._get_base()
        backup_dir = self.percorsi.get("backup", os.path.join(base, "backup"))
        os.makedirs(backup_dir, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_db = self.conf.get("nome_database", "database").replace(" ", "_")
        zip_name = "%s_backup_%s.zip" % (nome_db, ts)
        dest = os.path.join(backup_dir, zip_name)

        try:
            conteggio = 0
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
                # Dati (tutti i .json)
                dati_dir = self.percorsi.get("dati", os.path.join(base, "dati"))
                if os.path.isdir(dati_dir):
                    for f in os.listdir(dati_dir):
                        if f.endswith(".json"):
                            zf.write(os.path.join(dati_dir, f), "dati/%s" % f)
                            conteggio += 1

                # Definizioni (tutti i .def)
                def_dir = self.percorsi.get("definizioni", os.path.join(base, "definizioni"))
                if os.path.isdir(def_dir):
                    for f in os.listdir(def_dir):
                        if f.endswith(".def"):
                            zf.write(os.path.join(def_dir, f), "definizioni/%s" % f)
                            conteggio += 1

                # Configurazione
                conf_path = os.path.join(base, "conf.dat")
                if os.path.exists(conf_path):
                    zf.write(conf_path, "conf.dat"); conteggio += 1

                # Colori
                colori_path = os.path.join(base, "colori.json")
                if os.path.exists(colori_path):
                    zf.write(colori_path, "colori.json"); conteggio += 1

                # Info backup
                info = {
                    "data_backup": datetime.now().isoformat(),
                    "utente": get_display_name(self.sessione),
                    "versione": APP_VERSION,
                    "nome_database": self.conf.get("nome_database", ""),
                    "file_inclusi": conteggio,
                }
                zf.writestr("_backup_info.json", json.dumps(info, ensure_ascii=False, indent=2))

            size_kb = os.path.getsize(dest) / 1024

            # Pulizia: mantieni solo gli ultimi 2 backup completi
            prefix = "%s_backup_" % nome_db
            vecchi = sorted([f for f in os.listdir(backup_dir)
                             if f.startswith(prefix) and f.endswith(".zip")])
            while len(vecchi) > 2:
                os.remove(os.path.join(backup_dir, vecchi.pop(0)))

            msg = "Backup OK: %s (%.1f KB, %d file)" % (zip_name, size_kb, conteggio)
            if hasattr(self, "_menu_status"):
                self._menu_status.config(text=msg, fg=c["stato_ok"])
            # Reset bottone e status dopo 3 secondi
            self._backup_reset_timer()

            # Invia backup via email agli admin che hanno un'email configurata
            self._invia_backup_email(dest, zip_name, conteggio, size_kb)

        except Exception as e:
            msg = "ERRORE Backup: %s" % str(e)
            if hasattr(self, "_menu_status"):
                self._menu_status.config(text=msg, fg=c["stato_errore"])
            self._backup_reset_timer()

    def _backup_reset_timer(self):
        """Reset bottone BACKUP e status dopo 3 secondi."""
        def _reset():
            c = carica_colori()
            if hasattr(self, '_btn_backup'):
                try:
                    self._btn_backup.config(bg=c["pulsanti_sfondo"], fg=c["stato_ok"], text="BACKUP")
                except: pass
            if hasattr(self, '_menu_status'):
                try:
                    self._menu_status.config(text="", fg=c["testo_dim"])
                except: pass
        self.root.after(3000, _reset)

    def _invia_backup_email(self, zip_path, zip_name, conteggio, size_kb):
        """Invia il backup ZIP via email a tutti gli admin con email.
        Esecuzione in background, silente."""
        smtp_srv = self.conf.get("smtp_server", "").strip()
        smtp_usr = self.conf.get("smtp_user", "").strip()
        smtp_pwd = self.conf.get("smtp_password", "").strip()
        smtp_port = int(self.conf.get("smtp_port", 587))
        if not smtp_srv or not smtp_usr or not smtp_pwd:
            return

        # Trova tutti gli admin con email
        destinatari = []
        utenti = carica_utenti()
        for u in utenti:
            if str(u.get("Admin", "0")).strip() in ("1", "X", "x", "S", "V", "si", "vero", "true"):
                email = str(u.get("Email", "")).strip()
                if email and "@" in email:
                    destinatari.append(email)

        if not destinatari:
            print("[BACKUP EMAIL] Nessun admin con email trovato")
            return

        print("[BACKUP EMAIL] Invio a: %s" % ", ".join(destinatari))

        nome_db = self.conf.get("nome_database", "RetroDB")
        utente = get_display_name(self.sessione) if self.sessione else "(sistema)"
        ora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        def _invia():
            try:
                import smtplib
                from email.mime.multipart import MIMEMultipart
                from email.mime.text import MIMEText
                from email.mime.base import MIMEBase
                from email import encoders

                corpo = (
                    "BACKUP AUTOMATICO\n"
                    "==================\n"
                    "Database: %s v%s\n"
                    "Data: %s\n"
                    "Eseguito da: %s\n"
                    "File inclusi: %d\n"
                    "Dimensione: %.1f KB\n"
                    "\nIl file ZIP e' allegato a questa email.\n"
                ) % (nome_db, APP_VERSION, ora, utente, conteggio, size_kb)

                for dest_email in destinatari:
                    msg = MIMEMultipart()
                    msg["Subject"] = "[%s] Backup - %s" % (nome_db, ora)
                    msg["From"] = smtp_usr
                    msg["To"] = dest_email
                    msg.attach(MIMEText(corpo, "plain", "utf-8"))

                    # Allega ZIP
                    with open(zip_path, "rb") as f:
                        part = MIMEBase("application", "zip")
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", "attachment", filename=zip_name)
                    msg.attach(part)

                    with smtplib.SMTP(smtp_srv, smtp_port, timeout=30) as s:
                        s.starttls()
                        s.login(smtp_usr, smtp_pwd)
                        s.send_message(msg)
                    print("[BACKUP EMAIL] Inviato a %s" % dest_email)
            except Exception as e:
                print("[BACKUP EMAIL] Errore: %s" % e)

        threading.Thread(target=_invia, daemon=True).start()

    def _esegui_ripristino(self):
        """Ripristino: mostra schermata inline con lista backup disponibili."""
        if not is_admin(self.sessione):
            return

        self._pulisci(); c = carica_colori()

        base = self._get_base()
        backup_dir = self.percorsi.get("backup", os.path.join(base, "backup"))
        os.makedirs(backup_dir, exist_ok=True)

        # Header
        header = tk.Frame(self._vista, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))
        tk.Button(header, text="< MENU", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._schermata_menu).pack(side="left")
        tk.Label(header, text="  RIPRISTINA BACKUP", bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(_S(8), 0))
        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(4)))

        # Cerca file ZIP nella cartella backup
        zip_files = []
        for f in sorted(os.listdir(backup_dir), reverse=True):
            if f.endswith(".zip"):
                fp = os.path.join(backup_dir, f)
                size_kb = os.path.getsize(fp) / 1024
                # Leggi info se possibile
                info_txt = ""
                try:
                    with zipfile.ZipFile(fp, "r") as zf:
                        if "_backup_info.json" in zf.namelist():
                            info = json.loads(zf.read("_backup_info.json"))
                            info_txt = "  %s  %s  %d file" % (
                                info.get("data_backup", "")[:16],
                                info.get("utente", ""),
                                info.get("file_inclusi", 0))
                except: pass
                zip_files.append((f, fp, size_kb, info_txt))

        if not zip_files:
            tk.Label(self._vista, text="Nessun backup trovato in:\n%s" % backup_dir,
                     bg=c["sfondo"], fg=c["stato_avviso"], font=self._f_label).pack(pady=_S(30))
            self.root.bind("<Escape>", lambda e: self._schermata_menu())
            return

        # Listbox backup
        list_frame = tk.Frame(self._vista, bg=c["sfondo"])
        list_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(2), _S(4)))

        lb = tk.Listbox(list_frame, font=self._f_list,
                         bg=c["sfondo_celle"], fg=c["dati"],
                         selectbackground=c["cursore"], selectforeground=c["testo_cursore"],
                         highlightthickness=1, highlightbackground=c["bordo_vuote"],
                         relief="flat", exportselection=False)
        lb.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(list_frame, orient="vertical", command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.configure(yscrollcommand=sb.set)

        for f, fp, size_kb, info_txt in zip_files:
            lb.insert("end", "  %s  (%.0f KB)%s" % (f, size_kb, info_txt))

        lb.selection_set(0)
        lb.focus_set()

        # Status
        self._ripr_status = tk.Label(self._vista, text="%d backup trovati  |  Seleziona e premi RIPRISTINA" % len(zip_files),
                                      bg=c["sfondo"], fg=c["testo_dim"], font=self._f_status)
        self._ripr_status.pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))

        # Bottoni
        bar = tk.Frame(self._vista, bg=c["sfondo"])
        bar.pack(pady=(_S(2), _S(6)))

        def _fai_ripristino():
            sel = lb.curselection()
            if not sel: return
            src = zip_files[sel[0]][1]
            self._ripristina_da_file(src)

        btn_ripr = tk.Button(bar, text="RIPRISTINA", font=self._f_btn, width=_S(12),
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2", command=_fai_ripristino)
        btn_ripr.pack(side="left", padx=_S(3))
        btn_ann = tk.Button(bar, text="ANNULLA", font=self._f_btn, width=_S(12),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2", command=self._schermata_menu)
        btn_ann.pack(side="left", padx=_S(3))
        self._kb_setup_bottoni([btn_ripr, btn_ann], orizzontale=True)

        lb.bind("<Return>", lambda e: _fai_ripristino())
        self.root.bind("<Escape>", lambda e: self._schermata_menu())
        self._rimuovi_coperta()

    def _ripristina_da_file(self, src):
        """Esegue il ripristino effettivo da un file ZIP."""
        c = carica_colori()
        base = self._get_base()
        backup_dir = self.percorsi.get("backup", os.path.join(base, "backup"))

        # Verifica che sia un backup valido
        try:
            with zipfile.ZipFile(src, "r") as zf:
                nomi = zf.namelist()
                ha_dati = any(n.startswith("dati/") for n in nomi)
                ha_def = any(n.startswith("definizioni/") for n in nomi)
                if not ha_dati and not ha_def:
                    self._ripr_status.config(text="File non valido: nessun dato!", fg=c["stato_errore"])
                    return
        except zipfile.BadZipFile:
            self._ripr_status.config(text="File ZIP corrotto!", fg=c["stato_errore"])
            return
        except Exception as e:
            self._ripr_status.config(text="Errore: %s" % e, fg=c["stato_errore"])
            return

        try:
            # Backup automatico pre-ripristino
            pre_backup_dir = os.path.join(backup_dir, "pre_ripristino")
            os.makedirs(pre_backup_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            pre_zip = os.path.join(pre_backup_dir, "pre_ripristino_%s.zip" % ts)

            with zipfile.ZipFile(pre_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                dati_dir = self.percorsi.get("dati", os.path.join(base, "dati"))
                if os.path.isdir(dati_dir):
                    for f in os.listdir(dati_dir):
                        if f.endswith(".json"):
                            zf.write(os.path.join(dati_dir, f), "dati/%s" % f)
                def_dir = self.percorsi.get("definizioni", os.path.join(base, "definizioni"))
                if os.path.isdir(def_dir):
                    for f in os.listdir(def_dir):
                        if f.endswith(".def"):
                            zf.write(os.path.join(def_dir, f), "definizioni/%s" % f)
                conf_path = os.path.join(base, "conf.dat")
                if os.path.exists(conf_path): zf.write(conf_path, "conf.dat")
                colori_path = os.path.join(base, "colori.json")
                if os.path.exists(colori_path): zf.write(colori_path, "colori.json")

            # Estrai il backup selezionato
            with zipfile.ZipFile(src, "r") as zf:
                for nome in zf.namelist():
                    if nome.startswith("_"): continue  # skip info
                    dest_path = os.path.join(base, nome)
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with open(dest_path, "wb") as out:
                        out.write(zf.read(nome))

            # Ricarica conf e torna al login
            self.conf = carica_conf()
            self.percorsi = get_percorsi(self.conf)
            self._schermata_login()

        except Exception as e:
            if hasattr(self, '_ripr_status'):
                self._ripr_status.config(text="Errore ripristino: %s" % e, fg=c["stato_errore"])

    def _get_base(self):
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    def _lancia_editor_tabelle(self):
        """Apre Editor Tabelle nella stessa finestra (embedded mode)."""
        if not _HAS_EDITOR:
            self._status("Editor Tabelle non disponibile!", "stato_errore")
            return
        # Salva schermata di ritorno
        self._pulisci()
        EditorTabelle(self._vista, on_close=self._ritorno_da_editor)
        self._rimuovi_coperta()

    def _ritorno_da_editor(self):
        """Callback: torna al menu dopo chiusura Editor Tabelle."""
        self._schermata_menu()


    # =========================================================================
    #  CRONO (modulo esterno)
    # =========================================================================
    def _lancia_crono(self):
        """Lancia il modulo Crono con contesto dal setup corrente."""
        if not _HAS_CRONO:
            self._status("Modulo Crono non disponibile!", "stato_errore")
            return
        contesto = self._build_crono_contesto()
        self._pulisci()
        Crono(parent=self._vista, on_close=self._ritorno_da_crono,
              contesto=contesto)
        self._rimuovi_coperta()

    def _build_crono_contesto(self):
        """Costruisce il dizionario contesto per Crono dal record corrente."""
        base = self._get_base()
        dati_dir = self.percorsi.get("dati", os.path.join(base, "dati"))
        contesto = {"dati_dir": dati_dir}

        # Pilota dalla sessione
        if self.sessione:
            contesto["pilota"] = get_display_name(self.sessione)
            # Transponder dall'utente loggato
            utente = get_utente(self.sessione.get("codice", ""))
            if utente:
                contesto["transponder"] = str(utente.get("Transponder", "")).strip()

        # Record corrente
        record_id, setup_name = self._get_record_id()
        if record_id:
            contesto["record_id"] = record_id
            contesto["setup_name"] = setup_name

        rec = self.db.leggi(self.indice_corrente) if self.indice_corrente >= 0 else None
        if rec:
            contesto["data"] = rec.get("Data", rec.get("Data_Prova", "")).strip()
            contesto["ora"] = rec.get("Ora", "").strip()
            # SpeedHive ID e nome pista dal riferimento
            if "piste" in self.ref_dbs:
                codice_pista = rec.get("Codice_Pista", "")
                if codice_pista:
                    pista_db = self.ref_dbs["piste"]
                    for idx in range(len(pista_db.records)):
                        pr = pista_db.leggi(idx)
                        if pr and str(pr.get("Codice_Pista", "")) == str(codice_pista):
                            contesto["pista"] = pr.get("Nome", pr.get("Nome_Pista", "")).strip()
                            contesto["speedhive_id"] = str(pr.get("SpeedHive_ID", "")).strip()
                            break

            # Condizioni meteo/pista dal record
            for campo_meteo in ("Temp_Esterna", "Temp_Pista", "Umidita", "Condizioni_Pista", "Vento"):
                val = str(rec.get(campo_meteo, "")).strip()
                if val:
                    contesto[campo_meteo.lower()] = val

            # Auto-fetch meteo se campi vuoti e pista nota
            if _HAS_METEO and not contesto.get("condizioni_pista"):
                indirizzo = citta = nazione = ""
                if "piste" in self.ref_dbs:
                    codice_pista = rec.get("Codice_Pista", "")
                    if codice_pista:
                        pista_db = self.ref_dbs["piste"]
                        for idx in range(len(pista_db.records)):
                            pr = pista_db.leggi(idx)
                            if pr and str(pr.get("Codice_Pista", "")) == str(codice_pista):
                                indirizzo = str(pr.get("Indirizzo", "")).strip()
                                citta = str(pr.get("Citta", "")).strip()
                                nazione = str(pr.get("Nazione", "")).strip()
                                break
                if indirizzo or citta:
                    try:
                        meteo = meteo_da_indirizzo(indirizzo, citta, nazione)
                        if meteo:
                            for k in ("temp_esterna", "temp_pista", "umidita", "condizioni_pista", "vento"):
                                if k not in contesto and k in meteo:
                                    contesto[k] = str(meteo[k])
                    except Exception as e:
                        print("[METEO] Auto-fetch fallito: %s" % e)

            # Riferimenti setup (telaio, miscela, gomme) per analisi IA
            for rif in self.table_def.riferimenti:
                alias = rif.get("alias", rif["tabella"])
                campo_rec = rif.get("campo_record", rif["campo_chiave"])
                # Piste gia' gestita sopra
                if rif["tabella"] == "piste":
                    continue
                codice = str(rec.get(campo_rec, "")).strip()
                if not codice:
                    continue
                ref_db = self.ref_dbs.get(alias) or self.ref_dbs.get(rif["tabella"])
                if ref_db:
                    desc = ""
                    rk = ref_db.table_def.get_campo_chiave()
                    campo_lookup = rk["nome"] if rk else rif["campo_chiave"]
                    for ri in range(len(ref_db.records)):
                        r = ref_db.leggi(ri)
                        if r and str(r.get(campo_lookup, "")).strip() == codice:
                            # Costruisci descrizione dai campi non-chiave
                            parti = []
                            for c in ref_db.table_def.get_campi_non_chiave():
                                val = str(r.get(c["nome"], "")).strip()
                                if val:
                                    parti.append(val)
                            desc = " ".join(parti) if parti else ref_db.get_descrizione_record(ri)
                            break
                    key = "ref_%s" % alias.lower().replace(" ", "_")
                    contesto[key] = desc or codice

        # Oggetti per modulo Confronta Setup (passati per riferimento)
        contesto["_db"] = self.db
        contesto["_table_def"] = self.table_def
        contesto["_ref_dbs"] = self.ref_dbs
        indici = self._indici_visibili if self._indici_visibili else \
                 self.db.get_records_filtrati(self.filtro_utente())
        contesto["_indici_visibili"] = list(indici)

        return contesto


    def _lancia_crono_scouting(self):
        """Lancia Crono in modalita' scouting (dal menu tabelle, senza setup)."""
        if not _HAS_CRONO:
            return
        base = self._get_base()
        dati_dir = self.percorsi.get("dati", os.path.join(base, "dati"))
        contesto = {"dati_dir": dati_dir}
        if self.sessione:
            contesto["pilota"] = get_display_name(self.sessione)
        self._pulisci()
        Crono(parent=self._vista, on_close=self._schermata_menu,
              contesto=contesto)
        self._rimuovi_coperta()

    def _ritorno_da_crono(self):
        """Callback: torna alla scheda setup dopo chiusura Crono."""
        nome = self._nome_tabella if hasattr(self, '_nome_tabella') else ''
        if nome:
            self._schermata_selezione(nome)
        else:
            self._schermata_menu()

    # =================================================================
    #  METEO AUTOMATICO
    # =================================================================


    def _applica_meteo(self, meteo):
        """Applica i dati meteo ai campi vuoti del form."""
        if not meteo:
            return
        mapping = {
            "Temp_Esterna": str(meteo["temp_esterna"]),
            "Temp_Pista": str(meteo["temp_pista"]),
            "Umidita": str(meteo["umidita"]),
            "Condizioni_Pista": meteo["condizioni_pista"],
            "Vento": meteo["vento"],
        }
        compilati = 0
        for nome_campo, valore in mapping.items():
            if hasattr(self, 'fields') and nome_campo in self.fields:
                rf = self.fields[nome_campo]
                if not rf.get().strip():
                    rf.set(valore)
                    compilati += 1
        if compilati > 0:
            desc = meteo.get("descrizione", "")
            self._status("Meteo auto: %s, %d°C, %s" % (
                desc, meteo["temp_esterna"], meteo["condizioni_pista"]), "stato_ok")

    def _auto_meteo(self):
        """Auto-fetch meteo in background quando il form si carica.
        SOLO se la data del setup e' oggi (o vuota). Record vecchi non vengono toccati."""
        if not _HAS_METEO:
            return
        # Controlla se i campi meteo sono gia' compilati
        if hasattr(self, 'fields') and "Condizioni_Pista" in self.fields:
            if self.fields["Condizioni_Pista"].get().strip():
                return
        # Controlla la data: solo se e' oggi o vuota
        oggi = date.today().strftime("%d/%m/%Y")
        for campo_data in ("Data", "Data_Prova"):
            if hasattr(self, 'fields') and campo_data in self.fields:
                data_setup = self.fields[campo_data].get().strip()
                # Rimuovi separatori vuoti della maschera (es. '//' o '..')
                data_pulita = data_setup.replace("/", "").replace(".", "").replace("-", "").strip()
                if data_pulita and data_setup != oggi:
                    return  # data vecchia, non aggiornare meteo
                break
        # Cerca pista: prima dal record, poi dai riferimenti wizard
        indirizzo = citta = nazione = ""
        codice_pista = ""
        # Prova dal record corrente
        rec = self.db.leggi(self.indice_corrente) if self.indice_corrente >= 0 else None
        if rec:
            codice_pista = rec.get("Codice_Pista", "")
        # Se non trovato, prova dai campi del form (nuovo record)
        if not codice_pista and hasattr(self, 'fields') and "Codice_Pista" in self.fields:
            codice_pista = self.fields["Codice_Pista"].get().strip()
        # Se ancora non trovato, prova dai riferimenti fissi wizard
        if not codice_pista and hasattr(self, '_ref_fixed') and self._ref_fixed:
            codice_pista = self._ref_fixed.get("Codice_Pista", "")
        if not codice_pista:
            return
        # Cerca indirizzo nella tabella piste
        if "piste" in self.ref_dbs:
            pista_db = self.ref_dbs["piste"]
            for idx in range(len(pista_db.records)):
                pr = pista_db.leggi(idx)
                if pr and str(pr.get("Codice_Pista", "")) == str(codice_pista):
                    indirizzo = str(pr.get("Indirizzo", "")).strip()
                    citta = str(pr.get("Citta", "")).strip()
                    nazione = str(pr.get("Nazione", "")).strip()
                    break
        if not indirizzo and not citta:
            return
        def _fetch():
            try:
                result = meteo_da_indirizzo(indirizzo, citta, nazione)
                if result:
                    self.root.after(0, lambda: self._applica_meteo(result))
            except Exception:
                pass
        threading.Thread(target=_fetch, daemon=True).start()

    def _get_record_id(self):
        """Ritorna (record_id, setup_name) per il record corrente. Gestisce tabelle senza chiave."""
        if not hasattr(self, 'indice_corrente') or self.indice_corrente is None:
            return "", ""
        rec = self.db.leggi(self.indice_corrente)
        if not rec:
            return "", ""

        record_id = ""
        setup_name = ""
        campo_k = self.table_def.get_campo_chiave()
        if campo_k:
            record_id = str(rec.get(campo_k["nome"], ""))
            setup_name = record_id
        else:
            # Nessun campo chiave: usa indice record
            record_id = "rec_%d" % self.indice_corrente
            setup_name = record_id
            for c in self.table_def.campi[:5]:
                val = str(rec.get(c["nome"], "")).strip()
                if val and len(val) > 1:
                    setup_name = val
                    break

        # Cerca un campo "Nome" o simile per nome leggibile
        for c_nome in ["Nome", "nome", "Descrizione", "descrizione"]:
            val = rec.get(c_nome, "")
            if val:
                setup_name = str(val)
                break

        # Sanitizza per nomi file
        record_id = record_id.replace("/", "-").replace("\\", "-").replace(":", "-").replace(" ", "_")
        setup_name = setup_name.replace("/", "-").replace("\\", "-").replace(":", "-")
        return record_id, setup_name
    def _stampa_scheda(self, sessioni, best_assoluto):
        """Stampa la scheda gara sulla stampante termica."""
        if not _HAS_THERMAL:
            return
        c = carica_colori()
        mac = self.conf.get("stampante_bt", "").strip()
        if not mac:
            mac = "auto"  # Su Windows cerca automaticamente via win32print

        self._tempi_status.config(text="Stampa in corso...", fg=c["stato_avviso"])
        self.root.update()

        righe = genera_scheda_completa(sessioni, best_assoluto)
        ok, msg = stampa_bluetooth(righe, mac)

        if ok:
            self._tempi_status.config(text="Scheda stampata!", fg=c["stato_ok"])
        else:
            self._tempi_status.config(text=msg, fg=c["stato_errore"])

    def _esporta_scheda(self, sessioni, best_assoluto):
        """Esporta la scheda gara come file .txt nella cartella dati."""
        if not _HAS_THERMAL:
            return
        c = carica_colori()
        righe = genera_scheda_completa(sessioni, best_assoluto)
        base = self._get_base()
        dati_dir = self.percorsi.get("dati", os.path.join(base, "dati"))
        path = salva_scheda_txt(righe, dati_dir)
        if path:
            nome = os.path.basename(path)
            self._tempi_status.config(
                text="Esportato: %s" % nome, fg=c["stato_ok"])
        else:
            self._tempi_status.config(
                text="Errore esportazione!", fg=c["stato_errore"])

    # =========================================================================
    #  STAMPA SCHEDA (generico per qualsiasi tabella)
    # =========================================================================
    # =========================================================================
    #  WEB SYNC (aggiornamento catalogo da web)
    # =========================================================================
    def _sync_web(self):
        """Avvia sync web per la tabella corrente."""
        if not _HAS_WEBSYNC or not self.table_def.links:
            self._status("Nessun link configurato!", "stato_errore")
            return
        c = carica_colori()
        self._status("Sync in corso... (%d link)" % len(self.table_def.links), "stato_avviso")
        self._vista.update_idletasks()

        def _on_risultato(risultato):
            agg = risultato.get("aggiunti", 0)
            upd = risultato.get("aggiornati", 0)
            err = risultato.get("errori", [])
            try:
                if agg + upd > 0:
                    self._status(
                        "Sync: %d nuovi, %d aggiornati" % (agg, upd), "stato_ok")
                    # Ricarica il record per mostrare eventuali modifiche
                    self.db._carica_dati()
                    self._indici_visibili = self.db.get_records_filtrati(self.filtro_utente())
                    if self._indici_visibili:
                        self._mostra_record()
                elif err:
                    self._status("Sync: %s" % err[0][:50], "stato_errore")
                else:
                    self._status("Sync: nessun cambiamento", "testo_dim")
            except (tk.TclError, AttributeError):
                pass

        sync_tabella_background(
            self._nome_tabella, self.table_def, self.db,
            callback=lambda r: self.root.after(0, lambda: _on_risultato(r)))

    def _auto_sync_cataloghi(self):
        """Controlla tutte le tabelle con !link e sincronizza se > 24h."""
        if not _HAS_WEBSYNC:
            return
        def_dir = self.percorsi.get("definizioni", "")
        if not def_dir or not os.path.isdir(def_dir):
            return

        from datetime import timedelta
        oggi = datetime.now()

        tabelle_da_sync = []
        for fn in os.listdir(def_dir):
            if not fn.endswith(".def"):
                continue
            nome_tab = fn[:-4]
            try:
                td = TableDef(os.path.join(def_dir, fn))
            except Exception:
                continue
            if not td.links:
                continue

            # Controlla ultimo sync (letto da !sync_date nel .def)
            if td.sync_date:
                if (oggi - td.sync_date) < timedelta(hours=24):
                    continue

            tabelle_da_sync.append((nome_tab, td))

        if not tabelle_da_sync:
            return

        # Mostra stato sync
        nomi = ", ".join(t[0].upper() for t in tabelle_da_sync)
        self._sync_update("Sync web: %s ..." % nomi, "stato_avviso")
        self._sync_pendenti = len(tabelle_da_sync)

        for nome_tab, td in tabelle_da_sync:
            try:
                db = RetroDB(nome_tab, self.percorsi, td)
                sync_tabella_background(nome_tab, td, db,
                    callback=lambda r, n=nome_tab: self.root.after(0, lambda: self._sync_callback(n, r)))
            except Exception:
                self._sync_pendenti -= 1

    def _sync_update(self, testo, colore="testo_dim"):
        """Aggiorna la label di stato sync nel menu (se esiste)."""
        c = carica_colori()
        try:
            if hasattr(self, '_sync_status') and self._sync_status.winfo_exists():
                self._sync_status.config(text=testo, fg=c.get(colore, c["testo_dim"]))
        except (tk.TclError, AttributeError):
            pass

    def _sync_callback(self, nome_tab, risultato):
        """Callback da thread sync: aggiorna stato nel menu e colore bottone."""
        agg = risultato.get("aggiunti", 0)
        upd = risultato.get("aggiornati", 0)
        err = risultato.get("errori", [])
        self._sync_pendenti = getattr(self, '_sync_pendenti', 1) - 1

        if agg + upd > 0:
            # Segna tabella come aggiornata: verra' ricaricata quando l'utente la apre
            if not hasattr(self, '_sync_aggiornate'):
                self._sync_aggiornate = set()
            self._sync_aggiornate.add(nome_tab)
            self._sync_update("%s: +%d nuovi, %d aggiornati" % (nome_tab.upper(), agg, upd), "stato_ok")
            # Bottone GIALLO nel menu
            c = carica_colori()
            btn = getattr(self, '_menu_tab_btns', {}).get(nome_tab)
            if btn:
                try:
                    btn.config(fg=c["stato_avviso"])
                except (tk.TclError, AttributeError):
                    pass
        elif err:
            self._sync_update("%s: errore sync" % nome_tab.upper(), "stato_errore")

        # Ultimo sync completato: messaggio finale
        if self._sync_pendenti <= 0:
            if agg + upd == 0 and not err:
                self._sync_update("Sync completata - nessun aggiornamento", "testo_dim")
            # Pulisci dopo 8 secondi
            self.root.after(8000, lambda: self._sync_update(""))

    def _stampa_scheda_record(self):
        """Stampa il record corrente sulla stampante termica.
        Motore generico: legge .def e dati, funziona per qualsiasi tabella."""
        if not _HAS_THERMAL:
            return
        c = carica_colori()
        # Verifica che ci sia un record
        if self.indice_corrente < 0 or not self.db:
            self._status("Nessun record da stampare!", "stato_errore")
            return
        rec = self.db.leggi(self.indice_corrente)
        if not rec:
            self._status("Record vuoto!", "stato_errore")
            return

        W = 40  # Larghezza 58mm (margine sicurezza)
        righe = []
        r = righe.append

        # --- Header ---
        nome_tab = self._nome_tabella.upper().replace("_", " ")
        r("=" * W)
        r(nome_tab.center(W))
        r("=" * W)
        r("")

        # Pilota e data
        pilota = ""
        if self.sessione:
            from auth import get_display_name
            pilota = get_display_name(self.sessione)
        if pilota:
            r("Pilota: %s" % pilota)
        from datetime import datetime
        r("Stampato: %s" % datetime.now().strftime("%d/%m/%Y %H:%M"))
        r("")

        # --- Riferimenti (pista, telaio, motore, gomme...) ---
        for rif in self.table_def.riferimenti:
            alias = rif.get("alias", rif["tabella"])
            campo_rec = rif.get("campo_record", rif["campo_chiave"])
            val_chiave = rec.get(campo_rec, "")
            # Cerca il record di riferimento per mostrare il nome
            ref_db = self.ref_dbs.get(alias) or self.ref_dbs.get(rif["tabella"])
            ref_def = self.ref_defs.get(alias) or self.ref_defs.get(rif["tabella"])
            desc = str(val_chiave)
            if ref_db and ref_def and val_chiave:
                # Usa la vera chiave della tabella di riferimento
                ref_k = ref_def.get_campo_chiave()
                campo_lookup = ref_k["nome"] if ref_k else rif["campo_chiave"]
                for idx in range(ref_db.conteggio()):
                    rr = ref_db.leggi(idx)
                    if rr and str(rr.get(campo_lookup, "")) == str(val_chiave):
                        # Descrizione: campi non-chiave
                        parti = []
                        for campo in ref_def.campi:
                            if campo.get("chiave"): continue
                            v = rr.get(campo["nome"], "")
                            if v: parti.append(str(v))
                            if len(parti) >= 2: break
                        if parti: desc = " - ".join(parti)
                        break
            label = alias.upper().replace("_", " ")
            r("%s: %s" % (label, desc))
        if self.table_def.riferimenti:
            r("")

        # --- Campi con sezioni ---
        sezione_corrente = None
        for campo in self.table_def.campi:
            nome = campo["nome"]
            # Controlla se c'e' una sezione prima di questo campo
            if nome in self.table_def.sezioni:
                titolo = self.table_def.sezioni[nome]
                r("-" * W)
                r(titolo.center(W))
                r("-" * W)
            val = rec.get(nome, "")
            if val:  # Stampa solo campi con valore
                label = nome.replace("_", " ")
                val_str = str(val)
                # Password: stampa in chiaro come promemoria
                if nome == "Password" and self._nome_tabella == "utenti":
                    try:
                        from auth import decripta_password, _is_encrypted
                        if _is_encrypted(val_str):
                            val_str = decripta_password(val_str)
                    except Exception:
                        pass
                # Allinea label:valore
                spazi = W - len(label) - len(val_str) - 2
                if spazi < 1: spazi = 1
                r("%s: %s%s" % (label, " " * spazi, val_str))

        r("")
        r("=" * W)

        # --- Invia alla stampante ---
        self._status("Stampa in corso...", "stato_avviso")
        self._vista.update_idletasks()

        mac = self.conf.get("stampante_bt", "").strip()
        if not mac:
            mac = "auto"
        ok, msg = stampa_bluetooth(righe, mac)
        if ok:
            self._status("Scheda stampata!", "stato_ok")
        else:
            self._status(msg, "stato_errore")
    # =========================================================================
    def _schermata_licenza_scaduta(self, msg):
        self._pulisci(); c = carica_colori()
        tk.Label(self._vista, text="LICENZA SCADUTA", bg=c["sfondo"], fg=c["stato_errore"],
                 font=self._f_title).pack(pady=(_S(30), _S(15)))
        tk.Label(self._vista, text=msg, bg=c["sfondo"], fg=c["stato_avviso"],
                 font=self._f_label).pack()
        # Bottone per riattivare
        tk.Button(self._vista, text="RIATTIVA", font=self._f_btn, width=_S(12),
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._schermata_attivazione(
                      get_codice_macchina(), "Licenza scaduta - riattiva")).pack(pady=_S(15))
        self._rimuovi_coperta()

    # =========================================================================
    #  ATTIVAZIONE LICENZA
    # =========================================================================
    def _schermata_attivazione(self, codice_macchina, messaggio=""):
        self._pulisci(); c = carica_colori()

        nome_db = self.conf.get("nome_database", "RetroDB")
        tk.Label(self._vista, text=nome_db, bg=c["sfondo"], fg=c["dati"],
                 font=self._f_login).pack(pady=(_S(15), _S(5)))

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(30))

        tk.Label(self._vista, text="ATTIVAZIONE SOFTWARE", bg=c["sfondo"], fg=c["stato_avviso"],
                 font=self._f_title).pack(pady=(_S(10), _S(5)))

        # Codice macchina (copiabile)
        tk.Label(self._vista, text="Codice macchina:", bg=c["sfondo"], fg=c["label"],
                 font=self._f_label).pack(pady=(_S(5), 0))

        cod_frame = tk.Frame(self._vista, bg=c["sfondo"])
        cod_frame.pack(pady=(_S(2), _S(5)))
        cod_entry = tk.Entry(cod_frame, font=self._f_title, width=20,
                             bg=c["sfondo_celle"], fg=c["stato_ok"],
                             insertbackground=c["dati"], justify="center",
                             highlightthickness=1, highlightbackground=c["bordo_vuote"],
                             relief="flat", readonlybackground=c["sfondo_celle"])
        cod_entry.insert(0, codice_macchina)
        cod_entry.config(state="readonly")
        cod_entry.pack()
        tk.Label(self._vista, text="Comunica questo codice allo sviluppatore",
                 bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small).pack()

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(50), pady=(_S(8), _S(8)))

        # Form inserimento chiave
        form = tk.Frame(self._vista, bg=c["sfondo"])
        form.pack(pady=_S(5))

        tk.Label(form, text="Chiave di attivazione:", bg=c["sfondo"], fg=c["label"],
                 font=self._f_label).pack(anchor="w")
        self._att_chiave = RetroField(form, label="", tipo="S", lunghezza=29, label_width=0)
        self._att_chiave.pack(pady=_S(2))
        tk.Label(form, text="Formato: XXXX-XXXX-XXXX-XXXX-XXXX",
                 bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small).pack()

        # Bottoni
        btn_frame = tk.Frame(self._vista, bg=c["sfondo"])
        btn_frame.pack(pady=_S(10))
        tk.Button(btn_frame, text="ATTIVA", font=self._f_btn, width=_S(12),
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  activebackground=c["pulsanti_sfondo"], activeforeground=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._esegui_attivazione).pack(side="left", padx=_S(3))
        tk.Button(btn_frame, text="ESCI", font=self._f_btn, width=_S(12),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self.root.destroy).pack(side="left", padx=_S(3))

        # Status
        self._att_status = tk.Label(self._vista, text=messaggio if messaggio else "",
                                     bg=c["sfondo"], fg=c["testo_dim"], font=self._f_status)
        self._att_status.pack(pady=(_S(5), 0))

        self._att_chiave.set_focus()
        self._rimuovi_coperta()

    def _esegui_attivazione(self):
        c = carica_colori()
        chiave = self._att_chiave.get().strip()

        if not chiave:
            self._att_status.config(text="Inserisci la chiave di attivazione!", fg=c["stato_errore"])
            return

        ok, msg = attiva_licenza(self.conf, chiave)

        if ok:
            self._att_status.config(text=msg, fg=c["stato_ok"])
            # Ricarica conf aggiornata e vai al login dopo 1.5 secondi
            self.conf = carica_conf()
            self.root.after(1500, self._schermata_login)
        else:
            self._att_status.config(text=msg, fg=c["stato_errore"])

    # =========================================================================
    #  CONF
    # =========================================================================
    def _schermata_conf(self):
        self._pulisci(); c = carica_colori()
        tk.Label(self._vista, text="CONFIGURAZIONE SISTEMA", bg=c["sfondo"], fg=c["stato_errore"],
                 font=self._f_title).pack(pady=(_S(10), _S(5)))
        tk.Label(self._vista, text="RISERVATO AL PROGRAMMATORE", bg=c["sfondo"], fg=c["stato_avviso"],
                 font=self._f_small).pack()
        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(15), pady=(_S(5), _S(5)))

        # Canvas scrollabile
        scroll_frame = tk.Frame(self._vista, bg=c["sfondo"])
        scroll_frame.pack(fill="both", expand=True, padx=_S(5))
        canvas = tk.Canvas(scroll_frame, bg=c["sfondo"], highlightthickness=0)
        vsb = tk.Scrollbar(scroll_frame, orient="vertical", command=canvas.yview)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=vsb.set)
        frame = tk.Frame(canvas, bg=c["sfondo"])
        canvas.create_window((0, 0), window=frame, anchor="nw")
        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        frame.bind("<Configure>", _on_frame_configure)
        # Scroll con tastiera
        def _scroll_up(e): canvas.yview_scroll(-3, "units")
        def _scroll_down(e): canvas.yview_scroll(3, "units")
        def _scroll_pgup(e): canvas.yview_scroll(-1, "pages")
        def _scroll_pgdn(e): canvas.yview_scroll(1, "pages")
        self.root.bind("<Prior>", _scroll_pgup)
        self.root.bind("<Next>", _scroll_pgdn)
        # MouseWheel scroll
        def _on_mousewheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self._conf_fields = {}

        # Campi normali
        campi_normali = [
            ("nome_database", "Nome Database", "S", 30),
            ("larghezza_max", "Larghezza Max", "N", 5),
            ("altezza_max", "Altezza Max", "N", 5),
            ("scala", "Scala (1.0=PC 1.5=uConsole)", "N", 4),
            ("fullscreen", "Fullscreen (0/1)", "N", 1),
            ("cella_dimensione", "Dim.Cella (def.16)", "N", 3),
            ("cella_spaziatura", "Spazio Celle (def.1)", "N", 2),
            ("font_campi", "Font Campi (def.9)", "N", 3),
            ("font_label", "Font Label (def.9)", "N", 3),
            ("stampante_bt", "Stampante (COM/MAC/nome)", "S", 25),
            ("multiutente", "Utenze (0=illim,1=mono,2+)", "N", 3),
            ("data_installazione", "Data Installazione", "D", 10),
            ("data_fine_licenza", "Fine Licenza", "D", 10),
        ]

        # Campi percorso (con bottone sfoglia)
        campi_percorso = [
            ("percorso_installazione", "Percorso Installazione"),
            ("percorso_tabelle", "Percorso Definizioni"),
            ("percorso_dati", "Percorso Dati"),
            ("percorso_backup", "Percorso Backup"),
            ("percorso_core", "Percorso Core"),
            ("percorso_addons", "Percorso Addons"),
        ]

        # â”€â”€ Sezione Percorsi â”€â”€
        tk.Label(frame, text="PERCORSI:", bg=c["sfondo"], fg=c["cerca_testo"],
                 font=self._f_btn).pack(anchor="w", pady=(_S(2), _S(1)))

        for chiave, label in campi_percorso:
            row = tk.Frame(frame, bg=c["sfondo"]); row.pack(fill="x", pady=_S(1))
            tk.Label(row, text=label, bg=c["sfondo"], fg=c["label"],
                     font=self._f_label, width=22, anchor="e").pack(side="left")
            entry = tk.Entry(row, font=self._f_list,
                             bg=c["sfondo_celle"], fg=c["dati"],
                             insertbackground=c["dati"],
                             highlightthickness=0,
                             relief="flat", bd=2)
            entry.pack(side="left", fill="x", expand=True, padx=(_S(4), 0))
            # Focus visivo: sfondo chiaro con focus, scuro senza
            entry.bind("<FocusIn>", lambda e, w=entry:
                w.config(bg=c["pulsanti_sfondo"], relief="solid"), add="+")
            entry.bind("<FocusOut>", lambda e, w=entry:
                w.config(bg=c["sfondo_celle"], relief="flat"), add="+")
            val = str(self.conf.get(chiave, ""))
            if val: entry.insert(0, val)
            # Bottone sfoglia
            btn = tk.Button(row, text="CHK", font=self._f_small, width=3,
                            bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                            relief="ridge", bd=1, cursor="hand2",
                            command=lambda e=entry, l=label: self._sfoglia_cartella(e, l))
            btn.pack(side="left", padx=(_S(2), 0))
            self._conf_fields[chiave] = entry

        tk.Frame(frame, bg=c["linee"], height=1).pack(fill="x", pady=(_S(6), _S(4)))

        # â”€â”€ Sezione Parametri â”€â”€
        tk.Label(frame, text="PARAMETRI:", bg=c["sfondo"], fg=c["cerca_testo"],
                 font=self._f_btn).pack(anchor="w", pady=(_S(2), _S(1)))

        # Default per campi non ancora in conf.dat
        _defaults = {
            "scala": "1.0", "fullscreen": "1", "cella_dimensione": "16",
            "cella_spaziatura": "1", "font_campi": "9", "font_label": "9",
            "multiutente": "1",
        }

        for chiave, label, tipo, lun in campi_normali:
            rf = RetroField(frame, label=label, tipo=tipo, lunghezza=lun, label_width=28)
            rf.pack(pady=_S(1), anchor="w")
            val = str(self.conf.get(chiave, _defaults.get(chiave, "")))
            if val:
                # Conversione date per campi D
                if tipo == "D":
                    # Rimuovi tutti i separatori per ottenere le 8 cifre
                    raw = val.replace("/", "").replace("-", "").replace(".", "")
                    if len(raw) == 8 and raw.isdigit():
                        # Se le prime 4 cifre sono un anno (>1900) -> formato YYYYMMDD
                        if int(raw[:4]) > 1900:
                            val = "%s/%s/%s" % (raw[6:8], raw[4:6], raw[0:4])
                        # Altrimenti gia' DDMMYYYY, rimetti i separatori
                        else:
                            val = "%s/%s/%s" % (raw[0:2], raw[2:4], raw[4:8])
                rf.set(val)
            self._conf_fields[chiave] = rf

        tk.Frame(frame, bg=c["linee"], height=1).pack(fill="x", pady=(_S(6), _S(4)))

        # ── Sezione Notifiche Sviluppatore ──
        tk.Label(frame, text="NOTIFICHE SVILUPPATORE:", bg=c["sfondo"], fg=c["cerca_testo"],
                 font=self._f_btn).pack(anchor="w", pady=(_S(2), _S(1)))

        campi_smtp = [
            ("email_sviluppatore", "Email Sviluppatore", "S", 30),
            ("smtp_server", "Server SMTP", "S", 25),
            ("smtp_port", "Porta SMTP", "N", 4),
            ("smtp_user", "Utente SMTP", "S", 30),
            ("smtp_password", "Password SMTP (App)", "P", 20),
        ]
        for chiave, label, tipo, lun in campi_smtp:
            rf = RetroField(frame, label=label, tipo=tipo, lunghezza=lun, label_width=28)
            rf.pack(pady=_S(1), anchor="w")
            val = str(self.conf.get(chiave, ""))
            if val: rf.set(val)
            self._conf_fields[chiave] = rf

        # Sezione IA
        tk.Label(frame, text="INTELLIGENZA ARTIFICIALE", bg=c["sfondo"], fg=c["cerca_testo"],
                 font=self._f_btn).pack(anchor="w", pady=(_S(8), _S(1)))
        rf_ai = RetroField(frame, label="API Key Anthropic", tipo="S", lunghezza=120, label_width=28)
        rf_ai.pack(pady=_S(1), anchor="w")
        val_ai = str(self.conf.get("anthropic_api_key", ""))
        if val_ai: rf_ai.set(val_ai)
        self._conf_fields["anthropic_api_key"] = rf_ai

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(15), pady=(_S(10), _S(5)))
        btn_bar = tk.Frame(self._vista, bg=c["sfondo"]); btn_bar.pack(pady=_S(5))
        tk.Button(btn_bar, text="SALVA CONF", font=self._f_btn, width=_S(14),
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2", command=self._salva_conf).pack(side="left", padx=_S(4))
        tk.Button(btn_bar, text="ANNULLA", font=self._f_btn, width=_S(14),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._schermata_menu() if self.sessione else self._schermata_login()
                  ).pack(side="left", padx=_S(4))

        # Help tastiera
        tk.Label(self._vista, text="Tab = Prossimo campo  |  PgUp/PgDn = Scorri  |  SALVA CONF = Applica",
                 bg=c["sfondo"], fg=c["puntini"], font=self._f_small).pack(fill="x", padx=_S(10), pady=(_S(4), _S(4)))
        self._rimuovi_coperta()

    def _sfoglia_cartella(self, entry_widget, titolo):
        """Apre dialogo selezione cartella e aggiorna il campo percorso."""
        path_attuale = entry_widget.get().strip()
        iniziale = path_attuale if path_attuale and os.path.isdir(path_attuale) else os.path.expanduser("~")
        nuova = _filedialog.askdirectory(
            title="Seleziona %s" % titolo,
            initialdir=iniziale)
        if nuova:
            entry_widget.delete(0, "end")
            entry_widget.insert(0, nuova)

    def _salva_conf(self):
        for chiave, widget in self._conf_fields.items():
            # widget puo' essere RetroField o tk.Entry (per i percorsi)
            if isinstance(widget, tk.Entry):
                val = widget.get().strip()
            else:
                val = widget.get()
            if chiave in ("larghezza_max", "altezza_max", "fullscreen",
                          "cella_dimensione", "cella_spaziatura", "font_campi", "font_label",
                          "smtp_port"):
                try: val = int(val)
                except: val = 700 if "max" in chiave else 0
            elif chiave == "scala":
                try: val = float(val)
                except: val = 1.0
            elif chiave in ("data_installazione", "data_fine_licenza"):
                # Conversione europeo (DD/MM/YYYY) -> ISO (YYYY-MM-DD)
                if len(val) == 10 and "/" in val:
                    parti = val.split("/")
                    if len(parti) == 3:
                        val = "%s-%s-%s" % (parti[2], parti[1], parti[0])
            self.conf[chiave] = val
        salva_conf(self.conf); self.percorsi = get_percorsi(self.conf); set_scala(self.conf.get("scala", 1.0))
        set_cell_params(
            size=self.conf.get("cella_dimensione", 16),
            pad=self.conf.get("cella_spaziatura", 1),
            font_cell=self.conf.get("font_campi", 9),
            font_label=self.conf.get("font_label", 9),
        )
        # Aggiorna dimensione finestra
        self._win_w = int(self.conf.get("larghezza_max", 900))
        self._win_h = int(self.conf.get("altezza_max", 700))
        self.root.title(_nome_base(self.conf.get("nome_database", "RetroDB")) + "  v" + __version__)

        # Applica fullscreen in tempo reale
        fs = int(self.conf.get("fullscreen", 0))
        self.root.attributes("-fullscreen", bool(fs))
        if not fs:
            self.root.geometry("%dx%d" % (self._win_w, self._win_h))

        # Rigenera font con nuova scala
        self._f_title.configure(size=_S(11))
        self._f_label.configure(size=_S(9))
        self._f_btn.configure(size=_S(8))
        self._f_small.configure(size=_S(8))
        self._f_status.configure(size=_S(8))
        self._f_nav.configure(size=_S(10))
        self._f_list.configure(size=_S(8))
        self._f_login.configure(size=_S(14))

        # Aggiorna limite utenti in tempo reale
        self._max_utenti = self._parse_max_utenti()

        # Crea le cartelle se non esistono
        for p in [self.percorsi["definizioni"], self.percorsi["dati"], self.percorsi["backup"]]:
            os.makedirs(p, exist_ok=True)
        if self.sessione: self._schermata_menu()
        else: self._schermata_login()

    # =========================================================================
    #  SETUP COLORI
    # =========================================================================
    def _schermata_setup(self):
        self._pulisci(); c = carica_colori()
        self._setup_colors = c.copy()
        header = tk.Frame(self._vista, bg=c["sfondo"]); header.pack(fill="x", padx=_S(10), pady=(_S(8), _S(4)))
        tk.Button(header, text="< MENU", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2", command=self._schermata_menu).pack(side="left")
        tk.Label(header, text="  SETUP COLORI", bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left")
        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(4)))
        scroll_cont = tk.Frame(self._vista, bg=c["sfondo"])
        scroll_cont.pack(fill="both", expand=True, padx=_S(10))
        canvas = tk.Canvas(scroll_cont, bg=c["sfondo"], highlightthickness=0)
        sb = tk.Scrollbar(scroll_cont, orient="vertical", command=canvas.yview)
        sb.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=sb.set)
        inner = tk.Frame(canvas, bg=c["sfondo"])
        canvas.create_window((0, 0), window=inner, anchor="nw")
        for chiave in DEFAULT_COLORS:
            desc = COLOR_DESCRIPTIONS.get(chiave, chiave)
            row = tk.Frame(inner, bg=c["sfondo"]); row.pack(fill="x", pady=1)
            tk.Label(row, text=desc, bg=c["sfondo"], fg=c["label"],
                     font=self._f_label, width=22, anchor="w").pack(side="left", padx=(0, _S(5)))
            preview = tk.Label(row, text="  ", bg=self._setup_colors[chiave], relief="solid", bd=1, width=3)
            preview.pack(side="left", padx=(0, _S(5)))
            val_label = tk.Label(row, text=self._setup_colors[chiave], bg=c["sfondo"],
                                  fg=c["dati"], font=self._f_small, width=8)
            val_label.pack(side="left", padx=(0, _S(5)))
            tk.Button(row, text="Cambia", font=self._f_small,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"], relief="ridge", bd=1, cursor="hand2",
                      command=lambda k=chiave, p=preview, v=val_label: self._scegli_colore(k, p, v)).pack(side="left")
        inner.update_idletasks(); canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(4)))
        btn_bar = tk.Frame(self._vista, bg=c["sfondo"]); btn_bar.pack(pady=(_S(4), _S(8)))
        tk.Button(btn_bar, text="SALVA", font=self._f_btn, width=_S(12),
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2", command=self._salva_setup_colori).pack(side="left", padx=_S(4))
        tk.Button(btn_bar, text="RESET DEFAULT", font=self._f_btn, width=_S(14),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2", command=self._reset_setup_colori).pack(side="left", padx=_S(4))
        self._rimuovi_coperta()

    def _scegli_colore(self, chiave, preview, val_label):
        """Editor colore inline: mostra entry hex al posto del color picker nativo."""
        # Se c'e' gia' un editor aperto, chiudilo
        if hasattr(self, '_color_edit_frame') and self._color_edit_frame:
            try: self._color_edit_frame.destroy()
            except: pass

        c = carica_colori()
        colore_attuale = self._setup_colors.get(chiave, "#000000")

        # Frame inline sotto il bottone
        ef = tk.Frame(preview.master, bg=c["sfondo"])
        ef.pack(side="left", padx=(_S(5), 0))
        self._color_edit_frame = ef

        entry = tk.Entry(ef, font=self._f_small, width=8,
                         bg=c["sfondo_celle"], fg=c["dati"],
                         insertbackground=c["dati"], relief="flat",
                         highlightthickness=1, highlightbackground=c["bordo_vuote"])
        entry.pack(side="left", padx=(0, _S(3)))
        entry.insert(0, colore_attuale)
        entry.select_range(0, "end")
        entry.focus_set()

        def _applica(event=None):
            val = entry.get().strip()
            if not val.startswith("#"): val = "#" + val
            if len(val) == 7:  # #RRGGBB
                try:
                    # Verifica che sia un colore valido
                    preview.config(bg=val)
                    self._setup_colors[chiave] = val
                    val_label.config(text=val)
                except tk.TclError:
                    pass  # Colore non valido
            try: ef.destroy()
            except: pass
            self._color_edit_frame = None

        def _annulla(event=None):
            try: ef.destroy()
            except: pass
            self._color_edit_frame = None

        entry.bind("<Return>", _applica)
        entry.bind("<Escape>", _annulla)
        tk.Button(ef, text="OK", font=self._f_small, width=3,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, command=_applica).pack(side="left")

    def _salva_setup_colori(self):
        salva_colori(self._setup_colors); carica_colori(force=True)
        pass  # Colori salvati
        self._schermata_menu()

    def _reset_setup_colori(self):
        if True:  # Reset colori
            salva_colori(DEFAULT_COLORS.copy()); carica_colori(force=True); self._schermata_setup()

    # =========================================================================
    #  APERTURA TABELLA
    # =========================================================================
    def _apri_tabella(self, nome_tabella):
        def_dir = self.percorsi["definizioni"]
        try: self.table_def = TableDef(os.path.join(def_dir, "%s.def" % nome_tabella))
        except Exception as e: return

        # Controllo accesso
        if not self.table_def.utente_autorizzato(self.sessione):
            pass  # Accesso negato
            return

        # Resetta flag cambiamenti web sync (bottone torna verde nel menu)
        if _HAS_WEBSYNC:
            from web_sync import azzera_cambiamenti
            azzera_cambiamenti(nome_tabella)
        # Rimuovi dalla lista tabelle aggiornate dal sync
        if hasattr(self, '_sync_aggiornate'):
            self._sync_aggiornate.discard(nome_tabella)

        self.ref_defs = {}; self.ref_dbs = {}
        _tab_cache = {}  # {tabella_reale: (TableDef, RetroDB)} per condividere istanze
        for rif in self.table_def.riferimenti:
            alias = rif.get("alias", rif["tabella"])
            tab = rif["tabella"]
            ref_path = os.path.join(def_dir, "%s.def" % tab)
            if os.path.exists(ref_path):
                if tab not in _tab_cache:
                    ref_td = TableDef(ref_path)
                    _tab_cache[tab] = (ref_td, RetroDB(tab, self.percorsi, ref_td))
                self.ref_defs[alias] = _tab_cache[tab][0]
                self.ref_dbs[alias] = _tab_cache[tab][1]

        try: self.db = RetroDB(nome_tabella, self.percorsi, self.table_def)
        except Exception as e: return

        if self.db.verifica_schema():
            pass  # Schema modificato

        self._nome_tabella = nome_tabella
        self.modo_ricerca = False; self.modo_nuovo = False; self.risultati_ricerca = []
        self.ref_selectors = {}
        self._rif_preselezionati = {}  # {alias: indice_record}

        # Tabelle composite: schermata selezione con storico
        if self.table_def.is_composite:
            self._schermata_selezione(nome_tabella)
        else:
            self._indici_visibili = self.db.get_records_filtrati(self.filtro_utente())
            self._pos_visibile = -1
            self.indice_corrente = -1
            self._costruisci_form(nome_tabella)
            # Tabella utenti: posiziona sempre sul primo record esistente
            # (l'utente preme NUOVO se vuole aggiungere, li' scatta il controllo limite)
            _vai_al_primo = (nome_tabella == "utenti" and self._indici_visibili)
            if _vai_al_primo:
                self._pos_visibile = 0
                self.indice_corrente = self._indici_visibili[0]
                self._mostra_record()
            # Altre tabelle: entra in modalita' nuovo record (Ctrl+O=prec, Ctrl+P=succ)
            elif self.table_def.puo("nuovo"):
                self._nuovo()
            elif self._indici_visibili:
                self._pos_visibile = 0
                self.indice_corrente = self._indici_visibili[0]
                self._mostra_record()

    # =========================================================================
    #  SELEZIONE RIFERIMENTI + STORICO (tabelle composite)
    # =========================================================================
    def _schermata_selezione(self, nome_tabella):
        """Entry point selezione: inizializza wizard a step."""
        self._rif_preselezionati = {}
        self._selezione_step(nome_tabella, 0)

    # =========================================================================
    #  SELEZIONE WIZARD - Uno step per ogni tabella di riferimento
    # =========================================================================
    def _selezione_step(self, nome_tabella, step_idx):
        """Mostra Treeview a schermo pieno per selezionare un riferimento."""
        riferimenti = self.table_def.riferimenti
        if step_idx >= len(riferimenti):
            # Tutti selezionati -> vai al riepilogo
            self._selezione_riepilogo(nome_tabella)
            return

        self._pulisci(); c = carica_colori()

        rif = riferimenti[step_idx]
        alias = rif.get("alias", rif["tabella"])
        campo_chiave = rif["campo_chiave"]
        ref_db = self.ref_dbs.get(alias)
        ref_td = self.ref_defs.get(alias)

        # Filtro
        if ref_td and ref_td.condiviso:
            filtro = None
        else:
            filtro = self.filtro_utente()

        # ── Stile Treeview retro ──
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Retro.Treeview",
            background=c["sfondo_celle"], foreground=c["dati"],
            fieldbackground=c["sfondo_celle"], font=("Consolas", _S(8)),
            rowheight=_S(22), borderwidth=0)
        style.configure("Retro.Treeview.Heading",
            background=c["pulsanti_sfondo"], foreground=c["pulsanti_testo"],
            font=("Consolas", _S(8), "bold"), borderwidth=1, relief="ridge")
        style.map("Retro.Treeview",
            background=[("selected", c["cursore"])],
            foreground=[("selected", c["testo_cursore"])])
        style.map("Retro.Treeview.Heading",
            background=[("active", c["cerca_sfondo"])])

        # ── Header ──
        header = tk.Frame(self._vista, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))

        if step_idx > 0:
            tk.Button(header, text="< INDIETRO", font=self._f_small,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=lambda: self._selezione_step(nome_tabella, step_idx - 1)
                      ).pack(side="left")
        else:
            tk.Button(header, text="< MENU", font=self._f_small,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=self._schermata_menu).pack(side="left")

        step_text = "Step %d/%d" % (step_idx + 1, len(riferimenti))
        tk.Label(header, text="  %s  -  %s" % (alias.upper().replace("_", " "), step_text),
                 bg=c["sfondo"], fg=c["dati"], font=self._f_title).pack(side="left", padx=(_S(8), 0))

        # Mostra selezioni precedenti
        if self._rif_preselezionati:
            sel_text_parts = []
            for prev_alias, prev_idx in self._rif_preselezionati.items():
                prev_db = self.ref_dbs.get(prev_alias)
                if prev_db:
                    sel_text_parts.append("%s: %s" % (
                        prev_alias.upper().replace("_", " "), prev_db.get_descrizione_record(prev_idx)))
            if sel_text_parts:
                tk.Label(header, text="  |  ".join(sel_text_parts),
                         bg=c["sfondo"], fg=c["stato_avviso"], font=self._f_small
                         ).pack(side="right")

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(2)))

        # ── Treeview con tutti i campi della tabella riferimento ──
        if not ref_db or not ref_td:
            tk.Label(self._vista, text="Tabella %s non trovata!" % alias,
                     bg=c["sfondo"], fg=c["stato_errore"], font=self._f_label).pack(pady=_S(20))
            return

        # Prepara colonne
        colonne = []
        campo_k = ref_td.get_campo_chiave()
        if campo_k:
            colonne.append((campo_k["nome"], campo_k["nome"].replace("_", " "), _S(60)))
        for campo in ref_td.campi:
            if campo.get("chiave"): continue
            larg = max(_S(50), min(_S(200), _S(campo["lunghezza"] * 8)))
            colonne.append((campo["nome"], campo["nome"].replace("_", " "), larg))

        col_ids = [col[0] for col in colonne]

        tree_frame = tk.Frame(self._vista, bg=c["sfondo"])
        tree_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(2), _S(2)))

        step_tree = ttk.Treeview(tree_frame, columns=col_ids,
                                  show="headings", style="Retro.Treeview",
                                  selectmode="browse")

        vsb = tk.Scrollbar(tree_frame, orient="vertical", command=step_tree.yview)
        vsb.pack(side="right", fill="y")
        hsb = tk.Scrollbar(tree_frame, orient="horizontal", command=step_tree.xview)
        hsb.pack(side="bottom", fill="x")
        step_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        step_tree.pack(side="left", fill="both", expand=True)

        for col_id, titolo, larg in colonne:
            step_tree.heading(col_id, text=titolo, anchor="w")
            step_tree.column(col_id, width=larg, minwidth=_S(40), anchor="w")

        # Popola righe
        indici_visibili = ref_db.get_records_filtrati(filtro)
        for idx in indici_visibili:
            rec = ref_db.leggi(idx)
            if not rec: continue
            valori = []
            if campo_k:
                valori.append(rec.get(campo_k["nome"], ""))
            for campo in ref_td.campi:
                if campo.get("chiave"): continue
                valori.append(str(rec.get(campo["nome"], "")))
            step_tree.insert("", "end", iid=str(idx), values=valori)

        # Zebra
        step_tree.tag_configure("dispari", background=c["sfondo_celle_piene"])
        for i, item in enumerate(step_tree.get_children()):
            if i % 2:
                step_tree.item(item, tags=("dispari",))

        # ── Conteggio ──
        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))

        n_rec = len(indici_visibili)
        status_text = "%d record" % n_rec

        # ── Azione: conferma selezione ──
        def _conferma_step():
            sel = step_tree.selection()
            if not sel:
                status_lbl.config(text="Seleziona un record!", fg=c["stato_errore"])
                return
            idx_sel = int(sel[0])
            self._rif_preselezionati[alias] = idx_sel
            # Se !storico;vero nel .def e siamo al primo step: mostra storico
            if step_idx == 0 and self.table_def.storico:
                self._storico_pista(nome_tabella)
            else:
                self._selezione_step(nome_tabella, step_idx + 1)

        # Barra bottoni
        bar = tk.Frame(self._vista, bg=c["sfondo"])
        bar.pack(pady=(_S(2), _S(4)))

        btn_conf = tk.Button(bar, text="CONFERMA >", font=self._f_btn, width=_S(14),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2", command=_conferma_step)
        btn_conf.pack(side="left", padx=_S(3))

        self._kb_setup_bottoni([btn_conf], orizzontale=True)

        status_lbl = tk.Label(self._vista, text=status_text,
                               bg=c["sfondo"], fg=c["testo_dim"], font=self._f_status, anchor="w")
        status_lbl.pack(fill="x", padx=_S(10), pady=(0, _S(2)))
        tk.Label(self._vista, text="Frecce = Scorri  |  Enter = Seleziona  |  Tab = Bottoni  |  Esc = Indietro",
                 bg=c["sfondo"], fg=c["puntini"], font=self._f_small).pack(fill="x", padx=_S(10), pady=(0, _S(4)))

        # Enter e frecce su Treeview
        step_tree.bind("<Return>", lambda e: _conferma_step())
        def _sync_sel(event):
            def _do():
                f = step_tree.focus()
                if f: step_tree.selection_set(f)
            step_tree.after_idle(_do)
        step_tree.bind("<Up>", _sync_sel)
        step_tree.bind("<Down>", _sync_sel)

        # Focus e selezione iniziale
        children = step_tree.get_children()
        if children:
            # Se c'era gia' una selezione precedente per questo step (torno indietro)
            prev_sel = self._rif_preselezionati.get(alias)
            target = str(prev_sel) if prev_sel is not None and str(prev_sel) in children else children[0]
            step_tree.selection_set(target)
            step_tree.focus(target)
            step_tree.see(target)
        step_tree.focus_set()

        # Escape -> step precedente o menu
        if step_idx > 0:
            self.root.bind("<Escape>",
                lambda e, s=step_idx: self._selezione_step(nome_tabella, s - 1))
        else:
            self.root.bind("<Escape>", lambda e: self._schermata_menu())
        self._rimuovi_coperta()

    # =========================================================================
    #  STORICO PISTA - Tutti i setup per la pista selezionata
    # =========================================================================
    def _storico_pista(self, nome_tabella):
        """Mostra tutti i setup per la pista selezionata, ordinati per data.
        Da qui: COPIA per clonare, CARICA per aprire, CONTINUA per wizard."""

        # ── Recupera codice pista selezionata ──
        rif_pista = self.table_def.riferimenti[0]
        alias_pista = rif_pista.get("alias", rif_pista["tabella"])
        campo_rec_pista = rif_pista.get("campo_record", rif_pista["campo_chiave"])
        idx_pista = self._rif_preselezionati.get(alias_pista)
        ref_db_pista = self.ref_dbs.get(alias_pista)

        pista_code = None
        pista_desc = ""
        if ref_db_pista and idx_pista is not None:
            rec_pista = ref_db_pista.leggi(idx_pista)
            ref_k = ref_db_pista.table_def.get_campo_chiave()
            if rec_pista and ref_k:
                pista_code = str(rec_pista.get(ref_k["nome"], ""))
            pista_desc = ref_db_pista.get_descrizione_record(idx_pista)

        if pista_code is None:
            self._selezione_step(nome_tabella, 1)
            return

        # ── Filtra i setup per questa pista + utente ──
        filtro = self.filtro_utente()
        indici = self.db.get_records_filtrati(filtro)
        storico = []  # [(idx, record), ...]

        for idx in indici:
            rec = self.db.leggi(idx)
            if not rec: continue
            val = str(rec.get(campo_rec_pista, "")).strip()
            try:
                if str(int(val)) != str(int(pista_code)): continue
            except:
                if val != pista_code: continue
            storico.append((idx, rec))

        # Ordina per data decrescente (ultimo in cima)
        def _sort_data(item):
            rec = item[1]
            for fn in ("Data_Prova", "Data"):
                v = rec.get(fn, "")
                if v:
                    try:
                        p = v.split("/")
                        return (int(p[2]), int(p[1]), int(p[0]))
                    except: pass
            # Fallback su timestamp
            ts = rec.get("_timestamp", "")
            if ts: return tuple(ts.replace("-", "").replace(":", "").replace(" ", ""))
            return (0,)
        storico.sort(key=_sort_data, reverse=True)

        storico_indici = [s[0] for s in storico]

        # ── Se nessun setup per questa pista: continua wizard ──
        if not storico:
            self._selezione_step(nome_tabella, 1)
            return

        # ── UI ──
        self._pulisci(); c = carica_colori()
        self._storico_indici = storico_indici

        # Stile Treeview
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Retro.Treeview",
            background=c["sfondo_celle"], foreground=c["dati"],
            fieldbackground=c["sfondo_celle"], font=("Consolas", _S(8)),
            rowheight=_S(22), borderwidth=0)
        style.configure("Retro.Treeview.Heading",
            background=c["pulsanti_sfondo"], foreground=c["pulsanti_testo"],
            font=("Consolas", _S(8), "bold"), borderwidth=1, relief="ridge")
        style.map("Retro.Treeview",
            background=[("selected", c["cursore"])],
            foreground=[("selected", c["testo_cursore"])])
        style.map("Retro.Treeview.Heading",
            background=[("active", c["cerca_sfondo"])])

        # ── Header ──
        header = tk.Frame(self._vista, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))

        tk.Button(header, text="< PISTA", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._selezione_step(nome_tabella, 0)).pack(side="left")

        tk.Label(header, text="  STORICO PISTA: %s  -  %d SETUP" % (
                    pista_desc.strip(), len(storico)),
                 bg=c["sfondo"], fg=c["dati"], font=self._f_title).pack(side="left", padx=(_S(8), 0))

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(2)))

        # ── Colonne Treeview dinamiche ──
        colonne = []
        campo_k = self.table_def.get_campo_chiave()
        if campo_k:
            colonne.append(("_id_setup", "#", _S(40)))

        # Colonna data
        for fn in ("Data_Prova", "Data"):
            for campo in self.table_def.campi:
                if campo["nome"] == fn:
                    colonne.append(("_data", "Data", _S(80)))
                    break
            else: continue
            break

        # Colonne per riferimenti extra (non pista): mostra descrizione risolta
        for i, rif in enumerate(self.table_def.riferimenti):
            if i == 0: continue  # Salta pista (gia' nel header)
            alias = rif.get("alias", rif["tabella"])
            lbl = alias.upper().replace("_", " ")
            # Compatta
            if len(lbl) > 12: lbl = lbl[:12]
            colonne.append(("_ref_%d" % i, lbl, _S(100)))

        # Colonne campi significativi
        for fn in ("Condizioni", "Tempo_Giro", "Note"):
            for campo in self.table_def.campi:
                if campo["nome"] == fn:
                    larg = max(_S(60), min(_S(140), _S(campo["lunghezza"] * 7)))
                    colonne.append((fn, fn.replace("_", " "), larg))
                    break

        col_ids = [c[0] for c in colonne]

        # ── Treeview ──
        tree_frame = tk.Frame(self._vista, bg=c["sfondo"])
        tree_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(2), _S(2)))

        storico_tree = ttk.Treeview(tree_frame, columns=col_ids,
                                     show="headings", style="Retro.Treeview",
                                     selectmode="browse")

        vsb = tk.Scrollbar(tree_frame, orient="vertical", command=storico_tree.yview)
        vsb.pack(side="right", fill="y")
        hsb = tk.Scrollbar(tree_frame, orient="horizontal", command=storico_tree.xview)
        hsb.pack(side="bottom", fill="x")
        storico_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        storico_tree.pack(side="left", fill="both", expand=True)

        for col_id, titolo, larg in colonne:
            storico_tree.heading(col_id, text=titolo, anchor="w")
            storico_tree.column(col_id, width=larg, minwidth=_S(35), anchor="w")

        # ── Popola righe ──
        for idx, rec in storico:
            valori = []
            # ID
            if campo_k:
                valori.append(rec.get(campo_k["nome"], "?"))
            # Data
            for fn in ("Data_Prova", "Data"):
                if fn in [c["nome"] for c in self.table_def.campi]:
                    valori.append(rec.get(fn, ""))
                    break
            # Riferimenti extra: risolvi descrizione
            for i, rif in enumerate(self.table_def.riferimenti):
                if i == 0: continue
                alias = rif.get("alias", rif["tabella"])
                campo_rec = rif.get("campo_record", rif["campo_chiave"])
                ref_db = self.ref_dbs.get(alias)
                val_code = str(rec.get(campo_rec, "")).strip()
                desc = val_code
                if ref_db and val_code:
                    ref_k = ref_db.table_def.get_campo_chiave()
                    if ref_k:
                        ref_filtro = None  # Cerca in tutti i record della ref
                        for ri in range(len(ref_db.records)):
                            r = ref_db.leggi(ri)
                            if r and str(r.get(ref_k["nome"], "")).strip() == val_code:
                                desc = ref_db.get_descrizione_record(ri)
                                break
                valori.append(desc)
            # Campi significativi
            for fn in ("Condizioni", "Tempo_Giro", "Note"):
                if fn in [c["nome"] for c in self.table_def.campi]:
                    v = rec.get(fn, "")
                    if fn == "Note" and len(str(v)) > 25:
                        v = str(v)[:25] + ".."
                    valori.append(str(v))

            storico_tree.insert("", "end", iid=str(idx), values=valori)

        # Zebra
        storico_tree.tag_configure("dispari", background=c["sfondo_celle_piene"])
        for i, item in enumerate(storico_tree.get_children()):
            if i % 2: storico_tree.item(item, tags=("dispari",))

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))

        # ── Bottoni ──
        bar = tk.Frame(self._vista, bg=c["sfondo"])
        bar.pack(pady=(_S(2), _S(4)))
        _st_btns = []

        def _get_sel_idx():
            sel = storico_tree.selection()
            if not sel: return None
            return int(sel[0])

        btn_copia = tk.Button(bar, text="COPIA\n^D", font=self._f_btn, width=_S(10),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._copia_setup(nome_tabella, _get_sel_idx()))
        btn_copia.pack(side="left", padx=_S(3))
        _st_btns.append(btn_copia)

        btn_carica = tk.Button(bar, text="CARICA", font=self._f_btn, width=_S(10),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._carica_da_storico_pista(nome_tabella, _get_sel_idx()))
        btn_carica.pack(side="left", padx=_S(3))
        _st_btns.append(btn_carica)

        tk.Label(bar, text=" ", bg=c["sfondo"], width=1).pack(side="left")

        btn_continua = tk.Button(bar, text="CONTINUA >", font=self._f_btn, width=_S(12),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._selezione_step(nome_tabella, 1))
        btn_continua.pack(side="left", padx=_S(3))
        _st_btns.append(btn_continua)

        self._kb_setup_bottoni(_st_btns, orizzontale=True)

        status_lbl = tk.Label(self._vista, text="",
            bg=c["sfondo"], fg=c["testo_dim"], font=self._f_status, anchor="w")
        status_lbl.pack(fill="x", padx=_S(10), pady=(0, _S(2)))
        tk.Label(self._vista,
            text="Frecce = Scorri  |  Enter = Carica  |  Tab = COPIA/CARICA/CONTINUA  |  Esc = Indietro",
            bg=c["sfondo"], fg=c["puntini"], font=self._f_small).pack(fill="x", padx=_S(10), pady=(0, _S(4)))

        # Enter / doppio-click
        storico_tree.bind("<Return>",
            lambda e: self._carica_da_storico_pista(nome_tabella, _get_sel_idx()))
        storico_tree.bind("<Double-Button-1>",
            lambda e: self._carica_da_storico_pista(nome_tabella, _get_sel_idx()))

        # Shortcut Ctrl+D per COPIA
        self.root.bind("<Control-d>",
            lambda e: self._copia_setup(nome_tabella, _get_sel_idx()))

        # Sync selezione frecce
        def _sync_sel(event):
            def _do():
                f = storico_tree.focus()
                if f: storico_tree.selection_set(f)
            storico_tree.after_idle(_do)
        storico_tree.bind("<Up>", _sync_sel)
        storico_tree.bind("<Down>", _sync_sel)

        # Seleziona primo (ultimo in ordine cronologico)
        children = storico_tree.get_children()
        if children:
            storico_tree.selection_set(children[0])
            storico_tree.focus(children[0])
            storico_tree.see(children[0])
        storico_tree.focus_set()

        # Escape -> torna a selezione pista
        self.root.bind("<Escape>",
            lambda e: self._selezione_step(nome_tabella, 0))
        self._rimuovi_coperta()


    def _carica_da_storico_pista(self, nome_tabella, idx):
        """Carica un setup dallo storico pista nella scheda form."""
        if idx is None: return

        # Risolvi tutti i riferimenti dal record per popolare _rif_preselezionati
        rec = self.db.leggi(idx)
        if not rec: return

        self._rif_preselezionati = {}
        for rif in self.table_def.riferimenti:
            alias = rif.get("alias", rif["tabella"])
            campo_rec = rif.get("campo_record", rif["campo_chiave"])
            ref_db = self.ref_dbs.get(alias)
            val_code = str(rec.get(campo_rec, "")).strip()
            if ref_db and val_code:
                ref_k = ref_db.table_def.get_campo_chiave()
                ref_td = self.ref_defs.get(alias)
                filtro_ref = None if (ref_td and ref_td.condiviso) else self.filtro_utente()
                indici_ref = ref_db.get_records_filtrati(filtro_ref)
                if ref_k:
                    for ri in indici_ref:
                        r = ref_db.leggi(ri)
                        if r and str(r.get(ref_k["nome"], "")).strip() == val_code:
                            self._rif_preselezionati[alias] = ri
                            break

        self._indici_visibili = self.db.get_records_filtrati(self.filtro_utente())
        if idx in self._indici_visibili:
            self._pos_visibile = self._indici_visibili.index(idx)
        else:
            self._pos_visibile = 0
        self.indice_corrente = idx

        self._costruisci_form(nome_tabella)
        self._mostra_record()


    def _copia_setup(self, nome_tabella, idx):
        """Clona un setup esistente in un nuovo record con data aggiornata."""
        if idx is None: return

        rec = self.db.leggi(idx)
        if not rec: return

        # Risolvi tutti i riferimenti dal record
        self._rif_preselezionati = {}
        for rif in self.table_def.riferimenti:
            alias = rif.get("alias", rif["tabella"])
            campo_rec = rif.get("campo_record", rif["campo_chiave"])
            ref_db = self.ref_dbs.get(alias)
            val_code = str(rec.get(campo_rec, "")).strip()
            if ref_db and val_code:
                ref_k = ref_db.table_def.get_campo_chiave()
                ref_td = self.ref_defs.get(alias)
                filtro_ref = None if (ref_td and ref_td.condiviso) else self.filtro_utente()
                indici_ref = ref_db.get_records_filtrati(filtro_ref)
                if ref_k:
                    for ri in indici_ref:
                        r = ref_db.leggi(ri)
                        if r and str(r.get(ref_k["nome"], "")).strip() == val_code:
                            self._rif_preselezionati[alias] = ri
                            break

        # Costruisci form con riferimenti pre-selezionati
        self._indici_visibili = self.db.get_records_filtrati(self.filtro_utente())
        self._pos_visibile = -1
        self.indice_corrente = -1

        self._costruisci_form(nome_tabella)

        # Attiva modo nuovo
        self._pulisci_campi()
        self.modo_nuovo = True; self.modo_ricerca = False

        # Pre-compila tutti i campi dal record sorgente
        from datetime import datetime as _dt
        for campo in self.table_def.campi:
            n = campo["nome"]
            if campo.get("chiave"): continue
            if n in self.fields:
                val = rec.get(n, "")
                # Aggiorna data a oggi
                if campo["tipo"] == "D" and n in ("Data_Prova", "Data"):
                    val = _dt.now().strftime("%d/%m/%Y")
                # Aggiorna ora
                if campo["tipo"] == "O" and n in ("Ora",):
                    val = _dt.now().strftime("%H:%M")
                if val: self.fields[n].set(str(val))

        # ID automatico
        campo_k = self.table_def.get_campo_chiave()
        if campo_k and self._label_auto_id:
            self._label_auto_id.config(text="Nuovo ID: %s (copia da #%s)" % (
                self.db.prossimo_id(), rec.get(campo_k["nome"], "?")))

        self._aggiorna_contatore(0, len(self._indici_visibili))
        self._status("COPIA: modifica e premi SALVA", "stato_ok")

        # Focus sul primo campo
        primi = list(self.fields.values())
        if primi: primi[0].set_focus()


    # =========================================================================
    #  RIEPILOGO SELEZIONE + STORICO
    # =========================================================================
    def _selezione_riepilogo(self, nome_tabella):
        """Mostra riepilogo selezioni e storico setup trovati.
        Se non ci sono setup -> va diretto alla scheda nuovo."""

        # ── Prima conta i setup esistenti (senza UI) ──
        filtri_ref = {}
        for rif in self.table_def.riferimenti:
            alias = rif.get("alias", rif["tabella"])
            campo_rec = rif.get("campo_record", rif["campo_chiave"])
            idx_rec = self._rif_preselezionati.get(alias)
            if idx_rec is not None:
                ref_db = self.ref_dbs.get(alias)
                if ref_db:
                    rec = ref_db.leggi(idx_rec)
                    ref_k = ref_db.table_def.get_campo_chiave()
                    if rec and ref_k:
                        filtri_ref[campo_rec] = str(rec.get(ref_k["nome"], ""))

        filtro = self.filtro_utente()
        indici = self.db.get_records_filtrati(filtro)
        storico_indici = []
        storico_righe = []

        for idx in indici:
            rec = self.db.leggi(idx)
            if not rec: continue
            trovato = True
            for campo_chiave, valore in filtri_ref.items():
                rec_val = str(rec.get(campo_chiave, "")).strip()
                try:
                    if str(int(rec_val)) != str(int(valore)): trovato = False; break
                except:
                    if rec_val != valore: trovato = False; break
            if not trovato: continue

            parti = []
            campo_k = self.table_def.get_campo_chiave()
            if campo_k: parti.append("#%s" % rec.get(campo_k["nome"], "?"))
            for nome_campo in ("Data_Prova", "Data", "Condizioni", "Tempo_Giro", "Note"):
                val = rec.get(nome_campo, "")
                if val: parti.append(str(val))
            if not parti[1:]:
                for campo in self.table_def.campi:
                    if campo.get("chiave"): continue
                    if campo["nome"] in filtri_ref: continue
                    val = rec.get(campo["nome"], "")
                    if val:
                        parti.append(str(val))
                        if len(parti) >= 4: break

            storico_righe.append("  %s" % "  |  ".join(parti))
            storico_indici.append(idx)

        # ── Se nessun setup: vai diretto a nuovo ──
        if not storico_indici:
            self._nuovo_da_selezione(nome_tabella)
            return

        # ── Ci sono setup: mostra riepilogo ──
        self._pulisci(); c = carica_colori()
        self._storico_indici = storico_indici

        # Header
        header = tk.Frame(self._vista, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))

        tk.Button(header, text="< CAMBIA", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._selezione_step(nome_tabella, 0)).pack(side="left")

        tk.Label(header, text="  %s  -  %d SETUP TROVATI" % (
                    nome_tabella.upper().replace("_", " "), len(storico_indici)),
                 bg=c["sfondo"], fg=c["dati"], font=self._f_title).pack(side="left", padx=(_S(8), 0))

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(4)))

        # Riepilogo selezioni (intestazione compatta)
        for alias, idx_rec in self._rif_preselezionati.items():
            ref_db = self.ref_dbs.get(alias)
            if ref_db:
                desc = ref_db.get_descrizione_record(idx_rec)
                row = tk.Frame(self._vista, bg=c["sfondo"])
                row.pack(fill="x", padx=_S(10), pady=0)
                tk.Label(row, text="%s:" % alias.upper().replace("_", " "),
                         bg=c["sfondo"], fg=c["cerca_testo"], font=self._f_btn,
                         width=12, anchor="w").pack(side="left")
                tk.Label(row, text=desc, bg=c["sfondo"], fg=c["dati"],
                         font=self._f_label, anchor="w").pack(side="left", padx=(_S(4), 0))

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(2)))

        # Listbox storico
        storico_frame = tk.Frame(self._vista, bg=c["sfondo"])
        storico_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(2), _S(4)))

        self._storico_listbox = tk.Listbox(storico_frame, font=self._f_list,
                                            bg=c["sfondo_celle"], fg=c["dati"],
                                            selectbackground=c["cursore"], selectforeground=c["testo_cursore"],
                                            highlightthickness=1, highlightbackground=c["bordo_vuote"],
                                            relief="flat", exportselection=False)
        self._storico_listbox.pack(side="left", fill="both", expand=True)
        st_sb = tk.Scrollbar(storico_frame, orient="vertical", command=self._storico_listbox.yview)
        st_sb.pack(side="right", fill="y")
        self._storico_listbox.configure(yscrollcommand=st_sb.set)

        for riga in storico_righe:
            self._storico_listbox.insert("end", riga)

        # Enter e double-click
        self._storico_listbox.bind("<Double-Button-1>",
            lambda e: self._carica_da_storico(nome_tabella))
        self._storico_listbox.bind("<Return>",
            lambda e: self._carica_da_storico(nome_tabella))

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))

        # Bottoni: solo CARICA e NUOVO
        bot_bar = tk.Frame(self._vista, bg=c["sfondo"])
        bot_bar.pack(pady=(_S(2), _S(4)))

        _riepilogo_btns = []
        btn_carica = tk.Button(bot_bar, text="CARICA", font=self._f_btn, width=_S(10),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._carica_da_storico(nome_tabella))
        btn_carica.pack(side="left", padx=_S(3))
        _riepilogo_btns.append(btn_carica)

        btn_nuovo = tk.Button(bot_bar, text="NUOVO", font=self._f_btn, width=_S(10),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._nuovo_da_selezione(nome_tabella))
        btn_nuovo.pack(side="left", padx=_S(3))
        _riepilogo_btns.append(btn_nuovo)

        self._kb_setup_bottoni(_riepilogo_btns, orizzontale=True)

        status_lbl = tk.Label(self._vista, text="",
                               bg=c["sfondo"], fg=c["testo_dim"], font=self._f_status, anchor="w")
        status_lbl.pack(fill="x", padx=_S(10), pady=(0, _S(2)))
        tk.Label(self._vista, text="Frecce = Scorri  |  Enter = Apri setup  |  Tab = Bottoni  |  Esc = Indietro",
                 bg=c["sfondo"], fg=c["puntini"], font=self._f_small).pack(fill="x", padx=_S(10), pady=(0, _S(4)))

        # Focus sulla listbox
        self._storico_listbox.selection_set(0)
        self._storico_listbox.focus_set()

        # Escape -> torna al wizard
        self.root.bind("<Escape>",
            lambda e: self._selezione_step(nome_tabella, 0))
        self._rimuovi_coperta()


    def _carica_da_storico(self, nome_tabella):
        """Carica un setup selezionato dallo storico."""
        c = carica_colori()
        sel = self._storico_listbox.curselection()
        if not sel or not self._storico_indici:
            return

        idx = self._storico_indici[sel[0]]
        self._indici_visibili = self._storico_indici
        self._pos_visibile = sel[0]
        self.indice_corrente = idx

        self._costruisci_form(nome_tabella)
        self._mostra_record()

    def _nuovo_da_selezione(self, nome_tabella):
        """Crea nuovo setup con riferimenti pre-selezionati dal wizard."""
        # I riferimenti sono gia' in self._rif_preselezionati dal wizard
        for alias in self._rif_preselezionati:
            ref_db = self.ref_dbs.get(alias)
            if not ref_db or ref_db.conteggio() == 0:
                pass  # Tabella vuota
                return

        self._indici_visibili = self.db.get_records_filtrati(self.filtro_utente())
        self._pos_visibile = -1
        self.indice_corrente = -1

        self._costruisci_form(nome_tabella)
        self._nuovo_con_riferimenti()

    def _nuovo_con_riferimenti(self):
        """Prepara form nuovo con riferimenti selezionati."""
        self._pulisci_campi()
        self.modo_nuovo = True; self.modo_ricerca = False

        # Pre-seleziona solo se ci sono listbox (non label fisse da wizard)
        if not (hasattr(self, '_ref_fixed') and self._ref_fixed):
            for alias, idx_rec in self._rif_preselezionati.items():
                if alias in self.ref_selectors:
                    lb = self.ref_selectors[alias]["listbox"]
                    ref_db = self.ref_selectors[alias]["db"]
                    if ref_db:
                        lb.selection_clear(0, "end")
                        filtro = self.filtro_utente()
                        indici_ref = ref_db.get_records_filtrati(filtro)
                        for lb_pos, real_idx in enumerate(indici_ref):
                            if real_idx == idx_rec:
                                lb.selection_set(lb_pos)
                                lb.see(lb_pos)
                                break

        campo_k = self.table_def.get_campo_chiave()
        if campo_k and self._label_auto_id:
            self._label_auto_id.config(text="Nuovo ID: %s" % self.db.prossimo_id())
        self._aggiorna_contatore(0, len(self._indici_visibili))
        self._status("Compila i campi e premi SALVA", "stato_ok")
        primi = list(self.fields.values())
        if primi: primi[0].set_focus()
        # Auto-fetch meteo per nuovo record wizard
        if _HAS_METEO and self.table_def.puo("crono"):
            self.root.after(500, self._auto_meteo)

    def _apri_tutti(self, nome_tabella):
        """Apri tutti i record della tabella (come prima)."""
        self._indici_visibili = self.db.get_records_filtrati(self.filtro_utente())
        self._pos_visibile = 0 if self._indici_visibili else -1
        self.indice_corrente = self._indici_visibili[0] if self._indici_visibili else -1
        self._costruisci_form(nome_tabella)
        self._mostra_record()

    def _elenco_da_selezione(self, nome_tabella):
        """Apri vista elenco dalla schermata selezione."""
        self._indici_visibili = self.db.get_records_filtrati(self.filtro_utente())
        self._pos_visibile = 0 if self._indici_visibili else -1
        self.indice_corrente = self._indici_visibili[0] if self._indici_visibili else -1
        self._schermata_elenco(nome_tabella)

    # =========================================================================
    #  VISTA ELENCO (griglia tabellare)
    # =========================================================================
    def _schermata_elenco(self, nome_tabella, cerca=False):
        """Vista elenco: tutti i record su righe, seleziona e torna a scheda.
        Se cerca=True, mostra barra di ricerca con filtro in tempo reale."""
        self._pulisci(); c = carica_colori()

        # ── Stile Treeview retro ──
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Retro.Treeview",
            background=c["sfondo_celle"], foreground=c["dati"],
            fieldbackground=c["sfondo_celle"], font=("Consolas", _S(8)),
            rowheight=_S(22), borderwidth=0)
        style.configure("Retro.Treeview.Heading",
            background=c["pulsanti_sfondo"], foreground=c["pulsanti_testo"],
            font=("Consolas", _S(8), "bold"), borderwidth=1, relief="ridge")
        style.map("Retro.Treeview",
            background=[("selected", c["cursore"])],
            foreground=[("selected", c["testo_cursore"])])
        style.map("Retro.Treeview.Heading",
            background=[("active", c["cerca_sfondo"])])

        # ── Header ──
        header = tk.Frame(self._vista, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))

        if self.table_def.is_composite:
            back_cmd = lambda: self._schermata_selezione(nome_tabella)
            tk.Button(header, text="< SELEZIONE", font=self._f_small,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2", command=back_cmd).pack(side="left")
        else:
            tk.Button(header, text="< MENU", font=self._f_small,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2", command=self._schermata_menu).pack(side="left")

        tk.Label(header, text="  %s  -  ELENCO" % nome_tabella.upper().replace("_", " "),
                 bg=c["sfondo"], fg=c["dati"], font=self._f_title).pack(side="left", padx=(_S(8), 0))

        # Conteggio record
        indici = self._indici_visibili if self._indici_visibili else \
                 self.db.get_records_filtrati(self.filtro_utente())
        self._elenco_indici = list(indici)

        tk.Label(header, text="%d record" % len(self._elenco_indici),
                 bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small).pack(side="right")

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(2)))

        # ── Barra ricerca (solo se !cerca;vero) ──
        self._elenco_cerca_var = tk.StringVar()
        self._elenco_search_entry = None
        count_bar = tk.Frame(self._vista, bg=c["sfondo"])
        count_bar.pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))
        if self.table_def.puo("cerca"):
            tk.Label(count_bar, text="CERCA:", bg=c["sfondo"], fg=c["cerca_testo"],
                     font=self._f_btn).pack(side="left")
            self._elenco_search_entry = tk.Entry(count_bar, font=self._f_label, width=30,
                     bg=c["sfondo_celle"], fg=c["dati"], insertbackground=c["dati"],
                     relief="flat", highlightthickness=1, highlightbackground=c["bordo_vuote"],
                     highlightcolor=c["cerca_testo"],
                     textvariable=self._elenco_cerca_var)
            self._elenco_search_entry.pack(side="left", padx=(_S(4), _S(8)), fill="x", expand=True)
        self._elenco_count_label = tk.Label(count_bar, text="%d record" % len(self._elenco_indici),
                 bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small)
        self._elenco_count_label.pack(side="right")

        # ── Prepara colonne ──
        colonne = []  # lista di (id_colonna, titolo, larghezza_px)
        campo_k = self.table_def.get_campo_chiave()

        # Colonna chiave
        if campo_k:
            colonne.append((campo_k["nome"], campo_k["nome"].replace("_", " "), _S(60)))

        # Colonne riferimenti (risolti a descrizione)
        for rif in self.table_def.riferimenti:
            alias = rif.get("alias", rif["tabella"])
            col_id = "_ref_%s" % alias
            colonne.append((col_id, alias.upper().replace("_", " "), _S(140)))

        # Colonne campi propri
        for campo in self.table_def.campi:
            if campo.get("chiave"): continue
            larg = max(_S(50), min(_S(200), _S(campo["lunghezza"] * 8)))
            colonne.append((campo["nome"], campo["nome"].replace("_", " "), larg))

        col_ids = [col[0] for col in colonne]

        # ── Treeview con scroll ──
        tree_frame = tk.Frame(self._vista, bg=c["sfondo"])
        tree_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(2), _S(2)))

        self._elenco_tree = ttk.Treeview(tree_frame, columns=col_ids,
                                          show="headings", style="Retro.Treeview",
                                          selectmode="browse")

        # Scrollbar verticale
        vsb = tk.Scrollbar(tree_frame, orient="vertical", command=self._elenco_tree.yview)
        vsb.pack(side="right", fill="y")
        # Scrollbar orizzontale
        hsb = tk.Scrollbar(tree_frame, orient="horizontal", command=self._elenco_tree.xview)
        hsb.pack(side="bottom", fill="x")
        self._elenco_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._elenco_tree.pack(side="left", fill="both", expand=True)

        # Configura colonne
        for col_id, titolo, larg in colonne:
            self._elenco_tree.heading(col_id, text=titolo, anchor="w")
            self._elenco_tree.column(col_id, width=larg, minwidth=_S(40), anchor="w")

        # ── Prepara cache riferimenti (ID -> descrizione) ──
        ref_cache = {}  # {alias: {codice: descrizione}}
        for rif in self.table_def.riferimenti:
            alias = rif.get("alias", rif["tabella"])
            ref_db = self.ref_dbs.get(alias)
            ref_td = self.ref_defs.get(alias)
            ref_cache[alias] = {}
            if ref_db:
                for i in range(len(ref_db.records)):
                    rec = ref_db.leggi(i)
                    if rec:
                        rk = ref_db.table_def.get_campo_chiave()
                        if rk:
                            codice = str(rec.get(rk["nome"], ""))
                            ref_cache[alias][codice] = ref_db.get_descrizione_record(i)

        # ── Popola righe ──
        for idx in self._elenco_indici:
            rec = self.db.leggi(idx)
            if not rec: continue
            valori = []
            # Chiave
            if campo_k:
                valori.append(rec.get(campo_k["nome"], ""))
            # Riferimenti risolti
            for rif in self.table_def.riferimenti:
                alias = rif.get("alias", rif["tabella"])
                campo_rec = rif.get("campo_record", rif["campo_chiave"])
                codice_val = str(rec.get(campo_rec, ""))
                desc = ref_cache.get(alias, {}).get(codice_val, codice_val)
                valori.append(desc)
            # Campi propri
            for campo in self.table_def.campi:
                if campo.get("chiave"): continue
                val = rec.get(campo["nome"], "")
                # Nascondi password
                if campo["nome"] == "Password" and self._nome_tabella == "utenti":
                    val = "******" if val else ""
                valori.append(str(val))

            self._elenco_tree.insert("", "end", iid=str(idx), values=valori)

        # ── Salva dati per filtro ricerca ──
        self._elenco_all_rows = []  # [(iid, valori_tuple), ...]
        for child in self._elenco_tree.get_children():
            vals = self._elenco_tree.item(child, "values")
            self._elenco_all_rows.append((child, vals))

        # ── Righe alternate (zebra) ──
        self._elenco_tree.tag_configure("dispari",
            background=c["sfondo_celle_piene"])
        for i, item in enumerate(self._elenco_tree.get_children()):
            if i % 2:
                self._elenco_tree.item(item, tags=("dispari",))

        # ── Double-click e Enter per aprire ──
        self._elenco_tree.bind("<Double-1>",
            lambda e: self._elenco_apri_record(nome_tabella))
        self._elenco_tree.bind("<Return>",
            lambda e: self._elenco_apri_record(nome_tabella))

        # Frecce: sincronizza selezione con focus (alcune versioni di Tk non lo fanno)
        def _sync_sel(event):
            # after_idle: esegui DOPO che Tk ha spostato il focus interno
            def _do_sync():
                focused = self._elenco_tree.focus()
                if focused:
                    self._elenco_tree.selection_set(focused)
            self._elenco_tree.after_idle(_do_sync)
        self._elenco_tree.bind("<Up>", _sync_sel)
        self._elenco_tree.bind("<Down>", _sync_sel)

        # ── Barra inferiore ──
        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))

        bar = tk.Frame(self._vista, bg=c["sfondo"])
        bar.pack(pady=(_S(2), _S(6)))

        btn_apri = tk.Button(bar, text="APRI SCHEDA", font=self._f_btn, width=_S(14),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  activebackground=c["pulsanti_sfondo"], activeforeground=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._elenco_apri_record(nome_tabella))
        btn_apri.pack(side="left", padx=_S(3))

        btn_scheda = tk.Button(bar, text="SCHEDA", font=self._f_btn, width=_S(9),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._elenco_vai_scheda(nome_tabella))
        btn_scheda.pack(side="left", padx=_S(3))

        # Navigazione tastiera bottoni elenco
        self._kb_setup_bottoni([btn_apri, btn_scheda], orizzontale=True)

        self._elenco_status = tk.Label(self._vista, text="",
                                        bg=c["sfondo"], fg=c["testo_dim"], font=self._f_status, anchor="w")
        self._elenco_status.pack(fill="x", padx=_S(10), pady=(0, _S(2)))
        tk.Label(self._vista, text="Frecce = Scorri  |  Enter = Apri record  |  Tab = Bottoni  |  Esc = Torna alla scheda",
                 bg=c["sfondo"], fg=c["puntini"], font=self._f_small).pack(fill="x", padx=_S(10), pady=(0, _S(4)))

        # Focus sulla Treeview + seleziona record
        children = self._elenco_tree.get_children()
        if children:
            # Se c'e' un record corrente, selezionalo
            target = None
            if self.indice_corrente >= 0:
                iid = str(self.indice_corrente)
                if iid in children:
                    target = iid
            # Altrimenti seleziona il primo
            if not target:
                target = children[0]

            self._elenco_tree.selection_set(target)
            self._elenco_tree.focus(target)  # focus interno Treeview (riga evidenziata)
            self._elenco_tree.see(target)

        # Focus widget sulla Treeview (abilita frecce)
        self._elenco_tree.focus_set()

        # ── Attiva filtro ricerca in tempo reale (solo se cerca attivo) ──
        if self._elenco_search_entry:
            self._elenco_cerca_var.trace_add("write", lambda *a: self._elenco_filtra())
            self._elenco_search_entry.bind("<Escape>", lambda e: self._elenco_clear_search())
            self._elenco_search_entry.bind("<Return>", lambda e: self._elenco_tree.focus_set())
            self._elenco_search_entry.bind("<Down>", lambda e: self._elenco_tree.focus_set())

        # Se aperto da CERCA, focus sulla barra di ricerca
        if cerca and self._elenco_search_entry:
            self._elenco_search_entry.focus_set()

        # Escape -> torna indietro
        if self.table_def.is_composite:
            self.root.bind("<Escape>", lambda e: self._schermata_selezione(nome_tabella))
        else:
            self.root.bind("<Escape>", lambda e: self._schermata_menu())
        self._rimuovi_coperta()

    def _elenco_filtra(self):
        """Filtra righe ELENCO in tempo reale in base al testo cercato."""
        testo = self._elenco_cerca_var.get().strip().lower()
        c = carica_colori()
        # Cancella tutto
        self._elenco_tree.delete(*self._elenco_tree.get_children())
        # Re-inserisci solo le righe che matchano
        count = 0
        for iid, vals in self._elenco_all_rows:
            if not testo or any(testo in str(v).lower() for v in vals):
                self._elenco_tree.insert("", "end", iid=iid, values=vals)
                count += 1
        # Zebra
        for i, item in enumerate(self._elenco_tree.get_children()):
            if i % 2:
                self._elenco_tree.item(item, tags=("dispari",))
            else:
                self._elenco_tree.item(item, tags=())
        # Seleziona primo risultato
        children = self._elenco_tree.get_children()
        if children:
            self._elenco_tree.selection_set(children[0])
            self._elenco_tree.focus(children[0])
            self._elenco_tree.see(children[0])
        # Aggiorna conteggio
        tot = len(self._elenco_all_rows)
        if testo:
            self._elenco_count_label.config(
                text="%d/%d trovati" % (count, tot), fg=c["cerca_testo"])
        else:
            self._elenco_count_label.config(
                text="%d record" % tot, fg=c["testo_dim"])

    def _elenco_clear_search(self):
        """Pulisce la barra di ricerca e mostra tutti i record."""
        if self._elenco_cerca_var.get():
            self._elenco_cerca_var.set("")
        else:
            # Se gia' vuoto, ESC torna alla scheda
            self._elenco_tree.focus_set()

    def _elenco_apri_record(self, nome_tabella):
        """Apri il record selezionato nella vista scheda."""
        sel = self._elenco_tree.selection()
        if not sel:
            c = carica_colori()
            self._elenco_status.config(text="Seleziona un record!", fg=c["stato_errore"])
            return

        idx = int(sel[0])  # iid = indice nel db
        # Posiziona sulla lista visibile
        if idx in self._elenco_indici:
            self._pos_visibile = self._elenco_indici.index(idx)
        self.indice_corrente = idx
        self._indici_visibili = list(self._elenco_indici)

        self._costruisci_form(nome_tabella)
        self._mostra_record()

    def _elenco_vai_scheda(self, nome_tabella):
        """Torna alla vista scheda mantenendo la posizione corrente."""
        self._indici_visibili = list(self._elenco_indici) if hasattr(self, '_elenco_indici') else \
                                self.db.get_records_filtrati(self.filtro_utente())
        if not self._indici_visibili:
            self._pos_visibile = -1
        elif self._pos_visibile < 0:
            self._pos_visibile = 0
        self.indice_corrente = self._indici_visibili[self._pos_visibile] \
                               if self._pos_visibile >= 0 and self._indici_visibili else -1
        self._costruisci_form(nome_tabella)
        self._mostra_record()

    # =========================================================================
    #  FORM CRUD
    # =========================================================================
    def _costruisci_form(self, nome_tabella):
        self._pulisci(); c = carica_colori()
        self.fields = {}; self.ref_selectors = {}

        max_label = 12
        for campo in self.table_def.campi:
            if len(campo["nome"]) + 1 > max_label: max_label = len(campo["nome"]) + 1
        for rif in self.table_def.riferimenti:
            l = len(rif.get("alias", rif["tabella"])) + 2
            if l > max_label: max_label = l
        max_lun = max((c["lunghezza"] for c in self.table_def.campi), default=20)
        n_own = len(self.table_def.campi); n_refs = len(self.table_def.riferimenti)

        # Header
        header = tk.Frame(self._vista, bg=c["sfondo"]); header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))
        if self.table_def.is_composite:
            back_cmd = lambda: self._schermata_selezione(nome_tabella)
            tk.Button(header, text="< SELEZIONE", font=self._f_small,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2", command=back_cmd).pack(side="left")
        else:
            tk.Button(header, text="< MENU", font=self._f_small,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2", command=self._schermata_menu).pack(side="left")
        tipo_label = " [SETUP]" if self.table_def.is_composite else ""
        tk.Label(header, text="  %s%s" % (nome_tabella.upper().replace("_"," "), tipo_label),
                 bg=c["sfondo"], fg=c["dati"], font=self._f_title).pack(side="left", padx=(_S(8), 0))
        # Indicazione sessione
        if self.table_def.condiviso:
            tk.Label(header, text="[DATI CONDIVISI]", bg=c["sfondo"], fg=c["stato_ok"],
                     font=self._f_small).pack(side="right", padx=(_S(5), 0))
        else:
            tk.Label(header, text="[I TUOI DATI]", bg=c["sfondo"], fg=c["stato_avviso"],
                     font=self._f_small).pack(side="right", padx=(_S(5), 0))
        self._label_contatore = tk.Label(header, text="", bg=c["sfondo"], fg=c["testo_dim"],
                                          font=self._f_label)
        self._label_contatore.pack(side="right")
        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(2)))

        # Area scrollabile
        scroll_cont = tk.Frame(self._vista, bg=c["sfondo"])
        scroll_cont.pack(fill="both", expand=True, padx=_S(10), pady=(_S(2), _S(2)))
        self._fields_canvas = tk.Canvas(scroll_cont, bg=c["sfondo"], highlightthickness=0)
        self._fields_canvas.pack(side="left", fill="both", expand=True)
        total_items = n_refs + n_own
        if total_items > MAX_VISIBLE_FIELDS:
            csb = tk.Scrollbar(scroll_cont, orient="vertical", command=self._fields_canvas.yview)
            csb.pack(side="right", fill="y")
            self._fields_canvas.configure(yscrollcommand=csb.set)
        self._fields_inner = tk.Frame(self._fields_canvas, bg=c["sfondo"])
        self._fields_canvas.create_window((0, 0), window=self._fields_inner, anchor="nw")

        # Riferimenti
        self._ref_fixed = {}  # {campo_record: valore} per riferimenti fissi (wizard)
        ha_rif_preselezionati = (self.table_def.is_composite and
                                  hasattr(self, '_rif_preselezionati') and
                                  self._rif_preselezionati)

        if self.table_def.riferimenti and ha_rif_preselezionati:
            # ── INTESTAZIONE FISSA (da wizard) ──
            # Mostra riferimenti come testo non modificabile, stile "foglio A4"
            intestazione = tk.Frame(self._fields_inner, bg=c["sfondo"])
            intestazione.pack(fill="x", pady=(_S(1), _S(4)))

            for rif in self.table_def.riferimenti:
                alias = rif.get("alias", rif["tabella"])
                campo_rec = rif.get("campo_record", rif["campo_chiave"])
                idx_rec = self._rif_preselezionati.get(alias)
                ref_db = self.ref_dbs.get(alias)

                # Recupera descrizione e codice chiave
                desc = ""
                if idx_rec is not None and ref_db:
                    desc = ref_db.get_descrizione_record(idx_rec)
                    rec = ref_db.leggi(idx_rec)
                    ref_k = ref_db.table_def.get_campo_chiave()
                    if rec and ref_k:
                        self._ref_fixed[campo_rec] = str(rec.get(ref_k["nome"], ""))

                row = tk.Frame(intestazione, bg=c["sfondo"])
                row.pack(fill="x", pady=0)
                tk.Label(row, text="%s:" % alias.upper().replace("_", " "),
                         bg=c["sfondo"], fg=c["cerca_testo"], font=self._f_btn,
                         width=max_label, anchor="w").pack(side="left")
                tk.Label(row, text=desc if desc else "(non selezionato)",
                         bg=c["sfondo"], fg=c["dati"], font=self._f_label,
                         anchor="w").pack(side="left", padx=(_S(4), 0))

            tk.Frame(self._fields_inner, bg=c["linee"], height=1).pack(fill="x", pady=(_S(2), _S(4)))

        elif self.table_def.riferimenti:
            # ── LISTBOX SELEZIONABILI (tabelle non-composite o senza wizard) ──
            for rif in self.table_def.riferimenti:
                alias = rif.get("alias", rif["tabella"])
                campo_rec = rif.get("campo_record", rif["campo_chiave"])
                ref_db = self.ref_dbs.get(alias)
                ref_frame = tk.Frame(self._fields_inner, bg=c["sfondo"])
                ref_frame.pack(fill="x", pady=(_S(2), _S(2)))
                tk.Label(ref_frame, text="%s:" % alias.upper().replace("_"," "),
                         bg=c["sfondo"], fg=c["cerca_testo"], font=self._f_btn, anchor="w").pack(anchor="w")
                list_frame = tk.Frame(ref_frame, bg=c["sfondo"]); list_frame.pack(fill="x")
                lb = tk.Listbox(list_frame, font=self._f_list, height=3,
                                bg=c["sfondo_celle"], fg=c["dati"],
                                selectbackground=c["cursore"], selectforeground=c["testo_cursore"],
                                highlightthickness=1, highlightbackground=c["bordo_vuote"],
                                relief="flat", exportselection=False)
                lb.pack(side="left", fill="x", expand=True)
                lb_sb = tk.Scrollbar(list_frame, orient="vertical", command=lb.yview)
                lb_sb.pack(side="right", fill="y"); lb.configure(yscrollcommand=lb_sb.set)
                if ref_db:
                    ref_td = self.ref_defs.get(alias)
                    if ref_td and ref_td.condiviso:
                        filtro = None
                    else:
                        filtro = self.filtro_utente()
                    indici_ref = ref_db.get_records_filtrati(filtro)
                    for idx in indici_ref:
                        lb.insert("end", ref_db.get_descrizione_record(idx))
                self.ref_selectors[alias] = {"listbox": lb, "campo_record": campo_rec,
                                              "campo_chiave": rif["campo_chiave"],
                                              "db": ref_db, "indici": indici_ref if ref_db else []}
                if not ref_db or ref_db.conteggio() == 0:
                    tk.Label(ref_frame, text="(vuota - inserisci prima in %s)" % alias,
                             bg=c["sfondo"], fg=c["stato_avviso"], font=self._f_small).pack(anchor="w")
            tk.Frame(self._fields_inner, bg=c["linee"], height=1).pack(fill="x", pady=_S(4))

        # ID auto
        campo_k = self.table_def.get_campo_chiave()
        if campo_k:
            id_frame = tk.Frame(self._fields_inner, bg=c["sfondo"]); id_frame.pack(fill="x", pady=_S(1), anchor="w")
            tk.Label(id_frame, text=campo_k["nome"].replace("_"," "), bg=c["sfondo"], fg=c["testo_dim"],
                     font=self._f_label, width=max_label, anchor="w").pack(side="left", padx=(0, _S(4)))
            self._label_auto_id = tk.Label(id_frame, text="[AUTO]", bg=c["sfondo"], fg=c["stato_avviso"],
                                            font=self._f_btn)
            self._label_auto_id.pack(side="left")
        else: self._label_auto_id = None

        # Campi propri (con separatori sezione da .def)
        self._sezione_primi_campi = []  # Primo campo di ogni sezione (per PgUp/PgDown)
        _prima_sezione = True
        for campo in self.table_def.campi:
            if campo.get("chiave"): continue
            # Separatore sezione (da !sezione;TITOLO nel .def)
            sez_titolo = self.table_def.sezioni.get(campo["nome"])
            if sez_titolo:
                sez_frame = tk.Frame(self._fields_inner, bg=c["sfondo"])
                sez_frame.pack(fill="x", pady=(_S(6), _S(1)))
                tk.Frame(sez_frame, bg=c["linee"], height=1).pack(fill="x", pady=(0, _S(2)))
                tk.Label(sez_frame, text=sez_titolo, bg=c["sfondo"],
                         fg=c["cerca_testo"], font=self._f_btn, anchor="w").pack(anchor="w")
                _prima_sezione = False  # prossimo campo e' il primo della sezione
            rf = RetroField(self._fields_inner, label=campo["nome"].replace("_"," "), tipo=campo["tipo"],
                            lunghezza=campo["lunghezza"], label_width=max_label)
            rf.pack(pady=_S(1), anchor="w", fill="x")
            self.fields[campo["nome"]] = rf
            # Segna primo campo di ogni sezione (per navigazione rapida)
            if sez_titolo or (not self._sezione_primi_campi and _prima_sezione):
                self._sezione_primi_campi.append(campo["nome"])
                _prima_sezione = False

        self._fields_inner.update_idletasks()
        self._fields_canvas.configure(scrollregion=self._fields_canvas.bbox("all"))
        self._fields_canvas.bind_all("<MouseWheel>",
            lambda e: self._fields_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # Auto-scroll: quando un campo riceve focus, scrolla per renderlo visibile
        def _scroll_al_campo(event):
            widget = event.widget
            # Risali al RetroField padre (il canvas e' dentro RetroField -> row -> ...)
            rf = widget
            while rf and rf != self._fields_inner:
                if rf.master == self._fields_inner:
                    break
                rf = rf.master
            if not rf or rf == self._fields_inner:
                return
            try:
                self._fields_canvas.update_idletasks()
                # Posizione del campo nell'inner frame
                wy = rf.winfo_y()
                wh = rf.winfo_height()
                # Dimensioni area visibile
                canvas_h = self._fields_canvas.winfo_height()
                bbox = self._fields_canvas.bbox("all")
                if not bbox: return
                total_h = bbox[3] - bbox[1]
                if total_h <= canvas_h: return
                # Posizione scroll corrente in pixel
                top_frac = self._fields_canvas.yview()[0]
                top_px = top_frac * total_h
                bottom_px = top_px + canvas_h
                # Se il campo e' fuori vista, scrolla
                if wy < top_px:
                    self._fields_canvas.yview_moveto(max(0, wy - _S(10)) / total_h)
                elif wy + wh > bottom_px:
                    self._fields_canvas.yview_moveto(
                        min(1.0, (wy + wh - canvas_h + _S(10)) / total_h))
            except: pass

        # Bind FocusIn su tutti i canvas dei RetroField
        for nome, rf in self.fields.items():
            if hasattr(rf, '_canvas'):
                rf._canvas.bind("<FocusIn>", _scroll_al_campo, add="+")

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))

        # Barra nav + azioni (controllata da flag .def)
        bar = tk.Frame(self._vista, bg=c["sfondo"]); bar.pack(pady=(_S(2), _S(2)))
        _bar_btns = []  # tutti i bottoni della barra per navigazione tastiera
        # Tabella utenti: limita in base a licenza
        _limite_utenti = False
        if self._nome_tabella == "utenti" and self._max_utenti == 1:
            _limite_utenti = True  # mono: blocca tutto
        nav_state = "disabled" if _limite_utenti else (
            "normal" if self.table_def.puo("naviga") else "disabled")
        for sym, cmd in [("\u23ee", self._vai_primo), ("\u25c4\n^O", self._vai_precedente),
                         ("\u25ba\n^P", self._vai_successivo), ("\u23ed", self._vai_ultimo)]:
            btn = tk.Button(bar, text=sym, font=self._f_nav, bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      activebackground=c["pulsanti_sfondo"], activeforeground=c["pulsanti_testo"],
                      relief="ridge", bd=1, width=2, state=nav_state)
            if nav_state == "normal":
                btn.config(cursor="hand2", command=self._flash_btn(btn, cmd))
            btn.pack(side="left", padx=_S(2))
            _bar_btns.append(btn)
        tk.Label(bar, text=" ", bg=c["sfondo"], width=1).pack(side="left")

        # Mappa operazione -> bottone (con shortcut nel testo)
        bottoni_ops = [
            ("NUOVO\n^N",    "nuovo",    "pulsanti_sfondo", "pulsanti_testo", self._nuovo),
            ("SALVA\n^S",    "salva",    "pulsanti_sfondo", "pulsanti_testo", self._salva),
            ("CERCA\n^F",    "cerca",    "pulsanti_sfondo", "pulsanti_testo", self._cerca),
            ("CANCELLA\n^X", "cancella", "pulsanti_sfondo", "pulsanti_testo", self._cancella),
        ]
        # Mappa bottoni per flash da tastiera
        self._btn_map = {}
        for txt, op, bg_k, fg_k, cmd in bottoni_ops:
            abilitato = self.table_def.puo(op)
            # Monoutente: solo SALVA nella tabella utenti
            if _limite_utenti and op != "salva":
                abilitato = False
            btn = tk.Button(bar, text=txt, font=self._f_btn, width=_S(9),
                      relief="ridge", bd=1)
            if abilitato:
                btn.config(bg=c[bg_k], fg=c[fg_k],
                          activebackground=c[bg_k], activeforeground=c[fg_k],
                          state="normal", cursor="hand2",
                          command=self._flash_btn(btn, cmd))
                self._btn_map[op] = btn
            else:
                btn.config(bg=c["sfondo"], fg=c["testo_dim"],
                          state="disabled", disabledforeground=c["testo_dim"],
                          relief="flat")
            btn.pack(side="left", padx=_S(2))
            _bar_btns.append(btn)

        # Bottone ELENCO (vista tabellare) — nascosto se monoutente
        if self.table_def.puo("elenca") and not _limite_utenti:
            tk.Label(bar, text=" ", bg=c["sfondo"], width=1).pack(side="left")
            btn_el = tk.Button(bar, text="ELENCO\n^E", font=self._f_btn, width=_S(9),
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      activebackground=c["pulsanti_sfondo"], activeforeground=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2")
            btn_el.config(command=self._flash_btn(btn_el, lambda: self._schermata_elenco(nome_tabella)))
            btn_el.pack(side="left", padx=_S(2))
            _bar_btns.append(btn_el)
            self._btn_map["elenca"] = btn_el

        # Bottone COPIA (solo tabelle composite / setup)
        if self.table_def.is_composite and self.table_def.puo("nuovo"):
            btn_cp = tk.Button(bar, text="COPIA\n^D", font=self._f_btn, width=_S(9),
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      activebackground=c["pulsanti_sfondo"], activeforeground=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2")
            btn_cp.config(command=self._flash_btn(btn_cp, lambda: self._copia_setup(nome_tabella,
                          self.indice_corrente if self.indice_corrente >= 0 else None)))
            btn_cp.pack(side="left", padx=_S(2))
            _bar_btns.append(btn_cp)
            self._btn_map["copia"] = btn_cp

        # Bottone CRONO (cronometraggio) e TEMPI (solo se !crono;vero nel .def + laptimer attivo)
        if self.table_def.puo("crono") and _HAS_CRONO:
            tk.Label(bar, text=" ", bg=c["sfondo"], width=1).pack(side="left")
            btn_crono = tk.Button(bar, text="CRONO\n^T", font=self._f_btn, width=_S(9),
                      bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                      activebackground=c["pulsanti_sfondo"], activeforeground=c["stato_avviso"],
                      relief="ridge", bd=1, cursor="hand2")
            btn_crono.config(command=self._flash_btn(btn_crono, self._lancia_crono))
            btn_crono.pack(side="left", padx=_S(2))
            _bar_btns.append(btn_crono)
            self._btn_map["crono"] = btn_crono



        # Bottone STAMPA SCHEDA (solo se !stampa;vero nel .def + modulo stampante)
        if _HAS_THERMAL and self.table_def.puo("stampa"):
            tk.Label(bar, text=" ", bg=c["sfondo"], width=1).pack(side="left")
            # Disabilita se stampante BT non trovata (Linux)
            stampa_attiva = (not _is_linux()) or self._bt_stampante_ok or os.path.exists("/dev/rfcomm0")
            btn_stampa_scheda = tk.Button(bar, text="STAMPA\n^G", font=self._f_btn, width=_S(9),
                      relief="ridge", bd=1)
            if stampa_attiva:
                btn_stampa_scheda.config(bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                          activebackground=c["pulsanti_sfondo"], activeforeground=c["stato_avviso"],
                          state="normal", cursor="hand2",
                          command=self._flash_btn(btn_stampa_scheda, self._stampa_scheda_record))
                self._btn_map["stampa"] = btn_stampa_scheda
            else:
                btn_stampa_scheda.config(bg=c["sfondo"], fg=c["testo_dim"],
                          state="disabled", disabledforeground=c["testo_dim"],
                          relief="flat")
            btn_stampa_scheda.pack(side="left", padx=_S(2))
            _bar_btns.append(btn_stampa_scheda)

        # Navigazione tastiera sulla barra
        self._kb_setup_bottoni(_bar_btns, orizzontale=True)

        # Scorciatoie da tastiera
        attivi = [b for b in _bar_btns if str(b["state"]) != "disabled"]
        if attivi:
            self.root.bind("<Control-b>", lambda e: attivi[0].focus_set())
        self.root.bind("<Control-s>", lambda e: self._flash_key("salva", self._salva))
        self.root.bind("<Control-n>", lambda e: self._flash_key("nuovo", self._nuovo))
        self.root.bind("<Control-f>", lambda e: self._flash_key("cerca", self._cerca))
        self.root.bind("<Control-x>", lambda e: self._flash_key("cancella", self._cancella))
        if self.table_def.puo("elenca"):
            self.root.bind("<Control-e>", lambda e: self._flash_key("elenca",
                lambda: self._schermata_elenco(nome_tabella)))
        # COPIA setup (solo tabelle composite)
        if self.table_def.is_composite and self.table_def.puo("nuovo"):
            self.root.bind("<Control-d>", lambda e: self._flash_key("copia",
                lambda: self._copia_setup(nome_tabella,
                    self.indice_corrente if self.indice_corrente >= 0 else None)))
        # Ctrl+H = torna al primo campo (Home campi)
        primi_campi = list(self.fields.values())
        if primi_campi:
            self.root.bind("<Control-h>", lambda e: primi_campi[0].set_focus())
        # Salta tra sezioni: PgUp/PgDown oppure Ctrl+Freccia Su/Giu
        self.root.bind("<Prior>", lambda e: self._salta_sezione(-1))           # PgUp
        self.root.bind("<Next>", lambda e: self._salta_sezione(1))             # PgDown
        self.root.bind("<Control-Up>", lambda e: self._salta_sezione(-1))      # Ctrl+Su
        self.root.bind("<Control-Down>", lambda e: self._salta_sezione(1))     # Ctrl+Giu
        # Navigazione record
        self.root.bind("<Control-o>", lambda e: self._vai_precedente())
        self.root.bind("<Control-p>", lambda e: self._vai_successivo())
        # LapTimer (solo se !laptimer;vero nel .def + licenza attiva)
        if self.table_def.puo("crono") and _HAS_CRONO:
            self.root.bind("<Control-t>", lambda e: self._flash_key("crono", self._lancia_crono))
        # Stampa scheda (solo se !stampa;vero nel .def + stampante trovata)
        if _HAS_THERMAL and self.table_def.puo("stampa") and ((not _is_linux()) or self._bt_stampante_ok):
            self.root.bind("<Control-g>", lambda e: self._flash_key("stampa", self._stampa_scheda_record))
        # Escape -> torna indietro
        if self.table_def.is_composite:
            self.root.bind("<Escape>", lambda e: self._schermata_selezione(nome_tabella))
        else:
            self.root.bind("<Escape>", lambda e: self._schermata_menu())

        self._label_status = tk.Label(self._vista, text="",  bg=c["sfondo"], fg=c["testo_dim"],
                                       font=self._f_status, anchor="w")
        self._label_status.pack(fill="x", padx=_S(10), pady=(0, _S(2)))

        # Help tastiera form
        hint_text = "^S=Salva ^N=Nuovo ^F=Cerca ^B=Bottoni ^H=Campi Ctrl+\u2191\u2193=Sezione Esc=Indietro"
        if self.table_def.is_composite and self.table_def.puo("nuovo"):
            hint_text += " ^D=Copia"
        hint = tk.Label(self._vista, text=hint_text,
                         bg=c["sfondo"], fg=c["puntini"], font=self._f_small, anchor="e")
        hint.pack(fill="x", padx=_S(10), pady=(0, _S(2)))
        self._rimuovi_coperta()

    # =========================================================================
    #  VISUALIZZAZIONE
    # =========================================================================
    def _mostra_record(self):
        # Monoutente: forza sempre record 1 nella tabella utenti
        if (self._nome_tabella == "utenti" and
            self._is_monoutente()):
            self._pos_visibile = 0
        tot_vis = len(self._indici_visibili)
        if tot_vis == 0 or self._pos_visibile < 0:
            self._pulisci_campi(); self._aggiorna_contatore(0, 0)
            self._status("Nessun record - premi NUOVO", "stato_avviso"); return

        self.indice_corrente = self._indici_visibili[self._pos_visibile]
        rec = self.db.leggi(self.indice_corrente)
        if not rec: return

        campo_k = self.table_def.get_campo_chiave()
        if campo_k and self._label_auto_id:
            val_id = rec.get(campo_k["nome"], "?")
            # Mostra anche chi e il proprietario se admin
            pil_id = rec.get("_utente_id", "")
            if pil_id and is_admin(self.sessione):
                pil = get_utente(pil_id)
                ut_name = get_display_name(pil) if pil else "?"
                self._label_auto_id.config(text="ID: %s | Utente: %s" % (val_id, ut_name))
            else:
                self._label_auto_id.config(text="ID: %s" % val_id)

        for rif in self.table_def.riferimenti:
            alias = rif.get("alias", rif["tabella"])
            campo_rec = rif.get("campo_record", rif["campo_chiave"])
            val = rec.get(campo_rec, "")
            # Se riferimenti fissi (wizard), non serve aggiornare listbox
            if hasattr(self, '_ref_fixed') and self._ref_fixed:
                continue
            sel = self.ref_selectors.get(alias)
            if sel and sel["db"]:
                lb = sel["listbox"]; lb.selection_clear(0, "end")
                ref_db = sel["db"]; ref_k = ref_db.table_def.get_campo_chiave()
                indici = sel.get("indici", list(range(ref_db.conteggio())))
                if ref_k:
                    for lb_pos, real_idx in enumerate(indici):
                        r = ref_db.leggi(real_idx)
                        if r and str(r.get(ref_k["nome"], "")) == str(val):
                            lb.selection_set(lb_pos); lb.see(lb_pos); break

        for campo in self.table_def.campi:
            n = campo["nome"]
            if campo.get("chiave"): continue
            if n in self.fields:
                self.fields[n].clear()
                val = rec.get(n, "")
                # Decripta password per visualizzazione admin
                if val and n == "Password" and self._nome_tabella == "utenti":
                    val = decripta_password(str(val))
                if val: self.fields[n].set(str(val))

        self._aggiorna_contatore(self._pos_visibile + 1, tot_vis)
        if self.modo_ricerca:
            self._status("Risultato %d/%d" % (self.indice_ricerca+1, len(self.risultati_ricerca)), "cerca_testo")
        else:
            self._status("Pronto.", "testo_dim")

        # Tabella utenti: nessun blocco readonly, il limite agisce solo sulla creazione

        # Focus automatico sul primo campo
        primi = list(self.fields.values())
        if primi:
            primi[0].set_focus()
            # Monoutente: rinforza focus con delay (evita che bottoni rubino il focus)
            if (self._nome_tabella == "utenti" and
                self._is_monoutente()):
                self.root.after(200, primi[0].set_focus)

        # Auto-fetch meteo se campi vuoti e pista nota
        if _HAS_METEO and self.table_def.puo("crono"):
            self.root.after(500, self._auto_meteo)

    def _pulisci_campi(self):
        for campo in self.fields.values(): campo.clear()
        for sel in self.ref_selectors.values():
            try: sel["listbox"].selection_clear(0, "end")
            except: pass
        if self._label_auto_id: self._label_auto_id.config(text="[AUTO]")

    def _leggi_campi(self):
        dati = {}
        # Riferimenti fissi (da wizard) — chiavi gia' campo_record
        if hasattr(self, '_ref_fixed') and self._ref_fixed:
            for campo_rec, valore in self._ref_fixed.items():
                dati[campo_rec] = valore
        # Riferimenti da listbox (tabelle non-composite)
        for alias, sel in self.ref_selectors.items():
            lb = sel["listbox"]; ref_db = sel["db"]
            campo_rec = sel.get("campo_record", sel["campo_chiave"])
            selection = lb.curselection()
            if selection and ref_db:
                # Mappa indice listbox -> indice reale nel db
                indici = sel.get("indici", list(range(ref_db.conteggio())))
                real_idx = indici[selection[0]] if selection[0] < len(indici) else selection[0]
                rec = ref_db.leggi(real_idx)
                ref_k = ref_db.table_def.get_campo_chiave()
                if rec and ref_k: dati[campo_rec] = str(rec.get(ref_k["nome"], ""))
        for campo in self.table_def.campi:
            if campo.get("chiave"): continue
            if campo["nome"] in self.fields:
                val = self.fields[campo["nome"]].get()
                # Cripta password prima di salvare nel JSON
                if campo["nome"] == "Password" and self._nome_tabella == "utenti" and val.strip():
                    val = cripta_password(val)
                dati[campo["nome"]] = val
        return dati

    def _aggiorna_contatore(self, curr, tot):
        self._label_contatore.config(text="Rec %d/%d" % (curr, tot) if tot else "Nessun record")

    def _status(self, msg, color_key="testo_dim"):
        c = carica_colori()
        self._label_status.config(text=" %s" % msg, fg=c.get(color_key, c["testo_dim"]))

    def _flash_btn(self, btn, cmd):
        """Ritorna un comando wrappato con flash rosso 0.8s."""
        def _wrapper():
            try:
                orig_bg = btn.cget("bg")
                orig_fg = btn.cget("fg")
                btn.config(bg="#ff0000", fg="#ffffff")
                # Esegui comando dopo 150ms cosi' il flash si vede
                self.root.after(150, lambda: self._flash_exec(btn, orig_bg, orig_fg, cmd))
            except Exception:
                cmd()
        return _wrapper

    def _flash_exec(self, btn, orig_bg, orig_fg, cmd):
        """Ripristina colore e esegui comando."""
        try:
            btn.config(bg=orig_bg, fg=orig_fg)
        except Exception:
            pass
        cmd()

    def _flash_key(self, op, cmd):
        """Flash bottone per nome operazione + esegui comando (da scorciatoia tastiera)."""
        btn = getattr(self, '_btn_map', {}).get(op)
        if btn:
            try:
                orig_bg = btn.cget("bg")
                orig_fg = btn.cget("fg")
                btn.config(bg="#ff0000", fg="#ffffff")
                self.root.after(150, lambda: self._flash_exec(btn, orig_bg, orig_fg, cmd))
            except Exception:
                cmd()
        else:
            cmd()

    def _salta_sezione(self, direzione):
        """Salta alla sezione precedente (-1) o successiva (+1).
        PgUp/PgDown per navigazione rapida tra sezioni nel form."""
        sezioni = getattr(self, '_sezione_primi_campi', [])
        if not sezioni or not self.fields:
            return
        # Trova quale sezione ha il focus corrente
        focus_w = self.root.focus_get()
        campo_corrente = None
        nomi_campi = list(self.fields.keys())
        for nome, rf in self.fields.items():
            if hasattr(rf, '_canvas') and rf._canvas == focus_w:
                campo_corrente = nome
                break
        # Se non ha focus su un campo, vai alla prima/ultima sezione
        if not campo_corrente:
            target = sezioni[0] if direzione > 0 else sezioni[-1]
        else:
            # Trova sezione corrente
            idx_campo = nomi_campi.index(campo_corrente) if campo_corrente in nomi_campi else 0
            sez_corrente = 0
            for si, sez_nome in enumerate(sezioni):
                sez_idx = nomi_campi.index(sez_nome) if sez_nome in nomi_campi else 0
                if sez_idx <= idx_campo:
                    sez_corrente = si
            # Salta alla prossima/precedente
            nuovo_sez = sez_corrente + direzione
            nuovo_sez = max(0, min(nuovo_sez, len(sezioni) - 1))
            target = sezioni[nuovo_sez]
        # Focus sul primo campo della sezione target
        if target in self.fields:
            self.fields[target].set_focus()

    # =========================================================================
    #  NAVIGAZIONE (filtrata)
    # =========================================================================
    def _aggiorna_visibili(self):
        self._indici_visibili = self.db.get_records_filtrati(self.filtro_utente())

    def _vai_primo(self):
        self._aggiorna_visibili()
        if self._indici_visibili: self._pos_visibile = 0
        self.modo_nuovo = False; self.modo_ricerca = False; self._mostra_record()

    def _vai_precedente(self):
        if self._pos_visibile > 0: self._pos_visibile -= 1
        self.modo_nuovo = False; self._mostra_record()

    def _vai_successivo(self):
        if self._pos_visibile < len(self._indici_visibili) - 1: self._pos_visibile += 1
        self.modo_nuovo = False; self._mostra_record()

    def _vai_ultimo(self):
        self._aggiorna_visibili()
        if self._indici_visibili: self._pos_visibile = len(self._indici_visibili) - 1
        self.modo_nuovo = False; self.modo_ricerca = False; self._mostra_record()

    # =========================================================================
    #  CRUD
    # =========================================================================
    def _nuovo(self):
        if not self.table_def.puo("nuovo"):
            self._status("Inserimento non abilitato!", "stato_errore"); return
        # Limita utenti in base alla licenza (conta solo record validi)
        if self._nome_tabella == "utenti" and self._max_utenti >= 1:
            n_validi = len(self._utenti_validi())
            if n_validi >= self._max_utenti:
                self._status("Utente gia' registrato", "stato_avviso"); return
        self._pulisci_campi(); self.modo_nuovo = True; self.modo_ricerca = False
        self._aggiorna_contatore(0, len(self._indici_visibili))
        campo_k = self.table_def.get_campo_chiave()
        if campo_k and self._label_auto_id:
            self._label_auto_id.config(text="Nuovo ID: %s" % self.db.prossimo_id())
        for rif in self.table_def.riferimenti:
            alias = rif.get("alias", rif["tabella"])
            ref_db = self.ref_dbs.get(alias)
            ref_td = self.ref_defs.get(alias)
            ref_filtro = None if (ref_td and ref_td.condiviso) else self.filtro_utente()
            if not ref_db or ref_db.conteggio(ref_filtro) == 0:
                self._status("Prima inserisci dati in %s!" % alias.upper(), "stato_errore"); return
        self._status("Seleziona riferimenti e compila, poi SALVA", "stato_ok")
        primi = list(self.fields.values())
        if primi: primi[0].set_focus()
        # Auto-fetch meteo per nuovo record
        if _HAS_METEO and self.table_def.puo("crono"):
            self.root.after(500, self._auto_meteo)

    def _salva(self):
        if self.modo_nuovo and not self.table_def.puo("nuovo"):
            self._status("Inserimento non abilitato!", "stato_errore"); return
        if not self.modo_nuovo and not self.table_def.puo("salva"):
            self._status("Modifica non abilitata!", "stato_errore"); return
        # Il limite utenti agisce solo sulla creazione (in _nuovo), non sul salvataggio
        dati = self._leggi_campi()
        for rif in self.table_def.riferimenti:
            campo_rec = rif.get("campo_record", rif["campo_chiave"])
            alias = rif.get("alias", rif["tabella"])
            if not dati.get(campo_rec):
                self._status("Seleziona %s!" % alias.upper(), "stato_errore"); return
        campi_propri = {k: v for k, v in dati.items()
                        if k not in [r.get("campo_record", r["campo_chiave"]) for r in self.table_def.riferimenti]}
        ha_rif = any(dati.get(r.get("campo_record", r["campo_chiave"])) for r in self.table_def.riferimenti)
        ha_dati = any(v.strip() for v in campi_propri.values() if isinstance(v, str))
        if not ha_rif and not ha_dati:
            self._status("Compila almeno un campo!", "stato_errore"); return
        for nome, rf in self.fields.items():
            val = dati.get(nome, "")
            # Considera vuoti anche i campi con solo separatori (date: "  /  /    ", ore: "  :  ")
            val_pulito = val.replace("/","").replace(":","").replace(".","").replace("-","").strip()
            if val_pulito:
                ok, msg = rf.validate()
                if not ok: self._status("%s: %s" % (nome.replace("_"," "), msg), "stato_errore"); rf.set_focus(); return

        if self.modo_nuovo:
            utente_id = None
            if not self.table_def.condiviso:
                utente_id = self.sessione.get("codice") if self.sessione else None
            idx = self.db.inserisci(dati, utente_id=utente_id)
            self.modo_nuovo = False; self._aggiorna_visibili()
            # Posizionati sul nuovo record
            if idx in self._indici_visibili:
                self._pos_visibile = self._indici_visibili.index(idx)
            self._mostra_record()
            self._status("Inserito! (#%d)" % (idx+1), "stato_ok")
        elif self.indice_corrente >= 0:
            self.db.aggiorna(self.indice_corrente, dati)
            self._mostra_record(); self._status("Aggiornato!", "stato_ok")
        else:
            utente_id = None
            if not self.table_def.condiviso:
                utente_id = self.sessione.get("codice") if self.sessione else None
            idx = self.db.inserisci(dati, utente_id=utente_id)
            self._aggiorna_visibili()
            if idx in self._indici_visibili:
                self._pos_visibile = self._indici_visibili.index(idx)
            self._mostra_record(); self._status("Inserito!", "stato_ok")

    def _cerca(self):
        if not self.table_def.puo("cerca"):
            self._status("Ricerca non abilitata!", "stato_errore"); return
        # Apri ELENCO con barra di ricerca
        self._schermata_elenco(self._nome_tabella, cerca=True)

    def _cancella(self):
        if not self.table_def.puo("cancella"):
            self._status("Cancellazione non abilitata!", "stato_errore"); return
        # Limita utenti
        if (self._nome_tabella == "utenti" and
            self._is_monoutente()):
            self._status("Impossibile eliminare l'utente", "stato_avviso"); return
        if self.indice_corrente < 0 or not self._indici_visibili:
            self._status("Nessun record.", "stato_avviso"); return
        if self.modo_nuovo:
            self._pulisci_campi(); self.modo_nuovo = False; self._mostra_record(); return
        # Doppia pressione per confermare
        import time
        now = time.time()
        if not hasattr(self, '_canc_time') or now - self._canc_time > 3:
            self._canc_time = now
            self._status("Premi CANCELLA di nuovo per confermare!", "stato_errore"); return
        del self._canc_time
        self.db.cancella(self.indice_corrente)
        self.modo_ricerca = False; self.risultati_ricerca = []; self._aggiorna_visibili()
        if not self._indici_visibili: self._pos_visibile = -1
        elif self._pos_visibile >= len(self._indici_visibili):
            self._pos_visibile = len(self._indici_visibili) - 1
        self._mostra_record(); self._status("Cancellato.", "stato_ok")

    def _pulisci(self):
        try: self.root.unbind_all("<MouseWheel>")
        except: pass
        for key in ("<Return>", "<Escape>", "<Control-b>", "<Control-s>",
                    "<Control-n>", "<Control-f>", "<Control-e>",
                    "<Control-p>", "<Control-o>", "<Control-d>",
                    "<Control-t>", "<Control-r>", "<Control-h>", "<Control-g>",
                    "<Prior>", "<Next>", "<Control-Up>", "<Control-Down>",
                    "<Button-1>", "<Key>"):
            try: self.root.unbind(key)
            except: pass
        # Distrugge contenuto e ricrea frame schermata sopra _base
        c = carica_colori()
        self._base.configure(bg=c["sfondo"])
        if self._vista and self._vista.winfo_exists():
            self._vista.destroy()
        self._vista = tk.Frame(self._base, bg=c["sfondo"])
        self._vista.place(x=0, y=0, relwidth=1, relheight=1)
        self._kb_original_colors = {}

    def _rimuovi_coperta(self):
        """Compatibilita': non serve piu' con il frame fisso."""
        pass


# =============================================================================
#  AVVIO
# =============================================================================
def main():
    conf = carica_conf()
    percorsi = get_percorsi(conf)
    for p in percorsi.values(): os.makedirs(p, exist_ok=True)
    def_dir = percorsi["definizioni"]
    if not [f for f in os.listdir(def_dir) if f.endswith(".def")]:
        # Nessuna tabella definita: genera esempi generici dimostrativi
        # L'utente li sostituira' con i propri file .def specifici
        esempi = {
            "categorie": "# Tabella esempio - Categorie\n# Ogni utente gestisce le proprie\n!accesso;tutti\n#\nCodice_Cat;4;N;K\nNome_Categoria;25;S\nDescrizione;30;S\n",
            "articoli": "# Tabella esempio - Articoli\n# Ogni utente gestisce i propri\n!accesso;tutti\n#\nCodice_Art;4;N;K\nNome_Articolo;25;S\nMarca;20;S\nModello;20;S\nNote;30;S\n",
            "schede": "# Tabella esempio composita - Schede\n# Tutti gli utenti gestiscono le proprie schede\n!accesso;tutti\n#\n@categorie;Codice_Cat\n@articoli;Codice_Art\n#\nValore_1;10;S\nValore_2;10;S\nData;10;D\nNote;30;S\n",
        }
        for nome, contenuto in esempi.items():
            with open(os.path.join(def_dir, "%s.def" % nome), "w", encoding="utf-8") as f:
                f.write(contenuto)
    root = tk.Tk()
    root.withdraw()  # Nascondi finestra durante init per evitare flash bianco
    c = carica_colori()
    root.configure(bg=c["sfondo"])
    root.update_idletasks()
    app = RetroDBApp(root)
    root.deiconify()  # Mostra finestra solo quando tutto e' pronto
    root.mainloop()

if __name__ == "__main__":
    main()
