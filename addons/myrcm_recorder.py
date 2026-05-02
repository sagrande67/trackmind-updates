"""
TrackMind - MyRCM Live Recorder
================================
Recorder background che ascolta MyRCM live e salva i tempi della
categoria selezionata in `dati/scouting/lap_myrcm_*.json` man mano
che le manche/batterie finiscono.

Strategia: scopo del recorder NON e' ricostruire i tempi giro dal
flusso WebSocket (che da' solo last lap + best + ABSOLUTTIME senza
storico completo), ma USARE il flusso live come "trigger di sessione
finita" per scaricare il report HTML completo (lo stesso usato da
RICERCA manuale in NUOVA LETTURA). I report HTML hanno tutti i tempi
giro ma vengono pubblicati con qualche secondo di ritardo. Quindi:

  1) Connetti al WebSocket
  2) Tieni traccia del GROUP corrente (es. "GT8_SPORT :: Prove ::
     Batteria 2 - Manche 1") e dello stato RACESTATE
  3) Quando il GROUP cambia o si entra in rsFinished/rsClosed,
     attendi qualche secondo (settling time per MyRCM che pubblica
     il report) poi scarica via HTTP la categoria con
     `import_evento_completo(category_filter=...)`. I file scouting
     finiscono in `dati/scouting/` esattamente come l'import manuale.
  4) Notifica via callback `on_sessione_salvata(group, n_files)`

Cosi' al rientro ai box il pilota apre TUTTI I TEMPI e trova gia'
tutto, senza fare nulla.
"""

import os
import threading
import time

from core.myrcm_ws import MyRcmWsClient

try:
    from myrcm_import import import_evento_completo
    _HAS_MYRCM = True
except Exception:
    _HAS_MYRCM = False


# Secondi da aspettare dopo "rsFinished" prima di tentare il download
# del report HTML. MyRCM tipicamente pubblica il report entro 5-15s
# dalla bandiera a scacchi.
_DELAY_DOWNLOAD = 15


