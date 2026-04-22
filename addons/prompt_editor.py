"""
TrackMind - Prompt Editor v1.1
Mini-notepad integrato per editare il system prompt dell'IA (ai_prompt.txt).
Stile terminale retro WarGames/Matrix con cursore a blocco verde.

Solo libreria standard Python 3.x + tkinter.
"""

import os
import sys
import tkinter as tk
from tkinter import font as tkfont

# Font monospace + helper colori centralizzati
try:
    from config_colori import FONT_MONO, carica_colori as _carica_colori
except ImportError:
    # Fallback robusto se lanciato in modalita' diversa (sys.path mancante)
    _here = os.path.dirname(os.path.abspath(__file__))
    _parent = os.path.dirname(_here)
    if _parent not in sys.path:
        sys.path.insert(0, _parent)
    try:
        from config_colori import FONT_MONO, carica_colori as _carica_colori
    except ImportError:
        FONT_MONO = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"
        def _carica_colori():
            return {}

# Guardia anti-popup di sistema (uConsole).
try:
    from core.focus_guard import proteggi_finestra_sicura as _proteggi_finestra
except Exception:
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        _parent = os.path.dirname(_here)
        if _parent not in sys.path:
            sys.path.insert(0, _parent)
        from core.focus_guard import proteggi_finestra_sicura as _proteggi_finestra
    except Exception:
        def _proteggi_finestra(root, **kwargs):
            return


# DPI scaling
_DPI_SCALE = 1.0


def _S(val):
    """Scala un valore per DPI."""
    return int(val * _DPI_SCALE)


