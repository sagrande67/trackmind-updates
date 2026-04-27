"""
TrackMind - ASSISTENTE GARA
Addon che monitora un evento MyRCM in corso e ti avverte quando la
tua categoria deve entrare in pista (prove libere, qualifiche, gara)
con countdown live e alert a -15 min (preparazione vettura) e -1 min
(avvio motore).

Flusso:
1. All'avvio scarica gli eventi MyRCM ATTUALMENTE online (filtrabili
   per nazione, default Italia)
2. L'utente seleziona il proprio evento dalla lista
3. Scarica le categorie dell'evento
4. L'utente sceglie la propria categoria (dinamica per evento)
5. Mostra il time table della categoria con countdown live e alert

Tutto stdlib + tkinter, niente dipendenze esterne (urllib via myrcm_import).
"""

import os
import sys
import re
import threading
import tkinter as tk
from tkinter import font as tkfont
from datetime import datetime, timedelta

# Import myrcm_import dal modulo fratello
try:
    from myrcm_import import (lista_eventi_online_completa,
                              scarica_categorie, scarica_html_evento,
                              _TableParser, _http_get, MYRCM_BASE)
    _HAS_MYRCM = True
except ImportError:
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from myrcm_import import (lista_eventi_online_completa,
                                  scarica_categorie, scarica_html_evento,
                                  _TableParser, _http_get, MYRCM_BASE)
        _HAS_MYRCM = True
    except ImportError:
        _HAS_MYRCM = False

# Font + colori
try:
    from config_colori import FONT_MONO, carica_colori as _carica_colori
except ImportError:
    FONT_MONO = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"
    def _carica_colori():
        return {
            "sfondo": "#0a0a0a", "dati": "#39ff14",
            "label": "#39ff14", "linee": "#1a3a1a",
            "stato_ok": "#39ff14", "stato_avviso": "#ffff00",
            "stato_errore": "#ff4444", "testo_dim": "#1a8c1a",
            "pulsanti_sfondo": "#1a3a1a", "pulsanti_testo": "#39ff14",
            "cerca_sfondo": "#0a0a0a", "cerca_testo": "#39ff14",
            "sfondo_celle": "#0a0a0a",
        }

# Barra batteria (opzionale)
try:
    from core.batteria import aggiungi_barra_batteria as _aggiungi_barra_bat
except Exception:
    def _aggiungi_barra_bat(*args, **kwargs):
        return None


# =====================================================================
#  PARSER TIME TABLE MyRCM
# =====================================================================
def parse_time_table(html):
    """Estrae il time table di un evento dall'HTML pagina principale.

    MyRCM espone (di solito) una tabella con colonne tipo:
        Ora | Categoria | Turno | Manche | ...
    Le righe possono avere orario in formato "HH:MM" come prima cella.

    Strategia: scansiona tutte le tabelle, prende quelle dove la prima
    colonna contiene piu' valori in formato HH:MM e tiene quel match
    come time table principale.

    Ritorna lista di dict:
        [{"ora": "HH:MM", "categoria": "...", "turno": "...",
          "raw": [...colonne...]}, ...]

    NB: il parsing e' euristico e va affinato sul dump HTML reale di
    un evento. Se non trova un time table riconoscibile, ritorna [].
    """
    if not html:
        return []
    parser = _TableParser()
    try:
        parser.feed(html)
    except Exception:
        return []

    re_ora = re.compile(r'^\d{1,2}[:.]\d{2}$')
    miglior = None  # (n_righe_con_ora, righe)

    for table in parser.tables:
        if len(table) < 3:
            continue
        # Conto le righe (dopo eventuale header) con prima cella in
        # formato orario.
        n_ora = 0
        body_rows = table[1:] if len(table) > 1 else []
        for row in body_rows:
            if not row:
                continue
            primo = (row[0] or "").strip()
            if re_ora.match(primo):
                n_ora += 1
        if n_ora >= 3 and (miglior is None or n_ora > miglior[0]):
            miglior = (n_ora, table)

    if not miglior:
        return []

    risultati = []
    table = miglior[1]
    header = [c.strip().lower() for c in table[0]]
    # Trova indici delle colonne note (best-effort)
    idx_cat = None
    for i, h in enumerate(header):
        if any(k in h for k in ("categoria", "class", "cat")):
            idx_cat = i
            break
    idx_turno = None
    for i, h in enumerate(header):
        if any(k in h for k in ("turno", "round", "round/manche",
                                "session", "manche", "qualif",
                                "fase", "tipo")):
            idx_turno = i
            break

    for row in table[1:]:
        if not row:
            continue
        primo = (row[0] or "").strip()
        if not re_ora.match(primo):
            continue
        ora = primo.replace(".", ":")
        cat = ((row[idx_cat] or "").strip()
               if idx_cat is not None and idx_cat < len(row)
               else "")
        turno = ((row[idx_turno] or "").strip()
                 if idx_turno is not None and idx_turno < len(row)
                 else "")
        risultati.append({
            "ora": ora,
            "categoria": cat,
            "turno": turno,
            "raw": [c.strip() for c in row],
        })
    return risultati


