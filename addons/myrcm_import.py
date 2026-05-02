"""
TrackMind - MyRCM Import Module v1.0
Scarica i tempi di gara da MyRCM (rc-timing.ch) e li salva
in formato compatibile con il modulo CRONO di TrackMind.

MyRCM non ha API pubblica, si fa scraping delle pagine report HTML.
Solo libreria standard Python 3.x (urllib + html.parser).

Uso da codice:
    from myrcm_import import cerca_eventi_per_pista, scarica_tempi_evento
"""

import json
import os
import re
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser


MYRCM_BASE = "https://www.myrcm.ch/myrcm"
USER_AGENT = "TrackMind/1.0 (MyRCM Import)"


# =====================================================================
#  HTTP helper
# =====================================================================
def _http_get(url, timeout=20):
    """GET HTTP con headers browser-like. Ritorna testo HTML o None."""
    req = Request(url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "text/html,application/xhtml+xml,*/*")
    req.add_header("Accept-Language", "it,en;q=0.5")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, Exception) as e:
        print("[MyRCM] Errore HTTP: %s" % e)
        return None


# =====================================================================
#  Cache locale eventi (pista, data) -> event_id
# =====================================================================
# Gli eventi MyRCM ONLINE (live timing) spariscono dal listing
# pubblico appena finiscono. Pero' i report restano archiviati per
# sempre se conosci l'event_id. Per non perdere accesso a eventi
# passati registriamo (pista_normalizzata, data) -> event_id in una
# cache locale 'dati/myrcm_eventi.json', alimentata sia
# dall'Assistente Gara (al primo APRI EVENTO) sia dai file scouting
# MyRCM gia' presenti in dati/scouting/. Cosi' il bottone RICERCA in
# NUOVA LETTURA puo' riscaricare anche eventi finiti settimane fa.

def _path_cache_eventi(scouting_dir):
    """Path della cache eventi (dati/myrcm_eventi.json).
    `scouting_dir` e' di solito '<dati>/scouting' - risaliamo al
    parent per stare in 'dati/' come gli altri file di mappatura
    (piloti.json, trasponder.json, wifi.json)."""
    if not scouting_dir:
        return None
    parent = os.path.dirname(scouting_dir.rstrip(os.sep))
    if not parent:
        return None
    return os.path.join(parent, "myrcm_eventi.json")


def _norm_pista(nome):
    """Normalizza nome pista per uso come chiave: minuscolo,
    senza spazi/punteggiatura. Cosi' 'Mycandy Arena' e
    'mycandy arena' e 'MYCANDY ARENA' collidono sulla stessa
    chiave (= match piu' tollerante)."""
    return re.sub(r"\W+", "", (nome or "").lower())


def _carica_cache_eventi(scouting_dir):
    """Carica cache da disco. Ritorna dict (pu vuoto)."""
    p = _path_cache_eventi(scouting_dir)
    if not p or not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _salva_cache_eventi(scouting_dir, cache):
    """Scrive cache su disco (atomico via rename non garantito,
    ma su uConsole/Linux il filesystem e' affidabile)."""
    p = _path_cache_eventi(scouting_dir)
    if not p:
        return
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[MyRCM] Errore salvataggio cache eventi: %s" % e)


def _bootstrap_cache_da_scouting(scouting_dir):
    """One-shot: scansiona i file lap_myrcm_*.json gia' presenti
    in scouting/ e popola la cache con (pista, data) -> event_id.
    Idempotente: rispetta entry gia' presenti (cosi' valori
    aggiornati a mano dall'utente non vengono sovrascritti)."""
    cache = _carica_cache_eventi(scouting_dir)
    if not scouting_dir or not os.path.isdir(scouting_dir):
        return cache
    nuove = 0
    try:
        for f in os.listdir(scouting_dir):
            if not f.startswith("lap_myrcm_") or not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(scouting_dir, f),
                          "r", encoding="utf-8") as fh:
                    d = json.load(fh)
            except Exception:
                continue
            ev_id = str(d.get("myrcm_event", "")).strip()
            data = (d.get("data", "") or "").strip()
            setup = d.get("setup", "") or ""
            pista = ""
            if " - " in setup:
                pista = setup.split(" - ", 1)[1].strip()
            if not (ev_id and data and pista):
                continue
            key = "%s|%s" % (_norm_pista(pista), data)
            if key not in cache:
                cache[key] = {
                    "event_id": ev_id,
                    "nome_pista": pista,
                    "nome_evento": "",
                    "fonte": "scouting",
                }
                nuove += 1
    except Exception:
        pass
    if nuove > 0:
        _salva_cache_eventi(scouting_dir, cache)
        print("[MyRCM] Cache eventi: aggiunte %d entry da archivio "
              "scouting" % nuove)
    return cache


def registra_evento_in_cache(scouting_dir, pista, data, event_id,
                              nome_evento="", fonte="import"):
    """Da chiamare quando si apre con successo un evento MyRCM
    (Assistente Gara o RICERCA), per memorizzare (pista, data) ->
    event_id. Idempotente: aggiorna l'entry esistente.

    `fonte`:
      - "import"  : utente l'ha aperto in Assistente Gara (TRUSTED)
      - "scouting": derivato da file scouting esistenti (TRUSTED)
      - "online"  : ricavato da cerca_eventi_online (TRANSIENT,
                    ignorato in lookup successivi perche' l'evento
                    LIVE puo' cambiare nome durante la giornata)"""
    if not (scouting_dir and pista and data and event_id):
        return
    cache = _carica_cache_eventi(scouting_dir)
    key = "%s|%s" % (_norm_pista(pista), data)
    # NON sovrascrivere un'entry trusted ("import"/"scouting") con
    # una "online": se ho gia' un'entry confermata da Assistente
    # Gara per (pista, data), non vogliamo che il lookup online la
    # peggiori.
    existing = cache.get(key)
    if (existing and fonte == "online"
            and existing.get("fonte") in ("import", "scouting")):
        return
    cache[key] = {
        "event_id": str(event_id),
        "nome_pista": pista,
        "nome_evento": nome_evento or "",
        "fonte": fonte,
    }
    _salva_cache_eventi(scouting_dir, cache)