def _get_prompt_path():
    """Ritorna il percorso del file ai_prompt.txt."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "ai_prompt.txt")


PROMPT_DEFAULT = """Sei un ingegnere di gara (race engineer) per automodelli RC scala 1/8 e 1/10.
Rispondi SEMPRE in italiano. Sii conciso, pratico e specifico."""


# =====================================================================
#  PROMPT EDITOR - Finestra Toplevel
# =====================================================================
class PromptEditor:
    """Finestra notepad per editare il system prompt IA.
    Cursore a blocco verde stile terminale WarGames.

    Uso:
        PromptEditor(root)          # Apre finestra editor
        PromptEditor(root, dpi=1.2) # Con DPI scaling
    """

    def __init__(self, master, dpi=1.0, on_close=None):
        global _DPI_SCALE
        _DPI_SCALE = dpi

        self.master = master
        self.c = _carica_colori()
        self._on_close = on_close
        self._modificato = False
        self._prompt_path = _get_prompt_path()
        self._cursore_visibile = True
        self._feedback_attivo = False

        # ── Finestra ──
        self.win = tk.Toplevel(master)
        self.win.title("PROMPT IA - Editor")
        self.win.configure(bg=self.c["sfondo"])
        self.win.resizable(True, True)
        self.win.transient(master)

        # Dimensione finestra
        w, h = _S(700), _S(550)
        try:
            mx = master.winfo_rootx() + master.winfo_width() // 2
            my = master.winfo_rooty() + master.winfo_height() // 2
            x = mx - w // 2
            y = my - h // 2
        except Exception:
            x, y = 100, 100
        self.win.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.win.minsize(_S(400), _S(300))

        # Font
        self._f_title = tkfont.Font(family=FONT_MONO, size=_S(11), weight="bold")
        self._f_text = tkfont.Font(family=FONT_MONO, size=_S(9))
        self._f_btn = tkfont.Font(family=FONT_MONO, size=_S(8), weight="bold")
        self._f_small = tkfont.Font(family=FONT_MONO, size=_S(8))
        self._f_status = tkfont.Font(family=FONT_MONO, size=_S(7))
        self._f_feedback = tkfont.Font(family=FONT_MONO, size=_S(12), weight="bold")

        self._build_ui()
        self._carica_prompt()

        # Bind
        self.win.protocol("WM_DELETE_WINDOW", self._chiudi)
        # Case-insensitive: funziona anche con CapsLock/Shift
        self.win.bind("<Control-s>", lambda e: self._salva())
        self.win.bind("<Control-S>", lambda e: self._salva())
        self.win.bind("<Control-r>", lambda e: self._ripristina_default())
        self.win.bind("<Control-R>", lambda e: self._ripristina_default())
        self.win.bind("<Escape>", lambda e: self._chiudi())

        # Focus
        self.win.focus_force()
        self._txt.focus_set()

        # Protezione popup di sistema (uConsole): mantiene l'editor sopra
        # a dialog di NetworkManager/keyring/ecc. Idempotente.
        _proteggi_finestra(self.win)

        # Avvia cursore lampeggiante a blocco
        self._blink_cursore()

    def _build_ui(self):
        """Costruisce l'interfaccia dell'editor."""
        c = self.c

        # ── HEADER ──
        header = tk.Frame(self.win, bg=c["sfondo"])
        header.pack(fill="x", padx=_S(10), pady=(_S(8), _S(4)))

        tk.Label(header, text="[ PROMPT IA ]", bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left")

        # Barra batteria all'estrema destra (prima della label stato)
        try:
            from core.sd_bar import BarraBatteria as _BarraBat
            from core.batteria import get_batteria_info as _get_bat_info
            _pct, _ = _get_bat_info()
            if _pct is not None:
                _BarraBat(header, get_info_func=_get_bat_info).pack(
                    side="right", padx=(6, 0))
        except Exception:
            pass

        # Indicatore stato modifica
        self._lbl_stato = tk.Label(header, text="", bg=c["sfondo"],
                                    fg=c["stato_ok"], font=self._f_small)
        self._lbl_stato.pack(side="right")

        # Linea separatore
        tk.Frame(self.win, bg=c["linee"], height=1).pack(fill="x", padx=_S(10))

        # ── BANNER FEEDBACK (nascosto, appare su azioni) ──
        self._feedback_frame = tk.Frame(self.win, bg=c["sfondo"])
        # Non packato inizialmente
        self._lbl_feedback = tk.Label(self._feedback_frame, text="",
                                       bg=c["sfondo"], fg=c["stato_ok"],
                                       font=self._f_feedback)
        self._lbl_feedback.pack(fill="x", pady=(_S(6), _S(6)))

        # ── AREA TESTO ──
        txt_frame = tk.Frame(self.win, bg=c["linee"], bd=0)
        txt_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(6), _S(4)))

        txt_inner = tk.Frame(txt_frame, bg=c["sfondo_celle"])
        txt_inner.pack(fill="both", expand=True, padx=1, pady=1)

        # Scrollbar
        scroll = tk.Scrollbar(txt_inner, orient="vertical",
                               bg=c["sfondo"], troughcolor=c["sfondo"],
                               activebackground=c["pulsanti_sfondo"])
        scroll.pack(side="right", fill="y")

        # Text widget con cursore a blocco (insertwidth largo)
        self._txt = tk.Text(
            txt_inner,
            bg=c["sfondo_celle"],
            fg=c["dati"],
            insertbackground=c["cursore"],
            insertwidth=_S(8),
            insertofftime=0,      # Niente blink nativo, lo gestiamo noi
            insertontime=1000,
            selectbackground=c["cursore"],
            selectforeground=c["sfondo"],
            font=self._f_text,
            wrap="word",
            undo=True,
            maxundo=-1,
            padx=_S(8),
            pady=_S(6),
            relief="flat",
            bd=0,
            yscrollcommand=scroll.set,
        )
        self._txt.pack(fill="both", expand=True)
        scroll.config(command=self._txt.yview)

        # Tag per cursore a blocco (simula evidenziazione carattere corrente)
        self._txt.tag_configure("cursore_blocco",
                                 background=c["cursore"],
                                 foreground=c["sfondo"])

        # Traccia modifiche e movimenti cursore
        self._txt.bind("<<Modified>>", self._on_modify)
        self._txt.bind("<KeyRelease>", self._aggiorna_cursore_blocco)
        self._txt.bind("<ButtonRelease-1>", self._aggiorna_cursore_blocco)

        # ── INFO RIGA ──
        info_bar = tk.Frame(self.win, bg=c["sfondo"])
        info_bar.pack(fill="x", padx=_S(10), pady=(_S(2), _S(2)))

        self._lbl_info = tk.Label(info_bar, text="", bg=c["sfondo"],
                                   fg=c["testo_dim"], font=self._f_status,
                                   anchor="w")
        self._lbl_info.pack(side="left")

        self._lbl_file = tk.Label(info_bar, text="", bg=c["sfondo"],
                                   fg=c["testo_dim"], font=self._f_status,
                                   anchor="e")
        self._lbl_file.pack(side="right")

        # ── BARRA BOTTONI ──
        tk.Frame(self.win, bg=c["linee"], height=1).pack(fill="x", padx=_S(10))

        btn_bar = tk.Frame(self.win, bg=c["sfondo"])
        btn_bar.pack(fill="x", padx=_S(10), pady=(_S(6), _S(8)))

        # Bottone SALVA
        self._btn_salva = tk.Button(
            btn_bar, text="SALVA [Ctrl+S]", font=self._f_btn,
            bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
            activebackground=c["linee"], activeforeground=c["stato_ok"],
            relief="ridge", bd=1, cursor="hand2",
            command=self._salva, width=_S(14))
        self._btn_salva.pack(side="left", padx=(_S(0), _S(6)))

        # Bottone DEFAULT
        btn_default = tk.Button(
            btn_bar, text="DEFAULT [Ctrl+R]", font=self._f_btn,
            bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
            activebackground=c["linee"], activeforeground=c["stato_avviso"],
            relief="ridge", bd=1, cursor="hand2",
            command=self._ripristina_default, width=_S(16))
        btn_default.pack(side="left", padx=(_S(0), _S(6)))

        # Bottone RICARICA
        btn_ricarica = tk.Button(
            btn_bar, text="RICARICA", font=self._f_btn,
            bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
            activebackground=c["linee"], activeforeground=c["pulsanti_testo"],
            relief="ridge", bd=1, cursor="hand2",
            command=self._ricarica, width=_S(10))
        btn_ricarica.pack(side="left")

        # Bottone CHIUDI a destra
        btn_chiudi = tk.Button(
            btn_bar, text="CHIUDI [Esc]", font=self._f_btn,
            bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
            activebackground=c["linee"], activeforeground=c["pulsanti_testo"],
            relief="ridge", bd=1, cursor="hand2",
            command=self._chiudi, width=_S(12))
        btn_chiudi.pack(side="right")

        # Help
        help_frame = tk.Frame(self.win, bg=c["sfondo"])
        help_frame.pack(fill="x", padx=_S(10), pady=(_S(0), _S(4)))
        tk.Label(help_frame,
                 text="Ctrl+S = Salva  |  Ctrl+R = Default  |  Ctrl+Z = Annulla  |  Esc = Chiudi",
                 bg=c["sfondo"], fg=c["puntini"], font=self._f_status).pack()

        # Timer info
        self._aggiorna_info()

    # ─────────────────────────────────────────────────────────────────
    #  CURSORE A BLOCCO LAMPEGGIANTE (stile terminale)
    # ─────────────────────────────────────────────────────────────────
    def _aggiorna_cursore_blocco(self, event=None):
        """Posiziona il tag cursore_blocco sul carattere sotto il cursore.
        Su righe vuote non applica il tag (evita illuminazione intera riga),
        lascia il cursore nativo (insertwidth) visibile."""
        self._txt.tag_remove("cursore_blocco", "1.0", "end")
        if self._cursore_visibile:
            try:
                pos = self._txt.index("insert")
                ch = self._txt.get(pos, pos + "+1c")
                if ch != "\n" and ch != "":
                    self._txt.tag_add("cursore_blocco", pos, pos + "+1c")
            except Exception:
                pass

    def _blink_cursore(self):
        """Lampeggio cursore a blocco: alterna visibile/invisibile."""
        try:
            if not self.win.winfo_exists():
                return
        except Exception:
            return

        self._cursore_visibile = not self._cursore_visibile
        if self._cursore_visibile:
            self._aggiorna_cursore_blocco()
        else:
            self._txt.tag_remove("cursore_blocco", "1.0", "end")

        try:
            self.win.after(530, self._blink_cursore)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────
    #  FEEDBACK VISIVO FORTE
    # ─────────────────────────────────────────────────────────────────
    def _mostra_feedback(self, messaggio, colore, durata=2500):
        """Mostra un banner di feedback grande e visibile sopra l'editor.
        Flash del bordo della finestra + banner colorato."""
        c = self.c

        # Aggiorna label feedback
        self._lbl_feedback.config(text=messaggio, fg=colore)

        # Mostra banner (pack prima dell'area testo)
        if not self._feedback_attivo:
            self._feedback_frame.pack(fill="x", padx=_S(10),
                                       after=self.win.winfo_children()[1])  # dopo linea separatore
            self._feedback_attivo = True

        # Flash sfondo del banner per attirare attenzione
        flash_bg = colore
        self._feedback_frame.config(bg=flash_bg)
        self._lbl_feedback.config(bg=flash_bg, fg=c["sfondo"])

        # Dopo 300ms torna a sfondo normale ma testo colorato
        def _fade1():
            try:
                self._feedback_frame.config(bg=c["sfondo"])
                self._lbl_feedback.config(bg=c["sfondo"], fg=colore)
            except Exception:
                pass

        # Dopo durata totale, nascondi il banner
        def _nascondi():
            try:
                self._feedback_frame.pack_forget()
                self._feedback_attivo = False
            except Exception:
                pass

        try:
            self.win.after(350, _fade1)
            self.win.after(durata, _nascondi)
        except Exception:
            pass

        # Aggiorna anche la label stato nell'header
        self._lbl_stato.config(text=messaggio, fg=colore)

    # ─────────────────────────────────────────────────────────────────
    #  CARICA / SALVA
    # ─────────────────────────────────────────────────────────────────
    def _carica_prompt(self):
        """Carica il contenuto di ai_prompt.txt nell'editor."""
        contenuto = ""
        if os.path.exists(self._prompt_path):
            try:
                with open(self._prompt_path, "r", encoding="utf-8") as f:
                    contenuto = f.read()
            except Exception as e:
                contenuto = "# ERRORE lettura: %s" % e
        else:
            contenuto = PROMPT_DEFAULT

        self._txt.delete("1.0", "end")
        self._txt.insert("1.0", contenuto)
        self._txt.edit_modified(False)
        self._modificato = False
        self._aggiorna_stato()

        # Info file
        self._lbl_file.config(text=os.path.basename(self._prompt_path))

    def _on_modify(self, event=None):
        """Callback quando il testo viene modificato."""
        if self._txt.edit_modified():
            self._modificato = True
            self._aggiorna_stato()

    def _aggiorna_stato(self):
        """Aggiorna indicatore stato nell'header."""
        c = self.c
        if self._modificato:
            self._lbl_stato.config(text="* MODIFICATO *", fg=c["stato_avviso"])
            self.win.title("PROMPT IA - Editor *")
        else:
            self._lbl_stato.config(text="Salvato", fg=c["stato_ok"])
            self.win.title("PROMPT IA - Editor")

    def _aggiorna_info(self):
        """Aggiorna barra info (posizione cursore, conteggio caratteri)."""
        try:
            pos = self._txt.index("insert")
            riga, col = pos.split(".")
            contenuto = self._txt.get("1.0", "end-1c")
            n_righe = contenuto.count("\n") + 1
            n_car = len(contenuto)
            self._lbl_info.config(
                text="Riga %s  Col %s  |  %d righe  %d caratteri" % (
                    riga, col, n_righe, n_car))
        except Exception:
            pass
        try:
            self.win.after(500, self._aggiorna_info)
        except Exception:
            pass

    def _salva(self):
        """Salva il contenuto nel file ai_prompt.txt."""
        contenuto = self._txt.get("1.0", "end-1c")
        try:
            with open(self._prompt_path, "w", encoding="utf-8") as f:
                f.write(contenuto)
            self._modificato = False
            self._txt.edit_modified(False)
            self._aggiorna_stato()

            # FEEDBACK FORTE: banner verde grande
            self._mostra_feedback(
                ">>> PROMPT SALVATO! <<<", self.c["stato_ok"], 3000)

            # Flash bottone salva
            self._flash_bottone(self._btn_salva, self.c["stato_ok"])

            print("[PromptEditor] Salvato: %s (%d car.)" % (
                self._prompt_path, len(contenuto)))
        except Exception as e:
            self._mostra_feedback(
                "!!! ERRORE: %s !!!" % e, self.c["stato_errore"], 4000)

    def _ricarica(self):
        """Ricarica il file da disco (scarta modifiche)."""
        self._carica_prompt()
        self._mostra_feedback(
            ">>> RICARICATO DA DISCO <<<", self.c["cerca_testo"], 2500)

    def _ripristina_default(self):
        """Ripristina il prompt di default nell'editor (non salva su disco)."""
        self._txt.delete("1.0", "end")
        self._txt.insert("1.0", PROMPT_DEFAULT)
        self._txt.edit_modified(True)
        self._modificato = True
        self._aggiorna_stato()
        self._mostra_feedback(
            "DEFAULT CARICATO - Premi SALVA per confermare",
            self.c["stato_avviso"], 3500)

    def _flash_bottone(self, btn, colore):
        """Flash visivo su un bottone: inverte i colori per un attimo."""
        c = self.c
        old_bg = btn.cget("bg")
        old_fg = btn.cget("fg")
        btn.config(bg=colore, fg=c["sfondo"])
        try:
            self.win.after(400, lambda: btn.config(bg=old_bg, fg=old_fg))
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────
    #  CHIUSURA
    # ─────────────────────────────────────────────────────────────────
    def _chiudi(self):
        """Chiude l'editor. Se ci sono modifiche non salvate, chiede conferma."""
        if self._modificato:
            self._chiedi_salvataggio()
        else:
            self._chiudi_effettivo()

    def _chiedi_salvataggio(self):
        """Mostra mini-dialog per salvataggio prima di chiudere."""
        c = self.c

        # Overlay
        dialog = tk.Frame(self.win, bg=c["sfondo"], bd=0)
        dialog.place(relx=0.5, rely=0.5, anchor="center",
                     width=_S(380), height=_S(140))

        # Bordo verde
        border = tk.Frame(dialog, bg=c["cursore"])
        border.pack(fill="both", expand=True)
        content = tk.Frame(border, bg=c["sfondo"])
        content.pack(fill="both", expand=True, padx=2, pady=2)

        tk.Label(content, text="!!! MODIFICHE NON SALVATE !!!",
                 bg=c["sfondo"], fg=c["stato_avviso"],
                 font=self._f_feedback).pack(pady=(_S(14), _S(10)))

        btn_row = tk.Frame(content, bg=c["sfondo"])
        btn_row.pack(pady=(_S(0), _S(12)))

        def _salva_e_chiudi():
            dialog.destroy()
            self._salva()
            self._chiudi_effettivo()

        def _chiudi_senza():
            dialog.destroy()
            self._chiudi_effettivo()

        def _annulla():
            dialog.destroy()
            self._txt.focus_set()

        tk.Button(btn_row, text="SALVA", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=_salva_e_chiudi, width=_S(10)).pack(side="left", padx=_S(5))

        tk.Button(btn_row, text="NON SALVARE", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["stato_errore"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=_chiudi_senza, width=_S(14)).pack(side="left", padx=_S(5))

        tk.Button(btn_row, text="ANNULLA", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=_annulla, width=_S(10)).pack(side="left", padx=_S(5))

    def _chiudi_effettivo(self):
        """Chiude la finestra per davvero."""
        try:
            self.win.destroy()
        except Exception:
            pass
        if self._on_close:
            try:
                self._on_close()
            except Exception:
                pass


# =====================================================================
#  Funzione di lancio (chiamata da retrodb.py)
# =====================================================================
def apri_prompt_editor(master, dpi=1.0, on_close=None):
    """Apre la finestra Prompt Editor.

    master: finestra tkinter parent
    dpi: fattore scala DPI
    on_close: callback opzionale alla chiusura

    Ritorna l'istanza PromptEditor.
    """
    return PromptEditor(master, dpi=dpi, on_close=on_close)


# =====================================================================
#  Standalone test
# =====================================================================
if __name__ == "__main__":
    root = tk.Tk()
    root.title("TrackMind")
    root.configure(bg="#0a0a0a")
    root.geometry("200x100")
    tk.Label(root, text="TrackMind", bg="#0a0a0a", fg="#39ff14").pack(pady=20)
    tk.Button(root, text="APRI EDITOR", bg="#1a3a1a", fg="#39ff14",
              command=lambda: apri_prompt_editor(root)).pack()
    root.mainloop()
