#!/bin/bash
# ============================================================================
#  TrackMind 5.4 - Preparatore microSD per uConsole
# ============================================================================
#  Scenario tipico:
#    1. Flash di Raspberry Pi OS (o immagine uConsole) su microSD col tuo
#       tool preferito (Raspberry Pi Imager, balenaEtcher, dd...)
#    2. Primo boot della SD nella uConsole per generare /home/pi e finire
#       il setup iniziale (utente pi gia' creato, partizione espansa)
#    3. Spegni la uConsole, sposta la SD su questo PC
#    4. Esegui questo script COME ROOT (sudo) puntando al rootfs montato
#
#  Cosa fa:
#    - Copia TrackMind5.4 in <SD>/home/pi/Documenti/TrackMind5.4 (selezione
#      pulita: niente dev/, dati personali, backup, cataloghi, pycache)
#    - Imposta uid/gid 1000:1000 (utente pi) su tutti i file copiati
#    - Crea conf.dat di default (scala=1.5, fullscreen=1)
#    - Crea cartelle vuote dati/scouting, dati/backup, dati/loghi
#    - Abilita SSH server (touch /boot/ssh per attivazione al primo boot)
#    - Copia la TUA chiave pubblica SSH in /home/pi/.ssh/authorized_keys
#      (la genera se non esiste)
#    - Configura autostart desktop per lanciare TrackMind al login
#    - Configura autologin utente + boot grafico (target graphical.target)
#    - Installa /etc/sudoers.d/trackmind con NOPASSWD per shutdown/poweroff/
#      reboot, cosi' il bottone SPEGNI funziona senza richiedere password
#
#  Uso:
#    sudo bash prepara_sd.sh --root /media/$USER/rootfs --boot /media/$USER/bootfs
#
#  Opzioni:
#    --root PATH       Mountpoint partizione root della SD (obbligatorio)
#    --boot PATH       Mountpoint partizione boot della SD (consigliato)
#    --user NOME       Utente target sulla SD (default: pi)
#    --uid NUMERO      UID utente target  (default: 1000)
#    --gid NUMERO      GID utente target  (default: 1000)
#    --no-ssh          Salta configurazione SSH
#    --no-key          Non installare chiave SSH (solo password)
#    --no-autostart    Non installare autostart desktop
#    --dry-run         Mostra cosa verrebbe fatto senza scrivere nulla
#    -y, --yes         Salta la conferma interattiva
#    -h, --help        Mostra questo messaggio
# ============================================================================

set -e
set -u
set -o pipefail

# ---------------------------------------------------------------------------
#  Colori
# ---------------------------------------------------------------------------
VERDE='\033[1;32m'
GIALLO='\033[1;33m'
ROSSO='\033[1;31m'
CIANO='\033[1;36m'
RESET='\033[0m'

log()    { echo -e "${VERDE}[OK]${RESET}  $*"; }
info()   { echo -e "${CIANO}[..]${RESET}  $*"; }
warn()   { echo -e "${GIALLO}[!!]${RESET}  $*" >&2; }
err()    { echo -e "${ROSSO}[KO]${RESET}  $*" >&2; }
fatal()  { err "$*"; exit 1; }

# ---------------------------------------------------------------------------
#  Parametri di default
# ---------------------------------------------------------------------------
SD_ROOT=""
SD_BOOT=""
TARGET_USER="pi"
TARGET_UID="1000"
TARGET_GID="1000"
DO_SSH="1"
DO_KEY="1"
DO_AUTOSTART="1"
DO_AUTOLOGIN="1"
DRY_RUN="0"
ASSUME_YES="0"

# Path sorgente: lo script vive in TrackMind5.4/installer/
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SRC_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
TEMPLATES_DIR="$SCRIPT_DIR/templates"

