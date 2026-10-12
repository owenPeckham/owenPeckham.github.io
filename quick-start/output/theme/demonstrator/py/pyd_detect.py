"""pyd_detect -- static parameter detection for the Python demonstrator.

Finds the values a visitor might want to tweak in an *unmodified* Python
project and decides how a change should be applied:

* **live**    -- the value is read per frame (e.g. inside an animation
                 ``update()``), so the runtime writes the new value straight
                 into the running module's namespace;
* **restart** -- the value is consumed at start-up, so the runtime re-runs the
                 project with the new value injected (by rewriting the literal
                 in the AST at execution time, or by rebuilding ``sys.argv``).

Pure standard library (``ast`` and ``tokenize``); nothing is executed.  It runs
inside the Pyodide worker before the project is started, and on desktop
CPython for the unit tests (``tests/demonstrator/test_detect.py``).

Detection sources, in priority order
------------------------------------
1. ``argparse``: ``ArgumentParser.add_argument(...)`` calls.
2. Config structures: a top-level ``CONFIG``/``PARAMS``/``SETTINGS``-style dict,
   ``@dataclass`` / ``NamedTuple`` classes, and the keyword defaults of an
   entry function (``main``/``run``/``simulate``... or whatever is called under
   ``if __name__ == "__main__":``).
3. Top-level constants such as ``FREQ = 2.0`` (UPPER_CASE ranked highest;
   lower-case module literals go to an "Other detected values" group).
4. The optional ``demo_params`` helper (declared values override detection).

Overrides come from inline ``# demo: ...`` comment hints and the manifest's
``parameter_overrides`` (applied last, so they always win).

Public API
----------
``detect(files, main, overrides=None, options=None) -> dict``
``rewrite_source(source, filename, replacements) -> ast.Module``
``build_argv(params, values, main) -> list[str]``
``coerce_value(param, value)``
"""

from __future__ import annotations

import ast
import io
import math
import posixpath
import re
import shlex
import time
import tokenize

__all__ = [
    "detect",
    "rewrite_source",
    "build_argv",
    "coerce_value",
    "literal_node",
    "prettify",
    "infer_range",
    "parse_hint",
]

DETECT_VERSION = 1

# ---------------------------------------------------------------------------
# Name heuristics
# ---------------------------------------------------------------------------

UPPER_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
CONFIG_NAME_RE = re.compile(
    r"^(CONFIG|CONF|CFG|PARAMS|PARAMETERS|SETTINGS|OPTIONS|OPTS|DEFAULTS|SETUP)(_[A-Z0-9_]+)?$",
    re.I,
)
ENTRY_NAMES = (
    "main", "run", "simulate", "simulation", "run_simulation", "animate",
    "solve", "demo", "plot", "start", "app", "visualise", "visualize",
)
PATHLIKE_NAME_RE = re.compile(
    r"(^|_)(PATH|PATHS|FILE|FILES|FILENAME|FNAME|DIR|DIRS|FOLDER|URL|URI)$|^(PATH|FILE|DIR|OUTDIR|OUT_DIR|OUTPUT_DIR)_",
    re.I,
)
FILE_EXT_RE = re.compile(
    r"\.(csv|tsv|txt|dat|json|npy|npz|png|jpe?g|gif|svg|pdf|mp4|h5|hdf5|pkl|pickle|"
    r"xlsx?|ya?ml|toml|ini|log|py|md|wav|mp3|bin)$",
    re.I,
)
SEED_RE = re.compile(r"(^|_)(seed|random_state|rng_seed)($|_)", re.I)
COUNT_RE = re.compile(r"(^n$|^n_|_n$|num|count|steps|iters?|iterations|frames|particles|points|samples|size|grid|cells|bins|trail|history|order)", re.I)
RATIO_RE = re.compile(r"(?:^|[_\W])(ratio|fraction|frac|prob|probability|opacity|alpha|zeta|share|portion)(?:$|[_\W])", re.I)
ANGLE_DEG_RE = re.compile(r"(?:^|_)(deg|degree|degrees)(?:$|_)", re.I)
PERCENT_RE = re.compile(r"(?:^|_)(pct|percent|percentage)(?:$|_)", re.I)

# Unit suffixes recognised at the end of UPPER/lower snake names.
NAME_UNITS = {
    "HZ": "Hz", "KHZ": "kHz", "MHZ": "MHz",
    "S": "s", "SEC": "s", "SECS": "s", "SECONDS": "s", "MS": "ms", "US": "µs", "NS": "ns",
    "MIN": "min", "MINS": "min", "HOURS": "h",
    "M": "m", "MM": "mm", "CM": "cm", "KM": "km", "UM": "µm", "NM": "nm",
    "KG": "kg", "G": "g",
    "DEG": "°", "DEGREES": "°", "RAD": "rad",
    "PCT": "%", "PERCENT": "%",
    "PX": "px", "V": "V", "MV": "mV", "W": "W", "KW": "kW",
    "PA": "Pa", "KPA": "kPa", "MPA": "MPa", "BAR": "bar",
    "MPS": "m/s", "KMH": "km/h", "RPM": "rpm", "FPS": "fps",
}
# Units that are also common stand-alone names (``N``, ``G``, ``M``...): only
# treated as a unit when there is a preceding word, e.g. ``LENGTH_M``.
AMBIGUOUS_UNIT_SUFFIXES = {"M", "G", "S", "V", "W"}

ACRONYMS = {
    "fps", "rk4", "rk2", "cfl", "pid", "ode", "pde", "rgb", "hsv", "cpu", "gpu",
    "id", "ui", "snr", "rms", "fft", "dpi", "lr", "ai", "ml", "io", "sir", "seir",
}

COSMETIC_CALLS = {
    "print", "set_title", "title", "suptitle", "set_xlabel", "set_ylabel", "set_zlabel",
    "xlabel", "ylabel", "set_label", "text", "annotate", "legend", "set_text",
    "info", "debug", "warning", "warn", "log", "set_window_title", "figtext",
}

# Calls that hand a function to an event/animation system: functions passed
# to these are invoked later, per frame or per event.
CALLBACK_SINK_CALLS = {
    "FuncAnimation", "ArtistAnimation", "TimedAnimation",
    "mpl_connect", "add_callback", "on_changed", "on_clicked", "on_submit",
    "on_text_change", "on_change", "connect", "new_timer", "Timer", "Thread",
}
BLOCKING_CALLS = {"pause", "flush_events", "sleep", "start_event_loop"}

DEFAULT_OPTIONS = {
    "max_controls": 14,      # primary controls shown before the rest go to "More"
    "max_other": 8,          # lower-case module literals shown under "Other"
    "max_tuple": 4,          # longest tuple/list exposed as grouped inputs
    "max_str_len": 80,       # longer strings are treated as text, not parameters
}

SOURCE_ORDER = {
    "demo_params": 0, "manifest": 1, "argparse": 2, "config-dict": 3,
    "dataclass": 4, "namedtuple": 4, "function": 5, "constant": 6,
}
SOURCE_GROUP = {
    "argparse": "Command-line options",
    "function": "Function arguments",
    "demo_params": "Parameters",
    "constant": "Parameters",
    "manifest": "Parameters",
}


class DetectError(Exception):
    """A recoverable problem found while analysing one file."""


# ---------------------------------------------------------------------------
# Small helpers: comments, hints, literal evaluation, labels, ranges
# ---------------------------------------------------------------------------

def scan_comments(source):
    """Return ``{line: comment_text}`` (text after ``#``, stripped)."""
    comments = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                comments[tok.start[0]] = tok.string[1:].strip()
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Fall back to a crude scan; good enough for hints.
        for i, line in enumerate(source.splitlines(), 1):
            if "#" in line:
                comments.setdefault(i, line.split("#", 1)[1].strip())
    return comments


HINT_RE = re.compile(r"(?:^|\s|;)demo:\s*(.*)$", re.I)
HINT_FLAGS = {"ignore", "hide", "live", "restart", "log", "linear", "show"}
HINT_KEYS = {
    "range", "min", "max", "step", "label", "unit", "mode", "scale", "choices",
    "group", "help", "type", "default", "name", "order",
}


def parse_hint(comment):
    """Parse ``demo: range=0.5..8 step=0.5 label="Frequency" unit=Hz mode=live``.

    Returns a dict (empty when the comment carries no hint).  Unknown keys are
    reported under ``"_unknown"`` so the UI can warn about typos.
    """
    if not comment:
        return {}
    m = HINT_RE.search(comment)
    if not m:
        return {}
    body = m.group(1).strip()
    try:
        tokens = shlex.split(body, posix=True)
    except ValueError:
        tokens = body.split()
    hint = {}
    unknown = []
    for tok in tokens:
        if "=" in tok:
            key, _, val = tok.partition("=")
            key = key.strip().lower()
            if key not in HINT_KEYS:
                unknown.append(key)
                continue
            if key == "range":
                lo, sep, hi = val.partition("..")
                if not sep:
                    lo, sep, hi = val.partition(":")
                lo_v, hi_v = _num_or_none(lo), _num_or_none(hi)
                if lo_v is not None:
                    hint["min"] = lo_v
                if hi_v is not None:
                    hint["max"] = hi_v
            elif key in ("min", "max", "step", "default", "order"):
                v = _num_or_none(val)
                if v is not None:
                    hint[key] = v
                elif key == "default":
                    hint[key] = val
            elif key == "choices":
                sep = "|" if "|" in val else ","
                hint["choices"] = [c.strip() for c in val.split(sep) if c.strip()]
            elif key == "mode":
                v = val.strip().lower()
                if v in ("live", "restart"):
                    hint["mode"] = v
                else:
                    unknown.append("mode=" + val)
            elif key == "scale":
                v = val.strip().lower()
                if v in ("log", "linear"):
                    hint["scale"] = v
            else:
                hint[key] = val
        else:
            flag = tok.strip().lower()
            if flag in ("ignore", "skip"):
                hint["ignore"] = True
            elif flag == "hide":
                hint["hide"] = True
            elif flag == "show":
                hint["hide"] = False
            elif flag in ("live", "restart"):
                hint["mode"] = flag
            elif flag in ("log", "linear"):
                hint["scale"] = flag
            elif flag:
                unknown.append(flag)
    if unknown:
        hint["_unknown"] = unknown
    return hint


def strip_hint(comment):
    """Comment text with any ``demo: ...`` hint removed."""
    if not comment:
        return ""
    m = HINT_RE.search(comment)
    if m:
        comment = comment[: m.start()].rstrip(" ;,-")
    return comment.strip()


def _num_or_none(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text)
        v = float(text)
        if math.isfinite(v):
            return v
    except ValueError:
        pass
    m = re.fullmatch(r"([+-]?[\d.]+(?:e[+-]?\d+)?)\s*\*?\s*(pi|π)", text, re.I)
    if m:
        return float(m.group(1)) * math.pi
    if text.lower() in ("pi", "π"):
        return math.pi
    return None


_MATH_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}
_MATH_MODULES = {"math", "np", "numpy", "scipy", "constants"}


def safe_eval(node, env=None):
    """Evaluate a literal, or constant arithmetic on literals (``1/60``,
    ``2*np.pi``).  ``env`` optionally maps names to known numeric values
    (e.g. other constants of the module).  Raises ``ValueError`` otherwise.

    Returns ``(value, is_expression)``.
    """
    try:
        return ast.literal_eval(node), False
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        pass

    def ev(n, depth=0):
        if depth > 20:
            raise ValueError("too deep")
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = ev(n.operand, depth + 1)
            return v if isinstance(n.op, ast.UAdd) else -v
        if isinstance(n, ast.BinOp):
            left = ev(n.left, depth + 1)
            right = ev(n.right, depth + 1)
            op = n.op
            if isinstance(op, ast.Add):
                return left + right
            if isinstance(op, ast.Sub):
                return left - right
            if isinstance(op, ast.Mult):
                return left * right
            if isinstance(op, ast.Div):
                return left / right
            if isinstance(op, ast.FloorDiv):
                return left // right
            if isinstance(op, ast.Mod):
                return left % right
            if isinstance(op, ast.Pow):
                if abs(right) > 64 or abs(left) > 1e6:
                    raise ValueError("power too large")
                return left ** right
            raise ValueError("operator")
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            if n.value.id in _MATH_MODULES and n.attr in _MATH_CONSTANTS:
                return _MATH_CONSTANTS[n.attr]
        if isinstance(n, ast.Name) and env and n.id in env:
            v = env[n.id]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
        if isinstance(n, ast.Name) and n.id in ("pi", "tau"):
            return _MATH_CONSTANTS[n.id]
        raise ValueError("not a constant expression")

    try:
        v = ev(node)
    except (ArithmeticError, ValueError, TypeError):
        raise ValueError("not a literal") from None
    if isinstance(v, float) and not math.isfinite(v):
        raise ValueError("not finite")
    return v, True


def value_type(value, max_tuple=4):
    """Map a Python value to a control type, or ``None`` when not exposable.

    Returns ``(type, elem_type, length)``.
    """
    if isinstance(value, bool):
        return "bool", None, None
    if isinstance(value, int):
        return "int", None, None
    if isinstance(value, float):
        return ("float", None, None) if math.isfinite(value) else (None, None, None)
    if isinstance(value, str):
        return "str", None, None
    if isinstance(value, (tuple, list)) and 1 <= len(value) <= max_tuple:
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            elem = "int" if all(isinstance(v, int) for v in value) else "float"
            return "tuple", elem, len(value)
    return None, None, None


def annotation_info(node):
    """Interpret a type annotation.  Returns dict with optional
    ``type``, ``choices``, ``elem_type``, ``length``."""
    info = {}
    if node is None:
        return info
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return info
    name = _dotted_name(node)
    simple = name.split(".")[-1] if name else None
    if simple in ("float", "int", "bool", "str"):
        info["type"] = simple
        return info
    if isinstance(node, ast.Subscript):
        base = _dotted_name(node.value)
        base = base.split(".")[-1] if base else ""
        sl = node.slice
        elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
        if base == "Literal":
            choices = []
            for e in elts:
                if isinstance(e, ast.Constant):
                    choices.append(e.value)
            if choices:
                info["choices"] = choices
                if all(isinstance(c, str) for c in choices):
                    info["type"] = "choice"
                elif all(isinstance(c, bool) for c in choices):
                    info["type"] = "bool"
                elif all(isinstance(c, int) and not isinstance(c, bool) for c in choices):
                    info["type"] = "choice"
            return info
        if base in ("Optional",):
            return annotation_info(elts[0])
        if base in ("tuple", "Tuple", "list", "List", "Sequence"):
            inner = [annotation_info(e).get("type") for e in elts if not (isinstance(e, ast.Constant) and e.value is Ellipsis)]
            if inner and all(t in ("int", "float") for t in inner):
                info["type"] = "tuple"
                info["elem_type"] = "int" if all(t == "int" for t in inner) else "float"
                if base in ("tuple", "Tuple") and not any(isinstance(e, ast.Constant) and e.value is Ellipsis for e in elts):
                    info["length"] = len(inner)
            return info
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # ``float | None``
        left = annotation_info(node.left)
        return left if left else annotation_info(node.right)
    return info


