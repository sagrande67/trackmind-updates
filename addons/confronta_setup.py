"""
TrackMind - Confronta Setup v1.0
Confronto cross-setup: compara sessioni di prova tra setup diversi
per evidenziare miglioramenti/peggioramenti su tempi e configurazione.

Lanciato da retrodb.py in modalita' embedded (parent frame + on_close).
Riceve una lista di record setup con i rispettivi tempi.

Flusso:
  1. Schermata selezione: elenco multi-select dei record setup
  2. Schermata confronto: tabella diff + riepilogo tempi + delta
  3. Grafico overlay: Canvas tkinter con curve sovrapposte

Navigazione:
  - Spazio = toggle selezione record
  - Enter = avvia confronto con i record selezionati
  - G = grafico overlay dalle sessioni confrontate
  - Esc = torna indietro
"""

import os
import sys
import json
import tkinter as tk
from tkinter import ttk

# Font monospace per compatibilità cross-platform
try:
    from config_colori import FONT_MONO
except ImportError:
    FONT_MONO = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"


# =================================================================
#  UTILITA'
# =================================================================

def _fmt(sec):
    """Formatta secondi in M:SS.ddd o SS.ddd."""
    if not sec or sec <= 0:
        return "-"
    m = int(sec) // 60
    s = sec - m * 60
    if m > 0:
        return "%d:%06.3f" % (m, s)
    return "%.3f" % s


def _cerca_sessioni(dati_dir, record_id):
    """Cerca tutti i file lap_[record_id]_*.json nella cartella dati/.
    Coerente con crono.py _trova_sessioni() che cerca in dati_dir diretto."""
    sessioni = []
    if not dati_dir or not record_id:
        return sessioni
    if not os.path.isdir(dati_dir):
        return sessioni
    prefisso = "lap_%s_" % record_id
    for f in sorted(os.listdir(dati_dir)):
        if f.startswith(prefisso) and f.endswith(".json"):
            try:
                path = os.path.join(dati_dir, f)
                with open(path, "r", encoding="utf-8") as fh:
                    s = json.load(fh)
                sessioni.append(s)
            except Exception:
                pass
    return sessioni


def _stats_sessioni(sessioni):
    """Calcola statistiche aggregate da una lista di sessioni."""
    tutti_tempi = []
    best_assoluto = 0
    n_giri_tot = 0
    n_sessioni = len(sessioni)

    for s in sessioni:
        giri = s.get("giri", [])
        tempi = [g["tempo"] for g in giri
                 if g.get("stato") in ("valido", None) and g.get("tempo", 0) > 0]
        tutti_tempi.extend(tempi)
        n_giri_tot += len(tempi)

    if tutti_tempi:
        best_assoluto = min(tutti_tempi)
        media = sum(tutti_tempi) / len(tutti_tempi)
        # Consistenza: % giri entro 3% dalla media
        soglia = media * 1.03
        consist = sum(1 for t in tutti_tempi if t <= soglia)
        consist_pct = (consist / len(tutti_tempi)) * 100
        # Top 5 media (migliori 5 giri)
        top5 = sorted(tutti_tempi)[:5]
        media_top5 = sum(top5) / len(top5) if top5 else 0
    else:
        best_assoluto = 0
        media = 0
        consist_pct = 0
        media_top5 = 0

    return {
        "n_sessioni": n_sessioni,
        "n_giri": n_giri_tot,
        "best": best_assoluto,
        "media": media,
        "media_top5": media_top5,
        "consist_pct": consist_pct,
    }


# Colori linee grafico (riusa stessa palette di crono.py)
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


# =================================================================
#  CLASSE PRINCIPALE
# =================================================================