# ---------------------------------------------------------------------------
#  Parsing argomenti
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --root)         SD_ROOT="$2"; shift 2 ;;
        --boot)         SD_BOOT="$2"; shift 2 ;;
        --user)         TARGET_USER="$2"; shift 2 ;;
        --uid)          TARGET_UID="$2"; shift 2 ;;
        --gid)          TARGET_GID="$2"; shift 2 ;;
        --no-ssh)       DO_SSH="0"; shift ;;
        --no-key)       DO_KEY="0"; shift ;;
        --no-autostart) DO_AUTOSTART="0"; shift ;;
        --no-autologin) DO_AUTOLOGIN="0"; shift ;;
        --dry-run)      DRY_RUN="1"; shift ;;
        -y|--yes)       ASSUME_YES="1"; shift ;;
        -h|--help)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        *)
            fatal "Opzione sconosciuta: $1 (usa --help)"
            ;;
    esac
done

# ---------------------------------------------------------------------------
#  Sanity check
# ---------------------------------------------------------------------------
if [ "$DRY_RUN" = "0" ] && [ "$(id -u)" != "0" ]; then
    fatal "Lancia come root: sudo bash prepara_sd.sh ..."
fi

[ -z "$SD_ROOT" ] && fatal "Indica la partizione root della SD con --root /percorso/al/rootfs"
[ ! -d "$SD_ROOT" ] && fatal "Mountpoint root non esiste: $SD_ROOT"

# Auto-detect boot se non specificato (RaspiOS recente: /boot/firmware)
if [ -z "$SD_BOOT" ]; then
    if [ -d "$SD_ROOT/boot/firmware" ]; then
        SD_BOOT="$SD_ROOT/boot/firmware"
        info "Boot rilevato in $SD_BOOT (interno al rootfs)"
    elif [ -d "$SD_ROOT/boot" ] && [ -f "$SD_ROOT/boot/cmdline.txt" ]; then
        SD_BOOT="$SD_ROOT/boot"
        info "Boot rilevato in $SD_BOOT (interno al rootfs)"
    else
        warn "Partizione boot non specificata e non rilevata automaticamente."
        warn "Indica con --boot /percorso/al/bootfs (necessaria per abilitare SSH)."
        DO_SSH="0"
    fi
fi

# Verifica che la SD sembri davvero un sistema Linux
if [ ! -d "$SD_ROOT/home" ] || [ ! -d "$SD_ROOT/etc" ]; then
    fatal "$SD_ROOT non sembra un rootfs Linux (mancano /home o /etc)"
fi

# Verifica che esista la home dell'utente target
TARGET_HOME="$SD_ROOT/home/$TARGET_USER"
if [ ! -d "$TARGET_HOME" ]; then
    warn "Home utente '$TARGET_USER' non trovata in $TARGET_HOME"
    warn "Verra' creata. Verifica che l'utente '$TARGET_USER' esista nel sistema."
fi

# Verifica sorgente
[ ! -f "$SRC_DIR/retrodb.py" ] && fatal "retrodb.py non trovato in $SRC_DIR"
[ ! -d "$SRC_DIR/tabelle" ]    && fatal "Cartella tabelle/ non trovata in $SRC_DIR"
[ ! -d "$TEMPLATES_DIR" ]      && fatal "Cartella templates/ non trovata in $TEMPLATES_DIR"

# ---------------------------------------------------------------------------
#  Riepilogo
# ---------------------------------------------------------------------------
DEST_DIR="$TARGET_HOME/Documenti/TrackMind5.4"

echo ""
echo -e "${VERDE}=============================================================${RESET}"
echo -e "${VERDE}  TrackMind 5.4 - Preparatore microSD${RESET}"
echo -e "${VERDE}=============================================================${RESET}"
echo ""
echo -e "  Sorgente            : ${GIALLO}$SRC_DIR${RESET}"
echo -e "  SD rootfs           : ${GIALLO}$SD_ROOT${RESET}"
echo -e "  SD bootfs           : ${GIALLO}${SD_BOOT:-<non specificato>}${RESET}"
echo -e "  Utente target       : ${GIALLO}$TARGET_USER  (uid=$TARGET_UID gid=$TARGET_GID)${RESET}"
echo -e "  Destinazione        : ${GIALLO}$DEST_DIR${RESET}"
echo -e "  Abilita SSH         : ${GIALLO}$([ "$DO_SSH" = "1" ] && echo SI || echo NO)${RESET}"
echo -e "  Installa chiave SSH : ${GIALLO}$([ "$DO_KEY" = "1" ] && echo SI || echo NO)${RESET}"
echo -e "  Autostart desktop   : ${GIALLO}$([ "$DO_AUTOSTART" = "1" ] && echo SI || echo NO)${RESET}"
echo -e "  Autologin + grafica : ${GIALLO}$([ "$DO_AUTOLOGIN" = "1" ] && echo SI || echo NO)${RESET}"
echo -e "  Dry-run             : ${GIALLO}$([ "$DRY_RUN" = "1" ] && echo SI || echo NO)${RESET}"
echo ""

