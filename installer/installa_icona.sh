#!/bin/bash
# ==========================================================
# TrackMind - Installazione icona menu applicazioni Linux
# ==========================================================
# Crea la voce "TrackMind" nel menu applicazioni di sistema
# (gnome-shell, xfce4-panel, lxqt, plasma, openbox, ecc.) cosi'
# puoi lanciare TrackMind cliccandoci sopra invece di aprire un
# terminale.
#
# Uso:
#   bash installer/installa_icona.sh
#
# Disinstallazione:
#   rm ~/.local/share/applications/trackmind.desktop
# ==========================================================

set -e

USER_NAME="${USER:-$(whoami)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TM_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE="${SCRIPT_DIR}/templates/trackmind-app.desktop"
TARGET_DIR="${HOME}/.local/share/applications"
TARGET="${TARGET_DIR}/trackmind.desktop"

echo "[TrackMind] Installazione icona menu applicazioni"
echo "  Utente:    ${USER_NAME}"
echo "  TrackMind: ${TM_ROOT}"
echo "  Template:  ${TEMPLATE}"
echo "  Target:    ${TARGET}"

if [ ! -f "${TEMPLATE}" ]; then
    echo "ERRORE: template non trovato: ${TEMPLATE}"
    exit 1
fi

# Crea la directory delle applicazioni utente se non esiste
mkdir -p "${TARGET_DIR}"

# Sostituisci i placeholder con i path reali
sed "s|__USER__|${USER_NAME}|g; s|/home/${USER_NAME}/Documenti/Trackmind5.4|${TM_ROOT}|g" \
    "${TEMPLATE}" > "${TARGET}"

# Rendilo eseguibile (alcuni desktop environment lo richiedono)
chmod +x "${TARGET}"

# Aggiorna la cache desktop per far apparire subito l'icona
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${TARGET_DIR}" 2>/dev/null || true
fi

# Verifica che lo script avvia.sh esista e sia eseguibile
AVVIA="${TM_ROOT}/avvia.sh"
if [ ! -f "${AVVIA}" ]; then
    echo ""
    echo "ATTENZIONE: ${AVVIA} non esiste!"
    echo "Lo script avvia.sh dovrebbe essere nella root TrackMind."
    echo "Puoi crearlo a partire dal template:"
    echo "  cp installer/templates/avvia.sh ${TM_ROOT}/avvia.sh"
    echo "  chmod +x ${TM_ROOT}/avvia.sh"
fi

if [ ! -x "${AVVIA}" ] && [ -f "${AVVIA}" ]; then
    echo "  -> chmod +x ${AVVIA}"
    chmod +x "${AVVIA}"
fi

# Verifica che l'icona logo.png esista
LOGO="${TM_ROOT}/loghi/logo.png"
if [ ! -f "${LOGO}" ]; then
    echo "ATTENZIONE: ${LOGO} non trovato. L'icona potrebbe non apparire."
fi

# Crea anche un'icona cliccabile sulla scrivania (Desktop folder).
# Le distribuzioni Linux variano nel nome della cartella desktop:
# Italiana=Scrivania, Inglese=Desktop, Francese=Bureau, ecc. Tentiamo
# di rilevarla via xdg-user-dir, fallback alla cartella "Desktop".
DESKTOP_DIR=""
if command -v xdg-user-dir >/dev/null 2>&1; then
    DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo '')"
fi
if [ -z "${DESKTOP_DIR}" ] || [ ! -d "${DESKTOP_DIR}" ]; then
    # Fallback: prova le piu' comuni
    for cand in "${HOME}/Scrivania" "${HOME}/Desktop" \
                "${HOME}/Bureau" "${HOME}/Escritorio"; do
        if [ -d "${cand}" ]; then
            DESKTOP_DIR="${cand}"; break
        fi
    done
fi

if [ -n "${DESKTOP_DIR}" ] && [ -d "${DESKTOP_DIR}" ]; then
    DESKTOP_FILE="${DESKTOP_DIR}/trackmind.desktop"
    cp "${TARGET}" "${DESKTOP_FILE}"
    chmod +x "${DESKTOP_FILE}"

    # GNOME/Nautilus richiedono di marcare il file come "trusted"
    # altrimenti chiedono conferma ad ogni avvio o non lo lanciano
    # affatto. Su KDE/XFCE/LXDE basta eseguibile.
    if command -v gio >/dev/null 2>&1; then
        gio set "${DESKTOP_FILE}" metadata::trusted true 2>/dev/null \
            || true
    fi

    echo ""
    echo "[TrackMind] Icona aggiunta anche sulla scrivania:"
    echo "  ${DESKTOP_FILE}"
else
    echo ""
    echo "ATTENZIONE: cartella desktop non trovata (Scrivania/Desktop)."
    echo "Icona installata solo nel menu applicazioni."
fi

echo ""
echo "============================================================"
echo "[TrackMind] Icona installata!"
echo "============================================================"
echo ""
echo ">>> Verifica: ${TARGET} esiste?"
if [ -f "${TARGET}" ]; then
    echo "    SI (${TARGET})"
    echo ""
    echo ">>> Contenuto del file:"
    sed 's/^/    | /' "${TARGET}"
else
    echo "    NO! Errore: file non creato."
fi
echo ""
echo ">>> Come trovare TrackMind nel menu Linux Mint:"
echo "    1. Clicca il pulsante 'Menu' in basso a sinistra (logo Mint)"
echo "    2. Cerca nella casella in alto: digita  track"
echo "    3. Oppure naviga: Menu -> Accessori -> TrackMind"
echo ""
echo "Se non lo vedi subito, attendi 2-3 secondi (Cinnamon ha un"
echo "watcher su ~/.local/share/applications/ ma a volte e' lento)."
echo "In alternativa riavvia la shell di Cinnamon premendo Ctrl+Alt+Esc"
echo "(oppure: cinnamon --replace & disown)."
echo ""
echo "Per disinstallare:"
echo "  rm ${TARGET}"
if [ -n "${DESKTOP_DIR}" ]; then
    echo "  rm ${DESKTOP_DIR}/trackmind.desktop"
fi