def _dotted_name(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def split_name_unit(name):
    """``FREQ_HZ`` -> (``FREQ``, ``Hz``); ``LENGTH_M`` -> (``LENGTH``, ``m``)."""
    parts = re.split(r"_+", name.strip("_"))
    if len(parts) >= 2:
        last = parts[-1].upper()
        if last in NAME_UNITS:
            if last in AMBIGUOUS_UNIT_SUFFIXES and len(parts) < 2:
                return name, None
            return "_".join(parts[:-1]), NAME_UNITS[last]
    return name, None


def prettify(name):
    """``DAMPING_RATIO`` -> ``Damping ratio``; ``dampingRatio`` -> ``Damping ratio``;
    ``N`` -> ``N``; ``rk4_order`` -> ``RK4 order``."""
    raw = name.strip("_")
    if not raw:
        return name
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    if len(raw) <= 2:
        return raw                      # symbols: N, L, DT, dx
    # camelCase -> words
    raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw)
    words = [w for w in re.split(r"[_\s]+", raw) if w]
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in ACRONYMS:
            out.append(lw.upper())
        elif len(w) == 1 and w.isupper() and i == 0:
            out.append(w)
        else:
            out.append(lw)
    if out and out[0] and out[0][0].islower():
        out[0] = out[0][0].upper() + out[0][1:]
    return " ".join(out)


_UNIT_WORDS = {
    "seconds": "s", "second": "s", "minutes": "min", "degrees": "°", "deg": "°",
    "radians": "rad", "percent": "%", "pixels": "px", "metres": "m", "meters": "m",
    "millimetres": "mm", "millimeters": "mm", "hertz": "Hz",
}
_UNIT_RE = (r"Hz|kHz|MHz|s|ms|µs|us|seconds?|minutes|min|h|m|mm|cm|km|µm|nm|kg|g|°|deg|degrees|rad|radians|"
            r"%|percent|px|pixels|V|mV|W|kW|Pa|kPa|MPa|bar|K|N|m/s|km/h|rpm|fps|metres|meters|"
            r"millimetres|millimeters|hertz")


def label_from_comment(comment):
    """Split a trailing comment into (label, help, unit).

    ``natural frequency [Hz]``     -> ("Natural frequency", "natural frequency [Hz]", "Hz")
    ``signal frequency in Hz``     -> ("Signal frequency", ..., "Hz")
    ``damping ratio (0 = none)``   -> ("Damping ratio", "damping ratio (0 = none)", None)
    Long comments become help text only.
    """
    text = strip_hint(comment)
    if not text:
        return None, None, None
    unit = None
    head = text
    m = re.search(r"\[([^\]]{1,12})\]", head)
    if m:
        unit = m.group(1).strip()
        head = (head[: m.start()] + head[m.end():]).strip()
    # parenthetical asides are help text, not part of the label; a bare unit
    # in parentheses, e.g. "(Hz)", is a unit
    for paren in re.findall(r"\(([^)]*)\)", head):
        if unit is None and re.fullmatch(r"\s*(?:in\s+)?(" + _UNIT_RE + r")\s*", paren):
            unit = re.sub(r"^\s*in\s+", "", paren).strip()
    head = re.sub(r"\s*\([^)]*\)", "", head).strip()
    if unit is None:
        m3 = re.search(r"\s+(?:in|measured in)\s+(" + _UNIT_RE + r")\s*$", head)
        if m3:
            unit = m3.group(1)
            head = head[: m3.start()].strip()
    if unit:
        unit = _UNIT_WORDS.get(unit, unit)
    # "label: help", "label - help", "label; details", "label, details"
    main = re.split(r"\s+[-–—]\s+|:\s+|;\s*|,\s+(?=[a-z0-9])", head, maxsplit=1)[0].strip()
    label = None
    words = main.split()
    if 1 <= len(words) <= 4 and len(main) <= 32 and not main.endswith("?"):
        label = main[0].upper() + main[1:]
    # help only when it adds something beyond the label and unit
    bare = re.sub(r"\s*\[[^\]]*\]\s*$", "", text).strip()
    bare = re.sub(r"\s*\(\s*(?:in\s+)?(" + _UNIT_RE + r")\s*\)\s*$", "", bare).strip()
    bare = re.sub(r"\s+(?:in|measured in)\s+(" + _UNIT_RE + r")\s*$", "", bare).strip()
    help_text = text if (label is None or bare.lower() != main.strip().lower()) else None
    return label, help_text, unit


def choices_from_comment(comment):
    """``one of: sinc, gaussian, lorentzian`` -> list of choices."""
    text = strip_hint(comment)
    m = re.search(r"(?:one of|options?|choices?)\s*[:=]?\s*(.+)$", text, re.I)
    if not m:
        return None
    raw = m.group(1).strip().rstrip(".")
    sep = "|" if "|" in raw else ","
    items = [re.sub(r"^[\"']|[\"']$", "", c.strip()) for c in re.split(r"\s*(?:\||,|\bor\b)\s*", raw)]
    items = [i for i in items if i and re.fullmatch(r"[\w.+-]{1,32}", i)]
    return items if len(items) >= 2 else None


def nice_step(span, divisions=200):
    if not span or not math.isfinite(span) or span <= 0:
        return 0.01
    raw = span / divisions
    exp = math.floor(math.log10(raw))
    base = raw / 10 ** exp
    for nice in (1, 2, 2.5, 5, 10):
        if base <= nice:
            return float(round(nice * 10 ** exp, 12))
    return float(10 ** (exp + 1))


def infer_range(name, typ, value, unit=None, context=None):
    """Sensible slider bounds for a default value (``context`` is the label or
    help text, used for hints such as "ratio").

    * positive floats: 0.1x .. 10x on a log scale (ratios: 0..1, angles: 0..360)
    * zero: -1 .. 1
    * negative floats: symmetric linear range
    * ints: small values linear from 0/1, large values log-ish 0.1x .. 10x
    * seeds: 0 .. 1000
    The visitor can always type a value outside the range.
    """
    lname = name.lower()
    if typ == "int":
        v = int(value)
        if SEED_RE.search(lname):
            return {"min": 0, "max": max(1000, v * 2), "step": 1, "scale": "linear"}
        countlike = bool(COUNT_RE.search(lname))
        if v == 0:
            return {"min": 0 if countlike else -10, "max": 10, "step": 1, "scale": "linear"}
        if v > 0:
            if v <= 20:
                lo = 1 if (countlike and v >= 1) else 0
                return {"min": lo, "max": max(10, 3 * v), "step": 1, "scale": "linear"}
            return {"min": max(1, round(v / 10)), "max": v * 10, "step": 1, "scale": "log"}
        span = max(10, abs(v) * 10)
        return {"min": -span, "max": span, "step": 1, "scale": "linear"}
    if typ == "float":
        v = float(value)
        if v == 0.0:
            return {"min": -1.0, "max": 1.0, "step": 0.01, "scale": "linear"}
        if v > 0:
            if (unit in ("°", "deg") or ANGLE_DEG_RE.search(lname)) and v <= 360:
                return {"min": 0.0, "max": 360.0, "step": 1.0, "scale": "linear"}
            if (unit == "%" or PERCENT_RE.search(lname)) and v <= 100:
                return {"min": 0.0, "max": 100.0, "step": 0.5, "scale": "linear"}
            if (RATIO_RE.search(lname) or (context and RATIO_RE.search(context))) and v <= 1:
                return {"min": 0.0, "max": 1.0, "step": 0.01, "scale": "linear"}
            lo, hi = v / 10.0, v * 10.0
            return {"min": _round_sig(lo), "max": _round_sig(hi), "step": None, "scale": "log"}
        span = abs(v) * 10.0
        return {"min": _round_sig(-span), "max": _round_sig(span), "step": nice_step(2 * span), "scale": "linear"}
    return None


def _scale_for_range(lo, hi, explicit_step=False):
    """Log scale only for positive ranges spanning two or more decades."""
    if explicit_step or lo is None or hi is None or lo <= 0 or hi <= 0:
        return "linear"
    return "log" if hi / lo >= 100 else "linear"


def _round_sig(x, sig=3):
    if x == 0 or not math.isfinite(x):
        return x
    return round(x, sig - 1 - int(math.floor(math.log10(abs(x)))))


