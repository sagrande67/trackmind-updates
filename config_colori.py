"""
Caricatore configurazione colori per Retro Database.
Legge colori.cfg e li rende disponibili come dizionario.
Se il file non esiste, usa i colori di default.
"""

__version__ = '05.04.4'

import os
import sys

# Font monospace: Consolas su Windows, DejaVu Sans Mono su Linux (uConsole/RPi)
if sys.platform == "win32":
    FONT_MONO = "Consolas"
else:
    FONT_MONO = "DejaVu Sans Mono"

# Colori di default
DEFAULT_COLORS = {
    "sfondo":              "#0a0a0a",
    "dati":                "#39ff14",
    "label":               "#22aa22",
    "puntini":             "#1a6a1a",
    "bordo_vuote":         "#144a14",
    "sfondo_celle":        "#080808",
    "sfondo_celle_piene":  "#0c120c",
    "separatori":          "#22882a",
    "cursore":             "#39ff14",
    "testo_cursore":       "#0a0a0a",
    "pulsanti_sfondo":     "#1a3a1a",
    "pulsanti_testo":      "#39ff14",
    "cancella_sfondo":     "#3a1a1a",
    "cancella_testo":      "#ff6666",
    "cerca_sfondo":        "#1a1a3a",
    "cerca_testo":         "#6688ff",
    "stato_ok":            "#39ff14",
    "stato_avviso":        "#ffaa00",
    "stato_errore":        "#ff5555",
    "testo_dim":           "#1a6a1a",
    "linee":               "#1a5a0a",
}

# Colori modalità notturna (rosso su nero)
NIGHT_COLORS = {
    "sfondo":              "#0a0a0a",
    "dati":                "#ff3333",
    "label":               "#aa2222",
    "puntini":             "#6a1a1a",
    "bordo_vuote":         "#4a1414",
    "sfondo_celle":        "#080808",
    "sfondo_celle_piene":  "#120c0c",
    "separatori":          "#882222",
    "cursore":             "#ff3333",
    "testo_cursore":       "#0a0a0a",
    "pulsanti_sfondo":     "#3a1a1a",
    "pulsanti_testo":      "#ff3333",
    "cancella_sfondo":     "#3a1a1a",
    "cancella_testo":      "#ff6666",
    "cerca_sfondo":        "#1a1a3a",
    "cerca_testo":         "#6688ff",
    "stato_ok":            "#ff3333",
    "stato_avviso":        "#ffaa00",
    "stato_errore":        "#ff5555",
    "testo_dim":           "#6a1a1a",
    "linee":               "#5a0a0a",
}

_config_path = None
_colors = None


def _find_config():
    """Trova il percorso di colori.cfg."""
    import sys
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    cfg = os.path.join(base, "colori.cfg")
    return cfg


def carica_colori(force=False):
    """Carica i colori dal file .cfg. Ritorna un dizionario."""
    global _colors, _config_path
    if _colors and not force:
        return _colors

    _config_path = _find_config()
    _colors = DEFAULT_COLORS.copy()

    if os.path.exists(_config_path):
        try:
            with open(_config_path, "r", encoding="utf-8") as f:
                for riga in f:
                    riga = riga.strip()
                    if not riga or riga.startswith("#"):
                        continue
                    if "=" in riga:
                        chiave, valore = riga.split("=", 1)
                        chiave = chiave.strip()
                        valore = valore.strip()
                        if chiave in _colors and valore.startswith("#"):
                            _colors[chiave] = valore
        except Exception as e:
            print(f"[AVVISO] Errore lettura colori.cfg: {e}")

    return _colors


def salva_colori(colori):
    """Salva i colori nel file .cfg."""
    global _colors
    _colors = colori

    path = _find_config()

    # Descrizioni per ogni chiave
    desc = {
        "sfondo":              "Sfondo generale dell'applicazione",
        "dati":                "Dati inseriti dall'utente (testo brillante)",
        "label":               "Label dei campi (nome campo)",
        "puntini":             "Puntini placeholder (celle vuote)",
        "bordo_vuote":         "Bordo celle vuote",
        "sfondo_celle":        "Sfondo celle vuote",
        "sfondo_celle_piene":  "Sfondo celle con dato inserito",
        "separatori":          "Separatori (/ nelle date, : nelle ore)",
        "cursore":             "Cursore (quadratino pieno)",
        "testo_cursore":       "Testo sul cursore",
        "pulsanti_sfondo":     "Pulsanti - sfondo",
        "pulsanti_testo":      "Pulsanti - testo",
        "cancella_sfondo":     "Pulsante cancella - sfondo",
        "cancella_testo":      "Pulsante cancella - testo",
        "cerca_sfondo":        "Pulsante cerca - sfondo",
        "cerca_testo":         "Pulsante cerca - testo",
        "stato_ok":            "Barra di stato OK",
        "stato_avviso":        "Barra di stato avviso",
        "stato_errore":        "Barra di stato errore",
        "testo_dim":           "Testo dimmed (info secondarie)",
        "linee":               "Linee separatrici",
    }

    with open(path, "w", encoding="utf-8") as f:
        f.write("# RETRO DATABASE - Configurazione Colori\n")
        f.write("# Modificato dall'app\n\n")
        for chiave in DEFAULT_COLORS:
            valore = colori.get(chiave, DEFAULT_COLORS[chiave])
            d = desc.get(chiave, "")
            f.write(f"# {d}\n")
            f.write(f"{chiave}={valore}\n\n")


def get_config_path():
    """Restituisce il percorso del file colori.cfg."""
    return _find_config()
