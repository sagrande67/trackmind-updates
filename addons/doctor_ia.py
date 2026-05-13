"""
TrackMind - Doctor IA v1.0
Add-on TrackMind: il pilota indica UN sintomo di guida (sottosterza,
sovrasterza, testa-coda, ecc.) e l'IA propone le modifiche minime al
setup attuale, evidenziando le differenze.

Il modulo si integra con il form record di setup di retrodb tramite
il bottone "DR. IA": appare solo se nel .def della tabella e' attiva
la direttiva "!dr_ia;vero" e ci sono campi marcati con il flag ;A
(analisi_ia).

Flusso:
  1. Schermata SINTOMI: pulsanti raggruppati per categoria, selezione
     esclusiva (un solo sintomo). Campo "Note libere" opzionale.
  2. Click ANALIZZA: chiamata Claude API in thread separato, animazione
     di attesa che non blocca la UI.
  3. Schermata CONFRONTO: per ogni campo IA mostra valore vecchio e
     proposto, evidenziando in rosso/giallo i campi cambiati. Sotto,
     la spiegazione discorsiva dell'IA. Bottone SALVA NUOVO SETUP.
"""

from version import __version__

import tkinter as tk
from tkinter import font as tkfont
import json
import threading

# Import condizionale per ai_analisi (riusa la chiamata API esistente).
try:
    from addons.ai_analisi import chiama_claude as _chiama_claude
    from addons.ai_analisi import _get_api_key as _get_api_key
except Exception:
    try:
        from ai_analisi import chiama_claude as _chiama_claude
        from ai_analisi import _get_api_key as _get_api_key
    except Exception:
        _chiama_claude = None
        _get_api_key = lambda: None

# Font monospace + colori centralizzati.
try:
    from config_colori import FONT_MONO, carica_colori as _carica_colori
except ImportError:
    import sys as _sys
    FONT_MONO = "Consolas" if _sys.platform == "win32" else "DejaVu Sans Mono"
    def _carica_colori():
        return {}

# RetroField (per il campo Note libere e l'eventuale campo testo del prompt).
try:
    from tm_field import RetroField
except Exception:
    try:
        from core.tm_field import RetroField
    except Exception:
        RetroField = None

# Navigazione tastiera centralizzata (frecce + Enter + focus visivo).
# Stesso modulo usato da retrodb / editor_tabelle / crono per garantire
# che il Dr. IA si comporti esattamente come il resto dell'app: niente
# obbligo di mouse, tutto navigabile con TAB/frecce/Enter.
try:
    from core.ui_bottoni import (setup_bottoni as _setup_bottoni,
                                 setup_griglia as _setup_griglia,
                                 focus_evidenzia as _focus_evidenzia)
    _HAS_UI_BTN = True
except Exception:
    try:
        from ui_bottoni import (setup_bottoni as _setup_bottoni,
                                setup_griglia as _setup_griglia,
                                focus_evidenzia as _focus_evidenzia)
        _HAS_UI_BTN = True
    except Exception:
        _HAS_UI_BTN = False
        _setup_bottoni = None
        _setup_griglia = None
        _focus_evidenzia = None


# ─────────────────────────────────────────────────────────────────────
#  CATALOGO SINTOMI
# ─────────────────────────────────────────────────────────────────────
# Lista (gruppo, [sintomi]) ordinata. Il sintomo selezionato viene
# passato al prompt IA cosi' come scritto qui (italiano colloquiale).
SINTOMI_GUIDA = [
    ("STERZO", [
        "Sottosterza in entrata curva",
        "Sottosterza al centro curva",
        "Sottosterza in uscita curva",
        "Sottosterza in piena accelerazione",
        "Sovrasterza in entrata curva",
        "Sovrasterza al centro curva",
        "Sovrasterza in uscita curva",
        "Sovrasterza in piena accelerazione",
    ]),
    ("STABILITA'", [
        "Testa-coda in frenata",
        "Testa-coda in accelerazione",
        "Salta sui cordoli",
        "Rollio eccessivo",
        "Beccheggio eccessivo",
    ]),
    ("TRAZIONE / GRIP", [
        "Poco grip anteriore",
        "Poco grip posteriore",
        "Pattina in uscita",
        "Wheelie / impenna",
    ]),
    ("MOTORE / ALTRO", [
        "Strappo all'apertura del gas",
        "Spegne sotto carico",
        "Vibrazioni anomale",
    ]),
]


# ─────────────────────────────────────────────────────────────────────
#  DOMANDE PER LA MODALITA' "SETUP BASE"
# ─────────────────────────────────────────────────────────────────────
# Ogni domanda e' una tupla (chiave_interna, label_visibile, [opzioni]).
# Le opzioni sono i pulsanti che il pilota seleziona (UNO per gruppo).
DOMANDE_SETUP_BASE = [
    ("tipo_pista", "Tipo di pista",
        ["Lenta (curve strette)",
         "Media (mix)",
         "Veloce (curve ampie)",
         "Piazzale (asfalto piatto, curve secche)"]),
    ("superficie", "Superficie",
        ["Liscia (asfalto regolare)",
         "Sconnessa (buche, cordoli alti)"]),
    ("rettilinei", "Rettilinei",
        ["Corti (top speed poco rilevante)",
         "Lunghi (top speed importante)"]),
]


# ─────────────────────────────────────────────────────────────────────
#  PROMPT PER CLAUDE
# ─────────────────────────────────────────────────────────────────────
# Blocco condiviso: dice all'IA quali UNITA' DI MISURA e RANGE TIPICI
# usare per i valori proposti, cosi' non scrive cose tipo '40W' (olio
# motore di una macchina vera) o '0.4 cSt'. I valori RC silicone sono
# semplici numeri interi senza unita' (es. 400, 1000, 5000).
RANGE_TIPICI_HINT = (
    "RANGE TIPICI E CONVENZIONI DI SCRITTURA (RC scala 1/8 e 1/10):\n"
    "- Olio ammortizzatori (Olio_Ammo_*): numero secco in cSt (silicone), "
    "tipico 200-800 per 1/10, 250-1500 per 1/8. Esempi: '400', '500', "
    "'650', '1000'. NON scrivere 'W' o '40W': non sono oli motore.\n"
    "- Olio differenziali (Olio_Diff_*): numero secco in cSt, range "
    "1000-100000. Esempi: '3000', '5000', '10000', '30000', '50000'.\n"
    "- Camber (Camber_*): gradi con un decimale, range -3.0 / +1.0. "
    "Esempi: '-2.0', '-1.5', '0.0'. Negativo = ruota inclinata in alto.\n"
    "- Caster: gradi interi, range 0-30. Esempi: '12', '15', '20'.\n"
    "- Convergenza (Convergenza_*): gradi con decimale, range -3.0/+3.0. "
    "Negativo = toe out, positivo = toe in. Esempi: '-1.0', '0', '+0.5'.\n"
    "- Downstop (Downstop_*): mm, range 1-7. Esempi: '3', '5', '7'.\n"
    "- Altezza telaio (Altezza_*): mm, tipico 18-30 (1/8) o 5-10 (1/10). "
    "Esempi: '23', '25'.\n"
    "- Pos_Ammo_*: numero del foro sul triangolo, range 1-5 (interno -> "
    "esterno). Esempi: '1', '2', '3'.\n"
    "- Barra_*: spessore in mm o codice colore secondo telaio. Mantieni "
    "la convenzione del valore attuale se presente.\n"
    "- Molla_Ant / Molla_Post: codice o colore della molla secondo il "
    "produttore. NON inventare codici nuovi: scegli tra le convenzioni "
    "tipiche (es. 'gold', 'silver', '2.5', '3.0') o lascia il valore "
    "attuale se non sei sicuro.\n"
    "- Fori piattello: numero di fori (1-3 in 1/10, 2-4 in 1/8) "
    "eventualmente seguito da diametro mm. Esempi: '2', '3', '2x1.4'.\n"
    "- Precarico_*: numero di clip o spessore mm. Esempi: '0', '1', "
    "'2', '0.5'.\n"
    "- Pignoni/Corone: numero denti come intero. Esempi: '13', '47', "
    "'15', '50'.\n"
    "- Gradazione candela: numero singolo da 5 a 9 (5=calda, 9=fredda). "
    "Esempi: '6', '7', '8'.\n"
    "- Marca_Frizione, Candela: NON modificare, sono componenti.\n\n"
    "REGOLA D'ORO: se il campo ha gia' un valore (lo trovi nel SETUP "
    "ATTUALE), il tuo nuovo valore deve avere LO STESSO formato e "
    "convenzione (stesso numero di decimali, stessa unita' implicita, "
    "stesso stile). Non aggiungere mai unita' di misura ('cSt', 'mm', "
    "'°', 'W') al valore: solo il numero.\n\n"
    "RICONOSCIMENTO SCALA DEL MODELLO: nei dati del Telaio puoi trovare "
    "la scala (es. '1/8', '1/10', '1/12'). Se presente, usala come "
    "riferimento per orientarti sui range tipici (es. altezza telaio "
    "6-12 mm in 1/10, 18-30 mm in 1/8; oli ammo 200-700 cSt in 1/10, "
    "300-1500 cSt in 1/8). Comunque, rispetta SEMPRE l'ordine di "
    "grandezza del valore attuale (regola sotto).\n\n"
    "RICONOSCIMENTO TIPO MODELLO (CRITICO per i range di altezza): nei "
    "dati del Telaio trovi anche il tipo. Combina sempre Scala + Tipo "
    "per scegliere i range corretti. Tabella riferimento ALTEZZA TELAIO "
    "(mm), valore tipico ANT/POST:\n"
    "  - BUGGY 1/8 (off-road, gomme tassellate): ANT 24-30 / POST 26-32\n"
    "  - BUGGY 1/10 (off-road): ANT 18-23 / POST 20-25\n"
    "  - TRUGGY 1/8 (off-road, ruote larghe): ANT 22-28 / POST 24-30\n"
    "  - PISTA GT 1/8 (on-road, gomme CAVE/pneumatiche, scocca GT "
    "chiusa, modello pesante): ANT 8-12 / POST 10-15. NON 24!\n"
    "  - PISTA 1/8 nitro classica (gomme spugna): ANT 6-10 / POST 8-12\n"
    "  - TOURING 1/10 elettrico (gomme spugna o gommata pista): "
    "ANT 4-7 / POST 5-8\n"
    "  - PISTA 1/12 (formula): ANT 3-5 / POST 4-6\n"
    "  - TRUCK / SHORT COURSE 1/10: ANT 25-32 / POST 28-35\n"
    "Caratteristiche generali per tipo:\n"
    "- BUGGY/TRUGGY (off-road): downstop generoso, ammo morbidi, oli "
    "fluidi, diff piu' liberi.\n"
    "- PISTA GT (on-road pesante con gomme cave): altezza bassa ma non "
    "estrema, ammo medi, oli ammo 350-700, diff abbastanza bloccato.\n"
    "- PISTA / TOURING (on-road con gomme spugna/gommata): altezza "
    "bassissima, ammo rigidi, oli viscosi, diff bloccato.\n"
    "- TRUCK / SHORT COURSE: centro gravita' alto, molle rigide, "
    "downstop generoso.\n"
    "Adatta tutti gli altri parametri di conseguenza.\n\n"
    "REGOLA DELL'ORDINE DI GRANDEZZA (CRITICA, mai violare): il valore "
    "che proponi deve essere dello STESSO ORDINE DI GRANDEZZA del valore "
    "attuale. Esempi:\n"
    "  - Se Altezza_Ant = '8', il valore proposto deve essere fra "
    "5 e 12 (NO '24', NO '30', NO '20')\n"
    "  - Se Altezza_Post = '12', proporzionalmente fra 8 e 18 (NO '6', "
    "NO '24')\n"
    "  - Se Olio_Ammo_Ant = '400', proponi fra 200 e 800 (NO '4000', "
    "NO '40')\n"
    "  - Se Olio_Diff_Cent = '5000', proponi fra 2000 e 15000 (NO "
    "'50000', NO '500')\n"
    "  - Se Camber_Ant = '-1.5', proponi fra -3.0 e 0.0 (NO '+5')\n"
    "Variazione massima accettabile: ~3x in piu' o /3 in meno rispetto "
    "al valore attuale.\n\n"
    "FIDUCIA ASSOLUTA NEI VALORI DEL PILOTA: il pilota sa cosa sta "
    "facendo. Se hai un sospetto (es. 'questo valore non sembra tipico "
    "per un buggy 1/8'), tu NON devi correggerlo: i suoi numeri sono "
    "RIFERIMENTO INVIOLABILE. Anche se il telaio dichiara una certa "
    "scala/tipo, NON modificare valori per allinearli a una tua idea di "
    "'normalita''. Lavora ESCLUSIVAMENTE in piccola variazione attorno "
    "ai valori esistenti. Se non sai cosa cambiare per risolvere un "
    "sintomo, restituisci 'modifiche': {} con spiegazione del perche'.\n"
)