def json_safe(value):
    """Make a detected value JSON-friendly (tuples -> lists)."""
    if isinstance(value, (tuple, list)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


# ---------------------------------------------------------------------------
# Scope analysis
# ---------------------------------------------------------------------------

class Scope:
    """A Python name scope (module, class, function, lambda or comprehension)."""

    __slots__ = ("kind", "node", "parent", "bound", "global_decl", "nonlocal_decl", "func")

    def __init__(self, kind, node, parent, func=None):
        self.kind = kind
        self.node = node
        self.parent = parent
        self.bound = set()
        self.global_decl = set()
        self.nonlocal_decl = set()
        self.func = func            # FuncInfo owning this scope (functions/lambdas)

    def resolves_global(self, name):
        """Would a *read* of ``name`` in this scope look up the module global?"""
        s = self
        if s.kind == "module":
            return True
        if name in s.global_decl:
            return True
        if name in s.nonlocal_decl or name in s.bound:
            return False
        p = s.parent
        while p is not None:
            if p.kind == "module":
                return True
            if p.kind == "class":
                p = p.parent
                continue
            if name in p.global_decl:
                return True
            if name in p.bound or name in p.nonlocal_decl:
                return False
            p = p.parent
        return True

    def find_binding_scope(self, name):
        """Innermost enclosing function scope that binds ``name`` (closures)."""
        s = self
        while s is not None and s.kind != "module":
            if s.kind != "class" and name in s.bound and name not in s.global_decl:
                return s
            s = s.parent
        return None

    def owner(self):
        """The function scope executing this code (``None`` = module level)."""
        s = self
        while s is not None:
            if s.kind in ("function", "lambda"):
                return s
            if s.kind == "module":
                return None
            if s.kind == "class":
                # class bodies run when the class statement runs
                s = s.parent
                continue
            s = s.parent
        return None


class FuncInfo:
    """What one function (or method/lambda) reads, writes and calls."""

    def __init__(self, module, qualname, node, scope, class_name=None):
        self.module = module
        self.qualname = qualname
        self.name = qualname.rsplit(".", 1)[-1]
        self.node = node
        self.scope = scope
        self.class_name = class_name
        self.is_method = class_name is not None
        self.reads = []          # Read records whose owner is this function
        self.calls = []          # (callee descriptor, in_blocking_loop)
        self.global_writes = set()
        self.blocking = False    # directly calls pause/sleep/flush_events
        self.parent_func = None

    @property
    def key(self):
        return (self.module.rel, self.qualname)

    def __repr__(self):
        return f"<FuncInfo {self.module.rel}:{self.qualname}>"


class Read:
    """One read of a module-global name."""

    __slots__ = ("name", "node", "kind", "attr", "key", "owner", "context", "in_loop", "line")

    def __init__(self, name, node, kind, owner, context, in_loop, attr=None, key=None):
        self.name = name
        self.node = node
        self.kind = kind          # "name" | "attr" | "item" | "call"
        self.attr = attr
        self.key = key
        self.owner = owner        # FuncInfo or None (module level)
        self.context = context    # "cosmetic" | "derive" | "default" | "other"
        self.in_loop = in_loop    # inside a blocking (pause/sleep) loop
        self.line = getattr(node, "lineno", 0)


class ModuleInfo:
    def __init__(self, rel, source, modname, is_main):
        self.rel = rel
        self.source = source
        self.modname = modname
        self.is_main = is_main
        self.is_package = rel.endswith("__init__.py")
        self.package = modname if self.is_package else (modname.rpartition(".")[0])
        self.tree = None
        self.comments = {}
        self.lines = source.splitlines()
        self.module_scope = None
        self.functions = {}       # qualname -> FuncInfo
        self.func_by_node = {}
        self.classes = {}         # name -> ast.ClassDef
        self.class_methods = {}   # class name -> {method name: FuncInfo}
        self.bindings = {}        # module-level name -> list of (node, kind)
        self.imports = {}         # local name -> ("module", modname) | ("from", modname, attr)
        self.star_imports = []
        self.instances = {}       # global name -> class name (x = Class(...))
        self.reads = []           # all Read records (all owners)
        self.module_calls = []    # (callee, in_loop) calls made at module level
        self.callback_refs = []   # (ref descriptor, kind, owner, in_loop)
        self.blocking_loops = []  # (node, owner)
        self.main_guard_calls = []  # calls under ``if __name__ == "__main__":``
        self.state_names = set()  # globals rebound inside functions
        self.parser_vars = set()  # names bound to ArgumentParser instances
        self.namespace_vars = set()  # names bound to parse_args() results
        self.error = None


class Analyzer(ast.NodeVisitor):
    """Two-pass analysis of one module.

    Pass 1 (``bind``) builds scopes and their bindings; pass 2 (``collect``)
    records reads, calls and callback references, resolving names with the
    finished scopes.
    """

    def __init__(self, mod):
        self.mod = mod
        self.scope_of = {}   # id(node) -> Scope
        self.pass_ = 1
        self.scope = None
        self.func_stack = []
        self.class_stack = []
        self.loop_stack = []        # blocking loop flags
        self.call_stack = []        # enclosing calls: (callee simple name, is_sink)
        self.cosmetic_depth = 0
        self.derive_depth = 0
        self.default_depth = 0
        self.main_guard = 0

    # -- entry -------------------------------------------------------------
    def run(self):
        mod = self.mod
        self.pass_ = 1
        mod.module_scope = Scope("module", mod.tree, None)
        self.scope = mod.module_scope
        self.scope_of[id(mod.tree)] = mod.module_scope
        for stmt in mod.tree.body:
            self.visit(stmt)
        # pass 2
        self.pass_ = 2
        self.scope = mod.module_scope
        self.func_stack = []
        self.class_stack = []
        for stmt in mod.tree.body:
            self.visit(stmt)

    # -- helpers -------------------------------------------------------------
    def _bind(self, name, node=None, kind="assign"):
        if self.pass_ != 1:
            return
        self.scope.bound.add(name)
        if self.scope.kind == "module":
            self.mod.bindings.setdefault(name, []).append((node, kind))

    def _bind_target(self, target, kind="assign"):
        if isinstance(target, ast.Name):
            self._bind(target.id, target, kind)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind_target(elt, "unpack")
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value, "unpack")
        # attribute/subscript targets do not bind names

    def _owner(self):
        return self.func_stack[-1] if self.func_stack else None

    def _context(self):
        if self.cosmetic_depth:
            return "cosmetic"
        if self.default_depth:
            return "default"
        if self.derive_depth:
            return "derive"
        return "other"

    def _in_loop(self):
        return bool(self.loop_stack) and any(self.loop_stack)

    def _record_read(self, name, node, kind, attr=None, key=None):
        if self.pass_ != 2:
            return
        if not self.scope.resolves_global(name):
            return
        r = Read(name, node, kind, self._owner(), self._context(), self._in_loop(), attr, key)
        self.mod.reads.append(r)
        if r.owner is not None:
            r.owner.reads.append(r)

    # -- scopes ----------------------------------------------------------------
    def _visit_args_defaults(self, args):
        self.default_depth += 1
        for d in list(args.defaults) + [d for d in args.kw_defaults if d is not None]:
            self.visit(d)
        self.default_depth -= 1
        for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs) + [args.vararg, args.kwarg]:
            if a is not None and a.annotation is not None:
                self.visit(a.annotation)

    def _bind_args(self, args):
        for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs) + [args.vararg, args.kwarg]:
            if a is not None:
                self._bind(a.arg, a, "param")

    def visit_FunctionDef(self, node):
        for d in node.decorator_list:
            self.visit(d)
        self._visit_args_defaults(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        self._bind(node.name, node, "def")
        if self.pass_ == 1:
            class_name = self.class_stack[-1] if (self.scope.kind == "class" and self.class_stack) else None
            prefix = ".".join(f.qualname for f in self.func_stack[-1:]) if self.func_stack else ""
            if class_name:
                qual = f"{self._class_qual()}.{node.name}"
            elif prefix:
                qual = f"{prefix}.{node.name}"
            else:
                qual = node.name
            scope = Scope("function", node, self.scope)
            info = FuncInfo(self.mod, qual, node, scope, class_name=class_name if self.scope.kind == "class" else None)
            info.parent_func = self._owner()
            scope.func = info
            self.scope_of[id(node)] = scope
            self.mod.functions[qual] = info
            self.mod.func_by_node[id(node)] = info
            if class_name and self.scope.kind == "class":
                self.mod.class_methods.setdefault(class_name, {})[node.name] = info
        scope = self.scope_of[id(node)]
        outer = self.scope
        self.scope = scope
        self.func_stack.append(scope.func)
        saved = (self.loop_stack, self.cosmetic_depth, self.derive_depth, self.default_depth, self.call_stack)
        self.loop_stack, self.cosmetic_depth, self.derive_depth, self.default_depth, self.call_stack = [], 0, 0, 0, []
        self._bind_args(node.args)
        for stmt in node.body:
            self.visit(stmt)
        self.loop_stack, self.cosmetic_depth, self.derive_depth, self.default_depth, self.call_stack = saved
        self.func_stack.pop()
        self.scope = outer

    visit_AsyncFunctionDef = visit_FunctionDef

    def _class_qual(self):
        return ".".join(self.class_stack)

    def visit_Lambda(self, node):
        self._visit_args_defaults(node.args)
        if self.pass_ == 1:
            scope = Scope("lambda", node, self.scope)
            owner = self._owner()
            qual = f"{owner.qualname}.<lambda@{node.lineno}:{node.col_offset}>" if owner else f"<lambda@{node.lineno}:{node.col_offset}>"
            info = FuncInfo(self.mod, qual, node, scope)
            info.parent_func = owner
            scope.func = info
            self.scope_of[id(node)] = scope
            self.mod.functions[qual] = info
            self.mod.func_by_node[id(node)] = info
        scope = self.scope_of[id(node)]
        if self.pass_ == 2:
            # a lambda is always a function *value*: passed, stored or returned
            self._maybe_callback_ref(("lambda", scope.func.qualname), node)
        outer = self.scope
        self.scope = scope
        self.func_stack.append(scope.func)
        saved = (self.loop_stack, self.cosmetic_depth, self.derive_depth, self.default_depth, self.call_stack)
        self.loop_stack, self.cosmetic_depth, self.derive_depth, self.default_depth, self.call_stack = [], 0, 0, 0, []
        self._bind_args(node.args)
        self.visit(node.body)
        self.loop_stack, self.cosmetic_depth, self.derive_depth, self.default_depth, self.call_stack = saved
        self.func_stack.pop()
        self.scope = outer

    def visit_ClassDef(self, node):
        for d in node.decorator_list:
            self.visit(d)
        for b in node.bases:
            self.visit(b)
        for k in node.keywords:
            self.visit(k.value)
        self._bind(node.name, node, "class")
        if self.pass_ == 1:
            scope = Scope("class", node, self.scope)
            self.scope_of[id(node)] = scope
            if self.scope.kind == "module":
                self.mod.classes[node.name] = node
        scope = self.scope_of[id(node)]
        outer = self.scope
        self.scope = scope
        self.class_stack.append(node.name)
        for stmt in node.body:
            self.visit(stmt)
        self.class_stack.pop()
        self.scope = outer

    def _visit_comprehension(self, node, elts):
        gens = node.generators
        # The first iterable is evaluated in the enclosing scope.
        self.visit(gens[0].iter)
        if self.pass_ == 1:
            scope = Scope("comprehension", node, self.scope)
            self.scope_of[id(node)] = scope
        scope = self.scope_of[id(node)]
        outer = self.scope
        self.scope = scope
        for i, g in enumerate(gens):
            if self.pass_ == 1:
                self._bind_comp_target(g.target, scope)
            if i > 0:
                self.visit(g.iter)
            for cond in g.ifs:
                self.visit(cond)
        for e in elts:
            self.visit(e)
        self.scope = outer

    def _bind_comp_target(self, target, scope):
        if isinstance(target, ast.Name):
            scope.bound.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for e in target.elts:
                self._bind_comp_target(e, scope)
        elif isinstance(target, ast.Starred):
            self._bind_comp_target(target.value, scope)

    def visit_ListComp(self, node):
        self._visit_comprehension(node, [node.elt])

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node):
        self._visit_comprehension(node, [node.key, node.value])

    def visit_NamedExpr(self, node):
        self.visit(node.value)
        if self.pass_ == 1:
            s = self.scope
            while s.kind == "comprehension":
                s = s.parent
            s.bound.add(node.target.id)
            if s.kind == "module":
                self.mod.bindings.setdefault(node.target.id, []).append((node.target, "walrus"))

    # -- declarations / bindings ---------------------------------------------
    def visit_Global(self, node):
        if self.pass_ == 1:
            self.scope.global_decl.update(node.names)

    def visit_Nonlocal(self, node):
        if self.pass_ == 1:
            self.scope.nonlocal_decl.update(node.names)

    def visit_Import(self, node):
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self._bind(local, node, "import")
            if self.pass_ == 1 and self.scope.kind == "module":
                target = alias.name if alias.asname else alias.name.split(".")[0]
                self.mod.imports[local] = ("module", target, alias.name)

    def visit_ImportFrom(self, node):
        base = resolve_relative(self.mod, node.module, node.level)
        for alias in node.names:
            if alias.name == "*":
                if self.pass_ == 1 and self.scope.kind == "module":
                    self.mod.star_imports.append(base)
                continue
            local = alias.asname or alias.name
            self._bind(local, node, "import")
            if self.pass_ == 1 and self.scope.kind == "module":
                self.mod.imports[local] = ("from", base, alias.name)

    def visit_ExceptHandler(self, node):
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._bind(node.name, node, "except")
        for stmt in node.body:
            self.visit(stmt)

    def visit_Assign(self, node):
        self.derive_depth += 1
        self.visit(node.value)
        self.derive_depth -= 1
        for t in node.targets:
            self._bind_target(t)
            self._visit_target_reads(t)
        if self.pass_ == 1:
            self._note_assign_value(node.targets, node.value)

    def visit_AnnAssign(self, node):
        self.visit(node.annotation)
        if node.value is not None:
            self.derive_depth += 1
            self.visit(node.value)
            self.derive_depth -= 1
            if self.pass_ == 1:
                self._note_assign_value([node.target], node.value)
        if isinstance(node.target, ast.Name):
            if node.value is not None or self.scope.kind != "module":
                self._bind(node.target.id, node.target, "annassign")
        else:
            self._visit_target_reads(node.target)

    def visit_AugAssign(self, node):
        self.derive_depth += 1
        self.visit(node.value)
        self.derive_depth -= 1
        if isinstance(node.target, ast.Name):
            # ``x += 1`` both reads and rebinds x
            self._record_read(node.target.id, node.target, "name")
            self._bind(node.target.id, node.target, "augassign")
        else:
            self._visit_target_reads(node.target)

    def visit_Delete(self, node):
        for t in node.targets:
            if isinstance(t, ast.Name):
                self._bind(t.id, t, "del")
            else:
                self._visit_target_reads(t)

    def _visit_target_reads(self, target):
        """Attribute/subscript targets read their base object."""
        if isinstance(target, (ast.Attribute, ast.Subscript)):
            self.visit(target.value)
            if isinstance(target, ast.Subscript):
                self.visit(target.slice)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for e in target.elts:
                self._visit_target_reads(e)
        elif isinstance(target, ast.Starred):
            self._visit_target_reads(target.value)

    def _note_assign_value(self, targets, value):
        """Track parser objects, parse_args() namespaces and class instances."""
        if self.scope.kind != "module" and not self.func_stack:
            return
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not names or not isinstance(value, ast.Call):
            return
        callee = _dotted_name(value.func) or ""
        simple = callee.split(".")[-1]
        if simple == "ArgumentParser":
            self.mod.parser_vars.update(names)
        elif simple in ("parse_args", "parse_known_args", "parse_intermixed_args"):
            if self.scope.kind == "module":
                self.mod.namespace_vars.update(names)
        elif self.scope.kind == "module" and simple and simple[0].isupper():
            for n in names:
                self.mod.instances[n] = callee

    def visit_For(self, node):
        self.visit(node.iter)
        self._bind_target(node.target, "for")
        blocking = self.pass_ == 2 and _contains_blocking_call(node)
        if blocking and self.pass_ == 2:
            self.mod.blocking_loops.append((node, self._owner()))
        self.loop_stack.append(blocking)
        for stmt in node.body:
            self.visit(stmt)
        self.loop_stack.pop()
        for stmt in node.orelse:
            self.visit(stmt)

    visit_AsyncFor = visit_For

    def visit_While(self, node):
        blocking = self.pass_ == 2 and _contains_blocking_call(node)
        if blocking:
            self.mod.blocking_loops.append((node, self._owner()))
        self.loop_stack.append(blocking)
        self.visit(node.test)
        for stmt in node.body:
            self.visit(stmt)
        self.loop_stack.pop()
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars, "with")
                self._visit_target_reads(item.optional_vars)
        for stmt in node.body:
            self.visit(stmt)

    visit_AsyncWith = visit_With

    def visit_If(self, node):
        guard = _is_main_guard(node.test)
        self.visit(node.test)
        if guard:
            self.main_guard += 1
        for stmt in node.body:
            self.visit(stmt)
        if guard:
            self.main_guard -= 1
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_Match(self, node):
        self.visit(node.subject)
        for case in node.cases:
            self.visit(case.pattern)
            if case.guard is not None:
                self.visit(case.guard)
            for stmt in case.body:
                self.visit(stmt)

    def visit_MatchAs(self, node):
        if node.pattern is not None:
            self.visit(node.pattern)
        if node.name:
            self._bind(node.name, node, "match")

    def visit_MatchStar(self, node):
        if node.name:
            self._bind(node.name, node, "match")

    def visit_MatchMapping(self, node):
        for k in node.keys:
            self.visit(k)
        for p in node.patterns:
            self.visit(p)
        if node.rest:
            self._bind(node.rest, node, "match")

    # -- reads -------------------------------------------------------------------
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self._record_read(node.id, node, "name")
            if self.pass_ == 2:
                self._maybe_callback_ref(("name", node.id), node)
        elif isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(node.id, node, "assign")

    def visit_Attribute(self, node):
        if isinstance(node.ctx, ast.Load) and isinstance(node.value, ast.Name):
            base = node.value.id
            if self.pass_ == 2 and self.scope.resolves_global(base):
                r = Read(base, node, "attr", self._owner(), self._context(), self._in_loop(), attr=node.attr)
                self.mod.reads.append(r)
                if r.owner is not None:
                    r.owner.reads.append(r)
                self._maybe_callback_ref(("attr", base, node.attr), node)
            elif self.pass_ == 2 and base == "self":
                self._maybe_callback_ref(("self", node.attr), node)
            return
        self.generic_visit(node)

    def visit_Subscript(self, node):
        if isinstance(node.ctx, ast.Load) and isinstance(node.value, ast.Name):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, (str, int)):
                base = node.value.id
                if self.pass_ == 2 and self.scope.resolves_global(base):
                    r = Read(base, node, "item", self._owner(), self._context(), self._in_loop(), key=key.value)
                    self.mod.reads.append(r)
                    if r.owner is not None:
                        r.owner.reads.append(r)
                return
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        callee = self._callee_descriptor(func)
        simple = callee[-1] if callee else None
        is_sink = simple in CALLBACK_SINK_CALLS
        is_cosmetic = simple in COSMETIC_CALLS
        if self.pass_ == 2 and callee:
            owner = self._owner()
            if owner is not None:
                owner.calls.append((callee, self._in_loop()))
                if simple in BLOCKING_CALLS:
                    owner.blocking = True
            else:
                self.mod.module_calls.append((callee, self._in_loop()))
            if self.main_guard and owner is None:
                self.mod.main_guard_calls.append(node)
        # ``CONFIG.get("key", default)`` is an item read of CONFIG.
        if (self.pass_ == 2 and isinstance(func, ast.Attribute) and func.attr == "get"
                and isinstance(func.value, ast.Name) and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                and self.scope.resolves_global(func.value.id)):
            r = Read(func.value.id, node, "item", self._owner(), self._context(), self._in_loop(), key=node.args[0].value)
            self.mod.reads.append(r)
            if r.owner is not None:
                r.owner.reads.append(r)
        # visit the callee expression (records reads of the function name)
        self.call_stack.append(("func", simple, is_sink))
        if isinstance(func, ast.Name):
            # a call is a read of the function name, but not a callback reference
            self._record_read(func.id, func, "call")
        elif isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                base = func.value.id
                if self.pass_ == 2 and self.scope.resolves_global(base):
                    r = Read(base, func, "attr", self._owner(), self._context(), self._in_loop(), attr=func.attr)
                    self.mod.reads.append(r)
                    if r.owner is not None:
                        r.owner.reads.append(r)
            else:
                self.visit(func.value)
        else:
            self.visit(func)
        self.call_stack.pop()
        if is_cosmetic:
            self.cosmetic_depth += 1
        self.call_stack.append(("arg", simple, is_sink))
        for a in node.args:
            self.visit(a)
        for k in node.keywords:
            self.visit(k.value)
        self.call_stack.pop()
        if is_cosmetic:
            self.cosmetic_depth -= 1

    def _callee_descriptor(self, func):
        if isinstance(func, ast.Name):
            return ("name", func.id)
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                if func.value.id == "self":
                    return ("self", func.attr)
                return ("attr", func.value.id, func.attr)
            inner = _dotted_name(func.value)
            if inner:
                return ("dotted", inner, func.attr)
            return ("method", func.attr)
        return None

    def _maybe_callback_ref(self, ref, node):
        """A function referenced as a value (not called)."""
        if self.call_stack and self.call_stack[-1][0] == "func":
            return
        if self.call_stack and self.call_stack[-1][0] == "arg":
            kind = "sink" if self.call_stack[-1][2] else "arg"
        else:
            kind = "store"
        self.mod.callback_refs.append((ref, kind, self._owner(), self._in_loop()))


