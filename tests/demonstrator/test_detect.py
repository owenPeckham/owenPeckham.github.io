"""Unit tests for the parameter detector (pyd_detect.py).

Run from the repository root with plain CPython (no dependencies):

    python -m unittest discover -s tests/demonstrator -p "test_*.py" -v
"""
import ast
import os
import sys
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "themes", "owenpeckham", "static", "demonstrator", "py"))

import pyd_detect as D  # noqa: E402


def run(main_src, extra=None, main="main.py", overrides=None, options=None):
    files = {main: textwrap.dedent(main_src)}
    for k, v in (extra or {}).items():
        files[k] = textwrap.dedent(v)
    return D.detect(files, main, overrides, options)


def by_id(result):
    return {p["id"]: p for p in result["params"]}


class TestConstants(unittest.TestCase):
    SRC = """
        import numpy as np
        FREQ = 2.0          # natural frequency [Hz]
        N = 200
        SHOW_GRID = True
        METHOD = "rk4"
        COLOURS = ("r", "g")
        SIZE = (4, 3)
        alpha = 0.5
        __version__ = "1.0"
        DATA_FILE = "data/x.csv"
        OUT_DIR = "results"
        IGNORED = 3         # demo: ignore
        TWO_PI_F = 2 * np.pi * 1.5
        DT = 1 / 60
        LENGTH = FREQ * 3
        """

    def setUp(self):
        self.r = run(self.SRC)
        self.p = by_id(self.r)

    def test_types(self):
        self.assertEqual(self.p["FREQ"]["type"], "float")
        self.assertEqual(self.p["N"]["type"], "int")
        self.assertEqual(self.p["SHOW_GRID"]["type"], "bool")
        self.assertEqual(self.p["METHOD"]["type"], "str")
        self.assertEqual(self.p["SIZE"]["type"], "tuple")
        self.assertEqual(self.p["SIZE"]["elem_type"], "int")
        self.assertEqual(self.p["SIZE"]["length"], 2)

    def test_skips(self):
        skipped = {s["name"]: s["reason"] for s in self.r["skipped"]}
        self.assertNotIn("COLOURS", self.p)
        self.assertIn("unsupported", skipped["COLOURS"])
        self.assertNotIn("__version__", self.p)
        self.assertNotIn("DATA_FILE", self.p)
        self.assertIn("path", skipped["DATA_FILE"])
        self.assertNotIn("OUT_DIR", self.p)
        self.assertNotIn("IGNORED", self.p)
        self.assertIn("ignore", skipped["IGNORED"])

    def test_constant_arithmetic_is_a_literal(self):
        self.assertAlmostEqual(self.p["DT"]["default"], 1 / 60)
        self.assertTrue(self.p["DT"]["expr"])
        self.assertAlmostEqual(self.p["TWO_PI_F"]["default"], 2 * 3.141592653589793 * 1.5)

    def test_expressions_of_params_are_derived(self):
        self.assertNotIn("LENGTH", self.p)
        derived = {d["name"]: d for d in self.r["derived"]}
        self.assertEqual(derived["LENGTH"]["depends"], ["FREQ"])

    def test_lower_case_goes_to_other(self):
        self.assertEqual(self.p["alpha"]["tier"], "other")
        self.assertEqual(self.p["alpha"]["group"], "Other detected values")
        self.assertEqual(self.p["FREQ"]["tier"], "primary")
        ranks = [p["id"] for p in self.r["params"]]
        self.assertLess(ranks.index("FREQ"), ranks.index("alpha"))

    def test_label_unit_help_from_comment(self):
        f = self.p["FREQ"]
        self.assertEqual(f["label"], "Natural frequency")
        self.assertEqual(f["unit"], "Hz")
        self.assertIsNone(f["help"])      # the comment is fully used as label + unit
        self.assertEqual(self.p["SHOW_GRID"]["label"], "Show grid")

    def test_apply_site_points_at_literal(self):
        a = self.p["FREQ"]["apply"]
        self.assertEqual(a["kind"], "literal")
        self.assertEqual(a["span"][0], 3)

    def test_caps(self):
        src = "\n".join(f"v{i} = {i}" for i in range(20))
        r = run(src, options={"max_other": 5})
        self.assertEqual(len(r["params"]), 5)
        self.assertTrue(any("max_other" in s["reason"] for s in r["skipped"]))

    def test_reassigned_and_state_are_not_parameters(self):
        r = run("""
            X = 1.0
            X = 2.0
            T = 0.0
            def update(i):
                global T
                T += 0.1
            """)
        p = by_id(r)
        self.assertNotIn("X", p)
        self.assertNotIn("T", p)


