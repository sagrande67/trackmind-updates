#!/usr/bin/env python3
"""Sync manuale della tabella Miscela da roga.it"""
import os, sys, json

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

from addons.web_sync import _fetch_html, _html_to_testo, _chiedi_ai, _merge_records

DATI_FILE = os.path.join("dati", "miscela.json")
DEF_FILE = os.path.join("tabelle", "miscela.def")

# Leggi campi dal .def
campi_nomi = []
with open(DEF_FILE, "r", encoding="utf-8") as f:
    for riga in f:
        riga = riga.strip()
        if not riga or riga.startswith("#") or riga.startswith("!") or riga.startswith("@"):
            continue
        parti = riga.split(";")
        if len(parti) >= 3:
            campi_nomi.append(parti[0].strip())

print("Campi: %s" % ", ".join(campi_nomi))

# Fetch
print("Fetch http://www.roga.it/prodotti.asp ...")
html = _fetch_html("http://www.roga.it/prodotti.asp")
if not html:
    print("ERRORE: fetch fallito!")
    sys.exit(1)

testo = _html_to_testo(html)
print("Testo: %d char" % len(testo))

# IA (usa _chiedi_ai aggiornato con max_tokens=8192 e riparo JSON)
print("Chiamata IA in corso (30-40 secondi)...")
prodotti = _chiedi_ai(testo, "miscela", campi_nomi,
                       "Miscele e carburanti per motori a scoppio automodelli RC")
print("Prodotti IA: %d" % len(prodotti))

if not prodotti:
    print("Nessun prodotto trovato.")
    sys.exit(1)

# Carica dati esistenti
if os.path.exists(DATI_FILE):
    with open(DATI_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = data.get("records", [])
else:
    data = {"_meta": {"tabella": "miscela", "accesso": "tutti", "versione": "5.4"},
            "records": []}
    records = []

print("Record esistenti: %d" % len(records))

# Merge
campo_chiave = campi_nomi[0]
aggiornati, n_aggiunti, n_modificati = _merge_records(records, prodotti, campo_chiave)
print("Risultato: +%d nuovi, ~%d modificati" % (n_aggiunti, n_modificati))

if n_aggiunti > 0 or n_modificati > 0:
    data["records"] = aggiornati
    with open(DATI_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("SALVATO in %s (%d record totali)" % (DATI_FILE, len(aggiornati)))

print("\n--- Primi 5 prodotti ---")
for p in prodotti[:5]:
    print("  %s | %s | Nitro:%s | %s | %s" % (
        p.get("Codice", "?"), p.get("Marca_Miscela", "?"),
        p.get("Percentuale_Nitro", "?"), p.get("Formato", "?"),
        p.get("Prezzo", "?")))
