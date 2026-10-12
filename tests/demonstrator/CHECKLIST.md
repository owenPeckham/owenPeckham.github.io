# Demonstrator test checklist and results

This file records the checklist that was actually run against the demonstrator,
what it covered, and what could not be tested. Re-run everything with the
commands in [DEMONSTRATOR.md](../../DEMONSTRATOR.md#testing).
`python tests/demonstrator/e2e_checklist.py --report FILE` regenerates the
browser table below.

## Summary (11 October 2026)

| Suite | Result |
|---|---|
| Detection and runtime unit tests, CPython 3.13.16 with matplotlib 3.11.2 | 97 passed |
| Same tests inside Pyodide 0.29.5 (Python 3.13.2, matplotlib 3.8.4) under Node 22 | 97 passed |
| `demo:` link parser, `node --test` | 10 passed |
| Browser unit tests: sanitiser with real DOMPurify, Markdown pipeline, manifests, paths, ZIP | 18 passed |
| End-to-end checklist, headless Chromium 141 | 39 of 39 passed |

## Environment of the recorded run

- Linux x86_64 cloud sandbox with 2 slow vCPUs, Python 3.13, Playwright 1.56,
  headless Chromium 141.0.7390.37 (JSPI available), Node 22.22.
- The site was built with Pelican 4.12 from `quick-start/` and served from a
  local HTTP server together with `tests/demonstrator/fixtures/`.
- The sandbox could not reach cdn.jsdelivr.net, so the checklist served the
  exact pinned files (Pyodide 0.29.5 and the npm packages, from their release
  archives) from a local mirror (`--cdn-mirror`). The SRI hashes in
  `config.js` matched those files, and jsdelivr serves the same npm files.
  PyPI was reached through Python (`--python-fetch`) because the browser had
  no direct network route.

## End-to-end checklist (headless Chromium)

| # | Check | Area | Result | Time | Details |
|---|---|---|---|---|---|
| 1 | **page-shell**: Example page keeps the site header, nav, footer, back link, title, meta and stylesheet; three panes render | Page | PASS | 11.9 s | 1 site stylesheet(s) reused; files ['main.py', 'physics.py'] |
| 2 | **generic-page**: Bare /demonstrator.html lists the demos; ?demo= loads one with its manifest title | Page | PASS | 8.1 s | 6 demos listed |
| 3 | **bad-input**: Off-site demos, odd schemes, traversal and broken manifests give clear errors and fetch nothing | Security | PASS | 2.2 s | 7 error cases |
| 4 | **lazy-load**: load=click: no Python download until Run is pressed | Page | PASS | 12.0 s | no download before Run |
| 5 | **detect-controls**: Oscillator: controls detected from plain constants, live vs restart, 'Why these controls?' | Parameters | PASS | 6.9 s | FREQ:live, DAMPING:live, DRIVE:live, DRIVE_FREQ:live, SPEED:live, WINDOW:live, DT:live, TRAIL:restart, INTERVAL_MS:restart, physics.py:NONLINEARITY:live |
| 6 | **live-latency**: A live change reaches the running script within about one frame, without a restart | Parameters | PASS | 12.0 s | latency to the script: median 12 ms, max 34 ms (frame interval 33 ms) |
| 7 | **live-coalesce**: Dragging a live slider sends coalesced updates; the last value wins | Parameters | PASS | 12.6 s | 40 input events -> 8 parameter messages; final value applied |
| 8 | **restart-warm**: A restart parameter re-runs in the warm worker (no Pyodide reload); the shown code is unchanged; URL updated | Parameters | PASS | 11.5 s | change to first frame of the re-run: 0.11 s, same worker |
| 9 | **url-state**: Values in the URL are applied on load; the Link button copies the current setup | Parameters | PASS | 7.0 s | p.FREQ=1.5&p.DAMPING=0.5&p.TRAIL=100 |
| 10 | **preset-links**: demo:preset and demo:set links apply values; demo:code shows and highlights lines | Explainer | PASS | 7.2 s | preset -> {'DAMPING': '0', 'DRIVE': '0'}; highlighted lines 7-12 |
| 11 | **pause-step**: Pause freezes frames, Step advances exactly one, Resume continues | Runtime | PASS | 12.3 s | ok |
| 12 | **stop-busy-loop**: A script that never yields: timeout warning, Stop terminates the worker, Run works again | Runtime | PASS | 18.0 s | terminate + respawn twice |
| 13 | **pause-loop-jspi**: plt.pause() loop: frames stream; pause, step, live values and a graceful Stop work (JSPI) | Runtime | PASS | 13.6 s | graceful stop, worker kept |
| 14 | **pause-loop-nojspi**: Same loop in a browser without JSPI (?jspi=0): frames still stream, loop parameters become restarts, Stop works | Runtime | PASS | 21.1 s | workers used: 3 |
| 15 | **module-error**: An exception in a helper module names the file and line, highlights it, and pauses (files found by import scanning) | Runtime | PASS | 13.3 s | ZeroDivisionError in helpers.py line 8: integer division or modulo by zero |
| 16 | **syntax-error**: A module with a syntax error: detection reports it and still finds main.py's controls; the run shows file and line | Runtime | PASS | 12.3 s | SyntaxError in broken.py line 4: expected ':' (broken.py, line 4) |
| 17 | **detect-crash**: If the detector itself fails, the demo still runs without controls | Runtime | PASS | 10.6 s | ran without controls |
| 18 | **missing-package**: An import that exists nowhere is reported clearly | Runtime | PASS | 12.4 s | reported |
| 19 | **micropip**: A pure-Python package Pyodide lacks (tabulate) is installed from PyPI automatically | Runtime | PASS | 11.8 s | installed from PyPI |
| 20 | **multi-figure**: Two figures get tabs; switching shows the other figure; the animated one keeps running | Runtime | PASS | 10.7 s | tabs ['Wave', 'Histogram'] |
| 21 | **widgets**: matplotlib Slider and Button widgets respond to clicks forwarded from the canvas | Runtime | PASS | 12.7 s | slider -> 3.0 |
| 22 | **static-figure**: Static figure: shown once, Run (not Stop) offered, a dropdown change re-runs the script | Runtime | PASS | 9.8 s | re-ran on change |
| 23 | **anim-save-finish**: anim.save() is skipped with a friendly note; a finite animation ends in 'finished' and can be replayed | Runtime | PASS | 13.6 s | finished at frame 41 |
| 24 | **bundle**: A project shipped as one ZIP (deflated, wrapped in a folder) loads; junk and ../ entries are dropped | Projects | PASS | 12.4 s | files ['data/values.csv', 'main.py', 'pkg/__init__.py', 'pkg/util.py'] |
| 25 | **editable**: Editing a module and re-running re-detects its parameters; Reset restores the original | Projects | PASS | 11.8 s | edit -> 3 -> reset -> 2 |
| 26 | **fps**: Measured frame rate is shown (full redraw and blitting examples) | Performance | PASS | 45.9 s | oscillator: 14 fps (68 ms/tick); cli_style (blit): 30 fps (2 ms/tick); heat_map: 20 fps (35 ms/tick) |
| 27 | **explainer-hostile**: A hostile explainer (XSS, mXSS, javascript:/data: links, KaTeX \href, CSS) renders inert; bad demo: links degrade to text | Security | PASS | 11.9 s | 7 demo: links degraded, 3 images blocked |
| 28 | **unit-browser**: Browser unit tests: sanitiser (real DOMPurify), Markdown pipeline, demo: links, manifests, paths, ZIP | Security | PASS | 0.3 s | 18/18 passed |
| 29 | **unit-node**: Node unit tests for the demo: link parser (node --test) | Security | PASS | 0.2 s | 10 passed |
| 30 | **deep-link**: A #heading in the URL scrolls the explainer to that section (split layout and phone-width tabs) | Explainer | PASS | 5.7 s | #equation-of-motion at 8px from the top of the explainer; at phone width 14px below the tab bar |
| 31 | **layout-resize-persist**: Dividers drag (mouse and keyboard); sizes and collapsed panes persist; Maximise and Esc work | Layout | PASS | 9.9 s | explainer 514px -> 669px, persisted |
| 32 | **layout-options**: panes= and layout= (split, columns, stacked, tabs, single) are honoured | Layout | PASS | 2.0 s | panes=sim,code -> split, layout=columns -> columns, layout=stacked -> stacked, layout=tabs -> tabs, panes=sim -> single |
| 33 | **narrow-tabs-mini**: Phone width: tabs; the mini-player shows the live figure and controls playback from other tabs | Layout | PASS | 11.9 s | tabs + mini-player |
| 34 | **reduced-motion**: prefers-reduced-motion: animations wait for an explicit play | Layout | PASS | 10.7 s | paused until Resume |
| 35 | **a11y-basics**: Named buttons and inputs, labelled regions, keyboard control of the canvas | Accessibility | PASS | 11.4 s | ok |
| 36 | **dark-mode**: ?theme=dark styles panes and figures; colours the script set are kept (a figure with its own light background stays readable); a live theme switch recolours a running figure | Theme | PASS | 22.4 s | figure corner [255, 255, 255] -> [15, 16, 20]; explicit colours kept |
| 37 | **embed-page**: Page with three <py-demo> embeds: panes= respected, iframes resize to content, theme follows the parent, lazy loading | Embedding | PASS | 16.9 s | heights [1236, 819, 634] |
| 38 | **embed-cross-origin**: A page on another origin cannot drive an embed (theme messages ignored, no messages posted to it) | Security | PASS | 2.9 s | ignored |
| 39 | **static-scan**: No innerHTML/eval/new Function/document.write in the demonstrator; CDN scripts pinned with integrity hashes | Security | PASS | 0.0 s | 19 modules scanned; pinned: @highlightjs/cdn-assets@11.12.0, dompurify@3.4.16, katex@0.16.11, markdown-it-footnote@4.0.0, markdown-it@14.3.2 |

Screenshots were also reviewed by eye: page mode (light), the explainer with
KaTeX, a figure and a table, dark mode with a script-coloured figure, the
embeds page in dark mode, phone-width tabs with the mini-player, and the
hostile explainer.

## How the checks map to the requirements

| Requirement | Checks |
|---|---|
| Multi-file projects, manifest, globs, bundles, script mode, clear errors | page-shell, bundle, module-error (no index: imports followed), bad-input, generic-page |
| Detection from unmodified scripts, live vs restart | detect-controls, unit tests (`test_detect.py`: argparse, config dicts, dataclasses, NamedTuples, entry functions, constants, hints, overrides, ranges, classification) |
| Live changes coalesced and fast; restarts debounced, warm, flicker-free; URL state | live-latency, live-coalesce, restart-warm, url-state |
| Overrides: demo.json, `# demo:` hints, `demo_params`, presets, buttons | unit tests (`test_detect.py`, `test_runtime.py`), preset-links; with_hints example |
| Template integration, page and embed modes, lazy loading | page-shell, lazy-load, embed-page, embed-cross-origin |
| Theme following, matplotlib colours, explicit styling kept | dark-mode, embed-page (theme sync), unit tests (retheme, own background) |
| Explainer: Markdown, maths, figures, TOC, `demo:` links, tour, deep links | page-shell, preset-links, explainer-hostile, deep-link, narrow-tabs-mini |
| Layout: dividers, collapse, focus, persistence, tabs and mini-player, options | layout-resize-persist, layout-options, narrow-tabs-mini |
| Runtime: show/FuncAnimation, `plt.pause` loops, several figures, static figures, `anim.save`, packages, Stop, timeout | pause-step, pause-loop-jspi, pause-loop-nojspi, multi-figure, static-figure, anim-save-finish, micropip, missing-package, stop-busy-loop, widgets |
| Performance, FPS shown | fps, live-latency |
| Security | bad-input, explainer-hostile, unit-browser, unit-node, embed-cross-origin, static-scan |
| Accessibility | a11y-basics, reduced-motion, layout-resize-persist (keyboard resizing) |

## Not tested

- **Firefox and Safari.** Only Chromium was available. The path for browsers
  without WebAssembly JSPI (no pausing of `plt.pause()` loops, loop
  parameters restart) was tested in Chromium with `?jspi=0`. Everything
  else uses standard APIs (module scripts, Web Workers, `ResizeObserver`,
  container queries, `DecompressionStream` for ZIP bundles), but layout
  details in other engines were not seen.
- **Real phones and tablets.** Narrow layouts were tested with Chromium's
  mobile emulation at 390 × 844 with touch enabled. Touch dragging of
  dividers and of matplotlib widgets was not tested on a device.
- **Screen readers.** Names, roles, labels and keyboard operation were
  checked automatically. No screen reader (NVDA, VoiceOver) was used.
- **The live GitHub Pages site and the real jsdelivr CDN.** The built output
  was served locally. The plugin writes `.nojekyll` and the output contains
  `__init__.py` files, but deployment itself (`make github`) was not run.
- **Your own projects.** Detection was tested on the six examples, the test
  fixtures and the unit-test corpus, not on the projects you will add.
- **Windows.** The checklist script uses only cross-platform Python
  (pathlib, http.server, Playwright), but it was run on Linux only.
- **Frame rates on ordinary hardware.** The numbers above come from a slow
  cloud machine; expect better on a laptop.
