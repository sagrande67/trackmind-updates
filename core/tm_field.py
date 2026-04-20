"""
TMField v4.3 - Cursore stile WarGames (rettangolo verticale)
Tipi: S=Stringa N=Numero D=Data O=Ora F=Flag P=Password V=ValoriVirgola $=Valuta
"""

import tkinter as tk
from tkinter import font as tkfont
from typing import Optional, Callable
from datetime import datetime
import sys
from config_colori import carica_colori, FONT_MONO

# Dimensioni base (scala 1.0)
BASE_CELL_W    = 10   # Larghezza cella (stretto, stile WarGames)
BASE_CELL_H    = 22   # Altezza cella (alto, stile WarGames)
BASE_CELL_PAD  = 2
BASE_FONT_LABEL = 9
BASE_FONT_CELL  = 9
BASE_FONT_TYPE  = 7
DOT_CHAR = chr(183)

# Scala globale (impostata da trackmind all'avvio)
_scala = 1.0

def set_scala(s):
    global _scala
    _scala = max(0.5, min(3.0, float(s)))

def get_scala():
    return _scala

def _S(val):
    """Scala un valore intero."""
    return max(1, int(val * _scala))

def set_cell_params(size=None, pad=None, font_cell=None, font_label=None,
                    width=None, height=None):
    """Permette di personalizzare le dimensioni celle da configurazione.
    size: imposta dimensione base (W e H calcolati con rapporto WarGames ~2:3)
    width/height: override espliciti (priorita' su size)
    pad: spaziatura tra celle
    font_cell/font_label: dimensione font
    """
    global BASE_CELL_W, BASE_CELL_H, BASE_CELL_PAD, BASE_FONT_CELL, BASE_FONT_LABEL
    if size is not None:
        # Rapporto WarGames: larghezza ~80% e altezza ~125% del size
        BASE_CELL_W = max(8, int(size * 0.8))
        BASE_CELL_H = max(12, int(size * 1.25))
    if width is not None:
        BASE_CELL_W = max(8, int(width))
    if height is not None:
        BASE_CELL_H = max(12, int(height))
    if pad is not None:
        BASE_CELL_PAD = max(0, int(pad))
    if font_cell is not None:
        BASE_FONT_CELL = max(6, int(font_cell))
    if font_label is not None:
        BASE_FONT_LABEL = max(6, int(font_label))


