"""pyd_runtime -- the Python half of the demonstrator's Web Worker.

``worker.js`` loads Pyodide, writes this package's files into the virtual
filesystem and calls the functions of :data:`RT` (a :class:`Runtime`).  The
runtime owns

* the project directory and the import machinery: modules are re-imported
  fresh on every run, and restart-mode parameters are injected by rewriting
  the literal in the AST at import time (the displayed source never changes);
* running the main script as ``__main__`` with ``sys.argv``, the working
  directory and ``sys.path`` set up as if started with ``python main.py``;
* the matplotlib bridge (see ``pyd_backend``): figures are rendered with Agg
  and handed to the page frame by frame; animation timers fire when the page
  ticks the runtime clock;
* live parameter writes, manifest buttons, pointer events and theme changes.

All communication with the page goes through a ``host`` object:

    host.post(text)                          JSON message (status, figures, errors...)
    host.frame(fig_id, width, height, n, buf)  RGBA frame, ``buf`` supports the buffer protocol
    host.now() -> float                      milliseconds (performance.now)
    host.sleep(ms) -> awaitable              resolves after ms (used with JSPI)

The unit tests (tests/demonstrator/test_runtime.py) drive it with a fake host
on desktop CPython, so nothing here may import ``js`` or ``pyodide`` at
module level.
"""
from __future__ import annotations

import builtins
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import math
import os
import sys
import time
import traceback
import types
import warnings

import pyd_detect

__all__ = ["RT", "Runtime", "PydAbort"]

RUNTIME_DIR = os.path.dirname(os.path.abspath(__file__))

# Modules the browser cannot provide, with a friendlier explanation.
UNAVAILABLE = {
    "tkinter": "Tk windows can't open in a browser; the demonstrator shows matplotlib figures instead.",
    "_tkinter": "Tk windows can't open in a browser.",
    "PyQt5": "Qt GUIs can't run in a browser.", "PyQt6": "Qt GUIs can't run in a browser.",
    "PySide2": "Qt GUIs can't run in a browser.", "PySide6": "Qt GUIs can't run in a browser.",
    "wx": "wxPython GUIs can't run in a browser.",
    "cupy": "CuPy needs a CUDA GPU, which a browser can't use.",
    "torch": "PyTorch has no WebAssembly build.",
    "tensorflow": "TensorFlow has no WebAssembly build.",
    "jax": "JAX has no WebAssembly build.",
    "numba": "Numba compiles to native machine code, which a browser can't run.",
    "multiprocessing": "Python can't start processes in a browser.",
    "subprocess": "Python can't start other programs in a browser.",
    "serial": "Serial ports aren't reachable from a browser.",
    "vtk": "VTK has no WebAssembly build.",
    "mayavi": "Mayavi needs a desktop GUI.",
}

# Text and line colours for a figure whose background the script chose
# itself, when that background is light and the page is dark (matplotlib's
# own defaults) or dark and the page is light.  Facecolours the script set
# are never touched.
LIGHT_FIGURE_RC = {
    "axes.facecolor": "#ffffff", "axes.edgecolor": "#000000", "axes.labelcolor": "#000000",
    "axes.titlecolor": "#000000", "text.color": "#000000", "xtick.color": "#000000",
    "ytick.color": "#000000", "xtick.labelcolor": "#000000", "ytick.labelcolor": "#000000",
    "grid.color": "#b0b0b0", "legend.facecolor": "#ffffff", "legend.edgecolor": "#cccccc",
    "legend.labelcolor": "#000000",
}
DARK_FIGURE_RC = {
    "axes.edgecolor": "#c9ccd3", "axes.labelcolor": "#e8e9ed", "axes.titlecolor": "#f2f3f5",
    "text.color": "#f2f3f5", "xtick.color": "#c9ccd3", "ytick.color": "#c9ccd3",
    "xtick.labelcolor": "#e8e9ed", "ytick.labelcolor": "#e8e9ed", "grid.color": "#4a4d57",
    "legend.edgecolor": "#4a4d57", "legend.labelcolor": "#e8e9ed",
}