SYSTEM_PROMPT_DR_IA = (
    "Sei un ingegnere di gara (race engineer) per automodelli RC scala "
    "1/8 e 1/10. Il pilota ti riporta UN problema di guida specifico. "
    "Il tuo compito: proporre la MINIMA modifica al setup necessaria "
    "per correggere il problema indicato.\n\n"
    "REGOLE FONDAMENTALI:\n"
    "1. Modifica SOLO i parametri strettamente necessari (max 2-3 valori)\n"
    "2. I valori proposti devono essere coerenti con i tipi e le unita' "
    "indicati (numeri, gradi, mm, ecc.)\n"
    "3. Spiegazione concisa in italiano (max 3 righe)\n"
    "4. Rispondi SOLO in JSON valido, niente testo prima o dopo\n"
    "5. Usa ESATTAMENTE i nomi dei campi forniti (case-sensitive)\n\n"
    "MAPPA SINTOMI -> PARAMETRI TIPICAMENTE COINVOLTI (orientativa, "
    "non esaustiva):\n"
    "- Sottosterzo entrata: caster, olio diff anteriore, downstop "
    "anteriore, ripartizione frenata, ammo anteriori\n"
    "- Sottosterzo centro: camber anteriore, convergenza anteriore, "
    "barra anteriore, altezza anteriore\n"
    "- Sottosterzo uscita / accelerazione: peso davanti, olio diff "
    "anteriore, downstop anteriore, molle anteriori\n"
    "- Sovrasterzo entrata: ammo anteriori, olio diff anteriore, "
    "convergenza posteriore, downstop posteriore\n"
    "- Sovrasterzo centro: camber posteriore, barra posteriore, "
    "altezza posteriore, convergenza posteriore\n"
    "- Sovrasterzo uscita / accelerazione: olio diff posteriore (piu' "
    "viscoso), olio ammo posteriori, downstop anteriore (ridurre per "
    "limitare trasferimento), molle posteriori\n"
    "- Testa-coda in frenata: olio diff anteriore (piu' viscoso), "
    "downstop posteriore, ripartizione frenata\n"
    "- Testa-coda in accelerazione: olio diff posteriore, downstop "
    "anteriore, ammo posteriori\n"
    "- Salta sui cordoli: olio ammo (piu' fluido), fori piattello, "
    "downstop, molle\n"
    "- Rollio eccessivo: barre antirollio, molle, olio ammo\n"
    "- Beccheggio eccessivo: olio ammo, downstop, fori piattello\n"
    "- Poco grip anteriore: camber/caster anteriore, peso davanti, "
    "molle anteriori, olio diff anteriore\n"
    "- Poco grip posteriore: camber/convergenza posteriore, downstop "
    "posteriore, olio diff posteriore, molle posteriori\n"
    "- Pattina in uscita: olio diff posteriore (piu' viscoso), molle "
    "posteriori (piu' morbide), downstop posteriore (aumentare)\n"
    "- Wheelie / impenna: peso davanti, downstop anteriore (aumentare), "
    "altezza anteriore\n\n"
    "Considera SEMPRE le voci sopra prima di scegliere altre modifiche. "
    "Non e' obbligatorio toccare ognuna: agisci sulla 1-2 piu' efficaci "
    "in base al setup attuale.\n\n"
    + RANGE_TIPICI_HINT +
    "\nFormato risposta richiesto:\n"
    "{\n"
    '  "modifiche": {"NomeCampo": "nuovo_valore", ...},\n'
    '  "spiegazione": "Breve motivazione delle modifiche"\n'
    "}\n"
)


# Prompt alternativo per la modalita' "Setup base": qui non si correggono
# sintomi, si STILA un setup di partenza ottimizzato per le caratteristiche
# della pista e le condizioni meteo del giorno.
SYSTEM_PROMPT_SETUP_BASE = (
    "Sei un ingegnere di gara (race engineer) per automodelli RC scala "
    "1/8 e 1/10. Devi proporre un SETUP DI PARTENZA per la pista e le "
    "condizioni di oggi, basandoti sulle caratteristiche fornite dal "
    "pilota (tipo di pista, superficie, rettilinei) e sul setup attuale "
    "che fa da riferimento.\n\n"
    "REGOLE:\n"
    "1. Proponi valori per TUTTI i parametri tecnici principali, non "
    "solo 2-3 (a differenza della modalita' diagnosi)\n"
    "2. Mantieni i valori attuali quando sono gia' coerenti con la "
    "tipologia di pista\n"
    "3. Modifica SOLO i campi che ti vengono elencati come 'modificabili' "
    "(usa esattamente quei nomi, case-sensitive)\n"
    "4. I valori devono essere coerenti con il tipo del campo (numero, "
    "gradi, mm, ecc.) e con i range tipici degli automodelli RC\n"
    "5. Spiegazione concisa in italiano (max 4 righe), evidenzia le 2-3 "
    "scelte chiave del setup\n"
    "6. Rispondi SOLO in JSON valido, niente testo prima o dopo\n\n"
    "LINEE GUIDA RAPIDE:\n"
    "- Pista LENTA/PIAZZALE: setup piu' rigido (molle/ammo piu' duri), "
    "downstop ridotto, diff piu' bloccato (oli viscosi), piu' caster\n"
    "- Pista VELOCE: setup piu' morbido per l'aderenza, downstop "
    "piu' generoso, gomme che lavorano, alleggerire diff\n"
    "- Pista SCONNESSA: ammo piu' fluidi (oli leggeri), molle piu' "
    "morbide, downstop maggiore per assorbire i salti, fori piattello "
    "piu' aperti\n"
    "- Pista LISCIA: setup teso, oli ammo piu' viscosi, downstop "
    "ridotto, fori piattello chiusi\n"
    "- Rettilinei LUNGHI: rapporti piu' lunghi (piu' corona o meno "
    "pignone secondo convenzione), assetto stabile in alta velocita'\n"
    "- Rettilinei CORTI: rapporti piu' corti per accelerazione, setup "
    "che favorisce la trazione dalle curve\n"
    "- Caldo: gomma piu' dura/usurata, miscela piu' magra; oli "
    "leggermente piu' viscosi negli ammo\n"
    "- Freddo: gomma piu' morbida, oli leggermente piu' fluidi\n\n"
    + RANGE_TIPICI_HINT +
    "\nFormato risposta richiesto:\n"
    "{\n"
    '  "modifiche": {"NomeCampo": "valore_proposto", ...},\n'
    '  "spiegazione": "Filosofia del setup base e le scelte chiave"\n'
    "}\n"
)


