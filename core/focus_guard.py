"""core/focus_guard.py - Protezione da popup di sistema (uConsole/Linux).

PROBLEMA (uConsole):
    Su uConsole il window manager (Openbox / ClockworkPi OS) lascia
    apparire sopra TrackMind popup di sistema indesiderati:
      - notifiche NetworkManager (connessione persa, richieste keyring)
      - prompt Bluetooth (pairing, accoppiamento stampante termica)
      - avvisi power management / batteria scarica
      - dialog di polkit / autenticazione
      - update manager, PackageKit, aptd
    Questi popup rubano focus e copertura visiva: durante un cronometraggio
    live puo' voler dire perdere un giro o non riuscire a premere SPAZIO.

SOLUZIONE:
    Questo modulo forza la finestra TrackMind a restare sempre in primo
    piano (attributo Tk -topmost) e, a intervalli regolari, chiama lift()
    per "ricoprire" eventuali popup gia' apparsi sopra. Se un popup ruba
    il focus, basta che l'utente prema un tasto sulla finestra per
    riprenderlo: la finestra e' gia' visibile sopra.

    NON tenta di chiudere i popup automaticamente (rischio troppo alto).
    Li lascia aperti ma invisibili finche' non si esce da TrackMind.

USO:
    from core.focus_guard import proteggi_finestra, sblocca_finestra
    proteggi_finestra(self.root)              # attivazione (idempotente)
    sblocca_finestra(self.root)               # disattiva prima di chiudere

SICUREZZA:
    - E' sicuro chiamare proteggi_finestra() piu' volte sulla stessa root
      (deduplica via id interno).
    - Su Windows e Mac e' un no-op quasi totale (topmost attivo, utile per
      testing): non interferisce.
    - Se la finestra viene distrutta, la guardia si ferma da sola.
"""

import sys

# Intervallo ri-lift: 2s e' un compromesso fra reattivita' e carico CPU.
# Su uConsole (ARM) intervalli piu' bassi scaldano il processore.
_INTERVALLO_MS_DEFAULT = 2000

# Dedup interno: finestre gia' protette (identificate per id)
_guard_attivo = set()


def proteggi_finestra(root, intervallo_ms=_INTERVALLO_MS_DEFAULT, topmost=True):
    """Mantiene la finestra root in primo piano contro popup di sistema.

    Parametri:
        root          -- istanza tk.Tk o tk.Toplevel gia' creata
        intervallo_ms -- periodo di ri-lift in millisecondi (default 2000)
        topmost       -- se True attiva l'attributo -topmost (default True)

    La funzione e' idempotente: chiamarla due volte non raddoppia la guardia.
    """
    try:
        rid = id(root)
    except Exception:
        return
    if rid in _guard_attivo:
        return
    _guard_attivo.add(rid)

    # Attiva topmost: su X11/Windows la finestra resta sopra a tutte le altre.
    # Su uConsole/Openbox funziona: il WM rispetta _NET_WM_STATE_ABOVE.
    if topmost:
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

    def _guardia():
        # Se la finestra e' stata distrutta fermiamo il loop.
        try:
            if not root.winfo_exists():
                _guard_attivo.discard(rid)
                return
        except Exception:
            _guard_attivo.discard(rid)
            return

        # Re-applica topmost (alcuni WM lo resettano dopo dialog esterni)
        # e solleva la finestra sopra qualunque popup apparso nel frattempo.
        try:
            if topmost:
                root.attributes("-topmost", True)
            root.lift()
        except Exception:
            pass

        # Riprogramma il prossimo giro
        try:
            root.after(intervallo_ms, _guardia)
        except Exception:
            _guard_attivo.discard(rid)

    try:
        root.after(intervallo_ms, _guardia)
    except Exception:
        _guard_attivo.discard(rid)


def sblocca_finestra(root):
    """Rimuove topmost e ferma la guardia periodica su questa finestra.

    Utile prima di aprire dialog di sistema (es. filedialog nativi) che
    devono potersi sovrapporre, oppure durante l'uscita pulita dell'app.
    """
    try:
        rid = id(root)
    except Exception:
        return
    _guard_attivo.discard(rid)
    try:
        root.attributes("-topmost", False)
    except Exception:
        pass


def proteggi_finestra_sicura(root, **kwargs):
    """Variante che ingoia qualsiasi eccezione. Utile in punti di
    inizializzazione dove il fallimento del focus_guard non deve impedire
    l'avvio dell'app (es. su platform o WM non supportati)."""
    try:
        proteggi_finestra(root, **kwargs)
    except Exception:
        pass
