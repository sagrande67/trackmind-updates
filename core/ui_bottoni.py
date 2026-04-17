"""
UI Bottoni v1.0 - Gestione centralizzata focus/navigazione bottoni TrackMind
Tutti i moduli (retrodb, editor_tabelle, crono, ecc.) usano queste funzioni.

Funzionalita':
  - Focus visivo: inversione bg/fg + bordo verde neon (#39ff14) al focus
  - Navigazione frecce: orizzontale, verticale, griglia
  - Flash rosso al click (150ms)
  - Enter invoca il bottone

Uso:
  from core.ui_bottoni import setup_bottoni, setup_griglia, flash_btn
"""

import tkinter as tk

# Cache colori originali per widget {widget_id: (bg, fg, relief, bd)}
_colori_cache = {}

# Flag per sospendere temporaneamente il focus visivo durante transizioni
_focus_sospeso = False


def sospendi_focus(attivo=True):
    """Sospende/riattiva il focus visivo. Usare durante transizioni schermata."""
    global _focus_sospeso
    _focus_sospeso = attivo


def focus_evidenzia(widget, on=True):
    """Evidenzia/ripristina focus su un bottone: inverte bg/fg."""
    if _focus_sospeso:
        return
    try:
        if not widget.winfo_exists():
            return
        if str(widget.cget("state")) == "disabled":
            return
    except (tk.TclError, AttributeError):
        return
    wid = id(widget)
    try:
        if on:
            # Salva colori originali (solo la prima volta)
            if wid not in _colori_cache:
                bg = widget.cget("bg")
                fg = widget.cget("fg")
                # Protezione: se bg e fg sono uguali, widget in stato sporco
                if bg == fg:
                    return
                _colori_cache[wid] = (
                    bg, fg,
                    widget.cget("relief"), str(widget.cget("bd")),
                )
            orig_bg, orig_fg = _colori_cache[wid][:2]
            # Focus: sfondo CHIARO + testo SCURO (inversione)
            widget.config(bg=orig_fg, fg=orig_bg,
                          activebackground=orig_fg, activeforeground=orig_bg,
                          relief="solid", bd=2,
                          highlightthickness=2, highlightcolor="#39ff14")
        else:
            # Senza focus: ripristina originali
            if wid in _colori_cache:
                orig_bg, orig_fg, orig_rel, orig_bd = _colori_cache[wid]
                widget.config(bg=orig_bg, fg=orig_fg,
                              activebackground=orig_bg, activeforeground=orig_fg,
                              highlightthickness=2, highlightcolor=orig_bg,
                              relief=orig_rel, bd=int(orig_bd))
    except (tk.TclError, AttributeError):
        pass


def _enter_invoca(event):
    """Enter su bottone -> invoca il comando."""
    try:
        w = event.widget
        if str(w["state"]) != "disabled":
            w.invoke()
    except (tk.TclError, AttributeError):
        pass
    return "break"


def setup_bottoni(bottoni, orizzontale=True):
    """Configura navigazione frecce + Enter + focus visivo su lista bottoni.
    orizzontale=True: Left/Right navigano, False: Up/Down navigano."""
    attivi = [b for b in bottoni if str(b["state"]) != "disabled"]
    if not attivi:
        return

    for btn in attivi:
        btn.bind("<Return>", _enter_invoca)
        btn.bind("<FocusIn>", lambda e, b=btn: focus_evidenzia(b, True))
        btn.bind("<FocusOut>", lambda e, b=btn: focus_evidenzia(b, False))

    # Frecce per spostarsi
    tasto_avanti = "<Right>" if orizzontale else "<Down>"
    tasto_indietro = "<Left>" if orizzontale else "<Up>"

    for i, btn in enumerate(attivi):
        if i < len(attivi) - 1:
            next_btn = attivi[i + 1]
            btn.bind(tasto_avanti, lambda e, b=next_btn: (b.focus_set(), "break")[-1])
        if i > 0:
            prev_btn = attivi[i - 1]
            btn.bind(tasto_indietro, lambda e, b=prev_btn: (b.focus_set(), "break")[-1])


