"""
TrackMind - AI Analisi v1.0
Add-on TrackMind: analisi intelligente sessioni cronometriche con Claude AI.
Invia i dati della sessione all'API Anthropic e mostra l'analisi.

Richiede:
  - API key Anthropic (configurabile in CONF → anthropic_api_key)
  - Connessione internet

Lanciato da AnalizzaTempi come schermata embedded.
"""

from version import __version__

import tkinter as tk
from tkinter import font as tkfont
import json, os, sys, threading, socket, urllib.request, urllib.error

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

# Font monospace + helper colori centralizzati
try:
    from config_colori import FONT_MONO, carica_colori as _carica_colori
except ImportError:
    import sys as _sys
    FONT_MONO = "Consolas" if _sys.platform == "win32" else "DejaVu Sans Mono"
    def _carica_colori():
        return {}

# Barra batteria (opzionale)
try:
    from core.batteria import aggiungi_barra_batteria as _aggiungi_barra_bat
except Exception:
    def _aggiungi_barra_bat(*args, **kwargs):
        return None


def _fmt(sec):
    if not sec: return "--"
    m = int(sec) // 60; s = sec - m * 60
    return "%02d:%05.2f" % (m, s)


# ─────────────────────────────────────────────────────────────────────
#  API KEY
# ─────────────────────────────────────────────────────────────────────
def _get_api_key():
    """Legge la API key Anthropic. Cerca in: api_key.txt, conf.dat, env."""
    # 1. File api_key.txt nella cartella app
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    key_file = os.path.join(base, "api_key.txt")
    if os.path.exists(key_file):
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                key = f.read().strip()
            if key and len(key) > 20:
                return key
        except Exception:
            pass
    # 2. Configurazione conf.dat
    try:
        from conf_manager import carica_conf
        conf = carica_conf()
        key = conf.get("anthropic_api_key", "").strip()
        if key and len(key) > 20:
            return key
    except Exception:
        pass
    # 3. Variabile d'ambiente
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


# ─────────────────────────────────────────────────────────────────────
#  CHIAMATA API CLAUDE
# ─────────────────────────────────────────────────────────────────────
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"


