"""
TrackMind - Analizza Tempi v1.0
Add-on TrackMind: analisi interattiva sessioni cronometriche.
Funziona su dati LapTimer, SpeedHive e Scouting.

Lanciato da TrackMind in modalita' embedded (parent frame + on_close callback).

Flusso:
  1. Carica sessione (da path JSON o dict)
  2. Auto-classifica: PIT (>+10sec) + INCIDENTE? (>+20% mediana)
  3. Editing manuale: E=Escludi  P=Pit  V=Valido
  4. STRATEGIA: report gara completo con stint, consumo, pit
  5. SALVA: riscrive JSON con classificazione
"""

from version import __version__

import tkinter as tk
from tkinter import font as tkfont, ttk
import json, os, sys
from datetime import datetime

# Guardia anti-popup di sistema (uConsole).
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

# Font monospace per compatibilità cross-platform
try:
    from config_colori import FONT_MONO
except ImportError:
    import sys as _sys
    FONT_MONO = "Consolas" if _sys.platform == "win32" else "DejaVu Sans Mono"

# Importa classificatore da laptimer
try:
    from laptimer import classifica_giri
except ImportError:
    classifica_giri = None

# Barra batteria (opzionale)
try:
    from core.batteria import aggiungi_barra_batteria as _aggiungi_barra_bat
except Exception:
    def _aggiungi_barra_bat(*args, **kwargs):
        return None

# Analisi IA (opzionale)
try:
    from ai_analisi import AIAnalisi
    _HAS_AI = True
except ImportError:
    _HAS_AI = False

