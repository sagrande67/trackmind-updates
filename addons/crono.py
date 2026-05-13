"""
TrackMind - Crono v2.0
Hub cronometraggi TrackMind. Gestisce TUTTO cio' che riguarda tempi:
  - CRONOMETRA: acquisizione manuale con LapTimer
  - SPEEDHIVE: import tempi da API SpeedHive
  - TEMPI: revisione sessioni salvate + analisi + strategia
  - SCOUTING: cronometraggio libero senza setup

Lanciato da retrodb.py in modalita' embedded (parent frame + on_close).
Riceve un dizionario 'contesto' con dati dal record setup corrente.
Se contesto e' None, parte direttamente in modalita' scouting.

contesto = {
    "dati_dir": str,          # Cartella dati principale
    "record_id": str,         # ID record setup (per trovare sessioni)
    "setup_name": str,        # Nome leggibile del setup
    "pilota": str,            # Nome pilota (dalla sessione)
    "pista": str,             # Nome pista (dal riferimento)
    "data": str,              # Data dal record
    "ora": str,               # Ora dal record
    "transponder": str,       # Transponder utente (per SpeedHive)
    "speedhive_id": str,      # ID pista SpeedHive (dal riferimento piste)
    # Riferimenti setup (per analisi IA)
    "ref_telai": str,         # Descrizione telaio (es. "Mugen MBX8")
    "ref_motori": str,        # Descrizione motore (es. "OS Speed T1204")
    "ref_miscela": str,       # Descrizione miscela (es. "Byron 25%")
    "ref_gomma_anteriore": str, # Descrizione gomma ant (es. "Jetko Marco 39")
    "ref_gomma_posteriore": str, # Descrizione gomma post
}
"""

from version import __version__

import tkinter as tk
from tkinter import font as tkfont, ttk
import json, os, sys, time, threading, socket
from datetime import datetime

# Helper centralizzato per la cartella scouting (struttura ad albero
# <anno>/<pista>/, vedi core/scouting_paths.py).
try:
    from core import scouting_paths as _scouting_paths
except ImportError:
    _ROOT_TM = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                            os.pardir))
    if _ROOT_TM not in sys.path:
        sys.path.insert(0, _ROOT_TM)
    from core import scouting_paths as _scouting_paths

# Guardia anti-popup di sistema (uConsole): blocca dialog di NetworkManager,
# keyring, Bluetooth, batteria, polkit sopra al cronometro.
try:
    from core.focus_guard import proteggi_finestra_sicura as _proteggi_finestra
except Exception:
    try:
        import os as _os, sys as _sys
        _here = _os.path.dirname(_os.path.abspath(__file__))
        _parent = _os.path.dirname(_here)
        if _parent not in _sys.path:
            _sys.path.insert(0, _parent)
        from core.focus_guard import proteggi_finestra_sicura as _proteggi_finestra
    except Exception:
        def _proteggi_finestra(root, **kwargs):
            return


def _check_internet(timeout=3):
    """Check rapido connettivita' internet (DNS Google).
    Ritorna True se connesso, False altrimenti."""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=timeout).close()
        return True
    except (OSError, socket.timeout):
        return False

# Font monospace + helper colori centralizzati
try:
    from config_colori import FONT_MONO, carica_colori as _carica_colori
except ImportError:
    FONT_MONO = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"
    def _carica_colori():
        return {}

# ── Import componenti TrackMind ──
try:
    from tm_field import RetroField
except ImportError:
    RetroField = None

try:
    from laptimer import LapTimer, classifica_giri
except ImportError:
    LapTimer = None
    classifica_giri = None

try:
    from analizza_tempi import AnalizzaTempi
except ImportError:
    AnalizzaTempi = None

try:
    from speedhive_import import (scarica_sessioni, converti_sessione,
                                   import_automatico, cerca_attivita)
    _HAS_SPEEDHIVE = True
except ImportError:
    _HAS_SPEEDHIVE = False

try:
    from confronta_setup import ConfrontaSetup
    _HAS_CONFRONTA = True
except ImportError:
    _HAS_CONFRONTA = False

try:
    from myrcm_import import (scarica_tutti_tempi_evento, crea_scouting_json,
                               salva_scouting, import_evento_completo,
                               pulisci_scouting_data_vecchia)
    _HAS_MYRCM = True
except ImportError:
    _HAS_MYRCM = False

# Barra batteria (opzionale: se il modulo non c'e', si ignora)
try:
    from core.batteria import aggiungi_barra_batteria as _aggiungi_barra_bat
except Exception:
    def _aggiungi_barra_bat(*args, **kwargs):
        return None


def _fmt(sec):
    """MM:SS.cc"""
    if not sec: return "--"
    m = int(sec) // 60; s = sec - m * 60
    return "%02d:%05.2f" % (m, s)


def _data_ita(d):
    """Normalizza data a formato italiano GG/MM/AAAA.
    Accetta: AAAA-MM-GG, GG/MM/AAAA, GG-MM-AAAA, MM/GG/AAAA (auto-detect)."""
    if not d or not isinstance(d, str):
        return d or "?"
    d = d.strip()
    # Formato ISO: AAAA-MM-GG
    if len(d) == 10 and d[4] == "-":
        return "%s/%s/%s" % (d[8:10], d[5:7], d[0:4])
    # Formato con /
    if "/" in d:
        parti = d.split("/")
        if len(parti) == 3:
            try:
                p0, p1 = int(parti[0]), int(parti[1])
                # Se primo campo > 12, sicuramente e' il giorno -> gia' DD/MM/AAAA
                # Se secondo campo > 12, e' MM/DD/AAAA -> invertire
                if p1 > 12 and p0 <= 12:
                    return "%s/%s/%s" % (parti[1], parti[0], parti[2])
            except ValueError:
                pass
        return d
    # GG-MM-AAAA
    if len(d) == 10 and d[2] == "-":
        return d.replace("-", "/")
    return d