def chiama_claude(prompt, api_key, system_prompt=""):
    """Chiama l'API Anthropic. Ritorna (risposta_testo, errore)."""
    if not api_key:
        return None, "API key Anthropic non configurata!\nVai in CONF (Ctrl+Shift+F12) e inserisci 'anthropic_api_key'"

    # Check rapido connessione internet
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3).close()
    except (OSError, socket.timeout):
        return None, "Nessuna connessione internet!\nVerifica il WiFi e riprova."

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    body = {
        "model": MODEL,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        body["system"] = system_prompt

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            # Estrai testo dalla risposta
            texts = [b["text"] for b in result.get("content", []) if b.get("type") == "text"]
            return "\n".join(texts), None
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(body_err)
            msg = err_json.get("error", {}).get("message", body_err[:200])
        except Exception:
            msg = body_err[:200]
        return None, "Errore API (%d): %s" % (e.code, msg)
    except urllib.error.URLError as e:
        return None, "Errore connessione: %s" % str(e.reason)
    except Exception as e:
        return None, "Errore: %s" % str(e)


# ─────────────────────────────────────────────────────────────────────
#  COSTRUZIONE PROMPT
# ─────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT_DEFAULT = """Sei un ingegnere di gara (race engineer) per automodelli RC scala 1/8 e 1/10.
Rispondi SEMPRE in italiano. Sii conciso, pratico e specifico."""

def _carica_system_prompt():
    """Carica il system prompt da ai_prompt.txt ad OGNI chiamata (nessun caching).
    Se il file non esiste o e' vuoto, usa il default."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_file = os.path.join(base, "ai_prompt.txt")
    if os.path.exists(prompt_file):
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                prompt = f.read().strip()
            if prompt:
                print("[IA] System prompt caricato da: %s (%d car.)" % (prompt_file, len(prompt)))
                return prompt
        except Exception:
            pass
    print("[IA] System prompt: DEFAULT (file ai_prompt.txt non trovato)")
    return SYSTEM_PROMPT_DEFAULT


def costruisci_prompt(sessione, storico=None, strategia=None):
    """Costruisce il prompt con DATI GREZZI.
    Nessuna analisi pre-calcolata: l'IA riceve i tempi giro e fa tutto lei."""
    # Costruisci lista completa sessioni
    if storico:
        tutte = list(storico) + [sessione]
        tutte.sort(key=lambda s: s.get("ora", "00:00"))
    else:
        tutte = [sessione]

    pilota = sessione.get("pilota", "?")
    setup = sessione.get("setup", "?")
    data = sessione.get("data", "?")

    # Rileva piloti diversi
    piloti_unici = list(dict.fromkeys(s.get("pilota", "?") for s in tutte))
    multi_pilota = len(piloti_unici) > 1

    if multi_pilota:
        prompt = "CONFRONTO PILOTI RC\n"
        prompt += "===================\n"
        prompt += "Piloti: %s\n" % " vs ".join(piloti_unici)
    else:
        prompt = "ANALISI %s\n" % ("GIORNATA" if storico else "SESSIONE RC")
        prompt += "===================\n"
        prompt += "Pilota: %s\n" % pilota
    prompt += "Setup: %s\n" % setup
    prompt += "Data: %s\n" % data
    prompt += "Sessioni da analizzare: %d\n" % len(tutte)

    # Info pilota dal registro (piloti.json) — veicolo, categoria, note
    _piloti_info = {}
    try:
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _pj = os.path.join(_base, "dati", "piloti.json")
        if os.path.exists(_pj):
            with open(_pj, "r", encoding="utf-8") as _fp:
                for _p in json.load(_fp):
                    _pn = _p.get("nome", "").strip()
                    if _pn:
                        _piloti_info[_pn.lower()] = _p
    except Exception:
        pass

    # Mostra info per ogni pilota coinvolto
    info_mostrate = set()
    for pil_nome in piloti_unici:
        pil_data = _piloti_info.get(pil_nome.lower(), {})
        note_pil = pil_data.get("note", "").strip()
        if note_pil and pil_nome not in info_mostrate:
            prompt += "Info %s: %s\n" % (pil_nome, note_pil)
            info_mostrate.add(pil_nome)
    for sess in tutte:
        ip = sess.get("info_pilota", "").strip()
        pn = sess.get("pilota", "?")
        if ip and pn not in info_mostrate:
            prompt += "Info %s: %s\n" % (pn, ip)
            info_mostrate.add(pn)

    # Info setup (telaio, miscela, gomme)
    ref_info = []
    for sess_check in tutte:
        for key in sorted(sess_check.keys()):
            if key.startswith("ref_"):
                nome = key[4:].replace("_", " ").title()
                ref_info.append((nome, sess_check[key]))
        if ref_info:
            break
    if ref_info:
        prompt += "\nSETUP VEICOLO:\n"
        for nome, val in ref_info:
            prompt += "  %s: %s\n" % (nome, val)

    # Parametri setup marcati con flag A (analisi IA)
    parametri_ia = None
    for sess_check in tutte:
        if "parametri_ia" in sess_check:
            parametri_ia = sess_check["parametri_ia"]
            break
    if parametri_ia:
        prompt += "\nPARAMETRI SETUP:\n"
        for sezione, campi_lista in parametri_ia.items():
            if sezione:
                prompt += "  [%s]\n" % sezione
            for nome_campo, valore in campi_lista:
                prompt += "    %s: %s\n" % (nome_campo, valore)
    prompt += "\n"

    # Rileva piloti elettrici
    piloti_elettrici = set()
    for pil_nome in piloti_unici:
        pil_data = _piloti_info.get(pil_nome.lower(), {})
        note_pil = pil_data.get("note", "").lower()
        if "elettric" in note_pil:
            piloti_elettrici.add(pil_nome)
    for sess in tutte:
        ip = (sess.get("info_pilota", "") or "").lower()
        pn = sess.get("pilota", "?")
        if "elettric" in ip:
            piloti_elettrici.add(pn)

    if piloti_elettrici:
        nomi_el = ", ".join(sorted(piloti_elettrici))
        prompt += "\nIMPORTANTE - PILOTA/I ELETTRICO/I: %s\n" % nomi_el
        prompt += "Veicoli ELETTRICI: NO carburante/miscela/serbatoio/pit rifornimento.\n"
        prompt += "Corrono fino a scarica batteria. Pit = cambio batteria.\n\n"

    # ── RIEPILOGO ANDAMENTO (pre-calcolato per l'AI) ──
    # Senza questo blocco l'AI tendeva a dichiarare miglioramento anche
    # quando i numeri peggioravano (probabilmente un bias del modello a
    # essere positivo). Mettendo i delta numerici espliciti in faccia,
    # con frecce e segno, il modello non ha piu' margine per "non
    # vedere" la regressione.
    if len(tutte) >= 2:
        riepilogo_rows = []
        for sess in tutte:
            giri = sess.get("giri", [])
            tempi_v = [g.get("tempo", 0) for g in giri
                       if g.get("tempo", 0) > 0
                       and g.get("stato", "valido") == "valido"]
            # Filtro outlier per la media: ignora i giri > 1.5x il best
            # (sono pit/incidenti che falserebbero il "passo gara").
            best = min(tempi_v) if tempi_v else 0
            tempi_passo = [t for t in tempi_v if best > 0 and t <= best * 1.5]
            media_passo = (sum(tempi_passo) / len(tempi_passo)
                           if tempi_passo else 0)
            riepilogo_rows.append({
                "ora": sess.get("ora", "?")[:5],
                "pilota": sess.get("pilota", "?"),
                "n_giri": len(tempi_v),
                "best": best,
                "media_passo": media_passo,
            })
        # Per il confronto andamento usiamo solo sessioni dello STESSO
        # pilota (altrimenti i delta non hanno senso).
        # In multi-pilota stampiamo comunque la tabella, ma calcoliamo
        # i delta separati per ogni pilota.
        prompt += "RIEPILOGO ANDAMENTO (ordine cronologico):\n"
        prompt += "  ora    pilota         giri  best     media-passo  delta\n"
        # Tieni traccia best/media precedente per pilota
        prev_per_pil = {}
        for r in riepilogo_rows:
            pn = r["pilota"]
            best_str = _fmt(r["best"])
            media_str = _fmt(r["media_passo"])
            delta_str = ""
            prev = prev_per_pil.get(pn)
            if prev and prev["media_passo"] > 0 and r["media_passo"] > 0:
                d = r["media_passo"] - prev["media_passo"]
                # Soglia 0.05s: sotto questa il segnale e' rumore.
                if d <= -0.05:
                    delta_str = "MIGLIORA  -%.2fs/giro" % abs(d)
                elif d >= 0.05:
                    delta_str = "PEGGIORA  +%.2fs/giro" % d
                else:
                    delta_str = "stabile   %+.2fs" % d
            prompt += "  %-6s %-13s %4d  %-8s %-12s %s\n" % (
                r["ora"], pn[:13], r["n_giri"],
                best_str, media_str, delta_str)
            prev_per_pil[pn] = r
        # Verdetto sintetico per ogni pilota: confronta prima vs ultima
        # sessione del pilota. Questo e' il segnale "macroscopico" che
        # l'AI DEVE riportare se diverso da zero.
        prompt += "\nVERDETTO ANDAMENTO (prima vs ultima sessione, per pilota):\n"
        per_pil = {}
        for r in riepilogo_rows:
            per_pil.setdefault(r["pilota"], []).append(r)
        for pn, rows in per_pil.items():
            if len(rows) < 2:
                continue
            prima = rows[0]
            ultima = rows[-1]
            if prima["media_passo"] > 0 and ultima["media_passo"] > 0:
                d_media = ultima["media_passo"] - prima["media_passo"]
                d_best = ((ultima["best"] - prima["best"])
                          if prima["best"] > 0 and ultima["best"] > 0 else 0)
                if d_media <= -0.10:
                    verdetto = ("MIGLIORATO: media-passo -%.2fs/giro"
                                % abs(d_media))
                elif d_media >= 0.10:
                    verdetto = ("PEGGIORATO: media-passo +%.2fs/giro - "
                                "REGRESSIONE da segnalare al pilota"
                                % d_media)
                else:
                    verdetto = "STABILE: media-passo %+.2fs/giro" % d_media
                prompt += ("  %s: best %s -> %s (%+.2fs)  |  %s\n"
                           % (pn, _fmt(prima["best"]), _fmt(ultima["best"]),
                              d_best, verdetto))
        prompt += "\nIMPORTANTE: il verdetto qui sopra e' CALCOLATO sui dati " \
                  "reali dei giri. Se dice PEGGIORATO devi riportarlo come " \
                  "regressione, NON come miglioramento. Non e' opzionale.\n\n"

    # ── DATI GREZZI: tempi giro per ogni sessione ──
    for si, sess in enumerate(tutte):
        giri = sess.get("giri", [])

        prompt += "=" * 50 + "\n"
        sess_pilota = sess.get("pilota", "?")
        prompt += "SESSIONE %d/%d  -  %s  -  %s %s\n" % (
            si + 1, len(tutte), sess_pilota, sess.get("data", "?"), sess.get("ora", "?")[:5])
        prompt += "=" * 50 + "\n"

        serb = sess.get("serbatoio_cc", 0)
        if serb:
            prompt += "Serbatoio: %d cc\n" % serb
        condizioni = str(sess.get("condizioni_pista", "")).strip()
        if condizioni:
            prompt += "Pista: %s\n" % condizioni

        if not giri:
            prompt += "(nessun giro registrato)\n\n"
            continue

        prompt += "Giri totali: %d\n" % len(giri)
        prompt += "\nTEMPI GIRO (dati grezzi dal transponder):\n"
        for g in giri:
            tempo = g.get("tempo", 0)
            if tempo <= 0:
                continue
            num = g.get("giro", g.get("numero", "?"))
            prompt += "  %s  %s\n" % (num, _fmt(tempo))

        prompt += "\n"

    # ── Richieste analisi ──
    prompt += "\nAnalizza i tempi giro grezzi e fornisci:\n"
    prompt += "1. ANALISI TEMPI: consistenza, degrado, anomalie\n"
    prompt += "2. RICOSTRUZIONE STINT: identifica pit/incidenti dai tempi anomali, "
    prompt += "ricostruisci gli stint seguendo le regole del system prompt\n"
    prompt += "3. SUGGERIMENTI: cosa migliorare concretamente\n"
    if storico and not multi_pilota:
        prompt += "4. EVOLUZIONE GIORNATA: come cambiano le prestazioni nel corso della giornata?\n"
    if multi_pilota:
        prompt += "\n" + "=" * 40 + "\n"
        prompt += "CONFRONTO TRA PILOTI:\n"
        prompt += "4. CLASSIFICA: chi e' il piu' veloce (passo medio e best lap)?\n"
        prompt += "5. CONSISTENZA COMPARATA: chi e' piu' regolare?\n"
        prompt += "6. RITMO GARA: proiettando su una gara reale, chi vincerebbe?\n"
        prompt += "7. PUNTI DI FORZA/DEBOLEZZA per ogni pilota\n"
        prompt += "8. CONSIGLIO COACHING: cosa imparare l'uno dall'altro?\n"

    # Strategia gara: passa solo durata, l'IA calcola tutto
    if strategia:
        prompt += "\n" + "=" * 40 + "\n"
        prompt += "STRATEGIA GARA RICHIESTA:\n"
        prompt += "Durata gara: %d minuti\n" % strategia.get("durata", 0)
        serb_strat = strategia.get("serbatoio", 0)
        if serb_strat > 0:
            prompt += "Serbatoio: %d cc\n" % serb_strat
            prompt += "Calcola la strategia ottimale: numero pit, giri per stint, "
            prompt += "chiamate pit con tempi, stint finale.\n"
        else:
            prompt += "Veicolo ELETTRICO (batteria, NO carburante).\n"
            prompt += "Non ci sono pit per rifornimento. Calcola autonomia batteria "
            prompt += "dai tempi e identifica eventuale degrado per calo tensione.\n"
        prompt += "Segui le regole del system prompt per stint minimo e autonomia.\n"

    return prompt


# =====================================================================
#  CLASSE PRINCIPALE: AIAnalisi
# =====================================================================
class AIAnalisi:
    """Schermata analisi IA integrata."""

    def __init__(self, sessione, path, storico=None, strategia=None, parent=None, on_close=None):
        self.sessione = sessione
        self.path = path
        self.storico = storico
        self.strategia = strategia
        self._on_close = on_close
        self._embedded = parent is not None

        self.c = _carica_colori()
        self._init_root(parent)
        self._init_fonts()
        self._top = self.root.winfo_toplevel()
        self._avvia_analisi()

    def _init_root(self, parent=None):
        if parent:
            self.root = parent
        else:
            self.root = tk.Tk()
            self.root.title(f"TrackMind - Analisi IA  v{__version__}")
            self.root.attributes("-fullscreen", True)
        self.root.configure(bg=self.c["sfondo"])
        # uConsole: anti popup di sistema (idempotente)
        _proteggi_finestra(self.root)

    def _init_fonts(self):
        self._f_title  = tkfont.Font(family=FONT_MONO, size=14, weight="bold")
        self._f_text   = tkfont.Font(family=FONT_MONO, size=11)
        self._f_btn    = tkfont.Font(family=FONT_MONO, size=11, weight="bold")
        self._f_small  = tkfont.Font(family=FONT_MONO, size=10)
        self._f_status = tkfont.Font(family=FONT_MONO, size=10)

    def _pulisci(self):
        for w in self.root.winfo_children():
            w.destroy()
        for k in ("<Escape>", "<Up>", "<Down>", "<Tab>", "<Right>", "<Left>", "<Return>"):
            try: self._top.unbind(k)
            except: pass
        self._action_btns = []
        self._action_cmds = []

    def _rigenera_analisi(self):
        """Cancella analisi salvata e rilancia una nuova chiamata IA."""
        self.sessione.pop("analisi_ia", None)
        self.sessione.pop("analisi_ia_data", None)
        self._avvia_analisi()

    def _salva_analisi_in_sessione(self, testo):
        """Salva il testo dell'analisi IA nel file JSON della sessione."""
        try:
            from datetime import datetime as _dt
            self.sessione["analisi_ia"] = testo
            self.sessione["analisi_ia_data"] = _dt.now().strftime("%Y-%m-%d %H:%M")
            if self.path and os.path.exists(self.path):
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(self.sessione, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("[IA] Errore salvataggio analisi: %s" % e)

    def _chiudi(self):
        if self._on_close:
            self._pulisci()
            self._on_close()
        elif not self._embedded:
            self.root.destroy()

    # =================================================================
    #  AVVIA ANALISI (mostra attesa + lancia thread)
    # =================================================================
    def _avvia_analisi(self):
        self._pulisci()
        c = self.c

        # Header
        header = tk.Frame(self.root, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        self._btn_indietro = tk.Button(header, text="< STRATEGIA", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._chiudi)
        self._btn_indietro.pack(side="left")
        # Rileva multi-pilota
        all_sess = [self.sessione]
        if self.storico:
            all_sess = list(self.storico) + [self.sessione]
        piloti_unici = list(dict.fromkeys(s.get("pilota", "?") for s in all_sess))
        if len(piloti_unici) > 1:
            titolo_ia = "  CONFRONTO PILOTI  |  %s" % " vs ".join(
                p.split()[0] if " " in p else p for p in piloti_unici)
        else:
            pilota = self.sessione.get("pilota", "?")
            setup = self.sessione.get("setup", "?")
            titolo_ia = "  ANALISI IA  |  %s  |  %s" % (pilota, setup)
        tk.Label(header, text=titolo_ia,
                 bg=c["sfondo"], fg=c["cerca_testo"], font=self._f_title).pack(side="left", padx=(8, 0))
        # Barra batteria in alto a destra (overlay)
        _aggiungi_barra_bat(header)

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(6, 0))

        # Area testo scrollabile
        text_frame = tk.Frame(self.root, bg=c["sfondo"])
        text_frame.pack(fill="both", expand=True, padx=10, pady=(8, 4))

        self._txt = tk.Text(text_frame, font=self._f_text,
            bg=c["sfondo_celle"], fg=c["dati"],
            insertbackground=c["dati"],
            selectbackground=c["cursore"], selectforeground=c["testo_cursore"],
            wrap="word", relief="flat", bd=0, padx=10, pady=8,
            state="disabled")
        vsb = tk.Scrollbar(text_frame, orient="vertical", command=self._txt.yview)
        vsb.pack(side="right", fill="y")
        self._txt.configure(yscrollcommand=vsb.set)
        self._txt.pack(side="left", fill="both", expand=True)

        # Tag per colorazione testo
        self._txt.tag_configure("titolo", foreground=c["cerca_testo"],
                                 font=tkfont.Font(family=FONT_MONO, size=12, weight="bold"))
        self._txt.tag_configure("avviso", foreground=c["stato_avviso"])
        self._txt.tag_configure("errore", foreground=c["stato_errore"])
        self._txt.tag_configure("dim", foreground=c["testo_dim"])

        tk.Frame(self.root, bg=c["linee"], height=1).pack(fill="x", padx=10, pady=(4, 2))

        # Status
        self._status = tk.Label(self.root, text="", bg=c["sfondo"],
                                fg=c["testo_dim"], font=self._f_status, anchor="w")
        self._status.pack(fill="x", padx=10, pady=(0, 4))

        self._top.bind("<Escape>", lambda e: self._chiudi())
        self._top.bind("<Up>", lambda e: self._txt.yview_scroll(-3, "units"))
        self._top.bind("<Down>", lambda e: self._txt.yview_scroll(3, "units"))

        # ── Analisi gia' salvata? Mostra quella senza richiamare Claude ──
        analisi_salvata = self.sessione.get("analisi_ia", "")
        if analisi_salvata and not self.storico and not self.strategia:
            data_analisi = self.sessione.get("analisi_ia_data", "")
            self._scrivi("ANALISI IA SALVATA", "titolo")
            if data_analisi:
                self._scrivi("  (%s)" % data_analisi, "dim")
            self._scrivi("\n\n", "dim")
            self._risposta_testo = analisi_salvata
            self._formatta_risposta(analisi_salvata)
            self._status.config(text="Analisi precedente  |  RIGENERA = nuova analisi  |  ESC = Torna",
                                fg=c["stato_ok"])
            # Bottoni: RIGENERA, COPIA, STAMPA
            btn_bar = tk.Frame(self.root, bg=c["sfondo"])
            btn_bar.pack(fill="x", padx=10, pady=(0, 4))
            btn_bar.columnconfigure(0, weight=1)
            self._action_btns = []
            self._action_cmds = []
            b_rigenera = tk.Button(btn_bar, text="RIGENERA", font=self._f_btn,
                bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                activebackground=c["cerca_sfondo"], activeforeground=c["pulsanti_testo"],
                relief="ridge", bd=1, cursor="hand2",
                command=self._rigenera_analisi)
            b_rigenera.grid(row=0, column=0, padx=4, sticky="e")
            self._action_btns.append(b_rigenera)
            self._action_cmds.append(self._rigenera_analisi)
            return

        # Mostra attesa e avvia
        n_sessioni = 1 + (len(self.storico) if self.storico else 0)
        if self.strategia:
            self._scrivi("ANALISI IA + STRATEGIA GARA %d'\n\n" % self.strategia.get("durata", 0), "titolo")
        elif n_sessioni > 1:
            self._scrivi("ANALISI GIORNATA - %d SESSIONI\n\n" % n_sessioni, "titolo")
        else:
            self._scrivi("ANALISI IA IN CORSO...\n\n", "titolo")
        self._scrivi("Invio %d sessioni per analisi...\n" % n_sessioni, "dim")
        # Mostra info setup se disponibili
        ref_keys = [(k, v) for k, v in self.sessione.items() if k.startswith("ref_") and v]
        if ref_keys:
            for k, v in ref_keys:
                nome = k[4:].replace("_", " ").title()
                self._scrivi("  %s: %s\n" % (nome, v), "dim")
        self._scrivi("Sessione: %d giri, %s\n\n" % (
            len(self.sessione.get("giri", [])),
            self.sessione.get("data", "?")), "dim")

        # Animazione attesa
        self._dots = 0
        self._animazione_id = self.root.after(500, self._anima_attesa)

        # Controlla crediti IA
        try:
            from conf_manager import carica_conf, crediti_ia_rimasti
            self._conf_ia = carica_conf()
            rimasti = crediti_ia_rimasti(self._conf_ia)
            if rimasti <= 0:
                self._scrivi("CREDITI IA ESAURITI\n\n", "errore")
                self._scrivi("Hai esaurito i crediti per l'analisi IA.\n", "avviso")
                self._scrivi("Contatta il rivenditore per una ricarica.\n\n", "dim")
                self._scrivi("Il tuo codice macchina e':\n", "dim")
                try:
                    from conf_manager import get_codice_macchina
                    self._scrivi("  %s\n" % get_codice_macchina(), "avviso")
                except Exception:
                    pass
                self._status.config(text="Crediti IA: 0 — ricarica necessaria",
                                    fg=c["stato_errore"])
                return
            self._scrivi("Crediti IA rimasti: %d\n\n" % rimasti, "dim")
        except ImportError:
            self._conf_ia = None

        # Thread API
        self._api_key = _get_api_key()
        self._prompt = costruisci_prompt(self.sessione, self.storico, self.strategia)
        self._thread = threading.Thread(target=self._chiama_api, daemon=True)
        self._thread.start()

    def _scrivi(self, testo, tag=None):
        """Aggiunge testo alla text area."""
        self._txt.config(state="normal")
        if tag:
            self._txt.insert("end", testo, tag)
        else:
            self._txt.insert("end", testo)
        self._txt.config(state="disabled")
        self._txt.see("end")

    def _anima_attesa(self):
        """Animazione puntini durante l'attesa."""
        self._dots = (self._dots + 1) % 4
        dots = "." * self._dots + " " * (3 - self._dots)
        try:
            self._status.config(text="Analisi in corso%s" % dots, fg=self.c["stato_avviso"])
            self._animazione_id = self.root.after(500, self._anima_attesa)
        except Exception:
            pass

    def _chiama_api(self):
        """Thread: chiama Claude API e aggiorna la UI."""
        risposta, errore = chiama_claude(self._prompt, self._api_key, _carica_system_prompt())

        # Aggiorna UI dal thread principale
        def _aggiorna():
            # Ferma animazione
            if hasattr(self, '_animazione_id'):
                try: self.root.after_cancel(self._animazione_id)
                except: pass

            # Pulisci area testo
            self._txt.config(state="normal")
            self._txt.delete("1.0", "end")
            self._txt.config(state="disabled")

            if errore:
                self._scrivi("ERRORE\n\n", "errore")
                self._scrivi(errore + "\n", "errore")
                self._status.config(text="Analisi fallita", fg=self.c["stato_errore"])
            else:
                self._scrivi("ANALISI IA  -  %s  %s\n" % (
                    self.sessione.get("data", "?"),
                    self.sessione.get("ora", "?")[:5]), "titolo")
                self._scrivi("=" * 50 + "\n\n", "dim")

                # Mostra info gara richiesta (l'IA calcola la strategia)
                if self.strategia:
                    sg = self.strategia
                    self._scrivi("GARA %d MINUTI - strategia calcolata dall'IA\n" % sg.get("durata", 0), "titolo")
                    self._scrivi("-" * 40 + "\n\n", "dim")

                # Scala credito IA (solo se la chiamata ha avuto successo)
                _crediti_msg = ""
                if hasattr(self, '_conf_ia') and self._conf_ia is not None:
                    try:
                        from conf_manager import usa_credito_ia
                        _ok, _rim = usa_credito_ia(self._conf_ia)
                        _crediti_msg = "  |  Crediti IA: %d" % _rim
                    except Exception:
                        pass

                # Colora le sezioni della risposta IA
                self._risposta_testo = risposta
                # Salva analisi IA nel JSON della sessione
                self._salva_analisi_in_sessione(risposta)
                self._formatta_risposta(risposta)
                self._status.config(text="Analisi completata%s  |  ESC = Torna" % _crediti_msg,
                                     fg=self.c["stato_ok"])
                # Aggiungi bottoni COPIA e STAMPA
                # Gestione MANUALE: niente focus tkinter sui bottoni,
                # tutto controllato da variabile + colori + binding toplevel
                btn_bar = tk.Frame(self.root, bg=self.c["sfondo"])
                btn_bar.pack(fill="x", padx=10, pady=(0, 4))
                btn_bar.columnconfigure(0, weight=1)
                # Crea bottoni con takefocus=0 (NO focus tkinter!)
                self._btn_copia = tk.Button(btn_bar, text="COPIA",
                          font=self._f_btn, width=8,
                          bg=self.c["pulsanti_sfondo"], fg=self.c["pulsanti_testo"],
                          relief="ridge", bd=1, cursor="hand2",
                          takefocus=0, command=self._copia_analisi)
                self._btn_copia.grid(row=0, column=1, padx=2)
                # STAMPA parte disabilitato, verifica connessione in background
                self._btn_stampa = tk.Button(btn_bar, text="STAMPA",
                          font=self._f_btn, width=8,
                          relief="flat", bd=1, takefocus=0,
                          bg=self.c["sfondo"], fg=self.c["testo_dim"],
                          state="disabled",
                          disabledforeground=self.c["testo_dim"])
                self._btn_stampa.grid(row=0, column=2, padx=2)
                # Lista bottoni attivi per navigazione manuale
                self._action_btns = [self._btn_copia]
                self._action_cmds = [self._copia_analisi]
                # Indice bottone selezionato (default: COPIA)
                self._sel_btn = 0
                self._evidenzia_bottone()

                # Verifica stampante in thread separato (non blocca UI).
                # Strategia (post v05.05.44): se nel conf.dat c'e' un MAC
                # BT configurato, lo consideriamo "stampante associata" e
                # abilitiamo il bottone subito senza pre-flight. Il vero
                # check avviene al click via stampa_bluetooth, che gestisce
                # gia' retry + socket connect (vedi core/thermal_print.py).
                # In questo modo evitiamo che il bottone resti grigio
                # quando un BLE scan satura il radio o la stampante e'
                # in sleep mode (hcitool name in entrambi i casi torna
                # vuoto). Se l'utente clicca e la stampante e' davvero
                # spenta, l'errore appare nel feedback.
                import threading
                def _check_stampante():
                    _mac = "auto"
                    try:
                        from conf_manager import carica_conf
                        _conf = carica_conf()
                        _mac_conf = (_conf.get("stampante_bt", "")
                                     or "").strip()
                        if _mac_conf:
                            _mac = _mac_conf
                    except Exception:
                        pass
                    # MAC valido formato BT (XX:XX:XX:XX:XX:XX)?
                    mac_bt_valido = (":" in _mac and len(_mac) == 17)
                    if mac_bt_valido:
                        ok = True  # Stampante associata, abilita subito
                    else:
                        # MAC non configurato (o non BT): fallback al
                        # check classico per USB / auto-detect Windows.
                        try:
                            from thermal_print import stampante_disponibile
                            ok = stampante_disponibile(_mac)
                        except Exception:
                            ok = False
                    if ok:
                        def _abilita():
                            try:
                                self._btn_stampa.config(
                                    bg=self.c["pulsanti_sfondo"],
                                    fg=self.c["stato_avviso"],
                                    cursor="hand2", state="normal",
                                    relief="ridge",
                                    command=self._stampa_analisi)
                                self._action_btns.append(self._btn_stampa)
                                self._action_cmds.append(self._stampa_analisi)
                                # Seleziona STAMPA come bottone attivo
                                self._sel_btn = len(self._action_btns) - 1
                                self._evidenzia_bottone()
                            except Exception:
                                pass
                        self.root.after(0, _abilita)
                threading.Thread(target=_check_stampante, daemon=True).start()
                # Binding tastiera sul toplevel (nessun conflitto con _safe_invoke)
                self._top.bind("<Tab>", self._tab_bottoni)
                self._top.bind("<Right>", self._tab_bottoni)
                self._top.bind("<Left>", self._shift_tab_bottoni)
                self._top.bind("<Return>", self._enter_bottone)

            self._txt.see("1.0")

        try:
            self.root.after(0, _aggiorna)
        except Exception:
            pass

    # ─── NAVIGAZIONE MANUALE BOTTONI (senza focus tkinter) ───
    def _evidenzia_bottone(self):
        """Colora il bottone selezionato con bordo luminoso, gli altri normali."""
        c = self.c
        for i, btn in enumerate(self._action_btns):
            if i == self._sel_btn:
                # Bottone attivo: sfondo verde chiaro, testo nero (inversione)
                btn.config(bg=c["dati"], fg=c["sfondo"], relief="solid", bd=2)
            else:
                # Bottone normale
                if btn == self._btn_stampa:
                    btn.config(bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                               relief="ridge", bd=1)
                else:
                    btn.config(bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                               relief="ridge", bd=1)

    def _tab_bottoni(self, event=None):
        """Tab/Freccia destra: prossimo bottone."""
        if not hasattr(self, '_action_btns') or not self._action_btns:
            return "break"
        self._sel_btn = (self._sel_btn + 1) % len(self._action_btns)
        self._evidenzia_bottone()
        return "break"

    def _shift_tab_bottoni(self, event=None):
        """Shift+Tab/Freccia sinistra: bottone precedente."""
        if not hasattr(self, '_action_btns') or not self._action_btns:
            return "break"
        self._sel_btn = (self._sel_btn - 1) % len(self._action_btns)
        self._evidenzia_bottone()
        return "break"

    def _enter_bottone(self, event=None):
        """Enter: esegui comando del bottone selezionato."""
        if not hasattr(self, '_action_cmds') or not self._action_cmds:
            return "break"
        cmd = self._action_cmds[self._sel_btn]
        cmd()
        return "break"

    def _formatta_risposta(self, testo):
        """Formatta la risposta di Claude con colori."""
        c = self.c
        for riga in testo.split("\n"):
            stripped = riga.strip()
            # Titoli sezione (numerate o con ###)
            if (stripped and stripped[0].isdigit() and "." in stripped[:3] and
                any(k in stripped.upper() for k in ["CONSIST", "STINT", "PIT", "SUGGER", "CONFRONT",
                                                      "STRATEG", "ANALISI", "TEMPI"])):
                self._scrivi(riga + "\n", "titolo")
            elif stripped.startswith("#"):
                self._scrivi(riga.lstrip("#").strip() + "\n", "titolo")
            elif stripped.startswith("**") and stripped.endswith("**"):
                self._scrivi(stripped.strip("*") + "\n", "titolo")
            # Warnings
            elif any(k in stripped.lower() for k in ["attenzione", "calo", "degrado", "peggiora",
                                                       "anomal", "critico", "problema", "eccessiv"]):
                self._scrivi(riga + "\n", "avviso")
            else:
                self._scrivi(riga + "\n")

    def _copia_analisi(self):
        """Copia l'analisi IA negli appunti."""
        if hasattr(self, '_risposta_testo') and self._risposta_testo:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._risposta_testo)
            self._status.config(text="Analisi copiata negli appunti!", fg=self.c["stato_ok"])

    def _stampa_analisi(self):
        """Stampa su termica: 1) CHIAMATE PIT per meccanico 2) pausa 3) ANALISI IA.
        Usa threading per non bloccare tkinter durante scan BT e stampa."""
        try:
            return self._stampa_analisi_impl()
        except Exception as e:
            print("[STAMPA] Errore: %s" % e)
            import traceback; traceback.print_exc()
            try:
                self._status.config(text="Errore stampa: %s" % e, fg=self.c["stato_errore"])
            except Exception:
                pass

    def _stampa_analisi_impl(self):
        if not hasattr(self, '_risposta_testo') or not self._risposta_testo:
            return
        c = self.c
        self._status.config(text="Preparazione stampa...", fg=c["stato_avviso"])
        self.root.update_idletasks()

        W = 32  # Larghezza stampante termica 58mm (Font A = 32 char)

        def _word_wrap(testo, larghezza):
            """Spezza testo per parole alla larghezza data."""
            righe = []
            for linea in testo.split("\n"):
                linea = linea.strip()
                if not linea:
                    righe.append("")
                    continue
                while len(linea) > larghezza:
                    pos = linea.rfind(" ", 0, larghezza)
                    if pos <= 0: pos = larghezza
                    righe.append(linea[:pos])
                    linea = linea[pos:].lstrip()
                if linea:
                    righe.append(linea)
            return righe

        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dati_dir = os.path.join(base, "dati")
        os.makedirs(dati_dir, exist_ok=True)
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        pilota = self.sessione.get("pilota", "?")
        setup = self.sessione.get("setup", "?")
        data = self.sessione.get("data", "?")
        ora = self.sessione.get("ora", "?")[:5]

        # Carica configurazione stampante
        try:
            from conf_manager import carica_conf
            conf = carica_conf()
            mac = conf.get("stampante_bt", "").strip() or "auto"
        except Exception:
            mac = "auto"

        # ─── PREPARA SCONTRINO 1: STRATEGIA IA PER MECCANICO ───
        pit_lines = []
        pit_lines.append("=" * W)
        if self.strategia and self.strategia.get("durata"):
            pit_lines.append(("STRATEGIA GARA %d'" % self.strategia["durata"]).center(W))
        else:
            pit_lines.append("ANALISI SESSIONE".center(W))
        pit_lines.append("MECCANICO".center(W))
        pit_lines.append("=" * W)
        pit_lines.append("Pilota: %s" % pilota)
        pit_lines.append("Setup:  %s" % setup)
        pit_lines.append("Data:   %s %s" % (data, ora))
        pit_lines.append("-" * W)
        pit_lines.append("")
        # Stampa la risposta IA (contiene strategia calcolata dall'IA)
        if hasattr(self, '_risposta_testo') and self._risposta_testo:
            for riga in self._risposta_testo.split("\n"):
                riga = riga.replace("**", "").replace("##", "").replace("###", "").strip()
                if not riga:
                    pit_lines.append("")
                else:
                    pit_lines += _word_wrap(riga, W)
        pit_lines.append("")
        pit_lines.append("=" * W)
        pit_lines.append("")
        pit_lines.append("")

        # Salva file scontrino meccanico
        _pit_label = "strategia_ia"
        path_pit = os.path.join(dati_dir, "%s_%s.txt" % (_pit_label, ts))
        try:
            with open(path_pit, "w", encoding="utf-8") as f:
                f.write("\n".join(pit_lines))
        except Exception:
            pass

        # ─── STAMPA IN THREAD SEPARATO (non blocca tkinter) ───
        import threading

        def _esegui_stampa():
            """Thread: stampa scontrino unico (strategia + analisi IA)."""
            try:
                from thermal_print import stampa_bluetooth
            except ImportError:
                self.root.after(0, lambda: self._status.config(
                    text="Modulo thermal_print non disponibile!", fg=c["stato_errore"]))
                return

            # Scontrino unico: strategia + analisi IA per meccanico
            self.root.after(0, lambda: self._status.config(
                text="Stampa analisi IA...", fg=c["stato_avviso"]))
            ok, msg = stampa_bluetooth(pit_lines, mac)
            if ok:
                self.root.after(0, lambda: self._status.config(
                    text="Analisi IA stampata!", fg=c["stato_ok"]))
            else:
                self.root.after(0, lambda m=msg: self._status.config(
                    text="Errore stampa: %s" % m, fg=c["stato_errore"]))

        t = threading.Thread(target=_esegui_stampa, daemon=True)
        t.start()

    # =================================================================
    #  RUN (standalone)
    # =================================================================
    def run(self):
        self.root.mainloop()
