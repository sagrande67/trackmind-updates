"""core/sd_health.py - Lettura metriche micro-SD (I/O, usura stimata, ext_csd).

Modulo di sola LETTURA DATI, senza UI. E' il layer su cui si appoggiano i
widget LED in core/sd_bar.py (BarraUsura, BarraIO).

FUNZIONI PRINCIPALI:

  trova_dev_sd()
      Restituisce il nome del dispositivo SD ("mmcblk0" in genere), oppure
      None se non e' un sistema con micro-SD (Windows, Mac, box senza SD).

  leggi_stat(dev)
      Ritorna (settori_letti, settori_scritti) dalla /sys/block/<dev>/stat
      corrente, oppure None in caso di errore. Un settore = 512 byte.

  leggi_boot_id()
      Ritorna una stringa univoca per la sessione di boot corrente (via
      /proc/sys/kernel/random/boot_id) o None. Serve ad accorgersi dei
      reboot: i contatori di /sys/block resettano ad ogni avvio.

  StatoUsura(file_stato)
      Gestisce un file JSON persistente che accumula i GB letti/scritti
      durante tutta la vita del sistema (attraverso i reboot). Esempio:
          s = StatoUsura("/percorso/dati/sd_wear.json")
          d = s.aggiorna()   # legge /sys, calcola delta, salva
          d["gb_scritti"]    # totale scritture di tutta la vita
          d["mbs_scrittura"] # rate MB/s dell'ultimo intervallo

  prova_ext_csd(dev)
      Tenta di leggere `mmc extcsd read /dev/<dev>` e estrae
      DEVICE_LIFE_TIME_EST (0..100%). Ritorna None se mmc-utils non e'
      installato / non accessibile / SD non supporta il comando.

PORTABILITA':
  Su sistemi non-Linux tutte le funzioni ritornano None / lista vuota
  in silenzio. I widget sopra fanno degradazione elegante.
"""

import os
import sys
import json
import time
import subprocess

# Un settore MMC/eMMC = 512 byte (fisso per standard).
_BYTES_PER_SETTORE = 512

# Byte in 1 MB e 1 GB (uso base binaria = MiB/GiB, coerente con shutil/df -h)
_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024


# ---------------------------------------------------------------------
#  DETECTION DEL DISPOSITIVO SD
# ---------------------------------------------------------------------
def trova_dev_sd():
    """Ritorna il nome del dispositivo SD (es. 'mmcblk0') o None.

    Strategia: su Linux cerchiamo in /sys/block/ il primo mmcblk*
    (esclude i mmcblkNpM che sono partizioni). Su altri OS None.
    """
    if sys.platform != "linux":
        return None
    try:
        for nome in sorted(os.listdir("/sys/block")):
            # "mmcblk0" ok, "mmcblk0p1" no (e' una partizione)
            if nome.startswith("mmcblk") and "p" not in nome[6:]:
                return nome
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------
#  LETTURA /sys/block/<dev>/stat
# ---------------------------------------------------------------------
def leggi_stat(dev):
    """Ritorna (settori_letti, settori_scritti) dal kernel, o None.

    Formato /sys/block/<dev>/stat (17 campi separati da whitespace):
      [0] reads completed
      [1] reads merged
      [2] sectors read          <-- CI INTERESSA
      [3] time reading (ms)
      [4] writes completed
      [5] writes merged
      [6] sectors written       <-- CI INTERESSA
      [7] time writing (ms)
      ... (altri 9 campi su kernel recenti)
    """
    if not dev:
        return None
    try:
        with open("/sys/block/%s/stat" % dev, "r") as f:
            campi = f.read().split()
        if len(campi) < 7:
            return None
        settori_r = int(campi[2])
        settori_w = int(campi[6])
        return settori_r, settori_w
    except Exception:
        return None


# ---------------------------------------------------------------------
#  BOOT ID - per distinguere reboot
# ---------------------------------------------------------------------
def leggi_boot_id():
    """ID univoco della sessione di boot corrente (da /proc), o None."""
    try:
        with open("/proc/sys/kernel/random/boot_id", "r") as f:
            return f.read().strip()
    except Exception:
        pass
    # Fallback: uptime in ticks iniziali come pseudo-ID
    try:
        with open("/proc/stat", "r") as f:
            for riga in f:
                if riga.startswith("btime "):
                    return "btime-" + riga.split()[1]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------
