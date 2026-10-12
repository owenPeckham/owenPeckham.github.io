"""Tests for the worker-side runtime (pyd_runtime.py + pyd_backend.py).

They drive the runtime with a fake host on desktop CPython, so they need
numpy and matplotlib installed (``pip install numpy matplotlib``).  The same
file also runs inside real Pyodide via ``node tests/demonstrator/run_pyodide_tests.mjs``.

    python -m unittest tests/demonstrator/test_runtime.py -v
"""
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
PYDIR = os.path.join(ROOT, "themes", "owenpeckham", "static", "demonstrator", "py")
EXAMPLES = os.path.join(ROOT, "quick-start", "content", "examples")
if PYDIR not in sys.path:
    sys.path.insert(0, PYDIR)

try:
    import numpy  # noqa: F401
    import matplotlib  # noqa: F401
    HAVE_MPL = True
except ImportError:  # pragma: no cover
    HAVE_MPL = False


class FakeHost:
    def __init__(self):
        self.messages = []
        self.frames = []
        self.t = 0.0

    def post(self, text):
        self.messages.append(json.loads(text))

    def frame(self, fid, w, h, n, buf):
        mv = memoryview(buf)
        data = mv.tobytes()
        assert len(data) == w * h * 4, (len(data), w, h)
        self.frames.append({"fig": fid, "w": w, "h": h, "n": n, "sum": sum(data[::997]), "data": data})

    def now(self):
        self.t += 1.0
        return self.t

    def sleep(self, ms):  # only used with JSPI
        raise AssertionError("sleep() should not be awaited without JSPI")

    # helpers
    def of(self, kind):
        return [m for m in self.messages if m.get("type") == kind]

    def errors(self):
        return self.of("error")


def project_files(folder):
    files = []
    for dirpath, _, names in os.walk(folder):
        for n in names:
            if n.endswith((".py", ".csv", ".json", ".txt")):
                full = os.path.join(dirpath, n)
                rel = os.path.relpath(full, folder).replace(os.sep, "/")
                with open(full, "rb") as fh:
                    files.append((rel, fh.read()))
    return files


@unittest.skipUnless(HAVE_MPL, "needs numpy + matplotlib")
class RuntimeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pyd_runtime
        cls.rt_mod = pyd_runtime
        cls.tmp = tempfile.mkdtemp(prefix="pyd-test-")
        cls.rt = pyd_runtime.RT
        cls.host = FakeHost()
        cls.cwd = os.getcwd()
        cls.rt.init(cls.host, json.dumps({"root": os.path.join(cls.tmp, "project")}))

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.cwd)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.host.messages.clear()
        self.host.frames.clear()

    def load(self, files, main="main.py", overrides=None):
        self.rt.write_files(files)
        det = json.loads(self.rt.detect(main, json.dumps(overrides or {}), "{}"))
        self.params = det["params"]
        return det

    def load_example(self, name):
        folder = os.path.join(EXAMPLES, name)
        if not os.path.isdir(folder):
            self.skipTest("examples missing")
        overrides = {}
        if os.path.exists(os.path.join(folder, "demo.json")):
            with open(os.path.join(folder, "demo.json"), encoding="utf-8") as fh:
                m = json.load(fh)
            overrides = {"parameter_overrides": m.get("parameter_overrides"), "buttons": m.get("buttons")}
        return self.load(project_files(folder), overrides=overrides)

    def run_main(self, values=None, main="main.py", **extra):
        cfg = {"main": main, "params": self.params, "values": values or {},
               "viewport": {"width": 640, "height": 400, "dpr": 1}, "theme_rc": extra.pop("theme_rc", {})}
        cfg.update(extra)
        return json.loads(self.rt.run(json.dumps(cfg)))

    def tick(self, dt=40, force=False):
        return json.loads(self.rt.tick(dt, force))

    def param(self, pid):
        return next(p for p in self.params if p["id"] == pid)


