"""core/sd_bar.py - Pannello strumenti micro-SD a LED segmentati.

Tre widget LED retro stile WarGames/LCD, interscambiabili nell'aspetto
ma con sorgenti dati diverse:

    BarraSD     -> capienza disco (% libero)            refresh 30s
    BarraUsura  -> usura stimata (GB scritti / TBW)     refresh 30s
                   tenta ext_csd reale in background
    BarraIO     -> VU meter I/O in tempo reale (MB/s)   refresh 1s

Tutte e tre condividono l'aspetto:

    [XX] [##############  ]  NNN

      etichetta (2-3 car)   LED colorati verde->giallo->rosso   valore dx

Sul layer dati si appoggiano a core/sd_health.py (I/O puro, niente UI).

USO TIPICO in retrodb.py:

    from core.sd_bar import BarraSD, BarraUsura, BarraIO

    BarraSD(header).place(relx=0, rely=0, x=0, y=2, anchor="nw")
    BarraUsura(header, tbw_gb=30000,
               file_stato="dati/sd_wear.json").place(
        relx=0, rely=0, x=0, y=22, anchor="nw")
    BarraIO(header).place(relx=0, rely=0, x=0, y=42, anchor="nw")

Su Windows / sistemi senza micro-SD BarraUsura e BarraIO mostrano "---"
e barra spenta; BarraSD continua a funzionare normalmente su qualsiasi
disco.

Progettazione retrocompatibile: la firma di BarraSD e' identica alla
versione precedente; il nuovo codice e' additivo.
"""

import os
import sys
import shutil
import tkinter as tk

# Font e colori coerenti con il resto di TrackMind
try:
    from config_colori import FONT_MONO, carica_colori as _carica_colori
except Exception:
    FONT_MONO = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"
    def _carica_colori():
        return {
            "sfondo": "#0a0a0a", "linee": "#144a14", "dati": "#39ff14",
            "testo_dim": "#1a6a1a", "stato_ok": "#39ff14",
            "stato_avviso": "#ffaa00", "stato_errore": "#ff5555",
        }

# Scaling DPI (se tm_field e' disponibile usiamo la sua scala)
try:
    from tm_field import get_scala as _get_scala
except Exception:
    def _get_scala():
        return 1.0

# Layer dati (può mancare nelle versioni vecchie: importo difensivo)
try:
    from core.sd_health import (StatoUsura as _StatoUsura,
                                prova_ext_csd as _prova_ext_csd,
                                trova_dev_sd as _trova_dev_sd,
                                leggi_stat as _leggi_stat)
    _HEALTH_DISPONIBILE = True
except Exception:
    _StatoUsura = None
    _prova_ext_csd = lambda dev=None: None
    _trova_dev_sd = lambda: None
    _leggi_stat = lambda dev: None
    _HEALTH_DISPONIBILE = False

# Livelli di "allarme" (per colorare etichetta/testo)
_LIV_OK = 0
_LIV_AVVISO = 1
_LIV_CRITICO = 2


def _path_default():
    """Path di riferimento per la capienza (drive/partizione di TrackMind)."""
    try:
        qui = os.path.dirname(os.path.abspath(__file__))
        if sys.platform == "win32":
            drive, _ = os.path.splitdrive(qui)
            return drive + os.sep if drive else "C:\\"
        return "/"
    except Exception:
        return "C:\\" if sys.platform == "win32" else "/"


