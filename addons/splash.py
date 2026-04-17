"""
RetroDB — Splash Screen (Matrix Rain)
Modulo standalone da importare in retrodb.py

Uso:
    from splash import mostra_splash
    
    def main():
        ...
        root = tk.Tk()
        root.withdraw()
        mostra_splash(root)      # blocca finché non si chiude
        root.deiconify()
        app = RetroDBApp(root)
        root.mainloop()
"""

import tkinter as tk
import random
import sys

# ─── Legge i colori da colori.cfg se disponibile, altrimenti default ───
try:
    from config_colori import carica_colori, FONT_MONO
    _COLORI = carica_colori()
except Exception:
    _COLORI = {
        "sfondo": "#0a0a0a", "dati": "#39ff14", "label": "#22aa22",
        "testo_dim": "#1a6a1a", "stato_avviso": "#ffaa00", "linee": "#144a14",
    }
    FONT_MONO = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"

# Versione — importata dal single source of truth
try:
    from version import __version__ as APP_VERSION
except Exception:
    APP_VERSION = "?"


def mostra_splash(root, durata_ms=10000):
    """
    Mostra la splash screen Matrix Rain direttamente sulla root window.
    Blocca l'esecuzione fino a chiusura (click / tasto / timeout).
    Al termine pulisce la root per l'app principale.
    
    Args:
        root:       finestra Tk principale
        durata_ms:  auto-chiusura in millisecondi (default 10 sec)
    """
    c = _COLORI
    BG    = c.get("sfondo", "#0a0a0a")
    GREEN = c.get("dati", "#39ff14")
    DIM   = c.get("testo_dim", "#1a6a1a")
    LABEL = c.get("label", "#22aa22")
    AMBER = c.get("stato_avviso", "#ffaa00")
    LINE  = c.get("linee", "#144a14")

    # Usa la root direttamente (niente Toplevel)
    sp = root
    sp.configure(bg=BG)
    sp.overrideredirect(True)      # Senza bordi/titolo durante la splash
    sp.attributes("-topmost", True)

    # ─── Dimensioni ───
    W, H = 640, 360
    sx = (sp.winfo_screenwidth()  - W) // 2
    sy = (sp.winfo_screenheight() - H) // 2
    sp.geometry(f"{W}x{H}+{sx}+{sy}")
    sp.deiconify()

    canvas = tk.Canvas(sp, width=W, height=H, bg=BG, highlightthickness=0)
    canvas.pack()

    # ─── Font ───
    F_TINY  = (FONT_MONO, 8)
    F_SMALL = (FONT_MONO, 10)
    F_MED   = (FONT_MONO, 12)
    F_BIG   = (FONT_MONO, 16, "bold")
    F_TITLE = (FONT_MONO, 20, "bold")

    # ─── Pioggia Matrix ───
    GLYPHS = "01アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
    COL_W = 14
    COLS  = W // COL_W

    drops = []
    drop_items = []

    for col in range(COLS):
        x = col * COL_W + COL_W // 2
        y = random.randint(-H * 2, -10)
        speed = random.uniform(3.0, 10.0)
        trail_len = random.randint(6, 20)
        items = []
        for t in range(trail_len):
            # Testa brillante, coda sfumata
            if t == 0:
                fill = GREEN
            elif t < 3:
                fill = LABEL
            else:
                fill = DIM
            opacity = max(0.08, 1.0 - (t / trail_len))
            item = canvas.create_text(
                x, y - t * 15,
                text=random.choice(GLYPHS),
                fill=fill, font=F_TINY,
                anchor="center"
            )
            # Simula opacità tramite stipple per le code più lontane
            if t > trail_len // 2:
                canvas.itemconfig(item, stipple="gray50")
            items.append(item)
        drops.append({"x": x, "y": y, "speed": speed, "trail": trail_len})
        drop_items.append(items)

    # ─── Logo (nascosto inizialmente) ───
    logo_group = []
    phase = [0]          # 0=solo pioggia, 1=logo visibile
    frame_count = [0]

    def _crea_logo():
        """Crea gli elementi del logo con effetto reveal."""
        # Sfondo scuro semi-trasparente
        pad = 8
        bx, by, bw, bh = 100, 55, W - 200, 250
        # Box esterno
        logo_group.append(canvas.create_rectangle(
            bx, by, bx + bw, by + bh,
            fill=BG, outline=LINE, width=1
        ))
        # Doppio bordo
        logo_group.append(canvas.create_rectangle(
            bx + pad, by + pad, bx + bw - pad, by + bh - pad,
            fill="", outline=DIM, width=1, dash=(3, 3)
        ))

        # ── Cornice decorativa top ──
        deco_top = "╔══════════════════════════════════════╗"
        logo_group.append(canvas.create_text(
            W // 2, by + 30, text=deco_top, fill=DIM, font=F_SMALL, anchor="center"
        ))

        # ── Nome app grande ──
        logo_group.append(canvas.create_text(
            W // 2, by + 58, text="T R A C K M I N D",
            fill=GREEN, font=F_TITLE, anchor="center"
        ))

        # ── Cornice decorativa bottom ──
        deco_bot = "╚══════════════════════════════════════╝"
        logo_group.append(canvas.create_text(
            W // 2, by + 85, text=deco_bot, fill=DIM, font=F_SMALL, anchor="center"
        ))

        # ── Separatore ──
        logo_group.append(canvas.create_text(
            W // 2, by + 110, text="━" * 34,
            fill=DIM, font=F_SMALL, anchor="center"
        ))

        # ── Versione ──
        logo_group.append(canvas.create_text(
            W // 2, by + 135, text=f"version {APP_VERSION}",
            fill=AMBER, font=F_BIG, anchor="center"
        ))

        # ── Sottotitolo ──
        logo_group.append(canvas.create_text(
            W // 2, by + 165, text="RC Car Setup Database",
            fill=LABEL, font=F_MED, anchor="center"
        ))

        # ── Motto ──
        logo_group.append(canvas.create_text(
            W // 2, by + 190, text="Track · Setup · Perform",
            fill=DIM, font=F_SMALL, anchor="center"
        ))

        # ── Separatore basso ──
        logo_group.append(canvas.create_text(
            W // 2, by + 215, text="━" * 34,
            fill=DIM, font=F_SMALL, anchor="center"
        ))

        # ── Hint ──
        logo_group.append(canvas.create_text(
            W // 2, by + 238, text="[ press any key ]",
            fill=DIM, font=F_SMALL, anchor="center"
        ))

        # Tutti nascosti — li facciamo apparire uno a uno
        for item in logo_group:
            canvas.itemconfig(item, state="hidden")

    _crea_logo()

    # ─── Reveal progressivo del logo ───
    reveal_idx = [0]
    reveal_delay = 80  # ms tra un elemento e l'altro

    def _reveal_next():
        if not sp.winfo_exists() or _done.get():
            return
        idx = reveal_idx[0]
        if idx < len(logo_group):
            canvas.itemconfig(logo_group[idx], state="normal")
            # Alza sopra la pioggia
            canvas.tag_raise(logo_group[idx])
            reveal_idx[0] += 1
            sp.after(reveal_delay, _reveal_next)
        else:
            # Reveal completo — avvia blink hint
            _blink_hint()

    # ─── Blink "press any key" ───
    def _blink_hint():
        if not sp.winfo_exists() or not logo_group or _done.get():
            return
        hint_item = logo_group[-1]
        vis = [True]
        def _toggle():
            if not sp.winfo_exists() or _done.get():
                return
            vis[0] = not vis[0]
            canvas.itemconfig(hint_item, fill=DIM if vis[0] else BG)
            canvas.tag_raise(hint_item)
            sp.after(500, _toggle)
        _toggle()

    # ─── Animazione pioggia ───
    def _animate():
        if not sp.winfo_exists() or _done.get():
            return
        frame_count[0] += 1

        for i, (drop, items) in enumerate(zip(drops, drop_items)):
            drop["y"] += drop["speed"]
            x = drop["x"]
            for t, item in enumerate(items):
                ny = drop["y"] - t * 15
                canvas.coords(item, x, ny)
                # Cambio casuale del glifo (effetto "digitale")
                if random.random() < 0.12:
                    canvas.itemconfig(item, text=random.choice(GLYPHS))

            # Reset quando la coda esce dallo schermo
            total_len = drop["trail"] * 15
            if drop["y"] - total_len > H:
                drop["y"] = random.randint(-120, -30)
                drop["speed"] = random.uniform(3.0, 10.0)

        # Dopo ~2 secondi (60 frame a 33ms) → mostra logo
        if phase[0] == 0 and frame_count[0] > 60:
            phase[0] = 1
            _reveal_next()

        sp.after(33, _animate)  # ~30 fps

    # ─── Scanlines sottili per effetto CRT ───
    for sy in range(0, H, 3):
        line = canvas.create_line(0, sy, W, sy, fill="#000000", stipple="gray12")
        canvas.tag_raise(line)

    # ─── Chiusura ───
    _done = tk.BooleanVar(value=False)

    def _close(e=None):
        if _done.get():
            return
        _done.set(True)
        # Pulisci tutto e ripristina la root come finestra normale
        try:
            sp.unbind("<Button-1>")
            sp.unbind("<Key>")
            for w in sp.winfo_children():
                w.destroy()
            sp.overrideredirect(False)     # Ripristina bordi e titolo
            sp.attributes("-topmost", False)
        except Exception:
            pass

    sp.bind("<Button-1>", _close)
    sp.bind("<Key>", _close)

    # Focus per catturare i tasti
    sp.focus_force()

    # Avvia animazione e timer auto-chiusura
    sp.after(100, _animate)
    sp.after(durata_ms, _close)

    # Blocca finché la splash non è chiusa
    sp.wait_variable(_done)


# ─── Test standalone ───
if __name__ == "__main__":
    root = tk.Tk()
    mostra_splash(root)
    root.destroy()
    print("Splash completata!")