def setup_griglia(bottoni, colonne):
    """Configura navigazione frecce 4 direzioni su griglia di bottoni.
    bottoni = lista piatta, colonne = numero colonne della griglia."""
    attivi = [(i, b) for i, b in enumerate(bottoni) if str(b["state"]) != "disabled"]
    if not attivi:
        return

    idx_map = {i: b for i, b in attivi}

    for grid_idx, btn in attivi:
        btn.bind("<Return>", _enter_invoca)
        btn.bind("<FocusIn>", lambda e, b=btn: focus_evidenzia(b, True))
        btn.bind("<FocusOut>", lambda e, b=btn: focus_evidenzia(b, False))

        riga = grid_idx // colonne
        col = grid_idx % colonne

        # Right
        for dc in range(1, colonne):
            next_c = col + dc
            next_idx = riga * colonne + next_c
            if next_c < colonne and next_idx in idx_map:
                btn.bind("<Right>", lambda e, b=idx_map[next_idx]: (b.focus_set(), "break")[-1])
                break
        # Left
        for dc in range(1, colonne):
            prev_c = col - dc
            prev_idx = riga * colonne + prev_c
            if prev_c >= 0 and prev_idx in idx_map:
                btn.bind("<Left>", lambda e, b=idx_map[prev_idx]: (b.focus_set(), "break")[-1])
                break
        # Down
        for dr in range(1, (len(bottoni) + colonne - 1) // colonne):
            next_r = riga + dr
            next_idx = next_r * colonne + col
            if next_idx in idx_map:
                btn.bind("<Down>", lambda e, b=idx_map[next_idx]: (b.focus_set(), "break")[-1])
                break
        # Up
        for dr in range(1, (len(bottoni) + colonne - 1) // colonne):
            prev_r = riga - dr
            prev_idx = prev_r * colonne + col
            if prev_r >= 0 and prev_idx in idx_map:
                btn.bind("<Up>", lambda e, b=idx_map[prev_idx]: (b.focus_set(), "break")[-1])
                break


def flash_btn(root, btn, cmd):
    """Ritorna un comando wrappato con flash rosso 150ms.
    root = widget principale per .after(), btn = bottone, cmd = funzione."""
    def _wrapper():
        try:
            wid = id(btn)
            # Colori originali dalla cache (sicuri)
            if wid in _colori_cache:
                orig_bg, orig_fg = _colori_cache[wid][:2]
            else:
                orig_bg = btn.cget("bg")
                orig_fg = btn.cget("fg")
                _colori_cache[wid] = (orig_bg, orig_fg,
                                       btn.cget("relief"), str(btn.cget("bd")))
            btn.config(bg="#ff0000", fg="#ffffff")
            root.after(150, lambda: _flash_exec(btn, orig_bg, orig_fg, cmd))
        except Exception:
            cmd()
    return _wrapper


def _flash_exec(btn, orig_bg, orig_fg, cmd):
    """Ripristina colori dopo flash e ri-applica focus se attivo."""
    try:
        btn.config(bg=orig_bg, fg=orig_fg)
        # Se il bottone ha ancora il focus, ri-applica inversione
        try:
            if btn == btn.focus_get():
                focus_evidenzia(btn, True)
        except (tk.TclError, AttributeError, KeyError):
            pass
    except Exception:
        pass
    cmd()


def flash_key(root, btn_map, op, cmd):
    """Flash bottone per nome operazione da scorciatoia tastiera.
    btn_map = dict {nome_op: widget_bottone}."""
    btn = btn_map.get(op)
    if btn:
        try:
            wid = id(btn)
            if wid in _colori_cache:
                orig_bg, orig_fg = _colori_cache[wid][:2]
            else:
                orig_bg = btn.cget("bg")
                orig_fg = btn.cget("fg")
            btn.config(bg="#ff0000", fg="#ffffff")
            root.after(150, lambda: _flash_exec(btn, orig_bg, orig_fg, cmd))
        except Exception:
            cmd()
    else:
        cmd()


def pulisci_cache():
    """Svuota la cache colori (chiamare quando si distruggono widget)."""
    _colori_cache.clear()


def init_focus_globale(root, colori):
    """Imposta defaults globali per highlight bottoni su un root/toplevel.
    colori = dict con chiavi 'dati' e 'sfondo'.
    NON usa bind_class (causava colori sporchi durante transizioni schermata).
    Il focus visivo viene gestito esplicitamente da setup_bottoni/setup_griglia."""
    root.option_add("*Button.highlightThickness", 2)
    root.option_add("*Button.highlightColor", colori.get("dati", "#39ff14"))
    root.option_add("*Button.highlightBackground", colori.get("sfondo", "#0a0a0a"))