if [ "$ASSUME_YES" != "1" ] && [ "$DRY_RUN" != "1" ]; then
    read -r -p "Confermi? [s/N] " RISP
    case "$RISP" in
        s|S|si|SI|y|Y|yes|YES) ;;
        *) fatal "Annullato dall'utente" ;;
    esac
fi

# Comando wrapper per dry-run
RUN() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] $*"
    else
        eval "$@"
    fi
}

# ---------------------------------------------------------------------------
#  [1/8] Copia file con whitelist (rsync)
# ---------------------------------------------------------------------------
echo ""
info "[1/8] Copia file TrackMind in $DEST_DIR ..."

if ! command -v rsync >/dev/null 2>&1; then
    fatal "rsync non installato sul PC. Installalo: sudo apt install rsync"
fi

RUN "mkdir -p '$DEST_DIR'"

# rsync con esclusioni esplicite. Usiamo --delete per pulizia totale del dest.
EXCLUDES=(
    --exclude='/dev/'
    --exclude='/dati/'
    --exclude='/backup/'
    --exclude='/cataloghi/'
    --exclude='/CLAUDE/'
    --exclude='/CLAUDE.md'
    --exclude='/File documentazione/'
    --exclude='/aggiornamento_uconsole/'
    --exclude='/installer/'
    --exclude='__pycache__/'
    --exclude='*.pyc'
    --exclude='/.git/'
    --exclude='/.gitignore'
    --exclude='/conf.dat'
    --exclude='/conf.dat.bak_prima_riparazione'
    --exclude='/conf.dat.bak*'
    --exclude='/api_key.txt'
    --exclude='/lancia_retrodb.bat'
    --exclude='/test_sync.py'
    --exclude='/sync_miscela.py'
    --exclude='/install.sh'
    --exclude='/Installa TrackMind.desktop'
    --exclude='/avvia.sh'
    --exclude='/trackmind.desktop'
    --exclude='*.bak'
    --exclude='/core/updater (copia).py'
    --exclude='/addons/dati/'
)

if [ "$DRY_RUN" = "1" ]; then
    rsync -aHn --delete "${EXCLUDES[@]}" "$SRC_DIR/" "$DEST_DIR/" | head -200
    info "(dry-run: prime 200 righe della copia)"
else
    rsync -aH --delete "${EXCLUDES[@]}" "$SRC_DIR/" "$DEST_DIR/"
fi

log "File copiati"

# ---------------------------------------------------------------------------
#  [2/8] Cartelle dati/ vuote + permessi
# ---------------------------------------------------------------------------
echo ""
info "[2/8] Crea cartelle dati/ vuote e imposta permessi ..."

RUN "mkdir -p '$DEST_DIR/dati/scouting'"
RUN "mkdir -p '$DEST_DIR/dati/backup'"
RUN "mkdir -p '$DEST_DIR/dati/loghi'"
RUN "mkdir -p '$DEST_DIR/backup'"

# Copia avvia.sh e trackmind.desktop personalizzati per l'utente
RUN "sed 's/__USER__/$TARGET_USER/g' '$TEMPLATES_DIR/avvia.sh' > '$DEST_DIR/avvia.sh'"
RUN "sed 's/__USER__/$TARGET_USER/g' '$TEMPLATES_DIR/trackmind.desktop' > '$DEST_DIR/trackmind.desktop'"
RUN "chmod +x '$DEST_DIR/avvia.sh'"
RUN "chmod +x '$DEST_DIR/retrodb.py' || true"

# Chown ricorsivo all'utente target
RUN "chown -R $TARGET_UID:$TARGET_GID '$TARGET_HOME/Documenti'"

log "Cartelle e permessi pronti"

