"""
TrackMind - Meteo v1.0
Modulo meteo automatico: geocoding indirizzo pista + meteo corrente.

Usa API gratuite senza chiave:
  - Nominatim (OpenStreetMap) per geocoding indirizzo → lat/lon
  - Open-Meteo per meteo corrente → temperatura, umidità, condizioni

Nessuna dipendenza esterna (usa urllib).
"""

import json, os, sys, urllib.request, urllib.error, time

# ─────────────────────────────────────────────────────────────────────
#  CACHE GEOCODING (evita chiamate ripetute per la stessa pista)
# ─────────────────────────────────────────────────────────────────────
_geo_cache = {}
_GEO_CACHE_FILE = None

def _get_cache_path():
    global _GEO_CACHE_FILE
    if _GEO_CACHE_FILE:
        return _GEO_CACHE_FILE
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _GEO_CACHE_FILE = os.path.join(base, "dati", "geo_cache.json")
    return _GEO_CACHE_FILE

def _carica_cache():
    global _geo_cache
    try:
        path = _get_cache_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                _geo_cache = json.load(f)
    except Exception:
        _geo_cache = {}

def _salva_cache():
    try:
        path = _get_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_geo_cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# Carica cache all'import
_carica_cache()


# ─────────────────────────────────────────────────────────────────────
#  GEOCODING (indirizzo → lat/lon)
# ─────────────────────────────────────────────────────────────────────
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

def _geocode_query(query):
    """Esegue una singola query Nominatim. Ritorna (lat, lon) o (None, None)."""
    cache_key = query.lower().strip()
    if cache_key in _geo_cache:
        c = _geo_cache[cache_key]
        return c.get("lat"), c.get("lon")
    try:
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "limit": "1",
        })
        url = "%s?%s" % (NOMINATIM_URL, params)
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "TrackMind/5.4 (RC Racing App)")
        time.sleep(1.1)
        with urllib.request.urlopen(req, timeout=10) as resp:
            results = json.loads(resp.read().decode("utf-8"))
        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            _geo_cache[cache_key] = {"lat": lat, "lon": lon, "display": results[0].get("display_name", "")}
            _salva_cache()
            return lat, lon
    except Exception as e:
        print("[METEO] Geocoding fallito per '%s': %s" % (query, e))
    return None, None


def geocode(indirizzo, citta="", nazione=""):
    """Converte indirizzo in coordinate lat/lon con fallback progressivo.
    Prova combinazioni via via piu' semplici se la query completa non trova nulla.
    Ritorna (lat, lon) o (None, None) se non trovato.
    Usa cache per evitare chiamate ripetute."""
    ind = (indirizzo or "").strip()
    cit = (citta or "").strip()
    naz = (nazione or "").strip()

    if not ind and not cit and not naz:
        return None, None

    # Costruisci lista di tentativi in ordine di precisione decrescente
    tentativi = []
    # 1) Query completa: indirizzo + citta + nazione
    full = ", ".join([p for p in [ind, cit, naz] if p])
    if full:
        tentativi.append(full)
    # 2) Solo citta + nazione (se diversa dalla query completa)
    if cit:
        q2 = ", ".join([p for p in [cit, naz] if p])
        if q2 != full:
            tentativi.append(q2)
    # 3) Solo indirizzo + nazione (senza citta)
    if ind and naz and cit:
        q3 = "%s, %s" % (ind, naz)
        if q3 not in tentativi:
            tentativi.append(q3)
    # 4) Solo nazione come ultimo fallback (almeno per avere coordinate generiche)
    # Non lo aggiungiamo: troppo impreciso per il meteo

    for query in tentativi:
        lat, lon = _geocode_query(query)
        if lat is not None:
            return lat, lon

    return None, None


# ─────────────────────────────────────────────────────────────────────
#  METEO CORRENTE (lat/lon → condizioni)
# ─────────────────────────────────────────────────────────────────────
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# Mappa codici WMO → condizioni pista
WMO_CONDIZIONI = {
    0: "Asciutta",      # Clear sky
    1: "Asciutta",      # Mainly clear
    2: "Asciutta",      # Partly cloudy
    3: "Asciutta",      # Overcast
    45: "Umida",        # Fog
    48: "Umida",        # Depositing rime fog
    51: "Umida",        # Light drizzle
    53: "Umida",        # Moderate drizzle
    55: "Bagnata",      # Dense drizzle
    56: "Bagnata",      # Light freezing drizzle
    57: "Bagnata",      # Dense freezing drizzle
    61: "Bagnata",      # Slight rain
    63: "Bagnata",      # Moderate rain
    65: "Acquazzone",   # Heavy rain
    66: "Bagnata",      # Light freezing rain
    67: "Acquazzone",   # Heavy freezing rain
    71: "Bagnata",      # Slight snow
    73: "Bagnata",      # Moderate snow
    75: "Acquazzone",   # Heavy snow
    77: "Bagnata",      # Snow grains
    80: "Bagnata",      # Slight rain showers
    81: "Bagnata",      # Moderate rain showers
    82: "Acquazzone",   # Violent rain showers
    85: "Bagnata",      # Slight snow showers
    86: "Acquazzone",   # Heavy snow showers
    95: "Acquazzone",   # Thunderstorm
    96: "Acquazzone",   # Thunderstorm with hail
    99: "Acquazzone",   # Thunderstorm with heavy hail
}

