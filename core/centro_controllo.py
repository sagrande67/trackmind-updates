"""
TrackMind - Centro di Controllo (popup a richiesta)

Mostra in un Toplevel compatto tutte le informazioni di stato del
software: utente, Wi-Fi, stampante, batteria, micro-SD, RAM, CPU,
prossimo turno gara. Refresh 1 Hz finche' aperto.

Si invoca da retrodb via:
    from core.centro_controllo import apri_centro_controllo
    apri_centro_controllo(self)
dove `self` e' l'istanza RetroDBApp (serve per leggere sessione,
conf, monitor assistente gara, ecc.)

Il popup e' singleton: la seconda invocazione mentre e' aperto lo
chiude. ESC e click sul bottone CHIUDI lo nascondono.
"""

import os
import sys
import tkinter as tk
from tkinter import font as tkfont
from datetime import datetime

try:
    from config_colori import FONT_MONO, carica_colori
except ImportError:
    FONT_MONO = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"
    def carica_colori():
        return {
            "sfondo": "#0a0a0a", "dati": "#39ff14",
            "label": "#39ff14", "linee": "#1a3a1a",
            "stato_ok": "#39ff14", "stato_avviso": "#ffff00",
            "stato_errore": "#ff4444", "testo_dim": "#1a8c1a",
            "pulsanti_sfondo": "#1a3a1a", "pulsanti_testo": "#39ff14",
        }

try:
    from core.sys_info import (get_ram_info, get_cpu_pct,
                                get_cpu_count, get_loadavg,
                                get_disk_info)
    _HAS_SYSINFO = True
except Exception:
    _HAS_SYSINFO = False
    def get_ram_info():
        return (None, None, None)
    def get_cpu_pct():
        return None
    def get_cpu_count():
        return 1
    def get_loadavg():
        return None
    def get_disk_info(path):
        return None


# Singleton: una sola istanza popup alla volta
_instance = None


def apri_centro_controllo(app):
    """Apri (o chiudi se gia' aperto) il popup centro di controllo.
    `app` e' l'istanza RetroDBApp."""
    global _instance
    if _instance is not None:
        try:
            if _instance.win.winfo_exists():
                _instance.chiudi()
                return
        except Exception:
            pass
        _instance = None
    _instance = _CentroControlloPopup(app)