# ---------------------------------------------------------------------------
#  [3/8] Inizializza conf.dat
# ---------------------------------------------------------------------------
echo ""
info "[3/8] Inizializza conf.dat di default (scala=1.5, fullscreen=1) ..."

# Lo facciamo con Python sul PC, sfruttando conf_manager.py copiato.
# Output diretto nel filesystem della SD.

if [ "$DRY_RUN" = "1" ]; then
    info "  [dry-run] (sarebbe creato $DEST_DIR/conf.dat)"
else
    python3 - <<PYEOF
import sys, os, datetime
sys.path.insert(0, "$DEST_DIR")
from conf_manager import DEFAULT_CONF, salva_conf

# salva_conf scrive sempre conf.dat nella cwd, quindi spostiamoci li'
os.chdir("$DEST_DIR")
conf = dict(DEFAULT_CONF)
conf["scala"] = 1.5
conf["fullscreen"] = 1
conf["stampante_bt"] = ""
conf["data_installazione"] = datetime.date.today().isoformat()
salva_conf(conf)
print("  conf.dat creato:", os.path.abspath("conf.dat"))
PYEOF
    chown "$TARGET_UID:$TARGET_GID" "$DEST_DIR/conf.dat"
fi

log "conf.dat inizializzato"

# ---------------------------------------------------------------------------
#  [4/8] Configura SSH
# ---------------------------------------------------------------------------
if [ "$DO_SSH" = "1" ]; then
    echo ""
    info "[4/8] Abilito SSH server ..."

    # Metodo Raspberry Pi: file vuoto 'ssh' nella partizione boot
    if [ -n "$SD_BOOT" ] && [ -d "$SD_BOOT" ]; then
        RUN "touch '$SD_BOOT/ssh'"
        log "  Creato $SD_BOOT/ssh (sshd attivato al primo boot)"
    fi

    # Backup safety: enable systemd unit ssh anche tramite symlink (utile su
    # immagini non Raspbian che ignorano il file /boot/ssh)
    SSH_UNIT="$SD_ROOT/lib/systemd/system/ssh.service"
    SSH_WANTS="$SD_ROOT/etc/systemd/system/multi-user.target.wants/ssh.service"
    if [ -f "$SSH_UNIT" ] && [ ! -e "$SSH_WANTS" ]; then
        RUN "mkdir -p '$SD_ROOT/etc/systemd/system/multi-user.target.wants'"
        RUN "ln -sf '/lib/systemd/system/ssh.service' '$SSH_WANTS'"
        log "  Symlink systemd creato (ssh.service abilitato)"
    fi
    # Stessa cosa per sshd.service (alias su alcune distro)
    SSHD_UNIT="$SD_ROOT/lib/systemd/system/sshd.service"
    SSHD_WANTS="$SD_ROOT/etc/systemd/system/multi-user.target.wants/sshd.service"
    if [ -f "$SSHD_UNIT" ] && [ ! -e "$SSHD_WANTS" ]; then
        RUN "mkdir -p '$SD_ROOT/etc/systemd/system/multi-user.target.wants'"
        RUN "ln -sf '/lib/systemd/system/sshd.service' '$SSHD_WANTS'"
        log "  Symlink systemd creato (sshd.service abilitato)"
    fi

    # Garantisce che PasswordAuthentication=yes sia attivo
    SSHD_CONF="$SD_ROOT/etc/ssh/sshd_config"
    if [ -f "$SSHD_CONF" ]; then
        if [ "$DRY_RUN" = "1" ]; then
            info "  [dry-run] (sarebbe assicurato PasswordAuthentication=yes in $SSHD_CONF)"
        else
            # Decommenta o aggiunge PasswordAuthentication yes
            if grep -qE '^[[:space:]]*PasswordAuthentication' "$SSHD_CONF"; then
                sed -i -E 's|^[[:space:]]*#?[[:space:]]*PasswordAuthentication.*|PasswordAuthentication yes|' "$SSHD_CONF"
            else
                echo "PasswordAuthentication yes" >> "$SSHD_CONF"
            fi
            # Pubkey auth esplicitamente on
            if grep -qE '^[[:space:]]*PubkeyAuthentication' "$SSHD_CONF"; then
                sed -i -E 's|^[[:space:]]*#?[[:space:]]*PubkeyAuthentication.*|PubkeyAuthentication yes|' "$SSHD_CONF"
            else
                echo "PubkeyAuthentication yes" >> "$SSHD_CONF"
            fi
            log "  sshd_config aggiornato (Password+Pubkey auth abilitati)"
        fi
    else
        warn "  sshd_config non trovato in $SSHD_CONF (sara' usato il default al primo avvio)"
    fi