def lookup_evento_da_cache(scouting_dir, pista, data):
    """Cerca (pista, data) nella cache. Ritorna (event_id, nome) o
    (None, None). Bootstrap della cache da scouting/ al primo
    accesso (zero costi se gia' popolata).

    Lookup in 2 step:
      1) match esatto su pista normalizzata (chiave canonica)
      2) match fuzzy: stessa data, almeno una parola chiave del nome
         pista digitato presente nel nome_evento o nome_pista in cache.
         Cosi' "Cardans Cremona" digitato in NUOVA LETTURA matcha
         un'entry di cache "Cardans Cremona Trofeo PEPS Tappa 4"
         registrata da Assistente Gara.
    """
    cache = _bootstrap_cache_da_scouting(scouting_dir)
    if not cache:
        return None, None
    # Filtro: ignora entry "online" (transient, possibile match
    # sbagliato dalla cerca_eventi_online che cambia nome durante
    # la giornata). Solo "import" (Assistente Gara) e "scouting"
    # (derivate da file scouting) sono affidabili.
    cache = {k: v for k, v in cache.items()
             if v.get("fonte") in (None, "import", "scouting")}
    if not cache:
        return None, None
    # 1) Match esatto
    key = "%s|%s" % (_norm_pista(pista), data)
    e = cache.get(key)
    if e:
        return e.get("event_id"), (e.get("nome_evento")
                                    or e.get("nome_pista"))
    # 2) Match fuzzy: stessa data, e match per substring sui nomi
    # normalizzati (cosi' "Mycandy Arena" matcha entry "MycandyArena"
    # e anche viceversa "mycandy" matcha "MycandyArena"), oppure
    # almeno una parola chiave in comune (per nomi multi-parola tipo
    # "Cardans Cremona Trofeo PEPS").
    pista_norm = _norm_pista(pista or "")
    pista_keys = set(_estrai_parole_chiave(pista or ""))
    if not pista_norm and not pista_keys:
        return None, None
    suff = "|" + data
    for k, e in cache.items():
        if not k.endswith(suff):
            continue
        cand_pista = e.get("nome_pista", "") or ""
        cand_evento = e.get("nome_evento", "") or ""
        cand_norm_pista = _norm_pista(cand_pista)
        cand_norm_evento = _norm_pista(cand_evento)
        # Substring match (entrambe le direzioni, min 4 char per
        # evitare falsi positivi su prefissi corti)
        if len(pista_norm) >= 4:
            if (pista_norm in cand_norm_pista
                    or pista_norm in cand_norm_evento
                    or (cand_norm_pista
                        and cand_norm_pista in pista_norm)):
                return e.get("event_id"), (cand_evento or cand_pista)
        # Parole chiave in comune (per nomi multi-parola)
        if pista_keys:
            cand_keys = set(_estrai_parole_chiave(
                cand_pista + " " + cand_evento))
            if pista_keys & cand_keys:
                return e.get("event_id"), (cand_evento or cand_pista)
    return None, None


# =====================================================================
#  Parser HTML per tabelle MyRCM
# =====================================================================
class _TableParser(HTMLParser):
    """Estrae tutte le tabelle HTML come lista di righe (lista di celle)."""
    def __init__(self):
        super().__init__()
        self.tables = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_table = []
        self._current_row = []
        self._current_cell = ""

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._current_row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._current_cell = ""
        elif tag == "br" and self._in_cell:
            self._current_cell += " "

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._current_row.append(self._current_cell.strip())
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._current_row:
                self._current_table.append(self._current_row)
        elif tag == "table" and self._in_table:
            self._in_table = False
            if self._current_table:
                self.tables.append(self._current_table)

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell += data


class _EventListParser(HTMLParser):
    """Estrae eventi dalla pagina 'Eventi Online' di MyRCM."""
    def __init__(self):
        super().__init__()
        self.eventi = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_row = []
        self._current_cell = ""
        self._cell_link = ""

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "table":
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._current_row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._current_cell = ""
            self._cell_link = ""
        elif tag == "a" and self._in_cell:
            href = attrs_d.get("href", "")
            self._cell_link = href

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._current_row.append({
                "text": self._current_cell.strip(),
                "link": self._cell_link
            })
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._current_row:
                self._current_row_copy = list(self._current_row)
                self._current_row = []
                # Cerca pattern: # | Organizzatore | Evento | Nazione | C'e' | A | Rapporti
                if len(self._current_row_copy) >= 6:
                    self.eventi.append(self._current_row_copy)
        elif tag == "table":
            self._in_table = False

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell += data


# =====================================================================
#  Ricerca eventi
# =====================================================================
def _normalizza_data(data_str):
    """Converte DD/MM/YYYY o DD.MM.YYYY in formato comparabile DD.MM.YYYY."""
    ds = data_str.strip()
    if "/" in ds:
        parti = ds.split("/")
        return "%02d.%02d.%04d" % (int(parti[0]), int(parti[1]), int(parti[2]))
    return ds


# Parole generiche da ignorare nella ricerca pista su MyRCM
_PAROLE_STOP = {
    "pista", "circuito", "autodromo", "tracciato", "track", "racing",
    "di", "del", "della", "delle", "dei", "degli", "il", "la", "le",
    "lo", "i", "gli", "un", "uno", "una", "e", "a", "da", "in", "con",
    "su", "per", "tra", "fra", "rc", "mini", "modellismo",
}


def _estrai_parole_chiave(nome_pista):
    """Estrae le parole significative dal nome pista.
    Es: 'PISTA DI LENO' -> ['leno']
        'Circuito del Lario - Lecco' -> ['lario', 'lecco']
        'MiniAutodromo Leno' -> ['leno']
    """
    # Pulisci: rimuovi punteggiatura, split su spazi e trattini
    pulito = nome_pista.lower().strip()
    pulito = re.sub(r'[^\w\s]', ' ', pulito)
    parole = pulito.split()
    # Filtra parole stop e parole troppo corte (< 3 lettere)
    chiave = [p for p in parole if p not in _PAROLE_STOP and len(p) >= 3]
    return chiave


def lista_eventi_online_completa(filtro_nazione=""):
    """Ritorna TUTTI gli eventi attualmente "online" su MyRCM, senza
    filtro per pista. Usato dall'addon Assistente Gara per mostrare la
    lista da cui l'utente sceglie il proprio evento.

    Param `filtro_nazione`: se valorizzato (es. "ITA"), filtra solo gli
    eventi di quella nazione. Match insensibile case + presenza
    sottostringa (cosi' "ita" matcha "Italia").

    Ritorna lista di dict:
        [{event_id, nome, organizzatore, nazione, link}, ...]
    """
    url = "%s/main?hId[1]=evt&pLa=it" % MYRCM_BASE
    html = _http_get(url)
    if not html:
        return []

    parser = _EventListParser()
    parser.feed(html)

    risultati = []
    naz_filter = (filtro_nazione or "").strip().lower()

    for row in parser.eventi:
        # Riga: [#, Organizzatore, Evento, Nazione, C'e', A, Rapporti, streaming]
        if len(row) < 7:
            continue
        try:
            organizzatore = (row[1]["text"] if isinstance(row[1], dict)
                             else str(row[1]))
            evento_text = (row[2]["text"] if isinstance(row[2], dict)
                           else str(row[2]))
            nazione = (row[3]["text"] if isinstance(row[3], dict)
                       else str(row[3]))
            rapporti = row[6] if len(row) > 6 else {}
        except (IndexError, KeyError):
            continue

        # Filtro nazione (opzionale)
        if naz_filter and naz_filter not in nazione.lower():
            continue

        # Estrai event ID dal link rapporti
        link = rapporti.get("link", "") if isinstance(rapporti, dict) else ""
        event_id = None
        m = re.search(r'dId\[E\]=(\d+)', link)
        if m:
            event_id = m.group(1)
        if not event_id:
            m = re.search(r'dId%5BE%5D=(\d+)', link)
            if m:
                event_id = m.group(1)
        if not event_id:
            continue

        risultati.append({
            "event_id": event_id,
            "nome": evento_text.strip(),
            "organizzatore": organizzatore.strip(),
            "nazione": nazione.strip(),
            "link": link,
        })

    return risultati


def scarica_html_evento(event_id):
    """Scarica l'HTML grezzo della pagina evento. Usato dal parser
    time table dell'Assistente Gara (parsing fatto lato addon perche'
    puo' variare da evento a evento)."""
    url = "%s/main?pLa=it&dId[E]=%s" % (MYRCM_BASE, event_id)
    return _http_get(url)