# =====================================================================
#  BASE: BarraLED - rendering di una riga di LED segmentati
# =====================================================================
class _BarraLEDBase(tk.Frame):
    """Base riusabile: etichetta sx + N LED + testo dx.

    Le sottoclassi implementano `_leggi_dati()` che ritorna il dict:
        {"pct_acceso": int 0-100,
         "testo_dx":   str,
         "livello":    _LIV_OK | _LIV_AVVISO | _LIV_CRITICO,
         "disponibile": bool}
    """

    # Soglie di posizione per il cambio colore dei LED accesi
    _SOGLIA_GIALLO_LED = 0.625
    _SOGLIA_ROSSO_LED  = 0.8125

    def __init__(self, master, etichetta="SD",
                 n_segmenti=16, intervallo_ms=30000,
                 scala=None, largh_testo_dx=6, **kw):
        if scala is None:
            try:
                scala = _get_scala()
            except Exception:
                scala = 1.0
        self._scala = scala
        self._n_seg = max(4, int(n_segmenti))
        self._intervallo_ms = max(500, int(intervallo_ms))
        self._after_id = None
        self._tip = None

        c = _carica_colori()
        kw.setdefault("bg", c["sfondo"])
        # IMPORTANTE: takefocus=0 sul Frame della barra cosi' non entra nel
        # traversal Tab e non ruba il focus ai bottoni del menu.
        kw.setdefault("takefocus", 0)
        super().__init__(master, **kw)

        # Dimensioni LED
        self._w_seg = max(5, int(round(7 * scala)))
        self._h_seg = max(9, int(round(12 * scala)))
        self._gap   = max(1, int(round(2 * scala)))

        # Font etichette - usiamo larghezza carattere fissa per allineare
        # piu' barre impilate (etichette sempre a 3 caratteri visivi).
        f_size = max(8, int(round(10 * scala)))
        self._font = (FONT_MONO, f_size, "bold")

        # Etichetta sx (2-3 car fissi, allineato w)
        self._lbl = tk.Label(self, text="%-3s" % etichetta[:3],
                             bg=c["sfondo"], fg=c["testo_dim"],
                             font=self._font, anchor="w", width=3,
                             takefocus=0)
        self._lbl.pack(side="left", padx=(0, max(3, int(4 * scala))))

        # Canvas con i LED. takefocus=0 per non rubare il focus via Tab.
        cw = self._n_seg * (self._w_seg + self._gap) + self._gap
        ch = self._h_seg + 2 * self._gap
        self._canvas = tk.Canvas(self, width=cw, height=ch,
                                 bg=c["sfondo"], highlightthickness=1,
                                 highlightbackground=c["linee"], bd=0,
                                 takefocus=0)
        self._canvas.pack(side="left")

        # Testo dx (valore numerico o rate)
        self._lbl_dx = tk.Label(self, text="---", bg=c["sfondo"],
                                fg=c["testo_dim"], font=self._font,
                                width=largh_testo_dx, anchor="w",
                                takefocus=0)
        self._lbl_dx.pack(side="left", padx=(max(3, int(4 * scala)), 0))

        # Tooltip al passaggio del mouse
        self._canvas.bind("<Enter>", self._mostra_tooltip)
        self._canvas.bind("<Leave>", self._nascondi_tooltip)

        # Cleanup quando viene distrutta
        self.bind("<Destroy>", lambda e: self.ferma())

        self.aggiorna()

    # ---------------------------------------------------------------
    #  API da sovrascrivere nelle sottoclassi
    # ---------------------------------------------------------------
    def _leggi_dati(self):
        """Ritorna dict con keys: pct_acceso, testo_dx, livello, disponibile.
        Deve essere sovrascritto."""
        return {"pct_acceso": 0, "testo_dx": "---",
                "livello": _LIV_OK, "disponibile": False}

    def _tooltip_testo(self):
        """Testo del tooltip. Opzionale, ritorna None per nessun tooltip."""
        return None

    # ---------------------------------------------------------------
    #  RENDERING
    # ---------------------------------------------------------------
    def _colore_led(self, idx, acceso, c):
        if not acceso:
            return c["linee"]
        sg = int(self._n_seg * self._SOGLIA_GIALLO_LED)
        sr = int(self._n_seg * self._SOGLIA_ROSSO_LED)
        if idx < sg:
            return c["stato_ok"]
        if idx < sr:
            return c["stato_avviso"]
        return c["stato_errore"]

    def _colore_livello(self, livello, c):
        if livello == _LIV_CRITICO:
            return c["stato_errore"]
        if livello == _LIV_AVVISO:
            return c["stato_avviso"]
        return c["testo_dim"]   # OK = dim verde (non troppo urlato)

    def aggiorna(self):
        c = _carica_colori()
        try:
            d = self._leggi_dati() or {}
        except Exception:
            d = {}
        pct = int(d.get("pct_acceso", 0))
        if pct < 0: pct = 0
        if pct > 100: pct = 100
        testo = str(d.get("testo_dx", "---"))
        livello = int(d.get("livello", _LIV_OK))

        # Numero LED accesi
        acc = int(round(self._n_seg * pct / 100.0))
        if pct > 0 and acc == 0:
            acc = 1
        if pct >= 100:
            acc = self._n_seg

        # Ridisegna canvas
        try:
            self._canvas.delete("all")
        except Exception:
            return  # widget gia' distrutto
        x = self._gap
        y = self._gap
        for i in range(self._n_seg):
            col = self._colore_led(i, i < acc, c)
            self._canvas.create_rectangle(
                x, y, x + self._w_seg, y + self._h_seg,
                fill=col, outline=c["sfondo"], width=1,
            )
            x += self._w_seg + self._gap

        # Aggiorna etichette
        col_liv = self._colore_livello(livello, c)
        try:
            self._lbl.config(fg=col_liv)
            # Se il dato non e' disponibile: tutto dim
            if not d.get("disponibile", False):
                self._lbl_dx.config(text=testo, fg=c["testo_dim"])
            else:
                fg_dx = c["stato_ok"] if livello == _LIV_OK else col_liv
                self._lbl_dx.config(text=testo, fg=fg_dx)
        except Exception:
            pass

        # Riprogramma refresh
        try:
            if self._after_id:
                self.after_cancel(self._after_id)
        except Exception:
            pass
        try:
            self._after_id = self.after(self._intervallo_ms, self.aggiorna)
        except Exception:
            self._after_id = None

    def ferma(self):
        try:
            if self._after_id:
                self.after_cancel(self._after_id)
        except Exception:
            pass
        self._after_id = None
        try:
            if self._tip is not None:
                self._tip.destroy()
        except Exception:
            pass
        self._tip = None

    # ---------------------------------------------------------------
    #  TOOLTIP
    # ---------------------------------------------------------------
    def _mostra_tooltip(self, ev=None):
        testo = None
        try:
            testo = self._tooltip_testo()
        except Exception:
            testo = None
        if not testo:
            return
        c = _carica_colori()
        try:
            self._nascondi_tooltip()
            tip = tk.Toplevel(self)
            tip.wm_overrideredirect(True)
            tip.configure(bg=c["linee"])
            lbl = tk.Label(tip, text=testo, bg=c["sfondo"], fg=c["dati"],
                           font=self._font, justify="left",
                           padx=6, pady=3, bd=0)
            lbl.pack(padx=1, pady=1)
            x = self._canvas.winfo_rootx()
            y = self._canvas.winfo_rooty() + self._canvas.winfo_height() + 4
            tip.geometry("+%d+%d" % (x, y))
            tip.attributes("-topmost", True)
            self._tip = tip
        except Exception:
            self._tip = None

    def _nascondi_tooltip(self, ev=None):
        try:
            if self._tip is not None:
                self._tip.destroy()
        except Exception:
            pass
        self._tip = None