class ConfrontaSetup:
    """Modulo confronto cross-setup. Embedded in retrodb.py."""

    def __init__(self, parent, db, table_def, ref_dbs, dati_dir,
                 indici_visibili, sessione, on_close=None, colori=None):
        """
        Args:
            parent:           Frame padre (self._vista di retrodb)
            db:               Database della tabella corrente
            table_def:        Definizione tabella (.def)
            ref_dbs:          Dict con database riferimenti (telai, motori, ecc.)
            dati_dir:         Cartella dati principale
            indici_visibili:  Lista indici record visibili (filtrati per utente)
            sessione:         Sessione utente corrente
            on_close:         Callback per tornare alla schermata precedente
            colori:           Schema colori (dict)
        """
        self.root = parent
        self.db = db
        self.table_def = table_def
        self.ref_dbs = ref_dbs
        self.dati_dir = dati_dir
        self.indici = list(indici_visibili)
        self.sessione = sessione
        self.on_close = on_close

        if colori:
            self.c = colori
        else:
            try:
                from config_colori import carica_colori
                self.c = carica_colori()
            except Exception:
                self.c = {}

        # Font (riusa pattern standard TrackMind)
        try:
            from retrodb import _S
            self._S = _S
        except ImportError:
            self._S = lambda x: x
        _S = self._S
        self._f_title = (FONT_MONO, _S(11), "bold")
        self._f_label = (FONT_MONO, _S(9))
        self._f_btn = (FONT_MONO, _S(8))
        self._f_small = (FONT_MONO, _S(7))
        self._f_data = (FONT_MONO, _S(8))

        # Toplevel per binding tastiera (usa parent.winfo_toplevel)
        self._top = parent.winfo_toplevel()

        # Selezione record per confronto
        self._sel_indices = set()

        # Avvia con schermata selezione
        self._schermata_selezione()

    # =================================================================
    #  UTILITA' UI
    # =================================================================

    def _pulisci(self):
        """Rimuove tutti i widget dal frame padre."""
        for w in self.root.winfo_children():
            w.destroy()
        # Unbind
        for seq in ("<Escape>", "<Return>", "<space>", "<g>", "<G>",
                    "<a>", "<A>", "<Up>", "<Down>"):
            try:
                self._top.unbind(seq)
            except Exception:
                pass

    def _status_label(self, parent, testo):
        """Crea label status in basso."""
        c = self.c
        lbl = tk.Label(parent, text=testo, bg=c.get("sfondo", "#0a0a0a"),
                       fg=c.get("testo_dim", "#555555"),
                       font=self._f_small, anchor="w")
        lbl.pack(fill="x", padx=10, pady=(0, 2), side="bottom")
        return lbl

    # =================================================================
    #  1. SCHERMATA SELEZIONE MULTI-RECORD
    # =================================================================

    def _schermata_selezione(self):
        """Elenco record con selezione multipla per confronto."""
        self._pulisci()
        c = self.c
        _S = self._S

        # ── Stile Treeview ──
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Confronta.Treeview",
            background=c.get("sfondo_celle", "#111111"),
            foreground=c.get("dati", "#39ff14"),
            fieldbackground=c.get("sfondo_celle", "#111111"),
            font=(FONT_MONO, _S(8)),
            rowheight=_S(22), borderwidth=0)
        style.configure("Confronta.Treeview.Heading",
            background=c.get("pulsanti_sfondo", "#1a3a1a"),
            foreground=c.get("pulsanti_testo", "#39ff14"),
            font=(FONT_MONO, _S(8), "bold"), borderwidth=1, relief="ridge")
        style.map("Confronta.Treeview",
            background=[("selected", c.get("cursore", "#39ff14"))],
            foreground=[("selected", c.get("testo_cursore", "#0a0a0a"))])

        # ── Header ──
        header = tk.Frame(self.root, bg=c.get("sfondo", "#0a0a0a"))
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))

        tk.Button(header, text="< INDIETRO", font=self._f_small,
                  bg=c.get("pulsanti_sfondo", "#1a3a1a"),
                  fg=c.get("pulsanti_testo", "#39ff14"),
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._esci).pack(side="left")

        tk.Label(header, text="  CONFRONTA SETUP  |  Seleziona 2+ record",
                 bg=c.get("sfondo", "#0a0a0a"),
                 fg=c.get("dati", "#39ff14"),
                 font=self._f_title).pack(side="left", padx=(_S(8), 0))

        self._sel_count_label = tk.Label(header, text="0 selezionati",
                 bg=c.get("sfondo", "#0a0a0a"),
                 fg=c.get("testo_dim", "#555555"),
                 font=self._f_small)
        self._sel_count_label.pack(side="right")

        tk.Frame(self.root, bg=c.get("linee", "#333333"),
                 height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(2)))

        # ── Prepara colonne ──
        colonne = []
        # Colonna selezione (checkbox visuale)
        colonne.append(("_sel", " ", _S(30)))
        # Campo chiave
        campo_k = self.table_def.get_campo_chiave()
        if campo_k:
            colonne.append((campo_k["nome"], campo_k["nome"].replace("_", " "), _S(60)))
        # Data
        colonne.append(("Data", "Data", _S(80)))
        # Riferimenti principali
        for rif in self.table_def.riferimenti:
            alias = rif.get("alias", rif["tabella"])
            if alias.lower() == "piste":
                continue  # Pista e' uguale per tutti, non serve
            col_id = "_ref_%s" % alias
            colonne.append((col_id, alias.upper().replace("_", " "), _S(120)))
        # Sessioni e best lap
        colonne.append(("_sessioni", "SESS", _S(40)))
        colonne.append(("_best", "BEST", _S(70)))
        colonne.append(("_media", "MEDIA", _S(70)))

        col_ids = [col[0] for col in colonne]

        # ── Treeview ──
        tree_frame = tk.Frame(self.root, bg=c.get("sfondo", "#0a0a0a"))
        tree_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(2), _S(4)))

        self._tree = ttk.Treeview(tree_frame, columns=col_ids,
                                   show="headings", style="Confronta.Treeview",
                                   selectmode="browse")
        # Configura colonne
        for col_id, titolo, larg in colonne:
            self._tree.heading(col_id, text=titolo)
            self._tree.column(col_id, width=larg, minwidth=_S(30))

        # Scrollbar
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._tree.pack(fill="both", expand=True)

        # Tag per righe selezionate
        self._tree.tag_configure("checked",
            background=c.get("pulsanti_sfondo", "#1a3a1a"),
            foreground=c.get("stato_avviso", "#ffaa00"))
        self._tree.tag_configure("normal",
            background=c.get("sfondo_celle", "#111111"),
            foreground=c.get("dati", "#39ff14"))

        # ── Popola righe ──
        self._righe_data = {}  # iid -> {indice, record_id, record, sessioni, stats}
        for idx in self.indici:
            rec = self.db.leggi(idx)
            if not rec:
                continue

            # Record ID
            record_id = ""
            campo_k = self.table_def.get_campo_chiave()
            if campo_k:
                record_id = str(rec.get(campo_k["nome"], "")).strip()
            if not record_id:
                record_id = "rec_%d" % idx
            # Sanitizza per ricerca file
            rid_file = record_id.replace("/", "-").replace("\\", "-").replace(":", "-").replace(" ", "_")

            # Cerca sessioni tempi
            sessioni = _cerca_sessioni(self.dati_dir, rid_file)
            stats = _stats_sessioni(sessioni)

            # Valori colonne
            valori = []
            for col_id, _, _ in colonne:
                if col_id == "_sel":
                    valori.append("  ")
                elif col_id == "_sessioni":
                    valori.append(str(stats["n_sessioni"]) if stats["n_sessioni"] else "-")
                elif col_id == "_best":
                    valori.append(_fmt(stats["best"]) if stats["best"] else "-")
                elif col_id == "_media":
                    valori.append(_fmt(stats["media"]) if stats["media"] else "-")
                elif col_id == "Data":
                    valori.append(str(rec.get("Data", "")).strip())
                elif col_id.startswith("_ref_"):
                    alias = col_id[5:]
                    ref_db = self.ref_dbs.get(alias) or self.ref_dbs.get(alias.lower())
                    rif_def = None
                    for r in self.table_def.riferimenti:
                        if r.get("alias", r["tabella"]) == alias:
                            rif_def = r; break
                    if rif_def and ref_db:
                        campo_rec = rif_def.get("campo_record", rif_def["campo_chiave"])
                        codice = str(rec.get(campo_rec, "")).strip()
                        desc = self._risolvi_ref(ref_db, rif_def, codice)
                        valori.append(desc or codice)
                    else:
                        valori.append("")
                else:
                    valori.append(str(rec.get(col_id, "")).strip())

            iid = str(idx)
            self._tree.insert("", "end", iid=iid, values=valori, tags=("normal",))
            self._righe_data[iid] = {
                "indice": idx,
                "record_id": rid_file,
                "record": rec,
                "sessioni": sessioni,
                "stats": stats,
            }

        # ── Binding tastiera ──
        self._top.bind("<space>", lambda e: self._toggle_selezione())
        self._top.bind("<Return>", lambda e: self._avvia_confronto())
        self._top.bind("<Escape>", lambda e: self._esci())

        # Status
        self._status_label(self.root,
            "SPAZIO = Seleziona/Deseleziona  |  ENTER = Confronta  |  ESC = Indietro")

        # Focus sul primo record
        figli = self._tree.get_children()
        if figli:
            self._tree.focus(figli[0])
            self._tree.selection_set(figli[0])

    def _risolvi_ref(self, ref_db, rif_def, codice):
        """Risolvi codice riferimento a descrizione leggibile."""
        if not codice:
            return ""
        rk = ref_db.table_def.get_campo_chiave()
        campo_lookup = rk["nome"] if rk else rif_def["campo_chiave"]
        for ri in range(len(ref_db.records)):
            r = ref_db.leggi(ri)
            if r and str(r.get(campo_lookup, "")).strip() == codice:
                parti = []
                for c in ref_db.table_def.get_campi_non_chiave():
                    val = str(r.get(c["nome"], "")).strip()
                    if val:
                        parti.append(val)
                return " ".join(parti[:3]) if parti else codice
        return codice

    def _toggle_selezione(self):
        """Toggle selezione del record sotto il cursore."""
        focused = self._tree.focus()
        if not focused:
            return
        idx = int(focused)
        if idx in self._sel_indices:
            self._sel_indices.discard(idx)
            self._tree.item(focused, tags=("normal",))
            # Aggiorna colonna checkbox
            vals = list(self._tree.item(focused, "values"))
            vals[0] = "  "
            self._tree.item(focused, values=vals)
        else:
            self._sel_indices.add(idx)
            self._tree.item(focused, tags=("checked",))
            vals = list(self._tree.item(focused, "values"))
            vals[0] = " *"
            self._tree.item(focused, values=vals)
        # Aggiorna contatore
        self._sel_count_label.config(text="%d selezionati" % len(self._sel_indices))

    def _avvia_confronto(self):
        """Avvia confronto se almeno 2 record selezionati."""
        if len(self._sel_indices) < 2:
            # Se meno di 2, mostra avviso
            self._sel_count_label.config(
                text="Serve selezionare almeno 2 record!",
                fg=self.c.get("stato_errore", "#ff5555"))
            return
        # Raccogli dati per i record selezionati
        self._setup_confronto = []
        for iid, data in self._righe_data.items():
            if data["indice"] in self._sel_indices:
                self._setup_confronto.append(data)
        self._schermata_confronto()

    # =================================================================
    #  2. SCHERMATA CONFRONTO (tabella diff + tempi + delta)
    # =================================================================

    def _schermata_confronto(self):
        """Mostra confronto tra i setup selezionati."""
        self._pulisci()
        c = self.c
        _S = self._S

        n_setup = len(self._setup_confronto)
        if n_setup < 2:
            return

        # ── Header ──
        header = tk.Frame(self.root, bg=c.get("sfondo", "#0a0a0a"))
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))

        tk.Button(header, text="< SELEZIONE", font=self._f_small,
                  bg=c.get("pulsanti_sfondo", "#1a3a1a"),
                  fg=c.get("pulsanti_testo", "#39ff14"),
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._schermata_selezione).pack(side="left")

        tk.Label(header, text="  CONFRONTO  |  %d setup" % n_setup,
                 bg=c.get("sfondo", "#0a0a0a"),
                 fg=c.get("dati", "#39ff14"),
                 font=self._f_title).pack(side="left", padx=(_S(8), 0))

        # Bottone GRAFICO
        btn_grafico = tk.Button(header, text="GRAFICO (G)", font=self._f_btn,
                  bg=c.get("pulsanti_sfondo", "#1a3a1a"),
                  fg=c.get("stato_avviso", "#ffaa00"),
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._schermata_grafico)
        btn_grafico.pack(side="right", padx=_S(4))

        tk.Frame(self.root, bg=c.get("linee", "#333333"),
                 height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(2)))

        # ── Area scrollabile ──
        canvas_scroll = tk.Canvas(self.root, bg=c.get("sfondo", "#0a0a0a"),
                                   highlightthickness=0)
        vsb = ttk.Scrollbar(self.root, orient="vertical", command=canvas_scroll.yview)
        inner = tk.Frame(canvas_scroll, bg=c.get("sfondo", "#0a0a0a"))

        inner.bind("<Configure>", lambda e: canvas_scroll.configure(
            scrollregion=canvas_scroll.bbox("all")))
        canvas_scroll.create_window((0, 0), window=inner, anchor="nw")
        canvas_scroll.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas_scroll.pack(fill="both", expand=True, padx=_S(10), pady=(_S(2), _S(4)))

        # Scroll con mouse
        def _on_mouse(event):
            canvas_scroll.yview_scroll(-1 * (event.delta // 120), "units")
        canvas_scroll.bind("<MouseWheel>", _on_mouse)
        # Linux
        canvas_scroll.bind("<Button-4>", lambda e: canvas_scroll.yview_scroll(-3, "units"))
        canvas_scroll.bind("<Button-5>", lambda e: canvas_scroll.yview_scroll(3, "units"))

        # ── Determina setup base (primo) e altri ──
        base_data = self._setup_confronto[0]
        base_rec = base_data["record"]
        base_stats = base_data["stats"]

        # ─── SEZIONE 1: RIEPILOGO TEMPI ───
        tk.Label(inner, text="RIEPILOGO TEMPI",
                 bg=c.get("sfondo", "#0a0a0a"),
                 fg=c.get("stato_avviso", "#ffaa00"),
                 font=self._f_title).pack(anchor="w", pady=(_S(6), _S(2)))

        # Tabella tempi
        tempi_frame = tk.Frame(inner, bg=c.get("sfondo", "#0a0a0a"))
        tempi_frame.pack(fill="x", pady=(_S(2), _S(8)))

        # Header
        headers = ["SETUP", "DATA", "SESS", "GIRI", "BEST", "MEDIA", "TOP5", "CONSIST", "DELTA BEST"]
        for j, h in enumerate(headers):
            tk.Label(tempi_frame, text=h, bg=c.get("pulsanti_sfondo", "#1a3a1a"),
                     fg=c.get("pulsanti_testo", "#39ff14"),
                     font=(FONT_MONO, _S(7), "bold"), width=_S(11),
                     relief="ridge", bd=1).grid(row=0, column=j, sticky="nsew", padx=0, pady=0)

        # Righe dati - ordinate per best time
        sorted_setup = sorted(self._setup_confronto,
                              key=lambda x: x["stats"]["best"] if x["stats"]["best"] else 999)
        best_globale = sorted_setup[0]["stats"]["best"] if sorted_setup[0]["stats"]["best"] else 0

        for i, sd in enumerate(sorted_setup):
            rec = sd["record"]
            st = sd["stats"]
            rid = sd["record_id"]

            # Nome setup (campo chiave o primi campi significativi)
            campo_k = self.table_def.get_campo_chiave()
            nome = str(rec.get(campo_k["nome"], rid)) if campo_k else rid

            data_val = str(rec.get("Data", "")).strip()

            # Delta rispetto al best globale
            if st["best"] and best_globale and st["best"] != best_globale:
                delta = st["best"] - best_globale
                delta_str = "+%.3f" % delta
                delta_fg = c.get("stato_errore", "#ff5555")
            elif st["best"] and st["best"] == best_globale:
                delta_str = "REF"
                delta_fg = c.get("stato_ok", "#39ff14")
            else:
                delta_str = "-"
                delta_fg = c.get("testo_dim", "#555555")

            vals = [
                nome[:14],
                data_val[-5:] if len(data_val) > 5 else data_val,
                str(st["n_sessioni"]),
                str(st["n_giri"]),
                _fmt(st["best"]),
                _fmt(st["media"]),
                _fmt(st["media_top5"]),
                "%.0f%%" % st["consist_pct"] if st["consist_pct"] else "-",
                delta_str,
            ]

            # Colore riga: primo (migliore) = verde, altri = default
            riga_fg = c.get("stato_ok", "#39ff14") if i == 0 else c.get("dati", "#39ff14")

            for j, v in enumerate(vals):
                fg = delta_fg if j == len(vals) - 1 else riga_fg
                tk.Label(tempi_frame, text=v,
                         bg=c.get("sfondo_celle", "#111111"), fg=fg,
                         font=self._f_data, width=_S(11),
                         relief="flat", bd=1, anchor="center").grid(
                    row=i + 1, column=j, sticky="nsew", padx=0, pady=0)

        # ─── SEZIONE 2: DIFFERENZE SETUP ───
        tk.Label(inner, text="DIFFERENZE SETUP",
                 bg=c.get("sfondo", "#0a0a0a"),
                 fg=c.get("stato_avviso", "#ffaa00"),
                 font=self._f_title).pack(anchor="w", pady=(_S(10), _S(2)))

        # Confronta campo per campo tra tutti i setup selezionati
        # Mostra solo i campi che differiscono
        sezione_corrente = ""
        diff_trovate = False

        # Raccolta campi per sezione
        sezioni_def = []
        sez_corrente_nome = ""
        sez_corrente_campi = []
        for linea in self.table_def._raw_lines if hasattr(self.table_def, '_raw_lines') else []:
            pass  # Fallback: usa table_def.campi direttamente

        # Costruisci lista campi con sezione dal .def
        all_campi = []
        for campo in self.table_def.campi:
            nome_campo = campo["nome"]
            # Salta campi interni e data/ora (gia' mostrati sopra)
            if nome_campo.startswith("_") or nome_campo in ("Data", "Ora"):
                continue
            all_campi.append(campo)

        # Trova differenze
        diff_frame = tk.Frame(inner, bg=c.get("sfondo", "#0a0a0a"))
        diff_frame.pack(fill="x", pady=(_S(2), _S(4)))

        # Header diff: CAMPO + un colonnino per setup
        diff_headers = ["CAMPO"]
        for sd in sorted_setup:
            rec = sd["record"]
            campo_k = self.table_def.get_campo_chiave()
            nome = str(rec.get(campo_k["nome"], sd["record_id"])) if campo_k else sd["record_id"]
            diff_headers.append(nome[:14])

        for j, h in enumerate(diff_headers):
            w = _S(16) if j == 0 else _S(12)
            tk.Label(diff_frame, text=h,
                     bg=c.get("pulsanti_sfondo", "#1a3a1a"),
                     fg=c.get("pulsanti_testo", "#39ff14"),
                     font=(FONT_MONO, _S(7), "bold"), width=w,
                     relief="ridge", bd=1).grid(row=0, column=j, sticky="nsew")

        riga_diff = 1
        sezione_attiva = ""

        for campo in all_campi:
            nome_campo = campo["nome"]
            # Raccogli valori per ogni setup
            valori = []
            for sd in sorted_setup:
                v = str(sd["record"].get(nome_campo, "")).strip()
                valori.append(v)

            # Mostra solo se almeno un valore diverso
            valori_set = set(v for v in valori if v)
            if len(valori_set) <= 1 and len(valori_set) > 0:
                continue  # Tutti uguali, salta
            if not any(valori):
                continue  # Tutti vuoti, salta

            diff_trovate = True

            # Sezione? Controlla se il campo ha una sezione associata
            sez = campo.get("sezione", "")
            if sez and sez != sezione_attiva:
                sezione_attiva = sez
                tk.Label(diff_frame, text=sez,
                         bg=c.get("sfondo", "#0a0a0a"),
                         fg=c.get("testo_dim", "#555555"),
                         font=(FONT_MONO, _S(7), "bold"),
                         anchor="w").grid(row=riga_diff, column=0,
                         columnspan=len(diff_headers), sticky="w", pady=(_S(4), 0))
                riga_diff += 1

            # Nome campo
            label_campo = nome_campo.replace("_", " ")
            tk.Label(diff_frame, text=label_campo,
                     bg=c.get("sfondo_celle", "#111111"),
                     fg=c.get("label", "#888888"),
                     font=self._f_data, width=_S(16),
                     anchor="w", relief="flat", bd=1).grid(
                row=riga_diff, column=0, sticky="nsew")

            # Valori per setup
            for j, v in enumerate(valori):
                # Colore: evidenzia se diverso dal primo (base)
                if v and v != valori[0]:
                    fg = c.get("stato_avviso", "#ffaa00")  # Diverso = arancione
                else:
                    fg = c.get("dati", "#39ff14")
                tk.Label(diff_frame, text=v if v else "-",
                         bg=c.get("sfondo_celle", "#111111"), fg=fg,
                         font=self._f_data, width=_S(12),
                         anchor="center", relief="flat", bd=1).grid(
                    row=riga_diff, column=j + 1, sticky="nsew")
            riga_diff += 1

        # Confronta anche i riferimenti (telai, motori, gomme, miscela)
        for rif in self.table_def.riferimenti:
            alias = rif.get("alias", rif["tabella"])
            if alias.lower() == "piste":
                continue
            campo_rec = rif.get("campo_record", rif["campo_chiave"])
            ref_db = self.ref_dbs.get(alias) or self.ref_dbs.get(rif["tabella"])

            valori_desc = []
            for sd in sorted_setup:
                codice = str(sd["record"].get(campo_rec, "")).strip()
                if ref_db:
                    desc = self._risolvi_ref(ref_db, rif, codice)
                    valori_desc.append(desc or codice)
                else:
                    valori_desc.append(codice)

            valori_set = set(v for v in valori_desc if v)
            if len(valori_set) <= 1:
                continue

            diff_trovate = True
            label_rif = alias.upper().replace("_", " ")
            tk.Label(diff_frame, text=label_rif,
                     bg=c.get("sfondo_celle", "#111111"),
                     fg=c.get("label", "#888888"),
                     font=self._f_data, width=_S(16),
                     anchor="w", relief="flat", bd=1).grid(
                row=riga_diff, column=0, sticky="nsew")

            for j, v in enumerate(valori_desc):
                fg = c.get("stato_avviso", "#ffaa00") if v != valori_desc[0] else c.get("dati", "#39ff14")
                tk.Label(diff_frame, text=v[:20] if v else "-",
                         bg=c.get("sfondo_celle", "#111111"), fg=fg,
                         font=self._f_data, width=_S(12),
                         anchor="center", relief="flat", bd=1).grid(
                    row=riga_diff, column=j + 1, sticky="nsew")
            riga_diff += 1

        if not diff_trovate:
            tk.Label(diff_frame, text="Setup identici - nessuna differenza trovata",
                     bg=c.get("sfondo", "#0a0a0a"),
                     fg=c.get("testo_dim", "#555555"),
                     font=self._f_label).grid(row=1, column=0, columnspan=len(diff_headers))

        # ── Binding ──
        self._top.bind("<Escape>", lambda e: self._schermata_selezione())
        self._top.bind("<g>", lambda e: self._schermata_grafico())
        self._top.bind("<G>", lambda e: self._schermata_grafico())

        self._status_label(self.root, "G = Grafico overlay  |  ESC = Torna a selezione")

    # =================================================================
    #  3. GRAFICO OVERLAY CROSS-SETUP (Canvas tkinter puro)
    # =================================================================

    def _schermata_grafico(self):
        """Grafico cumulativo: confronto visuale tra sessioni di setup diversi."""
        import math
        self._pulisci()
        c = self.c
        _S = self._S

        # Raccogli la migliore sessione per ogni setup
        sessioni_graf = []
        for sd in self._setup_confronto:
            rec = sd["record"]
            campo_k = self.table_def.get_campo_chiave()
            nome_setup = str(rec.get(campo_k["nome"], sd["record_id"])) if campo_k else sd["record_id"]

            # Trova la sessione con il miglior best lap
            migliore = None
            best_tempo = 999999
            for s in sd["sessioni"]:
                b = s.get("miglior_tempo", 0)
                if b and 0 < b < best_tempo:
                    best_tempo = b
                    migliore = s
            if not migliore:
                continue

            giri = migliore.get("giri", [])
            tempi = [g["tempo"] for g in giri
                     if g.get("stato") in ("valido", None) and g.get("tempo", 0) > 0]
            if not tempi:
                continue

            cumul = []
            tot = 0.0
            for t in tempi:
                tot += t
                cumul.append(tot)

            data_s = migliore.get("data", "?")
            label = "%s %s" % (nome_setup[:12], data_s[-5:] if len(data_s) > 5 else data_s)
            sessioni_graf.append({
                "label": label,
                "tempi": tempi,
                "cumul": cumul,
                "best": migliore.get("miglior_tempo", min(tempi)),
                "media": sum(tempi) / len(tempi),
                "n_giri": len(tempi),
            })

        if not sessioni_graf:
            self._schermata_confronto()
            return

        # ── Header ──
        header = tk.Frame(self.root, bg=c.get("sfondo", "#0a0a0a"))
        header.pack(fill="x", padx=_S(10), pady=(_S(6), 0))

        tk.Button(header, text="< CONFRONTO", font=self._f_small,
                  bg=c.get("pulsanti_sfondo", "#1a3a1a"),
                  fg=c.get("pulsanti_testo", "#39ff14"),
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._schermata_confronto).pack(side="left")

        tk.Label(header, text="  GRAFICO CONFRONTO  |  %d setup" % len(sessioni_graf),
                 bg=c.get("sfondo", "#0a0a0a"),
                 fg=c.get("dati", "#39ff14"),
                 font=self._f_title).pack(side="left", padx=(_S(8), 0))

        tk.Label(header, text="linea piatta = veloce",
                 bg=c.get("sfondo", "#0a0a0a"),
                 fg=c.get("testo_dim", "#555555"),
                 font=self._f_small).pack(side="right")

        tk.Frame(self.root, bg=c.get("linee", "#333333"),
                 height=1).pack(fill="x", padx=_S(10), pady=(_S(4), _S(4)))

        # ── Canvas ──
        canvas_frame = tk.Frame(self.root, bg=c.get("sfondo", "#0a0a0a"))
        canvas_frame.pack(fill="both", expand=True, padx=_S(10), pady=(_S(2), _S(4)))

        canvas = tk.Canvas(canvas_frame, bg=c.get("sfondo_celle", "#111111"),
                           highlightthickness=1,
                           highlightbackground=c.get("linee", "#333333"))
        canvas.pack(fill="both", expand=True)

        # ── Legenda in alto a sinistra, sovrapposta al canvas ──
        leg_frame = tk.Frame(canvas, bg=c.get("sfondo_celle", "#111111"))
        leg_frame.place(x=_S(70), y=_S(8), anchor="nw")
        for i, sess in enumerate(sessioni_graf):
            color = _GRAPH_COLORS[i % len(_GRAPH_COLORS)]
            lf = tk.Frame(leg_frame, bg=c.get("sfondo_celle", "#111111"))
            lf.pack(anchor="w", pady=(0, _S(1)))
            sq = tk.Canvas(lf, width=_S(10), height=_S(10), bg=color,
                           highlightthickness=0, bd=0)
            sq.pack(side="left", padx=(0, _S(4)))
            tk.Label(lf, text="%s  %dg  B:%s  M:%s" % (
                sess["label"], sess["n_giri"],
                _fmt(sess["best"]), _fmt(sess["media"])),
                bg=c.get("sfondo_celle", "#111111"), fg=color,
                font=self._f_small).pack(side="left")

        self._status_label(self.root, "ESC = Torna al confronto")
        self._top.bind("<Escape>", lambda e: self._schermata_confronto())

        # ── Disegno ──
        def _draw(event=None):
            canvas.delete("all")
            cw = canvas.winfo_width()
            ch = canvas.winfo_height()
            if cw < 80 or ch < 60:
                return

            ml, mr, mt, mb = 62, 15, 15, 28
            pw = cw - ml - mr
            ph = ch - mt - mb
            if pw < 30 or ph < 30:
                return

            max_giri = max(s["n_giri"] for s in sessioni_graf)
            c_max = max(s["cumul"][-1] for s in sessioni_graf)
            c_max *= 1.05
            c_range = c_max if c_max > 0 else 1.0

            def x_pos(giro):
                if max_giri <= 1:
                    return ml + pw // 2
                return ml + int(giro * pw / max_giri)

            def y_pos(val):
                return mt + int((1.0 - val / c_range) * ph)

            fg_grid = c.get("linee", "#333333")
            fg_label = c.get("testo_dim", "#555555")

            # Griglia orizzontale
            raw_step = c_range / 6
            if raw_step <= 10:
                step = 10
            elif raw_step <= 30:
                step = 30
            elif raw_step <= 60:
                step = 60
            elif raw_step <= 120:
                step = 120
            else:
                step = max(60, int(math.ceil(raw_step / 60)) * 60)

            val = step
            while val < c_max:
                y = y_pos(val)
                canvas.create_line(ml, y, ml + pw, y, fill=fg_grid, dash=(2, 4))
                mm = int(val) // 60
                ss = int(val) % 60
                canvas.create_text(ml - 4, y, text="%d:%02d" % (mm, ss),
                                   fill=fg_label, font=(FONT_MONO, 8), anchor="e")
                val += step

            # Griglia verticale
            g_step = max(1, max_giri // 10)
            for gi in range(0, max_giri + 1, g_step):
                x = x_pos(gi)
                canvas.create_line(x, mt, x, mt + ph, fill=fg_grid, dash=(2, 4))
                canvas.create_text(x, mt + ph + 4, text=str(gi),
                                   fill=fg_label, font=(FONT_MONO, 8), anchor="n")

            # Label assi
            canvas.create_text(ml + pw // 2, ch - 2, text="Giro",
                               fill=c.get("label", "#888888"),
                               font=(FONT_MONO, 9), anchor="s")
            canvas.create_text(4, mt + ph // 2, text="T",
                               fill=c.get("label", "#888888"),
                               font=(FONT_MONO, 9), anchor="w")

            # Assi
            canvas.create_line(ml, mt, ml, mt + ph,
                               fill=c.get("label", "#888888"), width=1)
            canvas.create_line(ml, mt + ph, ml + pw, mt + ph,
                               fill=c.get("label", "#888888"), width=1)

            # Linee sessioni
            for si, sess in enumerate(sessioni_graf):
                color = _GRAPH_COLORS[si % len(_GRAPH_COLORS)]
                cumul = sess["cumul"]
                points = []
                points.extend([x_pos(0), y_pos(0)])
                for gi, ct in enumerate(cumul):
                    points.extend([x_pos(gi + 1), y_pos(ct)])

                if len(points) >= 4:
                    canvas.create_line(points, fill=color, width=2, smooth=False)

                # Pallino finale
                px_end = x_pos(len(cumul))
                py_end = y_pos(cumul[-1])
                canvas.create_oval(px_end - 3, py_end - 3, px_end + 3, py_end + 3,
                                   fill=color, outline=c.get("sfondo_celle", "#111111"))
                # Etichetta tempo totale
                mm_tot = int(cumul[-1]) // 60
                ss_tot = cumul[-1] - mm_tot * 60
                canvas.create_text(px_end + 6, py_end,
                                   text="%d:%04.1f" % (mm_tot, ss_tot),
                                   fill=color, font=(FONT_MONO, 7), anchor="w")

        canvas.bind("<Configure>", _draw)

    # =================================================================
    #  ESCI
    # =================================================================

    def _esci(self):
        """Torna alla schermata chiamante."""
        self._pulisci()
        if self.on_close:
            self.on_close()