def _ora_to_dt(ora_str, base_date=None):
    """Converte 'HH:MM' nel datetime di oggi (o base_date) alla
    stessa ora. Ritorna None se non parseabile."""
    try:
        hh, mm = ora_str.split(":")
        hh = int(hh)
        mm = int(mm)
    except (ValueError, AttributeError):
        return None
    base = base_date or datetime.now()
    return base.replace(hour=hh, minute=mm, second=0, microsecond=0)


def filtra_per_categoria(time_table, categoria_keyword):
    """Ritorna le righe del time table che contengono la categoria
    indicata (match case-insensitive su qualunque cella raw)."""
    if not categoria_keyword:
        return list(time_table)
    kw = categoria_keyword.lower().strip()
    out = []
    for r in time_table:
        # match nella categoria o in qualsiasi cella raw
        if kw in (r.get("categoria", "") or "").lower():
            out.append(r)
            continue
        if any(kw in (c or "").lower() for c in r.get("raw", [])):
            out.append(r)
    return out


# =====================================================================
#  ASSISTENTE GARA - MONITOR PERSISTENTE (singleton)
# =====================================================================
class AssistenteGaraMonitor:
    """Monitor di sfondo che resta attivo anche quando l'UI
    fullscreen e' chiusa. L'utente pretende di poter lavorare sui
    setup mentre l'assistente lo avvisa quando arrivano i suoi turni:
    questo monitor gira con root.after(1000, ...) finche' viene
    disattivato esplicitamente, e notifica i listener registrati sia
    ad ogni tick (per il widget header) sia agli edge dei threshold
    (-15 min, -1 min) per scatenare popup alert.

    Singleton accessibile via AssistenteGaraMonitor.get(root):
    serve un unico monitor per processo, l'utente ha una sola gara
    alla volta in cui correre.
    """
    _instance = None
    SOGLIA_PREP_MIN = 15
    SOGLIA_AVVIA_MIN = 1

    @classmethod
    def get(cls, root=None):
        if cls._instance is None and root is not None:
            cls._instance = cls(root)
        return cls._instance

    def __init__(self, root):
        self.root = root
        # Stato: questi sono i dati che l'utente ha scelto in UI
        self.evento = None         # dict
        self.categoria = None      # dict
        self.time_table = []
        self.tt_filtrato = []
        self.delay_min = 0
        # Listeners
        self._tick_listeners = []   # f(prossimo, dt_target, now)
        self._alert_listeners = []  # f(stato, prossimo, dt_target)
        # Tick state
        self._attivo = False
        self._tick_id = None
        self._ultimo_alert_stato = None  # 'prep' | 'avvia' | None

    # ── attivazione/disattivazione ────────────────────────────────
    def attiva(self, evento, categoria, time_table, delay_min=0):
        self.evento = evento
        self.categoria = categoria
        self.time_table = time_table or []
        self.tt_filtrato = filtra_per_categoria(
            self.time_table, (categoria or {}).get("nome", ""))
        self.delay_min = delay_min
        self._ultimo_alert_stato = None
        if not self._attivo:
            self._attivo = True
            self._tick()

    def disattiva(self):
        self._attivo = False
        if self._tick_id is not None:
            try:
                self.root.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None
        self.evento = None
        self.categoria = None
        self.time_table = []
        self.tt_filtrato = []
        self.delay_min = 0
        # Notifica un ultimo tick "spento" cosi' i listener si nascondono
        for cb in list(self._tick_listeners):
            try:
                cb(None, None, datetime.now())
            except Exception:
                pass

    @property
    def attivo(self):
        return self._attivo

    # ── ritardo manuale ───────────────────────────────────────────
    def imposta_delay(self, delay_min):
        self.delay_min = int(delay_min)
        # Reset alert: se ho appena spostato gli orari, gli edge si
        # ricomputano alla prossima soglia raggiunta.
        self._ultimo_alert_stato = None

    def aggiungi_delay(self, delta):
        self.imposta_delay(self.delay_min + int(delta))

    # ── listeners ─────────────────────────────────────────────────
    def add_tick_listener(self, cb):
        if cb not in self._tick_listeners:
            self._tick_listeners.append(cb)

    def remove_tick_listener(self, cb):
        if cb in self._tick_listeners:
            self._tick_listeners.remove(cb)

    def add_alert_listener(self, cb):
        if cb not in self._alert_listeners:
            self._alert_listeners.append(cb)

    def remove_alert_listener(self, cb):
        if cb in self._alert_listeners:
            self._alert_listeners.remove(cb)

    # ── core ──────────────────────────────────────────────────────
    def trova_prossimo(self, now=None):
        """Ritorna (turno_dict, dt_target) del prossimo turno della
        categoria selezionata, applicando il delay manuale."""
        if not self._attivo or not self.tt_filtrato:
            return None, None
        if now is None:
            now = datetime.now()
        prossimo = None
        prossimo_dt = None
        for r in self.tt_filtrato:
            ora = r.get("ora", "")
            dt = _ora_to_dt(ora)
            if dt is None:
                continue
            dt = dt + timedelta(minutes=self.delay_min)
            if dt <= now:
                continue
            if prossimo_dt is None or dt < prossimo_dt:
                prossimo = r
                prossimo_dt = dt
        return prossimo, prossimo_dt

    def _tick(self):
        if not self._attivo:
            return
        now = datetime.now()
        prossimo, dt_target = self.trova_prossimo(now)
        # Notifica tick listeners (widget header, UI fullscreen, ecc.)
        for cb in list(self._tick_listeners):
            try:
                cb(prossimo, dt_target, now)
            except Exception:
                pass
        # Edge detection alert
        if dt_target is not None:
            secs = (dt_target - now).total_seconds()
            nuovo_stato = None
            if 0 < secs <= self.SOGLIA_AVVIA_MIN * 60:
                nuovo_stato = "avvia"
            elif 0 < secs <= self.SOGLIA_PREP_MIN * 60:
                nuovo_stato = "prep"
            # Trigger alert solo al CAMBIO di stato, e solo verso uno
            # stato di livello superiore (non torno indietro a "prep"
            # se ero gia' in "avvia").
            ordine = {None: 0, "prep": 1, "avvia": 2}
            if (nuovo_stato is not None and
                ordine[nuovo_stato] > ordine[self._ultimo_alert_stato]):
                self._ultimo_alert_stato = nuovo_stato
                for cb in list(self._alert_listeners):
                    try:
                        cb(nuovo_stato, prossimo, dt_target)
                    except Exception:
                        pass
            elif (nuovo_stato is None and
                  self._ultimo_alert_stato is not None):
                # Turno passato: reset stato cosi' al prossimo turno
                # gli alert ripartono da zero.
                self._ultimo_alert_stato = None
        # Ripianifica
        try:
            self._tick_id = self.root.after(1000, self._tick)
        except Exception:
            self._attivo = False


