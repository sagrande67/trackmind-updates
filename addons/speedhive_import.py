"""
TrackMind - SpeedHive Import Module v1.0
Scarica i tempi di pratica da MyLaps SpeedHive e li salva
in formato compatibile con il LapTimer di TrackMind.

Uso standalone:
    python speedhive_import.py 7593075984
    python speedhive_import.py 7593075984 --sessione 6
    python speedhive_import.py https://speedhive.mylaps.com/practice/7593075984/activity
    python speedhive_import.py 7593075984 --salva --sessione 3 --dati-dir ./dati

Uso da codice:
    from speedhive_import import scarica_sessioni
    dati = scarica_sessioni("7593075984")
"""

import json
import os
import sys
import re
import argparse
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

API_BASE = "https://practice-api.speedhive.com/api/v1/training/activities"
USER_AGENT = "TrackMind/1.0 (SpeedHive Import)"


def _api_get(url):
    req = Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Origin", "https://speedhive.mylaps.com")
    req.add_header("Referer", "https://speedhive.mylaps.com/")
    try:
        with urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except (HTTPError, URLError, Exception):
        return None


def estrai_id(input_str):
    input_str = input_str.strip()
    if input_str.isdigit():
        return input_str
    match = re.search(r'/practice/(\d+)', input_str)
    if match:
        return match.group(1)
    match = re.search(r'(\d{8,})', input_str)
    if match:
        return match.group(1)
    return None


def _fmt(secondi_str):
    try:
        s = float(secondi_str)
        minuti = int(s) // 60
        sec = s - (minuti * 60)
        if minuti > 0:
            return "%d:%06.3f" % (minuti, sec)
        return "%.3f" % sec
    except (ValueError, TypeError):
        return str(secondi_str)


def _fmt_delta(delta):
    if delta == 0:
        return "BEST"
    return "%+.3f" % delta


LOCATIONS_BASE = "https://practice-api.speedhive.com/api/v1/locations"


def scarica_sessioni(activity_id):
    url = "%s/%s/sessions" % (API_BASE, activity_id)
    return _api_get(url)


def scarica_attivita_pista(location_id, count=500):
    url = "%s/%s/activities?count=%d&offset=0" % (LOCATIONS_BASE, location_id, count)
    return _api_get(url)


def cerca_attivita(location_id, transponder, data_setup, count=500):
    dati = scarica_attivita_pista(location_id, count)
    if not dati or "activities" not in dati:
        return None, None

    transponder = str(transponder).strip()

    try:
        ds = data_setup.strip()
        if "/" in ds:
            parti = ds.split("/")
            data_target = "%04d-%02d-%02d" % (int(parti[2]), int(parti[1]), int(parti[0]))
        elif "." in ds:
            parti = ds.split(".")
            data_target = "%04d-%02d-%02d" % (int(parti[2]), int(parti[1]), int(parti[0]))
        else:
            data_target = ds[:10]
    except Exception:
        return None, None

    for att in dati["activities"]:
        chip = str(att.get("chipCode", "")).strip()
        start = att.get("startTime", "")
        att_data = start[:10]
        label = att.get("chipLabel", "?")

        chip_match = (chip == transponder or transponder in chip or chip in transponder)
        if chip_match and att_data == data_target:
            aid = att.get("id", "")
            return str(aid), att

    return None, None


def cerca_tutte_attivita_per_data(location_id, data_setup, count=500):
    """Cerca TUTTE le attivita' di una pista in una data specifica.
    Ritorna lista di dict: [{activity_id, chipCode, chipLabel, startTime}, ...]"""
    dati = scarica_attivita_pista(location_id, count)
    if not dati or "activities" not in dati:
        return []

    try:
        ds = data_setup.strip()
        if "/" in ds:
            parti = ds.split("/")
            data_target = "%04d-%02d-%02d" % (int(parti[2]), int(parti[1]), int(parti[0]))
        elif "." in ds:
            parti = ds.split(".")
            data_target = "%04d-%02d-%02d" % (int(parti[2]), int(parti[1]), int(parti[0]))
        else:
            data_target = ds[:10]
    except Exception:
        return []

    risultati = []
    visti = set()  # Evita duplicati per chipCode
    for att in dati["activities"]:
        start = att.get("startTime", "")
        att_data = start[:10]
        if att_data != data_target:
            continue
        chip = str(att.get("chipCode", "")).strip()
        label = att.get("chipLabel", "?")
        aid = str(att.get("id", ""))
        if not chip or not aid:
            continue
        if chip in visti:
            continue
        visti.add(chip)
        risultati.append({
            "activity_id": aid,
            "chipCode": chip,
            "chipLabel": label,
            "startTime": start,
        })

    return risultati


def import_automatico(location_id, transponder, data_setup, ora_setup=""):
    activity_id, att = cerca_attivita(location_id, transponder, data_setup)
    if not activity_id:
        return None, None, None

    dati = scarica_sessioni(activity_id)
    if not dati or "sessions" not in dati:
        return None, None, activity_id

    sessione_match = None
    if ora_setup:
        try:
            ds = data_setup.strip()
            if "/" in ds:
                parti = ds.split("/")
                iso_data = "%04d-%02d-%02d" % (int(parti[2]), int(parti[1]), int(parti[0]))
            else:
                iso_data = ds[:10]

            setup_dt = datetime.fromisoformat("%sT%s:00" % (iso_data, ora_setup.strip()))

            min_diff = None
            for sess in dati["sessions"]:
                try:
                    st = sess["dateTimeStart"]
                    st_clean = re.sub(r'[+-]\d{2}:\d{2}$', '', st)
                    sh_dt = datetime.fromisoformat(st_clean)
                    diff = abs((sh_dt - setup_dt).total_seconds())
                    if min_diff is None or diff < min_diff:
                        min_diff = diff
                        sessione_match = sess
                except Exception:
                    pass
        except Exception:
            pass

    return dati, sessione_match, activity_id