def _contains_blocking_call(loop):
    for node in ast.walk(loop):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)) and node is not loop:
            continue
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            if name in BLOCKING_CALLS:
                return True
    return False


def _is_main_guard(test):
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
        sides = [test.left, test.comparators[0]]
        names = [s for s in sides if isinstance(s, ast.Name) and s.id == "__name__"]
        consts = [s for s in sides if isinstance(s, ast.Constant) and s.value == "__main__"]
        return bool(names and consts)
    return False


def resolve_relative(mod, module, level):
    if not level:
        return module or ""
    pkg_parts = mod.package.split(".") if mod.package else []
    if level - 1 > len(pkg_parts):
        return module or ""
    base_parts = pkg_parts[: len(pkg_parts) - (level - 1)] if level > 1 else pkg_parts
    base = ".".join(p for p in base_parts if p)
    if module:
        return f"{base}.{module}" if base else module
    return base


def modname_for(rel, main_dir=""):
    path = rel[:-3] if rel.endswith(".py") else rel
    if main_dir and path.startswith(main_dir + "/"):
        path = path[len(main_dir) + 1:]
    parts = [p for p in path.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Parameter record
# ---------------------------------------------------------------------------

class Param:
    """A detected parameter.  Serialised with :meth:`to_dict`."""

    def __init__(self, pid, name, file, line, source, value, *, type_=None, node=None):
        self.id = pid
        self.name = name
        self.file = file
        self.line = line
        self.end_line = line
        self.source = source
        self.default = value
        self.type = type_
        self.elem_type = None
        self.length = None
        self.choices = None
        self.label = None
        self.help = None
        self.unit = None
        self.group = None
        self.min = None
        self.max = None
        self.step = None
        self.scale = None
        self.mode = "restart"
        self.mode_reason = ""
        self.live_via = []
        self.tier = "primary"
        self.rank = 0
        self.hidden = False
        self.apply = {"kind": "none"}
        self.live_targets = []
        self.hint = {}
        self.expr = False
        self.notes = []
        self.node = node              # value node (not serialised)
        self.order = None
        self.forced_mode = None

    def to_dict(self):
        d = {
            "id": self.id, "name": self.name, "file": self.file, "line": self.line,
            "end_line": self.end_line, "source": self.source, "type": self.type,
            "default": json_safe(self.default), "label": self.label, "help": self.help,
            "unit": self.unit, "group": self.group, "min": self.min, "max": self.max,
            "step": self.step, "scale": self.scale, "mode": self.mode,
            "mode_reason": self.mode_reason, "live_via": self.live_via,
            "tier": self.tier, "rank": self.rank, "hidden": self.hidden,
            "apply": self.apply, "live_targets": self.live_targets, "expr": self.expr,
            "notes": self.notes,
        }
        if self.choices is not None:
            d["choices"] = json_safe(self.choices)
        if self.type == "tuple":
            d["elem_type"] = self.elem_type
            d["length"] = self.length
        if self.hint:
            d["hint"] = {k: json_safe(v) for k, v in self.hint.items()}
        if self.forced_mode:
            d["forced_mode"] = self.forced_mode
        return d


def _span(node):
    return [node.lineno, node.col_offset, getattr(node, "end_lineno", node.lineno), getattr(node, "end_col_offset", node.col_offset)]


# ---------------------------------------------------------------------------
# Project analysis
# ---------------------------------------------------------------------------

class Project:
    def __init__(self, files, main, options):
        self.files = files
        self.main = main
        self.options = options
        self.main_dir = posixpath.dirname(main)
        self.modules = {}          # rel -> ModuleInfo
        self.by_modname = {}       # modname -> ModuleInfo
        self.errors = []
        self.skipped = []
        self.derived = []
        self.notes = []

    # -- loading -----------------------------------------------------------------
    def load(self):
        rels = [self.main] + sorted(r for r in self.files if r != self.main and r.endswith(".py"))
        for rel in rels:
            src = self.files.get(rel)
            if src is None:
                continue
            if isinstance(src, bytes):
                try:
                    src = src.decode("utf-8")
                except UnicodeDecodeError:
                    self.errors.append({"file": rel, "line": 0, "message": "file is not UTF-8 text"})
                    continue
            in_main_dir = (not self.main_dir) or rel.startswith(self.main_dir + "/")
            modname = modname_for(rel, self.main_dir if in_main_dir else "")
            mod = ModuleInfo(rel, src, "__main__" if rel == self.main else modname, rel == self.main)
            try:
                mod.tree = ast.parse(src, filename=rel, type_comments=False)
            except SyntaxError as exc:
                mod.error = exc
                self.errors.append({"file": rel, "line": exc.lineno or 0, "message": f"SyntaxError: {exc.msg}"})
                continue
            mod.comments = scan_comments(src)
            try:
                Analyzer(mod).run()
            except RecursionError:
                self.errors.append({"file": rel, "line": 0, "message": "file too deeply nested to analyse"})
                continue
            except Exception as exc:  # pragma: no cover - defensive
                self.errors.append({"file": rel, "line": 0, "message": f"analysis failed: {exc!r}"})
                continue
            self.modules[rel] = mod
            self.by_modname[modname] = mod
            if rel == self.main:
                self.by_modname.setdefault(modname, mod)
        for mod in self.modules.values():
            self._find_state_names(mod)

    def _find_state_names(self, mod):
        for f in mod.functions.values():
            for name in f.scope.global_decl:
                if name in f.scope.bound or _assigns_name(f.node, name):
                    mod.state_names.add(name)
                    f.global_writes.add(name)

    # -- name resolution across modules ---------------------------------------
    def module_for(self, modname):
        if not modname:
            return None
        return self.by_modname.get(modname)

    def resolve_global(self, mod, name):
        """What a module-level name refers to.

        Returns ``("func", FuncInfo)``, ``("class", mod, name)``,
        ``("module", ModuleInfo)``, ``("var", mod, name)`` or ``None``.
        """
        seen = 0
        while mod is not None and seen < 8:
            seen += 1
            if name in mod.functions and "." not in name:
                return ("func", mod.functions[name])
            if name in mod.classes:
                return ("class", mod, name)
            imp = mod.imports.get(name)
            if imp:
                if imp[0] == "module":
                    target = self.module_for(imp[2] if imp[2] and imp[1] != imp[2].split(".")[0] else imp[1])
                    if target is None:
                        target = self.module_for(imp[1])
                    return ("module", target) if target else None
                _, base, attr = imp
                sub = self.module_for(f"{base}.{attr}" if base else attr)
                if sub is not None:
                    return ("module", sub)
                mod, name = self.module_for(base), attr
                continue
            if name in mod.bindings:
                return ("var", mod, name)
            for star in mod.star_imports:
                target = self.module_for(star)
                if target and (name in target.bindings or name in target.functions):
                    mod = target
                    break
            else:
                return None
        return None

    def resolve_callee(self, mod, callee, owner):
        """Map a call descriptor to the project functions it may invoke."""
        kind = callee[0]
        out = []
        if kind == "name":
            name = callee[1]
            # nested functions (closures) first
            if owner is not None:
                s = owner.scope
                bscope = s.find_binding_scope(name) if not s.resolves_global(name) else None
                if bscope is not None and bscope.func is not None:
                    qual = f"{bscope.func.qualname}.{name}"
                    if qual in mod.functions:
                        return [mod.functions[qual]]
                    return []
                if bscope is None and not s.resolves_global(name):
                    return []
            target = self.resolve_global(mod, name)
            if target is None:
                return []
            if target[0] == "func":
                out.append(target[1])
            elif target[0] == "class":
                cmod, cname = target[1], target[2]
                methods = cmod.class_methods.get(cname, {})
                for m in ("__init__", "__post_init__", "__new__"):
                    if m in methods:
                        out.append(methods[m])
        elif kind == "attr":
            base, attr = callee[1], callee[2]
            if owner is not None and not owner.scope.resolves_global(base):
                return self._methods_named(attr)
            target = self.resolve_global(mod, base)
            if target and target[0] == "module" and target[1] is not None:
                t2 = self.resolve_global(target[1], attr)
                if t2 and t2[0] == "func":
                    out.append(t2[1])
                elif t2 and t2[0] == "class":
                    methods = t2[1].class_methods.get(t2[2], {})
                    out.extend(methods[m] for m in ("__init__", "__post_init__") if m in methods)
            elif target and target[0] == "var":
                cls = target[1].instances.get(target[2])
                if cls:
                    out.extend(self._class_method(target[1], cls, attr))
                else:
                    out.extend(self._methods_named(attr))
            elif target and target[0] == "class":
                out.extend(self._class_method(target[1], target[2], attr))
        elif kind == "self":
            if owner is not None and owner.class_name:
                out.extend(self._class_method(mod, owner.class_name, callee[1]))
        elif kind in ("method", "dotted"):
            out.extend(self._methods_named(callee[-1]))
        return out

    def _class_method(self, mod, cname, attr):
        cname = cname.split(".")[-1]
        methods = mod.class_methods.get(cname)
        if methods is None:
            # class imported from another module
            target = self.resolve_global(mod, cname)
            if target and target[0] == "class":
                methods = target[1].class_methods.get(target[2], {})
        if methods and attr in methods:
            return [methods[attr]]
        return []

    def _methods_named(self, attr):
        out = []
        for mod in self.modules.values():
            for methods in mod.class_methods.values():
                if attr in methods:
                    out.append(methods[attr])
        return out

    def resolve_ref(self, mod, ref, owner):
        """Function(s) referenced by a callback reference descriptor."""
        if ref[0] == "name":
            return self.resolve_callee(mod, ("name", ref[1]), owner)
        if ref[0] == "attr":
            return self.resolve_callee(mod, ("attr", ref[1], ref[2]), owner)
        if ref[0] == "self":
            return self.resolve_callee(mod, ("self", ref[1]), owner)
        if ref[0] == "lambda":
            f = mod.functions.get(ref[1])
            return [f] if f else []
        return []

    # -- execution contexts ---------------------------------------------------------
    def classify_functions(self):
        """Work out which functions run per frame and which only at start-up."""
        per_frame = set()
        startup = set()
        loop_roots = set()
        roots = []
        for mod in self.modules.values():
            for ref, kind, owner, in_loop in mod.callback_refs:
                targets = self.resolve_ref(mod, ref, owner)
                if not targets:
                    continue
                if kind in ("sink", "store"):
                    # handed to an animation/event system, or stored for later
                    roots.extend(targets)
                elif kind == "arg":
                    # passed to an ordinary call (``odeint(rhs, ...)``): it runs
                    # wherever that call runs
                    if owner is None:
                        (loop_roots if in_loop else startup).update(t.key for t in targets)
                    else:
                        self._pending_arg.append((owner, targets, in_loop))
            for callee, in_loop in mod.module_calls:
                targets = self.resolve_callee(mod, callee, None)
                (loop_roots if in_loop else startup).update(t.key for t in targets)
        funcs = {f.key: f for m in self.modules.values() for f in m.functions.values()}

        # Startup closure: functions called (transitively) from module level.
        work = list(startup)
        while work:
            key = work.pop()
            f = funcs.get(key)
            if f is None:
                continue
            for callee, in_loop in f.calls:
                for t in self.resolve_callee(f.module, callee, f):
                    if in_loop:
                        if t.key not in loop_roots:
                            loop_roots.add(t.key)
                    elif t.key not in startup:
                        startup.add(t.key)
                        work.append(t.key)
            for owner, targets, in_loop in self._pending_arg:
                if owner.key == key:
                    for t in targets:
                        if in_loop:
                            loop_roots.add(t.key)
                        elif t.key not in startup:
                            startup.add(t.key)
                            work.append(t.key)

        # Per-frame closure: callbacks, functions reached from blocking loops,
        # and everything they call.
        work = [t.key for t in roots] + list(loop_roots)
        per_frame.update(work)
        while work:
            key = work.pop()
            f = funcs.get(key)
            if f is None:
                continue
            for callee, _ in f.calls:
                for t in self.resolve_callee(f.module, callee, f):
                    if t.key not in per_frame:
                        per_frame.add(t.key)
                        work.append(t.key)
            for owner, targets, _ in self._pending_arg:
                if owner.key == key:
                    for t in targets:
                        if t.key not in per_frame:
                            per_frame.add(t.key)
                            work.append(t.key)
        self.per_frame = per_frame
        self.startup = startup
        self.funcs = funcs

    def analyse(self):
        self._pending_arg = []
        self.classify_functions()


def _assigns_name(fnode, name):
    for node in ast.walk(fnode):
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
            return True
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return True
    return False


# ---------------------------------------------------------------------------
# Detection sources
# ---------------------------------------------------------------------------

def _qualify(mod, name, main):
    return name if mod.rel == main else f"{mod.rel}:{name}"


def _comment_for(mod, node):
    """Trailing comment on the node's first or last line (without hint)."""
    lines = [getattr(node, "end_lineno", node.lineno), node.lineno]
    for ln in lines:
        c = mod.comments.get(ln)
        if c is not None:
            line_text = mod.lines[ln - 1] if 0 < ln <= len(mod.lines) else ""
            if line_text.strip().startswith("#"):
                continue
            return c
    return None


def _hint_for(mod, node):
    first, last = node.lineno, getattr(node, "end_lineno", node.lineno)
    for ln in range(first, last + 1):
        c = mod.comments.get(ln)
        if c and HINT_RE.search(c):
            line_text = mod.lines[ln - 1] if 0 < ln <= len(mod.lines) else ""
            # a hint on its own line applies to the statement below it
            if line_text.strip().startswith("#") and ln == first:
                continue
            return parse_hint(c)
    # comment line directly above
    above = first - 1
    if above >= 1:
        text = mod.lines[above - 1].strip() if above <= len(mod.lines) else ""
        if text.startswith("#") and HINT_RE.search(text):
            return parse_hint(text[1:])
    return {}


def _leading_comment(mod, node):
    """Contiguous comment lines directly above a statement (not hints)."""
    out = []
    ln = node.lineno - 1
    while ln >= 1:
        text = mod.lines[ln - 1].strip() if ln <= len(mod.lines) else ""
        if not text.startswith("#"):
            break
        body = text.lstrip("#").strip()
        if HINT_RE.search(body):
            body = strip_hint(body)
        if body:
            out.append(body)
        ln -= 1
    out.reverse()
    return " ".join(out) if out else None


def _fill_from_value(p, value, max_tuple):
    typ, elem, length = value_type(value, max_tuple)
    if p.type is None:
        p.type = typ
    if typ == "tuple":
        p.elem_type, p.length = elem, length
    return typ is not None


def detect_constants(project, mod, params, max_tuple, max_str_len):
    """Top-level literal assignments: ``FREQ = 2.0``."""
    seen = set()
    for stmt in _module_level_statements(mod.tree.body):
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                continue
            target, value_node, annotation = stmt.targets[0], stmt.value, None
        elif isinstance(stmt, ast.AnnAssign):
            if not isinstance(stmt.target, ast.Name) or stmt.value is None:
                continue
            target, value_node, annotation = stmt.target, stmt.value, stmt.annotation
        else:
            continue
        name = target.id
        if name in seen:
            continue
        seen.add(name)
        hint = _hint_for(mod, stmt)
        if name.startswith("__") and name.endswith("__"):
            continue
        if len(mod.bindings.get(name, [])) > 1:
            project.skipped.append({"name": name, "file": mod.rel, "line": stmt.lineno, "reason": "assigned more than once"})
            continue
        if name in mod.state_names:
            project.skipped.append({"name": name, "file": mod.rel, "line": stmt.lineno, "reason": "simulation state (rebound inside a function)"})
            continue
        if hint.get("ignore"):
            project.skipped.append({"name": name, "file": mod.rel, "line": stmt.lineno, "reason": "marked # demo: ignore"})
            continue
        if isinstance(value_node, ast.Dict) and CONFIG_NAME_RE.match(name):
            continue  # handled by detect_config_dicts
        try:
            value, is_expr = safe_eval(value_node)
        except ValueError:
            deps = sorted({n.id for n in ast.walk(value_node) if isinstance(n, ast.Name)} & set(mod.bindings))
            if deps and _is_simple_expression(value_node):
                project.derived.append({"name": name, "file": mod.rel, "line": stmt.lineno, "depends": deps})
            continue
        if isinstance(value, dict):
            continue
        ann = annotation_info(annotation)
        typ, _, _ = value_type(value, max_tuple)
        if typ is None:
            project.skipped.append({"name": name, "file": mod.rel, "line": stmt.lineno, "reason": f"unsupported value type ({type(value).__name__})"})
            continue
        if typ == "str":
            if (PATHLIKE_NAME_RE.search(name) or FILE_EXT_RE.search(value) or "/" in value or "\\" in value) and not ann.get("choices"):
                project.skipped.append({"name": name, "file": mod.rel, "line": stmt.lineno, "reason": "looks like a file path"})
                continue
            if len(value) > max_str_len or "\n" in value or "{" in value:
                project.skipped.append({"name": name, "file": mod.rel, "line": stmt.lineno, "reason": "long or formatted text"})
                continue
        elif PATHLIKE_NAME_RE.search(name) and typ not in ("bool",):
            pass
        upper = bool(UPPER_RE.match(name))
        p = Param(_qualify(mod, name, project.main), name, mod.rel, stmt.lineno, "constant", value, node=value_node)
        p.end_line = getattr(stmt, "end_lineno", stmt.lineno)
        p.expr = is_expr
        if ann.get("type"):
            p.type = ann["type"]
        if ann.get("choices"):
            p.choices = ann["choices"]
        _fill_from_value(p, value, max_tuple)
        if ann.get("type") == "float" and isinstance(value, int) and not isinstance(value, bool):
            p.type = "float"
            p.default = float(value)
        p.apply = {"kind": "literal", "file": mod.rel, "span": _span(value_node)}
        p.tier = "primary" if upper else "other"
        p.hint = hint
        _describe_from_comments(mod, stmt, p)
        params.append(p)


def _is_simple_expression(node):
    for n in ast.walk(node):
        if isinstance(n, (ast.Lambda, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp, ast.Await, ast.Yield)):
            return False
    return True


def _module_level_statements(body):
    """Module-level statements, descending into if/try/with blocks."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, ast.If):
            yield from _module_level_statements(stmt.body)
            yield from _module_level_statements(stmt.orelse)
        elif isinstance(stmt, ast.Try) or (hasattr(ast, "TryStar") and isinstance(stmt, getattr(ast, "TryStar"))):
            yield from _module_level_statements(stmt.body)
            for h in stmt.handlers:
                yield from _module_level_statements(h.body)
            yield from _module_level_statements(stmt.orelse)
            yield from _module_level_statements(stmt.finalbody)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            yield from _module_level_statements(stmt.body)


def _label_fits_name(label, name):
    """Use a comment as the label only if it relates to the name (shares a
    word stem) or the name is a cryptic symbol such as ``DT`` or ``K``."""
    base = split_name_unit(name)[0]
    parts = [w.lower() for w in re.split(r"[_\W]+|(?<=[a-z])(?=[A-Z])", base) if w]
    if len("".join(parts)) <= 3:
        return True
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", label)]
    for a in parts:
        for b in words:
            n = min(len(a), len(b))
            if n >= 4 and a[:4] == b[:4]:
                return True
            if n < 4 and a == b:
                return True
    return False


def _describe_from_comments(mod, node, p):
    comment = _comment_for(mod, node)
    label, help_text, unit = label_from_comment(comment) if comment else (None, None, None)
    if label and not _label_fits_name(label, p.name):
        help_text = help_text or strip_hint(comment)
        label = None
    if label and not p.label:
        p.label = label
    if help_text and not p.help:
        p.help = help_text
    elif comment and not p.help and not label and strip_hint(comment):
        p.help = strip_hint(comment)
    if unit and not p.unit:
        p.unit = unit
    if not p.help:
        lead = _leading_comment(mod, node)
        if lead:
            p.help = lead
    if p.type in ("str", "choice") and not p.choices and comment:
        ch = choices_from_comment(comment)
        if ch:
            p.choices = ch


def detect_config_dicts(project, mod, params, max_tuple, max_str_len):
    """``CONFIG = {"freq": 2.0, ...}`` -> one parameter per simple entry."""
    for stmt in _module_level_statements(mod.tree.body):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            name, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            name, value = stmt.target.id, stmt.value
        else:
            continue
        if not isinstance(value, ast.Dict) or not CONFIG_NAME_RE.match(name):
            continue
        if len(mod.bindings.get(name, [])) > 1:
            continue
        dict_hint = _hint_for(mod, stmt) if stmt.lineno == getattr(stmt, "end_lineno", stmt.lineno) else {}
        if dict_hint.get("ignore"):
            continue
        for k, v in zip(value.keys, value.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                continue
            try:
                val, is_expr = safe_eval(v)
            except ValueError:
                continue
            typ, _, _ = value_type(val, max_tuple)
            if typ is None or (typ == "str" and (len(val) > max_str_len or "/" in val or FILE_EXT_RE.search(val))):
                continue
            key = k.value
            entry_hint = _entry_hint(mod, k, v)
            if entry_hint.get("ignore"):
                continue
            pid = f"{name}.{key}" if key.isidentifier() else f"{name}[{key!r}]"
            p = Param(_qualify(mod, pid, project.main), key, mod.rel, k.lineno, "config-dict", val, node=v)
            p.end_line = getattr(v, "end_lineno", v.lineno)
            p.expr = is_expr
            _fill_from_value(p, val, max_tuple)
            p.apply = {"kind": "literal", "file": mod.rel, "span": _span(v)}
            p.group = name
            p.hint = entry_hint
            p.container = name
            _describe_entry(mod, k, v, p)
            params.append(p)


def _entry_hint(mod, k, v):
    for ln in range(k.lineno, getattr(v, "end_lineno", v.lineno) + 1):
        c = mod.comments.get(ln)
        if c and HINT_RE.search(c):
            return parse_hint(c)
    return {}


def _describe_entry(mod, k, v, p):
    ln = getattr(v, "end_lineno", v.lineno)
    comment = mod.comments.get(ln) or mod.comments.get(k.lineno)
    if comment:
        label, help_text, unit = label_from_comment(comment)
        p.label = p.label or label
        p.help = p.help or help_text or strip_hint(comment) or None
        p.unit = p.unit or unit
        if p.type in ("str", "choice") and not p.choices:
            p.choices = choices_from_comment(comment)


def detect_dataclasses(project, mod, params, max_tuple):
    """``@dataclass`` / ``NamedTuple`` config classes with literal defaults."""
    for cname, cnode in mod.classes.items():
        kind = None
        frozen = False
        for d in cnode.decorator_list:
            dn = _dotted_name(d.func if isinstance(d, ast.Call) else d) or ""
            if dn.split(".")[-1] == "dataclass":
                kind = "dataclass"
                if isinstance(d, ast.Call):
                    for kw in d.keywords:
                        if kw.arg == "frozen" and isinstance(kw.value, ast.Constant) and kw.value.value:
                            frozen = True
        for b in cnode.bases:
            bn = _dotted_name(b) or ""
            if bn.split(".")[-1] == "NamedTuple":
                kind = "namedtuple"
                frozen = True
        if kind is None:
            continue
        class_hint = _hint_for(mod, cnode)
        if class_hint.get("ignore"):
            continue
        for stmt in cnode.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None):
                continue
            fname = stmt.target.id
            value_node = stmt.value
            if isinstance(value_node, ast.Call) and (_dotted_name(value_node.func) or "").split(".")[-1] == "field":
                inner = None
                for kw in value_node.keywords:
                    if kw.arg == "default":
                        inner = kw.value
                if inner is None:
                    continue
                value_node = inner
            try:
                val, is_expr = safe_eval(value_node)
            except ValueError:
                continue
            hint = _hint_for(mod, stmt)
            if hint.get("ignore"):
                continue
            ann = annotation_info(stmt.annotation)
            p = Param(_qualify(mod, f"{cname}.{fname}", project.main), fname, mod.rel, stmt.lineno, kind, val, node=value_node)
            p.expr = is_expr
            if ann.get("type"):
                p.type = ann["type"]
            if ann.get("choices"):
                p.choices = ann["choices"]
            if ann.get("elem_type"):
                p.elem_type = ann["elem_type"]
            if not _fill_from_value(p, val, max_tuple) and not p.type:
                continue
            if p.type == "float" and isinstance(val, int) and not isinstance(val, bool):
                p.default = float(val)
            if p.type is None:
                continue
            p.apply = {"kind": "literal", "file": mod.rel, "span": _span(value_node)}
            p.group = cname
            p.hint = hint
            p.container = cname
            p.frozen = frozen
            _describe_from_comments(mod, stmt, p)
            params.append(p)


def find_entry_call(project):
    """The entry function called under ``if __name__ == "__main__":`` (or a
    conventionally named function called at module level)."""
    mod = project.modules.get(project.main)
    if mod is None:
        return None
    for call in mod.main_guard_calls:
        if isinstance(call.func, ast.Name) and call.func.id in mod.functions:
            return mod.functions[call.func.id], call
    for stmt in mod.tree.body:
        node = stmt.value if isinstance(stmt, (ast.Expr, ast.Assign)) else None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ENTRY_NAMES and node.func.id in mod.functions:
            return mod.functions[node.func.id], node
    return None


def detect_entry_function(project, params, max_tuple):
    """Keyword defaults of ``main(...)``/``run(...)``/``simulate(...)``."""
    mod = project.modules.get(project.main)
    if mod is None:
        return None
    found = find_entry_call(project)
    candidates = []
    if found:
        candidates.append(found)
    else:
        for name in ENTRY_NAMES:
            if name in mod.functions:
                candidates.append((mod.functions[name], None))
                break
    if not candidates:
        return None
    func, call = candidates[0]
    node = func.node
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    pairs = list(zip(positional, defaults)) + list(zip(args.kwonlyargs, args.kw_defaults))
    call_values = {}
    if call is not None:
        for i, a in enumerate(call.args):
            if isinstance(a, ast.Starred):
                break
            if i < len(positional):
                call_values[positional[i].arg] = a
        for kw in call.keywords:
            if kw.arg:
                call_values[kw.arg] = kw.value
    docs = parse_docstring_params(ast.get_docstring(node) or "")
    for arg, default in pairs:
        if arg.arg in ("self", "cls"):
            continue
        source_node = call_values.get(arg.arg, default)
        if source_node is None:
            continue
        try:
            val, is_expr = safe_eval(source_node)
        except ValueError:
            continue
        ann = annotation_info(arg.annotation)
        p = Param(f"{func.name}.{arg.arg}", arg.arg, mod.rel, getattr(source_node, "lineno", node.lineno), "function", val, node=source_node)
        p.expr = is_expr
        if ann.get("type"):
            p.type = ann["type"]
        if ann.get("choices"):
            p.choices = ann["choices"]
        if not _fill_from_value(p, val, max_tuple) and not p.type:
            continue
        if p.type is None:
            continue
        if p.type == "float" and isinstance(val, int) and not isinstance(val, bool):
            p.default = float(val)
        p.apply = {"kind": "literal", "file": mod.rel, "span": _span(source_node)}
        p.group = f"{func.name}() arguments"
        line_comment = mod.comments.get(arg.lineno)
        hint = parse_hint(line_comment) if line_comment and arg.lineno != node.lineno else {}
        if arg.arg in call_values and call_values[arg.arg] is source_node:
            hint = hint or _entry_line_hint(mod, source_node)
        p.hint = hint
        if arg.arg in docs:
            p.help = docs[arg.arg]
            _, _, unit = label_from_comment(docs[arg.arg])
            p.unit = unit
        elif line_comment and arg.lineno != node.lineno:
            label, help_text, unit = label_from_comment(line_comment)
            p.label, p.help, p.unit = label, help_text or strip_hint(line_comment) or None, unit
        if hint.get("ignore"):
            continue
        params.append(p)
    return {"function": func.name, "file": mod.rel, "line": node.lineno, "call_line": getattr(call, "lineno", None)}


def _entry_line_hint(mod, node):
    c = mod.comments.get(node.lineno)
    return parse_hint(c) if c else {}


def parse_docstring_params(doc):
    """Parameter descriptions from Google, NumPy or Sphinx style docstrings."""
    out = {}
    if not doc:
        return out
    for m in re.finditer(r":param\s+(?:\w+\s+)?(\w+)\s*:\s*(.+)", doc):
        out[m.group(1)] = m.group(2).strip()
    lines = doc.splitlines()
    section = None
    numpy_style = False
    current = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.fullmatch(r"(Args|Arguments|Parameters|Params|Keyword Args|Keyword Arguments|Other Parameters)\s*:?", stripped):
            section = "params"
            numpy_style = i + 1 < len(lines) and re.fullmatch(r"\s*-{3,}\s*", lines[i + 1]) is not None
            current = None
            continue
        if section != "params":
            continue
        if re.fullmatch(r"-{3,}", stripped):
            continue
        if re.fullmatch(r"(Returns|Return|Yields|Raises|Examples?|Notes?|See Also|References)\s*:?", stripped):
            section = None
            continue
        if not stripped:
            current = None
            continue
        if numpy_style:
            m = re.match(r"^(\w+)\s*:\s*(.*)$", stripped)
            if m and not line.startswith((" ", "\t")) or (m and current is None):
                current = m.group(1)
                continue
            if current:
                out[current] = (out.get(current, "") + " " + stripped).strip()
            continue
        m = re.match(r"^(\w+)\s*(\([^)]*\))?\s*:\s*(.*)$", stripped)
        if m:
            current = m.group(1)
            if m.group(3):
                out[current] = m.group(3).strip()
        elif current:
            out[current] = (out.get(current, "") + " " + stripped).strip()
    return out


def detect_argparse(project, params, max_tuple):
    """``parser.add_argument("--freq", type=float, default=2.0, help=...)``."""
    mod = project.modules.get(project.main)
    if mod is None:
        return False
    parser_like = set(mod.parser_vars)
    found = False
    set_defaults = {}
    calls = []
    for node in ast.walk(mod.tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("add_argument_group", "add_mutually_exclusive_group"):
            # groups added to a parser behave like the parser
            continue
        if isinstance(func, ast.Attribute) and func.attr == "add_argument":
            calls.append(node)
        if isinstance(func, ast.Attribute) and func.attr == "set_defaults":
            for kw in node.keywords:
                if kw.arg:
                    try:
                        set_defaults[kw.arg] = safe_eval(kw.value)[0]
                    except ValueError:
                        pass
    if not calls:
        return False
    # parse_args namespaces used at module level / per frame
    for order, call in enumerate(calls):
        opts = [a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if not opts:
            continue
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        positional = not opts[0].startswith("-")
        long_opts = [o for o in opts if o.startswith("--")]
        option = long_opts[0] if long_opts else opts[0]
        if "dest" in kw and isinstance(kw["dest"], ast.Constant):
            dest = kw["dest"].value
        elif positional:
            dest = opts[0]
        else:
            dest = option.lstrip("-").replace("-", "_")
        if dest in ("help", "version"):
            continue
        action = kw.get("action")
        action_name = action.value if isinstance(action, ast.Constant) else "store"
        if action_name in ("help", "version", "count", "append", "append_const", "store_const", "extend"):
            project.skipped.append({"name": dest, "file": mod.rel, "line": call.lineno, "reason": f"argparse action '{action_name}' not exposed"})
            continue
        hint = _hint_for(mod, call)
        if hint.get("ignore"):
            continue
        typ = None
        tnode = kw.get("type")
        if tnode is not None:
            tn = _dotted_name(tnode) or ""
            typ = {"float": "float", "int": "int", "str": "str"}.get(tn.split(".")[-1])
        default = None
        has_default = False
        if action_name in ("store_true", "store_false", "BooleanOptionalAction") or (isinstance(action, ast.Attribute) and action.attr == "BooleanOptionalAction"):
            typ = "bool"
            default = action_name == "store_false"
            has_default = True
            if isinstance(action, ast.Attribute):
                action_name = "boolean_optional"
        if "default" in kw:
            try:
                default, is_expr = safe_eval(kw["default"])
                has_default = True
            except ValueError:
                project.notes.append(f"argparse option {option}: default is an expression; it will be read when the script runs")
                default = None
        if dest in set_defaults:
            default = set_defaults[dest]
            has_default = True
        nargs = kw.get("nargs")
        nargs_val = nargs.value if isinstance(nargs, ast.Constant) else None
        if nargs is not None and not isinstance(nargs_val, int) and nargs_val != "?":
            project.skipped.append({"name": dest, "file": mod.rel, "line": call.lineno, "reason": "variable-length argparse option"})
            continue
        choices = None
        if "choices" in kw:
            try:
                choices = list(safe_eval(kw["choices"])[0])
            except (ValueError, TypeError):
                c = kw["choices"]
                if isinstance(c, ast.Call) and _dotted_name(c.func) == "range":
                    try:
                        rargs = [safe_eval(a)[0] for a in c.args]
                        choices = list(range(*rargs))
                    except (ValueError, TypeError):
                        choices = None
        if typ is None:
            if default is not None:
                typ = value_type(default, max_tuple)[0]
            elif choices:
                typ = "choice" if all(isinstance(c, str) for c in choices) else value_type(choices[0], max_tuple)[0]
            else:
                typ = "str"
        if positional and not has_default:
            if choices:
                default = choices[0]
            elif typ in ("int", "float"):
                default = 0 if typ == "int" else 0.0
            else:
                default = ""
            project.notes.append(f"argparse positional '{dest}' has no default; starting from {default!r}")
        if isinstance(nargs_val, int) and nargs_val > 1:
            if isinstance(default, (list, tuple)) and len(default) == nargs_val:
                typ = "tuple"
            else:
                project.skipped.append({"name": dest, "file": mod.rel, "line": call.lineno, "reason": "fixed-length argparse option without a default"})
                continue
        if typ == "float" and isinstance(default, int) and not isinstance(default, bool):
            default = float(default)
        if default is None and typ in ("int", "float"):
            default = 0 if typ == "int" else 0.0
        if typ == "str" and choices:
            typ = "choice"
        p = Param(f"args.{dest}", dest, mod.rel, call.lineno, "argparse", default, type_=typ)
        p.end_line = getattr(call, "end_lineno", call.lineno)
        if choices is not None:
            p.choices = choices
            if typ in ("int", "float") and all(isinstance(c, (int, float)) for c in choices):
                p.type = "choice"
        if typ == "tuple":
            p.elem_type = "float" if any(isinstance(v, float) for v in default) else "int"
            p.length = len(default)
        help_node = kw.get("help")
        if isinstance(help_node, ast.Constant) and isinstance(help_node.value, str):
            text = help_node.value.replace("%(default)s", str(default)).replace("%%", "%")
            p.help = text
            label, _, unit = label_from_comment(text)
            p.label = label
            p.unit = unit
        p.apply = {
            "kind": "argv", "dest": dest, "option": option, "action": action_name,
            "positional": positional, "order": order, "nargs": nargs_val,
            "has_default": has_default,
        }
        p.group = "Command-line options"
        p.hint = hint
        params.append(p)
        found = True
    return found


def detect_demo_params(project, params, max_tuple):
    """Values declared through the optional ``demo_params`` helper."""
    used = False
    for mod in project.modules.values():
        imported = [local for local, imp in mod.imports.items() if imp[0] == "from" and imp[1] == "demo_params" and imp[2] == "params"]
        if not imported and not any(imp[0] == "module" and imp[1] == "demo_params" for imp in mod.imports.values()):
            # also handle ``try: from demo_params import params`` (imports in try
            # blocks are still module level and recorded above)
            continue
        used = True
        pname = imported[0] if imported else "params"
        # 1. fallback dicts: ``params = {"freq": 2.0}`` (usually in ``except ImportError``)
        for stmt in _module_level_statements(mod.tree.body):
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) \
                    and stmt.targets[0].id == pname and isinstance(stmt.value, ast.Dict):
                for k, v in zip(stmt.value.keys, stmt.value.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        try:
                            val, _ = safe_eval(v)
                        except ValueError:
                            continue
                        _add_demo_param(project, mod, params, k.value, val, k.lineno, _entry_hint(mod, k, v), max_tuple, k, v)
        # 2. declarations: params.declare("freq", 2.0, min=..., ...)
        #    and params.get("freq", 2.0) / params.setdefault("freq", 2.0)
        for node in ast.walk(mod.tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)):
                continue
            if node.func.value.id not in (pname, "demo_params"):
                continue
            if node.func.attr not in ("declare", "get", "setdefault", "param"):
                continue
            if len(node.args) < 2 or not (isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                continue
            try:
                val, _ = safe_eval(node.args[1])
            except ValueError:
                continue
            extra = {}
            for kw in node.keywords:
                if kw.arg:
                    try:
                        extra[kw.arg] = safe_eval(kw.value)[0]
                    except ValueError:
                        pass
            hint = dict(_hint_for(mod, node))
            for key in ("min", "max", "step", "label", "unit", "help", "group", "choices", "scale", "mode"):
                if key in extra:
                    hint[key] = extra[key]
            if "range" in extra and isinstance(extra["range"], (tuple, list)) and len(extra["range"]) == 2:
                hint["min"], hint["max"] = extra["range"]
            _add_demo_param(project, mod, params, node.args[0].value, val, node.lineno, hint, max_tuple, None, None,
                            declared=node.func.attr == "declare")
    return used


def _add_demo_param(project, mod, params, key, val, line, hint, max_tuple, knode, vnode, declared=False):
    pid = f"params.{key}"
    for existing in params:
        if existing.id == pid:
            if declared:
                existing.hint.update(hint)
            return
    typ, _, _ = value_type(val, max_tuple)
    if typ is None:
        return
    p = Param(pid, key, mod.rel, line, "demo_params", val)
    _fill_from_value(p, val, max_tuple)
    p.apply = {"kind": "demo_params", "key": key}
    p.live_targets = [{"kind": "demo_params", "key": key}]
    p.hint = hint
    p.group = "Parameters"
    if knode is not None:
        _describe_entry(mod, knode, vnode, p)
    params.append(p)


# ---------------------------------------------------------------------------
# Choices inferred from how a string is used
# ---------------------------------------------------------------------------

def infer_string_choices(project, p):
    """``if METHOD == "rk4": ... elif METHOD == "euler":`` -> ["rk4", "euler"];
    ``SOLVERS[METHOD]`` with a literal dict -> its keys; ``match METHOD:``."""
    mod = project.modules.get(p.file)
    if mod is None or p.source not in ("constant", "config-dict", "dataclass", "function"):
        return None
    name = p.name
    found = []

    def is_ref(n):
        if p.source == "constant":
            return isinstance(n, ast.Name) and n.id == name
        if p.source == "config-dict":
            return isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and n.slice.value == name
        return (isinstance(n, ast.Attribute) and n.attr == name) or (isinstance(n, ast.Name) and n.id == name)

    dict_literals = {}
    for stmt in _module_level_statements(mod.tree.body):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) \
                and isinstance(stmt.value, ast.Dict):
            keys = [k.value for k in stmt.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if keys and len(keys) == len(stmt.value.keys):
                dict_literals[stmt.targets[0].id] = keys
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Compare):
            sides = [node.left] + list(node.comparators)
            if any(is_ref(s) for s in sides):
                for op, s in zip(node.ops, node.comparators):
                    if isinstance(op, (ast.Eq, ast.NotEq)):
                        for cand in (s, node.left):
                            if isinstance(cand, ast.Constant) and isinstance(cand.value, str):
                                found.append(cand.value)
                    elif isinstance(op, (ast.In, ast.NotIn)) and isinstance(s, (ast.Tuple, ast.List, ast.Set)):
                        found.extend(e.value for e in s.elts if isinstance(e, ast.Constant) and isinstance(e.value, str))
        elif isinstance(node, ast.Subscript) and is_ref(node.slice) and isinstance(node.value, ast.Name):
            found.extend(dict_literals.get(node.value.id, []))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                and isinstance(node.func.value, ast.Name) and node.args and is_ref(node.args[0]):
            found.extend(dict_literals.get(node.func.value.id, []))
        elif hasattr(ast, "Match") and isinstance(node, ast.Match) and is_ref(node.subject):
            for case in node.cases:
                pat = case.pattern
                pats = pat.patterns if isinstance(pat, ast.MatchOr) else [pat]
                for pp in pats:
                    if isinstance(pp, ast.MatchValue) and isinstance(pp.value, ast.Constant) and isinstance(pp.value.value, str):
                        found.append(pp.value.value)
    out = []
    for f in found:
        if isinstance(f, str) and f not in out:
            out.append(f)
    if p.default not in out and isinstance(p.default, str):
        out.insert(0, p.default)
    return out if len(out) >= 2 else None


# ---------------------------------------------------------------------------
# Live / restart classification
# ---------------------------------------------------------------------------

def classify(project, p):
    """Decide live vs restart for one parameter and record live targets."""
    if p.source == "argparse":
        return _classify_namespace_attr(project, p)
    if p.source in ("dataclass", "namedtuple"):
        return _classify_instance_attr(project, p)
    if p.source == "function":
        _set_mode(p, "restart", f"default argument of {p.group.split('(')[0]}(), read once when the function is called")
        return
    if p.source == "demo_params":
        return _classify_demo_param(project, p)
    if p.source == "config-dict":
        return _classify_item(project, p)
    if p.source in ("constant", "manifest"):
        return _classify_global(project, p)


def _set_mode(p, mode, reason, via=None):
    p.mode = mode
    p.mode_reason = reason
    if via:
        p.live_via = sorted(set(via))


def _reads_matching(project, mod, predicate):
    """Collect Read records in all modules that refer to the value."""
    hits = []
    for m in project.modules.values():
        for r in m.reads:
            if predicate(m, r):
                hits.append((m, r))
    return hits


def _summarise(project, hits):
    per_frame, startup, cosmetic_startup = [], [], []
    for m, r in hits:
        owner = r.owner
        if r.in_loop:
            per_frame.append((m, r, "loop"))
            continue
        if owner is None:
            if r.context == "cosmetic":
                cosmetic_startup.append((m, r))
            else:
                startup.append((m, r))
            continue
        key = owner.key
        if key in project.per_frame:
            per_frame.append((m, r, "callback"))
        elif key in project.startup:
            if r.context == "cosmetic":
                cosmetic_startup.append((m, r))
            else:
                startup.append((m, r))
        # functions that never run (as far as we can tell) are ignored
    return per_frame, startup, cosmetic_startup


def _where(m, r):
    owner = r.owner.qualname + "()" if r.owner else "module level"
    return f"{m.rel}:{r.line} ({owner})"


def _decide(p, per_frame, startup, cosmetic, what):
    if not per_frame:
        if startup:
            _set_mode(p, "restart", f"only used at start-up ({_where(*startup[0])}); a change needs a re-run")
        else:
            _set_mode(p, "restart", "not read while the animation runs; a change needs a re-run")
        return False
    if startup:
        _set_mode(p, "restart", f"read per frame, but also used to set things up at {_where(*startup[0])}, so a re-run is needed to keep everything consistent")
        return False
    via = [v for _, _, v in per_frame]
    first = per_frame[0]
    where = f"{first[1].owner.qualname}()" if first[1].owner else f"the loop at {first[0].rel}:{first[1].line}"
    reason = f"{what} is read every frame in {where}; changes apply immediately"
    if cosmetic:
        reason += " (labels/titles set at start-up update on restart)"
    _set_mode(p, "live", reason, via)
    return True


def _classify_global(project, p):
    defmod = project.modules.get(p.file)
    if defmod is None:
        return
    name = p.name
    # bindings of this value in other modules (``from mod import NAME as X``)
    importers = []
    for m in project.modules.values():
        if m is defmod:
            continue
        for local, imp in m.imports.items():
            if imp[0] == "from" and project.module_for(imp[1]) is defmod and imp[2] == name:
                importers.append((m, local))
    module_aliases = {}
    for m in project.modules.values():
        for local, imp in m.imports.items():
            target = None
            if imp[0] == "module":
                target = project.module_for(imp[2]) or project.module_for(imp[1])
            elif imp[0] == "from":
                target = project.module_for(f"{imp[1]}.{imp[2]}" if imp[1] else imp[2])
            if target is defmod:
                module_aliases.setdefault(m.rel, set()).add(local)

    if name in defmod.state_names:
        _set_mode(p, "restart", "rebound inside a function (simulation state)")
        return

    importer_names = {(m.rel, local) for m, local in importers}

    def pred(m, r):
        if m is defmod and r.kind in ("name", "call") and r.name == name:
            return True
        if (m.rel, r.name) in importer_names and r.kind in ("name", "call"):
            return True
        if r.kind == "attr" and r.attr == name and r.name in module_aliases.get(m.rel, ()):
            return True
        return False

    hits = _reads_matching(project, defmod, pred)
    per_frame, startup, cosmetic = _summarise(project, hits)
    if _decide(p, per_frame, startup, cosmetic, name):
        targets = [{"kind": "global", "module": defmod.rel, "name": name}]
        for m, local in importers:
            targets.append({"kind": "global", "module": m.rel, "name": local})
        p.live_targets = targets


def _classify_item(project, p):
    mod = project.modules.get(p.file)
    container = getattr(p, "container", None)
    key = p.name

    def pred(m, r):
        if m is not mod:
            return False
        if r.name != container:
            return False
        if r.kind == "item":
            return r.key == key
        # the whole dict passed around / unpacked: treat as a start-up use
        return r.kind == "name" and r.owner is None and r.context != "cosmetic"

    hits = _reads_matching(project, mod, pred)
    per_frame, startup, cosmetic = _summarise(project, hits)
    item_frame = [h for h in per_frame if h[1].kind == "item"]
    if _decide(p, item_frame, startup, cosmetic, f"{container}[{key!r}]"):
        p.live_targets = [{"kind": "item", "module": mod.rel, "name": container, "key": key}]


def _classify_instance_attr(project, p):
    """Dataclass fields: live only if a module-level instance's attribute is
    read per frame and the field isn't overridden at construction."""
    mod = project.modules.get(p.file)
    cname = getattr(p, "container", None)
    if getattr(p, "frozen", False):
        _set_mode(p, "restart", f"{cname} is immutable; its values are fixed when it is created")
        return
    instances = [n for n, c in mod.instances.items() if c.split(".")[-1] == cname]
    if not instances:
        _set_mode(p, "restart", f"default of {cname}.{p.name}, used when a {cname} is created at start-up")
        return
    inst = instances[0]
    construction = None
    for stmt in _module_level_statements(mod.tree.body):
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == inst for t in stmt.targets) \
                and isinstance(stmt.value, ast.Call):
            construction = stmt.value
    if construction is not None and any(kw.arg == p.name for kw in construction.keywords):
        _set_mode(p, "restart", f"overridden when {inst} is created")
        return

    def pred(m, r):
        return m is mod and r.name == inst and r.kind == "attr" and r.attr == p.name

    hits = _reads_matching(project, mod, pred)
    per_frame, startup, cosmetic = _summarise(project, hits)
    if _decide(p, per_frame, startup, cosmetic, f"{inst}.{p.name}"):
        p.live_targets = [{"kind": "attr", "module": mod.rel, "name": inst, "attr": p.name}]