# =====================================================================
#  CLASSE PRINCIPALE: Crono v2.0
# =====================================================================
class Crono:
    """Hub cronometraggi TrackMind."""

    def __init__(self, parent=None, on_close=None, contesto=None,
                 on_apri_setup=None):
        self._on_close = on_close
        # Callback opzionale per "VAI AL SETUP" dalla riga di una
        # sessione: retrodb riceve l'indice del record di setup da
        # aprire e ricostruisce la scheda. Se None la funzione non
        # e' disponibile.
        self._on_apri_setup = on_apri_setup
        self._embedded = parent is not None
        self.ctx = contesto or {}
        self.dati_dir = self.ctx.get("dati_dir", "")

        self.c = _carica_colori()
        self._init_root(parent)
        self._init_fonts()
        self._top = self.root.winfo_toplevel()

        # Carica dati piste per matching scouting
        self._piste_data = []
        self._load_piste()

        # Se ho un contesto setup → menu hub, altrimenti → hub libero
        if self.ctx.get("record_id"):
            self._schermata_hub()
        else:
            self._schermata_hub_libero()

    def _init_root(self, parent=None):
        if parent:
            self.root = parent
        else:
            self.root = tk.Tk()
            self.root.title(f"TrackMind - Crono  v{__version__}")
            self.root.attributes("-fullscreen", True)
        self.root.configure(bg=self.c["sfondo"])
        # Protezione popup di sistema (uConsole): idempotente, sicura anche
        # quando ereditiamo la root dal parent (retrodb.py).
        _proteggi_finestra(self.root)

    def _init_fonts(self):
        self._f_title  = tkfont.Font(family=FONT_MONO, size=14, weight="bold")
        self._f_info   = tkfont.Font(family=FONT_MONO, size=12)
        self._f_list   = tkfont.Font(family=FONT_MONO, size=11)
        self._f_btn    = tkfont.Font(family=FONT_MONO, size=11, weight="bold")
        self._f_small  = tkfont.Font(family=FONT_MONO, size=10)
        self._f_status = tkfont.Font(family=FONT_MONO, size=10)
        self._f_hub    = tkfont.Font(family=FONT_MONO, size=13, weight="bold")

    def _load_piste(self):
        """Carica dati piste da JSON per matching nel scouting."""
        if not self.dati_dir:
            return
        piste_json = os.path.join(self.dati_dir, "piste.json")
        if not os.path.exists(piste_json):
            return
        try:
            with open(piste_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            records = data.get("records", []) if isinstance(data, dict) else data
            for r in records:
                nome = r.get("Nome_Pista", r.get("Nome", "")).strip()
                if nome:
                    self._piste_data.append({
                        "nome": nome,
                        "codice": str(r.get("Codice_Pista", "")).strip(),
                        "speedhive_id": str(r.get("SpeedHive_ID", "")).strip(),
                        "citta": str(r.get("Citta", "")).strip(),
                        "indirizzo": str(r.get("Indirizzo", "")).strip(),
                        "nazione": str(r.get("Nazione", "")).strip(),
                    })
        except Exception:
            pass

    @staticmethod
    def _normalizza_data(d):
        """Normalizza data a DD/MM/YYYY da qualsiasi formato."""
        d = d.strip() if d else ""
        if not d:
            return ""
        if "-" in d and len(d) == 10:
            p = d.split("-")
            return "%s/%s/%s" % (p[2], p[1], p[0])
        if "." in d:
            return d.replace(".", "/")
        return d

    @staticmethod
    def _pista_da_sessione(dati):
        """Estrai nome pista da una sessione (campo pista o setup)."""
        pista = dati.get("pista", "").strip()
        if pista:
            return pista
        setup = dati.get("setup", "")
        if " - " in setup:
            return setup.split(" - ", 1)[1].strip()
        return ""

    @staticmethod
    def _identifica_pit(giri, soglia=1.4):
        """Identifica i pit-stop in una lista di giri.

        Ritorna lista di indici (0-based) dei giri che sono pit.
        Combinazione di:
          1) stato esplicito 'pit' o 'incidente' (LapTimer manuale,
             quando l'utente preme P o I durante la corsa)
          2) euristica: tempo > soglia x mediana dei tempi (default
             1.4x). La mediana e' robusta agli outlier - usare la
             media verrebbe inquinata proprio dai pit. Cosi' anche
             SpeedHive/MyRCM/LapMonitor (che arrivano sempre con
             stato='valido') vengono analizzati."""
        if not giri:
            return []
        pit_idx = set()
        # 1) Stato esplicito
        for i, g in enumerate(giri):
            st = (g.get("stato", "") or "").lower()
            if st in ("pit", "incidente"):
                pit_idx.add(i)
        # 2) Euristica per tutti i giri rimanenti
        tempi = [g.get("tempo", 0) for g in giri]
        tempi_norm = [t for i, t in enumerate(tempi)
                      if t > 0 and i not in pit_idx]
        if len(tempi_norm) >= 3:
            ts = sorted(tempi_norm)
            mediana = ts[len(ts) // 2]
            soglia_pit = mediana * soglia
            for i, t in enumerate(tempi):
                if t > soglia_pit:
                    pit_idx.add(i)
        return sorted(pit_idx)

    # ── Registro piloti persistente (piloti.json) ──

    def _piloti_path(self):
        """Percorso file piloti.json (nella cartella dati)."""
        if self.dati_dir:
            return os.path.join(self.dati_dir, "piloti.json")
        return None

    def _load_piloti(self):
        """Carica registro piloti. Ritorna dict {nome_lower: {nome, transponder, serbatoio, note}}."""
        path = self._piloti_path()
        if not path or not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                lista = json.load(f)
            registro = {}
            for p in lista:
                nome = p.get("nome", "").strip()
                if nome:
                    registro[nome.lower()] = {
                        "nome": nome,
                        "transponder": str(p.get("transponder", "")).strip(),
                        "serbatoio": str(p.get("serbatoio", "")).strip(),
                        "note": str(p.get("note", "")).strip(),
                    }
            return registro
        except Exception:
            return {}

    def _save_pilota(self, nome, transponder="", serbatoio="", note=""):
        """Aggiunge/aggiorna un pilota nel registro. Non sovrascrive campi con valori vuoti."""
        path = self._piloti_path()
        if not path or not nome.strip():
            return
        registro = self._load_piloti()
        key = nome.strip().lower()
        esistente = registro.get(key, {})
        registro[key] = {
            "nome": nome.strip(),
            "transponder": transponder.strip() or esistente.get("transponder", ""),
            "serbatoio": serbatoio.strip() or esistente.get("serbatoio", ""),
            "note": note.strip() or esistente.get("note", ""),
        }
        # Salva lista ordinata per nome
        lista = sorted(registro.values(), key=lambda p: p["nome"].lower())
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(lista, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _build_alias_per_trasponder(self):
        """Indice {transponder_str: nome} unificato da DUE fonti:
          1. piloti.json - registro alias manuali (ALIAS dal CRONO)
          2. trasponder.json - tabella partecipanti importata da
             MyRCM dall'addon Assistente Gara

        Usato sia all'import SpeedHive (per dare il nome ai record
        anonimi tipo "Trasp. 12345"), sia in TUTTI I TEMPI (per
        sostituire al volo i nomi anonimi nelle sessioni gia'
        salvate, senza riscrivere i file).

        Priorita': piloti.json vince su trasponder.json se entrambi
        hanno il transponder (cosi' un ALIAS manuale recente non
        viene sovrascritto da un nome MyRCM)."""
        idx = {}
        # 1. trasponder.json (caricato per primo cosi' piloti.json
        #    sovrascrive in caso di collisione)
        try:
            base = (self.dati_dir
                    if self.dati_dir
                    else os.path.dirname(os.path.abspath(__file__)))
            tr_path = os.path.join(base, "trasponder.json")
            if os.path.exists(tr_path):
                with open(tr_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Formato TrackMind: {_meta, records}
                records = (data.get("records", [])
                           if isinstance(data, dict) else (data or []))
                for r in records:
                    t = str(r.get("Numero", "") or "").strip()
                    n = str(r.get("Pilota", "") or "").strip()
                    if t and n:
                        idx[t] = n
        except Exception:
            pass
        # 2. piloti.json (override manuale ALIAS)
        try:
            registro = self._load_piloti()
            for p in registro.values():
                t = (p.get("transponder") or "").strip()
                n = (p.get("nome") or "").strip()
                if t and n:
                    idx[t] = n
        except Exception:
            pass
        return idx

    def _match_pista(self, testo):
        """Cerca match pista per testo parziale. Ritorna dict pista o None.
        Restituisce match solo se univoco. Se ambiguo ritorna None
        (i candidati multipli vengono mostrati dalla label sotto).

        Disambiguazione "prefisso principale": se i candidati hanno
        un capostipite (es. "Mycandy Arena") e gli altri sono varianti
        con suffisso aggiuntivo ("Mycandy Arena Backup loop"), preferisci
        il capostipite (la pista principale, non le varianti)."""
        if not testo or len(testo) < 2:
            return None
        testo = testo.lower().strip()
        candidati = []
        for p in self._piste_data:
            nome_low = p["nome"].lower()
            if testo == nome_low:
                # Match esatto: ritorna subito
                return p
            if nome_low.startswith(testo) or testo in nome_low:
                candidati.append(p)
        if len(candidati) == 1:
            return candidati[0]
        if len(candidati) > 1:
            # Cerca il "capostipite": il candidato piu' corto, se gli
            # altri iniziano tutti con "<capostipite> " (cioe' sono
            # sue varianti tipo "Mycandy Arena Backup loop"). Cosi'
            # con sync recenti che aggiungono varianti, l'autocomplete
            # continua a puntare alla pista principale.
            ord_per_lung = sorted(candidati, key=lambda c: len(c["nome"]))
            base_low = ord_per_lung[0]["nome"].lower()
            base_pref = base_low + " "
            tutti_varianti = all(
                (c["nome"].lower() == base_low
                 or c["nome"].lower().startswith(base_pref))
                for c in ord_per_lung[1:])
            if tutti_varianti:
                return ord_per_lung[0]
        # Piu' candidati genuinamente diversi: nessun match
        # (label mostra i suggerimenti).
        self._match_candidati = candidati
        return None

    def _safe_focus(self, widget):
        """Focus sicuro: ignora se il widget e' gia' stato distrutto."""
        try:
            widget.set_focus()
        except (tk.TclError, AttributeError):
            pass

    def _pulisci(self):
        # Ferma polling autocomplete prima di distruggere widget
        self._scouting_attivo = False
        # Ferma il timer di refresh automatico TUTTI I TEMPI (se
        # attivo): evita che un after() pendente si esegua dopo che
        # la schermata e' gia' stata distrutta.
        self._at_refresh_attivo = False
        for w in self.root.winfo_children():
            w.destroy()
        for k in ("<Return>", "<Escape>", "<Up>", "<Down>",
                  "<Left>", "<Right>", "<Prior>", "<Next>", "<Home>",
                  "<plus>", "<equal>", "<minus>",
                  "<KP_Add>", "<KP_Subtract>", "<0>",
                  "<e>", "<p>", "<v>", "<a>", "<s>",
                  "<l>", "<L>", "<g>", "<G>", "<P>",
                  "<t>", "<T>",
                  "<space>", "<r>", "<R>",
                  "<Control-a>", "<Control-A>",
                  "<Control-f>", "<Control-F>"):
            try: self._top.unbind(k)
            except: pass
            try: self.root.unbind(k)
            except: pass
        # Ferma animazione Ghost se attiva
        self._ghost_running = False

    def _chiudi(self):
        if self._on_close:
            self._pulisci()
            self._on_close()
        elif not self._embedded:
            self.root.destroy()

    def _status_label(self, parent, text="", fg=None):
        """Crea e ritorna una label di status."""
        lbl = tk.Label(parent, text=text, bg=self.c["sfondo"],
                       fg=fg or self.c["testo_dim"], font=self._f_status, anchor="w")
        lbl.pack(fill="x", padx=10, pady=(0, 4))
        return lbl

    # =================================================================
    #  1. MENU HUB (schermata principale con contesto setup)
    # =================================================================
    def _schermata_hub(self):
        self._pulisci()
        c = self.c

        # Header
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< SCHEDA", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._chiudi).pack(side="left")
        pilota = self.ctx.get("pilota", "?")
        setup = self.ctx.get("setup_name", "?")
        tk.Label(header, text="  CRONO v%s  |  %s  |  %s" % (__version__, pilota, setup),
                 bg=c["sfondo"], fg=c["dati"], font=self._f_title).pack(side="left", padx=(8, 0))
        # Barra batteria in alto a destra (overlay)
        _aggiungi_barra_bat(header)

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(6, 10))

        # Menu voci
        menu_frame = tk.Frame(self.root, bg=c["sfondo"])
        menu_frame.pack(padx=20, pady=10)

        # SCHERMATA SETUP - ridotta alle 2 azioni dedicate al setup:
        #   1) CRONOMETRA: apre LapTimer (manuale + import da SpeedHive,
        #      MyRCM, LapMonitor) in modalita' setup, salvando i tempi
        #      con riferimento al record corrente.
        #   2) CONFRONTA: passa i tempi della sessione al Dr. IA per
        #      proporre modifiche al setup basate sui giri reali.
        #
        # Le altre voci (TEMPI, TUTTI I TEMPI, SCOUTING) sono
        # raggiungibili dal menu principale di Crono fuori dal setup.
        btns = []
        descs = []
        voci = [
            ("CRONOMETRA",
             "Apri LapTimer (manuale, SpeedHive, MyRCM, LapMonitor)",
             self._avvia_cronometro),
            ("CONFRONTA",
             "Analizza tempi sessione con DR. IA per migliorare setup",
             self._lancia_confronta),
            ("PULISCI",
             "Cancella sessioni mirate (filtro Fonte/Data/Giri)",
             self._lancia_pulisci_setup),
        ]

        _fg_norm = c["pulsanti_testo"]   # verde chiaro
        _bg_norm = c["pulsanti_sfondo"]  # verde scuro
        _fg_sel  = c["sfondo"]           # nero (sfondo app)
        _bg_sel  = c["dati"]             # verde brillante

        for nome, desc, cmd in voci:
            row = tk.Frame(menu_frame, bg=c["sfondo"])
            row.pack(fill="x", pady=4)
            b = tk.Button(row, text=nome, font=self._f_hub, width=16,
                          bg=_bg_norm, fg=_fg_norm,
                          activebackground=_bg_sel, activeforeground=_fg_sel,
                          relief="ridge", bd=2, cursor="hand2", command=cmd)
            b.pack(side="left", padx=(0, 12))
            lbl = tk.Label(row, text=desc, bg=c["sfondo"], fg=c["testo_dim"],
                     font=self._f_small, anchor="w")
            lbl.pack(side="left")
            btns.append(b)
            descs.append(lbl)

        # Navigazione tastiera verticale
        # Focus visivo bottoni gestito dal binding globale di retrodb (_kb_focus_evidenzia)
        # Qui gestiamo solo l'illuminazione della label descrizione
        for i, b in enumerate(btns):
            b.bind("<FocusIn>", lambda e, l=descs[i]: l.config(fg=c["dati"]), add="+")
            b.bind("<FocusOut>", lambda e, l=descs[i]: l.config(fg=c["testo_dim"]), add="+")
            if i < len(btns) - 1:
                b.bind("<Down>", lambda e, n=btns[i+1]: (n.focus_set(), "break")[-1])
            if i > 0:
                b.bind("<Up>", lambda e, p=btns[i-1]: (p.focus_set(), "break")[-1])

        # ── Lista sessioni cronometrate per questo setup ──
        # Recupera record corrente (necessario al lookup)
        self._hub_tempi_sel_paths = []
        try:
            db_hub = self.ctx.get("_db")
            rec_id_hub = self.ctx.get("record_id", "")
            rec_hub = self.ctx.get("_record")
            if not rec_hub and db_hub and rec_id_hub.startswith("id_"):
                target = rec_id_hub[3:]
                for idx in self.ctx.get("_indici_visibili", []):
                    r = db_hub.leggi(idx)
                    if r and str(r.get("_id", "")) == target:
                        rec_hub = r
                        break
            files_record = (self._file_tempi_del_record(rec_hub, rec_id_hub)
                            if rec_hub else [])
        except Exception:
            files_record = []

        if files_record:
            tk.Frame(self.root, bg=c["linee"], height=1).pack(
                fill="x", padx=10, pady=(8, 4))
            n_questo = sum(1 for fr in files_record
                           if fr.get("e_questo_setup"))
            tk.Label(self.root,
                     text=("SESSIONI SU QUESTA PISTA (%d, di cui %d "
                           "del setup corrente %s)  -  Frecce per "
                           "scorrere, SPAZIO per selezionare, "
                           "V per VAI AL SETUP della riga, "
                           "INVIO per CONFRONTA"
                           % (len(files_record), n_questo,
                              chr(0x25CF))),
                     bg=c["sfondo"], fg=c["cerca_testo"],
                     font=self._f_btn,
                     anchor="w").pack(anchor="w", padx=20, pady=(2, 2))

            # Stile Treeview retrodb (lo stesso usato in TUTTI I TEMPI)
            style = ttk.Style()
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure("TT.Treeview",
                background=c["sfondo_celle"], foreground=c["dati"],
                fieldbackground=c["sfondo_celle"],
                font=(FONT_MONO, 10), rowheight=22, borderwidth=0)
            style.configure("TT.Treeview.Heading",
                background=c["pulsanti_sfondo"],
                foreground=c["pulsanti_testo"],
                font=(FONT_MONO, 10, "bold"),
                borderwidth=1, relief="ridge")
            style.map("TT.Treeview",
                background=[("selected", c["cursore"])],
                foreground=[("selected", c["testo_cursore"])])

            tf = tk.Frame(self.root, bg=c["sfondo"])
            tf.pack(fill="both", expand=True, padx=20, pady=(0, 4))
            cols = ("data", "ora", "setup", "fonte",
                    "giri", "best", "media")
            # Frame ampio: cresce con la finestra fino a ~18 righe.
            # Le frecce navigano, SPAZIO toggla la selezione della
            # riga corrente per costruire la multi-selezione senza mouse.
            tree = ttk.Treeview(tf, columns=cols, show="headings",
                                style="TT.Treeview",
                                height=min(18, len(files_record)) or 1,
                                selectmode="extended")
            larghezze = {"data": 95, "ora": 55, "setup": 200,
                         "fonte": 80, "giri": 50,
                         "best": 80, "media": 80}
            titoli = {"data": "Data", "ora": "Ora", "setup": "Setup",
                      "fonte": "Fonte", "giri": "Giri",
                      "best": "Best", "media": "Media"}
            for col in cols:
                tree.heading(col, text=titoli[col], anchor="w")
                tree.column(col, width=larghezze[col],
                            anchor="w" if col in ("data", "ora",
                                                  "setup", "fonte")
                            else "e")
            sb = tk.Scrollbar(tf, orient="vertical",
                              command=tree.yview)
            sb.pack(side="right", fill="y")
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            # Map fonte JSON -> etichetta amichevole italiana
            _FONTE_LABEL = {
                "laptimer":  "Manuale",
                "lapmonitor": "Manuale",  # cron. con tasto Spazio
                "speedhive": "SpeedHive",
                "myrcm":     "MyRCM",
                "scouting":  "Manuale",
            }

            def _data_eu(s):
                """Converte una data in formato europeo GG/MM/AAAA.
                Accetta sia ISO 'YYYY-MM-DD' che gia' 'DD/MM/YYYY'."""
                s = (str(s or "")).strip()
                if not s:
                    return ""
                if "-" in s and len(s) >= 10:
                    try:
                        y, m, d = s[:10].split("-")
                        return "%02d/%02d/%s" % (int(d), int(m), y)
                    except (ValueError, IndexError):
                        return s
                if "/" in s:
                    parti = s.split("/")
                    if len(parti) == 3:
                        try:
                            d, m, y = parti
                            if len(y) == 2:
                                y = "20" + y
                            return "%02d/%02d/%s" % (int(d), int(m), y)
                        except ValueError:
                            return s
                return s

            # Tag per evidenziare le righe del setup CORRENTE
            try:
                tree.tag_configure(
                    "setup_corrente",
                    foreground=c.get("stato_ok", "#39ff14"))
            except tk.TclError:
                pass

            # Map iid -> path
            iid_to_path = {}
            for fr in files_record:
                best_str = ("%.3f" % fr["best"]) if fr["best"] else "-"
                media_str = ("%.3f" % fr["media"]) if fr["media"] else "-"
                fonte_lbl = _FONTE_LABEL.get(
                    str(fr["fonte"]).lower(), fr["fonte"] or "?")
                data_eu = _data_eu(fr["data"])
                # Marker dot per le sessioni del setup corrente
                setup_txt = fr.get("setup_label", "") or ""
                if fr.get("e_questo_setup"):
                    setup_txt = chr(0x25CF) + " " + setup_txt
                row_tags = ("setup_corrente",) if fr.get(
                    "e_questo_setup") else ()
                iid = tree.insert(
                    "", "end",
                    values=(data_eu, fr["ora"][:5], setup_txt,
                            fonte_lbl, fr["num_giri"],
                            best_str, media_str),
                    tags=row_tags)
                iid_to_path[iid] = fr["path"]
            # Tag visivo per la riga "corrente" (focus item) che si
            # sposta con le frecce: bordo/sfondo intermedio (verde scuro
            # dei bottoni) cosi' l'utente vede la barra muoversi anche
            # quando la riga non e' selezionata. La selezione (SPAZIO)
            # resta col colore cursore brillante via style.map default.
            try:
                tree.tag_configure(
                    "cur_focus",
                    background=c.get("pulsanti_sfondo", "#114411"),
                    foreground=c.get("cerca_testo", "#7fff7f"))
            except tk.TclError:
                pass

            def _aggiorna_tag_focus():
                """Applica il tag 'cur_focus' SOLO alla riga in focus,
                rimuovendolo dalle altre."""
                cur = tree.focus()
                for iid in tree.get_children(""):
                    try:
                        tags = list(tree.item(iid, "tags") or [])
                        had = "cur_focus" in tags
                        want = (iid == cur)
                        if want and not had:
                            tags.append("cur_focus")
                            tree.item(iid, tags=tuple(tags))
                        elif had and not want:
                            tags.remove("cur_focus")
                            tree.item(iid, tags=tuple(tags))
                    except tk.TclError:
                        pass

            # Focus visivo sulla prima riga (barra di evidenziazione)
            # ma NESSUNA selezione attiva di default: l'utente deve
            # premere SPAZIO esplicitamente per scegliere le sessioni
            # da spedire al Dr. IA. Se preme CONFRONTA senza aver
            # selezionato nulla, _lancia_confronta usa per fallback
            # la sessione piu' recente come comodita'.
            primo_list = tree.get_children()
            if primo_list:
                tree.focus(primo_list[0])
                _aggiorna_tag_focus()
                self._hub_tempi_sel_paths = []

            def _aggiorna_paths():
                sel = tree.selection()
                self._hub_tempi_sel_paths = [
                    iid_to_path.get(iid) for iid in sel
                    if iid_to_path.get(iid)]

            def _vis_focus():
                """Scrolla il treeview per mostrare l'item con focus
                e aggiorna il tag visivo della riga corrente."""
                f = tree.focus()
                if f:
                    try:
                        tree.see(f)
                    except tk.TclError:
                        pass
                _aggiorna_tag_focus()

            # --- FRECCE: spostano SOLO il focus, NON la selezione ---
            # Il default ttk.Treeview in selectmode='extended' usa Up/Down
            # per cambiare focus E sovrascrivere la selezione singola.
            # Bloccando il default con "break" e gestendo noi il move,
            # la selezione costruita con SPAZIO resta intatta mentre
            # l'utente scorre la lista.
            def _move_focus(passo):
                cur = tree.focus() or (tree.get_children() or [None])[0]
                if not cur:
                    return "break"
                seq = tree.get_children()
                if not seq:
                    return "break"
                try:
                    i = seq.index(cur)
                except ValueError:
                    return "break"
                ni = max(0, min(len(seq) - 1, i + passo))
                if ni != i:
                    tree.focus(seq[ni])
                    _vis_focus()
                return "break"

            def _up(e):
                # Up sulla prima riga: torna al bottone sopra
                cur = tree.focus()
                seq = tree.get_children()
                if seq and cur == seq[0] and btns:
                    btns[-1].focus_set()
                    return "break"
                return _move_focus(-1)

            tree.bind("<Up>",   _up)
            tree.bind("<Down>", lambda e: _move_focus(1))
            tree.bind("<Home>", lambda e: (tree.focus(
                (tree.get_children() or [None])[0]), _vis_focus(),
                "break")[-1])
            tree.bind("<End>",  lambda e: (tree.focus(
                (tree.get_children() or [None])[-1]), _vis_focus(),
                "break")[-1])
            tree.bind("<Prior>",
                      lambda e: [_move_focus(-1) for _ in range(8)]
                      and "break")
            tree.bind("<Next>",
                      lambda e: [_move_focus(1) for _ in range(8)]
                      and "break")

            # --- SPAZIO: toggle della riga in focus nella selezione ---
            def _toggle_riga(_e=None):
                iid = tree.focus()
                if not iid:
                    return "break"
                if iid in tree.selection():
                    tree.selection_remove(iid)
                else:
                    tree.selection_add(iid)
                _aggiorna_paths()
                return "break"
            tree.bind("<KeyPress-space>", _toggle_riga)
            tree.bind("<space>",          _toggle_riga)

            # Click del mouse riallinea le ns. variabili
            tree.bind("<<TreeviewSelect>>",
                      lambda _e: _aggiorna_paths())

            # Invio sulla riga = lancia CONFRONTA su selezione corrente
            tree.bind("<Return>",
                      lambda e: (self._lancia_confronta(), "break")[-1])
            tree.bind("<KP_Enter>",
                      lambda e: (self._lancia_confronta(), "break")[-1])

            # V = VAI AL SETUP della riga in focus (chiude Crono e
            # apre nel form retrodb il record di setup originario)
            def _vai_setup(_e=None):
                iid = tree.focus()
                if not iid:
                    return "break"
                path = iid_to_path.get(iid)
                if not path:
                    return "break"
                # Recupera il file_record corrispondente
                target_fr = None
                for fr in files_record:
                    if fr["path"] == path:
                        target_fr = fr
                        break
                if target_fr:
                    self._vai_al_setup_della_sessione(target_fr)
                return "break"
            tree.bind("<KeyPress-v>", _vai_setup)
            tree.bind("<KeyPress-V>", _vai_setup)

            # Down dal bottone CONFRONTA porta al tree + focus widget
            if btns:
                btns[-1].bind(
                    "<Down>",
                    lambda e, t=tree: (t.focus_set(), "break")[-1])
        else:
            tk.Label(self.root,
                     text="(Nessuna sessione cronometrata per questo "
                          "setup. Premi CRONOMETRA per registrarne una.)",
                     bg=self.c["sfondo"], fg=self.c["testo_dim"],
                     font=self._f_small).pack(pady=(10, 4))

        # Niente riepilogo dettagliato sotto: i dati di pista, meteo e
        # riferimenti (gomme/miscela/motori/telai) sono gia' visibili
        # nella scheda del setup, quelli passati al Dr. IA sono solo
        # contesto interno per il prompt.
        self._hub_status = self._status_label(self.root)

        self._top.bind("<Escape>", lambda e: self._chiudi())
        btns[0].focus_set()

    # =================================================================
    #  2. CRONOMETRA (lancia LapTimer con dati setup)
    #
    #  NOTA: il modo LIVE (multi-pilota via ricevitore LapMonitor BT)
    #  e' integrato dentro LapTimer stesso. Se il ricevitore viene
    #  rilevato all'avvio della schermata cronometro, LapTimer
    #  passa automaticamente a multi-colonna; se no resta in manuale
    #  con barra spazio. Vedi laptimer.py.
    # =================================================================
    def _avvia_cronometro(self):
        if not LapTimer:
            return
        record_id = self.ctx.get("record_id", "")
        if not record_id:
            return
        setup_name = self.ctx.get("setup_name", "Setup")
        pilota = self.ctx.get("pilota", "?")
        pista = self.ctx.get("pista", "")
        dati_dir = self.ctx.get("dati_dir", "")

        # Copia file anche in scouting/ per comparazione in "Tutti i tempi"
        scouting_dir = os.path.join(self.dati_dir, "scouting") if self.dati_dir else ""
        def _on_close_setup():
            if scouting_dir:
                self._copia_in_scouting(dati_dir, scouting_dir)
            self._schermata_hub()

        self._pulisci()
        LapTimer(setup=setup_name, pilota=pilota, pista=pista,
                 dati_dir=dati_dir, record_id=record_id,
                 parent=self.root, on_close=_on_close_setup,
                 setup_snapshot=self._build_setup_snapshot())


    def _tutti_tempi_da_setup(self):
        """Apre Tutti i tempi (archivio scouting) dal contesto setup."""
        self._tutti_tempi_back_setup = True
        old_modo = getattr(self, '_modo_setup', False)
        self._modo_setup = False
        self._tutti_tempi_old_modo = old_modo
        self._schermata_tutti_tempi()

    def _ripristina_da_tutti(self):
        """Torna all'hub setup dopo Tutti i tempi."""
        self._modo_setup = getattr(self, '_tutti_tempi_old_modo', True)
        self._tutti_tempi_back_setup = False
        self._schermata_hub()

    def _copia_in_scouting(self, dati_dir, scouting_dir):
        """Copia l'ultimo file lap_*.json da dati_dir a scouting_dir.
        Il file viene posizionato nella sotto-cartella <anno>/<pista>/
        corretta in base ai suoi metadati (vedi core.scouting_paths)."""
        try:
            # Trova i file lap_*.json in dati_dir (non nelle sottocartelle)
            files = [f for f in os.listdir(dati_dir)
                     if f.startswith("lap_") and f.endswith(".json")
                     and os.path.isfile(os.path.join(dati_dir, f))]
            if not files:
                return
            # Prendi il piu' recente per data modifica
            files.sort(key=lambda f: os.path.getmtime(os.path.join(dati_dir, f)))
            src = os.path.join(dati_dir, files[-1])
            _scouting_paths.copia_in_scouting(src, scouting_dir)
        except Exception:
            pass

    # =================================================================
    #  3. SPEEDHIVE (import da API)
    # =================================================================
    def _importa_speedhive(self):
        if not _HAS_SPEEDHIVE:
            return
        c = self.c

        setup_data = self.ctx.get("data", "").strip()
        setup_ora = self.ctx.get("ora", "").strip()
        transponder = self.ctx.get("transponder", "").strip()
        speedhive_id = self.ctx.get("speedhive_id", "").strip()

        if not setup_data:
            self._hub_status.config(text="SpeedHive: compila Data nel setup!", fg=c["stato_errore"])
            return
        if not transponder:
            self._hub_status.config(text="SpeedHive: configura Transponder in Utenti!", fg=c["stato_errore"])
            return
        if not speedhive_id:
            self._hub_status.config(text="SpeedHive: configura SpeedHive_ID nella pista!", fg=c["stato_errore"])
            return

        self._hub_status.config(text="SpeedHive: ricerca in corso...", fg=c["stato_avviso"])
        self.root.update()

        # Cerca e scarica
        dati, sessione_match, activity_id = import_automatico(
            speedhive_id, transponder, setup_data, setup_ora)

        if not dati or "sessions" not in dati or not dati["sessions"]:
            self._hub_status.config(
                text="SpeedHive: nessun dato trovato per questa data/transponder!",
                fg=c["stato_errore"])
            return

        # Salva TUTTE le sessioni trovate (salta duplicati)
        record_id = self.ctx.get("record_id", "")
        pilota = self.ctx.get("pilota", "?")
        setup_name = self.ctx.get("setup_name", "?")
        dati_dir = self.ctx.get("dati_dir", "")
        snapshot = self._build_setup_snapshot()

        # Raccogli session_id SpeedHive gia' salvati in QUALSIASI record
        sh_ids_esistenti = set()
        if dati_dir and os.path.isdir(dati_dir):
            for f in os.listdir(dati_dir):
                if f.startswith("lap_") and f.endswith(".json"):
                    try:
                        with open(os.path.join(dati_dir, f), "r", encoding="utf-8") as fh:
                            j = json.load(fh)
                        sh = j.get("speedhive", {})
                        sid = sh.get("session_id")
                        if sid:
                            sh_ids_esistenti.add(sid)
                    except Exception:
                        pass

        salvate = 0
        skipped = 0
        best_globale = None
        scouting_dir = os.path.join(self.dati_dir, "scouting") if self.dati_dir else ""
        for sess in dati["sessions"]:
            session_id = sess.get("id", 0)
            # Salta se gia' importata per questo record
            if session_id in sh_ids_esistenti:
                skipped += 1
                continue
            risultato, path = converti_sessione(
                dati, session_id,
                setup=setup_name, pilota=pilota,
                record_id=record_id, dati_dir=dati_dir,
                setup_snapshot=snapshot)
            if risultato and path:
                salvate += 1
                bt = risultato.get("miglior_tempo", 0)
                if best_globale is None or (bt > 0 and bt < best_globale):
                    best_globale = bt
                # Copia in scouting (per "Tutti i tempi") nella
                # sotto-cartella <anno>/<pista>/.
                if scouting_dir:
                    _scouting_paths.copia_in_scouting(path, scouting_dir)

        if salvate > 0:
            msg = "SpeedHive OK: %d sessioni importate, best %s" % (
                salvate, _fmt(best_globale or 0))
            if skipped:
                msg += " (%d gia' presenti)" % skipped
            self._hub_status.config(text=msg, fg=c["stato_ok"])
        elif skipped > 0:
            self._hub_status.config(
                text="SpeedHive: %d sessioni gia' importate in altro record" % skipped,
                fg=c["stato_avviso"])
        else:
            self._hub_status.config(text="SpeedHive: errore salvataggio!", fg=c["stato_errore"])

    # =================================================================
    #  4. TEMPI (lista sessioni salvate)
    # =================================================================
    def _trova_sessioni(self, record_id):
        """Cerca file sessioni per un record_id. Ritorna (sessioni, paths)."""
        if not self.dati_dir or not os.path.isdir(self.dati_dir):
            return [], []
        prefisso = "lap_%s_" % record_id
        sessioni = []
        paths = []
        for f in sorted(os.listdir(self.dati_dir)):
            if f.startswith(prefisso) and f.endswith(".json"):
                fp = os.path.join(self.dati_dir, f)
                try:
                    with open(fp, "r", encoding="utf-8") as fh:
                        sessioni.append(json.load(fh))
                    paths.append(fp)
                except Exception:
                    pass
        return sessioni, paths

    def _trova_sessioni_setup(self):
        """Cerca TUTTE le sessioni setup: lap_id_*.json e lap_rec_*.json."""
        sessioni = []
        paths = []
        if not self.dati_dir or not os.path.isdir(self.dati_dir):
            return sessioni, paths
        for f in sorted(os.listdir(self.dati_dir)):
            if not f.endswith(".json"):
                continue
            # Nuovo formato (id_XXXX) e vecchio formato (rec_N)
            if f.startswith("lap_id_") or f.startswith("lap_rec_"):
                fp = os.path.join(self.dati_dir, f)
                try:
                    with open(fp, "r", encoding="utf-8") as fh:
                        s = json.load(fh)
                    sessioni.append(s)
                    paths.append(fp)
                except Exception:
                    pass
        return sessioni, paths

    def _schermata_tempi(self):
        """Mostra TUTTE le sessioni setup riusando la schermata Tutti i Tempi."""
        record_id = self.ctx.get("record_id", "")
        if not record_id:
            return

        sessioni, paths = self._trova_sessioni_setup()
        if not sessioni:
            if hasattr(self, '_hub_status'):
                self._hub_status.config(text="Nessuna sessione trovata.",
                                         fg=self.c["stato_avviso"])
            return

        # Inietta meteo/setup nelle sessioni del record corrente
        for s in sessioni:
            if s.get("record_id") == record_id:
                self._inietta_meteo(s)

        # Pre-carica dati e attiva modalita' setup
        self._tutti_sessioni = sessioni
        self._tutti_paths = paths
        self._modo_setup = True
        self._modo_setup_record_id = record_id
        self._tempi_on_close = self._schermata_tempi
        self._schermata_tutti_tempi()
        self._modo_setup = False
        # _tempi_on_close resta impostato per i callback di ritorno

    # =================================================================
    #  5. SCOUTING (cronometraggio libero senza setup)
    # =================================================================
    def _schermata_scouting(self, prefill=None):
        """Form scouting. Se prefill fornito, pre-compila."""
        self._pulisci()
        c = self.c
        pf = prefill or {}
        scouting_dir = os.path.join(self.dati_dir, "scouting") if self.dati_dir else ""

        # Header
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        back_cmd = self._schermata_hub if self.ctx.get("record_id") else (self._schermata_hub_libero if self.dati_dir else self._chiudi)
        back_txt = "< CRONO" if self.ctx.get("record_id") else ("< CRONO" if self.dati_dir else "< INDIETRO")
        tk.Button(header, text=back_txt, font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=back_cmd).pack(side="left")
        tk.Label(header, text="  SCOUTING", bg=c["sfondo"], fg=c["stato_avviso"],
                 font=self._f_title).pack(side="left", padx=(8, 0))
        # Barra batteria in alto a destra (overlay)
        _aggiungi_barra_bat(header)

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(4, 8))

        tk.Label(self.root, text="Cronometra senza setup - dati salvati in scouting/",
                 bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small).pack(pady=(4, 8))

        # Form
        if not RetroField:
            tk.Label(self.root, text="RetroField non disponibile!",
                     bg=c["sfondo"], fg=c["stato_errore"], font=self._f_info).pack()
            return

        form = tk.Frame(self.root, bg=c["sfondo"])
        form.pack(pady=5)

        self._sf_pilota = RetroField(form, label="Pilota", tipo="S", lunghezza=20, label_width=12)
        self._sf_pilota.pack(pady=2, anchor="w")
        self._sf_pilota_note = RetroField(form, label="Info Pilota", tipo="S", lunghezza=30, label_width=12)
        self._sf_pilota_note.pack(pady=2, anchor="w")
        self._sf_pista = RetroField(form, label="Pista", tipo="S", lunghezza=20, label_width=12)
        self._sf_pista.pack(pady=2, anchor="w")
        self._sf_transponder = RetroField(form, label="Transponder", tipo="N", lunghezza=10, label_width=12)
        self._sf_transponder.pack(pady=2, anchor="w")
        self._sf_serbatoio = RetroField(form, label="Serbatoio cc", tipo="N", lunghezza=4, label_width=12)
        self._sf_serbatoio.pack(pady=2, anchor="w")
        self._sf_data = RetroField(form, label="Data", tipo="D", lunghezza=10, label_width=12)
        self._sf_data.pack(pady=2, anchor="w")
        self._sf_ora = RetroField(form, label="Ora", tipo="O", lunghezza=5, label_width=12)
        self._sf_ora.pack(pady=2, anchor="w")
        self._sf_note = RetroField(form, label="Note", tipo="S", lunghezza=30, label_width=12)
        self._sf_note.pack(pady=2, anchor="w")

        # Pre-compila se dati disponibili (NO pilota: lasciare libero per autocomplete)
        if pf.get("pista"): self._sf_pista.set(pf["pista"])
        if pf.get("serbatoio"): self._sf_serbatoio.set(pf["serbatoio"])
        if pf.get("data"): self._sf_data.set(pf["data"])
        if pf.get("ora"): self._sf_ora.set(pf["ora"])

        # Match pista: controlla ogni 500ms cosa c'e' nel campo
        self._matched_pista = None
        self._last_pista_text = ""
        self._scouting_attivo = True
        self._check_pista_match()

        # Auto-lookup transponder + serbatoio + note dal nome pilota
        def _auto_fill_from_registry(event=None):
            nome = self._sf_pilota.get().strip().lower()
            if not nome:
                return
            registro = self._load_piloti()
            p = registro.get(nome)
            if p:
                if p["transponder"] and not self._sf_transponder.get().strip():
                    self._sf_transponder.set(p["transponder"])
                if p["serbatoio"] and not self._sf_serbatoio.get().strip():
                    self._sf_serbatoio.set(p["serbatoio"])
                if p["note"] and not self._sf_pilota_note.get().strip():
                    self._sf_pilota_note.set(p["note"])
        self._sf_pilota._canvas.bind("<FocusOut>", _auto_fill_from_registry, add="+")

        # Autocomplete pilota: cerca nomi dal registro piloti.json
        self._piloti_cache = None
        self._last_pilota_text = self._sf_pilota.get().strip()
        def _build_piloti_cache():
            """Costruisce cache {nome_lower: {nome, transponder, serbatoio}} da piloti.json."""
            return self._load_piloti()

        # Autocomplete pilota: primo match automatico, Tab per ciclare
        self._ac_candidati = []
        self._ac_indice = 0

        def _check_pilota_autocomplete():
            if not getattr(self, '_scouting_attivo', False):
                return
            try:
                if not self._sf_pilota._canvas.winfo_exists():
                    self._scouting_attivo = False
                    return
            except Exception:
                self._scouting_attivo = False
                return
            try:
                testo = self._sf_pilota.get().strip()
                if testo != self._last_pilota_text:
                    self._last_pilota_text = testo
                    if len(testo) >= 2:
                        if self._piloti_cache is None:
                            self._piloti_cache = _build_piloti_cache()
                        testo_lower = testo.lower()
                        self._ac_candidati = [
                            p for nome_lower, p in self._piloti_cache.items()
                            if nome_lower.startswith(testo_lower)
                        ]
                        self._ac_indice = 0
                        if self._ac_candidati:
                            _applica_pilota(self._ac_candidati[0])
                    else:
                        self._ac_candidati = []
                        self._ac_indice = 0
            except Exception:
                pass
            if getattr(self, '_scouting_attivo', False):
                self.root.after(400, _check_pilota_autocomplete)

        def _applica_pilota(p):
            """Compila campi dal pilota selezionato."""
            self._sf_pilota.set(p["nome"])
            self._last_pilota_text = p["nome"]
            if p["transponder"]:
                self._sf_transponder.set(p["transponder"])
            if p["serbatoio"]:
                self._sf_serbatoio.set(p["serbatoio"])
            if p["note"]:
                self._sf_pilota_note.set(p["note"])

        def _cicla_pilota(direction):
            """Freccia su/giu: cicla candidati."""
            if len(self._ac_candidati) <= 1:
                return
            self._ac_indice = (self._ac_indice + direction) % len(self._ac_candidati)
            _applica_pilota(self._ac_candidati[self._ac_indice])

        try:
            self._sf_pilota._canvas.bind("<Down>", lambda e: (_cicla_pilota(1), "break")[-1])
            self._sf_pilota._canvas.bind("<Up>", lambda e: (_cicla_pilota(-1), "break")[-1])
        except Exception:
            pass

        self.root.after(800, _check_pilota_autocomplete)

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(8, 4))

        # Status pista/ricerca (riga fissa sotto il form)
        self._sf_match = tk.Label(self.root, text="", bg=c["sfondo"], fg=c["cerca_testo"],
                                   font=self._f_small, anchor="w")
        self._sf_match.pack(fill="x", padx=10, pady=(0, 4))

        # Bottoni azione
        bar = tk.Frame(self.root, bg=c["sfondo"])
        bar.pack(pady=8)
        # CRONOMETRO MANUALE - sempre visibile.
        # NB: se il ricevitore LapMonitor BT e' acceso, LapTimer passa
        # automaticamente a modalita' LIVE multi-pilota (vedi laptimer.py).
        self._btn_crono_man = tk.Button(bar, text="CRONOMETRO", font=self._f_btn, width=14,
                  bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                  relief="ridge", bd=2, cursor="hand2",
                  command=self._avvia_scouting)
        self._btn_crono_man.pack(side="left", padx=4)
        # Enter sul bottone CRONOMETRO -> avvia scouting e BLOCCA propagazione
        # cosi' il binding <Return> sul toplevel non rifa' partire _scouting_enter
        # (che a form distrutto cadrebbe nel ramo default).
        self._btn_crono_man.bind(
            "<Return>", lambda e: (self._avvia_scouting(), "break")[-1])
        # RICERCA SPEEDHIVE/MYRCM (visibile quando pista matchata + data compilata)
        self._btn_speedhive_live = tk.Button(bar, text="RICERCA", font=self._f_btn, width=14,
                  bg=c["cerca_sfondo"], fg=c["cerca_testo"],
                  relief="ridge", bd=2, cursor="hand2",
                  command=self._avvia_ricerca)
        # Enter sul bottone RICERCA -> avvia ricerca e BLOCCA propagazione.
        # Senza "break" il binding sul toplevel scattava DOPO che _pulisci
        # aveva distrutto il form, focus_get tornava sul root, _scouting_enter
        # cadeva nel ramo else e partiva _avvia_scouting al posto della ricerca.
        self._btn_speedhive_live.bind(
            "<Return>", lambda e: (self._avvia_ricerca(), "break")[-1])
        # Nascosto inizialmente
        self._speedhive_live_visible = False

        # Binding
        self._top.bind("<Return>", lambda e: self._scouting_enter())
        self._top.bind("<Escape>", lambda e: back_cmd())
        self.root.after(100, lambda: self._safe_focus(self._sf_pilota))

    def _scouting_enter(self):
        """Enter intelligente: lancia in base al focus.
        Se focus su RICERCA -> ricerca. Se focus su CRONOMETRO -> cronometro.
        Altrimenti -> cronometro manuale (default)."""
        focused = self.root.focus_get()
        if hasattr(self, '_btn_speedhive_live') and focused == self._btn_speedhive_live:
            self._avvia_ricerca()
        else:
            self._avvia_scouting()

    def _check_pista_match(self):
        """Controlla periodicamente il campo Pista per match con database."""
        # Ferma polling se schermata scouting non piu' attiva
        if not getattr(self, '_scouting_attivo', False):
            return
        # Controllo widget ancora esistente
        try:
            if not self._sf_pista._canvas.winfo_exists():
                self._scouting_attivo = False
                return
        except Exception:
            self._scouting_attivo = False
            return

        try:
            # Ricarica piste se lista vuota (fallback primo accesso)
            if not self._piste_data:
                self._load_piste()

            testo = self._sf_pista.get().strip()
            if testo != self._last_pista_text:
                self._last_pista_text = testo
                self._match_candidati = []
                match = self._match_pista(testo)
                if match:
                    self._matched_pista = match
                    # Solo nome pista nella label, niente dettagli SpeedHive/MyRCM
                    info = match["nome"]
                    if match["citta"]:
                        info += "  (%s)" % match["citta"]
                    self._sf_match.config(text=info, fg=self.c["cerca_testo"])
                    # Auto-compila SOLO se: 4+ lettere, match univoco
                    if (len(testo) >= 4 and
                            testo.lower() != match["nome"].lower()):
                        self._sf_pista.clear()
                        self._sf_pista.set(match["nome"])
                        self._last_pista_text = match["nome"]
                else:
                    self._matched_pista = None
                    # Mostra candidati se ce ne sono (ambiguo)
                    if self._match_candidati:
                        nomi = [p["nome"] for p in self._match_candidati[:5]]
                        hint = ", ".join(nomi)
                        if len(self._match_candidati) > 5:
                            hint += " +%d" % (len(self._match_candidati) - 5)
                        self._sf_match.config(
                            text="%d piste: %s" % (len(self._match_candidati), hint),
                            fg=self.c["stato_avviso"])
                    else:
                        self._sf_match.config(text="", fg=self.c["testo_dim"])

            # Mostra/nascondi bottone RICERCA (controlla SEMPRE)
            # Visibile se: pista matchata + data compilata (SpeedHive e/o MyRCM)
            if hasattr(self, '_btn_speedhive_live'):
                has_shid = bool(self._matched_pista and self._matched_pista.get("speedhive_id"))
                has_pista = bool(self._matched_pista)
                transp = self._sf_transponder.get().strip() if hasattr(self, '_sf_transponder') else ""
                data = self._sf_data.get().strip() if hasattr(self, '_sf_data') else ""
                # Visibile se: pista matchata + (data O transponder)
                should_show = has_pista and (data or (has_shid and transp))
                if should_show and not self._speedhive_live_visible:
                    # Pack PRIMA di CRONOMETRO cosi' RICERCA appare a
                    # sinistra, che e' l'azione naturale dopo aver
                    # compilato pista+data.
                    self._btn_speedhive_live.pack(
                        side="left", padx=4, before=self._btn_crono_man)
                    # Catena TAB esplicita: ultimo campo form -> RICERCA
                    # -> CRONOMETRO -> primo campo. Cosi' entrambi i
                    # bottoni sono raggiungibili da tastiera (prima
                    # disabilitavamo takefocus su CRONOMETRO per andare
                    # dritti a RICERCA, ma cosi' CRONOMETRO non era piu'
                    # raggiungibile col TAB).
                    try:
                        self._btn_crono_man.config(takefocus=1)
                        self._btn_speedhive_live.config(takefocus=1)
                        # TAB da RICERCA -> CRONOMETRO
                        self._btn_speedhive_live.bind(
                            "<Tab>",
                            lambda e: (self._btn_crono_man.focus_set(),
                                        "break")[-1])
                        # Shift-Tab da CRONOMETRO -> RICERCA
                        self._btn_crono_man.bind(
                            "<Shift-Tab>",
                            lambda e: (self._btn_speedhive_live.focus_set(),
                                        "break")[-1])
                        self._btn_crono_man.bind(
                            "<ISO_Left_Tab>",
                            lambda e: (self._btn_speedhive_live.focus_set(),
                                        "break")[-1])
                    except Exception:
                        pass
                    self._speedhive_live_visible = True
                elif not should_show and self._speedhive_live_visible:
                    self._btn_speedhive_live.pack_forget()
                    try:
                        self._btn_crono_man.unbind("<Shift-Tab>")
                        self._btn_crono_man.unbind("<ISO_Left_Tab>")
                    except Exception:
                        pass
                    self._speedhive_live_visible = False

            # ── AUTO-DOWNLOAD: quando pista + data sono pronti ──
            data = self._sf_data.get().strip() if hasattr(self, '_sf_data') else ""
            pista_testo = self._sf_pista.get().strip() if hasattr(self, '_sf_pista') else ""
            pista_nome = ""
            if self._matched_pista:
                pista_nome = self._matched_pista.get("nome", "")
            elif pista_testo and len(pista_testo) >= 3:
                pista_nome = pista_testo  # pista libera (es. "Ponte di Piave")
            if pista_nome and data and len(data) >= 10:
                combo_key = "%s|%s" % (pista_nome, data)
                if combo_key != getattr(self, '_last_scouting_combo', ''):
                    self._last_scouting_combo = combo_key
                    self._auto_import_done_sh = False
                    self._auto_import_done_myrcm = False

                # Auto SpeedHive (in background, una volta sola per data)
                if self._matched_pista and not getattr(self, '_auto_import_done_sh', False):
                    has_shid = bool(self._matched_pista.get("speedhive_id"))
                    if has_shid and _HAS_SPEEDHIVE:
                        self._auto_import_done_sh = True
                        self._sf_match.config(
                            text="Ricerca SpeedHive...",
                            fg=self.c["stato_avviso"])
                        self._auto_speedhive_bg(data)

                # Auto MyRCM (in background, una volta sola per data)
                if self._matched_pista and not getattr(self, '_auto_import_done_myrcm', False):
                    if _HAS_MYRCM:
                        self._auto_import_done_myrcm = True
                        self._sf_match.config(
                            text="Ricerca MyRCM...",
                            fg=self.c["stato_avviso"])
                        self._auto_myrcm_bg(data)
        except Exception:
            pass

        # Rischedula solo se scouting ancora attivo
        if getattr(self, '_scouting_attivo', False):
            self.root.after(500, self._check_pista_match)

    # =================================================================
    #  AUTO-DOWNLOAD in background (SpeedHive + MyRCM)
    # =================================================================
    def _auto_speedhive_bg(self, data_str):
        """Scarica automaticamente TUTTE le sessioni SpeedHive in background."""
        speedhive_id = self._matched_pista.get("speedhive_id", "")
        pista_nome = self._matched_pista.get("nome", "")
        if not speedhive_id:
            return

        def _fetch():
            try:
                from speedhive_import import (cerca_tutte_attivita_per_data,
                                              scarica_sessioni as sh_scarica)
                from concurrent.futures import ThreadPoolExecutor, as_completed
                # Indice trasponder -> nome dal registro piloti.json:
                # se l'utente ha fatto ALIAS in passato, il nome viene
                # riusato in automatico (vedi _ricerca_completa).
                alias_per_chip = self._build_alias_per_trasponder()
                attivita = cerca_tutte_attivita_per_data(speedhive_id, data_str)
                if not attivita:
                    self.root.after(0, lambda: self._auto_import_status(
                        "SpeedHive: nessuna sessione", "avviso"))
                    return

                scouting_dir = os.path.join(self.dati_dir, "scouting") if self.dati_dir else "scouting"
                os.makedirs(scouting_dir, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")

                # Indice locale {chip: {sid: num_giri}}: cosi' una
                # sessione gia' presente viene riscaricata/sovrascritta
                # SOLO se cresce di giri (sessione live in corso).
                # Bug pre-fix: skip secco per (chip, sid) bloccava gli
                # aggiornamenti delle sessioni cresciute (es. da 2 a 5
                # giri).
                indice_locale = {}
                try:
                    # Scansione ricorsiva nella nuova struttura
                    # <anno>/<pista>/.
                    for f, full in _scouting_paths.elenca_lap_files(
                            scouting_dir):
                        try:
                            with open(full, "r", encoding="utf-8") as fp:
                                d = json.load(fp)
                            if d.get("data") != data_str:
                                continue
                            ch = str(d.get("transponder", "")).strip()
                            sid_l = d.get("speedhive_session")
                            ng_l = int(d.get("num_giri", 0) or 0)
                            if ch and sid_l:
                                indice_locale.setdefault(
                                    ch, {})[sid_l] = ng_l
                        except Exception:
                            pass
                except Exception:
                    pass

                def _processa_attivita(att):
                    local_saved = []  # (chip, sid, sessione_dict)
                    local_skipped = 0
                    try:
                        aid = att["activity_id"]
                        chip = att["chipCode"]
                        chip_str = str(chip).strip()
                        label = (alias_per_chip.get(chip_str)
                                 or att.get("chipLabel",
                                            "Pilota_%s" % chip[-4:]))
                        sid_locali = indice_locale.get(chip_str, {})
                        dati = sh_scarica(aid)
                        if not dati or "sessions" not in dati:
                            return local_saved, local_skipped
                        for sess in dati.get("sessions", []):
                            sid = sess.get("id", 0)
                            laps_remoti = sess.get("laps", []) or []
                            # SpeedHive ritorna duration come STRINGA
                            # (es. '23.551'). Convertiamo a float in
                            # safe-mode con try/except. Senza la
                            # conversione esplicita il confronto
                            # 'str' > 0 solleva TypeError che il
                            # try/except del worker cattura silently
                            # facendo fallire l'intera attivita.
                            n_remote = 0
                            for _l in laps_remoti:
                                try:
                                    if float(_l.get("duration", 0) or 0) > 0:
                                        n_remote += 1
                                except (ValueError, TypeError):
                                    pass
                            # Skip solo se gia' presente con num_giri
                            # locale >= remoto (niente da aggiornare).
                            if (sid and sid in sid_locali
                                    and sid_locali[sid] >= n_remote
                                    and n_remote > 0):
                                local_skipped += 1
                                continue
                            laps = sess.get("laps", [])
                            if not laps:
                                continue
                            tempi = []
                            giri_list = []
                            for lap in laps:
                                try:
                                    dur = float(lap.get("duration", 0))
                                except (ValueError, TypeError):
                                    dur = 0
                                if dur > 0:
                                    tempi.append(dur)
                                    giri_list.append({
                                        "giro": len(giri_list) + 1,
                                        "tempo": round(dur, 3),
                                        "stato": "valido",
                                    })
                            if not giri_list:
                                continue
                            dt_start = sess.get("dateTimeStart", "")
                            try:
                                from datetime import datetime as dt_cls
                                dt_obj = dt_cls.fromisoformat(dt_start)
                                ora = dt_obj.strftime("%H:%M:%S")
                            except Exception:
                                ora = (dt_start[:8]
                                       if len(dt_start) >= 8 else "?")
                            sessione = {
                                "pilota": label,
                                "setup": "SpeedHive - %s" % pista_nome,
                                "data": data_str,
                                "ora": ora,
                                "tipo": "speedhive",
                                "transponder": chip,
                                "serbatoio_cc": 0,
                                "sessione_carburante": False,
                                "speedhive_session": sid,
                                "speedhive_activity": aid,
                                "num_giri": len(giri_list),
                                "giri": giri_list,
                                "miglior_tempo": round(min(tempi), 3),
                                "media": round(sum(tempi) / len(tempi), 3),
                                "tempo_totale": round(sum(tempi), 3),
                            }
                            local_saved.append((chip, sid, sessione))
                    except Exception:
                        pass
                    return local_saved, local_skipped

                # Background: 4 worker (meno aggressivo del refresh
                # manuale che usa 8) per non saturare il radio Wi-Fi
                # mentre l'utente compila il form.
                nuove_sessioni = []
                skipped = 0
                with ThreadPoolExecutor(max_workers=4) as exe:
                    futures = [exe.submit(_processa_attivita, att)
                               for att in attivita]
                    for fut in as_completed(futures):
                        try:
                            saved_local, skipped_local = fut.result()
                        except Exception:
                            saved_local, skipped_local = [], 0
                        nuove_sessioni.extend(saved_local)
                        skipped += skipped_local

                saved = 0
                # Nome file UNIVOCO per (data, chip, sid): cosi' due
                # download concorrenti (es. _auto_speedhive_bg parte
                # mentre l'utente fa RICERCA) scrivono lo STESSO file
                # invece di duplicarlo. Niente piu' duplicati su
                # disco. La data viene compattata (no slash).
                _data_compact = "".join(ch for ch in str(data_str)
                                         if ch.isdigit())
                for chip, sid, sessione in nuove_sessioni:
                    fname = ("lap_speedhive_%s_%s_s%d.json"
                             % (_data_compact, str(chip)[-6:], sid))
                    if _scouting_paths.salva_sessione(
                            sessione, scouting_dir, fname):
                        saved += 1

                if saved:
                    msg = "SpeedHive: %d nuove" % saved
                    if skipped:
                        msg += " (+%d gia' presenti)" % skipped
                    self.root.after(0, lambda m=msg: self._auto_import_status(
                        m, "ok"))
                elif skipped:
                    self.root.after(0,
                        lambda s=skipped: self._auto_import_status(
                            "SpeedHive: nessuna nuova (%d gia' presenti)" % s,
                            "ok"))
                else:
                    self.root.after(0, lambda: self._auto_import_status(
                        "SpeedHive: nessuna sessione", "avviso"))

            except Exception as e:
                self.root.after(0, lambda: self._auto_import_status(
                    "SpeedHive: errore %s" % e, "errore"))

        threading.Thread(target=_fetch, daemon=True).start()

    def _auto_myrcm_bg(self, data_str):
        """Scarica automaticamente tempi gara da MyRCM in background."""
        pista_nome = self._matched_pista.get("nome", "")
        if not pista_nome:
            return

        def _fetch():
            try:
                scouting_dir = os.path.join(self.dati_dir, "scouting") if self.dati_dir else "scouting"
                os.makedirs(scouting_dir, exist_ok=True)

                saved, event_nome = import_evento_completo(
                    pista_nome, data_str, scouting_dir,
                    setup_snapshot=self._build_setup_snapshot())

                if saved:
                    self.root.after(0, lambda: self._auto_import_status(
                        "MyRCM: %d sessioni gara (%s)" % (len(saved), event_nome or ""), "ok"))
                else:
                    self.root.after(0, lambda: self._auto_import_status(
                        "MyRCM: nessuna gara trovata", "avviso"))

            except Exception as e:
                self.root.after(0, lambda: self._auto_import_status(
                    "MyRCM: errore %s" % e, "errore"))

        threading.Thread(target=_fetch, daemon=True).start()

    def _auto_import_status(self, msg, livello="ok"):
        """Aggiorna label stato import automatico (se ancora visibile).
        Accumula messaggi SpeedHive + MyRCM, sovrascrive solo 'Ricerca...'."""
        try:
            if not hasattr(self, '_sf_match') or not self._sf_match.winfo_exists():
                return
            c = self.c
            if livello == "ok":
                colore = c["stato_ok"]
            elif livello == "errore":
                colore = c["stato_errore"]
            else:
                colore = c["stato_avviso"]

            testo_attuale = self._sf_match.cget("text")
            # Se c'e' gia' un risultato di altra fonte, accoda
            fonte_msg = msg.split(":")[0].strip()  # "SpeedHive" o "MyRCM"
            if testo_attuale and not testo_attuale.startswith("Ricerca"):
                # C'e' gia' un risultato: accoda se fonte diversa
                if fonte_msg not in testo_attuale:
                    msg = "%s  |  %s" % (testo_attuale, msg)
                # Stessa fonte: sovrascrive
            self._sf_match.config(text=msg, fg=colore)
        except (tk.TclError, Exception):
            pass

    def _avvia_scouting(self):
        """Lancia LapTimer in modalita' scouting.
        Pilota vuoto e' ammesso: LapTimer parte comunque e, se il
        ricevitore LapMonitor BT e' acceso, passa a modalita' LIVE
        multi-pilota (i nomi arrivano dalla tabella Trasponder)."""
        if not LapTimer:
            return
        pilota = self._sf_pilota.get().strip()
        pilota_vuoto = not pilota
        if pilota_vuoto:
            pilota = "Multi-pilota"  # placeholder; LIVE lo sostituisce
        pista = self._sf_pista.get().strip()
        transponder = self._sf_transponder.get().strip()
        pilota_note = self._sf_pilota_note.get().strip()
        note = self._sf_note.get().strip()

        setup_name = "Scouting"
        parti = []
        if pista: parti.append(pista)
        if note: parti.append(note)
        if parti: setup_name = "Scouting - %s" % " - ".join(parti)

        scouting_dir = os.path.join(self.dati_dir, "scouting") if self.dati_dir else "scouting"
        os.makedirs(scouting_dir, exist_ok=True)
        record_id = "scout_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")

        serbatoio = self._sf_serbatoio.get().strip()

        # Salva pilota nel registro persistente (solo se compilato)
        if not pilota_vuoto:
            self._save_pilota(pilota, transponder, serbatoio, pilota_note)

        # Salva prefill per prossima volta
        self._scouting_prefill = {
            "pilota": pilota, "pista": pista, "transponder": transponder,
            "serbatoio": serbatoio,
        }

        # Passa info pilota al contesto (per l'IA)
        if pilota_note:
            self.ctx["info_pilota"] = pilota_note

        # Passa dati pista e data al contesto (matchata o libera)
        if self._matched_pista:
            self.ctx["pista"] = self._matched_pista["nome"]
            self.ctx["speedhive_id"] = self._matched_pista["speedhive_id"]
        elif pista:
            self.ctx["pista"] = pista
        if transponder:
            self.ctx["transponder"] = transponder
        # Salva data nel contesto per pulizia successiva
        data = self._sf_data.get().strip() if hasattr(self, '_sf_data') else ""
        if data and len(data) >= 10:
            self.ctx["data"] = data

        self._pulisci()
        LapTimer(setup=setup_name, pilota=pilota, pista=pista,
                 dati_dir=scouting_dir, record_id=record_id,
                 parent=self.root, on_close=lambda: self._schermata_scouting(
                     prefill=self._scouting_prefill),
                 setup_snapshot=self._build_setup_snapshot())

    def _build_setup_snapshot(self):
        """Costruisce un dizionario con i dati setup da 'fotografare' nella sessione."""
        snap = {}
        # Meteo
        for campo in ("condizioni_pista", "temp_esterna", "temp_pista", "umidita", "vento"):
            val = self.ctx.get(campo, "")
            if val:
                snap[campo] = val
        # Riferimenti setup (telaio, miscela, gomme, motore)
        for key, val in self.ctx.items():
            if key.startswith("ref_") and val:
                snap[key] = val
        # Parametri setup marcati con flag A
        if "parametri_ia" in self.ctx:
            snap["parametri_ia"] = self.ctx["parametri_ia"]
        return snap

    def _inietta_meteo(self, sessione):
        """Aggiunge dati meteo, setup e parametri IA dal contesto alla sessione."""
        # Meteo
        campi_meteo = ["condizioni_pista", "temp_esterna", "temp_pista", "umidita", "vento"]
        for campo in campi_meteo:
            val = self.ctx.get(campo, "")
            if val and campo not in sessione:
                sessione[campo] = val
        # Riferimenti setup (telaio, miscela, gomme, motore)
        for key, val in self.ctx.items():
            if key.startswith("ref_") and val and key not in sessione:
                sessione[key] = val
        # Parametri setup marcati con flag A (analisi IA)
        if "parametri_ia" in self.ctx and "parametri_ia" not in sessione:
            sessione["parametri_ia"] = self.ctx["parametri_ia"]

    # =================================================================
    #  RICERCA — importa sessioni SpeedHive + MyRCM
    # =================================================================
    def _avvia_ricerca(self):
        """Ricerca tempi su SpeedHive e MyRCM.
        - Data compilata -> cerca su entrambe le fonti, poi apre Tutti i Tempi
        - Nessuna data + Transponder -> LIVE polling SpeedHive
        """
        if not self._matched_pista:
            return
        transponder = self._sf_transponder.get().strip()
        data = self._sf_data.get().strip()
        if data:
            self._ricerca_completa(data)
        elif transponder and _HAS_SPEEDHIVE:
            # Nessuna data + Transponder -> LIVE polling
            self._avvia_speedhive_live()

    def _ricerca_completa(self, data_str, silenzioso=False):
        """Cerca tempi su TUTTE le fonti (SpeedHive + MyRCM) e apre Tutti i Tempi.

        Param `silenzioso`: se True (chiamato dal refresh automatico
        in TUTTI I TEMPI), salta la schermata di animazione "RICERCA
        TEMPI" e fa il download in background mantenendo la
        schermata corrente; al termine ricarica solo TUTTI I TEMPI."""
        c = self.c
        pista_nome = self._matched_pista.get("nome", "")
        speedhive_id = self._matched_pista.get("speedhive_id", "")
        transponder = self._sf_transponder.get().strip() if hasattr(self, '_sf_transponder') else ""
        pilota = self._sf_pilota.get().strip() if hasattr(self, '_sf_pilota') else "?"
        pilota_note = self._sf_pilota_note.get().strip() if hasattr(self, '_sf_pilota_note') else ""
        serbatoio_str = self._sf_serbatoio.get().strip() if hasattr(self, '_sf_serbatoio') else ""

        # Salva pilota nel registro persistente
        if pilota and pilota != "?":
            self._save_pilota(pilota, transponder, serbatoio_str, pilota_note)

        # Info pilota per IA
        if pilota_note:
            self.ctx["info_pilota"] = pilota_note

        # Salva prefill
        self._scouting_prefill = {
            "pilota": pilota, "pista": pista_nome,
            "transponder": transponder, "serbatoio": serbatoio_str,
        }
        # Memorizza pista+data dell'ultima ricerca completa cosi' il
        # refresh automatico in TUTTI I TEMPI puo' rilanciarla senza
        # uscire/ricompilare il form.
        self._ultima_ricerca_data = data_str
        self._ultima_ricerca_pista_match = dict(self._matched_pista or {})

        # Modo manuale (utente preme RICERCA): pulisce eventuali
        # filtri pending lasciati dal refresh silenzioso. Cosi'
        # una nuova RICERCA parte sempre con vista pulita (niente
        # filtro CERCA/FONTE residuo che nasconderebbe i tempi
        # appena scaricati). Anche in silenzioso=True i pending
        # vengono settati fresh dal chiamante (refresh).
        if not silenzioso:
            for _attr in ("_at_cerca_pending", "_at_fonte_pending"):
                if hasattr(self, _attr):
                    try:
                        delattr(self, _attr)
                    except Exception:
                        pass

        if silenzioso:
            # Modalita' silenziosa per refresh automatico:
            # niente animazione, niente _pulisci. Resta in TUTTI
            # I TEMPI durante il download. status/anim_label =
            # widget dummy (non packati) per riusare il flow di
            # _fetch_tutto qui sotto senza modifiche.
            status = tk.Label(self.root, text="")  # not packed
            anim_label = tk.Label(self.root, text="")  # not packed
            self._ricerca_attiva = False
            scouting_dir = os.path.join(self.dati_dir, "scouting") if self.dati_dir else "scouting"
            os.makedirs(scouting_dir, exist_ok=True)
        else:
            # Schermata di attesa con animazione (modo manuale =
            # bottone RICERCA premuto dall'utente)
            self._pulisci()
            tk.Label(self.root, text="RICERCA TEMPI", bg=c["sfondo"],
                     fg=c["cerca_testo"], font=self._f_title).pack(pady=(20, 10))
            status = tk.Label(self.root,
                     text="Ricerca in corso per %s..." % pista_nome,
                     bg=c["sfondo"], fg=c["stato_avviso"], font=self._f_info)
            status.pack(pady=10)
            # Barra animata puntini
            anim_label = tk.Label(self.root, text="", bg=c["sfondo"],
                                  fg=c["cerca_testo"], font=(FONT_MONO, 12, "bold"))
            anim_label.pack(pady=(4, 0))
            self._ricerca_attiva = True

            def _anima_ricerca(tick=0):
                if not self._ricerca_attiva:
                    return
                try:
                    if not anim_label.winfo_exists():
                        return
                except Exception:
                    return
                dots = "\u2588" * ((tick % 8) + 1)  # blocchi che crescono
                anim_label.config(text=dots)
                self.root.after(350, lambda: _anima_ricerca(tick + 1))

            _anima_ricerca()
            self.root.update_idletasks()

            # Check connessione internet prima di partire
            if not _check_internet():
                self._ricerca_attiva = False
                anim_label.config(text="")
                status.config(text="NESSUNA CONNESSIONE INTERNET!", fg=c["stato_errore"])
                self.root.after(3000, lambda: self._schermata_scouting(
                    prefill=getattr(self, '_scouting_prefill', None)))
                return

        scouting_dir = os.path.join(self.dati_dir, "scouting") if self.dati_dir else "scouting"
        os.makedirs(scouting_dir, exist_ok=True)
        self._refresh_silenzioso_flag = silenzioso

        scouting_dir = os.path.join(self.dati_dir, "scouting") if self.dati_dir else "scouting"
        os.makedirs(scouting_dir, exist_ok=True)

        # NON cancellare i file scouting esistenti: l'archivio va mantenuto
        # per tutti i piloti/date e filtrato dal campo CERCA in "Tutti i tempi".
        # La deduplica per transponder+data+session_id viene fatta piu' avanti
        # quando si salvano le nuove sessioni (sovrascrive solo quelle uguali).

        def _fetch_tutto():
            messaggi = []
            saved_sh = []
            saved_myrcm = []

            # --- SpeedHive ---
            if _HAS_SPEEDHIVE and speedhive_id:
                try:
                    self.root.after(0, lambda: status.config(
                        text="Ricerca SpeedHive...") if status.winfo_exists() else None)
                    from speedhive_import import (cerca_tutte_attivita_per_data,
                                                  scarica_sessioni as sh_scarica)
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    # Indice trasponder -> nome dal registro piloti.json:
                    # se l'utente ha gia' fatto ALIAS su quel chip in
                    # passato, il nome viene riutilizzato in automatico
                    # invece di ripescare chipLabel (= numero trasponder
                    # quando il pilota in SpeedHive non si e' registrato).
                    alias_per_chip = self._build_alias_per_trasponder()

                    # Indice locale {chipCode: set(session_ids)} dei file
                    # scouting GIA' presenti per questa data: serve per
                    # saltare sessioni gia' scaricate. In gara con 400-500
                    # piloti, senza questo skip ogni "ricerca" ri-processava
                    # tutto da capo (10+ minuti). Con la cache, il refresh
                    # successivo elabora SOLO le sessioni nuove.
                    # Indice locale {chip: {sid: num_giri}} cosi'
                    # possiamo skippare le sessioni gia' aggiornate
                    # MA scaricare quelle che hanno guadagnato giri
                    # (sessione live in corso). Bug pre-fix: skip
                    # secco per (chip, sid) bloccava aggiornamenti
                    # di sessioni cresciute (es. da 2 a 5 giri).
                    indice_locale = {}
                    try:
                        # Scansione ricorsiva nella struttura
                        # <anno>/<pista>/.
                        for f, full in _scouting_paths.elenca_lap_files(
                                scouting_dir):
                            try:
                                with open(full, "r", encoding="utf-8") as fp:
                                    d = json.load(fp)
                                if d.get("data") != data_str:
                                    continue
                                ch = str(d.get("transponder", "")).strip()
                                sid_l = d.get("speedhive_session")
                                ng_l = int(d.get("num_giri", 0) or 0)
                                if ch and sid_l:
                                    indice_locale.setdefault(
                                        ch, {})[sid_l] = ng_l
                            except Exception:
                                pass
                    except Exception:
                        pass

                    attivita = cerca_tutte_attivita_per_data(speedhive_id, data_str)
                    skipped_sh = 0
                    if attivita:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        # Worker che elabora UNA attivita': fa la chiamata
                        # HTTP a sh_scarica(aid) e ritorna la lista di
                        # sessioni nuove o aggiornate (piu' giri della
                        # versione locale).
                        def _processa_attivita(att):
                            local_skipped = 0
                            local_saved = []  # (chip, sid, sessione_dict)
                            try:
                                aid = att["activity_id"]
                                chip = att["chipCode"]
                                chip_str = str(chip).strip()
                                label = (alias_per_chip.get(chip_str)
                                         or att.get("chipLabel",
                                                    "Pilota_%s" % chip[-4:]))
                                sid_locali = indice_locale.get(chip_str, {})
                                dati = sh_scarica(aid)
                                if not dati or "sessions" not in dati:
                                    return local_saved, local_skipped
                                for sess in dati.get("sessions", []):
                                    sid = sess.get("id", 0)
                                    laps_remoti = sess.get("laps", []) or []
                                    n_remote = sum(
                                        1 for lap in laps_remoti
                                        if (lap.get("duration") and str(lap.get("duration")).replace(".","",1).isdigit() and float(lap.get("duration")) > 0))
                                    # SKIP solo se gia' presente con
                                    # numero di giri >= remoto (=
                                    # nessuna nuova info da scaricare).
                                    if (sid and sid in sid_locali
                                            and sid_locali[sid] >= n_remote
                                            and n_remote > 0):
                                        local_skipped += 1
                                        continue
                                    laps = sess.get("laps", [])
                                    if not laps:
                                        continue
                                    tempi = []
                                    giri_list = []
                                    for lap in laps:
                                        try:
                                            dur = float(lap.get("duration", 0))
                                        except (ValueError, TypeError):
                                            dur = 0
                                        if dur > 0:
                                            tempi.append(dur)
                                            giri_list.append({
                                                "giro": len(giri_list) + 1,
                                                "tempo": round(dur, 3),
                                                "stato": "valido",
                                            })
                                    if not giri_list:
                                        continue
                                    dt_start = sess.get("dateTimeStart", "")
                                    try:
                                        from datetime import datetime as dt_cls
                                        dt_obj = dt_cls.fromisoformat(dt_start)
                                        ora = dt_obj.strftime("%H:%M:%S")
                                    except Exception:
                                        ora = (dt_start[:8]
                                               if len(dt_start) >= 8 else "?")
                                    sessione = {
                                        "pilota": label,
                                        "setup": "SpeedHive - %s" % pista_nome,
                                        "data": data_str, "ora": ora,
                                        "tipo": "speedhive",
                                        "transponder": chip,
                                        "serbatoio_cc": 0,
                                        "sessione_carburante": False,
                                        "speedhive_session": sid,
                                        "speedhive_activity": aid,
                                        "num_giri": len(giri_list),
                                        "giri": giri_list,
                                        "miglior_tempo": round(min(tempi), 3),
                                        "media": round(
                                            sum(tempi) / len(tempi), 3),
                                        "tempo_totale": round(sum(tempi), 3),
                                    }
                                    local_saved.append((chip, sid, sessione))
                            except Exception:
                                pass
                            return local_saved, local_skipped

                        # Parallelismo controllato: 8 worker. SpeedHive
                        # regge bene questo carico e con ~500 piloti
                        # passiamo da ~10 minuti a 1-2 minuti totali.
                        nuove_sessioni = []  # (chip, sid, sessione_dict)
                        completate = 0
                        n_tot = len(attivita)
                        with ThreadPoolExecutor(max_workers=8) as exe:
                            futures = [exe.submit(_processa_attivita, att)
                                       for att in attivita]
                            for fut in as_completed(futures):
                                try:
                                    saved_local, skipped_local = fut.result()
                                except Exception:
                                    saved_local, skipped_local = [], 0
                                nuove_sessioni.extend(saved_local)
                                skipped_sh += skipped_local
                                completate += 1
                                # Aggiorna status di avanzamento
                                if completate % 10 == 0 or completate == n_tot:
                                    msg_prog = ("SpeedHive %d/%d piloti "
                                                "(%d nuove)" % (
                                                    completate, n_tot,
                                                    len(nuove_sessioni)))
                                    self.root.after(
                                        0, lambda m=msg_prog: status.config(
                                            text=m)
                                        if status.winfo_exists() else None)

                        # Scrittura SEQUENZIALE (no race su filesystem).
                        # Nome file UNIVOCO per (data, chip, sid):
                        # se _auto_speedhive_bg ha gia' scaricato lo
                        # stesso record poco prima, sovrascriviamo
                        # quel file invece di duplicarlo. Niente piu'
                        # duplicati a 2-3 secondi di distanza.
                        _data_compact = "".join(
                            ch for ch in str(data_str) if ch.isdigit())
                        for chip, sid, sessione in nuove_sessioni:
                            fname = ("lap_speedhive_%s_%s_s%d.json"
                                     % (_data_compact, str(chip)[-6:], sid))
                            path = _scouting_paths.salva_sessione(
                                sessione, scouting_dir, fname)
                            if path:
                                saved_sh.append(path)

                        if saved_sh:
                            msg = "SpeedHive: %d nuove" % len(saved_sh)
                            if skipped_sh:
                                msg += " (+%d gia' scaricate)" % skipped_sh
                            messaggi.append(msg)
                        elif skipped_sh:
                            messaggi.append(
                                "SpeedHive: nessuna nuova "
                                "(%d gia' scaricate)" % skipped_sh)
                        else:
                            messaggi.append("SpeedHive: nessun dato")
                    else:
                        messaggi.append("SpeedHive: nessun dato")
                except Exception as e:
                    messaggi.append("SpeedHive: errore (%s)" % str(e)[:40])

            # --- MyRCM ---
            if _HAS_MYRCM:
                try:
                    self.root.after(0, lambda: status.config(
                        text="Ricerca MyRCM...") if status.winfo_exists() else None)
                    s_myrcm, ev_nome = import_evento_completo(
                        pista_nome, data_str, scouting_dir)
                    if s_myrcm:
                        saved_myrcm = s_myrcm
                        messaggi.append("MyRCM: %d sessioni" % len(saved_myrcm))
                    else:
                        messaggi.append("MyRCM: nessun dato")
                except Exception as e:
                    messaggi.append("MyRCM: errore (%s)" % str(e)[:40])

            # --- Risultato ---
            tot = len(saved_sh) + len(saved_myrcm)
            # Controlla anche file scouting gia' presenti per questa data
            # (scansione ricorsiva nella struttura <anno>/<pista>/).
            n_scouting = 0
            try:
                n_scouting = len(
                    _scouting_paths.elenca_lap_files(scouting_dir))
            except Exception:
                pass

            def _mostra_risultato():
                self._ricerca_attiva = False
                # Modalita' silenziosa (refresh automatico): niente
                # update widget, ricarica diretta TUTTI I TEMPI.
                if getattr(self, "_refresh_silenzioso_flag", False):
                    self._refresh_silenzioso_flag = False
                    if tot > 0 or n_scouting > 0:
                        # Invalida la cache delle sessioni: anche se
                        # NON ricostruiamo subito la schermata, al
                        # prossimo rientro in TUTTI I TEMPI l'utente
                        # vede comunque i nuovi dati.
                        try:
                            self._tutti_sess_cache = None
                        except Exception:
                            pass
                        # Guardia: se durante il download (lento per
                        # MyRCM, 5-15s) l'utente e' uscito da TUTTI
                        # I TEMPI per andare in GRAFICO / ANALISI IA
                        # / RIVEDI, NON ricostruire la schermata
                        # (sbatterebbe fuori l'utente). Basta aver
                        # invalidato la cache: al rientro spontaneo
                        # vedra' i dati aggiornati.
                        try:
                            at_widget = getattr(self, "_at", None)
                            if at_widget is None or \
                                    not at_widget.winfo_exists():
                                # Utente non in TUTTI I TEMPI: skip
                                # rebuild
                                return
                        except (tk.TclError, AttributeError):
                            return
                        # Utente ancora in TUTTI I TEMPI: rebuild
                        # mantenendo filtri/selezione (gia' settati
                        # in pending da _refresh_silenzioso_tutti_tempi)
                        try:
                            self._schermata_tutti_tempi()
                        except Exception:
                            pass
                    return
                try:
                    if not status.winfo_exists():
                        return
                except Exception:
                    return
                try:
                    anim_label.config(text="")
                except Exception:
                    pass
                riepilogo = " | ".join(messaggi) if messaggi else "Nessuna fonte"
                if tot > 0 or n_scouting > 0:
                    status.config(
                        text="%s — Apro tutti i tempi..." % riepilogo,
                        fg=c["stato_ok"])
                    self.root.after(1200, self._schermata_tutti_tempi)
                else:
                    status.config(text=riepilogo, fg=c["stato_errore"])
                    self.root.after(3000, lambda: self._schermata_scouting(
                        prefill=getattr(self, '_scouting_prefill', None)))

            self.root.after(0, _mostra_risultato)

        threading.Thread(target=_fetch_tutto, daemon=True).start()

    def _import_result(self, saved, status_label, transponder, data_str):
        """Mostra risultato import e apre TUTTI I TEMPI."""
        c = self.c
        try:
            if not status_label.winfo_exists():
                return
            if not saved:
                status_label.config(
                    text="Nessuna sessione trovata per %s in data %s" % (transponder, data_str),
                    fg=c["stato_errore"])
                self.root.after(3000, lambda: self._schermata_scouting(
                    prefill=getattr(self, '_scouting_prefill', None)))
                return

            tot_giri = sum(s[3] for s in saved)
            status_label.config(
                text="Importate %d sessioni, %d giri totali!" % (len(saved), tot_giri),
                fg=c["stato_ok"])
        except (tk.TclError, Exception):
            pass

        # Apri TUTTI I TEMPI dopo 1 secondo per far leggere il messaggio
        self.root.after(1500, self._schermata_tutti_tempi)

    # =================================================================
    #  6. HUB LIBERO (senza setup - dal menu tabelle)
    # =================================================================
    def _schermata_hub_libero(self):
        """Menu CRONO senza setup: scouting + tutti i tempi."""
        self._pulisci()
        c = self.c

        # Header
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< MENU", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._chiudi).pack(side="left")
        tk.Label(header, text="  CRONO v" + __version__, bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(8, 0))
        # Barra batteria in alto a destra (overlay)
        _aggiungi_barra_bat(header)

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(6, 10))

        # Menu voci
        menu_frame = tk.Frame(self.root, bg=c["sfondo"])
        menu_frame.pack(padx=20, pady=10)

        btns = []
        descs = []
        voci = [
            ("NUOVA LETTURA", "Compila dati e cronometra / SpeedHive / LIVE BT", self._schermata_scouting),
            ("TUTTI I TEMPI", "Rivedi sessioni di tutti i piloti", self._schermata_tutti_tempi),
        ]

        _fg_norm = c["pulsanti_testo"]   # verde chiaro
        _bg_norm = c["pulsanti_sfondo"]  # verde scuro
        _fg_sel  = c["sfondo"]           # nero
        _bg_sel  = c["dati"]             # verde brillante

        for nome, desc, cmd in voci:
            row = tk.Frame(menu_frame, bg=c["sfondo"])
            row.pack(fill="x", pady=4)
            b = tk.Button(row, text=nome, font=self._f_hub, width=16,
                          bg=_bg_norm, fg=_fg_norm,
                          activebackground=_bg_sel, activeforeground=_fg_sel,
                          relief="ridge", bd=2, cursor="hand2", command=cmd)
            b.pack(side="left", padx=(0, 12))
            lbl = tk.Label(row, text=desc, bg=c["sfondo"], fg=c["testo_dim"],
                     font=self._f_small, anchor="w")
            lbl.pack(side="left")
            btns.append(b)
            descs.append(lbl)

        # Focus visivo bottoni gestito dal binding globale di retrodb (_kb_focus_evidenzia)
        # Qui gestiamo solo l'illuminazione della label descrizione
        for i, b in enumerate(btns):
            b.bind("<FocusIn>", lambda e, l=descs[i]: l.config(fg=c["dati"]), add="+")
            b.bind("<FocusOut>", lambda e, l=descs[i]: l.config(fg=c["testo_dim"]), add="+")
            if i < len(btns) - 1:
                b.bind("<Down>", lambda e, n=btns[i+1]: (n.focus_set(), "break")[-1])
            if i > 0:
                b.bind("<Up>", lambda e, p=btns[i-1]: (p.focus_set(), "break")[-1])

        self._top.bind("<Escape>", lambda e: self._chiudi())
        btns[0].focus_set()

    # =================================================================
    #  7. TUTTI I TEMPI (tutte le sessioni, tutti i piloti)
    # =================================================================
    def _refresh_silenzioso_tutti_tempi(self):
        """Refresh automatico TUTTI I TEMPI: ogni 30s ri-lancia
        ESATTAMENTE la stessa logica del bottone RICERCA, cosi' se
        funziona quella funziona anche questo (zero duplicazione).

        Riusa _ricerca_completa(data_str) che fa il flow standard:
          1) breve animazione "RICERCA TEMPI"
          2) download SpeedHive+MyRCM in thread bg
          3) apre _schermata_tutti_tempi con la lista aggiornata

        Prima di chiamare salviamo CERCA / FONTE / selezione / focus
        in pending; _schermata_tutti_tempi li ripristina dopo il
        rebuild (logica gia' presente).

        Il prossimo tick viene schedulato SUBITO all'inizio prima del
        ricerca_completa: cosi' anche se l'utente preme ESC durante
        l'animazione il timer continua al prossimo intervallo."""
        # Guard: schermata ancora aperta?
        if not getattr(self, "_at_refresh_attivo", False):
            return
        try:
            if not self._at.winfo_exists():
                self._at_refresh_attivo = False
                return
        except (tk.TclError, AttributeError):
            self._at_refresh_attivo = False
            return
        data_str = getattr(self, "_ultima_ricerca_data", None)
        pista_match = getattr(self, "_ultima_ricerca_pista_match",
                               None)
        if not data_str or not pista_match:
            return

        # Debounce: se l'utente ha interagito recentemente (tasto,
        # click, movimento mouse) negli ultimi 5s, posticipa il
        # refresh di altri 5s. Cosi' mentre seleziona/lavora la
        # lista non si ricostruisce sotto le mani.
        last_act = getattr(self, "_at_last_activity", 0)
        if last_act > 0 and (time.time() - last_act) < 5.0:
            try:
                self._at_refresh_after_id = self.root.after(
                    5000, self._refresh_silenzioso_tutti_tempi)
            except Exception:
                pass
            return

        # Rischedula SUBITO il prossimo tick prima di ogni altra cosa
        try:
            self._at_refresh_after_id = self.root.after(
                30000, self._refresh_silenzioso_tutti_tempi)
        except Exception:
            pass

        # Salva filtri CERCA + FONTE + selezione + focus in pending:
        # _schermata_tutti_tempi (chiamata da _ricerca_completa al
        # termine del download) li ripristinera' nei widget nuovi.
        try:
            self._at_cerca_pending = self._at_cerca_var.get()
        except Exception:
            self._at_cerca_pending = ""
        try:
            self._at_fonte_pending = self._at_fonte_var.get()
        except Exception:
            self._at_fonte_pending = "ALL"
        try:
            self._saved_selection = list(self._at.selection())
            self._saved_focus = self._at.focus()
        except Exception:
            pass

        # Re-imposta `_matched_pista` cosi' _ricerca_completa lo trova
        # (e' la stessa cosa che fa l'utente compilando il form
        # NUOVA LETTURA: poi RICERCA usa _matched_pista).
        try:
            self._matched_pista = dict(pista_match)
        except Exception:
            pass
        # Re-imposta anche _scouting_prefill (non strettamente
        # necessario ma per coerenza con il flow normale)
        if not getattr(self, "_scouting_prefill", None):
            self._scouting_prefill = {
                "pilota": "?",
                "pista": pista_match.get("nome", ""),
                "transponder": "",
                "serbatoio": "",
            }

        # Setta flag cosi' _schermata_tutti_tempi (chiamata al
        # termine di _ricerca_completa) NON rischedula un altro
        # timer (gia' schedulato all'inizio di questa funzione).
        self._at_refresh_da_finalize = True

        # Lancia RICERCA in modalita' SILENZIOSA: niente animazione
        # "RICERCA TEMPI", niente cambio schermata. Resta in TUTTI
        # I TEMPI durante il download. Al termine la lista viene
        # rebuilded mantenendo filtri/selezione/focus (via pending).
        try:
            self._ricerca_completa(data_str, silenzioso=True)
        except Exception:
            pass

    def _pulisci_myrcm_corrotti(self):
        """Elimina file MyRCM legacy salvati col bug pre-v05.05.71
        che metteva il numero di gara ('# 11') al posto del nome
        pilota nel campo 'pilota'. Cosi' al prossimo RICERCA i file
        vengono riscaricati corretti col nome reale risolto dalla
        classifica della batteria. Conservativo: tocca SOLO file con
        tipo='myrcm' E pilota matchante esattamente '# \\d+'. Ritorna
        il numero di file eliminati."""
        import re as _re
        if not self.dati_dir:
            return 0
        scouting_dir = os.path.join(self.dati_dir, "scouting")
        if not os.path.isdir(scouting_dir):
            return 0
        pattern_corrotto = _re.compile(r'^#\s*\d+\s*$')
        n_eliminati = 0
        # Scansione ricorsiva nella nuova struttura <anno>/<pista>/.
        for f, fp in _scouting_paths.elenca_lap_files(
                scouting_dir, prefisso="lap_myrcm_"):
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
            except Exception:
                continue
            if d.get("tipo") != "myrcm":
                continue
            pil = (d.get("pilota", "") or "").strip()
            if not pattern_corrotto.match(pil):
                continue
            try:
                os.remove(fp)
                n_eliminati += 1
            except Exception:
                pass
        if n_eliminati > 0:
            _scouting_paths.invalida_cache(scouting_dir)
            try:
                self._tutti_sess_cache = None
            except Exception:
                pass
        return n_eliminati

    def _pulisci_duplicati_scouting(self):
        """Elimina dal filesystem i file scouting SpeedHive duplicati
        (stesso chip + data + session_id), mantenendo il piu' recente.
        Conseguenza di un bug pre-v05.05.68 che usava timestamp nel
        nome file: due download concorrenti dello stesso record (es.
        _auto_speedhive_bg + RICERCA manuale) creavano due file
        diversi a pochi secondi di distanza.

        Sicurezza: il cleanup avviene SOLO se i due file hanno gli
        stessi giri (stesso num_giri e stessi tempi) - se differiscono
        nel contenuto, NON vengono toccati. Idempotente: chiamarlo
        piu' volte non da' problemi (la seconda volta non trova piu'
        duplicati). Ritorna il numero di file eliminati."""
        if not self.dati_dir:
            return 0
        scouting_dir = os.path.join(self.dati_dir, "scouting")
        if not os.path.isdir(scouting_dir):
            return 0
        # Raggruppa per (chip, data, sid) - solo SpeedHive (gli unici
        # che avevano il bug del timestamp nel nome).
        # Scansione ricorsiva nella struttura <anno>/<pista>/.
        gruppi = {}
        for f, fp in _scouting_paths.elenca_lap_files(
                scouting_dir, prefisso="lap_speedhive_"):
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
            except Exception:
                continue
            if d.get("tipo") != "speedhive":
                continue
            chip = str(d.get("transponder", "")).strip()
            data = d.get("data", "")
            sid = d.get("speedhive_session")
            if not chip or not data or sid is None:
                continue
            key = (chip, data, sid)
            try:
                mt = os.path.getmtime(fp)
            except Exception:
                mt = 0
            tempi_giri = tuple(g.get("tempo", 0)
                                for g in (d.get("giri") or []))
            gruppi.setdefault(key, []).append({
                "path": fp, "mtime": mt,
                "num_giri": d.get("num_giri", 0),
                "tempi_giri": tempi_giri,
            })

        n_eliminati = 0
        for key, lst in gruppi.items():
            if len(lst) < 2:
                continue
            # Verifica che TUTTI i file abbiano gli stessi giri:
            # se differiscono e' meglio non toccarli.
            base = lst[0]
            tutti_uguali = all(
                (it["num_giri"] == base["num_giri"]
                 and it["tempi_giri"] == base["tempi_giri"])
                for it in lst[1:])
            if not tutti_uguali:
                continue
            # Mantieni il piu' recente, elimina gli altri
            lst.sort(key=lambda it: it["mtime"], reverse=True)
            for it in lst[1:]:
                try:
                    os.remove(it["path"])
                    n_eliminati += 1
                except Exception:
                    pass
        # Invalida la cache _trova_tutte_sessioni (se popolata)
        if n_eliminati > 0:
            _scouting_paths.invalida_cache(scouting_dir)
            try:
                self._tutti_sess_cache = None
            except Exception:
                pass
        return n_eliminati

    def _trova_tutte_sessioni(self):
        """Cerca sessioni lap_*.json.
        Modalita' setup: dati/ + dati/scouting/ (tutto del setup).
        Modalita' libera (da menu): solo dati/scouting/ (le sessioni setup
        si vedono dal CRONO del rispettivo setup).
        Sostituisce i nomi anonimi (es. "Trasp. 1234567" o numero
        nudo "1234567") con il nome reale se il transponder e' nella
        tabella trasponder.json (importata da MyRCM partecipanti)
        o nel registro piloti.json (alias manuali).

        Cache in RAM: la prima apertura legge tutti i file (lento
        su SD uConsole con 400+ file), le successive riusano la
        cache. Invalidata automaticamente se cambia il numero di
        file o il piu' recente mtime (= aggiunta/modifica/rimozione
        di un file scouting da import SpeedHive, MyRCM o LapTimer)."""
        sessioni = []
        paths = []
        if not self.dati_dir or not os.path.isdir(self.dati_dir):
            return sessioni, paths

        modo_setup = hasattr(self, '_modo_setup') and self._modo_setup

        # One-shot: pulizia duplicati SpeedHive (legacy, da bug
        # pre-v05.05.68 che usava timestamp nel nome). Eseguita una
        # sola volta per sessione di TrackMind, all'apertura di
        # TUTTI I TEMPI.
        if not getattr(self, "_duplicati_puliti", False):
            try:
                n_eli = self._pulisci_duplicati_scouting()
                if n_eli > 0:
                    print("[crono] eliminati %d file scouting "
                          "duplicati (SpeedHive legacy)" % n_eli)
            except Exception as e:
                print("[crono] errore pulizia duplicati: %s" % e)
            try:
                n_myrcm = self._pulisci_myrcm_corrotti()
                if n_myrcm > 0:
                    print("[crono] eliminati %d file MyRCM legacy "
                          "(pilota '# NR' -> riscaricabili)" % n_myrcm)
            except Exception as e:
                print("[crono] errore pulizia myrcm: %s" % e)
            self._duplicati_puliti = True

        # Calcola signature dei file scouting per cache invalidation
        scouting_dir_check = os.path.join(self.dati_dir, "scouting")
        signature = None
        try:
            n_files = 0
            max_mtime = 0.0
            if os.path.isdir(scouting_dir_check):
                for f in os.listdir(scouting_dir_check):
                    if f.endswith(".json"):
                        n_files += 1
                        try:
                            m = os.path.getmtime(
                                os.path.join(scouting_dir_check, f))
                            if m > max_mtime:
                                max_mtime = m
                        except Exception:
                            pass
            # In modo_setup includiamo anche dati/lap_*.json
            n_files_setup = 0
            max_mtime_setup = 0.0
            if modo_setup:
                for f in os.listdir(self.dati_dir):
                    if f.startswith("lap_") and f.endswith(".json"):
                        n_files_setup += 1
                        try:
                            m = os.path.getmtime(
                                os.path.join(self.dati_dir, f))
                            if m > max_mtime_setup:
                                max_mtime_setup = m
                        except Exception:
                            pass
            # Includiamo anche mtime di trasponder.json e piloti.json
            # cosi' la cache si invalida se l'utente fa ALIAS o
            # importa partecipanti (gli alias cambiano).
            mt_extra = 0.0
            for nome in ("trasponder.json", "piloti.json"):
                fp = os.path.join(self.dati_dir, nome)
                if os.path.exists(fp):
                    try:
                        m = os.path.getmtime(fp)
                        if m > mt_extra:
                            mt_extra = m
                    except Exception:
                        pass
            signature = (modo_setup, n_files, max_mtime,
                         n_files_setup, max_mtime_setup, mt_extra)
        except Exception:
            signature = None

        # Cache hit?
        cached = getattr(self, "_tutti_sess_cache", None)
        if (signature is not None and cached is not None
                and cached.get("signature") == signature):
            # Ritorna copia delle liste (i dict sono shared, ma le
            # liste no, cosi' chi modifica l'ordine non rompe la
            # cache).
            return list(cached["sessioni"]), list(cached["paths"])

        # Indice unificato {transponder: nome} da piloti.json +
        # trasponder.json. Costruito una sola volta qui per non
        # rileggerlo per ogni file scouting.
        alias_per_chip = self._build_alias_per_trasponder()

        def _applica_alias(sess):
            """Se il nome pilota sembra anonimo (numero nudo,
            "Trasp", "Pilota_NNN" o stringhe tipo "1137349 [0]"
            che SpeedHive usa quando il pilota non ha nome) e c'e'
            un alias per il suo transponder, sostituisce il nome
            al volo (NON tocca il file su disco: la modifica e'
            solo sulla vista di TUTTI I TEMPI)."""
            if not alias_per_chip:
                return
            chip = str(sess.get("transponder", "")).strip()
            if not chip:
                return
            nome_giusto = alias_per_chip.get(chip)
            if not nome_giusto:
                return
            nome_attuale = (sess.get("pilota", "") or "").strip()
            # "Anonimo" se NON contiene LETTERE (a-z): cosi' matcha
            # numeri nudi tipo "1137349", "1137349 [0]", "12345/0",
            # ma NON nomi reali tipo "Mollo Felice" o "Bosa Raffaello".
            # Match anche per prefissi noti "Trasp"/"Pilota_" che
            # contengono lettere ma sono placeholder generici.
            ha_lettere = any(ch.isalpha() for ch in nome_attuale)
            nome_low = nome_attuale.lower()
            anon = (
                not nome_attuale or
                nome_attuale == "?" or
                nome_attuale == chip or
                not ha_lettere or
                nome_low.startswith("trasp") or
                nome_low.startswith("pilota_")
            )
            if anon:
                sess["pilota"] = nome_giusto

        # Sessioni da dati/ (solo in modalita' setup)
        if modo_setup:
            for f in sorted(os.listdir(self.dati_dir), reverse=True):
                if f.startswith("lap_") and f.endswith(".json"):
                    fp = os.path.join(self.dati_dir, f)
                    try:
                        with open(fp, "r", encoding="utf-8") as fh:
                            s = json.load(fh)
                        s["_fonte"] = "Setup"
                        _applica_alias(s)
                        sessioni.append(s)
                        paths.append(fp)
                    except Exception:
                        pass

        # Sessioni da dati/scouting/ (struttura ad albero <anno>/<pista>/).
        scouting_dir = os.path.join(self.dati_dir, "scouting")
        if os.path.isdir(scouting_dir):
            # Ordina per nome basename in reverse (compat ordinamento
            # precedente, poi viene riordinato per data/ora).
            elenco = _scouting_paths.elenca_lap_files(scouting_dir)
            elenco.sort(key=lambda t: t[0], reverse=True)
            for f, fp in elenco:
                try:
                    with open(fp, "r", encoding="utf-8") as fh:
                        s = json.load(fh)
                    s["_fonte"] = "Scouting"
                    _applica_alias(s)
                    sessioni.append(s)
                    paths.append(fp)
                except Exception:
                    pass

        # Ordina per data/ora (piu' recenti prima)
        # Normalizza data per sorting: GG/MM/AAAA -> AAAA-MM-GG, ISO resta uguale
        def _sort_data(d):
            d = d.strip()
            if "/" in d and len(d) == 10:
                return "%s-%s-%s" % (d[6:10], d[3:5], d[0:2])
            return d  # ISO o altro formato gia' ordinabile
        combined = list(zip(sessioni, paths))
        combined.sort(key=lambda x: (_sort_data(x[0].get("data", "")), x[0].get("ora", "")), reverse=True)
        sessioni = [c[0] for c in combined]
        paths = [c[1] for c in combined]
        # Salva in cache per riusare al prossimo open di TUTTI I TEMPI
        # senza rileggere 400+ file da SD card. Invalidata
        # automaticamente al cambio signature (vedi sopra).
        if signature is not None:
            self._tutti_sess_cache = {
                "signature": signature,
                "sessioni": sessioni,
                "paths": paths,
            }
        return sessioni, paths

    def _filtra_da_selezione(self):
        """Prende pista dalla sessione con focus e la mette nel campo CERCA."""
        c = self.c
        # Se CERCA ha gia' testo: pulisci (toggle)
        if hasattr(self, '_at_cerca_var') and self._at_cerca_var.get().strip():
            self._at_cerca_var.set("")
            self._at.focus_set()
            return
        focused = self._at.focus()
        if not focused:
            sel = self._at.selection()
            if sel: focused = sel[0]
        if not focused:
            return
        try:
            idx = int(focused)
        except (ValueError, TypeError):
            return
        if idx < 0 or idx >= len(self._tutti_sessioni):
            return
        s = self._tutti_sessioni[idx]
        # Estrai pista dalla sessione
        pista = self._pista_da_sessione(s)
        if not pista:
            pista = s.get("pilota", "").strip()
        if not pista:
            return
        # Metti nel campo CERCA
        if hasattr(self, '_at_cerca_var'):
            self._at_cerca_var.set(pista)
            self._at.focus_set()

    def _schermata_tutti_tempi(self):
        """Lista TUTTE le sessioni di tutti i piloti (o solo quelle del setup corrente)."""
        # Modalita' setup: dati pre-caricati in _tutti_sessioni/_tutti_paths
        if hasattr(self, '_modo_setup') and self._modo_setup:
            sessioni = self._tutti_sessioni
            paths = self._tutti_paths
            titolo = "TEMPI  |  %s" % self.ctx.get("setup_name", "?")
            back_cmd = self._schermata_hub
        else:
            sessioni, paths = self._trova_tutte_sessioni()
            self._tutti_sessioni = sessioni
            self._tutti_paths = paths
            titolo = "TUTTI I TEMPI"
            # Back: torna a hub setup se vengo da li', altrimenti hub libero
            if getattr(self, '_tutti_tempi_back_setup', False):
                back_cmd = self._ripristina_da_tutti
            else:
                back_cmd = self._schermata_hub_libero
            self._tempi_on_close = self._schermata_tutti_tempi

        if not sessioni:
            return

        self._pulisci()
        c = self.c

        # Header
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< CRONO", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=back_cmd).pack(side="left")
        tk.Label(header, text="  %s" % titolo,
                 bg=c["sfondo"], fg=c["dati"], font=self._f_title).pack(side="left", padx=(8, 0))
        # Barra batteria all'estrema destra: la impacchetto PRIMA del
        # contatore cosi' con side="right" la barra resta piu' a destra
        # e "N sessioni" le si piazza a fianco verso sinistra.
        try:
            from core.sd_bar import BarraBatteria as _BarraBat
            from core.batteria import get_batteria_info as _get_bat_info
            _pct, _ = _get_bat_info()
            if _pct is not None:
                _BarraBat(header, get_info_func=_get_bat_info).pack(
                    side="right", padx=(6, 0))
        except Exception:
            pass
        self._at_header_count = tk.Label(header, text="%d sessioni" % len(sessioni),
                 bg=c["sfondo"], fg=c["stato_avviso"], font=self._f_small)
        self._at_header_count.pack(side="right")

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(4, 4))

        # ── Barra CERCA (filtro in tempo reale) ──
        search_bar = tk.Frame(self.root, bg=c["sfondo"])
        search_bar.pack(fill="x", padx=10, pady=(2, 0))
        tk.Label(search_bar, text="CERCA:", bg=c["sfondo"], fg=c["cerca_testo"],
                 font=(FONT_MONO, 9, "bold")).pack(side="left")
        self._at_cerca_var = tk.StringVar()
        self._at_search_entry = tk.Entry(search_bar, font=(FONT_MONO, 10), width=30,
                 bg=c["sfondo_celle"], fg=c["dati"], insertbackground=c["dati"],
                 relief="flat", highlightthickness=1,
                 highlightbackground=c["linee"],
                 highlightcolor=c["cerca_testo"],
                 textvariable=self._at_cerca_var)
        self._at_search_entry.pack(side="left", padx=(4, 8), fill="x", expand=True)
        self._at_count_label = tk.Label(search_bar, text="%d sessioni" % len(sessioni),
                 bg=c["sfondo"], fg=c["testo_dim"], font=(FONT_MONO, 9))
        self._at_count_label.pack(side="right")

        # ── Barra FONTE: filtro per tipo di sessione ──
        # Riga separata sotto CERCA. Pulsanti radio-style (uno solo
        # attivo alla volta), colori di sfondo coordinati con i tag
        # delle righe nel Treeview cosi' la corrispondenza visiva e'
        # immediata: pulsante MR rosso = righe rosse MyRCM ecc.
        fonte_bar = tk.Frame(self.root, bg=c["sfondo"])
        fonte_bar.pack(fill="x", padx=10, pady=(0, 2))
        tk.Label(fonte_bar, text="FONTE:", bg=c["sfondo"],
                 fg=c["cerca_testo"],
                 font=(FONT_MONO, 9, "bold")).pack(side="left")
        self._at_fonte_var = tk.StringVar(value="ALL")
        self._at_fonte_btns = {}
        # (chiave_tag, label, colore_attivo)
        fonte_btn_def = [
            ("ALL",       "TUTTE", c["dati"]),
            ("speedhive", "SH",    "#00e0ff"),
            ("myrcm",     "MR",    "#ff4040"),
            ("scouting",  "LT",    "#ffaa00"),
            ("setup",     "ST",    "#39ff14"),
        ]

        def _aggiorna_fonte_btns():
            cur = self._at_fonte_var.get()
            for k, b in self._at_fonte_btns.items():
                if k == cur:
                    # Pulsante attivo: sfondo del colore della fonte +
                    # testo nero per contrasto massimo
                    col = dict((kk, cc) for kk, _, cc
                               in fonte_btn_def).get(k, c["dati"])
                    b.config(bg=col, fg=c["sfondo"])
                else:
                    b.config(bg=c["pulsanti_sfondo"],
                              fg=c["pulsanti_testo"])

        def _switch_fonte(key):
            self._at_fonte_var.set(key)
            _aggiorna_fonte_btns()
            # Trigger del filtro (riusa lo stesso _at_filtra che
            # combina CERCA + FONTE - vedi piu' sotto)
            try:
                _at_filtra()
            except NameError:
                # _at_filtra definita dopo: nessuna azione (alla
                # creazione iniziale lo stato e' "ALL" e non serve
                # filtrare).
                pass

        # Lista per gestire navigazione frecce sx/dx tra i pulsanti
        fonte_btn_lista = []
        for key, lbl, _col in fonte_btn_def:
            b = tk.Button(fonte_bar, text=lbl,
                          font=(FONT_MONO, 9, "bold"),
                          bg=c["pulsanti_sfondo"],
                          fg=c["pulsanti_testo"],
                          relief="ridge", bd=1, width=5, cursor="hand2",
                          takefocus=1,
                          command=lambda k=key: _switch_fonte(k))
            b.pack(side="left", padx=1)
            self._at_fonte_btns[key] = b
            fonte_btn_lista.append((key, b))
        _aggiorna_fonte_btns()

        # Navigazione tastiera fra pulsanti FONTE:
        #   <-/-> = sposta focus tra i pulsanti
        #   Enter = attiva il filtro del pulsante focuserato (Tk
        #           gestisce gia' Space, ma non Return - lo aggiungiamo)
        # NB: TAB usa l'ordine di creazione widget Tk, quindi dal CERCA
        # passa al primo pulsante TUTTE, poi SH, MR, LT, ST, infine
        # nelle righe del Treeview.
        for idx, (key, b) in enumerate(fonte_btn_lista):
            # Frecce sx/dx: navigazione circolare tra pulsanti FONTE
            prev_b = fonte_btn_lista[idx - 1][1] if idx > 0 \
                else fonte_btn_lista[-1][1]
            next_b = fonte_btn_lista[idx + 1][1] \
                if idx < len(fonte_btn_lista) - 1 \
                else fonte_btn_lista[0][1]
            b.bind("<Left>",
                   lambda e, w=prev_b: (w.focus_set(), "break")[-1])
            b.bind("<Right>",
                   lambda e, w=next_b: (w.focus_set(), "break")[-1])
            # Enter -> invoca il command come fa Spazio di default
            b.bind("<Return>",
                   lambda e, k=key: (_switch_fonte(k), "break")[-1])
            # Evidenziatore di focus: bordo piu' marcato quando attivo
            b.bind("<FocusIn>",
                   lambda e, w=b: w.config(bd=2, relief="ridge"))
            b.bind("<FocusOut>",
                   lambda e, w=b: w.config(bd=1, relief="ridge"))

        # Stile Treeview
        style = ttk.Style(); style.theme_use("clam")
        style.configure("TT.Treeview",
            background=c["sfondo_celle"], foreground=c["dati"],
            fieldbackground=c["sfondo_celle"], font=(FONT_MONO, 10),
            rowheight=22, borderwidth=0)
        style.configure("TT.Treeview.Heading",
            background=c["pulsanti_sfondo"], foreground=c["pulsanti_testo"],
            font=(FONT_MONO, 10, "bold"), borderwidth=1, relief="ridge")
        style.map("TT.Treeview",
            background=[("selected", c["cursore"])],
            foreground=[("selected", c["testo_cursore"])])

        # Treeview
        tree_frame = tk.Frame(self.root, bg=c["sfondo"])
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(2, 4))

        # Colonne diverse in modalita' setup vs tutti i tempi
        modo_s = hasattr(self, '_modo_setup') and self._modo_setup
        if modo_s:
            cols = ("data", "ora", "record", "fonte", "giri", "best", "media")
            col_defs = [
                ("data", "Data", 78), ("ora", "Ora", 42),
                ("record", "Record", 90), ("fonte", "Fonte", 75),
                ("giri", "Giri", 38), ("best", "Best", 72), ("media", "Media", 72)]
        else:
            cols = ("data", "ora", "pilota", "evento", "fase", "giri", "best", "media")
            col_defs = [
                ("data", "Data", 78), ("ora", "Ora", 42),
                ("pilota", "Pilota", 130), ("evento", "Evento/Pista", 170),
                ("fase", "Fase", 75), ("giri", "Giri", 38),
                ("best", "Best", 72), ("media", "Media", 72)]
        self._at = ttk.Treeview(tree_frame, columns=cols,
                                show="headings", style="TT.Treeview", selectmode="extended")
        for col, tit, w in col_defs:
            self._at.heading(col, text=tit, anchor="w")
            self._at.column(col, width=w, anchor="w")

        vsb = tk.Scrollbar(tree_frame, orient="vertical", command=self._at.yview)
        vsb.pack(side="right", fill="y")
        self._at.configure(yscrollcommand=vsb.set)
        self._at.pack(side="left", fill="both", expand=True)
        # v05.06.39: highlight visibile riga corrente al focus
        # (usa tag distinto 'focus_riga' per non collidere con il
        # tag 'focused' gia' usato altrove nel codice TUTTI I TEMPI)
        try:
            from focus_ui import evidenzia_treeview
            evidenzia_treeview(self._at, colori=c, tag_name="focus_riga")
        except Exception as _e:
            print("[crono] focus_ui non disponibile:", _e)

        # Tag colori per fonte. SOLO foreground - usare anche
        # background interferiva con la barra di selezione/focus
        # (tag_focused veniva sovrastato dal background del tag
        # fonte, la barra evidenziatrice spariva).
        # Colori molto distinti tra loro per riconoscere a colpo
        # d'occhio la fonte di ciascuna riga:
        #   setup     verde neon    #39ff14 (dati di test del setup)
        #   scouting  arancio       #ffaa00 (LapTimer / LapMonitor)
        #   speedhive ciano elettr. #00e0ff (prove live SpeedHive)
        #   myrcm     rosso brill.  #ff4040 (gare MyRCM importate)
        self._at.tag_configure("setup", foreground="#39ff14")
        self._at.tag_configure("scouting", foreground="#ffaa00")
        self._at.tag_configure("speedhive", foreground="#00e0ff")
        self._at.tag_configure("myrcm", foreground="#ff4040")
        # Tag per record corrente (evidenziato) vs altri record (piu' scuro)
        self._at.tag_configure("rec_corrente", foreground=c["dati"])
        self._at.tag_configure("rec_altro", foreground=c["testo_dim"])

        cur_rec_id = getattr(self, '_modo_setup_record_id', '') if modo_s else ''

        for i, s in enumerate(sessioni):
            data = _data_ita(s.get("data", "?"))
            ora = s.get("ora", "?")[:5]
            n_giri = s.get("num_giri", 0)
            best = _fmt(s.get("miglior_tempo", 0))
            media = _fmt(s.get("media", 0))

            if modo_s:
                # Modalita' setup: mostra record e fonte
                rec_id = s.get("record_id", "?")
                # Label record: es. "rec_5" -> "#5"
                # Label leggibile: id_a1b2c3d4 -> "#a1b2", rec_5 -> "#5"
                if rec_id.startswith("id_"):
                    rec_label = "#%s" % rec_id[3:7]
                elif rec_id.startswith("rec_"):
                    rec_label = "#%s" % rec_id[4:]
                else:
                    rec_label = rec_id[:8]
                if rec_id == cur_rec_id:
                    rec_label += " *"  # Asterisco = record corrente
                fonte_raw = s.get("tipo", "?")
                if fonte_raw == "speedhive":
                    fonte = "SpeedHive"
                elif fonte_raw == "myrcm":
                    fonte = "MyRCM"
                else:
                    fonte = "LapTimer"
                # Tag: record corrente evidenziato, altri smorzati
                tag = "rec_corrente" if rec_id == cur_rec_id else "rec_altro"
                self._at.insert("", "end", iid=str(i),
                    values=(data, ora, rec_label, fonte, n_giri, best, media),
                    tags=(tag,))
            else:
                # Modalita' tutti i tempi: mostra pilota, pista, fase
                pilota = s.get("pilota", "?")[:20]
                # Campo pista esplicito (nuovo), fallback su setup
                pista = s.get("pista", "").strip()
                if not pista:
                    setup_raw = s.get("setup", "?")
                    pista = setup_raw
                    for prefisso in ("SpeedHive - ", "Scouting - ", "MyRCM - "):
                        if pista.startswith(prefisso):
                            pista = pista[len(prefisso):]
                            break
                pista = pista[:35]
                fase = ""
                fonte_raw = s.get("tipo", s.get("_fonte", "?"))
                if fonte_raw == "myrcm":
                    tag = "myrcm"
                    # Sessioni MyRCM hanno tutte ora=00:00:00 (l'API
                    # non la espone). Per distinguere le manche di
                    # uno stesso pilota nella stessa data, estraggo
                    # un'etichetta "Q1.M4" / "F1.A" / "Pr1.M2" da
                    # `myrcm_sessione_nome` (es. "Manche 4 - Qualif 1").
                    import re as _re
                    sn = s.get("myrcm_sessione_nome", "")
                    sn_low = sn.lower()
                    m_man = _re.search(
                        r"manche\s*(\d+)|group\s*(\d+)|batteria\s*(\d+)",
                        sn_low)
                    man_n = ""
                    if m_man:
                        man_n = (m_man.group(1) or m_man.group(2)
                                 or m_man.group(3) or "")
                    if "finals" in sn_low or "finale" in sn_low:
                        m_f = _re.search(
                            r"final[s]?\s*([a-z]|\d+)", sn_low)
                        f_id = (m_f.group(1).upper()
                                if m_f else "")
                        fase = "F%s" % f_id if f_id else "Finale"
                    elif "qualif" in sn_low:
                        m_q = _re.search(r"qualif[a-z]*\s*(\d+)",
                                          sn_low)
                        q_n = m_q.group(1) if m_q else ""
                        fase = ("Q%s.M%s" % (q_n, man_n)
                                if q_n and man_n
                                else ("Qualif M%s" % man_n
                                      if man_n else "Qualif"))
                    elif "prove" in sn_low:
                        m_p = _re.search(r"prove\s*(\d+)", sn_low)
                        p_n = m_p.group(1) if m_p else ""
                        fase = ("Pr%s.M%s" % (p_n, man_n)
                                if p_n and man_n
                                else ("Prove M%s" % man_n
                                      if man_n else "Prove"))
                    else:
                        fase = "Gara"
                elif fonte_raw == "speedhive":
                    tag = "speedhive"
                    fase = "Libere"
                elif fonte_raw in ("Scouting", "laptimer") or "scout" in str(s.get("setup", "")).lower():
                    tag = "scouting"
                    fase = "Libere"
                elif fonte_raw == "lapmonitor":
                    tag = "scouting"
                    fase = "LapMon"
                else:
                    tag = "setup"
                    fase = "Setup"
                # Prefisso fonte nella colonna fase: cosi' anche su
                # display dove il colore del tag non rende bene
                # (terminali, dark/light theme), la fonte e'
                # comunque chiara dal testo:
                #   [SH] SpeedHive | [MR] MyRCM | [LT] LapTimer/scout
                #   [LM] LapMonitor | [ST] Setup
                _prefix = {
                    "speedhive": "[SH] ",
                    "myrcm": "[MR] ",
                    "scouting": "[LT] ",
                    "setup": "[ST] ",
                }.get(tag, "")
                fase_label = _prefix + fase
                self._at.insert("", "end", iid=str(i),
                    values=(data, ora, pilota, pista, fase_label,
                            n_giri, best, media),
                    tags=(tag,))

        # ── Cache righe per filtro CERCA ──
        self._at_all_rows = []
        for child in self._at.get_children():
            vals = self._at.item(child, "values")
            tag = self._at.item(child, "tags")
            self._at_all_rows.append((child, vals, tag))

        def _at_filtra(*args):
            """Filtra righe nel Treeview in tempo reale combinando:
            - CERCA: parole multiple (AND), case-insensitive
            - FONTE: filtra per tag fonte (speedhive/myrcm/scouting/setup)
            'angelino ponte' trova righe con entrambe le parole; il
            filtro fonte si applica in AND con la ricerca testuale."""
            testo = self._at_cerca_var.get().strip().lower()
            parole = testo.split() if testo else []
            fonte_sel = (getattr(self, "_at_fonte_var", None).get()
                         if getattr(self, "_at_fonte_var", None)
                         else "ALL")
            self._at.delete(*self._at.get_children())
            count = 0
            for iid, vals, tags in self._at_all_rows:
                # Filtro fonte (in AND)
                if fonte_sel != "ALL":
                    if fonte_sel not in (tags or ()):
                        continue
                # Filtro testo (in AND)
                if not parole:
                    match_t = True
                else:
                    riga = " ".join(str(v).lower() for v in vals)
                    match_t = all(p in riga for p in parole)
                if match_t:
                    self._at.insert("", "end", iid=iid, values=vals, tags=tags)
                    count += 1
            tot = len(self._at_all_rows)
            filtro_attivo = bool(testo) or fonte_sel != "ALL"
            if filtro_attivo:
                self._at_count_label.config(
                    text="%d/%d trovati" % (count, tot), fg=c["cerca_testo"])
                self._at_header_count.config(
                    text="%d/%d" % (count, tot), fg=c["cerca_testo"])
            else:
                self._at_count_label.config(
                    text="%d sessioni" % tot, fg=c["testo_dim"])
                self._at_header_count.config(
                    text="%d sessioni" % tot, fg=c["stato_avviso"])
            # Focus sul primo risultato
            children_f = self._at.get_children()
            if children_f:
                self._at.focus(children_f[0])
                self._at.see(children_f[0])

        self._at_cerca_var.trace_add("write", _at_filtra)
        self._at_search_entry.bind("<Escape>",
            lambda e: (self._at_cerca_var.set(""), self._at.focus_set()))
        self._at_search_entry.bind("<Return>", lambda e: self._at.focus_set())
        self._at_search_entry.bind("<Down>", lambda e: self._at.focus_set())

        # Ripristino filtri CERCA + FONTE dopo auto-refresh:
        # _refresh_silenzioso_finalizza salva _at_cerca_pending e
        # _at_fonte_pending prima del rebuild di questa schermata,
        # qui li riapplichiamo cosi' l'utente non perde la sua
        # ricerca/filtro quando arrivano nuovi tempi in background.
        cerca_save = getattr(self, "_at_cerca_pending", None)
        fonte_save = getattr(self, "_at_fonte_pending", None)
        if fonte_save and fonte_save != "ALL":
            try:
                self._at_fonte_var.set(fonte_save)
                _aggiorna_fonte_btns()
            except Exception:
                pass
        if cerca_save:
            try:
                # Settando il var scatta automaticamente il trace
                # _at_filtra che applica il filtro sulle righe.
                self._at_cerca_var.set(cerca_save)
            except Exception:
                pass
        elif fonte_save and fonte_save != "ALL":
            # CERCA vuoto ma FONTE attivo: chiama _at_filtra una
            # volta per applicare il filtro fonte
            try:
                _at_filtra()
            except Exception:
                pass
        # Pulisci i pending dopo l'uso (one-shot)
        for _attr in ("_at_cerca_pending", "_at_fonte_pending"):
            try:
                if hasattr(self, _attr):
                    delattr(self, _attr)
            except Exception:
                pass

        # v05.06.42: tag "focused" mantiene SEMPRE i colori verdi
        # fluo (bg=cursore, fg=testo_cursore). Per nasconderlo
        # quando il widget non ha focus, RIMUOVIAMO fisicamente
        # il tag dalle righe (invece di cambiare tag_configure
        # runtime, che su Tk 8.6 ARM/uConsole non sempre viene
        # ridisegnato fino al prossimo redraw forzato).
        # Questo approccio funziona cross-platform (PC + uConsole).
        self._at.tag_configure("focused",
            background=c["cursore"], foreground=c["testo_cursore"])
        # Tag per righe selezionate per IA (✓): sempre visibile
        # (anche senza focus l'utente deve vedere la pre-selezione)
        self._at.tag_configure("checked",
            background=c["pulsanti_sfondo"], foreground=c["dati"])

        # Bind focus-aware: al FocusIn (armed) il tag "focused"
        # viene RIAPPLICATO sulla riga corrente; al FocusOut
        # viene RIMOSSO da tutte le righe. Cambio del tag sulla
        # singola riga forza il re-rendering immediato anche su
        # Tk 8.6 ARM (uConsole).
        def _at_focus_in(_e=None):
            if not getattr(self._at, "_focus_ui_armed", False):
                return
            try:
                foc = self._at.focus()
                if foc:
                    cur_tags = list(self._at.item(foc, "tags") or [])
                    if "focused" not in cur_tags:
                        cur_tags.append("focused")
                        self._at.item(foc, tags=tuple(cur_tags))
            except Exception:
                pass

        def _at_focus_out(_e=None):
            # Rimuovi tag "focused" da TUTTE le righe = nessuna
            # evidenziazione visibile senza focus
            try:
                for iid in self._at.get_children(""):
                    cur_tags = list(self._at.item(iid, "tags") or [])
                    if "focused" in cur_tags:
                        cur_tags.remove("focused")
                        self._at.item(iid, tags=tuple(cur_tags))
            except Exception:
                pass

        self._at.bind("<FocusIn>", _at_focus_in, add="+")
        self._at.bind("<FocusOut>", _at_focus_out, add="+")

        self._prev_focused = None
        self._prev_sel = set()
        def _set_riga_tags(child, focused, sel):
            """Applica i tag focused/checked a UNA singola riga.
            v05.06.42: il tag "focused" viene applicato SOLO se
            il widget ha effettivamente il focus tastiera (controllo
            tramite il flag _focus_ui_has_focus settato dall'helper
            evidenzia_treeview). Cosi' anche se _aggiorna_selezione
            viene chiamato durante la popolazione iniziale (quando
            Tk auto-focusa il widget), nessuna riga appare verde
            finche' l'utente non interagisce davvero."""
            try:
                cur_tags = [t for t in self._at.item(child, "tags")
                            if t not in ("focused", "checked")]
                ha_focus_ui = bool(getattr(
                    self._at, "_focus_ui_has_focus", False))
                if child == focused and ha_focus_ui:
                    cur_tags.append("focused")
                elif child in sel:
                    cur_tags.append("checked")
                self._at.item(child, tags=tuple(cur_tags))
            except Exception:
                pass

        def _aggiorna_selezione(event=None):
            sel = set(self._at.selection())
            focused = self._at.focus()
            # Aggiorna SOLO le righe cambiate (delta), non tutte le
            # 400+ righe del treeview. Cosi' Up/Down e' istantaneo
            # invece di causare il freeze 200-400ms tipico del loop
            # `for child in get_children()` su grossi dataset.
            cambiate = set()
            # Riga focus precedente: va ridisegnata (perde "focused")
            if self._prev_focused and self._prev_focused != focused:
                cambiate.add(self._prev_focused)
            # Riga focus attuale: va ridisegnata (acquista "focused")
            if focused:
                cambiate.add(focused)
            # Righe selezionate/deselezionate dall'ultimo update
            # (XOR tra vecchia selezione e nuova)
            cambiate.update(self._prev_sel ^ sel)
            for child in cambiate:
                _set_riga_tags(child, focused, sel)
            self._prev_focused = focused
            self._prev_sel = sel
            # Aggiorna conteggio selezione
            n_sel = len(sel)
            if n_sel > 1:
                self._tutti_status.config(
                    text="%d sessioni selezionate  |  Shift+\u2191\u2193 = selezione multipla  |  ANALISI GIORNATA = IA su tutte" % n_sel,
                    fg=c["cerca_testo"])
            else:
                self._tutti_status.config(
                    text="RIVEDI = analisi giri  |  ELIMINA = cancella  |  Shift+\u2191\u2193 = selezione multipla",
                    fg=c["testo_dim"])

        self._at.bind("<<TreeviewSelect>>", _aggiorna_selezione)

        # Spazio = toggle selezione riga con focus
        def _toggle_sel(e):
            focused = self._at.focus()
            if not focused: return "break"
            sel = set(self._at.selection())
            if focused in sel:
                sel.discard(focused)
            else:
                sel.add(focused)
            self._at.selection_set(list(sel))
            return "break"
        self._at.bind("<space>", _toggle_sel)

        # Frecce: muovi SOLO il focus, NON toccare la selezione
        def _move_focus(direction):
            children = self._at.get_children()
            if not children: return
            focused = self._at.focus()
            if not focused:
                self._at.focus(children[0])
                _aggiorna_selezione()
                return
            items = list(children)
            try:
                idx = items.index(focused)
            except ValueError:
                return
            new_idx = idx + direction
            if 0 <= new_idx < len(items):
                self._at.focus(items[new_idx])
                self._at.see(items[new_idx])
                _aggiorna_selezione()
        self._at.bind("<Up>", lambda e: (_move_focus(-1), "break")[-1])
        self._at.bind("<Down>", lambda e: (_move_focus(1), "break")[-1])

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(2, 2))

        # Bottoni
        self._btn_bar = tk.Frame(self.root, bg=c["sfondo"])
        self._btn_bar.pack(pady=(4, 4))
        bar = self._btn_bar
        # Bottoni con navigazione TAB: frecce sx/dx tra bottoni,
        # TAB dall'ultimo bottone torna a CERCA
        self._at_btns = []
        for txt, w, fg_c, cmd in [
            ("RIVEDI", 10, c["pulsanti_testo"], self._rivedi_tutti),
            ("SEL.DATA\nA", 10, c["pulsanti_testo"], self._seleziona_stessa_data),
            ("GRAFICO", 10, c["stato_avviso"], self._schermata_grafico),
            ("GHOST", 10, c["stato_ok"], self._schermata_ghost_race),
            ("ANALISI IA", 12, c["cerca_testo"], self._analisi_giornata),
            ("ALIAS", 8, c["stato_avviso"], self._alias_pilota),
            ("ELIMINA", 10, c["stato_errore"], self._elimina_tutti),
            ("PULISCI", 10, c["stato_errore"], self._pulisci_archivio),
        ]:
            bg_c = c["cerca_sfondo"] if txt == "ANALISI IA" else c["pulsanti_sfondo"]
            b = tk.Button(bar, text=txt, font=self._f_btn, width=w,
                          bg=bg_c, fg=fg_c, relief="ridge", bd=1,
                          cursor="hand2", command=cmd)
            b.pack(side="left", padx=3)
            self._at_btns.append(b)
        self._btn_analisi = self._at_btns[4]  # riferimento per flash (index spostato +1 dopo bottone GHOST)

        # Frecce sx/dx tra bottoni, TAB dall'ultimo -> CERCA
        for i, b in enumerate(self._at_btns):
            if i < len(self._at_btns) - 1:
                nxt = self._at_btns[i + 1]
                b.bind("<Right>", lambda e, n=nxt: (n.focus_set(), "break")[-1])
                b.bind("<Tab>", lambda e, n=nxt: (n.focus_set(), "break")[-1])
            else:
                # Ultimo bottone: TAB torna a CERCA
                b.bind("<Tab>", lambda e: (
                    self._at_search_entry.focus_set(),
                    self._at_search_entry.select_range(0, "end"),
                    "break")[-1])
            if i > 0:
                prv = self._at_btns[i - 1]
                b.bind("<Left>", lambda e, p=prv: (p.focus_set(), "break")[-1])
            else:
                # Primo bottone: Shift+Tab o Left torna al Treeview
                b.bind("<Left>", lambda e: (self._at.focus_set(), "break")[-1])
                b.bind("<Shift-Tab>", lambda e: (self._at.focus_set(), "break")[-1])

        # Shift+Tab dal Treeview torna all'ultimo pulsante FONTE
        # (catena coerente: CERCA<-TUTTE<-...<-ST<-Treeview)
        def _shift_tab_tree_to_fonte(e):
            ord_btns = [self._at_fonte_btns[k]
                        for k in ("ALL", "speedhive", "myrcm",
                                   "scouting", "setup")
                        if k in getattr(self, "_at_fonte_btns", {})]
            if ord_btns:
                ord_btns[-1].focus_set()
            else:
                self._at_search_entry.focus_set()
                self._at_search_entry.select_range(0, "end")
            return "break"
        self._at.bind("<Shift-Tab>", _shift_tab_tree_to_fonte)
        self._at.bind("<ISO_Left_Tab>", _shift_tab_tree_to_fonte)

        self._tutti_status = self._status_label(self.root,
            "Ctrl+F = cerca  |  \u2191\u2193 = naviga  |  Spazio = seleziona  |  A = sel.data+pilota / annulla  |  Ctrl+A = annulla  |  F = filtra  |  Enter = rivedi")

        self._at.bind("<Return>", lambda e: self._rivedi_tutti())
        self._at.bind("<Double-Button-1>", lambda e: self._rivedi_tutti())
        self._at.bind("<r>", lambda e: self._alias_pilota())
        self._at.bind("<a>", lambda e: self._seleziona_stessa_data())
        self._at.bind("<g>", lambda e: self._schermata_grafico())
        self._at.bind("<f>", lambda e: self._filtra_da_selezione())
        # Ctrl+A = annulla selezione (toggle dedicato dopo aver fatto
        # comparazioni con GRAFICO/GHOST/ANALISI IA, per ricominciare)
        def _ctrl_a(e=None):
            self._deseleziona_tutto()
            return "break"
        self._at.bind("<Control-a>", _ctrl_a)
        self._at.bind("<Control-A>", _ctrl_a)
        self.root.bind("<Control-a>", _ctrl_a)
        self.root.bind("<Control-A>", _ctrl_a)

        # Ctrl+F = focus sulla barra CERCA
        def _focus_cerca(e=None):
            self._at_search_entry.focus_set()
            self._at_search_entry.select_range(0, "end")
            return "break"
        # Case-insensitive: funziona anche con CapsLock/Shift
        self._at.bind("<Control-f>", _focus_cerca)
        self._at.bind("<Control-F>", _focus_cerca)
        self.root.bind("<Control-f>", _focus_cerca)
        self.root.bind("<Control-F>", _focus_cerca)

        children = self._at.get_children()
        # Ripristina selezione salvata (dal grafico o altra schermata)
        if hasattr(self, '_saved_selection') and self._saved_selection:
            valid_sel = [iid for iid in self._saved_selection if iid in children]
            if valid_sel:
                self._at.selection_set(valid_sel)
                focus_iid = self._saved_focus if hasattr(self, '_saved_focus') and self._saved_focus in children else valid_sel[0]
                self._at.focus(focus_iid)
                self._at.see(focus_iid)
                _aggiorna_selezione()
            else:
                if children:
                    self._at.focus(children[0])
                    self._at.see(children[0])
                    _aggiorna_selezione()
            self._saved_selection = None
            self._saved_focus = None
        elif children:
            self._at.focus(children[0])
            self._at.see(children[0])
            _aggiorna_selezione()
        # Focus sulla barra CERCA all'avvio
        self._at_search_entry.focus_set()

        # ── Refresh automatico ogni 30s ──
        # Se siamo arrivati da NUOVA LETTURA con una ricerca completa
        # (data + pista memorizzate), schedula un download silenzioso
        # in background ogni 30 secondi che ricarica la lista coi
        # nuovi tempi senza far perdere selezione + focus all'utente.
        # Cosi' durante una sessione live i tempi appaiono in archivio
        # man mano senza dover uscire e rifare RICERCA.
        # Flag _at_refresh_da_finalize evita doppio scheduling: quando
        # _refresh_silenzioso_finalizza chiama _schermata_tutti_tempi
        # per rebuild dopo nuovi tempi, il timer e' gia' schedulato
        # all'inizio di _refresh_silenzioso_tutti_tempi e qui non
        # serve schedulare di nuovo.
        da_finalize = getattr(self, "_at_refresh_da_finalize", False)
        self._at_refresh_da_finalize = False  # reset one-shot
        if (not modo_s
                and getattr(self, '_ultima_ricerca_data', None)
                and getattr(self, '_ultima_ricerca_pista_match', None)):
            self._at_refresh_attivo = True
            if not da_finalize:
                self._at_refresh_after_id = self.root.after(
                    30000, self._refresh_silenzioso_tutti_tempi)
            # Tracciamento attivita' utente per il debounce: bind
            # globali registrati UNA volta sola (idempotente via
            # flag _activity_tracker_armato). Ogni tasto/click/
            # movimento mouse aggiorna _at_last_activity. Il refresh,
            # quando schedulato, controlla questa variabile: se
            # l'utente ha interagito negli ultimi 5s posticipa.
            self._at_last_activity = 0  # reset all'apertura
            if not getattr(self, "_activity_tracker_armato", False):
                def _track_activity(e=None):
                    self._at_last_activity = time.time()
                for ev in ("<Key>", "<Motion>", "<Button>",
                           "<MouseWheel>"):
                    try:
                        self._top.bind(ev, _track_activity,
                                        add="+")
                    except Exception:
                        pass
                self._activity_tracker_armato = True
        else:
            self._at_refresh_attivo = False

        # Ordine canonico tab: i 5 pulsanti FONTE (ALL, SH, MR, LT, ST)
        # nello stesso ordine di fonte_btn_def. Costruiamo la lista qui
        # cosi' i binding successivi possono usarla.
        _fonte_keys_ord = ("ALL", "speedhive", "myrcm", "scouting", "setup")
        _fonte_btns_ord = [self._at_fonte_btns[k]
                           for k in _fonte_keys_ord
                           if k in getattr(self, "_at_fonte_btns", {})]

        def _tab_cerca_to_fonte(e):
            """TAB da CERCA va al primo pulsante FONTE (TUTTE).
            Senza pulsanti FONTE va al treeview come fallback."""
            if _fonte_btns_ord:
                _fonte_btns_ord[0].focus_set()
            else:
                self._at.focus_set()
                children_t = self._at.get_children()
                if children_t and not self._at.focus():
                    self._at.focus(children_t[0])
                    self._at.see(children_t[0])
            return "break"

        def _shift_tab_cerca_da_fonte(e):
            """Shift-Tab dal primo pulsante FONTE torna a CERCA."""
            self._at_search_entry.focus_set()
            self._at_search_entry.select_range(0, "end")
            return "break"

        def _tab_fonte_to_tree(e):
            """TAB dall'ultimo pulsante FONTE (ST) va al treeview."""
            self._at.focus_set()
            children_t = self._at.get_children()
            if children_t and not self._at.focus():
                self._at.focus(children_t[0])
                self._at.see(children_t[0])
            return "break"

        def _tab_tree_to_btns(e):
            if self._at_btns:
                self._at_btns[0].focus_set()
            else:
                self._at_search_entry.focus_set()
            return "break"

        # CERCA -> TUTTE
        self._at_search_entry.bind("<Tab>", _tab_cerca_to_fonte)
        # Catena TAB tra pulsanti FONTE: TUTTE->SH->MR->LT->ST
        for i, b in enumerate(_fonte_btns_ord):
            if i < len(_fonte_btns_ord) - 1:
                nxt = _fonte_btns_ord[i + 1]
                b.bind("<Tab>",
                       lambda e, n=nxt: (n.focus_set(), "break")[-1])
            else:
                # Ultimo (ST) -> Treeview
                b.bind("<Tab>", _tab_fonte_to_tree)
            if i > 0:
                prv = _fonte_btns_ord[i - 1]
                b.bind("<Shift-Tab>",
                       lambda e, p=prv: (p.focus_set(), "break")[-1])
                b.bind("<ISO_Left_Tab>",
                       lambda e, p=prv: (p.focus_set(), "break")[-1])
            else:
                # Primo (TUTTE) -> CERCA
                b.bind("<Shift-Tab>", _shift_tab_cerca_da_fonte)
                b.bind("<ISO_Left_Tab>", _shift_tab_cerca_da_fonte)
        # Treeview -> primo bottone azione
        self._at.bind("<Tab>", _tab_tree_to_btns)
        # Forza bindtags: istanza prima della classe, cosi' il nostro <Tab>
        # viene processato prima della traversal di default di tkinter
        tags = self._at.bindtags()
        # Porta il widget in testa (istanza, classe, toplevel, all)
        self._at.bindtags((str(self._at), ) + tuple(t for t in tags if t != str(self._at)))
        # Stessa cosa per la search entry
        tags_s = self._at_search_entry.bindtags()
        self._at_search_entry.bindtags((str(self._at_search_entry), ) + tuple(t for t in tags_s if t != str(self._at_search_entry)))
        self._top.bind("<Escape>", lambda e: back_cmd())

    # =================================================================
    #  GHOST RACE - Animazione overlay gara delle sessioni selezionate
    # =================================================================
    def _schermata_ghost_race(self):
        """Replay animato in stile F1-TV delle sessioni selezionate:
        barre orizzontali che avanzano in tempo scalato, leader sempre
        in alto, visibilita' dei sorpassi in tempo reale.
        Richiede almeno 2 sessioni selezionate."""
        sel = self._at.selection()
        if len(sel) < 2:
            if hasattr(self, "_tutti_status"):
                try:
                    self._tutti_status.config(
                        text="Seleziona almeno 2 sessioni per GHOST RACE!",
                        fg=self.c["stato_errore"])
                except Exception:
                    pass
            return

        # Estrai sessioni con giri validi
        sessioni = []
        for iid in sel:
            try:
                idx = int(iid)
            except (ValueError, TypeError):
                continue
            if not (0 <= idx < len(self._tutti_sessioni)):
                continue
            s = self._tutti_sessioni[idx]
            validi = [g for g in s.get("giri", [])
                       if g.get("stato") == "valido"
                       and g.get("tempo", 0) > 0]
            if not validi:
                continue
            tempi = [g["tempo"] for g in validi]
            # Cumulativi pre-calcolati per _laps_at_time
            cumul = []
            tot = 0.0
            for t in tempi:
                tot += t
                cumul.append(tot)
            sessioni.append({
                "pilota": (s.get("pilota") or "?")[:14],
                "data": s.get("data", ""),
                "tempi": tempi,
                "cumul": cumul,
                "total_time": tot,
                "total_laps": len(tempi),
                "best": min(tempi),
            })

        if len(sessioni) < 2:
            if hasattr(self, "_tutti_status"):
                try:
                    self._tutti_status.config(
                        text="Servono almeno 2 sessioni con giri validi.",
                        fg=self.c["stato_errore"])
                except Exception:
                    pass
            return

        # Salva selezione per il ritorno
        self._saved_selection = list(sel)
        self._saved_focus = self._at.focus()

        # Assegna colore stabile per indice
        for i, ss in enumerate(sessioni):
            ss["color"] = self._GRAPH_COLORS[i % len(self._GRAPH_COLORS)]

        self._ghost_sessioni = sessioni
        # Tempo massimo = il pilota che ha girato piu' a lungo
        self._ghost_max_time = max(ss["total_time"] for ss in sessioni)
        self._ghost_t = 0.0
        self._ghost_speed = 10.0        # 10x realtime default
        self._ghost_paused = False
        self._ghost_prev_order = None   # per highlight sorpassi
        self._ghost_flash = {}          # pilot_idx -> frame counter di flash

        # Stato "telecamera F1" (modalita' follow di default ON):
        # asse X stretto su ~6 giri attorno al gruppo dei piloti attivi,
        # asse Y stretto su ~30 secondi attorno al loro tempo medio.
        # Cosi' i sorpassi si leggono anche quando i piloti sono molto
        # vicini in tempo. Chi si ferma resta indietro e naturalmente
        # esce dalla finestra (i suoi giri non avanzano + il leader
        # avanza, quindi la sua testa scivola fuori dal frame).
        # Movimento smussato (lerp) per evitare scatti.
        self._ghost_follow = True
        self._ghost_x_span = 6.0   # giri visibili
        self._ghost_y_span = 30.0  # secondi di tempo cumul
        self._ghost_view_cx = 0.0
        self._ghost_view_cy = 0.0
        self._ghost_view_init = False  # primo frame: salta lerp

        self._pulisci()
        c = self.c

        # Header
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< TEMPI", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._ghost_esci).pack(side="left")
        tk.Label(header,
                 text="  GHOST RACE  |  %d piloti in pista" % len(sessioni),
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(8, 0))

        tk.Frame(self.root, bg=c["linee"], height=1).pack(
            fill="x", padx=10, pady=(4, 4))

        # Canvas grande per l'animazione
        self._ghost_canvas = tk.Canvas(self.root, bg=c["sfondo"],
                                        highlightthickness=0, bd=0)
        self._ghost_canvas.pack(fill="both", expand=True,
                                 padx=6, pady=(0, 4))

        # Status bar con controlli
        self._ghost_status = self._status_label(self.root,
            "SPAZIO=pausa  +/-=velocita'  R=rewind  "
            "F=follow  Z/X=zoom  0=globale  ESC=esci")

        # Bindings
        self._top.bind("<space>", lambda e: self._ghost_toggle_pause())
        self._top.bind("<Escape>", lambda e: self._ghost_esci())
        self._top.bind("<plus>", lambda e: self._ghost_speed_up())
        self._top.bind("<equal>", lambda e: self._ghost_speed_up())
        self._top.bind("<KP_Add>", lambda e: self._ghost_speed_up())
        self._top.bind("<minus>", lambda e: self._ghost_speed_down())
        self._top.bind("<KP_Subtract>", lambda e: self._ghost_speed_down())
        self._top.bind("<r>", lambda e: self._ghost_rewind())
        self._top.bind("<R>", lambda e: self._ghost_rewind())
        # Telecamera F1
        self._top.bind("<f>", lambda e: self._ghost_toggle_follow())
        self._top.bind("<F>", lambda e: self._ghost_toggle_follow())
        self._top.bind("<z>", lambda e: self._ghost_zoom_in())
        self._top.bind("<Z>", lambda e: self._ghost_zoom_in())
        self._top.bind("<x>", lambda e: self._ghost_zoom_out())
        self._top.bind("<X>", lambda e: self._ghost_zoom_out())
        self._top.bind("<0>", lambda e: self._ghost_view_globale())

        self._ghost_running = True
        self._ghost_loop()

    # --- helpers Ghost Race ---
    def _ghost_laps_at_time(self, sess, t):
        """Ritorna il numero di giri (float) completati dal pilota
        al tempo t. Interpola nel giro in corso."""
        if t <= 0:
            return 0.0
        cumul = sess["cumul"]
        tempi = sess["tempi"]
        if t >= cumul[-1]:
            return float(len(tempi))
        # Binary search non serve: i dataset sono piccoli (decine di giri)
        prev = 0.0
        for i, ct in enumerate(cumul):
            if ct >= t:
                frac = (t - prev) / tempi[i] if tempi[i] > 0 else 0
                return float(i) + frac
            prev = ct
        return float(len(tempi))

    def _ghost_toggle_pause(self):
        self._ghost_paused = not self._ghost_paused
        try:
            stato = "PAUSA" if self._ghost_paused else "PLAY"
            self._ghost_status.config(
                text="%s  |  SPAZIO pausa  +/- velocita' (%.1fx)  R riavvolgi  ESC esci"
                     % (stato, self._ghost_speed),
                fg=self.c["stato_avviso"] if self._ghost_paused else self.c["stato_ok"])
        except Exception:
            pass

    def _ghost_speed_up(self):
        self._ghost_speed = min(100.0, self._ghost_speed * 1.5)
        self._ghost_aggiorna_status()

    def _ghost_speed_down(self):
        self._ghost_speed = max(0.5, self._ghost_speed / 1.5)
        self._ghost_aggiorna_status()

    def _ghost_rewind(self):
        self._ghost_t = 0.0
        self._ghost_prev_order = None

    def _ghost_aggiorna_status(self):
        try:
            stato = "PAUSA" if self._ghost_paused else "PLAY"
            self._ghost_status.config(
                text="%s  |  SPAZIO pausa  +/- velocita' (%.1fx)  R riavvolgi  ESC esci"
                     % (stato, self._ghost_speed))
        except Exception:
            pass

    def _ghost_esci(self):
        self._ghost_running = False
        try:
            for k in ("<space>", "<plus>", "<equal>", "<KP_Add>",
                      "<minus>", "<KP_Subtract>", "<r>", "<R>",
                      "<f>", "<F>", "<z>", "<Z>", "<x>", "<X>",
                      "<0>"):
                self._top.unbind(k)
        except Exception:
            pass
        self._tempi_on_close()

    # --- Telecamera F1: follow + zoom ---
    def _ghost_toggle_follow(self):
        self._ghost_follow = not getattr(self, "_ghost_follow", True)
        try:
            stato = "FOLLOW" if self._ghost_follow else "GLOBALE"
            self._ghost_status.config(
                text="Vista: %s  (F=cambia, Z/X=zoom, 0=globale)" % stato,
                fg=self.c["stato_avviso"])
        except Exception:
            pass
        # Reset transizione cosi' al prossimo frame riparte da centro
        # corrente senza scatti
        self._ghost_view_init = False

    def _ghost_zoom_in(self):
        """Riduce span finestra: si vede meno gara ma con piu' dettaglio."""
        self._ghost_x_span = max(2.0, self._ghost_x_span * 0.7)
        self._ghost_y_span = max(5.0, self._ghost_y_span * 0.7)
        self._ghost_follow = True
        self._ghost_view_init = False

    def _ghost_zoom_out(self):
        """Aumenta span finestra: si vede piu' gara con meno dettaglio."""
        self._ghost_x_span = min(60.0, self._ghost_x_span * 1.5)
        self._ghost_y_span = min(600.0, self._ghost_y_span * 1.5)
        self._ghost_follow = True
        self._ghost_view_init = False

    def _ghost_view_globale(self):
        """Reset alla vista globale (intera gara, no follow)."""
        self._ghost_follow = False
        self._ghost_x_span = 6.0
        self._ghost_y_span = 30.0
        self._ghost_view_init = False
        try:
            self._ghost_status.config(
                text="Vista: GLOBALE  (F=follow, Z/X=zoom)",
                fg=self.c["stato_avviso"])
        except Exception:
            pass

    def _ghost_loop(self):
        """Loop animazione: avanza _ghost_t e ridisegna."""
        if not getattr(self, "_ghost_running", False):
            return
        try:
            if not self._ghost_canvas.winfo_exists():
                return
        except (tk.TclError, Exception):
            return

        # Avanza tempo sim (30 fps)
        dt = 0.033
        if not self._ghost_paused:
            self._ghost_t += dt * self._ghost_speed
            if self._ghost_t >= self._ghost_max_time:
                self._ghost_t = self._ghost_max_time
                self._ghost_paused = True  # auto-pause a fine gara
                self._ghost_aggiorna_status()

        self._ghost_draw()
        try:
            self.root.after(33, self._ghost_loop)
        except Exception:
            pass

    def _ghost_draw(self):
        """Disegna frame corrente: grafico a LINEE stile F1-TV.
        Asse X = giri completati, asse Y = tempo cumulato. Ogni pilota
        ha una curva che si disegna progressivamente col tempo sim.
        Pallino + nome in testa a ogni curva (posizione istantanea).
        Asse Y cresce verso l'alto: curva piu' bassa = ritmo migliore."""
        import math
        canvas = self._ghost_canvas
        c = self.c
        canvas.delete("all")
        W = canvas.winfo_width()
        H = canvas.winfo_height()
        if W < 120 or H < 100:
            return

        # Margini: top alto per header+classifica, left largo per label Y,
        # bottom piu' alto per asse + progress bar
        ml, mr, mt, mb = 60, 20, 110, 40
        pw = W - ml - mr
        ph = H - mt - mb
        if pw < 50 or ph < 50:
            return

        # Calcola le posizioni correnti di ogni pilota (giri completati
        # + tempo cumulato) - servono sia per il ranking sia per il
        # centro della telecamera "follow".
        posizioni_calc = []
        for i, sess in enumerate(self._ghost_sessioni):
            laps_at_t = self._ghost_laps_at_time(sess, self._ghost_t)
            n_full = int(laps_at_t)
            if n_full == 0:
                tempo_acc = 0.0
            elif n_full >= len(sess["cumul"]):
                tempo_acc = sess["cumul"][-1]
                laps_at_t = float(len(sess["cumul"]))
            else:
                tempo_acc = sess["cumul"][n_full - 1]
            frac = laps_at_t - n_full
            if frac > 0 and n_full < len(sess["tempi"]):
                tempo_acc += sess["tempi"][n_full] * frac
            attivo = self._ghost_t < sess["total_time"]
            posizioni_calc.append({"idx": i, "sess": sess,
                                    "laps": laps_at_t,
                                    "tempo": tempo_acc,
                                    "attivo": attivo})

        # Range della VISTA: globale (vecchio comportamento) o follow
        # (telecamera centrata sui piloti attivi).
        max_giri_dom = max(s["total_laps"] for s in self._ghost_sessioni)
        max_tempo_dom = max(s["total_time"] for s in self._ghost_sessioni)
        if max_giri_dom < 1: max_giri_dom = 1
        if max_tempo_dom < 1: max_tempo_dom = 1

        if getattr(self, "_ghost_follow", False):
            # Centro = media dei piloti attivi (se nessuno attivo,
            # piloti totali). Mediana sarebbe piu' robusta agli outlier
            # ma con 2-12 piloti la media basta e segue meglio il
            # gruppo principale.
            attivi = [p for p in posizioni_calc if p["attivo"]]
            riferim = attivi if attivi else posizioni_calc
            tx = sum(p["laps"] for p in riferim) / len(riferim)
            ty = sum(p["tempo"] for p in riferim) / len(riferim)
            # Lerp smussato verso il target (alpha 0.15) cosi' la
            # telecamera segue senza scatti
            if not self._ghost_view_init:
                self._ghost_view_cx = tx
                self._ghost_view_cy = ty
                self._ghost_view_init = True
            else:
                a = 0.15
                self._ghost_view_cx += (tx - self._ghost_view_cx) * a
                self._ghost_view_cy += (ty - self._ghost_view_cy) * a
            xs = self._ghost_x_span
            ys = self._ghost_y_span
            v_xmin = self._ghost_view_cx - xs * 0.5
            v_xmax = self._ghost_view_cx + xs * 0.5
            v_ymin = self._ghost_view_cy - ys * 0.5
            v_ymax = self._ghost_view_cy + ys * 0.5
            # Clamp ai bordi del dominio gara
            if v_xmin < 0:
                v_xmax += -v_xmin; v_xmin = 0
            if v_xmax > max_giri_dom:
                v_xmin -= (v_xmax - max_giri_dom)
                v_xmax = max_giri_dom
                if v_xmin < 0: v_xmin = 0
            if v_ymin < 0:
                v_ymax += -v_ymin; v_ymin = 0
        else:
            # Vista globale: tutta la gara visibile come prima
            v_xmin = 0
            v_xmax = max_giri_dom
            v_ymin = 0
            v_ymax = max_tempo_dom * 1.05

        # Range visibile con guard contro intervalli zero
        rg_x = max(0.001, v_xmax - v_xmin)
        rg_y = max(0.001, v_ymax - v_ymin)

        def xp(giri):
            return ml + int((giri - v_xmin) / rg_x * pw)

        def yp(tempo):
            # Y cresce verso l'alto: inverti cosi' v_ymin sta in basso
            return mt + int((1.0 - (tempo - v_ymin) / rg_y) * ph)

        def in_view(giri, tempo):
            """Margine di tolleranza per non far sparire i pallini
            che toccano il bordo."""
            return (v_xmin - 0.5 <= giri <= v_xmax + 0.5
                    and v_ymin - rg_y * 0.05 <= tempo
                    <= v_ymax + rg_y * 0.05)

        fg_grid = c["linee"]
        fg_label = c["testo_dim"]

        # Assi + griglia verticale (giri) sulla VISTA corrente.
        # Step adattivo: se la finestra e' piccola (zoom in) usa step 1,
        # altrimenti calcola per avere ~10 tick visibili.
        if rg_x <= 12:
            step_x = 1
        else:
            step_x = max(1, int(rg_x // 10))
        gi = int(v_xmin // step_x) * step_x
        if gi < v_xmin: gi += step_x
        while gi <= v_xmax + 0.001:
            x = xp(gi)
            if ml <= x <= ml + pw:
                canvas.create_line(x, mt, x, mt + ph,
                                    fill=fg_grid, dash=(2, 4))
                canvas.create_text(x, mt + ph + 4, text=str(int(gi)),
                                    fill=fg_label, font=(FONT_MONO, 8),
                                    anchor="n")
            gi += step_x
        # Griglia orizzontale (tempi). Step adattivo all'ampiezza
        # verticale visibile.
        if rg_y <= 60:
            tick_step = 10.0
        elif rg_y <= 180:
            tick_step = 30.0
        elif rg_y <= 600:
            tick_step = 60.0
        else:
            tick_step = 120.0
        while rg_y / tick_step > 12:
            tick_step *= 2
        v = (int(v_ymin // tick_step) * tick_step) + tick_step
        while v < v_ymax:
            y = yp(v)
            if mt <= y <= mt + ph:
                canvas.create_line(ml, y, ml + pw, y,
                                    fill=fg_grid, dash=(2, 4))
                mm = int(v) // 60
                ss = int(v) % 60
                canvas.create_text(ml - 4, y,
                                    text="%d:%02d" % (mm, ss),
                                    fill=fg_label, font=(FONT_MONO, 8),
                                    anchor="e")
            v += tick_step
        # Assi principali
        canvas.create_line(ml, mt, ml, mt + ph,
                            fill=c["label"], width=2)
        canvas.create_line(ml, mt + ph, ml + pw, mt + ph,
                            fill=c["label"], width=2)
        canvas.create_text(ml + pw // 2, H - 8, text="Giro",
                            fill=c["label"], font=(FONT_MONO, 9), anchor="s")

        # Ranking: usa le posizioni gia' calcolate prima della view
        posizioni = list(posizioni_calc)
        posizioni.sort(key=lambda p: (-p["laps"], p["tempo"]))

        # Rileva sorpassi (flash giallo punto incrocio)
        nuovo_ordine = tuple(p["idx"] for p in posizioni)
        if self._ghost_prev_order is not None:
            for pos, idx in enumerate(nuovo_ordine):
                try:
                    vecchia_pos = self._ghost_prev_order.index(idx)
                except ValueError:
                    continue
                if pos < vecchia_pos:
                    self._ghost_flash[idx] = 10
        self._ghost_prev_order = nuovo_ordine

        # Disegna le linee di ogni pilota (in ordine: prima i non-leader
        # cosi' le teste del leader vanno in cima visivamente).
        # In modalita' follow filtriamo i punti fuori vista cosi' i
        # piloti che si fermano scivolano fuori dal frame e diventano
        # invisibili (tipico effetto "telecamera F1" che segue il
        # gruppo).
        for p in reversed(posizioni):
            sess = p["sess"]
            laps_at_t = p["laps"]
            if laps_at_t <= 0:
                continue
            color = sess["color"]
            idx = p["idx"]

            # Costruisci la curva fino a laps_at_t. In follow filtriamo
            # solo i punti DENTRO la finestra visibile (con margine):
            # cosi' le code dei piloti molto indietro non vengono
            # disegnate.
            pts_xy = []
            seg_correnti = []  # segmento cumulato di punti consecutivi visibili

            def _flush_segm():
                if len(seg_correnti) >= 4:
                    canvas.create_line(*seg_correnti, fill=color,
                                        width=2, smooth=False)
                seg_correnti.clear()

            # Costruzione coppia (giri, tempo) progressiva
            coords = [(0.0, 0.0)]
            n_full = int(laps_at_t)
            for i in range(min(n_full, len(sess["cumul"]))):
                coords.append((float(i + 1), sess["cumul"][i]))
            frac = laps_at_t - n_full
            if frac > 0 and n_full < len(sess["tempi"]):
                prev_cumul = sess["cumul"][n_full - 1] if n_full > 0 else 0.0
                partial_tempo = prev_cumul + sess["tempi"][n_full] * frac
                coords.append((laps_at_t, partial_tempo))

            # Conversione + segmenti visibili
            head_x = head_y = None
            for gi, ti in coords:
                if in_view(gi, ti):
                    seg_correnti.extend([xp(gi), yp(ti)])
                    head_x = xp(gi); head_y = yp(ti)
                else:
                    _flush_segm()
            _flush_segm()

            # Se l'ultimo punto e' fuori vista, il pilota e' "uscito"
            # dal frame: niente pallino + niente label (effetto desiderato:
            # piloti staccati o fermi scompaiono).
            ultimo_g, ultimo_t = coords[-1]
            if not in_view(ultimo_g, ultimo_t) or head_x is None:
                continue

            # Pallino sulla testa (posizione visibile)
            head_x_real = xp(ultimo_g)
            head_y_real = yp(ultimo_t)
            # Flash se appena sorpassato
            flash_count = self._ghost_flash.get(idx, 0)
            if flash_count > 0:
                self._ghost_flash[idx] = flash_count - 1
                canvas.create_oval(head_x_real - 10, head_y_real - 10,
                                    head_x_real + 10, head_y_real + 10,
                                    outline=c["stato_avviso"], width=3)
            canvas.create_oval(head_x_real - 5, head_y_real - 5,
                                head_x_real + 5, head_y_real + 5,
                                fill=color, outline=color)

            # Etichetta nome pilota accanto al pallino
            nome_x = head_x_real + 10
            nome_y = head_y_real
            if head_x_real > ml + pw - 80:
                nome_x = head_x_real - 10
                anchor = "e"
            else:
                anchor = "w"
            canvas.create_text(nome_x, nome_y,
                                text=sess["pilota"][:12],
                                fill=color,
                                font=(FONT_MONO, 10, "bold"),
                                anchor=anchor)

        # Header tempo gara (sopra il grafico)
        t_min = int(self._ghost_t) // 60
        t_sec = self._ghost_t - t_min * 60
        tempo_str = "%02d:%05.2f" % (t_min, t_sec)
        pct = int(100.0 * self._ghost_t / self._ghost_max_time) \
            if self._ghost_max_time > 0 else 100
        canvas.create_text(W // 2, 20, text=tempo_str,
                            fill=c["dati"],
                            font=(FONT_MONO, 24, "bold"),
                            anchor="center")
        modo_str = ("FOLLOW %.0fg/%.0fs"
                    % (self._ghost_x_span, self._ghost_y_span)
                    if getattr(self, "_ghost_follow", False)
                    else "GLOBALE")
        canvas.create_text(W // 2, 48,
                            text="gara: %d%%   x%.1f   [%s]"
                                 % (pct, self._ghost_speed, modo_str),
                            fill=c["testo_dim"],
                            font=(FONT_MONO, 10))

        # Classifica compatta nell'header (sotto al tempo)
        cls_y = 78
        x_cur = 20
        for rank, p in enumerate(posizioni):
            sess = p["sess"]
            laps = p["laps"]
            color = sess["color"]
            giri_int = int(laps)
            if rank == 0:
                txt = "%d.%s %dg*" % (rank + 1, sess["pilota"][:8], giri_int)
            else:
                leader_laps = posizioni[0]["laps"]
                leader_sess = posizioni[0]["sess"]
                avg_l = (leader_sess["total_time"] / leader_sess["total_laps"]) \
                         if leader_sess["total_laps"] else 0
                gap_laps = leader_laps - laps
                gap_sec = gap_laps * avg_l
                txt = "%d.%s %dg -%.1fs" % (
                    rank + 1, sess["pilota"][:8], giri_int, gap_sec)
            # A capo se finisce spazio
            # (larghezza stimata in monospace)
            w_text = len(txt) * 8 + 16
            if x_cur + w_text > W - 10:
                cls_y += 18
                x_cur = 20
                if cls_y > mt - 10:
                    break  # niente spazio
            canvas.create_text(x_cur, cls_y, text=txt,
                                fill=color,
                                font=(FONT_MONO, 10, "bold"),
                                anchor="w")
            x_cur += w_text

        # Progress bar globale in fondo
        bar_y = H - 14
        canvas.create_line(20, bar_y, W - 20, bar_y,
                            fill=c["linee"], width=1)
        if self._ghost_max_time > 0:
            fx = 20 + int((W - 40) * (self._ghost_t / self._ghost_max_time))
            canvas.create_line(20, bar_y, fx, bar_y,
                                fill=c["stato_ok"], width=3)
            canvas.create_oval(fx - 4, bar_y - 4, fx + 4, bar_y + 4,
                                fill=c["stato_ok"], outline=c["stato_ok"])

    def _deseleziona_tutto(self):
        """Cancella la selezione corrente nel treeview. Usato come toggle:
        l'utente preme SEL/Ctrl+A una seconda volta dopo aver fatto le sue
        comparazioni (GRAFICO/GHOST/ANALISI IA) per ricominciare da zero."""
        sel = self._at.selection()
        if not sel:
            return False
        try:
            self._at.selection_remove(*sel)
        except Exception:
            pass
        try:
            self._tutti_status.config(
                text="Selezione annullata (%d sessioni)" % len(sel),
                fg=self.c["stato_avviso"])
        except Exception:
            pass
        return True

    def _seleziona_stessa_data(self):
        """Toggle: prima pressione seleziona sessioni con stessa data+pilota
        (saltando quelle con 1-3 giri); seconda pressione (o se la selezione
        attuale e' gia' quella prodotta dall'azione, oppure se c'e' gia' una
        selezione multipla che NON appartiene allo stesso pilota/data)
        annulla tutto. La selezione persiste tra schermate (GRAFICO, GHOST,
        ANALISI IA, RIVEDI): per ricominciare basta tornare alla lista e
        ripremere SEL."""
        # Era 4 prima: scartava sessioni con 1-3 giri (incidenti
        # immediati / test rapidi). Pero' un cap del genere su una
        # giornata di gara faceva selezionare ad es. 10 sessioni su
        # 15 - poco intuitivo. Ora MIN_GIRI=1: includiamo tutto
        # tranne sessioni vuote (0 giri = file rotto). L'utente puo'
        # deselezionare singole con SPAZIO se non gli servono.
        MIN_GIRI = 1
        focused = self._at.focus()
        if not focused:
            sel = self._at.selection()
            if sel: focused = sel[0]
        if not focused:
            # Nessun focus ma magari c'e' una selezione attiva: clear
            if self._deseleziona_tutto():
                return
            return
        idx = int(focused)
        if idx < 0 or idx >= len(self._tutti_sessioni):
            return
        s_target = self._tutti_sessioni[idx]
        data_target = s_target.get("data", "")
        pilota_target = s_target.get("pilota", "").strip().lower()
        if not data_target:
            return
        # Trova sessioni stessa data + pilota, salta quelle con 1-3 giri
        to_select = []
        skipped = 0
        for child in self._at.get_children():
            i = int(child)
            if 0 <= i < len(self._tutti_sessioni):
                s = self._tutti_sessioni[i]
                if (s.get("data", "") == data_target and
                    s.get("pilota", "").strip().lower() == pilota_target):
                    n_giri = s.get("num_giri", 0)
                    if n_giri >= MIN_GIRI:
                        to_select.append(child)
                    else:
                        skipped += 1
        # TOGGLE: se la selezione attuale e' gia' uguale al risultato
        # dell'azione, l'utente sta premendo SEL una seconda volta per
        # annullare. Stessa cosa se c'e' gia' una selezione che non
        # corrisponde a stessa-data del pilota corrente (l'utente prima
        # ha fatto comparazioni, ora vuole ricominciare).
        sel_attuale = set(self._at.selection())
        to_select_set = set(to_select)
        if sel_attuale and (sel_attuale == to_select_set or
                            (len(sel_attuale) > 1 and not sel_attuale.issubset(to_select_set))):
            self._deseleziona_tutto()
            return
        if to_select:
            self._at.selection_set(to_select)
            self._at.focus(to_select[0])
            self._at.event_generate("<<TreeviewSelect>>")
            msg = "%d sessioni selezionate per %s" % (
                len(to_select), s_target.get("pilota", "?"))
            if skipped:
                msg += " | %d saltate (vuote)" % skipped
            self._tutti_status.config(text=msg, fg=self.c["cerca_testo"])
        else:
            self._tutti_status.config(
                text="Nessuna sessione con almeno %d giri" % MIN_GIRI,
                fg=self.c["stato_avviso"])

    # _riseleziona_data_pilota rimossa: non si eliminano piu' file

    def _rivedi_tutti(self):
        """Apre AnalizzaTempi sulla sessione con focus (Enter/Double-click)."""
        if not AnalizzaTempi:
            return
        focused = self._at.focus()
        if not focused:
            sel = self._at.selection()
            if sel: focused = sel[0]
        if not focused: return
        # Salva selezione per ripristinarla al ritorno
        self._saved_selection = list(self._at.selection())
        self._saved_focus = focused
        idx = int(focused)
        if 0 <= idx < len(self._tutti_sessioni):
            sessione = self._tutti_sessioni[idx]
            path = self._tutti_paths[idx]
            self._pulisci()
            AnalizzaTempi(sessione, path, parent=self.root,
                          on_close=self._tempi_on_close)

    def _analisi_giornata(self):
        """Mostra selettore durata gara AL POSTO della barra bottoni."""
        sel = self._at.selection()
        if not sel:
            self._tutti_status.config(text="Seleziona almeno una sessione!",
                                       fg=self.c["stato_errore"])
            return
        # Salva selezione per ripristinarla al ritorno dall'IA
        self._saved_selection = list(sel)
        self._saved_focus = self._at.focus()
        self._mostra_selettore_durata()

    def _kbd_nav_bottoni(self, btns, escape_cb=None):
        """Configura navigazione tastiera su una lista di bottoni: frecce sx/dx, Tab cicla, Esc."""
        for i, b in enumerate(btns):
            if i < len(btns) - 1:
                nxt = btns[i + 1]
                b.bind("<Right>", lambda e, n=nxt: (n.focus_set(), "break")[-1])
                b.bind("<Tab>", lambda e, n=nxt: (n.focus_set(), "break")[-1])
            else:
                # Ultimo bottone: Tab torna al primo
                b.bind("<Tab>", lambda e: (btns[0].focus_set(), "break")[-1])
            if i > 0:
                prv = btns[i - 1]
                b.bind("<Left>", lambda e, p=prv: (p.focus_set(), "break")[-1])
                b.bind("<Shift-Tab>", lambda e, p=prv: (p.focus_set(), "break")[-1])
            else:
                # Primo bottone: Shift+Tab va all'ultimo
                b.bind("<Shift-Tab>", lambda e: (btns[-1].focus_set(), "break")[-1])
                b.bind("<Left>", lambda e: (btns[-1].focus_set(), "break")[-1])
            if escape_cb:
                b.bind("<Escape>", lambda e: escape_cb())
        # Focus sul primo bottone
        if btns:
            btns[0].focus_set()

    def _mostra_selettore_durata(self):
        """Step 1: seleziona durata gara."""
        c = self.c
        self._btn_bar.pack_forget()
        if hasattr(self, '_dur_frame') and self._dur_frame:
            self._dur_frame.destroy()
        self._dur_frame = tk.Frame(self.root, bg=c["sfondo"])
        self._dur_frame.pack(pady=(4, 4), before=self._tutti_status)
        tk.Label(self._dur_frame, text="DURATA GARA:", bg=c["sfondo"],
                 fg=c["stato_avviso"], font=self._f_btn).pack(side="left", padx=(0, 8))
        dur_btns = []
        for dur in [20, 30, 45, 60, 90]:
            b = tk.Button(self._dur_frame, text="%d'" % dur, font=self._f_btn, width=5,
                      bg=c["cerca_sfondo"], fg=c["cerca_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=lambda d=dur: self._mostra_selettore_serbatoio(d))
            b.pack(side="left", padx=2)
            dur_btns.append(b)
        b_ann = tk.Button(self._dur_frame, text="ANNULLA", font=self._f_btn, width=8,
                  bg=c["pulsanti_sfondo"], fg=c["stato_errore"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._annulla_durata)
        b_ann.pack(side="left", padx=(10, 2))
        dur_btns.append(b_ann)
        self._kbd_nav_bottoni(dur_btns, escape_cb=self._annulla_durata)
        self._tutti_status.config(text="Scegli la durata della gara  |  \u2190\u2192 = naviga  |  ESC = Annulla",
                                   fg=c["stato_avviso"])

    def _mostra_selettore_serbatoio(self, durata):
        """Step 2: seleziona serbatoio/batteria."""
        c = self.c
        if hasattr(self, '_dur_frame') and self._dur_frame:
            self._dur_frame.destroy()
        self._dur_frame = tk.Frame(self.root, bg=c["sfondo"])
        self._dur_frame.pack(pady=(4, 4), before=self._tutti_status)
        tk.Label(self._dur_frame, text="SERBATOIO:", bg=c["sfondo"],
                 fg=c["stato_avviso"], font=self._f_btn).pack(side="left", padx=(0, 8))
        serb_btns = []
        for cc in [125, 150]:
            b = tk.Button(self._dur_frame, text="%dcc" % cc, font=self._f_btn, width=6,
                      bg=c["cerca_sfondo"], fg=c["cerca_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=lambda s=cc: self._avvia_analisi(durata, s))
            b.pack(side="left", padx=2)
            serb_btns.append(b)
        # Opzione batteria (elettrico)
        b_batt = tk.Button(self._dur_frame, text="BATT", font=self._f_btn, width=6,
                  bg=c["cerca_sfondo"], fg="#ffff00",
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._avvia_analisi(durata, 0))
        b_batt.pack(side="left", padx=2)
        serb_btns.append(b_batt)
        b_ind = tk.Button(self._dur_frame, text="< INDIETRO", font=self._f_btn, width=10,
                  bg=c["pulsanti_sfondo"], fg=c["stato_errore"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._mostra_selettore_durata)
        b_ind.pack(side="left", padx=(10, 2))
        serb_btns.append(b_ind)
        self._kbd_nav_bottoni(serb_btns, escape_cb=self._mostra_selettore_durata)
        self._tutti_status.config(
            text="Gara %d' - Scegli serbatoio  |  \u2190\u2192 = naviga  |  BATT = elettrico" % durata,
            fg=c["stato_avviso"])

    def _annulla_durata(self):
        """Annulla selezione durata e ripristina barra bottoni originale."""
        if hasattr(self, '_dur_frame') and self._dur_frame:
            self._dur_frame.destroy()
            self._dur_frame = None
        self._btn_bar.pack(pady=(4, 4), before=self._tutti_status)
        self._tutti_status.config(
            text="\u2191\u2193 = naviga  |  Spazio = seleziona  |  A = sel.data+pilota  |  Enter = rivedi",
            fg=self.c["stato_ok"])
        self._at.focus_set()

    def _avvia_analisi(self, durata_gara, serbatoio_cc=150):
        """Raccoglie sessioni selezionate e invia all'IA con dati grezzi."""
        c = self.c
        # Rimuovi selettore e ripristina barra bottoni
        if hasattr(self, '_dur_frame') and self._dur_frame:
            self._dur_frame.destroy()
            self._dur_frame = None
        self._btn_bar.pack(pady=(4, 4), before=self._tutti_status)

        btn = self._btn_analisi
        sel = self._at.selection()
        if not sel: return

        sessioni_sel = []
        for iid in sel:
            idx = int(iid)
            if 0 <= idx < len(self._tutti_sessioni):
                sessioni_sel.append(self._tutti_sessioni[idx])
        if not sessioni_sel: return
        sessioni_sel.sort(key=lambda s: s.get("ora", "00:00"))
        n_tot = len(sessioni_sel)

        # Verifica che ci siano giri
        has_giri = False
        for si, sess in enumerate(sessioni_sel):
            btn.config(bg="#ff0000", fg="#ffffff", text="ANALISI\n%d/%d" % (si + 1, n_tot))
            self.root.update_idletasks()
            giri = sess.get("giri", [])
            if giri:
                has_giri = True
            # Inietta serbatoio scelto dall'utente nella sessione
            sess["serbatoio_cc"] = serbatoio_cc

        if not has_giri:
            btn.config(bg=c["cerca_sfondo"], fg=c["cerca_testo"], text="ANALISI IA")
            self._tutti_status.config(text="Dati insufficienti!", fg=c["stato_errore"])
            return

        # Strategia: solo durata e serbatoio, l'IA calcola tutto il resto
        strategia = {
            "durata": durata_gara,
            "serbatoio": serbatoio_cc,
        }

        # --- INVIO IA ---
        btn.config(text="INVIO IA...")
        self.root.update_idletasks()

        sessione_principale = sessioni_sel[-1]
        storico = sessioni_sel[:-1] if len(sessioni_sel) > 1 else None

        try:
            from ai_analisi import AIAnalisi
            self._pulisci()
            AIAnalisi(sessione_principale, path=None, storico=storico,
                      strategia=strategia,
                      parent=self.root, on_close=self._tempi_on_close)
        except ImportError:
            btn.config(bg=c["cerca_sfondo"], fg=c["cerca_testo"], text="ANALISI IA")
            self._tutti_status.config(text="Modulo AI non disponibile!", fg=c["stato_errore"])

    # =================================================================
    #  8a. CONFRONTA SETUP (modulo esterno, lanciato da Hub)
    # =================================================================

    def _file_tempi_del_record(self, rec, record_id):
        """Restituisce la lista dei file lap_*.json di TUTTI i setup
        sulla STESSA PISTA del setup corrente (del pilota loggato),
        ordinati dal piu' recente al piu' vecchio. Ogni record include
        il flag 'e_questo_setup' per evidenziare le sessioni collegate
        al record di setup attuale.

        Criteri di filtro:
          - pista del file == pista del setup corrente (case-insensitive)
          - pilota del file contiene il pilota loggato (se nei dati)

        Cerca in dati_dir/, dati_dir/scouting/, <progetto>/scouting/.

        Args:
            rec: dict del record setup
            record_id: identificatore record (formato 'id_XXXX')
        Returns:
            lista di dict {path, mtime, data, ora, fonte, num_giri,
                           best, media, sessione_data, e_questo_setup,
                           setup_label}
            ordinata per mtime decrescente.
        """
        if not rec:
            return []
        dati_dir = self.ctx.get("dati_dir", "") or self.dati_dir
        # I tempi possono finire in 3 posti diversi:
        # - dati/             (LapTimer cronometro manuale dal setup)
        # - dati/scouting/    (import SpeedHive/MyRCM dal setup)
        # - <progetto>/scouting/  (LapMonitor live BT, salva in
        #                          <progetto>/scouting/ usando il
        #                          parent di dati_dir)
        scouting_dati = os.path.join(dati_dir, "scouting")
        # Risale al parent di dati/ per trovare la cartella scouting/
        # alla root del progetto (dove LapMonitor salvava i file live
        # nelle versioni vecchie). Se dati_dir e' relativo (es. "dati"),
        # usa la cartella corrente come parent.
        scouting_root = ""
        try:
            parent_dati = os.path.dirname(dati_dir.rstrip("/\\"))
            if not parent_dati:
                parent_dati = os.getcwd()
            cand = os.path.join(parent_dati, "scouting")
            # Solo se diversa da scouting_dati (evita doppia scansione)
            if (cand and os.path.normpath(cand)
                    != os.path.normpath(scouting_dati)):
                scouting_root = cand
        except Exception:
            pass

        rec_id_str = record_id or ""
        rec_internal_id = str(rec.get("_id", "") or "")

        prefissi = []
        if rec_id_str:
            prefissi.append("lap_%s_" % rec_id_str)
        if rec_internal_id:
            prefissi.append("lap_id_%s_" % rec_internal_id)

        # Fallback per file salvati PRIMA che il fix setup_record_id
        # fosse in piedi (quindi senza marker esplicito): tag basato su
        # pista + data del record di setup. Non e' perfetto ma
        # ragionevole per cronometraggi della stessa giornata.
        def _norm_data(s):
            s = (str(s or "")).strip()
            if not s:
                return ""
            if "-" in s and len(s) >= 10:
                return s[:10]
            if "/" in s:
                parti = s.split("/")
                if len(parti) == 3:
                    d, m, y = parti
                    if len(y) == 2:
                        y = "20" + y
                    try:
                        return "%04d-%02d-%02d" % (int(y), int(m), int(d))
                    except ValueError:
                        return ""
            return s
        pista_setup = (str(self.ctx.get("pista", "") or "")).strip().lower()
        pilota_setup = (str(self.ctx.get("pilota", "") or "")).strip().lower()

        risultati = []
        visti = set()  # evita duplicati se file in piu' cartelle

        # Per dati_dir scansione piatta (i lap del setup stanno
        # direttamente nella cartella dati/), per le cartelle scouting
        # scansione RICORSIVA nella struttura <anno>/<pista>/.
        def _enumera(d):
            if not d or not os.path.isdir(d):
                return []
            if d == scouting_dati or d == scouting_root:
                return _scouting_paths.elenca_lap_files(d)
            try:
                return [(name, os.path.join(d, name))
                        for name in os.listdir(d)
                        if name.startswith("lap_")
                        and name.endswith(".json")]
            except OSError:
                return []

        for d in (dati_dir, scouting_dati, scouting_root):
            for f, full in _enumera(d):
                if f in visti:
                    continue
                try:
                    with open(full, "r", encoding="utf-8") as _fh:
                        data = json.load(_fh)
                except Exception:
                    continue

                # FILTRO PRIMARIO: pista del file deve combaciare
                # con la pista del setup corrente. Senza pista non
                # ha senso confrontare i tempi.
                pista_file = (str(data.get("pista", "") or "")
                              ).strip().lower()
                if pista_setup and pista_file != pista_setup:
                    continue

                # FILTRO SECONDARIO: pilota. Tollerante:
                #   - pilota_file vuoto: includo (LapTimer senza nome)
                #   - pilota_file "Trasp. N": includo (transponder
                #     anonimo del ricevitore live, plausibilmente mio
                #     se il transponder e' nel mio ricevitore)
                #   - nome reale che combacia (sottostringa) col pilota
                #     loggato: includo
                #   - nome reale che NON combacia: ESCLUDO (e' un
                #     altro pilota, di solito da import SpeedHive)
                pilota_file = (str(data.get("pilota", "") or "")
                               ).strip().lower()
                if pilota_setup and pilota_file:
                    if (not pilota_file.startswith("trasp")
                            and not pilota_file.startswith("pilota ")):
                        nomi_loggato = [p.strip() for p in
                                        pilota_setup.replace("(", " ")
                                        .replace(")", " ").split()
                                        if len(p.strip()) >= 3]
                        if nomi_loggato:
                            match = any(n in pilota_file
                                        for n in nomi_loggato)
                            if not match:
                                continue

                # Determina se appartiene al setup corrente (per
                # marcare la riga nella UI).
                jr = str(data.get("record_id", "") or "").strip()
                jid = str(data.get("_id", "") or "").strip()
                jsr = str(data.get("setup_record_id", "") or "").strip()
                e_questo = False
                if any(f.startswith(p) for p in prefissi):
                    e_questo = True
                elif jr and rec_id_str and jr == rec_id_str:
                    e_questo = True
                elif jsr and rec_id_str and jsr == rec_id_str:
                    e_questo = True
                elif jid and rec_internal_id and jid == rec_internal_id:
                    e_questo = True

                # Label setup: dal campo 'setup' del JSON, accorciata
                setup_lbl = str(data.get("setup", "") or "").strip()
                if not setup_lbl:
                    setup_lbl = "(senza setup)"
                if len(setup_lbl) > 24:
                    setup_lbl = setup_lbl[:21] + "..."

                # Calcola statistiche giri
                giri = data.get("giri", []) or []
                tempi_validi = []
                for g in giri:
                    try:
                        t = float(g.get("tempo", 0) or 0)
                        st = (g.get("stato", "valido") or "valido").lower()
                    except (ValueError, TypeError):
                        continue
                    if t > 0 and st.startswith("valid"):
                        tempi_validi.append(t)
                best = min(tempi_validi) if tempi_validi else 0
                media = (sum(tempi_validi) / len(tempi_validi)
                         if tempi_validi else 0)
                try:
                    mt = os.path.getmtime(full)
                except OSError:
                    mt = 0
                risultati.append({
                    "path": full,
                    "mtime": mt,
                    "data": data.get("data", ""),
                    "ora": data.get("ora", ""),
                    "fonte": data.get("tipo", ""),
                    "num_giri": int(data.get("num_giri", len(giri)) or 0),
                    "best": best,
                    "media": media,
                    "sessione_data": data,
                    "e_questo_setup": e_questo,
                    "setup_label": setup_lbl,
                })
                visti.add(f)

        risultati.sort(key=lambda r: r["mtime"], reverse=True)
        return risultati

    def _lancia_pulisci_setup(self):
        """Apre la schermata mega-filtro PULISCI per cancellare
        sessioni mirate fra quelle del setup corrente (su questa pista
        / questo pilota)."""
        db = self.ctx.get("_db")
        record_id = self.ctx.get("record_id", "")
        rec = self.ctx.get("_record")
        if not rec and db and record_id.startswith("id_"):
            target_id = record_id[3:]
            for idx in self.ctx.get("_indici_visibili", []):
                r = db.leggi(idx)
                if r and str(r.get("_id", "")) == target_id:
                    rec = r
                    break
        files_record = (self._file_tempi_del_record(rec, record_id)
                        if rec else [])
        if not files_record:
            try:
                self._hub_status.config(
                    text="Nessuna sessione da pulire per questo setup.",
                    fg=self.c["stato_avviso"])
            except Exception:
                pass
            return
        self._schermata_pulisci_setup_filtro(files_record)

    def _schermata_pulisci_setup_filtro(self, files_record):
        """Schermata inline mega-filtro PULISCI per le sessioni del
        setup, in stile retrodb (RetroField a celle).

        Filtri disponibili (pista/pilota gia' fissi dal contesto):
          - Fonte: TUTTE / SPEEDHIVE / MYRCM / LAPTIMER / SETUP
          - Data DA / DATA A: range giorno (GG/MM/AAAA)
          - Giri DA / GIRI A: range numero di giri della sessione

        Conferma a doppia pressione del bottone ELIMINA per sicurezza.
        """
        c = self.c
        n_tot = len(files_record)
        self._pulisci()

        # ── Header ──
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< CRONO", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._schermata_hub).pack(side="left")
        tk.Label(header, text="  PULISCI - Sessioni del setup",
                 bg=c["sfondo"], fg=c["stato_errore"],
                 font=self._f_title).pack(side="left", padx=(8, 0))
        tk.Label(header,
                 text="%d sessioni totali" % n_tot,
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(side="right")
        tk.Frame(self.root, bg=c["linee"], height=1).pack(
            fill="x", padx=10, pady=(4, 4))

        # ── Help ──
        pista_lbl = str(self.ctx.get("pista", "")).strip() or "?"
        tk.Label(self.root,
                 text=("Filtra le sessioni di '%s' da eliminare. "
                       "I campi vuoti non filtrano. "
                       "I criteri si combinano in AND."
                       % pista_lbl),
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small,
                 wraplength=720, justify="center").pack(pady=(2, 6))

        if not RetroField:
            tk.Label(self.root, text="RetroField non disponibile!",
                     bg=c["sfondo"], fg=c["stato_errore"],
                     font=self._f_btn).pack(pady=20)
            tk.Button(self.root, text="< INDIETRO", font=self._f_btn,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=self._schermata_hub).pack(pady=8)
            return

        # ── Form ──
        form = tk.Frame(self.root, bg=c["sfondo"])
        form.pack(padx=18, pady=4, fill="x")

        rf_fonte = RetroField(form, label="Fonte",
                              tipo="S", lunghezza=10, label_width=12)
        rf_fonte.pack(pady=2, anchor="w", fill="x")
        tk.Label(form,
                 text="              vuoto=tutte | MANUALE | SPEEDHIVE "
                      "| MYRCM | LAPTIMER",
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small,
                 anchor="w").pack(anchor="w")

        tk.Frame(form, bg=c["linee"], height=1).pack(
            fill="x", pady=(6, 4))
        rf_data_da = RetroField(form, label="Data DA",
                                tipo="D", lunghezza=10, label_width=12)
        rf_data_da.pack(pady=2, anchor="w", fill="x")
        rf_data_a = RetroField(form, label="Data A",
                               tipo="D", lunghezza=10, label_width=12)
        rf_data_a.pack(pady=2, anchor="w", fill="x")

        tk.Frame(form, bg=c["linee"], height=1).pack(
            fill="x", pady=(6, 4))
        rf_giri_da = RetroField(form, label="Giri DA",
                                tipo="N", lunghezza=4, label_width=12)
        rf_giri_da.pack(pady=2, anchor="w", fill="x")
        rf_giri_a = RetroField(form, label="Giri A",
                               tipo="N", lunghezza=4, label_width=12)
        rf_giri_a.pack(pady=2, anchor="w", fill="x")

        rf_all = (rf_fonte, rf_data_da, rf_data_a, rf_giri_da, rf_giri_a)

        # ── Anteprima ──
        lbl_preview = tk.Label(self.root, text="",
                               bg=c["sfondo"], fg=c["stato_avviso"],
                               font=self._f_btn)
        lbl_preview.pack(pady=(10, 4))

        state = {"matched": [], "armato_ts": 0}

        _FONTE_MAP = {
            "":          "TUTTE",
            "TUTTE":     "TUTTE",
            "MANUALE":   "manuale",
            "LAPTIMER":  "laptimer",
            "LAPMONITOR": "lapmonitor",
            "SPEEDHIVE": "speedhive",
            "MYRCM":     "myrcm",
            "SCOUTING":  "scouting",
        }

        def _to_iso(s):
            s = (str(s or "")).strip()
            if not s:
                return ""
            if "/" in s:
                p = s.split("/")
                if len(p) == 3:
                    d, m, y = p
                    if len(y) == 2:
                        y = "20" + y
                    try:
                        return "%04d-%02d-%02d" % (int(y), int(m), int(d))
                    except ValueError:
                        return ""
            if "-" in s and len(s) >= 10:
                return s[:10]
            return s

        def _data_file_iso(fr):
            d = (str(fr.get("sessione_data", {}).get("data", "") or "")
                 ).strip()
            return _to_iso(d)

        def _filtra():
            fonte_raw = rf_fonte.get().strip().upper()
            fonte_target = _FONTE_MAP.get(fonte_raw, "")
            data_da = _to_iso(rf_data_da.get())
            data_a = _to_iso(rf_data_a.get())
            try:
                giri_da = int(rf_giri_da.get()) if rf_giri_da.get().strip() else None
            except ValueError:
                giri_da = None
            try:
                giri_a = int(rf_giri_a.get()) if rf_giri_a.get().strip() else None
            except ValueError:
                giri_a = None

            matched = []
            for i, fr in enumerate(files_record):
                # Fonte
                if fonte_target and fonte_target != "TUTTE":
                    ft = str(fr.get("fonte", "")).lower()
                    # "manuale" matcha laptimer + lapmonitor + scouting
                    if fonte_target == "manuale":
                        if ft not in ("laptimer", "lapmonitor",
                                      "scouting"):
                            continue
                    else:
                        if ft != fonte_target:
                            continue
                # Data
                if data_da or data_a:
                    df = _data_file_iso(fr)
                    if data_da and (not df or df < data_da):
                        continue
                    if data_a and (not df or df > data_a):
                        continue
                # Giri
                ng = int(fr.get("num_giri", 0) or 0)
                if giri_da is not None and ng < giri_da:
                    continue
                if giri_a is not None and ng > giri_a:
                    continue
                matched.append(i)
            return matched

        def _refresh(*_):
            state["armato_ts"] = 0
            ind = _filtra()
            state["matched"] = ind
            n = len(ind)
            if n == 0:
                lbl_preview.config(text="Nessuna sessione corrisponde",
                                   fg=c["testo_dim"])
                btn_elimina.config(state="disabled", text="ELIMINA")
            elif n == n_tot:
                lbl_preview.config(
                    text=("ATTENZIONE: TUTTE le %d sessioni saranno "
                          "eliminate" % n_tot),
                    fg=c["stato_errore"])
                btn_elimina.config(state="normal",
                                   text="ELIMINA TUTTE (%d)" % n)
            else:
                lbl_preview.config(
                    text="Saranno eliminate %d / %d sessioni"
                         % (n, n_tot),
                    fg=c["stato_avviso"])
                btn_elimina.config(state="normal",
                                   text="ELIMINA %d" % n)

        def _esegui():
            ind = list(state["matched"])
            if not ind:
                return
            now = time.time()
            if now - state["armato_ts"] > 3:
                state["armato_ts"] = now
                self._pulisci_status.config(
                    text="Premi ELIMINA di nuovo entro 3 secondi "
                         "per confermare.",
                    fg=c["stato_errore"])
                return
            state["armato_ts"] = 0
            cancellati = 0
            for i in ind:
                if 0 <= i < len(files_record):
                    p = files_record[i]["path"]
                    try:
                        os.remove(p)
                        cancellati += 1
                    except Exception:
                        pass
            # Torna all'hub Crono che ricarica la lista aggiornata
            self._schermata_hub()
            try:
                self._hub_status.config(
                    text="Pulizia: %d sessioni eliminate." % cancellati,
                    fg=c["stato_ok"])
            except Exception:
                pass

        # ── Barra pulsanti ──
        btn_bar = tk.Frame(self.root, bg=c["sfondo"])
        btn_bar.pack(pady=(10, 6))
        btn_back = tk.Button(btn_bar, text="< INDIETRO", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2", width=14,
                  command=self._schermata_hub)
        btn_back.pack(side="left", padx=8)
        btn_elimina = tk.Button(btn_bar, text="ELIMINA",
                  font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["stato_errore"],
                  relief="ridge", bd=1, cursor="hand2", width=20,
                  state="disabled", command=_esegui)
        btn_elimina.pack(side="left", padx=8)

        # Status
        self._pulisci_status = tk.Label(self.root, text="",
                                        bg=c["sfondo"],
                                        fg=c["testo_dim"],
                                        font=self._f_small)
        self._pulisci_status.pack(pady=(2, 4))

        # Esc -> indietro
        self.root.bind("<Escape>",
                       lambda e: self._schermata_hub())

        # Refresh in tempo reale
        for rf in rf_all:
            try:
                rf._canvas.bind("<KeyRelease>", lambda e: _refresh())
            except Exception:
                pass
        _refresh()
        rf_fonte.set_focus()

    def _vai_al_setup_della_sessione(self, file_record):
        """Apre la scheda del record di setup originario della
        sessione passata. Cerca nel db dei setup il record con
        _id corrispondente al record_id/setup_record_id del file.

        Args:
            file_record: dict come restituito da
                _file_tempi_del_record (deve contenere 'sessione_data')
        """
        if not self._on_apri_setup:
            self._hub_status.config(
                text="Funzione 'vai al setup' non disponibile.",
                fg=self.c["stato_errore"])
            return
        ses = file_record.get("sessione_data") or {}
        # I file possono avere record_id (es. 'id_1234' per LapTimer)
        # oppure setup_record_id (LapMonitor live aggiornato).
        candidates = []
        for k in ("setup_record_id", "record_id"):
            v = str(ses.get(k, "") or "").strip()
            if v:
                candidates.append(v)
        # Estrai l'_id numerico se formato 'id_XXXX'
        target_id = ""
        for cand in candidates:
            if cand.startswith("id_"):
                target_id = cand[3:]
                break
        # Se _id nel file (LapTimer salvati con il fix recente)
        if not target_id:
            jid = str(ses.get("_id", "") or "").strip()
            if jid:
                target_id = jid
        # Se anche cosi' niente, NON usciamo: lasciamo che il fallback
        # per data nel db (fase 3 della ricerca sotto) provi comunque
        # a trovare il setup. Il campo 'setup' del file molto spesso
        # contiene la data ('10-05-2026') che e' lo stesso valore del
        # campo Data/Data_Prova del record di setup originario.

        db = self.ctx.get("_db")
        if not db:
            self._hub_status.config(
                text="Database setup non disponibile.",
                fg=self.c["stato_errore"])
            return

        # Ricerca a cascata, dal piu' stretto al piu' largo:
        #   1) match _id fra gli indici visibili (filtro utente)
        #   2) match _id fra TUTTI i record del db (anche altri utenti
        #      / record che il filtro corrente esclude)
        #   3) fallback: match per data del setup (campo Data/Data_Prova)
        #      se setup_label contiene una data (file storici senza _id
        #      ma riconducibili per data)
        idx_target = -1

        def _scan(indici_da_provare):
            for idx in indici_da_provare:
                try:
                    r = db.leggi(idx)
                except Exception:
                    continue
                if r and str(r.get("_id", "")) == target_id:
                    return idx
            return -1

        # 1) Indici visibili
        indici_vis = list(self.ctx.get("_indici_visibili", []) or [])
        if indici_vis:
            idx_target = _scan(indici_vis)

        # 2) Tutti i record del db (no filtro utente)
        if idx_target < 0:
            try:
                # `records` e' la lista interna completa; preferisco
                # questa al conteggio filtrato per essere sicuro di
                # scandire tutto.
                tutti = list(range(len(getattr(db, "records", []) or [])))
                if not tutti:
                    # fallback al conteggio (filtrato): meglio di niente
                    tutti = list(range(getattr(db, "conteggio",
                                                lambda *a: 0)() or 0))
                idx_target = _scan(tutti)
            except Exception:
                pass

        # 3) Fallback per data: se il setup_label nel file e' una data
        # (es. "10-05-2026" o "10/05/2026"), cerco un record con campo
        # Data o Data_Prova uguale.
        if idx_target < 0:
            setup_label = (str(ses.get("setup", "") or "")).strip()
            # Normalizza a GG/MM/AAAA. La stringa puo' essere:
            #  - ISO 'AAAA-MM-GG' (es. '2026-05-10')
            #  - Italiana con trattini 'GG-MM-AAAA' (es. '10-05-2026')
            #  - Con slash 'GG/MM/AAAA' (es. '10/05/2026')
            # Discriminiamo guardando se la prima parte e' >= 1000
            # (anno) oppure < 32 (giorno).
            def _to_data_eu(s):
                s = (str(s or "")).strip()
                if not s:
                    return ""
                sep = "/" if "/" in s else ("-" if "-" in s else "")
                if not sep:
                    return ""
                parti = s.replace(" ", "").split(sep)
                if len(parti) < 3:
                    return ""
                try:
                    a = int(parti[0])
                    b = int(parti[1])
                    c = int(parti[2])
                except ValueError:
                    return ""
                # Anno-Mese-Giorno (ISO) se primo numero >= 1000
                if a >= 1000:
                    y, m, d = a, b, c
                else:
                    # Giorno-Mese-Anno (italiano)
                    d, m, y = a, b, c
                    if y < 100:  # anno 2 cifre -> 2000+
                        y += 2000
                if not (1 <= m <= 12 and 1 <= d <= 31):
                    return ""
                return "%02d/%02d/%04d" % (d, m, y)
            data_target = _to_data_eu(setup_label)
            if data_target:
                try:
                    tutti = list(range(len(
                        getattr(db, "records", []) or [])))
                except Exception:
                    tutti = []
                for idx in tutti:
                    try:
                        r = db.leggi(idx)
                    except Exception:
                        continue
                    if not r:
                        continue
                    d1 = str(r.get("Data", "") or "").strip()
                    d2 = str(r.get("Data_Prova", "") or "").strip()
                    if (_to_data_eu(d1) == data_target
                            or _to_data_eu(d2) == data_target):
                        idx_target = idx
                        break

        if idx_target < 0:
            self._hub_status.config(
                text=("Setup originario non trovato (record cancellato "
                      "o non collegato). Riferimento: %s"
                      % (target_id or "(senza id)")),
                fg=self.c["stato_avviso"])
            return

        # Chiama il callback verso retrodb: chiude Crono e apre il
        # record di setup indicato.
        try:
            self._on_apri_setup(idx_target)
        except Exception as e:
            self._hub_status.config(
                text="Errore apertura setup: %s" % e,
                fg=self.c["stato_errore"])

    def _lancia_confronta(self):
        """CONFRONTA dal setup: passa i tempi della sessione selezionata
        nella lista hub (o, se nessuna, la piu' recente) al Dr. IA, che
        li analizza e propone modifiche al setup."""
        # Trova il record corrente del setup
        db = self.ctx.get("_db")
        table_def = self.ctx.get("_table_def")
        if not db or not table_def:
            self._hub_status.config(
                text="Contesto setup non disponibile.",
                fg=self.c["stato_errore"])
            return

        # Risali al record dal record_id (formato 'id_XXXX')
        record_id = self.ctx.get("record_id", "")
        rec = self.ctx.get("_record") or None
        if not rec and record_id and record_id.startswith("id_"):
            target_id = record_id[3:]
            for idx in self.ctx.get("_indici_visibili", []):
                r = db.leggi(idx)
                if r and str(r.get("_id", "")) == target_id:
                    rec = r
                    break
        if not rec:
            self._hub_status.config(
                text="Record di setup non trovato.",
                fg=self.c["stato_errore"])
            return

        # Righe selezionate nella lista hub: tutte le sessioni che
        # l'utente ha evidenziato (Shift/Ctrl). Se nessuna selezione,
        # prendiamo la piu' recente come default.
        sel_paths = list(getattr(self, "_hub_tempi_sel_paths", []) or [])
        files_record = self._file_tempi_del_record(rec, record_id)
        if not files_record:
            self._hub_status.config(
                text=("Per questo setup non ci sono ancora tempi "
                      "cronometrati. Premi CRONOMETRA per registrarli "
                      "(LapTimer salva collegandoli al setup)."),
                fg=self.c["stato_avviso"])
            return
        # Risolvi i path selezionati nei record completi (preserva
        # l'ordine della lista files_record, dal piu' recente al piu'
        # vecchio, in modo che l'IA veda l'evoluzione cronologica).
        scelti = []
        if sel_paths:
            for fr in files_record:
                if fr["path"] in sel_paths:
                    scelti.append(fr)
        if not scelti:
            # Default: la sessione piu' recente
            scelti = [files_record[0]]

        # Costruisci la lista 'tempi_sessioni' attesa dal Dr. IA.
        # Ordine: cronologico crescente (dalla piu' vecchia alla piu'
        # recente) per facilitare al modello l'analisi temporale e
        # capire se il setup e' migliorato o peggiorato nel tempo.
        # Ordino in base a data+ora del JSON (piu' robusto del mtime).
        def _data_iso_sort(fr):
            ses = fr["sessione_data"] or {}
            d = (str(ses.get("data", "") or "")).strip()
            o = (str(ses.get("ora", "") or "")).strip()
            # Normalizza data in YYYY-MM-DD per ordinamento lessicografico
            if "/" in d:
                p = d.split("/")
                if len(p) == 3:
                    dd, mm, yy = p
                    if len(yy) == 2:
                        yy = "20" + yy
                    d = "%04d-%02d-%02d" % (int(yy), int(mm), int(dd))
            return d + " " + o
        scelti_ordinati = sorted(scelti, key=_data_iso_sort)

        tempi_sessioni = []
        for fr in scelti_ordinati:
            ses = fr["sessione_data"] or {}
            giri = ses.get("giri", []) or []
            if not giri:
                continue
            tempi_sessioni.append({
                "data":  ses.get("data", ""),
                "ora":   ses.get("ora", ""),
                "fonte": ses.get("tipo", ""),
                "giri":  giri,
                # setup_snapshot, se presente nel file, descrive il setup
                # al momento del cronometraggio: utile se l'utente ha
                # modificato il setup fra una sessione e l'altra.
                "setup_snapshot": ses.get("setup_snapshot") or {},
            })
        if not tempi_sessioni:
            self._hub_status.config(
                text="Le sessioni selezionate non contengono giri validi.",
                fg=self.c["stato_avviso"])
            return

        # Costruisci anche il contesto setup (riferimenti, meteo, ecc.)
        # riusando i dati gia' preparati per Crono.
        contesto_extra = dict(self.ctx)  # copia leggera

        # Lancio Dr. IA: la modalita' ANALIZZA TEMPI e' attivabile dalla
        # schermata di scelta perche' tempi_sessione != None.
        try:
            from doctor_ia import DoctorIA as _DoctorIA_local
        except Exception:
            try:
                from addons.doctor_ia import DoctorIA as _DoctorIA_local
            except Exception:
                self._hub_status.config(
                    text="Modulo Dr. IA non disponibile.",
                    fg=self.c["stato_errore"])
                return

        def _on_back():
            # Torna all'hub Crono dopo il Dr. IA
            self._schermata_hub()

        def _on_save(modifiche, spiegazione, etichetta):
            # Per ora solo segnaliamo che servirebbe ritornare al setup
            # in retrodb per applicare le modifiche; fuori dal contesto
            # di retrodb non possiamo creare un nuovo record di setup.
            self._schermata_hub()
            try:
                self._hub_status.config(
                    text="Per applicare le modifiche apri il Dr. IA "
                         "dal setup (Ctrl+I).",
                    fg=self.c["stato_avviso"])
            except Exception:
                pass

        self._pulisci()
        _DoctorIA_local(parent=self.root,
                        record=rec,
                        table_def=table_def,
                        contesto_extra=contesto_extra,
                        on_back=_on_back,
                        on_save=_on_save,
                        tempi_sessioni=tempi_sessioni)

    # =================================================================
    #  8. GRAFICO OVERLAY SESSIONI (Canvas tkinter puro)
    # =================================================================
    # Colori linee per sessioni sovrapposte (fino a 8)
    _GRAPH_COLORS = [
        "#39ff14",  # verde brillante
        "#ffaa00",  # arancione
        "#6688ff",  # blu
        "#ff5555",  # rosso
        "#00ffff",  # ciano
        "#ff66ff",  # magenta
        "#ffff00",  # giallo
        "#ff8844",  # arancione scuro
    ]

    def _schermata_grafico(self):
        """Grafico cumulativo: asse X = giro, asse Y = tempo progressivo.
        Linea piu' piatta = ritmo piu' veloce. La sessione migliore sta sotto."""
        import math

        # Se abbiamo gia' i dati raw (cambio modo), usiamo quelli
        if not getattr(self, '_grafico_sessioni_raw', None):
            # Prima volta: raccogli dal Treeview
            sel = self._at.selection()
            if not sel:
                focused = self._at.focus()
                if focused:
                    sel = (focused,)
            if not sel:
                return

            # Salva selezione per ripristinarla al ritorno
            self._saved_selection = list(sel)
            self._saved_focus = self._at.focus()

            sessioni_sel = []
            for iid in sel:
                idx = int(iid)
                if 0 <= idx < len(self._tutti_sessioni):
                    s = self._tutti_sessioni[idx]
                    giri = s.get("giri", [])
                    # Filtro giri validi (stesso ordine dei tempi).
                    giri_validi = [g for g in giri if g.get("tempo", 0) > 0]
                    tempi = [g["tempo"] for g in giri_validi]
                    if tempi:
                        cumul = []
                        tot = 0.0
                        for t in tempi:
                            tot += t
                            cumul.append(tot)
                        # Pit-stop identificati su giri_validi cosi' gli
                        # indici sono allineati a tempi/cumul.
                        pit_idx = self._identifica_pit(giri_validi)
                        label = "%s %s %s" % (
                            s.get("pilota", "?")[:12],
                            s.get("data", "?")[-5:],
                            s.get("ora", "?")[:5])
                        sessioni_sel.append({
                            "label": label, "tempi": tempi, "cumul": cumul,
                            "best": s.get("miglior_tempo", 0),
                            "media": s.get("media", 0),
                            "n_giri": len(tempi),
                            "pit_idx": pit_idx})
            if not sessioni_sel:
                return
            self._grafico_sessioni_raw = list(sessioni_sel)

        if not hasattr(self, '_grafico_modo'):
            self._grafico_modo = "GARA"

        def _ordina_sessioni(modo):
            """Ordina sessioni per modalita' GARA o PROVE."""
            ss = list(self._grafico_sessioni_raw)
            if modo == "GARA":
                # Chi ha fatto piu' giri nel minor tempo totale
                ss.sort(key=lambda s: (-s["n_giri"], s["cumul"][-1] if s["cumul"] else 0))
            else:
                # PROVE: conta lo STINT (passo gara), non il singolo giro:
                # un best-lap isolato puo' essere un taglio pista, mentre la
                # media del passo dice chi ha tenuto piu' velocita' su tutto
                # il run. Ordine per media ascendente (piu' veloce prima),
                # poi best come tiebreaker. Cosi' la sessione #1 coincide
                # davvero con la curva piu' bassa del grafico cumulativo.
                ss.sort(key=lambda s: (s["media"] if s["media"] > 0 else 9999,
                                        s["best"] if s["best"] > 0 else 9999))
            return ss

        sessioni_sel = _ordina_sessioni(self._grafico_modo)

        self._pulisci()
        c = self.c

        # Header
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        def _esci_grafico():
            self._grafico_sessioni_raw = None
            self._tempi_on_close()
        tk.Button(header, text="< TEMPI", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=_esci_grafico).pack(side="left")
        tk.Label(header, text="  GRAFICO PROGRESSIVO  |  %d sessioni" % len(sessioni_sel),
                 bg=c["sfondo"], fg=c["dati"], font=self._f_title).pack(side="left", padx=(8, 0))
        # Barra batteria all'estrema destra (prima del selettore modalita')
        try:
            from core.sd_bar import BarraBatteria as _BarraBat
            from core.batteria import get_batteria_info as _get_bat_info
            _pct, _ = _get_bat_info()
            if _pct is not None:
                _BarraBat(header, get_info_func=_get_bat_info).pack(
                    side="right", padx=(6, 0))
        except Exception:
            pass

        # Selettore GARA / PROVE
        sel_frame = tk.Frame(header, bg=c["sfondo"])
        sel_frame.pack(side="right")
        tk.Label(sel_frame, text="linea piatta = veloce   ",
                 bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small).pack(side="left")
        self._btn_gara = tk.Button(sel_frame, text="GARA", font=(FONT_MONO, 9, "bold"),
                  bg=c["cursore"], fg=c["sfondo"],
                  relief="ridge", bd=1, width=6, cursor="hand2",
                  command=lambda: _switch_modo("GARA"))
        self._btn_gara.pack(side="left", padx=1)
        self._btn_prove = tk.Button(sel_frame, text="PROVE", font=(FONT_MONO, 9, "bold"),
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, width=6, cursor="hand2",
                  command=lambda: _switch_modo("PROVE"))
        self._btn_prove.pack(side="left", padx=1)

        def _switch_modo(modo):
            self._grafico_modo = modo
            self._schermata_grafico()

        def _aggiorna_btn_modo():
            """Evidenzia il bottone del modo attivo."""
            if self._grafico_modo == "GARA":
                self._btn_gara.config(bg=c["cursore"], fg=c["sfondo"])
                self._btn_prove.config(bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"])
            else:
                self._btn_prove.config(bg=c["cursore"], fg=c["sfondo"])
                self._btn_gara.config(bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"])
        _aggiorna_btn_modo()

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(4, 4))

        # ─── Area centrale: grafico SX + pannello sessioni DX ───
        # Layout a due colonne per non far "rubare" spazio verticale dalla
        # legenda quando ci sono molte sessioni selezionate (prima era
        # overlay in alto e copriva meta' grafico).
        body = tk.Frame(self.root, bg=c["sfondo"])
        body.pack(fill="both", expand=True, padx=10, pady=(2, 4))

        # Colonna SX: canvas grafico (espandibile, si prende tutto lo spazio)
        graph_col = tk.Frame(body, bg=c["sfondo"])
        graph_col.pack(side="left", fill="both", expand=True)
        canvas = tk.Canvas(graph_col, bg=c["sfondo_celle"],
                           highlightthickness=1, highlightbackground=c["linee"])
        canvas.pack(fill="both", expand=True)

        # Colonna DX: pannello sessioni con scrollbar verde retro.
        # Larghezza ~280px (scelta utente: media). pack_propagate(False)
        # per rispettare la width anche se il contenuto e' piu' stretto.
        sess_col = tk.Frame(body, bg=c["sfondo"], width=280)
        sess_col.pack(side="right", fill="y", padx=(6, 0))
        sess_col.pack_propagate(False)
        tk.Label(sess_col, text="S E S S I O N I", bg=c["sfondo"],
                 fg=c["testo_dim"], font=self._f_small).pack(pady=(0, 2))

        sess_canvas = tk.Canvas(sess_col, bg=c["sfondo_celle"],
                                highlightthickness=1,
                                highlightbackground=c["linee"])
        sess_vsb = tk.Scrollbar(sess_col, orient="vertical",
                                command=sess_canvas.yview,
                                bg=c["sfondo"], troughcolor=c["sfondo"],
                                activebackground=c["dati"],
                                highlightbackground=c["linee"],
                                relief="flat", bd=0, width=12)
        sess_canvas.configure(yscrollcommand=sess_vsb.set)
        sess_vsb.pack(side="right", fill="y")
        sess_canvas.pack(side="left", fill="both", expand=True)

        sess_inner = tk.Frame(sess_canvas, bg=c["sfondo_celle"])
        sess_win = sess_canvas.create_window((0, 0), window=sess_inner,
                                              anchor="nw")

        def _on_sess_inner_cfg(e):
            sess_canvas.configure(scrollregion=sess_canvas.bbox("all"))
        sess_inner.bind("<Configure>", _on_sess_inner_cfg)

        def _on_sess_canvas_cfg(e):
            # Riallinea la larghezza del frame interno al canvas scrollabile
            sess_canvas.itemconfigure(sess_win, width=e.width)
        sess_canvas.bind("<Configure>", _on_sess_canvas_cfg)

        # Popola il pannello con una card per sessione
        for i, sess in enumerate(sessioni_sel):
            num = i + 1
            color = self._GRAPH_COLORS[i % len(self._GRAPH_COLORS)]
            card = tk.Frame(sess_inner, bg=c["sfondo_celle"])
            card.pack(fill="x", padx=4, pady=2, anchor="w")
            # Riga 1: N + quadrato colore + label (pilota/data/ora)
            r1 = tk.Frame(card, bg=c["sfondo_celle"])
            r1.pack(fill="x", anchor="w")
            tk.Label(r1, text="%d" % num, bg=c["sfondo_celle"], fg=color,
                     font=(FONT_MONO, 10, "bold"), width=2,
                     anchor="e").pack(side="left")
            sq = tk.Canvas(r1, width=10, height=10, bg=color,
                           highlightthickness=0, bd=0)
            sq.pack(side="left", padx=(3, 4), pady=3)
            tk.Label(r1, text=sess["label"], bg=c["sfondo_celle"], fg=color,
                     font=self._f_small, anchor="w").pack(side="left")
            # Riga 2: best / media / n giri (indentata sotto la label)
            info = "B:%s  M:%s  %dg" % (
                _fmt(sess["best"]), _fmt(sess["media"]), sess["n_giri"])
            tk.Label(card, text=info, bg=c["sfondo_celle"],
                     fg=c["testo_dim"], font=self._f_small,
                     anchor="w").pack(fill="x", padx=(22, 0))
            # Riga 3 (opzionale): Pit: N - solo se identificati pit
            n_pit = len(sess.get("pit_idx", []))
            if n_pit > 0:
                tk.Label(card, text="Pit: %d" % n_pit,
                         bg=c["sfondo_celle"],
                         fg=c["stato_avviso"], font=self._f_small,
                         anchor="w").pack(fill="x", padx=(22, 0))

        # Scroll con rotella mouse sul pannello sessioni (Windows/Mac/Linux)
        def _on_sess_wheel(e):
            delta = -1 if (getattr(e, 'delta', 0) > 0
                           or getattr(e, 'num', 0) == 4) else 1
            sess_canvas.yview_scroll(delta, "units")
        sess_canvas.bind("<MouseWheel>", _on_sess_wheel)
        sess_canvas.bind("<Button-4>", _on_sess_wheel)
        sess_canvas.bind("<Button-5>", _on_sess_wheel)
        sess_inner.bind("<MouseWheel>", _on_sess_wheel)
        sess_inner.bind("<Button-4>", _on_sess_wheel)
        sess_inner.bind("<Button-5>", _on_sess_wheel)

        zoom_status = self._status_label(self.root,
            "+/- = zoom  |  frecce = sposta  |  0 = reset  |  "
            "L = pannello  |  T = pit  |  G = gara  P = prove  |  ESC = tempi")

        self._top.bind("<Escape>", lambda e: _esci_grafico())

        # ── Stato zoom/pan ──
        full_max_giri = max(s["n_giri"] for s in sessioni_sel)
        full_c_max = max(s["cumul"][-1] for s in sessioni_sel) * 1.05
        zoom_state = {
            "g_min": 0.0, "g_max": float(full_max_giri),
            "t_min": 0.0, "t_max": full_c_max,
            "show_panel": True,
            "show_pit": True,
        }

        # Toggle pannello sessioni (L): nasconde/ripristina la colonna DX
        # cosi' il grafico puo' occupare tutta la larghezza se serve
        def _toggle_legenda(e=None):
            zoom_state["show_panel"] = not zoom_state["show_panel"]
            if zoom_state["show_panel"]:
                sess_col.pack(side="right", fill="y", padx=(6, 0))
            else:
                sess_col.pack_forget()
            return "break"

        # Toggle marker pit-stop (T): per togliere rumore visivo quando
        # sono selezionate molte sessioni e i marker si sovrappongono.
        def _toggle_pit(e=None):
            zoom_state["show_pit"] = not zoom_state.get("show_pit", True)
            _draw()
            return "break"

        # ── Funzione di disegno (chiamata su resize e zoom/pan) ──
        def _draw(event=None):
            canvas.delete("all")
            cw = canvas.winfo_width()
            ch = canvas.winfo_height()
            if cw < 80 or ch < 60:
                return

            # Margini (left piu' largo per label MM:SS)
            ml, mr, mt, mb = 62, 15, 15, 28
            pw = cw - ml - mr
            ph = ch - mt - mb
            if pw < 30 or ph < 30:
                return

            # Range dalla vista corrente (zoom)
            vg_min = zoom_state["g_min"]
            vg_max = zoom_state["g_max"]
            vt_min = zoom_state["t_min"]
            vt_max = zoom_state["t_max"]
            vg_range = vg_max - vg_min if vg_max > vg_min else 1.0
            vt_range = vt_max - vt_min if vt_max > vt_min else 1.0

            # Coordinate con zoom
            def x_pos(giro):
                return ml + int((giro - vg_min) / vg_range * pw)

            def y_pos(val):
                return mt + int((1.0 - (val - vt_min) / vt_range) * ph)

            fg_grid = c["linee"]
            fg_label = c["testo_dim"]

            # Indicatore zoom attivo
            is_zoomed = (vg_min > 0.5 or vg_max < full_max_giri - 0.5 or
                         vt_min > 1.0 or vt_max < full_c_max - 1.0)
            if is_zoomed:
                canvas.create_text(cw - 4, 4, text="ZOOM",
                                   fill=c["stato_avviso"], font=(FONT_MONO, 8, "bold"),
                                   anchor="ne")

            # ── Griglia orizzontale (tempi cumulativi) ──
            raw_step = vt_range / 6
            if raw_step <= 5:
                step = 5
            elif raw_step <= 10:
                step = 10
            elif raw_step <= 30:
                step = 30
            elif raw_step <= 60:
                step = 60
            elif raw_step <= 120:
                step = 120
            else:
                step = max(60, int(math.ceil(raw_step / 60)) * 60)

            val = max(step, int(vt_min / step) * step)
            while val < vt_max:
                y = y_pos(val)
                if mt <= y <= mt + ph:
                    canvas.create_line(ml, y, ml + pw, y, fill=fg_grid, dash=(2, 4))
                    mm = int(val) // 60
                    ss = int(val) % 60
                    canvas.create_text(ml - 4, y, text="%d:%02d" % (mm, ss),
                                       fill=fg_label, font=(FONT_MONO, 8), anchor="e")
                val += step

            # ── Griglia verticale (giri) ──
            vis_giri = int(vg_max - vg_min)
            g_step = max(1, vis_giri // 10)
            gi = max(0, int(vg_min / g_step) * g_step)
            while gi <= vg_max:
                x = x_pos(gi)
                if ml <= x <= ml + pw:
                    canvas.create_line(x, mt, x, mt + ph, fill=fg_grid, dash=(2, 4))
                    canvas.create_text(x, mt + ph + 4, text=str(int(gi)),
                                       fill=fg_label, font=(FONT_MONO, 8), anchor="n")
                gi += g_step

            # Label assi
            canvas.create_text(ml + pw // 2, ch - 2, text="Giro",
                               fill=c["label"], font=(FONT_MONO, 9), anchor="s")
            canvas.create_text(4, mt + ph // 2, text="T",
                               fill=c["label"], font=(FONT_MONO, 9), anchor="w")

            # Assi
            canvas.create_line(ml, mt, ml, mt + ph, fill=c["label"], width=2)
            canvas.create_line(ml, mt + ph, ml + pw, mt + ph, fill=c["label"], width=2)

            # Clipping: dimensione font proporzionale allo zoom
            num_font_sz = min(11, max(7, int(9 * (full_max_giri / max(1, vg_range)))))
            num_font_sz = min(num_font_sz, 11)  # mai troppo grande

            # ── Linee sessioni (con numeri identificativi) ──
            for si, sess in enumerate(sessioni_sel):
                num = si + 1
                color = self._GRAPH_COLORS[si % len(self._GRAPH_COLORS)]
                cumul = sess["cumul"]
                points = []
                # Punto 0,0 (partenza) — solo se visibile
                if vg_min <= 0:
                    points.extend([x_pos(0), y_pos(0)])
                for gi, ct in enumerate(cumul):
                    g = gi + 1
                    if g >= vg_min - 1 and g <= vg_max + 1:
                        points.extend([x_pos(g), y_pos(ct)])

                if len(points) >= 4:
                    canvas.create_line(points, fill=color, width=2, smooth=False)

                # ── Marker pit-stop sui giri identificati ──
                # Anello vuoto (raggio 5) col bordo del colore della
                # sessione e centro = sfondo, con piccola "P" al centro.
                # Toggle via tasto T.
                if zoom_state.get("show_pit", True):
                    pit_idx = sess.get("pit_idx") or []
                    for pi in pit_idx:
                        if pi < 0 or pi >= len(cumul):
                            continue
                        gp = pi + 1  # giro 1-based
                        if gp < vg_min - 0.5 or gp > vg_max + 0.5:
                            continue
                        mx = x_pos(gp)
                        my = y_pos(cumul[pi])
                        if not (ml <= mx <= ml + pw
                                and mt <= my <= mt + ph):
                            continue
                        canvas.create_oval(mx - 5, my - 5, mx + 5, my + 5,
                                           outline=color, width=2,
                                           fill=c["sfondo_celle"])
                        canvas.create_text(mx, my, text="P",
                                           fill=color,
                                           font=(FONT_MONO, 7, "bold"))

                # Numero sulla linea a ~1/3 della vista visibile
                g_vis_mid = int(vg_min + (vg_max - vg_min) / 3)
                g_mark = max(1, min(g_vis_mid, len(cumul)))
                if 1 <= g_mark <= len(cumul):
                    mx = x_pos(g_mark)
                    my = y_pos(cumul[g_mark - 1])
                    if ml <= mx <= ml + pw and mt <= my <= mt + ph:
                        canvas.create_text(mx, my - 8, text=str(num),
                                           fill=color,
                                           font=(FONT_MONO, num_font_sz, "bold"),
                                           anchor="s")

                # Pallino + numero sul tempo finale
                g_end = len(cumul)
                if vg_min <= g_end <= vg_max + 2:
                    px_end = x_pos(g_end)
                    py_end = y_pos(cumul[-1])
                    canvas.create_oval(px_end - 3, py_end - 3, px_end + 3, py_end + 3,
                                       fill=color, outline=c["sfondo_celle"])
                    mm_tot = int(cumul[-1]) // 60
                    ss_tot = cumul[-1] - mm_tot * 60
                    canvas.create_text(px_end + 6, py_end,
                                       text="%d  %d:%04.1f" % (num, mm_tot, ss_tot),
                                       fill=color, font=(FONT_MONO, 7), anchor="w")

        canvas.bind("<Configure>", _draw)

        # ── Zoom e pan da tastiera ──
        def _zoom(factor, axis="both"):
            """Zoom centrato sulla vista corrente.
            La finestra mantiene la stessa larghezza calcolata anche quando
            tocca i bordi: se un lato sarebbe fuori dal dominio, l'altro
            viene spinto in compenso cosi' la vista resta simmetrica
            intorno al centro precedente (o incollata al bordo)."""
            def _nuovo_range(vmin, vmax, lo, hi, f):
                center = (vmin + vmax) / 2.0
                half = (vmax - vmin) / 2.0 * f
                # Limita la meta' allo spazio disponibile totale
                half = min(half, (hi - lo) / 2.0)
                nmin = center - half
                nmax = center + half
                # Sposta la finestra se esce dai bordi (senza deformarla)
                if nmin < lo:
                    nmax += (lo - nmin); nmin = lo
                if nmax > hi:
                    nmin -= (nmax - hi); nmax = hi
                # Clamp finale per sicurezza
                nmin = max(lo, nmin); nmax = min(hi, nmax)
                return nmin, nmax

            if axis in ("both", "x"):
                zoom_state["g_min"], zoom_state["g_max"] = _nuovo_range(
                    zoom_state["g_min"], zoom_state["g_max"],
                    0.0, float(full_max_giri), factor)
            if axis in ("both", "y"):
                zoom_state["t_min"], zoom_state["t_max"] = _nuovo_range(
                    zoom_state["t_min"], zoom_state["t_max"],
                    0.0, full_c_max * 1.2, factor)
            _draw()

        def _pan(dx_frac, dy_frac):
            """Pan come frazione della vista corrente."""
            g_range = zoom_state["g_max"] - zoom_state["g_min"]
            t_range = zoom_state["t_max"] - zoom_state["t_min"]
            dg = g_range * dx_frac
            dt = t_range * dy_frac
            # Pan orizzontale
            if dg != 0:
                new_gmin = zoom_state["g_min"] + dg
                new_gmax = zoom_state["g_max"] + dg
                if new_gmin >= 0 and new_gmax <= full_max_giri:
                    zoom_state["g_min"] = new_gmin
                    zoom_state["g_max"] = new_gmax
            # Pan verticale
            if dt != 0:
                new_tmin = zoom_state["t_min"] + dt
                new_tmax = zoom_state["t_max"] + dt
                if new_tmin >= 0 and new_tmax <= full_c_max * 1.2:
                    zoom_state["t_min"] = new_tmin
                    zoom_state["t_max"] = new_tmax
            _draw()

        def _reset_zoom(e=None):
            zoom_state["g_min"] = 0.0
            zoom_state["g_max"] = float(full_max_giri)
            zoom_state["t_min"] = 0.0
            zoom_state["t_max"] = full_c_max
            _draw()
            return "break"

        # Binding tastiera: sia su canvas (focus diretto) sia sul toplevel
        # cosi' funzionano anche se l'utente ha cliccato sul pannello
        # sessioni a destra e il focus e' uscito dal grafico.
        _kb = (
            ("<plus>",        lambda e: (_zoom(0.7), "break")[-1]),
            ("<equal>",       lambda e: (_zoom(0.7), "break")[-1]),
            ("<minus>",       lambda e: (_zoom(1.4), "break")[-1]),
            ("<KP_Add>",      lambda e: (_zoom(0.7), "break")[-1]),
            ("<KP_Subtract>", lambda e: (_zoom(1.4), "break")[-1]),
            ("<Left>",        lambda e: (_pan(-0.15, 0), "break")[-1]),
            ("<Right>",       lambda e: (_pan(0.15, 0), "break")[-1]),
            ("<Up>",          lambda e: (_pan(0, 0.15), "break")[-1]),
            ("<Down>",        lambda e: (_pan(0, -0.15), "break")[-1]),
            ("<Prior>",       lambda e: (_zoom(0.5), "break")[-1]),
            ("<Next>",        lambda e: (_zoom(2.0), "break")[-1]),
            ("<0>",           _reset_zoom),
            ("<Home>",        _reset_zoom),
            ("<l>",           _toggle_legenda),
            ("<L>",           _toggle_legenda),
            ("<t>",           _toggle_pit),
            ("<T>",           _toggle_pit),
            ("<g>",           lambda e: (_switch_modo("GARA"), "break")[-1]),
            ("<G>",           lambda e: (_switch_modo("GARA"), "break")[-1]),
            ("<p>",           lambda e: (_switch_modo("PROVE"), "break")[-1]),
            ("<P>",           lambda e: (_switch_modo("PROVE"), "break")[-1]),
        )
        for ev, fn in _kb:
            canvas.bind(ev, fn)
            self._top.bind(ev, fn)
        canvas.focus_set()

    def _alias_pilota(self):
        """Rinomina un pilota: chiede alias e sovrascrive in TUTTI i file scouting
        che hanno lo stesso nome originale. Salva anche nel registro piloti."""
        c = self.c
        focused = self._at.focus()
        if not focused:
            return
        idx = int(focused)
        if idx < 0 or idx >= len(self._tutti_sessioni):
            return

        sessione = self._tutti_sessioni[idx]
        nome_old = sessione.get("pilota", "?")
        transponder = sessione.get("transponder", "")

        # Popup per inserire alias
        popup = tk.Toplevel(self.root)
        popup.title("Alias Pilota")
        popup.config(bg=c["sfondo"])
        popup.geometry("400x160")
        popup.transient(self.root)
        popup.grab_set()

        tk.Label(popup, text="Nome attuale: %s" % nome_old,
                 bg=c["sfondo"], fg=c["stato_avviso"],
                 font=self._f_info).pack(pady=(12, 2))
        if transponder:
            tk.Label(popup, text="Transponder: %s" % transponder,
                     bg=c["sfondo"], fg=c["testo_dim"],
                     font=self._f_small).pack(pady=(0, 6))

        tk.Label(popup, text="Nuovo nome (alias):",
                 bg=c["sfondo"], fg=c["label"],
                 font=self._f_info).pack(pady=(4, 2))

        entry = tk.Entry(popup, font=self._f_info, width=25,
                         bg=c["sfondo_celle"], fg=c["dati"],
                         insertbackground=c["dati"],
                         relief="solid", bd=1)
        entry.pack(pady=4)
        entry.focus_set()

        def _applica(event=None):
            alias = entry.get().strip()
            if not alias:
                popup.destroy()
                return

            # Conta e rinomina tutti i file con lo stesso nome pilota
            scouting_dir = os.path.join(self.dati_dir, "scouting") if self.dati_dir else "scouting"
            contatore = 0
            # Cerca in tutti i path delle sessioni caricate
            for i, (sess, path) in enumerate(zip(self._tutti_sessioni, self._tutti_paths)):
                if sess.get("pilota", "") == nome_old:
                    # Aggiorna in memoria
                    sess["pilota"] = alias
                    # Aggiorna su disco
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            dati_file = json.load(f)
                        dati_file["pilota"] = alias
                        # Scrittura atomica
                        tmp_path = path + ".tmp"
                        with open(tmp_path, "w", encoding="utf-8") as f:
                            json.dump(dati_file, f, ensure_ascii=False, indent=2)
                            f.flush()
                            os.fsync(f.fileno())
                        os.replace(tmp_path, path)
                        contatore += 1
                    except Exception:
                        pass

            # Salva nel registro piloti (associa transponder -> alias)
            if transponder:
                self._save_pilota(alias, transponder, "", "")

            popup.destroy()

            # Aggiorna Treeview senza ricaricare tutto
            for child in self._at.get_children():
                ci = int(child)
                if ci < len(self._tutti_sessioni):
                    s = self._tutti_sessioni[ci]
                    if s.get("pilota") == alias:
                        vals = list(self._at.item(child, "values"))
                        vals[2] = alias[:20]  # Colonna pilota
                        self._at.item(child, values=vals)

            # Messaggio conferma
            if hasattr(self, '_tutti_status'):
                try:
                    self._tutti_status.config(
                        text="Rinominato '%s' -> '%s' in %d sessioni" % (
                            nome_old, alias, contatore),
                        fg=c["stato_ok"])
                except (tk.TclError, AttributeError):
                    pass

        entry.bind("<Return>", _applica)
        popup.bind("<Escape>", lambda e: popup.destroy())
        tk.Button(popup, text="APPLICA", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=_applica).pack(pady=6)

    def _pulisci_archivio(self):
        """Apre la schermata mega-filtro per cancellare sessioni mirate.

        Sostituisce il vecchio comportamento "cancella TUTTO" con una
        schermata inline (stessa convenzione delle altre TrackMind, niente
        popup): l'utente combina criteri (Pista, Pilota, Fonte, Data DA/A,
        Range giri sessione) tutti in AND. I criteri vuoti sono ignorati.
        Anteprima del numero di sessioni che verra' cancellato e conferma
        a doppia pressione.

        Se l'utente ha selezionato una o piu' righe in TUTTI I TEMPI prima
        di premere PULISCI, i campi del filtro si pre-compilano con i dati
        della selezione (singola riga = match esatto, piu' righe = solo
        i criteri che sono uniformi tra le righe + range di date)."""
        if not self._tutti_sessioni:
            return
        pre = self._pulisci_pre_da_selezione()
        self._schermata_pulisci_filtro(pre=pre)

    def _pulisci_pre_da_selezione(self):
        """Costruisce il dict di pre-compilazione del filtro PULISCI a
        partire dalle righe attualmente selezionate in TUTTI I TEMPI.

        Ritorna un dict con eventuali chiavi:
          pista, pilota, fonte, data_da, data_a
        I valori non determinabili (es. piste diverse fra le righe) sono
        omessi cosi' il campo resta vuoto e funge da "non filtrare"."""
        pre = {}
        try:
            sel = self._at.selection()
        except Exception:
            return pre
        if not sel:
            return pre

        indices = []
        for iid in sel:
            try:
                idx = int(iid)
            except (ValueError, TypeError):
                continue
            if 0 <= idx < len(self._tutti_sessioni):
                indices.append(idx)
        if not indices:
            return pre

        sess_sel = [self._tutti_sessioni[i] for i in indices]

        # Pista / Pilota: solo se TUTTE le righe selezionate condividono
        # lo stesso valore (altrimenti lascio vuoto, l'utente puo' cosi'
        # decidere se restringere o meno).
        piste = {(self._pista_da_sessione(s) or "").strip()
                 for s in sess_sel}
        if len(piste) == 1:
            v = piste.pop()
            if v:
                pre["pista"] = v
        piloti = {(s.get("pilota", "") or "").strip()
                  for s in sess_sel}
        if len(piloti) == 1:
            v = piloti.pop()
            if v:
                pre["pilota"] = v

        # Fonte: usa la stessa categorizzazione di _pulisci_filtra_match
        def _label_fonte(s):
            fr = s.get("tipo", s.get("_fonte", "?"))
            if fr == "myrcm":
                return "MYRCM"
            if fr == "speedhive":
                return "SPEEDHIVE"
            if (fr in ("Scouting", "laptimer")
                    or "scout" in str(s.get("setup", "")).lower()):
                return "LAPTIMER"
            if fr == "lapmonitor":
                return "LAPTIMER"
            return "SETUP"
        fonti = {_label_fonte(s) for s in sess_sel}
        if len(fonti) == 1:
            pre["fonte"] = fonti.pop()

        # Data: range da min a max delle date selezionate.
        # _parse_data_filtro restituisce ISO 'AAAA-MM-GG'; il RetroField
        # data si aspetta GG/MM/AAAA quindi converto.
        date_iso = sorted(filter(None,
            [self._parse_data_filtro(s.get("data", "")) for s in sess_sel]))
        if date_iso:
            try:
                y, m, d = date_iso[0].split("-")
                pre["data_da"] = "%s/%s/%s" % (d, m, y)
                y, m, d = date_iso[-1].split("-")
                pre["data_a"] = "%s/%s/%s" % (d, m, y)
            except Exception:
                pass

        return pre

    def _schermata_pulisci_filtro(self, pre=None):
        """Schermata inline con il mega-filtro per PULISCI - stile retrodb.

        Layout coerente con il resto dell'app: campi RetroField (cursore
        lampeggiante e separatori automatici per data) impilati verticalmente
        con etichette larghe 12 caratteri come nei form di inserimento.

          - header: < TEMPI + titolo + barra batteria
          - body: form con Pista/Pilota/Fonte/Data DA-A/Giri DA-A
                  in stile retrodb (RetroField a celle)
          - anteprima conteggio + eventuali warning
          - barra pulsanti: INDIETRO + ELIMINA (doppia pressione)
          - status_label finale con istruzioni

        Args:
            pre: dict opzionale {pista,pilota,fonte,data_da,data_a,giri_da,
                 giri_a} con valori iniziali da pre-compilare nei RetroField.
                 Tipicamente generato da _pulisci_pre_da_selezione() quando
                 l'utente ha selezionato delle righe in TUTTI I TEMPI.
        """
        c = self.c
        n_tot = len(self._tutti_sessioni)
        pre = pre or {}

        # Range giri (per hint sotto il form)
        try:
            giri_min_v = min(int(s.get("num_giri", 0) or 0)
                             for s in self._tutti_sessioni)
            giri_max_v = max(int(s.get("num_giri", 0) or 0)
                             for s in self._tutti_sessioni)
        except (ValueError, TypeError):
            giri_min_v, giri_max_v = 0, 0

        # Pulisce la root e disinstalla bind globali
        self._pulisci()

        # ── Header ──
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< TEMPI", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._schermata_tutti_tempi).pack(side="left")
        tk.Label(header, text="  PULISCI - Filtro",
                 bg=c["sfondo"], fg=c["stato_errore"],
                 font=self._f_title).pack(side="left", padx=(8, 0))
        # Barra batteria (come in TUTTI I TEMPI)
        try:
            from core.sd_bar import BarraBatteria as _BarraBat
            from core.batteria import get_batteria_info as _get_bat_info
            _pct, _ = _get_bat_info()
            if _pct is not None:
                _BarraBat(header,
                          get_info_func=_get_bat_info).pack(side="right",
                                                            padx=(6, 0))
        except Exception:
            pass
        tk.Label(header, text="%d sessioni in archivio" % n_tot,
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(side="right")

        tk.Frame(self.root, bg=c["linee"], height=1).pack(
            fill="x", padx=10, pady=(4, 4))

        # ── Help / istruzioni ──
        if pre:
            help_txt = ("Campi pre-compilati dalla riga selezionata. "
                        "Modifica o svuota cio' che non vuoi filtrare.")
            help_fg = c["stato_avviso"]
        else:
            help_txt = ("Compila i campi (vuoto = nessun filtro). "
                        "Pista/Pilota: anche solo parte del nome. "
                        "I criteri si combinano in AND.")
            help_fg = c["testo_dim"]
        tk.Label(self.root,
                 text=help_txt,
                 bg=c["sfondo"], fg=help_fg,
                 font=self._f_small,
                 wraplength=720, justify="center").pack(pady=(2, 6))

        # ── Form (stile retrodb: RetroField impilati) ──
        if not RetroField:
            tk.Label(self.root, text="RetroField non disponibile!",
                     bg=c["sfondo"], fg=c["stato_errore"],
                     font=self._f_btn).pack(pady=20)
            tk.Button(self.root, text="< INDIETRO", font=self._f_btn,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=self._schermata_tutti_tempi).pack(pady=8)
            return

        form = tk.Frame(self.root, bg=c["sfondo"])
        form.pack(padx=18, pady=4, fill="x")

        # Pista / Pilota / Fonte
        rf_pista = RetroField(form, label="Pista",
                              tipo="S", lunghezza=20, label_width=12)
        rf_pista.pack(pady=2, anchor="w", fill="x")

        rf_pilota = RetroField(form, label="Pilota",
                               tipo="S", lunghezza=20, label_width=12)
        rf_pilota.pack(pady=2, anchor="w", fill="x")

        rf_fonte = RetroField(form, label="Fonte",
                              tipo="S", lunghezza=10, label_width=12)
        rf_fonte.pack(pady=2, anchor="w", fill="x")
        tk.Label(form,
                 text="              vuoto=tutte | SPEEDHIVE | MYRCM | "
                      "LAPTIMER | SETUP",
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small,
                 anchor="w").pack(anchor="w")

        # Separatore visivo prima del blocco DATA
        tk.Frame(form, bg=c["linee"], height=1).pack(
            fill="x", pady=(6, 4))

        rf_data_da = RetroField(form, label="Data DA",
                                tipo="D", lunghezza=10, label_width=12)
        rf_data_da.pack(pady=2, anchor="w", fill="x")

        rf_data_a = RetroField(form, label="Data A",
                               tipo="D", lunghezza=10, label_width=12)
        rf_data_a.pack(pady=2, anchor="w", fill="x")

        # Separatore visivo prima del blocco GIRI
        tk.Frame(form, bg=c["linee"], height=1).pack(
            fill="x", pady=(6, 4))

        rf_giri_da = RetroField(form, label="Giri DA",
                                tipo="N", lunghezza=4, label_width=12)
        rf_giri_da.pack(pady=2, anchor="w", fill="x")

        rf_giri_a = RetroField(form, label="Giri A",
                               tipo="N", lunghezza=4, label_width=12)
        rf_giri_a.pack(pady=2, anchor="w", fill="x")
        tk.Label(form,
                 text="              num. giri sessione (range "
                      "%d-%d)" % (giri_min_v, giri_max_v),
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small,
                 anchor="w").pack(anchor="w")

        # Lista campi per iterazione (focus / refresh)
        rf_all = (rf_pista, rf_pilota, rf_fonte,
                  rf_data_da, rf_data_a, rf_giri_da, rf_giri_a)

        # ── Anteprima + warning ──
        lbl_preview = tk.Label(self.root, text="",
                               bg=c["sfondo"], fg=c["stato_avviso"],
                               font=self._f_btn)
        lbl_preview.pack(pady=(10, 4))
        lbl_help = tk.Label(self.root, text="",
                            bg=c["sfondo"], fg=c["testo_dim"],
                            font=self._f_small,
                            wraplength=720, justify="center")
        lbl_help.pack(pady=(0, 4))

        # Stato (matched + timestamp doppia pressione)
        state = {"matched": [], "armato_ts": 0}

        # Mappa di normalizzazione fonte: input utente -> nome canonico
        _FONTE_MAP = {
            "":          "TUTTE",
            "TUTTE":     "TUTTE",
            "SPEEDHIVE": "SpeedHive",
            "SH":        "SpeedHive",
            "MYRCM":     "MyRCM",
            "MR":        "MyRCM",
            "LAPTIMER":  "LapTimer",
            "LT":        "LapTimer",
            "SETUP":     "Setup",
            "ST":        "Setup",
        }

        def _criteri():
            fonte_raw = rf_fonte.get().strip().upper()
            return {
                "pista":   rf_pista.get().strip(),
                "pilota":  rf_pilota.get().strip(),
                "fonte":   _FONTE_MAP.get(fonte_raw, fonte_raw or "TUTTE"),
                "data_da": rf_data_da.get().strip(),
                "data_a":  rf_data_a.get().strip(),
                "giri_da": rf_giri_da.get().strip(),
                "giri_a":  rf_giri_a.get().strip(),
            }

        def _refresh(*_):
            # Reset doppia pressione su qualsiasi modifica del filtro
            state["armato_ts"] = 0
            cr = _criteri()

            # Validazioni soft (non bloccanti, ignorano i campi invalidi)
            warn = []
            if cr["data_da"] and not self._parse_data_filtro(cr["data_da"]):
                warn.append("Data 'DA' incompleta o non valida")
            if cr["data_a"] and not self._parse_data_filtro(cr["data_a"]):
                warn.append("Data 'A' incompleta o non valida")
            if cr["giri_da"] and not cr["giri_da"].isdigit():
                warn.append("Giri 'DA' deve essere un numero")
            if cr["giri_a"] and not cr["giri_a"].isdigit():
                warn.append("Giri 'A' deve essere un numero")
            if cr["fonte"] not in (
                    "TUTTE", "SpeedHive", "MyRCM", "LapTimer", "Setup"):
                warn.append("Fonte sconosciuta '%s'" % cr["fonte"])

            try:
                ind = self._pulisci_filtra_match(cr)
            except Exception as e:
                lbl_preview.config(text="Errore filtro: %s" % e,
                                   fg=c["stato_errore"])
                lbl_help.config(text="")
                state["matched"] = []
                btn_elimina.config(state="disabled", text="ELIMINA")
                return

            state["matched"] = ind
            n_match = len(ind)
            if n_match == 0:
                lbl_preview.config(text="Nessuna sessione corrisponde",
                                   fg=c["testo_dim"])
                btn_elimina.config(state="disabled", text="ELIMINA")
            elif n_match == n_tot:
                lbl_preview.config(
                    text="ATTENZIONE: TUTTE le %d sessioni saranno eliminate"
                         % n_tot,
                    fg=c["stato_errore"])
                btn_elimina.config(state="normal",
                                   text="ELIMINA TUTTE (%d)" % n_match)
            else:
                lbl_preview.config(
                    text="Saranno eliminate %d / %d sessioni"
                         % (n_match, n_tot),
                    fg=c["stato_avviso"])
                btn_elimina.config(state="normal",
                                   text="ELIMINA %d" % n_match)

            if warn:
                lbl_help.config(text=" * ".join(warn),
                                fg=c["stato_errore"])
            else:
                lbl_help.config(text="", fg=c["testo_dim"])

        def _esegui():
            ind = list(state["matched"])
            if not ind:
                return
            now = time.time()
            if now - state["armato_ts"] > 3:
                state["armato_ts"] = now
                self._tutti_status.config(
                    text="Premi ELIMINA di nuovo entro 3 secondi per confermare.",
                    fg=c["stato_errore"])
                return
            state["armato_ts"] = 0

            # Cancellazione fisica dei file
            paths_eli = set()
            for i in ind:
                if 0 <= i < len(self._tutti_paths):
                    p = self._tutti_paths[i]
                    try:
                        os.remove(p)
                        paths_eli.add(p)
                    except Exception:
                        pass

            # Rimozione dalle liste in memoria (dal piu' alto al piu' basso)
            for i in sorted(ind, reverse=True):
                if 0 <= i < len(self._tutti_sessioni):
                    try:
                        del self._tutti_sessioni[i]
                        del self._tutti_paths[i]
                    except Exception:
                        pass

            # Sincronizza liste raw (se presenti)
            if hasattr(self, '_tutti_sessioni_raw'):
                try:
                    raw_s, raw_p = [], []
                    for s, p in zip(self._tutti_sessioni_raw,
                                    self._tutti_paths_raw):
                        if p not in paths_eli:
                            raw_s.append(s)
                            raw_p.append(p)
                    self._tutti_sessioni_raw = raw_s
                    self._tutti_paths_raw = raw_p
                except Exception:
                    pass

            # Invalida cache + filtri pending
            try:
                self._tutti_sess_cache = None
            except Exception:
                pass
            for _attr in ("_at_cerca_pending", "_at_fonte_pending"):
                if hasattr(self, _attr):
                    try:
                        delattr(self, _attr)
                    except Exception:
                        pass

            # Se restano sessioni: torna a TUTTI I TEMPI ricaricato.
            # Se sono state cancellate tutte: torna a hub come faceva
            # la versione originale.
            if self._tutti_sessioni:
                self._schermata_tutti_tempi()
            else:
                if (hasattr(self, '_tempi_on_close')
                        and self._tempi_on_close == self._schermata_tempi):
                    self._schermata_hub()
                else:
                    self._schermata_hub_libero()

        # ── Barra pulsanti ──
        btn_bar = tk.Frame(self.root, bg=c["sfondo"])
        btn_bar.pack(pady=(8, 6))
        tk.Button(btn_bar, text="< INDIETRO", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2", width=12,
                  command=self._schermata_tutti_tempi)\
            .pack(side="left", padx=8)
        btn_elimina = tk.Button(btn_bar, text="ELIMINA",
                  font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["stato_errore"],
                  relief="ridge", bd=1, cursor="hand2", width=20,
                  state="disabled",
                  command=_esegui)
        btn_elimina.pack(side="left", padx=8)

        # Status label finale
        self._tutti_status = self._status_label(
            self.root,
            "Esc = indietro  |  Tab = campo successivo  |  "
            "L'anteprima si aggiorna mentre digiti")

        # Refresh anteprima ad ogni KeyRelease sui canvas dei RetroField.
        # RetroField cattura <Key> per gestire i caratteri ma non blocca
        # <KeyRelease>, quindi possiamo intercettarlo per riaggiornare il
        # conteggio in tempo reale.
        for rf in rf_all:
            try:
                rf._canvas.bind("<KeyRelease>", lambda e: _refresh())
            except Exception:
                pass

        # Esc = indietro
        self.root.bind("<Escape>",
                       lambda e: self._schermata_tutti_tempi())

        # ── Pre-compilazione dai dati della selezione ──
        # I RetroField espongono .set(value) per impostare il contenuto.
        if pre:
            _campi_pre = (
                ("pista",   rf_pista),
                ("pilota",  rf_pilota),
                ("fonte",   rf_fonte),
                ("data_da", rf_data_da),
                ("data_a",  rf_data_a),
                ("giri_da", rf_giri_da),
                ("giri_a",  rf_giri_a),
            )
            for chiave, rf in _campi_pre:
                val = (pre.get(chiave) or "").strip()
                if val:
                    try:
                        rf.set(val)
                    except Exception:
                        pass

        _refresh()
        rf_pista.set_focus()

    def _pulisci_filtra_match(self, criteri):
        """Restituisce gli INDICI delle sessioni in `_tutti_sessioni` che
        soddisfano TUTTI i criteri (AND). I criteri vuoti sono ignorati.

        Criteri attesi (tutti stringhe):
          pista, pilota: match esatto case-insensitive
          fonte: TUTTE | SpeedHive | MyRCM | LapTimer | Setup
          data_da, data_a: GG/MM/AAAA o ISO AAAA-MM-GG (estremi inclusi)
          giri_da, giri_a: numero giri della sessione (estremi inclusi)
        """
        pista_sel  = (criteri.get("pista") or "").strip().lower()
        pilota_sel = (criteri.get("pilota") or "").strip().lower()
        fonte_sel  = (criteri.get("fonte") or "TUTTE").strip()
        data_da    = self._parse_data_filtro(criteri.get("data_da", ""))
        data_a     = self._parse_data_filtro(criteri.get("data_a", ""))
        giri_da_s  = (criteri.get("giri_da") or "").strip()
        giri_a_s   = (criteri.get("giri_a") or "").strip()
        giri_da    = int(giri_da_s) if giri_da_s.isdigit() else None
        giri_a     = int(giri_a_s)  if giri_a_s.isdigit()  else None

        def _tag_di_fonte(s):
            """Stessa logica del Treeview di TUTTI I TEMPI: ritorna il
            tag fonte canonico per la sessione (speedhive/myrcm/
            scouting/setup)."""
            fr = s.get("tipo", s.get("_fonte", "?"))
            if fr == "myrcm":
                return "myrcm"
            if fr == "speedhive":
                return "speedhive"
            if (fr in ("Scouting", "laptimer")
                    or "scout" in str(s.get("setup", "")).lower()):
                return "scouting"
            if fr == "lapmonitor":
                return "scouting"
            return "setup"

        fonte_target = {
            "TUTTE":     None,
            "SpeedHive": "speedhive",
            "MyRCM":     "myrcm",
            "LapTimer":  "scouting",
            "Setup":     "setup",
        }.get(fonte_sel, None)

        risultato = []
        for i, s in enumerate(self._tutti_sessioni):
            # Pista (match "contiene", case-insensitive: l'utente puo'
            # digitare anche solo parte del nome)
            if pista_sel:
                pista_s = (self._pista_da_sessione(s) or "").strip().lower()
                if pista_sel not in pista_s:
                    continue
            # Pilota (match "contiene", case-insensitive)
            if pilota_sel:
                pilota_s = (s.get("pilota", "") or "").strip().lower()
                if pilota_sel not in pilota_s:
                    continue
            # Fonte
            if fonte_target is not None:
                if _tag_di_fonte(s) != fonte_target:
                    continue
            # Data (estremi inclusi)
            if data_da or data_a:
                data_iso = self._parse_data_filtro(s.get("data", ""))
                if data_da and (not data_iso or data_iso < data_da):
                    continue
                if data_a and (not data_iso or data_iso > data_a):
                    continue
            # Giri sessione
            if giri_da is not None or giri_a is not None:
                try:
                    ng = int(s.get("num_giri", 0) or 0)
                except (TypeError, ValueError):
                    ng = 0
                if giri_da is not None and ng < giri_da:
                    continue
                if giri_a is not None and ng > giri_a:
                    continue
            risultato.append(i)
        return risultato

    @staticmethod
    def _parse_data_filtro(s):
        """Normalizza una data utente o sessione in 'AAAA-MM-GG'.
        Accetta GG/MM/AAAA, GG/MM/AA, AAAA-MM-GG. Ritorna '' se invalida."""
        s = (s or "").strip()
        if not s:
            return ""
        if "/" in s:
            parts = s.split("/")
            if len(parts) == 3:
                d, m, y = parts
                if len(y) == 2:
                    y = "20" + y
                try:
                    return "%04d-%02d-%02d" % (int(y), int(m), int(d))
                except ValueError:
                    return ""
            return ""
        if "-" in s and len(s) >= 10:
            # ISO: AAAA-MM-GG
            try:
                y, m, d = s[:10].split("-")
                return "%04d-%02d-%02d" % (int(y), int(m), int(d))
            except (ValueError, IndexError):
                return ""
        return ""

    def _elimina_tutti(self):
        """Elimina tutte le sessioni selezionate (doppia pressione)."""
        c = self.c
        sel = self._at.selection()
        if not sel: return

        # Raccogli indici selezionati
        indici = []
        for iid in sel:
            idx = int(iid)
            if 0 <= idx < len(self._tutti_sessioni):
                indici.append(idx)
        if not indici:
            return

        now = time.time()
        if not hasattr(self, '_del_tutti_ts') or now - self._del_tutti_ts > 3:
            self._del_tutti_ts = now
            if len(indici) == 1:
                s = self._tutti_sessioni[indici[0]]
                self._tutti_status.config(
                    text="Eliminare %s %s %s? Premi ELIMINA di nuovo!" % (
                        s.get("pilota", "?"), s.get("data", "?"), s.get("ora", "?")[:5]),
                    fg=c["stato_errore"])
            else:
                self._tutti_status.config(
                    text="Eliminare %d sessioni selezionate? Premi ELIMINA di nuovo!" % len(indici),
                    fg=c["stato_errore"])
            return
        del self._del_tutti_ts

        # Elimina dal piu' alto al piu' basso per non sballare gli indici
        for idx in sorted(indici, reverse=True):
            try:
                os.remove(self._tutti_paths[idx])
            except Exception:
                pass
            self._tutti_sessioni.pop(idx)
            self._tutti_paths.pop(idx)

        if self._tutti_sessioni:
            self._tempi_on_close()
        else:
            if hasattr(self, '_tempi_on_close') and self._tempi_on_close == self._schermata_tempi:
                self._schermata_hub()
            else:
                self._schermata_hub_libero()

    # =================================================================
    #  RUN (standalone)
    # =================================================================
    def run(self):
        self.root.mainloop()