else
    info "[4/8] Configurazione SSH saltata (--no-ssh)"
fi

# ---------------------------------------------------------------------------
#  [5/8] Chiave SSH del PC dell'utente reale
# ---------------------------------------------------------------------------
if [ "$DO_SSH" = "1" ] && [ "$DO_KEY" = "1" ]; then
    echo ""
    info "[5/8] Installazione chiave SSH ..."

    # SUDO_USER e' l'utente che ha lanciato sudo (l'umano vero)
    REAL_USER="${SUDO_USER:-$USER}"
    REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
    [ -z "$REAL_HOME" ] && REAL_HOME="/home/$REAL_USER"

    PUB_KEY=""
    for k in id_ed25519.pub id_rsa.pub id_ecdsa.pub; do
        if [ -f "$REAL_HOME/.ssh/$k" ]; then
            PUB_KEY="$REAL_HOME/.ssh/$k"
            break
        fi
    done

    # Genera chiave se non esiste
    if [ -z "$PUB_KEY" ]; then
        warn "  Nessuna chiave SSH trovata in $REAL_HOME/.ssh/"
        if [ "$DRY_RUN" = "1" ]; then
            info "  [dry-run] (sarebbe generata id_ed25519 senza passphrase)"
        else
            sudo -u "$REAL_USER" mkdir -p "$REAL_HOME/.ssh"
            sudo -u "$REAL_USER" ssh-keygen -t ed25519 -N "" -f "$REAL_HOME/.ssh/id_ed25519" -C "$REAL_USER@$(hostname)-trackmind"
            PUB_KEY="$REAL_HOME/.ssh/id_ed25519.pub"
            log "  Chiave generata: $PUB_KEY"
        fi
    else
        log "  Trovata: $PUB_KEY"
    fi

    SSH_DIR="$TARGET_HOME/.ssh"
    AUTH="$SSH_DIR/authorized_keys"

    if [ "$DRY_RUN" = "1" ]; then
        info "  [dry-run] (sarebbe aggiunta in $AUTH)"
    else
        mkdir -p "$SSH_DIR"
        # Aggiungi solo se non gia' presente
        KEY_LINE=$(cat "$PUB_KEY")
        if [ -f "$AUTH" ] && grep -qF "$KEY_LINE" "$AUTH"; then
            log "  Chiave gia' presente in $AUTH"
        else
            echo "$KEY_LINE" >> "$AUTH"
            log "  Chiave aggiunta in $AUTH"
        fi
        chmod 700 "$SSH_DIR"
        chmod 600 "$AUTH"
        chown -R "$TARGET_UID:$TARGET_GID" "$SSH_DIR"
    fi
else
    info "[5/8] Installazione chiave SSH saltata"
fi

# ---------------------------------------------------------------------------
#  [6/8] Autostart desktop
# ---------------------------------------------------------------------------
if [ "$DO_AUTOSTART" = "1" ]; then
    echo ""
    info "[6/8] Configura autostart desktop ..."

    AUTOSTART_DIR="$TARGET_HOME/.config/autostart"
    AUTOSTART_FILE="$AUTOSTART_DIR/trackmind.desktop"

    RUN "mkdir -p '$AUTOSTART_DIR'"
    RUN "sed 's/__USER__/$TARGET_USER/g' '$TEMPLATES_DIR/trackmind.desktop' > '$AUTOSTART_FILE'"
    RUN "chmod +x '$AUTOSTART_FILE'"
    RUN "chown -R $TARGET_UID:$TARGET_GID '$TARGET_HOME/.config'"

    log "  Autostart creato: $AUTOSTART_FILE"
else
    info "[6/8] Autostart saltato (--no-autostart)"
fi