def _luminance(rgba):
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(float(c)) for c in rgba[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


GUI_BACKENDS_NOTE = ("matplotlib.use({!r}) ignored: in the browser, figures are drawn by the "
                     "demonstrator's own backend.")


class PydAbort(BaseException):
    """Raised inside the script to unwind it when the page stops/restarts it.

    Derives from BaseException so ``except Exception`` in the script doesn't
    swallow it.
    """

    def __init__(self, reason="stop"):
        super().__init__(reason)
        self.reason = reason


class FigState:
    """A figure the page is displaying."""

    __slots__ = ("id", "fig", "manager", "title", "visible", "sent_version", "frames",
                 "closed", "base_dpi", "fitted_for", "announced", "bg_checked")

    def __init__(self, fid, fig, manager):
        self.id = fid
        self.fig = fig
        self.manager = manager
        self.title = None
        self.visible = True
        self.sent_version = -1
        self.frames = 0
        self.closed = False
        self.base_dpi = fig.dpi
        self.fitted_for = None
        self.announced = False
        self.bg_checked = False


class _ProjectLoader(importlib.machinery.SourceFileLoader):
    """Loads project modules, injecting restart-mode literal values."""

    def source_to_code(self, data, path, *, _optimize=-1):
        rt = RT
        rel = rt.rel(path)
        source = importlib.util.decode_source(data)
        reps = rt.literal_overrides.get(rel) if rel else None
        if reps:
            tree, missing = pyd_detect.rewrite_source(source, path, reps)
            if missing:
                rt.warn_once(f"missing:{rel}", f"{rel}: {len(missing)} parameter value(s) no longer match the "
                                               "source (was the file edited?); using the file's own values.")
            return compile(tree, path, "exec", dont_inherit=True, optimize=_optimize)
        return compile(source, path, "exec", dont_inherit=True, optimize=_optimize)


class _ProjectFinder(importlib.abc.MetaPathFinder):
    """Routes imports of project files through :class:`_ProjectLoader`."""

    def find_spec(self, fullname, path, target=None):
        rt = RT
        if not rt.root:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or not spec.origin or not isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            return None
        origin = os.path.abspath(spec.origin)
        if not origin.startswith(rt.root + os.sep) or not origin.endswith(".py"):
            return None
        spec.loader = _ProjectLoader(fullname, origin)
        return spec


class Runtime:
    def __init__(self):
        self.host = None
        self.root = ""
        self.main = "main.py"
        self.files = set()
        self.params = {}
        self.values = {}
        self.literal_overrides = {}
        self.demo_param_values = {}
        self.main_module = None
        self.figures = {}            # id -> FigState
        self._fig_by_canvas = {}
        self._next_fig = 1
        self.timers = []
        self.animations = []
        self.clock = 0.0
        self.viewport = (640.0, 420.0, 1.0)
        self.quality = "auto"
        self.max_pixels = 1_600_000
        self.speed = 1.0
        self.fps_cap = 30.0
        self.paused = False
        self.step_pending = False
        self.abort = None
        self.running = False
        self.frames_in_flight = 0
        self.last_loop_capture = -1e9
        self.theme_rc = {}
        self._warned = set()
        self._patched = False
        self._orig_sleep = time.sleep
        self._orig_input = builtins.input
        self.blocking_loop_seen = False
        self.callback_error = None
        self.jspi = None             # can the running script suspend? (set per run)
        self.can_run_sync = lambda: False
        self.run_sync = None
        self.mpl = None

    # ------------------------------------------------------------------
    # set-up
    # ------------------------------------------------------------------
    def init(self, host, options_json="{}"):
        """Called once when the worker starts."""
        self.host = host
        opts = json.loads(options_json or "{}")
        self.root = os.path.abspath(opts.get("root", "/home/pyodide/project"))
        os.makedirs(self.root, exist_ok=True)
        sys.dont_write_bytecode = True
        if RUNTIME_DIR not in sys.path:
            sys.path.append(RUNTIME_DIR)
        os.environ["MPLBACKEND"] = "module://pyd_backend"
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
        if not any(isinstance(f, _ProjectFinder) for f in sys.meta_path):
            sys.meta_path.insert(0, _ProjectFinder())
        try:
            from pyodide.ffi import can_run_sync, run_sync  # type: ignore
            self.can_run_sync = can_run_sync
            self.run_sync = run_sync
        except ImportError:
            pass
        return json.dumps({"python": sys.version.split()[0], "root": self.root})

    def rel(self, path):
        """Project-relative POSIX path, or None for files outside the project."""
        if not path:
            return None
        try:
            ap = os.path.abspath(path)
        except (TypeError, ValueError):
            return None
        if ap.startswith(self.root + os.sep):
            return ap[len(self.root) + 1:].replace(os.sep, "/")
        return None

    def post(self, msg_type, **data):
        data["type"] = msg_type
        if self.host is not None:
            self.host.post(json.dumps(data, default=_json_default))

    def warn_once(self, key, text):
        if key in self._warned:
            return
        self._warned.add(key)
        self.post("console", stream="note", text=text + "\n")

    # ------------------------------------------------------------------
    # project files
    # ------------------------------------------------------------------
    def write_files(self, files):
        """Write the project into the virtual filesystem.

        ``files`` is a sequence of ``(path, data)`` pairs (``data`` is str or
        bytes).  Paths were validated by the page; they are checked again here.
        Files from a previous load that are no longer listed are removed.
        """
        written = set()
        for path, data in files:
            rel = _safe_rel(str(path))
            if rel is None:
                raise ValueError(f"unsafe path in project: {path!r}")
            full = os.path.join(self.root, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if hasattr(data, "to_py"):
                data = data.to_py()
            if isinstance(data, memoryview):
                data = data.tobytes()
            if isinstance(data, str):
                data = data.encode("utf-8")
            with open(full, "wb") as fh:
                fh.write(bytes(data))
            written.add(rel)
        for old in self.files - written:
            try:
                os.remove(os.path.join(self.root, *old.split("/")))
            except OSError:
                pass
        self.files = written
        importlib.invalidate_caches()
        return json.dumps(sorted(written))

    def read_sources(self):
        out = {}
        for rel in self.files:
            if rel.endswith(".py"):
                try:
                    with open(os.path.join(self.root, *rel.split("/")), encoding="utf-8") as fh:
                        out[rel] = fh.read()
                except (OSError, UnicodeDecodeError):
                    pass
        return out

    def read_sources_json(self):
        return json.dumps(self.read_sources())

    def unavailable_json(self):
        return json.dumps(UNAVAILABLE)

    def detect(self, main, overrides_json="{}", options_json="{}"):
        """Run parameter detection over the project's Python files."""
        self.main = main
        result = pyd_detect.detect(self.read_sources(), main,
                                   json.loads(overrides_json or "{}"),
                                   json.loads(options_json or "{}"))
        return json.dumps(result, default=_json_default)

    def missing_imports(self):
        """Top-level imports that are neither stdlib, project modules nor
        importable now.  Used by the worker to decide what to fetch."""
        # Our own scanner: pyodide.code.find_imports also reports relative
        # imports ("from .grid import x" -> "grid"), which are project files.
        find_imports = _find_imports_fallback
        project_top = set()
        for rel in self.files:
            if rel.endswith(".py"):
                parts = rel[:-3].split("/")
                project_top.add(parts[0])
                main_dir = os.path.dirname(self.main)
                if main_dir and rel.startswith(main_dir + "/"):
                    project_top.add(rel[len(main_dir) + 1:-3].split("/")[0])
        stdlib = set(getattr(sys, "stdlib_module_names", ()))
        missing = {}
        for rel, src in self.read_sources().items():
            try:
                names = find_imports(src)
            except SyntaxError:
                continue
            for name in names:
                top = name.split(".")[0]
                if not top or top in project_top or top in stdlib or top in ("demo_params", "pyd_runtime"):
                    continue
                if top in missing:
                    continue
                try:
                    found = importlib.util.find_spec(top) is not None
                except (ImportError, ValueError):
                    found = False
                if not found:
                    missing[top] = rel
        return json.dumps(missing)

    # ------------------------------------------------------------------
    # running
    # ------------------------------------------------------------------
    def _install_patches(self):
        if self._patched:
            return
        import matplotlib
        import matplotlib.animation as manim
        import pyd_backend
        pyd_backend.RUNTIME = self
        self.mpl = matplotlib
        orig_use = matplotlib.use

        def use(backend, *args, **kwargs):
            name = str(backend)
            if name.lower() not in ("agg", "module://pyd_backend"):
                self.warn_once("use:" + name, GUI_BACKENDS_NOTE.format(name))
            return None

        use.__doc__ = orig_use.__doc__
        matplotlib.use = use
        self._orig_use = orig_use

        orig_init = manim.Animation.__init__
        rt = self

        def anim_init(anim_self, *args, **kwargs):
            orig_init(anim_self, *args, **kwargs)
            rt.animations.append(anim_self)   # keep alive like a GUI would

        manim.Animation.__init__ = anim_init

        def save(anim_self, filename, *args, **kwargs):
            rt.post("console", stream="note",
                    text=f"anim.save({filename!r}) skipped: saving animations needs files and video "
                         "encoders on your own computer. Run the script locally to save it.\n")

        def to_html5_video(anim_self, *args, **kwargs):
            rt.post("console", stream="note", text="to_html5_video() isn't available in the browser (no ffmpeg).\n")
            return ""

        def to_jshtml(anim_self, *args, **kwargs):
            rt.post("console", stream="note", text="to_jshtml() skipped: the demonstrator already animates the figure.\n")
            return ""

        manim.Animation.save = save
        manim.Animation.to_html5_video = to_html5_video
        manim.Animation.to_jshtml = to_jshtml

        import matplotlib.pyplot as plt
        from matplotlib import rcsetup
        orig_switch = plt.switch_backend
        # pyplot itself calls switch_backend() through its module globals, so
        # the wrapper must always end up selecting our backend.
        if str(matplotlib.get_backend()) != "module://pyd_backend":
            orig_switch("module://pyd_backend")

        def switch_backend(newbackend):
            name = str(newbackend)
            if newbackend is not rcsetup._auto_backend_sentinel and name.lower() not in ("agg", "module://pyd_backend"):
                self.warn_once("use:" + name, GUI_BACKENDS_NOTE.format(name))
            return orig_switch("module://pyd_backend")

        plt.switch_backend = switch_backend
        self._orig_switch = orig_switch

        def blocked(name):
            def f(*args, **kwargs):
                raise RuntimeError(f"{name} needs to wait for clicks or key presses, which the browser "
                                   "demonstrator can't do. Use a parameter or a button instead.")
            return f

        plt.ginput = blocked("plt.ginput()")
        plt.waitforbuttonpress = blocked("plt.waitforbuttonpress()")
        self._patched = True

    def _script_input(self, prompt=""):
        raise RuntimeError("input() isn't available in the browser demonstrator. Give the value a default "
                           "(or use argparse) so it can become a control.")

    def _script_sleep(self, seconds):
        """``time.sleep`` while the script runs: paces loops cooperatively."""
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return self._orig_sleep(seconds)
        self.check_abort()
        self.capture(throttle=True)
        self.cooperative_wait(max(0.0, seconds) * 1000.0 / max(self.speed, 1e-3))

    def reset(self):
        """Close figures, stop timers and forget project modules."""
        self.check_abort_clear()
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
            plt.ioff()
        except Exception:
            pass
        for t in self.timers:
            t._pyd_running = False
        self.timers = []
        self.animations = []
        self.figures = {}
        self._fig_by_canvas = {}
        self._next_fig = 1
        self.clock = 0.0
        self.paused = False
        self.step_pending = False
        self.frames_in_flight = 0
        self.callback_error = None
        self.blocking_loop_seen = False
        for name, mod in list(sys.modules.items()):
            f = getattr(mod, "__file__", None)
            if (f and self.rel(f) is not None) or (name == "__main__" and mod is self.main_module):
                del sys.modules[name]
        self.main_module = None
        importlib.invalidate_caches()
        if self.mpl is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.mpl.rcdefaults()
            self.apply_theme(self.theme_rc)

    def check_abort_clear(self):
        self.abort = None

    def apply_theme(self, rc):
        self.theme_rc = dict(rc or {})
        if self.mpl is None or not rc:
            return
        good = {}
        for k, v in rc.items():
            if k in self.mpl.rcParams:
                good[k] = v
        try:
            self.mpl.rcParams.update(good)
        except (ValueError, KeyError) as exc:
            self.warn_once("theme", f"theme colours not applied: {exc}")

    def prepare(self, config):
        """Coerce values and work out argv / literal overrides for a run."""
        self.params = {p["id"]: p for p in config.get("params", [])}
        values = config.get("values") or {}
        coerced = {}
        errors = []
        for pid, val in values.items():
            p = self.params.get(pid)
            if p is None:
                continue
            try:
                coerced[pid] = pyd_detect.coerce_value(p, val)
            except ValueError as exc:
                errors.append(str(exc))
        self.values = coerced
        overrides = {}
        demo_values = {}
        for pid, p in self.params.items():
            ap = p.get("apply") or {}
            if ap.get("kind") == "demo_params":
                demo_values[ap.get("key", p["name"])] = coerced.get(pid, _from_json(p.get("default"), p))
            if pid not in coerced:
                continue
            v = coerced[pid]
            if ap.get("kind") == "literal":
                if _same_value(v, _from_json(p.get("default"), p)) and not p.get("force_write"):
                    continue
                if isinstance(p.get("default"), list) and p.get("type") == "tuple" and p.get("source") != "constant":
                    v = list(v)
                overrides.setdefault(ap["file"], {})[tuple(ap["span"])] = v
        self.literal_overrides = overrides
        self.demo_param_values = demo_values
        argv = pyd_detect.build_argv(list(self.params.values()), {k: _to_jsonable(v) for k, v in coerced.items()}, self.main)
        return argv, errors

    def run(self, config_json):
        """Run the project's main file.  Called with ``callPromising`` from the
        worker so that ``plt.pause`` loops can yield to the page (JSPI)."""
        config = json.loads(config_json)
        self.main = config.get("main", self.main)
        self.speed = float(config.get("speed", self.speed) or 1.0)
        self.fps_cap = float(config.get("fps", self.fps_cap) or 30)
        if config.get("viewport"):
            self.set_viewport(json.dumps(config["viewport"]))
        if config.get("theme_rc") is not None:
            self.theme_rc = config["theme_rc"]
        self._install_patches()
        self.reset()
        self.jspi = bool(self.can_run_sync())
        argv, errors = self.prepare(config)
        for e in errors:
            self.post("console", stream="note", text=f"ignored value: {e}\n")
        import demo_params
        demo_params._pyd_reset(self.demo_param_values)
        sys.modules["demo_params"] = demo_params

        main_abs = os.path.join(self.root, *self.main.split("/"))
        main_dir = os.path.dirname(main_abs)
        sys.argv = list(argv)
        os.chdir(self.root)
        for p in (main_dir, self.root):
            while p in sys.path:
                sys.path.remove(p)
        sys.path.insert(0, self.root)
        sys.path.insert(0, main_dir)
        importlib.invalidate_caches()

        mod = types.ModuleType("__main__")
        mod.__file__ = main_abs
        mod.__builtins__ = builtins
        mod.__spec__ = None
        mod.__package__ = None
        mod.__loader__ = None
        self.main_module = mod
        sys.modules["__main__"] = mod

        self.post("status", stage="running", message="Running " + self.main, jspi=self.jspi,
                  argv=sys.argv)
        builtins.input = self._script_input
        time.sleep = self._script_sleep
        self.running = True
        outcome = "done"
        try:
            with open(main_abs, "rb") as fh:
                source = importlib.util.decode_source(fh.read())
            reps = self.literal_overrides.get(self.main)
            if reps:
                tree, missing = pyd_detect.rewrite_source(source, main_abs, reps)
                if missing:
                    self.warn_once("missing:" + self.main, f"{self.main}: some parameter values no longer match the "
                                                           "source; using the file's own values.")
                code = compile(tree, main_abs, "exec", dont_inherit=True)
            else:
                code = compile(source, main_abs, "exec", dont_inherit=True)
            exec(code, mod.__dict__)
        except PydAbort as exc:
            outcome = exc.reason
        except SystemExit as exc:
            if exc.code not in (None, 0):
                self.post("console", stream="stderr", text=f"The script called sys.exit({exc.code!r}).\n")
                outcome = "exit"
        except BaseException as exc:  # noqa: BLE001 - report everything
            self.report_error(exc, "script")
            outcome = "error"
        finally:
            self.running = False
            time.sleep = self._orig_sleep
            builtins.input = self._orig_input
        if outcome in ("stop", "restart"):
            return json.dumps({"outcome": outcome})
        try:
            self.auto_show()
        except PydAbort as exc:
            return json.dumps({"outcome": exc.reason})
        except BaseException as exc:  # noqa: BLE001
            self.report_error(exc, "draw")
            outcome = "error"
        return json.dumps({
            "outcome": outcome,
            "figures": self.figure_list(),
            "timers": sum(1 for t in self.timers if t._pyd_running),
            "next_due_in": self.next_due_in(),
            "jspi": self.jspi,
        })

    # ------------------------------------------------------------------
    # figures
    # ------------------------------------------------------------------
    def figure_created(self, manager):
        import matplotlib
        if matplotlib.is_interactive():
            self.show_figures([manager], draw=False)

    def figure_retitled(self, manager):
        fs = self._fig_by_canvas.get(id(manager.canvas))
        if fs is not None:
            self.post("figures", figures=self.figure_list())

    def figure_destroyed(self, manager):
        fs = self._fig_by_canvas.get(id(manager.canvas))
        if fs is not None:
            fs.closed = True

    def register(self, manager):
        canvas = manager.canvas
        fs = self._fig_by_canvas.get(id(canvas))
        if fs is not None:
            return fs, False
        fs = FigState(self._next_fig, canvas.figure, manager)
        self._next_fig += 1
        self.figures[fs.id] = fs
        self._fig_by_canvas[id(canvas)] = fs
        self.fit(fs)
        return fs, True

    def show_figures(self, managers, draw=True):
        """``plt.show()``: register figures and send their first frames now
        (a script may close a figure right after showing it)."""
        new = False
        for m in managers:
            if not hasattr(m.canvas, "_pyd_version"):
                continue
            fs, created = self.register(m)
            new = new or created
            if draw and (created or fs.sent_version < 0):
                self.draw_and_send(fs, force=True)
        if new:
            self.post("figures", figures=self.figure_list())

    def auto_show(self):
        """After the script finishes: show figures it never showed."""
        from matplotlib._pylab_helpers import Gcf
        self.show_figures(Gcf.get_all_fig_managers())
        for fs in self.figures.values():
            if fs.sent_version < 0 and not fs.closed:
                self.draw_and_send(fs, force=True)
        self.post("figures", figures=self.figure_list())

    def figure_list(self):
        out = []
        for fs in self.figures.values():
            fig = fs.fig
            title = None
            if fs.manager is not None:
                title = getattr(fs.manager, "_pyd_title", None)
                if title and title.startswith("Figure ") and fig._suptitle is not None and fig._suptitle.get_text():
                    title = fig._suptitle.get_text()
            w, h = fig.get_size_inches()
            out.append({
                "id": fs.id, "title": title or f"Figure {fs.id}", "width_in": float(w), "height_in": float(h),
                "animated": self.is_animated(fs), "closed": fs.closed, "frames": fs.frames,
            })
        return out

    def is_animated(self, fs):
        return any(t._pyd_running and t._pyd_canvas is fs.fig.canvas for t in self.timers)

    def figure_dirty(self, canvas):
        pass  # captured on the next frame boundary

    def fit(self, fs):
        """Choose the figure's dpi so it renders at the page's pixel size."""
        vw, vh, dpr = self.viewport
        w_in, h_in = fs.fig.get_size_inches()
        if w_in <= 0 or h_in <= 0:
            return False
        q = {"low": 1.0, "medium": min(dpr, 1.5), "high": min(dpr, 2.0)}.get(self.quality, min(dpr, 2.0))
        dpi = min(vw / w_in, vh / h_in) * max(q, 0.5)
        if w_in * h_in * dpi * dpi > self.max_pixels:
            dpi = math.sqrt(self.max_pixels / (w_in * h_in))
        dpi = max(24.0, min(dpi, 480.0))
        key = round(dpi, 1)
        if fs.fitted_for == key:
            return False
        fs.fitted_for = key
        if abs(fs.fig.dpi - dpi) > 0.05:
            fs.fig.set_dpi(dpi)
            try:
                from matplotlib.backend_bases import ResizeEvent
                ResizeEvent("resize_event", fs.fig.canvas)._process()
            except Exception:
                pass
            fs.fig.canvas._pyd_needs_draw = True
            return True
        return False

    def set_viewport(self, json_text):
        v = json.loads(json_text)
        w = float(v.get("width") or self.viewport[0])
        h = float(v.get("height") or self.viewport[1])
        dpr = float(v.get("dpr") or 1.0)
        self.viewport = (max(80.0, w), max(60.0, h), max(0.5, min(dpr, 4.0)))
        if v.get("quality"):
            self.quality = v["quality"]
        if v.get("max_pixels"):
            self.max_pixels = int(v["max_pixels"])
        changed = False
        for fs in self.figures.values():
            if self.fit(fs):
                changed = True
        if changed:
            self.capture(force_all_visible=True)
        return json.dumps({"changed": changed})

    def set_visible(self, ids_json):
        ids = set(json.loads(ids_json))
        for fs in self.figures.values():
            fs.visible = fs.id in ids
        self.capture()

    def draw_and_send(self, fs, force=False):
        if not fs.bg_checked:
            self.match_own_background(fs)
        canvas = fs.fig.canvas
        if canvas._pyd_needs_draw or canvas._pyd_version == 0 or force and fs.sent_version < 0:
            canvas.draw()
        self.send_frame(fs)

    def send_frame(self, fs):
        canvas = fs.fig.canvas
        renderer = canvas.get_renderer()
        w, h = int(renderer.width), int(renderer.height)
        buf = canvas.buffer_rgba()
        fs.frames += 1
        fs.sent_version = canvas._pyd_version
        self.frames_in_flight += 1
        self.host.frame(fs.id, w, h, fs.frames, buf)

    def capture(self, throttle=False, force_all_visible=False):
        """Draw dirty visible figures and send changed frames."""
        if throttle:
            now = self.host.now()
            if now - self.last_loop_capture < 1000.0 / max(self.fps_cap, 1.0):
                return 0
            if self.frames_in_flight > 2 and self.jspi:
                return 0
            self.last_loop_capture = now
        sent = 0
        for fs in list(self.figures.values()):
            if fs.closed or not fs.visible:
                continue
            if not fs.bg_checked:
                self.match_own_background(fs)
            canvas = fs.fig.canvas
            if canvas._pyd_needs_draw or force_all_visible:
                canvas.draw()
            if canvas._pyd_version != fs.sent_version:
                self.send_frame(fs)
                sent += 1
        return sent

    def frame_ack(self, n=1):
        self.frames_in_flight = max(0, self.frames_in_flight - int(n))

    # ------------------------------------------------------------------
    # animation clock
    # ------------------------------------------------------------------
    def register_timer(self, timer):
        self.timers.append(timer)

    def timer_started(self, timer):
        pass

    def next_due_in(self):
        due = [t._pyd_due for t in self.timers if t._pyd_running]
        if not due:
            return None
        return max(0.0, min(due) - self.clock)

    def tick(self, dt_ms=0.0, force=False, snap_ms=0.0):
        """Advance the virtual clock and fire due timers (one firing per timer
        per tick: when the page falls behind, frames are skipped, not queued).

        ``snap_ms``: the page ticks on display frames, so a timer due less
        than this far ahead counts as due now (the clock moves to its due
        time rather than waiting a whole display frame)."""
        self.clock += max(0.0, float(dt_ms))
        running = [t for t in self.timers if t._pyd_running]
        if force and running:
            self.clock = max(self.clock, min(t._pyd_due for t in running))
        elif running and snap_ms > 0:
            soonest = min(t._pyd_due for t in running)
            if self.clock < soonest <= self.clock + float(snap_ms):
                self.clock = soonest
        fired = 0
        error = False
        for t in sorted(running, key=lambda t: t._pyd_due):
            if not t._pyd_running or t._pyd_due > self.clock + 0.5:
                continue
            t._pyd_due = self.clock + max(1, t.interval)
            if t.single_shot:
                t._pyd_running = False
            try:
                t._on_timer()
            except PydAbort:
                raise
            except Exception as exc:  # noqa: BLE001
                self.report_error(exc, "animation")
                error = True
                break
            fired += 1
        try:
            sent = self.capture()
        except Exception as exc:  # noqa: BLE001
            self.report_error(exc, "draw")
            sent, error = 0, True
        return json.dumps({
            "fired": fired, "sent": sent, "error": error,
            "running": sum(1 for t in self.timers if t._pyd_running),
            "next_due_in": self.next_due_in(),
        })

    # ------------------------------------------------------------------
    # blocking-loop patterns (plt.pause, flush_events, time.sleep)
    # ------------------------------------------------------------------
    def check_abort(self):
        if self.abort:
            reason, self.abort = self.abort, None
            raise PydAbort(reason)

    def suspendable(self):
        try:
            return bool(self.run_sync) and bool(self.can_run_sync())
        except Exception:
            return False

    def cooperative_wait(self, ms):
        """Wait ``ms`` milliseconds of page time.  With JSPI the worker keeps
        handling messages (pause, step, stop, live parameters) meanwhile;
        without it we can only keep the script's pace."""
        if self.suspendable():
            deadline = self.host.now() + ms
            while True:
                self.check_abort()
                if self.paused and not self.step_pending:
                    self.run_sync(self.host.sleep(40))
                    deadline = self.host.now() + ms
                    continue
                remaining = deadline - self.host.now()
                if remaining <= 0:
                    break
                self.run_sync(self.host.sleep(min(remaining, 40)))
            self.step_pending = False
            self.check_abort()
        else:
            end = time.perf_counter() + ms / 1000.0
            while time.perf_counter() < end:
                pass

    def event_loop(self, canvas, timeout):
        """``plt.pause(t)`` -> canvas.start_event_loop(t)."""
        self.blocking_loop_seen = True
        self.check_abort()
        if timeout is None or timeout <= 0:
            timeout = 0.0
        self.capture(throttle=True)
        # Let timers (e.g. a FuncAnimation shown with block=False) run too.
        if any(t._pyd_running for t in self.timers):
            self.tick(timeout * 1000.0 * self.speed)
        self.cooperative_wait(timeout * 1000.0 / max(self.speed, 1e-3))

    def flush_events(self, canvas):
        self.blocking_loop_seen = True
        self.check_abort()
        self.capture(throttle=True)
        if self.suspendable():
            self.cooperative_wait(0)

    def stop_event_loop(self, canvas):
        pass

    # ------------------------------------------------------------------
    # control from the page
    # ------------------------------------------------------------------
    def request_abort(self, reason="stop"):
        """Ask a running (suspended) script to unwind at its next yield."""
        if self.running:
            self.abort = reason
            return json.dumps({"pending": True})
        self.stop_timers()
        return json.dumps({"pending": False})

    def stop_timers(self):
        for t in self.timers:
            t._pyd_running = False

    def set_paused(self, flag):
        self.paused = bool(flag)

    def step(self):
        self.step_pending = True

    def set_speed(self, speed):
        self.speed = max(0.01, float(speed))

    def set_fps(self, fps):
        self.fps_cap = max(1.0, float(fps))

    def module_for(self, rel):
        if rel == self.main:
            return self.main_module
        modname = pyd_detect.modname_for(rel, os.path.dirname(self.main) if not rel.startswith("..") else "")
        mod = sys.modules.get(modname)
        if mod is None:
            # imported relative to the project root rather than main's folder
            mod = sys.modules.get(pyd_detect.modname_for(rel))
        if mod is not None and self.rel(getattr(mod, "__file__", None)) == rel:
            return mod
        return None

    def set_params(self, values_json):
        """Live parameter changes: write values into the running modules."""
        values = json.loads(values_json)
        applied, errors = [], []
        for pid, raw in values.items():
            p = self.params.get(pid)
            if p is None:
                errors.append(f"unknown parameter {pid!r}")
                continue
            try:
                value = pyd_detect.coerce_value(p, raw)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            self.values[pid] = value
            ok = False
            for target in p.get("live_targets") or []:
                try:
                    ok = self._write_target(target, value) or ok
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{pid}: {exc}")
            if ok:
                applied.append(pid)
            else:
                errors.append(f"{pid}: nothing to update yet (module not imported?)")
        sent = 0
        try:
            sent = self.capture()     # on_change callbacks may have redrawn
        except Exception as exc:  # noqa: BLE001
            self.report_error(exc, "draw")
        return json.dumps({"applied": applied, "errors": errors, "sent": sent})

    def _write_target(self, target, value):
        kind = target.get("kind")
        if kind == "demo_params":
            import demo_params
            demo_params._pyd_set(target["key"], value)
            return True
        mod = self.module_for(target.get("module"))
        if mod is None:
            return False
        if kind == "global":
            setattr(mod, target["name"], value)
            return True
        if kind == "item":
            container = getattr(mod, target["name"], None)
            if container is None:
                return False
            container[target["key"]] = value
            return True
        if kind == "attr":
            obj = getattr(mod, target["name"], None)
            if obj is None:
                return False
            setattr(obj, target["attr"], value)
            return True
        return False

    def call_button(self, name, file=None):
        if not name.isidentifier() or name.startswith("_"):
            self.post("console", stream="stderr", text=f"Button: {name!r} is not a public function name.\n")
            return json.dumps({"ok": False})
        mod = self.module_for(file or self.main)
        func = getattr(mod, name, None) if mod is not None else None
        if not callable(func):
            self.post("console", stream="stderr", text=f"Button: {name}() is not defined (yet).\n")
            return json.dumps({"ok": False})
        try:
            func()
        except PydAbort:
            raise
        except Exception as exc:  # noqa: BLE001
            self.report_error(exc, "button")
            return json.dumps({"ok": False})
        sent = self.capture()
        return json.dumps({"ok": True, "sent": sent})

    def pointer(self, msg_json):
        """Forward a pointer event from the page canvas to matplotlib
        (widgets such as Slider/Button and mpl_connect handlers)."""
        from matplotlib.backend_bases import LocationEvent, MouseButton, MouseEvent
        msg = json.loads(msg_json)
        fs = self.figures.get(int(msg.get("fig", 0)))
        if fs is None or fs.closed:
            return json.dumps({"sent": 0})
        canvas = fs.fig.canvas
        bbox = fs.fig.bbox
        x = float(msg.get("x", 0)) * bbox.width
        y = (1.0 - float(msg.get("y", 0))) * bbox.height
        button = {0: MouseButton.LEFT, 1: MouseButton.MIDDLE, 2: MouseButton.RIGHT}.get(msg.get("button"), None)
        mods = [m for m in ("ctrl", "alt", "shift") if msg.get(m)]
        kind = msg.get("kind")
        try:
            if kind == "move":
                MouseEvent("motion_notify_event", canvas, x, y, button=button if msg.get("buttons") else None,
                           modifiers=mods)._process()
            elif kind == "down":
                MouseEvent("button_press_event", canvas, x, y, button=button,
                           dblclick=bool(msg.get("dbl")), modifiers=mods)._process()
            elif kind == "up":
                MouseEvent("button_release_event", canvas, x, y, button=button, modifiers=mods)._process()
            elif kind == "scroll":
                step = float(msg.get("step", 0))
                MouseEvent("scroll_event", canvas, x, y, button="up" if step > 0 else "down", step=step,
                           modifiers=mods)._process()
            elif kind == "leave":
                LocationEvent("figure_leave_event", canvas, x, y, modifiers=mods)._process()
        except PydAbort:
            raise
        except Exception as exc:  # noqa: BLE001
            self.report_error(exc, "event")
        try:
            sent = self.capture()
        except Exception as exc:  # noqa: BLE001
            self.report_error(exc, "draw")
            sent = 0
        return json.dumps({"sent": sent})

    # ------------------------------------------------------------------
    # theme
    # ------------------------------------------------------------------
    def retheme(self, old_json, new_json):
        """Swap theme colours on existing figures -- only where the current
        colour still equals the old theme's default, so styling the script set
        explicitly is never overridden.  Figures with a background of their
        own keep the colours chosen for it."""
        old = json.loads(old_json or "{}")
        new = json.loads(new_json or "{}")
        self.apply_theme(new)
        if self.mpl is None or not self.figures:
            return json.dumps({"changed": 0})
        from matplotlib.colors import same_color, to_rgba
        changed = 0
        for fs in self.figures.values():
            fig = fs.fig
            try:
                own = "figure.facecolor" in old and not same_color(to_rgba(fig.patch.get_facecolor()), to_rgba(old["figure.facecolor"]))
            except ValueError:
                own = False
            if own:
                continue
            changed += self.swap_figure_colors(fig, old, new)
            fig.canvas._pyd_needs_draw = True
            try:
                from matplotlib.backend_bases import ResizeEvent
                ResizeEvent("resize_event", fig.canvas)._process()   # rebuild blit caches
            except Exception:
                pass
        sent = self.capture(force_all_visible=True)
        return json.dumps({"changed": changed, "sent": sent})

    def match_own_background(self, fs):
        """Before a figure's first draw: if the script gave it a background of
        its own that is light on a dark page (or dark on a light page), give
        its text, ticks and frames colours that read on that background."""
        fs.bg_checked = True
        rc = self.theme_rc or {}
        if "figure.facecolor" not in rc:
            return 0
        from matplotlib.colors import same_color, to_rgba
        try:
            face = to_rgba(fs.fig.patch.get_facecolor())
            theme_bg = to_rgba(rc["figure.facecolor"])
        except ValueError:
            return 0
        if face[3] < 0.5 or same_color(face, theme_bg):
            return 0
        light, theme_dark = _luminance(face) >= 0.45, _luminance(theme_bg) < 0.4
        if light and theme_dark:
            target = LIGHT_FIGURE_RC
        elif not light and not theme_dark:
            target = DARK_FIGURE_RC
        else:
            return 0
        n = self.swap_figure_colors(fs.fig, rc, target)
        if n:
            fs.fig.canvas._pyd_needs_draw = True
        return n

    def swap_figure_colors(self, fig, old, new):
        """Replace colours equal to old[key] with new[key] on one figure.
        Colours are compared without their alpha (a legend frame is drawn at
        80 % opacity), and the alpha already in use is kept."""
        from matplotlib.colors import to_rgba

        def same_color(a, b):
            ra, rb = to_rgba(a), to_rgba(b)
            return all(abs(x - y) < 1.5 / 255 for x, y in zip(ra[:3], rb[:3]))

        def swap(get, set_, key):
            if key not in old or key not in new:
                return 0
            try:
                cur = get()
                if cur is None or isinstance(cur, str) and cur in ("none", "auto", "inherit"):
                    return 0
                cur = to_rgba(cur)
                if cur[3] > 0 and same_color(cur, old[key]):
                    r, g, b, _ = to_rgba(new[key])
                    set_((r, g, b, cur[3]))
                    return 1
            except (ValueError, TypeError, AttributeError):
                return 0
            return 0

        def key_or(primary, fallback):
            return primary if old.get(primary) not in (None, "auto", "inherit") and primary in new else fallback

        changed = 0
        changed += swap(fig.patch.get_facecolor, fig.patch.set_facecolor, "figure.facecolor")
        changed += swap(fig.patch.get_edgecolor, fig.patch.set_edgecolor, "figure.edgecolor")
        if getattr(fig, "_suptitle", None) is not None:
            changed += swap(fig._suptitle.get_color, fig._suptitle.set_color, "text.color")
        for t in fig.texts:
            changed += swap(t.get_color, t.set_color, "text.color")
        for ax in fig.axes:
            changed += swap(ax.patch.get_facecolor, ax.patch.set_facecolor, "axes.facecolor")
            for sp in ax.spines.values():
                changed += swap(sp.get_edgecolor, sp.set_edgecolor, "axes.edgecolor")
            for title in (ax.title, getattr(ax, "_left_title", None), getattr(ax, "_right_title", None)):
                if title is not None:
                    changed += swap(title.get_color, title.set_color, key_or("axes.titlecolor", "text.color"))
            for axis, prefix in ((ax.xaxis, "xtick"), (ax.yaxis, "ytick")):
                changed += swap(axis.label.get_color, axis.label.set_color, "axes.labelcolor")
                ticks = axis.get_major_ticks() + axis.get_minor_ticks()
                if ticks:
                    t0 = ticks[0]
                    tick_key = f"{prefix}.color"
                    label_key = key_or(f"{prefix}.labelcolor", tick_key)
                    try:
                        if tick_key in old and tick_key in new and same_color(to_rgba(t0.tick1line.get_color()), to_rgba(old[tick_key])):
                            axis.set_tick_params(which="both", color=new[tick_key])
                            changed += 1
                        if label_key in old and label_key in new and same_color(to_rgba(t0.label1.get_color()), to_rgba(old[label_key])):
                            axis.set_tick_params(which="both", labelcolor=new[label_key])
                            changed += 1
                        if "grid.color" in old and "grid.color" in new and same_color(to_rgba(t0.gridline.get_color()), to_rgba(old["grid.color"])):
                            axis.set_tick_params(which="both", grid_color=new["grid.color"])
                            changed += 1
                    except (ValueError, TypeError):
                        pass
            for t in ax.texts:
                changed += swap(t.get_color, t.set_color, "text.color")
            leg = ax.get_legend()
            if leg is not None:
                frame = leg.get_frame()
                changed += swap(frame.get_facecolor, frame.set_facecolor, "legend.facecolor")
                changed += swap(frame.get_edgecolor, frame.set_edgecolor, "legend.edgecolor")
                for t in leg.get_texts():
                    changed += swap(t.get_color, t.set_color, key_or("legend.labelcolor", "text.color"))
        return changed

    # ------------------------------------------------------------------
    # errors
    # ------------------------------------------------------------------
    def report_error(self, exc, context="script"):
        """Send a readable error with project-relative file/line links."""
        try:
            te = traceback.TracebackException.from_exception(exc)
        except Exception:  # pragma: no cover
            self.post("error", kind=context, exc_type=type(exc).__name__, message=str(exc),
                      traceback=repr(exc), frames=[], hint=None)
            return
        frames = []
        kept = []
        for fr in te.stack:
            fn = fr.filename or ""
            if fn.startswith(RUNTIME_DIR) or "/_pyodide/" in fn or fn.startswith("<frozen"):
                continue
            kept.append(fr)
            rel = self.rel(fn)
            if rel:
                frames.append({"file": rel, "line": fr.lineno, "name": fr.name, "code": (fr.line or "").strip()})
        te.stack = traceback.StackSummary.from_list(kept)
        if isinstance(exc, SyntaxError) and exc.filename and self.rel(exc.filename):
            frames.append({"file": self.rel(exc.filename), "line": exc.lineno or 0, "name": "<syntax>",
                           "code": (exc.text or "").strip()})
        text = "".join(te.format())
        text = text.replace(self.root + os.sep, "")
        message = str(exc)
        hint = self.error_hint(exc)
        self.post("error", kind=context, exc_type=type(exc).__name__, message=message,
                  traceback=text, frames=frames, hint=hint)
        if context in ("animation", "draw"):
            self.callback_error = text

    def error_hint(self, exc):
        if isinstance(exc, ModuleNotFoundError):
            name = (exc.name or "").split(".")[0]
            if name in UNAVAILABLE:
                return UNAVAILABLE[name]
            if name and any(f.split("/")[0] == name or f == name + ".py" for f in self.files):
                return None
            if name:
                return (f"'{name}' isn't available in the browser: it isn't part of Pyodide and no pure-Python "
                        f"wheel could be installed. If it is one of your own modules, add {name}.py to the "
                        f"demo's \"files\" list in demo.json.")
        if isinstance(exc, FileNotFoundError):
            return ("Data files must be part of the demo: list them under \"files\" in demo.json "
                    "(paths are relative to the demo folder, which is the working directory).")
        if isinstance(exc, MemoryError):
            return "The browser ran out of memory for this run. Try smaller sizes."
        if isinstance(exc, RecursionError):
            return "Recursion is shallower in the browser than on a desktop Python."
        return None


def _json_default(o):
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    if isinstance(o, (set, tuple)):
        return list(o)
    return repr(o)


def _to_jsonable(v):
    if isinstance(v, tuple):
        return list(v)
    return v


def _from_json(v, p):
    if p.get("type") == "tuple" and isinstance(v, list):
        return tuple(v)
    return v


def _same_value(a, b):
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same_value(x, y) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b and type(a) is type(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-15) and type(a) is type(b)
    return a == b


def _safe_rel(path):
    """Normalise a project-relative path; None if it escapes the project."""
    p = path.replace("\\", "/")
    if not p or p.startswith("/") or ":" in p.split("/")[0]:
        return None
    parts = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            return None
        parts.append(seg)
    return "/".join(parts) if parts else None


def _find_imports_fallback(src):
    import ast
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module)
    return sorted(names)


RT = Runtime()