class TestOscillator(RuntimeTestCase):
    def test_runs_and_animates(self):
        self.load_example("oscillator")
        out = self.run_main()
        self.assertEqual(out["outcome"], "done", self.host.errors())
        self.assertEqual(len(out["figures"]), 1)
        self.assertTrue(out["figures"][0]["animated"])
        self.assertGreaterEqual(out["timers"], 1)
        self.assertEqual(len(self.host.frames), 1)          # first frame from plt.show()
        f0 = self.host.frames[0]
        # dpi was fitted to the 640x400 viewport (figsize 9 x 3.6 in)
        self.assertLessEqual(f0["w"], 640)
        self.assertLessEqual(f0["h"], 400)
        r = self.tick(40)
        self.assertEqual(r["fired"], 1)
        self.assertEqual(r["sent"], 1)
        self.assertIsNotNone(r["next_due_in"])
        main = sys.modules["__main__"]
        self.assertGreater(main.t, 0)

    def test_no_double_fire_when_behind(self):
        self.load_example("oscillator")
        self.run_main()
        r = self.tick(1000)    # far behind: still one firing
        self.assertEqual(r["fired"], 1)

    def test_snap_fires_a_frame_due_within_the_display_frame(self):
        self.load_example("oscillator")           # INTERVAL_MS = 33
        self.run_main()
        self.tick(40)                             # fires; the next frame is due 33 ms later
        r = json.loads(self.rt.tick(20, False, 6))
        self.assertEqual(r["fired"], 0)           # 13 ms early: too early even with the snap
        self.assertAlmostEqual(r["next_due_in"], 13, delta=0.6)
        r = json.loads(self.rt.tick(8, False, 6))
        self.assertEqual(r["fired"], 1)           # 5 ms early: within the snap, fires now
        self.assertAlmostEqual(r["next_due_in"], 33, delta=0.6)   # and keeps the 33 ms rhythm
        r = json.loads(self.rt.tick(4, False, 0))
        self.assertEqual(r["fired"], 0)           # without a snap nothing fires early

    def test_live_param_reaches_next_frame(self):
        self.load_example("oscillator")
        self.run_main()
        self.tick(40)
        main = sys.modules["__main__"]
        res = json.loads(self.rt.set_params(json.dumps({"FREQ": 3.5, "physics.py:NONLINEARITY": 0.5})))
        self.assertEqual(sorted(res["applied"]), ["FREQ", "physics.py:NONLINEARITY"], res)
        self.assertEqual(main.FREQ, 3.5)
        self.assertEqual(sys.modules["physics"].NONLINEARITY, 0.5)
        before = main.t
        self.tick(40)
        self.assertGreater(main.t, before)

    def test_restart_injects_literal(self):
        self.load_example("oscillator")
        self.run_main({"TRAIL": 50, "FREQ": 2.0})
        main = sys.modules["__main__"]
        self.assertEqual(main.history_x.maxlen, 50)
        self.assertEqual(main.FREQ, 2.0)
        # source on disk is untouched
        with open(os.path.join(self.rt.root, "main.py"), encoding="utf-8") as fh:
            self.assertIn("TRAIL = 240", fh.read())

    def test_rerun_gives_fresh_modules(self):
        self.load_example("oscillator")
        self.run_main()
        sys.modules["physics"].NONLINEARITY = 9.0
        self.run_main()
        self.assertEqual(sys.modules["physics"].NONLINEARITY, 0.0)

    def test_values_for_unknown_or_bad_params_are_ignored(self):
        self.load_example("oscillator")
        out = self.run_main({"FREQ": "not a number", "NOPE": 1})
        self.assertEqual(out["outcome"], "done")
        self.assertTrue(any("ignored value" in m.get("text", "") for m in self.host.of("console")))

    def test_pause_step_semantics_via_tick(self):
        self.load_example("oscillator")
        self.run_main()
        r = self.tick(0, force=True)   # "step" jumps to the next due timer
        self.assertEqual(r["fired"], 1)


class TestHeatMapPackage(RuntimeTestCase):
    def test_package_relative_imports_and_data_file(self):
        self.load_example("heat_map")
        out = self.run_main()
        self.assertEqual(out["outcome"], "done", self.host.errors())
        main = sys.modules["__main__"]
        self.assertEqual(len(main.sources), 4)          # read from data/sources.csv
        self.assertIn("heatsim.sources.loader", sys.modules)

    def test_restart_param_in_subpackage(self):
        self.load_example("heat_map")
        self.run_main({"GRID": 32, "heatsim/grid.py:AMBIENT": 0.9})
        main = sys.modules["__main__"]
        self.assertEqual(main.plate.temperature.shape, (32, 32))
        self.assertEqual(sys.modules["heatsim.grid"].AMBIENT, 0.9)

    def test_live_param(self):
        self.load_example("heat_map")
        self.run_main()
        json.loads(self.rt.set_params(json.dumps({"DIFFUSIVITY": 0.1})))
        self.assertEqual(sys.modules["__main__"].DIFFUSIVITY, 0.1)


