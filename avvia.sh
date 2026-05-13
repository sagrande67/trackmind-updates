#!/bin/bash
# ============================================================================
#  TrackMind - Launcher per uConsole
# ============================================================================
#  Attende che il desktop (Openbox) sia pronto prima di lanciare l'app.
#  Tenta di forzare il keyboard focus tramite xdotool (se installato).
#  Path utente sostituito automaticamente dall'installer (sandro).
# ============================================================================

# Disabilita screen blanking e DPMS (schermo che si spegne)
if command -v xset >/dev/null 2>&1; then
    xset s off       2>/dev/null || true
    xset s noblank   2>/dev/null || true
    xset -dpms       2>/dev/null || true
fi

# Nasconde il cursore dopo 3 secondi di inattivita' (se unclutter e' presente)
if command -v unclutter >/dev/null 2>&1; then
    unclutter -idle 3 -root &
fi

# ----------------------------------------------------------------------------
# Forza attivazione Bluetooth (necessario per la stampante termica BT).
# Se l'utente ha spento il BT da pannello/sistema, lo riaccendiamo qui prima
# che TrackMind parta. Tutti i comandi sono "best effort": se falliscono
# (es. permessi, BT gia' attivo) procediamo comunque.
# ----------------------------------------------------------------------------
# 1) Sblocca eventuale rfkill (block software/hardware)
if command -v rfkill >/dev/null 2>&1; then
    rfkill unblock bluetooth 2>/dev/null \
        || sudo -n rfkill unblock bluetooth 2>/dev/null \
        || true
fi
# 2) Assicura che il servizio bluetooth sia attivo
if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active --quiet bluetooth 2>/dev/null \
        || sudo -n systemctl start bluetooth 2>/dev/null \
        || true
fi
# 3) Accende l'adapter (bluetoothctl power on)
if command -v bluetoothctl >/dev/null 2>&1; then
    bluetoothctl power on >/dev/null 2>&1 || true
fi

# Aspetta 8 secondi per dare tempo al window manager di completare il setup
# (il BT ha cosi' anche il tempo di salire prima che TrackMind tenti la stampa)
sleep 8

cd /home/sandro/Documenti/TrackMind5.4 || exit 1

# Se xdotool e' installato, attiva la finestra dopo il lancio
if command -v xdotool >/dev/null 2>&1; then
    (
        sleep 4
        WID=$(xdotool search --name "TrackMind\|RetroDB\|Login" 2>/dev/null | head -1)
        if [ -n "$WID" ]; then
            xdotool windowactivate "$WID" 2>/dev/null
            xdotool windowfocus "$WID" 2>/dev/null
            xdotool mousemove --window "$WID" 400 300 click 1 2>/dev/null
        fi
    ) &
fi

# Lancia TrackMind
exec python3 retrodb.py