# =====================================================================
#  POPUP ALERT (toplevel transient sopra qualsiasi schermata)
# =====================================================================
def mostra_popup_alert(root, stato, prossimo, dt_target, colori=None):
    """Mostra popup grande con messaggio "PREPARARE LA VETTURA" o
    "AVVIA MOTORE". Si chiude da solo dopo 30 sec o al click utente.
    Usato dal monitor per avvertire anche quando l'utente sta
    lavorando in altre schermate (setup, crono, ecc.)."""
    c = colori or _carica_colori()
    if stato == "avvia":
        titolo = ">>> AVVIA MOTORE <<<"
        sotto = "1 minuto al tuo turno!"
        col_bg = "#330000"
        col_fg = "#ff4444"
    else:
        titolo = ">>> PREPARARE LA VETTURA <<<"
        sotto = "15 minuti al tuo turno"
        col_bg = "#332200"
        col_fg = "#ffaa00"
    cat = (prossimo.get("categoria", "")
           if prossimo else "") or "?"
    turno = (prossimo.get("turno", "")
             if prossimo else "") or ""
    ora = dt_target.strftime("%H:%M") if dt_target else "?"

    try:
        popup = tk.Toplevel(root)
    except Exception:
        return None
    popup.title("ASSISTENTE GARA - ALERT")
    popup.config(bg=col_bg)
    popup.transient(root.winfo_toplevel())
    try:
        popup.attributes("-topmost", True)
    except Exception:
        pass
    # Centratura
    try:
        rw = root.winfo_toplevel().winfo_width()
        rh = root.winfo_toplevel().winfo_height()
        rx = root.winfo_toplevel().winfo_rootx()
        ry = root.winfo_toplevel().winfo_rooty()
        w, h = 540, 220
        x = rx + (rw - w) // 2
        y = ry + (rh - h) // 2
        popup.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))
    except Exception:
        popup.geometry("540x220")

    f_titolo = tkfont.Font(family=FONT_MONO, size=20, weight="bold")
    f_sotto = tkfont.Font(family=FONT_MONO, size=14, weight="bold")
    f_dett = tkfont.Font(family=FONT_MONO, size=11)
    f_btn = tkfont.Font(family=FONT_MONO, size=11, weight="bold")

    tk.Label(popup, text=titolo, bg=col_bg, fg=col_fg,
             font=f_titolo).pack(pady=(20, 6))
    tk.Label(popup, text=sotto, bg=col_bg, fg=col_fg,
             font=f_sotto).pack(pady=(0, 8))
    tk.Label(popup, text="%s   %s   %s" % (cat[:25], turno[:30], ora),
             bg=col_bg, fg="#ffffff", font=f_dett).pack(pady=(0, 12))

    # Bottone OK chiude e torna a ciò che si stava facendo
    def _close():
        try:
            popup.destroy()
        except Exception:
            pass
    tk.Button(popup, text="OK", font=f_btn, width=10,
              bg=col_fg, fg=col_bg, relief="ridge", bd=2,
              cursor="hand2", command=_close).pack(pady=(0, 12))

    popup.bind("<Return>", lambda e: _close())
    popup.bind("<Escape>", lambda e: _close())
    popup.protocol("WM_DELETE_WINDOW", _close)

    # Auto-close dopo 30s cosi' non resta li' tipo modale infinito
    try:
        popup.after(30000, _close)
    except Exception:
        pass

    # Lampeggio del titolo per attirare l'attenzione (10 cicli)
    state = {"alt": False, "n": 0}
    lbl_titolo = popup.winfo_children()[0]
    def _flash():
        if state["n"] >= 20:
            return
        try:
            if not popup.winfo_exists():
                return
        except Exception:
            return
        state["alt"] = not state["alt"]
        try:
            if state["alt"]:
                lbl_titolo.config(bg=col_fg, fg=col_bg)
            else:
                lbl_titolo.config(bg=col_bg, fg=col_fg)
        except Exception:
            return
        state["n"] += 1
        try:
            popup.after(400, _flash)
        except Exception:
            pass
    _flash()

    try:
        popup.lift()
        popup.focus_force()
    except Exception:
        pass
    return popup