#  ACCUMULATORE PERSISTENTE
# ---------------------------------------------------------------------
class StatoUsura:
    """Mantiene un file JSON con i GB totali letti/scritti da sempre.

    Gestisce correttamente i reboot: quando il boot_id cambia, il delta
    per quella sessione parte da 0 (i contatori del kernel si sono
    resettati). Quando il boot_id e' uguale, il delta e' current - last.

    L'I/O rate ("MB/s") si calcola fra due chiamate successive in base
    al tempo trascorso.
    """

    def __init__(self, file_stato, dev=None):
        self.file_stato = file_stato
        self.dev = dev if dev else trova_dev_sd()
        self._cache = None   # ultimo dizionario letto/scritto
        self._ultimo_sample_ts = None
        self._ultimo_settori_r = None
        self._ultimo_settori_w = None

    # ---- I/O file ----
    def _leggi_file(self):
        if not os.path.exists(self.file_stato):
            return {
                "gb_scritti": 0.0,
                "gb_letti":   0.0,
                "boot_id":    None,
                "settori_w_ultimo": 0,
                "settori_r_ultimo": 0,
                "ultima_iso": None,
            }
        try:
            with open(self.file_stato, "r", encoding="utf-8") as f:
                d = json.load(f)
            # Difesa: garantisce le chiavi
            d.setdefault("gb_scritti", 0.0)
            d.setdefault("gb_letti", 0.0)
            d.setdefault("boot_id", None)
            d.setdefault("settori_w_ultimo", 0)
            d.setdefault("settori_r_ultimo", 0)
            d.setdefault("ultima_iso", None)
            return d
        except Exception:
            # File corrotto: riparti da zero
            return {
                "gb_scritti": 0.0, "gb_letti": 0.0, "boot_id": None,
                "settori_w_ultimo": 0, "settori_r_ultimo": 0,
                "ultima_iso": None,
            }

    def _salva_file(self, d):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.file_stato)),
                        exist_ok=True)
            tmp = self.file_stato + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)
            os.replace(tmp, self.file_stato)
        except Exception:
            pass

    # ---- aggiornamento ----
    def aggiorna(self):
        """Legge /sys/block, calcola delta vs ultimo stato salvato,
        accumula nel file JSON e restituisce un dizionario con tutto.

        Keys restituite:
          gb_scritti, gb_letti        -> totale di vita
          mbs_scrittura, mbs_lettura  -> rate in MB/s dell'ultimo intervallo
          delta_gb_w, delta_gb_r      -> GB aggiunti in questo intervallo
          dev                         -> dispositivo
          disponibile                 -> bool (False se non Linux/SD)
        """
        risultato = {
            "gb_scritti": 0.0, "gb_letti": 0.0,
            "mbs_scrittura": 0.0, "mbs_lettura": 0.0,
            "delta_gb_w": 0.0, "delta_gb_r": 0.0,
            "dev": self.dev, "disponibile": False,
        }

        letto = leggi_stat(self.dev)
        if letto is None:
            return risultato  # non disponibile (Windows / no SD)

        settori_r, settori_w = letto
        boot_id = leggi_boot_id()
        ora = time.time()
        d = self._leggi_file()

        # Delta di vita (accumulatore persistente)
        if d.get("boot_id") == boot_id and boot_id is not None:
            # Stesso boot: delta semplice
            d_w = max(0, settori_w - int(d.get("settori_w_ultimo", 0)))
            d_r = max(0, settori_r - int(d.get("settori_r_ultimo", 0)))
        else:
            # Reboot (o primo avvio): sector counters del kernel sono
            # ripartiti da 0, quindi il "delta" di vita e' tutto il
            # contatore corrente (tutto cio' che e' accaduto dal boot).
            d_w = settori_w
            d_r = settori_r

        gb_w = (d_w * _BYTES_PER_SETTORE) / _GB
        gb_r = (d_r * _BYTES_PER_SETTORE) / _GB

        d["gb_scritti"] = float(d.get("gb_scritti", 0.0)) + gb_w
        d["gb_letti"]   = float(d.get("gb_letti",   0.0)) + gb_r
        d["boot_id"] = boot_id
        d["settori_w_ultimo"] = settori_w
        d["settori_r_ultimo"] = settori_r
        try:
            from datetime import datetime as _dt
            d["ultima_iso"] = _dt.utcnow().isoformat() + "Z"
        except Exception:
            pass

        self._salva_file(d)

        # Rate MB/s: calcolato fra due chiamate consecutive a aggiorna()
        # all'interno della stessa sessione di processo.
        if self._ultimo_sample_ts is not None \
                and self._ultimo_settori_w is not None \
                and self._ultimo_settori_r is not None:
            dt = ora - self._ultimo_sample_ts
            if dt > 0.05:
                d_w_s = max(0, settori_w - self._ultimo_settori_w)
                d_r_s = max(0, settori_r - self._ultimo_settori_r)
                mbs_w = (d_w_s * _BYTES_PER_SETTORE) / _MB / dt
                mbs_r = (d_r_s * _BYTES_PER_SETTORE) / _MB / dt
                risultato["mbs_scrittura"] = mbs_w
                risultato["mbs_lettura"] = mbs_r
        self._ultimo_sample_ts = ora
        self._ultimo_settori_w = settori_w
        self._ultimo_settori_r = settori_r

        risultato["gb_scritti"] = d["gb_scritti"]
        risultato["gb_letti"]   = d["gb_letti"]
        risultato["delta_gb_w"] = gb_w
        risultato["delta_gb_r"] = gb_r
        risultato["disponibile"] = True
        self._cache = risultato
        return risultato

    def stato(self):
        """Ritorna l'ultimo risultato di aggiorna() senza rileggere."""
        return self._cache


