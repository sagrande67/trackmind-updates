"""
TrackMind LapTimer v2.0 - Cronometro da pista con gestione carburante
Software opzionale per prove libere.
Lanciato da TrackMind con riferimento a setup e pilota.

Uso: python laptimer.py --setup "Nome Setup" --pilota "Nome Pilota" --dati-dir "/percorso/dati"
     oppure senza argomenti per test standalone.

Flusso:
  1. Scegli serbatoio (125cc / 150cc)
  2. SPAZIO = Avvia cronometro
  3. SPAZIO = Segna giro ad ogni passaggio
  4. ESC   = Stop, revisiona giri (VALIDO/ESCLUSO/PIT), salva
"""

from version import __version__

import tkinter as tk
from tkinter import font as tkfont, ttk
import time, json, os, sys, argparse
from datetime import datetime

# Guardia anti-popup di sistema (uConsole): tiene la finestra del cronometro
# sopra a eventuali dialog di NetworkManager, keyring, Bluetooth, batteria,
# polkit che potrebbero apparire durante un giro.
try:
    from core.focus_guard import proteggi_finestra_sicura as _proteggi_finestra
except Exception:
    try:
        # Anche quando laptimer.py gira come processo separato con cwd
        # diverso, il parent contiene core/
        import os as _os, sys as _sys
        _here = _os.path.dirname(_os.path.abspath(__file__))
        _parent = _os.path.dirname(_here)
        if _parent not in _sys.path:
            _sys.path.insert(0, _parent)
        from core.focus_guard import proteggi_finestra_sicura as _proteggi_finestra
    except Exception:
        def _proteggi_finestra(root, **kwargs):
            return

# Stampa termica (opzionale)
try:
    from core.thermal_print import (genera_scheda_gara, stampa_bluetooth,
                                     _fmt_tempo, _linea, _centra, _riga, W)
    _HAS_PRINT = True
except ImportError:
    _HAS_PRINT = False

# Analisi IA (opzionale)
try:
    from ai_analisi import AIAnalisi
    _HAS_AI = True
except ImportError:
    _HAS_AI = False

# Font monospace per compatibilità cross-platform
try:
    from config_colori import FONT_MONO
except ImportError:
    import sys as _sys
    FONT_MONO = "Consolas" if _sys.platform == "win32" else "DejaVu Sans Mono"

