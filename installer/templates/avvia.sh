#!/bin/bash
# ============================================================================
#  TrackMind - Launcher per uConsole
# ============================================================================
#  Attende che il desktop (Openbox) sia pronto prima di lanciare l'app.
#  Tenta di forzare il keyboard focus tramite xdotool (se installato).
#  Forza il layout tastiera US (la uConsole a volte torna in italiano).
#  Path utente sostituito automaticamente dall'installer (__USER__).
# ============================================================================

# Forza layout tastiera US: la uConsole a volte torna in italiano dopo
# aggiornamenti di sistema o se la locale e' configurata in italiano.
# Idempotente: se gia' US non cambia nulla.
if command -v setxkbmap >/dev/null 2>&1; then
    setxkbmap us 2>/dev/null || true
fi

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

# Aspetta 8 secondi per dare tempo al window manager di completare il setup
sleep 8

cd /home/__USER__/Documenti/TrackMind5.4 || exit 1

# Auto-install 'bleak' se mancante (richiesto dalla feature CRONO LIVE via BLE).
# Idempotente: se gia' installato Python exit 0 e saltiamo. In background per
# non rallentare il boot, logga in dati/bleak_install.log. Best-effort: se
# fallisce TrackMind parte uguale, semplicemente il bottone LIVE resta grigio.
if command -v python3 >/dev/null 2>&1; then
    if ! python3 -c "import bleak" >/dev/null 2>&1; then
        mkdir -p dati 2>/dev/null || true
        (
            pip install bleak --break-system-packages \
                >> dati/bleak_install.log 2>&1 || \
            pip3 install bleak --break-system-packages \
                >> dati/bleak_install.log 2>&1 || true
        ) &
    fi
fi

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