# =====================================================================
#  BarraSD - CAPIENZA
# =====================================================================
class BarraSD(_BarraLEDBase):
    """Barra capienza disco: accesi = spazio USATO, testo = spazio LIBERO.
    Compatibile con la firma originale."""

    _PCT_ROSSO_LIBERO  = 10
    _PCT_GIALLO_LIBERO = 25

    def __init__(self, master, path=None, n_segmenti=16,
                 mostra_etichetta=True, mostra_percento=True,
                 intervallo_ms=30000, scala=None, **kw):
        self._path = path if path else _path_default()
        # I parametri mostra_* sono accettati per retrocompatibilita'
        # ma la nuova base li tratta come sempre attivi (estetica
        # piu' uniforme fra le tre barre).
        self._mostra_etichetta = mostra_etichetta
        self._mostra_percento = mostra_percento
        super().__init__(master, etichetta="SD", n_segmenti=n_segmenti,
                         intervallo_ms=intervallo_ms, scala=scala,
                         largh_testo_dx=5, **kw)
        if not mostra_etichetta:
            try: self._lbl.pack_forget()
            except Exception: pass
        if not mostra_percento:
            try: self._lbl_dx.pack_forget()
            except Exception: pass

    def _leggi_dati(self):
        try:
            u = shutil.disk_usage(self._path)
        except Exception:
            return {"pct_acceso": 0, "testo_dx": "---%",
                    "livello": _LIV_OK, "disponibile": False}
        if u.total <= 0:
            return {"pct_acceso": 0, "testo_dx": "---%",
                    "livello": _LIV_OK, "disponibile": False}
        pct_usato = int(round(u.used * 100.0 / u.total))
        pct_libero = 100 - pct_usato
        if pct_libero <= self._PCT_ROSSO_LIBERO:
            liv = _LIV_CRITICO
        elif pct_libero <= self._PCT_GIALLO_LIBERO:
            liv = _LIV_AVVISO
        else:
            liv = _LIV_OK
        return {"pct_acceso": pct_usato,
                "testo_dx": "%d%%" % pct_libero,
                "livello": liv, "disponibile": True}

    def _tooltip_testo(self):
        try:
            u = shutil.disk_usage(self._path)
        except Exception:
            return None
        def fmt(b):
            if b >= 1024**3:
                return "%.1f GB" % (b / (1024.0**3))
            if b >= 1024**2:
                return "%.0f MB" % (b / (1024.0**2))
            return "%d B" % b
        return ("CAPIENZA DISCO\n"
                "usato  : %s\n"
                "totale : %s\n"
                "libero : %s" % (fmt(u.used), fmt(u.total), fmt(u.free)))