WMO_DESCRIZIONE = {
    0: "Sereno", 1: "Prevalentemente sereno", 2: "Parzialmente nuvoloso",
    3: "Coperto", 45: "Nebbia", 48: "Nebbia con brina",
    51: "Pioviggine leggera", 53: "Pioviggine", 55: "Pioviggine intensa",
    61: "Pioggia leggera", 63: "Pioggia", 65: "Pioggia forte",
    80: "Rovesci leggeri", 81: "Rovesci", 82: "Rovesci violenti",
    95: "Temporale", 96: "Temporale con grandine", 99: "Temporale forte",
}


def meteo_corrente(lat, lon):
    """Ottiene meteo corrente per le coordinate date.
    Ritorna dict con: temp_esterna, temp_pista (stimata), umidita,
    condizioni_pista, vento, descrizione.
    Ritorna None se fallisce."""
    try:
        params = urllib.parse.urlencode({
            "latitude": "%.4f" % lat,
            "longitude": "%.4f" % lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,surface_pressure",
            "timezone": "auto",
        })
        url = "%s?%s" % (OPENMETEO_URL, params)
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "TrackMind/5.4")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        current = data.get("current", {})
        temp = current.get("temperature_2m", 0)
        umidita = current.get("relative_humidity_2m", 0)
        wmo = current.get("weather_code", 0)
        vento_kmh = current.get("wind_speed_10m", 0)

        # Stima temperatura pista: +10-15°C rispetto aria se asciutta e soleggiata
        # Meno se nuvoloso/bagnata
        if wmo <= 3:
            temp_pista = temp + 12  # Soleggiato/nuvoloso
        elif wmo <= 48:
            temp_pista = temp + 5   # Nebbia
        else:
            temp_pista = temp + 2   # Pioggia

        condizioni = WMO_CONDIZIONI.get(wmo, "Asciutta")
        descrizione = WMO_DESCRIZIONE.get(wmo, "Codice %d" % wmo)

        # Vento in descrizione
        if vento_kmh < 5:
            vento_desc = "Calmo"
        elif vento_kmh < 15:
            vento_desc = "Leggero %.0f km/h" % vento_kmh
        elif vento_kmh < 30:
            vento_desc = "Moderato %.0f km/h" % vento_kmh
        else:
            vento_desc = "Forte %.0f km/h" % vento_kmh

        return {
            "temp_esterna": round(temp),
            "temp_pista": round(temp_pista),
            "umidita": round(umidita),
            "condizioni_pista": condizioni,
            "vento": vento_desc,
            "descrizione": descrizione,
            "wmo_code": wmo,
        }

    except Exception as e:
        print("[METEO] Fetch fallito: %s" % e)
        return None


# ─────────────────────────────────────────────────────────────────────
#  FUNZIONE PRINCIPALE (indirizzo → meteo completo)
# ─────────────────────────────────────────────────────────────────────
def meteo_da_indirizzo(indirizzo, citta="", nazione=""):
    """Funzione completa: indirizzo → geocoding → meteo corrente.
    Ritorna dict con dati meteo o None se fallisce."""
    lat, lon = geocode(indirizzo, citta, nazione)
    if lat is None:
        return None
    return meteo_corrente(lat, lon)


# ─────────────────────────────────────────────────────────────────────
#  TEST
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Test meteo TrackMind")
    print("=" * 40)
    result = meteo_da_indirizzo("Via Roma", "Castelfranco Veneto", "Italia")
    if result:
        print("Temp esterna:  %d C" % result["temp_esterna"])
        print("Temp pista:    %d C" % result["temp_pista"])
        print("Umidita:       %d%%" % result["umidita"])
        print("Condizioni:    %s" % result["condizioni_pista"])
        print("Vento:         %s" % result["vento"])
        print("Descrizione:   %s" % result["descrizione"])
    else:
        print("Meteo non disponibile")