def scarica_suddivisione_batteria(event_id, category_id, report_key=101):
    """Scarica la suddivisione batterie (manche) di una categoria
    per una fase specifica. Le fasi standard MyRCM sono:
        101 = Prove Libere
        102 = Prove
        103 = Qualif
        104 = Finale (suddivisione in Final A / Final B / ecc.)

    La pagina HTML ha questa struttura per ogni manche:
        <p id="title">Manche N</p>
        <table>
            <tr><th>#</th><th>Nr.</th><th>Pilota</th> ...</tr>
            <tr><td>1</td><td/><td>Mlivic Denis</td> ...</tr>
            ...
        </table>

    Ritorna lista di dict, una per manche:
        [{manche: "Manche 1",
          piloti: [{nome, transponder, club, ...}, ...]},
         {manche: "Manche 2",
          piloti: [...]}]
    Se non trova nulla ritorna [].
    """
    url = "%s/report/it/%s/%s?reportKey=%d" % (
        MYRCM_BASE, event_id, category_id, report_key)
    html = _http_get(url)
    if not html:
        return []
    # Spezza l'HTML sui marker <p id="title">Manche N</p>: ogni
    # blocco contiene UNA tabella, dal titolo Manche al successivo.
    pattern = re.compile(
        r'<p\s+id="title"[^>]*>([^<]+)</p>\s*(<table[^>]*>.*?</table>)',
        re.IGNORECASE | re.DOTALL)
    risultati = []
    for m in pattern.finditer(html):
        manche_label = (m.group(1) or "").strip()
        table_html = m.group(2)
        # Parsa la singola tabella
        parser = _TableParser()
        try:
            parser.feed(table_html)
        except Exception:
            continue
        if not parser.tables:
            continue
        table = parser.tables[0]
        if len(table) < 2:
            continue
        header = [(c or "").strip().lower() for c in table[0]]

        def _idx(targets):
            for i, h in enumerate(header):
                if any(t in h for t in targets):
                    return i
            return None

        i_pil = _idx(("pilota", "driver", "name"))
        i_naz = _idx(("stato", "nat", "country"))
        i_club = _idx(("club", "team"))
        i_tr = _idx(("transp", "chip"))
        i_nr = _idx(("nr", "n.", "num"))

        def _cella(row, idx):
            if idx is None or idx >= len(row):
                return ""
            return (row[idx] or "").replace("\xa0", " ").strip()

        piloti = []
        for row in table[1:]:
            if not row:
                continue
            nome = _cella(row, i_pil)
            if not nome:
                continue
            tr_raw = _cella(row, i_tr)
            tr = tr_raw.split("/")[0].strip() if tr_raw else ""
            piloti.append({
                "nome": nome,
                "transponder": tr,
                "transponder_raw": tr_raw,
                "numero_gara": _cella(row, i_nr),
                "nazione": _cella(row, i_naz),
                "club": _cella(row, i_club),
            })
        if piloti:
            risultati.append({
                "manche": manche_label,
                "piloti": piloti,
            })
    return risultati


# Mappa fasi standard MyRCM -> reportKey suddivisione batteria
SUDDIVISIONE_REPORT_KEYS = {
    "prove_libere": 101,
    "prove": 102,
    "qualif": 103,
    "finale": 104,
}


def trova_manche_pilota_per_fase(event_id, category_id, nome_pilota):
    """Cerca a quale Manche e' assegnato il pilota per ogni fase
    (Prove Libere, Prove, Qualif, Finale) di una categoria.

    Match nome: case-insensitive, normalizza spazi, accetta sia
    "Cognome Nome" che "Nome Cognome" (MyRCM usa "Cognome Nome").

    Ritorna dict {fase_key: manche_label}, es:
        {"prove_libere": "Manche 1",
         "prove": "Manche 1",
         "qualif": "Manche 1",
         "finale": "Final A"}
    Se in una fase il pilota non c'e' (non ancora pubblicata o
    eliminato), quella chiave manca dal dict. Se nome_pilota e'
    vuoto o None, ritorna {} (nessun filtro)."""
    if not nome_pilota:
        return {}
    target = re.sub(r'\s+', ' ', str(nome_pilota or "")).strip().lower()
    if not target:
        return {}
    # Estrai le parti del nome (cognome, nome, ecc.)
    parti_target = set(target.split())

    risultato = {}
    for fase_key, rk in SUDDIVISIONE_REPORT_KEYS.items():
        try:
            manches = scarica_suddivisione_batteria(
                event_id, category_id, report_key=rk)
        except Exception:
            continue
        for m in manches:
            for p in m.get("piloti", []):
                pn = re.sub(r'\s+', ' ',
                             p.get("nome", "")).strip().lower()
                if not pn:
                    continue
                # Match esatto, oppure tutte le parti del nome target
                # sono presenti nel nome pilota (gestisce inversioni
                # tipo "Marco Modolo" vs "Modolo Marco")
                if pn == target or parti_target.issubset(set(pn.split())):
                    risultato[fase_key] = m.get("manche", "?")
                    break
            if fase_key in risultato:
                break
    return risultato


def scarica_partecipanti(event_id, category_id):
    """Scarica la lista partecipanti di una categoria MyRCM dal
    reportKey=100. Ritorna lista di dict:
        [{nome, transponder, club, modello, motore, gomme,
          radio, batteria, nazione, numero_gara}, ...]
    Il transponder e' restituito senza il suffisso "/N" che MyRCM
    aggiunge per la versione (es. "1053911/0" -> "1053911"). Lo
    spazio non-breaking \\xa0 nei nomi viene normalizzato a spazio
    normale. Se la pagina non e' parsabile ritorna []."""
    url = "%s/report/it/%s/%s?reportKey=100" % (
        MYRCM_BASE, event_id, category_id)
    html = _http_get(url)
    if not html:
        return []
    parser = _TableParser()
    try:
        parser.feed(html)
    except Exception:
        return []

    # Cerca la tabella con header "Pilota" e "Transponder"
    target = None
    for table in parser.tables:
        if not table or len(table) < 2:
            continue
        header = [(c or "").strip().lower() for c in table[0]]
        if any("pilota" in h for h in header) and \
           any("transp" in h for h in header):
            target = table
            break
    if not target:
        return []

    header = [(c or "").strip().lower() for c in target[0]]

    def _idx(targets):
        for i, h in enumerate(header):
            if any(t in h for t in targets):
                return i
        return None

    i_nr = _idx(("nr", "n.", "num"))
    i_pil = _idx(("pilota", "driver", "name"))
    i_naz = _idx(("stato", "nat", "country"))
    i_club = _idx(("club", "team"))
    i_tr = _idx(("transp", "chip"))
    i_mod = _idx(("modello", "model", "car", "chass"))
    i_mot = _idx(("motore", "engine", "motor"))
    i_gom = _idx(("gomme", "tyre", "tires"))
    i_rad = _idx(("radio", "remote"))
    i_bat = _idx(("battery", "batteria"))

    def _cella(row, idx):
        if idx is None or idx >= len(row):
            return ""
        return (row[idx] or "").replace("\xa0", " ").strip()

    risultati = []
    for row in target[1:]:
        if not row:
            continue
        nome = _cella(row, i_pil)
        tr_raw = _cella(row, i_tr)
        if not nome or not tr_raw:
            continue
        # Estrai numero trasponder pulito: "1053911/0" -> "1053911"
        tr = tr_raw.split("/")[0].strip()
        if not tr:
            continue
        risultati.append({
            "nome": nome,
            "transponder": tr,
            "transponder_raw": tr_raw,
            "numero_gara": _cella(row, i_nr),
            "nazione": _cella(row, i_naz),
            "club": _cella(row, i_club),
            "modello": _cella(row, i_mod),
            "motore": _cella(row, i_mot),
            "gomme": _cella(row, i_gom),
            "radio": _cella(row, i_rad),
            "batteria": _cella(row, i_bat),
        })
    return risultati


