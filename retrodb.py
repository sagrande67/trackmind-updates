#!/usr/bin/env python3
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

from version import __version__
APP_VERSION = __version__

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
from config_colori import carica_colori, salva_colori, DEFAULT_COLORS, NIGHT_COLORS, FONT_MONO
from conf_manager import (carica_conf, salva_conf, verifica_licenza, get_percorsi,
                          verifica_attivazione, attiva_licenza, get_codice_macchina,
                          ha_opzione_laptimer, genera_token_laptimer,
                          crediti_ia_rimasti, applica_ricarica_ia)
from auth import (carica_utenti, verifica_login,
                  get_utente, is_admin, get_display_name,
                  username_esiste, cripta_password, decripta_password,
                  _verifica_accesso_speciale)
# Guardia anti-popup di sistema (uConsole: NetworkManager, keyring, BT, polkit).
# Se il modulo manca (versioni vecchie) tolleriamo l'assenza senza crashare.
try:
    from core.focus_guard import proteggi_finestra_sicura as _proteggi_finestra
except Exception:
    def _proteggi_finestra(root, **kwargs):
        return

# Pannello strumenti micro-SD (3 barre LED: capienza, usura, I/O live)
try:
    from core.sd_bar import BarraSD as _BarraSD, BarraUsura as _BarraUsura, \
                            BarraIO as _BarraIO, BarraBatteria as _BarraBatteria
    _HAS_SD_BAR = True
except Exception:
    _BarraSD = None
    _BarraUsura = None
    _BarraIO = None
    _BarraBatteria = None
    _HAS_SD_BAR = False

# Lettura stato batteria centralizzata in core/batteria.py (stessa logica
# usata dagli addons via aggiungi_barra_batteria()). Fallback no-op se il
# modulo manca.
try:
    from core.batteria import get_batteria_info as _get_batteria_info
except Exception:
    def _get_batteria_info():
        return None, None

# Auto-riconnessione Wi-Fi (quando l'hotspot del telefono va giu' e torna,
# Windows non si ricollega sempre da solo). Modulo opzionale: se manca,
# TrackMind resta comunque funzionante senza riconnessione automatica.
try:
    from core.wifi_monitor import AutoRiconnettore as _WifiAutoRiconnettore
    _HAS_WIFI_AUTO = True
except Exception:
    _WifiAutoRiconnettore = None
    _HAS_WIFI_AUTO = False
# splash.py non piu' usato: la splash e' integrata in _schermata_splash()