class TestArgparseAndConfig(RuntimeTestCase):
    def test_argv_rebuilt(self):
        self.load_example("cli_style")
        out = self.run_main({"args.freq": 3.0, "args.wave": "square", "args.grid": True})
        self.assertEqual(out["outcome"], "done", self.host.errors())
        status = self.host.of("status")[-1]
        self.assertEqual(status["argv"], ["main.py", "--freq=3.0", "--wave=square", "--grid"])
        self.tick(40)
        self.assertGreaterEqual(len(self.host.frames), 2)   # blit animation produced frames

    def test_dataclass_and_main_defaults(self):
        self.load_example("config_style")
        out = self.run_main({"GasConfig.n_particles": 30, "main.seed": 3, "GasConfig.integrator": "euler"})
        self.assertEqual(out["outcome"], "done", self.host.errors())
        anim = self.rt.animations[-1]
        self.assertEqual(len(anim._fig.axes[0].collections[0].get_offsets()), 30)
        self.assertIn("euler", anim._fig.axes[0].get_title())


class TestPatterns(RuntimeTestCase):
    def test_static_figure_renders_once(self):
        self.load_example("static_plot")
        out = self.run_main({"SHAPE": "gaussian"})
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(out["timers"], 0)
        self.assertEqual(len(self.host.frames), 1)
        self.assertFalse(out["figures"][0]["animated"])
        self.assertIn("gaussian", sys.modules["__main__"].ax.get_title())

    def test_figures_never_shown_are_auto_shown(self):
        self.load([("main.py", b"import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nfig2 = plt.figure()\n")])
        out = self.run_main()
        self.assertEqual(len(out["figures"]), 2)
        self.assertEqual(len(self.host.frames), 2)

    def test_show_then_close(self):
        self.load([("main.py", b"import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.show()\nplt.close('all')\n")])
        out = self.run_main()
        self.assertEqual(len(self.host.frames), 1)
        self.assertTrue(out["figures"][0]["closed"])

    def test_pause_loop_without_jspi_streams_frames(self):
        self.load([("main.py", textwrap.dedent("""
            import matplotlib.pyplot as plt
            AMP = 1.0
            fig, ax = plt.subplots()
            line, = ax.plot([0, 1], [0, 0])
            for i in range(5):
                line.set_ydata([0, AMP * i])
                plt.pause(0.001)
            """).encode())])
        self.rt.fps_cap = 1e6
        out = self.run_main(fps=1e6)
        self.assertEqual(out["outcome"], "done", self.host.errors())
        self.assertGreaterEqual(len(self.host.frames), 5)
        self.assertEqual(self.param("AMP")["mode"], "live")

    def test_draw_flush_events_loop(self):
        self.load([("main.py", textwrap.dedent("""
            import time
            import matplotlib.pyplot as plt
            plt.ion()
            fig, ax = plt.subplots()
            line, = ax.plot([0, 1], [0, 0])
            for i in range(3):
                line.set_ydata([0, i])
                fig.canvas.draw()
                fig.canvas.flush_events()
                time.sleep(0.001)
            """).encode())])
        out = self.run_main(fps=1e6)
        self.assertEqual(out["outcome"], "done", self.host.errors())
        self.assertGreaterEqual(len(self.host.frames), 3)

    def test_finite_animation_finishes(self):
        self.load([("main.py", textwrap.dedent("""
            import matplotlib.pyplot as plt
            from matplotlib.animation import FuncAnimation
            fig, ax = plt.subplots()
            line, = ax.plot([], [])
            def update(i):
                line.set_data([0, 1], [0, i])
                return line,
            def gen():
                yield from range(3)
            def main():
                anim = FuncAnimation(fig, update, frames=gen, interval=10, repeat=False)
                plt.show()
            main()   # the animation object is only referenced inside main()
            """).encode())])
        self.run_main()
        fired = 0
        for _ in range(10):
            r = self.tick(10)
            fired += r["fired"]
            if r["running"] == 0:
                break
        self.assertEqual(r["running"], 0)
        self.assertGreaterEqual(fired, 3)

    def test_repeat_delay(self):
        self.load([("main.py", textwrap.dedent("""
            import matplotlib.pyplot as plt
            from matplotlib.animation import FuncAnimation
            fig, ax = plt.subplots()
            anim = FuncAnimation(fig, lambda i: None, frames=2, interval=10, repeat=True, repeat_delay=500)
            plt.show()
            """).encode())])
        self.run_main()
        dues = [self.tick(10)["next_due_in"] for _ in range(4)]
        self.assertIn(500, [round(d) for d in dues if d is not None])

    def test_blit_animation(self):
        self.load([("main.py", textwrap.dedent("""
            import numpy as np
            import matplotlib.pyplot as plt
            from matplotlib.animation import FuncAnimation
            fig, ax = plt.subplots()
            ax.set_ylim(-1, 1)
            x = np.linspace(0, 6, 50)
            line, = ax.plot(x, np.sin(x))
            def update(i):
                line.set_ydata(np.sin(x + i / 5))
                return line,
            anim = FuncAnimation(fig, update, interval=20, blit=True)
            plt.show()
            """).encode())])
        self.run_main()
        a = self.tick(20)
        b = self.tick(20)
        self.assertEqual((a["sent"], b["sent"]), (1, 1))
        self.assertNotEqual(self.host.frames[-1]["sum"], self.host.frames[-2]["sum"])

    def test_save_is_friendly(self):
        self.load([("main.py", textwrap.dedent("""
            import matplotlib.pyplot as plt
            from matplotlib.animation import FuncAnimation
            fig = plt.figure()
            anim = FuncAnimation(fig, lambda i: None, frames=3)
            anim.save("movie.mp4")
            print("still running")
            """).encode())])
        out = self.run_main()
        self.assertEqual(out["outcome"], "done", self.host.errors())
        notes = " ".join(m.get("text", "") for m in self.host.of("console"))
        self.assertIn("anim.save", notes)

    def test_gui_backend_request_is_ignored(self):
        self.load([("main.py", b"import matplotlib\nmatplotlib.use('TkAgg')\nimport matplotlib.pyplot as plt\nplt.plot([1])\nplt.show()\n")])
        out = self.run_main()
        self.assertEqual(out["outcome"], "done", self.host.errors())
        self.assertEqual(len(self.host.frames), 1)

    def test_input_is_friendly_error(self):
        self.load([("main.py", b"x = input('value? ')\n")])
        out = self.run_main()
        self.assertEqual(out["outcome"], "error")
        self.assertIn("input()", self.host.errors()[0]["message"])

    def test_viewport_refit(self):
        self.load_example("static_plot")
        self.run_main()
        w0 = self.host.frames[-1]["w"]
        json.loads(self.rt.set_viewport(json.dumps({"width": 320, "height": 200, "dpr": 1})))
        self.assertEqual(len(self.host.frames), 2)
        self.assertNotEqual(self.host.frames[-1]["w"], w0)