class MyRcmLiveRecorder(object):
    """Registratore live silenzioso di una categoria MyRCM.

    Param:
        event_id (str): ID evento MyRCM
        scouting_dir (str): cartella dati/scouting/
        category_filter_nome (str|None): se passato, filtra il
            download solo a quella categoria (match per substring
            del nome); None = scarica tutte le categorie del GROUP
            che cambia.
        on_sessione_salvata (callable|None): callback(group_str,
            n_files_salvati) chiamato dopo ogni download riuscito.
        on_status (callable|None): callback(msg) per indicatori UI.
        tk_root (tk widget|None): se passato, le callback sono
            schedulate via root.after() (thread-safe per la UI).

    Uso:
        rec = MyRcmLiveRecorder(
            event_id="96297",
            scouting_dir="/path/dati/scouting",
            category_filter_nome="GT8_SPORT",
        )
        rec.start()
        ...
        rec.stop()
    """

    def __init__(self, event_id, scouting_dir,
                 category_filter_nome=None,
                 on_sessione_salvata=None,
                 on_status=None,
                 tk_root=None):
        self.event_id = str(event_id)
        self.scouting_dir = scouting_dir
        self.category_filter_nome = category_filter_nome
        self._cb_saved = on_sessione_salvata
        self._cb_status = on_status
        self._tk_root = tk_root

        self._ws = None
        self._stop = threading.Event()

        # Stato sessione corrente
        self._cur_group = None
        self._cur_state = None
        self._cur_meta = {}      # ultima METADATA vista
        self._n_piloti_live = 0  # ultimo numero piloti nel GROUP
        # Lock per evitare doppi download concorrenti
        self._dl_lock = threading.Lock()
        self._sessioni_salvate = []  # lista (group, ts, n_files)
        # Tieni l'ultimo group "snapshot" per il download asincrono:
        # ricordo il group della sessione che sta finendo cosi' lo
        # passo al thread di download (nel frattempo il GROUP corrente
        # sul WS puo' gia' essere cambiato).
        self._pending_dl = None  # dict {group, event_nome}
        # Listeners per UI live: ad ogni EVENT WS, oltre al processo
        # interno per il salvataggio, propaghiamo (metadata, data) ai
        # listener registrati. Cosi' la UI live (myrcm_live_ui) puo'
        # mostrare tempi in real-time riusando la stessa connessione.
        self._event_listeners = []   # callable(meta, data)
        self._ultima_data = []

    # ---- API ----
    def start(self):
        if not _HAS_MYRCM:
            self._notify_status("MyRCM import non disponibile")
            return False
        if self._ws is not None:
            return True
        self._stop.clear()
        self._ws = MyRcmWsClient(
            event_id=self.event_id,
            on_event=self._on_event,
            on_status=self._notify_status,
            on_error=self._notify_status,
            tk_root=self._tk_root,
        )
        self._ws.start()
        self._notify_status("Recorder MyRCM avviato (evento %s)"
                             % self.event_id)
        return True

    def stop(self):
        self._stop.set()
        if self._ws is not None:
            self._ws.stop()
            self._ws = None
        # Se stava per partire un download, prova a finalizzarlo
        if self._pending_dl is not None:
            self._dl_thread_run(self._pending_dl)
            self._pending_dl = None

    def stato(self):
        """Snapshot dello stato per UI: gruppo corrente + n piloti +
        sessioni salvate finora."""
        return {
            "connected": self._ws.is_connected() if self._ws else False,
            "group": self._cur_group,
            "state": self._cur_state,
            "n_piloti": self._n_piloti_live,
            "n_sessioni_salvate": len(self._sessioni_salvate),
            "ultime": list(self._sessioni_salvate[-3:]),
        }

    def piloti_live(self):
        """Lista dict piloti dell'ultimo EVENT (per UI live).
        Ritorna una snapshot della struttura DATA cosi' come arriva
        da MyRCM."""
        return list(getattr(self, "_ultima_data", []) or [])

    def metadata_live(self):
        return dict(self._cur_meta or {})

    def add_event_listener(self, callback):
        """Aggiungi un listener chiamato a ogni EVENT WS con
        (metadata, data). Idempotente."""
        if callable(callback) and callback not in self._event_listeners:
            self._event_listeners.append(callback)

    def remove_event_listener(self, callback):
        try:
            self._event_listeners.remove(callback)
        except ValueError:
            pass

    # ---- Logica WebSocket ----
    def _on_event(self, metadata, data):
        """Chiamato a ogni EVENT MyRCM."""
        self._cur_meta = metadata or {}
        self._ultima_data = data or []
        self._n_piloti_live = len(data or [])
        # Propaga a tutti i listener UI registrati (best-effort)
        for cb in list(self._event_listeners):
            try:
                cb(metadata, data)
            except Exception as e:
                print("[recorder] listener error:", e)
        new_group = (metadata or {}).get("GROUP", "") or ""
        new_state = (metadata or {}).get("RACESTATE", "") or ""
        # Trigger 1: cambio di GROUP
        if (self._cur_group is not None
                and new_group != self._cur_group
                and self._cur_group):
            self._schedula_download(self._cur_group, motivo="cambio group")
        # Trigger 2: passaggio a rsFinished (sessione finita)
        elif (new_state in ("rsFinished", "rsClosed")
              and self._cur_state not in ("rsFinished", "rsClosed")
              and new_group):
            # Per rsFinished aspetta _DELAY_DOWNLOAD prima di scaricare
            self._schedula_download(new_group, motivo="finished",
                                    delay=_DELAY_DOWNLOAD)
        self._cur_group = new_group
        self._cur_state = new_state

    def _schedula_download(self, group, motivo="", delay=10):
        """Schedula il download HTML del report dopo `delay` secondi
        in un thread separato, cosi' il loop WebSocket non si blocca."""
        if not group:
            return
        ev_nome = (self._cur_meta.get("NAME", "") or "").strip()
        snapshot = {
            "group": group,
            "event_nome": ev_nome,
            "motivo": motivo,
        }
        self._pending_dl = snapshot
        self._notify_status(
            "Sessione finita (%s): download tra %ds..."
            % (group[:60], delay))
        t = threading.Thread(
            target=self._dl_thread_wait_then_run,
            args=(snapshot, delay),
            name="MyRcmDL",
            daemon=True)
        t.start()

    def _dl_thread_wait_then_run(self, snapshot, delay):
        """Attende delay sec poi esegue il download."""
        # Sleep interrompibile via _stop
        if self._stop.wait(timeout=delay):
            return
        self._dl_thread_run(snapshot)

    def _dl_thread_run(self, snapshot):
        """Esegue il download del report. Salva file in scouting_dir."""
        with self._dl_lock:
            if not _HAS_MYRCM:
                print("[recorder] MyRCM module non disponibile")
                return
            ev_nome = snapshot.get("event_nome", "")
            group = snapshot.get("group", "")
            try:
                from datetime import datetime as _dt
                data_str = _dt.now().strftime("%d/%m/%Y")
                print("[recorder] DOWNLOAD START: ev=%r data=%s "
                      "group=%r" % (ev_nome[:50], data_str,
                                     group[:60]))
                self._notify_status(
                    "Scarico report MyRCM per: %s..." % group[:60])
                saved, ev_nome_ret = import_evento_completo(
                    nome_pista=ev_nome or "MyRCM",
                    data_str=data_str,
                    scouting_dir=self.scouting_dir,
                    pilota_filtro=None,
                    setup_snapshot=None)
                n = len(saved or [])
                self._sessioni_salvate.append((group, time.time(), n))
                print("[recorder] DOWNLOAD END: %d file salvati per "
                      "group=%r" % (n, group[:60]))
                self._notify_status(
                    "Salvati %d file scouting per %s" % (n, group[:60]))
                if self._cb_saved is not None:
                    try:
                        if self._tk_root is not None:
                            self._tk_root.after(
                                0,
                                lambda g=group, nn=n:
                                    self._cb_saved(g, nn))
                        else:
                            self._cb_saved(group, n)
                    except Exception:
                        pass
            except Exception as e:
                self._notify_status(
                    "Errore download MyRCM: %s" % str(e)[:100])

    def _notify_status(self, msg):
        if self._cb_status is None:
            return
        try:
            if self._tk_root is not None:
                self._tk_root.after(0, lambda: self._cb_status(msg))
            else:
                self._cb_status(msg)
        except Exception:
            pass