def cerca_eventi_online(nome_pista):
    """Cerca eventi online su MyRCM che corrispondono a un nome pista.
    Ricerca intelligente: estrae parole chiave dal nome pista
    (es. 'PISTA DI LENO' cerca 'leno' negli eventi).
    Ritorna lista di dict: [{event_id, nome, nazione, link}, ...]
    """
    url = "%s/main?hId[1]=evt&pLa=it" % MYRCM_BASE
    html = _http_get(url)
    if not html:
        return []

    parser = _EventListParser()
    parser.feed(html)

    risultati = []

    # Estrai parole chiave dalla pista
    parole_chiave = _estrai_parole_chiave(nome_pista)
    if not parole_chiave:
        # Fallback: usa il nome intero
        parole_chiave = [nome_pista.lower().strip()]
    print("[MyRCM] Ricerca eventi per: %s (parole chiave: %s)" % (
        nome_pista, ", ".join(parole_chiave)))

    for row in parser.eventi:
        # Ogni riga: [#, Organizzatore, Evento, Nazione, C'e, A, Rapporti, streaming]
        if len(row) < 7:
            continue
        try:
            evento_text = row[2]["text"] if isinstance(row[2], dict) else str(row[2])
            nazione = row[3]["text"] if isinstance(row[3], dict) else str(row[3])
            rapporti = row[6] if len(row) > 6 else {}
        except (IndexError, KeyError):
            continue

        # Match: TUTTE le parole chiave devono essere presenti come
        # PAROLE INTERE nel testo evento (non substring). Cosi'
        # "Mycandy Arena" non matcha falsamente "Cockpit Kartarena"
        # (dove "arena" e' parte di "kartarena", non parola intera).
        # Il match per substring causava falsi positivi su parole
        # generiche tipo "arena", "cup", "race".
        evento_parole = set(_estrai_parole_chiave(evento_text))
        if not all(p in evento_parole for p in parole_chiave):
            continue
        # Da qui in poi: match valido
        if True:
            # Estrai event ID dal link rapporti
            link = rapporti.get("link", "") if isinstance(rapporti, dict) else ""
            event_id = None
            m = re.search(r'dId\[E\]=(\d+)', link)
            if m:
                event_id = m.group(1)
            if not m:
                m = re.search(r'dId%5BE%5D=(\d+)', link)
                if m:
                    event_id = m.group(1)

            if event_id:
                risultati.append({
                    "event_id": event_id,
                    "nome": evento_text,
                    "nazione": nazione,
                    "link": link,
                })

    return risultati


def cerca_evento_per_data(nome_pista, data_str, scouting_dir=None):
    """Cerca un evento MyRCM che corrisponda a pista e data.
    data_str in formato DD/MM/YYYY.

    Strategia in 2 step:
    1) Cache locale (dati/myrcm_eventi.json + bootstrap da scouting/):
       trova eventi anche se finiti settimane fa, basta che siano gia'
       stati visti almeno una volta (Assistente Gara o RICERCA precedente).
    2) Fallback online: cerca tra gli eventi LIVE attualmente trasmessi.
       Funziona solo durante la gara stessa.

    `scouting_dir` (opzionale): se passato abilita la cache. Se omesso
    mantiene il vecchio comportamento (solo lookup online).

    Ritorna (event_id, nome_evento) o (None, None)."""
    # Step 1: cache locale (eventi gia' visti, anche conclusi)
    if scouting_dir:
        ev_id, ev_nome = lookup_evento_da_cache(
            scouting_dir, nome_pista, data_str)
        if ev_id:
            print("[MyRCM] Evento da cache: %s (ID: %s)" % (
                ev_nome or "?", ev_id))
            return ev_id, (ev_nome or nome_pista)

    # Step 2: fallback su eventi LIVE
    eventi = cerca_eventi_online(nome_pista)
    if not eventi:
        print("[MyRCM] Nessun evento trovato per '%s'" % nome_pista)
        return None, None

    # Per ora ritorna il primo evento trovato (gli eventi online sono quelli attivi)
    # In futuro si puo' filtrare per data
    if eventi:
        ev = eventi[0]
        print("[MyRCM] Evento trovato online: %s (ID: %s)" % (
            ev["nome"], ev["event_id"]))
        # Registra in cache come "online" (transient) cosi' al
        # prossimo lookup non viene riusata: gli eventi LIVE
        # possono cambiare nome durante la giornata e una vecchia
        # entry sbagliata bloccherebbe il refresh CRONO. Solo le
        # entry "import" (Assistente Gara) e "scouting" sono
        # trustate dal lookup.
        if scouting_dir:
            try:
                registra_evento_in_cache(scouting_dir, nome_pista,
                                          data_str, ev["event_id"],
                                          ev["nome"],
                                          fonte="online")
            except Exception:
                pass
        return ev["event_id"], ev["nome"]

    return None, None


# =====================================================================
#  Scarica categorie di un evento
# =====================================================================
def scarica_categorie(event_id):
    """Scarica le categorie di un evento. Ritorna lista:
    [{category_id, nome}, ...]"""
    url = "%s/main?pLa=it&dId[E]=%s" % (MYRCM_BASE, event_id)
    html = _http_get(url)
    if not html:
        return []

    categorie = []
    # Cerca pattern: openNewWindows(eventId, categoryId)
    for m in re.finditer(r'openNewWindows\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', html):
        eid, cid = m.group(1), m.group(2)
        if eid == event_id:
            # Cerca il testo del link vicino
            # Prende il testo tra > e </a> piu' vicino
            pos = m.start()
            chunk = html[max(0, pos - 200):pos + 100]
            nome_m = re.search(r'>([^<]+)</a>', chunk)
            nome = nome_m.group(1).strip() if nome_m else "Cat_%s" % cid
            categorie.append({"category_id": cid, "nome": nome})

    return categorie


# =====================================================================
#  Scarica info evento (titolo + pista vera dal Block HTML)
# =====================================================================
def scarica_info_evento(event_id):
    """Scarica le info principali dell'evento dalla pagina HTML.
    Ritorna dict {titolo, pista_block, nazione} o {} se errore.

    Il campo 'Block' nell'HTML MyRCM contiene il NOME DELLA PISTA
    vero (es. 'MycandyArena'), che NON sempre coincide col titolo
    dell'evento (es. 'Campionato Costruttori 1/8 On-Road 2026').
    Cosi' la cache puo' essere registrata con la pista reale e il
    lookup da NUOVA LETTURA funziona anche per nomi di gara astratti.
    """
    url = "%s/main?pLa=it&dId[E]=%s" % (MYRCM_BASE, event_id)
    html = _http_get(url)
    if not html:
        return {}
    info = {"titolo": "", "pista_block": "", "nazione": ""}
    # <title>MyRCM : Eventi Online : <NOME EVENTO></title>
    m = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    if m:
        t = m.group(1).strip()
        if "::" in t or ":" in t:
            # Tipico: "MyRCM : Eventi Online : Campionato ..."
            parts = re.split(r'\s*:\s*', t)
            info["titolo"] = parts[-1].strip() if parts else t
        else:
            info["titolo"] = t
    # <span class="label">Block:</span> <span class="value">MycandyArena</span>
    m = re.search(
        r'class="label">\s*Block\s*:?\s*</span>\s*'
        r'<span\s+class="value">\s*([^<]+?)\s*</span>',
        html, re.IGNORECASE | re.DOTALL)
    if m:
        info["pista_block"] = m.group(1).strip()
    # Nazione (best-effort, stesso pattern)
    m = re.search(
        r'class="label">\s*Nazione\s*:?\s*</span>\s*'
        r'<span\s+class="value">\s*([^<]+?)\s*</span>',
        html, re.IGNORECASE | re.DOTALL)
    if m:
        info["nazione"] = m.group(1).strip()
    return info