# =====================================================================
#  BarraUsura - USURA STIMATA
# =====================================================================
class BarraUsura(_BarraLEDBase):
    """Stima dell'usura della SD basata sui GB scritti accumulati.

    Tenta inoltre `mmc extcsd read` in background: se la SD supporta
    DEVICE_LIFE_TIME_EST, usa QUEL dato (reale) invece della stima
    (il tooltip lo segnala).
    """

    _SOGLIA_GIALLO = 75   # % consumato
    _SOGLIA_ROSSO  = 90

    def __init__(self, master, tbw_gb=30000, file_stato=None,
                 n_segmenti=16, intervallo_ms=30000, scala=None, **kw):
        self._tbw_gb = max(1, int(tbw_gb))
        if file_stato is None:
            qui = os.path.dirname(os.path.abspath(__file__))
            radice = os.path.dirname(qui)
            file_stato = os.path.join(radice, "dati", "sd_wear.json")
        self._file_stato = file_stato
        self._dev = _trova_dev_sd() if _HEALTH_DISPONIBILE else None
        self._stato_usura = (_StatoUsura(file_stato, self._dev)
                             if _HEALTH_DISPONIBILE else None)
        # Cache del risultato ext_csd (tentato una volta all'avvio, in
        # background per non bloccare l'UI; mmc extcsd ha timeout 3s).
        self._ext_csd = None
        self._ext_csd_testato = False
        super().__init__(master, etichetta="WR", n_segmenti=n_segmenti,
                         intervallo_ms=intervallo_ms, scala=scala,
                         largh_testo_dx=5, **kw)

    def _tenta_ext_csd(self):
        """Prova ext_csd una sola volta, in un thread, per non bloccare."""
        if self._ext_csd_testato or not _HEALTH_DISPONIBILE:
            return
        self._ext_csd_testato = True
        try:
            import threading
            def _lavoro():
                try:
                    self._ext_csd = _prova_ext_csd(self._dev)
                except Exception:
                    self._ext_csd = None
            threading.Thread(target=_lavoro, daemon=True).start()
        except Exception:
            try:
                self._ext_csd = _prova_ext_csd(self._dev)
            except Exception:
                self._ext_csd = None

    def _leggi_dati(self):
        self._tenta_ext_csd()

        # Priorita' al dato reale se disponibile
        if self._ext_csd and "pct_usura" in self._ext_csd:
            pct = int(self._ext_csd["pct_usura"])
            if self._ext_csd.get("eol"):
                pct = 100
            if pct >= self._SOGLIA_ROSSO or self._ext_csd.get("eol"):
                liv = _LIV_CRITICO
            elif pct >= self._SOGLIA_GIALLO:
                liv = _LIV_AVVISO
            else:
                liv = _LIV_OK
            return {"pct_acceso": pct,
                    "testo_dx": "%d%%" % pct,
                    "livello": liv, "disponibile": True}

        # Fallback: stima via accumulatore GB scritti / TBW
        if self._stato_usura is None:
            return {"pct_acceso": 0, "testo_dx": "---",
                    "livello": _LIV_OK, "disponibile": False}
        d = self._stato_usura.aggiorna()
        if not d.get("disponibile"):
            return {"pct_acceso": 0, "testo_dx": "---",
                    "livello": _LIV_OK, "disponibile": False}
        gb_w = d.get("gb_scritti", 0.0)
        pct = int(round(gb_w * 100.0 / self._tbw_gb))
        if pct > 100: pct = 100
        if pct >= self._SOGLIA_ROSSO:
            liv = _LIV_CRITICO
        elif pct >= self._SOGLIA_GIALLO:
            liv = _LIV_AVVISO
        else:
            liv = _LIV_OK
        # Testo: pct se alto, altrimenti GB scritti come info piu' utile
        if pct >= 5:
            testo = "%d%%" % pct
        elif gb_w >= 1024:
            testo = "%.1fT" % (gb_w / 1024.0)
        else:
            testo = "%dG" % int(gb_w)
        return {"pct_acceso": pct, "testo_dx": testo,
                "livello": liv, "disponibile": True}

    def _tooltip_testo(self):
        righe = ["USURA STIMATA"]
        sorgente_reale = bool(self._ext_csd)
        if sorgente_reale:
            pct = self._ext_csd.get("pct_usura", 0)
            righe.append("fonte    : ext_csd (reale)")
            righe.append("consumo  : %d%%" % pct)
            if self._ext_csd.get("eol"):
                righe.append("STATO    : EOL (fine vita!)")
        elif self._stato_usura is not None and self._stato_usura.stato():
            d = self._stato_usura.stato()
            if d.get("disponibile"):
                righe.append("fonte    : stima GB scritti")
                righe.append("scritti  : %.1f GB" % d.get("gb_scritti", 0))
                righe.append("letti    : %.1f GB" % d.get("gb_letti", 0))
                righe.append("TBW rif. : %d GB" % self._tbw_gb)
                pct = int(round(d.get("gb_scritti", 0) * 100.0 / self._tbw_gb))
                righe.append("consumo  : %d%%" % min(100, pct))
            else:
                righe.append("dati non disponibili (no SD / no Linux)")
        else:
            righe.append("modulo sd_health non disponibile")
        return "\n".join(righe)