class TestPrettifyAndRanges(unittest.TestCase):
    def test_prettify(self):
        self.assertEqual(D.prettify("DAMPING_RATIO"), "Damping ratio")
        self.assertEqual(D.prettify("dampingRatio"), "Damping ratio")
        self.assertEqual(D.prettify("N"), "N")
        self.assertEqual(D.prettify("DT"), "DT")
        self.assertEqual(D.prettify("rk4_order"), "RK4 order")
        self.assertEqual(D.prettify("n_particles"), "N particles")

    def test_units_from_names(self):
        self.assertEqual(D.split_name_unit("FREQ_HZ"), ("FREQ", "Hz"))
        self.assertEqual(D.split_name_unit("LENGTH_MM"), ("LENGTH", "mm"))
        self.assertEqual(D.split_name_unit("ANGLE_DEG"), ("ANGLE", "°"))
        self.assertEqual(D.split_name_unit("N"), ("N", None))
        r = by_id(run("TIMEOUT_MS = 250\n"))
        self.assertEqual(r["TIMEOUT_MS"]["unit"], "ms")
        self.assertEqual(r["TIMEOUT_MS"]["label"], "Timeout")

    def test_positive_float_is_log_decade_each_side(self):
        r = D.infer_range("FREQ", "float", 2.0)
        self.assertEqual((r["min"], r["max"], r["scale"]), (0.2, 20.0, "log"))

    def test_zero_float(self):
        r = D.infer_range("OFFSET", "float", 0.0)
        self.assertEqual((r["min"], r["max"], r["scale"]), (-1.0, 1.0, "linear"))

    def test_negative_float_symmetric(self):
        r = D.infer_range("GRAVITY", "float", -9.81)
        self.assertLess(r["min"], -9.81)
        self.assertGreater(r["max"], 0)
        self.assertEqual(r["scale"], "linear")

    def test_ratio_and_angle(self):
        self.assertEqual(D.infer_range("MIX_RATIO", "float", 0.3)["max"], 1.0)
        self.assertEqual(D.infer_range("THETA_DEG", "float", 45.0)["max"], 360.0)
        # "integration" contains "ratio" but is not a ratio
        self.assertEqual(D.infer_range("dt", "float", 0.01, None, "integration time step")["scale"], "log")

    def test_ints(self):
        self.assertEqual(D.infer_range("SEED", "int", 7), {"min": 0, "max": 1000, "step": 1, "scale": "linear"})
        r = D.infer_range("N", "int", 200)
        self.assertEqual((r["min"], r["max"], r["scale"]), (20, 2000, "log"))
        r = D.infer_range("STEPS", "int", 3)
        self.assertEqual((r["min"], r["max"]), (1, 10))

    def test_range_includes_default(self):
        r = by_id(run("K = 5.0  # demo: range=0..2\n"))
        self.assertEqual(r["K"]["max"], 5.0)

    def test_choices_from_comment(self):
        r = by_id(run('CMAP = "viridis"  # one of: viridis, magma, plasma\n'))
        self.assertEqual(r["CMAP"]["type"], "choice")
        self.assertEqual(r["CMAP"]["choices"], ["viridis", "magma", "plasma"])