# ─────────────────────────────────────────────────────────────────────
#  FUNZIONE STANDALONE: classificazione giri (importabile da retrodb)
# ─────────────────────────────────────────────────────────────────────
def classifica_giri(giri):
    """Pre-classifica automatica di una lista giri.
    - PIT STOP: giri oltre +10sec dalla mediana
    - INCIDENTE?: giri oltre +20% dalla mediana (segnalati, restano validi)
    Modifica i giri in-place. Ritorna numero di giri classificati."""
    if not giri or len(giri) < 3:
        return 0
    if any(g.get("stato") not in ("valido", None) for g in giri):
        return 0
    tempi = sorted([g["tempo"] for g in giri if g.get("tempo", 0) > 0])
    n = len(tempi)
    if n < 3:
        return 0
    mediana = tempi[n // 2] if n % 2 == 1 else (tempi[n // 2 - 1] + tempi[n // 2]) / 2.0
    soglia_segnalato = mediana * 1.20
    soglia_pit = mediana + 10.0
    classificati = 0
    for g in giri:
        if "stato" not in g:
            g["stato"] = "valido"
        t = g.get("tempo", 0)
        if t > soglia_pit:
            g["stato"] = "pit"
            g["segnalato"] = False
            classificati += 1
        elif t > soglia_segnalato:
            g["segnalato"] = True
            classificati += 1
        else:
            g["segnalato"] = False
    return classificati


_LAPTIMER_TOKEN_SECRET = b"TrKm1nd_L4pT1m3r_T0k3n!!"

def _verifica_token(token):
    import hashlib, time
    if not token:
        return False
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


DEFAULT_COLORS = {
    "sfondo":       "#0a0a0a",
    "dati":         "#39ff14",
    "label":        "#22aa22",
    "testo_dim":    "#1a6a1a",
    "stato_ok":     "#39ff14",
    "stato_avviso": "#ffaa00",
    "stato_errore": "#ff5555",
    "linee":        "#1a5a0a",
    "pulsanti_sfondo": "#1a3a1a",
    "pulsanti_testo":  "#39ff14",
}

def _carica_colori():
    colori = DEFAULT_COLORS.copy()
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = os.path.join(base, "colori.cfg")
    if os.path.exists(cfg):
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                for riga in f:
                    riga = riga.strip()
                    if not riga or riga.startswith("#") or "=" not in riga:
                        continue
                    k, v = riga.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k in colori and v.startswith("#"):
                        colori[k] = v
        except Exception:
            pass
    return colori


def _fmt(secondi):
    if secondi < 0:
        return "-" + _fmt(-secondi)
    m = int(secondi) // 60
    s = secondi - m * 60
    return "%02d:%05.2f" % (m, s)

def _fmt_delta(delta):
    if abs(delta) < 0.005:
        return "  0.00"
    return "%s%.2f" % ("+" if delta > 0 else "-", abs(delta))


SERBATOI = [125, 150]

class LapTimer:
    FUEL_SELECT = 0
    ATTESA      = 1
    RUNNING     = 2
    FERMO       = 3   # cronometro fermo, mostra riepilogo, attesa conferma
    STOP        = 4

    def __init__(self, setup="", pilota="", pista="", dati_dir="", record_id="",
                 parent=None, on_close=None, setup_snapshot=None):
        self.setup = setup or "Setup Sconosciuto"
        self.pilota = pilota or "Pilota"
        self.pista = pista or ""
        self.dati_dir = dati_dir
        self.record_id = record_id
        self.setup_snapshot = setup_snapshot or {}  # dati setup "fotografati"
        self._on_close = on_close
        self._embedded = parent is not None
        self.stato = self.FUEL_SELECT
        self.serbatoio = 0
        self.t_start = 0.0
        self.t_ultimo_giro = 0.0
        self.giri = []
        self.miglior_tempo = None
        self._space_locked = False
        self._lock_tempo = 1.0
        self.colori = _carica_colori()
        self._init_root(parent)
        self._init_fonts()
        self._schermata_carburante()

    def _init_root(self, parent=None):
        c = self.colori
        if parent:
            self.root = parent
        else:
            # Windows: rende il processo DPI-aware PRIMA di creare Tk,
            # cosi' la finestra non viene scalata automaticamente e il
            # rendering e' identico a uConsole (pixel 1:1).
            if sys.platform == "win32":
                try:
                    import ctypes
                    try:
                        ctypes.windll.shcore.SetProcessDpiAwareness(2)
                    except Exception:
                        try:
                            ctypes.windll.shcore.SetProcessDpiAwareness(1)
                        except Exception:
                            ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass
            self.root = tk.Tk()
            # Forza scaling Tk 1:1 su Windows per matchare uConsole
            if sys.platform == "win32":
                try:
                    self.root.tk.call('tk', 'scaling', 1.0)
                except Exception:
                    pass
            self.root.title(f"TrackMind LapTimer  v{__version__}")
            # Fullscreen solo su Linux/uConsole. Su Windows finestra 1280x720
            # (stesso formato 16:9 di uConsole) centrata, cosi' l'UI e'
            # identica senza bisogno di lottare con risoluzioni multi-monitor.
            if sys.platform == "win32":
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                ww, wh = 1280, 720
                x = max(0, (sw - ww) // 2)
                y = max(0, (sh - wh) // 2)
                self.root.geometry("%dx%d+%d+%d" % (ww, wh, x, y))
            else:
                self.root.attributes("-fullscreen", True)
        self.root.configure(bg=c["sfondo"])
        self.root.focus_force()
        # uConsole: blocca popup di sistema sopra al cronometro.
        # Su finestra "in-process" (self.root gia' esistente passato dall'app)
        # la guardia e' idempotente quindi e' sicuro chiamarla comunque.
        _proteggi_finestra(self.root)

    def _chiudi(self):
        if self._on_close:
            self._pulisci()
            self._on_close()
        else:
            self.root.destroy()

    def _init_fonts(self):
        self._f_timer  = tkfont.Font(family=FONT_MONO, size=72, weight="bold")
        self._f_lap    = tkfont.Font(family=FONT_MONO, size=28, weight="bold")
        self._f_delta  = tkfont.Font(family=FONT_MONO, size=24, weight="bold")
        self._f_info   = tkfont.Font(family=FONT_MONO, size=14)
        self._f_list   = tkfont.Font(family=FONT_MONO, size=13)
        self._f_status = tkfont.Font(family=FONT_MONO, size=12)
        self._f_best   = tkfont.Font(family=FONT_MONO, size=18, weight="bold")
        self._f_big    = tkfont.Font(family=FONT_MONO, size=36, weight="bold")
        self._f_fuel   = tkfont.Font(family=FONT_MONO, size=20, weight="bold")

    def _pulisci(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.root.unbind("<space>")
        self.root.unbind("<Return>")
        self.root.unbind("<Escape>")
        self.root.unbind("<Up>")
        self.root.unbind("<Down>")
        self.root.unbind("<Prior>")
        self.root.unbind("<Next>")
        self.root.unbind("<Home>")
        self.root.unbind("<End>")
        self.root.unbind("<Key>")
        # Canvas distrutti dal winfo_children: rimuovi anche le ref cached
        for attr in ("_grid_canvas", "_grid_header_canvas", "_mini_canvas",
                     "_proiezioni_canvas"):
            if hasattr(self, attr):
                delattr(self, attr)

    # =================================================================
    #  SCHERMATA 1: SCELTA SERBATOIO
    # =================================================================
    def _schermata_carburante(self):
        self._pulisci()
        self.stato = self.FUEL_SELECT
        c = self.colori
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=20, pady=(20, 0))
        tk.Label(header, text="LAPTIMER", bg=c["sfondo"], fg=c["dati"],
                 font=tkfont.Font(family=FONT_MONO, size=16, weight="bold")).pack()
        info_txt = "%s  |  %s" % (self.pilota, self.setup)
        tk.Label(header, text=info_txt, bg=c["sfondo"], fg=c["label"],
                 font=self._f_info).pack(pady=(2, 0))
        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, pady=(15, 0))
        centro = tk.Frame(self.root, bg=c["sfondo"])
        centro.pack(expand=True)
        tk.Label(centro, text="S E R B A T O I O", bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_info).pack(pady=(0, 20))
        self._fuel_btns = []
        self._fuel_sel = 0
        for i, cc in enumerate(SERBATOI):
            btn = tk.Button(centro, text="  %d cc  " % cc,
                            font=self._f_big, width=12,
                            bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                            activebackground=c["dati"], activeforeground=c["sfondo"],
                            relief="ridge", bd=2, cursor="hand2",
                            command=lambda v=cc: self._seleziona_fuel(v))
            btn.pack(pady=10)
            self._fuel_btns.append(btn)
        self._aggiorna_fuel_highlight()
        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, side="bottom")
        status = tk.Frame(self.root, bg=c["sfondo"])
        status.pack(fill="x", side="bottom", padx=20, pady=(6, 8))
        tk.Label(status, text="Frecce = Seleziona  |  SPAZIO = Conferma  |  ESC = Esci",
                 bg=c["sfondo"], fg=c["stato_ok"], font=self._f_status).pack(side="left")
        self.root.bind("<space>", lambda e: self._seleziona_fuel(SERBATOI[self._fuel_sel]))
        self.root.bind("<Return>", lambda e: self._seleziona_fuel(SERBATOI[self._fuel_sel]))
        self.root.bind("<Up>", self._fuel_nav)
        self.root.bind("<Down>", self._fuel_nav)
        self.root.bind("<Escape>", lambda e: self._chiudi())

    def _fuel_nav(self, event):
        if event.keysym == "Up" and self._fuel_sel > 0:
            self._fuel_sel -= 1
        elif event.keysym == "Down" and self._fuel_sel < len(SERBATOI) - 1:
            self._fuel_sel += 1
        self._aggiorna_fuel_highlight()

    def _aggiorna_fuel_highlight(self):
        c = self.colori
        for i, btn in enumerate(self._fuel_btns):
            if i == self._fuel_sel:
                btn.config(bg=c["dati"], fg=c["sfondo"])
            else:
                btn.config(bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"])

    def _seleziona_fuel(self, cc):
        self.serbatoio = cc
        self._schermata_timer()

    # -----------------------------------------------------------------
    #  DIMENSIONI ADATTIVE MINI-GRAFICI (uConsole / Desktop)
    # -----------------------------------------------------------------
    def _calcola_dim_grafici(self):
        """Calcola larghezza/altezza dei mini-grafici SX (tempi) e DX
        (proiezioni) in base alla larghezza REALE della FINESTRA (non dello
        schermo), cosi' funziona identico sia in fullscreen su uConsole sia
        in finestra 1280x720 su Windows.

        Lascia almeno ~440px liberi al centro per il timer.
        """
        try:
            self.root.update_idletasks()
            # Priorita': larghezza finestra effettiva (geometry). Se ancora
            # non disponibile in fase di init, fallback a screenwidth.
            ww = self.root.winfo_width()
            wh = self.root.winfo_height()
            if ww < 200:
                # Finestra non ancora renderizzata: prova geometry configurata
                geo = self.root.geometry().split("+")[0]
                if "x" in geo:
                    gw, gh = geo.split("x")
                    try:
                        ww = int(gw); wh = int(gh)
                    except Exception:
                        pass
            if ww < 200:
                ww = self.root.winfo_screenwidth()
                wh = self.root.winfo_screenheight()
        except Exception:
            ww, wh = 1280, 720
        # Spazio minimo riservato al timer centrale + margini laterali
        centro_min = 440
        margini = 40  # padx totali esterni
        # Larghezza disponibile per ciascun grafico
        w_disp = (ww - centro_min - margini) // 2
        # Clamp: min 280, max 540
        w = max(280, min(540, w_disp))
        # Altezza proporzionale: 15-18% altezza finestra, tra 90 e 140
        h = max(90, min(140, int(wh * 0.18)))
        return w, h

    # =================================================================
    #  SCHERMATA 2: TIMER
    # =================================================================
    def _schermata_timer(self):
        self._pulisci()
        self.stato = self.ATTESA
        self.giri = []
        self.miglior_tempo = None
        self._salva_path = None
        self._space_locked = False
        self._lock_tempo = 1.0
        c = self.colori
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=20, pady=(10, 0))
        tk.Label(header, text="LAPTIMER", bg=c["sfondo"], fg=c["dati"],
                 font=tkfont.Font(family=FONT_MONO, size=16, weight="bold")).pack()
        info_txt = "%s  |  %s  |  %dcc" % (self.pilota, self.setup, self.serbatoio)
        tk.Label(header, text=info_txt, bg=c["sfondo"], fg=c["label"],
                 font=self._f_info).pack(pady=(2, 0))
        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, pady=(8, 0))

        # ── Area superiore: 3 colonne con grid ──
        # SX=grafico, CENTRO=timer, DX=proiezioni
        top_area = tk.Frame(self.root, bg=c["sfondo"])
        top_area.pack(fill="x", padx=10, pady=(5, 0))
        top_area.columnconfigure(0, weight=1)  # grafico SX
        top_area.columnconfigure(1, weight=1)  # timer centro
        top_area.columnconfigure(2, weight=1)  # proiezioni DX

        # Mini grafico andamento tempi (SX) — dimensioni adattive (uConsole-friendly)
        self._grafico_w, self._grafico_h = self._calcola_dim_grafici()
        self._mini_canvas = tk.Canvas(top_area, bg=c.get("sfondo_celle", "#080808"),
                                       width=self._grafico_w, height=self._grafico_h,
                                       highlightthickness=1,
                                       highlightbackground=c.get("linee", "#1a3a1a"))
        self._mini_canvas.grid(row=0, column=0, sticky="w", padx=(10, 5))

        # Timer + info (CENTRO)
        timer_col = tk.Frame(top_area, bg=c["sfondo"])
        timer_col.grid(row=0, column=1, sticky="nsew")
        self._lbl_timer = tk.Label(timer_col, text="00:00.00",
                                    bg=c["sfondo"], fg=c["dati"], font=self._f_timer)
        self._lbl_timer.pack()
        self._lbl_ultimo = tk.Label(timer_col, text="", bg=c["sfondo"],
                                     fg=c["label"], font=self._f_lap)
        self._lbl_ultimo.pack()
        self._lbl_delta = tk.Label(timer_col, text="", bg=c["sfondo"],
                                    fg=c["testo_dim"], font=self._f_delta)
        self._lbl_delta.pack()
        info_row = tk.Frame(timer_col, bg=c["sfondo"])
        info_row.pack(pady=(2, 0))
        self._lbl_best = tk.Label(info_row, text="", bg=c["sfondo"],
                                   fg=c["stato_avviso"], font=self._f_best)
        self._lbl_best.pack(side="left", padx=(0, 15))
        self._lbl_fuel = tk.Label(info_row, text="%dcc" % self.serbatoio,
                                   bg=c["sfondo"], fg=c["testo_dim"], font=self._f_best)
        self._lbl_fuel.pack(side="left")
        # Tempo totale trascorso dalla partenza
        self._lbl_totale = tk.Label(timer_col, text="00:00:00.0",
                                     bg=c["sfondo"], fg=c["testo_dim"], font=self._f_info)
        self._lbl_totale.pack(pady=(2, 0))

        # Pannello PROIEZIONI GIRI (DX) — stesse dimensioni del grafico SX
        self._proiezioni_w = self._grafico_w
        self._proiezioni_h = self._grafico_h
        self._proiezioni_canvas = tk.Canvas(top_area, bg=c.get("sfondo_celle", "#080808"),
                                             width=self._proiezioni_w, height=self._proiezioni_h,
                                             highlightthickness=1,
                                             highlightbackground=c.get("linee", "#1a3a1a"))
        self._proiezioni_canvas.grid(row=0, column=2, sticky="e", padx=(5, 10))
        self._aggiorna_proiezioni()

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, pady=(5, 0))

        # ── Lista giri con griglia (sotto, tutta larghezza) ──
        self._grid_row_h = 22  # altezza riga griglia (definita PRIMA dei canvas)
        self._grid_rows_drawn = 0

        # Canvas HEADER fisso: intestazioni GIRO/TEMPO/DELTA/TOTALE sempre visibili
        self._grid_header_canvas = tk.Canvas(self.root, bg=c["sfondo"],
                                              height=self._grid_row_h,
                                              highlightthickness=0, bd=0)
        self._grid_header_canvas.pack(fill="x", padx=20, pady=(2, 0))

        # Canvas BODY scrollabile: solo linee griglia + dati giri (niente intestazioni)
        self._grid_canvas = tk.Canvas(self.root, bg=c["sfondo"],
                                       highlightthickness=0, bd=0)
        self._grid_canvas.pack(fill="both", expand=True, padx=20, pady=(0, 2))
        # Scroll con mousewheel
        def _on_mousewheel(event):
            self._grid_canvas.yview_scroll(-1 if event.delta > 0 or event.num == 4
                                           else 1, "units")
        self._grid_canvas.bind("<MouseWheel>", _on_mousewheel)      # Windows/Mac
        self._grid_canvas.bind("<Button-4>", _on_mousewheel)         # Linux scroll up
        self._grid_canvas.bind("<Button-5>", _on_mousewheel)         # Linux scroll down
        # Ridisegna griglia al ridimensionamento (stato-aware)
        def _on_grid_resize(event):
            if self.stato == self.FERMO:
                self._ridisegna_griglia_analisi()
                if hasattr(self, '_giro_selezionato') and self._giro_selezionato >= 0:
                    self._evidenzia_selezione()
            else:
                self._ridisegna_griglia_completa()
        self._grid_canvas.bind("<Configure>", _on_grid_resize)
        # Ridisegna intestazioni header al resize
        self._grid_header_canvas.bind("<Configure>",
                                       lambda e: self._disegna_header_intestazione())
        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, side="bottom")
        status_bar = tk.Frame(self.root, bg=c["sfondo"])
        status_bar.pack(fill="x", side="bottom", padx=20, pady=(6, 8))
        self._lbl_status = tk.Label(status_bar, text="SPAZIO = Avvia  |  \u2191\u2193 Scorri  |  ESC = Indietro",
                                     bg=c["sfondo"], fg=c["stato_ok"], font=self._f_status,
                                     anchor="w")
        self._lbl_status.pack(side="left")
        self._lbl_giri_count = tk.Label(status_bar, text="", bg=c["sfondo"],
                                         fg=c["testo_dim"], font=self._f_status, anchor="e")
        self._lbl_giri_count.pack(side="right")
        self.root.bind("<space>", self._on_spazio)
        self.root.bind("<Return>", self._on_spazio)
        self.root.bind("<Escape>", self._on_stop)
        # Scroll lista giri con tastiera durante live (uConsole senza mouse)
        self.root.bind("<Up>", lambda e: self._scroll_live(-1))
        self.root.bind("<Down>", lambda e: self._scroll_live(1))
        self.root.bind("<Prior>", lambda e: self._scroll_live_page(-1))  # PgUp
        self.root.bind("<Next>", lambda e: self._scroll_live_page(1))    # PgDn
        self.root.bind("<Home>", lambda e: self._grid_canvas.yview_moveto(0))
        self.root.bind("<End>", lambda e: self._grid_canvas.yview_moveto(1.0))

    # -----------------------------------------------------------------
    #  LOGICA TIMER
    # -----------------------------------------------------------------
    def _on_spazio(self, event=None):
        if self.stato == self.ATTESA:
            self._avvia()
        elif self.stato == self.RUNNING:
            if self._space_locked:
                return
            self._segna_giro()
        elif self.stato == self.FERMO:
            pass  # In analisi integrata, SPAZIO non fa nulla

    def _avvia(self):
        self.stato = self.RUNNING
        self.t_start = time.perf_counter()
        self.t_ultimo_giro = self.t_start
        c = self.colori
        self._lbl_status.config(text="SPAZIO = Giro  |  ESC = Stop e Salva", fg=c["stato_ok"])
        self._aggiorna_timer()

    def _aggiorna_timer(self):
        if self.stato != self.RUNNING:
            return
        try:
            elapsed = time.perf_counter() - self.t_ultimo_giro
            self._lbl_timer.config(text=_fmt(elapsed))
            totale = time.perf_counter() - self.t_start
            self._aggiorna_fuel_live(totale)
            self.root.after(33, self._aggiorna_timer)
        except tk.TclError:
            pass  # Widget distrutto, ferma loop

    def _aggiorna_fuel_live(self, totale_sec):
        c = self.colori
        minuti = totale_sec / 60.0
        if minuti < 3.5:
            fg = c["stato_ok"]
        elif minuti < 5.0:
            fg = c["stato_avviso"]
        else:
            fg = c["stato_errore"]
        min_int = int(minuti)
        sec_rest = int((minuti - min_int) * 60)
        self._lbl_fuel.config(text="%dcc | %d:%02d" % (self.serbatoio, min_int, sec_rest), fg=fg)
        # Aggiorna tempo totale trascorso hh:mm:ss.d
        if hasattr(self, '_lbl_totale'):
            ore = int(totale_sec) // 3600
            resto = totale_sec - ore * 3600
            min_t = int(resto) // 60
            sec_t = resto - min_t * 60
            self._lbl_totale.config(
                text="%02d:%02d:%04.1f" % (ore, min_t, sec_t))

    def _segna_giro(self):
        ora = time.perf_counter()
        tempo_giro = ora - self.t_ultimo_giro
        tempo_cum = ora - self.t_start
        self.t_ultimo_giro = ora
        num = len(self.giri) + 1
        delta = None
        nuovo_best = False
        if self.miglior_tempo is not None:
            delta = tempo_giro - self.miglior_tempo
            if tempo_giro < self.miglior_tempo:
                self.miglior_tempo = tempo_giro
                nuovo_best = True
        else:
            self.miglior_tempo = tempo_giro
            nuovo_best = True
        giro = {"giro": num, "tempo": round(tempo_giro, 3), "cumulativo": round(tempo_cum, 3),
                "stato": "valido"}
        if delta is not None:
            giro["delta"] = round(delta, 3)
        self.giri.append(giro)
        c = self.colori
        self._lbl_ultimo.config(text="GIRO %d:  %s" % (num, _fmt(tempo_giro)))
        if delta is not None:
            fg = c["stato_ok"] if delta <= 0 else c["stato_errore"]
            self._lbl_delta.config(text=_fmt_delta(delta), fg=fg)
        else:
            self._lbl_delta.config(text="")
        if nuovo_best and num > 1:
            self._lbl_best.config(text="* BEST: %s *" % _fmt(self.miglior_tempo))
        elif nuovo_best:
            self._lbl_best.config(text="BEST: %s" % _fmt(self.miglior_tempo))
        self._aggiungi_riga(giro, nuovo_best)
        self._lbl_giri_count.config(text="Giri: %d" % num)
        self._lock_tempo = max(1.0, self.miglior_tempo * 0.4)
        self._space_locked = True
        self.root.after(int(self._lock_tempo * 1000), self._sblocca_space)

    def _sblocca_space(self):
        self._space_locked = False

    def _ridisegna_griglia_completa(self):
        """Ridisegna griglia + tutti i dati giri (usato al resize)."""
        if not hasattr(self, '_grid_canvas'):
            return
        canvas = self._grid_canvas
        canvas.delete("all")
        self._disegna_header_intestazione()
        self._disegna_griglia_sfondo()
        c = self.colori
        w = canvas.winfo_width()
        rh = self._grid_row_h
        font_data = (FONT_MONO, 11)
        tempi_validi = [g["tempo"] for g in self.giri if g.get("stato") == "valido"]
        best = min(tempi_validi) if tempi_validi else None
        for g in self.giri:
            row_idx = g["giro"]
            y = row_idx * rh + rh // 2
            is_best = best and abs(g["tempo"] - best) < 0.001 and g["giro"] > 1
            fg = c["stato_avviso"] if is_best else c["dati"]
            canvas.create_text(w * 0.04, y, text="%d" % g["giro"], fill=fg,
                               font=font_data, anchor="center", tags="data")
            canvas.create_text(w * 0.18, y, text=_fmt(g["tempo"]), fill=fg,
                               font=font_data, anchor="center", tags="data")
            if "delta" in g:
                dtxt = _fmt_delta(g["delta"])
                dfg = c["stato_ok"] if g["delta"] <= 0 else c["stato_errore"]
                canvas.create_text(w * 0.365, y, text=dtxt, fill=dfg,
                                   font=font_data, anchor="center", tags="data")
            canvas.create_text(w * 0.55, y, text=_fmt(g["cumulativo"]), fill=c["testo_dim"],
                               font=font_data, anchor="center", tags="data")
        if self.giri:
            last_y = len(self.giri) * rh + 2 * rh
            canvas.configure(scrollregion=(0, 0, w, last_y))

    def _disegna_griglia_sfondo(self):
        """Disegna griglia orizzontale/verticale sul canvas body.
        NON disegna intestazioni (sono sul canvas header fisso separato).
        Si estende dinamicamente in base al numero di giri correnti."""
        if not hasattr(self, '_grid_canvas'):
            return
        canvas = self._grid_canvas
        c = self.colori
        canvas.delete("grid")  # rimuovi vecchia griglia
        canvas.update_idletasks()
        w = canvas.winfo_width()
        h_visibile = canvas.winfo_height()
        if w < 10 or h_visibile < 10:
            return
        rh = self._grid_row_h
        # Altezza totale: copri sempre tutti i giri correnti + buffer,
        # e almeno l'area visibile (cosi la griglia non finisce a meta schermo)
        n_righe = max(len(self.giri) + 3, h_visibile // rh + 1)
        h_totale = n_righe * rh
        col_griglia = c.get("linee", "#1a3a1a")
        # Linee orizzontali (coprono tutta la scrollregion)
        for y in range(0, h_totale, rh):
            canvas.create_line(0, y, w, y, fill=col_griglia, tags="grid")
        # Colonne verticali
        cols_x = [w * 0.08, w * 0.28, w * 0.45, w * 0.65]
        for x in cols_x:
            canvas.create_line(int(x), 0, int(x), h_totale, tags="grid", fill=col_griglia)
        # Tag per bassa priorita (sotto i dati)
        canvas.tag_lower("grid")
        # Imposta scrollregion per l'intera area
        canvas.configure(scrollregion=(0, 0, w, h_totale))

    def _disegna_header_intestazione(self):
        """Disegna le intestazioni GIRO/TEMPO/DELTA/TOTALE nel canvas header FISSO
        (non scrolla mai con il body). Richiamato al primo setup e al resize."""
        if not hasattr(self, '_grid_header_canvas'):
            return
        hc = self._grid_header_canvas
        c = self.colori
        hc.delete("all")
        hc.update_idletasks()
        w = hc.winfo_width()
        if w < 10:
            return
        rh = self._grid_row_h
        col_griglia = c.get("linee", "#1a3a1a")
        # Colonne verticali coerenti col body
        cols_x = [w * 0.08, w * 0.28, w * 0.45, w * 0.65]
        for x in cols_x:
            hc.create_line(int(x), 0, int(x), rh, fill=col_griglia)
        # Separatore inferiore (bordo verso il body)
        hc.create_line(0, rh - 1, w, rh - 1, fill=col_griglia)
        # Etichette stile colonna
        y0 = rh // 2
        font_hdr = (FONT_MONO, 10, "bold")
        # In analisi mostriamo STATO al posto di TOTALE
        ultimo = "STATO" if self.stato == self.FERMO else "TOTALE"
        hc.create_text(w * 0.04, y0, text="GIRO", fill=c["testo_dim"],
                       font=font_hdr, anchor="center")
        hc.create_text(w * 0.18, y0, text="TEMPO", fill=c["testo_dim"],
                       font=font_hdr, anchor="center")
        hc.create_text(w * 0.365, y0, text="DELTA", fill=c["testo_dim"],
                       font=font_hdr, anchor="center")
        hc.create_text(w * 0.55, y0, text=ultimo, fill=c["testo_dim"],
                       font=font_hdr, anchor="center")

    def _scroll_live(self, direzione):
        """Scroll a una riga con freccia Up/Down durante stato RUNNING/ATTESA.
        In stato FERMO il bind Up/Down viene sovrascritto da _naviga_griglia."""
        if hasattr(self, '_grid_canvas') and self.stato != self.FERMO:
            self._grid_canvas.yview_scroll(direzione, "units")

    def _scroll_live_page(self, direzione):
        """Scroll a pagina con PgUp/PgDn durante live."""
        if hasattr(self, '_grid_canvas') and self.stato != self.FERMO:
            self._grid_canvas.yview_scroll(direzione, "pages")

    def _aggiungi_riga(self, giro, is_best):
        c = self.colori
        canvas = self._grid_canvas
        canvas.update_idletasks()
        w = canvas.winfo_width()
        rh = self._grid_row_h
        # Dati partono dalla riga 1 (riga 0 lasciata come spaziatore sotto l'header
        # che e' ora in un canvas separato fisso)
        row_idx = giro["giro"]
        y = row_idx * rh + rh // 2

        fg = c["stato_avviso"] if is_best and giro["giro"] > 1 else c["dati"]
        font_data = (FONT_MONO, 11)

        # Prima volta: disegna header fisso
        if self._grid_rows_drawn == 0:
            self._disegna_header_intestazione()
        # Ridisegna SEMPRE la griglia di sfondo per mantenerla coerente con lo
        # scrollregion corrente (se no le linee orizzontali finiscono a meta'
        # schermo quando la lista scrolla). E' leggero: solo qualche linea.
        self._disegna_griglia_sfondo()
        self._grid_rows_drawn = max(self._grid_rows_drawn, row_idx + 3)

        # GIRO
        canvas.create_text(w * 0.04, y, text="%d" % giro["giro"], fill=fg,
                           font=font_data, anchor="center", tags="data")
        # TEMPO
        canvas.create_text(w * 0.18, y, text=_fmt(giro["tempo"]), fill=fg,
                           font=font_data, anchor="center", tags="data")
        # DELTA
        dtxt, dfg = "", c["testo_dim"]
        if "delta" in giro:
            dtxt = _fmt_delta(giro["delta"])
            dfg = c["stato_ok"] if giro["delta"] <= 0 else c["stato_errore"]
        if dtxt:
            canvas.create_text(w * 0.365, y, text=dtxt, fill=dfg,
                               font=font_data, anchor="center", tags="data")
        # TOTALE
        canvas.create_text(w * 0.55, y, text=_fmt(giro["cumulativo"]), fill=c["testo_dim"],
                           font=font_data, anchor="center", tags="data")

        # Aggiorna sempre scrollregion all'ultima riga disegnata
        new_sr_h = max((row_idx + 3) * rh, canvas.winfo_height())
        canvas.configure(scrollregion=(0, 0, w, new_sr_h))

        # Scroll automatico solo se l'ultima riga non e' gia' visibile
        first_visible = canvas.canvasy(0)
        visible_h = canvas.winfo_height()
        if y > first_visible + visible_h - rh:
            canvas.yview_moveto(1.0)

        self._aggiorna_mini_grafico()
        self._aggiorna_proiezioni()

    def _aggiorna_proiezioni(self):
        """Disegna pannello proiezioni giri stimati per 5/20/30/45 min."""
        if not hasattr(self, '_proiezioni_canvas'):
            return
        c = self.colori
        canvas = self._proiezioni_canvas
        canvas.delete("all")
        w = canvas.winfo_width() or self._proiezioni_w
        h = canvas.winfo_height() or self._proiezioni_h

        tempi_validi = [g["tempo"] for g in self.giri
                        if g.get("stato") == "valido"
                        and not g.get("segnalato", False)]
        if not tempi_validi:
            canvas.create_text(w // 2, h // 2,
                               text="PROIEZIONI GIRI", fill=c["testo_dim"],
                               font=(FONT_MONO, 12), anchor="center")
            canvas.create_text(w // 2, h // 2 + 18,
                               text="in attesa di giri...", fill=c["testo_dim"],
                               font=(FONT_MONO, 9), anchor="center")
            return

        # Media ponderata (pesi crescenti: ultimi giri pesano di piu)
        n = len(tempi_validi)
        if n == 1:
            media = tempi_validi[0]
        else:
            pesi = [i + 1 for i in range(n)]
            media = sum(t * p for t, p in zip(tempi_validi, pesi)) / sum(pesi)

        # Intestazione
        canvas.create_text(w // 2, 12,
                           text="PROIEZIONI GIRI", fill=c["dati"],
                           font=(FONT_MONO, 11, "bold"), anchor="center")
        canvas.create_text(w // 2, 28,
                           text="media: %s" % _fmt(media), fill=c["testo_dim"],
                           font=(FONT_MONO, 8), anchor="center")
        canvas.create_line(10, 36, w - 10, 36, fill=c["linee"], dash=(2, 2))

        # Proiezioni per 5, 20, 30, 45 minuti
        # Il pilota che passa il traguardo appena prima dello scadere
        # completa un giro in piu, percio +1 al conteggio base
        durate = [5, 20, 30, 45]
        y_start = 46
        row_h = (h - y_start - 6) // len(durate)

        for i, minuti in enumerate(durate):
            secondi_tot = minuti * 60.0
            giri_stimati = int(secondi_tot / media) + 1 if media > 0 else 0
            y = y_start + i * row_h + row_h // 2

            # Label minuti (SX)
            canvas.create_text(60, y,
                               text="%d min" % minuti, fill=c["label"],
                               font=(FONT_MONO, 12), anchor="e")
            # Freccia
            canvas.create_text(80, y,
                               text="\u25b6", fill=c["testo_dim"],
                               font=(FONT_MONO, 9), anchor="center")
            # Numero giri stimati (centro-DX, grande e verde)
            canvas.create_text(w // 2 + 40, y,
                               text="%d giri" % giri_stimati, fill=c["dati"],
                               font=(FONT_MONO, 14, "bold"), anchor="center")
            # Tempo totale stimato (DX)
            tempo_tot = giri_stimati * media
            min_t = int(tempo_tot) // 60
            sec_t = tempo_tot - min_t * 60
            canvas.create_text(w - 15, y,
                               text="~%d:%05.2f" % (min_t, sec_t), fill=c["testo_dim"],
                               font=(FONT_MONO, 9), anchor="e")

    def _aggiorna_mini_grafico(self):
        """Disegna mini grafico andamento tempi vs media ponderata."""
        if not hasattr(self, '_mini_canvas'):
            return
        c = self.colori
        canvas = self._mini_canvas
        canvas.delete("all")
        tempi_validi = [g["tempo"] for g in self.giri if g.get("stato") == "valido"]
        if len(tempi_validi) < 2:
            canvas.create_text(self._grafico_w // 2, self._grafico_h // 2,
                               text="min 2 giri", fill=c["testo_dim"],
                               font=self._f_status, anchor="center")
            return

        w = canvas.winfo_width() or self._grafico_w
        h = canvas.winfo_height() or self._grafico_h
        pad_top, pad_bot, pad_sx, pad_dx = 15, 20, 8, 8
        gw = w - pad_sx - pad_dx
        gh = h - pad_top - pad_bot

        # Calcola media ponderata progressiva
        n = len(tempi_validi)
        t_min = min(tempi_validi)
        t_max = max(tempi_validi)
        margine = (t_max - t_min) * 0.15 if t_max > t_min else 1.0
        y_min = t_min - margine
        y_max = t_max + margine

        def _y(t):
            if y_max == y_min:
                return pad_top + gh // 2
            return pad_top + gh - int((t - y_min) / (y_max - y_min) * gh)

        def _x(i):
            if n <= 1:
                return pad_sx + gw // 2
            return pad_sx + int(i * gw / (n - 1))

        # Linea media
        media = sum(tempi_validi) / n
        my = _y(media)
        canvas.create_line(pad_sx, my, w - pad_dx, my,
                           fill=c["testo_dim"], dash=(3, 3))
        canvas.create_text(w - pad_dx, my - 8,
                           text=_fmt(media), fill=c["testo_dim"],
                           font=(FONT_MONO, 7), anchor="e")

        # Punti e linee tempo
        punti = []
        gi = 0
        for g in self.giri:
            if g.get("stato") != "valido":
                continue
            x = _x(gi)
            y = _y(g["tempo"])
            punti.append((x, y, g["tempo"]))
            gi += 1

        # Linee tra punti
        for i in range(1, len(punti)):
            x0, y0, _ = punti[i - 1]
            x1, y1, t = punti[i]
            # Colore: verde sotto media, rosso sopra
            col = c["stato_ok"] if t <= media else c["stato_errore"]
            canvas.create_line(x0, y0, x1, y1, fill=col, width=2)

        # Punti
        best = min(tempi_validi)
        for x, y, t in punti:
            if abs(t - best) < 0.001:
                col = c["stato_avviso"]  # best = giallo
                r = 4
            elif t <= media:
                col = c["stato_ok"]
                r = 3
            else:
                col = c["stato_errore"]
                r = 3
            canvas.create_oval(x - r, y - r, x + r, y + r, fill=col, outline="")

        # Label best e worst
        canvas.create_text(pad_sx, pad_top - 4,
                           text=_fmt(t_min), fill=c["stato_avviso"],
                           font=(FONT_MONO, 7), anchor="w")
        canvas.create_text(pad_sx, h - pad_bot + 12,
                           text=_fmt(t_max), fill=c["stato_errore"],
                           font=(FONT_MONO, 7), anchor="w")

    # -----------------------------------------------------------------
    #  STOP E RISULTATI
    # -----------------------------------------------------------------
    def _on_stop(self, event=None):
        if self.stato == self.RUNNING:
            # ESC = segna ultimo giro in corso + ferma + entra in modalita analisi
            self._segna_giro()
            self.stato = self.FERMO
            self._totale = time.perf_counter() - self.t_start
            self._entra_modalita_analisi()
        elif self.stato == self.FERMO:
            # Secondo ESC: salva e esci
            self._salva_risultati()
            self._chiudi()
        elif self.stato == self.ATTESA:
            self._schermata_carburante()

    def _entra_modalita_analisi(self):
        """Trasforma la schermata timer in analisi integrata.
        La griglia diventa selezionabile, appaiono bottoni per modifica stato."""
        c = self.colori
        self._giro_selezionato = 0  # indice 0-based nel array giri

        # Auto-classifica giri (pit stop, incidenti)
        if not hasattr(self, '_sessione_carburante'):
            self._sessione_carburante = True
        auto = self._auto_classifica_giri()

        # Timer mostra tempo finale
        self._lbl_timer.config(text=_fmt(self._totale), fg=c["stato_avviso"])
        # Ferma aggiornamento totale
        if hasattr(self, '_lbl_totale'):
            tot_min = int(self._totale) // 60
            tot_sec = self._totale - tot_min * 60
            self._lbl_totale.config(text="%02d:%02d:%04.1f" % (
                tot_min // 60, tot_min % 60, tot_sec))

        # Ridisegna griglia con colori stato + riga riepilogo
        self._ridisegna_griglia_analisi()

        # Salva automaticamente
        path = self._salva_risultati()

        # Trasforma status bar in barra comandi analisi
        self._lbl_status.config(text="", fg=c["dati"])
        # Rimuovi vecchio status bar content e ricrea
        status_parent = self._lbl_status.master
        self._lbl_status.destroy()
        for w in status_parent.winfo_children():
            w.destroy()

        # Bottoni analisi (con navigazione TAB)
        btn_frame = tk.Frame(status_parent, bg=c["sfondo"])
        btn_frame.pack(side="left", fill="x", expand=True)
        self._analisi_btns = []  # lista bottoni per navigazione TAB

        btns = [
            ("ESCLUDI E", 9, c["pulsanti_testo"], lambda: self._toggle_stato_grid("escluso")),
            ("PIT P", 6, c["stato_avviso"], lambda: self._toggle_stato_grid("pit")),
            ("INCID. C", 8, c["stato_errore"], lambda: self._toggle_stato_grid("incidente")),
            ("SPENTA M", 8, "#ff66ff", lambda: self._toggle_stato_grid("spenta")),
            ("VALIDO V", 9, c["stato_ok"], lambda: self._toggle_stato_grid("valido")),
        ]
        for txt, bw, fg, cmd in btns:
            b = tk.Button(btn_frame, text=txt, font=self._f_status, width=bw,
                      bg=c["pulsanti_sfondo"], fg=fg,
                      relief="ridge", bd=1, cursor="hand2",
                      command=cmd)
            b.pack(side="left", padx=2)
            self._analisi_btns.append(b)

        tk.Label(btn_frame, text=" ", bg=c["sfondo"]).pack(side="left")

        fuel_fg = c["stato_ok"] if self._sessione_carburante else c["stato_errore"]
        fuel_txt = "FUEL:SI F" if self._sessione_carburante else "FUEL:NO F"
        self._btn_fuel_toggle = tk.Button(btn_frame, text=fuel_txt, font=self._f_status,
                  width=9, bg=c["pulsanti_sfondo"], fg=fuel_fg,
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._toggle_carburante_grid)
        self._btn_fuel_toggle.pack(side="left", padx=2)
        self._analisi_btns.append(self._btn_fuel_toggle)

        if _HAS_PRINT:
            b = tk.Button(btn_frame, text="STAMPA S", font=self._f_status, width=9,
                      bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=self._stampa_termica)
            b.pack(side="left", padx=2)
            self._analisi_btns.append(b)

        if _HAS_AI:
            b = tk.Button(btn_frame, text="IA  I", font=self._f_status, width=6,
                      bg=c["pulsanti_sfondo"], fg="#ff66ff",
                      relief="ridge", bd=1, cursor="hand2",
                      command=self._lancia_analisi_ia)
            b.pack(side="left", padx=2)
            self._analisi_btns.append(b)

        b = tk.Button(btn_frame, text="ESCI ESC", font=self._f_status, width=8,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: (self._salva_risultati(), self._chiudi()))
        b.pack(side="left", padx=2)
        self._analisi_btns.append(b)

        # Label status/info (a destra)
        self._lbl_res_status = tk.Label(status_parent, text="", bg=c["sfondo"],
                                         fg=c["testo_dim"], font=self._f_status, anchor="e")
        self._lbl_res_status.pack(side="right", padx=(5, 0))

        # Navigazione TAB tra bottoni
        self._btn_focus_idx = -1  # -1 = focus sulla griglia
        def _tab_bottoni(event=None):
            self._btn_focus_idx += 1
            if self._btn_focus_idx >= len(self._analisi_btns):
                self._btn_focus_idx = -1  # torna alla griglia
            if self._btn_focus_idx >= 0:
                self._analisi_btns[self._btn_focus_idx].focus_set()
            else:
                self._grid_canvas.focus_set()
            return "break"
        def _shift_tab_bottoni(event=None):
            self._btn_focus_idx -= 1
            if self._btn_focus_idx < -1:
                self._btn_focus_idx = len(self._analisi_btns) - 1
            if self._btn_focus_idx >= 0:
                self._analisi_btns[self._btn_focus_idx].focus_set()
            else:
                self._grid_canvas.focus_set()
            return "break"
        self.root.bind("<Tab>", _tab_bottoni)
        self.root.bind("<Shift-Tab>", _shift_tab_bottoni)

        # Mostra info
        hint = "Frecce=Seleziona  TAB=Bottoni  E/P/C/M/V=Stato"
        if auto:
            hint = "Auto: %d classificati | %s" % (auto, hint)
        if path:
            self._lbl_res_status.config(text="Salvato", fg=c["stato_ok"])
        else:
            self._lbl_res_status.config(text=hint, fg=c["testo_dim"])

        # Rebind tastiera per modalita analisi
        self.root.unbind("<space>")
        self.root.unbind("<Return>")

        # Helper: tutti gli handler scorciatoia ritornano "break" per fermare
        # la propagazione (impedisce al widget con focus di consumare l'evento).
        def _h(fn):
            def _wrap(e=None):
                fn()
                return "break"
            return _wrap

        scorciatoie = [
            ("<Up>",    lambda: self._naviga_griglia(-1)),
            ("<Down>",  lambda: self._naviga_griglia(1)),
            ("<Prior>", lambda: self._naviga_griglia(-10)),   # PgUp
            ("<Next>",  lambda: self._naviga_griglia(10)),    # PgDn
            ("<Home>",  lambda: self._naviga_vai(0)),
            ("<End>",   lambda: self._naviga_vai(len(self.giri) - 1)),
            ("<e>",     lambda: self._toggle_stato_grid("escluso")),
            ("<E>",     lambda: self._toggle_stato_grid("escluso")),
            ("<p>",     lambda: self._toggle_stato_grid("pit")),
            ("<P>",     lambda: self._toggle_stato_grid("pit")),
            ("<c>",     lambda: self._toggle_stato_grid("incidente")),
            ("<C>",     lambda: self._toggle_stato_grid("incidente")),
            ("<m>",     lambda: self._toggle_stato_grid("spenta")),
            ("<M>",     lambda: self._toggle_stato_grid("spenta")),
            ("<v>",     lambda: self._toggle_stato_grid("valido")),
            ("<V>",     lambda: self._toggle_stato_grid("valido")),
            ("<f>",     lambda: self._toggle_carburante_grid()),
            ("<F>",     lambda: self._toggle_carburante_grid()),
            ("<s>",     lambda: self._stampa_termica()),
            ("<S>",     lambda: self._stampa_termica()),
            ("<i>",     lambda: self._lancia_analisi_ia()),
            ("<I>",     lambda: self._lancia_analisi_ia()),
            ("<Escape>",lambda: (self._salva_risultati(), self._chiudi())),
        ]

        # 1) Bind sul root (quando il focus e' sul canvas o nessun widget)
        for key, fn in scorciatoie:
            self.root.bind(key, _h(fn))

        # 2) Bind sugli stessi tasti ANCHE su ogni bottone analisi: cosi se
        #    l'utente ha premuto TAB e il focus e' su un bottone, le frecce
        #    e le scorciatoie continuano a pilotare la griglia (e il "break"
        #    impedisce al bottone di consumare l'evento).
        for btn in self._analisi_btns:
            for key, fn in scorciatoie:
                btn.bind(key, _h(fn))

        # Click sulla griglia per selezionare (se mouse disponibile)
        self._grid_canvas.bind("<Button-1>", self._click_griglia)
        self._grid_canvas.focus_set()

        # Riporta scroll in cima e seleziona giro 1 (barra cursore in riga 1)
        if self.giri:
            self._giro_selezionato = 0
            self._grid_canvas.yview_moveto(0)
            self._evidenzia_selezione()

    def _ridisegna_griglia_analisi(self):
        """Ridisegna la griglia con colori per stato giro + riga riepilogo."""
        if not hasattr(self, '_grid_canvas'):
            return
        canvas = self._grid_canvas
        c = self.colori
        canvas.delete("all")
        self._disegna_header_intestazione()
        self._disegna_griglia_sfondo()
        w = canvas.winfo_width()
        rh = self._grid_row_h
        font_data = (FONT_MONO, 11)
        font_bold = (FONT_MONO, 12, "bold")

        tempi_validi = [g["tempo"] for g in self.giri if g.get("stato") == "valido"]
        best = min(tempi_validi) if tempi_validi else None

        # Disegna righe dati con colori per stato
        for g in self.giri:
            row_idx = g["giro"]
            y = row_idx * rh + rh // 2
            stato = g.get("stato", "valido")
            segnalato = g.get("segnalato", False)
            corretto = g.get("corretto", False)

            # Colore in base allo stato
            if stato == "pit":
                fg = c["stato_avviso"]
            elif stato == "incidente":
                fg = c["stato_errore"]
            elif stato == "spenta":
                fg = "#ff66ff"
            elif stato == "escluso":
                fg = c["testo_dim"]
            elif corretto:
                fg = c.get("cerca_testo", "#00ccff")
            elif segnalato:
                fg = c["stato_errore"]
            elif best and abs(g["tempo"] - best) < 0.001 and g["giro"] > 1:
                fg = c["stato_avviso"]
            else:
                fg = c["dati"]

            # Stato display
            if stato == "pit":
                stato_txt = "PIT"
            elif stato == "incidente":
                stato_txt = "INC"
            elif stato == "spenta":
                stato_txt = "SPENTA"
            elif stato == "escluso":
                stato_txt = "ESC"
            elif corretto:
                stato_txt = "CORR"
            elif segnalato:
                stato_txt = "INC?"
            elif best and abs(g["tempo"] - best) < 0.001 and g["giro"] > 1:
                stato_txt = "BEST"
            else:
                stato_txt = ""

            canvas.create_text(w * 0.04, y, text="%d" % g["giro"], fill=fg,
                               font=font_data, anchor="center", tags="data")
            canvas.create_text(w * 0.18, y, text=_fmt(g["tempo"]), fill=fg,
                               font=font_data, anchor="center", tags="data")
            if "delta" in g:
                dtxt = _fmt_delta(g["delta"])
                dfg = c["stato_ok"] if g["delta"] <= 0 else c["stato_errore"]
                if stato != "valido" and not corretto:
                    dfg = fg  # colore uniforme per pit/escluso
                canvas.create_text(w * 0.365, y, text=dtxt, fill=dfg,
                                   font=font_data, anchor="center", tags="data")
            # Colonna STATO al posto di TOTALE
            if stato_txt:
                canvas.create_text(w * 0.55, y, text=stato_txt, fill=fg,
                                   font=font_data, anchor="center", tags="data")
            else:
                canvas.create_text(w * 0.55, y, text=_fmt(g["cumulativo"]),
                                   fill=c["testo_dim"], font=font_data,
                                   anchor="center", tags="data")

        # Riga separatore + riepilogo
        n_giri = len(self.giri)
        n_validi = len(tempi_validi)
        row_sep = n_giri + 1
        y_sep = row_sep * rh
        canvas.create_line(0, y_sep, w, y_sep, fill=c["dati"], width=2, tags="data")

        y = row_sep * rh + rh // 2 + 4
        tempo_reale = self._totale
        min_t = int(tempo_reale) // 60
        sec_t = tempo_reale - min_t * 60

        canvas.create_text(w * 0.04, y, text="TOT", fill=c["dati"],
                           font=font_bold, anchor="center", tags="data")
        canvas.create_text(w * 0.18, y, text="%d giri" % n_validi, fill=c["dati"],
                           font=font_bold, anchor="center", tags="data")
        canvas.create_text(w * 0.365, y, text="%d:%06.3f" % (min_t, sec_t), fill=c["dati"],
                           font=font_bold, anchor="center", tags="data")
        if n_validi > 0:
            media = sum(tempi_validi) / n_validi
            canvas.create_text(w * 0.55, y, text="avg %s" % _fmt(media), fill=c["stato_avviso"],
                               font=font_bold, anchor="center", tags="data")

        canvas.configure(scrollregion=(0, 0, w, (row_sep + 2) * rh + 10))

    def _evidenzia_selezione(self):
        """Evidenzia il giro selezionato nella griglia."""
        canvas = self._grid_canvas
        c = self.colori
        canvas.delete("sel")
        if not self.giri or self._giro_selezionato < 0:
            return
        w = canvas.winfo_width()
        rh = self._grid_row_h
        row_idx = self._giro_selezionato + 1  # giri partono da 1
        y_top = row_idx * rh
        canvas.create_rectangle(0, y_top, w, y_top + rh,
                                fill=c["dati"], outline="", tags="sel")
        # Ridisegna testo del giro selezionato sopra il rettangolo
        g = self.giri[self._giro_selezionato]
        y = y_top + rh // 2
        font_data = (FONT_MONO, 11)
        fg = c["sfondo"]  # testo invertito
        canvas.create_text(w * 0.04, y, text="%d" % g["giro"], fill=fg,
                           font=font_data, anchor="center", tags="sel")
        canvas.create_text(w * 0.18, y, text=_fmt(g["tempo"]), fill=fg,
                           font=font_data, anchor="center", tags="sel")
        if "delta" in g:
            canvas.create_text(w * 0.365, y, text=_fmt_delta(g["delta"]), fill=fg,
                               font=font_data, anchor="center", tags="sel")
        stato = g.get("stato", "valido")
        segnalato = g.get("segnalato", False)
        corretto = g.get("corretto", False)
        if stato == "pit":
            s = "PIT"
        elif stato == "incidente":
            s = "INC"
        elif stato == "spenta":
            s = "SPENTA"
        elif stato == "escluso":
            s = "ESC"
        elif corretto:
            s = "CORR"
        elif segnalato:
            s = "INC?"
        else:
            s = _fmt(g["cumulativo"])
        canvas.create_text(w * 0.55, y, text=s, fill=fg,
                           font=font_data, anchor="center", tags="sel")
        # Assicura visibilita
        canvas.tag_raise("sel")

    def _naviga_vai(self, idx):
        """Sposta la selezione a un indice assoluto (usato da Home/End)."""
        if not self.giri:
            return
        nuovo = max(0, min(len(self.giri) - 1, idx))
        delta = nuovo - self._giro_selezionato
        if delta != 0:
            self._naviga_griglia(delta)

    def _naviga_griglia(self, direzione):
        """Naviga su/giu nella griglia giri. Lo scroll viene aggiornato
        SOLO se la riga selezionata esce dal viewport (come in un editor
        di lista standard)."""
        if not self.giri:
            return
        self._giro_selezionato = max(0, min(
            len(self.giri) - 1, self._giro_selezionato + direzione))
        self._evidenzia_selezione()
        # Scroll intelligente: mantieni la selezione visibile
        rh = self._grid_row_h
        row_idx = self._giro_selezionato + 1
        y_top = row_idx * rh
        y_bot = y_top + rh
        canvas = self._grid_canvas
        first_visible = canvas.canvasy(0)
        visible_h = canvas.winfo_height()
        last_visible = first_visible + visible_h
        sr = canvas.cget("scrollregion")
        if not sr:
            return
        parts = sr.split()
        if len(parts) != 4:
            return
        tot_h = float(parts[3])
        if tot_h <= 0:
            return
        if y_top < first_visible + rh:
            # Sopra il viewport: scrolla su con una riga di margine
            canvas.yview_moveto(max(0, (y_top - rh) / tot_h))
        elif y_bot > last_visible - rh:
            # Sotto il viewport: scrolla giu con una riga di margine
            new_top = y_bot - visible_h + rh
            canvas.yview_moveto(min(1.0, new_top / tot_h))

    def _click_griglia(self, event):
        """Seleziona giro cliccando sulla griglia."""
        rh = self._grid_row_h
        # Converti coordinate canvas
        canvas = self._grid_canvas
        y_canvas = canvas.canvasy(event.y)
        row = int(y_canvas / rh) - 1  # -1 perche riga 0 e' header
        if 0 <= row < len(self.giri):
            self._giro_selezionato = row
            self._evidenzia_selezione()

    def _toggle_stato_grid(self, nuovo_stato):
        """Cambia stato del giro selezionato nella griglia integrata."""
        if not self.giri or self._giro_selezionato < 0:
            return
        idx = self._giro_selezionato
        if 0 <= idx < len(self.giri):
            g = self.giri[idx]
            vecchio = g.get("stato", "valido")
            segnalato = g.get("segnalato", False)
            if vecchio == nuovo_stato:
                g["stato"] = "valido"
            else:
                g["stato"] = nuovo_stato
            # INCIDENTE? validato: sostituisci tempo con media ponderata
            if nuovo_stato == "valido" and segnalato:
                tempi_ok = [gi["tempo"] for gi in self.giri
                            if gi.get("stato") == "valido"
                            and not gi.get("segnalato", False)
                            and gi is not g]
                if tempi_ok:
                    media = sum(tempi_ok) / len(tempi_ok)
                    g["tempo_originale"] = g["tempo"]
                    g["tempo"] = round(media, 3)
                    g["corretto"] = True
                    g["segnalato"] = False
            # Ridisegna (selezione resta ferma per poter rivedere)
            self._ridisegna_griglia_analisi()
            self._evidenzia_selezione()
            # Aggiorna grafico e proiezioni
            self._aggiorna_mini_grafico()
            self._aggiorna_proiezioni()
            self._salva_risultati()

    def _toggle_carburante_grid(self):
        """Toggle fuel nella modalita griglia integrata."""
        c = self.colori
        self._sessione_carburante = not self._sessione_carburante
        if self._sessione_carburante:
            self._btn_fuel_toggle.config(text="FUEL:SI F", fg=c["stato_ok"])
        else:
            self._btn_fuel_toggle.config(text="FUEL:NO F", fg=c["stato_errore"])
        self._salva_risultati()

    # _mostra_risultati rimossa: l'analisi e' integrata nella schermata timer

    def _auto_classifica_giri(self):
        if not self.giri:
            return 0
        if any(g.get("stato") != "valido" for g in self.giri):
            return 0
        tempi = sorted([g["tempo"] for g in self.giri])
        n = len(tempi)
        if n < 3:
            return 0
        if n % 2 == 1:
            mediana = tempi[n // 2]
        else:
            mediana = (tempi[n // 2 - 1] + tempi[n // 2]) / 2.0
        soglia_segnalato = mediana * 1.20
        soglia_pit = mediana + 10.0
        classificati = 0
        for g in self.giri:
            if g["tempo"] > soglia_pit:
                g["stato"] = "pit"
                g["segnalato"] = False
                classificati += 1
            elif g["tempo"] > soglia_segnalato:
                g["segnalato"] = True
                classificati += 1
            else:
                g["segnalato"] = False
        return classificati

    # _popola_giri_tree, _toggle_stato, _toggle_carburante rimossi:
    # sostituiti da _ridisegna_griglia_analisi, _toggle_stato_grid, _toggle_carburante_grid

    def _calcola_stint(self):
        stint_list = []
        stint_corrente = []
        ha_pit = any(g.get("stato") == "pit" for g in self.giri)
        for g in self.giri:
            stato = g.get("stato", "valido")
            if stato == "pit" or stato == "spenta":
                # PIT e SPENTA: chiudono lo stint corrente
                if stint_corrente:
                    dur = sum(gi["tempo"] for gi in stint_corrente)
                    stint_list.append({"giri": stint_corrente, "durata": dur,
                                       "n_giri": len(stint_corrente), "completo": True})
                stint_corrente = []
            elif stato == "valido":
                stint_corrente.append(g)
            # incidente/escluso: ignorati nel calcolo stint
        if stint_corrente:
            dur = sum(gi["tempo"] for gi in stint_corrente)
            stint_list.append({"giri": stint_corrente, "durata": dur,
                               "n_giri": len(stint_corrente), "completo": not ha_pit})
        return stint_list

    # _ricalcola_stats rimosso: la griglia analisi integrata mostra tutto nel Canvas

    # -----------------------------------------------------------------
    #  ANALISI IA
    # -----------------------------------------------------------------
    def _build_sessione_dict(self):
        """Costruisce dizionario sessione compatibile con AIAnalisi/scheda gara."""
        validi = [g for g in self.giri if g.get("stato") == "valido"]
        pit_giri = [g for g in self.giri if g.get("stato") == "pit"]
        tempi_v = [g["tempo"] for g in validi]
        best = min(tempi_v) if tempi_v else 0
        media = sum(tempi_v) / len(tempi_v) if tempi_v else 0
        totale = getattr(self, '_totale', 0)

        consumo_min = 0
        autonomia_min = 0
        if hasattr(self, '_sessione_carburante') and self._sessione_carburante:
            stint_list = self._calcola_stint()
            completi = [s for s in stint_list if s["completo"]]
            if completi:
                durate = [s["durata"] / 60.0 for s in completi]
                autonomia_min = sum(durate) / len(durate)
                consumo_min = self.serbatoio / autonomia_min if autonomia_min > 0 else 0

        sessione = {
            "tipo": "laptimer",
            "versione": "2.0",
            "setup": self.setup,
            "pista": self.pista,
            "record_id": self.record_id,
            "pilota": self.pilota,
            "data": datetime.now().strftime("%Y-%m-%d"),
            "ora": datetime.now().strftime("%H:%M:%S"),
            "serbatoio_cc": self.serbatoio,
            "tempo_totale": round(totale, 3),
            "num_giri": len(self.giri),
            "num_giri_validi": len(validi),
            "num_pit_stop": len(pit_giri),
            "miglior_tempo": round(best, 3),
            "media": round(media, 3),
            "consumo_cc_min": round(consumo_min, 2),
            "autonomia_min": round(autonomia_min, 2),
            "sessione_carburante": getattr(self, '_sessione_carburante', True),
            "giri": self.giri,
        }
        if self.setup_snapshot:
            for k, v in self.setup_snapshot.items():
                if k not in sessione:
                    sessione[k] = v
        return sessione

    def _ricostruisci_timer_fermo(self):
        """Ricostruisce la schermata timer in stato FERMO (dopo ritorno da IA).
        NON resetta giri/tempi — ricostruisce solo i widget e poi entra in analisi."""
        self._pulisci()
        self.stato = self.FERMO
        c = self.colori

        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=20, pady=(10, 0))
        tk.Label(header, text="LAPTIMER", bg=c["sfondo"], fg=c["dati"],
                 font=tkfont.Font(family=FONT_MONO, size=16, weight="bold")).pack()
        info_txt = "%s  |  %s  |  %dcc" % (self.pilota, self.setup, self.serbatoio)
        tk.Label(header, text=info_txt, bg=c["sfondo"], fg=c["label"],
                 font=self._f_info).pack(pady=(2, 0))
        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, pady=(8, 0))

        # Area superiore: 3 colonne
        top_area = tk.Frame(self.root, bg=c["sfondo"])
        top_area.pack(fill="x", padx=10, pady=(5, 0))
        top_area.columnconfigure(0, weight=1)
        top_area.columnconfigure(1, weight=1)
        top_area.columnconfigure(2, weight=1)

        # Mini grafico (SX) — dimensioni adattive (uConsole-friendly)
        self._grafico_w, self._grafico_h = self._calcola_dim_grafici()
        self._mini_canvas = tk.Canvas(top_area, bg=c.get("sfondo_celle", "#080808"),
                                       width=self._grafico_w, height=self._grafico_h,
                                       highlightthickness=1,
                                       highlightbackground=c.get("linee", "#1a3a1a"))
        self._mini_canvas.grid(row=0, column=0, sticky="w", padx=(10, 5))

        # Timer (CENTRO)
        timer_col = tk.Frame(top_area, bg=c["sfondo"])
        timer_col.grid(row=0, column=1, sticky="nsew")
        self._lbl_timer = tk.Label(timer_col, text="00:00.00",
                                    bg=c["sfondo"], fg=c["dati"], font=self._f_timer)
        self._lbl_timer.pack()
        self._lbl_ultimo = tk.Label(timer_col, text="", bg=c["sfondo"],
                                     fg=c["label"], font=self._f_lap)
        self._lbl_ultimo.pack()
        self._lbl_delta = tk.Label(timer_col, text="", bg=c["sfondo"],
                                    fg=c["testo_dim"], font=self._f_delta)
        self._lbl_delta.pack()
        info_row = tk.Frame(timer_col, bg=c["sfondo"])
        info_row.pack(pady=(2, 0))
        self._lbl_best = tk.Label(info_row, text="", bg=c["sfondo"],
                                   fg=c["stato_avviso"], font=self._f_best)
        self._lbl_best.pack(side="left", padx=(0, 15))
        self._lbl_fuel = tk.Label(info_row, text="%dcc" % self.serbatoio,
                                   bg=c["sfondo"], fg=c["testo_dim"], font=self._f_best)
        self._lbl_fuel.pack(side="left")
        self._lbl_totale = tk.Label(timer_col, text="00:00:00.0",
                                     bg=c["sfondo"], fg=c["testo_dim"], font=self._f_info)
        self._lbl_totale.pack(pady=(2, 0))

        # Proiezioni (DX) — stesse dimensioni del grafico SX
        self._proiezioni_w = self._grafico_w
        self._proiezioni_h = self._grafico_h
        self._proiezioni_canvas = tk.Canvas(top_area, bg=c.get("sfondo_celle", "#080808"),
                                             width=self._proiezioni_w, height=self._proiezioni_h,
                                             highlightthickness=1,
                                             highlightbackground=c.get("linee", "#1a3a1a"))
        self._proiezioni_canvas.grid(row=0, column=2, sticky="e", padx=(5, 10))

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, pady=(5, 0))

        # Griglia giri (header fisso + body scrollabile)
        self._grid_row_h = 22
        self._grid_rows_drawn = 0
        self._grid_header_canvas = tk.Canvas(self.root, bg=c["sfondo"],
                                              height=self._grid_row_h,
                                              highlightthickness=0, bd=0)
        self._grid_header_canvas.pack(fill="x", padx=20, pady=(2, 0))
        self._grid_canvas = tk.Canvas(self.root, bg=c["sfondo"],
                                       highlightthickness=0, bd=0)
        self._grid_canvas.pack(fill="both", expand=True, padx=20, pady=(0, 2))
        def _on_mousewheel(event):
            self._grid_canvas.yview_scroll(-1 if event.delta > 0 or event.num == 4
                                           else 1, "units")
        self._grid_canvas.bind("<MouseWheel>", _on_mousewheel)
        self._grid_canvas.bind("<Button-4>", _on_mousewheel)
        self._grid_canvas.bind("<Button-5>", _on_mousewheel)
        def _on_grid_resize(event):
            if self.stato == self.FERMO:
                self._ridisegna_griglia_analisi()
                if hasattr(self, '_giro_selezionato') and self._giro_selezionato >= 0:
                    self._evidenzia_selezione()
            else:
                self._ridisegna_griglia_completa()
        self._grid_canvas.bind("<Configure>", _on_grid_resize)
        self._grid_header_canvas.bind("<Configure>",
                                       lambda e: self._disegna_header_intestazione())

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, side="bottom")
        status_bar = tk.Frame(self.root, bg=c["sfondo"])
        status_bar.pack(fill="x", side="bottom", padx=20, pady=(6, 8))
        self._lbl_status = tk.Label(status_bar, text="",
                                     bg=c["sfondo"], fg=c["stato_ok"], font=self._f_status,
                                     anchor="w")
        self._lbl_status.pack(side="left")
        self._lbl_giri_count = tk.Label(status_bar, text="Giri: %d" % len(self.giri),
                                         bg=c["sfondo"], fg=c["testo_dim"],
                                         font=self._f_status, anchor="e")
        self._lbl_giri_count.pack(side="right")

        # Aggiorna grafico, proiezioni e analisi DOPO che i canvas hanno dimensioni reali
        def _init_dopo_render():
            self.root.update_idletasks()
            self._aggiorna_mini_grafico()
            self._aggiorna_proiezioni()
            self._entra_modalita_analisi()
        self.root.after(50, _init_dopo_render)

    def _lancia_analisi_ia(self):
        """Lancia analisi IA sulla sessione corrente (dati gia salvati su disco)."""
        if not _HAS_AI:
            if hasattr(self, '_lbl_res_status'):
                self._lbl_res_status.config(
                    text="Modulo IA non disponibile", fg=self.colori["stato_errore"])
            return

        self._salva_risultati()
        sessione = self._build_sessione_dict()

        # Strategia carburante
        strategia = {}
        autonomia = sessione.get("autonomia_min", 0)
        if autonomia > 0:
            for dur in [5, 20, 30, 45]:
                pit = max(0, int(dur / autonomia) - 1)
                rientro = autonomia * 0.85
                strategia["gara_%d_min" % dur] = {
                    "pit_stop": pit,
                    "rientro_min": round(rientro, 1),
                }

        self._pulisci()
        AIAnalisi(sessione, path=getattr(self, '_salva_path', None),
                  strategia=strategia,
                  parent=self.root, on_close=self._ricostruisci_timer_fermo)

    #  STAMPA TERMICA
    # -----------------------------------------------------------------
    def _genera_scheda_crono(self):
        """Genera righe testo per stampa termica del cronometraggio."""
        righe = []
        r = righe.append

        # Dati sessione
        validi = [g for g in self.giri if g.get("stato") == "valido"]
        pit_giri = [g for g in self.giri if g.get("stato") == "pit"]
        tempi_v = [g["tempo"] for g in validi]
        best = min(tempi_v) if tempi_v else 0
        media = sum(tempi_v) / len(tempi_v) if tempi_v else 0
        n_giri = len(self.giri)
        n_validi = len(validi)
        n_pit = len(pit_giri)

        # Tempo totale
        totale = getattr(self, '_totale', 0)
        tot_min = int(totale) // 60
        tot_sec = totale - tot_min * 60

        # Header
        r("=" * 42)
        r("CRONOMETRAGGIO".center(42))
        r(("TrackMind v%s" % __version__).center(42))
        r("=" * 42)
        r("")
        r("%-20s %21s" % ("Pilota:", self.pilota))
        r("%-20s %21s" % ("Setup:", self.setup[:21]))
        if self.pista:
            r("%-20s %21s" % ("Pista:", self.pista[:21]))
        r("%-20s %21s" % ("Data:", datetime.now().strftime("%d/%m/%Y  %H:%M")))
        r("%-20s %21s" % ("Serbatoio:", "%dcc" % self.serbatoio))
        r("")

        # Riepilogo
        r("-" * 42)
        r("RIEPILOGO".center(42))
        r("-" * 42)
        r("")
        r("%-20s %21s" % ("Tempo totale:", "%d:%06.3f" % (tot_min, tot_sec)))
        r("%-20s %21s" % ("Giri totali:", "%d (%d validi)" % (n_giri, n_validi)))
        if n_pit:
            r("%-20s %21s" % ("Pit stop:", "%d" % n_pit))
        r("%-20s %21s" % ("BEST LAP:", _fmt_tempo(best) if _HAS_PRINT else "%05.2f" % best))
        r("%-20s %21s" % ("MEDIA:", _fmt_tempo(media) if _HAS_PRINT else "%05.2f" % media))
        r("")

        # Dettaglio giri
        r("-" * 42)
        r("DETTAGLIO GIRI".center(42))
        r("-" * 42)
        r("%-5s %10s %8s  %-10s" % ("GIRO", "TEMPO", "DELTA", "STATO"))
        r("-" * 42)

        for g in self.giri:
            num = g["giro"]
            stato = g.get("stato", "valido")
            tempo = g["tempo"]
            corretto = g.get("corretto", False)
            segnalato = g.get("segnalato", False)

            # Formatta tempo
            t_min = int(tempo) // 60
            t_sec = tempo - t_min * 60
            t_txt = "%02d:%05.2f" % (t_min, t_sec)

            # Delta
            d_txt = ""
            if "delta" in g:
                d = g["delta"]
                d_txt = "%+.2f" % d

            # Stato
            if stato == "pit":
                s_txt = "PIT"
            elif stato == "incidente":
                s_txt = "INC"
            elif stato == "spenta":
                s_txt = "SPENTA"
            elif stato == "escluso":
                s_txt = "ESC"
            elif corretto:
                s_txt = "CORR"
            elif segnalato:
                s_txt = "INC?"
            elif tempo == best and num > 1:
                s_txt = "*BEST*"
            else:
                s_txt = ""

            r("%3d   %10s %8s  %-10s" % (num, t_txt, d_txt, s_txt))

        r("-" * 42)

        # Riga totale
        r("%-5s %10s %8s  %d giri" % ("TOT",
            "%d:%06.3f" % (tot_min, tot_sec), "", n_validi))
        r("")

        # Stint/carburante
        if hasattr(self, '_sessione_carburante') and self._sessione_carburante:
            stint_list = self._calcola_stint()
            if stint_list:
                completi = [s for s in stint_list if s["completo"]]
                if completi:
                    durate = [s["durata"] / 60.0 for s in completi]
                    autonomia = sum(durate) / len(durate)
                    consumo = self.serbatoio / autonomia if autonomia > 0 else 0
                    r("-" * 42)
                    r("CARBURANTE".center(42))
                    r("-" * 42)
                    r("")
                    aut_min = int(autonomia)
                    aut_sec = int((autonomia - aut_min) * 60)
                    r("%-20s %21s" % ("Autonomia:", "%d:%02d" % (aut_min, aut_sec)))
                    r("%-20s %21s" % ("Consumo:", "%.1f cc/min" % consumo))
                    r("")

        r("=" * 42)
        r(("Stampato: %s" % datetime.now().strftime("%d/%m/%Y %H:%M")).center(42))
        r("=" * 42)
        r("")

        return righe

    def _stampa_termica(self):
        """Stampa scheda cronometraggio su stampante termica BT."""
        if not _HAS_PRINT:
            if hasattr(self, '_lbl_res_status'):
                self._lbl_res_status.config(
                    text="Modulo stampa non disponibile", fg=self.colori["stato_errore"])
            return

        c = self.colori
        if hasattr(self, '_lbl_res_status'):
            self._lbl_res_status.config(text="Stampa in corso...", fg=c["stato_avviso"])
            self._lbl_res_status.update_idletasks()

        righe = self._genera_scheda_crono()

        def _stampa_thread():
            ok, msg = stampa_bluetooth(righe, "auto")
            try:
                if hasattr(self, '_lbl_res_status'):
                    fg = c["stato_ok"] if ok else c["stato_errore"]
                    self._lbl_res_status.config(text=msg, fg=fg)
            except Exception:
                pass

        import threading
        t = threading.Thread(target=_stampa_thread, daemon=True)
        t.start()

    #  SALVATAGGIO
    # -----------------------------------------------------------------
    def _salva_risultati(self):
        if not self.giri:
            return None
        validi = [g for g in self.giri if g.get("stato") == "valido"]
        pit_giri = [g for g in self.giri if g.get("stato") == "pit"]
        tempi_v = [g["tempo"] for g in validi]
        tempi_pit = [g["tempo"] for g in pit_giri]
        media = sum(tempi_v) / len(tempi_v) if tempi_v else 0
        miglior = min(tempi_v) if tempi_v else 0
        miglior_idx = 0
        if tempi_v:
            best_val = min(tempi_v)
            for g in validi:
                if abs(g["tempo"] - best_val) < 0.001:
                    miglior_idx = g["giro"]
                    break
        media_pit = sum(tempi_pit) / len(tempi_pit) if tempi_pit else 0
        stint_list = self._calcola_stint()
        completi = [s for s in stint_list if s["completo"]]
        if completi and self._sessione_carburante:
            durate_stint = [s["durata"] / 60.0 for s in completi]
            autonomia_min = sum(durate_stint) / len(durate_stint)
            consumo_min = self.serbatoio / autonomia_min if autonomia_min > 0 else 0
        else:
            consumo_min = 0
            autonomia_min = 0
        stint_data = []
        for i, st in enumerate(stint_list):
            dur_m = st["durata"] / 60.0
            stint_data.append({
                "stint": i + 1, "n_giri": st["n_giri"],
                "durata_sec": round(st["durata"], 3),
                "durata_min": round(dur_m, 2),
                "consumo_cc_min": round(self.serbatoio / dur_m, 2) if dur_m > 0 else 0,
            })
        risultato = {
            "tipo": "laptimer",
            "versione": "2.0",
            "setup": self.setup,
            "pista": self.pista,
            "record_id": self.record_id,
            "pilota": self.pilota,
            "data": datetime.now().strftime("%Y-%m-%d"),
            "ora": datetime.now().strftime("%H:%M:%S"),
            "serbatoio_cc": self.serbatoio,
            "tempo_totale": round(self._totale, 3),
            "num_giri": len(self.giri),
            "num_giri_validi": len(validi),
            "num_pit_stop": len(pit_giri),
            "miglior_giro": miglior_idx,
            "miglior_tempo": round(miglior, 3),
            "media": round(media, 3),
            "media_pit": round(media_pit, 3),
            "consumo_cc_min": round(consumo_min, 2),
            "autonomia_min": round(autonomia_min, 2),
            "sessione_carburante": self._sessione_carburante,
            "stint": stint_data,
            "giri": self.giri,
        }
        # Fotografia setup al momento della sessione (per analisi IA)
        if self.setup_snapshot:
            for k, v in self.setup_snapshot.items():
                if k not in risultato:
                    risultato[k] = v
        if self.dati_dir and os.path.isdir(self.dati_dir):
            save_dir = self.dati_dir
        else:
            if getattr(sys, 'frozen', False):
                save_dir = os.path.dirname(sys.executable)
            else:
                save_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            save_dir = os.path.join(save_dir, "laptimer_dati")
        os.makedirs(save_dir, exist_ok=True)
        if hasattr(self, '_salva_path') and self._salva_path and os.path.exists(self._salva_path):
            path = self._salva_path
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            prefisso = self.record_id if self.record_id else self.setup.replace(" ", "_")[:20]
            prefisso = prefisso.replace("/", "-").replace("\\", "-").replace(":", "-").replace(" ", "_")
            nome = "lap_%s_%s.json" % (prefisso, ts)
            path = os.path.join(save_dir, nome)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(risultato, f, ensure_ascii=False, indent=2)
            self._salva_path = path
            return path
        except Exception as e:
            print("Errore salvataggio: %s" % e)
            return None

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------
#  MAIN
# ---------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TrackMind LapTimer")
    parser.add_argument("--setup", default="", help="Nome setup attivo")
    parser.add_argument("--pilota", default="", help="Nome pilota")
    parser.add_argument("--record-id", default="", help="ID record setup in TrackMind")
    parser.add_argument("--token", default="", help="Token di lancio da TrackMind")
    parser.add_argument("--dati-dir", default="", help="Cartella dati TrackMind")
    args = parser.parse_args()

    if not args.token or not _verifica_token(args.token):
        import tkinter as _tk
        _r = _tk.Tk()
        _r.title(f"TrackMind LapTimer  v{__version__}")
        _r.configure(bg="#0a0a0a")
        _r.geometry("500x200")
        _tk.Label(_r, text="ACCESSO NEGATO", bg="#0a0a0a", fg="#ff5555",
                  font=(FONT_MONO, 24, "bold")).pack(pady=(40, 10))
        _tk.Label(_r, text="Il LapTimer puo' essere avviato solo da TrackMind",
                  bg="#0a0a0a", fg="#22aa22", font=(FONT_MONO, 12)).pack()
        _r.bind("<Escape>", lambda e: _r.destroy())
        _r.bind("<space>", lambda e: _r.destroy())
        _r.mainloop()
        sys.exit(0)

    app = LapTimer(setup=args.setup, pilota=args.pilota, dati_dir=args.dati_dir, record_id=args.record_id)
    app.run()
