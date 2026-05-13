"""core/batteria.py - Lettura stato batteria e helper UI.

Espone:

    get_batteria_info() -> (percentuale, stato)
        Ritorna percentuale (int 0-100) e stato ('Charging', 'Discharging',
        'Full', 'Unknown') oppure (None, None) se non c'e' batteria.
        Funziona su Linux/uConsole (/sys/class/power_supply) e Windows
        (GetSystemPowerStatus via ctypes). Su Mac e sistemi senza batteria
        ritorna (None, None).

    aggiungi_barra_batteria(parent, anchor="ne", padx=0, pady=0)
        Helper generico per gli addons: aggiunge una BarraBatteria in
        overlay sulla Frame `parent` usando place(). Se la batteria non
        e' disponibile (PC fisso / Mac) non fa nulla e ritorna None.
        Ritorna l'istanza BarraBatteria oppure None.

La logica di lettura e' centralizzata qui per evitare duplicazione tra
retrodb.py (entry point) e i vari addons (laptimer, crono, ecc.).
"""

import os
import sys


# =====================================================================
#  LETTURA STATO BATTERIA (cross-platform, solo stdlib)
# =====================================================================
def _get_batteria_info_windows():
    """Legge lo stato della batteria su Windows via GetSystemPowerStatus.
    Usa solo ctypes (stdlib). Ritorna (percentuale, stato) o (None, None)
    se non c'e' batteria (es. desktop PC fisso)."""
    try:
        import ctypes
        from ctypes import wintypes

        class _SPS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus",         wintypes.BYTE),
                ("BatteryFlag",          wintypes.BYTE),
                ("BatteryLifePercent",   wintypes.BYTE),
                ("SystemStatusFlag",     wintypes.BYTE),
                ("BatteryLifeTime",      wintypes.DWORD),
                ("BatteryFullLifeTime",  wintypes.DWORD),
            ]

        sps = _SPS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
            return None, None

        flag = sps.BatteryFlag & 0xFF
        pct = sps.BatteryLifePercent & 0xFF
        ac = sps.ACLineStatus & 0xFF

        # Nessuna batteria (desktop) o stato sconosciuto
        if flag == 128 or flag == 255 or pct == 255:
            return None, None

        # Mappa stato al formato Linux usato altrove nel codice
        if flag & 8:
            stato = "Charging"
        elif ac == 1 and pct >= 100:
            stato = "Full"
        elif ac == 0:
            stato = "Discharging"
        elif ac == 1:
            stato = "Charging"
        else:
            stato = "Unknown"

        return int(pct), stato
    except Exception:
        return None, None


def get_batteria_info():
    """Legge lo stato della batteria.
    - Linux/uConsole: da /sys/class/power_supply
    - Windows: via GetSystemPowerStatus (kernel32)
    - Mac e sistemi senza batteria: (None, None)
    Ritorna (percentuale, stato) dove:
      percentuale: int 0-100, oppure None se non disponibile
      stato: 'Charging', 'Discharging', 'Full', 'Unknown', oppure None
    """
    if sys.platform == "win32":
        return _get_batteria_info_windows()
    ps_dir = "/sys/class/power_supply"
    if not os.path.isdir(ps_dir):
        return None, None
    try:
        for device in os.listdir(ps_dir):
            dev_path = os.path.join(ps_dir, device)
            cap_file = os.path.join(dev_path, "capacity")
            type_file = os.path.join(dev_path, "type")
            if not os.path.isfile(cap_file):
                continue
            # Verifica che sia una batteria (scarta AC, USB, ecc.)
            try:
                with open(type_file, "r") as f:
                    tipo = f.read().strip()
                if tipo != "Battery":
                    continue
            except Exception:
                pass
            # Leggi percentuale
            try:
                with open(cap_file, "r") as f:
                    percent = int(f.read().strip())
            except Exception:
                continue
            # Leggi stato ricarica (opzionale)
            stato = None
            status_file = os.path.join(dev_path, "status")
            try:
                with open(status_file, "r") as f:
                    stato = f.read().strip()
            except Exception:
                pass
            return percent, stato
    except Exception:
        pass
    return None, None


# =====================================================================
#  HELPER UI - aggiunta barra batteria negli addons
# =====================================================================
def aggiungi_barra_batteria(parent, anchor="ne", relx=1.0, rely=0.0,
                            x=-6, y=2):
    """Aggiunge una BarraBatteria in overlay su `parent` (Frame tkinter).

    Usa `.place()` per posizionare la barra come overlay, cosi' non
    interferisce con il layout pack/grid esistente del parent. Di default
    la piazza nell'angolo superiore destro (anchor='ne').

    Se la batteria non e' disponibile (es. PC desktop senza batteria) o
    se il modulo BarraBatteria non e' importabile, non fa nulla e
    ritorna None.

    Parametri:
        parent : Frame tkinter su cui piazzare la barra
        anchor : ancoraggio tkinter ('ne' = nord-est)
        relx, rely, x, y : offset di place() (default: angolo sup. dx
                           con leggero margine interno)

    Ritorna: l'istanza BarraBatteria oppure None se non disponibile.
    """
    # Probe: se non c'e' batteria non creo nulla
    try:
        pct, _ = get_batteria_info()
    except Exception:
        pct = None
    if pct is None:
        return None
    # Import pigro per evitare dipendenze circolari a import-time
    try:
        from core.sd_bar import BarraBatteria
    except Exception:
        try:
            from sd_bar import BarraBatteria  # fallback se core/ e' in path
        except Exception:
            return None
    try:
        barra = BarraBatteria(parent, get_info_func=get_batteria_info)
        barra.place(relx=relx, rely=rely, x=x, y=y, anchor=anchor)
        return barra
    except Exception:
        return None