class TestErrors(RuntimeTestCase):
    def test_error_in_module_reports_file_and_line(self):
        self.load([
            ("main.py", b"import helper\nhelper.boom()\n"),
            ("helper.py", b"def boom():\n    return 1 / 0\n"),
        ])
        out = self.run_main()
        self.assertEqual(out["outcome"], "error")
        err = self.host.errors()[0]
        self.assertEqual(err["exc_type"], "ZeroDivisionError")
        self.assertEqual(err["frames"][-1], {"file": "helper.py", "line": 2, "name": "boom", "code": "return 1 / 0"})
        self.assertNotIn(self.rt.root, err["traceback"])
        self.assertNotIn("pyd_runtime", err["traceback"])

    def test_syntax_error_location(self):
        self.load([("main.py", b"x = (\n")])
        self.run_main()
        err = self.host.errors()[0]
        self.assertEqual(err["exc_type"], "SyntaxError")
        self.assertEqual(err["frames"][-1]["file"], "main.py")

    def test_missing_module_hint(self):
        self.load([("main.py", b"import definitely_not_a_module\n")])
        self.run_main()
        err = self.host.errors()[0]
        self.assertIn("isn't available", err["hint"])
        missing = json.loads(self.rt.missing_imports())
        self.assertIn("definitely_not_a_module", missing)

    def test_error_in_animation_callback(self):
        self.load([("main.py", textwrap.dedent("""
            import matplotlib.pyplot as plt
            from matplotlib.animation import FuncAnimation
            fig = plt.figure()
            K = 0.0
            def update(i):
                return 1 / K
            anim = FuncAnimation(fig, update, interval=10)
            plt.show()
            """).encode())])
        out = self.run_main()
        # FuncAnimation draws the first frame (calls update) when shown
        errs = self.host.errors()
        if not errs:
            r = self.tick(10)
            self.assertTrue(r["error"])
            errs = self.host.errors()
        self.assertEqual(errs[0]["exc_type"], "ZeroDivisionError")
        self.assertEqual(errs[0]["frames"][-1]["line"], 7)

    def test_unsafe_paths_rejected(self):
        with self.assertRaises(ValueError):
            self.rt.write_files([("../evil.py", b"")])
        with self.assertRaises(ValueError):
            self.rt.write_files([("/abs.py", b"")])