# =====================================================================
#  Scarica tempi giro di una sessione dal report
# =====================================================================
def scarica_tempi_report(event_id, category_id, sessione_tipo="Qualif", manche=None, qualif=None):
    """Scarica tutti i tempi giro da una pagina report MyRCM.
    Ritorna lista di dict per ogni batteria/manche trovata:
    [{
        "titolo": "Qualif :: Manche 10 - Qualif 1",
        "info": "1/8_IC_TRACK_EFRA [EFRA-1/8T] - Orario gara: 4:00...",
        "classifica": [{pos, nr, pilota, giri, tempo_finale, miglior_tempo, tempo_medio}, ...],
        "tempi_giro": {pilota: [tempo1, tempo2, ...], ...}
    }, ...]
    """
    url = "%s/report/it/%s/%s" % (MYRCM_BASE, event_id, category_id)
    html = _http_get(url)
    if not html:
        return []

    parser = _TableParser()
    parser.feed(html)

    risultati = []

    # Le tabelle nel report MyRCM sono a coppie:
    # 1. Tabella classifica (Pos, Nr, Pilota Nr, Pilota, I, Giri, Tempo finale, ...)
    # 2. Tabella tempi giro (#Giri, Pilota1, Pilota2, ...)
    i = 0
    while i < len(parser.tables):
        table = parser.tables[i]

        # Cerca tabella classifica (header con "Pos" e "Pilota")
        if len(table) > 1 and len(table[0]) >= 6:
            header = [c.lower() for c in table[0]]
            if "pos" in header and "pilota" in header:
                classifica = []
                for row in table[1:]:
                    if len(row) >= 6:
                        entry = {
                            "pos": row[0],
                            "nr": row[1] if len(row) > 1 else "",
                            "pilota_nr": row[2] if len(row) > 2 else "",
                            "pilota": row[3] if len(row) > 3 else "",
                            "giri": row[5] if len(row) > 5 else "",
                            "tempo_finale": row[6] if len(row) > 6 else "",
                            "miglior_tempo": row[7] if len(row) > 7 else "",
                            "tempo_medio": row[8] if len(row) > 8 else "",
                        }
                        classifica.append(entry)

                # La tabella tempi giro segue subito dopo
                tempi_giro = {}
                if i + 1 < len(parser.tables):
                    lap_table = parser.tables[i + 1]
                    if len(lap_table) > 1 and lap_table[0][0].lower().startswith("#giri"):
                        piloti_header = lap_table[0][1:]
                        # Risolvi numeri di gara ('# 11') -> nome pilota
                        # usando la classifica appena letta (vedi
                        # _risolvi_nome_da_header e v05.05.71).
                        nr_to_nome = {}
                        for entry in classifica:
                            nr = str(entry.get("nr", "") or "").strip()
                            nome = str(
                                entry.get("pilota", "") or "").strip()
                            if nr and nome:
                                nr_to_nome[nr] = nome
                        piloti_header = [
                            _risolvi_nome_da_header(p, nr_to_nome)
                            for p in piloti_header
                        ]
                        for row in lap_table[1:]:
                            giro_n = row[0] if row else ""
                            if giro_n == "0":
                                continue  # Ignora giro 0 (partenza)
                            for pi, pilota_nome in enumerate(piloti_header):
                                if not pilota_nome:
                                    continue
                                if pi + 1 < len(row):
                                    try:
                                        t = float(row[pi + 1])
                                        if t > 0:
                                            if pilota_nome not in tempi_giro:
                                                tempi_giro[pilota_nome] = []
                                            tempi_giro[pilota_nome].append(t)
                                    except (ValueError, TypeError):
                                        pass
                        i += 1  # Salta tabella tempi (gia' processata)

                risultati.append({
                    "classifica": classifica,
                    "tempi_giro": tempi_giro,
                })
        i += 1

    return risultati


def _estrai_report_keys(html, event_id, category_id):
    """Estrae tutte le URL con reportKey dalla pagina report principale.
    Filtra solo manche con tempi reali (Prove, Qualif, Finale con giri).
    Ritorna lista di (url_completa, titolo_sessione)."""
    matches = re.findall(
        r"doAjaxCall\s*\(\s*'([^']+)'\s*,\s*'([^']*)'\s*\)", html)

    risultati = []
    visti = set()
    for url_path, target in matches:
        rk_m = re.search(r'reportKey=(\d+)', url_path)
        if not rk_m:
            continue
        rk = rk_m.group(1)

        # Salta placeholder vuoti (reportKey=1000) e riassunti (100-499)
        if rk == "1000":
            continue
        try:
            rk_int = int(rk)
            if rk_int < 500:
                continue
        except ValueError:
            continue

        # Salta se gia' visto
        if rk in visti:
            continue

        # Prendi solo sessioni con tempi giro: Prove, Qualif, Finale (manche)
        # Escludi "Prove Libere" (quelle sono su SpeedHive)
        target_low = target.lower()
        ha_tempi = False
        if "qualif" in target_low and "manche" in target_low:
            ha_tempi = True
        elif "finale" in target_low and ("finals" in target_low or "gruppo" in target_low):
            ha_tempi = True
        elif "prove ::" in target_low and "manche" in target_low:
            # Prove controllate (non prove libere)
            if "prove libere" not in target_low:
                ha_tempi = True

        if ha_tempi:
            visti.add(rk)
            full_url = "https://www.myrcm.ch%s" % url_path
            risultati.append((full_url, target))

    return risultati


