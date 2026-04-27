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

    def __init__(self, parent=None, on_close=None, contesto=None):
        self._on_close = on_close
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
        (i candidati multipli vengono mostrati dalla label sotto)."""
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
        # Piu' candidati: nessun match (label mostra i suggerimenti)
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
        for w in self.root.winfo_children():
            w.destroy()
        for k in ("<Return>", "<Escape>", "<Up>", "<Down>",
                  "<Left>", "<Right>", "<Prior>", "<Next>", "<Home>",
                  "<plus>", "<equal>", "<minus>",
                  "<KP_Add>", "<KP_Subtract>", "<0>",
                  "<e>", "<p>", "<v>", "<a>", "<s>",
                  "<l>", "<L>", "<g>", "<G>", "<P>",
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

        btns = []
        descs = []
        voci = [
            ("CRONOMETRA", "Acquisisci tempi con cronometro manuale", self._avvia_cronometro),
            ("TEMPI", "Rivedi sessioni salvate, analisi e strategia", self._schermata_tempi),
        ]

        # SpeedHive solo se disponibile e dati presenti
        if _HAS_SPEEDHIVE and self.ctx.get("speedhive_id") and self.ctx.get("transponder"):
            voci.insert(1, ("SPEEDHIVE", "Importa tempi da SpeedHive (transponder %s)" %
                self.ctx.get("transponder", ""), self._importa_speedhive))

        # Confronta Setup: solo se modulo disponibile e contesto ha gli oggetti db
        if _HAS_CONFRONTA and self.ctx.get("_db"):
            voci.append(("CONFRONTA", "Compara tempi tra setup diversi (gomme, miscela, motore)",
                         self._lancia_confronta))

        # Tutti i tempi (archivio globale per confronto con altri piloti)
        voci.append(("TUTTI I TEMPI", "Confronta con tutti i piloti (archivio globale)", self._tutti_tempi_da_setup))

        # Scouting sempre disponibile
        voci.append(("SCOUTING", "Cronometra senza setup (dati in scouting/)", self._schermata_scouting))

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

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(10, 4))

        # ── Riepilogo setup passato dal contesto ──
        riepilogo = tk.Frame(self.root, bg=c["sfondo"])
        riepilogo.pack(fill="x", padx=20, pady=(4, 2))

        # Colonna sinistra: dati sessione
        col_sx = tk.Frame(riepilogo, bg=c["sfondo"])
        col_sx.pack(side="left", anchor="nw", padx=(0, 30))

        righe_sx = []
        if self.ctx.get("pista"):
            righe_sx.append(("Pista", self.ctx["pista"]))
        if self.ctx.get("data") or self.ctx.get("ora"):
            dt = []
            if self.ctx.get("data"):
                dt.append(self.ctx["data"])
            if self.ctx.get("ora"):
                dt.append(self.ctx["ora"])
            righe_sx.append(("Sessione", " / ".join(dt)))
        if self.ctx.get("transponder"):
            righe_sx.append(("Transponder", self.ctx["transponder"]))
        # Meteo
        meteo_parts = []
        if self.ctx.get("temp_esterna"):
            meteo_parts.append("Aria %s" % self.ctx["temp_esterna"])
        if self.ctx.get("temp_pista"):
            meteo_parts.append("Pista %s" % self.ctx["temp_pista"])
        if self.ctx.get("umidita"):
            meteo_parts.append("Um. %s%%" % self.ctx["umidita"])
        if self.ctx.get("condizioni_pista"):
            meteo_parts.append(self.ctx["condizioni_pista"])
        if self.ctx.get("vento"):
            meteo_parts.append("Vento %s" % self.ctx["vento"])
        if meteo_parts:
            righe_sx.append(("Meteo", "  ".join(meteo_parts)))

        for etichetta, valore in righe_sx:
            row = tk.Frame(col_sx, bg=c["sfondo"])
            row.pack(anchor="w", pady=1)
            tk.Label(row, text="%s:" % etichetta, width=12, anchor="e",
                     bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small).pack(side="left")
            tk.Label(row, text=" %s" % valore,
                     bg=c["sfondo"], fg=c["dati"], font=self._f_small).pack(side="left")

        # Colonna destra: riferimenti setup (ref_*)
        col_dx = tk.Frame(riepilogo, bg=c["sfondo"])
        col_dx.pack(side="left", anchor="nw")

        righe_dx = []
        for chiave, val in sorted(self.ctx.items()):
            if chiave.startswith("ref_") and val:
                nome = chiave[4:].replace("_", " ").capitalize()
                righe_dx.append((nome, val))

        for etichetta, valore in righe_dx:
            row = tk.Frame(col_dx, bg=c["sfondo"])
            row.pack(anchor="w", pady=1)
            tk.Label(row, text="%s:" % etichetta, width=12, anchor="e",
                     bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small).pack(side="left")
            tk.Label(row, text=" %s" % valore,
                     bg=c["sfondo"], fg=c["dati"], font=self._f_small).pack(side="left")

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
        """Copia l'ultimo file lap_*.json da dati_dir a scouting_dir."""
        try:
            os.makedirs(scouting_dir, exist_ok=True)
            # Trova i file lap_*.json in dati_dir (non nelle sottocartelle)
            files = [f for f in os.listdir(dati_dir)
                     if f.startswith("lap_") and f.endswith(".json")
                     and os.path.isfile(os.path.join(dati_dir, f))]
            if not files:
                return
            # Prendi il piu' recente per data modifica
            files.sort(key=lambda f: os.path.getmtime(os.path.join(dati_dir, f)))
            ultimo = files[-1]
            src = os.path.join(dati_dir, ultimo)
            dst = os.path.join(scouting_dir, ultimo)
            if not os.path.exists(dst):
                with open(src, "r", encoding="utf-8") as f:
                    contenuto = f.read()
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(contenuto)
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
                # Copia in scouting per "Tutti i tempi"
                if scouting_dir:
                    try:
                        os.makedirs(scouting_dir, exist_ok=True)
                        dst = os.path.join(scouting_dir, os.path.basename(path))
                        if not os.path.exists(dst):
                            with open(path, "r", encoding="utf-8") as _f:
                                _c = _f.read()
                            with open(dst, "w", encoding="utf-8") as _f:
                                _f.write(_c)
                    except Exception:
                        pass

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
                    # Disabilita takefocus su CRONOMETRO cosi' TAB
                    # dall'ultimo campo del form salta CRONOMETRO e va
                    # direttamente a RICERCA (tk_focusNext() rispetta
                    # takefocus=0 e salta il widget). CRONOMETRO resta
                    # cliccabile col mouse.
                    try:
                        self._btn_crono_man.config(takefocus=0)
                    except Exception:
                        pass
                    self._speedhive_live_visible = True
                elif not should_show and self._speedhive_live_visible:
                    self._btn_speedhive_live.pack_forget()
                    # Ripristina takefocus su CRONOMETRO: adesso il
                    # TAB dai campi deve tornare a lui come default
                    # (RICERCA non e' piu' disponibile).
                    try:
                        self._btn_crono_man.config(takefocus=1)
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

                # Indice locale {chipCode: set(session_ids)} per skip
                # delle sessioni gia' scaricate (vedi _ricerca_completa).
                indice_locale = {}
                try:
                    for f in os.listdir(scouting_dir):
                        if not f.endswith(".json"):
                            continue
                        try:
                            with open(os.path.join(scouting_dir, f),
                                      "r", encoding="utf-8") as fp:
                                d = json.load(fp)
                            if d.get("data") != data_str:
                                continue
                            ch = str(d.get("transponder", "")).strip()
                            sid_l = d.get("speedhive_session")
                            if ch and sid_l:
                                indice_locale.setdefault(
                                    ch, set()).add(sid_l)
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
                        sid_locali = indice_locale.get(chip_str, set())
                        dati = sh_scarica(aid)
                        if not dati or "sessions" not in dati:
                            return local_saved, local_skipped
                        for sess in dati.get("sessions", []):
                            sid = sess.get("id", 0)
                            if sid and sid in sid_locali:
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
                for chip, sid, sessione in nuove_sessioni:
                    path = os.path.join(scouting_dir,
                        "lap_speedhive_%s_%s_s%d.json"
                        % (ts, str(chip)[-6:], sid))
                    try:
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump(sessione, f,
                                      ensure_ascii=False, indent=2)
                        saved += 1
                    except Exception:
                        pass

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

    def _ricerca_completa(self, data_str):
        """Cerca tempi su TUTTE le fonti (SpeedHive + MyRCM) e apre Tutti i Tempi."""
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

        # Schermata di attesa con animazione
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
                    indice_locale = {}
                    try:
                        for f in os.listdir(scouting_dir):
                            if not f.endswith(".json"):
                                continue
                            try:
                                with open(os.path.join(scouting_dir, f),
                                          "r", encoding="utf-8") as fp:
                                    d = json.load(fp)
                                if d.get("data") != data_str:
                                    continue
                                ch = str(d.get("transponder", "")).strip()
                                sid_l = d.get("speedhive_session")
                                if ch and sid_l:
                                    indice_locale.setdefault(
                                        ch, set()).add(sid_l)
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
                        # sessioni nuove da salvare. Le sessioni gia'
                        # presenti (sid in indice_locale) vengono
                        # scartate senza scrittura su disco.
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
                                sid_locali = indice_locale.get(chip_str, set())
                                dati = sh_scarica(aid)
                                if not dati or "sessions" not in dati:
                                    return local_saved, local_skipped
                                for sess in dati.get("sessions", []):
                                    sid = sess.get("id", 0)
                                    # SKIP: sessione gia' scaricata in
                                    # passato. Niente HTTP risparmio qui
                                    # (la lista sessioni e' arrivata col
                                    # singolo GET di sh_scarica), ma
                                    # niente IO/parsing/scrittura.
                                    if sid and sid in sid_locali:
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
                        # Solo le nuove sessioni: niente delete+rewrite di
                        # file gia' presenti.
                        for chip, sid, sessione in nuove_sessioni:
                            path = os.path.join(scouting_dir,
                                "lap_speedhive_%s_%s_s%d.json"
                                % (ts, str(chip)[-6:], sid))
                            try:
                                with open(path, "w", encoding="utf-8") as f:
                                    json.dump(sessione, f,
                                              ensure_ascii=False, indent=2)
                                saved_sh.append(path)
                            except Exception:
                                pass

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
            n_scouting = 0
            try:
                for f in os.listdir(scouting_dir):
                    if f.endswith(".json"):
                        n_scouting += 1
            except Exception:
                pass

            def _mostra_risultato():
                self._ricerca_attiva = False
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
    def _trova_tutte_sessioni(self):
        """Cerca sessioni lap_*.json.
        Modalita' setup: dati/ + dati/scouting/ (tutto del setup).
        Modalita' libera (da menu): solo dati/scouting/ (le sessioni setup
        si vedono dal CRONO del rispettivo setup).
        Sostituisce i nomi anonimi (es. "Trasp. 1234567" o numero
        nudo "1234567") con il nome reale se il transponder e' nella
        tabella trasponder.json (importata da MyRCM partecipanti)
        o nel registro piloti.json (alias manuali)."""
        sessioni = []
        paths = []
        if not self.dati_dir or not os.path.isdir(self.dati_dir):
            return sessioni, paths

        modo_setup = hasattr(self, '_modo_setup') and self._modo_setup

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

        # Sessioni da dati/scouting/
        scouting_dir = os.path.join(self.dati_dir, "scouting")
        if os.path.isdir(scouting_dir):
            for f in sorted(os.listdir(scouting_dir), reverse=True):
                if f.endswith(".json"):
                    fp = os.path.join(scouting_dir, f)
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
        search_bar.pack(fill="x", padx=10, pady=(2, 2))
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

        # Tag colori per fonte
        self._at.tag_configure("setup", foreground=c["dati"])
        self._at.tag_configure("scouting", foreground=c["stato_avviso"])
        self._at.tag_configure("speedhive", foreground=c["cerca_testo"])
        self._at.tag_configure("myrcm", foreground="#ff6600")  # Arancio gara
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
                    sn = s.get("myrcm_sessione_nome", "").lower()
                    if "finale" in sn or "finals" in sn:
                        fase = "Finale"
                    elif "qualif" in sn:
                        fase = "Qualif"
                    elif "prove" in sn:
                        fase = "Prove"
                    else:
                        fase = "Gara"
                elif fonte_raw == "speedhive":
                    tag = "speedhive"
                    fase = "Libere"
                elif fonte_raw in ("Scouting", "laptimer") or "scout" in str(s.get("setup", "")).lower():
                    tag = "scouting"
                    fase = "Libere"
                else:
                    tag = "setup"
                    fase = "Setup"
                self._at.insert("", "end", iid=str(i),
                    values=(data, ora, pilota, pista, fase, n_giri, best, media),
                    tags=(tag,))

        # ── Cache righe per filtro CERCA ──
        self._at_all_rows = []
        for child in self._at.get_children():
            vals = self._at.item(child, "values")
            tag = self._at.item(child, "tags")
            self._at_all_rows.append((child, vals, tag))

        def _at_filtra(*args):
            """Filtra righe nel Treeview in tempo reale.
            Supporta parole multiple (AND): 'angelino ponte' trova righe
            che contengono sia 'angelino' che 'ponte'. Case-insensitive."""
            testo = self._at_cerca_var.get().strip().lower()
            parole = testo.split() if testo else []
            self._at.delete(*self._at.get_children())
            count = 0
            for iid, vals, tags in self._at_all_rows:
                if not parole:
                    match = True
                else:
                    riga = " ".join(str(v).lower() for v in vals)
                    match = all(p in riga for p in parole)
                if match:
                    self._at.insert("", "end", iid=iid, values=vals, tags=tags)
                    count += 1
            tot = len(self._at_all_rows)
            if testo:
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

        # Tag per riga con focus: inversione colori (sfondo verde chiaro, testo nero)
        self._at.tag_configure("focused",
            background=c["cursore"], foreground=c["testo_cursore"])
        # Tag per righe selezionate per IA (✓): sfondo leggermente illuminato
        self._at.tag_configure("checked",
            background=c["pulsanti_sfondo"], foreground=c["dati"])

        self._prev_focused = None
        def _aggiorna_selezione(event=None):
            sel = set(self._at.selection())
            focused = self._at.focus()
            # Ripristina tag della riga precedente
            if self._prev_focused and self._prev_focused != focused:
                old = self._prev_focused
                try:
                    old_tags = [t for t in self._at.item(old, "tags") if t not in ("focused", "checked")]
                    if old in sel:
                        old_tags.append("checked")
                    self._at.item(old, tags=tuple(old_tags))
                except Exception:
                    pass
            # Aggiorna tag di tutte le righe selezionate/deselezionate
            for child in self._at.get_children():
                cur_tags = [t for t in self._at.item(child, "tags") if t not in ("focused", "checked")]
                if child == focused:
                    cur_tags.append("focused")
                elif child in sel:
                    cur_tags.append("checked")
                self._at.item(child, tags=tuple(cur_tags))
            self._prev_focused = focused
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

        # Shift+Tab dal Treeview torna a CERCA
        self._at.bind("<Shift-Tab>", lambda e: (
            self._at_search_entry.focus_set(),
            self._at_search_entry.select_range(0, "end"),
            "break")[-1])

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

        def _tab_cerca_to_tree(e):
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

        self._at_search_entry.bind("<Tab>", _tab_cerca_to_tree)
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
            "SPAZIO = pausa  |  +/- velocita'  |  R = riavvolgi  |  ESC = esci")

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
            self._top.unbind("<space>")
            self._top.unbind("<plus>")
            self._top.unbind("<equal>")
            self._top.unbind("<KP_Add>")
            self._top.unbind("<minus>")
            self._top.unbind("<KP_Subtract>")
            self._top.unbind("<r>")
            self._top.unbind("<R>")
        except Exception:
            pass
        self._tempi_on_close()

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

        # Range fisso basato sulla sessione piu' lunga (leader reale)
        # cosi' le curve crescono dall'origine senza rescale continuo.
        max_giri = max(s["total_laps"] for s in self._ghost_sessioni)
        max_tempo = max(s["total_time"] for s in self._ghost_sessioni)
        if max_giri < 1: max_giri = 1
        if max_tempo < 1: max_tempo = 1
        # Piccolo padding Y
        vt_max = max_tempo * 1.05

        def xp(giri):
            return ml + int(giri / max_giri * pw)

        def yp(tempo):
            # Y cresce verso l'alto nel grafico (ma tk Y cresce verso il
            # basso): inverti cosi' tempo=0 sta in basso, tempo_max in alto
            return mt + int((1.0 - tempo / vt_max) * ph)

        fg_grid = c["linee"]
        fg_label = c["testo_dim"]

        # Assi + griglia verticale (giri)
        n_ticks_x = min(10, max_giri)
        step_x = max(1, max_giri // n_ticks_x)
        gi = 0
        while gi <= max_giri:
            x = xp(gi)
            canvas.create_line(x, mt, x, mt + ph,
                                fill=fg_grid, dash=(2, 4))
            canvas.create_text(x, mt + ph + 4, text=str(gi),
                                fill=fg_label, font=(FONT_MONO, 8), anchor="n")
            gi += step_x
        # Griglia orizzontale (tempi) ogni ~60s
        tick_step = 60.0
        while vt_max / tick_step > 8:
            tick_step *= 2
        while vt_max / tick_step < 3 and tick_step > 15:
            tick_step /= 2
        v = tick_step
        while v < vt_max:
            y = yp(v)
            canvas.create_line(ml, y, ml + pw, y,
                                fill=fg_grid, dash=(2, 4))
            mm = int(v) // 60
            ss = int(v) % 60
            canvas.create_text(ml - 4, y,
                                text="%d:%02d" % (mm, ss),
                                fill=fg_label, font=(FONT_MONO, 8), anchor="e")
            v += tick_step
        # Assi principali
        canvas.create_line(ml, mt, ml, mt + ph,
                            fill=c["label"], width=2)
        canvas.create_line(ml, mt + ph, ml + pw, mt + ph,
                            fill=c["label"], width=2)
        canvas.create_text(ml + pw // 2, H - 8, text="Giro",
                            fill=c["label"], font=(FONT_MONO, 9), anchor="s")

        # Calcola posizioni attuali per ogni pilota + ranking
        posizioni = []
        for i, sess in enumerate(self._ghost_sessioni):
            laps_at_t = self._ghost_laps_at_time(sess, self._ghost_t)
            # tempo cumulato al punto raggiunto (interpolato)
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
            posizioni.append({"idx": i, "sess": sess,
                               "laps": laps_at_t, "tempo": tempo_acc})
        # Sort per giri desc, poi tempo asc (leader prima)
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
        # cosi' le teste del leader vanno in cima visivamente)
        for p in reversed(posizioni):
            sess = p["sess"]
            laps_at_t = p["laps"]
            if laps_at_t <= 0:
                continue
            color = sess["color"]
            idx = p["idx"]

            # Costruisci punti: (0,0), (1, cumul[0]), ...
            pts_xy = [xp(0), yp(0)]
            n_full = int(laps_at_t)
            for i in range(min(n_full, len(sess["cumul"]))):
                pts_xy.extend([xp(i + 1), yp(sess["cumul"][i])])
            # Punto parziale
            frac = laps_at_t - n_full
            if frac > 0 and n_full < len(sess["tempi"]):
                prev_cumul = sess["cumul"][n_full - 1] if n_full > 0 else 0.0
                partial_tempo = prev_cumul + sess["tempi"][n_full] * frac
                pts_xy.extend([xp(laps_at_t), yp(partial_tempo)])
            # Disegna linea
            if len(pts_xy) >= 4:
                canvas.create_line(*pts_xy, fill=color, width=2,
                                    smooth=False)

            # Pallino sulla testa
            head_x = pts_xy[-2]
            head_y = pts_xy[-1]
            # Flash se appena sorpassato
            flash_count = self._ghost_flash.get(idx, 0)
            if flash_count > 0:
                self._ghost_flash[idx] = flash_count - 1
                canvas.create_oval(head_x - 10, head_y - 10,
                                    head_x + 10, head_y + 10,
                                    outline=c["stato_avviso"], width=3)
            canvas.create_oval(head_x - 5, head_y - 5,
                                head_x + 5, head_y + 5,
                                fill=color, outline=color)

            # Etichetta nome pilota accanto al pallino
            nome_x = head_x + 10
            nome_y = head_y
            # Se la testa e' troppo vicino al bordo destro, metti l'etichetta
            # a sinistra del pallino
            if head_x > ml + pw - 80:
                nome_x = head_x - 10
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
        canvas.create_text(W // 2, 48, text="gara: %d%%   x%.1f" %
                            (pct, self._ghost_speed),
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
        MIN_GIRI = 4  # salta solo sessioni brevissime (1-3 giri)
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
            msg = "%d sessioni selezionate per %s" % (len(to_select), s_target.get("pilota", "?"))
            if skipped:
                msg += " | %d saltate (1-3 giri)" % skipped
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

    def _lancia_confronta(self):
        """Lancia il modulo Confronta Setup per comparare sessioni cross-setup."""
        if not _HAS_CONFRONTA:
            return
        db = self.ctx.get("_db")
        table_def = self.ctx.get("_table_def")
        ref_dbs = self.ctx.get("_ref_dbs", {})
        indici = self.ctx.get("_indici_visibili", [])
        if not db or not table_def:
            return
        self._pulisci()
        ConfrontaSetup(
            parent=self.root,
            db=db,
            table_def=table_def,
            ref_dbs=ref_dbs,
            dati_dir=self.dati_dir,
            indici_visibili=indici,
            sessione=None,
            on_close=self._schermata_hub,
            colori=self.c)

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
                    tempi = [g["tempo"] for g in giri if g.get("tempo", 0) > 0]
                    if tempi:
                        cumul = []
                        tot = 0.0
                        for t in tempi:
                            tot += t
                            cumul.append(tot)
                        label = "%s %s %s" % (
                            s.get("pilota", "?")[:12],
                            s.get("data", "?")[-5:],
                            s.get("ora", "?")[:5])
                        sessioni_sel.append({
                            "label": label, "tempi": tempi, "cumul": cumul,
                            "best": s.get("miglior_tempo", 0),
                            "media": s.get("media", 0),
                            "n_giri": len(tempi)})
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
            "+/- = zoom  |  frecce = sposta  |  0 = reset  |  L = pannello  |  G = gara  P = prove  |  ESC = tempi")

        self._top.bind("<Escape>", lambda e: _esci_grafico())

        # ── Stato zoom/pan ──
        full_max_giri = max(s["n_giri"] for s in sessioni_sel)
        full_c_max = max(s["cumul"][-1] for s in sessioni_sel) * 1.05
        zoom_state = {
            "g_min": 0.0, "g_max": float(full_max_giri),
            "t_min": 0.0, "t_max": full_c_max,
            "show_panel": True,
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
        """Cancella TUTTE le sessioni visibili in lista (doppia pressione)."""
        c = self.c
        n = len(self._tutti_sessioni)
        if n == 0:
            return

        now = time.time()
        if not hasattr(self, '_del_archivio_ts') or now - self._del_archivio_ts > 3:
            self._del_archivio_ts = now
            self._tutti_status.config(
                text="PULISCI: cancellare TUTTE le %d sessioni in lista? Premi di nuovo!" % n,
                fg=c["stato_errore"])
            return
        del self._del_archivio_ts

        # Cancella tutti i file visibili
        eliminati = 0
        for path in self._tutti_paths:
            try:
                os.remove(path)
                eliminati += 1
            except Exception:
                pass

        # Aggiorna anche i raw se presenti
        if hasattr(self, '_tutti_sessioni_raw'):
            paths_set = set(self._tutti_paths)
            raw_s, raw_p = [], []
            for s, p in zip(self._tutti_sessioni_raw, self._tutti_paths_raw):
                if p not in paths_set:
                    raw_s.append(s)
                    raw_p.append(p)
            self._tutti_sessioni_raw = raw_s
            self._tutti_paths_raw = raw_p

        self._tutti_sessioni = []
        self._tutti_paths = []

        # Torna alla schermata precedente
        if hasattr(self, '_tempi_on_close') and self._tempi_on_close == self._schermata_tempi:
            self._schermata_hub()
        else:
            self._schermata_hub_libero()

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