# ---------------------------------------------------------------------
#  COLORI (legge colori.cfg di RetroDB se presente)
# ---------------------------------------------------------------------
DEFAULT_COLORS = {
    "sfondo":           "#0a0a0a",
    "sfondo_celle":     "#0f0f0f",
    "dati":             "#39ff14",
    "label":            "#22aa22",
    "testo_dim":        "#1a6a1a",
    "testo_cursore":    "#0a0a0a",
    "cursore":          "#39ff14",
    "stato_ok":         "#39ff14",
    "stato_avviso":     "#ffaa00",
    "stato_errore":     "#ff5555",
    "linee":            "#1a5a0a",
    "pulsanti_sfondo":  "#1a3a1a",
    "pulsanti_testo":   "#39ff14",
    "bordo_vuote":      "#1a3a1a",
    "cerca_sfondo":     "#1a1a3a",
    "cerca_testo":      "#6688ff",
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
    """MM:SS.cc"""
    if secondi is None or secondi == 0:
        return "--"
    if secondi < 0:
        return "-" + _fmt(-secondi)
    m = int(secondi) // 60
    s = secondi - m * 60
    return "%02d:%05.2f" % (m, s)

def _fmt_delta(delta):
    """+0.15 o -0.42"""
    if delta is None or abs(delta) < 0.005:
        return ""
    return "%+.2f" % delta


# ---------------------------------------------------------------------
#  DURATE GARA standard RC 1/8
# ---------------------------------------------------------------------
DURATE_GARA = [20, 30, 45, 60, 90]


# ---------------------------------------------------------------------
#  CALCOLO STRATEGIA GARA (con ultimo pit smart)
# ---------------------------------------------------------------------
def calcola_strategia(durata_gara, media, autonomia_min, giri_sicuri,
                      consumo_min, serbatoio, media_pit):
    """Calcola strategia gara RC.
    Logica: pit SEMPRE pieno. L'ultimo pit e' anticipato cosi'
    lo stint finale copre quasi tutta l'autonomia → arrivi vuoto.
    
    Ritorna dict con tutti i dati calcolati."""
    if media <= 0 or autonomia_min <= 0:
        return None

    race_sec = durata_gara * 60.0
    autonomia_sec = autonomia_min * 60.0
    stint_sec = giri_sicuri * media  # durata stint pieno
    ciclo_sec = stint_sec + media_pit  # stint + pit

    # Se non servono pit
    if race_sec <= autonomia_sec:
        giri_totali = int(race_sec / media)
        return {
            "durata": durata_gara, "n_pit": 0,
            "giri_sicuri": giri_sicuri, "giri_totali": giri_totali,
            "autonomia_min": autonomia_min,
            "giri_finali": giri_totali, "fuel_finale": serbatoio,
            "fuel_pct": 100, "ultimo_pit_tempo": 0,
            "tempo_perso_totale": 0,
            "chiamate": [{"tipo": "finale", "giri": giri_totali}],
        }

    # Ultimo pit: calcolato in modo che dopo il pit exit
    # lo stint finale copra quasi tutta l'autonomia
    last_pit_exit = race_sec - autonomia_sec  # tempo pit exit
    last_pit_entry = last_pit_exit - media_pit  # tempo pit entry

    if last_pit_entry <= 0:
        # Gara troppo corta per anche un solo pit
        giri_totali = int(race_sec / media)
        return {
            "durata": durata_gara, "n_pit": 0,
            "giri_sicuri": giri_sicuri, "giri_totali": giri_totali,
            "autonomia_min": autonomia_min,
            "giri_finali": giri_totali, "fuel_finale": serbatoio,
            "fuel_pct": 100, "ultimo_pit_tempo": 0,
            "tempo_perso_totale": 0,
            "chiamate": [{"tipo": "finale", "giri": giri_totali}],
        }

    # Quanti stint pieni ci stanno prima dell'ultimo pit?
    n_full = int(last_pit_entry / ciclo_sec)
    tempo_dopo_full = n_full * ciclo_sec

    # Stint corto prima dell'ultimo pit (puo' essere < giri_sicuri)
    tempo_short = last_pit_entry - tempo_dopo_full
    giri_short = max(1, int(tempo_short / media))

    # Totale pit = n_full + 1 (ultimo)
    n_pit = n_full + 1

    # Giri finale (stint lungo, quasi tutta l'autonomia)
    tempo_dopo_ultimo_pit = race_sec - (tempo_dopo_full + giri_short * media + media_pit)
    giri_finali = int(tempo_dopo_ultimo_pit / media)

    # Fuel usato nel finale
    fuel_usato = min((giri_finali * media / 60.0) * consumo_min, serbatoio)
    fuel_pct = round(fuel_usato / serbatoio * 100, 0) if serbatoio > 0 else 0
    fuel_rimasto = max(0, serbatoio - fuel_usato)

    # Giri totali
    giri_totali = n_full * giri_sicuri + giri_short + giri_finali

    # Tempo perso per pit
    tempo_perso_per_pit = (media_pit - media) if media_pit > media else media_pit
    tempo_perso_totale = n_pit * tempo_perso_per_pit

    # Costruisci tabella chiamate pit
    chiamate = []
    t_acc = 0.0
    g_acc = 0
    for i in range(n_pit):
        if i < n_full:
            # Pit pieno dopo stint pieno
            g_acc += giri_sicuri
            t_acc += giri_sicuri * media
            chiamate.append({
                "tipo": "pieno", "num": i + 1,
                "giro": g_acc, "tempo": t_acc,
                "pit_tempo": media_pit, "fuel": serbatoio,
            })
            t_acc += media_pit
        else:
            # Ultimo pit: anticipato, sempre pieno
            g_acc += giri_short
            t_acc += giri_short * media
            chiamate.append({
                "tipo": "anticipato", "num": i + 1,
                "giro": g_acc, "tempo": t_acc,
                "giri_stint": giri_short,
                "pit_tempo": media_pit, "fuel": serbatoio,
            })
            t_acc += media_pit

    # Finale
    chiamate.append({
        "tipo": "finale", "giri": giri_finali,
        "fuel_usato": round(fuel_usato, 0),
        "fuel_pct": fuel_pct,
        "fuel_rimasto": round(max(0, fuel_rimasto), 0),
    })

    return {
        "durata": durata_gara,
        "n_pit": n_pit,
        "giri_sicuri": giri_sicuri,
        "autonomia_min": autonomia_min,
        "giri_short": giri_short,
        "giri_finali": giri_finali,
        "fuel_usato": round(fuel_usato, 0),
        "fuel_pct": fuel_pct,
        "fuel_rimasto": round(max(0, fuel_rimasto), 0),
        "ultimo_pit_tempo": media_pit,
        "tempo_perso_totale": tempo_perso_totale,
        "giri_totali": giri_totali,
        "chiamate": chiamate,
    }

def calcola_stint(giri):
    """Divide i giri in stint separati dai PIT STOP.
    Solo gli stint COMPLETI (chiusi da un PIT) contano per il carburante.
    Se non ci sono PIT, l'intera sessione e' un unico stint completo."""
    stint_list = []
    stint_corrente = []
    ha_pit = any(g.get("stato") == "pit" for g in giri)

    for g in giri:
        stato = g.get("stato", "valido")
        if stato == "pit":
            if stint_corrente:
                dur = sum(gi["tempo"] for gi in stint_corrente)
                stint_list.append({"giri": stint_corrente, "durata": dur,
                                   "n_giri": len(stint_corrente), "completo": True})
            stint_corrente = []
        elif stato == "valido":
            stint_corrente.append(g)

    if stint_corrente:
        dur = sum(gi["tempo"] for gi in stint_corrente)
        stint_list.append({"giri": stint_corrente, "durata": dur,
                           "n_giri": len(stint_corrente), "completo": not ha_pit})
    return stint_list


# =====================================================================
#  CLASSE PRINCIPALE: AnalizzaTempi
# =====================================================================
class AnalizzaTempi:
    """Editor interattivo per analisi sessioni cronometriche.
    Modalita' embedded: riceve parent frame e callback on_close."""

    def __init__(self, sessione, path, parent=None, on_close=None):
        self.sessione = sessione
        self.path = path
        self._on_close = on_close
        self._embedded = parent is not None

        self.giri = sessione.get("giri", [])
        self.serbatoio = sessione.get("serbatoio_cc", 0)
        self.fuel_valido = sessione.get("sessione_carburante", True)

        # Inizializza stati se mancanti
        for g in self.giri:
            if "stato" not in g:
                g["stato"] = "valido"
            if "segnalato" not in g:
                g["segnalato"] = False

        self.colori = _carica_colori()
        self._init_root(parent)
        self._init_fonts()
        self._schermata_analisi()

    def _init_root(self, parent=None):
        c = self.colori
        if parent:
            self.root = parent
        else:
            self.root = tk.Tk()
            self.root.title(f"TrackMind - Analizza Tempi  v{__version__}")
            self.root.attributes("-fullscreen", True)
        self.root.configure(bg=c["sfondo"])
        # uConsole: anti popup di sistema (idempotente)
        _proteggi_finestra(self.root)

    def _chiudi(self):
        if self._on_close:
            self._pulisci()
            self._on_close()
        elif not self._embedded:
            self.root.destroy()

    def _init_fonts(self):
        self._f_title = tkfont.Font(family=FONT_MONO, size=14, weight="bold")
        self._f_info  = tkfont.Font(family=FONT_MONO, size=12)
        self._f_list  = tkfont.Font(family=FONT_MONO, size=11)
        self._f_btn   = tkfont.Font(family=FONT_MONO, size=11, weight="bold")
        self._f_small = tkfont.Font(family=FONT_MONO, size=10)
        self._f_fuel  = tkfont.Font(family=FONT_MONO, size=16, weight="bold")
        self._f_status = tkfont.Font(family=FONT_MONO, size=10)

    def _pulisci(self):
        for w in self.root.winfo_children():
            w.destroy()
        top = self.root.winfo_toplevel()
        for k in ("<e>", "<p>", "<v>", "<a>", "<s>",
                  "<Control-s>", "<Control-S>",
                  "<Escape>", "<Up>", "<Down>"):
            try: top.unbind(k)
            except: pass

    # =================================================================
    #  SCHERMATA ANALISI (editor giri)
    # =================================================================
    def _schermata_analisi(self):
        self._pulisci()
        c = self.colori

        # Auto-classificazione (solo prima volta)
        auto = 0
        if classifica_giri and all(g.get("stato") == "valido" for g in self.giri):
            auto = classifica_giri(self.giri)

        # Header
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< INDIETRO", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._chiudi).pack(side="left")
        setup = self.sessione.get("setup", "?")
        data = self.sessione.get("data", "?")
        ora = self.sessione.get("ora", "?")[:5]
        pilota = self.sessione.get("pilota", "?")
        tk.Label(header, text="  ANALISI  %s  |  %s  |  %s %s" % (pilota, setup, data, ora),
                 bg=c["sfondo"], fg=c["dati"], font=self._f_title).pack(side="left", padx=(8, 0))
        # Barra batteria in alto a destra (overlay)
        _aggiungi_barra_bat(header)

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(4, 0))

        # Pannello stats (aggiornabile)
        stats = tk.Frame(self.root, bg=c["sfondo"])
        stats.pack(fill="x", padx=10, pady=(4, 0))
        r1 = tk.Frame(stats, bg=c["sfondo"])
        r1.pack(fill="x")
        self._lbl_giri = tk.Label(r1, text="", bg=c["sfondo"], fg=c["dati"],
                                   font=self._f_info, anchor="w")
        self._lbl_giri.pack(side="left", padx=(0, 12))
        self._lbl_best = tk.Label(r1, text="", bg=c["sfondo"], fg=c["stato_avviso"],
                                   font=self._f_info, anchor="w")
        self._lbl_best.pack(side="left", padx=(0, 12))
        self._lbl_media = tk.Label(r1, text="", bg=c["sfondo"], fg=c["dati"],
                                    font=self._f_info, anchor="w")
        self._lbl_media.pack(side="left")

        r2 = tk.Frame(stats, bg=c["sfondo"])
        r2.pack(fill="x", pady=(2, 0))
        self._lbl_fuel = tk.Label(r2, text="", bg=c["sfondo"], fg=c["dati"],
                                   font=self._f_info, anchor="w")
        self._lbl_fuel.pack(side="left", padx=(0, 12))
        self._lbl_pit = tk.Label(r2, text="", bg=c["sfondo"], fg=c["stato_avviso"],
                                  font=self._f_info, anchor="w")
        self._lbl_pit.pack(side="left")

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(4, 2))

        # Treeview giri
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("AT.Treeview",
            background=c["sfondo"], foreground=c["dati"],
            fieldbackground=c["sfondo"], font=(FONT_MONO, 11),
            rowheight=22, borderwidth=0)
        style.configure("AT.Treeview.Heading",
            background=c["pulsanti_sfondo"], foreground=c["pulsanti_testo"],
            font=(FONT_MONO, 10, "bold"), borderwidth=1, relief="ridge")
        style.map("AT.Treeview",
            background=[("selected", c["cursore"])],
            foreground=[("selected", c["testo_cursore"])])

        tree_frame = tk.Frame(self.root, bg=c["sfondo"])
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(2, 2))
        cols = ("giro", "tempo", "delta", "stato")
        self._tree = ttk.Treeview(tree_frame, columns=cols,
                                   show="headings", style="AT.Treeview", selectmode="browse")
        self._tree.heading("giro", text="GIRO", anchor="w")
        self._tree.heading("tempo", text="TEMPO", anchor="e")
        self._tree.heading("delta", text="DELTA", anchor="e")
        self._tree.heading("stato", text="STATO", anchor="center")
        self._tree.column("giro", width=60, anchor="w")
        self._tree.column("tempo", width=110, anchor="e")
        self._tree.column("delta", width=90, anchor="e")
        self._tree.column("stato", width=110, anchor="center")

        vsb = tk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        vsb.pack(side="right", fill="y")
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)

        self._tree.tag_configure("valido", foreground=c["dati"])
        self._tree.tag_configure("segnalato", foreground=c["stato_errore"])
        self._tree.tag_configure("escluso", foreground=c["testo_dim"])
        self._tree.tag_configure("pit", foreground=c["stato_avviso"])
        self._tree.tag_configure("best", foreground=c["stato_avviso"])

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(2, 2))

        # Bottoni
        bar = tk.Frame(self.root, bg=c["sfondo"])
        bar.pack(pady=(3, 3))
        for txt, fg, cmd in [
            ("AUTO\nA", c["stato_avviso"], self._auto),
            ("ESCLUDI\nE", c["stato_errore"], lambda: self._toggle("escluso")),
            ("PIT\nP", c["stato_avviso"], lambda: self._toggle("pit")),
            ("VALIDO\nV", c["stato_ok"], lambda: self._toggle("valido")),
            ("SALVA\nCtrl+S", c["stato_ok"], self._salva)]:
            b = tk.Button(bar, text=txt, font=self._f_small, width=9,
                      bg=c["pulsanti_sfondo"], fg=fg,
                      relief="ridge", bd=1, cursor="hand2", command=cmd)
            b.pack(side="left", padx=2)

        # Status
        hint = "E=Escludi  P=Pit  V=Valido  A=Auto"
        if auto:
            hint = "Auto: %d giri classificati | %s" % (auto, hint)
        self._lbl_status = tk.Label(self.root, text=hint,
                 bg=c["sfondo"], fg=c["testo_dim"], font=self._f_status, anchor="w")
        self._lbl_status.pack(fill="x", padx=10, pady=(0, 4))

        # Shortcut (sul toplevel per funzionare in embedded)
        top = self.root.winfo_toplevel()
        top.bind("<e>", lambda e: self._toggle("escluso"))
        top.bind("<p>", lambda e: self._toggle("pit"))
        top.bind("<v>", lambda e: self._toggle("valido"))
        top.bind("<a>", lambda e: self._auto())
        top.bind("<Control-s>", lambda e: self._salva())
        top.bind("<Control-S>", lambda e: self._salva())
        top.bind("<Escape>", lambda e: self._chiudi())

        # Popola e calcola
        self._popola()
        self._ricalcola()

        children = self._tree.get_children()
        if children:
            self._tree.selection_set(children[0])
            self._tree.focus(children[0])
        self._tree.focus_set()

    # -----------------------------------------------------------------
    #  POPOLA TREEVIEW
    # -----------------------------------------------------------------
    def _popola(self):
        self._tree.delete(*self._tree.get_children())
        validi = [g["tempo"] for g in self.giri if g.get("stato") == "valido"]
        best = min(validi) if validi else None

        for g in self.giri:
            stato = g.get("stato", "valido")
            segnalato = g.get("segnalato", False)
            tempo = g.get("tempo", 0)
            num = g.get("giro", g.get("numero", "?"))
            delta = g.get("delta", g.get("delta_best", None))
            delta_txt = _fmt_delta(delta) if delta else ""

            stato_display = {"valido": "VALIDO", "escluso": "ESCLUSO", "pit": "PIT STOP"}.get(stato, stato)
            tag = stato
            if stato == "valido" and best and abs(tempo - best) < 0.001:
                tag = "best"; stato_display = "* BEST *"
            elif stato == "valido" and segnalato:
                tag = "segnalato"; stato_display = "INCIDENTE?"

            self._tree.insert("", "end", iid=str(num),
                values=(num, _fmt(tempo), delta_txt, stato_display), tags=(tag,))

    # -----------------------------------------------------------------
    #  TOGGLE STATO
    # -----------------------------------------------------------------
    def _toggle(self, nuovo_stato):
        sel = self._tree.selection()
        if not sel:
            return
        iid = sel[0]
        for g in self.giri:
            gn = str(g.get("giro", g.get("numero", "")))
            if gn == iid:
                vecchio = g.get("stato", "valido")
                g["stato"] = "valido" if vecchio == nuovo_stato else nuovo_stato
                # Se l'utente mette VALIDO manualmente, pulisci il flag segnalato
                if g["stato"] == "valido":
                    g["segnalato"] = False
                break

        self._popola()
        self._ricalcola()

        # Avanza al prossimo
        children = self._tree.get_children()
        try:
            idx = list(children).index(iid)
            next_idx = min(idx + 1, len(children) - 1)
        except ValueError:
            next_idx = 0
        if children:
            self._tree.selection_set(children[next_idx])
            self._tree.focus(children[next_idx])
            self._tree.see(children[next_idx])

    # -----------------------------------------------------------------
    #  AUTO-CLASSIFICA
    # -----------------------------------------------------------------
    def _auto(self):
        c = self.colori
        for g in self.giri:
            g["stato"] = "valido"
            g["segnalato"] = False
        n = classifica_giri(self.giri) if classifica_giri else 0
        self._popola()
        self._ricalcola()
        if n:
            self._lbl_status.config(text="Auto: %d giri classificati. Rivedi e premi SALVA." % n,
                                     fg=c["stato_avviso"])
        else:
            self._lbl_status.config(text="Nessuna anomalia rilevata.", fg=c["stato_ok"])
        children = self._tree.get_children()
        if children:
            self._tree.selection_set(children[0])
            self._tree.focus(children[0])
        self._tree.focus_set()

    # -----------------------------------------------------------------
    #  RICALCOLA STATS
    # -----------------------------------------------------------------
    def _ricalcola(self):
        c = self.colori
        validi = [g for g in self.giri if g.get("stato") == "valido"]
        pit_g = [g for g in self.giri if g.get("stato") == "pit"]
        tempi_v = [g["tempo"] for g in validi]
        tempi_pit = [g["tempo"] for g in pit_g]

        n_val = len(validi); n_tot = len(self.giri)
        media = sum(tempi_v) / n_val if tempi_v else 0
        best = min(tempi_v) if tempi_v else 0
        best_idx = 0
        if tempi_v:
            bv = min(tempi_v)
            for g in validi:
                if abs(g["tempo"] - bv) < 0.001:
                    best_idx = g.get("giro", g.get("numero", 0)); break

        self._lbl_giri.config(text="Validi: %d/%d" % (n_val, n_tot))
        self._lbl_best.config(text="Best: %s (giro %s)" % (_fmt(best), best_idx) if best else "Best: ---")
        self._lbl_media.config(text="Passo: %s" % (_fmt(media) if media else "---"))

        # Fuel stint-based
        if self.fuel_valido and self.serbatoio:
            stint_list = calcola_stint(self.giri)
            completi = [s for s in stint_list if s["completo"]]
            if completi:
                durate = [s["durata"] / 60.0 for s in completi]
                m_stint = sum(durate) / len(durate)
                cons = self.serbatoio / m_stint if m_stint > 0 else 0
                self._lbl_fuel.config(text="%dcc  stint %.1f min  %.1f cc/min (%d/%d stint)" % (
                    self.serbatoio, m_stint, cons, len(completi), len(stint_list)), fg=c["dati"])
            else:
                self._lbl_fuel.config(text="%dcc  (nessun stint completo)" % self.serbatoio,
                                       fg=c["stato_avviso"])
        else:
            self._lbl_fuel.config(text="No fuel", fg=c["testo_dim"])

        if tempi_pit:
            mp = sum(tempi_pit) / len(tempi_pit)
            self._lbl_pit.config(text="Pit: %d  media %s" % (len(tempi_pit), _fmt(mp)))
        else:
            self._lbl_pit.config(text="Pit: 0")

    # -----------------------------------------------------------------
    #  SALVA
    # -----------------------------------------------------------------
    def _salva(self):
        c = self.colori
        self.sessione["giri"] = self.giri
        validi = [g for g in self.giri if g.get("stato") == "valido"]
        pit_g = [g for g in self.giri if g.get("stato") == "pit"]
        tempi_v = [g["tempo"] for g in validi]
        if tempi_v:
            self.sessione["miglior_tempo"] = round(min(tempi_v), 3)
            self.sessione["media"] = round(sum(tempi_v) / len(tempi_v), 3)
        self.sessione["num_giri_validi"] = len(validi)
        self.sessione["num_pit_stop"] = len(pit_g)
        if pit_g:
            self.sessione["media_pit"] = round(
                sum(g["tempo"] for g in pit_g) / len(pit_g), 3)
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.sessione, f, ensure_ascii=False, indent=2)
            self._lbl_status.config(text="Salvato!", fg=c["stato_ok"])
        except Exception as e:
            self._lbl_status.config(text="Errore: %s" % e, fg=c["stato_errore"])

    # =================================================================
    #  ANALISI IA
    # =================================================================
    def _lancia_ai(self):
        """Lancia l'analisi IA con Claude, include strategia gara."""
        # Calcola strategia per la durata selezionata
        strategia = None
        validi = [g for g in self.giri if g.get("stato") == "valido"]
        pit_g = [g for g in self.giri if g.get("stato") == "pit"]
        tempi_v = [g["tempo"] for g in validi]
        tempi_pit = [g["tempo"] for g in pit_g]
        media = sum(tempi_v) / len(tempi_v) if tempi_v else 0
        media_pit = sum(tempi_pit) / len(tempi_pit) if tempi_pit else 0
        stint_list = calcola_stint(self.giri)
        completi = [s for s in stint_list if s["completo"]]
        if completi and self.fuel_valido and self.serbatoio and media > 0:
            durate = [s["durata"] / 60.0 for s in completi]
            autonomia_min = sum(durate) / len(durate)
            consumo_min = self.serbatoio / autonomia_min if autonomia_min > 0 else 0
            rientro_sicuro = autonomia_min - (media / 60.0)
            giri_sicuri = int(rientro_sicuro * 60 / media)
            durata_sel = getattr(self, '_ultima_durata', 30)
            strategia = calcola_strategia(
                durata_sel, media, autonomia_min, giri_sicuri,
                consumo_min, self.serbatoio, media_pit)

        # Carica storico
        storico = []
        if self.path:
            dati_dir = os.path.dirname(self.path)
            nome_file = os.path.basename(self.path)
            # Cerca sessioni con stesso prefisso (stesso record_id)
            # Formato: lap_{record_id}_{timestamp}.json
            parti = nome_file.split("_")
            if len(parti) >= 2:
                # Prova a ricostruire il prefisso record_id
                prefisso = None
                if nome_file.startswith("lap_"):
                    # lap_rec_3_20260402_114034.json → prefisso = "lap_rec_3_"
                    # Cerca tutti i file che iniziano con "lap_" nella stessa dir
                    for f in sorted(os.listdir(dati_dir)):
                        if f.endswith(".json") and f != nome_file and f.startswith("lap_"):
                            try:
                                with open(os.path.join(dati_dir, f), "r", encoding="utf-8") as fh:
                                    s = json.load(fh)
                                # Solo se stesso setup
                                if s.get("setup") == self.sessione.get("setup"):
                                    storico.append(s)
                            except Exception:
                                pass

        self._pulisci()
        AIAnalisi(self.sessione, self.path, storico=storico,
                  strategia=strategia,
                  parent=self.root, on_close=self._schermata_analisi)

    # =================================================================
    #  RUN (standalone)
    # =================================================================
    def run(self):
        self.root.mainloop()