def _classify_namespace_attr(project, p):
    """argparse: values are consumed at start-up, unless a module-level
    ``args = parser.parse_args()`` namespace is read per frame."""
    mod = project.modules.get(p.file)
    dest = p.name
    if not mod.namespace_vars:
        _set_mode(p, "restart", "command-line option, read when the script starts")
        return
    ns = sorted(mod.namespace_vars)[0]

    def pred(m, r):
        return m is mod and r.name in mod.namespace_vars and r.kind == "attr" and r.attr == dest

    hits = _reads_matching(project, mod, pred)
    per_frame, startup, cosmetic = _summarise(project, hits)
    if not per_frame:
        _set_mode(p, "restart", "command-line option, read when the script starts")
        return
    if _decide(p, per_frame, startup, cosmetic, f"{ns}.{dest}"):
        p.live_targets = [{"kind": "attr", "module": mod.rel, "name": ns, "attr": dest}]


def _classify_demo_param(project, p):
    key = p.name
    pnames = set()
    for m in project.modules.values():
        for local, imp in m.imports.items():
            if imp[0] == "from" and imp[1] == "demo_params" and imp[2] == "params":
                pnames.add((m.rel, local))
        for stmt in _module_level_statements(m.tree.body):
            if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "params" for t in stmt.targets):
                pnames.add((m.rel, "params"))

    def pred(m, r):
        return (m.rel, r.name) in pnames and r.kind == "item" and r.key == key

    hits = _reads_matching(project, None, pred)
    per_frame, startup, cosmetic = _summarise(project, hits)
    if not per_frame and not startup:
        _set_mode(p, "live", "declared through demo_params; the script reads it on demand")
        return
    if _decide(p, per_frame, startup, cosmetic, f"params[{key!r}]"):
        p.live_targets = [{"kind": "demo_params", "key": key}]


