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

try:
    from version import __version__
except ImportError:
    __version__ = "05.05.00"

# Import myrcm_import dal modulo fratello
try:
    from myrcm_import import (lista_eventi_online_completa,
                              scarica_categorie, scarica_html_evento,
                              scarica_partecipanti,
                              trova_manche_pilota_per_fase,
                              _TableParser, _http_get, MYRCM_BASE)
    _HAS_MYRCM = True
except ImportError:
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from myrcm_import import (lista_eventi_online_completa,
                                  scarica_categorie, scarica_html_evento,
                                  scarica_partecipanti,
                                  trova_manche_pilota_per_fase,
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

# RetroField (input retro-style, stesso usato in tutto il resto di
# TrackMind: celle singole verde su nero, cursore lampeggiante)
try:
    from core.tm_field import RetroField
    _HAS_RETROFIELD = True
except Exception:
    _HAS_RETROFIELD = False
    RetroField = None


# =====================================================================
#  PARSER TIME TABLE MyRCM
# =====================================================================
# Schema reale di una pagina "Tabella Oraria" MyRCM (verificato con
# event 94090, categoria 379791, reportKey 46138):
#
#   Categoria | Manche | Gruppo | Inzio | Orario gara | Commento | Commissari
#       0          1       2        3         4            5           6
#
# (NB: header e' "Inzio" non "Inizio" - typo MyRCM in italiano.)
#
# La pagina principale di una categoria (/report/it/<eid>/<cid>) NON
# contiene la tabella oraria, ma una lista di reportKey via AJAX, uno
# per giornata di gara, con pattern:
#   doAjaxCall('/myrcm/report/it/EID/CID?reportKey=KKK',
#              'Tabella Oraria :: DD.MM.YYYY')

# Parole riconosciute negli header per identificare la colonna ora
# di inizio. "Inzio" e' il typo MyRCM, "Inizio" e "Start" come
# fallback in caso il sito venga corretto o cambino lingua.
_HEADER_ORA = ("inzio", "inizio", "start", "time", "ora", "begin")
_HEADER_CAT = ("categoria", "category", "class")
_HEADER_MAN = ("manche", "round", "heat")
_HEADER_GRP = ("gruppo", "group", "session", "tipo", "type")


def _normalize_ws(s):
    """Collassa spazi multipli in uno + trim. Necessario perche' MyRCM
    inserisce talvolta doppi spazi nei nomi categoria (rendendo poco
    affidabile un naive substring match)."""
    return re.sub(r"\s+", " ", str(s or "")).strip()


def parse_time_table(html, base_date=None):
    """Estrae le righe time table da una pagina HTML MyRCM (singola
    giornata). Riconosce gli header italiani/inglesi e individua le
    colonne categoria, manche, gruppo, ora-inizio per nome (case
    insensitive). Robusto agli spazi multipli e alle varianti.

    Ritorna lista di dict:
        [{"ora": "HH:MM", "categoria": "...", "manche": "...",
          "gruppo": "...", "turno": "...", "base_date": datetime,
          "raw": [...]}]

    Se non trova nessuna tabella riconoscibile, ritorna [].
    """
    if not html:
        return []
    parser = _TableParser()
    try:
        parser.feed(html)
    except Exception:
        return []

    re_ora = re.compile(r"^\d{1,2}[:.]\d{2}$")
    risultati = []

    for table in parser.tables:
        if len(table) < 2:
            continue
        header = [(c or "").strip().lower() for c in table[0]]

        def _find(targets):
            for i, h in enumerate(header):
                if any(t in h for t in targets):
                    return i
            return None

        idx_ora = _find(_HEADER_ORA)
        if idx_ora is None:
            continue
        idx_cat = _find(_HEADER_CAT)
        idx_man = _find(_HEADER_MAN)
        idx_grp = _find(_HEADER_GRP)

        for row in table[1:]:
            if not row or idx_ora >= len(row):
                continue
            ora = (row[idx_ora] or "").strip()
            if not re_ora.match(ora):
                continue
            ora = ora.replace(".", ":")
            cat = (_normalize_ws(row[idx_cat])
                   if idx_cat is not None and idx_cat < len(row) else "")
            manche = (_normalize_ws(row[idx_man])
                      if idx_man is not None and idx_man < len(row) else "")
            gruppo = (_normalize_ws(row[idx_grp])
                      if idx_grp is not None and idx_grp < len(row) else "")
            # turno = manche + gruppo, per testo amichevole nella UI
            turno_parts = [p for p in (manche, gruppo) if p]
            turno = " - ".join(turno_parts) if turno_parts else ""
            risultati.append({
                "ora": ora,
                "categoria": cat,
                "manche": manche,
                "gruppo": gruppo,
                "turno": turno,
                "base_date": base_date,
                "raw": [_normalize_ws(c) for c in row],
            })
    return risultati


def scarica_timetable_evento(event_id, category_id, data_target=None):
    """Aggrega le tabelle orarie dell'evento. Se `data_target` (date)
    e' specificato, scarica SOLO la giornata che matcha (default:
    nessuna data = scarica tutte le giornate). In gara con 3 giorni
    di programma, all'utente serve solo la giornata di OGGI: scaricare
    le altre rallenta la UI senza beneficio.

    Strategia: scarica la pagina report della categoria, estrae i
    reportKey delle giornate (via regex sul markup AJAX), poi scarica
    e parsa solo le giornate di interesse. Aggiunge il `base_date`
    di ciascuna riga cosi' il countdown puo' calcolare il datetime
    assoluto del turno.

    NB: il time table contiene TUTTE le categorie dell'evento, non
    solo quella selezionata. Il filtro per categoria avviene poi via
    `filtra_per_categoria` lato monitor.

    Ritorna lista di righe time table (vedi parse_time_table). Se
    data_target non matcha nessuna giornata pubblicata, ritorna [].
    """
    if not _HAS_MYRCM:
        return []
    base_url = "https://www.myrcm.ch"
    main_url = "%s/myrcm/report/it/%s/%s" % (base_url, event_id, category_id)
    html_main = _http_get(main_url)
    if not html_main:
        return []
    # Pattern: doAjaxCall('/myrcm/report/it/EID/CID?reportKey=NNN',
    #                     'Tabella Oraria :: DD.MM.YYYY')
    pat = re.compile(
        r"doAjaxCall\s*\(\s*'([^']+\?reportKey=\d+)'\s*,"
        r"\s*'Tabella Oraria :: (\d{2}\.\d{2}\.\d{4})'",
        re.IGNORECASE)
    giornate = []  # (url_path, base_date)
    visti = set()
    for m in pat.finditer(html_main):
        url_path = m.group(1)
        if url_path in visti:
            continue
        visti.add(url_path)
        data_str = m.group(2)
        try:
            d, mo, y = data_str.split(".")
            base_date = datetime(int(y), int(mo), int(d))
        except Exception:
            base_date = None
        giornate.append((url_path, base_date))

    # Filtro per data_target: scarica SOLO la giornata che corrisponde.
    # Se data_target e' un datetime lo riduco a date per il confronto.
    if data_target is not None:
        try:
            target_date = (data_target.date()
                           if hasattr(data_target, "date")
                           else data_target)
        except Exception:
            target_date = None
        if target_date is not None:
            giornate_filtr = [(u, bd) for (u, bd) in giornate
                              if bd is not None
                              and bd.date() == target_date]
            # Se trovo la giornata target, uso solo quella; altrimenti
            # ritorno vuoto (l'utente sapra' che oggi non ci sono turni
            # pubblicati per questo evento)
            giornate = giornate_filtr

    risultati = []
    for url_path, base_date in giornate:
        full_url = base_url + url_path
        html_tt = _http_get(full_url)
        if not html_tt:
            continue
        try:
            rows = parse_time_table(html_tt, base_date=base_date)
        except Exception:
            rows = []
        risultati.extend(rows)
    return risultati


def _ora_to_dt(ora_str, base_date=None):
    """Converte 'HH:MM' in datetime, usando base_date come riferimento
    di giorno. Se base_date e' None usa oggi (per test rapido)."""
    try:
        hh, mm = ora_str.split(":")
        hh = int(hh)
        mm = int(mm)
    except (ValueError, AttributeError):
        return None
    base = base_date if base_date is not None else datetime.now()
    return base.replace(hour=hh, minute=mm, second=0, microsecond=0)


def classifica_fase_turno(turno):
    """Determina a quale fase appartiene un turno del time table:
    'prove_libere', 'prove', 'qualif', 'finale', oppure None.
    Riconosce sia label italiane (Prove Libere, Prove, Qualif,
    Finale) sia inglesi (Free practice, Practice/Timed practice,
    Qualification, Final). MyRCM puo' alternare le lingue tra una
    pagina e l'altra dello stesso evento.

    Esempi reali (event 94090):
        gruppo="Prove Libere 1", manche="Manche 1"   -> "prove_libere"
        gruppo="Prove 1", manche="Manche 1"          -> "prove"
        gruppo="Timed practice 1", manche="Group 1"  -> "prove"
        gruppo="Qualif 1", manche="Manche 1"         -> "qualif"
        gruppo="Qualification 1", manche="Group 1"   -> "qualif"
        gruppo="Final run 1", manche="Final A"       -> "finale"
    """
    g = _normalize_ws(turno.get("gruppo", "")).lower()
    m = _normalize_ws(turno.get("manche", "")).lower()
    # Free practice (IT/EN)
    if "prove libere" in g or "free practice" in g:
        return "prove_libere"
    # Qualifiche: "Qualif", "Qualification" (anche in italiano l'inizio
    # delle qualifiche giornata e' a volte etichettato come Timed
    # practice = ranking/cronometrata, da considerare come qualif).
    if ("qualif" in g or "qualification" in g
            or "timed practice" in g):
        return "qualif"
    # Prove (cronometrate, non libere): "Prove", "Practice"
    if (("prove" in g and "libere" not in g)
            or ("practice" in g and "free" not in g
                and "timed" not in g)):
        return "prove"
    # Finale: "Final run", "Final A/B/C", "Finals A/B"
    if "final" in g or "final" in m:
        return "finale"
    return None


def _normalizza_manche(label):
    """Normalizza un'etichetta manche per il match cross-lingua.
    Estrae numero o lettera distintiva, ignora il prefisso
    (Manche/Group/Batteria/Heat/Final/Finals).
    Esempi:
        "Manche 1"    -> "1"
        "Group 1"     -> "1"
        "Batteria 2"  -> "2"
        "Final A"     -> "A"
        "Finals B"    -> "B"
        "Finals A"    -> "A"
    """
    if not label:
        return ""
    s = _normalize_ws(label).strip().lower()
    # Cerca prima un numero
    m = re.search(r'\d+', s)
    if m:
        return m.group(0)
    # Altrimenti cerca lettera (Final A, Finals B)
    m = re.search(r'\b([a-z])\b', s)
    if m:
        return m.group(1).upper()
    return s


def filtra_per_manche_pilota(time_table, manche_per_fase):
    """Filtra il time table mostrando solo i turni delle Manche a
    cui il pilota e' assegnato per ogni fase.

    Param `manche_per_fase`: dict {fase_key: manche_label}, ottenuto
    da `trova_manche_pilota_per_fase()`. Se vuoto, ritorna time_table
    invariato (niente filtro).

    Per ogni riga del time table:
    - classifica la fase con `classifica_fase_turno`
    - se la fase non e' nel dict, tiene la riga (best-effort)
    - altrimenti tiene solo se la manche del turno matcha quella
      del pilota (case-insensitive, normalizza spazi)
    """
    if not manche_per_fase:
        return list(time_table)
    # Fallback intelligente: se la suddivisione di una fase non e'
    # ancora pubblicata su MyRCM (es. Qualif arriva dopo Prove), usa
    # la Manche del fase precedente. Le manche pre-finale (Prove
    # Libere, Prove, Qualif) sono normalmente le STESSE per ogni
    # pilota (chi e' Manche 2 in libere e' Manche 2 in qualif).
    # Per la FINALE invece niente fallback: Final A/B/C dipende dal
    # ranking qualifica, non si puo' estrapolare.
    fb_libere = (manche_per_fase.get("prove_libere") or
                 manche_per_fase.get("prove") or
                 manche_per_fase.get("qualif"))
    fb_prove = (manche_per_fase.get("prove") or
                manche_per_fase.get("prove_libere") or
                manche_per_fase.get("qualif"))
    fb_qualif = (manche_per_fase.get("qualif") or
                 manche_per_fase.get("prove") or
                 manche_per_fase.get("prove_libere"))
    fb_finale = manche_per_fase.get("finale")  # niente fallback

    out = []
    for r in time_table:
        fase = classifica_fase_turno(r)
        if fase is None:
            # Non classificabile: tieni la riga
            out.append(r)
            continue
        if fase == "prove_libere":
            manche_pilota = fb_libere
        elif fase == "prove":
            manche_pilota = fb_prove
        elif fase == "qualif":
            manche_pilota = fb_qualif
        elif fase == "finale":
            manche_pilota = fb_finale
        else:
            manche_pilota = manche_per_fase.get(fase)
        if not manche_pilota:
            # Fase non risolvibile per il pilota (tipico: finale non
            # ancora pubblicata). Tieni la riga (vedrai tutte le
            # final, sceglierai a colpo d'occhio quella tua).
            out.append(r)
            continue
        # Match cross-lingua: "Manche 1"/"Group 1" -> "1",
        # "Final A"/"Finals A" -> "A".
        norm_pilota = _normalizza_manche(manche_pilota)
        norm_riga = _normalizza_manche(r.get("manche", ""))
        if not norm_pilota or not norm_riga:
            out.append(r)
            continue
        if norm_pilota == norm_riga:
            out.append(r)
    return out


def filtra_per_categoria(time_table, categoria_keyword):
    """Ritorna le righe del time table che corrispondono alla
    categoria indicata. Match case-insensitive con normalizzazione
    spazi multipli (MyRCM ne usa spesso 2 o 3 di seguito) e supporto
    bidirezionale: matcha sia se la kw e' contenuta nella categoria
    della riga, sia viceversa (gestisce il caso in cui la lista
    categorie usa il nome corto e il time table aggiunge il codice
    in parentesi quadre, o viceversa)."""
    if not categoria_keyword:
        return list(time_table)
    kw = _normalize_ws(categoria_keyword).lower()
    if not kw:
        return list(time_table)
    out = []
    for r in time_table:
        cat_n = _normalize_ws(r.get("categoria", "")).lower()
        if kw in cat_n or (cat_n and cat_n in kw):
            out.append(r)
            continue
        # fallback: match in qualunque cella raw
        if any(kw in _normalize_ws(c).lower() for c in r.get("raw", [])):
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
    SOGLIA_PREP_MIN = 15    # giallo  - PREPARARE LA VETTURA
    SOGLIA_ATTESA_MIN = 3   # arancio - AVVICINARSI ALLA ZONA ATTESA
    SOGLIA_AVVIA_MIN = 1    # rosso lampeggiante - AVVIA MOTORE

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
        # Filtro per Manche del pilota (Suddivisione Batteria MyRCM)
        self.manche_per_fase = {}  # {fase_key: manche_label}
        self.nome_pilota = ""
        # Clock offset per modalita' SIMULAZIONE: differenza fissa tra
        # "ora simulata" e "ora reale del sistema". 0 = live (default).
        # Quando l'utente attiva una simulazione "26/04 09:00" mentre
        # ora reale e' "27/04 14:00", offset diventa -29h. Da li' in
        # poi self._now() ritorna sempre datetime.now() + offset, e
        # avanza in tempo reale (passa 1 sec reale -> 1 sec simulato).
        self.clock_offset = timedelta(0)
        # Path persistenza (sopravvive al riavvio TrackMind)
        self._state_path = self._calcola_state_path()
        # Listeners
        self._tick_listeners = []   # f(prossimo, dt_target, now)
        self._alert_listeners = []  # f(stato, prossimo, dt_target)
        # Tick state
        self._attivo = False
        self._tick_id = None
        self._ultimo_alert_stato = None  # 'prep' | 'avvia' | None

    def _calcola_state_path(self):
        """Path dove salvare lo stato per la persistenza tra riavvi."""
        # Stesso percorso che usa il resto di TrackMind: dati/
        try:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            return os.path.join(base, "dati", "assistente_gara_state.json")
        except Exception:
            return None

    def _now(self):
        """Tempo "corrente" del monitor. In live = datetime.now().
        In simulazione = datetime.now() + clock_offset (offset fisso
        calcolato all'attivazione)."""
        return datetime.now() + self.clock_offset

    @property
    def in_simulazione(self):
        return abs(self.clock_offset.total_seconds()) > 5

    # ── attivazione/disattivazione ────────────────────────────────
    def attiva(self, evento, categoria, time_table, delay_min=0,
               clock_offset=None, manche_per_fase=None,
               nome_pilota=""):
        self.evento = evento
        self.categoria = categoria
        self.time_table = time_table or []
        # Filtra prima per categoria, poi per manche del pilota.
        # `manche_per_fase` (es. {"prove_libere": "Manche 1"})
        # restringe ulteriormente alle sole batterie del pilota.
        cat_nome = (categoria or {}).get("nome", "")
        tt_cat = filtra_per_categoria(self.time_table, cat_nome)
        self.manche_per_fase = manche_per_fase or {}
        self.nome_pilota = (nome_pilota or "").strip()
        self.tt_filtrato = filtra_per_manche_pilota(
            tt_cat, self.manche_per_fase)
        self.delay_min = delay_min
        if clock_offset is not None:
            self.clock_offset = clock_offset
        self._ultimo_alert_stato = None
        if not self._attivo:
            self._attivo = True
            self._tick()
        # Salva stato per il riavvio
        self._salva_stato()
        # Avvia recorder MyRCM live (silenzioso, salva file scouting
        # ad ogni cambio sessione). Niente UI in v05.05.76 - in
        # v05.05.77 si aggiunge la transizione automatica alla
        # schermata griglia colonne quando inizia la manche del
        # pilota. Best-effort: se il modulo manca l'addon continua
        # senza recording.
        self._avvia_recorder_myrcm()

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
        # Ferma il recorder MyRCM
        self._ferma_recorder_myrcm()

    # ── Recorder MyRCM live (auto-import in background) ──
    def _avvia_recorder_myrcm(self):
        """Avvia (o riavvia) il MyRcmLiveRecorder per l'evento+
        categoria correnti. Idempotente. Si abbona anche come
        listener degli EVENT per gestire la transizione automatica
        alla UI live quando inizia la manche del pilota."""
        try:
            from myrcm_recorder import MyRcmLiveRecorder
        except Exception as e:
            print("[ag] recorder MyRCM non disponibile:", e)
            return
        # Stop precedente se cambia evento
        self._ferma_recorder_myrcm()
        eid = (self.evento or {}).get("event_id", "")
        if not eid:
            return
        try:
            base = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
            scouting_dir = os.path.join(base, "dati", "scouting")
            cat_nome = (self.categoria or {}).get("nome", "")
            self._recorder = MyRcmLiveRecorder(
                event_id=eid,
                scouting_dir=scouting_dir,
                category_filter_nome=cat_nome,
                on_sessione_salvata=lambda g, n: print(
                    "[ag] sessione salvata: %s (%d file)" % (g[:60], n)),
                on_status=lambda m: print("[ag] %s" % m),
                tk_root=self.root,
            )
            # Listener per auto-trigger UI live quando inizia la
            # manche del pilota
            self._recorder.add_event_listener(
                self._auto_apri_ui_live)
            # Listener per riallineare la time table sul tempo reale
            # MyRCM (se cronometraggio in tilt o ritardo non
            # registrato come "delay_min")
            self._recorder.add_event_listener(
                self._on_myrcm_event_riallineo)
            self._recorder.start()
            self._ui_live = None
            self._ui_live_attiva_per_group = None
        except Exception as e:
            print("[ag] errore avvio recorder MyRCM:", e)
            self._recorder = None

    def _on_myrcm_event_riallineo(self, meta, data):
        """Listener: ad ogni EVENT MyRCM con RACESTATE=rsRunning,
        cerca nella time table la riga corrispondente al GROUP
        attualmente in pista. Se la pianificazione TrackMind dice
        ora_X ma in realta' MyRCM e' partito alle ora_Y > ora_X,
        applica delay = (Y - X) cosi' il countdown si riallinea
        automaticamente. Trigger una sola volta per group (non
        oscilla ad ogni messaggio)."""
        try:
            state = (meta or {}).get("RACESTATE", "")
            group = (meta or {}).get("GROUP", "") or ""
            if state not in ("rsRunning", "rsStarted"):
                return
            if not group:
                return
            # Una sola volta per group (debounce)
            ultimo = getattr(self,
                             "_ultimo_group_riallineato", None)
            if ultimo == group:
                return
            self._ultimo_group_riallineato = group
            # Parse del GROUP -> categoria_tag, fase, manche
            try:
                from myrcm_import import parse_group_live
            except Exception:
                return
            cat_tag, fase, manche = parse_group_live(group)
            if not (cat_tag and manche):
                return
            # Cerca nella time_table COMPLETA (non filtrata per
            # categoria pilota) la riga matching
            tt_full = self.time_table or []
            riga_match = None
            cat_low = cat_tag.lower()
            fase_low = (fase or "").lower()
            for r in tt_full:
                r_cat = (r.get("categoria", "") or "").lower()
                if cat_low not in r_cat:
                    continue
                r_man = self._estrai_num_manche(r.get("manche", ""))
                if r_man != manche:
                    continue
                if fase_low:
                    r_grp = (r.get("gruppo", "") or "").lower()
                    if not self._fase_compatibile(fase_low, r_grp):
                        continue
                riga_match = r
                break
            if riga_match is None:
                return
            base_dt = riga_match.get("base_date")
            ora_str = riga_match.get("ora", "")
            if not base_dt or not ora_str:
                return
            # dt_pianificato = base_date + ora_str (HH:MM)
            try:
                hh, mm = ora_str.split(":")[:2]
                dt_plan = base_dt.replace(hour=int(hh),
                                          minute=int(mm),
                                          second=0,
                                          microsecond=0)
            except Exception:
                return
            now = self._now()
            delta_min = (now - dt_plan).total_seconds() / 60.0
            # Solo se il delay reale e' significativamente diverso
            # da quello attuale (margine 0.5 min) E positivo
            # (ritardi, non anticipi)
            if delta_min < 0.5:
                return
            new_delay = int(round(delta_min))
            if abs(new_delay - self.delay_min) < 1:
                return
            print("[ag riallineo] GROUP %r in corso: pianif=%s "
                  "now=%s delay reale=%d min (era %d)"
                  % (group[:60], dt_plan.strftime("%H:%M"),
                     now.strftime("%H:%M"), new_delay,
                     self.delay_min))
            self.delay_min = new_delay
            self._salva_stato()
            # Notifica subito i tick listener (UI countdown si
            # aggiorna senza aspettare il prossimo tick)
            try:
                for cb in list(self._tick_listeners):
                    try:
                        cb(None, None, now)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception as e:
            print("[ag riallineo] errore:", e)

    @staticmethod
    def _estrai_num_manche(s):
        """Estrae numero/lettera manche da 'Manche 1', 'Group 1',
        'Final A' ecc. Stessa logica di _norm_manche_mr."""
        try:
            from myrcm_import import _norm_manche_mr
            return _norm_manche_mr(s)
        except Exception:
            import re as _re
            m = _re.search(
                r'(?:manche|group|batteria|gruppo)\s*(\d+)',
                (s or "").lower())
            if m:
                return m.group(1)
            return None

    @staticmethod
    def _fase_compatibile(fase_myrcm, gruppo_tt):
        """True se la fase MyRCM (es. 'Prove') e' compatibile col
        campo gruppo della time table (es. 'Controlled practice 1').
        Cross-lingua IT/EN."""
        f = (fase_myrcm or "").lower()
        g = (gruppo_tt or "").lower()
        # Mapping fase -> parole chiave nel gruppo
        if "prove" in f or "practice" in f:
            return ("practice" in g or "prove" in g
                    or "training" in g or "freie" in g)
        if "qualif" in f:
            return "qualif" in g
        if "final" in f:
            return "final" in g
        return True  # generico, accetta

    def _auto_apri_ui_live(self, meta, data):
        """Chiamato a ogni EVENT WebSocket. Quando rileva che la
        sessione corrente sul WS e' una delle manche del pilota
        (filtrata) e RACESTATE diventa rsStarted/rsRunning, apre
        automaticamente la UI live griglia colonne. Quando termina
        (rsFinished), la chiude.

        Idempotente: non riapre se gia' aperta per la stessa manche.
        Una sola UI live alla volta per processo. RISPETTA la
        chiusura manuale: se l'utente ha premuto ESC sul LapTimer
        per uscire, NON riapre per lo stesso GROUP fino a quando il
        GROUP non cambia (= passa a una manche diversa)."""
        try:
            state = (meta or {}).get("RACESTATE", "")
            group = (meta or {}).get("GROUP", "") or ""
            if not group:
                return
            # Track del group corrente per rilevare cambi: se cambia,
            # svuota la blacklist "chiusi manualmente" cosi' alla
            # prossima manche pilota apriamo di nuovo automaticamente.
            ultimo_group = getattr(self, "_ui_live_ultimo_group", None)
            if ultimo_group is not None and ultimo_group != group:
                self._ui_live_chiusi_manualmente = set()
            self._ui_live_ultimo_group = group
            # Skip se l'utente ha chiuso manualmente la UI per
            # questo group (non vogliamo riaprirla loop infinito)
            chiusi = getattr(self, "_ui_live_chiusi_manualmente",
                             set())
            if group in chiusi:
                return
            # CHIUSURA AUTOMATICA in 3 casi:
            # 1) RACESTATE diventa rsFinished/rsClosed
            # 2) GROUP cambia (sessione precedente finita anche se
            #    MyRCM non ha mandato rsFinished esplicito)
            # 3) REMAININGTIME = 0:00:00 (countdown a zero)
            if (self._ui_live is not None
                    and self._ui_live_attiva_per_group):
                rem = (meta or {}).get("REMAININGTIME", "") or ""
                rem_zero = rem.strip() in ("0:00:00", "00:00:00",
                                           "0:00", "00:00", "")
                grp_aperto = self._ui_live_attiva_per_group
                if (state in ("rsFinished", "rsClosed")
                        and grp_aperto == group):
                    print("[ag] auto-close LapTimer: sessione "
                          "finita (rsFinished)")
                    self._chiudi_ui_live()
                    return
                if grp_aperto and grp_aperto != group:
                    print("[ag] auto-close LapTimer: GROUP cambiato "
                          "(%r -> %r)" % (grp_aperto[:40],
                                           group[:40]))
                    self._chiudi_ui_live()
                    return
                if (rem_zero and state in ("rsRunning", "rsStarted")
                        and grp_aperto == group):
                    print("[ag] auto-close LapTimer: REMAININGTIME=0")
                    self._chiudi_ui_live()
                    return
            # Apertura: stato RUNNING + non gia' aperta per questo
            # group. Se la sessione e' gia' in corso da piu' di 60s
            # (ci siamo connessi a meta' manche) APRIAMO comunque la
            # UI per visualizzare i tempi live, ma marchiamo
            # mid_session=True cosi' al close il LapTimer NON salva
            # i giri (sarebbero parziali, mancano i primi N giri
            # pregressi). Decisione utente: meglio vedere senza
            # salvare che non vedere affatto.
            running = state in ("rsStarted", "rsRunning")
            if running and not self._ui_live_attiva_per_group == group:
                ct_str = (meta or {}).get("CURRENTTIME", "0:00") or "0:00"
                ct_sec = self._parse_time_str(ct_str)
                mid_session = ct_sec > 60
                if mid_session:
                    print("[ag] LapTimer apertura MID-SESSION: "
                          "sessione %r gia' in corso da %ds. "
                          "I tempi NON verranno salvati al close."
                          % (group[:60], ct_sec))
                # Verifica che il group corrente corrisponda a una
                # manche del pilota (se non c'e' filtro, va sempre
                # bene)
                if self._group_e_del_pilota(group):
                    self._apri_ui_live_pilota(group,
                                              mid_session=mid_session)
        except Exception as e:
            print("[ag] errore auto_apri_ui:", e)

    def _parse_time_str(self, s):
        """Parse stringa CURRENTTIME/REMAININGTIME MyRCM in secondi.
        Accetta formati 'SS', 'MM:SS', 'H:MM:SS'. Ritorna 0 se
        parsing fallisce."""
        try:
            s = (s or "0").strip()
            if not s:
                return 0
            parts = s.split(":")
            if len(parts) == 3:
                return (int(parts[0]) * 3600
                        + int(parts[1]) * 60
                        + int(float(parts[2])))
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(float(parts[1]))
            return int(float(s))
        except (ValueError, TypeError):
            return 0

    def _group_e_del_pilota(self, group):
        """True se il GROUP MyRCM corrente corrisponde a una manche
        del pilota loggato. Se non c'e' filtro manche per fase
        (manche_per_fase vuoto), accetta tutte le manche della
        categoria scelta come 'del pilota'."""
        if not self.manche_per_fase:
            return True  # nessun filtro: tutte le manche sono "ok"
        gl = group.lower()
        # Estrai n manche dal group (es. 'Manche 4' o 'Group 4')
        import re as _re
        m = _re.search(r'(?:manche|group|batteria)\s*(\d+)', gl)
        manche_corr = m.group(1) if m else None
        if not manche_corr:
            return False
        # Cerca tra le manche del pilota
        for fase, label in self.manche_per_fase.items():
            mm = _re.search(r'(\d+)', str(label))
            if mm and mm.group(1) == manche_corr:
                return True
        return False

    def _apri_ui_live_pilota(self, group, mid_session=False):
        """Apre il LapTimer in modalita' MyRCM live. Riusa la
        schermata griglia colonne gia' fatta per LapMonitor BLE,
        alimentata dai dati WebSocket MyRCM tramite il recorder
        gia' attivo. Niente UI custom: LapTimer e' lo stesso
        addon di sempre.

        Param `mid_session`: True se la sessione era gia' partita
        (CURRENTTIME > 60s) al momento dell'apertura. In quel caso
        il LapTimer mostra i tempi LIVE ma NON salva nulla al close
        (i giri sarebbero parziali, manca lo storico iniziale)."""
        if self._ui_live is not None:
            self._chiudi_ui_live()
        try:
            from laptimer import LapTimer
        except Exception as e:
            print("[ag] LapTimer non disponibile:", e)
            return
        # Path dati per salvataggi (LapTimer usa dati_dir per piloti)
        try:
            base = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
            dati_dir = os.path.join(base, "dati")
        except Exception:
            dati_dir = ""
        cat_nome = (self.categoria or {}).get("nome", "")
        # Crea un Frame OVERLAY come parent del LapTimer: copre la
        # schermata corrente (countdown AssistenteGara o menu) ma
        # senza distruggerla. Al close del LapTimer distruggo solo
        # l'overlay e la schermata sotto torna visibile da sola
        # (niente schermo nero).
        parent_real = (getattr(self, "_vista_frame", None)
                        or self.root)
        try:
            import tkinter as _tk
            self._ui_live_overlay = _tk.Frame(parent_real,
                                               bg="#0a0a0a")
            self._ui_live_overlay.place(relx=0, rely=0,
                                         relwidth=1, relheight=1)
            self._ui_live_overlay.lift()
        except Exception as e:
            print("[ag] errore creazione overlay LapTimer:", e)
            self._ui_live_overlay = None
            return
        try:
            lt = LapTimer(
                setup="MyRCM Live - %s" % cat_nome[:30],
                pilota=self.nome_pilota or "Live",
                pista=(self.evento or {}).get("nome", ""),
                dati_dir=dati_dir,
                parent=self._ui_live_overlay,
                on_close=self._on_ui_live_chiusa)
            # Attiva modalita' MyRCM dopo che la UI e' stata creata.
            # Passa il flag mid_session cosi' il LapTimer sa se deve
            # disabilitare il salvataggio (sessione gia' in corso).
            self.root.after(50, lambda: lt.attiva_myrcm_live(
                self._recorder, mid_session=mid_session))
            self._ui_live = lt
            self._ui_live_attiva_per_group = group
            print("[ag] LapTimer MyRCM aperto per %s%s"
                  % (group[:60],
                     " [MID-SESSION: NO SAVE]" if mid_session else ""))
        except Exception as e:
            print("[ag] errore apertura LapTimer MyRCM:", e)
            self._ui_live = None

    def _chiudi_ui_live(self):
        """Chiusura automatica del LapTimer MyRCM al termine
        sessione. Chiama _chiudi() del LapTimer che SALVA i tempi
        accumulati + invoca _on_close (= _on_ui_live_chiusa) che
        distrugge l'overlay e disattiva il recorder.
        IMPORTANTE: NON disattivare il recorder PRIMA di _chiudi
        altrimenti il check 'if _myrcm_recorder' in _chiudi e
        _myrcm_salva_tempi_live fallisce e i tempi NON vengono
        salvati. La disattivazione listener avviene dopo via
        _on_ui_live_chiusa."""
        ui = self._ui_live
        if ui is not None:
            try:
                if hasattr(ui, "_chiudi"):
                    ui._chiudi()
            except Exception as e:
                print("[ag] errore chiusura auto LapTimer:", e)
        self._ui_live = None
        self._ui_live_attiva_per_group = None

    def _on_ui_live_chiusa(self):
        """Callback quando il LapTimer si chiude (ESC o auto a fine
        sessione). Distrugge solo l'overlay che conteneva il
        LapTimer: la schermata sotto (countdown Assistente Gara o
        menu retrodb) torna visibile da sola.
        Aggiunge il group corrente alla blacklist "chiusi
        manualmente" per evitare che _auto_apri_ui_live lo
        riapra subito al prossimo EVENT (loop infinito)."""
        ui = self._ui_live
        if ui is not None:
            try:
                if hasattr(ui, "disattiva_myrcm_live"):
                    ui.disattiva_myrcm_live()
            except Exception:
                pass
        # Memorizza che l'utente ha chiuso questo group (no auto-
        # riapertura finche' GROUP non cambia)
        try:
            grp_chiuso = self._ui_live_attiva_per_group
            if grp_chiuso:
                if not hasattr(self, "_ui_live_chiusi_manualmente"):
                    self._ui_live_chiusi_manualmente = set()
                self._ui_live_chiusi_manualmente.add(grp_chiuso)
        except Exception:
            pass
        self._ui_live = None
        self._ui_live_attiva_per_group = None
        # Distruggi l'overlay che conteneva il LapTimer.
        # La schermata sotto (countdown / menu) era nascosta dietro
        # ma non distrutta: torna visibile da sola.
        ov = getattr(self, "_ui_live_overlay", None)
        if ov is not None:
            try:
                ov.place_forget()
            except Exception:
                pass
            try:
                ov.destroy()
            except Exception:
                pass
        self._ui_live_overlay = None

    def _ferma_recorder_myrcm(self):
        # Chiudi prima la UI live se aperta
        ui = getattr(self, "_ui_live", None)
        if ui is not None:
            try:
                ui.chiudi()
            except Exception:
                pass
        self._ui_live = None
        self._ui_live_attiva_per_group = None
        rec = getattr(self, "_recorder", None)
        if rec is not None:
            try:
                rec.stop()
            except Exception:
                pass
        self._recorder = None

    def stato_recorder(self):
        """Snapshot stato recorder per UI (None se non attivo)."""
        rec = getattr(self, "_recorder", None)
        if rec is None:
            return None
        try:
            return rec.stato()
        except Exception:
            return None
        self.clock_offset = timedelta(0)
        # Cancella file di stato cosi' al prossimo avvio l'utente
        # ricomincia con la scelta evento.
        try:
            if self._state_path and os.path.exists(self._state_path):
                os.remove(self._state_path)
        except Exception:
            pass
        # Notifica un ultimo tick "spento" cosi' i listener si nascondono
        for cb in list(self._tick_listeners):
            try:
                cb(None, None, datetime.now())
            except Exception:
                pass

    @property
    def attivo(self):
        return self._attivo

    # ── persistenza ───────────────────────────────────────────────
    def _salva_stato(self):
        """Salva lo stato corrente del monitor su disco, cosi'
        sopravvive al riavvio TrackMind. Le righe time_table sono
        serializzate con base_date in formato ISO."""
        if not self._state_path:
            return
        try:
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        except Exception:
            pass
        try:
            tt_serial = []
            for r in self.time_table:
                bd = r.get("base_date")
                tt_serial.append({
                    "ora": r.get("ora", ""),
                    "categoria": r.get("categoria", ""),
                    "manche": r.get("manche", ""),
                    "gruppo": r.get("gruppo", ""),
                    "turno": r.get("turno", ""),
                    "raw": r.get("raw", []),
                    "base_date": bd.isoformat() if bd else None,
                })
            data = {
                "salvato": datetime.now().isoformat(),
                "evento": self.evento,
                "categoria": self.categoria,
                "time_table": tt_serial,
                "delay_min": self.delay_min,
                "clock_offset_sec": self.clock_offset.total_seconds(),
                "manche_per_fase": self.manche_per_fase or {},
                "nome_pilota": self.nome_pilota or "",
            }
            with open(self._state_path, "w", encoding="utf-8") as f:
                import json as _json
                _json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def carica_stato_persistito(self):
        """Carica lo stato dal disco e riattiva il monitor se trovato.
        Ritorna True se ha riattivato qualcosa, False altrimenti.
        Lo stato persiste finche' l'utente non lo annulla
        esplicitamente (STOP MONITOR / CAMBIA EVENTO). Niente timeout
        automatico: una gara puo' durare 3 giorni e l'utente non vuole
        ripartire dalla scelta evento ogni mattina."""
        if not self._state_path or not os.path.exists(self._state_path):
            return False
        try:
            import json as _json
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            return False
        # Ricostruisci time_table con base_date come datetime
        tt = []
        for r in data.get("time_table", []):
            bd_str = r.get("base_date")
            try:
                bd = datetime.fromisoformat(bd_str) if bd_str else None
            except Exception:
                bd = None
            tt.append({
                "ora": r.get("ora", ""),
                "categoria": r.get("categoria", ""),
                "manche": r.get("manche", ""),
                "gruppo": r.get("gruppo", ""),
                "turno": r.get("turno", ""),
                "raw": r.get("raw", []),
                "base_date": bd,
            })
        clock_offset_sec = data.get("clock_offset_sec", 0) or 0
        self.attiva(
            data.get("evento") or {},
            data.get("categoria") or {},
            tt,
            delay_min=int(data.get("delay_min", 0) or 0),
            clock_offset=timedelta(seconds=clock_offset_sec),
            manche_per_fase=data.get("manche_per_fase") or {},
            nome_pilota=data.get("nome_pilota") or "")
        return True

    # ── ritardo manuale ───────────────────────────────────────────
    def imposta_delay(self, delay_min):
        self.delay_min = int(delay_min)
        # Reset alert: se ho appena spostato gli orari, gli edge si
        # ricomputano alla prossima soglia raggiunta.
        self._ultimo_alert_stato = None
        self._salva_stato()

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
        categoria selezionata, applicando il delay manuale e tenendo
        conto della modalita' simulazione (clock_offset)."""
        if not self._attivo or not self.tt_filtrato:
            return None, None
        if now is None:
            now = self._now()
        prossimo = None
        prossimo_dt = None
        for r in self.tt_filtrato:
            ora = r.get("ora", "")
            base_date = r.get("base_date")
            # Usa la data della giornata di gara se disponibile,
            # altrimenti fallback a "oggi" (utile per test su un
            # singolo giorno).
            dt = _ora_to_dt(ora, base_date=base_date)
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
        now = self._now()
        prossimo, dt_target = self.trova_prossimo(now)
        # Notifica tick listeners (widget header, UI fullscreen, ecc.)
        for cb in list(self._tick_listeners):
            try:
                cb(prossimo, dt_target, now)
            except Exception:
                pass
        # Edge detection alert: 3 soglie progressive.
        if dt_target is not None:
            secs = (dt_target - now).total_seconds()
            nuovo_stato = None
            if 0 < secs <= self.SOGLIA_AVVIA_MIN * 60:
                nuovo_stato = "avvia"
            elif 0 < secs <= self.SOGLIA_ATTESA_MIN * 60:
                nuovo_stato = "attesa"
            elif 0 < secs <= self.SOGLIA_PREP_MIN * 60:
                nuovo_stato = "prep"
            # Trigger alert solo al CAMBIO di stato verso uno
            # piu' avanzato (non torno indietro). Ordine:
            # None < prep (-15) < attesa (-3) < avvia (-1).
            ordine = {None: 0, "prep": 1, "attesa": 2, "avvia": 3}
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
    elif stato == "attesa":
        titolo = ">>> AVVICINARSI ALLA ZONA ATTESA <<<"
        sotto = "3 minuti al tuo turno"
        col_bg = "#331a00"
        col_fg = "#ff8800"
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
    SOGLIA_PREP_MIN = 15    # giallo: PREPARARE LA VETTURA
    SOGLIA_ATTESA_MIN = 3   # arancio: AVVICINARSI ALLA ZONA ATTESA
    SOGLIA_AVVIA_MIN = 1    # rosso lampeggiante: AVVIA MOTORE
    REFRESH_COUNTDOWN_MS = 1000  # tick countdown 1 Hz

    def __init__(self, parent=None, on_close=None,
                 nome_pilota_default=""):
        self.c = _carica_colori()
        self._on_close = on_close
        # Nome utente loggato in TrackMind: usato come default per il
        # filtro "Tuo nome" cosi' chi corre con il proprio nome reale
        # registrato in MyRCM trova subito le SUE manche, senza dover
        # scrivere niente.
        self._nome_pilota_default = (nome_pilota_default or "").strip()

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
        # Variante con strikethrough per le sessioni gia' fatte
        # (durata trascorsa). Cosi' colpo d'occhio: le righe sbarrate
        # sono sessioni completate, le altre ancora da fare.
        self._f_info_strike = tkfont.Font(family=FONT_MONO, size=11,
                                            overstrike=1)
        self._f_small = tkfont.Font(family=FONT_MONO, size=9)
        self._f_count = tkfont.Font(family=FONT_MONO, size=22, weight="bold")
        self._f_count_big = tkfont.Font(family=FONT_MONO, size=36,
                                         weight="bold")

        # Stato locale (in fase di scelta evento/categoria).
        # Il time table NON e' qui: lo tiene il MONITOR singleton.
        self._eventi = []
        self._categorie = []
        self._tick_listener = None  # callback registrato sul monitor

        # Monitor singleton: se gia' attivo, salta dritto al countdown.
        # Se il monitor non e' attivo IN MEMORIA ma c'e' uno stato
        # salvato su disco recente (<24h), lo riattiviamo automaticamente:
        # cosi' al riavvio TrackMind l'addon riprende esattamente dove
        # era, senza dover ripassare per scelta evento/categoria.
        monitor = AssistenteGaraMonitor.get(self._top)
        if monitor and not monitor.attivo:
            try:
                monitor.carica_stato_persistito()
            except Exception:
                pass
        if monitor and monitor.attivo:
            self._schermata_timetable_monitor()
        else:
            self._schermata_iniziale()

    # =================================================================
    #  Helper UI
    # =================================================================
    def _pulisci(self):
        # Deregistra tick listener UI prima di distruggere widget
        # (il monitor singleton resta vivo con tutto il suo stato).
        if self._tick_listener is not None:
            try:
                m = AssistenteGaraMonitor.get(self._top)
                if m:
                    m.remove_tick_listener(self._tick_listener)
            except Exception:
                pass
            self._tick_listener = None
        for w in self.root.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

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

    def _footer_status(self, testo="", livello="info"):
        c = self.c
        col = {
            "ok": c["stato_ok"],
            "errore": c["stato_errore"],
            "avviso": c["stato_avviso"],
            "info": c["testo_dim"],
        }.get(livello, c["testo_dim"])
        self._status_lbl = tk.Label(self.root, text=testo,
                                     bg=c["sfondo"], fg=col,
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

        # ── Form input con stile retro coerente (RetroField) ──
        # Tre campi: filtro nazione, ID/URL evento, simulazione data.
        # I RetroField hanno lo stesso look del resto di TrackMind:
        # celle singole verde su nero, cursore lampeggiante, font
        # monospace WarGames-style. Niente piu' Entry tk grezze.
        form_frame = tk.Frame(self.root, bg=c["sfondo"])
        form_frame.pack(fill="x", padx=10, pady=(6, 4))

        # Riga 1: filtro nazione
        bar = tk.Frame(form_frame, bg=c["sfondo"])
        bar.pack(fill="x", pady=(0, 2))
        if _HAS_RETROFIELD:
            self._sf_naz = RetroField(bar, label="Filtro nazione",
                                       tipo="S", lunghezza=8,
                                       on_enter=lambda: self._carica_eventi(),
                                       label_width=22)
            self._sf_naz.pack(side="left", padx=(0, 8))
            try:
                self._sf_naz.set("ita")
            except Exception:
                pass
        else:
            self._naz_var = tk.StringVar(value="ita")
            tk.Label(bar, text="Filtro nazione:", bg=c["sfondo"],
                     fg=c["label"], font=self._f_info).pack(side="left",
                                                             padx=(0, 6))
            ent = tk.Entry(bar, textvariable=self._naz_var,
                           font=self._f_info, width=8,
                           bg=c["sfondo_celle"], fg=c["dati"],
                           insertbackground=c["dati"],
                           relief="solid", bd=1)
            ent.pack(side="left", padx=(0, 8))
            ent.bind("<Return>", lambda e: self._carica_eventi())
        tk.Button(bar, text="AGGIORNA", font=self._f_btn,
                  bg=c["cerca_sfondo"], fg=c["cerca_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._carica_eventi).pack(side="left", padx=4)
        tk.Label(bar,
                 text="(svuota per tutti gli eventi mondiali)",
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(side="left", padx=(8, 0))

        # Riga 2: apri per ID o URL evento (NIENTE bottone inline:
        # l'utente compila ID + altri campi, poi preme APRI EVENTO
        # in fondo alla schermata cosi' tutti i campi sono pronti).
        # Enter sul campo NON apre piu' subito, lascia tempo di
        # compilare gli altri campi.
        bar2 = tk.Frame(form_frame, bg=c["sfondo"])
        bar2.pack(fill="x", pady=(2, 2))
        if _HAS_RETROFIELD:
            self._sf_evt_id = RetroField(bar2,
                                          label="Apri ID o URL",
                                          tipo="S", lunghezza=42,
                                          label_width=22)
            self._sf_evt_id.pack(side="left", padx=(0, 8))
        else:
            self._evt_id_var = tk.StringVar(value="")
            tk.Label(bar2, text="Apri evento per ID o URL:",
                     bg=c["sfondo"], fg=c["label"],
                     font=self._f_info).pack(side="left", padx=(0, 6))
            ent_id = tk.Entry(bar2, textvariable=self._evt_id_var,
                               font=self._f_info, width=42,
                               bg=c["sfondo_celle"], fg=c["dati"],
                               insertbackground=c["dati"],
                               relief="solid", bd=1)
            ent_id.pack(side="left", padx=(0, 8))
        tk.Label(bar2,
                 text="(opzionale: compila gli altri campi, "
                      "poi APRI EVENTO)",
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(side="left", padx=(0, 8))

        # Riga 3: hint simulazione (popup nascosto, F12 per aprirlo).
        # I due campi data/ora simulati sono in un popup separato per
        # tenere pulita la schermata iniziale: la simulazione serve
        # solo in fase di test (verifica fix MyRCM su evento storico,
        # demo a -3 min dal turno, ecc.). Valori salvati in
        # self._sim_data_saved / self._sim_ora_saved e letti da
        # _leggi_sim anche dopo la chiusura del popup.
        bar3 = tk.Frame(form_frame, bg=c["sfondo"])
        bar3.pack(fill="x", pady=(2, 2))
        self._lbl_hint_sim = tk.Label(
            bar3,
            text="[F12] simulazione data/ora (test)",
            bg=c["sfondo"], fg=c["testo_dim"],
            font=self._f_small, cursor="hand2")
        self._lbl_hint_sim.pack(side="left", padx=(0, 8))
        # Click sulla label = apre popup (alternativa a F12, comoda
        # con touch screen su uConsole).
        self._lbl_hint_sim.bind(
            "<Button-1>", lambda e: self._apri_popup_simulazione())
        # Aggiorna label con stato corrente (se gia' attiva da
        # interazione precedente, mostra "[ATTIVA: ...]").
        self._aggiorna_hint_simulazione()

        # Riga 4: tuo nome (per filtro per Manche/Gruppo).
        # Se compilato, l'addon scarica la "Suddivisione Batteria"
        # MyRCM e mostra nel time table SOLO i turni della manche
        # cui sei assegnato (es. Manche 1 invece di Manche 1+2).
        # Se vuoto, niente filtro: mostra tutti i turni della
        # categoria (comportamento precedente).
        bar4 = tk.Frame(form_frame, bg=c["sfondo"])
        bar4.pack(fill="x", pady=(2, 2))
        nome_default = (self._nome_pilota_default or "")[:32]
        if _HAS_RETROFIELD:
            self._sf_nome = RetroField(bar4,
                                        label="Tuo nome (filtro)",
                                        tipo="S", lunghezza=32,
                                        label_width=22)
            self._sf_nome.pack(side="left", padx=(0, 8))
            try:
                if nome_default:
                    self._sf_nome.set(nome_default)
            except Exception:
                pass
        else:
            self._nome_var = tk.StringVar(value=nome_default)
            tk.Label(bar4, text="Tuo nome (filtro):",
                     bg=c["sfondo"], fg=c["label"],
                     font=self._f_info).pack(side="left", padx=(0, 6))
            ent_nome = tk.Entry(bar4, textvariable=self._nome_var,
                                 font=self._f_info, width=32,
                                 bg=c["sfondo_celle"], fg=c["dati"],
                                 insertbackground=c["dati"],
                                 relief="solid", bd=1)
            ent_nome.pack(side="left", padx=(0, 8))
        tk.Label(bar4,
                 text="(vuoto = mostra tutte le manche della categoria)",
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(side="left", padx=(0, 8))

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

        # Bottoni in fondo: UNICO "APRI EVENTO" che usa l'ID se
        # compilato, altrimenti la selezione della lista. Cosi' sai
        # sempre cosa cliccare a fine compilazione, e tutti i campi
        # (ID, simulazione, nome) sono gia' pronti al momento dell'apri.
        btnbar = tk.Frame(self.root, bg=c["sfondo"])
        btnbar.pack(fill="x", padx=10, pady=(0, 4))
        tk.Button(btnbar, text="APRI EVENTO", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                  relief="ridge", bd=2, cursor="hand2",
                  command=self._apri_evento).pack(side="left", padx=4)
        tk.Label(btnbar,
                 text="(usa ID se compilato, altrimenti la selezione "
                      "in lista)",
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(side="left", padx=(8, 0))

        self._footer_status("Pronto. Premi AGGIORNA per caricare la "
                            "lista eventi MyRCM.")
        # Scorciatoia tastiera per aprire popup simulazione
        # (alternativa al click sulla label hint)
        try:
            self._top.bind("<F12>",
                           lambda e: self._apri_popup_simulazione())
        except Exception:
            pass
        # Auto-carica all'avvio
        self.root.after(200, self._carica_eventi)

    def _leggi_naz(self):
        """Legge il filtro nazione dal RetroField o dalla StringVar
        di fallback."""
        try:
            if hasattr(self, "_sf_naz"):
                return (self._sf_naz.get() or "").strip()
            if hasattr(self, "_naz_var"):
                return (self._naz_var.get() or "").strip()
        except Exception:
            pass
        return ""

    def _leggi_evt_id(self):
        """Legge l'ID evento o URL dal RetroField o dalla StringVar."""
        try:
            if hasattr(self, "_sf_evt_id"):
                return (self._sf_evt_id.get() or "").strip()
            if hasattr(self, "_evt_id_var"):
                return (self._evt_id_var.get() or "").strip()
        except Exception:
            pass
        return ""

    def _leggi_sim(self):
        """Legge i campi simulazione (data + ora) e ritorna la
        stringa nel formato che `_parsa_simulazione` si aspetta:
            "DD/MM/YYYY HH:MM" se entrambi compilati
            "DD/MM/YYYY"       se solo data
            "HH:MM"            se solo ora
            ""                  se entrambi vuoti

        Tre fonti in priorita':
        1) RetroField nel popup simulazione (se aperto)
        2) Valori salvati negli attributi self._sim_data_saved /
           self._sim_ora_saved (anche dopo chiusura popup)
        3) Vecchio campo libero (compat retro)"""
        def _vuoto_o_separatori(s):
            # I RetroField D/O ritornano "  /  /    " o "  :  " quando
            # vuoti: rimuovo separatori e spazi, se resta vuoto e' tale.
            pulito = re.sub(r"[\s/:]", "", s)
            return not pulito
        try:
            data_raw = ""
            ora_raw = ""
            # Priorita' 1: RetroField popup (se ancora vivi)
            try:
                if (hasattr(self, "_sf_sim_data") and
                        self._sf_sim_data._canvas.winfo_exists()):
                    data_raw = (self._sf_sim_data.get() or "").strip()
            except Exception:
                pass
            try:
                if (hasattr(self, "_sf_sim_ora") and
                        self._sf_sim_ora._canvas.winfo_exists()):
                    ora_raw = (self._sf_sim_ora.get() or "").strip()
            except Exception:
                pass
            if data_raw and _vuoto_o_separatori(data_raw):
                data_raw = ""
            if ora_raw and _vuoto_o_separatori(ora_raw):
                ora_raw = ""
            # Priorita' 2: valori salvati in attributi (popup chiuso)
            if not data_raw:
                data_raw = (getattr(self, "_sim_data_saved", "") or "").strip()
            if not ora_raw:
                ora_raw = (getattr(self, "_sim_ora_saved", "") or "").strip()
            if data_raw or ora_raw:
                return ("%s %s" % (data_raw, ora_raw)).strip()
            # Priorita' 3: fallback al vecchio campo unico (retrocompat)
            if hasattr(self, "_sf_sim"):
                return (self._sf_sim.get() or "").strip()
            if hasattr(self, "_sim_var"):
                return (self._sim_var.get() or "").strip()
        except Exception:
            pass
        return ""

    def _aggiorna_hint_simulazione(self):
        """Aggiorna la label hint sotto il form: in grigio se
        simulazione disattivata, in arancione con valori se attiva."""
        try:
            if not (hasattr(self, "_lbl_hint_sim")
                    and self._lbl_hint_sim.winfo_exists()):
                return
        except Exception:
            return
        c = self.c
        d = (getattr(self, "_sim_data_saved", "") or "").strip()
        h = (getattr(self, "_sim_ora_saved", "") or "").strip()
        if d or h:
            txt = "[F12] SIMULAZIONE ATTIVA: %s %s" % (d or "(oggi)",
                                                        h or "(09:00)")
            self._lbl_hint_sim.config(text=txt, fg=c["stato_avviso"])
        else:
            self._lbl_hint_sim.config(
                text="[F12] simulazione data/ora (test)",
                fg=c["testo_dim"])

    def _apri_popup_simulazione(self):
        """Toplevel per impostare data/ora simulate. La simulazione
        serve a testare l'addon contro un evento storico (es. fix
        MyRCM, demo a -1 min dal turno) senza dover cambiare
        l'ora di sistema. Vuoti = LIVE."""
        c = self.c
        # Se gia' aperto, rialza
        if (hasattr(self, "_top_sim")
                and getattr(self, "_top_sim", None) is not None):
            try:
                if self._top_sim.winfo_exists():
                    self._top_sim.lift()
                    return
            except Exception:
                pass
        top = tk.Toplevel(self._top)
        top.title("Simulazione data/ora")
        top.transient(self._top)
        top.configure(bg=c["sfondo"])
        top.attributes("-topmost", True)
        # Geometria centrata sul Toplevel
        try:
            tw, th = 480, 220
            x = (self._top.winfo_rootx()
                 + self._top.winfo_width() // 2 - tw // 2)
            y = (self._top.winfo_rooty()
                 + self._top.winfo_height() // 2 - th // 2)
            top.geometry("%dx%d+%d+%d" % (tw, th, max(0, x), max(0, y)))
        except Exception:
            pass
        self._top_sim = top

        tk.Label(top, text="SIMULAZIONE DATA/ORA",
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_btn).pack(pady=(12, 4))
        tk.Label(top,
                 text="Test su un evento storico o anteprima turno.\n"
                      "Vuoti = LIVE. Solo data = quel giorno alle 09:00.",
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small,
                 justify="center").pack(pady=(0, 10))

        body = tk.Frame(top, bg=c["sfondo"])
        body.pack(pady=(0, 8))
        if _HAS_RETROFIELD:
            self._sf_sim_data = RetroField(body, label="Sim. data",
                                            tipo="D", label_width=12)
            self._sf_sim_data.pack(side="left", padx=(8, 8))
            self._sf_sim_ora = RetroField(body, label="Sim. ora",
                                           tipo="O", label_width=10)
            self._sf_sim_ora.pack(side="left", padx=(0, 8))
            # Precompila con valori salvati
            try:
                if getattr(self, "_sim_data_saved", ""):
                    self._sf_sim_data.set(self._sim_data_saved)
                if getattr(self, "_sim_ora_saved", ""):
                    self._sf_sim_ora.set(self._sim_ora_saved)
            except Exception:
                pass
        else:
            # Fallback senza RetroField: due Entry semplici
            self._sim_data_var_pop = tk.StringVar(
                value=getattr(self, "_sim_data_saved", ""))
            self._sim_ora_var_pop = tk.StringVar(
                value=getattr(self, "_sim_ora_saved", ""))
            tk.Label(body, text="Data (DD/MM/YYYY):",
                     bg=c["sfondo"], fg=c["label"],
                     font=self._f_info).pack(side="left", padx=(8, 4))
            tk.Entry(body, textvariable=self._sim_data_var_pop,
                     font=self._f_info, width=12).pack(side="left",
                                                        padx=(0, 8))
            tk.Label(body, text="Ora (HH:MM):",
                     bg=c["sfondo"], fg=c["label"],
                     font=self._f_info).pack(side="left", padx=(0, 4))
            tk.Entry(body, textvariable=self._sim_ora_var_pop,
                     font=self._f_info, width=8).pack(side="left",
                                                       padx=(0, 8))

        def _vuoto_o_separatori(s):
            return not re.sub(r"[\s/:]", "", s or "")

        def _salva_e_chiudi():
            d = ""
            h = ""
            try:
                if hasattr(self, "_sf_sim_data"):
                    d = (self._sf_sim_data.get() or "").strip()
                elif hasattr(self, "_sim_data_var_pop"):
                    d = (self._sim_data_var_pop.get() or "").strip()
                if hasattr(self, "_sf_sim_ora"):
                    h = (self._sf_sim_ora.get() or "").strip()
                elif hasattr(self, "_sim_ora_var_pop"):
                    h = (self._sim_ora_var_pop.get() or "").strip()
            except Exception:
                pass
            if _vuoto_o_separatori(d):
                d = ""
            if _vuoto_o_separatori(h):
                h = ""
            self._sim_data_saved = d
            self._sim_ora_saved = h
            try:
                top.destroy()
            except Exception:
                pass
            self._top_sim = None
            self._aggiorna_hint_simulazione()

        def _disattiva():
            self._sim_data_saved = ""
            self._sim_ora_saved = ""
            try:
                top.destroy()
            except Exception:
                pass
            self._top_sim = None
            self._aggiorna_hint_simulazione()

        bbar = tk.Frame(top, bg=c["sfondo"])
        bbar.pack(pady=(8, 12))
        tk.Button(bbar, text="OK", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=2, cursor="hand2", width=10,
                  command=_salva_e_chiudi).pack(side="left", padx=4)
        tk.Button(bbar, text="DISATTIVA", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                  relief="ridge", bd=2, cursor="hand2", width=12,
                  command=_disattiva).pack(side="left", padx=4)
        tk.Button(bbar, text="ANNULLA", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=2, cursor="hand2", width=10,
                  command=lambda: (top.destroy(),
                                    setattr(self, "_top_sim", None))
                  ).pack(side="left", padx=4)

        top.bind("<Escape>",
                 lambda e: (top.destroy(),
                            setattr(self, "_top_sim", None)))
        top.bind("<Return>", lambda e: _salva_e_chiudi())
        top.protocol("WM_DELETE_WINDOW",
                     lambda: (top.destroy(),
                              setattr(self, "_top_sim", None)))

    def _leggi_nome(self):
        """Legge il campo "tuo nome" dal RetroField o dalla StringVar.
        Usato per filtrare il time table per Manche del pilota."""
        try:
            if hasattr(self, "_sf_nome"):
                return (self._sf_nome.get() or "").strip()
            if hasattr(self, "_nome_var"):
                return (self._nome_var.get() or "").strip()
        except Exception:
            pass
        return ""

    def _carica_eventi(self):
        """Scarica lista eventi online MyRCM (in thread)."""
        c = self.c
        try:
            self._lb_eventi.delete(0, "end")
            self._lb_eventi.insert("end", "  Caricamento in corso...")
        except Exception:
            return
        self._set_status("Connessione a MyRCM...", "avviso")
        naz = self._leggi_naz()

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
        # Niente focus auto qui: l'utente puo' voler compilare prima
        # i campi (ID, simulazione, nome) e poi premere APRI EVENTO.
        # Se preferisce usare la lista basta cliccarla o premere Tab.
        self._set_status(
            "Trovati %d eventi. \u2191\u2193 in lista o compila "
            "ID/Simulazione/Nome poi APRI EVENTO."
            % len(self._eventi), "ok")

    def _apri_evento(self):
        """Dispatcher unico: se il campo "Apri ID o URL" e' compilato,
        apri quell'evento; altrimenti usa la selezione della lista."""
        raw = self._leggi_evt_id()
        if raw:
            self._apri_evento_per_id()
        else:
            self._scegli_evento()

    def _apri_evento_per_id(self):
        """Apre direttamente un evento dato il suo ID MyRCM (o un URL
        completo da cui estraggo dId[E]=NNN). Utile quando l'evento
        non e' in lista 'online' (passato, futuro, oppure scoperto da
        URL condiviso). Costruisce un dict evento minimale e va
        dritto alla schermata categorie."""
        raw = self._leggi_evt_id()
        if not raw:
            self._set_status("Inserisci un ID o un URL evento MyRCM",
                              "avviso")
            return
        # Estrai ID: o numero nudo, oppure pattern dId[E]=NNN dall'URL
        eid = None
        if raw.isdigit():
            eid = raw
        else:
            m = re.search(r'dId\[E\]=(\d+)', raw)
            if m:
                eid = m.group(1)
            else:
                m = re.search(r'dId%5BE%5D=(\d+)', raw)
                if m:
                    eid = m.group(1)
        if not eid:
            self._set_status(
                "ID non riconosciuto. Inserisci numero (es. 94090) "
                "o URL contenente dId[E]=NNN", "errore")
            return
        # Costruisci dict evento minimale - i campi mancanti verranno
        # mostrati come "?", non e' un problema.
        self._evento_sel = {
            "event_id": eid,
            "nome": "Evento #%s" % eid,
            "organizzatore": "?",
            "nazione": "?",
            "link": "",
        }
        self._set_status("Apertura evento %s..." % eid, "avviso")
        self._schermata_categorie()

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
        # Frecce su/giu sono native nella listbox tk, ma servono
        # con il widget che ha il focus. Diamo focus alla listbox
        # appena le categorie sono caricate (in _mostra_categorie).
        # TAB esce verso il bottone APRI CATEGORIA.
        self._lb_cat.bind("<Tab>", lambda e: (
            btn_apri_cat.focus_set(), "break")[-1])
        self._lb_cat.insert("end", "  Caricamento categorie...")

        btnbar = tk.Frame(self.root, bg=c["sfondo"])
        btnbar.pack(fill="x", padx=10, pady=(0, 4))
        btn_apri_cat = tk.Button(btnbar, text="APRI CATEGORIA",
                  font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["stato_avviso"],
                  relief="ridge", bd=2, cursor="hand2",
                  command=self._scegli_categoria)
        btn_apri_cat.pack(side="left", padx=4)
        # Enter sul bottone -> apri. Shift+Tab torna alla listbox.
        btn_apri_cat.bind("<Return>",
            lambda e: (self._scegli_categoria(), "break")[-1])
        btn_apri_cat.bind("<Shift-Tab>",
            lambda e: (self._lb_cat.focus_set(), "break")[-1])

        self._footer_status("Carico categorie...", "avviso")

        # Scarico SOLO le categorie. Il time table NON e' nella pagina
        # principale dell'evento: e' aggregato per giornata via AJAX
        # (un reportKey per giornata) sotto la pagina report di una
        # categoria specifica. Quindi lo scarichiamo in un secondo
        # tempo, dopo che l'utente sceglie la categoria.
        eid = self._evento_sel.get("event_id", "")

        def _bg():
            try:
                cats = scarica_categorie(eid) or []
            except Exception:
                cats = []
            self.root.after(0, lambda: self._mostra_categorie(cats))

        threading.Thread(target=_bg, daemon=True).start()

    def _mostra_categorie(self, categorie):
        try:
            self._lb_cat.delete(0, "end")
        except Exception:
            return
        self._categorie = categorie or []

        if not self._categorie:
            self._lb_cat.insert("end",
                "  Nessuna categoria trovata per questo evento.")
            self._set_status("Categorie non trovate. Verifica l'ID "
                              "evento e la connessione internet.",
                             "errore")
            return
        for cat in self._categorie:
            riga = "  %s   (id %s)" % (
                cat.get("nome", "?"), cat.get("category_id", "?"))
            self._lb_cat.insert("end", riga)
        self._lb_cat.selection_set(0)
        self._lb_cat.activate(0)
        # Focus sulla listbox cosi' frecce su/giu' (native) e
        # Enter/Tab funzionano subito senza dover cliccare.
        try:
            self._lb_cat.focus_set()
        except Exception:
            pass

        self._set_status(
            "%d categorie. \u2191\u2193 = naviga | Enter o doppio "
            "click = apri | Tab = vai a APRI CATEGORIA"
            % len(self._categorie), "ok")

    def _parsa_simulazione(self):
        """Legge il campo simulazione e ritorna (clock_offset, data).
        clock_offset = timedelta(0) se LIVE, altrimenti differenza
        tra "ora simulata" e "ora reale del sistema".
        data = la data per cui scaricare il time table.
        Formati accettati:
            ""              -> LIVE, oggi reale
            "HH:MM"         -> oggi reale alle HH:MM (test orario di
                               un turno tra qualche minuto)
            "DD/MM/YYYY"    -> quella data alle 09:00
            "DD/MM/YYYY HH:MM" -> quella data e ora
        Se non riesce a parsare, ritorna (timedelta(0), oggi)."""
        s = self._leggi_sim()
        if not s:
            return timedelta(0), datetime.now().date()
        # Solo HH:MM
        m = re.match(r"^(\d{1,2}):(\d{2})$", s)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            sim_dt = datetime.now().replace(hour=hh, minute=mm,
                                             second=0, microsecond=0)
            return sim_dt - datetime.now(), sim_dt.date()
        # DD/MM/YYYY HH:MM o DD/MM/YYYY
        m = re.match(
            r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})"
            r"(?:\s+(\d{1,2}):(\d{2}))?$", s)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
            hh = int(m.group(4)) if m.group(4) else 9
            mm = int(m.group(5)) if m.group(5) else 0
            try:
                sim_dt = datetime(y, mo, d, hh, mm, 0)
            except ValueError:
                return timedelta(0), datetime.now().date()
            return sim_dt - datetime.now(), sim_dt.date()
        # Non parsato: live + oggi
        return timedelta(0), datetime.now().date()

    def _scegli_categoria(self):
        sel = self._lb_cat.curselection()
        if not sel:
            self._set_status("Seleziona una categoria", "avviso")
            return
        idx = sel[0]
        if idx < 0 or idx >= len(self._categorie):
            return
        cat = self._categorie[idx]
        # Scarico in parallelo:
        # - time table (giornata target)
        # - partecipanti (popolano automaticamente trasponder.json)
        # - suddivisione batterie -> manche del pilota loggato
        eid = (self._evento_sel or {}).get("event_id", "")
        cid = cat.get("category_id", "")
        clock_offset, data_target = self._parsa_simulazione()
        nome_pilota = self._leggi_nome()
        sim_label = (" [SIM %s]" % data_target.strftime("%d/%m")
                     if abs(clock_offset.total_seconds()) > 5 else "")
        self._set_status(
            "Scarico time table + partecipanti per %s%s..."
            % (cat.get("nome", "?")[:30], sim_label),
            "avviso")

        def _bg():
            # Time table
            try:
                tt = scarica_timetable_evento(
                    eid, cid, data_target=data_target) or []
            except Exception as e:
                tt = []
                err = str(e)[:80]
                self.root.after(0, lambda: self._set_status(
                    "Errore time table: " + err, "errore"))
                return
            # Registra evento in cache locale (pista, data) -> event_id
            # cosi' RICERCA da NUOVA LETTURA puo' ritrovare l'evento
            # anche dopo che e' finito e sparito dall'elenco LIVE.
            # Usiamo il vero nome PISTA dal campo "Block" della pagina
            # evento (es. "MycandyArena" per evento 94090, mentre il
            # titolo evento e' "Campionato Costruttori..." e non
            # matcherebbe la digitazione utente in NUOVA LETTURA).
            try:
                from myrcm_import import (registra_evento_in_cache,
                                          scarica_info_evento)
                base = os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__)))
                scouting_dir = os.path.join(base, "dati", "scouting")
                ev_nome = (self._evento_sel or {}).get("nome", "")
                # Best-effort: estrai il "Block" (nome pista vero)
                info_ev = {}
                try:
                    info_ev = scarica_info_evento(eid) or {}
                except Exception:
                    info_ev = {}
                pista_block = (info_ev.get("pista_block", "") or "").strip()
                # Se Block e' "n/a" o vuoto usa il titolo come fallback
                if not pista_block or pista_block.lower() in ("n/a", "na"):
                    pista_block = ev_nome
                titolo_ev = info_ev.get("titolo", "") or ev_nome
                # Salva una entry per ogni giorno coperto dal time table:
                # l'utente in NUOVA LETTURA potrebbe selezionare giorni
                # diversi (Prove Libere, Qualif, Finale spesso su giorni
                # consecutivi).
                date_uniche = set()
                for r in tt:
                    bd = r.get("base_date")
                    if bd:
                        date_uniche.add(bd.strftime("%d/%m/%Y"))
                if not date_uniche and data_target:
                    date_uniche.add(data_target.strftime("%d/%m/%Y"))
                # Doppia entry per giorno: una con il nome PISTA (Block),
                # una col titolo evento. Cosi' il lookup matcha sia chi
                # digita "Mycandy Arena" sia chi digita parole del titolo.
                for d_str in date_uniche:
                    registra_evento_in_cache(
                        scouting_dir, pista_block, d_str, eid, titolo_ev)
                    if titolo_ev and titolo_ev != pista_block:
                        registra_evento_in_cache(
                            scouting_dir, titolo_ev, d_str, eid, titolo_ev)
            except Exception:
                pass
            # Partecipanti -> tabella trasponder.json (auto, no UI)
            n_aggiunti = 0
            n_aggiornati = 0
            try:
                piloti = scarica_partecipanti(eid, cid) or []
                if piloti:
                    n_aggiunti, n_aggiornati = (
                        self._scrivi_trasponder_json(piloti))
            except Exception:
                pass
            # Suddivisione batterie -> manche del pilota per ogni fase
            manche_per_fase = {}
            if nome_pilota:
                try:
                    manche_per_fase = trova_manche_pilota_per_fase(
                        eid, cid, nome_pilota) or {}
                except Exception:
                    manche_per_fase = {}
            self.root.after(
                0, lambda: self._attiva_monitor_con_tt(
                    cat, tt, clock_offset, n_aggiunti, n_aggiornati,
                    manche_per_fase, nome_pilota))

        threading.Thread(target=_bg, daemon=True).start()

    def _attiva_monitor_con_tt(self, cat, time_table,
                                clock_offset=None,
                                piloti_aggiunti=0,
                                piloti_aggiornati=0,
                                manche_per_fase=None,
                                nome_pilota=""):
        """Callback dopo download: attiva il monitor singleton e
        apre la schermata countdown. `manche_per_fase` (se non vuoto)
        filtra il time table per mostrare solo le batterie del
        pilota nelle varie fasi (Prove Libere, Prove, Qualif, Finale)."""
        if not time_table:
            self._set_status(
                "Nessun turno trovato per la giornata target. "
                "Verifica la data (campo Simulazione) o che la "
                "tabella oraria sia pubblicata su MyRCM.", "errore")
            return
        monitor = AssistenteGaraMonitor.get(self._top)
        if monitor:
            monitor.attiva(self._evento_sel, cat, time_table,
                           delay_min=0,
                           clock_offset=clock_offset
                           if clock_offset is not None
                           else timedelta(0),
                           manche_per_fase=manche_per_fase or {},
                           nome_pilota=nome_pilota or "")
        # Status iniziale: include piloti importati e info filtro
        msgs = []
        if piloti_aggiunti or piloti_aggiornati:
            msgs.append("Trasponder: %d nuovi, %d aggiornati"
                        % (piloti_aggiunti, piloti_aggiornati))
        if manche_per_fase and nome_pilota:
            mfs = ", ".join("%s=%s" % (k.replace("_", " "), v)
                            for k, v in manche_per_fase.items())
            msgs.append("Pilota %s | %s" % (nome_pilota, mfs))
        elif nome_pilota and not manche_per_fase:
            msgs.append("Pilota %s NON trovato in suddivisione "
                        "batterie (mostro tutte le manche)"
                        % nome_pilota)
        self._import_piloti_msg = " | ".join(msgs) if msgs else None
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
        # Registra callback per ridisegnare questa schermata quando
        # il LapTimer MyRCM si chiude (altrimenti restano widget
        # distrutti = schermo nero) + il vista_frame dove il
        # LapTimer deve essere embedded (NON il toplevel, altrimenti
        # distrugge anche il resto di TrackMind).
        try:
            monitor._on_chiudi_lap_callback = (
                self._schermata_timetable_monitor)
            monitor._vista_frame = self.root
        except Exception:
            pass
        evento = monitor.evento or {}
        categoria = monitor.categoria or {}
        cat_nome = categoria.get("nome", "?")

        # Header con bottoni back + stop monitor
        # IMPORTANTE: "TORNA AL MENU" fa solo uscire dall'UI ma il
        # monitor resta attivo (countdown + alert continuano sullo
        # sfondo). "ANNULLA EVENTO" e' l'UNICA strada per spegnere
        # il monitor. Richiede doppia pressione per evitare click
        # accidentali (l'utente deve PROPRIO volerlo).
        h = tk.Frame(self.root, bg=c["sfondo"])
        h.pack(fill="x", padx=10, pady=(8, 0))
        tk.Button(h, text="TORNA AL MENU", font=self._f_btn,
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=2, cursor="hand2",
                  command=self._chiudi_lasciando_monitor).pack(side="left")
        # Reset stato doppia pressione (ad ogni rebuild della UI)
        self._stop_doppia = 0
        self._btn_stop = tk.Button(h, text="ANNULLA EVENTO",
                  font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["stato_errore"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._stop_monitor_con_conferma)
        self._btn_stop.pack(side="left", padx=(6, 0))
        # Bottone CHECKLIST: apre l'editor (notepad retro gia' fatto
        # per il PROMPT IA) sul file dati/checklist_gara.txt.
        # L'utente compila la sua lista personale di cose da fare a
        # 15 min dal turno (es. RIEMPIRE BIBERON, MONTARE GOMME,
        # VERIFICARE TRASPONDER, ecc.). Quando il countdown raggiunge
        # -15 min, il tree turni viene sostituito da questa lista.
        tk.Button(h, text="CHECKLIST", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["cerca_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._apri_editor_checklist).pack(
            side="left", padx=(6, 0))
        # Bottone VEDI LIVE: apre la UI griglia colonne MyRCM live
        # a richiesta. Utile quando la sessione e' ancora in
        # rsPrepared (in attesa del via) oppure per ri-aprirla dopo
        # averla chiusa con ESC.
        tk.Button(h, text="VEDI LIVE", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["stato_ok"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._apri_ui_live_manuale).pack(
            side="left", padx=(6, 0))
        tk.Label(h, text="  TIME TABLE - " + cat_nome[:30],
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(8, 0))
        try:
            _aggiungi_barra_bat(self.root)
        except Exception:
            pass
        tk.Frame(self.root, bg=c["linee"], height=1).pack(
            fill="x", padx=10, pady=(6, 4))

        # Riga info + ritardo + indicatore SIMULAZIONE
        info_bar = tk.Frame(self.root, bg=c["sfondo"])
        info_bar.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(info_bar,
                 text="Evento: %s" % (evento.get("nome", "?")[:40]),
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(side="left")
        if monitor.in_simulazione:
            sim_now = monitor._now()
            tk.Label(info_bar,
                     text="   [SIMULAZIONE %s]"
                          % sim_now.strftime("%d/%m %H:%M"),
                     bg=c["sfondo"], fg=c["stato_errore"],
                     font=self._f_small).pack(side="left", padx=(8, 0))
        tk.Label(info_bar, text="   Ritardo:",
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

        # Tree turni della giornata: ogni turno e' una riga colorata
        # in base al suo stato (passato/in corso/prossimo/futuro).
        # Canvas scrollabile per gestire eventi con molti turni.
        list_frame = tk.Frame(self.root, bg=c["sfondo"])
        list_frame.pack(fill="both", expand=True, padx=10, pady=4)
        sb = tk.Scrollbar(list_frame, bg=c["sfondo"],
                          troughcolor=c["sfondo"],
                          activebackground=c["dati"])
        sb.pack(side="right", fill="y")
        self._tt_canvas = tk.Canvas(list_frame, bg=c["sfondo_celle"],
                                     highlightthickness=0,
                                     yscrollcommand=sb.set,
                                     bd=1, relief="solid")
        self._tt_canvas.pack(side="left", fill="both", expand=True)
        sb.config(command=self._tt_canvas.yview)
        self._tt_inner = tk.Frame(self._tt_canvas, bg=c["sfondo_celle"])
        self._tt_canvas.create_window((0, 0), window=self._tt_inner,
                                       anchor="nw", tags="inner")
        def _on_resize(event):
            try:
                self._tt_canvas.configure(
                    scrollregion=self._tt_canvas.bbox("all"))
                # Adatta larghezza inner alla larghezza canvas
                self._tt_canvas.itemconfig(
                    "inner",
                    width=event.width if event.widget == self._tt_canvas
                    else self._tt_canvas.winfo_width())
            except Exception:
                pass
        self._tt_canvas.bind("<Configure>", _on_resize)
        self._tt_inner.bind("<Configure>", _on_resize)
        # Mouse wheel scroll
        def _scroll(e):
            try:
                self._tt_canvas.yview_scroll(
                    int(-1 * (e.delta / 120)) if hasattr(e, "delta") and e.delta
                    else (-1 if getattr(e, "num", 0) == 4 else 1), "units")
            except Exception:
                pass
        self._tt_canvas.bind_all("<MouseWheel>", _scroll)
        self._tt_canvas.bind_all("<Button-4>", _scroll)
        self._tt_canvas.bind_all("<Button-5>", _scroll)

        # Popola le righe (sara' ricolorato ad ogni tick)
        self._tt_rows = []  # [(turno_dict, frame, lbls...)]
        # Stato switch tree <-> checklist (-15 min)
        self._checklist_visibile = False
        self._popola_tree_turni(monitor)

        sim_hint = (" | Modalita' SIMULAZIONE attiva"
                    if monitor.in_simulazione else "")
        import_msg = getattr(self, "_import_piloti_msg", None)
        if import_msg:
            self._footer_status(
                "%s | Monitor ATTIVO: countdown live anche fuori da qui."
                % import_msg + sim_hint, "ok")
            self._import_piloti_msg = None  # consumato
        else:
            self._footer_status(
                "Monitor ATTIVO: countdown live anche fuori da qui. "
                "STOP MONITOR per spegnerlo." + sim_hint, "ok")

        # Registra tick listener sul monitor cosi' la UI si aggiorna
        # automaticamente ad ogni tick (1 Hz). Quando esci, deregistra.
        self._tick_listener = self._on_tick_ui
        monitor.add_tick_listener(self._tick_listener)
        # Trigger immediato per non aspettare il primo secondo
        self._on_tick_ui(*monitor.trova_prossimo() + (datetime.now(),))

    def _popola_tree_turni(self, monitor):
        """Costruisce la lista verticale dei turni della giornata
        per la categoria selezionata. Una riga per turno, ognuna
        salvata in self._tt_rows. I colori vengono aggiornati ad
        ogni tick da _aggiorna_stato_tree_turni()."""
        c = self.c
        cat_nome = (monitor.categoria or {}).get("nome", "?")
        # Pulisci righe esistenti
        for w in list(self._tt_inner.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        self._tt_rows = []

        if not monitor.tt_filtrato:
            tk.Label(self._tt_inner,
                     text=("  Nessun turno trovato per categoria '%s'.\n"
                           "  Possibili cause:\n"
                           "  - tabella oraria non pubblicata su MyRCM\n"
                           "  - data simulazione non corrisponde a "
                           "una giornata di gara\n"
                           "  - parser non riconosce il layout"
                           % cat_nome),
                     bg=c["sfondo_celle"], fg=c["stato_avviso"],
                     font=self._f_info, justify="left",
                     anchor="w").pack(fill="x", padx=8, pady=8)
            return

        # Header riga
        hr = tk.Frame(self._tt_inner, bg=c["linee"])
        hr.pack(fill="x", padx=2, pady=(2, 4))
        for txt, w in (("Ora", 8), ("Manche", 16), ("Gruppo", 18),
                        ("Stato", 24)):
            tk.Label(hr, text=txt, bg=c["linee"], fg=c["dati"],
                     font=self._f_btn, width=w,
                     anchor="w").pack(side="left", padx=4)

        # Una riga per turno
        for r in monitor.tt_filtrato:
            row_frame = tk.Frame(self._tt_inner, bg=c["sfondo_celle"],
                                  bd=1, relief="flat")
            row_frame.pack(fill="x", padx=2, pady=1)
            ora_orig = r.get("ora", "?")
            dt = _ora_to_dt(ora_orig, r.get("base_date"))
            if dt and monitor.delay_min:
                dt = dt + timedelta(minutes=monitor.delay_min)
            ora_eff = (dt.strftime("%H:%M")
                       if dt is not None else ora_orig)
            lbl_ora = tk.Label(row_frame, text=ora_eff,
                                bg=c["sfondo_celle"], fg=c["dati"],
                                font=self._f_info, width=8,
                                anchor="w")
            lbl_ora.pack(side="left", padx=4, pady=2)
            lbl_man = tk.Label(row_frame,
                                text=(r.get("manche") or "")[:16],
                                bg=c["sfondo_celle"],
                                fg=c["testo_dim"],
                                font=self._f_info, width=16,
                                anchor="w")
            lbl_man.pack(side="left", padx=4, pady=2)
            lbl_grp = tk.Label(row_frame,
                                text=(r.get("gruppo") or "")[:18],
                                bg=c["sfondo_celle"],
                                fg=c["testo_dim"],
                                font=self._f_info, width=18,
                                anchor="w")
            lbl_grp.pack(side="left", padx=4, pady=2)
            lbl_stato = tk.Label(row_frame, text="",
                                  bg=c["sfondo_celle"], fg=c["dati"],
                                  font=self._f_info, width=24,
                                  anchor="w")
            lbl_stato.pack(side="left", padx=4, pady=2)
            self._tt_rows.append({
                "turno": r,
                "dt_target": dt,
                "frame": row_frame,
                "lbl_ora": lbl_ora,
                "lbl_man": lbl_man,
                "lbl_grp": lbl_grp,
                "lbl_stato": lbl_stato,
            })
        # Forza redraw + scroll region update
        try:
            self._tt_inner.update_idletasks()
            self._tt_canvas.configure(
                scrollregion=self._tt_canvas.bbox("all"))
        except Exception:
            pass

    def _aggiorna_stato_tree_turni(self, now):
        """Ad ogni tick, ricolora le righe del tree in base allo
        stato (passato/in corso/prossimo/futuro). Inoltre gestisce
        lo switch tree <-> checklist quando il prossimo turno si
        avvicina a -15 minuti."""
        c = self.c
        # ── Switch tree <-> checklist a -15 min ──
        # Determina secondi al prossimo turno (per il check)
        secs_min = None
        if getattr(self, "_tt_rows", None):
            for row in self._tt_rows:
                dt = row.get("dt_target")
                if dt is None:
                    continue
                s = (dt - now).total_seconds()
                if s > 0 and (secs_min is None or s < secs_min):
                    secs_min = s
        # Range per mostrare la checklist: da -15 min a -1 min.
        # Sotto -1 min (= AVVIA MOTORE) tornano i turni cosi' l'utente
        # vede di nuovo la sua riga IN CORSO.
        in_range_checklist = (
            secs_min is not None and 60 < secs_min <= 15 * 60)
        checklist_attiva = getattr(self, "_checklist_visibile", False)
        monitor = AssistenteGaraMonitor.get(self._top)
        if in_range_checklist and not checklist_attiva:
            # Entra in zona checklist: sostituisci tree con checklist.
            try:
                self._mostra_checklist_nel_tree()
            except Exception:
                pass
            return  # niente colorazione tree (e' nascosto)
        if (not in_range_checklist) and checklist_attiva:
            # Esci dalla zona checklist: ripristina tree turni.
            self._checklist_visibile = False
            try:
                if monitor is not None:
                    self._popola_tree_turni(monitor)
            except Exception:
                pass
        if checklist_attiva:
            # In checklist: niente da ricolorare (i widget sono altri)
            return
        # ── Colorazione standard del tree turni ──
        if not getattr(self, "_tt_rows", None):
            return
        # Trova il "prossimo turno" per evidenziarlo
        prossimo_dt = None
        for row in self._tt_rows:
            dt = row.get("dt_target")
            if dt is None:
                continue
            if dt > now and (prossimo_dt is None or dt < prossimo_dt):
                prossimo_dt = dt

        for row in self._tt_rows:
            dt = row.get("dt_target")
            if dt is None:
                continue
            # Durata stimata: cella raw[4] formato "MM:SS"
            durata_min = 10
            try:
                raw_dur = (row["turno"].get("raw") or [])
                if len(raw_dur) >= 5:
                    dms = raw_dur[4].strip()
                    if ":" in dms:
                        d_mm, _ = dms.split(":")
                        durata_min = max(1, int(d_mm))
            except Exception:
                pass
            fine_turno = dt + timedelta(minutes=durata_min)
            secs_to_start = (dt - now).total_seconds()

            if now >= fine_turno:
                # PASSATO / FATTA: sessione completata. Sbarriamo
                # con strikethrough cosi' a colpo d'occhio si
                # distinguono dalle non-fatte e dall'IN CORSO.
                bg = c["sfondo"]
                fg_main = c["testo_dim"]
                fg_dim = c["testo_dim"]
                stato_txt = "FATTA"
                font_riga = self._f_info_strike
            elif now >= dt:
                # IN CORSO
                bg = "#0a3a0a"
                fg_main = c["stato_ok"]
                fg_dim = c["stato_ok"]
                stato_txt = "IN CORSO"
                font_riga = self._f_info
            elif dt == prossimo_dt:
                # PROSSIMO turno - 4 stati progressivi
                font_riga = self._f_info
                if secs_to_start <= 60:
                    # AVVIA MOTORE (lampeggia)
                    if int(now.timestamp()) % 2 == 0:
                        bg = "#ff4444"; fg_main = "#000000"
                        fg_dim = "#000000"
                    else:
                        bg = "#660000"; fg_main = "#ff4444"
                        fg_dim = "#ff8888"
                    stato_txt = ">>> AVVIA MOTORE <<<"
                elif secs_to_start <= 3 * 60:
                    # AVVICINARSI ALLA ZONA ATTESA (-3 min)
                    bg = "#663300"; fg_main = "#ff8800"
                    fg_dim = "#ff8800"
                    secs = int(secs_to_start)
                    stato_txt = ("AVVICINARSI ATTESA  -%d:%02d"
                                  % (secs // 60, secs % 60))
                elif secs_to_start <= 15 * 60:
                    bg = "#664400"; fg_main = "#ffaa00"
                    fg_dim = "#ffaa00"
                    mins = int(secs_to_start // 60)
                    stato_txt = "PREP VETTURA  -%dmin" % mins
                else:
                    bg = c["sfondo_celle"]; fg_main = c["dati"]
                    fg_dim = c["dati"]
                    secs = int(secs_to_start)
                    ore = secs // 3600
                    mm = (secs % 3600) // 60
                    if ore > 0:
                        cd = "fra %dh%02dm" % (ore, mm)
                    else:
                        cd = "fra %dm" % mm
                    stato_txt = "PROSSIMO  " + cd
            else:
                # FUTURO (non e' il prossimo)
                bg = c["sfondo_celle"]
                fg_main = c["testo_dim"]
                fg_dim = c["testo_dim"]
                stato_txt = ""
                font_riga = self._f_info
            try:
                row["frame"].config(bg=bg)
                row["lbl_ora"].config(bg=bg, fg=fg_main, font=font_riga)
                row["lbl_man"].config(bg=bg, fg=fg_dim, font=font_riga)
                row["lbl_grp"].config(bg=bg, fg=fg_dim, font=font_riga)
                row["lbl_stato"].config(bg=bg, fg=fg_main,
                                         text=stato_txt,
                                         font=font_riga)
            except Exception:
                pass

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
        # Aggiorna tree turni con nuovi orari (effetto del delay)
        try:
            self._popola_tree_turni(monitor)
        except Exception:
            pass
        # Trigger update immediato del countdown
        self._on_tick_ui(*monitor.trova_prossimo() + (monitor._now(),))

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
            self._popola_tree_turni(monitor)
        except Exception:
            pass
        self._on_tick_ui(*monitor.trova_prossimo() + (monitor._now(),))

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
        # Stato visivo (4 livelli). NB: confronto su SECONDI esatti,
        # non su minuti interi: a 90 secondi mancanti `mins` sarebbe
        # gia' 1 e attiverebbe AVVIA MOTORE in anticipo di 30 secondi.
        if secs <= self.SOGLIA_AVVIA_MIN * 60:  # <= 60 sec
            # Lampeggia: rosso pieno alternato
            bg = "#660000"
            fg = "#ff4444"
            alert = ">>> AVVIA MOTORE <<<"
            if (now.second % 2) == 0:
                bg = "#ff4444"
                fg = "#000000"
        elif secs <= self.SOGLIA_ATTESA_MIN * 60:  # <= 180 sec
            bg = "#663300"
            fg = "#ff8800"
            alert = ">>> AVVICINARSI ALLA ZONA ATTESA <<<"
        elif secs <= self.SOGLIA_PREP_MIN * 60:  # <= 900 sec
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
        # Aggiorna anche i colori del tree turni
        try:
            self._aggiorna_stato_tree_turni(now)
        except Exception:
            pass

    # =================================================================
    #  CHECKLIST PRE-GARA (-15 min)
    # =================================================================
    _CHECKLIST_DEFAULT = """LISTA CONTROLLO PRE-GARA
========================
Cose da fare quando mancano 15 minuti al tuo turno.
Edita questa lista col bottone CHECKLIST nell'header.

 1. RIEMPIRE BIBERON CON MISCELA (controlla %)
 2. ASSICURARSI DEI NUMERI GARA GIUSTI SULLA VETTURA
 3. MONTARE E SERRARE LE GOMME (verifica usura/temp)
 4. VERIFICARE TRASPONDER FUNZIONANTE E SERRATO
 5. CONTROLLARE LIVELLO CARBURANTE / BATTERIA RX-TX
 6. REGOLARE FRENO E STERZO
 7. VERIFICARE FILTRO ARIA E CANDELA
 8. AVVITARE TUTTE LE VITI ACCESSIBILI
 9. PORTARE IN ZONA ATTESA: VETTURA, BIBERON, ATTREZZI
10. ACCENDERE RADIO E VERIFICARE PORTATA
"""

    def _path_checklist(self):
        """Path del file checklist_gara.txt nella cartella dati/."""
        try:
            base = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
            dati_dir = os.path.join(base, "dati")
            return os.path.join(dati_dir, "checklist_gara.txt")
        except Exception:
            return None

    def _carica_checklist(self):
        """Carica il testo della checklist da disco. Se il file non
        esiste o fallisce la lettura, ritorna il default."""
        path = self._path_checklist()
        if not path or not os.path.exists(path):
            return self._CHECKLIST_DEFAULT
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = f.read()
            return txt if txt.strip() else self._CHECKLIST_DEFAULT
        except Exception:
            return self._CHECKLIST_DEFAULT

    def _apri_ui_live_manuale(self):
        """Bottone VEDI LIVE: apre il LapTimer in modalita' MyRCM
        live a richiesta. Funziona in qualunque stato (anche
        rsPrepared = in attesa del via). Riusa il recorder gia'
        attivo del monitor + il LapTimer come UI."""
        monitor = AssistenteGaraMonitor.get(self.root)
        if monitor is None:
            self._set_status("Monitor non attivo", "errore")
            return
        rec = getattr(monitor, "_recorder", None)
        if rec is None:
            self._set_status(
                "Recorder MyRCM non attivo. "
                "Torna al menu e ri-seleziona evento+categoria.",
                "errore")
            return
        # Se gia' aperto un LapTimer MyRCM, niente da fare
        if monitor._ui_live is not None:
            self._set_status("LapTimer MyRCM gia' aperto", "ok")
            return
        # Delega al monitor che usa LapTimer (vedi
        # AssistenteGaraMonitor._apri_ui_live_pilota)
        try:
            group = (rec.metadata_live().get("GROUP", "") or "manuale")
            monitor._apri_ui_live_pilota(group)
            self._set_status("LapTimer MyRCM aperto", "ok")
        except Exception as e:
            self._set_status("Errore apertura LapTimer: %s" % e,
                              "errore")

    def _apri_editor_checklist(self):
        """Apre il PromptEditor (notepad retro) sul file
        dati/checklist_gara.txt. Stesso editor del PROMPT IA."""
        try:
            from prompt_editor import PromptEditor
        except ImportError:
            try:
                _here = os.path.dirname(os.path.abspath(__file__))
                if _here not in sys.path:
                    sys.path.insert(0, _here)
                from prompt_editor import PromptEditor
            except ImportError:
                self._set_status(
                    "PromptEditor non disponibile", "errore")
                return
        path = self._path_checklist()
        # Crea il file con default se non esiste, cosi' l'editor
        # apre qualcosa di gia' compilato (esempio modificabile).
        if path and not os.path.exists(path):
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._CHECKLIST_DEFAULT)
            except Exception:
                pass
        try:
            PromptEditor(self._top, file_path=path,
                          default_text=self._CHECKLIST_DEFAULT,
                          titolo="CHECKLIST PRE-GARA - Editor")
        except Exception as e:
            self._set_status("Errore editor: %s" % str(e)[:60],
                             "errore")

    def _mostra_checklist_nel_tree(self):
        """Sostituisce il tree turni con la checklist pre-gara.
        Chiamato quando il countdown entra in stato "prep" (-15min)
        o "attesa" (-3min)."""
        if not hasattr(self, "_tt_inner"):
            return
        try:
            for w in list(self._tt_inner.winfo_children()):
                try:
                    w.destroy()
                except Exception:
                    pass
        except Exception:
            return
        c = self.c
        # Header riga
        hr = tk.Frame(self._tt_inner, bg="#664400")
        hr.pack(fill="x", padx=2, pady=(2, 4))
        tk.Label(hr, text=">>> CHECKLIST PRE-GARA <<<",
                 bg="#664400", fg="#ffaa00",
                 font=self._f_btn, anchor="center").pack(
            fill="x", padx=4, pady=4)
        # Testo della checklist (Text widget readonly per layout
        # multi-riga rispettando le interruzioni del file).
        txt_frame = tk.Frame(self._tt_inner, bg=c["sfondo_celle"])
        txt_frame.pack(fill="both", expand=True, padx=4, pady=4)
        txt = tk.Text(txt_frame, font=self._f_info,
                      bg=c["sfondo_celle"], fg=c["stato_avviso"],
                      relief="flat", bd=0, wrap="word",
                      height=20)
        txt.pack(fill="both", expand=True)
        try:
            txt.insert("1.0", self._carica_checklist())
            txt.config(state="disabled")
        except Exception:
            pass
        # Forza redraw scroll region
        try:
            self._tt_inner.update_idletasks()
            self._tt_canvas.configure(
                scrollregion=self._tt_canvas.bbox("all"))
        except Exception:
            pass
        # Marca lo stato per non ricostruire ad ogni tick
        self._checklist_visibile = True

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

    def _stop_monitor_con_conferma(self):
        """Doppia pressione obbligatoria su ANNULLA EVENTO.
        Prima pressione: cambia il testo del bottone in "CONFERMA?"
        rosso brillante e arma per 4 secondi. Seconda pressione
        (entro 4s): spegne davvero il monitor.
        Cosi' un click accidentale non distrugge mai lo stato."""
        if self._stop_doppia == 0:
            # Prima pressione: arma
            self._stop_doppia = 1
            try:
                self._btn_stop.config(text="CONFERMA ANNULLA?",
                                       bg=self.c["stato_errore"],
                                       fg="#000000")
            except Exception:
                pass
            # Reset automatico dopo 4 secondi
            try:
                self.root.after(4000, self._stop_monitor_reset)
            except Exception:
                pass
            return
        # Seconda pressione (entro 4s): spegni davvero
        self._stop_monitor()

    def _stop_monitor_reset(self):
        """Resetta lo stato della doppia pressione."""
        if self._stop_doppia == 0:
            return
        self._stop_doppia = 0
        try:
            if self._btn_stop and self._btn_stop.winfo_exists():
                c = self.c
                self._btn_stop.config(text="ANNULLA EVENTO",
                                       bg=c["pulsanti_sfondo"],
                                       fg=c["stato_errore"])
        except Exception:
            pass

    def _stop_monitor(self):
        """Spegne completamente il monitor: niente piu' countdown,
        niente piu' alert. L'utente vorra' rilanciare l'addon
        (lista eventi) per riattivarlo. Cancella anche il file di
        stato persistito su disco."""
        m = AssistenteGaraMonitor.get(self._top)
        if m:
            m.disattiva()
        if self._tick_listener:
            self._tick_listener = None
        # Torna alla schermata iniziale (lista eventi)
        self._schermata_iniziale()

    def _importa_partecipanti(self):
        """Scarica la lista partecipanti della categoria corrente
        da MyRCM e aggiunge/aggiorna i record in dati/trasponder.json.
        Cosi' il LapMonitor BLE riconosce subito i nomi dei piloti
        gia' iscritti in gara, senza doverli aggiungere a mano."""
        m = AssistenteGaraMonitor.get(self._top)
        if not m or not m.attivo:
            self._set_status("Monitor non attivo", "errore")
            return
        eid = (m.evento or {}).get("event_id", "")
        cid = (m.categoria or {}).get("category_id", "")
        if not eid or not cid:
            self._set_status("Evento o categoria mancanti", "errore")
            return
        self._set_status("Scarico partecipanti da MyRCM...", "avviso")

        def _bg():
            try:
                piloti = scarica_partecipanti(eid, cid) or []
            except Exception as e:
                err = str(e)[:80]
                self.root.after(0, lambda: self._set_status(
                    "Errore: " + err, "errore"))
                return
            try:
                aggiunti, aggiornati = self._scrivi_trasponder_json(
                    piloti)
            except Exception as e:
                err = str(e)[:80]
                self.root.after(0, lambda: self._set_status(
                    "Errore salvataggio: " + err, "errore"))
                return
            msg = ("Importati %d piloti: %d nuovi, %d aggiornati"
                   % (len(piloti), aggiunti, aggiornati))
            self.root.after(0, lambda: self._set_status(msg, "ok"))

        threading.Thread(target=_bg, daemon=True).start()

    def _scrivi_trasponder_json(self, piloti):
        """Aggiunge/aggiorna i record in dati/trasponder.json. Match
        per Numero (transponder pulito senza /N). Ritorna
        (n_aggiunti, n_aggiornati). Niente cancellazioni."""
        import json as _json
        # Path: stesso di assistente_gara_state.json
        try:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        except Exception:
            base = "."
        path = os.path.join(base, "dati", "trasponder.json")
        # Carica esistenti
        records = []
        max_id = 0
        max_codice = 0
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                records = data.get("records", []) if isinstance(
                    data, dict) else (data or [])
                for r in records:
                    try:
                        cd = int(r.get("Codice", 0) or 0)
                        if cd > max_codice:
                            max_codice = cd
                    except Exception:
                        pass
                    rid = r.get("_id", "") or ""
                    if rid:
                        try:
                            n = int(rid, 16)
                            if n > max_id:
                                max_id = n
                        except Exception:
                            pass
        except Exception:
            records = []

        idx_per_num = {}
        for r in records:
            num = str(r.get("Numero", "") or "").strip()
            if num:
                idx_per_num[num] = r

        aggiunti = 0
        aggiornati = 0
        for p in piloti:
            num = (p.get("transponder") or "").strip()
            nome = (p.get("nome") or "").strip()
            if not num or not nome:
                continue
            note_parts = []
            if p.get("club"):
                note_parts.append(p["club"])
            if p.get("modello"):
                note_parts.append(p["modello"])
            if p.get("nazione"):
                note_parts.append(p["nazione"])
            note = " - ".join(note_parts)[:50]

            esist = idx_per_num.get(num)
            if esist:
                # Aggiorna solo se cambiano dati (non distrugge dati
                # personali aggiunti dall'utente)
                cambiato = False
                if (esist.get("Pilota") or "").strip() != nome:
                    esist["Pilota"] = nome
                    cambiato = True
                old_note = (esist.get("Note") or "").strip()
                if note and not old_note:
                    esist["Note"] = note
                    cambiato = True
                if cambiato:
                    aggiornati += 1
            else:
                max_codice += 1
                max_id += 1
                _id_hex = ("%08x" % max_id)
                rec = {
                    "_id": _id_hex,
                    "_utente_id": "",
                    "Codice": str(max_codice),
                    "Numero": num,
                    "Pilota": nome,
                    "Note": note,
                    "_timestamp": datetime.now().isoformat(),
                }
                records.append(rec)
                idx_per_num[num] = rec
                aggiunti += 1

        # Riconfeziona JSON nel formato TrackMind {_meta, records}
        # Se il file originale era una lista nuda (vecchio formato),
        # converto a quello con _meta.
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data_out = {
                "_meta": {
                    "tabella": "trasponder",
                    "accesso": "tutti",
                    "versione": __version__ if "__version__" in globals()
                                 else "05.05.00",
                },
                "records": records,
            }
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(data_out, f, ensure_ascii=False, indent=2)
        except Exception:
            raise
        return aggiunti, aggiornati

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