# Prompt per la modalita' "Analizza tempi": l'IA riceve i giri di una
# sessione gia' cronometrata e propone modifiche al setup basandosi sui
# pattern dei giri (costanza, calo, picchi, distribuzione).
SYSTEM_PROMPT_ANALIZZA_TEMPI = (
    "Sei un ingegnere di gara (race engineer) per automodelli RC scala "
    "1/8 e 1/10. Hai a disposizione una sessione di tempi gia' "
    "cronometrata. Devi analizzarli e proporre modifiche al setup per "
    "migliorare COSTANZA e VELOCITA'.\n\n"
    "ANALISI TIPICHE da fare prima di proporre modifiche:\n"
    "1. Best lap vs media: se la differenza e' > 5%% indica setup "
    "incostante o errori di guida; < 2%% setup ben rodato.\n"
    "2. Distribuzione tempi: se ci sono pochi tempi 'fuori scala' "
    "isolati, sono errori di guida (non toccare il setup); se e' "
    "diffuso, e' setup.\n"
    "3. Calo nel tempo (gomme/carburante): tempi che peggiorano "
    "progressivamente -> consumo gomme/squilibrio dovuto al carburante "
    "che cala -> agire su molle/altezza/diff.\n"
    "4. Pattern alternato (1 giro buono, 1 lento): difetto dinamico "
    "(es. sovrasterzo in uscita, perdita di trazione).\n"
    "5. Tempi simili ma lontani dal best ottenibile: setup conservativo, "
    "valuta scelte piu' aggressive (ammo piu' rigidi, downstop ridotto, "
    "diff piu' bloccato).\n\n"
    "REGOLE:\n"
    "1. Modifica SOLO i parametri necessari (max 3 valori)\n"
    "2. Spiegazione concisa (max 4 righe) dove citi ESPLICITAMENTE i "
    "tempi/pattern che ti hanno guidato (es. 'best 21.4 ma 4 giri sopra "
    "23 -> ...').\n"
    "3. Rispondi SOLO in JSON valido, niente testo prima o dopo\n"
    "4. Usa ESATTAMENTE i nomi dei campi forniti (case-sensitive)\n\n"
    + RANGE_TIPICI_HINT +
    "\nFormato risposta richiesto:\n"
    "{\n"
    '  "modifiche": {"NomeCampo": "nuovo_valore", ...},\n'
    '  "spiegazione": "Cosa hai notato dai tempi e cosa hai cambiato"\n'
    "}\n"
)


def _stat_tempi(giri):
    """Calcola statistiche da una lista di giri.

    Args:
        giri: lista di dict {tempo, stato} (o solo {tempo})
    Returns:
        dict con: best, media, dev, validi, totali, peggior, range_pct
    """
    validi = []
    totali = len(giri)
    for g in giri:
        try:
            t = float(g.get("tempo", 0) or 0)
            stato = (g.get("stato", "valido") or "valido").lower()
        except (ValueError, TypeError):
            continue
        if t > 0 and stato.startswith("valid"):
            validi.append(t)
    if not validi:
        return {"best": 0, "media": 0, "dev": 0, "peggior": 0,
                "validi": 0, "totali": totali, "range_pct": 0}
    best = min(validi)
    peggior = max(validi)
    media = sum(validi) / len(validi)
    # Deviazione standard
    var = sum((t - media) ** 2 for t in validi) / len(validi)
    dev = var ** 0.5
    range_pct = ((peggior - best) / best * 100.0) if best else 0
    return {
        "best": best, "media": media, "dev": dev, "peggior": peggior,
        "validi": len(validi), "totali": totali, "range_pct": range_pct,
    }


def _costruisci_prompt_analizza_tempi(tempi_sessioni, parametri_ia,
                                      contesto_extra):
    """Costruisce il prompt utente per la modalita' 'Analizza tempi'.

    Accetta sia un dict singolo (retrocompatibilita') sia una lista di
    sessioni. Le sessioni si assume siano in ORDINE CRONOLOGICO
    crescente (vecchia -> recente) cosi' l'IA puo' osservare l'evoluzione
    e capire se il setup migliora o peggiora.

    Args:
        tempi_sessioni: lista di dict {data, ora, fonte, giri,
                        setup_snapshot?} oppure dict singolo
        parametri_ia: dict sezione -> [(nome, val), ...]
        contesto_extra: dict con pista, telaio, motore, gomme, meteo
    """
    setup_txt = _formatta_setup_per_prompt(parametri_ia, contesto_extra)
    nomi_campi = sorted({n for lista in parametri_ia.values()
                         for (n, _v) in lista})

    # Normalizza a lista (singolo dict -> [dict])
    if isinstance(tempi_sessioni, dict):
        tempi_sessioni = [tempi_sessioni]
    sessioni = [s for s in (tempi_sessioni or []) if s and s.get("giri")]
    n_ses = len(sessioni)

    parts = []
    if n_ses == 0:
        parts.append("NESSUNA SESSIONE DI TEMPI FORNITA.")
    elif n_ses == 1:
        parts.append("TEMPI DELLA SESSIONE DA ANALIZZARE:")
    else:
        parts.append(
            "TEMPI DI %d SESSIONI DA ANALIZZARE (ordine cronologico "
            "crescente, dalla piu' vecchia alla piu' recente):" % n_ses)

    # Statistiche aggregate (utili soprattutto con piu' sessioni)
    if n_ses > 1:
        all_validi = []
        for s in sessioni:
            for g in s.get("giri", []):
                try:
                    t = float(g.get("tempo", 0) or 0)
                    st = (g.get("stato", "valido") or "valido").lower()
                except (ValueError, TypeError):
                    continue
                if t > 0 and st.startswith("valid"):
                    all_validi.append(t)
        if all_validi:
            best_g = min(all_validi)
            media_g = sum(all_validi) / len(all_validi)
            parts.append(
                "\n  RIEPILOGO TOTALE: best assoluto %.3f s, "
                "media globale %.3f s su %d giri validi."
                % (best_g, media_g, len(all_validi)))

    # Dettaglio per sessione
    for i, ses in enumerate(sessioni, 1):
        giri = ses.get("giri", []) or []
        stats = _stat_tempi(giri)
        intestazione = ("\n  --- SESSIONE %d/%d ---"
                        % (i, n_ses) if n_ses > 1
                        else "")
        parts.append(intestazione)
        if ses.get("data") or ses.get("ora"):
            parts.append("  Data/ora: %s %s" % (
                ses.get("data", ""), ses.get("ora", "")))
        if ses.get("fonte"):
            parts.append("  Fonte: %s" % ses.get("fonte"))
        parts.append("  Statistiche:")
        parts.append("    - Giri totali: %d (validi: %d)"
                     % (stats["totali"], stats["validi"]))
        parts.append("    - Best lap: %.3f s" % stats["best"])
        parts.append("    - Tempo medio: %.3f s" % stats["media"])
        parts.append("    - Peggior giro valido: %.3f s"
                     % stats["peggior"])
        parts.append("    - Dev. std: %.3f s" % stats["dev"])
        parts.append("    - Range (peggior-best): %.1f%%"
                     % stats["range_pct"])
        # Lista giri (limitata)
        max_giri = 60 if n_ses == 1 else 30
        parts.append("  Giri (n, tempo, stato):")
        for j, g in enumerate(giri[:max_giri], 1):
            try:
                t = float(g.get("tempo", 0) or 0)
            except (ValueError, TypeError):
                continue
            stato = g.get("stato", "valido")
            parts.append("    %2d) %.3f s   %s" % (j, t, stato))
        if len(giri) > max_giri:
            parts.append("    ... (%d giri totali, mostrati primi %d)"
                         % (len(giri), max_giri))
        # Setup_snapshot: se differisce nelle varie sessioni e' utile
        # all'IA per capire se il setup era diverso al momento.
        snap = ses.get("setup_snapshot") or {}
        if snap and isinstance(snap, dict):
            # Mostriamo solo i campi marcati IA presenti nello snapshot
            righe_snap = []
            for nome in nomi_campi:
                if nome in snap:
                    val = str(snap.get(nome, "")).strip()
                    if val:
                        righe_snap.append("%s=%s" % (nome, val))
            if righe_snap:
                parts.append("  Setup al momento di questa sessione: "
                             + ", ".join(righe_snap))

    parts.append("\nSETUP ATTUALE (di partenza per le modifiche):")
    parts.append(setup_txt or "  (nessun parametro disponibile)")
    parts.append(
        "\nCAMPI MODIFICABILI (usa solo questi nomi):\n  "
        + ", ".join(nomi_campi)
    )
    if n_ses > 1:
        parts.append(
            "\nAnalizza l'EVOLUZIONE tra sessioni (la piu' recente e' "
            "l'ultima): tempi che migliorano, peggiorano, costanza che "
            "cambia, eventuali differenze di setup_snapshot. Proponi "
            "modifiche che capitalizzino i miglioramenti gia' visti e "
            "correggano i problemi residui. Cita esplicitamente nei "
            "commenti quale sessione e quali tempi hanno orientato la "
            "scelta. Rispondi SOLO con il JSON richiesto."
        )
    else:
        parts.append(
            "\nAnalizza i pattern (costanza, calo, picchi) e proponi le "
            "modifiche piu' efficaci. Rispondi SOLO con il JSON nel "
            "formato richiesto, citando nei tuoi commenti i giri/tempi "
            "specifici che ti hanno orientato."
        )
    return "\n".join(parts)


def _costruisci_prompt_setup_base(risposte, parametri_ia, contesto_extra):
    """Costruisce il prompt utente per la modalita' 'Setup base'.

    Args:
        risposte: dict {chiave_domanda: opzione_scelta}
                  es. {'tipo_pista': 'Lenta (curve strette)', ...}
        parametri_ia: dict sezione -> [(nome, val), ...]
        contesto_extra: dict con pista, telaio, motore, gomme, meteo
    """
    setup_txt = _formatta_setup_per_prompt(parametri_ia, contesto_extra)
    nomi_campi = sorted({n for lista in parametri_ia.values()
                         for (n, _v) in lista})
    parts = [
        "CARATTERISTICHE DELLA PISTA / GIORNATA (selezionate dal pilota):",
    ]
    for chiave, label, _opzioni in DOMANDE_SETUP_BASE:
        scelta = risposte.get(chiave, "")
        if scelta:
            parts.append("  - %s: %s" % (label, scelta))
    parts.append("\nSETUP ATTUALE (di riferimento, da migliorare):")
    parts.append(setup_txt or "  (nessun parametro disponibile)")
    parts.append(
        "\nCAMPI MODIFICABILI (usa solo questi nomi):\n  "
        + ", ".join(nomi_campi)
    )
    parts.append(
        "\nProponi i valori del setup di partenza ottimale per queste "
        "caratteristiche. Rispondi SOLO in JSON nel formato richiesto."
    )
    return "\n".join(parts)