# ---------------------------------------------------------------------------
# Finishing: labels, ranges, hints, overrides, ranking
# ---------------------------------------------------------------------------

def finish_param(project, p):
    hint = p.hint or {}
    # type and choices
    if p.type in ("str",) and not p.choices:
        ch = infer_string_choices(project, p)
        if ch:
            p.choices = ch
    if hint.get("type") in ("int", "float", "bool", "str", "choice"):
        p.type = hint["type"]
    if hint.get("choices"):
        p.choices = _coerce_choice_list(hint["choices"], p.default)
    if p.choices and p.type in ("str", "int", "float"):
        p.type = "choice"
    if p.type == "choice" and p.choices and p.default not in p.choices:
        p.choices = [p.default] + list(p.choices)
    # unit / label
    base, unit = split_name_unit(p.name)
    if not p.unit and unit:
        p.unit = unit
    if not p.label:
        p.label = prettify(base if unit else p.name)
    for key in ("label", "unit", "help", "group"):
        if hint.get(key):
            setattr(p, key, str(hint[key]))
    # range
    if p.type in ("int", "float"):
        context = " ".join(x for x in (p.label, p.help) if x)
        rng = infer_range(p.name, p.type, p.default if p.default is not None else 0, p.unit, context)
        if rng:
            p.min, p.max, p.step, p.scale = rng["min"], rng["max"], rng["step"], rng["scale"]
    elif p.type == "tuple":
        vals = [abs(v) for v in p.default] or [1]
        top = max(vals) or 1
        p.min, p.max = (-top * 10, top * 10)
        p.step = 1 if p.elem_type == "int" else nice_step(top * 2, 100)
        p.scale = "linear"
    for key in ("min", "max", "step"):
        if hint.get(key) is not None:
            setattr(p, key, hint[key])
    if (hint.get("min") is not None or hint.get("max") is not None or hint.get("step") is not None) and not hint.get("scale"):
        p.scale = _scale_for_range(p.min, p.max, explicit_step=hint.get("step") is not None)
    if hint.get("scale"):
        p.scale = hint["scale"]
    if p.scale == "log" and (p.min is None or p.min <= 0 or (p.max is not None and p.max <= 0)):
        p.scale = "linear"
    if p.type == "float" and p.scale == "linear" and p.step is None and p.min is not None and p.max is not None:
        p.step = nice_step(p.max - p.min)
    if p.type == "int":
        if p.min is not None:
            p.min = int(math.floor(p.min))
        if p.max is not None:
            p.max = int(math.ceil(p.max))
        p.step = max(1, int(p.step or 1))
    if p.min is not None and p.max is not None and p.min > p.max:
        p.min, p.max = p.max, p.min
    # widen the range to include the default
    if p.type in ("int", "float") and isinstance(p.default, (int, float)) and p.min is not None and p.max is not None:
        if p.default < p.min:
            p.min = p.default
        if p.default > p.max:
            p.max = p.default
    if hint.get("hide") is not None:
        p.hidden = bool(hint["hide"])
    if hint.get("mode"):
        _force_mode(p, hint["mode"], "inline # demo: hint")
    if hint.get("order") is not None:
        p.order = hint["order"]
    if hint.get("_unknown"):
        p.notes.append("unknown hint keys: " + ", ".join(hint["_unknown"]))
    if not p.group:
        if p.source == "constant":
            p.group = "Parameters" if p.file == project.main else p.file
        else:
            p.group = SOURCE_GROUP.get(p.source, "Parameters")
    if p.tier == "other":
        p.group = "Other detected values"


