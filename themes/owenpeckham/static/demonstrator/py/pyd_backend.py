"""pyd_backend -- the matplotlib backend used inside the demonstrator worker.

Selected with ``MPLBACKEND=module://pyd_backend`` before the project runs, so
unmodified scripts get it automatically.  It renders with Agg (there is no DOM
in a Web Worker) and replaces the GUI event loop with the page:

* ``plt.show()`` registers the open figures with the runtime and returns
  immediately (the page keeps them alive and displays them);
* ``fig.canvas.new_timer()`` returns a :class:`TimerPyd`, which never fires
  on its own: the page "ticks" the runtime, and the runtime fires due timers
  between frames.  ``FuncAnimation``/``ArtistAnimation`` therefore run frame
  by frame under the page's control (pause, step, speed) without changes;
* ``draw_idle()`` only marks the figure dirty; the runtime draws it once when
  the frame is captured;
* ``start_event_loop()`` (used by ``plt.pause``) and ``flush_events()`` hand
  control back to the runtime, which sends a frame and -- where the browser
  supports JS Promise Integration -- lets the page pause, step or stop the
  script and apply live parameter changes.

Works with matplotlib 3.6+ (Pyodide 0.29 ships 3.8.4).
"""
from matplotlib.backend_bases import FigureManagerBase, TimerBase
from matplotlib.backends.backend_agg import FigureCanvasAgg

__all__ = ["FigureCanvas", "FigureManager", "show", "TimerPyd", "FigureCanvasPyd", "FigureManagerPyd"]

backend_version = "pyd-1"

# Set by pyd_runtime.Runtime when it installs itself.
RUNTIME = None


class TimerPyd(TimerBase):
    """A timer driven by the page's clock instead of a GUI event loop."""

    def __init__(self, *args, **kwargs):
        # TimerBase.__init__ sets ``interval``, which calls _timer_set_interval,
        # so these must exist first.
        self._pyd_running = False
        self._pyd_due = 0.0
        self._pyd_canvas = kwargs.pop("_pyd_canvas", None)
        super().__init__(*args, **kwargs)
        if RUNTIME is not None:
            RUNTIME.register_timer(self)

    def _timer_start(self):
        now = RUNTIME.clock if RUNTIME is not None else 0.0
        self._pyd_running = True
        self._pyd_due = now + max(1, self.interval)
        if RUNTIME is not None:
            RUNTIME.timer_started(self)

    def _timer_stop(self):
        self._pyd_running = False

    def _timer_set_interval(self):
        # GUI backends restart the countdown when the interval changes
        # (FuncAnimation does this for ``repeat_delay``).
        if getattr(self, "_pyd_running", False) and RUNTIME is not None:
            self._pyd_due = RUNTIME.clock + max(1, self.interval)


class FigureCanvasPyd(FigureCanvasAgg):
    """Agg canvas whose drawing is scheduled by the demonstrator runtime."""

    required_interactive_framework = None
    supports_blit = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pyd_version = 0        # bumps whenever the RGBA buffer changes
        self._pyd_needs_draw = True  # draw_idle() was requested
        self._pyd_drawing = False

    def new_timer(self, *args, **kwargs):
        kwargs["_pyd_canvas"] = self
        return TimerPyd(*args, **kwargs)

    def draw(self, *args, **kwargs):
        self._pyd_drawing = True
        try:
            super().draw(*args, **kwargs)
        finally:
            self._pyd_drawing = False
        self._pyd_version += 1
        self._pyd_needs_draw = False

    def draw_idle(self, *args, **kwargs):
        # Deferred: the runtime draws once per captured frame.
        self._pyd_needs_draw = True
        if RUNTIME is not None:
            RUNTIME.figure_dirty(self)

    def blit(self, bbox=None):
        # Agg blitting already updated the buffer in place.
        self._pyd_version += 1

    def flush_events(self):
        if RUNTIME is not None:
            RUNTIME.flush_events(self)

    def start_event_loop(self, timeout=0):
        if RUNTIME is not None:
            RUNTIME.event_loop(self, timeout)

    def stop_event_loop(self):
        if RUNTIME is not None:
            RUNTIME.stop_event_loop(self)


class FigureManagerPyd(FigureManagerBase):
    """Keeps a window title and tells the runtime when a figure is shown."""

    def __init__(self, canvas, num):
        super().__init__(canvas, num)
        self._pyd_title = f"Figure {num}"
        if RUNTIME is not None:
            RUNTIME.figure_created(self)

    def show(self):
        if RUNTIME is not None:
            RUNTIME.show_figures([self])

    def set_window_title(self, title):
        self._pyd_title = str(title)
        if RUNTIME is not None:
            RUNTIME.figure_retitled(self)

    def get_window_title(self):
        return self._pyd_title

    def resize(self, w, h):
        # Sizing is handled by the runtime (dpi follows the page); nothing to do.
        pass

    def destroy(self):
        if RUNTIME is not None:
            RUNTIME.figure_destroyed(self)
        super().destroy()


FigureCanvasPyd.manager_class = FigureManagerPyd
FigureCanvas = FigureCanvasPyd
FigureManager = FigureManagerPyd


def show(*args, block=None, **kwargs):
    """``plt.show()``: hand all open figures to the page and return."""
    from matplotlib._pylab_helpers import Gcf
    if RUNTIME is not None:
        RUNTIME.show_figures(Gcf.get_all_fig_managers())


def draw_if_interactive():
    import matplotlib
    from matplotlib._pylab_helpers import Gcf
    if matplotlib.is_interactive():
        manager = Gcf.get_active()
        if manager:
            manager.canvas.draw_idle()