def scarica_tutti_tempi_evento(event_id, category_id):
    """Scarica TUTTI i tempi (tutte le manche/qualif) di un evento.
    Approccio a 2 passaggi:
    1. Fetch pagina report principale -> estrai tutti i reportKey AJAX
    2. Fetch ogni reportKey individualmente -> parse tabelle HTML
    Ritorna lista di batterie con classifica e tempi giro."""

    # Passo 1: pagina report principale
    url_main = "%s/report/it/%s/%s" % (MYRCM_BASE, event_id, category_id)
    html_main = _http_get(url_main)
    if not html_main:
        return []

    # Passo 2: estrai tutti i reportKey validi
    report_keys = _estrai_report_keys(html_main, event_id, category_id)
    if not report_keys:
        print("[MyRCM] Nessun reportKey con tempi trovato per %s/%s" % (
            event_id, category_id))
        return []

    print("[MyRCM] Trovati %d report con tempi da scaricare" % len(report_keys))

    # Passo 3: fetch ogni reportKey e parse tabelle
    tutti_risultati = []
    for ajax_url, titolo in report_keys:
        html_frag = _http_get(ajax_url, timeout=15)
        if not html_frag:
            continue

        parser = _TableParser()
        parser.feed(html_frag)

        # Processa tabelle come in scarica_tempi_report
        i = 0
        while i < len(parser.tables):
            table = parser.tables[i]
            if len(table) > 1 and len(table[0]) >= 5:
                header = [c.lower().strip() for c in table[0]]
                if "pos" in header and "pilota" in header:
                    classifica = []
                    for row in table[1:]:
                        if len(row) >= 5:
                            entry = {
                                "pos": row[0],
                                "nr": row[1] if len(row) > 1 else "",
                                "pilota_nr": row[2] if len(row) > 2 else "",
                                "pilota": row[3].replace('\xa0', ' ') if len(row) > 3 else "",
                                "giri": row[5] if len(row) > 5 else "",
                                "tempo_finale": row[6] if len(row) > 6 else "",
                                "miglior_tempo": row[7] if len(row) > 7 else "",
                                "tempo_medio": row[8] if len(row) > 8 else "",
                            }
                            classifica.append(entry)

                    tempi_giro = {}
                    if i + 1 < len(parser.tables):
                        lap_table = parser.tables[i + 1]
                        if (len(lap_table) > 1 and
                                lap_table[0][0].lower().startswith("#giri")):
                            piloti_header = [
                                p.replace('\xa0', ' ')
                                for p in lap_table[0][1:]
                            ]
                            # Mappa "nr di gara" -> "nome pilota" usando
                            # la classifica della stessa batteria.
                            # MyRCM mette nell'header dei tempi solo
                            # "# 11", "# 1", ... (numeri di gara) - serve
                            # risalire al nome usando la classifica.
                            nr_to_nome = {}
                            for entry in classifica:
                                nr = str(entry.get("nr", "") or "").strip()
                                nome = str(
                                    entry.get("pilota", "") or "").strip()
                                if nr and nome:
                                    nr_to_nome[nr] = nome
                            piloti_header = [
                                _risolvi_nome_da_header(p, nr_to_nome)
                                for p in piloti_header
                            ]
                            for row in lap_table[1:]:
                                giro_n = row[0] if row else ""
                                if giro_n == "0":
                                    continue
                                for pi, pilota_nome in enumerate(piloti_header):
                                    if not pilota_nome:
                                        continue
                                    if pi + 1 < len(row):
                                        try:
                                            t = float(row[pi + 1])
                                            if t > 0:
                                                if pilota_nome not in tempi_giro:
                                                    tempi_giro[pilota_nome] = []
                                                tempi_giro[pilota_nome].append(t)
                                        except (ValueError, TypeError):
                                            pass
                            i += 1

                    if tempi_giro:
                        tutti_risultati.append({
                            "titolo": titolo,
                            "classifica": classifica,
                            "tempi_giro": tempi_giro,
                        })
            i += 1

    print("[MyRCM] Totale batterie con tempi: %d" % len(tutti_risultati))
    return tutti_risultati


def _risolvi_nome_da_header(header_label, nr_to_nome):
    """Header dei tempi giro su MyRCM e' del tipo '# 11', '# 1' ecc.
    (numero di gara). Risolve il nome del pilota cercando il numero
    nella mappa fornita. Se non trovato (header gia' col nome o caso
    ambiguo), ritorna l'header originale."""
    h = (header_label or "").strip()
    if not h:
        return ""
    # Pattern '# 11' o '#11' (con o senza spazio dopo cancelletto)
    m = re.match(r'^#\s*(\d+)\s*$', h)
    if m:
        nr = m.group(1)
        if nr in nr_to_nome:
            return nr_to_nome[nr]
        # Nr non in classifica: usa "Pilota_NR" come fallback
        # leggibile invece di lasciare "# 11"
        return "Pilota %s" % nr
    return h


# =====================================================================
#  Crea file scouting JSON compatibile con TrackMind
# =====================================================================
def crea_scouting_json(pilota, tempi_giro, data_str, ora_str,
                       pista_nome, event_id, category_nome,
                       session_idx=1, transponder="",
                       classifica_entry=None,
                       sessione_nome=""):
    """Crea un dict scouting compatibile con TrackMind da tempi MyRCM.

    pilota: nome pilota
    tempi_giro: lista di tempi float [14.015, 14.020, ...]
    data_str: "DD/MM/YYYY"
    ora_str: "HH:MM:SS"
    pista_nome: nome pista
    event_id: ID evento MyRCM
    category_nome: nome categoria (es. "1/8_IC_TRACK_EFRA")
    session_idx: indice sessione progressivo
    transponder: codice transponder se noto
    classifica_entry: dict con pos, giri, tempo_finale, etc.
    sessione_nome: nome sessione dal report (es. "Qualif :: Manche 3")
    """
    if not tempi_giro:
        return None

    giri_list = []
    for i, t in enumerate(tempi_giro, 1):
        giri_list.append({
            "giro": i,
            "tempo": round(t, 3),
            "stato": "valido",
        })

    miglior = round(min(tempi_giro), 3)
    media = round(sum(tempi_giro) / len(tempi_giro), 3)
    totale = round(sum(tempi_giro), 3)

    sessione = {
        "pilota": pilota,
        "setup": "MyRCM - %s" % pista_nome,
        "data": data_str,
        "ora": ora_str,
        "tipo": "myrcm",
        "transponder": transponder,
        "serbatoio_cc": 0,
        "sessione_carburante": False,
        "myrcm_event": event_id,
        "myrcm_category": category_nome,
        "myrcm_session": session_idx,
        "myrcm_sessione_nome": sessione_nome,
        "num_giri": len(giri_list),
        "giri": giri_list,
        "miglior_tempo": miglior,
        "media": media,
        "tempo_totale": totale,
    }

    # Aggiungi info classifica se disponibile
    if classifica_entry:
        sessione["myrcm_posizione"] = classifica_entry.get("pos", "")
        sessione["myrcm_tempo_finale"] = classifica_entry.get("tempo_finale", "")
        sessione["myrcm_nr"] = classifica_entry.get("nr", "")

    return sessione


