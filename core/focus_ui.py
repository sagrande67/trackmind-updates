"""TrackMind - helper UX per evidenziare la riga corrente nelle
Listbox e Treeview con stile retro verde-su-nero.

Problema risolto (v05.06.30, segnalato dai tester):
"quando scatta una selezione con i tasti freccia, nelle diverse
parti del software, non capiscono quando effettivamente il focus
e' su quella selezione".

Sui RetroField il cursore lampeggia, sui bottoni l'highlight si
vede chiaramente, ma sulle Listbox/Treeview il colore di selezione
nativo Tk e' poco distinguibile dal fondo verde scuro del tema, e
soprattutto sparisce quando il widget perde il focus tastiera.

Questa libreria espone due helper:
  - `evidenzia_listbox(lb, colori)`: per `tk.Listbox`
  - `evidenzia_treeview(tree, colori)`: per `ttk.Treeview`

Entrambe rendono la riga corrente con sfondo verde brillante
(`#39ff14`) e testo nero (`#0a0a0a`), VISIBILE anche quando il
widget non ha il focus tastiera. Bind automatici su select +
frecce + PgUp/PgDn + Home/End + click mouse.

Uso tipico:
    from focus_ui import evidenzia_listbox, evidenzia_treeview

    lb = tk.Listbox(parent, ...)
    lb.pack(...)
    # ... popola lb ...
    evidenzia_listbox(lb, colori=self.c)

L'helper e' idempotente: chiamarlo piu' volte non duplica i bind
(gli bind hanno `add="+"` per non sostituire eventuali handler
esistenti, e la funzione di refresh interna e' una closure).

Niente dipendenze esterne (solo tkinter stdlib)."""

import tkinter as tk


def evidenzia_listbox(lb, colori=None):
    """Evidenzia la riga corrente di una Listbox con sfondo verde
    brillante + testo nero. Persistente anche senza focus.

    Args:
        lb: istanza tk.Listbox gia' creata
        colori: dict con chiavi config_colori (sfondo, dati, ecc.).
                Se None usa default DEFAULT_COLORS.

    Ritorna: la funzione di refresh interna (callable senza
    argomenti) che si puo' richiamare manualmente dopo aver
    modificato il contenuto della Listbox dall'esterno.

    Bind aggiunti (con add="+", non sovrascrive handler esistenti):
        - <<ListboxSelect>>
        - <KeyRelease-Up>, <KeyRelease-Down>
        - <KeyRelease-Prior>, <KeyRelease-Next>  (PgUp/PgDn)
        - <KeyRelease-Home>, <KeyRelease-End>
        - <ButtonRelease-1>  (click mouse)
        - <FocusIn>          (rifresca quando il widget riceve focus)
    """
    c = colori or {}
    bg_normale = c.get("sfondo_celle", "#080808")
    fg_normale = c.get("dati", "#39ff14")
    bg_corrente = c.get("dati", "#39ff14")
    fg_corrente = c.get("sfondo", "#0a0a0a")

    def _refresh(_evt=None):
        try:
            sel = lb.curselection()
            n = lb.size()
            if sel:
                idx_sel = sel[0]
            else:
                # Se nessuna selezione, prova l'indice "active"
                # (la riga focused col dotbox tk)
                try:
                    idx_sel = lb.index("active")
                except tk.TclError:
                    idx_sel = -1
            for i in range(n):
                if i == idx_sel:
                    lb.itemconfig(i,
                                   bg=bg_corrente, fg=fg_corrente,
                                   selectbackground=bg_corrente,
                                   selectforeground=fg_corrente)
                else:
                    lb.itemconfig(i,
                                   bg=bg_normale, fg=fg_normale,
                                   selectbackground=bg_corrente,
                                   selectforeground=fg_corrente)
        except (tk.TclError, IndexError):
            pass

    eventi = (
        "<<ListboxSelect>>",
        "<KeyRelease-Up>", "<KeyRelease-Down>",
        "<KeyRelease-Prior>", "<KeyRelease-Next>",
        "<KeyRelease-Home>", "<KeyRelease-End>",
        "<ButtonRelease-1>", "<FocusIn>",
    )
    for ev in eventi:
        try:
            lb.bind(ev, _refresh, add="+")
        except tk.TclError:
            pass
    # Rimuove il dotbox tratteggiato di default (poco visibile sul
    # tema retro, sostituito dall'evidenziazione completa)
    try:
        lb.config(activestyle="none")
    except tk.TclError:
        pass
    # Render iniziale (popolato gia' da chiamante o vuoto)
    _refresh()
    return _refresh


def evidenzia_treeview(tree, colori=None, tag_name="focus_riga"):
    """Evidenzia la riga corrente di un Treeview con sfondo verde
    brillante + testo nero, applicando un tag dedicato. Persistente
    anche senza focus.

    Args:
        tree: istanza ttk.Treeview gia' creata
        colori: dict config_colori (None = default)
        tag_name: nome del tag interno (default "focus_riga"). Lascia
                  invariato a meno che il chiamante usi gia' un tag
                  con lo stesso nome.

    Ritorna: la funzione di refresh interna.

    Compatibile con altri tag esistenti sulla riga: aggiunge/rimuove
    solo `tag_name`, non tocca gli altri.
    """
    c = colori or {}
    bg_corrente = c.get("dati", "#39ff14")
    fg_corrente = c.get("sfondo", "#0a0a0a")

    try:
        tree.tag_configure(tag_name,
                            background=bg_corrente,
                            foreground=fg_corrente)
    except tk.TclError:
        pass

    def _refresh(_evt=None):
        try:
            sel = set(tree.selection())
            for iid in tree.get_children(""):
                tags = list(tree.item(iid, "tags") or [])
                if tag_name in tags:
                    tags.remove(tag_name)
                if iid in sel:
                    tags.append(tag_name)
                tree.item(iid, tags=tuple(tags))
        except tk.TclError:
            pass

    eventi = (
        "<<TreeviewSelect>>",
        "<KeyRelease-Up>", "<KeyRelease-Down>",
        "<KeyRelease-Prior>", "<KeyRelease-Next>",
        "<KeyRelease-Home>", "<KeyRelease-End>",
        "<ButtonRelease-1>", "<FocusIn>",
    )
    for ev in eventi:
        try:
            tree.bind(ev, _refresh, add="+")
        except tk.TclError:
            pass
    _refresh()
    return _refresh