def _estrai_parametri_ia(record, table_def):
    """Estrae dal record i campi marcati con ;A nel .def, raggruppati
    per sezione (vedi !sezione nel .def).

    Ritorna un dict {sezione: [(nome_campo, valore), ...]}.
    """
    parametri = {}
    sezioni_map = getattr(table_def, "sezioni", {}) or {}
    sezione_corrente = ""
    for campo_def in table_def.campi:
        nome = campo_def["nome"]
        # Aggiorna sezione di scorrimento (i campi seguono l'ordine del .def)
        if nome in sezioni_map:
            sezione_corrente = sezioni_map[nome]
        if not campo_def.get("analisi_ia"):
            continue
        val = str(record.get(nome, "")).strip()
        sez = sezione_corrente or ""
        parametri.setdefault(sez, []).append((nome, val))
    return parametri


def _formatta_setup_per_prompt(parametri_ia, contesto_extra=None):
    """Costruisce la parte 'setup attuale' del prompt in formato
    leggibile dall'IA. Restituisce stringa multi-riga.

    I riferimenti ad altre tabelle (@gomme, @motori, @miscela, @telai...)
    sono inviati come INFORMAZIONE DI CONTESTO: l'IA li legge per capire
    su quale base sta lavorando, ma non li puo' modificare nelle
    'modifiche' proposte (lo scope e' limitato ai campi marcati ;A).
    """
    righe = []
    contesto_extra = contesto_extra or {}
    # Pista (chiave fissa dal contesto crono)
    v_pista = str(contesto_extra.get("pista", "")).strip()
    if v_pista:
        righe.append("- Pista: " + v_pista)
    # Riferimenti dinamici: ogni alias dichiarato nel .def crea una
    # chiave 'ref_<alias_lower>' (es. ref_gomma_anteriore,
    # ref_gomma_posteriore, ref_telai, ref_motori, ref_miscela...).
    # Iteriamo TUTTI i ref_* del contesto cosi' il setup composito
    # con gomma anteriore/posteriore separate viene completo.
    for k in sorted(contesto_extra.keys()):
        if not k.startswith("ref_"):
            continue
        v = str(contesto_extra[k]).strip()
        if not v:
            continue
        # 'ref_gomma_anteriore' -> 'Gomma Anteriore'
        label = k[4:].replace("_", " ").title()
        righe.append("- %s: %s" % (label, v))
    # Meteo / pista
    for k, label in (("temp_esterna", "Temp. esterna"),
                     ("temp_pista", "Temp. pista"),
                     ("umidita", "Umidita'"),
                     ("condizioni_pista", "Condizioni pista"),
                     ("vento", "Vento")):
        v = str(contesto_extra.get(k, "")).strip()
        if v:
            righe.append("- %s: %s" % (label, v))
    # Parametri tecnici raggruppati per sezione.
    # I campi VUOTI vengono comunque elencati come '(vuoto)' cosi' l'IA
    # sa che deve compilarli con valori tipici della categoria del
    # modello (Tipo + Scala dal telaio), non con valori arbitrari.
    campi_vuoti = []
    for sez, lista in parametri_ia.items():
        if not lista:
            continue
        if sez:
            righe.append("\n[%s]" % sez)
        for nome, val in lista:
            if val:
                righe.append("  %s = %s" % (nome, val))
            else:
                righe.append("  %s = (vuoto, da compilare)" % nome)
                campi_vuoti.append(nome)
    if campi_vuoti:
        righe.append(
            "\nNOTA: i campi marcati '(vuoto, da compilare)' devono "
            "essere proposti dall'IA usando valori coerenti con "
            "Scala+Tipo del telaio (vedi tabella range). Non eccedere "
            "i range della categoria specifica."
        )
    return "\n".join(righe)


def _costruisci_prompt(sintomo, note, parametri_ia, contesto_extra):
    """Costruisce il prompt utente per l'IA."""
    setup_txt = _formatta_setup_per_prompt(parametri_ia, contesto_extra)
    nomi_campi = sorted({n for lista in parametri_ia.values()
                         for (n, _v) in lista})
    parts = [
        "PROBLEMA RIPORTATO DAL PILOTA:",
        "  " + sintomo,
    ]
    if note:
        parts.append("\nNOTE LIBERE DEL PILOTA:")
        parts.append("  " + note)
    parts.append("\nSETUP ATTUALE:")
    parts.append(setup_txt or "  (nessun parametro disponibile)")
    parts.append(
        "\nCAMPI MODIFICABILI (usa solo questi nomi nelle 'modifiche'):\n  "
        + ", ".join(nomi_campi)
    )
    parts.append(
        "\nProponi la modifica MINIMA che risolve il problema. "
        "Rispondi SOLO con il JSON nel formato richiesto."
    )
    return "\n".join(parts)


def _valore_sospetto(val_old, val_new):
    """Verifica se la modifica proposta dall'IA stravolge l'ordine di
    grandezza del valore originale (es. da 8 a 24, oppure da 400 a 4000).
    Tollera variazioni fino a ~3x in piu' o /3 in meno; oltre, segnala.

    Ritorna True se la modifica e' sospetta (worth a warning).
    Solo per campi numerici: per stringhe/codici torna sempre False.
    """
    if not val_old or not val_new:
        return False
    import re as _re
    try:
        # Normalizza: virgola -> punto, rimuove caratteri non numerici
        # tranne segno e punto.
        def _to_float(s):
            s2 = str(s).replace(",", ".").strip()
            s2 = _re.sub(r"[^0-9.+\-]", "", s2)
            return float(s2)
        a = _to_float(val_old)
        b = _to_float(val_new)
        if a == 0 or b == 0:
            return False
        ratio = abs(b / a)
        return ratio >= 3.5 or ratio <= 0.28
    except (ValueError, TypeError):
        return False


def _parse_risposta_ia(testo):
    """Estrae JSON dalla risposta IA. Tollerante: se Claude ha aggiunto
    testo prima/dopo, cerca il primo blocco { ... } valido."""
    if not testo:
        return None, "Risposta vuota dall'IA"
    # Tentativo diretto
    try:
        data = json.loads(testo.strip())
        if isinstance(data, dict) and "modifiche" in data:
            return data, None
    except (ValueError, TypeError):
        pass
    # Estrazione blocco { ... } piu' lungo
    inizio = testo.find("{")
    fine = testo.rfind("}")
    if inizio >= 0 and fine > inizio:
        blocco = testo[inizio:fine + 1]
        try:
            data = json.loads(blocco)
            if isinstance(data, dict) and "modifiche" in data:
                return data, None
        except (ValueError, TypeError):
            pass
    return None, "JSON non valido nella risposta IA"