# =====================================================================
#  ASSISTENTE GARA - UI
# =====================================================================
class AssistenteGara:
    """Addon Assistente Gara: monitor evento MyRCM con countdown
    e alert per la categoria scelta."""

    # Soglie per gli stati visivi delle imminenti chiamate
    SOGLIA_PREP_MIN = 15  # arancio: preparare la vettura
    SOGLIA_AVVIA_MIN = 1  # rosso lampeggiante: avvia motore
    REFRESH_COUNTDOWN_MS = 1000  # tick countdown 1 Hz

    def __init__(self, parent=None, on_close=None):
        self.c = _carica_colori()
        self._on_close = on_close

        if parent is not None:
            self.root = parent
            self._top = self.root.winfo_toplevel()
            self._embedded = True
        else:
            self.root = tk.Tk()
            self.root.title("TrackMind - Assistente Gara")
            self.root.config(bg=self.c["sfondo"])
            self.root.geometry("1024x680")
            self._top = self.root
            self._embedded = False

        self._f_title = tkfont.Font(family=FONT_MONO, size=16, weight="bold")
        self._f_btn = tkfont.Font(family=FONT_MONO, size=10, weight="bold")
        self._f_info = tkfont.Font(family=FONT_MONO, size=11)
        self._f_small = tkfont.Font(family=FONT_MONO, size=9)
        self._f_count = tkfont.Font(family=FONT_MONO, size=22, weight="bold")
        self._f_count_big = tkfont.Font(family=FONT_MONO, size=36,
                                         weight="bold")

        # Stato locale (in fase di scelta evento/categoria)
        self._eventi = []
        self._categorie = []
        self._html_evento = ""
        self._tick_listener = None  # callback registrato sul monitor

        # Monitor singleton: se gia' attivo, salta dritto al countdown
        # cosi' chi rientra in ASSIST. GARA dal menu non ripassa per la
        # lista eventi/categorie. Lo stato (evento, categoria, time
        # table, delay) sopravvive tra una sessione UI e l'altra.
        monitor = AssistenteGaraMonitor.get(self._top)
        if monitor and monitor.attivo:
            self._schermata_timetable_monitor()
        else:
            self._schermata_iniziale()

    # =================================================================
    #  Helper UI
    # =================================================================
    def _pulisci(self):
        for w in self.root.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        if self._countdown_id is not None:
            try:
                self.root.after_cancel(self._countdown_id)
            except Exception:
                pass
            self._countdown_id = None

    def _header(self, titolo, back_cmd=None):
        c = self.c
        h = tk.Frame(self.root, bg=c["sfondo"])
        h.pack(fill="x", padx=10, pady=(8, 0))
        if back_cmd:
            tk.Button(h, text="< INDIETRO", font=self._f_small,
                      bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                      relief="ridge", bd=1, cursor="hand2",
                      command=back_cmd).pack(side="left")
        tk.Label(h, text="  " + titolo, bg=c["sfondo"],
                 fg=c["dati"], font=self._f_title).pack(side="left")
        # Barra batteria a destra
        try:
            _aggiungi_barra_bat(self.root)
        except Exception:
            pass
        tk.Frame(self.root, bg=c["linee"], height=1).pack(
            fill="x", padx=10, pady=(6, 4))
        return h

    def _footer_status(self, testo=""):
        c = self.c
        self._status_lbl = tk.Label(self.root, text=testo,
                                     bg=c["sfondo"], fg=c["testo_dim"],
                                     font=self._f_small, anchor="w")
        self._status_lbl.pack(fill="x", side="bottom", padx=10, pady=4)
        return self._status_lbl

    def _set_status(self, testo, livello="info"):
        c = self.c
        col = {
            "ok": c["stato_ok"],
            "errore": c["stato_errore"],
            "avviso": c["stato_avviso"],
            "info": c["testo_dim"],
        }.get(livello, c["testo_dim"])
        try:
            self._status_lbl.config(text=testo, fg=col)
        except Exception:
            pass

    # =================================================================
    #  Step 1: schermata iniziale - lista eventi
    # =================================================================
    def _schermata_iniziale(self):
        self._pulisci()
        c = self.c
        back = self._on_close if self._on_close else self._chiudi
        self._header("ASSISTENTE GARA", back_cmd=back)

        if not _HAS_MYRCM:
            tk.Label(self.root,
                     text="Modulo MyRCM non disponibile.\n"
                          "Verifica che addons/myrcm_import.py esista.",
                     bg=c["sfondo"], fg=c["stato_errore"],
                     font=self._f_info).pack(pady=40)
            self._footer_status("ERRORE: import MyRCM fallito",
                                 livello="errore")
            return

        # Barra controlli: filtro nazione + bottone aggiorna
        bar = tk.Frame(self.root, bg=c["sfondo"])
        bar.pack(fill="x", padx=10, pady=(4, 4))
        tk.Label(bar, text="Filtro nazione:", bg=c["sfondo"],
                 fg=c["label"], font=self._f_info).pack(side="left",
                                                         padx=(0, 6))
        self._naz_var = tk.StringVar(value="ita")
        ent = tk.Entry(bar, textvariable=self._naz_var, font=self._f_info,
                       width=8, bg=c["sfondo_celle"], fg=c["dati"],
                       insertbackground=c["dati"], relief="solid", bd=1)
        ent.pack(side="left", padx=(0, 8))
        ent.bind("<Return>", lambda e: self._carica_eventi())
        tk.Button(bar, text="AGGIORNA", font=self._f_btn,
                  bg=c["cerca_sfondo"], fg=c["cerca_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._carica_eventi).pack(side="left", padx=4)
        tk.Label(bar,
                 text="(svuota per vedere tutti gli eventi mondiali)",
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(side="left", padx=(8, 0))

        # Listbox eventi
        list_frame = tk.Frame(self.root, bg=c["sfondo"])
        list_frame.pack(fill="both", expand=True, padx=10, pady=(4, 4))
        sb = tk.Scrollbar(list_frame, bg=c["sfondo"],
                          troughcolor=c["sfondo"],
                          activebackground=c["dati"])
        sb.pack(side="right", fill="y")
        self._lb_eventi = tk.Listbox(list_frame, font=self._f_info,
                                      bg=c["sfondo_celle"], fg=c["dati"],
                                      selectbackground=c["dati"],
                                      selectforeground=c["sfondo"],
                                      yscrollcommand=sb.set,
                                      relief="solid", bd=1,
                                      highlightthickness=0)
        self._lb_eventi.pack(side="left", fill="both", expand=True)
        sb.config(command=self._lb_eventi.yview)
        self._lb_eventi.bind("<Double-Button-1>",
                              lambda e: self._scegli_evento())
        self._lb_eventi.bind("<Return>",
                              lambda e: self._scegli_evento())

        # Bottoni in fondo
        btnbar = tk.Frame(self.root, bg=c["sfondo"])
        btnbar.pack(fill="x", padx=10, pady=(0, 4))
        tk.Button(btnbar, text="APRI EVENTO", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._scegli_evento).pack(side="left", padx=4)

        self._footer_status("Pronto. Premi AGGIORNA per caricare la "
                            "lista eventi MyRCM.")
        # Auto-carica all'avvio
        self.root.after(200, self._carica_eventi)

    def _carica_eventi(self):
        """Scarica lista eventi online MyRCM (in thread)."""
        c = self.c
        try:
            self._lb_eventi.delete(0, "end")
            self._lb_eventi.insert("end", "  Caricamento in corso...")
        except Exception:
            return
        self._set_status("Connessione a MyRCM...", "avviso")
        naz = self._naz_var.get().strip()

        def _bg():
            try:
                eventi = lista_eventi_online_completa(filtro_nazione=naz)
            except Exception as e:
                eventi = []
                err = str(e)[:80]
                self.root.after(0, lambda: self._set_status(
                    "Errore: " + err, "errore"))
                return
            self.root.after(0, lambda: self._mostra_eventi(eventi))

        threading.Thread(target=_bg, daemon=True).start()

    def _mostra_eventi(self, eventi):
        """Riempie la listbox con la lista eventi."""
        try:
            self._lb_eventi.delete(0, "end")
        except Exception:
            return
        self._eventi = eventi or []
        if not self._eventi:
            self._lb_eventi.insert("end",
                "  Nessun evento online trovato per questo filtro.")
            self._set_status("Lista vuota: prova a svuotare il filtro "
                              "nazione o riprovare piu' tardi.", "avviso")
            return
        for ev in self._eventi:
            riga = "  %s  -  %s  [%s]" % (
                ev.get("nome", "?")[:60],
                ev.get("organizzatore", "?")[:25],
                ev.get("nazione", "?")[:6])
            self._lb_eventi.insert("end", riga)
        self._lb_eventi.selection_set(0)
        self._lb_eventi.activate(0)
        self._set_status("Trovati %d eventi. Doppio click per aprire."
                          % len(self._eventi), "ok")

    def _scegli_evento(self):
        sel = self._lb_eventi.curselection()
        if not sel:
            self._set_status("Seleziona un evento dalla lista", "avviso")
            return
        idx = sel[0]
        if idx < 0 or idx >= len(self._eventi):
            return
        self._evento_sel = self._eventi[idx]
        self._schermata_categorie()

    # =================================================================
    #  Step 2: lista categorie dell'evento
    # =================================================================
    def _schermata_categorie(self):
        self._pulisci()
        c = self.c
        self._header("CATEGORIE - " + (self._evento_sel.get("nome",
                                                            "?")[:40]),
                     back_cmd=self._schermata_iniziale)

        info = tk.Label(self.root,
            text="Organizzatore: %s   Nazione: %s   ID: %s"
                 % (self._evento_sel.get("organizzatore", "?"),
                    self._evento_sel.get("nazione", "?"),
                    self._evento_sel.get("event_id", "?")),
            bg=c["sfondo"], fg=c["testo_dim"], font=self._f_small)
        info.pack(fill="x", padx=10, pady=(0, 6))

        list_frame = tk.Frame(self.root, bg=c["sfondo"])
        list_frame.pack(fill="both", expand=True, padx=10, pady=4)
        sb = tk.Scrollbar(list_frame, bg=c["sfondo"],
                          troughcolor=c["sfondo"],
                          activebackground=c["dati"])
        sb.pack(side="right", fill="y")
        self._lb_cat = tk.Listbox(list_frame, font=self._f_info,
                                   bg=c["sfondo_celle"], fg=c["dati"],
                                   selectbackground=c["dati"],
                                   selectforeground=c["sfondo"],
                                   yscrollcommand=sb.set,
                                   relief="solid", bd=1,
                                   highlightthickness=0)
        self._lb_cat.pack(side="left", fill="both", expand=True)
        sb.config(command=self._lb_cat.yview)
        self._lb_cat.bind("<Double-Button-1>",
                           lambda e: self._scegli_categoria())
        self._lb_cat.bind("<Return>",
                           lambda e: self._scegli_categoria())
        self._lb_cat.insert("end", "  Caricamento categorie...")

        btnbar = tk.Frame(self.root, bg=c["sfondo"])
        btnbar.pack(fill="x", padx=10, pady=(0, 4))
        tk.Button(btnbar, text="APRI CATEGORIA", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._scegli_categoria).pack(side="left", padx=4)

        self._footer_status("Carico categorie...", "avviso")

        # Scarico categorie + HTML evento (per time table) in parallelo
        eid = self._evento_sel.get("event_id", "")

        def _bg():
            try:
                cats = scarica_categorie(eid) or []
            except Exception:
                cats = []
            try:
                html = scarica_html_evento(eid) or ""
            except Exception:
                html = ""
            self.root.after(0, lambda: self._mostra_categorie(cats, html))

        threading.Thread(target=_bg, daemon=True).start()

    def _mostra_categorie(self, categorie, html_evento):
        try:
            self._lb_cat.delete(0, "end")
        except Exception:
            return
        self._categorie = categorie or []
        self._html_evento = html_evento or ""
        # Pre-parsa anche il time table (lo riusiamo)
        try:
            self._time_table = parse_time_table(self._html_evento)
        except Exception:
            self._time_table = []

        if not self._categorie:
            self._lb_cat.insert("end",
                "  Nessuna categoria trovata per questo evento.")
            self._set_status("Categorie non trovate. Verifica che "
                              "l'evento sia ancora online.", "errore")
            return
        for cat in self._categorie:
            riga = "  %s   (id %s)" % (
                cat.get("nome", "?"), cat.get("category_id", "?"))
            self._lb_cat.insert("end", riga)
        self._lb_cat.selection_set(0)
        self._lb_cat.activate(0)

        n_tt = len(self._time_table)
        if n_tt:
            self._set_status(
                "%d categorie. Time table: %d righe rilevate. "
                "Doppio click per aprire."
                % (len(self._categorie), n_tt), "ok")
        else:
            self._set_status(
                "%d categorie. ATTENZIONE: time table non rilevato "
                "(parser euristico - potrebbe servire taratura)."
                % len(self._categorie), "avviso")

    def _scegli_categoria(self):
        sel = self._lb_cat.curselection()
        if not sel:
            self._set_status("Seleziona una categoria", "avviso")
            return
        idx = sel[0]
        if idx < 0 or idx >= len(self._categorie):
            return
        cat = self._categorie[idx]
        # Attiva il monitor singleton: da questo momento il countdown
        # gira anche se l'utente esce dall'addon e va sui setup.
        monitor = AssistenteGaraMonitor.get(self._top)
        if monitor:
            monitor.attiva(self._evento_sel, cat, self._time_table,
                           delay_min=0)
        self._schermata_timetable_monitor()

    # =================================================================
    #  Step 3: time table + countdown live + alert (legge dal MONITOR)
    # =================================================================
    def _schermata_timetable_monitor(self):
        """Schermata countdown che usa il monitor singleton come
        sorgente dati. Quando l'utente fa INDIETRO il monitor NON
        viene fermato: e' il bottone STOP MONITOR esplicito che lo
        spegne."""
        self._pulisci()
        c = self.c
        monitor = AssistenteGaraMonitor.get(self._top)
        if monitor is None or not monitor.attivo:
            self._schermata_iniziale()
            return
        evento = monitor.evento or {}
        categoria = monitor.categoria or {}
        cat_nome = categoria.get("nome", "?")

        # Header con bottoni back + stop monitor
        h = tk.Frame(self.root, bg=c["sfondo"])
        h.pack(fill="x", padx=10, pady=(8, 0))
        tk.Button(h, text="< MENU", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._chiudi_lasciando_monitor).pack(side="left")
        tk.Button(h, text="STOP MONITOR", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["stato_errore"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._stop_monitor).pack(side="left", padx=(6, 0))
        tk.Button(h, text="CAMBIA EVENTO", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._cambia_evento).pack(side="left", padx=(6, 0))
        tk.Label(h, text="  TIME TABLE - " + cat_nome[:30],
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(8, 0))
        try:
            _aggiungi_barra_bat(self.root)
        except Exception:
            pass
        tk.Frame(self.root, bg=c["linee"], height=1).pack(
            fill="x", padx=10, pady=(6, 4))

        # Riga info + ritardo
        info_bar = tk.Frame(self.root, bg=c["sfondo"])
        info_bar.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(info_bar,
                 text="Evento: %s" % (evento.get("nome", "?")[:50]),
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(side="left")
        tk.Label(info_bar, text="   Ritardo applicato:",
                 bg=c["sfondo"], fg=c["label"],
                 font=self._f_small).pack(side="left", padx=(20, 4))
        self._lbl_delay = tk.Label(
            info_bar, text=self._delay_str(monitor.delay_min),
            bg=c["sfondo"], fg=c["stato_avviso"],
            font=self._f_small)
        self._lbl_delay.pack(side="left", padx=(0, 6))
        tk.Button(info_bar, text="+5 min", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._aggiungi_delay(5)).pack(
            side="left", padx=2)
        tk.Button(info_bar, text="+1 min", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._aggiungi_delay(1)).pack(
            side="left", padx=2)
        tk.Button(info_bar, text="-1 min", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._aggiungi_delay(-1)).pack(
            side="left", padx=2)
        tk.Button(info_bar, text="RESET", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["stato_errore"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=lambda: self._reset_delay()).pack(
            side="left", padx=8)

        # Box countdown grande in alto
        self._cd_frame = tk.Frame(self.root, bg=c["sfondo_celle"],
                                   relief="ridge", bd=2)
        self._cd_frame.pack(fill="x", padx=10, pady=(4, 6))
        self._lbl_prossimo = tk.Label(self._cd_frame, text="-",
                                       bg=c["sfondo_celle"], fg=c["dati"],
                                       font=self._f_info)
        self._lbl_prossimo.pack(pady=(8, 2))
        self._lbl_countdown = tk.Label(self._cd_frame, text="--:--",
                                        bg=c["sfondo_celle"],
                                        fg=c["dati"],
                                        font=self._f_count_big)
        self._lbl_countdown.pack(pady=(0, 4))
        self._lbl_alert = tk.Label(self._cd_frame, text="",
                                    bg=c["sfondo_celle"],
                                    fg=c["stato_avviso"],
                                    font=self._f_count)
        self._lbl_alert.pack(pady=(0, 8))

        # Lista turni filtrata
        list_frame = tk.Frame(self.root, bg=c["sfondo"])
        list_frame.pack(fill="both", expand=True, padx=10, pady=4)
        sb = tk.Scrollbar(list_frame, bg=c["sfondo"],
                          troughcolor=c["sfondo"],
                          activebackground=c["dati"])
        sb.pack(side="right", fill="y")
        self._lb_tt = tk.Listbox(list_frame, font=self._f_info,
                                  bg=c["sfondo_celle"], fg=c["dati"],
                                  selectbackground=c["dati"],
                                  selectforeground=c["sfondo"],
                                  yscrollcommand=sb.set,
                                  relief="solid", bd=1,
                                  highlightthickness=0)
        self._lb_tt.pack(side="left", fill="both", expand=True)
        sb.config(command=self._lb_tt.yview)
        self._popola_lista_turni(monitor)

        self._footer_status(
            "Monitor ATTIVO: il countdown gira anche fuori da questa "
            "schermata. STOP MONITOR per spegnerlo.", "ok")

        # Registra tick listener sul monitor cosi' la UI si aggiorna
        # automaticamente ad ogni tick (1 Hz). Quando esci, deregistra.
        self._tick_listener = self._on_tick_ui
        monitor.add_tick_listener(self._tick_listener)
        # Trigger immediato per non aspettare il primo secondo
        self._on_tick_ui(*monitor.trova_prossimo() + (datetime.now(),))

    def _popola_lista_turni(self, monitor):
        cat_nome = (monitor.categoria or {}).get("nome", "?")
        try:
            self._lb_tt.delete(0, "end")
        except Exception:
            return
        if not monitor.tt_filtrato:
            self._lb_tt.insert("end",
                "  Nessun turno rilevato per categoria '%s'." % cat_nome)
            self._lb_tt.insert("end", "  Possibili cause:")
            self._lb_tt.insert("end",
                "  - Time table dell'evento non ancora pubblicato")
            self._lb_tt.insert("end",
                "  - Parser non riconosce il layout di questa pagina")
            self._lb_tt.insert("end",
                "  - Il nome categoria non matcha esattamente "
                "quello del time table")
            return
        for r in monitor.tt_filtrato:
            cat_short = (r.get("categoria") or "")[:25]
            turno = (r.get("turno") or "")[:30]
            ora_orig = r.get("ora", "?")
            dt = _ora_to_dt(ora_orig)
            if dt and monitor.delay_min:
                dt = dt + timedelta(minutes=monitor.delay_min)
                ora_eff = dt.strftime("%H:%M")
            else:
                ora_eff = ora_orig
            self._lb_tt.insert("end",
                "  %s   %-25s  %s" % (ora_eff, cat_short, turno))

    def _delay_str(self, m):
        if not m:
            return "0 min"
        return "%+d min" % int(m)

    def _aggiungi_delay(self, minuti):
        monitor = AssistenteGaraMonitor.get(self._top)
        if monitor is None:
            return
        monitor.aggiungi_delay(minuti)
        try:
            self._lbl_delay.config(text=self._delay_str(monitor.delay_min))
        except Exception:
            pass
        # Aggiorna lista turni con nuovi orari (effetto del delay)
        try:
            self._popola_lista_turni(monitor)
        except Exception:
            pass
        # Trigger update immediato del countdown
        self._on_tick_ui(*monitor.trova_prossimo() + (datetime.now(),))

    def _reset_delay(self):
        monitor = AssistenteGaraMonitor.get(self._top)
        if monitor is None:
            return
        monitor.imposta_delay(0)
        try:
            self._lbl_delay.config(text="0 min")
        except Exception:
            pass
        try:
            self._popola_lista_turni(monitor)
        except Exception:
            pass
        self._on_tick_ui(*monitor.trova_prossimo() + (datetime.now(),))

    def _on_tick_ui(self, prossimo, dt_target, now):
        """Listener registrato sul monitor: aggiorna le label del
        countdown ad ogni tick (1 Hz)."""
        c = self.c
        try:
            if not self._cd_frame.winfo_exists():
                # UI distrutta (utente ha cambiato schermata): deregistra.
                m = AssistenteGaraMonitor.get(self._top)
                if m and self._tick_listener:
                    m.remove_tick_listener(self._tick_listener)
                    self._tick_listener = None
                return
        except Exception:
            return
        if prossimo is None or dt_target is None:
            try:
                self._lbl_prossimo.config(
                    text="Nessun turno futuro per questa categoria oggi",
                    bg=c["sfondo_celle"], fg=c["testo_dim"])
                self._lbl_countdown.config(text="--:--",
                                            bg=c["sfondo_celle"],
                                            fg=c["testo_dim"])
                self._lbl_alert.config(text="", bg=c["sfondo_celle"],
                                        fg=c["sfondo_celle"])
                self._cd_frame.config(bg=c["sfondo_celle"])
            except Exception:
                pass
            return
        delta = dt_target - now
        secs = int(delta.total_seconds())
        mins = secs // 60
        ore = mins // 60
        mm = mins % 60
        ss = secs % 60
        if ore > 0:
            cd_str = "%d:%02d:%02d" % (ore, mm, ss)
        else:
            cd_str = "%02d:%02d" % (mm, ss)
        # Stato visivo
        if mins <= self.SOGLIA_AVVIA_MIN:
            bg = "#660000"
            fg = "#ff4444"
            alert = ">>> AVVIA MOTORE <<<"
            if (now.second % 2) == 0:
                bg = "#ff4444"
                fg = "#000000"
        elif mins <= self.SOGLIA_PREP_MIN:
            bg = "#664400"
            fg = "#ffaa00"
            alert = ">>> PREPARARE LA VETTURA <<<"
        else:
            bg = c["sfondo_celle"]
            fg = c["dati"]
            alert = ""
        try:
            txt_prox = "Prossimo: %s   %s   alle %s" % (
                prossimo.get("categoria", "?")[:25],
                prossimo.get("turno", "")[:30],
                dt_target.strftime("%H:%M"))
            self._lbl_prossimo.config(text=txt_prox, bg=bg, fg=fg)
            self._lbl_countdown.config(text=cd_str, bg=bg, fg=fg)
            self._lbl_alert.config(text=alert, bg=bg, fg=fg)
            self._cd_frame.config(bg=bg)
        except Exception:
            pass

    # =================================================================
    #  Chiusura / controllo monitor
    # =================================================================
    def _chiudi_lasciando_monitor(self):
        """L'utente torna al menu di TrackMind. Il MONITOR resta vivo:
        il countdown continua e gli alert popup arrivano comunque."""
        # Deregistra tick listener UI (la UI sta sparendo)
        m = AssistenteGaraMonitor.get(self._top)
        if m and self._tick_listener:
            try:
                m.remove_tick_listener(self._tick_listener)
            except Exception:
                pass
            self._tick_listener = None
        if self._on_close:
            self._pulisci()
            self._on_close()
        elif not self._embedded:
            self.root.destroy()

    def _stop_monitor(self):
        """Spegne completamente il monitor: niente piu' countdown,
        niente piu' alert. L'utente vorra' rilanciare l'addon
        (lista eventi) per riattivarlo."""
        m = AssistenteGaraMonitor.get(self._top)
        if m:
            m.disattiva()
        if self._tick_listener:
            self._tick_listener = None
        # Torna alla schermata iniziale (lista eventi)
        self._schermata_iniziale()

    def _cambia_evento(self):
        """Spegne il monitor e torna alla lista eventi per scegliere
        un altro evento/categoria. Usa lo stesso path di stop."""
        self._stop_monitor()

    def _chiudi(self):
        # Chiamato come legacy. Non spegne il monitor.
        self._chiudi_lasciando_monitor()

    def run(self):
        if not self._embedded:
            self.root.mainloop()


# =====================================================================
#  ENTRY POINT STANDALONE (per test rapido)
# =====================================================================
if __name__ == "__main__":
    AssistenteGara().run()
