"""
Audio/orb_overlay.py

A small floating voice orb, similar to Gemini/Siri's listening indicator:
- Transparent, borderless, always-on-top window near the top-center of the screen
- Hidden until show() is called (e.g. right when the wake word fires)
- Pulses in radius/color based on live audio level via update_level(rms)
- hide() to dismiss it (e.g. after a response finishes / follow-up window times out)

Runs its own Tkinter window on a dedicated thread. All public methods
(show/hide/update_level) are thread-safe — call them freely from mic
callback threads, asyncio tasks, or anywhere else in the app.
"""

import tkinter as tk
import queue
import threading


class OrbOverlay:
    def __init__(
        self,
        size: int = 90,
        top_margin: int = 20,
        color_idle: str = "#00e5ff",
        color_active: str = "#7c4dff",
    ):
        self._size = size
        self._top_margin = top_margin
        self._color_idle = color_idle
        self._color_active = color_active

        self._queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._ready = threading.Event()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait()  # block __init__ until the window actually exists

    # ─── Internal: runs entirely on the Tkinter thread ───

    def _run(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)        # no title bar / border
        # stay above every other window
        self.root.attributes("-topmost", True)

        # arbitrary color used as the "see-through" key
        transparent_key = "magenta"
        self.root.attributes("-transparentcolor", transparent_key)
        self.root.configure(bg=transparent_key)

        screen_w = self.root.winfo_screenwidth()
        x = (screen_w - self._size) // 2
        y = self._top_margin
        self.root.geometry(f"{self._size}x{self._size}+{x}+{y}")

        self.canvas = tk.Canvas(
            self.root,
            width=self._size,
            height=self._size,
            bg=transparent_key,
            highlightthickness=0,
        )
        self.canvas.pack()

        self._circle = self.canvas.create_oval(
            0, 0, 0, 0, fill=self._color_idle, outline="")
        self._draw_level(0.0)

        self.root.withdraw()  # start hidden
        self._ready.set()

        self._poll_queue()
        self.root.mainloop()

    def _poll_queue(self):
        try:
            while True:
                action, payload = self._queue.get_nowait()
                if action == "show":
                    self.root.deiconify()
                elif action == "hide":
                    self.root.withdraw()
                elif action == "level":
                    self._draw_level(payload)
        except queue.Empty:
            pass
        # ~50fps poll, cheap and smooth enough
        self.root.after(20, self._poll_queue)

    def _draw_level(self, level: float):
        level = max(0.0, min(1.0, level))
        min_r = self._size * 0.22
        max_r = self._size * 0.48
        r = min_r + (max_r - min_r) * level

        cx = cy = self._size / 2
        color = self._color_active if level > 0.04 else self._color_idle

        self.canvas.coords(self._circle, cx - r, cy - r, cx + r, cy + r)
        self.canvas.itemconfig(self._circle, fill=color)

    # ─── Public API — safe to call from ANY thread ───

    def show(self):
        self._queue.put(("show", None))

    def hide(self):
        self._queue.put(("hide", None))

    def update_level(self, rms: float):
        """rms should be roughly 0.0 (silence) to 1.0 (loud) — normalize your
        audio chunk's amplitude before calling this."""
        self._queue.put(("level", rms))


_orb: OrbOverlay | None = None


def get_orb() -> OrbOverlay:
    """Lazy singleton — the orb window is created on first use, reused after."""
    global _orb
    if _orb is None:
        _orb = OrbOverlay()
    return _orb