# ---------------------------------------------------------------------------
#  [7/8] Autologin utente + boot grafico
# ---------------------------------------------------------------------------
if [ "$DO_AUTOLOGIN" = "1" ]; then
    echo ""
    info "[7/8] Configura autologin e boot grafico ..."

    # 1) Default systemd target = graphical.target (boot in GUI)
    DEFAULT_TARGET_LINK="$SD_ROOT/etc/systemd/system/default.target"
    RUN "ln -sf /lib/systemd/system/graphical.target '$DEFAULT_TARGET_LINK'"
    log "  default.target -> graphical.target"

    # 2) Display manager: preferisci lightdm se presente
    DM_LINK="$SD_ROOT/etc/systemd/system/display-manager.service"
    DM_FOUND=""
    for dm in lightdm gdm3 gdm sddm lxdm; do
        if [ -f "$SD_ROOT/lib/systemd/system/$dm.service" ]; then
            RUN "ln -sf '/lib/systemd/system/$dm.service' '$DM_LINK'"
            DM_FOUND="$dm"
            log "  display-manager.service -> $dm.service"
            break
        fi
    done
    if [ -z "$DM_FOUND" ]; then
        warn "  Nessun display manager trovato sull'immagine."
        warn "  Configurera' autologin console + startx come fallback."
    fi

    # 3) Autologin nel display manager
    if [ "$DM_FOUND" = "lightdm" ]; then
        LIGHTDM_CONF="$SD_ROOT/etc/lightdm/lightdm.conf"
        if [ "$DRY_RUN" = "1" ]; then
            info "  [dry-run] (configurerebbe autologin in $LIGHTDM_CONF)"
        else
            mkdir -p "$SD_ROOT/etc/lightdm"
            # Usa configparser via Python per gestire bene [Seat:*]
            python3 - <<PYEOF
import configparser, os
path = "$LIGHTDM_CONF"
cp = configparser.RawConfigParser(strict=False, allow_no_value=True)
cp.optionxform = str  # preserva case
if os.path.exists(path):
    cp.read(path)
if not cp.has_section("Seat:*"):
    cp.add_section("Seat:*")
cp.set("Seat:*", "autologin-user", "$TARGET_USER")
cp.set("Seat:*", "autologin-user-timeout", "0")
with open(path, "w") as f:
    cp.write(f)
print("  Aggiornato:", path)
PYEOF
            # Il gruppo nopasswdlogin evita il prompt di password PAM
            NOPASS_GRP="$SD_ROOT/etc/group"
            if [ -f "$NOPASS_GRP" ] && ! grep -qE "^nopasswdlogin:" "$NOPASS_GRP"; then
                echo "nopasswdlogin:x:65400:$TARGET_USER" >> "$NOPASS_GRP"
                log "  Gruppo nopasswdlogin creato con $TARGET_USER dentro"
            elif [ -f "$NOPASS_GRP" ] && ! grep -qE "^nopasswdlogin:.*[:,]$TARGET_USER(,|$)" "$NOPASS_GRP"; then
                sed -i -E "s|^(nopasswdlogin:[^:]*:[^:]*:)(.*)$|\1\2,$TARGET_USER|" "$NOPASS_GRP"
                sed -i -E "s|,,|,|g; s|:,|:|g" "$NOPASS_GRP"
                log "  $TARGET_USER aggiunto a nopasswdlogin"
            fi
            log "  Autologin LightDM configurato per '$TARGET_USER'"
        fi
    fi

    # 4) Fallback: getty@tty1 autologin + startx (se nessun DM)
    if [ -z "$DM_FOUND" ]; then
        GETTY_DROPIN="$SD_ROOT/etc/systemd/system/getty@tty1.service.d"
        RUN "mkdir -p '$GETTY_DROPIN'"
        if [ "$DRY_RUN" = "1" ]; then
            info "  [dry-run] (creerebbe $GETTY_DROPIN/autologin.conf)"
        else
            cat > "$GETTY_DROPIN/autologin.conf" <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $TARGET_USER --noclear %I \$TERM
EOF
            log "  getty@tty1 autologin -> $TARGET_USER"
            # .bash_profile con startx su tty1
            BP="$TARGET_HOME/.bash_profile"
            if [ ! -f "$BP" ] || ! grep -q "startx" "$BP"; then
                cat >> "$BP" <<'EOF'