class TestLiveRestart(unittest.TestCase):
    def test_read_in_animation_callback_is_live(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            FREQ = 2.0
            def update(i):
                return FREQ * i
            anim = FuncAnimation(fig, update, interval=30)
            """))
        self.assertEqual(r["FREQ"]["mode"], "live")
        self.assertEqual(r["FREQ"]["live_targets"], [{"kind": "global", "module": "main.py", "name": "FREQ"}])
        self.assertIn("update()", r["FREQ"]["mode_reason"])

    def test_module_level_only_is_restart(self):
        r = by_id(run("""
            import numpy as np
            N = 100
            x = np.linspace(0, 1, N)
            """))
        self.assertEqual(r["N"]["mode"], "restart")

    def test_per_frame_and_derived_is_restart(self):
        r = by_id(run("""
            import numpy as np
            from matplotlib.animation import FuncAnimation
            L = 2.0
            x = np.linspace(0, L, 50)
            def update(i):
                return x * L
            FuncAnimation(fig, update)
            """))
        self.assertEqual(r["L"]["mode"], "restart")
        self.assertIn("set things up", r["L"]["mode_reason"])

    def test_cosmetic_startup_use_still_live(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            FREQ = 2.0
            ax.set_title(f"f = {FREQ}")
            print("frequency", FREQ)
            def update(i):
                return FREQ
            FuncAnimation(fig, update)
            """))
        self.assertEqual(r["FREQ"]["mode"], "live")
        self.assertIn("labels", r["FREQ"]["mode_reason"])

    def test_default_argument_capture_is_restart(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            FREQ = 2.0
            def update(i, f=FREQ):
                return f * i
            FuncAnimation(fig, update)
            """))
        self.assertEqual(r["FREQ"]["mode"], "restart")

    def test_closure_capture_is_restart(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            FREQ = 2.0
            def make(freq):
                def update(i):
                    return freq * i
                return update
            FuncAnimation(fig, make(FREQ))
            """))
        self.assertEqual(r["FREQ"]["mode"], "restart")

    def test_shadowed_local_is_not_a_read(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            FREQ = 2.0
            def update(i):
                FREQ = 3.0
                return FREQ
            FuncAnimation(fig, update)
            """))
        self.assertEqual(r["FREQ"]["mode"], "restart")

    def test_global_declaration_read_is_live(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            GAIN = 2.0
            def update(i):
                global GAIN
                return GAIN * i
            FuncAnimation(fig, update)
            """))
        self.assertEqual(r["GAIN"]["mode"], "live")

    def test_precomputed_with_odeint_is_restart(self):
        r = by_id(run("""
            from scipy.integrate import odeint
            from matplotlib.animation import FuncAnimation
            K = 1.5
            def rhs(y, t):
                return -K * y
            sol = odeint(rhs, 1.0, ts)
            def update(i):
                return sol[i]
            FuncAnimation(fig, update)
            """))
        self.assertEqual(r["K"]["mode"], "restart")

    def test_solver_called_per_frame_is_live(self):
        r = by_id(run("""
            from scipy.integrate import solve_ivp
            from matplotlib.animation import FuncAnimation
            K = 1.5
            def rhs(t, y):
                return -K * y
            def update(i):
                return solve_ivp(rhs, (0, 1), [1.0])
            FuncAnimation(fig, update)
            """))
        self.assertEqual(r["K"]["mode"], "live")

    def test_callgraph_across_modules_and_from_import(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            from helpers import step, GAIN
            import helpers as h
            def update(i):
                return step(i) + GAIN + h.OFFSET
            FuncAnimation(fig, update)
            """, extra={"helpers.py": """
            G = 9.81
            GAIN = 2.0
            OFFSET = 0.5
            def step(i):
                return G * i
            """}))
        self.assertEqual(r["helpers.py:G"]["mode"], "live")
        self.assertEqual(r["helpers.py:OFFSET"]["mode"], "live")
        gain = r["helpers.py:GAIN"]
        self.assertEqual(gain["mode"], "live")
        targets = {(t["module"], t["name"]) for t in gain["live_targets"]}
        self.assertEqual(targets, {("helpers.py", "GAIN"), ("main.py", "GAIN")})

    def test_method_called_from_update_is_live(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            DRAG = 0.1
            class Sim:
                def __init__(self):
                    self.v = 1.0
                def step(self):
                    self.v -= DRAG * self.v
            sim = Sim()
            def update(i):
                sim.step()
            FuncAnimation(fig, update)
            """))
        self.assertEqual(r["DRAG"]["mode"], "live")

    def test_init_only_is_restart(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            START = 1.0
            class Sim:
                def __init__(self):
                    self.v = START
                def step(self):
                    self.v *= 0.9
            sim = Sim()
            def update(i):
                sim.step()
            FuncAnimation(fig, update)
            """))
        self.assertEqual(r["START"]["mode"], "restart")

    def test_blocking_pause_loop_is_live(self):
        r = run("""
            import matplotlib.pyplot as plt
            FREQ = 1.0
            N_STEPS = 500
            for i in range(N_STEPS):
                y = FREQ * i
                plt.pause(0.01)
            """)
        p = by_id(r)
        self.assertEqual(p["FREQ"]["mode"], "live")
        self.assertEqual(p["FREQ"]["live_via"], ["loop"])
        self.assertEqual(p["N_STEPS"]["mode"], "restart")
        self.assertEqual(len(r["blocking_loops"]), 1)

    def test_lambda_callback(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            A = 1.0
            FuncAnimation(fig, lambda i: A * i)
            """))
        self.assertEqual(r["A"]["mode"], "live")

    def test_mpl_connect_callback(self):
        r = by_id(run("""
            STEP = 0.5
            def on_click(event):
                print(STEP)
            fig.canvas.mpl_connect("button_press_event", on_click)
            """))
        self.assertEqual(r["STEP"]["mode"], "live")

    def test_startup_function_is_restart(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            N = 50
            def build():
                return list(range(N))
            data = build()
            def update(i):
                return data[i]
            FuncAnimation(fig, update)
            """))
        self.assertEqual(r["N"]["mode"], "restart")

    def test_main_guard_and_nested_update(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            FREQ = 2.0
            def main():
                def update(i):
                    return FREQ * i
                anim = FuncAnimation(fig, update)
                return anim
            if __name__ == "__main__":
                main()
            """))
        self.assertEqual(r["FREQ"]["mode"], "live")