# ─────────────────────────────────────────────────────────────────────
#  CLASSE UI
# ─────────────────────────────────────────────────────────────────────
class DoctorIA:
    """Schermata Dr. IA embedded in retrodb.

    Args:
        parent: tk.Frame contenitore (self._vista di retrodb)
        record: dict del record setup corrente
        table_def: TableDef della tabella setup
        contesto_extra: dict opzionale {pista, ref_telai, ref_motori, ...}
        on_back: callback() per chiudere senza salvare
        on_save: callback(modifiche, spiegazione, sintomo) per salvare
                 nuovo setup. modifiche = {NomeCampo: nuovo_valore, ...}
    """

    def __init__(self, parent, record, table_def,
                 contesto_extra=None, on_back=None, on_save=None,
                 tempi_sessioni=None, tempi_sessione=None):
        self.parent = parent
        self.record = record or {}
        self.table_def = table_def
        self.contesto = contesto_extra or {}
        self._on_back = on_back
        self._on_save = on_save
        # Tempi delle sessioni (lista) passati dall'esterno.
        # Accetta sia 'tempi_sessioni' (lista, nuovo) sia
        # 'tempi_sessione' (singolo dict, retrocompat). Internamente
        # normalizziamo a lista. Formato di ogni elemento:
        # {data, ora, fonte, giri:[{tempo, stato}, ...], setup_snapshot?}
        if tempi_sessioni:
            self.tempi_sessioni = list(tempi_sessioni)
        elif tempi_sessione:
            self.tempi_sessioni = [tempi_sessione]
        else:
            self.tempi_sessioni = []
        # Comodo riferimento all'ultima (piu' recente) sessione.
        self.tempi_sessione = (self.tempi_sessioni[-1]
                               if self.tempi_sessioni else None)
        self.c = _carica_colori()

        # Stato corrente
        self._sintomo_sel = None  # stringa o None (modalita' diagnosi)
        self._btn_sintomi = []    # lista (sintomo_str, tk.Button)
        self._risposta_ia = None  # dict da parse_risposta_ia
        self._anima_id = None     # id after() per animazione attesa
        self._note_field = None   # RetroField note libere
        self._btn_analizza = None
        # Stato modalita' setup base
        self._risposte_base = {}  # {chiave_domanda: opzione_scelta}
        self._btn_base = []       # lista (chiave, opzione, tk.Button)
        # Modalita' attiva: 'sintomi' o 'setup_base' (impostata
        # quando l'utente sceglie dalla schermata iniziale)
        self._modalita = None

        # Font (uconsole-friendly, no scaling: la finestra retrodb
        # ha gia' la sua scala globale)
        self._f_title = tkfont.Font(family=FONT_MONO, size=11, weight="bold")
        self._f_btn   = tkfont.Font(family=FONT_MONO, size=9, weight="bold")
        self._f_label = tkfont.Font(family=FONT_MONO, size=9)
        self._f_small = tkfont.Font(family=FONT_MONO, size=8)
        self._f_info  = tkfont.Font(family=FONT_MONO, size=9)

        # Schermata iniziale: lascia scegliere DIAGNOSI o SETUP BASE.
        # Da qui in poi le altre schermate sostituiscono il contenuto
        # del frame parent.
        self._build_scelta()

    # ── helper ────────────────────────────────────────────────────────
    def _pulisci_parent(self):
        for w in self.parent.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

    def _back(self):
        if callable(self._on_back):
            self._on_back()

    # ── SCHERMATA 0: SCELTA MODALITA' ─────────────────────────────────
    def _build_scelta(self):
        """Schermata iniziale: scegli DIAGNOSI o SETUP BASE."""
        c = self.c
        self._pulisci_parent()

        # Header
        header = tk.Frame(self.parent, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< SETUP", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._back).pack(side="left")
        tk.Label(header, text="  DR. IA - Cosa fai?",
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(8, 0))
        tk.Frame(self.parent, bg=c["linee"], height=1).pack(
            fill="x", padx=10, pady=(4, 8))

        # Riepilogo essenziale (pista + meteo)
        info_parts = []
        for k, label in (("pista", "Pista"),):
            v = str(self.contesto.get(k, "")).strip()
            if v:
                info_parts.append("%s: %s" % (label, v[:24]))
        for k, label in (("temp_pista", "T.pista"),
                         ("condizioni_pista", "Cond.")):
            v = str(self.contesto.get(k, "")).strip()
            if v:
                info_parts.append("%s: %s" % (label, v[:14]))
        if info_parts:
            tk.Label(self.parent, text="  |  ".join(info_parts),
                     bg=c["sfondo"], fg=c["testo_dim"],
                     font=self._f_small).pack(pady=(0, 12))

        # Due grossi bottoni con descrizione
        scelte = tk.Frame(self.parent, bg=c["sfondo"])
        scelte.pack(pady=4, padx=20, fill="x")

        # DIAGNOSI
        f1 = tk.Frame(scelte, bg=c["sfondo"])
        f1.pack(fill="x", pady=6)
        btn_diagnosi = tk.Button(f1, text="DIAGNOSI",
                                 font=self._f_btn,
                                 bg=c["pulsanti_sfondo"],
                                 fg=c["stato_avviso"],
                                 relief="ridge", bd=1, cursor="hand2",
                                 width=18, height=2,
                                 command=self._scegli_diagnosi)
        btn_diagnosi.pack(side="left", padx=(0, 12))
        tk.Label(f1,
                 text="Hai un problema in pista. Lo descrivi (sintomo) e\n"
                      "l'IA propone modifiche al setup attuale.",
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_label, justify="left",
                 anchor="w").pack(side="left", fill="x", expand=True)

        # SETUP BASE
        f2 = tk.Frame(scelte, bg=c["sfondo"])
        f2.pack(fill="x", pady=6)
        btn_base = tk.Button(f2, text="SETUP BASE",
                             font=self._f_btn,
                             bg=c["pulsanti_sfondo"],
                             fg=c["stato_ok"],
                             relief="ridge", bd=1, cursor="hand2",
                             width=18, height=2,
                             command=self._scegli_setup_base)
        btn_base.pack(side="left", padx=(0, 12))
        tk.Label(f2,
                 text="Setup di partenza per la pista e le condizioni\n"
                      "di oggi: poche domande e l'IA stila il setup.",
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_label, justify="left",
                 anchor="w").pack(side="left", fill="x", expand=True)

        # ANALIZZA TEMPI: solo se sono stati passate sessioni dall'esterno
        # (tipicamente da Crono/CONFRONTA dal setup).
        btn_tempi = None
        ses_validi = [s for s in self.tempi_sessioni
                      if s and s.get("giri")]
        if ses_validi:
            f3 = tk.Frame(scelte, bg=c["sfondo"])
            f3.pack(fill="x", pady=6)
            btn_tempi = tk.Button(f3, text="ANALIZZA TEMPI",
                                  font=self._f_btn,
                                  bg=c["pulsanti_sfondo"],
                                  fg=c["cerca_testo"],
                                  relief="ridge", bd=1, cursor="hand2",
                                  width=18, height=2,
                                  command=self._scegli_analizza_tempi)
            btn_tempi.pack(side="left", padx=(0, 12))
            n_ses = len(ses_validi)
            n_giri_tot = sum(len(s.get("giri", [])) for s in ses_validi)
            if n_ses == 1:
                data_t = ses_validi[0].get("data", "")
                desc_txt = ("Analizza la sessione di %d giri (%s)\n"
                            "e proponi modifiche al setup."
                            % (n_giri_tot, data_t or "ultima"))
            else:
                d_min = ses_validi[0].get("data", "?")
                d_max = ses_validi[-1].get("data", "?")
                desc_txt = (
                    "Analizza %d sessioni (%s -> %s, %d giri)\n"
                    "e proponi miglioramenti al setup."
                    % (n_ses, d_min, d_max, n_giri_tot))
            tk.Label(f3, text=desc_txt,
                     bg=c["sfondo"], fg=c["dati"],
                     font=self._f_label, justify="left",
                     anchor="w").pack(side="left", fill="x", expand=True)

        # Help piede pagina
        self._status = tk.Label(self.parent, text="",
                                bg=c["sfondo"], fg=c["testo_dim"],
                                font=self._f_small,
                                wraplength=720, justify="center")
        self._status.pack(pady=(12, 4))

        # Navigazione tastiera + focus iniziale
        bottoni_scelta = [btn_diagnosi, btn_base]
        if btn_tempi is not None:
            bottoni_scelta.append(btn_tempi)
        if _HAS_UI_BTN:
            try:
                _setup_bottoni(bottoni_scelta, orizzontale=False)
            except Exception:
                pass
        # Se sono stati passati tempi dall'esterno, e' molto probabile
        # che l'utente voglia usarli: focus su ANALIZZA TEMPI; altrimenti
        # focus su DIAGNOSI come default.
        focus_target = btn_tempi if btn_tempi is not None else btn_diagnosi
        try:
            self.parent.after(150, lambda b=focus_target: b.focus_set())
        except Exception:
            pass

    def _scegli_diagnosi(self):
        self._modalita = "sintomi"
        self._build_sintomi()

    def _scegli_setup_base(self):
        self._modalita = "setup_base"
        self._build_setup_base()

    def _scegli_analizza_tempi(self):
        """Modalita' analizza tempi: lanciata da Crono CONFRONTA quando
        c'e' una sessione cronometrata da analizzare. Niente domande,
        l'IA ha gia' tutto: setup attuale + giri + meteo."""
        self._modalita = "tempi"
        self._lancia_analisi_tempi()

    def _lancia_analisi_tempi(self):
        """Avvia chiamata Claude con prompt 'Analizza tempi'."""
        if _chiama_claude is None:
            self._mostra_errore_inline("Modulo ai_analisi non disponibile.")
            return
        api_key = _get_api_key()
        if not api_key:
            self._mostra_errore_inline(
                "API key Anthropic non configurata.\n"
                "Inserirla in CONF -> anthropic_api_key.")
            return
        ses_validi = [s for s in self.tempi_sessioni
                      if s and s.get("giri")]
        if not ses_validi:
            self._mostra_errore_inline(
                "Nessun tempo disponibile per l'analisi.")
            return
        parametri_ia = _estrai_parametri_ia(self.record, self.table_def)
        if not parametri_ia:
            self._mostra_errore_inline(
                "Nessun campo del setup e' marcato per l'analisi IA "
                "(flag ;A nel .def).")
            return

        prompt = _costruisci_prompt_analizza_tempi(
            ses_validi, parametri_ia, self.contesto)

        # UI di attesa: pulisce il parent e mostra solo l'animazione,
        # cosi' l'utente vede subito che sta succedendo qualcosa.
        c = self.c
        self._pulisci_parent()
        header = tk.Frame(self.parent, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< INDIETRO", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._build_scelta).pack(side="left")
        tk.Label(header, text="  DR. IA - Analizza tempi",
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(8, 0))
        tk.Frame(self.parent, bg=c["linee"], height=1).pack(
            fill="x", padx=10, pady=(4, 8))
        n_ses = len(ses_validi)
        n_giri_tot = sum(len(s.get("giri", [])) for s in ses_validi)
        if n_ses == 1:
            riepilogo_txt = ("Sessione: %d giri | Data: %s | Fonte: %s"
                             % (n_giri_tot,
                                ses_validi[0].get("data", "?"),
                                ses_validi[0].get("fonte", "?")))
        else:
            riepilogo_txt = (
                "%d sessioni | %s -> %s | %d giri totali"
                % (n_ses,
                   ses_validi[0].get("data", "?"),
                   ses_validi[-1].get("data", "?"),
                   n_giri_tot))
        tk.Label(self.parent, text=riepilogo_txt,
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small).pack(pady=(0, 12))
        self._status = tk.Label(self.parent, text="",
                                bg=c["sfondo"], fg=c["stato_avviso"],
                                font=self._f_btn).pack(pady=20)
        # _anima_status si aspetta self._status come Label
        self._status = self.parent.children[list(
            self.parent.children.keys())[-1]]
        self._anima_status("L'ingegnere sta analizzando i tempi", 0)

        def _worker():
            try:
                risposta, errore = _chiama_claude(
                    prompt, api_key, SYSTEM_PROMPT_ANALIZZA_TEMPI)
            except Exception as e:
                risposta, errore = None, "Eccezione: %s" % e
            self.parent.after(0, lambda: self._fine_analisi(
                risposta, errore))
        threading.Thread(target=_worker, daemon=True).start()

    def _mostra_errore_inline(self, msg):
        """Pulisce e mostra un errore con bottone INDIETRO. Usato dalle
        schermate che potrebbero non avere _status piazzato."""
        c = self.c
        self._pulisci_parent()
        header = tk.Frame(self.parent, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< INDIETRO", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._build_scelta).pack(side="left")
        tk.Label(self.parent, text=msg,
                 bg=c["sfondo"], fg=c["stato_errore"],
                 font=self._f_btn,
                 wraplength=720, justify="center").pack(pady=40)

    # ── SCHERMATA 1bis: SETUP BASE - DOMANDE PISTA ────────────────────
    def _build_setup_base(self):
        """Schermata domande sulla pista per il setup di partenza."""
        c = self.c
        self._pulisci_parent()
        self._risposte_base = {}
        self._btn_base = []

        # Header
        header = tk.Frame(self.parent, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< INDIETRO", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._build_scelta).pack(side="left")
        tk.Label(header, text="  DR. IA - Setup di partenza",
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(8, 0))
        tk.Frame(self.parent, bg=c["linee"], height=1).pack(
            fill="x", padx=10, pady=(4, 4))

        # Riepilogo info note (dal contesto, sola lettura)
        info_parts = []
        for k, label in (("pista", "Pista"),
                         ("ref_telai", "Telaio"),
                         ("ref_motori", "Motore")):
            v = str(self.contesto.get(k, "")).strip()
            if v:
                info_parts.append("%s: %s" % (label, v[:18]))
        for k, label in (("temp_pista", "T.pista"),
                         ("temp_esterna", "T.est"),
                         ("condizioni_pista", "Cond.")):
            v = str(self.contesto.get(k, "")).strip()
            if v:
                info_parts.append("%s: %s" % (label, v[:14]))
        if info_parts:
            tk.Label(self.parent, text="  |  ".join(info_parts),
                     bg=c["sfondo"], fg=c["testo_dim"],
                     font=self._f_small).pack(pady=(0, 4))

        tk.Label(self.parent,
                 text="Rispondi alle domande sulla pista. Una scelta "
                      "per gruppo. Quando tutte sono fatte, premi "
                      "ANALIZZA.",
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small,
                 wraplength=720, justify="center").pack(pady=(2, 6))

        # Body scrollabile
        scroll_cont = tk.Frame(self.parent, bg=c["sfondo"])
        scroll_cont.pack(fill="both", expand=True, padx=18, pady=(0, 4))
        canvas = tk.Canvas(scroll_cont, bg=c["sfondo"],
                           highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(scroll_cont, orient="vertical",
                          command=canvas.yview)
        sb.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=sb.set)
        inner = tk.Frame(canvas, bg=c["sfondo"])
        canvas.create_window((0, 0), window=inner, anchor="nw")

        bottoni_navigabili = []
        for chiave, label, opzioni in DOMANDE_SETUP_BASE:
            tk.Label(inner, text=label.upper(), bg=c["sfondo"],
                     fg=c["cerca_testo"], font=self._f_btn,
                     anchor="w").pack(anchor="w", pady=(8, 2))
            tk.Frame(inner, bg=c["linee"], height=1).pack(
                fill="x", pady=(0, 4))
            grid = tk.Frame(inner, bg=c["sfondo"])
            grid.pack(fill="x")
            for i, opzione in enumerate(opzioni):
                row, col = divmod(i, 2)
                b = tk.Button(grid, text=opzione,
                              font=self._f_label, width=30,
                              bg=c["pulsanti_sfondo"],
                              fg=c["pulsanti_testo"],
                              relief="ridge", bd=1, cursor="hand2",
                              anchor="w", justify="left",
                              wraplength=240,
                              command=lambda k=chiave, o=opzione:
                                      self._toggle_base(k, o))
                b.grid(row=row, column=col, padx=4, pady=2,
                       sticky="ew")
                self._btn_base.append((chiave, opzione, b))
                bottoni_navigabili.append(b)
            grid.columnconfigure(0, weight=1)
            grid.columnconfigure(1, weight=1)

        inner.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(
                            int(-1 * (e.delta / 120)), "units"))

        # Status + barra bottoni
        self._status = tk.Label(self.parent, text="",
                                bg=c["sfondo"], fg=c["testo_dim"],
                                font=self._f_small,
                                wraplength=720, justify="center")
        self._status.pack(pady=(2, 4))

        btn_bar = tk.Frame(self.parent, bg=c["sfondo"])
        btn_bar.pack(pady=(2, 8))
        self._btn_back = tk.Button(btn_bar, text="< INDIETRO",
                                   font=self._f_btn,
                                   bg=c["pulsanti_sfondo"],
                                   fg=c["pulsanti_testo"],
                                   relief="ridge", bd=1, cursor="hand2",
                                   width=14,
                                   command=self._build_scelta)
        self._btn_back.pack(side="left", padx=8)
        self._btn_analizza = tk.Button(btn_bar, text="ANALIZZA con IA",
                                       font=self._f_btn,
                                       bg=c["pulsanti_sfondo"],
                                       fg=c["stato_avviso"],
                                       relief="ridge", bd=1,
                                       cursor="hand2", width=20,
                                       state="disabled",
                                       command=self._lancia_analisi_base)
        self._btn_analizza.pack(side="left", padx=8)

        # Navigazione tastiera
        if _HAS_UI_BTN:
            try:
                _setup_bottoni(bottoni_navigabili, orizzontale=False)
            except Exception:
                pass
            try:
                _setup_bottoni([self._btn_back, self._btn_analizza],
                               orizzontale=True)
            except Exception:
                pass
        if bottoni_navigabili:
            try:
                self.parent.after(150,
                                  lambda b=bottoni_navigabili[0]:
                                  b.focus_set())
            except Exception:
                pass

    def _toggle_base(self, chiave, opzione):
        """Selezione esclusiva all'interno di un gruppo di domande
        (radio-button-like). Marker '●' sul bottone selezionato."""
        marker = chr(0x25CF) + " "
        # Aggiorna stato logico
        if self._risposte_base.get(chiave) == opzione:
            self._risposte_base.pop(chiave, None)
        else:
            self._risposte_base[chiave] = opzione
        # Aggiorna i bottoni
        for k, o, b in self._btn_base:
            try:
                if k == chiave:
                    if self._risposte_base.get(chiave) == o:
                        b.config(text=marker + o, font=self._f_btn)
                    else:
                        b.config(text=o, font=self._f_label)
            except tk.TclError:
                pass
        # Abilita ANALIZZA solo se TUTTE le domande hanno risposta
        tutte = all(self._risposte_base.get(k)
                    for k, _l, _o in DOMANDE_SETUP_BASE)
        try:
            stato_old = str(self._btn_analizza["state"])
            self._btn_analizza.config(state="normal" if tutte
                                       else "disabled")
            stato_new = str(self._btn_analizza["state"])
            if stato_old != stato_new and _HAS_UI_BTN and self._btn_back:
                try:
                    _setup_bottoni([self._btn_back, self._btn_analizza],
                                   orizzontale=True)
                except Exception:
                    pass
        except tk.TclError:
            pass

    def _lancia_analisi_base(self):
        """Avvia chiamata Claude con prompt 'Setup base'."""
        if _chiama_claude is None:
            self._mostra_errore("Modulo ai_analisi non disponibile.")
            return
        api_key = _get_api_key()
        if not api_key:
            self._mostra_errore(
                "API key Anthropic non configurata.\n"
                "Inserirla in CONF -> anthropic_api_key.")
            return
        # Tutte le domande devono avere risposta
        if not all(self._risposte_base.get(k)
                   for k, _l, _o in DOMANDE_SETUP_BASE):
            self._mostra_errore(
                "Rispondi a tutte le domande prima di analizzare.")
            return
        parametri_ia = _estrai_parametri_ia(self.record, self.table_def)
        if not parametri_ia:
            self._mostra_errore(
                "Nessun campo del setup e' marcato per l'analisi IA "
                "(flag ;A nel .def).")
            return
        prompt = _costruisci_prompt_setup_base(
            self._risposte_base, parametri_ia, self.contesto)
        try:
            self._btn_analizza.config(state="disabled",
                                      text="...analizzo...")
        except tk.TclError:
            pass
        self._anima_status("L'ingegnere sta stilando il setup", 0)

        def _worker():
            try:
                risposta, errore = _chiama_claude(
                    prompt, api_key, SYSTEM_PROMPT_SETUP_BASE)
            except Exception as e:
                risposta, errore = None, "Eccezione: %s" % e
            self.parent.after(0, lambda: self._fine_analisi(
                risposta, errore))
        threading.Thread(target=_worker, daemon=True).start()

    # ── SCHERMATA 1: SINTOMI ──────────────────────────────────────────
    def _build_sintomi(self):
        c = self.c
        self._pulisci_parent()

        # Header
        header = tk.Frame(self.parent, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text="< MENU", font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=self._build_scelta).pack(side="left")
        tk.Label(header, text="  DR. IA - Cosa non va in pista",
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(8, 0))
        tk.Frame(self.parent, bg=c["linee"], height=1).pack(
            fill="x", padx=10, pady=(4, 4))

        # Riepilogo setup base
        info_parts = []
        for k, label in (("pista", "Pista"), ("ref_telai", "Telaio"),
                         ("ref_motori", "Motore")):
            v = str(self.contesto.get(k, "")).strip()
            if v:
                info_parts.append("%s: %s" % (label, v[:18]))
        data_rec = self.record.get("Data", "") or self.record.get("Data_Prova", "")
        if data_rec:
            info_parts.append("Data: %s" % data_rec)
        if info_parts:
            tk.Label(self.parent, text="  |  ".join(info_parts),
                     bg=c["sfondo"], fg=c["testo_dim"],
                     font=self._f_small).pack(pady=(0, 4))

        # Help
        tk.Label(self.parent,
                 text="Seleziona UN sintomo (clic per attivare/disattivare). "
                      "Il sintomo attivo e' evidenziato in rosso.",
                 bg=c["sfondo"], fg=c["testo_dim"],
                 font=self._f_small,
                 wraplength=720, justify="center").pack(pady=(0, 6))

        # Area scrollabile per i sintomi (su uconsole servono ~18 bottoni)
        scroll_cont = tk.Frame(self.parent, bg=c["sfondo"])
        scroll_cont.pack(fill="both", expand=True, padx=18, pady=(0, 4))
        canvas = tk.Canvas(scroll_cont, bg=c["sfondo"], highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(scroll_cont, orient="vertical",
                          command=canvas.yview)
        sb.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=sb.set)
        inner = tk.Frame(canvas, bg=c["sfondo"])
        canvas.create_window((0, 0), window=inner, anchor="nw")

        # Pulsanti raggruppati per categoria, 2 colonne.
        # I bottoni di TUTTI i gruppi finiscono in una unica lista piatta
        # cosi' setup_griglia configura la navigazione frecce 4 direzioni
        # in modo coerente (su/giu' attraversa anche le sezioni).
        self._btn_sintomi = []
        bottoni_flat = []  # solo i tk.Button, in ordine di lettura riga-x-riga
        for gruppo, sintomi in SINTOMI_GUIDA:
            tk.Label(inner, text=gruppo, bg=c["sfondo"],
                     fg=c["cerca_testo"], font=self._f_btn,
                     anchor="w").pack(anchor="w", pady=(8, 2))
            tk.Frame(inner, bg=c["linee"], height=1).pack(
                fill="x", pady=(0, 4))
            grid = tk.Frame(inner, bg=c["sfondo"])
            grid.pack(fill="x")
            # Per garantire 2 colonne piene anche con sezioni dispari,
            # se la sezione ha un numero dispari aggiungo un placeholder.
            for i, sintomo in enumerate(sintomi):
                row, col = divmod(i, 2)
                b = tk.Button(grid, text=sintomo,
                              font=self._f_label, width=30,
                              bg=c["pulsanti_sfondo"],
                              fg=c["pulsanti_testo"],
                              relief="ridge", bd=1, cursor="hand2",
                              anchor="w", justify="left",
                              wraplength=240,
                              command=lambda s=sintomo: self._toggle_sintomo(s))
                b.grid(row=row, column=col, padx=4, pady=2,
                       sticky="ew")
                self._btn_sintomi.append((sintomo, b))
                bottoni_flat.append(b)
            # Pareggia la riga se il gruppo ha numero dispari di sintomi
            if len(sintomi) % 2 == 1:
                bottoni_flat.append(None)
            grid.columnconfigure(0, weight=1)
            grid.columnconfigure(1, weight=1)
        # Conserva la lista piatta (con eventuali None) per l'attivazione
        # successiva della navigazione griglia, dopo che i bottoni della
        # barra finale sono stati creati.
        self._bottoni_flat_sintomi = bottoni_flat

        # Note libere
        if RetroField:
            tk.Label(inner, text="NOTE LIBERE (opzionale)",
                     bg=c["sfondo"], fg=c["cerca_testo"],
                     font=self._f_btn,
                     anchor="w").pack(anchor="w", pady=(10, 2))
            tk.Frame(inner, bg=c["linee"], height=1).pack(
                fill="x", pady=(0, 4))
            self._note_field = RetroField(inner, label="Note",
                                          tipo="S", lunghezza=40,
                                          label_width=8)
            self._note_field.pack(pady=2, anchor="w", fill="x")

        # Aggiorna scrollregion dopo che i widget sono stati posati
        inner.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(
                            int(-1 * (e.delta / 120)), "units"))

        # Status + barra bottoni
        self._status = tk.Label(self.parent, text="",
                                bg=c["sfondo"], fg=c["testo_dim"],
                                font=self._f_small,
                                wraplength=720, justify="center")
        self._status.pack(pady=(2, 4))

        btn_bar = tk.Frame(self.parent, bg=c["sfondo"])
        btn_bar.pack(pady=(2, 8))
        self._btn_back = tk.Button(btn_bar, text="< INDIETRO",
                                   font=self._f_btn,
                                   bg=c["pulsanti_sfondo"],
                                   fg=c["pulsanti_testo"],
                                   relief="ridge", bd=1, cursor="hand2",
                                   width=14,
                                   command=self._build_scelta)
        self._btn_back.pack(side="left", padx=8)
        self._btn_analizza = tk.Button(btn_bar, text="ANALIZZA con IA",
                                       font=self._f_btn,
                                       bg=c["pulsanti_sfondo"],
                                       fg=c["stato_avviso"],
                                       relief="ridge", bd=1,
                                       cursor="hand2", width=20,
                                       state="disabled",
                                       command=self._lancia_analisi)
        self._btn_analizza.pack(side="left", padx=8)

        # ── Navigazione tastiera (TAB / frecce / Enter) ───────────────
        # 1) I 18 bottoni dei sintomi: navigazione lineare verticale.
        #    Up/Down si sposta nell'ordine di lettura (sx-prima, dx-dopo).
        #    Enter su un bottone evidenziato lo attiva (toggle del sintomo).
        # 2) La coppia INDIETRO + ANALIZZA: navigazione orizzontale.
        # 3) TAB segue l'ordine di creazione dei widget (Tk gestisce
        #    automaticamente il giro: sintomi -> Note -> back -> analizza).
        bottoni_sintomi_validi = [b for b in self._bottoni_flat_sintomi
                                  if b is not None]
        if _HAS_UI_BTN:
            try:
                _setup_bottoni(bottoni_sintomi_validi, orizzontale=False)
            except Exception:
                pass
            try:
                _setup_bottoni([self._btn_back, self._btn_analizza],
                               orizzontale=True)
            except Exception:
                pass

        # Focus iniziale sul primo bottone-sintomo, cosi' l'utente puo'
        # iniziare a navigare con frecce/TAB/Enter senza toccare il mouse.
        if bottoni_sintomi_validi:
            try:
                self.parent.after(150,
                                  lambda b=bottoni_sintomi_validi[0]:
                                  b.focus_set())
            except Exception:
                pass

    def _toggle_sintomo(self, sintomo):
        """Selezione esclusiva: Enter/click su un sintomo deseleziona
        il precedente. Riselezione dello stesso lo disattiva.

        Per non interferire con il sistema di focus visivo (che cachea
        i colori originali al primo FocusIn e li ripristina al FocusOut)
        NON modifichiamo bg/fg del bottone: marchiamo il sintomo attivo
        cambiando il TESTO con un prefisso 'pallino' e il font in
        grassetto. Cosi' lo stato del toggle e' indipendente dal focus."""
        nuovo = None if self._sintomo_sel == sintomo else sintomo
        self._sintomo_sel = nuovo
        marker = chr(0x25CF) + " "  # ● seguito da spazio
        for s, b in self._btn_sintomi:
            try:
                if s == nuovo:
                    b.config(text=marker + s, font=self._f_btn)
                else:
                    b.config(text=s, font=self._f_label)
            except tk.TclError:
                pass
        # Riabilita / disabilita il bottone ANALIZZA. Quando passa da
        # disabilitato a normale, ri-applica i binding di navigazione
        # (setup_bottoni ignora i bottoni disabilitati al primo giro).
        try:
            stato_old = str(self._btn_analizza["state"])
            self._btn_analizza.config(
                state="normal" if nuovo else "disabled")
            stato_new = str(self._btn_analizza["state"])
            if stato_old != stato_new and _HAS_UI_BTN and self._btn_back:
                try:
                    _setup_bottoni([self._btn_back, self._btn_analizza],
                                   orizzontale=True)
                except Exception:
                    pass
        except tk.TclError:
            pass

    # ── CHIAMATA IA ───────────────────────────────────────────────────
    def _lancia_analisi(self):
        if not self._sintomo_sel:
            return
        if _chiama_claude is None:
            self._mostra_errore("Modulo ai_analisi non disponibile.")
            return
        api_key = _get_api_key()
        if not api_key:
            self._mostra_errore(
                "API key Anthropic non configurata.\n"
                "Inserirla in CONF -> anthropic_api_key.")
            return

        # Prepara prompt
        parametri_ia = _estrai_parametri_ia(self.record, self.table_def)
        if not parametri_ia:
            self._mostra_errore(
                "Nessun campo del setup e' marcato per l'analisi IA "
                "(flag ;A nel .def).")
            return
        note = ""
        if self._note_field:
            try:
                note = self._note_field.get().strip()
            except Exception:
                note = ""
        prompt = _costruisci_prompt(self._sintomo_sel, note,
                                    parametri_ia, self.contesto)

        # Disabilita UI durante l'attesa
        try:
            self._btn_analizza.config(state="disabled",
                                      text="...analizzo...")
        except tk.TclError:
            pass
        self._anima_status("L'ingegnere sta studiando", 0)

        # Thread per non bloccare la UI
        def _worker():
            try:
                risposta, errore = _chiama_claude(
                    prompt, api_key, SYSTEM_PROMPT_DR_IA)
            except Exception as e:
                risposta, errore = None, "Eccezione: %s" % e
            # Torna nel thread Tkinter
            self.parent.after(0, lambda: self._fine_analisi(
                risposta, errore))
        threading.Thread(target=_worker, daemon=True).start()

    def _anima_status(self, testo_base, step):
        """Animazione 'ingegnere sta studiando...' con puntini."""
        try:
            dots = "." * (step % 4)
            self._status.config(
                text=testo_base + dots,
                fg=self.c["stato_avviso"])
            self._anima_id = self.parent.after(
                500, lambda: self._anima_status(testo_base, step + 1))
        except tk.TclError:
            pass

    def _stop_animazione(self):
        if self._anima_id is not None:
            try:
                self.parent.after_cancel(self._anima_id)
            except Exception:
                pass
            self._anima_id = None

    def _fine_analisi(self, risposta, errore):
        self._stop_animazione()
        if errore:
            self._mostra_errore(errore)
            try:
                self._btn_analizza.config(state="normal",
                                          text="ANALIZZA con IA")
            except tk.TclError:
                pass
            return
        data, errp = _parse_risposta_ia(risposta)
        if not data:
            self._mostra_errore(
                "Risposta IA non interpretabile:\n%s\n\n%s"
                % (errp or "?", (risposta or "")[:300]))
            try:
                self._btn_analizza.config(state="normal",
                                          text="ANALIZZA con IA")
            except tk.TclError:
                pass
            return
        # OK: passa alla schermata di confronto
        self._risposta_ia = data
        self._build_confronto()

    def _mostra_errore(self, msg):
        try:
            self._status.config(text=msg, fg=self.c["stato_errore"])
        except tk.TclError:
            pass

    # ── SCHERMATA 2: CONFRONTO VECCHIO/NUOVO ──────────────────────────
    def _build_confronto(self):
        c = self.c
        self._pulisci_parent()
        modifiche = self._risposta_ia.get("modifiche") or {}
        spiegazione = self._risposta_ia.get("spiegazione") or ""
        # Se le modifiche non sono un dict (l'IA ha inventato), aborto
        if not isinstance(modifiche, dict):
            self._risposta_ia = None
            self._build_sintomi()
            self._mostra_errore("L'IA non ha proposto modifiche valide.")
            return

        # ── CLAMP AUTOMATICO ─────────────────────────────────────────
        # Filtra le modifiche dove il rapporto valore_nuovo/valore_attuale
        # eccede ~3.5x: l'IA continua a proporre valori "tipici" invece
        # di rispettare quelli del pilota. Tagliamo prima di mostrare.
        modifiche_scartate = []
        modifiche_filtrate = {}
        for nome, val_new in modifiche.items():
            val_old = str(self.record.get(nome, "")).strip()
            if val_old and _valore_sospetto(val_old, str(val_new)):
                modifiche_scartate.append(
                    "%s (era %s, IA proponeva %s)"
                    % (nome.replace("_", " "), val_old, val_new))
                continue
            modifiche_filtrate[nome] = val_new
        modifiche = modifiche_filtrate

        # Determina schermata di "ritorno" in base alla modalita' attiva.
        # Se siamo arrivati qui da "Setup base", tornare alle domande
        # ha piu' senso che tornare ai sintomi (e viceversa).
        if self._modalita == "setup_base":
            torna_label = "< DOMANDE"
            torna_func = self._build_setup_base
            riepilogo_txt = self._riepilogo_setup_base()
        elif self._modalita == "tempi":
            torna_label = "< MENU"
            torna_func = self._build_scelta
            ses_validi = [s for s in self.tempi_sessioni
                          if s and s.get("giri")]
            n_ses = len(ses_validi)
            n_giri = sum(len(s.get("giri", [])) for s in ses_validi)
            if n_ses <= 1:
                riepilogo_txt = "Analisi tempi: %d giri" % n_giri
            else:
                riepilogo_txt = ("Analisi %d sessioni (%d giri totali)"
                                 % (n_ses, n_giri))
        else:
            torna_label = "< SINTOMI"
            torna_func = self._build_sintomi
            riepilogo_txt = "Sintomo: %s" % (self._sintomo_sel or "?")

        # Header
        header = tk.Frame(self.parent, bg=c["sfondo"])
        header.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(header, text=torna_label, font=self._f_small,
                  bg=c["pulsanti_sfondo"], fg=c["pulsanti_testo"],
                  relief="ridge", bd=1, cursor="hand2",
                  command=torna_func).pack(side="left")
        titolo_centro = ("DR. IA - Setup base proposto"
                         if self._modalita == "setup_base"
                         else "DR. IA - Setup proposto")
        tk.Label(header, text="  " + titolo_centro,
                 bg=c["sfondo"], fg=c["dati"],
                 font=self._f_title).pack(side="left", padx=(8, 0))
        tk.Frame(self.parent, bg=c["linee"], height=1).pack(
            fill="x", padx=10, pady=(4, 4))

        # Riepilogo selezione (sintomo o risposte setup base)
        tk.Label(self.parent,
                 text=riepilogo_txt,
                 bg=c["sfondo"], fg=c["stato_avviso"],
                 font=self._f_btn,
                 wraplength=720, justify="left").pack(pady=(0, 4))

        # Tabella confronto
        scroll_cont = tk.Frame(self.parent, bg=c["sfondo"])
        scroll_cont.pack(fill="both", expand=True, padx=14, pady=(0, 4))
        canvas = tk.Canvas(scroll_cont, bg=c["sfondo"], highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(scroll_cont, orient="vertical",
                          command=canvas.yview)
        sb.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=sb.set)
        inner = tk.Frame(canvas, bg=c["sfondo"])
        canvas.create_window((0, 0), window=inner, anchor="nw")

        # Itera i campi IA in ordine, mostra solo quelli rilevanti
        n_modificati = 0
        sezioni_map = getattr(self.table_def, "sezioni", {}) or {}
        sezione_corrente = ""
        ultima_sez_stampata = None
        for campo_def in self.table_def.campi:
            nome = campo_def["nome"]
            if nome in sezioni_map:
                sezione_corrente = sezioni_map[nome]
            if not campo_def.get("analisi_ia"):
                continue
            val_old = str(self.record.get(nome, "")).strip()
            val_new = modifiche.get(nome)
            if val_new is None:
                continue  # non toccato
            val_new = str(val_new).strip()
            cambiato = (val_new != val_old)
            if cambiato:
                n_modificati += 1
            # Stampa intestazione sezione la prima volta che la incontro
            if sezione_corrente and sezione_corrente != ultima_sez_stampata:
                tk.Label(inner, text=sezione_corrente,
                         bg=c["sfondo"], fg=c["cerca_testo"],
                         font=self._f_btn,
                         anchor="w").pack(anchor="w", pady=(8, 2))
                tk.Frame(inner, bg=c["linee"], height=1).pack(
                    fill="x", pady=(0, 2))
                ultima_sez_stampata = sezione_corrente
            # Riga campo: nome | old -> new
            row = tk.Frame(inner, bg=c["sfondo"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=nome.replace("_", " "),
                     bg=c["sfondo"], fg=c["label"],
                     font=self._f_label, width=22,
                     anchor="w").pack(side="left")
            tk.Label(row, text=val_old or "-",
                     bg=c["sfondo"], fg=c["testo_dim"],
                     font=self._f_info,
                     width=10, anchor="w").pack(side="left")
            tk.Label(row, text="->",
                     bg=c["sfondo"], fg=c["testo_dim"],
                     font=self._f_info,
                     width=3).pack(side="left")
            fg_new = c["stato_errore"] if cambiato else c["testo_dim"]
            font_new = self._f_btn if cambiato else self._f_info
            tk.Label(row, text=val_new or "-",
                     bg=c["sfondo"], fg=fg_new,
                     font=font_new,
                     width=10, anchor="w").pack(side="left")
            if cambiato:
                # Avviso visivo se il salto di valore e' anomalo
                # (>3.5x o <1/3.5 rispetto all'originale).
                sospetto = _valore_sospetto(val_old, val_new)
                if sospetto:
                    tk.Label(row, text=" * MODIFICATO  ⚠ ORDINE DI "
                                       "GRANDEZZA INSOLITO - controlla!",
                             bg=c["sfondo"], fg=c["stato_errore"],
                             font=self._f_small).pack(side="left")
                else:
                    tk.Label(row, text=" * MODIFICATO",
                             bg=c["sfondo"], fg=c["stato_errore"],
                             font=self._f_small).pack(side="left")

        if n_modificati == 0:
            tk.Label(inner,
                     text="L'IA non ha proposto alcuna modifica.",
                     bg=c["sfondo"], fg=c["stato_avviso"],
                     font=self._f_btn).pack(pady=10)

        # Spiegazione IA
        if spiegazione:
            tk.Label(inner, text="SPIEGAZIONE",
                     bg=c["sfondo"], fg=c["cerca_testo"],
                     font=self._f_btn,
                     anchor="w").pack(anchor="w", pady=(12, 2))
            tk.Frame(inner, bg=c["linee"], height=1).pack(
                fill="x", pady=(0, 2))
            tk.Label(inner, text=spiegazione,
                     bg=c["sfondo"], fg=c["dati"],
                     font=self._f_label,
                     wraplength=700, justify="left",
                     anchor="w").pack(anchor="w", pady=(2, 8))

        inner.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(
                            int(-1 * (e.delta / 120)), "units"))

        # Barra bottoni
        btn_bar = tk.Frame(self.parent, bg=self.c["sfondo"])
        btn_bar.pack(pady=(2, 8))
        btn_indietro = tk.Button(btn_bar, text=torna_label,
                                 font=self._f_btn,
                                 bg=c["pulsanti_sfondo"],
                                 fg=c["pulsanti_testo"],
                                 relief="ridge", bd=1, cursor="hand2",
                                 width=12,
                                 command=torna_func)
        btn_indietro.pack(side="left", padx=6)
        btn_salva = None
        if n_modificati > 0:
            btn_salva = tk.Button(btn_bar, text="SALVA NUOVO SETUP",
                                  font=self._f_btn,
                                  bg=c["pulsanti_sfondo"],
                                  fg=c["stato_ok"],
                                  relief="ridge", bd=1, cursor="hand2",
                                  width=22,
                                  command=lambda: self._salva(modifiche,
                                                              spiegazione))
            btn_salva.pack(side="left", padx=6)
        btn_scarta = tk.Button(btn_bar, text="SCARTA", font=self._f_btn,
                               bg=c["pulsanti_sfondo"],
                               fg=c["stato_errore"],
                               relief="ridge", bd=1, cursor="hand2",
                               width=10, command=self._back)
        btn_scarta.pack(side="left", padx=6)

        # ── Navigazione tastiera barra bottoni (frecce sx/dx + Enter) ─
        bar_btns = [btn_indietro]
        if btn_salva is not None:
            bar_btns.append(btn_salva)
        bar_btns.append(btn_scarta)
        if _HAS_UI_BTN:
            try:
                _setup_bottoni(bar_btns, orizzontale=True)
            except Exception:
                pass

        # Focus iniziale: sul bottone SALVA se c'e' una proposta da
        # accettare, altrimenti sul SINTOMI (per tornare indietro).
        focus_target = btn_salva if btn_salva is not None else btn_indietro
        try:
            self.parent.after(150, lambda b=focus_target: b.focus_set())
        except Exception:
            pass

    def _riepilogo_setup_base(self):
        """Stringa breve riassuntiva delle risposte 'setup base'.
        Es. 'Lenta | Liscia | Corti rettilinei'."""
        if not self._risposte_base:
            return "Setup base"
        # Compatto: prendi solo la prima parola (prima della parentesi)
        parti = []
        for chiave, _label, _opz in DOMANDE_SETUP_BASE:
            risp = self._risposte_base.get(chiave, "")
            if not risp:
                continue
            # 'Lenta (curve strette)' -> 'Lenta'
            parti.append(risp.split(" (")[0].strip())
        return "Setup base | " + " | ".join(parti) if parti else "Setup base"

    def _salva(self, modifiche, spiegazione):
        """Delega al callback di retrodb. Le modifiche sono un dict
        {NomeCampo: nuovo_valore} che retrodb applichera' come override
        durante la copia del setup base."""
        if callable(self._on_save):
            # 'sintomo' del callback diventa: per la modalita' diagnosi
            # il sintomo selezionato; per setup_base un riassunto delle
            # risposte (entrambi finiscono nella nota IA del nuovo record).
            if self._modalita == "setup_base":
                etichetta = self._riepilogo_setup_base()
            elif self._modalita == "tempi":
                ses_validi = [s for s in self.tempi_sessioni
                              if s and s.get("giri")]
                n_ses = len(ses_validi)
                n_giri = sum(len(s.get("giri", []))
                             for s in ses_validi)
                if n_ses <= 1:
                    etichetta = "Analisi tempi (%d giri)" % n_giri
                else:
                    etichetta = ("Analisi %d sessioni (%d giri)"
                                 % (n_ses, n_giri))
            else:
                etichetta = self._sintomo_sel or "?"
            try:
                self._on_save(modifiche, spiegazione, etichetta)
            except Exception as e:
                self._mostra_errore("Errore salvataggio: %s" % e)
