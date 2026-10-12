# Test fixtures

Small projects for the end-to-end checklist (`../e2e_checklist.py`). They are
not published with the site. The checklist's local server exposes this folder
at `/_pyd_tests/fixtures/`, so with
`python tests/demonstrator/e2e_checklist.py --site quick-start/output --serve`
each one opens at `/demonstrator.html?demo=/_pyd_tests/fixtures/<name>/`.

| Fixture | What it exercises |
|---|---|
| `latency_probe` | prints the wall-clock time at which a live value reaches `update()` |
| `pause_loop` | a `plt.pause()` loop: pause, step, live values, graceful Stop (and `?jspi=0`) |
| `busy_loop` | an endless loop that never yields: timeout warning, Stop by terminating the worker |
| `module_error` | an exception in a helper module after a few frames; no file index (`.noindex`), so imports are followed |
| `syntax_error` | a syntax error in an imported module: detection still finds `main.py`'s controls |
| `missing_package` | an import that exists nowhere (`micropip: false` keeps the test offline) |
| `micropip_fallback` | `tabulate`, a pure-Python package from PyPI |
| `multi_figure` | an animated and a static figure: figure tabs |
| `widgets` | matplotlib `Slider` and `Button` driven by forwarded clicks |
| `anim_save` | `anim.save()` is skipped; a finite animation finishes |
| `explicit_style` | script-chosen colours survive dark mode |
| `editable` | edit `model.py` in the browser, re-run, re-detect, reset |
| `bundle_demo` | the project as one deflated ZIP with a wrapper folder, junk entries and a `../` entry |
| `hostile_markdown` | an explainer full of XSS and CSS payloads and broken `demo:` links |
| `bad_manifest` | invalid JSON in `demo.json` |
| `unsafe_manifest` | `demo.json` listing a file outside the demo folder |

`../browser/unit.html` holds the browser unit tests (sanitiser, Markdown
pipeline, manifests, paths, ZIP), and `../browser/foreign_parent.html` is the
cross-origin embedding page used by the `embed-cross-origin` check.