class TestConfigSources(unittest.TestCase):
    def test_config_dict(self):
        r = run("""
            from matplotlib.animation import FuncAnimation
            CONFIG = {
                "freq": 2.0,      # frequency [Hz]
                "n": 64,          # demo: range=8..256
                "label": "hello",
                "path": "out/x.png",
            }
            grid = make(CONFIG["n"])
            def update(i):
                return CONFIG["freq"] * i
            FuncAnimation(fig, update)
            """)
        p = by_id(r)
        self.assertEqual(p["CONFIG.freq"]["mode"], "live")
        self.assertEqual(p["CONFIG.freq"]["live_targets"], [{"kind": "item", "module": "main.py", "name": "CONFIG", "key": "freq"}])
        self.assertEqual(p["CONFIG.freq"]["unit"], "Hz")
        self.assertEqual(p["CONFIG.n"]["mode"], "restart")
        self.assertEqual(p["CONFIG.n"]["min"], 8)
        self.assertEqual(p["CONFIG.n"]["max"], 256)
        self.assertNotIn("CONFIG.path", p)
        self.assertEqual(p["CONFIG.freq"]["group"], "CONFIG")

    def test_dataclass_and_namedtuple(self):
        r = run("""
            from dataclasses import dataclass, field
            from typing import Literal, NamedTuple
            @dataclass
            class Config:
                n: int = 100                  # number of particles
                dt: float = 0.01
                method: Literal["euler", "rk4"] = "rk4"
                size: tuple[float, float] = (4.0, 3.0)
                data: list = field(default_factory=list)
                temp: float = field(default=1.5)
            class Opts(NamedTuple):
                verbose: bool = False
            cfg = Config()
            """)
        p = by_id(r)
        self.assertEqual(p["Config.n"]["type"], "int")
        self.assertEqual(p["Config.n"]["label"], "Number of particles")
        self.assertEqual(p["Config.method"]["type"], "choice")
        self.assertEqual(p["Config.method"]["choices"], ["euler", "rk4"])
        self.assertEqual(p["Config.size"]["type"], "tuple")
        self.assertNotIn("Config.data", p)
        self.assertEqual(p["Config.temp"]["default"], 1.5)
        self.assertEqual(p["Opts.verbose"]["type"], "bool")
        self.assertEqual(p["Config.n"]["mode"], "restart")

    def test_dataclass_instance_read_per_frame_is_live(self):
        p = by_id(run("""
            from dataclasses import dataclass
            from matplotlib.animation import FuncAnimation
            @dataclass
            class Config:
                speed: float = 1.0
                size: int = 10
            cfg = Config(size=20)
            def update(i):
                return cfg.speed * i + cfg.size
            FuncAnimation(fig, update)
            """))
        self.assertEqual(p["Config.speed"]["mode"], "live")
        self.assertEqual(p["Config.speed"]["live_targets"], [{"kind": "attr", "module": "main.py", "name": "cfg", "attr": "speed"}])
        self.assertEqual(p["Config.size"]["mode"], "restart")

    def test_entry_function_defaults_and_call_site(self):
        r = run('''
            def main(freq: float = 2.0, n: int = 100, method: str = "rk4", verbose=False):
                """Run it.

                Args:
                    freq: frequency in Hz
                    n (int): number of samples
                """
                pass

            if __name__ == "__main__":
                main(n=50)
            ''')
        p = by_id(r)
        self.assertEqual(r["entry"]["function"], "main")
        self.assertEqual(p["main.freq"]["default"], 2.0)
        self.assertEqual(p["main.freq"]["unit"], "Hz")
        self.assertEqual(p["main.n"]["default"], 50)            # call site wins
        self.assertEqual(p["main.n"]["help"], "number of samples")
        self.assertEqual(p["main.n"]["apply"]["span"][0], 12)   # rewrite the call argument
        self.assertEqual(p["main.verbose"]["type"], "bool")
        self.assertEqual(p["main.freq"]["mode"], "restart")

    def test_numpy_docstring(self):
        docs = D.parse_docstring_params("""Do it.

Parameters
----------
freq : float
    Frequency of the wave.
n : int
    Number of points.
""")
        self.assertEqual(docs["freq"], "Frequency of the wave.")
        self.assertEqual(docs["n"], "Number of points.")