def _coerce_choice_list(choices, default):
    out = []
    for c in choices:
        if isinstance(default, bool):
            out.append(str(c).lower() in ("1", "true", "yes", "on"))
        elif isinstance(default, int) and not isinstance(default, bool):
            try:
                out.append(int(c))
            except (TypeError, ValueError):
                out.append(c)
        elif isinstance(default, float):
            try:
                out.append(float(c))
            except (TypeError, ValueError):
                out.append(c)
        else:
            out.append(c if isinstance(c, str) else str(c))
    return out


def _force_mode(p, mode, why):
    if mode == "live" and not p.live_targets:
        if p.source in ("constant", "manifest"):
            p.live_targets = [{"kind": "global", "module": p.file, "name": p.name}]
        elif p.source == "config-dict":
            p.live_targets = [{"kind": "item", "module": p.file, "name": getattr(p, "container", ""), "key": p.name}]
        elif p.source == "demo_params":
            p.live_targets = [{"kind": "demo_params", "key": p.name}]
        else:
            p.notes.append(f"mode=live requested by {why}, but there is no module-level value to update; using restart")
            return
    if mode == "restart" and p.apply.get("kind") == "none":
        p.notes.append(f"mode=restart requested by {why}, but the value cannot be injected on restart")
    detected = p.mode
    p.mode = mode
    p.forced_mode = mode
    p.mode_reason = f"set to {mode} by {why}" + (f" (detected: {detected})" if detected != mode else "")


def apply_manifest_overrides(project, params, overrides, max_tuple):
    """Apply ``parameter_overrides`` from demo.json.  Keys are parameter ids,
    bare names, or ``file:NAME``.  Unknown names that match a module-level
    assignment are added as new parameters."""
    if not overrides:
        return
    by_id = {p.id: p for p in params}
    by_name = {}
    for p in params:
        by_name.setdefault(p.name, []).append(p)
    for key, ov in overrides.items():
        if not isinstance(ov, dict):
            project.errors.append({"file": "demo.json", "line": 0, "message": f"parameter_overrides[{key!r}] must be an object"})
            continue
        file = ov.get("file")
        target = None
        if key in by_id:
            target = by_id[key]
        elif file and f"{file}:{key}" in by_id:
            target = by_id[f"{file}:{key}"]
        elif key in by_name:
            cands = [p for p in by_name[key] if not file or p.file == file]
            if len(cands) == 1:
                target = cands[0]
            elif len(cands) > 1:
                project.notes.append(f"override {key!r} matches {len(cands)} parameters; add \"file\" to choose one")
                target = cands[0]
        elif ":" in key and key.split(":", 1)[1] in by_name:
            fname, nm = key.split(":", 1)
            cands = [p for p in by_name[nm] if p.file == fname]
            target = cands[0] if cands else None
        if target is None:
            target = _add_override_param(project, key, ov, max_tuple)
            if target is None:
                project.notes.append(f"parameter_overrides: {key!r} not found in the project")
                continue
            params.append(target)
            by_id[target.id] = target
        _apply_override_fields(target, ov)


def _apply_override_fields(p, ov):
    for k in ("label", "unit", "help", "group"):
        if k in ov and ov[k] is not None:
            setattr(p, k, str(ov[k]))
    if "type" in ov and ov["type"] in ("int", "float", "bool", "str", "choice"):
        p.type = ov["type"]
    if "choices" in ov and isinstance(ov["choices"], list):
        p.choices = _coerce_choice_list(ov["choices"], p.default)
        if p.type in ("str", "int", "float"):
            p.type = "choice"
        if p.default not in p.choices:
            p.choices = [p.default] + p.choices
    for k in ("min", "max", "step"):
        if k in ov and isinstance(ov[k], (int, float)) and not isinstance(ov[k], bool):
            setattr(p, k, ov[k])
    if "range" in ov and isinstance(ov["range"], list) and len(ov["range"]) == 2:
        p.min, p.max = ov["range"]
    if any(k in ov for k in ("min", "max", "step", "range")) and ov.get("scale") not in ("log", "linear") and p.type in ("int", "float"):
        p.scale = _scale_for_range(p.min, p.max, explicit_step="step" in ov)
        if p.scale == "linear" and p.step is None and p.min is not None and p.max is not None:
            p.step = 1 if p.type == "int" else nice_step(p.max - p.min)
    if ov.get("scale") in ("log", "linear"):
        p.scale = ov["scale"]
        if p.scale == "log" and (p.min is None or p.min <= 0):
            p.notes.append("log scale needs a positive range; using linear")
            p.scale = "linear"
    if "hide" in ov:
        p.hidden = bool(ov["hide"])
    if "default" in ov and ov["default"] is not None:
        p.default = ov["default"]
    if "order" in ov and isinstance(ov["order"], (int, float)):
        p.order = ov["order"]
    if ov.get("mode") in ("live", "restart"):
        _force_mode(p, ov["mode"], "demo.json parameter_overrides")
    if p.type == "int":
        for k in ("min", "max"):
            v = getattr(p, k)
            if v is not None:
                setattr(p, k, int(v))
    if p.tier == "other" and any(k in ov for k in ("label", "group", "hide", "mode", "min", "max")):
        p.tier = "primary"
        if p.group == "Other detected values":
            p.group = ov.get("group") or ("Parameters" if p.source == "constant" else p.group)