# =====================================================================
#  BarraIO - VU METER I/O IN TEMPO REALE
# =====================================================================
class BarraIO(_BarraLEDBase):
    """VU meter LED dell'attivita' I/O: riempie in base a MB/s scrittura
    (+lettura), sample veloce (1 sec).

    Il fondo scala `max_mbs` e' configurabile: per una SD Class 10 una
    soglia di 20 MB/s e' adeguata; per UHS-I si puo' alzare a 60.
    """

    def __init__(self, master, max_mbs=20, n_segmenti=16,
                 intervallo_ms=1000, scala=None, **kw):
        self._max_mbs = max(1.0, float(max_mbs))
        self._dev = _trova_dev_sd() if _HEALTH_DISPONIBILE else None
        self._stato = (_StatoUsura(os.devnull, self._dev)
                       if _HEALTH_DISPONIBILE else None)
        # Nota: riusiamo StatoUsura SOLO per la logica di sample (MB/s).
        # Il file /dev/null fa sparire il salvataggio persistente: l'unica
        # scrittura "vera" dell'accumulatore la fa BarraUsura. Evita di
        # scrivere sulla SD ogni secondo (sarebbe controproducente!).
        self._ultima_mbs_w = 0.0
        self._ultima_mbs_r = 0.0
        super().__init__(master, etichetta="IO", n_segmenti=n_segmenti,
                         intervallo_ms=intervallo_ms, scala=scala,
                         largh_testo_dx=6, **kw)

    def _leggi_dati(self):
        if self._stato is None:
            return {"pct_acceso": 0, "testo_dx": "---",
                    "livello": _LIV_OK, "disponibile": False}
        d = self._stato.aggiorna()
        if not d.get("disponibile"):
            return {"pct_acceso": 0, "testo_dx": "---",
                    "livello": _LIV_OK, "disponibile": False}
        mbs_w = float(d.get("mbs_scrittura", 0.0))
        mbs_r = float(d.get("mbs_lettura", 0.0))
        self._ultima_mbs_w = mbs_w
        self._ultima_mbs_r = mbs_r
        mbs_tot = mbs_w + mbs_r
        pct = int(round(mbs_tot * 100.0 / self._max_mbs))
        if pct < 0: pct = 0
        if pct > 100: pct = 100
        # Testo a destra: MB/s leggibile
        if mbs_tot >= 10:
            testo = "%dM/s" % int(round(mbs_tot))
        elif mbs_tot >= 1:
            testo = "%.1fM" % mbs_tot
        elif mbs_tot >= 0.01:
            testo = "%dk/s" % int(round(mbs_tot * 1024))
        else:
            testo = "idle"
        # Il VU meter non ha "allarme": tutto verde/giallo/rosso viene
        # gia' dal colore dei LED per posizione.
        return {"pct_acceso": pct, "testo_dx": testo,
                "livello": _LIV_OK, "disponibile": True}

    def _tooltip_testo(self):
        if self._stato is None or not self._stato.stato():
            return "VU METER I/O\nnon disponibile"
        return ("VU METER I/O\n"
                "scrittura: %.2f MB/s\n"
                "lettura  : %.2f MB/s\n"
                "scala    : %.0f MB/s" %
                (self._ultima_mbs_w, self._ultima_mbs_r, self._max_mbs))