class TestArgparse(unittest.TestCase):
    SRC = """
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("--freq", "-f", type=float, default=2.0, help="frequency in Hz (default %(default)s)")
        p.add_argument("--n-points", type=int, default=100)
        p.add_argument("--method", choices=["euler", "rk4"], default="rk4")
        p.add_argument("--grid", action="store_true", help="show grid")
        p.add_argument("--no-legend", dest="legend", action="store_false")
        p.add_argument("--size", type=float, nargs=2, default=[4.0, 3.0])
        p.add_argument("--tags", nargs="+")
        p.add_argument("--verbose", "-v", action="count", default=0)
        args = p.parse_args()
        """

    def setUp(self):
        self.r = run(self.SRC)
        self.p = by_id(self.r)

    def test_capture(self):
        p = self.p
        self.assertTrue(self.r["uses_argparse"])
        self.assertEqual(p["args.freq"]["type"], "float")
        self.assertEqual(p["args.freq"]["unit"], "Hz")
        self.assertIn("default 2.0", p["args.freq"]["help"])
        self.assertEqual(p["args.n_points"]["type"], "int")
        self.assertEqual(p["args.method"]["type"], "choice")
        self.assertEqual(p["args.grid"]["type"], "bool")
        self.assertIs(p["args.grid"]["default"], False)
        self.assertIs(p["args.legend"]["default"], True)
        self.assertEqual(p["args.size"]["type"], "tuple")
        self.assertNotIn("args.tags", p)
        self.assertNotIn("args.verbose", p)
        self.assertEqual(p["args.freq"]["mode"], "restart")

    def test_argv_rebuild(self):
        params = self.r["params"]
        self.assertEqual(D.build_argv(params, {}, "main.py"), ["main.py"])
        argv = D.build_argv(params, {
            "args.freq": -3.5, "args.method": "euler", "args.grid": True,
            "args.legend": False, "args.size": [5.0, 2.0], "args.n_points": 100,
        }, "main.py")
        self.assertEqual(argv, ["main.py", "--freq=-3.5", "--method=euler", "--grid", "--no-legend", "--size", "5.0", "2.0"])

    def test_argv_parses_back(self):
        import argparse
        ns = {}
        exec(compile(ast.parse(textwrap.dedent(self.SRC).replace("args = p.parse_args()", "")), "main.py", "exec"), ns)
        parser = ns["p"]
        argv = D.build_argv(self.r["params"], {"args.freq": -3.5, "args.grid": True, "args.size": [5, 2]}, "main.py")
        args = parser.parse_args(argv[1:])
        self.assertEqual(args.freq, -3.5)
        self.assertTrue(args.grid)
        self.assertEqual(args.size, [5.0, 2.0])
        self.assertIsInstance(argparse.Namespace(), argparse.Namespace)

    def test_live_namespace_attribute(self):
        p = by_id(run("""
            import argparse
            from matplotlib.animation import FuncAnimation
            parser = argparse.ArgumentParser()
            parser.add_argument("--speed", type=float, default=1.0)
            parser.add_argument("--n", type=int, default=10)
            args = parser.parse_args()
            data = list(range(args.n))
            def update(i):
                return args.speed * i
            FuncAnimation(fig, update)
            """))
        self.assertEqual(p["args.speed"]["mode"], "live")
        self.assertEqual(p["args.speed"]["live_targets"], [{"kind": "attr", "module": "main.py", "name": "args", "attr": "speed"}])
        self.assertEqual(p["args.n"]["mode"], "restart")