def stampa_riepilogo(dati):
    if not dati or "sessions" not in dati:
        return
    # Solo per uso standalone


def stampa_sessione(dati, session_id):
    if not dati or "sessions" not in dati:
        return None
    sessione = None
    for s in dati["sessions"]:
        if s.get("id") == session_id:
            sessione = s
            break
    return sessione


def converti_sessione(dati, session_id, setup="", pilota="",
                      record_id="", dati_dir="", setup_snapshot=None):
    sessione = None
    for s in dati.get("sessions", []):
        if s.get("id") == session_id:
            sessione = s
            break

    if not sessione:
        return None, None

    laps_raw = sessione.get("laps", [])
    if not laps_raw:
        return None, None

    tempi = []
    for lap in laps_raw:
        try:
            tempi.append(float(lap["duration"]))
        except (ValueError, KeyError):
            pass

    if not tempi:
        return None, None

    best = min(tempi)
    media = sum(tempi) / len(tempi)
    totale = sum(tempi)
    best_idx = tempi.index(best) + 1

    dt_start = sessione.get("dateTimeStart", "")
    try:
        dt_obj = datetime.fromisoformat(dt_start)
        data_str = dt_obj.strftime("%Y-%m-%d")
        ora_str = dt_obj.strftime("%H:%M:%S")
        ts_file = dt_obj.strftime("%Y%m%d_%H%M%S")
    except Exception:
        data_str = datetime.now().strftime("%Y-%m-%d")
        ora_str = datetime.now().strftime("%H:%M:%S")
        ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")

    stats = dati.get("stats", {})
    chip = stats.get("chip", {})

    giri = []
    cumulativo = 0.0
    for lap in laps_raw:
        try:
            dur = float(lap["duration"])
        except (ValueError, KeyError):
            continue
        cumulativo += dur
        delta = round(dur - best, 3)
        giri.append({
            "giro": lap.get("nr", len(giri) + 1),
            "tempo": round(dur, 3),
            "cumulativo": round(cumulativo, 3),
            "delta": delta,
        })

    risultato = {
        "tipo": "speedhive",
        "versione": "1.0",
        "setup": setup,
        "record_id": record_id,
        "pilota": pilota,
        "data": data_str,
        "ora": ora_str,
        "serbatoio_cc": 0,
        "tempo_totale": round(totale, 3),
        "num_giri": len(giri),
        "miglior_giro": best_idx,
        "miglior_tempo": round(best, 3),
        "media": round(media, 3),
        "consumo_cc_min": 0,
        "autonomia_min": 0,
        "strategia": {},
        "speedhive": {
            "session_id": session_id,
            "transponder": chip.get("codeNr", ""),
            "velocita_best_kph": round(
                sessione.get("bestLap", {}).get("speed", {}).get("kph", 0), 1),
        },
        "giri": giri,
    }
    # Fotografia setup al momento dell'import (per analisi IA)
    if setup_snapshot:
        for k, v in setup_snapshot.items():
            if k not in risultato:
                risultato[k] = v

    path_salvato = None
    if dati_dir:
        os.makedirs(dati_dir, exist_ok=True)
        prefisso = record_id if record_id else "SpeedHive"
        nome = "lap_%s_%s.json" % (prefisso, ts_file)
        path_salvato = os.path.join(dati_dir, nome)
        try:
            with open(path_salvato, "w", encoding="utf-8") as f:
                json.dump(risultato, f, ensure_ascii=False, indent=2)
        except Exception:
            path_salvato = None

    return risultato, path_salvato


def main():
    parser = argparse.ArgumentParser(
        description="TrackMind - Import tempi da MyLaps SpeedHive")
    parser.add_argument("activity",
        help="ID attivita' o URL SpeedHive completo")
    parser.add_argument("--sessione", "-s", type=int, default=0,
        help="ID sessione da visualizzare in dettaglio")
    parser.add_argument("--salva", action="store_true",
        help="Salva la sessione in formato TrackMind")
    parser.add_argument("--dati-dir", default="dati",
        help="Cartella di salvataggio")
    parser.add_argument("--setup", default="")
    parser.add_argument("--pilota", default="")
    parser.add_argument("--record-id", default="")
    parser.add_argument("--json", action="store_true",
        help="Output JSON grezzo")
    args = parser.parse_args()

    activity_id = estrai_id(args.activity)
    if not activity_id:
        sys.exit(1)

    dati = scarica_sessioni(activity_id)
    if not dati:
        sys.exit(1)

    if args.json:
        print(json.dumps(dati, ensure_ascii=False, indent=2))
        return

    if args.sessione > 0:
        sessione = stampa_sessione(dati, args.sessione)
        if args.salva and sessione:
            converti_sessione(
                dati, args.sessione,
                setup=args.setup,
                pilota=args.pilota,
                record_id=args.record_id,
                dati_dir=args.dati_dir)
    else:
        stampa_riepilogo(dati)


if __name__ == "__main__":
    main()