# TrackMind: avvia X automaticamente su tty1
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    exec startx
fi
EOF
                chown "$TARGET_UID:$TARGET_GID" "$BP"
                log "  .bash_profile con startx creato"
            fi
        fi
    fi

    log "Autologin + boot grafico configurati"
else
    info "[7/8] Autologin saltato (--no-autologin)"
fi

# ---------------------------------------------------------------------------
#  [8/8] Sudoers NOPASSWD per shutdown/poweroff/reboot
# ---------------------------------------------------------------------------
#  Permette a TrackMind di spegnere la uConsole dal bottone SPEGNI senza che
#  sudo chieda la password (non c'e' un tty disponibile dopo root.destroy()).
#  File: /etc/sudoers.d/trackmind con permessi 440.
# ---------------------------------------------------------------------------
echo ""
info "[8/8] Configura sudoers NOPASSWD per shutdown/poweroff/reboot ..."

SUDOERS_DIR="$SD_ROOT/etc/sudoers.d"
SUDOERS_FILE="$SUDOERS_DIR/trackmind"

RUN "mkdir -p '$SUDOERS_DIR'"
if [ "$DRY_RUN" = "1" ]; then
    info "  [dry-run] scriverebbe regola NOPASSWD per $TARGET_USER in $SUDOERS_FILE"
else
    cat > "$SUDOERS_FILE" <<EOF
# Generato da prepara_sd.sh - TrackMind 5.4
# Permette il bottone SPEGNI e il comando shutdown da TrackMind senza
# richiedere la password. Coinvolge solo i binari di spegnimento/riavvio.
$TARGET_USER ALL=(ALL) NOPASSWD: /sbin/shutdown, /sbin/poweroff, /sbin/reboot, /usr/sbin/shutdown, /usr/sbin/poweroff, /usr/sbin/reboot
EOF
    chmod 440 "$SUDOERS_FILE"
    chown 0:0 "$SUDOERS_FILE"
    log "  Sudoers installato: $SUDOERS_FILE"
fi

# ---------------------------------------------------------------------------
#  Riepilogo finale
# ---------------------------------------------------------------------------
echo ""
echo -e "${VERDE}=============================================================${RESET}"
echo -e "${VERDE}  PREPARAZIONE COMPLETATA${RESET}"
echo -e "${VERDE}=============================================================${RESET}"
echo ""

if [ "$DRY_RUN" = "1" ]; then
    warn "Questa era una DRY-RUN: nessun file e' stato realmente scritto."
    exit 0
fi

# Statistiche
N_FILES=$(find "$DEST_DIR" -type f | wc -l)
SIZE=$(du -sh "$DEST_DIR" | cut -f1)

echo -e "  File installati  : ${GIALLO}$N_FILES${RESET}"
echo -e "  Spazio occupato  : ${GIALLO}$SIZE${RESET}"
echo -e "  Path destinazione: ${GIALLO}$DEST_DIR${RESET}"
echo ""

if [ "$DO_SSH" = "1" ]; then
    REAL_USER="${SUDO_USER:-$USER}"
    SD_HOSTNAME=$(cat "$SD_ROOT/etc/hostname" 2>/dev/null || echo "raspberrypi")
    echo -e "  ${CIANO}SSH al primo boot:${RESET}"
    echo -e "    ssh $TARGET_USER@$SD_HOSTNAME.local"
    echo -e "    (oppure ssh $TARGET_USER@<IP-uConsole>)"
    echo ""
fi

echo -e "  ${CIANO}Prossimi passi:${RESET}"
echo "    1. Smonta in sicurezza la SD da questo PC"
echo "    2. Inserisci la SD nella uConsole"
echo "    3. Avvia: TrackMind partira' automaticamente al login"
echo ""
echo -e "  ${CIANO}Note feature CRONO LIVE (ricevitore LapMonitor via BT):${RESET}"
echo "    Al primo boot avvia.sh tenta automaticamente:"
echo "      pip install bleak --break-system-packages"
echo "    Se serve la rete wi-fi, collegati prima via HOTSPOT."
echo "    Log installazione: dati/bleak_install.log"
echo ""
log "Fatto."