class TestHintsAndOverrides(unittest.TestCase):
    def test_hint_parsing(self):
        h = D.parse_hint('demo: range=0.5..8 step=0.5 label="Frequency" unit=Hz mode=live')
        self.assertEqual(h, {"min": 0.5, "max": 8, "step": 0.5, "label": "Frequency", "unit": "Hz", "mode": "live"})
        self.assertEqual(D.parse_hint("demo: ignore"), {"ignore": True})
        self.assertEqual(D.parse_hint("demo: choices=a,b,c log")["choices"], ["a", "b", "c"])
        self.assertEqual(D.parse_hint("demo: range=0..2pi")["max"], 2 * 3.141592653589793)
        self.assertEqual(D.parse_hint("just a comment"), {})
        self.assertIn("frobnicate", D.parse_hint("demo: frobnicate=3")["_unknown"])

    def test_hints_apply(self):
        p = by_id(run("""
            from matplotlib.animation import FuncAnimation
            FREQ = 2.0  # demo: range=0.5..8 step=0.5 label="Frequency" unit=Hz
            N = 10      # demo: mode=live group="Grid"
            H = 3       # demo: hide
            def update(i):
                return FREQ
            FuncAnimation(fig, update)
            """))
        self.assertEqual((p["FREQ"]["min"], p["FREQ"]["max"], p["FREQ"]["step"]), (0.5, 8, 0.5))
        self.assertEqual(p["FREQ"]["label"], "Frequency")
        self.assertEqual(p["FREQ"]["scale"], "linear")
        self.assertEqual(p["N"]["mode"], "live")
        self.assertEqual(p["N"]["forced_mode"], "live")
        self.assertEqual(p["N"]["group"], "Grid")
        self.assertTrue(p["H"]["hidden"])

    def test_manifest_overrides(self):
        r = run("""
            import numpy as np
            from matplotlib.animation import FuncAnimation
            FREQ = 2.0
            GAIN = 1.0
            H = 3       # demo: hide
            OMEGA = 2 * np.pi * FREQ
            def update(i):
                return FREQ * OMEGA * GAIN
            def reset():
                pass
            def needs_arg(x):
                pass
            FuncAnimation(fig, update)
            """, extra={"helpers.py": "G = 9.81\n"}, overrides={
                "parameter_overrides": {
                    "FREQ": {"label": "f", "min": 1, "max": 3},
                    "GAIN": {"mode": "restart"},
                    "H": {"hide": False},
                    "OMEGA": {"type": "float", "min": 0, "max": 20},
                    "helpers.py:G": {"unit": "m/s²"},
                    "MISSING": {"label": "x"},
                },
                "buttons": [{"label": "Reset", "call": "reset"}, {"call": "needs_arg"}, {"call": "nope"}],
            })
        p = by_id(r)
        self.assertEqual(p["FREQ"]["label"], "f")
        self.assertEqual((p["FREQ"]["min"], p["FREQ"]["max"], p["FREQ"]["scale"]), (1, 3, "linear"))
        self.assertEqual(p["FREQ"]["mode"], "restart")      # feeds OMEGA at start-up
        self.assertEqual(p["GAIN"]["mode"], "restart")
        self.assertIn("detected: live", p["GAIN"]["mode_reason"])
        self.assertFalse(p["H"]["hidden"])
        self.assertEqual(p["OMEGA"]["source"], "manifest")
        self.assertAlmostEqual(p["OMEGA"]["default"], 4 * 3.141592653589793)
        self.assertEqual(p["OMEGA"]["mode"], "live")
        self.assertEqual(p["helpers.py:G"]["unit"], "m/s²")
        self.assertTrue(any("MISSING" in n for n in r["notes"]))
        b = {x["call"]: x for x in r["buttons"]}
        self.assertTrue(b["reset"]["available"])
        self.assertFalse(b["needs_arg"]["available"])
        self.assertFalse(b["nope"]["available"])

    def test_demo_params_helper(self):
        r = run("""
            from matplotlib.animation import FuncAnimation
            try:
                from demo_params import params, on_change
            except ImportError:
                params = {"freq": 2.0, "n": 50}
                def on_change(name, cb):
                    pass
            data = list(range(params["n"]))
            def update(i):
                return params["freq"] * i
            FuncAnimation(fig, update)
            """)
        p = by_id(r)
        self.assertTrue(r["uses_demo_params"])
        self.assertEqual(p["params.freq"]["mode"], "live")
        self.assertEqual(p["params.freq"]["apply"], {"kind": "demo_params", "key": "freq"})
        self.assertEqual(p["params.n"]["mode"], "restart")
        self.assertNotIn("params", p)


