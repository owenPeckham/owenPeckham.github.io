# Python demonstrator

Interactive pages that run your Python projects in the visitor's browser. Each
page has three panes: a Markdown **explainer**, the project's **code**, and a
live matplotlib **simulator** with controls the demonstrator works out from the
code itself. Python runs in [Pyodide](https://pyodide.org) inside a Web Worker,
so the site stays fully static and GitHub Pages needs no backend.

Your scripts need **no demonstrator-specific code**. Anything that runs with
`python main.py` and draws with matplotlib should work as it is. Optional
`# demo:` comments, a `demo.json` file and a small helper module let you tune
the result without changing what the script does.

- [Quick start](#quick-start)
- [What scripts can do](#what-scripts-can-do)
- [How the controls are chosen](#how-the-controls-are-chosen)
- [Tuning: demo.json, # demo: hints, demo_params](#tuning)
- [The explainer](#the-explainer)
- [Page options](#page-options)
- [Embedding demos in other pages](#embedding-demos-in-other-pages)
- [Layout, keyboard and accessibility](#layout-keyboard-and-accessibility)
- [Light and dark themes](#light-and-dark-themes)
- [Configuration and dependencies](#configuration-and-dependencies)
- [The build plugin](#the-build-plugin)
- [How it works](#how-it-works)
- [Security](#security)
- [Performance](#performance)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

---

## Quick start

1. **Add the project.** Copy it into its own folder under
   `quick-start/content/demos/`, for example
   `quick-start/content/demos/pendulum/main.py` plus any modules, packages and
   data files it uses. The folder is published as it is.
2. **Add a page** (`quick-start/content/pages/pendulum.md`):

   ```
   Title: Double pendulum
   Template: demonstrator
   Demo: /demos/pendulum/
   Summary: Chaotic motion of two linked pendulums.
   Status: hidden
   ```

   `Status: hidden` keeps the page out of the menus, so you can link to it
   from a project write-up. Any text in the page body appears above the demo.
   You can skip this step: every demo also works at
   `/demonstrator.html?demo=/demos/pendulum/`.
3. **Optionally** add `explainer.md` (the left pane) and `demo.json`
   (title, extra files, control tweaks, presets) to the project folder.
4. Preview with `make devserver` as usual and open the page. Deploy with
   `make github`. Nothing else has to be built or installed.

Six examples are included, each with an explainer that explains the
feature it demonstrates:

| Example | Shows |
|---|---|
| [/examples/oscillator/](quick-start/content/examples/oscillator/) | constants become live sliders; presets; `demo:` links; KaTeX |
| [/examples/heat_map/](quick-start/content/examples/heat_map/) | a package with relative imports and a CSV file; restart parameters |
| [/examples/cli_style/](quick-start/content/examples/cli_style/) | an `argparse` script; values go in through `sys.argv`; blitting |
| [/examples/config_style/](quick-start/content/examples/config_style/) | a `@dataclass` config and `main()` keyword defaults |
| [/examples/with_hints/](quick-start/content/examples/with_hints/) | `# demo:` hints, `parameter_overrides`, a button, the guided tour |
| [/examples/static_plot/](quick-start/content/examples/static_plot/) | a static figure re-drawn when a dropdown changes |

`/examples/embeds.html` embeds three demos in an ordinary page, and
`/demonstrator.html` lists every demo the build found.

## What scripts can do

The project is copied into Pyodide's virtual file system with its folder
layout intact. The working directory is the demo folder, and both the demo
folder and the main file's folder are on `sys.path`. The main file runs as
`__main__`. In practice:

- **Figures:** `plt.show()`, several figures (they get tabs), static figures,
  `FuncAnimation` and `ArtistAnimation` (with or without `blit=True`),
  `plt.pause()` / `fig.canvas.flush_events()` / `time.sleep()` loops, figure
  and axes resizing, and matplotlib's own widgets (`Slider`, `Button`,
  `CheckButtons`...) and mouse events, which receive the visitor's clicks.
- **Projects:** several modules, packages with relative imports, and data files
  read with relative paths (`open("data/values.csv")`). A project can also be
  published as a single ZIP file (`"bundle"` in demo.json).
- **Command-line scripts:** `argparse` options become controls, and values are
  passed through a rebuilt `sys.argv`.
- **Packages:** anything in the
  [Pyodide distribution](https://pyodide.org/en/stable/usage/packages-in-pyodide.html)
  (numpy, scipy, pandas, matplotlib, sympy, networkx, scikit-learn, ...) is
  loaded automatically from the imports. Pure-Python packages from PyPI are
  installed with micropip.
- **Friendly failures:** `anim.save(...)`, `to_html5_video()`, `input()`, GUI
  backends (`matplotlib.use("TkAgg")`) and modules a browser can't provide
  (`tkinter`, `multiprocessing`, `torch`, ...) give a clear message in the
  console instead of a crash.

Limits: no threads or subprocesses, no GUI toolkits, no native extensions
that aren't in Pyodide, no sockets (so no `requests`), and files the script
writes only exist until the page is closed. Python in WebAssembly is roughly
1.5 to 3 times slower than on a desktop. A script that computes for a long
time before drawing doesn't freeze the page: a warning appears after 20 s and
**Stop** always works.

## How the controls are chosen

When Python has loaded, `pyd_detect.py` reads the project's source with Python's
`ast` module. Nothing is executed at this stage. It looks for values in this
order:

1. `argparse` options (`parser.add_argument("--freq", type=float, default=2.0, help=...)`);
2. a top-level settings dict (`CONFIG`, `PARAMS`, `SETTINGS`, ...), `@dataclass`
   and `NamedTuple` config classes, and the keyword defaults of the entry
   function (`main`, `run`, `simulate`, ... or whatever is called under
   `if __name__ == "__main__":`);
3. top-level constants such as `FREQ = 2.0`. UPPER_CASE names come first;
   lower-case module literals go to an "Other detected values" group;
4. values declared with the optional `demo_params` helper, which take
   precedence over everything above.

Each value gets a control that matches its type: a slider plus number box for
numbers, a switch for booleans, a dropdown for `choices` or for strings used as
keys of a dict, a text box for other strings, and grouped boxes for short
tuples. Labels, units and help text come from the name (`FREQ_HZ`,
`LENGTH_MM`), the trailing comment (`# natural frequency [Hz]`), argparse
`help=` and docstrings. Ranges are inferred from the default and the name:
counts and grid sizes become integers, ratios and probabilities stay within 0
to 1, seeds get a wide integer range, and ranges that span several decades use
a log scale. Paths, file names, values computed from other values
(`OMEGA = 2*np.pi*FREQ`) and huge literals are skipped. **Why these controls?**
under the panel lists what was skipped and why.

**Live or restart.** Each control is marked with a badge:

- **Live**: the value is read by code that runs again and again, such as an
  animation `update()` function, an event handler or the body of a
  `plt.pause()` loop. A change is written straight into the running module
  between two frames and takes effect in the next frame without a restart.
  Changes from a dragged slider are merged so that only the latest value is
  sent each frame.
- **Restarts**: the value is used once at start-up, for example to size an
  array, build the figure or set the animation interval. Changing it re-runs
  the script in the same, already loaded Python. A re-run takes well under a
  second, and the old frame stays on screen until the new one is drawn. The
  new value is injected without touching your files: the literal is replaced
  in the parsed source at run time, or `sys.argv` is rebuilt for argparse
  scripts. Line numbers in tracebacks still match, and the code pane always
  shows the file exactly as written.

Hover over a badge to see the reason. Values used only for cosmetic things at
start-up, such as a title or a `print`, don't force a restart.

## Tuning

You can adjust the result in three ways. None of them changes what the script
does when you run it yourself.

### `# demo:` hints (in the source, as comments)

```python
FREQ = 1.0        # demo: range=0.2..4 step=0.05 label="Natural frequency" unit=Hz mode=live
SPEED = 1.0       # demo: range=0.25..4 scale=log
METHOD = "rk4"    # demo: choices=euler|rk2|rk4
WINDOW = 8.0      # demo: hide
DT = 1 / 240      # demo: ignore
```

Keys: `range=lo..hi`, `min`, `max`, `step`, `label`, `unit`, `help`, `group`,
`mode=live|restart`, `scale=log|linear`, `choices=a|b|c`, `type`, `default`,
`order`. Flags: `hide` (keep the value but don't show its control) and `ignore`
(not a parameter). Quote values that contain spaces. Unknown keys are reported
in the browser console.

### `demo.json` (next to `main.py`; every key is optional)

```json
{
  "title": "Damped, driven oscillator",
  "description": "A mass on a spring, integrated with RK4 and animated live.",
  "main": "main.py",
  "files": ["physics.py", "data/*.csv", "pkg/**/*.py"],
  "explainer": "explainer.md",
  "packages": ["sympy"],
  "parameter_overrides": {
    "DRIVE_FREQ": {"mode": "restart"},
    "physics.py:NONLINEARITY": {"label": "Duffing stiffness", "min": -0.5, "max": 2, "group": "Physics"},
    "OMEGA": {"min": 0, "max": 20, "default": 6.28}
  },
  "presets": {
    "resonance": {"label": "Resonance", "description": "Drive at the natural frequency.",
                  "values": {"DAMPING": 0.05, "DRIVE": 0.3, "DRIVE_FREQ": 1.0}}
  },
  "buttons": [{"label": "Kick the mass", "call": "kick", "help": "Adds a push"}],
  "back": {"label": "Back to the project", "url": "/projects/oscillators.html"},
  "layout": "auto", "panes": ["explainer", "sim", "code"], "fps": 30, "quality": "auto",
  "editable": false, "autorun": true, "load": "visible", "timeout": 20, "theme": "site",
  "toc": "auto", "tour": "off", "micropip": true, "max_controls": 14, "max_other": 8
}
```

| Key | Meaning |
|---|---|
| `title`, `description` | Page title and summary. They are used when the page itself doesn't set them, and in the `/demonstrator.html` catalogue. |
| `main` | The file to run (default `main.py`). |
| `files` | Files to load, as paths or globs (`*`, `**`). By default every file in the folder is loaded. The build writes a file index, and without one the page follows the imports. |
| `bundle` | A `.zip` with the whole project, used instead of separate files. A single top-level folder inside it is unwrapped. |
| `explainer` | The Markdown file for the left pane (default `explainer.md`, then `README.md`), or `false` for none. |
| `packages` | Extra packages to install. Packages for normal imports are found automatically. |
| `parameter_overrides` | Per-parameter changes, keyed by the parameter id (`NAME`, or `file.py:NAME` for other files). Fields: `label`, `unit`, `help`, `group`, `type`, `min`, `max`, `step`, `range`, `scale`, `choices`, `default`, `order`, `mode`, `hide`. A key the detector skipped (such as an expression) is added as a new control. |
| `presets` | Named sets of values, offered in a dropdown and through `demo:preset=` links. |
| `buttons` | Buttons that call a top-level function of the script that takes no arguments. The figure is redrawn afterwards; with `"after": "restart"` the script is re-run instead. A button whose function doesn't exist is shown disabled, with the reason. |
| `back` | Where the back link points (same site only). |
| `layout`, `panes`, `fps`, `quality`, `editable`, `autorun`, `load`, `timeout`, `theme`, `toc`, `micropip` | Display options, also settable from the URL ([Page options](#page-options)). |
| `tour` | `"auto"` switches the guided tour on when the page opens. |
| `max_controls`, `max_other` | How many detected values are shown before the rest go under "More". |
| `version` | A cache-busting string, only needed when you serve demos without the build plugin. |

Paths in `demo.json` must stay inside the demo folder. Absolute paths, `..`
and URLs are rejected with a clear error.

### The `demo_params` helper (optional)

Use it when you want to be sure a value is live, or to react when the
visitor changes it. Copy `themes/owenpeckham/static/demonstrator/py/demo_params.py` next to
your script if you also want the declarations to work at home, or use the
fallback pattern so the script runs unchanged without it:

```python
try:
    from demo_params import params, on_change
except ImportError:                       # plain Python: just the defaults
    params = {"freq": 2.0, "method": "rk4"}
    def on_change(name, callback):
        pass

def update(frame):
    w = 2 * np.pi * params["freq"]        # always the current slider value
```

## The explainer

`explainer.md` is ordinary Markdown with a few extras:

- **Front matter** (optional): `title`, `summary`, `toc: true|false|auto` (by
  default a contents box appears when there are three or more sections),
  `tour: auto`.
- **Standard features:** tables, footnotes, fenced code with syntax
  highlighting, `$inline$` and `$$display$$` maths (KaTeX, loaded only when
  there is maths), and the site's prose styles.
- **Figures:** an image on its own line becomes a figure, and its title becomes
  the caption: `![Mass and spring](figures/mass-spring.svg "The mass–spring–damper")`.
  Paths are relative to the explainer.
- **Callouts:** `> [!NOTE]`, `> [!TIP]`, `> [!IMPORTANT]`, `> [!WARNING]`, `> [!CAUTION]`.
- **Raw HTML** is allowed but sanitised (see [Security](#security)).

**`demo:` links** control the simulator from the text:

| Link | Effect |
|---|---|
| `[heavy damping](demo:set?DAMPING=0.9&FREQ=2)` | set one or more values |
| `[resonance](demo:preset=resonance)` | apply a preset from demo.json |
| `[the integrator](demo:code=physics.py:12-30)` | show and highlight lines in the code pane |
| `[frequency](demo:param=FREQ)` | scroll to and highlight that control |
| `demo:run`, `demo:pause`, `demo:step`, `demo:restart`, `demo:stop`, `demo:reset` | playback and reset to defaults |

Links are treated as data. They are checked against the detected parameters,
presets and files, and a link that doesn't match anything, such as a typo or a
missing file, shows as plain text with a warning in the browser console.

**Guided tour:** put a comment such as `<!-- step: preset=resonance code=main.py:12-20 -->`
right after a heading. The heading gets a **Show** button, and with **Tour**
switched on the step's actions run as the reader scrolls to it.

**Deep links:** every heading has an anchor, so `page.html#damping-regimes`
opens the explainer at that section.

## Page options

You can set these as page metadata (the `Demo:`, `Panes:` ... lines), in
demo.json, or in the URL. URL values win. Invalid values fall back to the
defaults.

| Option | Values | Default |
|---|---|---|
| `demo` | a demo folder on this site | (none) |
| `script`, `files`, `explainer` | a bare script plus extra files: `?script=/x/run.py&files=util.py,data/a.csv` | (none) |
| `panes` | any of `explainer,sim,code` | all three |
| `layout` | `auto`, `split`, `columns`, `stacked`, `tabs` | `auto` (split, or tabs below 860 px) |
| `load` | `visible` (start Python when the demo nears the screen), `click` (wait for Run), `eager` | `visible` |
| `autorun` | run as soon as Python is ready | `true` |
| `fps` | frame-rate cap, 1–60 | `30` |
| `quality` | `auto`, `low`, `medium`, `high`: figure resolution against screen pixels | `auto` |
| `editable` | allow editing the code and re-running | `false` |
| `theme` | `site`, `light`, `dark`, `auto` | `site` |
| `toc` | explainer contents box: `auto`, `true`, `false` | `auto` |
| `height` | height in px of the demo area (split/columns layouts) | fits the window |
| `p.NAME=value` | start with a parameter value (the **Link** button writes these) | |
| `preset` | start with a preset | |
| `back`, `back_label` | back-link target (same site) and text | |
| `embed` | `1` drops the site header and footer (used by embedded demos) | |
| `jspi=0` | testing only: behave like a browser without JSPI | |

Page metadata only: `Metalabel:` (the small caption above the title) and
`Navactive:` (which top-menu item to highlight, default `projects`).

## Embedding demos in other pages

In any page or article on the site:

```html
<py-demo demo="/examples/oscillator/" panes="sim" height="520"></py-demo>
```

The template adds the small `embed.js` script to any page whose content
contains `<py-demo`. The element creates a lazily loaded `<iframe>` of the
demonstrator in embed mode. The iframe resizes to fit its content and follows
the host page's theme. Both work through `postMessage`, with origins checked
on both sides.

The element accepts the attributes from [Page options](#page-options)
(`demo`, `script`, `files`, `explainer`, `panes`, `layout`, `load`,
`autorun`, `fps`, `quality`, `editable`, `theme`, `toc`), plus `height` (the
initial height; add `fixed` to keep it), `title` (for screen readers) and
`page` (a different demonstrator page on this site). A plain
`<iframe data-pyd-embed src="/demonstrator.html?demo=...&embed=1">` gets the
same resizing when `embed.js` is on the page.

Other websites can embed demos with a plain iframe. They get resizing and theme
syncing only if their origin is added to `EMBED_PARENT_ORIGINS` in `config.js`.

## Layout, keyboard and accessibility

- Drag the dividers, or focus one and use the arrow keys, Page Up/Down,
  Home or End. Double-click a divider to reset it.
- Each pane can be collapsed or maximised (Esc restores the layout). Sizes,
  collapsed panes and the last tab are remembered per demo in
  `localStorage`.
- Below 860 px the panes become tabs. A **mini-player** with the live figure
  and play/pause/step/restart buttons follows you while you read the
  explainer.
- The figure canvas can be focused: Space pauses or resumes, → steps one
  frame, R restarts.
- Panes are labelled regions. Every button and input has an accessible name,
  slider values are announced with their units, and errors and status changes
  are announced politely. Animations don't start by themselves when the visitor
  prefers reduced motion.

## Light and dark themes

The demonstrator takes every colour from the site's own CSS custom properties
(`--bg`, `--ink`, `--text`, `--muted`, `--rule`, `--surface`, `--accent`), so it
follows `site.css`. The site currently has a light design only. A dark
palette is built in for `?theme=dark` and for dark embedding pages, keyed on
the same `<html data-theme="dark">` hook the PCLS tool uses. If `site.css`
gains a dark palette, the demonstrator follows it automatically.

Matplotlib colours (backgrounds, text, ticks, grid, legend) are given to the
script as rcParams before it runs. Anything the script sets explicitly wins. A
figure whose background the script chose keeps text colours that read on that
background. When the page theme changes while a demo runs, colours that still
equal the old theme's defaults are swapped on the live figures.

## Configuration and dependencies

`themes/owenpeckham/static/demonstrator/js/config.js` holds everything that
names a version or a URL:

| Constant | Purpose |
|---|---|
| `PYODIDE_VERSION` | Pyodide release (0.29.5: Python 3.13, matplotlib 3.8.4) |
| `CDN_BASE` | `https://cdn.jsdelivr.net`. Change this one constant to self-host. |
| `LIBS` | markdown-it 14.3.2, markdown-it-footnote 4.0.0, DOMPurify 3.4.16, KaTeX 0.16.11 (the PCLS page uses the same version), highlight.js 11.12.0. All pinned to exact versions. Those loaded with `<script>`/`<link>` tags (markdown-it, its footnote plugin, the KaTeX stylesheet) carry SRI hashes; the ES modules (DOMPurify, KaTeX, highlight.js) are imported by exact version URL. |
| `ALLOWED_ORIGINS` | Other origins demo files and explainer images may come from (empty: this site only). |
| `EMBED_PARENT_ORIGINS` | Other sites allowed to embed demos and talk to them. |
| `DEFAULTS`, `NARROW_BREAKPOINT`, `MAX_FRAME_PIXELS` | Display defaults, the tabs breakpoint and the frame-size budget. |

There is no build step: plain HTML, CSS and ES modules. Pyodide (about 20 MB
with numpy and matplotlib) is downloaded the first time a visitor runs a demo
and cached by the browser afterwards. It is never loaded on pages without a
demo.

**Upgrading Pyodide:** change `PYODIDE_VERSION`, then run the tests below,
especially the Pyodide unit tests and the browser checklist, because the
matplotlib version changes with Pyodide.

**Self-hosting** (for example if a network blocks jsdelivr): copy the Pyodide
distribution (or just `pyodide.js`, `pyodide.asm.*`, `python_stdlib.zip`,
`pyodide-lock.json` and the wheels you need) and the five libraries to your
own server, keeping the same paths, and set `CDN_BASE`.

## The build plugin

`quick-start/plugins/demonstrator/` is enabled in `pelicanconf.py` and runs on
every build:

- writes `demo.index.json` in each demo folder under `demos/` and `examples/`
  (`DEMONSTRATOR_ROOTS`): the file list with hashes. The page uses it to
  expand globs and to cache-bust each file (`main.py?v=<hash>`), so edits show
  up at once while unchanged files stay cached;
- writes `index.json` catalogues for `/demonstrator.html`;
- warns at build time about invalid `demo.json` files or a missing main file;
- writes `.nojekyll`. Without it GitHub Pages would drop `__init__.py` and
  other files starting with `_`;
- sets `DEMONSTRATOR_VERSION` (a hash of the demonstrator's files) to
  cache-bust its own JS and CSS.

`pelicanconf.py` also copies `demos/` and `examples/` through as static files
(so `.py` and `.md` files there are never treated as articles) and ignores
`__pycache__`, `.pyc` and dotfiles. The output is identical between builds
when nothing changed.

## How it works

```
page (main thread)                         worker (Pyodide)
──────────────────                         ────────────────
explainer: markdown-it → DOMPurify         pyd_detect.py   parameter detection (ast only)
code pane, controls, layout                pyd_runtime.py  files, packages, runs __main__,
Player: paces animation ticks  ── tick ──▶                 live writes, restarts, errors
canvas  ◀── RGBA frame (transferred) ────  pyd_backend.py  matplotlib backend (Agg) whose
                                                           timers are driven by the page
```

- **Frames:** figures are drawn with Agg at the canvas's real pixel size
  (device pixel ratio, `quality` and `MAX_FRAME_PIXELS` taken into account),
  and the RGBA buffer is transferred to the page without copying. The page
  paces the animation using the script's own interval, the speed control and
  the fps cap. Only one tick is in flight at a time, so control messages are
  handled between frames, and when drawing is slower than the interval frames
  are skipped rather than queued. The measured frame rate is shown under the
  figure.
- **`plt.pause()` loops** run the script synchronously. With WebAssembly JSPI
  (JavaScript Promise Integration) the script is suspended at each `pause`, so
  pause, step, Stop and live values keep working. Chrome and Edge have shipped
  JSPI since version 137. At the time of writing Firefox (planned for version
  153) and Safari (27 beta) were still rolling it out. Without JSPI these loops
  still animate, but their controls restart the script and Pause is
  disabled.
- **Stop** asks the script to stop. If it doesn't respond (an endless loop),
  the worker is terminated and a fresh, warm one is started in the background.
- **Message protocol:** documented at the top of `js/worker.js`.

Files (all under `themes/owenpeckham/`):

| File | Role |
|---|---|
| `templates/demonstrator.html` | page template; extends `base.html`, so the site header, nav, footer, back link and stylesheet are reused unchanged |
| `templates/base.html` | two additions only: a `head` block and the conditional `embed.js` include |
| `static/demonstrator/css/demonstrator.css` | the demonstrator's own styles, built on the site's tokens |
| `static/demonstrator/js/page.js`, `app.js` | entry point and application |
| `js/manifest.js`, `util.js`, `zip.js` | options, demo.json, file loading, path checks, ZIP reading and writing |
| `js/explainer.js`, `sanitize.js`, `demolinks.js`, `highlight.js` | explainer pipeline |
| `js/layout.js`, `simulator.js`, `controls.js`, `codepane.js`, `console.js`, `theme.js` | the panes |
| `js/runtime.js`, `worker.js` | worker client and frame pacing; the worker |
| `js/embed.js` | `<py-demo>` element |
| `static/demonstrator/py/pyd_detect.py`, `pyd_runtime.py`, `pyd_backend.py`, `demo_params.py` | the Python side |

## Security

The project files, explainer, demo.json and URL parameters are all treated as
untrusted data:

- No `innerHTML` with any content, no `eval`, no `new Function`. Elements
  are built with DOM calls. highlight.js output is parsed in an inert document,
  and only its text and class names are copied.
- Explainer HTML goes through DOMPurify with a strict allowlist: no scripts,
  styles, forms, iframes (unless a demo opts in, and then only same-origin
  and sandboxed), `<object>`, `<base>` or `<meta>`. Event-handler and style
  attributes are removed, and only http(s), mailto, tel, relative and `demo:`
  URLs are allowed. Images load only from this site or `ALLOWED_ORIGINS`. External
  links open in a new tab with `rel="noopener noreferrer"`. KaTeX runs with
  `trust: false`.
- `demo:` links, presets and URL values are parsed as data and checked against
  the detected parameters before use. Injected values become literals in the
  parsed source, never code.
- Demo files, explainers and images load only from the same origin or the
  allowlist. Paths with `..`, absolute paths, schemes or backslashes are
  rejected, including inside ZIP bundles.
- `postMessage` in both directions checks the origin, and the message source
  on the parent side. Messages are sent only to allowed origins and contain
  only sizes, theme names and greetings.
- Python runs in the visitor's browser, sandboxed by WebAssembly and the
  worker, with no access to the page.

## Performance

The target is 20–30 fps. Moving a frame between Python and the page costs a
few milliseconds. Almost all the time goes into matplotlib redrawing the
figure. These numbers were measured in headless Chromium on a slow 2-core cloud
machine, where the same oscillator redraw takes 47 ms in desktop CPython, so
Python in the browser is only about 30 % slower than native here. A typical
laptop does better:

| Example | Time per frame in Python | Measured |
|---|---|---|
| cli_style (blitting) | ~2 ms | 30 fps (the default cap) |
| heat_map (`imshow`) | ~36 ms | 20 fps |
| oscillator (full redraw of two axes and text) | ~61 ms | 14 fps |

To make a demo faster, use `blit=True` in `FuncAnimation`, plot fewer points,
use smaller figures, or set `quality=low`.

## Testing

Run these from the repository root:

| What | Command | Needs |
|---|---|---|
| Detection and runtime unit tests (CPython) | `python -m unittest tests/demonstrator/test_detect.py tests/demonstrator/test_runtime.py` | numpy, matplotlib |
| The same tests inside real Pyodide | `node tests/demonstrator/run_pyodide_tests.mjs`, after `npm install pyodide@0.29.5` (in the repository, or elsewhere with `PYODIDE_DIR` pointing at its `node_modules/pyodide`; don't commit `node_modules`) | Node 18+ |
| `demo:` link parser | `node --test tests/demonstrator/js/demolinks.test.mjs` | Node 18+ |
| Sanitiser, Markdown pipeline, manifests, paths, ZIP (real browser) | `/_pyd_tests/browser/unit.html` on the test server (also run by the checklist) | |
| End-to-end checklist (39 checks in headless Chromium) | `python tests/demonstrator/e2e_checklist.py --build` or `--site quick-start/output` | `pip install playwright` and `python -m playwright install chromium` |

`python tests/demonstrator/e2e_checklist.py --site quick-start/output --serve`
serves the site and the test fixtures for manual testing. The fixtures in
`tests/demonstrator/fixtures/` (errors in modules, missing packages, endless
loops, `plt.pause` loops, widgets, several figures, a hostile explainer, a
ZIP bundle...) can be opened at `/demonstrator.html?demo=/_pyd_tests/fixtures/<name>/`.
The latest results are in [tests/demonstrator/CHECKLIST.md](tests/demonstrator/CHECKLIST.md).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Python could not start" | The CDN is blocked or offline. Try again, or self-host (see above). |
| "Project file not found: x.py" | The file isn't in the demo folder or in `files`, or the site was built without the plugin. |
| A value you expected is missing | It was skipped. Open **Why these controls?** Add it with a `parameter_overrides` entry or a `# demo:` hint. |
| A slider restarts the script, but you'd like it live | The value is read once at start-up. Reading it inside the update function, or using `demo_params`, makes it live. `mode=live` forces it, but only takes effect where the script reads the value again. |
| `ModuleNotFoundError` | The package has no browser build. The console names it and says why. |
| A demo works locally but not on GitHub Pages | Check that `.nojekyll` reached `docs/` (the build writes it), then hard-refresh with Ctrl+Shift+R. |
| Old code still runs after a change | Files are cache-busted by hash on every build. Make sure the site was rebuilt. |