class TestInteractionAndTheme(RuntimeTestCase):
    def test_button_calls_function(self):
        self.load_example("with_hints")
        self.run_main()
        main = sys.modules["__main__"]
        v0 = main.state[1]
        res = json.loads(self.rt.call_button("kick", "main.py"))
        self.assertTrue(res["ok"])
        self.assertGreater(main.state[1], v0)

    def test_forced_restart_and_override(self):
        det = self.load_example("with_hints")
        p = {x["id"]: x for x in det["params"]}
        self.assertEqual(p["DRIVE_FREQ"]["mode"], "restart")
        self.assertFalse(p["WINDOW"]["hidden"])

    def test_pointer_events_reach_widgets(self):
        self.load([("main.py", textwrap.dedent("""
            import matplotlib.pyplot as plt
            from matplotlib.widgets import Slider
            fig = plt.figure(figsize=(4, 2))
            ax = fig.add_axes([0.1, 0.4, 0.8, 0.2])
            s = Slider(ax, "v", 0.0, 10.0, valinit=1.0)
            got = []
            s.on_changed(got.append)
            plt.show()
            """).encode())])
        self.run_main()
        fid = self.rt.figures and next(iter(self.rt.figures))
        for kind in ("down", "up"):
            json.loads(self.rt.pointer(json.dumps({"fig": fid, "kind": kind, "x": 0.82, "y": 0.5, "button": 0})))
        main = sys.modules["__main__"]
        self.assertTrue(main.got, "slider callback not called")
        self.assertGreater(main.s.val, 5)

    def test_retheme_only_swaps_defaults(self):
        light = {"figure.facecolor": "#ffffff", "axes.facecolor": "#ffffff", "text.color": "#14151a",
                 "axes.labelcolor": "#14151a", "xtick.color": "#14151a", "ytick.color": "#14151a",
                 "axes.edgecolor": "#70747e", "legend.facecolor": "#ffffff", "legend.labelcolor": "#14151a"}
        dark = {"figure.facecolor": "#0f1014", "axes.facecolor": "#0f1014", "text.color": "#e6e7eb",
                "axes.labelcolor": "#e6e7eb", "xtick.color": "#e6e7eb", "ytick.color": "#e6e7eb",
                "axes.edgecolor": "#9a9eab", "legend.facecolor": "#0f1014", "legend.labelcolor": "#e6e7eb"}
        self.load([("main.py", textwrap.dedent("""
            import matplotlib.pyplot as plt
            fig, (a, b) = plt.subplots(1, 2)
            b.set_facecolor("#ffeeaa")      # explicit: must be kept
            a.set_xlabel("x")
            a.plot([0, 1], label="line")
            a.legend()                      # frame drawn at 80 % opacity
            plt.show()
            """).encode())])
        self.run_main(theme_rc=light)
        res = json.loads(self.rt.retheme(json.dumps(light), json.dumps(dark)))
        self.assertGreater(res["changed"], 0)
        from matplotlib.colors import to_hex
        main = sys.modules["__main__"]
        self.assertEqual(to_hex(main.fig.get_facecolor()), "#0f1014")
        self.assertEqual(to_hex(main.a.get_facecolor()), "#0f1014")
        self.assertEqual(to_hex(main.b.get_facecolor()), "#ffeeaa")
        self.assertEqual(to_hex(main.a.xaxis.label.get_color()), "#e6e7eb")
        frame = main.a.get_legend().get_frame()
        self.assertEqual(to_hex(frame.get_facecolor()), "#0f1014")
        self.assertAlmostEqual(frame.get_facecolor()[3], 0.8, places=2)
        self.assertEqual(to_hex(main.a.get_legend().get_texts()[0].get_color()), "#e6e7eb")

    def test_own_light_background_on_dark_page_stays_readable(self):
        dark = {"figure.facecolor": "#0f1014", "axes.facecolor": "#0f1014", "text.color": "#e6e7eb",
                "axes.labelcolor": "#e6e7eb", "xtick.color": "#9a9eab", "ytick.color": "#9a9eab",
                "xtick.labelcolor": "#e6e7eb", "ytick.labelcolor": "#e6e7eb", "axes.edgecolor": "#9a9eab",
                "axes.titlecolor": "#f2f3f5"}
        light = {k: "#ffffff" if "face" in k else "#14151a" for k in dark}
        self.load([("main.py", textwrap.dedent("""
            import matplotlib.pyplot as plt
            fig, (a, b) = plt.subplots(1, 2)
            fig.patch.set_facecolor("#ffeecc")   # the script's own light background
            a.set_facecolor("#ccddff")            # explicit axes colour
            a.plot([0, 1], color="red")
            b.set_title("t")
            b.set_xlabel("x")
            plain, ax3 = plt.subplots()
            ax3.set_xlabel("y")
            plt.show()
            """).encode())])
        self.run_main(theme_rc=dark)
        from matplotlib.colors import to_hex
        main = sys.modules["__main__"]
        # own light background: matplotlib's dark-on-light colours, explicit colours untouched
        self.assertEqual(to_hex(main.fig.get_facecolor()), "#ffeecc")
        self.assertEqual(to_hex(main.a.get_facecolor()), "#ccddff")
        self.assertEqual(to_hex(main.b.get_facecolor()), "#ffffff")
        self.assertEqual(to_hex(main.b.xaxis.label.get_color()), "#000000")
        self.assertEqual(to_hex(main.b.title.get_color()), "#000000")
        self.assertEqual(to_hex(main.b.xaxis.get_major_ticks()[0].label1.get_color()), "#000000")
        self.assertEqual(to_hex(main.a.lines[0].get_color()), "#ff0000")
        # a figure on the theme background keeps the theme
        self.assertEqual(to_hex(main.plain.get_facecolor()), "#0f1014")
        self.assertEqual(to_hex(main.ax3.xaxis.label.get_color()), "#e6e7eb")
        # switching the page theme leaves the own-background figure alone
        self.rt.retheme(json.dumps(dark), json.dumps(light))
        self.assertEqual(to_hex(main.b.xaxis.label.get_color()), "#000000")
        self.assertEqual(to_hex(main.plain.get_facecolor()), "#ffffff")
        self.assertEqual(to_hex(main.ax3.xaxis.label.get_color()), "#14151a")


class TestDemoParams(RuntimeTestCase):
    def test_helper_live_and_callbacks(self):
        self.load([("main.py", textwrap.dedent("""
            import matplotlib.pyplot as plt
            from matplotlib.animation import FuncAnimation
            try:
                from demo_params import params, on_change
            except ImportError:
                params = {"gain": 2.0, "n": 5}
                def on_change(name, cb):
                    pass
            seen = []
            on_change("gain", seen.append)
            data = list(range(params["n"]))
            fig = plt.figure()
            def update(i):
                return params["gain"] * i
            anim = FuncAnimation(fig, update, interval=10)
            plt.show()
            """).encode())])
        self.run_main({"params.n": 3})
        main = sys.modules["__main__"]
        self.assertEqual(main.data, [0, 1, 2])
        res = json.loads(self.rt.set_params(json.dumps({"params.gain": 4.5})))
        self.assertEqual(res["applied"], ["params.gain"])
        self.assertEqual(main.params["gain"], 4.5)
        self.assertEqual(main.seen, [4.5])


if __name__ == "__main__":
    unittest.main()