class TestRewriteAndCoerce(unittest.TestCase):
    def test_rewrite_keeps_lines(self):
        src = "A = 1.0\nB = (1, 2)\nC = {'k': 3}\ndef f(x=4):\n    return x\nf(x=5)\n"
        r = run(src)
        p = by_id(r)
        reps = {
            tuple(p["A"]["apply"]["span"]): 2.5,
            tuple(p["B"]["apply"]["span"]): (7, 8),
        }
        tree, missing = D.rewrite_source(src, "main.py", reps)
        self.assertEqual(missing, [])
        ns = {}
        exec(compile(tree, "main.py", "exec"), ns)
        self.assertEqual(ns["A"], 2.5)
        self.assertEqual(ns["B"], (7, 8))
        # line numbers are unchanged
        self.assertEqual(tree.body[3].lineno, 4)

    def test_rewrite_reports_missing(self):
        _, missing = D.rewrite_source("A = 1\n", "main.py", {(9, 0, 9, 1): 3})
        self.assertEqual(missing, [(9, 0, 9, 1)])

    def test_literal_node_rejects_objects(self):
        with self.assertRaises(ValueError):
            D.literal_node(object())
        with self.assertRaises(ValueError):
            D.literal_node(float("nan"))

    def test_coerce(self):
        self.assertEqual(D.coerce_value({"id": "x", "type": "int"}, "4.6"), 5)
        self.assertEqual(D.coerce_value({"id": "x", "type": "float"}, "1e-3"), 0.001)
        self.assertIs(D.coerce_value({"id": "x", "type": "bool"}, "false"), False)
        self.assertEqual(D.coerce_value({"id": "x", "type": "choice", "choices": ["a", "b"]}, "b"), "b")
        self.assertEqual(D.coerce_value({"id": "x", "type": "tuple", "elem_type": "int", "length": 2}, "3, 4"), (3, 4))
        with self.assertRaises(ValueError):
            D.coerce_value({"id": "x", "type": "choice", "choices": ["a"]}, "z")
        with self.assertRaises(ValueError):
            D.coerce_value({"id": "x", "type": "float"}, "__import__('os')")
        with self.assertRaises(ValueError):
            D.coerce_value({"id": "x", "type": "float"}, "inf")