class _CentroControlloPopup:
    """Popup che mostra info sistema + stato app. Refresh 1 Hz."""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.c = carica_colori()

        self.win = tk.Toplevel(self.root)
        self.win.title("TrackMind - Centro di Controllo")
        self.win.config(bg=self.c["sfondo"])
        self.win.transient(self.root)
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        # Posizione centrata sul Toplevel principale
        try:
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            w, h = 560, 380
            x = rx + max(0, (rw - w) // 2)
            y = ry + max(0, (rh - h) // 2)
            self.win.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except Exception:
            self.win.geometry("560x380")

        f_titolo = tkfont.Font(family=FONT_MONO, size=12, weight="bold")
        f_label = tkfont.Font(family=FONT_MONO, size=10)
        f_val = tkfont.Font(family=FONT_MONO, size=10, weight="bold")
        f_btn = tkfont.Font(family=FONT_MONO, size=10, weight="bold")

        # Header
        tk.Label(self.win, text="[ CENTRO DI CONTROLLO ]",
                 bg=self.c["sfondo"], fg=self.c["dati"],
                 font=f_titolo).pack(pady=(10, 4))
        tk.Frame(self.win, bg=self.c["linee"], height=1).pack(
            fill="x", padx=12, pady=(0, 8))

        # Griglia chiave: valore
        body = tk.Frame(self.win, bg=self.c["sfondo"])
        body.pack(fill="both", expand=True, padx=20, pady=4)

        # Salvo le label valore in un dict per aggiornare nel tick
        self._labels = {}

        def riga(chiave):
            r = tk.Frame(body, bg=self.c["sfondo"])
            r.pack(fill="x", pady=1)
            tk.Label(r, text=chiave + ":",
                     bg=self.c["sfondo"], fg=self.c["label"],
                     font=f_label, width=14, anchor="w").pack(
                side="left")
            v = tk.Label(r, text="--",
                         bg=self.c["sfondo"], fg=self.c["dati"],
                         font=f_val, anchor="w")
            v.pack(side="left", fill="x", expand=True)
            self._labels[chiave] = v
            return v

        riga("Utente")
        riga("Wi-Fi")
        riga("Stampante")
        riga("Batteria")
        riga("RAM")
        riga("CPU")
        riga("Carico")
        riga("micro-SD")
        riga("GARA")
        riga("Versione")
        riga("Data/Ora")

        # Footer con bottone chiudi
        tk.Frame(self.win, bg=self.c["linee"], height=1).pack(
            fill="x", padx=12, pady=(8, 6))
        bar = tk.Frame(self.win, bg=self.c["sfondo"])
        bar.pack(pady=(0, 10))
        tk.Button(bar, text="CHIUDI  (ESC)", font=f_btn,
                  bg=self.c["pulsanti_sfondo"],
                  fg=self.c["pulsanti_testo"],
                  relief="ridge", bd=2, cursor="hand2",
                  command=self.chiudi).pack(side="left", padx=4)
        tk.Label(bar,
                 text="  Refresh ogni secondo - Ctrl+I per "
                      "aprire/chiudere",
                 bg=self.c["sfondo"], fg=self.c["testo_dim"],
                 font=f_label).pack(side="left")

        self.win.bind("<Escape>", lambda e: self.chiudi())
        self.win.bind("<Control-i>", lambda e: self.chiudi())
        self.win.bind("<Control-I>", lambda e: self.chiudi())
        self.win.protocol("WM_DELETE_WINDOW", self.chiudi)

        # Avvia refresh
        self._tick_id = None
        self._refresh()
        try:
            self.win.lift()
            self.win.focus_force()
        except Exception:
            pass

    # =================================================================
    def _refresh(self):
        try:
            if not self.win.winfo_exists():
                return
        except Exception:
            return
        c = self.c
        # ── Utente ──
        try:
            from auth import get_display_name, is_admin
            ses = self.app.sessione if hasattr(self.app, "sessione") else None
            if ses:
                ruolo = "ADMIN" if is_admin(ses) else "UTENTE"
                nome = get_display_name(ses) or "?"
                self._set("Utente", "%s  (%s)" % (nome, ruolo),
                          c["dati"])
            else:
                self._set("Utente", "non loggato", c["testo_dim"])
        except Exception:
            self._set("Utente", "?", c["testo_dim"])

        # ── Wi-Fi ──
        try:
            connesso, ssid = self.app._wifi_stato()
            if connesso:
                self._set("Wi-Fi", ssid, c["stato_ok"])
            else:
                self._set("Wi-Fi", "OFFLINE", c["stato_errore"])
        except Exception:
            self._set("Wi-Fi", "?", c["testo_dim"])

        # ── Stampante ──
        try:
            ok = getattr(self.app, "_bt_stampante_ok", False)
            nome = getattr(self.app, "_bt_stampante_nome", "")
            if ok:
                self._set("Stampante",
                          ("ON  (%s)" % nome) if nome else "ON",
                          c["stato_ok"])
            else:
                self._set("Stampante", "OFF / non configurata",
                          c["stato_errore"])
        except Exception:
            self._set("Stampante", "?", c["testo_dim"])

        # ── Batteria ──
        try:
            from core.batteria import get_batteria_info
            pct, stato = get_batteria_info()
            if pct is None:
                self._set("Batteria", "n/d (no batteria)",
                          c["testo_dim"])
            else:
                in_carica = (stato == "Charging")
                if pct <= 10 and not in_carica:
                    fg = c["stato_errore"]
                elif pct <= 25 and not in_carica:
                    fg = c["stato_avviso"]
                else:
                    fg = c["stato_ok"]
                txt = "%d%%" % pct
                if in_carica:
                    txt += " (in carica)"
                elif stato == "Full":
                    txt += " (carica completa)"
                self._set("Batteria", txt, fg)
        except Exception:
            self._set("Batteria", "?", c["testo_dim"])

        # ── RAM ──
        try:
            pct, used, total = get_ram_info()
            if pct is None:
                self._set("RAM", "n/d", c["testo_dim"])
            else:
                if pct >= 90:
                    fg = c["stato_errore"]
                elif pct >= 75:
                    fg = c["stato_avviso"]
                else:
                    fg = c["stato_ok"]
                # Mostra in GB se > 1024 MB
                if total and total >= 1024:
                    total_str = "%.1f GB" % (total / 1024.0)
                    used_str = "%.1f GB" % (used / 1024.0)
                else:
                    total_str = "%d MB" % (total or 0)
                    used_str = "%d MB" % (used or 0)
                self._set("RAM",
                          "%s usati / %s totali  (%.0f%%)"
                          % (used_str, total_str, pct), fg)
        except Exception:
            self._set("RAM", "?", c["testo_dim"])

        # ── CPU ──
        try:
            pct = get_cpu_pct()
            n = get_cpu_count()
            if pct is None:
                self._set("CPU", "n/d  (%d core)" % n, c["testo_dim"])
            else:
                if pct >= 90:
                    fg = c["stato_errore"]
                elif pct >= 70:
                    fg = c["stato_avviso"]
                else:
                    fg = c["stato_ok"]
                self._set("CPU",
                          "%.0f%%  (%d core)" % (pct, n), fg)
        except Exception:
            self._set("CPU", "?", c["testo_dim"])

        # ── Load average ──
        try:
            la = get_loadavg()
            if la is None:
                self._set("Carico", "n/d", c["testo_dim"])
            else:
                la1, la5, la15 = la
                n = get_cpu_count() or 1
                # Codice colore: load > n_core e' sovraccarico
                if la1 > n * 1.5:
                    fg = c["stato_errore"]
                elif la1 > n:
                    fg = c["stato_avviso"]
                else:
                    fg = c["stato_ok"]
                self._set("Carico",
                          "%.2f  %.2f  %.2f  (1m / 5m / 15m)"
                          % (la1, la5, la15), fg)
        except Exception:
            self._set("Carico", "?", c["testo_dim"])

        # ── Disco / micro-SD ──
        try:
            # Path: dati_dir di TrackMind (es. ~/Trackmind5.4/dati)
            base = None
            try:
                if hasattr(self.app, "percorsi"):
                    base = self.app.percorsi.get("dati", None)
            except Exception:
                pass
            info = get_disk_info(base or os.path.expanduser("~"))
            if info:
                fg = c["stato_ok"]
                free_gb = info["free_gb"]
                total_gb = info["total_gb"]
                pct = info["pct_usata"]
                if pct >= 90:
                    fg = c["stato_errore"]
                elif pct >= 75:
                    fg = c["stato_avviso"]
                self._set("micro-SD",
                          "%.1f GB liberi / %.1f GB totali  (%.0f%% pieno)"
                          % (free_gb, total_gb, pct), fg)
            else:
                self._set("micro-SD", "n/d", c["testo_dim"])
        except Exception:
            self._set("micro-SD", "n/d", c["testo_dim"])

        # ── GARA (Assistente Gara) ──
        try:
            from assistente_gara import AssistenteGaraMonitor
            mon = AssistenteGaraMonitor.get(self.root)
            if mon is None or not mon.attivo:
                self._set("GARA", "non attivo", c["testo_dim"])
            else:
                p, dt = mon.trova_prossimo()
                if p is None or dt is None:
                    self._set("GARA",
                              "monitor attivo - nessun turno futuro",
                              c["testo_dim"])
                else:
                    secs = int((dt - mon._now()).total_seconds())
                    if secs < 0:
                        secs = 0
                    ore = secs // 3600
                    mm = (secs % 3600) // 60
                    ss = secs % 60
                    if ore > 0:
                        cd = "%d:%02d:%02d" % (ore, mm, ss)
                    else:
                        cd = "%02d:%02d" % (mm, ss)
                    cat = (p.get("categoria", "") or
                           (mon.categoria or {}).get("nome", "?"))
                    if secs <= 60:
                        fg = c["stato_errore"]
                    elif secs <= 180:
                        fg = "#ff8800"
                    elif secs <= 900:
                        fg = c["stato_avviso"]
                    else:
                        fg = c["stato_ok"]
                    self._set("GARA",
                              "%s fra %s" % (cat[:30], cd), fg)
        except Exception:
            self._set("GARA", "n/d", c["testo_dim"])

        # ── Versione + Data/Ora ──
        try:
            from version import __version__
            self._set("Versione", "TrackMind v" + __version__,
                      c["testo_dim"])
        except Exception:
            self._set("Versione", "?", c["testo_dim"])
        self._set("Data/Ora",
                  datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                  c["testo_dim"])

        # Ripianifica
        try:
            self._tick_id = self.win.after(1000, self._refresh)
        except Exception:
            pass

    def _set(self, chiave, valore, fg=None):
        try:
            lbl = self._labels.get(chiave)
            if lbl is None or not lbl.winfo_exists():
                return
            if fg is not None:
                lbl.config(text=valore, fg=fg)
            else:
                lbl.config(text=valore)
        except Exception:
            pass

    def chiudi(self):
        global _instance
        try:
            if self._tick_id is not None:
                self.win.after_cancel(self._tick_id)
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        if _instance is self:
            _instance = None