# ---------------------------------------------------------------------
#  ext_csd: usura reale se il controller la espone
# ---------------------------------------------------------------------
#
#  Interpretazione dei byte DEVICE_LIFE_TIME_EST_TYP_A/B:
#
#       0x00   informazione non disponibile
#       0x01   0-10% usura consumata
#       0x02   10-20%
#       0x03   20-30%
#       ...
#       0x0A   90-100%
#       0x0B   consumata oltre il rating (segnale EOL)
#
#  Valore restituito: percentuale centrale della fascia (0x01 -> 5%).

def prova_ext_csd(dev=None):
    """Prova a leggere DEVICE_LIFE_TIME_EST via `mmc extcsd read`.

    Ritorna:
      dict {"pct_usura": int 0-100, "eol": bool, "sorgente": "ext_csd"}
      oppure None se non disponibile / non supportato / permessi mancanti.
    """
    if sys.platform != "linux":
        return None
    if not dev:
        dev = trova_dev_sd()
    if not dev:
        return None
    path = "/dev/" + dev

    # mmc-utils potrebbe essere assente. Timeout basso: non deve bloccare
    # l'UI se il tool e' lento o l'SD non risponde.
    try:
        res = subprocess.run(
            ["mmc", "extcsd", "read", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=3,
        )
    except FileNotFoundError:
        return None   # mmc non installato
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None
    if res.returncode != 0:
        return None

    txt = res.stdout.decode("utf-8", "ignore")
    # Cerchiamo i due byte TYP_A e TYP_B; prendiamo il peggiore (max).
    import re
    val_a = None
    val_b = None
    pre_eol = None
    for riga in txt.splitlines():
        if "DEVICE_LIFE_TIME_EST_TYP_A" in riga or \
           "Life Time Estimation A" in riga:
            m = re.search(r"0x([0-9a-fA-F]+)", riga)
            if m:
                val_a = int(m.group(1), 16)
        elif "DEVICE_LIFE_TIME_EST_TYP_B" in riga or \
             "Life Time Estimation B" in riga:
            m = re.search(r"0x([0-9a-fA-F]+)", riga)
            if m:
                val_b = int(m.group(1), 16)
        elif "PRE_EOL_INFO" in riga or "Pre EOL" in riga:
            m = re.search(r"0x([0-9a-fA-F]+)", riga)
            if m:
                pre_eol = int(m.group(1), 16)

    candidati = [v for v in (val_a, val_b) if v is not None and v > 0]
    if not candidati:
        return None
    worst = max(candidati)
    if worst >= 0x0B:
        pct = 100
        eol = True
    else:
        # 0x01 -> 0-10% (centrato 5%), 0x02 -> 10-20% (15%), ecc.
        pct = (worst - 1) * 10 + 5
        eol = (pre_eol is not None and pre_eol >= 0x03)
    return {"pct_usura": min(100, max(0, pct)),
            "eol": eol, "sorgente": "ext_csd"}


# ---------------------------------------------------------------------
#  Test standalone:  python -m core.sd_health
# ---------------------------------------------------------------------
if __name__ == "__main__":
    dev = trova_dev_sd()
    print("Dispositivo SD rilevato:", dev)
    print("Boot id:", leggi_boot_id())
    stat = leggi_stat(dev)
    if stat:
        r, w = stat
        print("Settori letti:    %d  (%.2f GB)" % (r, r * _BYTES_PER_SETTORE / _GB))
        print("Settori scritti:  %d  (%.2f GB)" % (w, w * _BYTES_PER_SETTORE / _GB))
    else:
        print("Nessuna SD leggibile (probabile non-Linux o no mmcblk).")
    print("ext_csd:", prova_ext_csd(dev))