# Stampa termica (opzionale)
try:
    from thermal_print import (genera_scheda_completa,
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

# Gestione bottoni centralizzata
try:
    from ui_bottoni import (setup_bottoni as _ui_setup_bottoni,
                             setup_griglia as _ui_setup_griglia,
                             flash_btn as _ui_flash_btn,
                             flash_key as _ui_flash_key,
                             focus_evidenzia as _ui_focus_evidenzia,
                             init_focus_globale as _ui_init_focus,
                             pulisci_cache, sospendi_focus)
    _HAS_UI_BTN = True
except ImportError:
    _HAS_UI_BTN = False

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

# Updater (aggiornamento software).
# NB: le funzioni di BUILD (prepara_aggiornamento, prepara_aggiornamento_github,
# get_app_files_full, _trova_unita_usb) non servono piu' qui dentro perche'
# il tool di pubblicazione e' stato spostato in dev/pubblica.py.
# Qui importiamo solo le funzioni di CLIENT (applica un aggiornamento
# scaricato): cerca_aggiornamento_usb, verifica_aggiornamento,
# applica_aggiornamento, controlla_aggiornamento_github, ecc.
try:
    from updater import (cerca_aggiornamento_usb,
                         verifica_aggiornamento, applica_aggiornamento,
                         riavvia_app, get_app_files,
                         controlla_aggiornamento_github,
                         scarica_aggiornamento_github)
    _HAS_UPDATER = True
except ImportError:
    _HAS_UPDATER = False

# Web Sync (aggiornamento cataloghi da web)
try:
    from web_sync import sync_tabella_background, ha_cambiamenti
    _HAS_WEBSYNC = True
except ImportError:
    _HAS_WEBSYNC = False

# Prompt Editor (editor system prompt IA)
try:
    from prompt_editor import _get_prompt_path, PROMPT_DEFAULT
    _HAS_PROMPT_EDITOR = True
except ImportError:
    _HAS_PROMPT_EDITOR = False

# Assistente Gara (monitor evento MyRCM live + countdown turni)
try:
    from assistente_gara import (AssistenteGara,
                                  AssistenteGaraMonitor,
                                  mostra_popup_alert)
    _HAS_ASSISTENTE = True
except ImportError:
    _HAS_ASSISTENTE = False

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
        self.nascosto = False   # Se True, non compare nel menu (!nascosto;vero)
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
                    elif chiave == "nascosto" and len(parti) >= 2:
                        val = parti[1].strip().lower()
                        self.nascosto = val in ("vero", "true", "si", "1")
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
                    flags_raw = parti[3].strip().upper() if len(parti) >= 4 else ""
                    self.campi.append({
                        "nome": nome_campo, "lunghezza": int(parti[1].strip()),
                        "tipo": parti[2].strip().upper(),
                        "chiave": "K" in flags_raw,
                        "analisi_ia": "A" in flags_raw,
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
        # Scala 0 = auto-detect.
        # Su Linux/uConsole si basa sulla risoluzione dello SCHERMO (di solito
        # l'app gira fullscreen, quindi schermo e finestra coincidono).
        # Su Windows in modalita' finestra si basa invece sulla larghezza
        # configurata (larghezza_max): se metti 1280x720 nel conf come su
        # uConsole, ottieni scala 1.5 e l'aspetto e' identico.
        if scala == 0:
            try:
                if sys.platform == "win32":
                    riferimento = int(self.conf.get("larghezza_max", 900))
                else:
                    riferimento = root.winfo_screenwidth()
                if riferimento <= 1280:
                    scala = 1.5  # uConsole 1280x720 o finestra equivalente
                elif riferimento <= 1920:
                    scala = 1.0  # Full HD
                else:
                    scala = 0.8  # 4K
            except: scala = 1.0
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

        # Focus visivo globale: bordo highlight su TUTTI i bottoni quando ricevono focus via Tab
        if _HAS_UI_BTN:
            _ui_init_focus(self.root, c)
        else:
            self.root.option_add("*Button.highlightThickness", 2)
            self.root.option_add("*Button.highlightColor", c["dati"])
            self.root.option_add("*Button.highlightBackground", c["sfondo"])

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

        # Sanamento connessioni Wi-Fi: promuove da agent-owned (keyring)
        # a system-owned, cosi' NetworkManager non fa piu' comparire il
        # popup di sblocco portachiavi sopra TrackMind. Solo Linux.
        if sys.platform != "win32":
            try:
                self._wifi_sanifica_esistenti()
            except Exception as _e:
                print("[WIFI] sanamento fallito: %s" % _e)

        # Monitor WiFi ogni 30 secondi
        self._wifi_monitor()

        # Auto-riconnessione Wi-Fi: thread in background che, se configurato
        # da CONFI, tenta di riagganciare il profilo Wi-Fi preferito quando
        # la rete cade (tipicamente hotspot del telefono su Windows).
        self._wifi_auto = None
        self._wifi_auto_start()

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

        self._f_title  = tkfont.Font(family=FONT_MONO, size=_S(11), weight="bold")
        self._f_label  = tkfont.Font(family=FONT_MONO, size=_S(9))
        self._f_btn    = tkfont.Font(family=FONT_MONO, size=_S(8), weight="bold")
        self._f_small  = tkfont.Font(family=FONT_MONO, size=_S(8))
        self._f_status = tkfont.Font(family=FONT_MONO, size=_S(8))
        self._f_nav    = tkfont.Font(family=FONT_MONO, size=_S(10), weight="bold")
        self._f_list   = tkfont.Font(family=FONT_MONO, size=_S(8))
        self._f_login  = tkfont.Font(family=FONT_MONO, size=_S(14), weight="bold")
        # Font compatti per la barra bottoni delle tabelle (uConsole scala 1.5)
        # Testo su una sola riga + dimensioni ridotte per farci stare tutti i pulsanti
        self._f_btn_tab = tkfont.Font(family=FONT_MONO, size=_S(7), weight="bold")
        self._f_nav_tab = tkfont.Font(family=FONT_MONO, size=_S(8), weight="bold")

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

        # Focus visivo globale: gestito da init_focus_globale() in ui_bottoni.py
        # (bind_class su tutti i Button per inversione colori al focus)

        # Binding globali rimossi (CONF accessibile solo da login)
        # Ctrl+Q = ESCI da qualsiasi schermata (case-insensitive: funziona anche con CapsLock/Shift)
        self.root.bind("<Control-q>", lambda e: self.root.destroy())
        self.root.bind("<Control-Q>", lambda e: self.root.destroy())

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

    def _kb_focus_evidenzia(self, widget, on=True):
        """Evidenzia focus bottone (delega a core/ui_bottoni)."""
        if _HAS_UI_BTN:
            _ui_focus_evidenzia(widget, on)
            return
        # Fallback inline (se ui_bottoni non disponibile)
        try:
            if str(widget.cget("state")) == "disabled":
                return
        except (tk.TclError, AttributeError):
            return

    def _kb_setup_bottoni(self, bottoni, orizzontale=True):
        """Configura navigazione frecce + Enter + focus visivo su lista bottoni.
        Delega a core/ui_bottoni per logica centralizzata."""
        if _HAS_UI_BTN:
            _ui_setup_bottoni(bottoni, orizzontale)
            return
        # Fallback minimo
        attivi = [b for b in bottoni if str(b["state"]) != "disabled"]
        if not attivi: return
        for btn in attivi:
            btn.bind("<Return>", self._kb_enter_invoca)
        tasto_avanti = "<Right>" if orizzontale else "<Down>"
        tasto_indietro = "<Left>" if orizzontale else "<Up>"
        for i, btn in enumerate(attivi):
            if i < len(attivi) - 1:
                btn.bind(tasto_avanti, lambda e, b=attivi[i+1]: (b.focus_set(), "break")[-1])
            if i > 0:
                btn.bind(tasto_indietro, lambda e, b=attivi[i-1]: (b.focus_set(), "break")[-1])

    def _kb_setup_griglia(self, bottoni, colonne):
        """Configura navigazione frecce 4 direzioni su griglia di bottoni.
        Delega a core/ui_bottoni per logica centralizzata."""
        if _HAS_UI_BTN:
            _ui_setup_griglia(bottoni, colonne)
            # Focus iniziale sul primo attivo
            attivi = [b for b in bottoni if str(b["state"]) != "disabled"]
            if attivi:
                attivi[0].focus_set()
            return
        # Fallback (se ui_bottoni non disponibile)
        attivi = [(i, b) for i, b in enumerate(bottoni) if str(b["state"]) != "disabled"]
        if not attivi: return
        for _, btn in attivi:
            btn.bind("<Return>", self._kb_enter_invoca)
        attivi[0][1].focus_set()

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
        F_TINY  = (FONT_MONO, _S(7))
        F_SMALL = (FONT_MONO, _S(9))
        F_MED   = (FONT_MONO, _S(11))
        F_BIG   = (FONT_MONO, _S(14), "bold")
        F_TITLE = (FONT_MONO, _S(18), "bold")

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

        # Logo personalizzabile del commercializzatore (vedi _mostra_logo_login):
        # e' un file PNG/GIF in dati/ e viene mostrato in mezzo alla finestra,
        # sotto i campi utente/password. dati/ non viene toccata dagli
        # aggiornamenti => il logo persiste.
        self._login_logo_img = None  # tkinter richiede ref viva all'immagine

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

        # Logo del rivenditore: mostrato qui, in mezzo alla finestra,
        # sotto i campi di login/password e sopra gli stati di sistema.
        self._mostra_logo_login(self._vista, c)

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
        # Salvata in self._lbl_wifi_login cosi' _wifi_monitor puo'
        # aggiornarla in tempo reale quando la rete cade/torna.
        self._lbl_wifi_login = tk.Label(self._vista, text=wifi_txt, bg=c["sfondo"],
                                         fg=wifi_fg, font=self._f_small)
        self._lbl_wifi_login.pack(pady=(_S(4), 0))

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

        # Aggiornamenti: solo manuali dal menu principale (bottone AGGIORNA).
        # Il check automatico al login e' stato rimosso per evitare aggiornamenti
        # indesiderati all'avvio (es. su uConsole in autostart).

        # Monoutente: auto-compila username e focus su password
        # Focus autostart (uConsole): una sola passata a 600ms, semplice e
        # non invasiva. Niente <Visibility> (troppo aggressivo), niente
        # retry multipli che continuano dopo il login.
        if self._is_monoutente():
            utenti = carica_utenti()
            if utenti:
                nome = utenti[0].get("Username", "").strip()
                if nome:
                    self._login_user.set(nome)
                    target = self._login_pwd
                else:
                    target = self._login_user
            else:
                target = self._login_user
        else:
            target = self._login_user

        # Un solo tentativo di focus dopo un delay ragionevole.
        # Il flag _focus_login_attivo permette di abortire se l'utente
        # e' gia' uscito dalla schermata di login prima del tentativo.
        self._focus_login_attivo = True

        def _log_focus(msg):
            """Log diagnostico del focus autostart in /tmp/tm_focus.log."""
            try:
                with open("/tmp/tm_focus.log", "a") as _f:
                    _f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
            except Exception:
                pass

        def _click_ctypes(cx, cy):
            """Simula click sinistro via X11 XTest. Torna True se ok."""
            import ctypes, sys, os
            if not sys.platform.startswith("linux"):
                return False
            # Prova piu' nomi di libreria
            x11 = xtst = None
            for _n in ("libX11.so.6", "libX11.so"):
                try:
                    x11 = ctypes.CDLL(_n); break
                except OSError:
                    continue
            if x11 is None:
                _log_focus("libX11 non trovato"); return False
            for _n in ("libXtst.so.6", "libXtst.so"):
                try:
                    xtst = ctypes.CDLL(_n); break
                except OSError:
                    continue
            if xtst is None:
                _log_focus("libXtst non trovato"); return False
            # Firme esplicite (critico su ARM 64-bit)
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XFlush.argtypes = [ctypes.c_void_p]
            x11.XFlush.restype = ctypes.c_int
            x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
            x11.XSync.restype = ctypes.c_int
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XCloseDisplay.restype = ctypes.c_int
            xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                  ctypes.c_int, ctypes.c_int,
                                                  ctypes.c_ulong]
            xtst.XTestFakeMotionEvent.restype = ctypes.c_int
            xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                  ctypes.c_int, ctypes.c_ulong]
            xtst.XTestFakeButtonEvent.restype = ctypes.c_int
            dpy = x11.XOpenDisplay(None)
            if not dpy:
                _log_focus("XOpenDisplay fallito, DISPLAY=%s"
                           % os.environ.get("DISPLAY", "unset"))
                return False
            try:
                xtst.XTestFakeMotionEvent(dpy, -1, int(cx), int(cy), 0)
                x11.XSync(dpy, 0)
                xtst.XTestFakeButtonEvent(dpy, 1, 1, 0)
                x11.XSync(dpy, 0)
                xtst.XTestFakeButtonEvent(dpy, 1, 0, 10)
                x11.XFlush(dpy)
            finally:
                x11.XCloseDisplay(dpy)
            _log_focus("click ctypes ok a %d,%d" % (cx, cy))
            return True

        def _click_xdotool(cx, cy):
            """Fallback: usa xdotool via subprocess se installato."""
            try:
                import subprocess, shutil
                if not shutil.which("xdotool"):
                    return False
                subprocess.run(["xdotool", "mousemove", str(cx), str(cy),
                                "click", "1"],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=3)
                _log_focus("click xdotool ok a %d,%d" % (cx, cy))
                return True
            except Exception as _e:
                _log_focus("xdotool errore: %s" % _e)
                return False

        def _x11_setinputfocus(xid):
            """Forza X11 keyboard focus sulla finestra data via XSetInputFocus.
            Scavalca completamente il window manager."""
            import ctypes
            try:
                x11 = ctypes.CDLL("libX11.so.6")
            except OSError:
                _log_focus("SetInputFocus: libX11 non trovato")
                return False
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            x11.XRaiseWindow.restype = ctypes.c_int
            x11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                            ctypes.c_int, ctypes.c_ulong]
            x11.XSetInputFocus.restype = ctypes.c_int
            x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
            x11.XSync.restype = ctypes.c_int
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XCloseDisplay.restype = ctypes.c_int
            dpy = x11.XOpenDisplay(None)
            if not dpy:
                _log_focus("SetInputFocus: XOpenDisplay fallito")
                return False
            try:
                x11.XRaiseWindow(dpy, ctypes.c_ulong(xid))
                # RevertTo = 2 (RevertToParent), CurrentTime = 0
                x11.XSetInputFocus(dpy, ctypes.c_ulong(xid), 2, 0)
                x11.XSync(dpy, 0)
            finally:
                x11.XCloseDisplay(dpy)
            _log_focus("XSetInputFocus ok su xid=%s" % hex(xid))
            return True

        def _x11_activate_window(xid):
            """Manda _NET_ACTIVE_WINDOW ClientMessage al root window.
            E' il metodo EWMH ufficiale per attivare una finestra:
            tutti i WM compliant (incluso Openbox) lo rispettano."""
            import ctypes
            try:
                x11 = ctypes.CDLL("libX11.so.6")
            except OSError:
                return False

            # Struct XClientMessageEvent (64 bit compatibile)
            # typedef struct {
            #   int type; unsigned long serial; Bool send_event;
            #   Display *display; Window window; Atom message_type;
            #   int format; union { long l[5]; ... } data;
            # }
            class XClientMessageData(ctypes.Union):
                _fields_ = [("b", ctypes.c_char * 20),
                            ("s", ctypes.c_short * 10),
                            ("l", ctypes.c_long * 5)]

            class XClientMessageEvent(ctypes.Structure):
                _fields_ = [("type", ctypes.c_int),
                            ("serial", ctypes.c_ulong),
                            ("send_event", ctypes.c_int),
                            ("display", ctypes.c_void_p),
                            ("window", ctypes.c_ulong),
                            ("message_type", ctypes.c_ulong),
                            ("format", ctypes.c_int),
                            ("data", XClientMessageData)]

            # XEvent e' un union grande, usiamo una struct piena di padding
            class XEvent(ctypes.Union):
                _fields_ = [("type", ctypes.c_int),
                            ("xclient", XClientMessageEvent),
                            ("pad", ctypes.c_long * 24)]

            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            x11.XInternAtom.restype = ctypes.c_ulong
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                        ctypes.c_int, ctypes.c_long,
                                        ctypes.POINTER(XEvent)]
            x11.XSendEvent.restype = ctypes.c_int
            x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
            x11.XSync.restype = ctypes.c_int
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XCloseDisplay.restype = ctypes.c_int

            dpy = x11.XOpenDisplay(None)
            if not dpy:
                _log_focus("ActiveWindow: XOpenDisplay fallito")
                return False
            try:
                atom = x11.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", 0)
                root = x11.XDefaultRootWindow(dpy)
                ev = XEvent()
                ctypes.memset(ctypes.byref(ev), 0, ctypes.sizeof(ev))
                ev.xclient.type = 33  # ClientMessage
                ev.xclient.window = xid
                ev.xclient.message_type = atom
                ev.xclient.format = 32
                ev.xclient.data.l[0] = 2  # source: pager (forza l'attivazione)
                ev.xclient.data.l[1] = 0  # CurrentTime
                ev.xclient.data.l[2] = 0
                # SubstructureRedirectMask (1<<20) | SubstructureNotifyMask (1<<19)
                mask = (1 << 20) | (1 << 19)
                x11.XSendEvent(dpy, root, 0, mask, ctypes.byref(ev))
                x11.XSync(dpy, 0)
            finally:
                x11.XCloseDisplay(dpy)
            _log_focus("_NET_ACTIVE_WINDOW inviato per xid=%s" % hex(xid))
            return True

        def _get_toplevel_xid():
            """Torna l'XID della finestra toplevel gestita dal WM (frame
            Openbox), non quella interna Tk. Fallback su winfo_id() se
            wm_frame non e' disponibile o ritorna 0."""
            try:
                fr = self.root.wm_frame()
                # wm_frame torna una stringa tipo "0x1234567"
                xid = int(fr, 16) if isinstance(fr, str) else int(fr)
                if xid:
                    return xid
            except Exception:
                pass
            return self.root.winfo_id()

        def _forza_focus_once():
            if not getattr(self, '_focus_login_attivo', False):
                return

            # WM_CLASS per eventuali regole Openbox
            try:
                self.root.wm_class("TrackMind", "TrackMind")
            except Exception:
                pass

            # Log diagnostico posizione finestra + XID
            try:
                self.root.update_idletasks()
                xid_int = self.root.winfo_id()
                xid_top = _get_toplevel_xid()
                _log_focus("Finestra: pos=(%d,%d) size=(%d,%d) "
                           "xid_int=%s xid_top=%s" % (
                    self.root.winfo_rootx(), self.root.winfo_rooty(),
                    self.root.winfo_width(), self.root.winfo_height(),
                    hex(xid_int), hex(xid_top)))
            except Exception:
                xid_top = self.root.winfo_id()

            # (withdraw+deiconify rimosso: rompeva il fullscreen su Openbox)

            # 2) Tk standard
            try:
                self.root.lift()
                self.root.focus_force()
                target.set_focus()
            except Exception as _e:
                _log_focus("Tk focus errore: %s" % _e)

            # 3) _NET_ACTIVE_WINDOW sul TOPLEVEL (frame WM)
            try:
                _x11_activate_window(xid_top)
            except Exception as _e:
                _log_focus("ActiveWindow eccezione: %s" % _e)

            # 4) XSetInputFocus sul TOPLEVEL e sulla finestra interna
            try:
                _x11_setinputfocus(xid_top)
                _x11_setinputfocus(self.root.winfo_id())
            except Exception as _e:
                _log_focus("XSetInputFocus eccezione: %s" % _e)

            # 5) Click simulato al centro della finestra
            try:
                cx = self.root.winfo_rootx() + max(1, self.root.winfo_width()) // 2
                cy = self.root.winfo_rooty() + max(1, self.root.winfo_height()) // 2
            except Exception:
                cx, cy = 400, 300
            ok = False
            try:
                ok = _click_ctypes(cx, cy)
            except Exception as _e:
                _log_focus("ctypes eccezione: %s" % _e)
            if not ok:
                _click_xdotool(cx, cy)

            # 6) Dopo il click, un altro giro di XSetInputFocus sul toplevel
            def _rifocus():
                if not getattr(self, '_focus_login_attivo', False):
                    return
                try:
                    _x11_activate_window(_get_toplevel_xid())
                    _x11_setinputfocus(_get_toplevel_xid())
                    self.root.focus_force()
                    target.set_focus()
                    _log_focus("Rifocus finale ok")
                except Exception as _e:
                    _log_focus("Rifocus errore: %s" % _e)
            self.root.after(150, _rifocus)
            self.root.after(500, _rifocus)

        import time
        self.root.after(600, _forza_focus_once)

    def _mostra_logo_login(self, parent, c):
        """Mostra, se presente, il logo del rivenditore sotto i campi login.

        Il file viene cercato in quest'ordine:
          1. dati/loghi/<nome>    - override utente/rivenditore locale,
                                    NON toccato dagli aggiornamenti
          2. dati/loghi/<base>.gif
          3. loghi/<nome>         - default spedito con l'app via GitHub update
          4. loghi/<base>.gif
          5. dati/<nome>          - retrocompat vecchie installazioni
          6. dati/<base>.gif      - retrocompat

        <nome> e' configurabile in conf (chiave 'login_logo', default
        'logo.png'). Se il logo e' troppo grande viene ridotto con
        subsample() (stdlib tkinter, no PIL).

        Il doppio livello permette: default bello out-of-the-box per tutti
        (cartella loghi/ distribuita), ma il rivenditore puo' brandizzare
        mettendo il proprio PNG in dati/loghi/ senza paura che gli venga
        sovrascritto dall'aggiornamento successivo.

        La reference alla PhotoImage viene salvata in self._login_logo_img
        per evitare che il garbage collector di Python la cancelli (tkinter
        tiene solo una weak-ref alle immagini)."""
        try:
            base_dir  = self._get_base()
            dati_dir  = self.percorsi.get("dati", "dati")
            user_dir  = os.path.join(dati_dir, "loghi")   # override utente
            ship_dir  = os.path.join(base_dir, "loghi")   # default spedito
            # Crea la cartella utente se manca, cosi' il rivenditore sa
            # dove mettere un eventuale override senza leggere la doc.
            try:
                os.makedirs(user_dir, exist_ok=True)
            except Exception:
                pass
            nome_logo = self.conf.get("login_logo", "logo.png") or "logo.png"
            base_nome, _ext = os.path.splitext(nome_logo)

            candidati = [
                os.path.join(user_dir, nome_logo),
                os.path.join(user_dir, base_nome + ".gif"),
                os.path.join(ship_dir, nome_logo),
                os.path.join(ship_dir, base_nome + ".gif"),
                os.path.join(dati_dir, nome_logo),           # retrocompat
                os.path.join(dati_dir, base_nome + ".gif"),  # retrocompat
            ]
            path = None
            for p in candidati:
                if os.path.exists(p):
                    path = p
                    break
            if path is None:
                return  # Nessun logo disponibile, schermata standard
            img = tk.PhotoImage(file=path)
            # Limiti massimi: non piu' di ~300 px di larghezza e ~100 px
            # di altezza, cosi' il form di login resta visibile sotto.
            w = max(1, img.width())
            h = max(1, img.height())
            max_w = _S(300)
            max_h = _S(100)
            fx = (w + max_w - 1) // max_w if w > max_w else 1
            fy = (h + max_h - 1) // max_h if h > max_h else 1
            fattore = max(fx, fy)
            if fattore > 1:
                img = img.subsample(int(fattore), int(fattore))
            self._login_logo_img = img
            # Logo centrato sotto i campi login/password
            tk.Label(parent, image=img, bg=c["sfondo"], bd=0,
                     highlightthickness=0).pack(pady=(_S(8), _S(8)))
        except Exception:
            # File corrotto o formato non supportato: ignora silenziosamente
            # (la schermata login deve comunque funzionare).
            self._login_logo_img = None

    def _spegni_console(self):
        """Spegne la console (Linux/uConsole). Doppia pressione per conferma."""
        import time
        c = carica_colori()
        now = time.time()
        # Trova label di stato (login o menu) - verifica che esista ancora
        _lbl = None
        for attr in ('_login_status', '_menu_status'):
            w = getattr(self, attr, None)
            if w:
                try:
                    if w.winfo_exists():
                        _lbl = w; break
                except: pass
        if not hasattr(self, '_spegni_ts') or now - self._spegni_ts > 3:
            self._spegni_ts = now
            if _lbl:
                _lbl.config(text="Premi SPEGNI di nuovo per confermare!",
                            fg=c["stato_errore"])
            return
        # Confermato: backup + spegni
        del self._spegni_ts
        if _lbl:
            _lbl.config(text="Backup e spegnimento...", fg=c["stato_avviso"])
            self.root.update()
        # Backup automatico pre-spegnimento
        try:
            self._esegui_backup(force=True)
        except Exception:
            pass  # Se il backup fallisce, spegni comunque
        if sys.platform == "win32":
            self.root.destroy()  # Su Windows: solo esci
        else:
            # Linux/uConsole: prova piu' strade per spegnere senza richiedere
            # la password sudo (non c'e' un tty dove digitarla dopo destroy).
            # Ordine tentativi:
            #   1) systemctl poweroff              -> passa via polkit
            #   2) sudo -n shutdown -h now         -> NOPASSWD sudoers
            #   3) sudo -n poweroff                -> NOPASSWD sudoers (alias)
            #   4) sudo shutdown -h now            -> ultimo tentativo
            import subprocess
            comandi = [
                ["systemctl", "poweroff"],
                ["sudo", "-n", "shutdown", "-h", "now"],
                ["sudo", "-n", "poweroff"],
                ["sudo", "shutdown", "-h", "now"],
            ]
            spento = False
            for cmd in comandi:
                try:
                    r = subprocess.run(cmd, capture_output=True,
                                       text=True, timeout=5)
                    if r.returncode == 0:
                        spento = True
                        break
                except Exception:
                    continue
            if spento:
                self.root.destroy()
            else:
                # Tutti i tentativi falliti: avvisa e NON distruggere la UI
                # cosi' l'utente capisce che deve configurare sudoers.
                if _lbl:
                    _lbl.config(
                        text=("Impossibile spegnere: configurare sudoers "
                              "(vedi LEGGIMI.txt)"),
                        fg=c["stato_errore"])
                    self.root.update()

    def _esci_al_desktop(self):
        """Chiude TrackMind e torna al desktop (Linux/uConsole).

        Prima fa un backup automatico per sicurezza, poi ferma il thread di
        auto-riconnessione Wi-Fi e distrugge la finestra Tk. Al ritorno su
        console l'utente trova il desktop Openbox/LXDE e puo' aprire un
        terminale, modificare file di sistema, cambiare layout tastiera, ecc.
        Doppia pressione per conferma (come SPEGNI) per evitare uscite
        accidentali in pista.
        """
        import time
        c = carica_colori()
        now = time.time()
        # Trova label di stato in base alla schermata corrente
        _lbl = None
        for attr in ('_menu_status', '_login_status'):
            w = getattr(self, attr, None)
            if w:
                try:
                    if w.winfo_exists():
                        _lbl = w; break
                except: pass
        if not hasattr(self, '_esci_so_ts') or now - self._esci_so_ts > 3:
            self._esci_so_ts = now
            if _lbl:
                _lbl.config(text="Premi ESCI A SO di nuovo per confermare!",
                            fg=c["stato_avviso"])
            return
        # Confermato: backup + uscita
        del self._esci_so_ts
        if _lbl:
            _lbl.config(text="Backup e uscita al desktop...",
                        fg=c["stato_avviso"])
            self.root.update()
        try:
            self._esegui_backup(force=True)
        except Exception:
            pass  # Se il backup fallisce, esci comunque
        # Ferma il thread Wi-Fi (se attivo) prima di chiudere
        try:
            self._wifi_auto_stop()
        except Exception:
            pass
        self.root.destroy()

    def _esegui_login(self):
        # Disattiva click/focus autostart: se l'utente ha gia' premuto Invio
        # non serve piu' forzare nulla.
        self._focus_login_attivo = False
        c = carica_colori()
        username = self._login_user.get().strip()
        password = self._login_pwd.get_raw().strip()

        # Accesso sviluppatore (configurazione sistema)
        if username == "CoNfI":
            self._login_user.clear()
            self._login_pwd.clear()
            self._apri_conf()
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
            # Bootstrap Assistente Gara: se c'e' uno stato salvato su
            # disco da una sessione precedente, riattiva il monitor
            # subito (cosi' l'utente vede gia' il widget header e
            # gli alert popup, senza dover riaprire l'addon).
            self._bootstrap_assistente_gara()
            # Bottone [i] CENTRO DI CONTROLLO: overlay sempre visibile
            # in alto a sinistra del Toplevel. Click apre il popup
            # con tutte le info di stato (utente, wifi, stampante,
            # batteria, RAM, CPU, SD, gara). Scorciatoia: Ctrl+I.
            self._bootstrap_centro_controllo()
            self._schermata_menu()
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

        # â"€â"€ HEADER COMPATTO â"€â"€
        # Layout orizzontale unico: barre SD a sinistra, colonna dx con
        # titolo + info stampanti/wifi/batteria impilati verticalmente.
        # L'altezza totale del header e' quella delle 3 barre (~3 righe),
        # ma contiene anche titolo e info: risparmiamo 1-2 righe rispetto
        # al layout precedente, lasciando piu' spazio alla sezione TABELLE.
        header = tk.Frame(self._vista, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(20), pady=(_S(4), _S(2)))

        riga_top = tk.Frame(header, bg=c["sfondo"])
        riga_top.pack(fill="x")

        # Pannello strumenti SD: 3 barre LED impilate a sinistra.
        #   riga 1: capienza disco   (% libero)
        #   riga 2: usura stimata    (GB scritti vs TBW, o ext_csd reale)
        #   riga 3: VU meter I/O     (MB/s in tempo reale)
        self._barra_sd = None
        self._barra_usura = None
        self._barra_io = None
        self._barra_batteria = None
        if _HAS_SD_BAR:
            # TBW di riferimento letto dal CONF (default 30 TB per una buona
            # micro-SD gaming); path file stato dentro dati/.
            tbw_gb = int(self.conf.get("sd_tbw_gb", 30000))
            file_wear = os.path.join(self.percorsi.get("dati", "dati"),
                                     "sd_wear.json")
            max_mbs = float(self.conf.get("sd_vu_max_mbs", 20))
            pannello_sd = tk.Frame(riga_top, bg=c["sfondo"])
            pannello_sd.pack(side="left", anchor="nw")
            try:
                self._barra_sd = _BarraSD(pannello_sd)
                self._barra_sd.pack(anchor="w", pady=(0, _S(1)))
            except Exception:
                self._barra_sd = None
            try:
                self._barra_usura = _BarraUsura(pannello_sd, tbw_gb=tbw_gb,
                                                file_stato=file_wear)
                self._barra_usura.pack(anchor="w", pady=_S(1))
            except Exception:
                self._barra_usura = None
            try:
                self._barra_io = _BarraIO(pannello_sd, max_mbs=max_mbs)
                self._barra_io.pack(anchor="w", pady=(_S(1), 0))
            except Exception:
                self._barra_io = None
            # Nota: BarraBatteria non e' piu' qui. Va nel pannello destro,
            # sotto la info_line, per non rendere la colonna sx troppo alta.

        # Colonna destra: titolo (sopra) + riga info (sotto), impilati vertical.
        # Si prende tutto lo spazio rimanente, col contenuto centrato.
        pannello_dx = tk.Frame(riga_top, bg=c["sfondo"])
        pannello_dx.pack(side="left", expand=True, fill="both")

        # Riga titolo + batteria, sulla stessa linea (BAT a destra del titolo).
        # Cosi' la colonna dx resta alta solo 2 righe (titolo+BAT / info_line).
        riga_titolo = tk.Frame(pannello_dx, bg=c["sfondo"])
        riga_titolo.pack(pady=(0, _S(1)))

        nome_db = _nome_base(self.conf.get("nome_database", "RetroDB"))
        tk.Label(riga_titolo, text=nome_db + "  v" + __version__,
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_login).pack(side="left")

        ruolo_txt = "ADMIN" if is_admin(self.sessione) else "UTENTE"
        ruolo_fg = c["stato_avviso"] if is_admin(self.sessione) else c["dati"]
        info_line = tk.Frame(pannello_dx, bg=c["sfondo"])
        info_line.pack()
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
        # Salvata in self._lbl_wifi_menu cosi' _wifi_monitor puo'
        # aggiornarla in tempo reale quando la rete cade/torna.
        self._lbl_wifi_menu = tk.Label(info_line, text=wifi_txt, bg=c["sfondo"],
                                        fg=wifi_fg, font=self._f_small)
        self._lbl_wifi_menu.pack(side="left")

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

        # Widget ASSISTENTE GARA: label nel menu (figlia di
        # info_line_gara, su riga propria sotto info_line). Cosi'
        # non copre la barra batteria nel header (era questo il bug
        # dell'overlay puro). Quando l'utente entra in un addon
        # (CRONO/setup) la label nel menu viene distrutta col menu,
        # e l'overlay figlia del Toplevel (creato in _bootstrap)
        # prende il suo posto. _aggiorna_widget_assistente gestisce
        # entrambi: aggiorna la label nel menu se viva, altrimenti
        # mostra l'overlay sopra qualunque schermata.
        self._lbl_assist_gara_menu = None
        if _HAS_ASSISTENTE:
            try:
                self._info_line_gara = tk.Frame(pannello_dx,
                                                  bg=c["sfondo"])
                self._info_line_gara.pack(fill="x")
                self._lbl_assist_gara_menu = tk.Label(
                    self._info_line_gara, text="",
                    bg=c["sfondo"], fg=c["stato_avviso"],
                    font=self._f_small, cursor="hand2")
                self._lbl_assist_gara_menu.bind(
                    "<Button-1>",
                    lambda e: self._lancia_assistente_gara())
                _mon = AssistenteGaraMonitor.get(self.root)
                if _mon is not None and _mon.attivo:
                    self._lbl_assist_gara_menu.pack(side="left")
            except Exception:
                self._lbl_assist_gara_menu = None

        # Indicatore Batteria: barra LED sulla stessa riga del titolo,
        # a destra di "TRACKMIND vX". Solo se disponibile (uConsole).
        # Niente label testuale: la barra gia' mostra il % a destra.
        self._lbl_batteria = None  # compat: _aggiorna_label_batteria lo legge
        try:
            _batt_probe_pct, _ = _get_batteria_info()
        except Exception:
            _batt_probe_pct = None
        if _batt_probe_pct is not None and _BarraBatteria is not None:
            try:
                self._barra_batteria = _BarraBatteria(
                    riga_titolo, get_info_func=_get_batteria_info)
                self._barra_batteria.pack(side="left", padx=(_S(10), 0))
            except Exception:
                self._barra_batteria = None

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(20), pady=(_S(3), 0))

        # â"€â"€ AREA CENTRALE (espandibile) â"€â"€
        centro = tk.Frame(self._vista, bg=c["sfondo"])
        centro.pack(fill="both", expand=True, padx=_S(20), pady=(_S(2), _S(2)))

        # Etichetta sezione (compatta: meno pady per lasciare spazio ai bottoni)
        tk.Label(centro, text="T A B E L L E", bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(pady=(_S(2), _S(2)))

        # ── Griglia tabelle con scroll orizzontale ──
        grid_border = tk.Frame(centro, bg=c["linee"], bd=0)
        grid_border.pack(fill="x", pady=(_S(0), _S(4)))

        # Canvas con scroll orizzontale
        _scroll_canvas = tk.Canvas(grid_border, bg=c["sfondo"], highlightthickness=0, bd=0)
        _hscroll = tk.Scrollbar(grid_border, orient="horizontal", command=_scroll_canvas.xview,
                                bg=c["sfondo"], troughcolor=c["sfondo"],
                                activebackground=c["pulsanti_sfondo"])
        _scroll_canvas.configure(xscrollcommand=_hscroll.set)
        _scroll_canvas.pack(fill="x", expand=True, padx=1, pady=(1, 0))

        grid_inner = tk.Frame(_scroll_canvas, bg=c["sfondo"], padx=_S(6), pady=_S(4))
        _canvas_win = _scroll_canvas.create_window((0, 0), window=grid_inner, anchor="nw")

        # Bottoni tabelle
        def_dir = self.percorsi["definizioni"]
        os.makedirs(def_dir, exist_ok=True)
        tabelle = [f[:-4] for f in sorted(os.listdir(def_dir)) if f.endswith(".def")]

        BTN_W_CHAR = 12                       # larghezza bottone ridotta
        BTN_PAD_X = _S(3)                     # padding orizzontale
        BTN_PAD_Y = _S(2)                     # padding verticale compatto
        MAX_RIGHE = 5                          # righe massime visibili
        btn_w = BTN_W_CHAR                     # width in caratteri (non scalato, tkinter lo gestisce)

        grid_frame = tk.Frame(grid_inner, bg=c["sfondo"])
        grid_frame.pack()

        menu_btns = []
        self._menu_tab_btns = {}
        for i, tab in enumerate(tabelle):
            def_path = os.path.join(def_dir, "%s.def" % tab)
            td = TableDef(def_path)
            if td.nascosto:
                continue  # Tabelle con !nascosto;vero non compaiono nel menu
            autorizzato = td.utente_autorizzato(self.sessione)
            nome_btn = tab.upper().replace("_", " ")

            if autorizzato:
                btn_fg = c["pulsanti_testo"]
                if _HAS_WEBSYNC and td.links and ha_cambiamenti(tab):
                    btn_fg = c["stato_avviso"]
                btn = tk.Button(grid_frame, text=nome_btn, font=self._f_btn, width=btn_w,
                                bg=c["pulsanti_sfondo"], fg=btn_fg,
                                activebackground=c["cerca_sfondo"], activeforeground=c["pulsanti_testo"],
                                relief="ridge", bd=1, cursor="hand2")
                btn.config(command=self._flash_btn(btn, lambda t=tab: self._apri_tabella(t)))
            else:
                btn = tk.Button(grid_frame, text=nome_btn, font=self._f_btn, width=btn_w,
                                bg=c["sfondo"], fg=c["testo_dim"],
                                relief="flat", bd=1, state="disabled",
                                disabledforeground=c["testo_dim"])
            menu_btns.append(btn)
            if autorizzato:
                self._menu_tab_btns[tab] = btn

        # ── Layout: righe fisse, colonne quante servono, scroll H ──
        import math as _math
        n_tab = len(tabelle)
        RIGHE = min(MAX_RIGHE, n_tab)  # righe effettive
        if RIGHE < 1:
            RIGHE = 1
        COLONNE = _math.ceil(n_tab / RIGHE) if n_tab else 1
        self._menu_colonne = COLONNE

        for i, btn in enumerate(menu_btns):
            riga = i // COLONNE      # riempi per riga (sx->dx, alto->basso)
            col_pos = i % COLONNE
            btn.grid(row=riga, column=col_pos, padx=BTN_PAD_X, pady=BTN_PAD_Y, sticky="ew")

        # Navigazione tastiera
        if menu_btns:
            self._kb_setup_griglia(menu_btns, COLONNE)

        # Aggiorna scrollregion, altezza e centratura canvas dopo render
        def _aggiorna_scroll(event=None):
            grid_inner.update_idletasks()
            bbox = _scroll_canvas.bbox("all")
            if bbox:
                content_w = bbox[2] - bbox[0]
                content_h = bbox[3] - bbox[1]
                _scroll_canvas.configure(height=content_h)
                canvas_w = _scroll_canvas.winfo_width()
                if content_w <= canvas_w:
                    # Griglia ci sta: centra orizzontalmente
                    offset_x = (canvas_w - content_w) // 2
                    _scroll_canvas.configure(scrollregion=(0, 0, canvas_w, content_h))
                    _scroll_canvas.coords(_canvas_win, offset_x, 0)
                    _hscroll.pack_forget()
                else:
                    # Griglia troppo larga: scroll orizzontale
                    _scroll_canvas.coords(_canvas_win, 0, 0)
                    _scroll_canvas.configure(scrollregion=bbox)
                    _hscroll.pack(fill="x", padx=1, pady=(0, 1))

        grid_inner.bind("<Configure>", _aggiorna_scroll)
        _scroll_canvas.bind("<Configure>", _aggiorna_scroll)
        _scroll_canvas.after(80, _aggiorna_scroll)

        # Scroll con rotella mouse (orizzontale)
        def _on_mouse_scroll(event):
            if event.num == 4 or event.delta > 0:
                _scroll_canvas.xview_scroll(-3, "units")
            elif event.num == 5 or event.delta < 0:
                _scroll_canvas.xview_scroll(3, "units")
        _scroll_canvas.bind("<MouseWheel>", _on_mouse_scroll)
        _scroll_canvas.bind("<Button-4>", _on_mouse_scroll)
        _scroll_canvas.bind("<Button-5>", _on_mouse_scroll)
        grid_inner.bind("<MouseWheel>", _on_mouse_scroll)
        grid_inner.bind("<Button-4>", _on_mouse_scroll)
        grid_inner.bind("<Button-5>", _on_mouse_scroll)

        # Linea decorativa sotto griglia
        deco_bot = tk.Frame(centro, bg=c["sfondo"])
        deco_bot.pack(fill="x", pady=(_S(2), 0))
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

        # ── Crediti IA ──
        _cr_rimasti = crediti_ia_rimasti(self.conf)
        _cr_colore = c["stato_ok"] if _cr_rimasti > 50 else (c.get("cerca_testo", "#ffcc00") if _cr_rimasti > 0 else c["stato_errore"])
        ia_frame = tk.Frame(centro, bg=c["sfondo"])
        ia_frame.pack(pady=(_S(4), 0))
        self._lbl_crediti = tk.Label(ia_frame, text="Crediti IA: %d" % _cr_rimasti,
                                      bg=c["sfondo"], fg=_cr_colore, font=self._f_small)
        self._lbl_crediti.pack(side="left")
        # Bottone ricarica (apre mini-form inline)
        self._btn_ricarica = tk.Button(ia_frame, text="RICARICA", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._mostra_ricarica_ia)
        self._btn_ricarica.pack(side="left", padx=(_S(6), 0))

        # Frame per il form ricarica (inizialmente nascosto)
        self._ricarica_frame = tk.Frame(centro, bg=c["sfondo"])
        # Non packato, verra' mostrato da _mostra_ricarica_ia

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


        # â"€â"€ BARRA INFERIORE (sempre in fondo) â"€â"€
        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(20), side="bottom")

        bottom_bar = tk.Frame(self._vista, bg=c["sfondo"])
        bottom_bar.pack(fill="x", side="bottom", padx=_S(20), pady=(_S(6), _S(8)))

        # Licenza a sinistra
        _, lic_msg, _ = verifica_licenza(self.conf)
        tk.Label(bottom_bar, text=lic_msg, bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small, anchor="w").pack(side="left")

        # Bottoni funzione — griglia compatta su piu' righe
        _bb = []  # lista bottoni per navigazione tastiera
        btn_grid = tk.Frame(bottom_bar, bg=c["sfondo"])
        btn_grid.pack(side="right")

        _bw = 8  # larghezza bottone (caratteri, senza _S per compattezza)

        def _mkb(parent, testo, cmd, fg=None, w=None):
            """Crea bottone menu compatto."""
            _fg = fg or c["pulsanti_testo"]
            _w = w or _bw
            b = tk.Button(parent, text=testo, font=self._f_small, width=_w,
                          bg=c["pulsanti_sfondo"], fg=_fg,
                          activebackground=c["cerca_sfondo"], activeforeground=c["pulsanti_testo"],
                          relief="ridge", bd=1, cursor="hand2", command=cmd)
            return b

        if is_admin(self.sessione):
            self._btn_backup = _mkb(btn_grid, "BACKUP", self._esegui_backup, fg=c["stato_ok"])
            _bb.append(self._btn_backup)
            _bb.append(_mkb(btn_grid, "RIPRISTI", self._esegui_ripristino))
            _bb.append(_mkb(btn_grid, "EDITA TB", self._lancia_editor_tabelle))
            _bb.append(_mkb(btn_grid, "ATTIVA", lambda: self._schermata_attivazione(
                          get_codice_macchina(), "Inserisci nuova chiave di attivazione")))
            _bb.append(_mkb(btn_grid, "HOTSPOT", self._schermata_hotspot))
            if _HAS_UPDATER:
                _bb.append(_mkb(btn_grid, "AGGIORNA", self._schermata_aggiorna))
                # Nota: il bottone PREPARA (confezionamento pacchetti di
                # aggiornamento) e' stato spostato in dev/pubblica.py
                # (tool standalone per sviluppatore). Sulla uConsole non
                # serve: e' una macchina di uso sul campo, non di build.

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
            _bb.append(_mkb(btn_grid, "CRONO", lambda: self._lancia_crono_scouting(),
                            fg=c["stato_avviso"]))

        # Bottone PROMPT IA (editor system prompt)
        if _HAS_PROMPT_EDITOR:
            _bb.append(_mkb(btn_grid, "PROMPT", self._apri_prompt_editor,
                            fg=c["cerca_testo"]))

        # Bottone ASSISTENTE GARA (monitor evento MyRCM live)
        if _HAS_ASSISTENTE:
            _bb.append(_mkb(btn_grid, "ASSIST.\nGARA",
                            self._lancia_assistente_gara,
                            fg=c["stato_avviso"]))

        night_label = "GIORNO" if self._is_night_mode() else "NOTTE"
        _bb.append(_mkb(btn_grid, night_label, self._toggle_night_mode))
        _bb.append(_mkb(btn_grid, "COLORI", self._schermata_setup))
        _bb.append(_mkb(btn_grid, "ESCI", self._schermata_login))

        # Solo Linux: bottone SPEGNI sistema
        if sys.platform != "win32":
            _bb.append(_mkb(btn_grid, "SPEGNI", self._spegni_console,
                            fg=c["stato_errore"]))
            # Esce da TrackMind lasciandoti al desktop (Openbox/LXDE della uConsole).
            # Utile per accedere al terminale, modificare file di sistema,
            # aggiornare la tastiera, lanciare nmtui, ecc. senza dover spegnere.
            _bb.append(_mkb(btn_grid, "ESCI A SO", self._esci_al_desktop,
                            fg=c["stato_avviso"]))

        # Disponi in griglia automatica: max bottoni per riga in base alla larghezza
        _COLS_MENU = 7  # bottoni per riga
        for idx, b in enumerate(_bb):
            r = idx // _COLS_MENU
            cl = idx % _COLS_MENU
            b.grid(row=r, column=cl, padx=_S(2), pady=_S(1))

        # Navigazione tastiera griglia bottoni
        self._kb_setup_griglia(_bb, _COLS_MENU)

        # Riporta il focus sul primo bottone tabella autorizzato:
        # _kb_setup_griglia dei bottoni inferiori (BACKUP/HOTSPOT/...) lo aveva
        # rubato, e le barre SD in alto con le loro .after() di init potrebbero
        # influire sul traversal. Posticipiamo con after_idle cosi' avviene
        # DOPO tutte le inizializzazioni pendenti.
        def _ripristina_focus_menu():
            try:
                for _tb in menu_btns:
                    if str(_tb["state"]) != "disabled":
                        _tb.focus_set()
                        break
            except Exception:
                pass
        try:
            self.root.after_idle(_ripristina_focus_menu)
        except Exception:
            _ripristina_focus_menu()

        # Escape -> logout
        self.root.bind("<Escape>", lambda e: self._schermata_login())
        self._rimuovi_coperta()

        # Auto-sync piste SpeedHive (controlla !sync_date, agisce solo se > 24h)
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

    @staticmethod
    def _smtp_connect(server, port, timeout=15):
        """Restituisce un client SMTP gia' pronto (autenticazione a carico del chiamante).

        Sceglie automaticamente il metodo di crittografia in base alla porta:
        - 465 -> SMTP_SSL (TLS implicito, Gmail "SSL/TLS" di Thunderbird)
        - 587 (o altro) -> SMTP + STARTTLS (TLS esplicito, standard moderno)

        Cosi' l'utente puo' impostare in CONFI qualsiasi porta supportata dal
        provider senza dover toccare il codice. Se la rete blocca una porta
        (es. alcuni hotspot mobili bloccano 587) basta cambiare a 465 in CONFI.
        """
        import smtplib
        p = int(port)
        if p == 465:
            s = smtplib.SMTP_SSL(server, p, timeout=timeout)
        else:
            s = smtplib.SMTP(server, p, timeout=timeout)
            s.starttls()
        return s

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
                with self._smtp_connect(smtp_srv, smtp_port, timeout=15) as s:
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

    def _wifi_sanifica_esistenti(self):
        """Promuove tutte le connessioni Wi-Fi salvate da 'agent-owned'
        (password cifrata nel keyring utente) a 'system-owned' (password
        in /etc/NetworkManager/system-connections/, leggibile dal sistema).

        Scopo: eliminare il popup 'La rete Wi-Fi richiede autenticazione'
        che il keyring manager (gnome-keyring / kwallet) fa comparire SOPRA
        TrackMind quando NM prova ad attivare una connessione user-owned
        col portachiavi chiuso. Sulla uConsole in modalita' kiosk questo
        popup e' intrusivo e non sempre chiudibile.

        Strategia best-effort:
        - legge l'elenco connessioni wi-fi con nmcli
        - per ogni connessione con psk-flags != 0 (user-owned o similari)
          prova a leggere la password in chiaro (--show-secrets); se ci
          riesce la riscrive con psk-flags=0
        - se la password non e' recuperabile (keyring locked, p.es.) NON
          tocca la connessione: resterebbe inservibile senza la password
        Tutti gli errori sono silenziosi: nessuna connessione utente va
        persa se qualcosa va storto.
        """
        if sys.platform == "win32":
            return
        try:
            # 1) Trova tutte le connessioni wireless
            r = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
                capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return
            nomi_wifi = []
            for line in r.stdout.splitlines():
                # Formato: NAME:TYPE (nmcli -t escapa : con \:)
                import re as _re
                parti = _re.split(r'(?<!\\):', line)
                parti = [p.replace('\\:', ':') for p in parti]
                if len(parti) >= 2 and "wireless" in parti[1].lower():
                    nomi_wifi.append(parti[0])
            if not nomi_wifi:
                return

            sanate = 0
            for nome in nomi_wifi:
                try:
                    # 2) Controlla il valore di psk-flags
                    rf = subprocess.run(
                        ["nmcli", "-t", "-f",
                         "802-11-wireless-security.psk-flags",
                         "connection", "show", nome],
                        capture_output=True, text=True, timeout=5)
                    if rf.returncode != 0:
                        continue
                    flag_line = rf.stdout.strip()
                    # Puo' essere "802-11-wireless-security.psk-flags:1" oppure
                    # solo "1"; prendo l'ultima parte dopo :
                    val = flag_line.split(":")[-1].strip() if ":" in flag_line else flag_line
                    # Estrai solo cifre (a volte c'e' testo tipo "1 (agent-owned)")
                    val_num = ""
                    for ch in val:
                        if ch.isdigit():
                            val_num += ch
                        elif val_num:
                            break
                    if val_num == "0" or val_num == "":
                        continue  # gia' system-owned, nulla da fare

                    # 3) Prova a leggere la password in chiaro
                    rp = subprocess.run(
                        ["nmcli", "--show-secrets", "-t", "-f",
                         "802-11-wireless-security.psk",
                         "connection", "show", nome],
                        capture_output=True, text=True, timeout=5)
                    if rp.returncode != 0:
                        continue
                    pwd_line = rp.stdout.strip()
                    pwd = pwd_line.split(":", 1)[-1] if ":" in pwd_line else ""
                    if not pwd:
                        # Password non recuperabile (keyring chiuso / rete
                        # open / errore). Lasciamo stare: meglio un popup
                        # occasionale che una connessione cancellata.
                        continue

                    # 4) Riscrivi come system-owned
                    subprocess.run(
                        ["nmcli", "connection", "modify", nome,
                         "802-11-wireless-security.psk-flags", "0"],
                        capture_output=True, text=True, timeout=5)
                    subprocess.run(
                        ["nmcli", "connection", "modify", nome,
                         "802-11-wireless-security.psk", pwd],
                        capture_output=True, text=True, timeout=5)
                    sanate += 1
                except subprocess.TimeoutExpired:
                    continue
                except Exception:
                    continue
            if sanate:
                print("[WIFI] %d connessioni promosse a system-owned" % sanate)
        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            pass  # nmcli non disponibile
        except Exception:
            pass


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
                    # Auto-salva nell'elenco Wi-Fi conosciute
                    try:
                        self._wifi_elenco_aggiungi(ssid, password or "")
                    except Exception:
                        pass
                    return True, "Connesso a %s" % ssid
                return False, r.stdout.strip() or r.stderr.strip() or "Connessione fallita"
            else:
                # Linux (nmcli) - uConsole / Raspberry Pi
                # IMPORTANTE: se esiste un profilo salvato con lo stesso SSID
                # (anche della rete di default del SO), nmcli lo RIUSA ignorando
                # la password appena digitata. Per hotspot cellulari con SSID
                # generici (iPhone, AndroidAP, ecc.) questo causa "Secrets
                # were required" o connessione fallita silenziosamente.
                # Soluzione: cancella sempre il profilo vecchio prima.
                try:
                    subprocess.run(["nmcli", "connection", "delete", "id", ssid],
                                   capture_output=True, text=True, timeout=10)
                except Exception:
                    pass  # Il profilo potrebbe non esistere, ok
                # Prova anche varianti (nmcli a volte salva con suffissi)
                try:
                    r_list = subprocess.run(
                        ["nmcli", "-t", "-f", "NAME", "connection", "show"],
                        capture_output=True, text=True, timeout=5)
                    for nome in r_list.stdout.splitlines():
                        nome = nome.strip()
                        if nome and (nome == ssid or nome.startswith(ssid + " ")):
                            subprocess.run(["nmcli", "connection", "delete", "id", nome],
                                           capture_output=True, text=True, timeout=5)
                except Exception:
                    pass
                # Connessione pulita con la password fresca
                cmd = ["nmcli", "device", "wifi", "connect", ssid]
                if password:
                    cmd += ["password", password]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    # IMPORTANTE: promuovi la connessione appena creata da
                    # "agent-owned" (keyring utente) a "system-owned". Senza
                    # questo, al prossimo boot o a ogni riconnessione NM
                    # chiede all'utente di sbloccare il portachiavi con un
                    # popup di sistema che compare SOPRA TrackMind fullscreen.
                    # Con psk-flags=0 la password viene salvata in
                    # /etc/NetworkManager/system-connections/ e letta
                    # automaticamente dal sistema, niente popup.
                    if password:
                        try:
                            subprocess.run(
                                ["nmcli", "connection", "modify", ssid,
                                 "802-11-wireless-security.psk-flags", "0"],
                                capture_output=True, text=True, timeout=5)
                            subprocess.run(
                                ["nmcli", "connection", "modify", ssid,
                                 "802-11-wireless-security.psk", password],
                                capture_output=True, text=True, timeout=5)
                        except Exception:
                            pass  # Se fallisce la connessione e' attiva, best-effort
                    # Auto-salva nell'elenco Wi-Fi conosciute
                    try:
                        self._wifi_elenco_aggiungi(ssid, password or "")
                    except Exception:
                        pass
                    return True, "Connesso a %s" % ssid
                err = r.stderr.strip() or r.stdout.strip() or "Connessione fallita"
                # Messaggio piu' chiaro per errori comuni
                if "Secrets were required" in err or "802-11-wireless-security" in err:
                    err = "Password errata o hotspot non raggiungibile"
                elif "No network with SSID" in err:
                    err = "Hotspot non piu' visibile - premi AGGIORNA"
                return False, err
        except subprocess.TimeoutExpired:
            return False, "Timeout connessione"
        except Exception as e:
            return False, str(e)

    def _internet_ok(self, timeout=1.0):
        """Test veloce di connettivita' reale via socket TCP verso 1.1.1.1:53
        (Cloudflare DNS). Un TCP connect richiede il 3-way handshake, quindi
        se l'access point e' caduto ma il driver Wi-Fi non se n'e' ancora
        accorto, il connect va in timeout. Molto piu' affidabile dello stato
        del device NetworkManager (che resta 'connected' per minuti dopo una
        caduta per via del timeout di beacon loss)."""
        import socket
        for host in ("1.1.1.1", "8.8.8.8"):
            try:
                s = socket.create_connection((host, 53), timeout=timeout)
                s.close()
                return True
            except OSError:
                continue
        return False

    def _wifi_stato(self):
        """Ritorna (connesso, ssid_corrente).

        Per l'SSID interroga nmcli (Linux) o netsh (Windows). Per il flag
        'connesso' NON si fida del solo stato del device: aggiunge un test
        TCP reale verso 1.1.1.1:53 con timeout 1s, perche' il driver Wi-Fi
        puo' impiegare minuti a notificare la beacon-loss a NetworkManager
        quando l'access point scompare. Se il socket non si collega in 1s,
        consideriamo la rete caduta a prescindere da cosa dice nmcli/netsh.
        """
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
                nmcli_ok = "conness" in stato or "connected" in stato
            else:
                nmcli_ok = False
                ssid = ""
                # Legge lo stato di ogni device: righe formato TYPE:STATE:CONNECTION
                # es. "wlan0:connected:Galaxy" oppure "wlan0:disconnected:"
                r = subprocess.run(
                    ["nmcli", "-t", "-f", "TYPE,STATE,CONNECTION", "device"],
                    capture_output=True, text=True, timeout=5)
                for line in r.stdout.splitlines():
                    # nmcli usa ":" come separatore ma puo' apparire negli SSID:
                    # splittiamo al massimo in 3 parti cosi' il CONNECTION
                    # mantiene eventuali ":" dentro il nome.
                    parti = line.split(":", 2)
                    if len(parti) < 3:
                        continue
                    tipo, stato, conn = parti
                    if tipo.lower() != "wifi":
                        continue
                    # STATE puo' essere: connected, disconnected, unavailable,
                    # connecting, deactivating, unmanaged
                    if stato.lower().startswith("connected") and conn:
                        nmcli_ok = True
                        ssid = conn
                        break
            # Verita' finale: c'e' connettivita' IP?
            if nmcli_ok and self._internet_ok():
                return (True, ssid)
            return (False, "")
        except Exception:
            return (False, "")

    def _wifi_monitor(self):
        """Controlla WiFi ogni 5 sec. Aggiorna il flag [OFFLINE] nel titolo
        della finestra e, se la schermata HOTSPOT e' aperta, aggiorna anche
        la label 'Connesso a: ...' cosi' il cambio di stato e' immediato
        quando l'access point cade o si rialza."""
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
            # Se la schermata hotspot e' aperta, aggiorna la label stato
            # in tempo reale (non solo dopo uno scan).
            try:
                lbl = getattr(self, "_wifi_stato_label", None)
                if lbl is not None and lbl.winfo_exists():
                    c = carica_colori()
                    stato_txt = ("Connesso a: %s" % ssid) if connesso else "Non connesso"
                    stato_fg = c["stato_ok"] if connesso else c["stato_errore"]
                    lbl.config(text=stato_txt, fg=stato_fg)
            except (tk.TclError, AttributeError):
                pass
            # Aggiorna anche le due label Wi-Fi piazzate nel menu principale
            # (riga info sotto il nome utente) e nella schermata di login.
            # Sono create senza ref self finche' v05.05.32, quindi _wifi_monitor
            # non riusciva ad aggiornarle: l'utente vedeva "Wi-Fi: Galaxy"
            # in verde anche a rete caduta finche' non cambiava schermata.
            try:
                c = carica_colori()
                lbl_menu = getattr(self, "_lbl_wifi_menu", None)
                if lbl_menu is not None and lbl_menu.winfo_exists():
                    txt = ("  |  Wi-Fi: %s" % ssid) if connesso else "  |  Wi-Fi: offline"
                    fg = c["stato_ok"] if connesso else c["stato_errore"]
                    lbl_menu.config(text=txt, fg=fg)
                lbl_login = getattr(self, "_lbl_wifi_login", None)
                if lbl_login is not None and lbl_login.winfo_exists():
                    txt = ("Wi-Fi: %s" % ssid) if connesso else "Wi-Fi: non connesso"
                    fg = c["stato_ok"] if connesso else c["stato_errore"]
                    lbl_login.config(text=txt, fg=fg)
            except (tk.TclError, AttributeError):
                pass
        except Exception:
            pass
        self.root.after(5000, self._wifi_monitor)

    def _wifi_auto_start(self):
        """Avvia (o riavvia) il thread di auto-riconnessione Wi-Fi.

        Idempotente: se il modulo core.wifi_monitor manca, o se nella CONFI
        l'opzione e' disattivata, non fa nulla ma sopravvive l'app.
        Chiamato all'avvio e ad ogni salvataggio della CONFI.

        v05.05.30: niente piu' SSID singolo in CONFI; il thread consulta
        l'elenco dati/wifi.json (tabella wifi, popolata dalla cattura
        automatica) e tenta tutte le reti conosciute che risultano a portata.
        """
        # Ferma il thread precedente, se in esecuzione
        self._wifi_auto_stop()

        if not _HAS_WIFI_AUTO:
            return
        attivo = int(self.conf.get("wifi_auto_attivo", 0) or 0)
        if not attivo:
            return

        try:
            intervallo = int(self.conf.get("wifi_auto_intervallo", 15) or 15)
        except (ValueError, TypeError):
            intervallo = 15

        log_path = os.path.join(self.percorsi["dati"], "wifi_log.txt")
        wifi_json = os.path.join(self.percorsi["dati"], "wifi.json")
        try:
            self._wifi_auto = _WifiAutoRiconnettore(
                wifi_json_path=wifi_json,
                intervallo_sec=intervallo,
                log_path=log_path,
            )
            self._wifi_auto.start()
        except Exception as _e:
            print("[WIFI_AUTO] avvio fallito: %s" % _e)
            self._wifi_auto = None

    def _wifi_auto_stop(self):
        """Ferma il thread di auto-riconnessione se in esecuzione."""
        try:
            if self._wifi_auto is not None:
                self._wifi_auto.stop()
                self._wifi_auto = None
        except Exception:
            self._wifi_auto = None

    # ── ELENCO WI-FI CONOSCIUTE (dati/wifi.json - tabella wifi) ──
    #
    # L'elenco viene popolato in due modi:
    #   1) Cattura automatica: quando _wifi_monitor rileva una connessione
    #      a un SSID non ancora salvato, apre un popup che chiede la
    #      password per aggiungerlo.
    #   2) Salvataggio diretto: quando l'utente si connette via la
    #      schermata CONNESSIONE HOTSPOT (_wifi_connect), TrackMind
    #      conosce gia' la password e la salva senza chiedere.
    #
    # AutoRiconnettore (in core/wifi_monitor.py) legge lo stesso file e,
    # quando il Wi-Fi cade, scansiona le reti visibili: per qualsiasi SSID
    # conosciuto in portata tenta la riconnessione usando la password
    # salvata. Nessun SSID personale e' piu' hardcoded in CONFI.

    def _wifi_elenco_path(self):
        return os.path.join(self.percorsi["dati"], "wifi.json")

    def _wifi_elenco_carica(self):
        """Legge dati/wifi.json e ritorna la lista di record (dict)."""
        path = self._wifi_elenco_path()
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                contenuto = json.load(f)
            return list(contenuto.get("records", []) or [])
        except Exception:
            return []

    def _wifi_elenco_salva(self, records):
        """Sovrascrive dati/wifi.json con la lista passata, mantenendo _meta."""
        path = self._wifi_elenco_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        contenuto = {
            "_meta": {"tabella": "wifi", "accesso": "admin",
                      "versione": APP_VERSION},
            "records": records,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(contenuto, f, indent=2, ensure_ascii=False)

    def _wifi_elenco_aggiungi(self, ssid, password, note=""):
        """Aggiunge una rete all'elenco se non presente. Ritorna True se
        aggiunta, False se duplicata o in caso di errore. Se la password
        e' vuota e la rete esiste gia', aggiorna comunque la password
        (utile dopo una connessione manuale dalla schermata hotspot)."""
        ssid = (ssid or "").strip()
        if not ssid:
            return False
        recs = self._wifi_elenco_carica()
        ssid_low = ssid.lower()
        # Se gia' presente: aggiorna la password se ora ne abbiamo una
        # migliore di quella salvata (tipicamente dopo connessione ok).
        for r in recs:
            if str(r.get("SSID", "")).strip().lower() == ssid_low:
                if password and str(r.get("Password", "")) != password:
                    r["Password"] = password
                    r["_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        self._wifi_elenco_salva(recs)
                    except Exception:
                        pass
                return False
        # Nuovo record: calcola prossimo Codice PK
        max_codice = 0
        for r in recs:
            try:
                v = int(r.get("Codice", 0))
                if v > max_codice:
                    max_codice = v
            except Exception:
                pass
        nuovo = {
            "_id": str(uuid.uuid4())[:8],
            "Codice": str(max_codice + 1),
            "SSID": ssid,
            "Password": password or "",
            "Note": note or "",
            "_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        # Associa all'utente corrente (se gia' loggato)
        try:
            sess = getattr(self, "sessione", None)
            if sess:
                uid = (sess.get("Codice") or sess.get("_utente_id")
                       or sess.get("codice") or "")
                if uid:
                    nuovo["_utente_id"] = str(uid)
        except Exception:
            pass
        recs.append(nuovo)
        try:
            self._wifi_elenco_salva(recs)
            return True
        except Exception:
            return False

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

    def _batteria_formatta(self, pct, stato, c):
        """Formatta testo e colore per la label batteria.
        Ritorna (testo, colore_fg)."""
        in_carica = (stato == "Charging")
        if pct <= 10 and not in_carica:
            return ("  |  Batt: %d%%  METTIMI IN CARICA!" % pct, c["stato_errore"])
        if pct <= 25 and not in_carica:
            return ("  |  Batt: %d%%  (bassa)" % pct, c["stato_avviso"])
        if in_carica:
            return ("  |  Batt: %d%% (in carica)" % pct, c["stato_ok"])
        if stato == "Full":
            return ("  |  Batt: carica", c["stato_ok"])
        return ("  |  Batt: %d%%" % pct, c["stato_ok"])

    def _aggiorna_label_batteria(self):
        """Aggiorna la label batteria ogni 60 sec (se presente)."""
        try:
            if not hasattr(self, '_lbl_batteria') or self._lbl_batteria is None:
                return
            if not self._lbl_batteria.winfo_exists():
                self._lbl_batteria = None
                return
        except Exception:
            self._lbl_batteria = None
            return
        batt_pct, batt_stato = _get_batteria_info()
        if batt_pct is None:
            return
        c = carica_colori()
        txt, fg = self._batteria_formatta(batt_pct, batt_stato, c)
        try:
            self._lbl_batteria.config(text=txt, fg=fg)
        except Exception:
            pass
        # Prossimo check tra 30 secondi
        self.root.after(30000, self._aggiorna_label_batteria)

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
        # Password in chiaro (niente show="*"): su pista le password Wi-Fi
        # sono spesso complicate e vederle mentre si digitano evita errori.
        self._wifi_pwd_entry = tk.Entry(conn_bar, font=self._f_label, width=20,
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
                if not self._wifi_listbox.winfo_exists():
                    return
            except (tk.TclError, AttributeError):
                return
            reti, errore = self._wifi_scan_result or ([], "Errore sconosciuto")
            # Ricorda selezione attuale
            old_ssid = ""
            try:
                old_sel = self._wifi_listbox.curselection()
            except (tk.TclError, AttributeError):
                return
            if old_sel and self._wifi_reti:
                old_ssid = self._wifi_reti[old_sel[0]].get("ssid", "")
            self._wifi_reti = reti
            try:
                self._wifi_listbox.delete(0, "end")
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
            except (tk.TclError, AttributeError):
                return

            # Auto-refresh ogni 20 secondi (solo se la schermata e' ancora viva)
            if getattr(self, '_wifi_auto_refresh', False):
                try:
                    if self._wifi_listbox.winfo_exists():
                        self.root.after(20000, self._wifi_aggiorna)
                except (tk.TclError, AttributeError):
                    pass

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
    def _login_blocca(self, blocca):
        """Blocca/sblocca i campi login durante il check aggiornamenti."""
        try:
            if blocca:
                self._login_user.set_readonly(True)
                self._login_pwd.set_readonly(True)
            else:
                self._login_user.set_readonly(False)
                self._login_pwd.set_readonly(False)
                # Attiva cursore visibile sul campo utente
                self._login_user._canvas.focus_set()
        except (tk.TclError, AttributeError):
            pass

    def _login_sblocca_dopo_check(self):
        """Sblocca il login dopo il check aggiornamenti."""
        c = carica_colori()
        self._login_blocca(False)
        try:
            self._login_status.config(text="Default: admin / 000000", fg=c["testo_dim"])
        except (tk.TclError, AttributeError):
            pass

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

                    with self._smtp_connect(smtp_srv, smtp_port, timeout=30) as s:
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

    def _lancia_editor_tabelle_dev(self):
        """Apre Editor Tabelle in modalita' sviluppatore (mostra tabelle nascoste).
        Accessibile solo dalla schermata conf (login CoNfI)."""
        if not _HAS_EDITOR:
            return
        self._pulisci()
        EditorTabelle(self._vista, on_close=self._schermata_login,
                      mostra_nascosti=True)
        self._rimuovi_coperta()


    # =========================================================================
    #  PROMPT EDITOR (editor system prompt IA) - schermata inline
    # =========================================================================
    def _apri_prompt_editor(self):
        """Editor system prompt IA come schermata inline (niente Toplevel).
        Usa _pulisci/_vista come tutte le altre schermate RetroDB."""
        if not _HAS_PROMPT_EDITOR:
            self._status("Prompt Editor non disponibile!", "stato_errore")
            return

        self._pulisci(); c = carica_colori()
        prompt_path = _get_prompt_path()
        pe_modificato = [False]  # lista per closure

        # ── HEADER ──
        header = tk.Frame(self._vista, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))
        tk.Button(header, text="< MENU", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: _chiudi_editor()).pack(side="left")
        tk.Label(header, text="[ PROMPT IA ]", bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(_S(10), 0))
        lbl_stato = tk.Label(header, text="Salvato", bg=c["sfondo"],
                             fg=c["stato_ok"], font=self._f_small)
        lbl_stato.pack(side="right")

        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10), pady=(_S(4), 0))

        # ── FEEDBACK BANNER (nascosto inizialmente) ──
        feedback_frame = tk.Frame(self._vista, bg=c["sfondo"])
        lbl_feedback = tk.Label(feedback_frame, text="", bg=c["sfondo"],
                                fg=c["stato_ok"],
                                font=tkfont.Font(family=FONT_MONO, size=_S(10), weight="bold"))
        lbl_feedback.pack(fill="x", pady=(_S(4), _S(4)))
        feedback_attivo = [False]

        def _mostra_feedback(msg, colore, durata=2500):
            lbl_feedback.config(text=msg, fg=colore)
            if not feedback_attivo[0]:
                feedback_frame.pack(fill="x", padx=_S(10), after=self._vista.winfo_children()[1])
                feedback_attivo[0] = True
            feedback_frame.config(bg=colore)
            lbl_feedback.config(bg=colore, fg=c["sfondo"])
            def _fade():
                try:
                    feedback_frame.config(bg=c["sfondo"])
                    lbl_feedback.config(bg=c["sfondo"], fg=colore)
                except Exception: pass
            def _nascondi():
                try:
                    feedback_frame.pack_forget()
                    feedback_attivo[0] = False
                except Exception: pass
            try:
                self.root.after(350, _fade)
                self.root.after(durata, _nascondi)
            except Exception: pass
            lbl_stato.config(text=msg, fg=colore)

        # ── AREA TESTO ──
        txt_frame = tk.Frame(self._vista, bg=c["linee"], bd=0)
        txt_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(6), _S(4)))
        txt_inner = tk.Frame(txt_frame, bg=c["sfondo_celle"])
        txt_inner.pack(fill="both", expand=True, padx=1, pady=1)
        scroll = tk.Scrollbar(txt_inner, orient="vertical",
                              bg=c["sfondo"], troughcolor=c["sfondo"],
                              activebackground=c["pulsanti_sfondo"])
        scroll.pack(side="right", fill="y")
        pe_txt = tk.Text(txt_inner, bg=c["sfondo_celle"], fg=c["dati"],
                         insertbackground=c["cursore"], insertwidth=_S(8),
                         selectbackground=c["cursore"], selectforeground=c["sfondo"],
                         font=self._f_label, wrap="word", undo=True, maxundo=-1,
                         padx=_S(8), pady=_S(6), relief="flat", bd=0,
                         yscrollcommand=scroll.set)
        pe_txt.pack(fill="both", expand=True)
        scroll.config(command=pe_txt.yview)

        # Tag cursore a blocco
        pe_txt.tag_configure("cursore_blocco",
                             background=c["cursore"], foreground=c["sfondo"])

        # ── INFO RIGA ──
        info_bar = tk.Frame(self._vista, bg=c["sfondo"])
        info_bar.pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))
        lbl_info = tk.Label(info_bar, text="", bg=c["sfondo"],
                            fg=c["testo_dim"], font=self._f_status, anchor="w")
        lbl_info.pack(side="left")
        lbl_file = tk.Label(info_bar, text=os.path.basename(prompt_path),
                            bg=c["sfondo"], fg=c["testo_dim"],
                            font=self._f_status, anchor="e")
        lbl_file.pack(side="right")

        # ── BARRA BOTTONI ──
        tk.Frame(self._vista, bg=c["linee"], height=1).pack(fill="x", padx=_S(10))
        btn_bar = tk.Frame(self._vista, bg=c["sfondo"])
        btn_bar.pack(fill="x", padx=_S(10), pady=(_S(4), _S(4)))

        pe_btns = []
        btn_salva = tk.Button(btn_bar, text="SALVA [Ctrl+S]", font=self._f_btn,
                              bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                              relief="ridge", bd=1, cursor="hand2",
                              command=lambda: _salva(), width=14)
        btn_salva.pack(side="left", padx=(0, _S(4)))
        pe_btns.append(btn_salva)

        btn_default = tk.Button(btn_bar, text="DEFAULT [Ctrl+R]", font=self._f_btn,
                                bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                                relief="ridge", bd=1, cursor="hand2",
                                command=lambda: _ripristina_default(), width=16)
        btn_default.pack(side="left", padx=(0, _S(4)))
        pe_btns.append(btn_default)

        btn_ricarica = tk.Button(btn_bar, text="RICARICA", font=self._f_btn,
                                 bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                                 relief="ridge", bd=1, cursor="hand2",
                                 command=lambda: _ricarica(), width=10)
        btn_ricarica.pack(side="left")
        pe_btns.append(btn_ricarica)

        btn_chiudi = tk.Button(btn_bar, text="< MENU [Esc]", font=self._f_btn,
                               bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                               relief="ridge", bd=1, cursor="hand2",
                               command=lambda: _chiudi_editor(), width=12)
        btn_chiudi.pack(side="right")
        pe_btns.append(btn_chiudi)

        # Navigazione tastiera tra bottoni
        for i, b in enumerate(pe_btns):
            if i < len(pe_btns) - 1:
                nxt = pe_btns[i + 1]
                b.bind("<Right>", lambda e, n=nxt: (n.focus_set(), "break")[-1])
                b.bind("<Tab>", lambda e, n=nxt: (n.focus_set(), "break")[-1])
            else:
                b.bind("<Tab>", lambda e: (pe_txt.focus_set(), "break")[-1])
            if i > 0:
                prv = pe_btns[i - 1]
                b.bind("<Left>", lambda e, p=prv: (p.focus_set(), "break")[-1])
                b.bind("<Shift-Tab>", lambda e, p=prv: (p.focus_set(), "break")[-1])
            else:
                b.bind("<Left>", lambda e: (pe_btns[-1].focus_set(), "break")[-1])
                b.bind("<Shift-Tab>", lambda e: (pe_txt.focus_set(), "break")[-1])

        # Help
        self._help_bar(self._vista,
            "Ctrl+S = Salva  |  Ctrl+R = Default  |  Ctrl+Z = Annulla  |  Esc = Menu")

        # ── FUNZIONI ──
        def _aggiorna_stato():
            if pe_modificato[0]:
                lbl_stato.config(text="* MODIFICATO *", fg=c["stato_avviso"])
            else:
                lbl_stato.config(text="Salvato", fg=c["stato_ok"])

        def _on_modify(event=None):
            if pe_txt.edit_modified():
                pe_modificato[0] = True
                _aggiorna_stato()

        def _aggiorna_info():
            try:
                pos = pe_txt.index("insert")
                riga, col = pos.split(".")
                contenuto = pe_txt.get("1.0", "end-1c")
                n_righe = contenuto.count("\n") + 1
                n_car = len(contenuto)
                lbl_info.config(text="Riga %s  Col %s  |  %d righe  %d car." % (
                    riga, col, n_righe, n_car))
            except Exception: pass
            try:
                self.root.after(500, _aggiorna_info)
            except Exception: pass

        def _carica():
            contenuto = ""
            if os.path.exists(prompt_path):
                try:
                    with open(prompt_path, "r", encoding="utf-8") as f:
                        contenuto = f.read()
                except Exception as e:
                    contenuto = "# ERRORE lettura: %s" % e
            else:
                contenuto = PROMPT_DEFAULT
            pe_txt.delete("1.0", "end")
            pe_txt.insert("1.0", contenuto)
            pe_txt.edit_modified(False)
            pe_modificato[0] = False
            _aggiorna_stato()

        def _salva():
            contenuto = pe_txt.get("1.0", "end-1c")
            try:
                with open(prompt_path, "w", encoding="utf-8") as f:
                    f.write(contenuto)
                pe_modificato[0] = False
                pe_txt.edit_modified(False)
                _aggiorna_stato()
                _mostra_feedback(">>> PROMPT SALVATO! <<<", c["stato_ok"], 3000)
            except Exception as e:
                _mostra_feedback("!!! ERRORE: %s !!!" % e, c["stato_errore"], 4000)

        def _ricarica():
            _carica()
            _mostra_feedback(">>> RICARICATO DA DISCO <<<", c["cerca_testo"], 2500)

        def _ripristina_default():
            pe_txt.delete("1.0", "end")
            pe_txt.insert("1.0", PROMPT_DEFAULT)
            pe_txt.edit_modified(True)
            pe_modificato[0] = True
            _aggiorna_stato()
            _mostra_feedback("DEFAULT CARICATO - Premi SALVA per confermare",
                             c["stato_avviso"], 3500)

        def _chiudi_editor():
            if pe_modificato[0]:
                _chiedi_salvataggio()
            else:
                self._schermata_menu()

        def _chiedi_salvataggio():
            """Mini-dialog inline per salvataggio."""
            # Overlay centrato
            dialog = tk.Frame(self._vista, bg=c["sfondo"], bd=0)
            dialog.place(relx=0.5, rely=0.5, anchor="center",
                         width=_S(380), height=_S(140))
            border = tk.Frame(dialog, bg=c["cursore"])
            border.pack(fill="both", expand=True)
            content = tk.Frame(border, bg=c["sfondo"])
            content.pack(fill="both", expand=True, padx=2, pady=2)
            tk.Label(content, text="!!! MODIFICHE NON SALVATE !!!",
                     bg=c["sfondo"], fg=c["stato_avviso"],
                     font=tkfont.Font(family=FONT_MONO, size=_S(10), weight="bold")
                     ).pack(pady=(_S(14), _S(10)))
            btn_row = tk.Frame(content, bg=c["sfondo"])
            btn_row.pack(pady=(_S(0), _S(12)))
            dlg_btns = []
            def _salva_e_chiudi():
                dialog.destroy()
                _salva()
                self._schermata_menu()
            def _chiudi_senza():
                dialog.destroy()
                self._schermata_menu()
            def _annulla():
                dialog.destroy()
                pe_txt.focus_set()
            b1 = tk.Button(btn_row, text="SALVA", font=self._f_btn,
                      bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                      relief="ridge", bd=1, command=_salva_e_chiudi, width=10)
            b1.pack(side="left", padx=_S(5))
            dlg_btns.append(b1)
            b2 = tk.Button(btn_row, text="NON SALVARE", font=self._f_btn,
                      bg=c["pulsanti_sfondo"], fg=c["stato_errore"],
                      relief="ridge", bd=1, command=_chiudi_senza, width=14)
            b2.pack(side="left", padx=_S(5))
            dlg_btns.append(b2)
            b3 = tk.Button(btn_row, text="ANNULLA", font=self._f_btn,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, command=_annulla, width=10)
            b3.pack(side="left", padx=_S(5))
            dlg_btns.append(b3)
            # Navigazione tastiera dialog
            for i, b in enumerate(dlg_btns):
                if i < len(dlg_btns) - 1:
                    nxt = dlg_btns[i + 1]
                    b.bind("<Right>", lambda e, n=nxt: (n.focus_set(), "break")[-1])
                    b.bind("<Tab>", lambda e, n=nxt: (n.focus_set(), "break")[-1])
                else:
                    b.bind("<Tab>", lambda e: (dlg_btns[0].focus_set(), "break")[-1])
                if i > 0:
                    prv = dlg_btns[i - 1]
                    b.bind("<Left>", lambda e, p=prv: (p.focus_set(), "break")[-1])
                else:
                    b.bind("<Left>", lambda e: (dlg_btns[-1].focus_set(), "break")[-1])
                b.bind("<Escape>", lambda e: _annulla())
            dlg_btns[0].focus_set()

        # ── CURSORE A BLOCCO LAMPEGGIANTE ──
        cursore_vis = [True]
        def _aggiorna_cursore():
            pe_txt.tag_remove("cursore_blocco", "1.0", "end")
            if cursore_vis[0]:
                try:
                    pos = pe_txt.index("insert")
                    ch = pe_txt.get(pos, pos + "+1c")
                    if ch != "\n" and ch != "":
                        # Carattere normale: evidenzia col tag blocco
                        pe_txt.tag_add("cursore_blocco", pos, pos + "+1c")
                    # Su righe vuote/newline: il cursore nativo (insertwidth)
                    # mostra gia' la barra verde, nessun tag necessario
                except Exception: pass

        def _blink():
            try:
                if not self._vista.winfo_exists(): return
            except Exception: return
            cursore_vis[0] = not cursore_vis[0]
            _aggiorna_cursore() if cursore_vis[0] else pe_txt.tag_remove("cursore_blocco", "1.0", "end")
            try: self.root.after(530, _blink)
            except Exception: pass

        # ── BIND ──
        pe_txt.bind("<<Modified>>", _on_modify)
        pe_txt.bind("<KeyRelease>", lambda e: _aggiorna_cursore())
        self.root.bind("<Control-s>", lambda e: _salva())
        self.root.bind("<Control-S>", lambda e: _salva())
        self.root.bind("<Control-r>", lambda e: _ripristina_default())
        self.root.bind("<Control-R>", lambda e: _ripristina_default())
        self.root.bind("<Escape>", lambda e: _chiudi_editor())

        # Tab da testo ai bottoni
        pe_txt.bind("<Tab>", lambda e: (pe_btns[0].focus_set(), "break")[-1])

        # Carica contenuto e avvia
        _carica()
        _aggiorna_info()
        _blink()
        pe_txt.focus_set()


    # =========================================================================
    #  CRONO (modulo esterno)
    # =========================================================================
    def _lancia_crono(self):
        """Lancia il modulo Crono con contesto dal setup corrente."""
        if not _HAS_CRONO:
            self._status("Modulo Crono non disponibile!", "stato_errore")
            return
        contesto = self._build_crono_contesto()
        # Memorizza il punto di ingresso: se parto dalla scheda editor
        # di un record, all'uscita tornero' alla stessa scheda, non alla lista.
        self._crono_ritorno_record = (self.indice_corrente
                                       if self.indice_corrente is not None
                                       else -1)
        self._pulisci()
        Crono(parent=self._vista, on_close=self._ritorno_da_crono,
              contesto=contesto)
        self._rimuovi_coperta()

    def _elimina_file_tempi(self, idx):
        """Elimina i file lap_*.json associati al record in posizione idx.
        Cerca per id_XXXX (nuovo formato) e rec_N (vecchio formato)."""
        rec = self.db.leggi(idx)
        if not rec:
            return
        base = self._get_base()
        dati_dir = self.percorsi.get("dati", os.path.join(base, "dati"))
        if not dati_dir or not os.path.isdir(dati_dir):
            return

        prefissi = set()
        # Nuovo formato: id_{_id}
        _id = rec.get("_id", "")
        if _id:
            prefissi.add("lap_id_%s_" % _id)
        # Vecchio formato: rec_{indice}
        prefissi.add("lap_rec_%d_" % idx)
        # Se c'e' un campo chiave, anche quel valore
        campo_k = self.table_def.get_campo_chiave()
        if campo_k:
            val = str(rec.get(campo_k["nome"], "")).strip()
            if val:
                val_safe = val.replace("/", "-").replace("\\", "-").replace(":", "-").replace(" ", "_")
                prefissi.add("lap_%s_" % val_safe)

        eliminati = 0
        for f in os.listdir(dati_dir):
            if not f.endswith(".json"):
                continue
            for pref in prefissi:
                if f.startswith(pref):
                    try:
                        os.remove(os.path.join(dati_dir, f))
                        eliminati += 1
                    except Exception:
                        pass
                    break

    def _migra_rec_a_id(self, dati_dir):
        """Migra file lap_rec_N_*.json al nuovo formato lap_id_XXXX_*.json.
        Usa la corrispondenza indice -> _id dalla tabella corrente.
        Eseguito una sola volta (i file rinominati non matchano piu' rec_)."""
        if not dati_dir or not os.path.isdir(dati_dir):
            return
        # Cerca file vecchio formato
        import re as _re
        vecchi = {}
        for f in os.listdir(dati_dir):
            m = _re.match(r'^lap_rec_(\d+)_(.+\.json)$', f)
            if m:
                idx = int(m.group(1))
                resto = m.group(2)
                vecchi.setdefault(idx, []).append((f, resto))
        if not vecchi:
            return
        # Costruisci mappa indice -> _id dal database corrente
        for idx, file_list in vecchi.items():
            rec = self.db.leggi(idx)
            if not rec:
                continue
            _id = rec.get("_id", "")
            if not _id:
                continue
            nuovo_prefisso = "id_%s" % _id
            for old_name, resto in file_list:
                nuovo_nome = "lap_%s_%s" % (nuovo_prefisso, resto)
                old_path = os.path.join(dati_dir, old_name)
                new_path = os.path.join(dati_dir, nuovo_nome)
                if os.path.exists(new_path):
                    continue
                try:
                    # Aggiorna record_id dentro il JSON
                    with open(old_path, "r", encoding="utf-8") as fh:
                        j = json.load(fh)
                    j["record_id"] = nuovo_prefisso
                    with open(new_path, "w", encoding="utf-8") as fh:
                        json.dump(j, fh, ensure_ascii=False, indent=2)
                    os.remove(old_path)
                except Exception:
                    pass

    def _build_crono_contesto(self):
        """Costruisce il dizionario contesto per Crono dal record corrente."""
        base = self._get_base()
        dati_dir = self.percorsi.get("dati", os.path.join(base, "dati"))

        # Migra vecchi file rec_N -> id_XXXX (una tantum)
        self._migra_rec_a_id(dati_dir)

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

            # Campi setup marcati con flag A (analisi IA)
            parametri_ia = {}
            sezione_corrente = ""
            for campo_def in self.table_def.campi:
                if campo_def.get("analisi_ia"):
                    nome = campo_def["nome"]
                    val = str(rec.get(nome, "")).strip()
                    if val:
                        # Cerca la sezione di appartenenza
                        for cn, sez in self.table_def.sezioni.items():
                            if cn == nome:
                                sezione_corrente = sez
                                break
                        # Nome leggibile: "Camber_Ant" -> "Camber Ant"
                        nome_label = nome.replace("_", " ")
                        if sezione_corrente:
                            chiave_sez = sezione_corrente.replace("_", " ")
                            if chiave_sez not in parametri_ia:
                                parametri_ia[chiave_sez] = []
                            parametri_ia[chiave_sez].append((nome_label, val))
                        else:
                            if "" not in parametri_ia:
                                parametri_ia[""] = []
                            parametri_ia[""].append((nome_label, val))
            if parametri_ia:
                contesto["parametri_ia"] = parametri_ia

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

    def _bootstrap_centro_controllo(self):
        """Registra la scorciatoia Ctrl+I globale che apre/chiude il
        popup centro di controllo. Niente bottone overlay: l'utente
        puo' evocarlo solo da tastiera (centro di controllo ha
        priorita' bassa, non deve disturbare il layout)."""
        try:
            from core.centro_controllo import apri_centro_controllo
        except Exception:
            return  # se manca il modulo, niente shortcut
        try:
            self.root.bind(
                "<Control-i>",
                lambda e: apri_centro_controllo(self))
            self.root.bind(
                "<Control-I>",
                lambda e: apri_centro_controllo(self))
        except Exception:
            pass

    def _bootstrap_assistente_gara(self):
        """Inizializza il monitor singleton dell'Assistente Gara al
        login. Carica lo stato salvato su disco, registra l'alert
        listener (BEEP sonoro), e crea un OVERLAY label figlia
        diretta del Toplevel: cosi' resta visibile in QUALUNQUE
        schermata (menu, CRONO, setup, ecc.), e' il vero "centro di
        controllo" del countdown gara. Idempotente."""
        if not _HAS_ASSISTENTE:
            return
        try:
            _mon = AssistenteGaraMonitor.get(self.root)
        except Exception:
            return
        if _mon is None:
            return
        # Prova a caricare lo stato dal disco. Se c'era una sessione
        # attiva, il monitor parte gia' attivo a questo punto.
        if not _mon.attivo:
            try:
                _mon.carica_stato_persistito()
            except Exception:
                pass
        # Crea il widget OVERLAY come figlia diretta del Toplevel.
        # Sopravvive a tutti i _pulisci() che agiscono su _vista o
        # _base. Posizionato in alto a destra con place(). Visibile
        # solo se monitor attivo (place_forget altrimenti).
        if (not hasattr(self, "_lbl_assist_gara")
                or self._lbl_assist_gara is None):
            try:
                c = carica_colori()
                self._lbl_assist_gara = tk.Label(
                    self.root, text="GARA: ...",
                    bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                    font=tkfont.Font(family=FONT_MONO, size=_S(9),
                                      weight="bold"),
                    cursor="hand2", padx=6, pady=2,
                    relief="ridge", bd=1)
                self._lbl_assist_gara.bind(
                    "<Button-1>",
                    lambda e: self._lancia_assistente_gara())
            except Exception:
                self._lbl_assist_gara = None
        # Arma l'alert listener una volta sola (idempotente).
        # Niente popup invadenti: solo BEEP sonoro al cambio soglia.
        if not getattr(self, "_assist_listeners_armati", False):
            def _on_alert(stato, prossimo, dt_target):
                try:
                    self.root.bell()
                except Exception:
                    pass
            try:
                _mon.add_alert_listener(_on_alert)
            except Exception:
                pass
            self._assist_listeners_armati = True
            # Avvia il refresh periodico dell'overlay
            self._aggiorna_widget_assistente()

    def _lancia_assistente_gara(self):
        """Lancia l'addon Assistente Gara: monitor evento MyRCM live
        con countdown turni e alert per la categoria selezionata.
        Alla prima invocazione registra anche l'alert listener
        globale (popup -15min/-1min) e avvia l'updater del widget
        header. Il monitor sopravvive alla chiusura dell'addon."""
        if not _HAS_ASSISTENTE:
            return
        try:
            _mon = AssistenteGaraMonitor.get(self.root)
        except Exception:
            _mon = None
        if _mon is not None and not getattr(self,
                                            "_assist_listeners_armati",
                                            False):
            # Alert popup: scatena un Toplevel a -15 min e -1 min anche
            # se l'utente sta lavorando in altre schermate.
            def _on_alert(stato, prossimo, dt_target):
                try:
                    mostra_popup_alert(self.root, stato,
                                        prossimo, dt_target,
                                        colori=carica_colori())
                except Exception:
                    pass
            _mon.add_alert_listener(_on_alert)
            self._assist_listeners_armati = True
            # Avvia il refresh periodico del widget header
            self._aggiorna_widget_assistente()
        self._pulisci()
        # Passa il nome dell'utente loggato come default per il
        # filtro "Tuo nome" della schermata iniziale dell'addon
        # (cosi' chi corre col proprio nome registrato in MyRCM
        # vede subito le sue manche, senza dover digitare).
        nome_loggato = ""
        try:
            if self.sessione:
                nome_loggato = (get_display_name(self.sessione)
                                or "").strip()
        except Exception:
            pass
        AssistenteGara(parent=self._vista,
                       on_close=self._schermata_menu,
                       nome_pilota_default=nome_loggato)
        self._rimuovi_coperta()

    def _aggiorna_widget_assistente(self):
        """Aggiorna periodicamente DUE label "GARA: ...":
        1. nel menu retrodb (`_lbl_assist_gara_menu`, riga sotto
           info_line) - visibile solo quando il menu e' attivo;
        2. overlay sul Toplevel (`_lbl_assist_gara`) - visibile
           SOLO quando la label nel menu non c'e' (cioe' quando
           l'utente e' in CRONO/setup/altro addon).
        Cosi' nel menu non copre la batteria, e fuori dal menu
        e' sempre visibile sopra a qualunque schermata."""
        if not _HAS_ASSISTENTE:
            return
        try:
            _mon = AssistenteGaraMonitor.get(self.root)
        except Exception:
            _mon = None
        c = carica_colori()
        # Calcola testo e colore una sola volta
        testo = ""
        col_fg = c["testo_dim"]
        col_bg = c["sfondo"]   # menu
        col_bg_overlay = c["pulsanti_sfondo"]
        attivo = _mon is not None and _mon.attivo
        if attivo:
            prossimo, dt_target = _mon.trova_prossimo()
            if prossimo is None or dt_target is None:
                testo = "GARA: nessun turno"
                col_fg = c["testo_dim"]
            else:
                secs = int((dt_target - _mon._now()).total_seconds())
                if secs < 0:
                    secs = 0
                ore = secs // 3600
                mm = (secs % 3600) // 60
                ss = secs % 60
                if ore > 0:
                    cd = "%d:%02d:%02d" % (ore, mm, ss)
                else:
                    cd = "%02d:%02d" % (mm, ss)
                cat = (prossimo.get("categoria", "") or
                       (_mon.categoria or {}).get("nome", "?"))
                cat = cat[:18]
                # Soglie: confronto su SECONDI esatti.
                if secs <= 60:
                    col_fg = c["stato_errore"]
                    col_bg_overlay = "#ff4444"
                elif secs <= 180:
                    col_fg = "#ff8800"
                    col_bg_overlay = "#ff8800"
                elif secs <= 900:
                    col_fg = c["stato_avviso"]
                    col_bg_overlay = "#ffaa00"
                else:
                    col_fg = c["stato_ok"]
                    col_bg_overlay = c["pulsanti_sfondo"]
                testo = "  |  GARA: %s fra %s" % (cat, cd)

        # 1. Label nel MENU: aggiorna se viva
        try:
            lbl_menu = getattr(self, "_lbl_assist_gara_menu", None)
            if lbl_menu is not None:
                try:
                    if not lbl_menu.winfo_exists():
                        self._lbl_assist_gara_menu = None
                        lbl_menu = None
                except Exception:
                    self._lbl_assist_gara_menu = None
                    lbl_menu = None
            menu_visibile = False
            if lbl_menu is not None:
                if not attivo:
                    try:
                        lbl_menu.pack_forget()
                    except Exception:
                        pass
                else:
                    try:
                        lbl_menu.config(text=testo, fg=col_fg, bg=col_bg)
                        lbl_menu.pack(side="left")
                        menu_visibile = True
                    except Exception:
                        pass
        except Exception:
            menu_visibile = False

        # 2. Overlay sul Toplevel: visibile SOLO se la label menu
        # non e' visibile (= utente fuori dal menu, in un addon).
        try:
            lbl_ov = getattr(self, "_lbl_assist_gara", None)
            if lbl_ov is not None:
                try:
                    if not lbl_ov.winfo_exists():
                        self._lbl_assist_gara = None
                        lbl_ov = None
                except Exception:
                    self._lbl_assist_gara = None
                    lbl_ov = None
            if lbl_ov is not None:
                if not attivo or menu_visibile:
                    try:
                        lbl_ov.place_forget()
                    except Exception:
                        pass
                else:
                    # Adatta il testo per overlay (no prefisso "  |  ")
                    testo_ov = testo.lstrip(" |").strip() or "GARA"
                    fg_ov = ("#000000"
                             if col_bg_overlay in ("#ff4444",
                                                    "#ff8800",
                                                    "#ffaa00")
                             else col_fg)
                    try:
                        lbl_ov.config(text=testo_ov,
                                       bg=col_bg_overlay, fg=fg_ov)
                        lbl_ov.place(relx=1.0, rely=0.0,
                                      anchor="ne", x=-6, y=4)
                        lbl_ov.lift()
                    except Exception:
                        pass
        except Exception:
            pass

        # Ripianifica il prossimo tick
        try:
            self.root.after(1000, self._aggiorna_widget_assistente)
        except Exception:
            pass

    def _ritorno_da_crono(self):
        """Callback: torna al punto di ingresso da cui si e' entrati in Crono.
        - Se si era nella scheda editor di un record -> torna a quella scheda
        - Altrimenti (nessun record) -> torna al wizard selezione della tabella
        - Se non c'e' tabella corrente -> torna al menu principale
        """
        nome = self._nome_tabella if hasattr(self, '_nome_tabella') else ''
        idx_ritorno = getattr(self, '_crono_ritorno_record', -1)
        # Pulisce il flag di ritorno
        self._crono_ritorno_record = -1
        if nome:
            # Se avevamo un record aperto in editor, ricostruisci la scheda
            if idx_ritorno is not None and idx_ritorno >= 0:
                self.indice_corrente = idx_ritorno
                # Ricostruisci la lista indici visibili se mancante
                if not getattr(self, '_indici_visibili', None):
                    self._indici_visibili = self.db.get_records_filtrati(
                        self.filtro_utente())
                if idx_ritorno in self._indici_visibili:
                    self._pos_visibile = self._indici_visibili.index(idx_ritorno)
                self._costruisci_form(nome)
                self._mostra_record()
            else:
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
        # Fallback: se non c'e' indirizzo/citta, usa Nome_Pista + Nazione
        if not indirizzo and not citta:
            if "piste" in self.ref_dbs:
                pista_db = self.ref_dbs["piste"]
                for idx in range(len(pista_db.records)):
                    pr = pista_db.leggi(idx)
                    if pr and str(pr.get("Codice_Pista", "")) == str(codice_pista):
                        nome_pista = str(pr.get("Nome_Pista", "")).strip()
                        if nome_pista:
                            indirizzo = nome_pista
                        break
            if not indirizzo:
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
        """Ritorna (record_id, setup_name) per il record corrente.
        Usa _id (hex8 stabile) per tabelle senza chiave, cosi' non si sfasa
        se si eliminano/riordinano record."""
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
            # Usa _id (hex8, stabile e univoco) al posto dell'indice
            _id = rec.get("_id", "")
            if _id:
                record_id = "id_%s" % _id
            else:
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
    # =========================================================================
    #  WEB SYNC (aggiornamento catalogo da web)
    # =========================================================================
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

            # Se il file dati non esiste o e' vuoto, forza sync
            dati_dir = self.percorsi.get("dati", "")
            json_path = os.path.join(dati_dir, "%s.json" % nome_tab) if dati_dir else ""
            dati_vuoti = not json_path or not os.path.exists(json_path)
            if not dati_vuoti:
                try:
                    with open(json_path, "r", encoding="utf-8") as _jf:
                        _jdata = json.load(_jf)
                    if not _jdata.get("records"):
                        dati_vuoti = True
                except Exception:
                    dati_vuoti = True

            # Controlla ultimo sync (letto da !sync_date nel .def)
            # Se dati vuoti, forza sync anche se < 24h
            if not dati_vuoti and td.sync_date:
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
    #  CONF - tabella monorecord gestita dal motore generico
    # =========================================================================

    # Default per campi conf non ancora presenti in conf.dat
    _CONF_DEFAULTS = {
        "scala": "1.0", "fullscreen": "", "cella_dimensione": "16",
        "cella_spaziatura": "1", "font_campi": "9", "font_label": "9",
        "multiutente": "1", "crediti_ia": "500",
        "sd_tbw_gb": "30000", "sd_vu_max_mbs": "20",
        "wifi_auto_attivo": "",
        "wifi_auto_intervallo": "15",
    }

    def _apri_conf(self):
        """Apre la configurazione come tabella monorecord del motore generico.

        Bridge: serializza self.conf -> dati/conf.json come singolo record,
        poi _apri_tabella("conf") lo carica normalmente.
        Dopo il salvataggio, _applica_conf() riscrive conf.dat cifrato.
        """
        # 1. Prepara il record dalla configurazione corrente
        rec = {}
        # Prima carica il .def per sapere quali campi aspettarsi
        def_path = os.path.join(self.percorsi["definizioni"], "conf.def")
        if not os.path.exists(def_path):
            self._status("conf.def mancante in %s" % self.percorsi["definizioni"],
                         "stato_errore")
            self._schermata_login()
            return
        td = TableDef(def_path)
        for campo in td.campi:
            chiave = campo["nome"]
            val = str(self.conf.get(chiave, self._CONF_DEFAULTS.get(chiave, "")))
            # Conversione date ISO (YYYY-MM-DD) -> europeo (DD/MM/YYYY) per RetroField
            if campo["tipo"].upper() == "D" and val:
                raw = val.replace("/", "").replace("-", "").replace(".", "")
                if len(raw) == 8 and raw.isdigit():
                    if int(raw[:4]) > 1900:
                        val = "%s/%s/%s" % (raw[6:8], raw[4:6], raw[0:4])
                    else:
                        val = "%s/%s/%s" % (raw[0:2], raw[2:4], raw[4:8])
            # Conversione flag: "0"/"1" -> ""/"X" per RetroField tipo F
            if campo["tipo"].upper() == "F":
                val_lower = str(val).strip().lower()
                val = "X" if val_lower in ("1", "x", "s", "si", "vero", "true") else ""
            rec[chiave] = val

        # Se anthropic_api_key e' vuota, prova a leggerla da api_key.txt
        if not rec.get("anthropic_api_key", "").strip():
            try:
                key_file = os.path.join(self._get_base(), "api_key.txt")
                if os.path.exists(key_file):
                    with open(key_file, "r", encoding="utf-8") as f:
                        key = f.read().strip()
                    if key and len(key) > 20:
                        rec["anthropic_api_key"] = key
            except Exception:
                pass

        # 2. Serializza come JSON monorecord in dati/conf.json
        dati_dir = self.percorsi["dati"]
        os.makedirs(dati_dir, exist_ok=True)
        conf_json = os.path.join(dati_dir, "conf.json")
        contenuto = {
            "_meta": {"tabella": "conf", "accesso": "admin", "versione": APP_VERSION},
            "records": [rec],
        }
        with open(conf_json, "w", encoding="utf-8") as f:
            json.dump(contenuto, f, indent=2, ensure_ascii=False)

        # 3. Apri con il motore generico (form, scroll, Tab, tutto gratis).
        # _apri_tabella("conf") carica il .def, crea il RetroDB, costruisce
        # il form e mostra il record 0 automaticamente (monorecord: un solo
        # indice in _indici_visibili, conf non ha !nuovo -> va al primo record).
        self._apri_tabella("conf")

    def _applica_conf(self):
        """Post-save per tabella conf: sincronizza JSON -> conf.dat e applica."""
        # Rileggi il record appena salvato dal motore generico
        if not hasattr(self, 'db') or not self.db:
            return
        rec = self.db.leggi(0)
        if not rec:
            return

        # Aggiorna self.conf con i valori dal record
        for chiave, val in rec.items():
            if chiave.startswith("_"):
                continue  # Salta campi meta (_id, _utente_id, _timestamp)
            # Conversione date DD/MM/YYYY -> ISO (YYYY-MM-DD) per conf.dat
            if chiave in ("data_installazione", "data_fine_licenza"):
                if isinstance(val, str) and len(val) == 10 and "/" in val:
                    parti = val.split("/")
                    if len(parti) == 3:
                        val = "%s-%s-%s" % (parti[2], parti[1], parti[0])
            # Conversione flag: "X"/"" -> int per conf.dat
            if chiave == "fullscreen":
                val = 1 if str(val).strip().upper() in ("X", "1") else 0
            # Conversione numerici
            if chiave in ("larghezza_max", "altezza_max",
                          "cella_dimensione", "cella_spaziatura",
                          "font_campi", "font_label", "smtp_port", "crediti_ia",
                          "sd_tbw_gb", "sd_vu_max_mbs",
                          "wifi_auto_intervallo"):
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    val = 700 if "max" in chiave else 0
            elif chiave == "wifi_auto_attivo":
                val = 1 if str(val).strip().upper() in ("X", "1") else 0
            elif chiave == "scala":
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    val = 1.0
            elif chiave == "multiutente":
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    val = 1
            self.conf[chiave] = val

        # Se c'e' la API key, aggiorna anche api_key.txt (per retrocompatibilita')
        api_key = str(self.conf.get("anthropic_api_key", "")).strip()
        if api_key and len(api_key) > 20:
            try:
                key_file = os.path.join(self._get_base(), "api_key.txt")
                with open(key_file, "w", encoding="utf-8") as f:
                    f.write(api_key)
            except Exception:
                pass

        # Salva conf.dat cifrato
        salva_conf(self.conf)
        self.percorsi = get_percorsi(self.conf)
        set_scala(self.conf.get("scala", 1.0))
        set_cell_params(
            size=self.conf.get("cella_dimensione", 16),
            pad=self.conf.get("cella_spaziatura", 1),
            font_cell=self.conf.get("font_campi", 9),
            font_label=self.conf.get("font_label", 9),
        )
        # Aggiorna dimensione finestra
        self._win_w = int(self.conf.get("larghezza_max", 900))
        self._win_h = int(self.conf.get("altezza_max", 700))
        self.root.title(
            _nome_base(self.conf.get("nome_database", "RetroDB")) + "  v" + __version__)

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
        for p in [self.percorsi["definizioni"], self.percorsi["dati"],
                  self.percorsi["backup"]]:
            os.makedirs(p, exist_ok=True)

        # Riavvia l'auto-riconnessione Wi-Fi con i nuovi parametri
        # (se l'utente ha cambiato SSID/intervallo/flag nella CONFI)
        try:
            self._wifi_auto_start()
        except Exception as _e:
            print("[WIFI_AUTO] restart fallito: %s" % _e)

    def _mostra_ricarica_ia(self):
        """Mostra/nasconde il mini-form ricarica IA con RICHIEDI e APPLICA CODICE."""
        c = carica_colori()
        # Toggle: se gia' visibile, nascondi
        if self._ricarica_frame.winfo_ismapped():
            self._ricarica_frame.pack_forget()
            return
        # Pulisci e popola il frame
        for w in self._ricarica_frame.winfo_children():
            w.destroy()

        # Riga 1: bottone RICHIEDI RICARICA (manda email allo sviluppatore)
        row1 = tk.Frame(self._ricarica_frame, bg=c["sfondo"])
        row1.pack(pady=(_S(2), _S(2)))
        tk.Button(row1, text="RICHIEDI RICARICA", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._richiedi_ricarica_ia).pack(side="left")
        tk.Label(row1, text=" Invia richiesta allo sviluppatore",
                 bg=c["sfondo"], fg=c["puntini"], font=self._f_small).pack(side="left")

        # Separatore
        tk.Frame(self._ricarica_frame, bg=c["linee"], height=1).pack(fill="x", pady=(_S(2), _S(2)))

        # Riga 2: campo codice + bottone APPLICA
        row2 = tk.Frame(self._ricarica_frame, bg=c["sfondo"])
        row2.pack(pady=(_S(2), 0))
        tk.Label(row2, text="Hai gia' un codice?", bg=c["sfondo"],
                 fg=c["puntini"], font=self._f_small).pack(side="left", padx=(0, _S(4)))
        self._rf_ricarica = RetroField(row2, label="Codice", tipo="S",
                                        lunghezza=20, label_width=10)
        self._rf_ricarica.pack(side="left")
        tk.Button(row2, text="APPLICA", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._applica_ricarica_ia).pack(side="left", padx=(_S(4), 0))

        # Label esito (usata sia da richiedi che da applica)
        self._lbl_esito_ricarica = tk.Label(self._ricarica_frame, text="",
                                             bg=c["sfondo"], fg=c["puntini"], font=self._f_small)
        self._lbl_esito_ricarica.pack(pady=(_S(1), 0))

        # Pack il frame nel menu
        self._ricarica_frame.pack(after=self._lbl_crediti.master, pady=(_S(2), 0))

    def _richiedi_ricarica_ia(self):
        """Invia email di richiesta ricarica crediti IA allo sviluppatore."""
        c = carica_colori()
        # Verifica connessione
        connesso, _ = self._wifi_stato()
        if not connesso:
            self._lbl_esito_ricarica.config(text="Nessuna connessione internet!",
                                             fg=c["stato_errore"])
            return
        # Dati SMTP da conf
        email_dev = self.conf.get("email_sviluppatore", "").strip()
        smtp_srv = self.conf.get("smtp_server", "").strip()
        smtp_usr = self.conf.get("smtp_user", "").strip()
        smtp_pwd = self.conf.get("smtp_password", "").strip()
        smtp_port = int(self.conf.get("smtp_port", 587))
        if not email_dev or not smtp_srv or not smtp_usr or not smtp_pwd:
            self._lbl_esito_ricarica.config(text="Configurazione email non completa!",
                                             fg=c["stato_errore"])
            return
        # Dati admin corrente
        nome_admin = get_display_name(self.sessione)
        codice_utente = self.sessione.get("codice", "") if self.sessione else ""
        email_admin = ""
        if codice_utente:
            rec_utente = get_utente(codice_utente)
            if rec_utente:
                email_admin = str(rec_utente.get("Email", "")).strip()
        if not email_admin:
            self._lbl_esito_ricarica.config(
                text="Inserisci la tua Email in UTENTI prima di richiedere!",
                fg=c["stato_errore"])
            return
        # Dati macchina
        codice_macchina = get_codice_macchina()
        rimasti = crediti_ia_rimasti(self.conf)
        ora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        nome_db = self.conf.get("nome_database", "RetroDB")

        # Feedback immediato
        self._lbl_esito_ricarica.config(text="Invio in corso...", fg=c["testo_dim"])

        def _invia():
            try:
                import smtplib
                from email.mime.text import MIMEText
                corpo = (
                    "RICHIESTA RICARICA CREDITI IA\n"
                    "====================================\n\n"
                    "Applicazione: %s v%s\n"
                    "Codice macchina: %s\n"
                    "Data richiesta: %s\n\n"
                    "Admin: %s\n"
                    "Email Admin: %s\n"
                    "Crediti rimasti: %d\n"
                    "Piattaforma: %s\n\n"
                    "---\n"
                    "Rispondere a questa email con il codice\n"
                    "di ricarica dopo aver ricevuto il pagamento.\n"
                ) % (nome_db, __version__, codice_macchina, ora,
                     nome_admin, email_admin, rimasti, sys.platform)
                msg = MIMEText(corpo, "plain", "utf-8")
                msg["Subject"] = "[%s] Richiesta Ricarica IA - %s" % (nome_db, codice_macchina)
                msg["From"] = smtp_usr
                msg["To"] = email_dev
                msg["Reply-To"] = email_admin
                with self._smtp_connect(smtp_srv, smtp_port, timeout=15) as s:
                    s.login(smtp_usr, smtp_pwd)
                    s.send_message(msg)
                # Successo - aggiorna UI dal thread principale
                self.root.after(0, lambda: self._lbl_esito_ricarica.config(
                    text="Richiesta inviata! Riceverai il codice via email.",
                    fg=c["stato_ok"]))
            except Exception as e:
                self.root.after(0, lambda: self._lbl_esito_ricarica.config(
                    text="Errore invio: %s" % str(e)[:60],
                    fg=c["stato_errore"]))

        threading.Thread(target=_invia, daemon=True).start()

    def _applica_ricarica_ia(self):
        """Applica un codice di ricarica crediti IA."""
        c = carica_colori()
        codice = self._rf_ricarica.get().strip()
        if not codice:
            self._lbl_esito_ricarica.config(text="Inserisci un codice ricarica",
                                             fg=c["stato_errore"])
            return
        ok, msg, _ = applica_ricarica_ia(self.conf, codice)
        if ok:
            self._lbl_esito_ricarica.config(text=msg, fg=c["stato_ok"])
            # Aggiorna label crediti nel menu
            rimasti = crediti_ia_rimasti(self.conf)
            _cr_col = c["stato_ok"] if rimasti > 50 else (c.get("cerca_testo", "#ffcc00") if rimasti > 0 else c["stato_errore"])
            self._lbl_crediti.config(text="Crediti IA: %d" % rimasti, fg=_cr_col)
            self._rf_ricarica.set("")
        else:
            self._lbl_esito_ricarica.config(text=msg, fg=c["stato_errore"])

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

        # Controllo accesso (conf bypassa: arriva dal login, non c'e' sessione)
        if nome_tabella != "conf" and not self.table_def.utente_autorizzato(self.sessione):
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
            fieldbackground=c["sfondo_celle"], font=(FONT_MONO, _S(8)),
            rowheight=_S(22), borderwidth=0)
        style.configure("Retro.Treeview.Heading",
            background=c["pulsanti_sfondo"], foreground=c["pulsanti_testo"],
            font=(FONT_MONO, _S(8), "bold"), borderwidth=1, relief="ridge")
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

        # ── Barra ricerca riferimento ──
        search_bar = tk.Frame(self._vista, bg=c["sfondo"])
        search_bar.pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))
        tk.Label(search_bar, text="CERCA:", bg=c["sfondo"], fg=c["cerca_testo"],
                 font=self._f_btn).pack(side="left")
        _step_cerca_var = tk.StringVar()
        _step_search_entry = tk.Entry(search_bar, font=self._f_label, width=30,
                 bg=c["sfondo_celle"], fg=c["dati"], insertbackground=c["dati"],
                 relief="flat", highlightthickness=1, highlightbackground=c["bordo_vuote"],
                 highlightcolor=c["cerca_testo"],
                 textvariable=_step_cerca_var)
        _step_search_entry.pack(side="left", padx=(_S(4), _S(8)), fill="x", expand=True)
        _step_count_label = tk.Label(search_bar, text="",
                 bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small)
        _step_count_label.pack(side="right")

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

        # ── Cache righe per filtro ricerca ──
        _step_all_rows = []
        for child in step_tree.get_children():
            vals = step_tree.item(child, "values")
            _step_all_rows.append((child, vals))

        _step_count_label.config(text="%d record" % len(_step_all_rows))

        def _step_filtra(*args):
            """Filtra righe nel Treeview riferimento in tempo reale."""
            testo = _step_cerca_var.get().strip().lower()
            step_tree.delete(*step_tree.get_children())
            count = 0
            for iid, vals in _step_all_rows:
                if not testo or any(testo in str(v).lower() for v in vals):
                    step_tree.insert("", "end", iid=iid, values=vals)
                    count += 1
            # Zebra
            for i, item in enumerate(step_tree.get_children()):
                if i % 2:
                    step_tree.item(item, tags=("dispari",))
                else:
                    step_tree.item(item, tags=())
            # Seleziona primo risultato
            children_f = step_tree.get_children()
            if children_f:
                step_tree.selection_set(children_f[0])
                step_tree.focus(children_f[0])
                step_tree.see(children_f[0])
            # Conteggio
            tot = len(_step_all_rows)
            if testo:
                _step_count_label.config(
                    text="%d/%d trovati" % (count, tot), fg=c["cerca_testo"])
            else:
                _step_count_label.config(
                    text="%d record" % tot, fg=c["testo_dim"])

        _step_cerca_var.trace_add("write", _step_filtra)
        _step_search_entry.bind("<Escape>", lambda e: (_step_cerca_var.set(""), step_tree.focus_set()))
        _step_search_entry.bind("<Return>", lambda e: step_tree.focus_set())
        _step_search_entry.bind("<Down>", lambda e: step_tree.focus_set())

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
        tk.Label(self._vista, text="Digita = Cerca  |  Frecce = Scorri  |  Enter = Conferma  |  Tab = Bottoni  |  Esc = Indietro",
                 bg=c["sfondo"], fg=c["puntini"], font=self._f_small).pack(fill="x", padx=_S(10), pady=(0, _S(4)))

        # Digitazione diretta -> salta alla barra ricerca
        def _tree_key_to_search(event):
            ch = event.char
            if ch and ch.isprintable() and len(ch) == 1:
                _step_search_entry.focus_set()
                _step_search_entry.insert("end", ch)
                return "break"
        step_tree.bind("<Key>", _tree_key_to_search)

        # Ctrl+F -> focus barra ricerca (case-insensitive)
        step_tree.bind("<Control-f>", lambda e: (_step_search_entry.focus_set(), "break"))
        step_tree.bind("<Control-F>", lambda e: (_step_search_entry.focus_set(), "break"))

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
            fieldbackground=c["sfondo_celle"], font=(FONT_MONO, _S(8)),
            rowheight=_S(22), borderwidth=0)
        style.configure("Retro.Treeview.Heading",
            background=c["pulsanti_sfondo"], foreground=c["pulsanti_testo"],
            font=(FONT_MONO, _S(8), "bold"), borderwidth=1, relief="ridge")
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

        # Shortcut Ctrl+D per COPIA (case-insensitive)
        self.root.bind("<Control-d>",
            lambda e: self._copia_setup(nome_tabella, _get_sel_idx()))
        self.root.bind("<Control-D>",
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
            fieldbackground=c["sfondo_celle"], font=(FONT_MONO, _S(8)),
            rowheight=_S(22), borderwidth=0)
        style.configure("Retro.Treeview.Heading",
            background=c["pulsanti_sfondo"], foreground=c["pulsanti_testo"],
            font=(FONT_MONO, _S(8), "bold"), borderwidth=1, relief="ridge")
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
        # Aprire un record esistente dall'elenco = siamo in modifica, non in nuovo.
        # Senza questo reset, se modo_nuovo era True (es. utente aveva premuto NUOVO
        # prima di andare in elenco) il successivo SALVA creava un nuovo record
        # invece di aggiornare quello aperto.
        self.modo_nuovo = False
        self.modo_ricerca = False

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
        # Ritorno alla scheda di un record esistente: non siamo in nuovo/ricerca
        self.modo_nuovo = False
        self.modo_ricerca = False
        self._costruisci_form(nome_tabella)
        self._mostra_record()

    # =========================================================================
    #  FORM CRUD
    # =========================================================================
    def _costruisci_form(self, nome_tabella):
        self._pulisci(); c = carica_colori()
        self.fields = {}; self.ref_selectors = {}

        max_label = 12
        _IA_TAG = " [IA]"  # indicatore visivo per campi analisi IA
        for campo in self.table_def.campi:
            extra = len(_IA_TAG) if campo.get("analisi_ia") else 0
            if len(campo["nome"]) + 1 + extra > max_label: max_label = len(campo["nome"]) + 1 + extra
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
        elif nome_tabella == "conf":
            # Conf aperta dal login (senza sessione): torna al login
            tk.Button(header, text="< LOGIN", font=self._f_small,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=self._schermata_login).pack(side="left")
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
            # ── INTESTAZIONE MODIFICABILE (da wizard/copia) ──
            # Riferimenti preselezionati, navigabili con tastiera (Enter = cambia)
            intestazione = tk.Frame(self._fields_inner, bg=c["sfondo"])
            intestazione.pack(fill="x", pady=(_S(1), _S(4)))
            self._ref_btns = []  # bottoni riferimento per navigazione

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
                row.pack(fill="x", pady=_S(1))
                # Bottone focusable: mostra il valore, Enter lo cambia
                btn_text = "%s: %s" % (alias.upper().replace("_", " "),
                                       desc if desc else "(non selezionato)")
                ref_btn = tk.Button(row, text=btn_text, font=self._f_label,
                    bg=c["sfondo"], fg=c["dati"], anchor="w",
                    activebackground=c["pulsanti_sfondo"],
                    activeforeground=c["cerca_testo"],
                    highlightbackground=c["linee"],
                    highlightcolor=c["cerca_testo"],
                    highlightthickness=1, relief="flat", bd=0,
                    cursor="hand2")
                ref_btn.pack(fill="x")
                # Stile focus: evidenzia quando ha il focus
                ref_btn.bind("<FocusIn>",
                    lambda e, b=ref_btn: b.config(
                        fg=c["cerca_testo"], bg=c["pulsanti_sfondo"]))
                ref_btn.bind("<FocusOut>",
                    lambda e, b=ref_btn: b.config(
                        fg=c["dati"], bg=c["sfondo"]))
                if ref_db:
                    _cmd = lambda a=alias, cr=campo_rec, db=ref_db, btn=ref_btn, r=rif: \
                        self._cambia_riferimento(a, cr, db, btn, r)
                    # Solo binding tastiera, NO command (evita doppia chiamata
                    # perche' tk.Button con command scatta su Space/Enter di default)
                    ref_btn.bind("<Return>", lambda e, fn=_cmd: (fn(), "break")[-1])
                    ref_btn.bind("<space>", lambda e, fn=_cmd: (fn(), "break")[-1])
                    ref_btn.bind("<KP_Enter>", lambda e, fn=_cmd: (fn(), "break")[-1])
                self._ref_btns.append(ref_btn)

            # Primo bottone prende il focus se stiamo duplicando
            if self._ref_btns and self.modo_nuovo:
                self.root.after(200, lambda: self._ref_btns[0].focus_set())

            # Navigazione frecce su/giu' tra riferimenti
            for i, b in enumerate(self._ref_btns):
                if i > 0:
                    prev = self._ref_btns[i - 1]
                    b.bind("<Up>", lambda e, p=prev: (p.focus_set(), "break")[-1])
                if i < len(self._ref_btns) - 1:
                    nxt = self._ref_btns[i + 1]
                    b.bind("<Down>", lambda e, n=nxt: (n.focus_set(), "break")[-1])

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
            _lbl = campo["nome"].replace("_", " ")
            if campo.get("analisi_ia"):
                _lbl += _IA_TAG
            rf = RetroField(self._fields_inner, label=_lbl, tipo=campo["tipo"],
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
        # Solo precedente/successivo: primo/ultimo record non servono e
        # tolgono spazio agli altri bottoni della barra.
        for sym, cmd in [("\u25c4 ^O", self._vai_precedente),
                         ("\u25ba ^P", self._vai_successivo)]:
            btn = tk.Button(bar, text=sym, font=self._f_nav_tab, bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      activebackground=c["pulsanti_sfondo"], activeforeground=c["pulsanti_testo"],
                      relief="ridge", bd=1, width=4, state=nav_state, pady=0)
            if nav_state == "normal":
                btn.config(cursor="hand2", command=self._flash_btn(btn, cmd))
            btn.pack(side="left", padx=_S(1))
            _bar_btns.append(btn)
        tk.Label(bar, text=" ", bg=c["sfondo"], width=1).pack(side="left")

        # Mappa operazione -> bottone (testo su una riga sola per farci stare tutti i pulsanti)
        bottoni_ops = [
            ("NUOVO ^N",    "nuovo",    "pulsanti_sfondo", "pulsanti_testo", self._nuovo),
            ("SALVA ^S",    "salva",    "pulsanti_sfondo", "pulsanti_testo", self._salva),
            ("CERCA ^F",    "cerca",    "pulsanti_sfondo", "pulsanti_testo", self._cerca),
            ("CANC ^X",     "cancella", "pulsanti_sfondo", "pulsanti_testo", self._cancella),
        ]
        # Mappa bottoni per flash da tastiera
        self._btn_map = {}
        for txt, op, bg_k, fg_k, cmd in bottoni_ops:
            abilitato = self.table_def.puo(op)
            # Monoutente: solo SALVA nella tabella utenti
            if _limite_utenti and op != "salva":
                abilitato = False
            btn = tk.Button(bar, text=txt, font=self._f_btn_tab, width=_S(8),
                      relief="ridge", bd=1, pady=0)
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
            btn.pack(side="left", padx=_S(1))
            _bar_btns.append(btn)

        # Bottone ELENCO (vista tabellare) — nascosto se monoutente
        if self.table_def.puo("elenca") and not _limite_utenti:
            tk.Label(bar, text=" ", bg=c["sfondo"], width=1).pack(side="left")
            btn_el = tk.Button(bar, text="ELENCO ^E", font=self._f_btn_tab, width=_S(8),
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      activebackground=c["pulsanti_sfondo"], activeforeground=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2", pady=0)
            btn_el.config(command=self._flash_btn(btn_el, lambda: self._schermata_elenco(nome_tabella)))
            btn_el.pack(side="left", padx=_S(1))
            _bar_btns.append(btn_el)
            self._btn_map["elenca"] = btn_el

        # Bottone COPIA (solo tabelle composite / setup)
        if self.table_def.is_composite and self.table_def.puo("nuovo"):
            btn_cp = tk.Button(bar, text="COPIA ^D", font=self._f_btn_tab, width=_S(8),
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      activebackground=c["pulsanti_sfondo"], activeforeground=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2", pady=0)
            btn_cp.config(command=self._flash_btn(btn_cp, lambda: self._copia_setup(nome_tabella,
                          self.indice_corrente if self.indice_corrente >= 0 else None)))
            btn_cp.pack(side="left", padx=_S(1))
            _bar_btns.append(btn_cp)
            self._btn_map["copia"] = btn_cp

        # Bottone CRONO (cronometraggio) e TEMPI (solo se !crono;vero nel .def + laptimer attivo)
        if self.table_def.puo("crono") and _HAS_CRONO:
            tk.Label(bar, text=" ", bg=c["sfondo"], width=1).pack(side="left")
            btn_crono = tk.Button(bar, text="CRONO ^T", font=self._f_btn_tab, width=_S(8),
                      bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                      activebackground=c["pulsanti_sfondo"], activeforeground=c["stato_avviso"],
                      relief="ridge", bd=1, cursor="hand2", pady=0)
            btn_crono.config(command=self._flash_btn(btn_crono, self._lancia_crono))
            btn_crono.pack(side="left", padx=_S(1))
            _bar_btns.append(btn_crono)
            self._btn_map["crono"] = btn_crono



        # Bottone STAMPA SCHEDA (solo se !stampa;vero nel .def + modulo stampante)
        if _HAS_THERMAL and self.table_def.puo("stampa"):
            tk.Label(bar, text=" ", bg=c["sfondo"], width=1).pack(side="left")
            # Disabilita se stampante BT non trovata (Linux)
            stampa_attiva = (not _is_linux()) or self._bt_stampante_ok or os.path.exists("/dev/rfcomm0")
            btn_stampa_scheda = tk.Button(bar, text="STAMPA ^G", font=self._f_btn_tab, width=_S(8),
                      relief="ridge", bd=1, pady=0)
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
            btn_stampa_scheda.pack(side="left", padx=_S(1))
            _bar_btns.append(btn_stampa_scheda)

        # Bottone EDITA TB: solo per la tabella conf (accesso sviluppatore)
        if nome_tabella == "conf" and _HAS_EDITOR:
            tk.Label(bar, text=" ", bg=c["sfondo"], width=1).pack(side="left")
            btn_edit_def = tk.Button(bar, text="EDITA TB", font=self._f_btn_tab, width=_S(8),
                      bg=c["pulsanti_sfondo"], fg=c["stato_errore"],
                      activebackground=c["pulsanti_sfondo"], activeforeground=c["stato_errore"],
                      relief="ridge", bd=1, cursor="hand2", pady=0)
            btn_edit_def.config(command=self._flash_btn(btn_edit_def,
                                lambda: self._lancia_editor_tabelle_dev()))
            btn_edit_def.pack(side="left", padx=_S(1))
            _bar_btns.append(btn_edit_def)

        # Navigazione tastiera sulla barra
        self._kb_setup_bottoni(_bar_btns, orizzontale=True)

        # Scorciatoie da tastiera - case-insensitive (lower + upper) per funzionare
        # anche con CapsLock attivo o Shift premuto.
        def _bind_ctrl(letter, cb):
            self.root.bind(f"<Control-{letter.lower()}>", cb)
            self.root.bind(f"<Control-{letter.upper()}>", cb)
        attivi = [b for b in _bar_btns if str(b["state"]) != "disabled"]
        if attivi:
            _bind_ctrl("b", lambda e: attivi[0].focus_set())
        _bind_ctrl("s", lambda e: self._flash_key("salva", self._salva))
        _bind_ctrl("n", lambda e: self._flash_key("nuovo", self._nuovo))
        _bind_ctrl("f", lambda e: self._flash_key("cerca", self._cerca))
        _bind_ctrl("x", lambda e: self._flash_key("cancella", self._cancella))
        if self.table_def.puo("elenca"):
            _bind_ctrl("e", lambda e: self._flash_key("elenca",
                lambda: self._schermata_elenco(nome_tabella)))
        # COPIA setup (solo tabelle composite)
        if self.table_def.is_composite and self.table_def.puo("nuovo"):
            _bind_ctrl("d", lambda e: self._flash_key("copia",
                lambda: self._copia_setup(nome_tabella,
                    self.indice_corrente if self.indice_corrente >= 0 else None)))
        # Ctrl+H = torna al primo campo (Home campi)
        primi_campi = list(self.fields.values())
        if primi_campi:
            _bind_ctrl("h", lambda e: primi_campi[0].set_focus())
        # Salta tra sezioni: PgUp/PgDown oppure Ctrl+Freccia Su/Giu
        self.root.bind("<Prior>", lambda e: self._salta_sezione(-1))           # PgUp
        self.root.bind("<Next>", lambda e: self._salta_sezione(1))             # PgDown
        self.root.bind("<Control-Up>", lambda e: self._salta_sezione(-1))      # Ctrl+Su
        self.root.bind("<Control-Down>", lambda e: self._salta_sezione(1))     # Ctrl+Giu
        # Navigazione record
        _bind_ctrl("o", lambda e: self._vai_precedente())
        _bind_ctrl("p", lambda e: self._vai_successivo())
        # LapTimer (solo se !laptimer;vero nel .def + licenza attiva)
        if self.table_def.puo("crono") and _HAS_CRONO:
            _bind_ctrl("t", lambda e: self._flash_key("crono", self._lancia_crono))
        # Stampa scheda (solo se !stampa;vero nel .def + stampante trovata)
        if _HAS_THERMAL and self.table_def.puo("stampa") and ((not _is_linux()) or self._bt_stampante_ok):
            _bind_ctrl("g", lambda e: self._flash_key("stampa", self._stampa_scheda_record))
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

    def _cambia_riferimento(self, alias, campo_rec, ref_db, label_widget, rif):
        """Selettore inline per cambiare un riferimento dopo duplicazione.
        Nasconde il form e mostra una Listbox fullscreen con filtro rapido.
        Niente Toplevel popup (troppo piccoli su uConsole)."""
        c = carica_colori()

        # Raccogli tutti i record disponibili
        ref_td = self.ref_defs.get(alias)
        if ref_td and ref_td.condiviso:
            filtro = None
        else:
            filtro = self.filtro_utente()
        indici = ref_db.get_records_filtrati(filtro) if ref_db else []
        if not indici:
            self._status("Nessun elemento in %s!" % alias, "stato_errore")
            return

        # Salva widget visibili attuali e nascondili
        self._ref_sel_hidden = []
        for child in self._vista.winfo_children():
            if child.winfo_ismapped():
                self._ref_sel_hidden.append(child)
                child.pack_forget()

        # Frame selettore inline (occupa tutta la _vista)
        sel_frame = tk.Frame(self._vista, bg=c["sfondo"])
        sel_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(6), _S(4)))
        self._ref_sel_frame = sel_frame

        # Titolo
        tk.Label(sel_frame, text="SCEGLI %s" % alias.upper().replace("_", " "),
                 bg=c["sfondo"], fg=c["cerca_testo"],
                 font=self._f_title).pack(anchor="w", pady=(_S(2), _S(4)))

        # Filtro rapido
        cerca_var = tk.StringVar()
        cerca_entry = tk.Entry(sel_frame, textvariable=cerca_var,
                               bg=c["sfondo_celle"], fg=c["dati"],
                               insertbackground=c["dati"], font=self._f_label,
                               relief="flat", highlightthickness=1,
                               highlightbackground=c["linee"],
                               highlightcolor=c["cerca_testo"])
        cerca_entry.pack(fill="x", pady=(_S(2), _S(4)))

        # Listbox fullscreen
        lb_frame = tk.Frame(sel_frame, bg=c["sfondo"])
        lb_frame.pack(fill="both", expand=True, pady=(_S(2), _S(4)))
        lb = tk.Listbox(lb_frame, font=self._f_list,
                        bg=c["sfondo_celle"], fg=c["dati"],
                        selectbackground=c["cursore"],
                        selectforeground=c["testo_cursore"],
                        highlightthickness=0, relief="flat",
                        exportselection=False)
        lb.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(lb_frame, orient="vertical", command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.configure(yscrollcommand=sb.set)

        # Status bar in basso
        status_lbl = tk.Label(sel_frame, text="\u2191\u2193 = naviga  |  Enter = conferma  |  Esc = annulla  |  digita per filtrare",
                              bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small, anchor="w")
        status_lbl.pack(fill="x", pady=(_S(2), 0))

        # Popola lista
        descrizioni = []
        for idx in indici:
            desc = ref_db.get_descrizione_record(idx)
            descrizioni.append(desc)
            lb.insert("end", desc)

        # Preseleziona il valore corrente
        current_val = self._ref_fixed.get(campo_rec, "")
        ref_k = ref_db.table_def.get_campo_chiave()
        for i, idx in enumerate(indici):
            rec = ref_db.leggi(idx)
            if rec and ref_k and str(rec.get(ref_k["nome"], "")) == current_val:
                lb.selection_set(i)
                lb.see(i)
                break

        # Indici filtrati (per filtro cerca)
        filtered_indices = list(range(len(indici)))

        def _filtra(*args):
            testo = cerca_var.get().strip().lower()
            lb.delete(0, "end")
            filtered_indices.clear()
            for i, desc in enumerate(descrizioni):
                if not testo or testo in desc.lower():
                    lb.insert("end", desc)
                    filtered_indices.append(i)
            if lb.size() > 0:
                lb.selection_set(0)
                lb.see(0)

        cerca_var.trace_add("write", _filtra)

        def _ripristina_form(focus_btn=True):
            """Rimuove il selettore e ricostruisce il form.
            Ricostruire e' piu' sicuro che fare pack/unpack."""
            sel_frame.destroy()
            self._ref_sel_hidden = []
            # Ricostruisci form completo (come fa _mostra_record)
            nome_tab = self._nome_tabella
            rec_idx = self._pos_visibile
            self._costruisci_form(nome_tab)
            self._mostra_record()
            # Focus sul bottone del riferimento appena cambiato
            if focus_btn and hasattr(self, '_ref_btns') and self._ref_btns:
                for b in self._ref_btns:
                    txt = b.cget("text").lower()
                    if alias.lower().replace("_", " ") in txt:
                        self.root.after(100, lambda bb=b: bb.focus_set())
                        break

        def _conferma(e=None):
            sel = lb.curselection()
            if not sel:
                _ripristina_form()
                return
            # Mappa selezione filtrata -> indice reale
            filt_idx = sel[0]
            if filt_idx < len(filtered_indices):
                real_list_idx = filtered_indices[filt_idx]
            else:
                real_list_idx = filt_idx
            real_db_idx = indici[real_list_idx]
            rec = ref_db.leggi(real_db_idx)
            if rec and ref_k:
                new_key = str(rec.get(ref_k["nome"], ""))
                # Aggiorna _ref_fixed PRIMA di ricostruire il form
                self._ref_fixed[campo_rec] = new_key
                # Aggiorna anche _rif_preselezionati con il nuovo indice
                self._rif_preselezionati[alias] = real_db_idx
                self._status("Cambiato %s" % alias, "stato_ok")
            _ripristina_form()

        lb.bind("<Return>", _conferma)
        cerca_entry.bind("<Return>", lambda e: (lb.focus_set(), "break")[-1])
        cerca_entry.bind("<Down>", lambda e: (lb.focus_set(), "break")[-1])
        cerca_entry.bind("<Escape>", lambda e: _ripristina_form())
        lb.bind("<Escape>", lambda e: _ripristina_form())

        # Tab: cerca -> listbox -> cerca
        cerca_entry.bind("<Tab>", lambda e: (lb.focus_set(), "break")[-1])
        lb.bind("<Tab>", lambda e: (cerca_entry.focus_set(), "break")[-1])

        # Focus iniziale sulla cerca
        cerca_entry.focus_set()

        cerca_entry.focus_set()

    def _aggiorna_contatore(self, curr, tot):
        self._label_contatore.config(text="Rec %d/%d" % (curr, tot) if tot else "Nessun record")

    def _status(self, msg, color_key="testo_dim"):
        c = carica_colori()
        self._label_status.config(text=" %s" % msg, fg=c.get(color_key, c["testo_dim"]))

    def _flash_btn(self, btn, cmd):
        """Ritorna un comando wrappato con flash rosso (delega a ui_bottoni)."""
        if _HAS_UI_BTN:
            return _ui_flash_btn(self.root, btn, cmd)
        # Fallback
        def _wrapper():
            try:
                orig_bg = btn.cget("bg"); orig_fg = btn.cget("fg")
                btn.config(bg="#ff0000", fg="#ffffff")
                self.root.after(150, lambda: (btn.config(bg=orig_bg, fg=orig_fg), cmd()))
            except Exception:
                cmd()
        return _wrapper

    def _flash_key(self, op, cmd):
        """Flash bottone per nome operazione (delega a ui_bottoni)."""
        if _HAS_UI_BTN:
            _ui_flash_key(self.root, getattr(self, '_btn_map', {}), op, cmd)
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

    def _vai_precedente(self):
        if self._pos_visibile > 0: self._pos_visibile -= 1
        self.modo_nuovo = False; self._mostra_record()

    def _vai_successivo(self):
        if self._pos_visibile < len(self._indici_visibili) - 1: self._pos_visibile += 1
        self.modo_nuovo = False; self._mostra_record()

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

        # Utenti: controlli admin
        if self._nome_tabella == "utenti":
            _ADMIN_VALS = ("1", "X", "x", "S", "V", "si", "vero", "true")
            is_adm = str(dati.get("Admin", "")).strip() in _ADMIN_VALS
            email_utente = str(dati.get("Email", "")).strip()
            if is_adm and not email_utente:
                self._status("Email obbligatoria per Admin!", "stato_errore")
                if "Email" in self.fields:
                    self.fields["Email"].set_focus()
                return
            # Protezione ultimo admin: se sto togliendo il flag Admin,
            # verifico che resti almeno un altro admin nel sistema
            if not is_adm and not self.modo_nuovo and self.indice_corrente >= 0:
                rec_attuale = self.db.records[self.indice_corrente]
                era_admin = str(rec_attuale.get("Admin", "")).strip() in _ADMIN_VALS
                if era_admin:
                    # Conta quanti admin ci sono (escluso questo record)
                    altri_admin = 0
                    for i, u in enumerate(self.db.records):
                        if i == self.indice_corrente:
                            continue
                        if str(u.get("Admin", "")).strip() in _ADMIN_VALS:
                            # Verifica che sia un utente valido (con username e password)
                            if u.get("Username", "").strip() and u.get("Password", "").strip():
                                altri_admin += 1
                    if altri_admin == 0:
                        self._status("Deve esserci almeno un Admin!", "stato_errore")
                        return

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
            # Tabella conf: dopo salvataggio, sincronizza conf.dat e applica
            if self._nome_tabella == "conf":
                self._applica_conf()
                self._status("Configurazione salvata!", "stato_ok")
                return
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
        # Protezione ultimo admin: non cancellare l'ultimo admin
        if self._nome_tabella == "utenti":
            _ADMIN_VALS = ("1", "X", "x", "S", "V", "si", "vero", "true")
            rec = self.db.records[self.indice_corrente]
            if str(rec.get("Admin", "")).strip() in _ADMIN_VALS:
                altri_admin = 0
                for i, u in enumerate(self.db.records):
                    if i == self.indice_corrente:
                        continue
                    if str(u.get("Admin", "")).strip() in _ADMIN_VALS:
                        if u.get("Username", "").strip() and u.get("Password", "").strip():
                            altri_admin += 1
                if altri_admin == 0:
                    self._status("Impossibile: e' l'ultimo Admin!", "stato_errore")
                    return
        # Doppia pressione per confermare
        import time
        now = time.time()
        if not hasattr(self, '_canc_time') or now - self._canc_time > 3:
            self._canc_time = now
            self._status("Premi CANCELLA di nuovo per confermare!", "stato_errore"); return
        del self._canc_time
        # Prima di cancellare: elimina file tempi associati al record
        self._elimina_file_tempi(self.indice_corrente)
        self.db.cancella(self.indice_corrente)
        self.modo_ricerca = False; self.risultati_ricerca = []; self._aggiorna_visibili()
        if not self._indici_visibili: self._pos_visibile = -1
        elif self._pos_visibile >= len(self._indici_visibili):
            self._pos_visibile = len(self._indici_visibili) - 1
        self._mostra_record(); self._status("Cancellato.", "stato_ok")

    def _pulisci(self):
        try: self.root.unbind_all("<MouseWheel>")
        except: pass
        for key in ("<Return>", "<Escape>",
                    # Ctrl+lettera: unbind di entrambe le varianti (lower + upper)
                    # per coerenza con i bind case-insensitive di _kb_bottoni
                    "<Control-b>", "<Control-B>",
                    "<Control-s>", "<Control-S>",
                    "<Control-n>", "<Control-N>",
                    "<Control-f>", "<Control-F>",
                    "<Control-e>", "<Control-E>",
                    "<Control-x>", "<Control-X>",
                    "<Control-p>", "<Control-P>",
                    "<Control-o>", "<Control-O>",
                    "<Control-d>", "<Control-D>",
                    "<Control-t>", "<Control-T>",
                    "<Control-r>", "<Control-R>",
                    "<Control-h>", "<Control-H>",
                    "<Control-g>", "<Control-G>",
                    "<Control-q>", "<Control-Q>",
                    "<Prior>", "<Next>", "<Control-Up>", "<Control-Down>",
                    "<Button-1>", "<Key>"):
            try: self.root.unbind(key)
            except: pass
        # Sospendi focus visivo durante transizione per evitare
        # che bind_class FocusIn/Out sporchi i colori dei bottoni
        sospendi_focus(True)
        pulisci_cache()
        # Distrugge contenuto e ricrea frame schermata sopra _base
        c = carica_colori()
        self._base.configure(bg=c["sfondo"])
        if self._vista and self._vista.winfo_exists():
            self._vista.destroy()
        self._vista = tk.Frame(self._base, bg=c["sfondo"])
        self._vista.place(x=0, y=0, relwidth=1, relheight=1)
        self._kb_original_colors = {}
        # Riattiva focus visivo (i nuovi widget partiranno puliti)
        sospendi_focus(False)

    def _rimuovi_coperta(self):
        """Compatibilita': non serve piu' con il frame fisso."""
        pass


# =============================================================================
#  AVVIO
# =============================================================================
def _applica_dpi_windows():
    """Su Windows rende il processo DPI-aware e neutralizza lo scaling Tk,
    cosi' la geometria in conf.dat produce pixel fisici 1:1 come su uConsole.
    Su Linux/uConsole non fa nulla (il DPI e' gia' 96 e Tk lavora 1:1).

    Ritorna True se il fix e' stato applicato (utile per diagnostica).
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        # PROCESS_PER_MONITOR_DPI_AWARE (2) su Win 8.1+; fallback a SYSTEM (1)
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                # Fallback legacy per Windows 7 / Vista
                ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


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
    # Windows: allinea DPI/scaling a uConsole PRIMA di creare il Tk root
    _applica_dpi_windows()
    root = tk.Tk()
    # Forza scaling Tk a 1:1 su Windows (su Linux/uConsole non serve ma
    # nemmeno disturba: rende il rendering identico sui due sistemi).
    if sys.platform == "win32":
        try:
            root.tk.call('tk', 'scaling', 1.0)
        except Exception:
            pass
    root.withdraw()  # Nascondi finestra durante init per evitare flash bianco
    c = carica_colori()
    root.configure(bg=c["sfondo"])
    root.update_idletasks()
    app = RetroDBApp(root)
    root.deiconify()  # Mostra finestra solo quando tutto e' pronto
    # uConsole: proteggi TrackMind da popup di sistema (NetworkManager, keyring,
    # Bluetooth pairing, avvisi batteria, polkit). Mantiene la finestra sopra.
    _proteggi_finestra(root)
    root.mainloop()

if __name__ == "__main__":
    main()