# =====================================================================
#  BarraBatteria - CARICA RESIDUA
# =====================================================================
class BarraBatteria(_BarraLEDBase):
    """Indicatore batteria LED.

    Semantica invertita rispetto alle altre barre: i LED accesi rappresentano
    la carica RIMANENTE (100% = tutti accesi). Il colore e' globale (tutti
    i LED stessa tonalita' in base al livello, non per posizione):

        >= 50%        : verde  (o CIANO se in carica)
        20% <= x < 50%: giallo
        < 20%         : rosso

    La batteria in carica usa un colore ciano distintivo per differenziare
    visualmente dallo stato "scarico" e dalle altre barre.

    Riceve un callable get_info_func() -> (percentuale, stato) cosi' resta
    indipendente da come l'app legge la batteria (retrodb ha gia' una sua
    funzione, non la duplichiamo).
    """

    _PCT_GIALLO = 50
    _PCT_ROSSO  = 20

    # Colore distintivo per "in carica". Se il tema ha "cerca_testo" lo uso,
    # altrimenti fallback a un ciano fisso.
    _COLORE_CARICA_FALLBACK = "#40c4ff"

    def __init__(self, master, get_info_func, n_segmenti=16,
                 intervallo_ms=30000, scala=None, **kw):
        self._get_info = get_info_func
        self._ultimo_pct = 0
        self._ultimo_stato = None
        super().__init__(master, etichetta="BAT", n_segmenti=n_segmenti,
                         intervallo_ms=intervallo_ms, scala=scala,
                         largh_testo_dx=6, **kw)

    # Override: colore LED uniforme basato sul LIVELLO GLOBALE, non sulla
    # posizione (a differenza di SD/USURA/IO che usano la soglia per indice).
    def _colore_led(self, idx, acceso, c):
        if not acceso:
            return c["linee"]
        # In carica: ciano, sempre (visivamente inequivocabile)
        if self._ultimo_stato == "Charging":
            return c.get("cerca_testo", self._COLORE_CARICA_FALLBACK)
        # Scarica o a riposo: colore per livello
        if self._ultimo_pct < self._PCT_ROSSO:
            return c["stato_errore"]
        if self._ultimo_pct < self._PCT_GIALLO:
            return c["stato_avviso"]
        return c["stato_ok"]

    def _leggi_dati(self):
        try:
            pct, stato = self._get_info()
        except Exception:
            pct, stato = None, None
        if pct is None:
            return {"pct_acceso": 0, "testo_dx": "---",
                    "livello": _LIV_OK, "disponibile": False}
        self._ultimo_pct = int(pct)
        self._ultimo_stato = stato
        # Testo dx: XX% / XX%+ se in carica / FULL se 100%
        if stato == "Charging":
            testo = "%d%%+" % self._ultimo_pct
        elif stato == "Full" or self._ultimo_pct >= 100:
            testo = "FULL"
        else:
            testo = "%d%%" % self._ultimo_pct
        # Livello logico (per colore dell'etichetta e del testo dx)
        if stato == "Charging":
            liv = _LIV_OK
        elif self._ultimo_pct < self._PCT_ROSSO:
            liv = _LIV_CRITICO
        elif self._ultimo_pct < self._PCT_GIALLO:
            liv = _LIV_AVVISO
        else:
            liv = _LIV_OK
        return {"pct_acceso": self._ultimo_pct, "testo_dx": testo,
                "livello": liv, "disponibile": True}

    def _tooltip_testo(self):
        if self._ultimo_stato is None and self._ultimo_pct == 0:
            return "BATTERIA\nnon disponibile"
        stato_it = {
            "Charging":     "in carica",
            "Discharging":  "in scarica",
            "Full":         "carica al 100%",
            "Not charging": "non in carica",
            "Unknown":      "sconosciuto",
        }.get(self._ultimo_stato, str(self._ultimo_stato or "n/d"))
        return ("BATTERIA\n"
                "carica: %d%%\n"
                "stato : %s" % (self._ultimo_pct, stato_it))


# =====================================================================
#  Test standalone:  python -m core.sd_bar
# =====================================================================
if __name__ == "__main__":
    root = tk.Tk()
    root.title("Test Pannello SD")
    root.configure(bg="#0a0a0a")
    root.geometry("360x160")

    BarraSD(root).pack(padx=20, pady=(20, 4), anchor="w")
    BarraUsura(root, tbw_gb=30000).pack(padx=20, pady=4, anchor="w")
    BarraIO(root, max_mbs=20).pack(padx=20, pady=4, anchor="w")

    # Per il test, batteria finta al 73% in carica
    def _bat_finta():
        return 73, "Charging"
    BarraBatteria(root, get_info_func=_bat_finta).pack(
        padx=20, pady=(4, 20), anchor="w")

    root.mainloop()
