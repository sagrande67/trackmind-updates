"""
TrackMind - Letture di sistema (RAM, CPU)
Helper stdlib-only per il "Centro di Controllo": niente psutil.

Linux/uConsole: legge /proc/meminfo e /proc/stat.
Windows: usa ctypes con kernel32 (GlobalMemoryStatusEx, GetSystemTimes).
Mac/altri: ritorna None.

Uso:
    from core.sys_info import get_ram_info, get_cpu_pct, get_loadavg

    pct, used_mb, total_mb = get_ram_info()
    cpu = get_cpu_pct()  # percentuale, mediata sull'ultima chiamata
"""

import os
import sys
import time

_is_linux = sys.platform.startswith("linux")
_is_windows = sys.platform == "win32"


# =====================================================================
#  RAM
# =====================================================================
def get_ram_info():
    """Ritorna (pct_usata, used_mb, total_mb) oppure (None, None, None)
    se non determinabile."""
    if _is_linux:
        return _ram_linux()
    if _is_windows:
        return _ram_windows()
    return (None, None, None)


def _ram_linux():
    try:
        info = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                # Valori in kB
                v = v.strip().split()[0]
                try:
                    info[k.strip()] = int(v)
                except ValueError:
                    pass
        total_kb = info.get("MemTotal", 0)
        # MemAvailable e' la metrica giusta per "RAM disponibile":
        # tiene conto di buffer/cache riutilizzabili. Fallback a
        # MemFree + Buffers + Cached per kernel vecchi.
        avail_kb = info.get("MemAvailable")
        if avail_kb is None:
            avail_kb = (info.get("MemFree", 0)
                        + info.get("Buffers", 0)
                        + info.get("Cached", 0))
        if total_kb <= 0:
            return (None, None, None)
        used_kb = total_kb - avail_kb
        pct = (used_kb * 100.0) / total_kb
        return (round(pct, 1),
                round(used_kb / 1024.0, 0),
                round(total_kb / 1024.0, 0))
    except Exception:
        return (None, None, None)


def _ram_windows():
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(
                ctypes.byref(m)):
            return (None, None, None)
        total_mb = m.ullTotalPhys / (1024.0 * 1024.0)
        avail_mb = m.ullAvailPhys / (1024.0 * 1024.0)
        used_mb = total_mb - avail_mb
        pct = float(m.dwMemoryLoad)
        return (round(pct, 1), round(used_mb, 0), round(total_mb, 0))
    except Exception:
        return (None, None, None)


# =====================================================================
#  CPU
# =====================================================================
# Stato ultima lettura per calcolo delta (sample 1 = differenza)
_cpu_state = {"idle": None, "total": None, "ts": None}


def get_cpu_pct():
    """Ritorna percentuale di CPU usata (0..100) come differenza
    tra l'ultima chiamata e quella precedente. Su Linux usa
    /proc/stat (somma di tutti i core). Prima chiamata ritorna 0
    (non c'e' delta), serve un secondo campione per il valore vero."""
    if _is_linux:
        return _cpu_pct_linux()
    if _is_windows:
        return _cpu_pct_windows()
    return None


def _cpu_pct_linux():
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        # Formato: "cpu  user nice system idle iowait irq softirq steal..."
        if not line.startswith("cpu "):
            return None
        parti = line.split()
        valori = [int(x) for x in parti[1:]]
        if len(valori) < 4:
            return None
        idle = valori[3]
        if len(valori) > 4:
            idle += valori[4]  # iowait
        total = sum(valori)
        prev_idle = _cpu_state.get("idle")
        prev_total = _cpu_state.get("total")
        _cpu_state["idle"] = idle
        _cpu_state["total"] = total
        if prev_idle is None or prev_total is None:
            return 0.0
        d_idle = idle - prev_idle
        d_total = total - prev_total
        if d_total <= 0:
            return 0.0
        pct = 100.0 * (d_total - d_idle) / d_total
        return round(max(0.0, min(100.0, pct)), 1)
    except Exception:
        return None


def _cpu_pct_windows():
    try:
        import ctypes
        from ctypes import wintypes

        idle = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle),
                ctypes.byref(kernel),
                ctypes.byref(user)):
            return None

        def _ft_to_int(ft):
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        idle_t = _ft_to_int(idle)
        # Kernel time include idle, quindi total = kernel + user
        total_t = _ft_to_int(kernel) + _ft_to_int(user)
        prev_idle = _cpu_state.get("idle")
        prev_total = _cpu_state.get("total")
        _cpu_state["idle"] = idle_t
        _cpu_state["total"] = total_t
        if prev_idle is None or prev_total is None:
            return 0.0
        d_idle = idle_t - prev_idle
        d_total = total_t - prev_total
        if d_total <= 0:
            return 0.0
        pct = 100.0 * (d_total - d_idle) / d_total
        return round(max(0.0, min(100.0, pct)), 1)
    except Exception:
        return None


def get_cpu_count():
    """Numero di CPU/core logici."""
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


# =====================================================================
#  Load average (Linux/Mac)
# =====================================================================
def get_loadavg():
    """Ritorna (load1, load5, load15) o None.
    Disponibile solo su Linux/Mac (os.getloadavg)."""
    try:
        return os.getloadavg()
    except (AttributeError, OSError):
        return None


# =====================================================================
#  Disco (micro-SD su uConsole, partizione su altri)
# =====================================================================
def get_disk_info(path):
    """Ritorna dict con free_gb, used_gb, total_gb, pct_usata,
    oppure None se il path non esiste o `shutil.disk_usage` fallisce."""
    try:
        import shutil
        if not path or not os.path.exists(path):
            # Fallback sulla home se il path passato non esiste
            path = os.path.expanduser("~")
        u = shutil.disk_usage(path)
        gb = 1024.0 ** 3
        total_gb = u.total / gb
        free_gb = u.free / gb
        used_gb = u.used / gb
        pct = (u.used * 100.0 / u.total) if u.total else 0.0
        return {
            "free_gb": round(free_gb, 1),
            "used_gb": round(used_gb, 1),
            "total_gb": round(total_gb, 1),
            "pct_usata": round(pct, 1),
        }
    except Exception:
        return None
