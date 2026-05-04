"""
Editor Tabelle v1.0 - Editor strutturato file .def
App standalone per admin - stessa estetica retro di TrackMind.
Tutto inline (niente popup), navigabile da tastiera.

Scorciatoie:  Esc=Indietro  Enter=Modifica  ^A=Aggiungi  ^D=Elimina
              ^S=Salva/Conferma  ^Up/^Down=Sposta
"""

from version import __version__

import tkinter as tk
from tkinter import font as tkfont, ttk
import os, sys, json

# Font monospace per compatibilità cross-platform
FONT_MONO = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"

try:
    from core.ui_bottoni import (setup_bottoni, setup_griglia, flash_btn,
                                  flash_key, focus_evidenzia, init_focus_globale,
                                  pulisci_cache, sospendi_focus)
    _HAS_UI_BTN = True
except ImportError:
    try:
        from ui_bottoni import (setup_bottoni, setup_griglia, flash_btn,
                                 flash_key, focus_evidenzia, init_focus_globale,
                                 pulisci_cache, sospendi_focus)
        _HAS_UI_BTN = True
    except ImportError:
        _HAS_UI_BTN = False
        def sospendi_focus(a=True): pass

def _get_base():
    if getattr(sys, 'frozen', False): return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _carica_conf():
    """Legge e decifra conf.dat (XOR + Base64). Ritorna dict o None."""
    base = _get_base()
    conf_path = os.path.join(base, "conf.dat")
    if not os.path.exists(conf_path):
        return None
    try:
        import base64
        _KEY = b"Tr4ckM1nd_C0nf_K3y_2026!#"
        with open(conf_path, "r") as f:
            enc = f.read().strip()
        data = base64.b64decode(enc.encode("ascii"))
        dec = bytes([b ^ _KEY[i % len(_KEY)] for i, b in enumerate(data)])
        return json.loads(dec.decode("utf-8"))
    except Exception:
        return None


def _carica_scala():
    """Restituisce il fattore di scala da conf.dat.
    0 = auto-detect dalla risoluzione; default 1.0."""
    conf = _carica_conf()
    if not conf:
        return 1.0
    try:
        s = float(conf.get("scala", 1.0))
    except (ValueError, TypeError):
        return 1.0
    if s == 0:
        # Auto-detect basato su larghezza schermo (stessa logica di retrodb.py)
        try:
            import tkinter as _tk
            _tmp = _tk.Tk(); _tmp.withdraw()
            sw = _tmp.winfo_screenwidth()
            _tmp.destroy()
            if sw <= 1280:
                return 1.5  # uConsole 1280x720
            elif sw <= 1920:
                return 1.0
            else:
                return 0.8
        except Exception:
            return 1.0
    return s


def _get_def_dir():
    base = _get_base()
    conf = _carica_conf()
    if conf:
        p = conf.get("percorso_tabelle", "")
        if p and os.path.isdir(p):
            return p
    for d in ["tabelle", "definizioni"]:
        p = os.path.join(base, d)
        if os.path.isdir(p): return p
    return os.path.join(base, "tabelle")

DEFAULT_COLORS = {
    "sfondo":"#0a0a0a","dati":"#39ff14","label":"#22aa22","puntini":"#1a6a1a",
    "sfondo_celle":"#080808","sfondo_celle_piene":"#0c120c","cursore":"#39ff14",
    "testo_cursore":"#0a0a0a","pulsanti_sfondo":"#1a3a1a","pulsanti_testo":"#39ff14",
    "stato_errore":"#ff5555","stato_avviso":"#ffaa00","stato_ok":"#39ff14",
    "testo_dim":"#1a6a1a","linee":"#1a5a0a","cerca_sfondo":"#1a3a1a","cerca_testo":"#ffcc00",
}
def _carica_colori():
    c = DEFAULT_COLORS.copy()
    path = os.path.join(_get_base(), "colori.cfg")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for riga in f:
                    riga = riga.strip()
                    if not riga or riga.startswith("#"): continue
                    if "=" in riga:
                        k, v = riga.split("=", 1)
                        if k.strip() in c and v.strip().startswith("#"): c[k.strip()] = v.strip()
        except: pass
    return c

TIPI = {"S":"Stringa","N":"Numero","D":"Data","O":"Ora","F":"Flag","P":"Password","V":"Valore","$":"Valuta"}