class RetroField(tk.Frame):

    _SEPARATORS    = {"data": {2: "/", 5: "/"}, "ora": {2: ":"}}
    _FIXED_LENGTHS = {"data": 10, "ora": 5, "flag": 1}
    _ALIAS = {
        "s": "stringa", "stringa": "stringa", "n": "numero", "numero": "numero",
        "d": "data", "data": "data", "o": "ora", "ora": "ora",
        "f": "flag", "flag": "flag", "p": "password", "password": "password",
        "$": "valuta", "valuta": "valuta",
    }

    def __init__(self, parent, label="Campo", tipo="S", lunghezza=20,
                 on_enter=None, label_width=12):
        c = carica_colori()
        super().__init__(parent, bg=c["sfondo"])
        self._tipo = self._ALIAS.get(tipo.lower(), tipo.lower())
        self._on_enter = on_enter
        self._label_text = label
        self._has_focus = False
        # Id del timer di lampeggio, memorizzato per poterlo cancellare ed
        # evitare cicli concorrenti quando il focus rimbalza (tipico della
        # schermata di login, dove _forza_focus_once triggera piu' FocusIn
        # di fila - XSetInputFocus + click simulato + rifocus a 150/500 ms).
        self._blink_after_id = None
        self._blink_on = False

        # Dimensioni scalate
        self._cell_w = _S(BASE_CELL_W)
        self._cell_h = _S(BASE_CELL_H)
        self._cell_pad = _S(BASE_CELL_PAD)

        if self._tipo in self._FIXED_LENGTHS:
            self._total_len = self._FIXED_LENGTHS[self._tipo]
        else:
            self._total_len = max(1, lunghezza)

        self._separators = self._SEPARATORS.get(self._tipo, {})
        self._editable_positions = [i for i in range(self._total_len) if i not in self._separators]
        self._max_editable = len(self._editable_positions)
        self._chars = [""] * self._total_len
        self._cursor_pos = 0
        self._flag_value = False

        for pos, ch in self._separators.items():
            self._chars[pos] = ch

        # Font scalati
        self._font_label = tkfont.Font(family=FONT_MONO, size=_S(BASE_FONT_LABEL))
        self._font_cell  = tkfont.Font(family=FONT_MONO, size=_S(BASE_FONT_CELL), weight="bold")
        self._font_type  = tkfont.Font(family=FONT_MONO, size=_S(BASE_FONT_TYPE))

        self._build_ui(label_width)

        for ev, fn in [("<Button-1>", self._on_click), ("<FocusIn>", self._on_focus_in),
                        ("<FocusOut>", self._on_focus_out), ("<Key>", self._on_key),
                        ("<BackSpace>", self._on_backspace), ("<Delete>", self._on_delete),
                        ("<Left>", self._on_left), ("<Right>", self._on_right),
                        ("<Up>", self._on_up), ("<Down>", self._on_down),
                        ("<Home>", self._on_home), ("<End>", self._on_end),
                        ("<Return>", self._on_return), ("<Tab>", self._on_tab),
                        ("<Control-v>", self._on_paste), ("<Control-V>", self._on_paste),
                        ("<Control-c>", self._on_copy), ("<Control-C>", self._on_copy)]:
            self._canvas.bind(ev, fn)

    def _build_ui(self, label_width):
        c = carica_colori()
        cw = self._cell_w
        ch = self._cell_h
        cp = self._cell_pad

        row = tk.Frame(self, bg=c["sfondo"])
        row.pack(fill="x")

        tk.Label(row, text=self._label_text, bg=c["sfondo"], fg=c["label"],
                 font=self._font_label, anchor="w", width=label_width).pack(side="left", padx=(0, _S(4)))

        # Indicatore tipo campo rimosso (informazione interna, non utile all'utente)

        canvas_w = self._total_len * (cw + cp) + cp + 4
        canvas_h = ch + cp * 2 + 4

        self._canvas = tk.Canvas(row, width=canvas_w, height=canvas_h, bg=c["sfondo"],
                                  highlightthickness=0, highlightbackground=c["bordo_vuote"],
                                  highlightcolor=c["dati"], cursor="xterm")
        self._canvas.pack(side="left")
        self._canvas.config(takefocus=True)

        # Suffisso € per campi valuta
        if self._tipo == "valuta":
            tk.Label(row, text=" \u20ac", bg=c["sfondo"], fg=c["stato_avviso"],
                     font=self._font_label).pack(side="left")

        self._cell_rects = []
        self._cell_texts = []
        for i in range(self._total_len):
            x = cp + 2 + i * (cw + cp)
            y = cp + 2
            rect = self._canvas.create_rectangle(x, y, x+cw, y+ch,
                                                  fill=c["sfondo_celle"], outline=c["bordo_vuote"], width=0)
            self._cell_rects.append(rect)
            is_sep = i in self._separators
            display = self._separators[i] if is_sep else DOT_CHAR
            color = c["separatori"] if is_sep else c["puntini"]
            txt = self._canvas.create_text(x+cw//2, y+ch//2,
                                            text=display, fill=color, font=self._font_cell, anchor="center")
            self._cell_texts.append(txt)
        self._redraw()

    def _redraw(self):
        c = carica_colori()
        cursor_real = self._editable_positions[self._cursor_pos] if self._cursor_pos < self._max_editable else -1
        for i in range(self._total_len):
            is_sep = i in self._separators
            is_cursor = (i == cursor_real) and self._has_focus
            char = self._chars[i]

            if is_sep:
                display, fg, cell_bg = self._separators[i], c["separatori"], c["sfondo_celle"]
            elif self._tipo == "flag":
                display = "X" if self._flag_value else " "
                fg = c["dati"] if self._flag_value else c["puntini"]
                cell_bg = c["sfondo_celle_piene"] if self._flag_value else c["sfondo_celle"]
            elif char:
                display = "*" if self._tipo == "password" else char
                fg, cell_bg = c["dati"], c["sfondo_celle_piene"]
            else:
                display, fg, cell_bg = DOT_CHAR, c["puntini"], c["sfondo_celle"]

            outline = c["puntini"] if char or is_sep else c["bordo_vuote"]
            if is_cursor:
                cell_bg, fg, outline = c["cursore"], c["testo_cursore"], c["dati"]

            self._canvas.itemconfig(self._cell_rects[i], fill=cell_bg, outline=outline)
            self._canvas.itemconfig(self._cell_texts[i], text=display, fill=fg)

    # -- EVENTI --
    def _on_click(self, event):
        self._canvas.focus_set()
        cw = self._cell_w; cp = self._cell_pad
        idx = (event.x - cp - 2) // (cw + cp)
        idx = max(0, min(idx, self._total_len - 1))
        if idx in self._separators:
            for ei, ep in enumerate(self._editable_positions):
                if ep >= idx: self._cursor_pos = ei; break
        elif idx in self._editable_positions:
            self._cursor_pos = self._editable_positions.index(idx)
        self._redraw()

    def _cancel_blink(self):
        """Cancella il timer di lampeggio pendente, se esiste.
        Chiamato prima di ogni nuovo _blink() per evitare cicli concorrenti."""
        if self._blink_after_id is not None:
            try:
                self.after_cancel(self._blink_after_id)
            except Exception:
                pass
            self._blink_after_id = None

    def _on_focus_in(self, event):
        c = carica_colori()
        self._has_focus = True
        self._canvas.config(highlightbackground=c["dati"])
        self._redraw()
        # Cancella un eventuale blink residuo (focus rimbalzato sul login)
        # prima di avviare il nuovo ciclo, cosi' non girano due timer insieme.
        self._cancel_blink()
        self._blink_on = True
        self._blink()

    def _on_focus_out(self, event):
        c = carica_colori()
        self._has_focus = False
        self._canvas.config(highlightbackground=c["bordo_vuote"])
        self._redraw()
        # Niente timer residui: cosi' il prossimo _on_focus_in
        # parte "pulito" dal colore cursore acceso.
        self._cancel_blink()

    def _blink(self):
        # Se nel frattempo il focus e' andato via, niente nuova schedulata
        if not self._has_focus:
            self._blink_after_id = None
            return
        c = carica_colori()
        self._blink_on = not self._blink_on
        cr = self._editable_positions[self._cursor_pos] if self._cursor_pos < self._max_editable else -1
        if cr >= 0:
            if self._blink_on:
                self._canvas.itemconfig(self._cell_rects[cr], fill=c["cursore"])
                self._canvas.itemconfig(self._cell_texts[cr], fill=c["testo_cursore"])
            else:
                char = self._chars[cr]
                if self._tipo == "flag": display = "X" if self._flag_value else " "
                elif char and self._tipo == "password": display = "*"
                elif char: display = char
                else: display = DOT_CHAR
                bg = c["sfondo_celle_piene"] if char else c["sfondo_celle"]
                fg = c["dati"] if char else c["puntini"]
                self._canvas.itemconfig(self._cell_rects[cr], fill=bg)
                self._canvas.itemconfig(self._cell_texts[cr], fill=fg)
        # Memorizza l'id per poterlo cancellare se il focus rimbalza
        self._blink_after_id = self.after(530, self._blink)

    def _on_key(self, event):
        if getattr(self, '_readonly', False): return
        char = event.char
        if not char or ord(char) < 32: return
        if self._tipo == "flag":
            if char == " ": self._flag_value = not self._flag_value; self._redraw()
            return "break"
        if not self._is_valid_char(char): return "break"
        # Valuta: converte punto in virgola (formato italiano)
        if self._tipo == "valuta" and char == ".":
            char = ","
        if self._cursor_pos < self._max_editable:
            self._chars[self._editable_positions[self._cursor_pos]] = char
            if self._cursor_pos < self._max_editable - 1: self._cursor_pos += 1
            self._redraw()
        return "break"

    def _on_backspace(self, event):
        if getattr(self, '_readonly', False): return "break"
        if self._tipo == "flag": return "break"
        if self._cursor_pos > 0:
            rp = self._editable_positions[self._cursor_pos]
            if not self._chars[rp]: self._cursor_pos -= 1
            rp = self._editable_positions[self._cursor_pos]
            self._chars[rp] = ""; self._redraw()
        else:
            self._chars[self._editable_positions[0]] = ""; self._redraw()
        return "break"

    def _on_delete(self, event):
        if getattr(self, '_readonly', False): return "break"
        if self._tipo == "flag": return "break"
        if self._cursor_pos < self._max_editable:
            for i in range(self._cursor_pos, self._max_editable - 1):
                self._chars[self._editable_positions[i]] = self._chars[self._editable_positions[i+1]]
            self._chars[self._editable_positions[-1]] = ""; self._redraw()
        return "break"

    def _on_left(self, e):
        if self._cursor_pos > 0: self._cursor_pos -= 1; self._redraw()
        return "break"
    def _on_right(self, e):
        if self._cursor_pos < self._max_editable-1: self._cursor_pos += 1; self._redraw()
        return "break"
    def _on_up(self, e):
        e.widget.tk_focusPrev().focus_set()
        return "break"
    def _on_down(self, e):
        e.widget.tk_focusNext().focus_set()
        return "break"
    def _on_home(self, e): self._cursor_pos = 0; self._redraw(); return "break"
    def _on_end(self, e):
        for i, ep in enumerate(self._editable_positions):
            if not self._chars[ep]: self._cursor_pos = i; self._redraw(); return "break"
        self._cursor_pos = self._max_editable - 1; self._redraw(); return "break"

    def _on_return(self, event):
        if self._tipo == "flag":
            self._flag_value = not self._flag_value; self._redraw()
        elif self._tipo in ("data", "ora") and not self._has_data():
            now = datetime.now()
            if self._tipo == "data":
                self.set(now.strftime("%d%m%Y"))
            else:
                self.set(now.strftime("%H%M"))
        elif self._on_enter:
            self._on_enter(self.get())
        event.widget.tk_focusNext().focus_set()
        return "break"

    def _on_paste(self, event):
        """Ctrl+V: incolla testo dagli appunti nel campo."""
        if self._tipo == "flag": return "break"
        try:
            testo = self._canvas.clipboard_get().strip()
        except Exception:
            return "break"
        if not testo: return "break"
        # Pulisci campo e scrivi da posizione 0
        for ep in self._editable_positions:
            self._chars[ep] = ""
        self._cursor_pos = 0
        # Filtra solo caratteri validi e inserisci
        for ch in testo:
            if self._cursor_pos >= self._max_editable:
                break
            if self._is_valid_char(ch):
                self._chars[self._editable_positions[self._cursor_pos]] = ch
                self._cursor_pos += 1
        if self._cursor_pos > 0 and self._cursor_pos < self._max_editable:
            pass  # Cursore rimane dopo l'ultimo carattere incollato
        elif self._cursor_pos > 0:
            self._cursor_pos = self._max_editable - 1
        self._redraw()
        return "break"

    def _on_copy(self, event):
        """Ctrl+C: copia il contenuto del campo negli appunti."""
        val = self.get().strip()
        if val:
            self._canvas.clipboard_clear()
            self._canvas.clipboard_append(val)
        return "break"

    def _has_data(self):
        """Controlla se almeno una posizione editabile contiene un carattere."""
        return any(self._chars[ep] for ep in self._editable_positions)

    def _on_tab(self, event):
        if event.state & 0x1: event.widget.tk_focusPrev().focus_set()
        else: event.widget.tk_focusNext().focus_set()
        return "break"

    # -- VALIDAZIONE --
    def _is_valid_char(self, char):
        if self._tipo == "numero":
            if char.isdigit(): return True
            if char == "." and "." not in self._get_raw(): return True
            if char == "-" and self._cursor_pos == 0 and "-" not in self._get_raw(): return True
            return False
        elif self._tipo == "valuta":
            if char.isdigit(): return True
            # Virgola come separatore decimale (italiano)
            if char == "," and "," not in self._get_raw(): return True
            # Accetta anche punto (convertito in virgola)
            if char == "." and "," not in self._get_raw(): return True
            return False
        elif self._tipo in ("data", "ora"): return char.isdigit()
        return True

    # -- API --
    def get(self):
        if self._tipo == "flag": return "X" if self._flag_value else ""
        val = "".join(self._chars).strip()
        # Valuta: restituisce con punto decimale per calcoli
        if self._tipo == "valuta":
            val = val.replace(",", ".")
        return val

    def get_raw(self): return self._get_raw()
    def _get_raw(self): return "".join(self._chars[ep] for ep in self._editable_positions)

    def set(self, value):
        if self._tipo == "flag":
            self._flag_value = value.upper() in ("X","1","TRUE","SI","YES")
            self._redraw(); return
        for ep in self._editable_positions: self._chars[ep] = ""
        if self._tipo == "data": value = value.replace("/","").replace("-","").replace(".","")
        elif self._tipo == "ora": value = value.replace(":","").replace(".","")
        elif self._tipo == "valuta": value = value.replace(".", ",")  # Mostra virgola
        for i, ch in enumerate(value):
            if i < self._max_editable: self._chars[self._editable_positions[i]] = ch
        filled = sum(1 for ep in self._editable_positions if self._chars[ep])
        self._cursor_pos = min(filled, self._max_editable - 1)
        self._redraw()

    def clear(self):
        for ep in self._editable_positions: self._chars[ep] = ""
        self._flag_value = False; self._cursor_pos = 0; self._redraw()

    def is_complete(self):
        if self._tipo == "flag": return True
        return all(self._chars[ep] for ep in self._editable_positions)

    def validate(self):
        val = self.get()
        if self._tipo == "flag": return True, "OK"
        if not val.replace("/","").replace(":","").replace(".","").replace("-","").strip():
            return False, "Campo vuoto"
        if self._tipo == "numero":
            try: float(self._get_raw()); return True, "OK"
            except ValueError: return False, "Numero non valido"
        elif self._tipo == "valuta":
            try: float(self._get_raw().replace(",", ".")); return True, "OK"
            except ValueError: return False, "Importo non valido"
        elif self._tipo == "data":
            if not self.is_complete(): return False, "Data incompleta"
            try: datetime.strptime(val, "%d/%m/%Y"); return True, "OK"
            except ValueError: return False, "Data non valida"
        elif self._tipo == "ora":
            if not self.is_complete(): return False, "Ora incompleta"
            raw = self._get_raw(); hh, mm = int(raw[:2]), int(raw[2:])
            if 0 <= hh <= 23 and 0 <= mm <= 59: return True, "OK"
            return False, "Ora non valida"
        return True, "OK"

    def set_focus(self): self._canvas.focus_set()

    def set_readonly(self, readonly=True):
        """Blocca/sblocca l'editing del campo."""
        self._readonly = readonly
        c = carica_colori()
        if readonly:
            self._canvas.config(state="disabled", bg=c["sfondo"])
        else:
            self._canvas.config(state="normal", bg=c["sfondo"])
