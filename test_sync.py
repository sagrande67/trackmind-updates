#!/usr/bin/env python3
# Test debug per sync miscela - verifica fetch + risposta IA
import os, json, re, urllib.request

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Leggi API key
with open('api_key.txt', 'r') as f:
    api_key = f.read().strip()
print('API key: %s...' % api_key[:8])

# Fetch pagina
from addons.web_sync import _fetch_html, _html_to_testo
html = _fetch_html('http://www.roga.it/prodotti.asp')
if not html:
    print('ERRORE: fetch fallito!')
    exit(1)

testo = _html_to_testo(html)
print('Testo: %d char' % len(testo))
print('--- Prime 300 char ---')
print(testo[:300])
print('---')

# Chiama AI manualmente per vedere risposta raw
campi_str = 'Codice, Marca_Miscela, Percentuale_Nitro, Percentuale_Olio, Tipo_Miscela, Formato, Prezzo, Note'
prompt = (
    'Sei un assistente di estrazione dati per un database di automodellismo RC. '
    'Analizza il testo e identifica le miscele/carburanti.\n'
    'Restituisci SOLO un array JSON con i campi: %s.\n'
    'Se un campo non e disponibile, usa stringa vuota.\n'
    'Restituisci massimo 10 prodotti come esempio.\n\n'
    'TESTO PAGINA:\n%s'
) % (campi_str, testo[:4000])

payload = json.dumps({
    'model': 'claude-sonnet-4-20250514',
    'max_tokens': 4096,
    'messages': [{'role': 'user', 'content': prompt}]
}).encode('utf-8')

req = urllib.request.Request(
    'https://api.anthropic.com/v1/messages',
    data=payload,
    headers={
        'Content-Type': 'application/json',
        'x-api-key': api_key,
        'anthropic-version': '2023-06-01',
    },
    method='POST',
)

try:
    print('Chiamata IA in corso...')
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    testo_r = ''
    for block in data.get('content', []):
        if block.get('type') == 'text':
            testo_r += block['text']
    print('RISPOSTA IA (%d char):' % len(testo_r))
    print(testo_r[:2000])

    # Prova a parsare come JSON
    testo_r = testo_r.strip()
    testo_r = re.sub(r'^```json\s*', '', testo_r)
    testo_r = re.sub(r'```$', '', testo_r).strip()
    try:
        prodotti = json.loads(testo_r)
        print('\nProdotti trovati: %d' % len(prodotti))
        for p in prodotti[:5]:
            print(p)
    except json.JSONDecodeError as je:
        print('\nERRORE JSON: %s' % je)
        print('Primi 200 char dopo cleanup:', testo_r[:200])

except Exception as e:
    print('ERRORE API: %s' % e)