def parse_def(fp):
    righe = []
    with open(fp, "r", encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\n").strip()
            if not s: righe.append({"tipo":"vuoto"})
            elif s.startswith("#"): righe.append({"tipo":"commento","testo":s[1:].strip()})
            elif s.startswith("!"):
                p = s[1:].split(";")
                righe.append({"tipo":"meta","chiave":p[0].strip(),"valore":p[1].strip() if len(p)>=2 else ""})
            elif s.startswith("@"):
                p = s[1:].split(";")
                righe.append({"tipo":"ref","tabella":p[0].strip(),
                    "campo":p[1].strip() if len(p)>=2 else "",
                    "alias":p[2].strip() if len(p)>=3 else ""})
            else:
                p = s.split(";")
                if len(p) >= 3:
                    _flags = p[3].strip().upper() if len(p) >= 4 else ""
                    righe.append({"tipo":"campo","nome":p[0].strip(),
                        "lunghezza":int(p[1].strip()) if p[1].strip().isdigit() else 10,
                        "tipo_campo":p[2].strip().upper(),
                        "chiave":"K" in _flags, "analisi_ia":"A" in _flags})
                else: righe.append({"tipo":"commento","testo":s})
    return righe

def salva_def(fp, righe):
    with open(fp, "w", encoding="utf-8") as f:
        for r in righe:
            t = r["tipo"]
            if t == "vuoto": f.write("\n")
            elif t == "commento": f.write("# %s\n" % r.get("testo",""))
            elif t == "meta": f.write("!%s;%s\n" % (r["chiave"],r["valore"]))
            elif t == "ref":
                alias = r.get("alias","").strip()
                if alias:
                    f.write("@%s;%s;%s\n" % (r["tabella"],r["campo"],alias))
                else:
                    f.write("@%s;%s\n" % (r["tabella"],r["campo"]))
            elif t == "campo":
                l = "%s;%d;%s" % (r["nome"],r["lunghezza"],r["tipo_campo"])
                _fl = ""
                if r.get("chiave"): _fl += "K"
                if r.get("analisi_ia"): _fl += "A"
                if _fl: l += ";%s" % _fl
                f.write(l + "\n")

class EditorTabelle:
    def __init__(self, root, on_close=None, mostra_nascosti=False):
        self.root = root; self.c = _carica_colori(); self.def_dir = _get_def_dir()
        self._on_close = on_close
        self._mostra_nascosti = mostra_nascosti
        self._embedded = on_close is not None
        # Scala dimensioni font da conf.dat (uConsole / DPI alti)
        self._scala = _carica_scala()
        if not self._embedded:
            self.root.title(f"Editor Tabelle - TrackMind  v{__version__}")
            self.root.configure(bg=self.c["sfondo"])
            # Finestra standalone: ridimensiona in base alla scala
            _gw = max(640, int(900 * self._scala))
            _gh = max(480, int(700 * self._scala))
            self.root.geometry("%dx%d" % (_gw, _gh))
        else:
            self.root.configure(bg=self.c["sfondo"])
        def _S(n):
            return max(1, int(round(n * self._scala)))
        self._S = _S
        self._f_title = tkfont.Font(family=FONT_MONO, size=_S(11), weight="bold")
        self._f_label = tkfont.Font(family=FONT_MONO, size=_S(9))
        self._f_btn = tkfont.Font(family=FONT_MONO, size=_S(8), weight="bold")
        self._f_small = tkfont.Font(family=FONT_MONO, size=_S(8))
        self._f_edit = tkfont.Font(family=FONT_MONO, size=_S(10))
        self._top = self.root.winfo_toplevel()  # Per binding tastiera
        # Focus visivo globale (modulo centralizzato)
        if _HAS_UI_BTN:
            init_focus_globale(self.root, self.c)
        self._righe = []; self._filepath = ""; self._nome = ""; self._btn_map = {}
        self._schermata_lista()

    def _pulisci(self):
        for k in ("<Escape>",
                  "<Control-a>","<Control-A>",
                  "<Control-d>","<Control-D>",
                  "<Control-s>","<Control-S>",
                  "<Control-Up>","<Control-Down>","<Return>"):
            try: self._top.unbind(k)
            except: pass
        # Sospendi focus + pulisci cache durante transizione
        if _HAS_UI_BTN:
            sospendi_focus(True)
            pulisci_cache()
        for w in self.root.winfo_children(): w.destroy()
        if _HAS_UI_BTN:
            sospendi_focus(False)

    def _stile(self):
        c = self.c; s = ttk.Style(); s.theme_use("clam")
        _S = self._S
        s.configure("R.Treeview", background=c["sfondo_celle"], foreground=c["dati"],
            fieldbackground=c["sfondo_celle"], font=(FONT_MONO,_S(9)), rowheight=_S(22), borderwidth=0)
        s.configure("R.Treeview.Heading", background=c["pulsanti_sfondo"],
            foreground=c["pulsanti_testo"], font=(FONT_MONO,_S(9),"bold"), borderwidth=1, relief="ridge")
        s.map("R.Treeview", background=[("selected",c["cursore"])], foreground=[("selected",c["testo_cursore"])])

    def _setup_btns(self, btns):
        """Navigazione orizzontale + focus visivo (delega a ui_bottoni)."""
        if _HAS_UI_BTN:
            setup_bottoni(btns, orizzontale=True)
        # Fallback minimo se modulo non disponibile
        else:
            for i, b in enumerate([b for b in btns if str(b["state"])!="disabled"]):
                if i < len(btns)-1: b.bind("<Right>", lambda e,n=btns[i+1]: (n.focus_set(),"break")[-1])
                if i > 0: b.bind("<Left>", lambda e,p=btns[i-1]: (p.focus_set(),"break")[-1])

    def _bfoc(self, w, on):
        """Focus evidenzia (delega a ui_bottoni)."""
        if _HAS_UI_BTN:
            focus_evidenzia(w, on)

    def _flash_btn(self, btn, cmd):
        """Flash rosso 150ms (delega a ui_bottoni)."""
        if _HAS_UI_BTN:
            return flash_btn(self.root, btn, cmd)
        return cmd

    def _flash_key(self, op, cmd):
        """Flash bottone da scorciatoia tastiera (delega a ui_bottoni)."""
        if _HAS_UI_BTN:
            flash_key(self.root, self._btn_map, op, cmd)
        else:
            cmd()

    def _tsync(self, tree):
        def s(e): tree.after_idle(lambda: tree.selection_set(tree.focus()) if tree.focus() else None)
        tree.bind("<Up>",s); tree.bind("<Down>",s)

    # === LISTA ===
    def _schermata_lista(self):
        self._pulisci(); c = self.c; self._stile()
        h = tk.Frame(self.root,bg=c["sfondo"]); h.pack(fill="x",padx=10,pady=(6,0))
        tk.Label(h,text="EDITOR TABELLE",bg=c["sfondo"],fg=c["dati"],font=self._f_title).pack(side="left")
        tk.Label(h,text="  [%s]"%self.def_dir,bg=c["sfondo"],fg=c["testo_dim"],font=self._f_small).pack(side="left",padx=(8,0))
        tk.Frame(self.root,bg=c["linee"],height=1).pack(fill="x",padx=10,pady=(4,4))
        tf = tk.Frame(self.root,bg=c["sfondo"]); tf.pack(fill="both",expand=True,padx=10,pady=(2,4))
        cols = ("nome","campi","rif","tipo","accesso")
        tree = ttk.Treeview(tf,columns=cols,show="headings",style="R.Treeview",selectmode="browse")
        for col,tit,w in [("nome","Tabella",150),("campi","Campi",50),("rif","Rif",40),("tipo","Tipo",90),("accesso","Accesso",70)]:
            tree.heading(col,text=tit,anchor="w"); tree.column(col,width=w,anchor="w")
        sb = tk.Scrollbar(tf,orient="vertical",command=tree.yview); sb.pack(side="right",fill="y")
        tree.configure(yscrollcommand=sb.set); tree.pack(side="left",fill="both",expand=True)
        # v05.06.39: highlight visibile riga corrente al focus
        try:
            from focus_ui import evidenzia_treeview
            evidenzia_treeview(tree, colori=c)
        except Exception:
            pass
        os.makedirs(self.def_dir,exist_ok=True)
        for f in sorted(os.listdir(self.def_dir)):
            if not f.endswith(".def"): continue
            nome = f[:-4]
            try:
                rr = parse_def(os.path.join(self.def_dir,f))
                # Tabelle con !nascosto;vero compaiono solo se mostra_nascosti=True
                _nasc = any(r["tipo"]=="meta" and r["chiave"]=="nascosto"
                            and r["valore"].lower() in ("vero","true","si","1")
                            for r in rr)
                if _nasc and not self._mostra_nascosti: continue
                nc = sum(1 for r in rr if r["tipo"]=="campo"); nr = sum(1 for r in rr if r["tipo"]=="ref")
                tp = "Composita" if nr>0 else "Semplice"
                ac = next((r["valore"] for r in rr if r["tipo"]=="meta" and r["chiave"]=="accesso"),"")
            except: nc="?"; nr="?"; tp="?"; ac="?"
            tree.insert("","end",iid=nome,values=(nome.upper(),nc,nr,tp,ac))
        self._tsync(tree); self._ltree = tree
        tree.bind("<Return>",lambda e: self._apri()); tree.bind("<Double-1>",lambda e: self._apri())
        tk.Frame(self.root,bg=c["linee"],height=1).pack(fill="x",padx=10,pady=(2,2))
        bar = tk.Frame(self.root,bg=c["sfondo"]); bar.pack(pady=(2,6))
        bb = []
        _chiudi = self._on_close if self._embedded else self.root.destroy
        for t,cmd in [("APRI",self._apri),("RINOMINA",self._rinomina_form),("CLONA",self._clona_form),("NUOVA TAB",self._nuova_form),("ELIMINA",self._elimina_tab),("ESCI",_chiudi)]:
            b = tk.Button(bar,text=t,font=self._f_btn,width=10,bg=c["pulsanti_sfondo"],fg=c["pulsanti_testo"],
                          relief="ridge",bd=1,cursor="hand2",
                          highlightthickness=2,highlightcolor=c["dati"],highlightbackground=c["sfondo"])
            b.config(command=self._flash_btn(b, cmd))
            b.pack(side="left",padx=3); bb.append(b)
        self._setup_btns(bb)
        ch = tree.get_children()
        if ch: tree.selection_set(ch[0]); tree.focus(ch[0])
        tree.focus_set()
        self._top.bind("<Escape>",lambda e: _chiudi())

    def _apri(self):
        sel = self._ltree.selection()
        if not sel: return
        nome = sel[0]; fp = os.path.join(self.def_dir,"%s.def"%nome)
        if os.path.exists(fp):
            self._filepath=fp; self._nome=nome; self._righe=parse_def(fp)
            self._editor()

    def _trova_referenze(self, nome_tabella):
        """Cerca quali tabelle .def referenziano questa tabella."""
        dipendenti = []
        for f in os.listdir(self.def_dir):
            if not f.endswith(".def"): continue
            tab = f[:-4]
            if tab == nome_tabella: continue
            try:
                righe = parse_def(os.path.join(self.def_dir, f))
                for r in righe:
                    if r["tipo"] == "ref" and r["tabella"] == nome_tabella:
                        dipendenti.append(tab)
                        break
            except: pass
        return dipendenti

    def _elimina_tab(self):
        """Elimina tabella .def con doppia pressione e controllo referenze."""
        sel = self._ltree.selection()
        if not sel: return
        nome = sel[0]
        c = self.c

        # Tabelle di sistema: non cancellabili
        if nome.lower() == "utenti":
            _warn_txt = "UTENTI e' una tabella di sistema e non puo' essere eliminata!"
            if hasattr(self, '_del_warning') and self._del_warning.winfo_exists():
                self._del_warning.config(text=_warn_txt, fg=c["stato_errore"])
            else:
                children = self.root.winfo_children()
                self._del_warning = tk.Label(self.root, text=_warn_txt,
                    bg=c["sfondo"], fg=c["stato_errore"], font=self._f_small, anchor="w")
                if len(children) >= 2:
                    self._del_warning.pack(fill="x", padx=10, before=children[-2])
                else:
                    self._del_warning.pack(fill="x", padx=10)
            return

        import time
        now = time.time()

        # Prima pressione: mostra avviso
        if not hasattr(self, '_del_tab_ts') or now - self._del_tab_ts > 4 or getattr(self, '_del_tab_nome', '') != nome:
            self._del_tab_ts = now
            self._del_tab_nome = nome
            # Controlla referenze
            refs = self._trova_referenze(nome)
            if refs:
                # Evidenzia in rosso e avvisa
                self._ltree.tag_configure("da_eliminare", foreground=c["stato_errore"])
                self._ltree.item(nome, tags=("da_eliminare",))
                # Mostra avviso inline sotto la lista
                if not hasattr(self, '_del_warning') or not self._del_warning.winfo_exists():
                    self._del_warning = tk.Label(self.root, text="", bg=c["sfondo"],
                        fg=c["stato_errore"], font=self._f_small, anchor="w")
                    self._del_warning.pack(fill="x", padx=10, before=self.root.winfo_children()[-2])
                self._del_warning.config(
                    text="ATTENZIONE: %s e' referenziata da: %s  |  Premi ELIMINA di nuovo per confermare" % (
                        nome.upper(), ", ".join(r.upper() for r in refs)))
            else:
                if not hasattr(self, '_del_warning') or not self._del_warning.winfo_exists():
                    self._del_warning = tk.Label(self.root, text="", bg=c["sfondo"],
                        fg=c["stato_avviso"], font=self._f_small, anchor="w")
                    self._del_warning.pack(fill="x", padx=10, before=self.root.winfo_children()[-2])
                self._del_warning.config(
                    text="Eliminare %s? Premi ELIMINA di nuovo per confermare" % nome.upper(),
                    fg=c["stato_avviso"])
            return

        # Seconda pressione: elimina .def e archivia dati
        del self._del_tab_ts
        del self._del_tab_nome

        # Archivia dati associati in _eliminati/
        import shutil
        dati_dir = os.path.join(os.path.dirname(self.def_dir), "dati")
        archiviati = []
        if os.path.isdir(dati_dir):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S") if 'datetime' in dir() else ""
            try:
                from datetime import datetime as _dt
                ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            except: ts = "bak"
            arch_dir = os.path.join(dati_dir, "_eliminati", "%s_%s" % (nome, ts))
            # Cerca file dati: {nome}.json e {nome}.json.meta
            for ext in (".json", ".json.meta"):
                src = os.path.join(dati_dir, nome + ext)
                if os.path.exists(src):
                    os.makedirs(arch_dir, exist_ok=True)
                    shutil.move(src, os.path.join(arch_dir, nome + ext))
                    archiviati.append(nome + ext)

        # Elimina il .def
        fp = os.path.join(self.def_dir, "%s.def" % nome)
        try:
            os.remove(fp)
        except Exception:
            pass
        self._schermata_lista()

    def _clona_form(self):
        """Form per clonare una tabella esistente con nuovo nome."""
        sel = self._ltree.selection()
        if not sel: return
        self._clona_origine = sel[0]
        self._pulisci(); c = self.c
        h = tk.Frame(self.root,bg=c["sfondo"]); h.pack(fill="x",padx=10,pady=(6,0))
        tk.Button(h,text="< LISTA",font=self._f_small,bg=c["pulsanti_sfondo"],fg=c["pulsanti_testo"],
            relief="ridge",bd=1,command=self._schermata_lista).pack(side="left")
        tk.Label(h,text="  CLONA: %s" % self._clona_origine.upper(),bg=c["sfondo"],fg=c["dati"],
                 font=self._f_title).pack(side="left",padx=(8,0))
        tk.Frame(self.root,bg=c["linee"],height=1).pack(fill="x",padx=10,pady=(4,8))
        f = tk.Frame(self.root,bg=c["sfondo"]); f.pack(padx=15,pady=10)
        tk.Label(f,text="Nuovo nome:",bg=c["sfondo"],fg=c["label"],font=self._f_label).grid(row=0,column=0,sticky="e",padx=(0,5))
        self._cn = tk.Entry(f,font=self._f_edit,width=25,bg=c["sfondo_celle"],fg=c["dati"],insertbackground=c["dati"])
        self._cn.grid(row=0,column=1,pady=3)
        self._cn.insert(0, self._clona_origine + "_copia")
        self._cn.select_range(0, "end")
        self._cn.focus_set()
        self._clona_status = tk.Label(self.root,text="",bg=c["sfondo"],fg=c["testo_dim"],font=self._f_small)
        self._clona_status.pack(pady=(5,0))
        self._top.bind("<Return>",lambda e: self._clona_esegui())
        self._top.bind("<Escape>",lambda e: self._schermata_lista())

    def _clona_esegui(self):
        """Esegue la clonazione del .def."""
        import shutil
        nuovo = self._cn.get().strip().lower().replace(" ","_")
        if not nuovo: return
        origine_fp = os.path.join(self.def_dir, "%s.def" % self._clona_origine)
        nuovo_fp = os.path.join(self.def_dir, "%s.def" % nuovo)
        c = self.c
        if os.path.exists(nuovo_fp):
            self._clona_status.config(text="Tabella '%s' esiste gia'!" % nuovo, fg=c["stato_errore"])
            return
        # Controlla dati orfani
        dati_dir = os.path.join(os.path.dirname(self.def_dir), "dati")
        dati_orfani = os.path.join(dati_dir, "%s.json" % nuovo)
        if os.path.exists(dati_orfani):
            import time; now = time.time()
            if not hasattr(self, '_clona_conferma') or now - self._clona_conferma > 4:
                self._clona_conferma = now
                self._clona_status.config(
                    text="ATTENZIONE: dati orfani '%s.json'! Premi Enter per confermare" % nuovo,
                    fg=c["stato_errore"])
                return
            del self._clona_conferma
        try:
            shutil.copy2(origine_fp, nuovo_fp)
            self._filepath = nuovo_fp; self._nome = nuovo; self._righe = parse_def(nuovo_fp)
            self._editor()
        except Exception as e:
            self._clona_status.config(text="Errore: %s" % e, fg=c["stato_errore"])

    def _rinomina_form(self):
        """Form per rinominare una tabella."""
        sel = self._ltree.selection()
        if not sel: return
        nome = sel[0]
        c = self.c
        # Tabelle di sistema: non rinominabili
        if nome.lower() == "utenti":
            if hasattr(self, '_del_warning') and self._del_warning.winfo_exists():
                self._del_warning.config(text="UTENTI non puo' essere rinominata!", fg=c["stato_errore"])
            else:
                self._del_warning = tk.Label(self.root, text="UTENTI non puo' essere rinominata!",
                    bg=c["sfondo"], fg=c["stato_errore"], font=self._f_small, anchor="w")
                self._del_warning.pack(fill="x", padx=10)
            return
        self._rin_origine = nome
        self._pulisci()
        h = tk.Frame(self.root,bg=c["sfondo"]); h.pack(fill="x",padx=10,pady=(6,0))
        tk.Button(h,text="< LISTA",font=self._f_small,bg=c["pulsanti_sfondo"],fg=c["pulsanti_testo"],
            relief="ridge",bd=1,command=self._schermata_lista).pack(side="left")
        tk.Label(h,text="  RINOMINA: %s" % nome.upper(),bg=c["sfondo"],fg=c["dati"],
                 font=self._f_title).pack(side="left",padx=(8,0))
        tk.Frame(self.root,bg=c["linee"],height=1).pack(fill="x",padx=10,pady=(4,8))
        f = tk.Frame(self.root,bg=c["sfondo"]); f.pack(padx=15,pady=10)
        tk.Label(f,text="Nuovo nome:",bg=c["sfondo"],fg=c["label"],font=self._f_label).grid(row=0,column=0,sticky="e",padx=(0,5))
        self._rn = tk.Entry(f,font=self._f_edit,width=25,bg=c["sfondo_celle"],fg=c["dati"],insertbackground=c["dati"])
        self._rn.grid(row=0,column=1,pady=3)
        self._rn.insert(0, nome)
        self._rn.select_range(0, "end")
        # Avviso referenze
        refs = self._trova_referenze(nome)
        self._rin_status = tk.Label(self.root,text="",bg=c["sfondo"],fg=c["testo_dim"],font=self._f_small)
        self._rin_status.pack(pady=(5,0))
        if refs:
            tk.Label(self.root,
                text="ATTENZIONE: %s e' referenziata da: %s" % (nome.upper(), ", ".join(r.upper() for r in refs)),
                bg=c["sfondo"],fg=c["stato_avviso"],font=self._f_small).pack(pady=(2,0))
            tk.Label(self.root,
                text="I riferimenti (@) verranno aggiornati automaticamente.",
                bg=c["sfondo"],fg=c["testo_dim"],font=self._f_small).pack(pady=(1,0))
        self._rn.focus_set()
        self._top.bind("<Return>",lambda e: self._rinomina_esegui())
        self._top.bind("<Escape>",lambda e: self._schermata_lista())

    def _rinomina_esegui(self):
        """Esegue la rinomina: .def, .json, e aggiorna riferimenti nelle altre tabelle."""
        nuovo = self._rn.get().strip().lower().replace(" ","_")
        c = self.c
        if not nuovo:
            self._rin_status.config(text="Inserisci un nome!", fg=c["stato_errore"]); return
        if nuovo == self._rin_origine:
            self._rin_status.config(text="Il nome e' uguale!", fg=c["stato_errore"]); return
        # Caratteri validi
        if not all(ch.isalnum() or ch == "_" for ch in nuovo):
            self._rin_status.config(text="Solo lettere, numeri e underscore!", fg=c["stato_errore"]); return
        nuovo_fp = os.path.join(self.def_dir, "%s.def" % nuovo)
        if os.path.exists(nuovo_fp):
            self._rin_status.config(text="Tabella '%s' esiste gia'!" % nuovo, fg=c["stato_errore"]); return
        origine_fp = os.path.join(self.def_dir, "%s.def" % self._rin_origine)
        dati_dir = os.path.join(os.path.dirname(self.def_dir), "dati")
        # Rinomina .def
        try:
            os.rename(origine_fp, nuovo_fp)
        except Exception as e:
            self._rin_status.config(text="Errore rinomina .def: %s" % e, fg=c["stato_errore"]); return
        # Rinomina .json dati (se esiste)
        for ext in (".json", ".json.meta"):
            src = os.path.join(dati_dir, self._rin_origine + ext)
            dst = os.path.join(dati_dir, nuovo + ext)
            if os.path.exists(src):
                try: os.rename(src, dst)
                except: pass
        # Aggiorna riferimenti (@) nelle altre tabelle
        aggiornati = []
        for f in os.listdir(self.def_dir):
            if not f.endswith(".def"): continue
            tab = f[:-4]
            fp = os.path.join(self.def_dir, f)
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    contenuto = fh.read()
                # Cerca @vecchio_nome; nelle righe di riferimento
                if "@%s;" % self._rin_origine in contenuto:
                    contenuto = contenuto.replace("@%s;" % self._rin_origine, "@%s;" % nuovo)
                    with open(fp, "w", encoding="utf-8") as fh:
                        fh.write(contenuto)
                    aggiornati.append(tab)
            except: pass
        # Aggiorna _meta nel .json (se esiste)
        json_nuovo = os.path.join(dati_dir, "%s.json" % nuovo)
        if os.path.exists(json_nuovo):
            try:
                with open(json_nuovo, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if "_meta" in data:
                    data["_meta"]["tabella"] = nuovo
                with open(json_nuovo, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
            except: pass
        # Messaggio finale
        msg = "Rinominata: %s -> %s" % (self._rin_origine.upper(), nuovo.upper())
        if aggiornati:
            msg += "  |  Riferimenti aggiornati in: %s" % ", ".join(a.upper() for a in aggiornati)
        self._rin_status.config(text=msg, fg=c.get("stato_ok", c["dati"]))
        self.root.after(1500, self._schermata_lista)

    def _nuova_form(self):
        self._pulisci(); c = self.c
        h = tk.Frame(self.root,bg=c["sfondo"]); h.pack(fill="x",padx=10,pady=(6,0))
        tk.Button(h,text="< LISTA",font=self._f_small,bg=c["pulsanti_sfondo"],fg=c["pulsanti_testo"],
            relief="ridge",bd=1,command=self._schermata_lista).pack(side="left")
        tk.Label(h,text="  NUOVA TABELLA",bg=c["sfondo"],fg=c["dati"],font=self._f_title).pack(side="left",padx=(8,0))
        tk.Frame(self.root,bg=c["linee"],height=1).pack(fill="x",padx=10,pady=(4,8))
        f = tk.Frame(self.root,bg=c["sfondo"]); f.pack(padx=15,pady=10)
        tk.Label(f,text="Nome tabella:",bg=c["sfondo"],fg=c["label"],font=self._f_label).grid(row=0,column=0,sticky="e",padx=(0,5))
        self._nn = tk.Entry(f,font=self._f_edit,width=25,bg=c["sfondo_celle"],fg=c["dati"],insertbackground=c["dati"])
        self._nn.grid(row=0,column=1,pady=3)
        self._nn.focus_set()
        self._top.bind("<Return>",lambda e: self._crea_tab())
        self._top.bind("<Escape>",lambda e: self._schermata_lista())

    def _crea_tab(self):
        nome = self._nn.get().strip().lower().replace(" ","_")
        if not nome: return
        fp = os.path.join(self.def_dir,"%s.def"%nome)
        if os.path.exists(fp): return
        # Controlla se esistono dati orfani con questo nome
        dati_dir = os.path.join(os.path.dirname(self.def_dir), "dati")
        dati_orfani = os.path.join(dati_dir, "%s.json" % nome)
        if os.path.exists(dati_orfani):
            c = self.c
            import time; now = time.time()
            if not hasattr(self, '_crea_conferma') or now - self._crea_conferma > 4:
                self._crea_conferma = now
                if not hasattr(self, '_crea_warn') or not self._crea_warn.winfo_exists():
                    self._crea_warn = tk.Label(self.root, text="", bg=c["sfondo"],
                        fg=c["stato_errore"], font=self._f_small)
                    self._crea_warn.pack(pady=(5,0))
                self._crea_warn.config(
                    text="ATTENZIONE: esistono dati orfani '%s.json'! Premi Enter per confermare o Esc" % nome)
                return
            del self._crea_conferma
        self._righe = [
            {"tipo":"commento","testo":nome.upper()},
            {"tipo":"meta","chiave":"accesso","valore":"tutti"},
            {"tipo":"meta","chiave":"nuovo","valore":"vero"},{"tipo":"meta","chiave":"salva","valore":"vero"},
            {"tipo":"meta","chiave":"cancella","valore":"vero"},{"tipo":"meta","chiave":"cerca","valore":"vero"},
            {"tipo":"meta","chiave":"naviga","valore":"vero"},{"tipo":"meta","chiave":"elenca","valore":"vero"},
            {"tipo":"vuoto"},
            {"tipo":"campo","nome":"Codice","lunghezza":4,"tipo_campo":"N","chiave":True},
            {"tipo":"campo","nome":"Nome","lunghezza":25,"tipo_campo":"S","chiave":False},
        ]
        self._filepath=fp; self._nome=nome; salva_def(fp,self._righe); self._editor()

    # === EDITOR ===
    def _editor(self, si=0):
        self._pulisci(); c = self.c; self._stile()
        h = tk.Frame(self.root,bg=c["sfondo"]); h.pack(fill="x",padx=10,pady=(6,0))
        tk.Button(h,text="< LISTA",font=self._f_small,bg=c["pulsanti_sfondo"],fg=c["pulsanti_testo"],
            relief="ridge",bd=1,command=self._schermata_lista).pack(side="left")
        tk.Label(h,text="  %s.def"%self._nome.upper(),bg=c["sfondo"],fg=c["dati"],font=self._f_title).pack(side="left",padx=(8,0))
        tk.Frame(self.root,bg=c["linee"],height=1).pack(fill="x",padx=10,pady=(4,2))
        tf = tk.Frame(self.root,bg=c["sfondo"]); tf.pack(fill="both",expand=True,padx=10,pady=(2,2))
        cols = ("num","tipo","contenuto")
        self._et = ttk.Treeview(tf,columns=cols,show="headings",style="R.Treeview",selectmode="browse")
        self._et.heading("num",text="#",anchor="w"); self._et.heading("tipo",text="Tipo",anchor="w")
        self._et.heading("contenuto",text="Contenuto",anchor="w")
        self._et.column("num",width=35); self._et.column("tipo",width=80); self._et.column("contenuto",width=600)
        sb = tk.Scrollbar(tf,orient="vertical",command=self._et.yview); sb.pack(side="right",fill="y")
        self._et.configure(yscrollcommand=sb.set); self._et.pack(side="left",fill="both",expand=True)
        # v05.06.39: highlight visibile riga corrente al focus
        try:
            from focus_ui import evidenzia_treeview
            evidenzia_treeview(self._et, colori=c)
        except Exception:
            pass
        for i,r in enumerate(self._righe):
            t=r["tipo"]
            if t=="vuoto": cont=""
            elif t=="commento": cont="# %s"%r.get("testo","")
            elif t=="meta": cont="!%s = %s"%(r["chiave"],r["valore"])
            elif t=="ref":
                alias = r.get("alias","")
                if alias:
                    cont="@%s -> %s  [%s]"%(r["tabella"],r["campo"],alias)
                else:
                    cont="@%s -> %s"%(r["tabella"],r["campo"])
            elif t=="campo":
                k=" [CHIAVE]" if r.get("chiave") else ""
                ia=" [IA]" if r.get("analisi_ia") else ""
                cont="%s  |  %s(%d)%s%s"%(r["nome"],TIPI.get(r["tipo_campo"],r["tipo_campo"]),r["lunghezza"],k,ia)
            else: cont=str(r)
            tl={"vuoto":"","commento":"Commento","ref":"Riferimento","campo":"CAMPO"}.get(t,t)
            if t=="meta":
                ch = r.get("chiave","").lower()
                tl = "Sezione" if ch == "sezione" else ("Link" if ch == "link" else "Parametro")
            self._et.insert("","end",iid=str(i),values=(i+1,tl,cont))
        self._tsync(self._et)
        self._et.bind("<Return>",lambda e: self._mod()); self._et.bind("<Double-1>",lambda e: self._mod())
        tk.Frame(self.root,bg=c["linee"],height=1).pack(fill="x",padx=10,pady=(2,2))
        bar = tk.Frame(self.root,bg=c["sfondo"]); bar.pack(pady=(2,2))
        bb = []
        self._btn_map = {}
        for t,op,cmd in [("MODIFICA\nEnter","mod",self._mod),("AGGIUNGI\n^A","agg",self._agg),("ELIMINA\n^D","eli",self._eli),
                       ("SU\n^Up","su",lambda:self._spo(-1)),("GIU'\n^Dn","giu",lambda:self._spo(1)),("SALVA\n^S","save",self._save)]:
            b = tk.Button(bar,text=t,font=self._f_btn,width=10,bg=c["pulsanti_sfondo"],fg=c["pulsanti_testo"],
                          relief="ridge",bd=1,cursor="hand2",
                          highlightthickness=2,highlightcolor=c["dati"],highlightbackground=c["sfondo"])
            b.config(command=self._flash_btn(b, cmd))
            b.pack(side="left",padx=2); bb.append(b)
            self._btn_map[op] = b
        self._setup_btns(bb)
        tk.Label(self.root,text="Esc=Lista",bg=c["sfondo"],fg=c["puntini"],font=self._f_small,anchor="e").pack(fill="x",padx=10,pady=(0,4))
        # Case-insensitive: funziona anche con CapsLock/Shift
        self._top.bind("<Control-a>",lambda e:self._flash_key("agg",self._agg)); self._top.bind("<Control-A>",lambda e:self._flash_key("agg",self._agg))
        self._top.bind("<Control-d>",lambda e:self._flash_key("eli",self._eli)); self._top.bind("<Control-D>",lambda e:self._flash_key("eli",self._eli))
        self._top.bind("<Control-s>",lambda e:self._flash_key("save",self._save)); self._top.bind("<Control-S>",lambda e:self._flash_key("save",self._save))
        self._top.bind("<Control-Up>",lambda e:self._flash_key("su",lambda:self._spo(-1))); self._top.bind("<Control-Down>",lambda e:self._flash_key("giu",lambda:self._spo(1)))
        self._top.bind("<Escape>",lambda e:self._schermata_lista())
        ch = self._et.get_children()
        if ch:
            si2 = min(si, len(ch)-1)
            self._et.selection_set(ch[si2]); self._et.focus(ch[si2]); self._et.see(ch[si2])
        self._et.focus_set()

    def _gsi(self):
        s = self._et.selection(); return int(s[0]) if s else -1

    def _mod(self):
        i = self._gsi()
        if i<0: return
        r = self._righe[i]; t=r["tipo"]
        if t=="campo": self._fcampo(i,r)
        elif t=="meta": self._fmeta(i,r)
        elif t=="ref": self._fref(i,r)
        elif t=="commento": self._fcomm(i,r)

    def _agg(self):
        """Mostra scelta tipo elemento da aggiungere."""
        i = self._gsi(); pos = i+1 if i>=0 else len(self._righe)
        self._pulisci(); c = self.c
        h = tk.Frame(self.root,bg=c["sfondo"]); h.pack(fill="x",padx=10,pady=(6,0))
        tk.Button(h,text="< ANNULLA",font=self._f_small,bg=c["pulsanti_sfondo"],fg=c["pulsanti_testo"],
            relief="ridge",bd=1,command=lambda:self._editor(si=max(0,pos-1))).pack(side="left")
        tk.Label(h,text="  AGGIUNGI ELEMENTO",bg=c["sfondo"],fg=c["dati"],font=self._f_title).pack(side="left",padx=(8,0))
        tk.Frame(self.root,bg=c["linee"],height=1).pack(fill="x",padx=10,pady=(4,8))
        f = tk.Frame(self.root,bg=c["sfondo"]); f.pack(padx=20,pady=10)
        bb = []
        tipi_elem = [
            ("CAMPO",       "Campo dati (nome, lunghezza, tipo)",
             lambda: self._agg_tipo(pos, {"tipo":"campo","nome":"Nuovo_Campo","lunghezza":10,"tipo_campo":"S","chiave":False,"analisi_ia":False})),
            ("RIFERIMENTO", "Collegamento a tabella esterna (@)",
             lambda: self._agg_tipo(pos, {"tipo":"ref","tabella":"","campo":"","alias":""})),
            ("PARAMETRO",   "Direttiva configurazione (!chiave;valore)",
             lambda: self._agg_tipo(pos, {"tipo":"meta","chiave":"","valore":""})),
            ("SEZIONE",     "Separatore visivo nel form (!sezione;TITOLO)",
             lambda: self._agg_tipo(pos, {"tipo":"meta","chiave":"sezione","valore":"NUOVA SEZIONE"})),
            ("LINK",        "URL per auto-sync catalogo (!link;URL)",
             lambda: self._agg_tipo(pos, {"tipo":"meta","chiave":"link","valore":"https://"})),
            ("COMMENTO",    "Riga di commento (#)",
             lambda: self._agg_tipo(pos, {"tipo":"commento","testo":""})),
        ]
        for nome, desc, cmd in tipi_elem:
            row = tk.Frame(f, bg=c["sfondo"]); row.pack(fill="x",pady=3)
            b = tk.Button(row,text=nome,font=self._f_btn,width=14,
                bg=c["pulsanti_sfondo"],fg=c["pulsanti_testo"],relief="ridge",bd=1,
                cursor="hand2",command=cmd)
            b.pack(side="left",padx=(0,8))
            tk.Label(row,text=desc,bg=c["sfondo"],fg=c["testo_dim"],font=self._f_small,
                     anchor="w").pack(side="left")
            bb.append(b)
        # Navigazione tastiera verticale
        for idx_b,b in enumerate(bb):
            b.bind("<FocusIn>", lambda e,w=b: self._bfoc(w,1))
            b.bind("<FocusOut>", lambda e,w=b: self._bfoc(w,0))
            if idx_b < len(bb)-1: b.bind("<Down>", lambda e,n=bb[idx_b+1]: (n.focus_set(),"break")[-1])
            if idx_b > 0: b.bind("<Up>", lambda e,p=bb[idx_b-1]: (p.focus_set(),"break")[-1])
        bb[0].focus_set()
        self._top.bind("<Escape>",lambda e:self._editor(si=max(0,pos-1)))

    def _agg_tipo(self, pos, nuovo):
        """Inserisce l'elemento e apre il form di modifica."""
        self._righe.insert(pos, nuovo)
        t = nuovo["tipo"]
        if t == "campo": self._fcampo(pos, nuovo)
        elif t == "ref": self._fref(pos, nuovo)
        elif t == "meta": self._fmeta(pos, nuovo)
        elif t == "commento": self._fcomm(pos, nuovo)

    def _eli(self):
        i=self._gsi()
        if i<0: return
        r=self._righe[i]; d=r.get("nome",r.get("chiave",r.get("testo","?")))
        import time; now=time.time()
        if not hasattr(self,'_del2') or now-self._del2>3: self._del2=now; return
        del self._del2
        self._righe.pop(i); self._editor(si=min(i,len(self._righe)-1))

    def _spo(self,d):
        i=self._gsi()
        if i<0: return
        ni=i+d
        if ni<0 or ni>=len(self._righe): return
        self._righe[i],self._righe[ni]=self._righe[ni],self._righe[i]; self._editor(si=ni)

    def _save(self):
        salva_def(self._filepath,self._righe)
        pass  # Salvato

    # === FORM INLINE ===
    def _header_form(self, titolo, idx):
        c = self.c
        h = tk.Frame(self.root,bg=c["sfondo"]); h.pack(fill="x",padx=10,pady=(6,0))
        tk.Button(h,text="< ANNULLA",font=self._f_small,bg=c["pulsanti_sfondo"],fg=c["pulsanti_testo"],
            relief="ridge",bd=1,command=lambda:self._editor(si=idx)).pack(side="left")
        tk.Label(h,text="  %s #%d"%(titolo,idx+1),bg=c["sfondo"],fg=c["dati"],font=self._f_title).pack(side="left",padx=(8,0))
        tk.Frame(self.root,bg=c["linee"],height=1).pack(fill="x",padx=10,pady=(4,8))

    def _bar_form(self, ok_cmd, idx):
        c = self.c
        tk.Frame(self.root,bg=c["linee"],height=1).pack(fill="x",padx=10,pady=(10,5))
        bar = tk.Frame(self.root,bg=c["sfondo"]); bar.pack(pady=5)
        b1 = tk.Button(bar,text="CONFERMA\n^S",font=self._f_btn,width=12,bg=c["pulsanti_sfondo"],fg=c["pulsanti_testo"],relief="ridge",bd=1)
        b1.config(command=self._flash_btn(b1, ok_cmd))
        b1.pack(side="left",padx=3)
        b2 = tk.Button(bar,text="ANNULLA\nEsc",font=self._f_btn,width=12,bg=c["pulsanti_sfondo"],fg=c["pulsanti_testo"],relief="ridge",bd=1)
        b2.config(command=self._flash_btn(b2, lambda:self._editor(si=idx)))
        b2.pack(side="left",padx=3)
        self._setup_btns([b1,b2])
        self._btn_map = {"conferma": b1, "annulla": b2}
        self._top.bind("<Control-s>",lambda e:self._flash_key("conferma",ok_cmd))
        self._top.bind("<Control-S>",lambda e:self._flash_key("conferma",ok_cmd))
        self._top.bind("<Escape>",lambda e:self._flash_key("annulla",lambda:self._editor(si=idx)))

    def _ent(self, parent, width=25):
        c = self.c
        e = tk.Entry(parent,font=self._f_edit,width=width,bg=c["sfondo_celle"],fg=c["dati"],
                     insertbackground=c["dati"],highlightthickness=0,relief="flat",bd=2)
        e.bind("<FocusIn>", lambda ev: e.config(bg=c["pulsanti_sfondo"], relief="solid"))
        e.bind("<FocusOut>", lambda ev: e.config(bg=c["sfondo_celle"], relief="flat"))
        return e

    def _fcampo(self, idx, r):
        self._pulisci(); c = self.c; self._header_form("CAMPO", idx)
        f = tk.Frame(self.root,bg=c["sfondo"]); f.pack(padx=20,pady=5)
        tk.Label(f,text="Nome:",bg=c["sfondo"],fg=c["label"],font=self._f_label,anchor="e",width=14).grid(row=0,column=0,sticky="e",padx=(0,5))
        en = self._ent(f); en.grid(row=0,column=1,pady=4); en.insert(0,r.get("nome",""))
        tk.Label(f,text="Lunghezza:",bg=c["sfondo"],fg=c["label"],font=self._f_label,anchor="e",width=14).grid(row=1,column=0,sticky="e",padx=(0,5))
        el = self._ent(f,6); el.grid(row=1,column=1,pady=4,sticky="w"); el.insert(0,str(r.get("lunghezza",10)))
        tk.Label(f,text="Tipo:",bg=c["sfondo"],fg=c["label"],font=self._f_label,anchor="e",width=14).grid(row=2,column=0,sticky="ne",padx=(0,5))
        tv = tk.StringVar(value=r.get("tipo_campo","S"))
        tf = tk.Frame(f,bg=c["sfondo"]); tf.grid(row=2,column=1,sticky="w",pady=4)
        _tipo_rbs = {}
        _tipo_codes = list(TIPI.keys())
        for i_t, cod in enumerate(_tipo_codes):
            desc = TIPI[cod]
            rb = tk.Radiobutton(tf, text="%s (%s)" % (cod, desc), variable=tv, value=cod,
                bg=c["sfondo"], fg=c["dati"],
                selectcolor=c["sfondo_celle"], activebackground=c["sfondo"],
                activeforeground=c["dati"],
                highlightthickness=2, highlightcolor=c["pulsanti_testo"],
                highlightbackground=c["sfondo"],
                font=self._f_small, takefocus=1, anchor="w")
            rb.pack(anchor="w", pady=1)
            _tipo_rbs[cod] = rb
        kv = tk.BooleanVar(value=r.get("chiave",False))
        _ck_chiave = tk.Checkbutton(f,text="Chiave primaria (auto-ID)",variable=kv,bg=c["sfondo"],fg=c["dati"],
            selectcolor=c["sfondo_celle"],activebackground=c["sfondo"],activeforeground=c["dati"],
            highlightthickness=2,highlightcolor=c["pulsanti_testo"],highlightbackground=c["sfondo"],
            font=self._f_small,takefocus=1)
        _ck_chiave.grid(row=3,column=1,sticky="w",pady=4)
        av = tk.BooleanVar(value=r.get("analisi_ia",False))
        _ck_ia = tk.Checkbutton(f,text="Analisi IA (passa questo campo all'intelligenza artificiale)",variable=av,bg=c["sfondo"],fg=c["cerca_testo"],
            selectcolor=c["sfondo_celle"],activebackground=c["sfondo"],activeforeground=c["cerca_testo"],
            highlightthickness=2,highlightcolor=c["pulsanti_testo"],highlightbackground=c["sfondo"],
            font=self._f_small,takefocus=1)
        _ck_ia.grid(row=4,column=1,sticky="w",pady=4)
        def ok():
            n=en.get().strip().replace(" ","_")
            if not n: return
            try: lu=int(el.get().strip())
            except: return
            self._righe[idx]={"tipo":"campo","nome":n,"lunghezza":lu,"tipo_campo":tv.get(),"chiave":kv.get(),"analisi_ia":av.get()}
            self._editor(si=idx)
        self._bar_form(ok,idx); en.focus_set()

    def _fmeta(self, idx, r):
        self._pulisci(); c = self.c; self._header_form("PARAMETRO", idx)
        f = tk.Frame(self.root,bg=c["sfondo"]); f.pack(padx=20,pady=10)
        tk.Label(f,text="Chiave:",bg=c["sfondo"],fg=c["label"],font=self._f_label,width=14,anchor="e").grid(row=0,column=0,padx=(0,5))
        ek = self._ent(f); ek.grid(row=0,column=1,pady=4); ek.insert(0,r.get("chiave",""))
        tk.Label(f,text="Valore:",bg=c["sfondo"],fg=c["label"],font=self._f_label,width=14,anchor="e").grid(row=1,column=0,padx=(0,5))
        ev = self._ent(f); ev.grid(row=1,column=1,pady=4); ev.insert(0,r.get("valore",""))
        def ok():
            self._righe[idx]={"tipo":"meta","chiave":ek.get().strip(),"valore":ev.get().strip()}
            self._editor(si=idx)
        self._bar_form(ok,idx); ek.focus_set()

    def _fref(self, idx, r):
        self._pulisci(); c = self.c; self._header_form("RIFERIMENTO", idx)
        f = tk.Frame(self.root,bg=c["sfondo"]); f.pack(padx=20,pady=10)
        # Tabella
        tk.Label(f,text="Tabella:",bg=c["sfondo"],fg=c["label"],font=self._f_label,width=14,anchor="e").grid(row=0,column=0,padx=(0,5))
        et = self._ent(f); et.grid(row=0,column=1,pady=4); et.insert(0,r.get("tabella",""))
        # Elenco tabelle .def disponibili
        defs = [fn[:-4] for fn in os.listdir(self.def_dir) if fn.endswith(".def")]
        defs.sort()
        if defs:
            tk.Label(f,text="Disponibili:",bg=c["sfondo"],fg=c["testo_dim"],font=self._f_small,
                     anchor="e",width=14).grid(row=1,column=0,padx=(0,5),sticky="ne")
            tk.Label(f,text=", ".join(defs),bg=c["sfondo"],fg=c["testo_dim"],font=self._f_small,
                     anchor="w",wraplength=400).grid(row=1,column=1,sticky="w")
        # Campo chiave
        tk.Label(f,text="Campo chiave:",bg=c["sfondo"],fg=c["label"],font=self._f_label,width=14,anchor="e").grid(row=2,column=0,padx=(0,5))
        ec = self._ent(f); ec.grid(row=2,column=1,pady=4); ec.insert(0,r.get("campo",""))
        # Alias (opzionale)
        tk.Label(f,text="Alias:",bg=c["sfondo"],fg=c["label"],font=self._f_label,width=14,anchor="e").grid(row=3,column=0,padx=(0,5))
        ea = self._ent(f); ea.grid(row=3,column=1,pady=4); ea.insert(0,r.get("alias",""))
        tk.Label(f,text="(opzionale: nome campo nel record, es. Gomma_Anteriore)",
                 bg=c["sfondo"],fg=c["testo_dim"],font=self._f_small).grid(row=4,column=1,sticky="w")
        def ok():
            self._righe[idx]={"tipo":"ref","tabella":et.get().strip(),"campo":ec.get().strip(),
                              "alias":ea.get().strip()}
            self._editor(si=idx)
        self._bar_form(ok,idx); et.focus_set()

    def _fcomm(self, idx, r):
        self._pulisci(); c = self.c; self._header_form("COMMENTO", idx)
        f = tk.Frame(self.root,bg=c["sfondo"]); f.pack(padx=20,pady=10)
        tk.Label(f,text="Testo:",bg=c["sfondo"],fg=c["label"],font=self._f_label,width=14,anchor="e").grid(row=0,column=0,padx=(0,5))
        et = self._ent(f,40); et.grid(row=0,column=1,pady=4); et.insert(0,r.get("testo",""))
        def ok():
            self._righe[idx]={"tipo":"commento","testo":et.get().strip()}
            self._editor(si=idx)
        self._bar_form(ok,idx); et.focus_set()

if __name__ == "__main__":
    root = tk.Tk(); EditorTabelle(root); root.mainloop()