def salva_scouting(sessione, scouting_dir, transponder_suffix="", setup_snapshot=None):
    """Salva una sessione scouting su disco. Ritorna il path salvato."""
    # Fotografia setup al momento dell'import (per analisi IA)
    if setup_snapshot:
        for k, v in setup_snapshot.items():
            if k not in sessione:
                sessione[k] = v
    os.makedirs(scouting_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sid = sessione.get("myrcm_session", 1)
    suffix = transponder_suffix[-6:] if transponder_suffix else "myrcm"
    filename = "lap_myrcm_%s_%s_s%d.json" % (ts, suffix, sid)
    path = os.path.join(scouting_dir, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sessione, f, ensure_ascii=False, indent=2)
        return path
    except Exception as e:
        print("[MyRCM] Errore salvataggio: %s" % e)
        return None


# =====================================================================
#  Match orario sessione MyRCM dal time table
# =====================================================================
# I report MyRCM (pagina /report/it/EID/CID) NON espongono l'orario di
# inizio della sessione - solo i tempi giro. Pero' la "Tabella Oraria"
# dello stesso evento contiene per ogni turno l'orario preciso (es.
# "09:00 - 1/8 GT Cardans BLS - Group 1 - Controlled practice 1").
# Sotto facciamo il match (categoria + manche + fase) tra il titolo
# MyRCM (italiano: "Prove :: Manche 1 - Prove 1") e le righe del time
# table (inglese: "Group 1 / Controlled practice 1") via normalizzatori
# cross-lingua. Cosi' invece di salvare ora="00:00" possiamo riempire
# l'orario corretto della batteria.

def _norm_manche_mr(s):
    """Estrae il numero/lettera di manche da 'Manche 4', 'Group 1',
    'Batteria 2', 'Final A', 'Finals B' (italiano + inglese).
    Ritorna stringa o None."""
    s_low = (s or "").lower().strip()
    if not s_low:
        return None
    m = re.search(r'(?:manche|group|batteria|gruppo)\s*(\d+)', s_low)
    if m:
        return m.group(1)
    # Final/Finals/Finale/Finali con spazio obbligatorio (altrimenti
    # 'finals a' verrebbe matchato come 'final' + 's' = 'S' invece
    # di 'finals' + ' ' + 'a' = 'A').
    m = re.search(r'final(?:e|i|s)?\s+([a-z]|\d+)\b', s_low)
    if m:
        return m.group(1).upper()
    m = re.search(r'^(\d+)$', s_low)
    if m:
        return m.group(1)
    return None


def _norm_fase_mr(s):
    """Estrae fase canonica:
      'P1' = Practice 1 / Prove 1 / Controlled practice 1
      'Q2' = Qualif 2 / Qualification 2
      'PL1' = Prove Libere 1 / Free practice 1
      'FR1' = Final run 1 / Gruppo finale 1 (run vero della finale)
      'TP' = Timed practice / "Prove" della finale (run di cronometraggio
             pre-finale, nel time table appare come 'Timed practice'
             mentre nei titoli MyRCM e' solo 'Prove')
      'FA'/'FB' = etichetta diretta della finale (caso usato come fase
                  quando il titolo non distingue manche/gruppo)
    Cross-lingua IT/EN."""
    s_low = (s or "").lower().strip()
    if not s_low:
        return None
    # Final run N / Gruppo finale N / Finale N (con numero) - fase
    # vera della finale
    m = re.search(
        r'(?:final\s*run|gruppo\s*finale|run\s*finale)\s*(\d+)',
        s_low)
    if m:
        return "FR" + m.group(1)
    # Timed practice / Prove (senza numero, contesto finale)
    if re.search(r'\btimed\s*prac?tice\b', s_low):
        return "TP"
    if re.fullmatch(r'prove', s_low):
        return "TP"
    # Final A/B (etichetta come fase, raro)
    m = re.search(r'final[s]?\s*([a-z])\s*$', s_low)
    if m:
        return "F" + m.group(1).upper()
    # Qualification / Qualif
    m = re.search(r'qualif[a-z]*\s*(\d+)', s_low)
    if m:
        return "Q" + m.group(1)
    # Prove Libere / Free practice
    m = re.search(
        r'(?:prove\s*libere|free\s*prac?tice|p\.?\s*libere)\s*(\d+)',
        s_low)
    if m:
        return "PL" + m.group(1)
    # Controlled practice / Practice / Prove (generico, con numero)
    m = re.search(
        r'(?:controlled\s+|free\s+)?prac?tice\s*(\d+)', s_low)
    if m:
        return "P" + m.group(1)
    m = re.search(r'prove\s*(\d+)', s_low)
    if m:
        return "P" + m.group(1)
    return None


def parse_group_live(group_str):
    """Parse del GROUP MyRCM live (METADATA.GROUP del WebSocket).
    Esempio: 'GT8_SPORT :: Prove :: Batteria 2 - Manche 1'
    Ritorna (categoria_tag, fase, manche) tutti come stringhe.

    Le tre parti:
      - categoria_tag: tag corto categoria (es. "GT8_SPORT")
      - fase: tipo turno (es. "Prove", "Qualif", "Finale")
      - manche: numero/lettera (es. "1", "2", "A")
    """
    if not group_str:
        return None, None, None
    parts = [p.strip() for p in group_str.split("::")]
    cat_tag = parts[0] if len(parts) > 0 else None
    fase = parts[1] if len(parts) > 1 else None
    body = parts[2] if len(parts) > 2 else ""
    # body tipico: "Batteria 2 - Manche 1" o "Manche 4 - Qualif 1"
    # o "Finals A - Gruppo finale 1"
    manche = None
    sub = [p.strip() for p in body.split(" - ")]
    for s in sub:
        m = _norm_manche_mr(s)
        if m:
            manche = m
            break
    return cat_tag, fase, manche


def _match_orario_da_tt(time_table, categoria_nome, titolo_sessione):
    """Cerca nel time table l'orario di una sessione MyRCM.
    titolo_sessione tipico: 'Prove :: Manche 1 - Prove 1',
    'Reports Online :: Manche 4 - Qualif 2', 'Reports Online :: Finals A'.
    Ritorna (ora_str 'HH:MM:SS', base_date) o (None, None)."""
    if not time_table or not titolo_sessione:
        return None, None
    # Estrai parte dopo l'ultimo ' :: '
    parts = titolo_sessione.split(' :: ')
    body = parts[-1].strip() if parts else titolo_sessione
    # Splitto su ' - ' per separare manche dalla fase
    sub = [p.strip() for p in body.split(' - ')]
    if len(sub) >= 2:
        sess_manche, sess_fase = sub[0], sub[-1]
    else:
        sess_manche = sess_fase = body
    # Per le finali "Finals A" non c'e' manche separata
    target_fase = _norm_fase_mr(sess_fase) or _norm_fase_mr(sess_manche)
    target_manche = _norm_manche_mr(sess_manche)
    # Per fase finale, manche (A/B/...) sta dentro la fase stessa
    if target_fase and target_fase.startswith("F") and not target_manche:
        target_manche = target_fase[1:]  # es. FA -> A
    cat_low = (categoria_nome or "").lower().strip()
    if not target_fase:
        return None, None
    for r in time_table:
        # Match categoria: substring (tollera tag tipo "[GT_Nitro]"
        # aggiunto da MyRCM in coda al nome). Niente fallback "parola
        # comune": le categorie nello stesso evento condividono di
        # solito quasi tutto il nome ("1/8 Nitro On-Road Open" vs
        # "1/8 Nitro On-Road +40") e si distinguono solo per l'ultimo
        # termine, quindi un match per parola comune accetterebbe
        # tutto.
        r_cat = (r.get("categoria", "") or "").lower()
        if cat_low and r_cat:
            if cat_low not in r_cat and r_cat not in cat_low:
                continue
        # Match manche
        r_manche = _norm_manche_mr(r.get("manche", ""))
        if target_manche and r_manche and r_manche != target_manche:
            continue
        # Match fase
        r_fase = _norm_fase_mr(r.get("gruppo", ""))
        if target_fase != r_fase:
            continue
        ora = r.get("ora", "")
        if ora and ":" in ora:
            # Normalizza a HH:MM:SS
            if len(ora) == 5:  # HH:MM
                ora = ora + ":00"
            return ora, r.get("base_date")
    return None, None


def _carica_time_table_evento(event_id, category_id):
    """Wrapper che importa scarica_timetable_evento da assistente_gara
    in lazy mode (per evitare dipendenza all'import). Ritorna lista
    righe time table o lista vuota su errore. data_target=None ->
    scarica TUTTE le giornate (servono per gare multi-giorno)."""
    try:
        from assistente_gara import scarica_timetable_evento
        return scarica_timetable_evento(event_id, category_id,
                                         data_target=None) or []
    except Exception as e:
        print("[MyRCM] Errore time table %s/%s: %s" % (
            event_id, category_id, e))
        return []


# =====================================================================
#  Import completo: cerca evento + scarica tutti i tempi
# =====================================================================
def import_evento_completo(nome_pista, data_str, scouting_dir,
                           pilota_filtro=None, setup_snapshot=None):
    """Cerca un evento MyRCM per nome pista, scarica tutte le categorie
    e tutti i tempi giro. Salva come file scouting.

    nome_pista: nome pista da cercare (es. "Leno")
    data_str: "DD/MM/YYYY"
    scouting_dir: cartella dove salvare i JSON
    pilota_filtro: se specificato, scarica solo quel pilota

    Ritorna: (lista_sessioni_salvate, event_nome) o ([], None)
    """
    # 1. Cerca evento (cache locale prima, poi online live)
    event_id, event_nome = cerca_evento_per_data(
        nome_pista, data_str, scouting_dir=scouting_dir)
    if not event_id:
        return [], None

    # 2. Scarica categorie
    categorie = scarica_categorie(event_id)
    if not categorie:
        print("[MyRCM] Nessuna categoria trovata per evento %s" % event_id)
        return [], event_nome

    print("[MyRCM] Trovate %d categorie: %s" % (
        len(categorie),
        ", ".join(c["nome"] for c in categorie)))

    # 3. Per ogni categoria scarica tutti i tempi
    saved = []
    session_counter = 1

    for cat in categorie:
        cid = cat["category_id"]
        cnome = cat["nome"]
        print("[MyRCM] Scarico categoria: %s (ID: %s)" % (cnome, cid))

        batterie = scarica_tutti_tempi_evento(event_id, cid)
        if not batterie:
            continue

        # Scarica il time table dell'evento+categoria UNA SOLA volta
        # per usarlo come fonte degli orari precisi delle batterie. I
        # report MyRCM espongono solo i tempi giro, non l'orario di
        # inizio sessione - per quello c'e' la Tabella Oraria.
        time_table_cat = _carica_time_table_evento(event_id, cid)
        if time_table_cat:
            print("[MyRCM]   time table: %d turni"
                  % len(time_table_cat))

        for batteria in batterie:
            tempi = batteria.get("tempi_giro", {})
            classifica = batteria.get("classifica", [])
            titolo_sessione = batteria.get("titolo", "")

            # Mappa pilota -> classifica entry
            class_map = {}
            for entry in classifica:
                class_map[entry.get("pilota", "")] = entry

            # Cerca orario della batteria nel time table.
            # data_str di default = quello digitato dall'utente; se
            # il time table dice una giornata diversa (gara multi-
            # giorno) la sostituiamo per ogni batteria.
            ora_batt, base_dt = _match_orario_da_tt(
                time_table_cat, cnome, titolo_sessione)
            if ora_batt:
                ora_da_usare = ora_batt
            else:
                ora_da_usare = "00:00:00"  # fallback come prima
            if base_dt is not None:
                data_da_usare = base_dt.strftime("%d/%m/%Y")
            else:
                data_da_usare = data_str

            for pilota, giri in tempi.items():
                if not giri or len(giri) < 2:
                    continue

                # Filtro pilota se specificato
                if pilota_filtro:
                    if pilota_filtro.lower() not in pilota.lower():
                        continue

                cls_entry = class_map.get(pilota)
                transponder = ""
                if cls_entry:
                    transponder = cls_entry.get("pilota_nr", "")

                sessione = crea_scouting_json(
                    pilota=pilota,
                    tempi_giro=giri,
                    data_str=data_da_usare,
                    ora_str=ora_da_usare,
                    pista_nome=event_nome or nome_pista,
                    event_id=event_id,
                    category_nome=cnome,
                    session_idx=session_counter,
                    transponder=transponder,
                    classifica_entry=cls_entry,
                    sessione_nome=titolo_sessione,
                )
                if sessione:
                    # Deduplica: elimina sessione vecchia stesso pilota+evento+session
                    for old_f in os.listdir(scouting_dir):
                        if not old_f.endswith(".json"):
                            continue
                        old_path = os.path.join(scouting_dir, old_f)
                        try:
                            with open(old_path, "r", encoding="utf-8") as fp:
                                old = json.load(fp)
                            if (old.get("tipo") == "myrcm" and
                                old.get("pilota") == pilota and
                                old.get("myrcm_event") == event_id and
                                old.get("myrcm_session") == session_counter):
                                os.remove(old_path)
                        except Exception:
                            pass

                    path = salva_scouting(sessione, scouting_dir, transponder,
                                          setup_snapshot=setup_snapshot)
                    if path:
                        saved.append((sessione, path, session_counter, len(giri)))
                    session_counter += 1

    return saved, event_nome


# =====================================================================
#  Pulizia file scouting per data
# =====================================================================
def pulisci_scouting_data_vecchia(scouting_dir, data_corrente):
    """Elimina tutti i file scouting con data diversa da data_corrente.
    data_corrente: "DD/MM/YYYY"
    Ritorna numero file eliminati."""
    if not os.path.isdir(scouting_dir):
        return 0

    eliminati = 0
    for f in os.listdir(scouting_dir):
        if not f.endswith(".json"):
            continue
        path = os.path.join(scouting_dir, f)
        try:
            with open(path, "r", encoding="utf-8") as fp:
                dati = json.load(fp)
            data_file = dati.get("data", "")
            # Normalizza entrambe le date per confronto
            if data_file and data_file != data_corrente:
                # Confronta anche formato ISO vs DD/MM/YYYY
                try:
                    if "-" in data_file:
                        # Formato ISO YYYY-MM-DD -> DD/MM/YYYY
                        p = data_file.split("-")
                        data_file_norm = "%s/%s/%s" % (p[2], p[1], p[0])
                    elif "." in data_file:
                        # Formato DD.MM.YYYY -> DD/MM/YYYY
                        data_file_norm = data_file.replace(".", "/")
                    else:
                        data_file_norm = data_file

                    if "/" in data_corrente:
                        data_corr_norm = data_corrente
                    elif "." in data_corrente:
                        data_corr_norm = data_corrente.replace(".", "/")
                    elif "-" in data_corrente:
                        p = data_corrente.split("-")
                        data_corr_norm = "%s/%s/%s" % (p[2], p[1], p[0])
                    else:
                        data_corr_norm = data_corrente

                    if data_file_norm != data_corr_norm:
                        os.remove(path)
                        eliminati += 1
                except Exception:
                    pass
        except Exception:
            pass

    if eliminati > 0:
        print("[MyRCM] Pulizia: eliminati %d file scouting con data diversa da %s" % (
            eliminati, data_corrente))
    return eliminati


# =====================================================================
#  Standalone test
# =====================================================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        pista = sys.argv[1]
        data = sys.argv[2] if len(sys.argv) > 2 else ""
        print("Ricerca eventi per '%s'..." % pista)
        eventi = cerca_eventi_online(pista)
        for e in eventi:
            print("  - %s (ID: %s, %s)" % (e["nome"], e["event_id"], e["nazione"]))
        if eventi and data:
            eid = eventi[0]["event_id"]
            print("\nScarico categorie evento %s..." % eid)
            cats = scarica_categorie(eid)
            for c in cats:
                print("  - %s (ID: %s)" % (c["nome"], c["category_id"]))
    else:
        print("Uso: python myrcm_import.py <nome_pista> [DD/MM/YYYY]")