class TestRobustness(unittest.TestCase):
    def test_syntax_error_fails_soft(self):
        r = run("""
            from helpers import X
            A = 1.0
            """, extra={"helpers.py": "def broken(:\n"})
        self.assertIn("A", by_id(r))
        self.assertEqual(r["errors"][0]["file"], "helpers.py")

    def test_missing_main(self):
        r = D.detect({}, "main.py")
        self.assertEqual(r["params"], [])
        self.assertTrue(r["errors"])

    def test_package_relative_imports(self):
        r = by_id(run("""
            from matplotlib.animation import FuncAnimation
            from pkg import Plate
            plate = Plate()
            def update(i):
                plate.step()
            FuncAnimation(fig, update)
            """, extra={
                "pkg/__init__.py": "from .solver import Plate\n",
                "pkg/solver.py": "from .grid import make\nRATE = 0.2\nclass Plate:\n    def __init__(self):\n        self.u = make()\n    def step(self):\n        self.u *= RATE\n",
                "pkg/grid.py": "AMBIENT = 0.3\ndef make():\n    return AMBIENT\n",
            }))
        self.assertEqual(r["pkg/solver.py:RATE"]["mode"], "live")
        self.assertEqual(r["pkg/grid.py:AMBIENT"]["mode"], "restart")

    def test_speed(self):
        body = "\n".join(f"P{i} = {i}.5\ndef f{i}(x):\n    return x * P{i}\n" for i in range(300))
        t0 = time.perf_counter()
        r = run(body + "\nfrom matplotlib.animation import FuncAnimation\nFuncAnimation(fig, f1)\n")
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 1.0)
        self.assertGreater(len(r["params"]), 10)

    def test_examples_detect_cleanly(self):
        ex = os.path.join(ROOT, "quick-start", "content", "examples")
        if not os.path.isdir(ex):
            self.skipTest("examples not present")
        for name in sorted(os.listdir(ex)):
            folder = os.path.join(ex, name)
            if not os.path.isfile(os.path.join(folder, "main.py")):
                continue
            files = {}
            for dirpath, _, fnames in os.walk(folder):
                for f in fnames:
                    if f.endswith(".py"):
                        full = os.path.join(dirpath, f)
                        with open(full, encoding="utf-8") as fh:
                            files[os.path.relpath(full, folder).replace(os.sep, "/")] = fh.read()
            r = D.detect(files, "main.py")
            with self.subTest(example=name):
                self.assertEqual(r["errors"], [])
                self.assertTrue(r["params"], name)


class TestExamplesClassification(unittest.TestCase):
    """The behaviour the example demos rely on."""

    def load(self, name):
        folder = os.path.join(ROOT, "quick-start", "content", "examples", name)
        if not os.path.isdir(folder):
            self.skipTest("examples not present")
        files = {}
        for dirpath, _, fnames in os.walk(folder):
            for f in fnames:
                if f.endswith(".py"):
                    full = os.path.join(dirpath, f)
                    with open(full, encoding="utf-8") as fh:
                        files[os.path.relpath(full, folder).replace(os.sep, "/")] = fh.read()
        return by_id(D.detect(files, "main.py"))

    def test_oscillator(self):
        p = self.load("oscillator")
        for name in ("FREQ", "DAMPING", "SPEED", "physics.py:NONLINEARITY"):
            self.assertEqual(p[name]["mode"], "live", name)
        for name in ("TRAIL", "INTERVAL_MS"):
            self.assertEqual(p[name]["mode"], "restart", name)

    def test_heat_map(self):
        p = self.load("heat_map")
        self.assertEqual(p["GRID"]["mode"], "restart")
        self.assertEqual(p["SEED"]["mode"], "restart")
        self.assertEqual(p["DIFFUSIVITY"]["mode"], "live")
        self.assertEqual(p["COLORMAP"]["type"], "choice")

    def test_static_plot(self):
        p = self.load("static_plot")
        self.assertEqual(list(p), ["SHAPE"])
        self.assertEqual(p["SHAPE"]["choices"], ["sinc", "gaussian", "lorentzian", "sech2"])


if __name__ == "__main__":
    unittest.main()