def _add_override_param(project, key, ov, max_tuple):
    """A parameter the detector skipped (e.g. ``OMEGA = 2*np.pi*F``): if the
    name has a module-level assignment, expose it."""
    file = ov.get("file") or project.main
    name = key.split(":", 1)[1] if ":" in key else key
    mod = project.modules.get(file)
    if mod is None or not name.isidentifier():
        return None
    for stmt in _module_level_statements(mod.tree.body):
        targets = []
        if isinstance(stmt, ast.Assign):
            targets = [t for t in stmt.targets if isinstance(t, ast.Name)]
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            targets = [stmt.target]
            value = stmt.value
        if any(t.id == name for t in targets) and len(stmt.targets if isinstance(stmt, ast.Assign) else [1]) == 1:
            default = ov.get("default")
            if default is None:
                env = _module_constants(mod)
                try:
                    default = safe_eval(value, env)[0]
                except ValueError:
                    default = None
            typ = ov.get("type") or (value_type(default, max_tuple)[0] if default is not None else "float")
            if default is None:
                lo, hi = ov.get("min"), ov.get("max")
                default = (lo + hi) / 2 if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) else (0 if typ == "int" else 0.0)
            p = Param(_qualify(mod, name, project.main), name, mod.rel, stmt.lineno, "manifest", default, type_=typ)
            p.apply = {"kind": "literal", "file": mod.rel, "span": _span(value)}
            p.notes.append("added by demo.json parameter_overrides")
            p.tier = "primary"
            if typ == "tuple":
                p.elem_type, p.length = value_type(default, max_tuple)[1:]
            p.node = value
            _classify_global(project, p)
            return p
    return None


def _module_constants(mod):
    """Numeric literal constants of a module, for evaluating expressions."""
    env = {}
    for stmt in _module_level_statements(mod.tree.body):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            name = stmt.targets[0].id
            if len(mod.bindings.get(name, [])) != 1:
                continue
            try:
                v, _ = safe_eval(stmt.value, env)
            except ValueError:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                env[name] = v
    return env


def check_buttons(project, buttons):
    """Validate manifest ``buttons``: each must call a function the script
    already defines (module-level, no required arguments)."""
    out = []
    for i, b in enumerate(buttons or []):
        if not isinstance(b, dict) or not isinstance(b.get("call"), str):
            project.errors.append({"file": "demo.json", "line": 0, "message": f"buttons[{i}] needs a \"call\" function name"})
            continue
        fname = b["call"]
        file = b.get("file") or project.main
        mod = project.modules.get(file)
        func = mod.functions.get(fname) if mod else None
        ok = func is not None and "." not in fname
        reason = ""
        if not ok:
            reason = f"function {fname}() not found in {file}"
        else:
            a = func.node.args
            required = len(a.posonlyargs) + len(a.args) - len(a.defaults) + sum(1 for d in a.kw_defaults if d is None)
            if required > 0:
                ok = False
                reason = f"{fname}() takes required arguments"
        out.append({
            "label": str(b.get("label") or prettify(fname)), "call": fname, "file": file,
            "available": ok, "reason": reason, "after": b.get("after", "redraw"),
            "help": b.get("help"),
            "line": func.node.lineno if func else None,
        })
    return out


def rank_params(project, params, options):
    file_order = {project.main: 0}
    for i, rel in enumerate(sorted(project.modules)):
        file_order.setdefault(rel, i + 1)
    def key(p):
        return (
            0 if p.tier == "primary" else 1,
            p.order if p.order is not None else 1e9,
            SOURCE_ORDER.get(p.source, 9),
            file_order.get(p.file, 99),
            p.line,
        )
    params.sort(key=key)
    primary = [p for p in params if p.tier == "primary" and not p.hidden]
    other = [p for p in params if p.tier == "other" and not p.hidden]
    max_controls = int(options.get("max_controls", 14))
    max_other = int(options.get("max_other", 8))
    for p in primary[max_controls:]:
        p.group = "More parameters"
        p.notes.append("beyond the default limit of visible controls")
    dropped = set()
    for p in other[max_other:]:
        dropped.add(p.id)
        project.skipped.append({"name": p.name, "file": p.file, "line": p.line, "reason": "too many lower-case values (raise max_other to show)"})
    kept = [p for p in params if p.id not in dropped]
    for i, p in enumerate(kept):
        p.rank = i
    return kept


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def detect(files, main, overrides=None, options=None):
    """Detect parameters in a project.

    Parameters
    ----------
    files : dict
        ``{relative_path: source_text}`` for (at least) every ``.py`` file.
    main : str
        Relative path of the entry-point script.
    overrides : dict, optional
        ``{"parameter_overrides": {...}, "buttons": [...]}`` from demo.json.
    options : dict, optional
        ``max_controls``, ``max_other``, ``max_tuple``, ``max_str_len``.

    Never raises for problems in the analysed code: those are reported in
    ``result["errors"]`` and detection continues with what it could read.
    """
    t0 = time.perf_counter()
    opts = dict(DEFAULT_OPTIONS)
    opts.update(options or {})
    overrides = overrides or {}
    max_tuple = int(opts["max_tuple"])
    project = Project(files, main, opts)
    result = {
        "version": DETECT_VERSION, "params": [], "buttons": [], "derived": [], "skipped": [],
        "errors": [], "notes": [], "entry": None, "uses_argparse": False,
        "uses_demo_params": False, "blocking_loops": [], "main": main,
    }
    try:
        project.load()
        if main not in project.modules:
            if main not in files:
                project.errors.append({"file": main, "line": 0, "message": "main file not found in the project"})
            result["errors"] = project.errors
            result["timing_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return result
        project.analyse()
        params = []
        steps = [
            ("demo_params", lambda: detect_demo_params(project, params, max_tuple)),
            ("argparse", lambda: detect_argparse(project, params, max_tuple)),
            ("config", lambda: [detect_config_dicts(project, m, params, max_tuple, opts["max_str_len"]) for m in project.modules.values()]),
            ("dataclass", lambda: [detect_dataclasses(project, m, params, max_tuple) for m in project.modules.values()]),
            ("entry", lambda: detect_entry_function(project, params, max_tuple)),
            ("constants", lambda: [detect_constants(project, m, params, max_tuple, opts["max_str_len"]) for m in project.modules.values()]),
        ]
        for name, step in steps:
            try:
                out = step()
            except Exception as exc:  # fail soft per source
                project.errors.append({"file": main, "line": 0, "message": f"{name} detection failed: {exc!r}"})
                continue
            if name == "demo_params":
                result["uses_demo_params"] = bool(out)
            elif name == "argparse":
                result["uses_argparse"] = bool(out)
            elif name == "entry":
                result["entry"] = out
        # de-duplicate (a demo_params declaration wins over the fallback constant)
        seen_ids = set()
        unique = []
        dp_names = {p.name for p in params if p.source == "demo_params"}
        for p in params:
            if p.id in seen_ids:
                continue
            if p.source == "constant" and p.name == "params" and dp_names:
                continue
            seen_ids.add(p.id)
            unique.append(p)
        params = unique
        for p in params:
            try:
                classify(project, p)
            except Exception as exc:  # pragma: no cover - defensive
                _set_mode(p, "restart", f"could not classify ({exc!r}); restarting is always safe")
            try:
                finish_param(project, p)
            except Exception as exc:  # pragma: no cover - defensive
                p.notes.append(f"could not infer range: {exc!r}")
        try:
            apply_manifest_overrides(project, params, overrides.get("parameter_overrides") or {}, max_tuple)
        except Exception as exc:  # pragma: no cover - defensive
            project.errors.append({"file": "demo.json", "line": 0, "message": f"parameter_overrides failed: {exc!r}"})
        for p in params:
            if p.source == "manifest" and p.label is None:
                finish_param(project, p)
        params = rank_params(project, params, opts)
        result["params"] = [p.to_dict() for p in params]
        result["buttons"] = check_buttons(project, overrides.get("buttons"))
        result["blocking_loops"] = [
            {"file": m.rel, "line": node.lineno, "function": owner.qualname if owner else None}
            for m in project.modules.values() for node, owner in m.blocking_loops
        ]
    except Exception as exc:  # pragma: no cover - the detector must never crash the demo
        project.errors.append({"file": main, "line": 0, "message": f"detection failed: {exc!r}"})
    # Only report derived values that depend on something we expose.
    names = {p["name"] for p in result["params"]}
    derived = []
    for d in project.derived:
        deps = [n for n in d["depends"] if n in names]
        if deps:
            derived.append({**d, "depends": deps})
    result["derived"] = derived
    result["skipped"] = project.skipped
    result["errors"] = project.errors
    result["notes"] = project.notes
    result["timing_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result


# ---------------------------------------------------------------------------
# Applying values
# ---------------------------------------------------------------------------

def literal_node(value):
    """An AST node for a JSON-ish value (numbers, bools, strings, sequences)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite value")
        return ast.Constant(value)
    if isinstance(value, tuple):
        return ast.Tuple([literal_node(v) for v in value], ast.Load())
    if isinstance(value, list):
        return ast.List([literal_node(v) for v in value], ast.Load())
    raise ValueError(f"cannot write a {type(value).__name__} literal")


class _Replacer(ast.NodeTransformer):
    def __init__(self, replacements):
        self.replacements = replacements
        self.done = set()

    def generic_visit(self, node):
        if isinstance(node, ast.expr) and hasattr(node, "end_lineno"):
            key = (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
            if key in self.replacements:
                self.done.add(key)
                new = literal_node(self.replacements[key])
                return ast.copy_location(new, node)
        return super().generic_visit(node)


def rewrite_source(source, filename, replacements):
    """Parse ``source`` and replace the expressions at the given spans.

    ``replacements`` maps ``(line, col, end_line, end_col)`` to new values.
    Line numbers are preserved, so tracebacks still point at the original
    file -- the displayed code is never edited.
    Returns ``(module_ast, missing_spans)``.
    """
    tree = ast.parse(source, filename=filename)
    if not replacements:
        return tree, []
    rep = _Replacer({tuple(k): v for k, v in replacements.items()})
    tree = rep.visit(tree)
    ast.fix_missing_locations(tree)
    missing = [k for k in rep.replacements if k not in rep.done]
    return tree, missing


def coerce_value(param, value):
    """Validate a value from the page against a parameter's type.

    Raises ``ValueError`` with a readable message when it does not fit.
    """
    typ = param.get("type")
    if typ == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str) and value.lower() in ("true", "1", "yes", "on", "false", "0", "no", "off"):
            return value.lower() in ("true", "1", "yes", "on")
        raise ValueError(f"{param['id']}: expected true/false")
    if typ == "int":
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{param['id']}: expected a whole number") from None
        if not math.isfinite(f):
            raise ValueError(f"{param['id']}: expected a finite number")
        return int(round(f))
    if typ == "float":
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{param['id']}: expected a number") from None
        if not math.isfinite(f):
            raise ValueError(f"{param['id']}: expected a finite number")
        return f
    if typ == "choice":
        choices = param.get("choices") or []
        for c in choices:
            if c == value or str(c) == str(value):
                return c
        raise ValueError(f"{param['id']}: {value!r} is not one of {choices}")
    if typ == "str":
        if not isinstance(value, str):
            value = str(value)
        if len(value) > 2000:
            raise ValueError(f"{param['id']}: text too long")
        return value
    if typ == "tuple":
        if isinstance(value, str):
            value = [v for v in re.split(r"[,\s]+", value.strip("()[] ")) if v]
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{param['id']}: expected a list of numbers")
        n = param.get("length")
        if n and len(value) != n:
            raise ValueError(f"{param['id']}: expected {n} numbers")
        conv = int if param.get("elem_type") == "int" else float
        try:
            out = tuple(conv(round(float(v))) if conv is int else float(v) for v in value)
        except (TypeError, ValueError):
            raise ValueError(f"{param['id']}: expected numbers") from None
        if param.get("default_is_list"):
            return list(out)
        return out
    raise ValueError(f"{param.get('id')}: unsupported type {typ!r}")


def build_argv(params, values, main):
    """Rebuild ``sys.argv`` for argparse parameters.

    Values equal to the default are omitted, so the script's own defaults
    (even computed ones) still apply.
    """
    argv = [main]
    positional = []
    options = []
    for p in params:
        ap = p.get("apply") or {}
        if ap.get("kind") != "argv":
            continue
        pid = p["id"]
        value = values.get(pid, p.get("default"))
        default = p.get("default")
        if ap.get("positional"):
            positional.append((ap.get("order", 0), _argv_str(value)))
            continue
        action = ap.get("action")
        if action == "store_true":
            if value:
                options.append((ap.get("order", 0), [ap["option"]]))
            continue
        if action == "store_false":
            if not value:
                options.append((ap.get("order", 0), [ap["option"]]))
            continue
        if action == "boolean_optional":
            if bool(value) != bool(default):
                opt = ap["option"]
                options.append((ap.get("order", 0), [opt if value else "--no-" + opt.lstrip("-")]))
            continue
        if ap.get("has_default", True) and _same(value, default):
            continue
        opt = ap["option"]
        if isinstance(value, (list, tuple)):
            options.append((ap.get("order", 0), [opt] + [_argv_str(v) for v in value]))
        elif opt.startswith("--"):
            # ``--opt=value`` is unambiguous even for negative numbers
            options.append((ap.get("order", 0), [f"{opt}={_argv_str(value)}"]))
        else:
            options.append((ap.get("order", 0), [opt, _argv_str(value)]))
    for _, v in sorted(positional):
        argv.append(v)
    for _, toks in sorted(options):
        argv.extend(toks)
    return argv


def _argv_str(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _same(a, b):
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-15)
        except (TypeError, ValueError):
            return False
    return a == b
