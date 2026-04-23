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
from tkinter import font as tkfont
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
    from core.thermal_print import (stampa_bluetooth,
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

# Barra batteria (opzionale: se il modulo non c'e', si ignora)
try:
    from core.batteria import aggiungi_barra_batteria as _aggiungi_barra_bat
except Exception:
    def _aggiungi_barra_bat(*args, **kwargs):
        return None

# LapMonitor BLE (opzionale): import protetto. Se bleak o il modulo
# mancano, LapTimer gira in modalita' manuale mono-pilota come sempre.
# Se bleak c'e', all'ingresso della schermata cronometro viene lanciato
# uno scan BLE in background (5s): se trova un dispositivo 'LapM*' si
# connette automaticamente e passa a modalita' LIVE multi-pilota.
try:
    from core.lapmonitor import (LapMonitorClient, scan_devices_async,
                                 _HAS_BLEAK)
    _HAS_LAPMONITOR = True
except Exception:
    _HAS_LAPMONITOR = False
    _HAS_BLEAK = False
    LapMonitorClient = None
    scan_devices_async = None

# Colori colonne pilota in modalita' LIVE (stessi del grafico Crono).
_LIVE_COLORS = [
    "#39ff14", "#ffaa00", "#6688ff", "#ff5555", "#00ffff",
    "#ff66ff", "#ffff00", "#ff8844", "#88ff88", "#ff88cc",
    "#bbbbff", "#ffbb88",
]


def _live_carica_trasponder_mapping(dati_dir_parent):
    """Legge dati/trasponder.json (se esiste) e ritorna
    {numero_int: nome_str}. dati_dir_parent e' il path alla cartella
    'dati/' (contenitore di scouting/ e id_XXXX/). Ritorna dict vuoto
    se il file manca o e' malformato."""
    mapping = {}
    if not dati_dir_parent:
        return mapping
    path = os.path.join(dati_dir_parent, "trasponder.json")
    if not os.path.exists(path):
        return mapping
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("records", []) if isinstance(data, dict) else data
        for r in records:
            try:
                num = int(r.get("Numero", 0))
            except (ValueError, TypeError):
                continue
            nome = (r.get("Pilota") or "").strip()
            if num > 0 and nome:
                mapping[num] = nome
    except Exception:
        pass
    return mapping


def _live_nome_display(nome_mapping_or_none, pilot_num, max_len=10):
    """Calcola il nome compatto da mostrare nella colonna live.
    Regole:
      - Trasponder non mappato -> 'T<num>' (es. 'T26')
      - Nome multi-parola -> iniziali con punti: 'Sandro Grandesso' -> 'S.G.',
        'Marco Aurelio Rossi' -> 'M.A.R.'. Preciso e sempre leggibile.
      - Nome mono-parola <= max_len -> usa as-is (es. 'SG', 'Sandro')
      - Nome mono-parola troppo lungo -> tronca a max_len."""
    nome = nome_mapping_or_none
    if not nome:
        return "T%d" % pilot_num
    nome = nome.strip()
    parti = [p for p in nome.split() if p]
    if len(parti) >= 2:
        # Iniziali con punti: 'Sandro Grandesso' -> 'S.G.'
        return ".".join(p[0].upper() for p in parti) + "."
    # Mono-parola
    if len(nome) <= max_len:
        return nome
    return nome[:max_len]

# Font monospace + helper colori centralizzati
try:
    from config_colori import FONT_MONO, carica_colori as _carica_colori
except ImportError:
    import sys as _sys
    FONT_MONO = "Consolas" if _sys.platform == "win32" else "DejaVu Sans Mono"
    def _carica_colori():
        return {}

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

        # ── Stato LIVE (multi-pilota via ricevitore LapMonitor BT) ──
        # _live_mode=True solo quando il ricevitore e' connesso.
        # _live_client: istanza LapMonitorClient del thread BLE.
        # _live_pilots: {pilot_num: {"laps":[], "best":f, "total":f}}.
        # _live_columns: {pilot_num: tk.Frame} - colonna UI del pilota.
        # _live_colwidgets: {pilot_num: {"last":L, "stats":L, "list":T}}.
        # _live_banner: Label nell'header per stato connessione BLE.
        # _live_mapping: {num: nome} caricato da dati/trasponder.json.
        self._live_mode = False
        self._live_client = None
        self._live_pilots = {}
        self._live_columns = {}
        self._live_colwidgets = {}
        self._live_banner = None
        self._live_mapping = {}
        self._live_connecting = False  # True durante lo scan/connect
        self._live_last_order = None   # cache ordine colonne (perf)
        self._live_prev_passo_pending = False  # debounce flag

        # Saltiamo la schermata di scelta carburante: non serve piu'.
        # Se in futuro serve, si puo' memorizzare come campo IA nel
        # record setup. Andiamo direttamente alla schermata cronometro
        # in stato ATTESA (il timer partira' col primo passaggio o con
        # la pressione di SPAZIO).
        self.serbatoio = 0
        self.stato = self.ATTESA
        self.t_ultimo_giro = 0.0
        self.giri = []
        self.miglior_tempo = None
        self._space_locked = False
        self._lock_tempo = 1.0
        self.colori = _carica_colori()
        self._init_root(parent)
        self._init_fonts()
        # Direttamente alla schermata timer (fuel select rimosso)
        self._schermata_timer()

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
        # Font GRANDI per tempo totale e passo gara (leggibili a distanza dalla
        # pista): lo spazio lo recuperiamo limitando l'altezza della griglia
        # giri (vedi _schermata_timer / _ricostruisci_timer_fermo).
        self._f_totale = tkfont.Font(family=FONT_MONO, size=32, weight="bold")
        self._f_passo  = tkfont.Font(family=FONT_MONO, size=22, weight="bold")

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
        # Cleanup tasti digit di simulazione LIVE (1-9, 0)
        for i in range(10):
            try:
                self.root.unbind("<Key-%d>" % i)
            except Exception:
                pass
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
        # Barra batteria: place() sul TOPLEVEL (self.root), NON sull'header,
        # cosi' e' un overlay puro e non occupa spazio nel pack layout.
        _aggiungi_barra_bat(self.root)
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

    def _calcola_altezza_griglia(self):
        """Altezza fissa (in px) per il corpo della griglia giri.
        Su uConsole (schermo 480px) riservo solo 4 righe visibili per
        garantire che la status-bar con i bottoni (ESCLUDI/PIT/INCID/...)
        resti sempre in vista. Su schermi piu' grandi (Windows 720+) posso
        permettermi piu' righe. I giri in eccesso si scorrono con rotella
        mouse o frecce Su/Giu.
        """
        try:
            self.root.update_idletasks()
            wh = self.root.winfo_height()
            if wh < 100:
                geo = self.root.geometry().split("+")[0]
                if "x" in geo:
                    try:
                        wh = int(geo.split("x")[1])
                    except Exception:
                        wh = self.root.winfo_screenheight()
                else:
                    wh = self.root.winfo_screenheight()
        except Exception:
            wh = 720
        # Riga griglia = 22px (definita in _schermata_timer come _grid_row_h)
        row_h = 22
        if wh <= 500:
            # uConsole 480: massimo 4 righe visibili
            return row_h * 4
        elif wh <= 720:
            # Notebook / desktop standard: 8 righe
            return row_h * 8
        else:
            # Schermi grandi: 12 righe
            return row_h * 12

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
        # Info subtitle: pilota + setup (niente piu' serbatoio cc)
        info_txt = "%s  |  %s" % (self.pilota, self.setup)
        tk.Label(header, text=info_txt, bg=c["sfondo"], fg=c["label"],
                 font=self._f_info).pack(pady=(2, 0))
        # Barra batteria: place() sul TOPLEVEL (self.root), NON sull'header,
        # cosi' e' un overlay puro e non occupa spazio nel pack layout.
        _aggiungi_barra_bat(self.root)
        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, pady=(2, 0))

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
        # Tempo totale trascorso dalla partenza - GRANDE, leggibile a colpo d'occhio
        self._lbl_totale = tk.Label(timer_col, text="00:00:00.0",
                                     bg=c["sfondo"], fg=c["stato_ok"], font=self._f_totale)
        self._lbl_totale.pack(pady=(6, 0))
        # Passo gara: media ultimi 3 giri validi (indicatore di ritmo)
        self._lbl_passo = tk.Label(timer_col, text="passo: --:--.--",
                                    bg=c["sfondo"], fg=c["testo_dim"], font=self._f_passo)
        self._lbl_passo.pack(pady=(2, 0))

        # Pannello PROIEZIONI GIRI (DX) — stesse dimensioni del grafico SX
        self._proiezioni_w = self._grafico_w
        self._proiezioni_h = self._grafico_h
        self._proiezioni_canvas = tk.Canvas(top_area, bg=c.get("sfondo_celle", "#080808"),
                                             width=self._proiezioni_w, height=self._proiezioni_h,
                                             highlightthickness=1,
                                             highlightbackground=c.get("linee", "#1a3a1a"))
        self._proiezioni_canvas.grid(row=0, column=2, sticky="e", padx=(5, 10))
        self._aggiorna_proiezioni()

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, pady=(2, 0))

        # ── Lista giri con griglia (sotto, tutta larghezza) ──
        self._grid_row_h = 22  # altezza riga griglia (definita PRIMA dei canvas)
        self._grid_rows_drawn = 0

        # Canvas HEADER fisso: intestazioni GIRO/TEMPO/DELTA/TOTALE sempre visibili
        self._grid_header_canvas = tk.Canvas(self.root, bg=c["sfondo"],
                                              height=self._grid_row_h,
                                              highlightthickness=0, bd=0)
        self._grid_header_canvas.pack(fill="x", padx=20, pady=(2, 0))

        # Canvas BODY scrollabile: solo linee griglia + dati giri (niente intestazioni)
        # Altezza FISSA calcolata in base allo schermo: su uConsole (480px) mostro
        # solo ~4 giri per garantire che la status-bar con i bottoni comandi
        # (ESCLUDI/PIT/INCID/...) resti sempre visibile in basso. I giri in
        # eccesso si scorrono con rotella mouse o frecce Su/Giu.
        self._grid_body_h = self._calcola_altezza_griglia()
        self._grid_canvas = tk.Canvas(self.root, bg=c["sfondo"],
                                       height=self._grid_body_h,
                                       highlightthickness=0, bd=0)
        self._grid_canvas.pack(fill="x", padx=20, pady=(0, 2))
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
        self._lbl_status = tk.Label(status_bar, text="SPAZIO = Avvia  |  1-0 = simula pilota (test)  |  S = Stampa  |  ESC = Indietro",
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
        # Stampa immediata del foglietto per il pilota (anche durante la gara)
        self.root.bind("<s>", lambda e: self._stampa_termica())
        self.root.bind("<S>", lambda e: self._stampa_termica())

        # Tasti numerici 1-9 e 0: simulano un passaggio trasponder per
        # test multi-pilota senza hardware (tasto 1 -> trasp 1, tasto 2
        # -> trasp 2, ..., tasto 0 -> trasp 10). Attivano automaticamente
        # la modalita' LIVE al primo press. Utili quando ho un solo
        # trasponder reale ma voglio verificare l'ordinamento GARA e
        # il riordino colonne.
        for i in range(1, 10):
            self.root.bind("<Key-%d>" % i,
                            lambda e, n=i: self._sim_trasponder(n))
        self.root.bind("<Key-0>", lambda e: self._sim_trasponder(10))

        # Avvia scan BLE LapMonitor in background (5s). Se trova e
        # connette -> passa a modalita' LIVE multi-pilota. Se non
        # trova, resta tutto come oggi (manuale mono-pilota).
        self._avvia_scan_lapmonitor()

    def _sim_trasponder(self, pilot_num):
        """Simula un passaggio trasponder per un numero pilota dato.
        Usato dai tasti 1-9, 0 per test multi-pilota senza hardware.
        La logica e' identica a quella del LapMonitorClient reale:
        primo passaggio -> delta=None (solo baseline), successivi
        -> delta = tempo dall'ultimo passaggio stesso pilota."""
        # Attiva LIVE se non ancora attivo (primo keypress)
        if not self._live_mode:
            # Serve che il timer sia in ATTESA o RUNNING
            if self.stato not in (self.ATTESA, self.RUNNING):
                return
            self._live_attiva_modo()
            # Se _live_attiva_modo non e' andato a buon fine, esci
            if not self._live_mode:
                return

        # State per-pilota per calcolare delta tra press consecutivi
        if not hasattr(self, "_live_sim_state"):
            self._live_sim_state = {}
        now = time.perf_counter()
        st = self._live_sim_state.get(pilot_num)
        if st is None:
            delta = None  # primo passaggio del pilota (baseline)
        else:
            delta = now - st["last_t"]
            if delta < 0.5:
                # Anti-raffica: se ripremo troppo veloce ignora
                return
        self._live_sim_state[pilot_num] = {"last_t": now}
        # Dispatcha come farebbe il client BLE reale
        self._live_on_lap(pilot_num, 0, delta, None, "sim")

    # -----------------------------------------------------------------
    #  LIVE: scan BLE, connessione, griglia multi-colonna
    # -----------------------------------------------------------------
    # Numero massimo di tentativi di scan consecutivi se non trova
    # nessun dispositivo (BlueZ su Linux a volte richiede piu' tentativi
    # specie dopo una sessione precedente). Dopo max tentativi rinuncia
    # silenziosamente e resta in modalita' manuale.
    _LIVE_SCAN_MAX_RETRY = 6
    _LIVE_SCAN_INTERVAL_MS = 6000  # 6 sec tra tentativi

    def _avvia_scan_lapmonitor(self):
        """Lancia scan BLE in background per trovare ricevitori LapM*.
        Se bleak manca o il modulo non e' disponibile, esce subito
        senza mostrare nulla (manuale funziona normalmente).
        Se nessun dispositivo viene trovato al primo scan, riprova
        automaticamente ogni 6 secondi (fino a max tentativi).
        Utile quando il ricevitore viene acceso dopo l'app, o quando
        BlueZ e' in cooldown dopo una sessione precedente."""
        if not _HAS_LAPMONITOR or not _HAS_BLEAK:
            return
        if self._live_connecting or self._live_mode:
            return
        self._live_connecting = True
        self._live_scan_attempt = 1
        c = self.colori
        # Banner fisso in alto a sinistra: vive per tutta la ricerca
        try:
            self._live_banner = tk.Label(self.root,
                text="  BT: ricerca LapMonitor...",
                bg=c["sfondo"], fg=c["stato_avviso"],
                font=self._f_status, anchor="w")
            self._live_banner.place(relx=0.01, rely=0.0, anchor="nw")
        except Exception:
            self._live_banner = None

        self._fai_scan_iterativo()

    def _fai_scan_iterativo(self):
        """Esegue un tentativo di scan. Se trova dispositivi connette
        al primo. Se non trova, ripianifica fra
        _LIVE_SCAN_INTERVAL_MS fino a _LIVE_SCAN_MAX_RETRY tentativi."""
        if self._live_mode:
            return
        # Se la schermata cronometro e' stata lasciata, smetti
        if self.stato not in (self.ATTESA, self.RUNNING):
            self._live_connecting = False
            return
        # Aggiorna banner col numero di tentativo
        try:
            if self._live_banner is not None and self._live_banner.winfo_exists():
                self._live_banner.config(
                    text="  BT: ricerca LapMonitor... (tent. %d/%d)"
                         % (self._live_scan_attempt, self._LIVE_SCAN_MAX_RETRY),
                    fg=self.colori["stato_avviso"])
        except Exception:
            pass

        def _on_scan_done(result):
            if self._live_mode:
                return
            # Widget distrutti nel frattempo: esci
            try:
                if self._live_banner is None or not self._live_banner.winfo_exists():
                    self._live_connecting = False
                    return
            except Exception:
                self._live_connecting = False
                return

            if result:
                # Trovato: connetti al primo
                name, addr = result[0]
                try:
                    self._live_banner.config(
                        text="  BT: connessione a %s..." % name,
                        fg=self.colori["stato_avviso"])
                except Exception:
                    pass
                self._live_connetti(addr, name)
                return

            # Zero dispositivi: retry se sotto la soglia
            self._live_scan_attempt += 1
            if self._live_scan_attempt > self._LIVE_SCAN_MAX_RETRY:
                # Rinuncia: lascia un banner neutro, resta in manuale
                try:
                    self._live_banner.config(
                        text="  BT: nessun LapMonitor - manuale attivo",
                        fg=self.colori["testo_dim"])
                except Exception:
                    pass
                self._live_connecting = False
                return
            # Ripianifica prossimo tentativo
            try:
                self._live_banner.config(
                    text="  BT: ritento fra %ds..."
                         % (self._LIVE_SCAN_INTERVAL_MS // 1000),
                    fg=self.colori["testo_dim"])
            except Exception:
                pass
            try:
                self.root.after(self._LIVE_SCAN_INTERVAL_MS,
                                 self._fai_scan_iterativo)
            except Exception:
                self._live_connecting = False

        scan_devices_async(prefix="LapM", timeout=5.0,
                           on_done=_on_scan_done, tk_root=self.root)

    def _live_connetti(self, addr, name):
        """Crea il LapMonitorClient e lo fa partire in thread BLE."""
        self._live_client = LapMonitorClient(
            address=addr, tk_root=self.root,
            on_lap=self._live_on_lap,
            on_status=self._live_on_status,
            on_connected=self._live_on_connected)
        self._live_client_name = name
        self._live_client.start()

    def _live_on_status(self, msg, livello="info"):
        """Aggiorna il banner header con lo stato BLE (thread-safe:
        invocato gia' sul main thread)."""
        try:
            if self._live_banner is None or not self._live_banner.winfo_exists():
                return
            c = self.colori
            colore = {
                "ok":     c["stato_ok"],
                "errore": c["stato_errore"],
                "avviso": c["stato_avviso"],
            }.get(livello, c["testo_dim"])
            self._live_banner.config(text="  BT: " + msg, fg=colore)
        except (tk.TclError, Exception):
            pass

    def _live_on_connected(self, connected):
        """Callback cambio stato connessione."""
        if connected:
            # Attiva modalita' LIVE: sostituisci griglia bottom e
            # auto-avvia il cronometro (visto che non c'e' piu' SPAZIO).
            self._live_attiva_modo()
            try:
                self._live_banner.config(
                    text="  LIVE: %s" % self._live_client_name,
                    fg=self.colori["stato_ok"])
            except Exception:
                pass
        else:
            # Disconnessione o connessione fallita.
            # Se eravamo in LIVE, e' stata una disconnessione vera e propria:
            # tenta di ricollegare automaticamente (l'utente ha acceso/spento).
            # Se eravamo ancora in fase di connessione (es. errore InProgress
            # di BlueZ), ripianifica un nuovo scan con backoff piu' lungo.
            try:
                if self._live_client is not None:
                    self._live_client = None
            except Exception:
                pass
            if self.stato not in (self.ATTESA, self.RUNNING):
                # Utente uscito dalla schermata: non ritentare
                return
            # Backoff: aspetta piu' a lungo per dare tempo a BlueZ di
            # liberare il device (tipicamente 5-10s dopo un InProgress).
            try:
                if self._live_banner is not None and self._live_banner.winfo_exists():
                    self._live_banner.config(
                        text="  BT: connessione fallita, ritento fra 10s...",
                        fg=self.colori["stato_avviso"])
            except Exception:
                pass
            # Reset flag e ripianifica scan dopo 10s
            self._live_connecting = False
            self._live_scan_attempt = 1
            try:
                self.root.after(10000, self._avvia_scan_lapmonitor)
            except Exception:
                pass

    def _live_attiva_modo(self):
        """Passa a modalita' LIVE: carica mapping trasponder, disabilita
        SPAZIO, sostituisce la griglia mono-pilota con multi-colonna,
        avvia automaticamente il cronometro (t_start = now)."""
        if self._live_mode:
            return
        # Guardia: se nel frattempo l'utente ha lasciato la schermata
        # cronometro (es. ESC prima che la connessione completasse),
        # non attivare LIVE. Stato valido: ATTESA o RUNNING.
        if self.stato not in (self.ATTESA, self.RUNNING):
            # Scollega il client per non lasciare thread BLE appeso
            try:
                if self._live_client is not None:
                    self._live_client.stop()
                    self._live_client = None
            except Exception:
                pass
            return
        self._live_mode = True

        # Carica mappatura trasponder -> pilota dalla cartella dati/
        try:
            parent = (os.path.dirname(self.dati_dir.rstrip("/\\"))
                       if self.dati_dir else "")
            self._live_mapping = _live_carica_trasponder_mapping(parent)
        except Exception:
            self._live_mapping = {}

        # NON disabilitiamo SPAZIO: serve per avviare il cronometro
        # (l'utente preme SPAZIO per partire anche in LIVE). Durante
        # RUNNING il SPAZIO viene ignorato (guardia in _on_spazio),
        # i giri arrivano dal ricevitore BLE.

        # Smonta i widget non necessari in LIVE:
        # - Griglia mono-pilota GIRO/TEMPO/DELTA/TOTALE (bottom)
        # - Mini grafico SX e proiezioni DX (li sostituiamo con pannelli
        #   previsione/passo multi-pilota, vedi sotto)
        # - Labels duplicate: totale (gia' mostrato dal _lbl_timer),
        #   passo, best, fuel, ultimo, delta (tutte mono-pilota)
        c = self.colori
        # Grab reference a top_area prima di distruggere (serve per
        # agganciare i nuovi pannelli SX/DX alle stesse celle grid)
        top_area = None
        try:
            if hasattr(self, "_lbl_timer"):
                top_area = self._lbl_timer.master.master
        except Exception:
            top_area = None
        for attr in ("_grid_header_canvas", "_grid_canvas",
                     "_mini_canvas", "_proiezioni_canvas",
                     "_lbl_totale", "_lbl_passo", "_lbl_best",
                     "_lbl_fuel", "_lbl_ultimo", "_lbl_delta"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
                try:
                    setattr(self, attr, None)
                except Exception:
                    pass

        # Pannelli LIVE: previsione arrivo (SX) e passo gara (DX),
        # allocati nelle stesse celle grid dei canvas rimossi
        if top_area is not None:
            self._live_crea_pannelli_laterali(top_area)

        # Nuovo container colonne: usa pack a side="left" per colonne
        # di larghezza fissa che si impacchettano da sinistra. Quando
        # arriva un nuovo pilota appare una colonna piccola accanto
        # alle esistenti; il riordino GARA le rimescola senza stretch.
        self._live_grid_frame = tk.Frame(self.root, bg=c["sfondo"])
        self._live_grid_frame.pack(fill="both", expand=True, padx=10,
                                    pady=(2, 2), side="top")

        # Auto-avvio al primo passaggio: il cronometro parte quando
        # arriva il primissimo trasponder (vedi _live_on_lap).
        if hasattr(self, "_lbl_status"):
            self._lbl_status.config(
                text="LIVE: in attesa del primo passaggio trasponder...",
                fg=c["stato_ok"])

    # Orizzonti temporali (in minuti) per il pannello "PREVISIONE ARRIVO"
    _LIVE_HORIZONS = [5, 20, 30, 45]

    def _live_crea_pannelli_laterali(self, top_area):
        """Crea i due pannelli laterali in LIVE:
          - SX: PREVISIONE ARRIVO (giri previsti a 5/20/30/45 min per pilota)
          - DX: PASSO GARA (media ultimi 3 giri per pilota + trend)
        Entrambi sono Text read-only aggiornati a ogni lap ricevuto."""
        c = self.colori

        # Pannello sinistro: PREVISIONE
        self._live_prev_frame = tk.Frame(top_area, bg=c["sfondo_celle"],
                relief="ridge", bd=1,
                highlightbackground=c["linee"], highlightthickness=1)
        self._live_prev_frame.grid(row=0, column=0, sticky="nsew",
                                    padx=(10, 5), pady=(0, 4))
        tk.Label(self._live_prev_frame, text="PREVISIONE ARRIVO",
                 bg=c["sfondo_celle"], fg=c["dati"],
                 font=self._f_status).pack(pady=(4, 0))
        self._live_prev_text = tk.Text(self._live_prev_frame,
                 bg=c["sfondo_celle"], fg=c["dati"],
                 font=self._f_status, relief="flat",
                 highlightthickness=0, bd=0,
                 wrap="none", cursor="arrow",
                 width=26, height=10)
        self._live_prev_text.pack(fill="both", expand=True,
                                   padx=4, pady=(2, 4))
        self._live_prev_text.config(state="disabled")

        # Pannello destro: PASSO
        self._live_passo_frame = tk.Frame(top_area, bg=c["sfondo_celle"],
                relief="ridge", bd=1,
                highlightbackground=c["linee"], highlightthickness=1)
        self._live_passo_frame.grid(row=0, column=2, sticky="nsew",
                                     padx=(5, 10), pady=(0, 4))
        tk.Label(self._live_passo_frame, text="PASSO GARA",
                 bg=c["sfondo_celle"], fg=c["dati"],
                 font=self._f_status).pack(pady=(4, 0))
        self._live_passo_text = tk.Text(self._live_passo_frame,
                 bg=c["sfondo_celle"], fg=c["dati"],
                 font=self._f_status, relief="flat",
                 highlightthickness=0, bd=0,
                 wrap="none", cursor="arrow",
                 width=26, height=10)
        self._live_passo_text.pack(fill="both", expand=True,
                                    padx=4, pady=(2, 4))
        self._live_passo_text.config(state="disabled")

        # Render iniziale con stato vuoto
        self._live_aggiorna_prev_passo()

    def _live_aggiorna_prev_passo(self):
        """Ricalcola e aggiorna i pannelli laterali PREVISIONE e PASSO.
        Chiamato dopo ogni giro ricevuto (BLE o simulato)."""
        c = self.colori

        # ── PREVISIONE ARRIVO ──
        # Per ogni pilota calcola: media_lap -> giri_previsti a
        # 5/20/30/45 minuti. Ranking per media ascendente (piu' veloce
        # = piu' giri stimati). I piloti senza giri validi vengono
        # saltati (non si puo' proiettare senza dati).
        pilot_info = []  # lista (pid, nome_vis, media, tempi, tot_time)
        for pid, d in self._live_pilots.items():
            validi = [l for l in d["laps"]
                       if l.get("stato") == "valido" and l.get("tempo", 0) > 0]
            if not validi:
                continue
            tempi = [l["tempo"] for l in validi]
            media = sum(tempi) / len(tempi)
            nome_vis = _live_nome_display(
                self._live_mapping.get(pid), pid, max_len=6)
            pilot_info.append((pid, nome_vis, media, tempi, sum(tempi)))
        # Sort per media (piu' veloce prima)
        pilot_info.sort(key=lambda x: x[2])

        prev_lines = []
        header = " %-6s %5s %5s %5s %5s" % (
            "Pil.", "5m", "20m", "30m", "45m")
        prev_lines.append(header)
        prev_lines.append("-" * len(header))
        if not pilot_info:
            prev_lines.append(" (in attesa giri)")
        for pid, nome, media, _, _ in pilot_info:
            row = [int((mm * 60) / media) if media > 0 else 0
                    for mm in self._LIVE_HORIZONS]
            prev_lines.append(" %-6s %5d %5d %5d %5d" % (
                nome, row[0], row[1], row[2], row[3]))

        try:
            self._live_prev_text.config(state="normal")
            self._live_prev_text.delete("1.0", "end")
            self._live_prev_text.insert("end", "\n".join(prev_lines))
            self._live_prev_text.config(state="disabled")
        except (tk.TclError, Exception):
            pass

        # ── PASSO GARA ──
        # Per ogni pilota: media ultimi 3 giri validi (passo recente)
        # + confronto con media globale del pilota -> trend UP/DN/==.
        passo_lines = []
        header_p = " %-6s %7s %s" % ("Pil.", "Passo", "Trend")
        passo_lines.append(header_p)
        passo_lines.append("-" * len(header_p))
        if not pilot_info:
            passo_lines.append(" (in attesa giri)")
        # Ordine: piloti con passo migliore in alto
        passo_rows = []
        for pid, nome, media_glob, tempi, _ in pilot_info:
            ultimi = tempi[-3:]
            passo = sum(ultimi) / len(ultimi)
            delta = passo - media_glob
            if delta < -0.05:
                trend = "migl."  # verde in senso semantico
            elif delta > 0.15:
                trend = "lento"
            else:
                trend = "stab."
            passo_rows.append((pid, nome, passo, trend, delta))
        passo_rows.sort(key=lambda x: x[2])  # passo ascendente
        for pid, nome, passo, trend, delta in passo_rows:
            sign = "+" if delta >= 0 else ""
            passo_lines.append(" %-6s %s  %s %s%.2f" % (
                nome, _fmt(passo), trend, sign, delta))

        try:
            self._live_passo_text.config(state="normal")
            self._live_passo_text.delete("1.0", "end")
            self._live_passo_text.insert("end", "\n".join(passo_lines))
            self._live_passo_text.config(state="disabled")
        except (tk.TclError, Exception):
            pass

    def _live_on_lap(self, pilot_num, device_cnt, delta, ts, raw_hex):
        """Callback giro BLE (gia' nel main thread). Aggiunge il giro
        al pilota, crea la colonna se e' nuovo, riordina per GARA.
        Al primissimo passaggio trasponder in assoluto, avvia il
        cronometro centrale (session timer) e passa a RUNNING."""
        # Protezione: se i widget sono stati distrutti ignora
        try:
            if not hasattr(self, "_live_grid_frame"):
                return
            if not self._live_grid_frame.winfo_exists():
                return
        except (tk.TclError, Exception):
            return

        # Auto-start al primo passaggio: partiamo il cronometro
        # quando vediamo il primo trasponder, non aspettiamo SPAZIO
        if self.stato == self.ATTESA:
            self._avvia()

        if pilot_num not in self._live_pilots:
            self._live_pilots[pilot_num] = {
                "laps": [], "best": None, "total": 0.0,
            }
            self._live_crea_colonna(pilot_num)

        data = self._live_pilots[pilot_num]
        if delta is None or delta <= 0:
            # Primo passaggio del trasponder: nessun tempo disponibile,
            # serve solo a far comparire la colonna e a dare a LapMonitor
            # la baseline per i delta successivi. NON aggiungiamo nulla
            # alla lista giri (altrimenti si vedrebbe un "1 --" fantasma
            # e poi il vero primo giro appare di nuovo come "1").
            self._live_aggiorna_colonna(pilot_num)
            return

        data["laps"].append({
            "giro": len(data["laps"]) + 1,
            "tempo": round(delta, 3),
            "stato": "valido",
        })
        data["total"] += delta
        if data["best"] is None or delta < data["best"]:
            data["best"] = delta

        self._live_aggiorna_colonna(pilot_num)
        self._live_riordina_colonne()
        # Pannelli laterali aggiornati in modo debounced (max 1/800ms)
        # per evitare micro-freeze su uConsole quando arrivano passaggi
        # fitti in rapida successione.
        self._live_aggiorna_prev_passo_debounced()

    # Larghezza fissa colonna pilota in px (abbastanza per 10 char
    # monospace a 12pt circa). Cosi' con 1 pilota la colonna resta
    # piccola a sinistra senza stretchare a tutto schermo.
    _LIVE_COL_WIDTH = 130

    def _live_crea_colonna(self, pilot_num):
        """Crea una colonna per il nuovo pilota. Le colonne vengono
        impacchettate con pack(side='left') a larghezza fissa: con un
        solo pilota resta piccola a sinistra, con piu' piloti si
        affiancano da sx a dx. Il riordino GARA le rimescola."""
        c = self.colori
        colore = _LIVE_COLORS[pilot_num % len(_LIVE_COLORS)]
        nome_raw = self._live_mapping.get(pilot_num)
        nome_vis = _live_nome_display(nome_raw, pilot_num, max_len=10)

        col = tk.Frame(self._live_grid_frame, bg=c["sfondo_celle"],
                        relief="ridge", bd=1,
                        highlightbackground=colore, highlightthickness=1,
                        width=self._LIVE_COL_WIDTH)
        # pack_propagate False per forzare la width fissa (senza, la
        # Frame si ridimensiona ai figli)
        col.pack_propagate(False)
        col.pack(side="left", fill="y", padx=2, pady=2)

        # Header: nome pilota (grande) + numero trasponder (piccolo)
        tk.Label(col, text=nome_vis, bg=c["sfondo_celle"], fg=colore,
                 font=self._f_best, anchor="center").pack(
            fill="x", padx=2, pady=(3, 0))
        tk.Label(col, text="#%d" % pilot_num,
                 bg=c["sfondo_celle"], fg=c["testo_dim"],
                 font=self._f_status).pack()

        tk.Frame(col, bg=c["linee"], height=1).pack(
            fill="x", padx=2, pady=2)

        # Stats compatte (giri + best)
        stats_lbl = tk.Label(col, text="0g  B:--",
                 bg=c["sfondo_celle"], fg=c["testo_dim"],
                 font=self._f_status)
        stats_lbl.pack(fill="x", padx=2)

        # Ultimo tempo grande
        last_lbl = tk.Label(col, text="--",
                 bg=c["sfondo_celle"], fg=colore,
                 font=tkfont.Font(family=FONT_MONO, size=20, weight="bold"))
        last_lbl.pack(pady=(4, 4))

        tk.Frame(col, bg=c["linee"], height=1).pack(
            fill="x", padx=2, pady=2)

        # Lista ultimi giri (Text fissa: altezza 12 righe, newest al top,
        # NON scrollabile - i giri piu' vecchi di 12 cadono fuori).
        list_txt = tk.Text(col, bg=c["sfondo_celle"], fg=c["dati"],
                 font=self._f_status, relief="flat",
                 highlightthickness=0, bd=0,
                 wrap="none", cursor="arrow",
                 height=12)
        list_txt.pack(fill="x", padx=2, pady=(0, 3))
        list_txt.config(state="disabled")

        # Separatore + resoconto totale in fondo (totale giri + tempo).
        # Si popola solo quando ci sono giri validi.
        tk.Frame(col, bg=c["linee"], height=1).pack(
            fill="x", padx=2, pady=(2, 0), side="bottom")
        tot_lbl = tk.Label(col, text="0g  --",
                 bg=c["sfondo_celle"], fg=c["stato_avviso"],
                 font=self._f_status, anchor="center")
        tot_lbl.pack(fill="x", padx=2, pady=(1, 3), side="bottom")

        self._live_columns[pilot_num] = col
        self._live_colwidgets[pilot_num] = {
            "stats": stats_lbl, "last": last_lbl,
            "list": list_txt, "tot": tot_lbl, "color": colore,
        }

    def _live_aggiorna_colonna(self, pilot_num):
        """Aggiorna stats, ultimo tempo e lista della colonna pilota."""
        w = self._live_colwidgets.get(pilot_num)
        if not w:
            return
        data = self._live_pilots[pilot_num]
        validi = [l for l in data["laps"]
                   if l.get("stato") == "valido" and l.get("tempo", 0) > 0]
        if validi:
            tempi = [l["tempo"] for l in validi]
            best = min(tempi)
            ultimo = validi[-1]["tempo"]
            totale_tempo = sum(tempi)
            w["stats"].config(text="%dg  B:%s" % (len(validi), _fmt(best)))
            c = self.colori
            if ultimo == best:
                w["last"].config(text=_fmt(ultimo), fg=c["stato_ok"])
            elif ultimo <= best * 1.05:
                w["last"].config(text=_fmt(ultimo), fg=c["stato_avviso"])
            else:
                w["last"].config(text=_fmt(ultimo), fg=w["color"])
            # Resoconto totale: N giri + tempo cumulato (senza prefisso
            # "Tot:" per risparmiare spazio nelle colonne strette).
            # Il colore arancione gia' distingue la riga.
            tot_lbl = w.get("tot")
            if tot_lbl is not None:
                try:
                    tot_lbl.config(
                        text="%dg  %s" % (len(validi), _fmt(totale_tempo)))
                except Exception:
                    pass
        else:
            w["stats"].config(text="0g")
            w["last"].config(text="--")
            tot_lbl = w.get("tot")
            if tot_lbl is not None:
                try:
                    tot_lbl.config(text="0g  --")
                except Exception:
                    pass

        # Lista recente: ultimi 12 giri, dal piu' nuovo in cima.
        # Cancella e ricostruisce ogni volta cosi' il newest resta
        # sempre in posizione 1.0 (top), la tabella non scrolla mai.
        w["list"].config(state="normal")
        w["list"].delete("1.0", "end")
        recenti = data["laps"][-12:]
        recenti = list(reversed(recenti))
        for l in recenti:
            num = l.get("giro", 0)
            t = l.get("tempo", 0)
            if l.get("stato") == "parziale":
                riga = "%2d  --\n" % num
            else:
                riga = "%2d  %s\n" % (num, _fmt(t))
            w["list"].insert("end", riga)
        w["list"].config(state="disabled")

    def _live_riordina_colonne(self):
        """Ordina colonne per GARA: piu' giri prima; se parita',
        tempo totale minore. Leader sempre a sinistra.
        Usa pack_forget + re-pack in ordine per spostare le colonne.
        OTTIMIZZAZIONE: se l'ordine non e' cambiato dall'ultima volta,
        skippa il re-pack (costoso su uConsole ARM: provoca layout
        reflow visibile come micro-freeze ad ogni giro)."""
        def _sort_key(item):
            pid, d = item
            validi = [l for l in d["laps"]
                       if l.get("stato") == "valido" and l.get("tempo", 0) > 0]
            n = len(validi)
            tot = sum(l["tempo"] for l in validi)
            return (-n, tot)

        ordered = sorted(self._live_pilots.items(), key=_sort_key)
        ordered_ids = tuple(pid for pid, _ in ordered)
        # Skip se l'ordine non e' cambiato (caso piu' frequente: giro
        # successivo del leader che resta leader)
        if getattr(self, "_live_last_order", None) == ordered_ids:
            return
        self._live_last_order = ordered_ids

        # pack_forget su tutti, poi ri-pack a side="left" nell'ordine
        # corretto: la prima packata va piu' a sinistra.
        for pid, col in self._live_columns.items():
            try:
                col.pack_forget()
            except Exception:
                pass
        for pid, _ in ordered:
            col = self._live_columns.get(pid)
            if col:
                try:
                    col.pack(side="left", fill="y", padx=2, pady=2)
                except Exception:
                    pass

    def _live_aggiorna_prev_passo_debounced(self):
        """Throttle per i pannelli laterali: al massimo un refresh ogni
        800ms. Evita di ricostruire i Text widget di PREVISIONE+PASSO
        ad ogni giro quando i passaggi arrivano fitti (es. piloti
        veloci con tempi 20s su pista piccola = molti giri/minuto
        cumulati tra tutti i piloti). Su uConsole ARM il rebuild
        dei due Text e' visibile come micro-freeze."""
        if getattr(self, "_live_prev_passo_pending", False):
            return
        self._live_prev_passo_pending = True
        try:
            self.root.after(800, self._live_prev_passo_flush)
        except Exception:
            self._live_prev_passo_pending = False

    def _live_prev_passo_flush(self):
        self._live_prev_passo_pending = False
        try:
            self._live_aggiorna_prev_passo()
        except Exception:
            pass

    def _live_entra_analisi(self):
        """Primo ESC in LIVE+RUNNING: ferma il ricevitore BLE ma tiene
        la griglia visibile, stoppa il timer e mostra i bottoni di
        azione (SALVA / STAMPA / IA / ESCI) in fondo - equivalente della
        modalita' analisi di LapTimer manuale.
        Secondo ESC chiamera' _live_stop_e_salva."""
        # Ferma il client BLE cosi' non arrivano piu' giri
        cli = self._live_client
        if cli is not None:
            try:
                cli.stop()
            except Exception:
                pass
        # Non azzeriamo self._live_client subito: serve a _on_stop per
        # riconoscere che e' LIVE; lo azzera _live_stop_e_salva.

        # Stoppa il timer e registra il totale di sessione
        self.stato = self.FERMO
        try:
            self._totale = time.perf_counter() - self.t_start
        except Exception:
            self._totale = 0.0

        c = self.colori

        # Aggiorna label stato nell'header
        if hasattr(self, "_lbl_status"):
            try:
                self._lbl_status.config(
                    text=("LIVE FERMO - %d piloti, %d passaggi totali. "
                          "ESC = salva e esci")
                         % (len(self._live_pilots),
                            sum(len(d["laps"]) for d in self._live_pilots.values())),
                    fg=c["stato_avviso"])
            except Exception:
                pass

        # Bottoniera analisi in fondo (sopra lo status bar originale)
        try:
            if hasattr(self, "_live_analisi_bar") and self._live_analisi_bar.winfo_exists():
                return  # gia' creata, niente da fare
        except Exception:
            pass

        self._live_analisi_bar = tk.Frame(self.root, bg=c["sfondo"])
        # Impacchettiamo come side="bottom" prima della status bar
        self._live_analisi_bar.pack(fill="x", side="bottom",
                                     padx=20, pady=(2, 2))

        def _mk_btn(text, bg, fg, cmd):
            return tk.Button(self._live_analisi_bar, text=text,
                             font=self._f_status,
                             bg=bg, fg=fg, relief="ridge", bd=1,
                             cursor="hand2", command=cmd, padx=8)

        _mk_btn("SALVA E ESCI", c["stato_ok"], c["sfondo"],
                self._live_stop_e_salva).pack(side="left", padx=3)
        if _HAS_PRINT:
            _mk_btn("STAMPA", c["pulsanti_sfondo"], c["pulsanti_testo"],
                    self._stampa_termica).pack(side="left", padx=3)
        _mk_btn("ESCI SENZA SALVARE", c["stato_errore"], c["sfondo"],
                self._chiudi).pack(side="left", padx=3)

    def _live_stop_e_salva(self):
        """Ferma il client BLE e salva un file scouting per ogni
        pilota con almeno un giro valido. Chiamato da _on_stop in
        modalita' LIVE."""
        cli = self._live_client
        if cli is not None:
            try:
                cli.stop()
            except Exception:
                pass
            self._live_client = None

        scouting_dir = ""
        if self.dati_dir:
            # Se dati_dir contiene 'scouting' o e' dati/id_XXXX,
            # il parent e' sempre dati/
            parent = os.path.dirname(self.dati_dir.rstrip("/\\"))
            scouting_dir = os.path.join(parent, "scouting")
            try:
                os.makedirs(scouting_dir, exist_ok=True)
            except Exception:
                scouting_dir = ""

        salvate = 0
        if scouting_dir and self._live_pilots:
            now = datetime.now()
            data_str = now.strftime("%Y-%m-%d")
            ora_str = now.strftime("%H:%M:%S")
            ts_file = now.strftime("%Y%m%d_%H%M%S")

            for pnum, d in self._live_pilots.items():
                validi = [l for l in d["laps"]
                           if l.get("stato") == "valido" and l.get("tempo", 0) > 0]
                if not validi:
                    continue
                tempi = [l["tempo"] for l in validi]
                nome_raw = self._live_mapping.get(pnum)
                nome_out = nome_raw if nome_raw else "Trasp. %d" % pnum

                giri_out = []
                cumul = 0.0
                for i, l in enumerate(validi, start=1):
                    cumul += l["tempo"]
                    giri_out.append({
                        "giro": i, "tempo": l["tempo"],
                        "cumulativo": round(cumul, 3),
                        "stato": "valido", "segnalato": False,
                    })

                sess = {
                    "tipo": "lapmonitor",
                    "versione": __version__,
                    "setup": self.setup or "Ricevitore LapMonitor",
                    "pista": self.pista or "",
                    "record_id": "lapmon_%s_%d" % (ts_file, pnum),
                    "pilota": nome_out,
                    "trasponder": pnum,
                    "data": data_str,
                    "ora": ora_str,
                    "serbatoio_cc": 0,
                    "tempo_totale": round(sum(tempi), 3),
                    "num_giri": len(validi),
                    "num_giri_validi": len(validi),
                    "num_pit_stop": 0,
                    "miglior_tempo": round(min(tempi), 3),
                    "miglior_giro": tempi.index(min(tempi)) + 1,
                    "media": round(sum(tempi) / len(tempi), 3),
                    "sessione_carburante": False,
                    "giri": giri_out,
                }
                # setup_snapshot solo per il pilota del setup corrente
                try:
                    if (self.pilota and nome_out.lower() == self.pilota.lower()
                        and self.setup_snapshot):
                        sess["setup_snapshot"] = dict(self.setup_snapshot)
                except Exception:
                    pass

                fname = "lap_lapmonitor_%s_%d.json" % (ts_file, pnum)
                try:
                    with open(os.path.join(scouting_dir, fname),
                              "w", encoding="utf-8") as f:
                        json.dump(sess, f, ensure_ascii=False, indent=2)
                    salvate += 1
                except Exception:
                    pass

        # Chiudi LapTimer via on_close come gli altri path
        if self._on_close:
            try:
                self._pulisci()
            except Exception:
                pass
            self._on_close()
        elif not self._embedded:
            try:
                self.root.destroy()
            except Exception:
                pass

    # -----------------------------------------------------------------
    #  LOGICA TIMER
    # -----------------------------------------------------------------
    def _on_spazio(self, event=None):
        if self.stato == self.ATTESA:
            self._avvia()
        elif self.stato == self.RUNNING:
            # In LIVE multi-pilota i giri arrivano dal ricevitore BLE:
            # SPAZIO non deve aggiungere giri manuali alla colonna
            # mono-pilota (che tra l'altro non esiste piu').
            if self._live_mode:
                return
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
        self._lbl_status.config(text="SPAZIO = Giro  |  S = Stampa  |  ESC = Stop e Salva", fg=c["stato_ok"])
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
        # In LIVE questi widget vengono distrutti e messi a None:
        # proteggiamo la config per non far crashare _aggiorna_timer
        lbl_fuel = getattr(self, '_lbl_fuel', None)
        if lbl_fuel is not None:
            try:
                lbl_fuel.config(
                    text="%dcc | %d:%02d" % (self.serbatoio, min_int, sec_rest),
                    fg=fg)
            except (tk.TclError, Exception):
                pass
        # Aggiorna tempo totale trascorso hh:mm:ss.d (solo se widget esiste)
        lbl_totale = getattr(self, '_lbl_totale', None)
        if lbl_totale is not None:
            try:
                ore = int(totale_sec) // 3600
                resto = totale_sec - ore * 3600
                min_t = int(resto) // 60
                sec_t = resto - min_t * 60
                lbl_totale.config(
                    text="%02d:%02d:%04.1f" % (ore, min_t, sec_t))
            except (tk.TclError, Exception):
                pass

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
        self._aggiorna_passo()
        self._lock_tempo = max(1.0, self.miglior_tempo * 0.4)
        self._space_locked = True
        self.root.after(int(self._lock_tempo * 1000), self._sblocca_space)

    def _aggiorna_passo(self):
        """Aggiorna la label PASSO con la media degli ultimi 3 giri validi.
        Colore:
          - verde se il passo corrente e' vicino al best (entro +5%)
          - giallo se entro +10% dal best
          - rosso oltre +10% (ritmo in calo)
        """
        if not hasattr(self, '_lbl_passo'):
            return
        c = self.colori
        validi = [g["tempo"] for g in self.giri if g.get("stato") == "valido"]
        if not validi:
            self._lbl_passo.config(text="passo: --:--.--", fg=c["testo_dim"])
            return
        # Media ultimi 3 giri validi (se meno di 3, media di quelli che ci sono)
        ultimi = validi[-3:]
        passo = sum(ultimi) / len(ultimi)
        if self.miglior_tempo and self.miglior_tempo > 0:
            scarto = (passo - self.miglior_tempo) / self.miglior_tempo
            if scarto <= 0.05:
                fg = c["stato_ok"]
            elif scarto <= 0.10:
                fg = c["stato_avviso"]
            else:
                fg = c["stato_errore"]
        else:
            fg = c["stato_ok"]
        self._lbl_passo.config(text="passo: %s" % _fmt(passo), fg=fg)

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
        # In modalita' LIVE ESC fa due step come in manuale:
        # - primo ESC (da RUNNING): ferma BLE, entra in analisi LIVE,
        #   mostra bottoni in fondo (SALVA / ESCI SENZA SALVARE)
        # - secondo ESC (da FERMO): salva tutti i file e esce
        if self._live_mode:
            if self.stato == self.RUNNING:
                self._live_entra_analisi()
                return
            elif self.stato == self.FERMO:
                self._live_stop_e_salva()
                return
            elif self.stato == self.ATTESA:
                # Cronometro non ancora avviato: ESC = esci senza salvare
                if self._live_client is not None:
                    try: self._live_client.stop()
                    except Exception: pass
                self._chiudi()
                return
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
            # Cronometro non ancora avviato: ESC = esci
            self._chiudi()

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

        # Aggiorna passo gara finale (rimane visibile nel riepilogo)
        self._aggiorna_passo()

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
        # Info subtitle: pilota + setup (niente piu' serbatoio cc)
        info_txt = "%s  |  %s" % (self.pilota, self.setup)
        tk.Label(header, text=info_txt, bg=c["sfondo"], fg=c["label"],
                 font=self._f_info).pack(pady=(2, 0))
        # Barra batteria: place() sul TOPLEVEL (self.root), NON sull'header,
        # cosi' e' un overlay puro e non occupa spazio nel pack layout.
        _aggiungi_barra_bat(self.root)
        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, pady=(2, 0))

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
        # Tempo totale trascorso dalla partenza - GRANDE, leggibile a colpo d'occhio
        self._lbl_totale = tk.Label(timer_col, text="00:00:00.0",
                                     bg=c["sfondo"], fg=c["stato_ok"], font=self._f_totale)
        self._lbl_totale.pack(pady=(6, 0))
        # Passo gara: media ultimi 3 giri validi (indicatore di ritmo)
        self._lbl_passo = tk.Label(timer_col, text="passo: --:--.--",
                                    bg=c["sfondo"], fg=c["testo_dim"], font=self._f_passo)
        self._lbl_passo.pack(pady=(2, 0))

        # Proiezioni (DX) — stesse dimensioni del grafico SX
        self._proiezioni_w = self._grafico_w
        self._proiezioni_h = self._grafico_h
        self._proiezioni_canvas = tk.Canvas(top_area, bg=c.get("sfondo_celle", "#080808"),
                                             width=self._proiezioni_w, height=self._proiezioni_h,
                                             highlightthickness=1,
                                             highlightbackground=c.get("linee", "#1a3a1a"))
        self._proiezioni_canvas.grid(row=0, column=2, sticky="e", padx=(5, 10))

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=20, pady=(2, 0))

        # Griglia giri (header fisso + body scrollabile)
        self._grid_row_h = 22
        self._grid_rows_drawn = 0
        self._grid_header_canvas = tk.Canvas(self.root, bg=c["sfondo"],
                                              height=self._grid_row_h,
                                              highlightthickness=0, bd=0)
        self._grid_header_canvas.pack(fill="x", padx=20, pady=(2, 0))
        # Altezza griglia limitata su uConsole per lasciare spazio ai bottoni
        self._grid_body_h = self._calcola_altezza_griglia()
        self._grid_canvas = tk.Canvas(self.root, bg=c["sfondo"],
                                       height=self._grid_body_h,
                                       highlightthickness=0, bd=0)
        self._grid_canvas.pack(fill="x", padx=20, pady=(0, 2))
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
        """Stampa scheda cronometraggio su stampante termica BT.
        Utilizzabile anche durante il cronometraggio live: in quel caso il
        feedback viene mostrato sullo status bar in basso (non c'e' ancora
        la label di analisi). Se non ci sono giri, non fa nulla."""
        c = self.colori

        def _feedback(testo, colore):
            """Scrive messaggio nella label di analisi se presente, altrimenti
            nello status bar in basso (modalita live)."""
            if hasattr(self, '_lbl_res_status'):
                try:
                    self._lbl_res_status.config(text=testo, fg=colore)
                    self._lbl_res_status.update_idletasks()
                    return
                except Exception:
                    pass
            if hasattr(self, '_lbl_status'):
                try:
                    self._lbl_status.config(text=testo, fg=colore)
                    self._lbl_status.update_idletasks()
                except Exception:
                    pass

        if not _HAS_PRINT:
            _feedback("Modulo stampa non disponibile", c["stato_errore"])
            return

        # Nessun giro ancora: evita stampa vuota
        if not self.giri:
            _feedback("Nessun giro da stampare", c["stato_avviso"])
            return

        _feedback("Stampa in corso...", c["stato_avviso"])

        # Legge il MAC della stampante da conf.dat (come fa retrodb).
        # Se non configurato, usa "auto" (Windows: win32print cerca generico;
        # Linux: scan BT).
        mac = "auto"
        try:
            from conf_manager import carica_conf
            _conf = carica_conf()
            _mac = (_conf.get("stampante_bt", "") or "").strip()
            if _mac:
                mac = _mac
        except Exception:
            pass

        # Durante la gara _totale non e' ancora stato settato: lo calcolo al volo
        # cosi' la scheda stampata riporta il tempo totale corrente.
        if self.stato == self.RUNNING and hasattr(self, 't_start') and self.t_start:
            try:
                self._totale = time.perf_counter() - self.t_start
            except Exception:
                pass

        righe = self._genera_scheda_crono()

        def _stampa_thread():
            ok, msg = stampa_bluetooth(righe, mac)
            try:
                fg = c["stato_ok"] if ok else c["stato_errore"]
                _feedback(msg, fg)
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
