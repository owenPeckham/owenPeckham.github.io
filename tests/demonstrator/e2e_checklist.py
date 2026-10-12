#!/usr/bin/env python3
"""End-to-end test checklist for the Python demonstrator, run in headless
Chromium with Playwright.

It serves a built copy of the site plus the test fixtures in
tests/demonstrator/fixtures on a local port, opens the demonstrator pages
and checks behaviour: page integration, parameter detection, live and
restart updates, the runtime (errors, packages, blocking loops, Stop),
the explainer and its sanitiser, layouts, themes, embedding and security.

Setup (once):
    pip install playwright
    python -m playwright install chromium

Run from the repository root:
    python tests/demonstrator/e2e_checklist.py --build                # builds the site first (needs Pelican)
    python tests/demonstrator/e2e_checklist.py --site quick-start/output
    python tests/demonstrator/e2e_checklist.py --site quick-start/output --only live-latency,widgets
    python tests/demonstrator/e2e_checklist.py --site quick-start/output --serve   # just serve, for manual testing

Options for unusual networks (used for the recorded run in a sandbox where
cdn.jsdelivr.net was blocked):
    --cdn-mirror DIR        answer https://cdn.jsdelivr.net/<path> from DIR/<path>
    --python-fetch HOSTS    fetch these hosts through Python (honours HTTPS_PROXY), e.g.
                            pypi.org,files.pythonhosted.org
    --offline               skip checks that need PyPI

Exit status is 0 when every check passed (skips allowed).  --report FILE
writes the results as Markdown; screenshots go to --shots DIR.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import mimetypes
import os
import re
import shutil
import ssl
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
QUICK = REPO / "quick-start"
JS_DIR = REPO / "themes" / "owenpeckham" / "static" / "demonstrator" / "js"
FIX = "/_pyd_tests/fixtures"

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("text/x-python", ".py")


# ---------------------------------------------------------------------------
# local web server: the site at /, tests/demonstrator at /_pyd_tests/
# ---------------------------------------------------------------------------
def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16]


def fixture_index(folder: Path) -> dict:
    """The same demo.index.json the Pelican plugin writes for real demos."""
    files = []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        parts = rel.split("/")
        if p.is_dir() or any(x.startswith(".") or x == "__pycache__" for x in parts):
            continue
        if rel in ("demo.index.json", "index.html") or rel.endswith(".pyc"):
            continue
        files.append({"path": rel, "size": p.stat().st_size, "sha1": _sha1(p)})
    main = "main.py"
    manifest = folder / "demo.json"
    if manifest.exists():
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(m, dict) and isinstance(m.get("main"), str):
                main = m["main"]
        except ValueError:
            pass
    return {"version": 1, "main": main, "files": files}


def make_handler(site: Path, tests: Path):
    site = site.resolve()
    tests = tests.resolve()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            path = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
            if path.startswith("/_pyd_tests/"):
                root, rel = tests, path[len("/_pyd_tests/"):]
            else:
                root, rel = site, path.lstrip("/")
            full = (root / rel).resolve()
            if full != root and root not in full.parents:
                return str(root / "__outside__")
            out = str(full)
            return out + os.sep if path.endswith("/") else out

        def do_GET(self):
            path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            if path.startswith(FIX + "/") and path.endswith("/demo.index.json"):
                folder = Path(self.translate_path(path[: -len("demo.index.json")]))
                if folder.is_dir() and not (folder / ".noindex").exists():
                    body = json.dumps(fixture_index(folder)).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)
                return
            super().do_GET()

        def end_headers(self):
            self.send_header("Cache-Control", "no-cache")
            super().end_headers()

        def log_message(self, *args):
            pass

    return Handler


def start_server(site: Path, tests: Path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(site, tests))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
CHECKS = []


class Skip(Exception):
    pass


class CheckFailed(AssertionError):
    pass


def check(cid, area, title):
    def deco(fn):
        CHECKS.append({"id": cid, "area": area, "title": title, "fn": fn})
        return fn
    return deco


def ensure(cond, msg):
    if not cond:
        raise CheckFailed(msg)


# ---------------------------------------------------------------------------
# browser harness
# ---------------------------------------------------------------------------
WORKER_COUNTER = """
(() => {
  const W = window.Worker;
  let n = 0;
  window.__pydWorkers = () => n;
  window.Worker = class extends W { constructor(...a) { super(...a); n += 1; } };
})();
"""

STATE_JS = """() => {
  const a = window.pydDemo;
  if (!a) return null;
  const s = a.sim;
  const n = s ? parseInt((s.frameOut.textContent || '').replace(/\\D+/g, ''), 10) || 0 : 0;
  return {
    state: a.state, frame: n, status: s ? s.statusOut.textContent : '', fps: s ? s.fpsOut.textContent : '',
    figures: s ? s.figures.length : 0, detected: !!a.det, running: !!a.scriptRunning,
    playing: a.player.playing, runSeq: a.runSeq || 0, alive: a.runtime.alive,
    jspi: a.runtime.info ? a.runtime.info.jspi : null,
    run: s ? s.runBtn.textContent.trim() : '', pauseDisabled: s ? s.pauseBtn.disabled : null,
    pause: s ? s.pauseBtn.textContent.trim() : '',
  };
}"""


class Page:
    """A page in its own browser context, with logs collected."""

    def __init__(self, harness, path, *, viewport=None, color_scheme="light", reduced_motion="no-preference",
                 init_script=None, permissions=None, mobile=False, route=None):
        self.h = harness
        opts = {"viewport": viewport or {"width": 1400, "height": 900}, "color_scheme": color_scheme,
                "reduced_motion": reduced_motion}
        if mobile:
            opts.update(is_mobile=True, has_touch=True, device_scale_factor=2)
        self.ctx = harness.browser.new_context(**opts)
        if permissions:
            self.ctx.grant_permissions(permissions, origin=harness.base)
        harness.add_routes(self.ctx)
        if route:
            for pattern, handler in route:
                self.ctx.route(pattern, handler)
        self.ctx.add_init_script(WORKER_COUNTER)
        if init_script:
            self.ctx.add_init_script(init_script)
        self.page = self.ctx.new_page()
        self.logs, self.errors, self.dialogs, self.requests = [], [], [], []
        self.page.on("console", lambda m: self.logs.append((m.type, m.text)))
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("dialog", self._dialog)
        self.page.on("request", lambda r: self.requests.append(r.url))
        if path is not None:
            self.goto(path)

    def _dialog(self, d):
        self.dialogs.append(d.message)
        d.dismiss()

    def goto(self, path):
        self.page.goto(path if path.startswith("http") else self.h.base + path)

    # -- helpers ----------------------------------------------------------------
    def js(self, expr, arg=None):
        return self.page.evaluate(expr, arg)

    def wait(self, expr, arg=None, timeout=60, what="condition"):
        try:
            self.page.wait_for_function(expr, arg=arg, timeout=timeout * 1000 * self.h.slow, polling=100)
        except Exception as e:  # noqa: BLE001
            st = self.state()
            raise CheckFailed(f"timed out waiting for {what} (state: {st}); {self.console_tail()}") from e

    def state(self):
        try:
            return self.js(STATE_JS)
        except Exception:  # noqa: BLE001
            return None

    def wait_state(self, *states, timeout=150):
        self.wait("(s) => window.pydDemo && s.includes(window.pydDemo.state)", list(states), timeout,
                  f"state in {states}")
        return self.state()

    def wait_frames(self, more=3, timeout=30):
        start = self.state()["frame"]
        self.wait("""(n) => { const t = window.pydDemo.sim.frameOut.textContent;
                     return (parseInt(t.replace(/\\D+/g, ''), 10) || 0) >= n; }""", start + more, timeout,
                  f"{more} more frames")
        return self.state()["frame"]

    def console_text(self):
        return self.js("() => { const o = document.querySelector('.pyd-console-out'); return o ? o.textContent : ''; }")

    def console_tail(self, n=600):
        try:
            return "console: " + self.console_text()[-n:].replace("\n", " | ")
        except Exception:  # noqa: BLE001
            return ""

    def wait_console(self, pattern, timeout=30):
        self.wait("(p) => { const o = document.querySelector('.pyd-console-out'); return !!o && new RegExp(p).test(o.textContent); }",
                  pattern, timeout, f"console text /{pattern}/")
        return self.console_text()

    def notice(self):
        return self.js("() => { const n = document.querySelector('.pyd-notice'); return n && !n.hidden ? n.querySelector('span').textContent : ''; }")

    def set_number(self, pid, value):
        """Type a value into a parameter's number box; returns Date.now() at the change."""
        return self.js("""([id, v]) => {
            const el = document.querySelector(`.pyd-ctl[data-param="${CSS.escape(id)}"] .pyd-num`);
            if (!el) throw new Error('no number box for ' + id);
            el.value = String(v);
            const t = Date.now();
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return t; }""", [pid, value])

    def control(self, pid):
        return self.js("""(id) => {
            const c = document.querySelector(`.pyd-ctl[data-param="${CSS.escape(id)}"]`);
            if (!c) return null;
            const num = c.querySelector('.pyd-num'), sel = c.querySelector('select'), badge = c.querySelector('.pyd-badge');
            return { value: num ? num.value : sel ? sel.value : null, live: !!(badge && badge.classList.contains('is-live')),
                     badge: badge ? badge.textContent.trim() : '' }; }""", pid)

    def workers(self):
        return self.js("() => window.__pydWorkers ? window.__pydWorkers() : -1")

    def canvas_hash(self, selector=".pyd-canvas"):
        return self.js("""(sel) => { const c = document.querySelector(sel); if (!c || !c.width) return '';
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let h = 0; for (let i = 0; i < d.length; i += 97) h = (h * 31 + d[i]) >>> 0; return c.width + 'x' + c.height + ':' + h; }""", selector)

    def pixel(self, fx, fy, selector=".pyd-canvas"):
        """RGB at a fractional position of the canvas."""
        return self.js("""([fx, fy, sel]) => { const c = document.querySelector(sel);
            const x = Math.min(c.width - 1, Math.round(fx * c.width)), y = Math.min(c.height - 1, Math.round(fy * c.height));
            const d = c.getContext('2d').getImageData(x, y, 1, 1).data; return [d[0], d[1], d[2]]; }""", [fx, fy, selector])

    def click_canvas(self, fx, fy):
        box = self.page.locator(".pyd-canvas").bounding_box()
        x, y = box["x"] + fx * box["width"], box["y"] + fy * box["height"]
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.wait_for_timeout(60)
        self.page.mouse.up()

    def no_errors(self, allow=()):
        bad = [e for e in self.errors if not any(re.search(a, e) for a in allow)]
        ensure(not bad, f"page errors: {bad[:3]}")
        cons = [t for (k, t) in self.logs if k == "error" and not any(re.search(a, t) for a in allow)]
        ensure(not cons, f"console errors: {cons[:3]}")
        ensure(not self.dialogs, f"dialogs opened: {self.dialogs}")

    def warnings(self):
        return [t for (k, t) in self.logs if k in ("warning", "warn")]

    def shot(self, name, full_page=False):
        if self.h.shots:
            path = self.h.shots / f"{name}.png"
            self.page.screenshot(path=str(path), full_page=full_page)
            return path
        return None

    def close(self):
        try:
            self.ctx.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Harness:
    def __init__(self, args, base, alt_base):
        from playwright.sync_api import sync_playwright
        self.args = args
        self.base = base
        self.alt_base = alt_base
        self.slow = args.slow
        self.shots = Path(args.shots).resolve() if args.shots else None
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=not args.headed)
        self.cdn = Path(args.cdn_mirror).resolve() if args.cdn_mirror else None
        self.python_hosts = [h.strip() for h in (args.python_fetch or "").split(",") if h.strip()]
        cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
        self.ssl = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()

    def page(self, path, **kw):
        return Page(self, path, **kw)

    def add_routes(self, ctx):
        if self.cdn:
            ctx.route("https://cdn.jsdelivr.net/**", self._cdn_route)
        for host in self.python_hosts:
            ctx.route(f"https://{host}/**", self._python_route)

    def _cdn_route(self, route):
        rel = urllib.parse.urlsplit(route.request.url).path.lstrip("/")
        fp = (self.cdn / rel).resolve()
        if self.cdn not in fp.parents or not fp.is_file():
            route.fulfill(status=404, body="not in mirror", headers={"access-control-allow-origin": "*"})
            return
        ctype = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        route.fulfill(status=200, body=fp.read_bytes(),
                      headers={"content-type": ctype, "access-control-allow-origin": "*", "cache-control": "max-age=3600"})

    def _python_route(self, route):
        req = route.request
        try:
            r = urllib.request.Request(req.url, method=req.method, headers={"User-Agent": "pyd-e2e", "Accept": req.headers.get("accept", "*/*")})
            with urllib.request.urlopen(r, timeout=60, context=self.ssl) as resp:
                route.fulfill(status=resp.status, body=resp.read(),
                              headers={"content-type": resp.headers.get("content-type", "application/octet-stream"),
                                       "access-control-allow-origin": "*"})
        except urllib.error.HTTPError as e:
            route.fulfill(status=e.code, body=e.read(), headers={"access-control-allow-origin": "*"})
        except Exception:  # noqa: BLE001
            route.abort()

    def close(self):
        try:
            self.browser.close()
        finally:
            self.pw.stop()


# ===========================================================================
# the checklist
# ===========================================================================
OSC = "/examples/oscillator/"


def demo(path, query=""):
    q = f"demo={urllib.parse.quote(path, safe='/')}"
    return f"/demonstrator.html?{q}{'&' + query if query else ''}"


# --- A. page integration ------------------------------------------------------
@check("page-shell", "Page", "Example page keeps the site header, nav, footer, back link, title, meta and stylesheet; three panes render")
def _(h):
    with h.page("/") as home:
        home_css = home.js("() => [...document.querySelectorAll('link[rel=stylesheet]')].map(l => new URL(l.href).pathname)")
        home_nav = home.js("() => document.querySelectorAll('header a, nav a').length")
    with h.page(OSC) as p:
        p.wait_state("running")
        p.wait_frames(5)
        info = p.js("""() => ({
            title: document.title, h1: document.querySelector('[data-pyd-title]').textContent,
            desc: (document.querySelector('meta[name=description]') || {}).content || '',
            og: (document.querySelector('meta[property="og:title"]') || {}).content || '',
            nav: document.querySelectorAll('header a, nav a').length,
            footer: !!document.querySelector('footer'),
            back: (document.querySelector('[data-pyd-back]') || {}).href || '',
            backText: (document.querySelector('[data-pyd-back]') || {}).textContent || '',
            css: [...document.querySelectorAll('link[rel=stylesheet]')].map(l => new URL(l.href).pathname),
            panes: [...document.querySelectorAll('.pyd-pane')].map(s => s.dataset.pane),
            katex: document.querySelectorAll('.pyd-pane-explainer .katex').length,
            imgs: [...document.querySelectorAll('.pyd-pane-explainer img')].map(i => i.complete && i.naturalWidth > 0),
            toc: !!document.querySelector('.pyd-toc'),
            demoLinks: document.querySelectorAll('.pyd-demo-link').length,
            files: [...document.querySelectorAll('.pyd-tab[role=tab], .pyd-tree-file')].map(t => t.title),
        })""")
        p.wait("() => document.querySelectorAll('.pyd-pane-explainer .katex').length > 0", timeout=30, what="KaTeX")
        ensure("Damped, driven oscillator" in info["title"] and "Owen Peckham" in info["title"], f"title {info['title']!r}")
        ensure(info["h1"].strip() == "Damped, driven oscillator", f"h1 {info['h1']!r}")
        ensure(len(info["desc"]) > 20 and info["og"], "meta description / og:title")
        ensure(info["nav"] >= home_nav and info["footer"], "site header/nav/footer missing")
        ensure(info["back"].endswith("/#projects") and "Back to projects" in info["backText"], f"back link {info['back']}")
        ensure(all(c in info["css"] for c in home_css), f"site stylesheets not all linked: {home_css} vs {info['css']}")
        ensure(info["panes"] == ["explainer", "sim", "code"], f"panes {info['panes']}")
        ensure(info["imgs"] and all(info["imgs"]), "explainer figure did not load")
        ensure(info["toc"] and info["demoLinks"] >= 3, "TOC or demo: links missing")
        ensure({"main.py", "physics.py"} <= set(info["files"]), f"code files {info['files']}")
        p.shot("page-shell")
        p.no_errors()
        return f"{len(home_css)} site stylesheet(s) reused; files {info['files']}"


@check("generic-page", "Page", "Bare /demonstrator.html lists the demos; ?demo= loads one with its manifest title")
def _(h):
    with h.page("/demonstrator.html") as p:
        p.wait("() => document.querySelectorAll('.pyd-catalogue a').length >= 6", timeout=20, what="catalogue")
        links = p.js("() => [...document.querySelectorAll('.pyd-catalogue a')].map(a => new URL(a.href).pathname)")
        ensure("/examples/oscillator/" in links, f"catalogue links {links}")
        p.no_errors()
    with h.page(demo("/examples/heat_map/")) as p:
        p.wait_state("running")
        info = p.js("() => ({t: document.title, h1: document.querySelector('[data-pyd-title]').textContent})")
        ensure(info["h1"].startswith("Heat") and info["t"].startswith(info["h1"]), f"title not from demo.json: {info}")
        p.no_errors()
    return f"{len(links)} demos listed"


@check("bad-input", "Security", "Off-site demos, odd schemes, traversal and broken manifests give clear errors and fetch nothing")
def _(h):
    cases = [
        (demo("https://evil.example/x/"), "only be loaded from this site"),
        ("/demonstrator.html?demo=javascript:alert(1)", "http(s) URL or a path"),
        ("/demonstrator.html?script=/examples/oscillator/physics.txt", "must point to a .py file"),
        (demo(OSC, "files=../secret.py"), "Unsafe or invalid path"),
        (demo("/examples/no_such_demo/"), "not found"),
        (demo(f"{FIX}/bad_manifest/"), "not valid JSON"),
        (demo(f"{FIX}/unsafe_manifest/"), "must stay inside the demo folder"),
    ]
    seen = []
    for url, text in cases:
        with h.page(url) as p:
            p.wait("() => !!document.querySelector('.pyd-error-box')", timeout=20, what=f"error box for {url}")
            msg = p.js("() => document.querySelector('.pyd-error-box').textContent")
            ensure(text in msg, f"{url}: message {msg!r} lacks {text!r}")
            ensure(not [r for r in p.requests if urllib.parse.urlsplit(r).hostname == "evil.example"], "requested evil.example")
            ensure(not p.dialogs, "dialog opened")
            seen.append(text)
    return f"{len(seen)} error cases"


@check("lazy-load", "Page", "load=click: no Python download until Run is pressed")
def _(h):
    with h.page(demo("/examples/static_plot/", "load=click")) as p:
        p.wait("() => !!document.querySelector('.pyd-start')", timeout=20, what="start button")
        p.page.wait_for_timeout(2500)
        early = [r for r in p.requests if "pyodide" in r]
        ensure(not early, f"Pyodide requested before Run: {early[:2]}")
        p.page.click(".pyd-start")
        p.wait_state("static")
        ensure(any("pyodide.asm.wasm" in r for r in p.requests), "Pyodide was not loaded after Run")
        p.no_errors()
    return "no download before Run"


# --- B. parameters ------------------------------------------------------------
@check("detect-controls", "Parameters", "Oscillator: controls detected from plain constants, live vs restart, 'Why these controls?'")
def _(h):
    with h.page(OSC) as p:
        p.wait_state("running")
        params = p.js("() => window.pydDemo.det.params.map(x => ({id: x.id, mode: x.mode, type: x.type, min: x.min, max: x.max, hidden: x.hidden}))")
        by = {x["id"]: x for x in params}
        for pid in ("FREQ", "DAMPING", "DRIVE", "DRIVE_FREQ"):
            ensure(pid in by and by[pid]["mode"] == "live", f"{pid} should be live: {by.get(pid)}")
            ensure((p.control(pid) or {}).get("live"), f"{pid} control badge not Live")
        for pid in ("TRAIL", "INTERVAL_MS"):
            ensure(pid in by and by[pid]["mode"] == "restart", f"{pid} should restart: {by.get(pid)}")
        p.page.click(".pyd-why summary")
        why = p.js("() => document.querySelector('.pyd-why-body').textContent")
        ensure("constants" in why, f"why panel: {why[:200]}")
        p.no_errors()
        return ", ".join(f"{x['id']}:{x['mode']}" for x in params)


@check("live-latency", "Parameters", "A live change reaches the running script within about one frame, without a restart")
def _(h):
    with h.page(demo(f"{FIX}/latency_probe/")) as p:
        p.wait_state("running")
        p.wait_frames(5)
        ensure((p.control("GAIN") or {}).get("live"), "GAIN is not live")
        seq = p.state()["runSeq"]
        lat = []
        for v in (2.5, 1.5, 2.0, 0.5, 1.25, 1.75):
            t0 = p.set_number("GAIN", v)
            txt = p.wait_console(rf"probe GAIN={re.escape(str(v))} wall_ms=\d+", timeout=10)
            wall = int(re.findall(rf"probe GAIN={re.escape(str(v))} wall_ms=(\d+)", txt)[-1])
            lat.append(wall - t0)
            p.page.wait_for_timeout(150)
        ensure(p.state()["runSeq"] == seq, "a live change restarted the script")
        med, worst = statistics.median(lat), max(lat)
        ensure(worst < 300 * h.slow, f"live latency too high: {lat} ms")
        p.no_errors()
        return f"latency to the script: median {med:.0f} ms, max {worst} ms (frame interval 33 ms)"


@check("live-coalesce", "Parameters", "Dragging a live slider sends coalesced updates; the last value wins")
def _(h):
    with h.page(demo(f"{FIX}/latency_probe/")) as p:
        p.wait_state("running")
        p.wait_frames(3)
        p.js("""() => { const rt = window.pydDemo.runtime, orig = rt.request.bind(rt); window.__paramMsgs = 0;
            rt.request = (type, payload, tr) => {
              if ((type === 'tick' && payload && payload.params && Object.keys(payload.params).length) || type === 'set-params') window.__paramMsgs++;
              return orig(type, payload, tr); }; }""")
        info = p.js("""async () => {
            const r = document.querySelector('.pyd-ctl[data-param="GAIN"] .pyd-range');
            const lo = Number(r.min), hi = Number(r.max), n = 40, vals = [];
            for (let i = 1; i <= n; i++) {
              const v = lo + (hi - lo) * (0.2 + 0.6 * i / n);
              r.value = String(v); r.dispatchEvent(new Event('input', { bubbles: true }));
              vals.push(Number(r.value));
              await new Promise(res => setTimeout(res, 8));
            }
            r.dispatchEvent(new Event('change', { bubbles: true }));
            return { last: window.pydDemo.values.GAIN, n }; }""")
        p.page.wait_for_timeout(1200)
        msgs = p.js("() => window.__paramMsgs")
        txt = p.console_text()
        seen = re.findall(r"probe GAIN=([\d.e+-]+)", txt)
        ensure(abs(float(seen[-1]) - info["last"]) < 1e-6, f"last value {info['last']} not applied (saw {seen[-3:]})")
        ensure(1 <= msgs <= info["n"] // 2, f"{msgs} parameter messages for {info['n']} slider events")
        p.no_errors()
        return f"{info['n']} input events -> {msgs} parameter messages; final value applied"


@check("restart-warm", "Parameters", "A restart parameter re-runs in the warm worker (no Pyodide reload); the shown code is unchanged; URL updated")
def _(h):
    with h.page(OSC) as p:
        p.wait_state("running")
        p.wait_frames(5)
        ensure(p.workers() == 1, f"workers {p.workers()}")
        before = p.state()["runSeq"]
        t0 = time.time()
        p.set_number("TRAIL", 100)
        p.wait("(s) => window.pydDemo.runSeq > s && window.pydDemo.state === 'running' && window.pydDemo.firstFrameSeen", before,
               timeout=30, what="restart")
        dt = time.time() - t0
        p.wait_frames(3)
        ensure(p.workers() == 1, f"a new worker was started ({p.workers()})")
        code = p.js("() => document.querySelector('.pyd-code-views').textContent")
        ensure("TRAIL = 240" in code, "displayed code was modified")
        p.page.wait_for_timeout(600)
        ensure("p.TRAIL=100" in p.page.url, f"URL not updated: {p.page.url}")
        p.no_errors()
        return f"change to first frame of the re-run: {dt:.2f} s, same worker"


@check("url-state", "Parameters", "Values in the URL are applied on load; the Link button copies the current setup")
def _(h):
    with h.page(OSC + "?p.DAMPING=0.5&p.TRAIL=100&p.NOPE=3", permissions=["clipboard-read", "clipboard-write"]) as p:
        p.wait_state("running")
        ensure(float(p.control("DAMPING")["value"]) == 0.5, f"DAMPING {p.control('DAMPING')}")
        ensure(p.control("TRAIL")["value"] == "100", f"TRAIL {p.control('TRAIL')}")
        ensure(any("NOPE" in w for w in p.warnings()), "unknown URL parameter not warned about")
        p.set_number("FREQ", 1.5)
        p.page.click("button[aria-label='Copy a link to this setup']")
        p.page.wait_for_timeout(300)
        clip = p.js("() => navigator.clipboard.readText()")
        ensure("p.DAMPING=0.5" in clip and "p.FREQ=1.5" in clip and "p.TRAIL=100" in clip, f"clipboard {clip}")
        p.no_errors()
        return clip.split("?", 1)[-1]


@check("preset-links", "Explainer", "demo:preset and demo:set links apply values; demo:code shows and highlights lines")
def _(h):
    with h.page(OSC) as p:
        p.wait_state("running")
        p.page.click(".pyd-demo-preset")
        p.page.wait_for_timeout(500)
        vals = {k: p.control(k)["value"] for k in ("DAMPING", "DRIVE")}
        ensure(vals != {"DAMPING": "0.08", "DRIVE": "0"}, f"preset not applied: {vals}")
        p.page.click(".pyd-demo-code")
        p.page.wait_for_timeout(600)
        hl = p.js("() => [...document.querySelectorAll('.pyd-line.is-highlight')].map(l => l.dataset.line)")
        ensure(hl, "no highlighted code lines")
        p.no_errors()
        return f"preset -> {vals}; highlighted lines {hl[0]}-{hl[-1]}"


@check("pause-step", "Runtime", "Pause freezes frames, Step advances exactly one, Resume continues")
def _(h):
    with h.page(OSC) as p:
        p.wait_state("running")
        p.wait_frames(5)
        p.page.click(".pyd-player button[aria-label='Pause']")
        p.page.wait_for_timeout(400)
        f1 = p.state()["frame"]
        p.page.wait_for_timeout(800)
        ensure(p.state()["frame"] == f1, "frames advanced while paused")
        ensure(p.state()["status"] == "Paused", f"status {p.state()['status']}")
        p.page.click(".pyd-player button[aria-label='Step']")
        p.wait("(f) => window.pydDemo.sim.frameOut.textContent === 'frame ' + (f + 1)", f1, 10, "one step")
        p.page.wait_for_timeout(400)
        ensure(p.state()["frame"] == f1 + 1, "Step advanced more than one frame")
        p.page.click(".pyd-player button[aria-label='Resume']")
        p.wait_frames(5)
        p.no_errors()
        return "ok"


@check("stop-busy-loop", "Runtime", "A script that never yields: timeout warning, Stop terminates the worker, Run works again")
def _(h):
    with h.page(demo(f"{FIX}/busy_loop/")) as p:
        p.wait_state("running")
        p.wait("() => /without showing anything/.test((document.querySelector('.pyd-notice') || {}).textContent || '')",
               timeout=30, what="timeout warning")
        p.page.click(".pyd-notice button")
        p.wait_state("stopped", timeout=20)
        p.wait_console("Stopped the script by restarting Python")
        ensure(p.workers() == 2, f"expected a replacement worker, have {p.workers()}")
        p.wait("() => window.pydDemo.runtime.alive && !window.pydDemo.warmP", timeout=90, what="warm worker")
        p.page.click(".pyd-player .pyd-btn-primary")  # Run
        p.wait_state("running")
        p.page.wait_for_timeout(500)
        p.page.click(".pyd-player button[aria-label='Stop']")
        p.wait_state("stopped", timeout=20)
        ensure(p.workers() == 3, f"workers {p.workers()}")
        p.no_errors()
        return "terminate + respawn twice"


@check("pause-loop-jspi", "Runtime", "plt.pause() loop: frames stream; pause, step, live values and a graceful Stop work (JSPI)")
def _(h):
    with h.page(demo(f"{FIX}/pause_loop/")) as p:
        p.wait_state("running")
        if not p.state()["jspi"]:
            raise Skip("this browser has no JSPI; covered by pause-loop-nojspi")
        p.wait_frames(5)
        ensure((p.control("RATE") or {}).get("live"), "RATE should be live in a plt.pause loop")
        p.page.click(".pyd-player button[aria-label='Pause']")
        p.page.wait_for_timeout(500)
        f1 = p.state()["frame"]
        p.page.wait_for_timeout(800)
        ensure(p.state()["frame"] == f1, "loop kept running while paused")
        p.page.click(".pyd-player button[aria-label='Step']")
        p.wait("(f) => (parseInt(window.pydDemo.sim.frameOut.textContent.replace(/\\D+/g, ''), 10) || 0) > f", f1, 10, "a step")
        p.page.click(".pyd-player button[aria-label='Resume']")
        p.wait_frames(3)
        p.set_number("RATE", 2)
        p.wait_console(r"loop RATE=2(\.0)? step=\d+", timeout=10)
        p.page.click(".pyd-player button[aria-label='Stop']")
        p.wait_state("stopped", timeout=20)
        ensure("restarting Python" not in p.console_text(), "Stop had to kill the worker")
        ensure(p.workers() == 1, f"workers {p.workers()}")
        p.no_errors()
        return "graceful stop, worker kept"


@check("pause-loop-nojspi", "Runtime", "Same loop in a browser without JSPI (?jspi=0): frames still stream, loop parameters become restarts, Stop works")
def _(h):
    with h.page(demo(f"{FIX}/pause_loop/", "jspi=0")) as p:
        p.wait_state("running")
        ensure(p.state()["jspi"] is False, "jspi=0 not honoured")
        p.wait_frames(5)
        c = p.control("RATE") or {}
        ensure(not c.get("live") and "Restarts" in c.get("badge", ""), f"RATE should be a restart control: {c}")
        ensure(p.state()["pauseDisabled"], "Pause should be disabled for a blocking loop without JSPI")
        p.set_number("RATE", 2)
        p.wait_console(r"loop RATE=2(\.0)? step=0", timeout=120)
        p.wait_frames(3)
        p.page.click(".pyd-player button[aria-label='Stop']")
        p.wait_state("stopped", timeout=20)
        p.no_errors()
        return f"workers used: {p.workers()}"


@check("module-error", "Runtime", "An exception in a helper module names the file and line, highlights it, and pauses (files found by import scanning)")
def _(h):
    with h.page(demo(f"{FIX}/module_error/")) as p:
        p.wait_state("error")
        p.page.wait_for_timeout(500)
        note = p.notice()
        ensure("ZeroDivisionError in helpers.py line 8" in note, f"notice {note!r}")
        info = p.js("""() => ({
            tab: (document.querySelector('.pyd-tab[aria-selected=true]') || {}).title,
            err: [...document.querySelectorAll('.pyd-line.is-error')].map(l => l.dataset.line),
            links: [...document.querySelectorAll('.pyd-con-link')].map(b => b.textContent) })""")
        ensure(info["tab"] == "helpers.py" and info["err"] == ["8"], f"code pane {info}")
        ensure(any("helpers.py" in t for t in info["links"]), f"traceback links {info['links']}")
        ensure(not p.state()["playing"], "animation should pause on an error")
        ensure(any(r.endswith("helpers.py") or "helpers.py?" in r for r in p.requests), "helpers.py not fetched")
        p.no_errors(allow=[r"404"])
        return note


@check("syntax-error", "Runtime", "A module with a syntax error: detection reports it and still finds main.py's controls; the run shows file and line")
def _(h):
    with h.page(demo(f"{FIX}/syntax_error/")) as p:
        p.wait_state("error")
        p.page.wait_for_timeout(300)
        txt = p.console_text()
        ensure(re.search(r"Parameter detection: broken\.py:4", txt), f"console {txt[:300]!r}")
        ensure(p.control("POINTS") and p.control("SCALE"), "main.py controls missing")
        note = p.notice()
        ensure("SyntaxError" in note and "broken.py" in note, f"notice {note!r}")
        p.no_errors()
        return note


@check("detect-crash", "Runtime", "If the detector itself fails, the demo still runs without controls")
def _(h):
    def broken(route):
        resp = route.fetch()
        body = resp.text() + "\n\ndef detect(*args, **kwargs):\n    raise RuntimeError('simulated detector failure')\n"
        route.fulfill(response=resp, body=body)
    with h.page(OSC, route=[("**/demonstrator/py/pyd_detect.py*", broken)]) as p:
        p.wait_state("running")
        p.wait_frames(5)
        ensure(p.js("() => !!document.querySelector('.pyd-params-none')"), "expected 'no parameters' message")
        p.wait_console(r"Parameter detection: main\.py: detection failed \(RuntimeError: simulated detector failure\)")
        p.no_errors()
        return "ran without controls"


@check("missing-package", "Runtime", "An import that exists nowhere is reported clearly")
def _(h):
    with h.page(demo(f"{FIX}/missing_package/")) as p:
        p.wait_state("error")
        txt = p.console_text()
        ensure('Package "definitely_not_a_real_package_xyz" is not available' in txt, f"console {txt[:300]!r}")
        ensure("ModuleNotFoundError" in p.notice(), f"notice {p.notice()!r}")
        p.no_errors()
        return "reported"


@check("micropip", "Runtime", "A pure-Python package Pyodide lacks (tabulate) is installed from PyPI automatically")
def _(h):
    if h.args.offline:
        raise Skip("--offline")
    with h.page(demo(f"{FIX}/micropip_fallback/")) as p:
        p.wait_state("static", "error", timeout=240)
        txt = p.console_text()
        ensure(p.state()["state"] == "static", f"state {p.state()['state']}: {txt[-300:]!r}")
        ensure("| alpha  |" in txt, f"tabulate output missing: {txt[-300:]!r}")
        ensure(any("tabulate" in r and ("pypi" in r or "pythonhosted" in r) for r in p.requests), "no PyPI request seen")
        p.no_errors()
        return "installed from PyPI"


@check("multi-figure", "Runtime", "Two figures get tabs; switching shows the other figure; the animated one keeps running")
def _(h):
    with h.page(demo(f"{FIX}/multi_figure/")) as p:
        p.wait_state("running")
        p.wait_frames(3)
        tabs = p.js("() => [...document.querySelectorAll('.pyd-figtab')].map(t => t.textContent)")
        ensure(tabs == ["Wave", "Histogram"], f"tabs {tabs}")
        a = p.canvas_hash()
        p.page.click(".pyd-figtab >> nth=1")
        p.wait("() => document.querySelectorAll('.pyd-figtab')[1].getAttribute('aria-selected') === 'true'", timeout=5)
        p.page.wait_for_timeout(500)
        b = p.canvas_hash()
        ensure(a.split(":")[0] != b.split(":")[0], f"canvas did not switch figure ({a} -> {b})")
        green = p.js("""() => { const c = document.querySelector('.pyd-canvas'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i+1] > 110 && d[i] < 90 && d[i+2] < 90) n++; return n; }""")
        ensure(green > 500, f"histogram not visible ({green} green pixels)")
        p.page.click(".pyd-figtab >> nth=0")
        p.wait_frames(3)
        p.no_errors()
        return f"tabs {tabs}"


@check("widgets", "Runtime", "matplotlib Slider and Button widgets respond to clicks forwarded from the canvas")
def _(h):
    with h.page(demo(f"{FIX}/widgets/")) as p:
        p.wait_state("static")
        p.page.wait_for_timeout(300)
        before = p.canvas_hash()
        p.click_canvas(0.2 + 0.6 * (3.0 - 0.5) / 4.5, 1 - (0.12 + 0.025))
        txt = p.wait_console(r"slider freq=\d", timeout=10)
        val = float(re.findall(r"slider freq=([\d.]+)", txt)[-1])
        ensure(abs(val - 3.0) < 0.15, f"slider value {val}")
        p.page.wait_for_timeout(600)
        ensure(p.canvas_hash() != before, "figure was not redrawn after the slider moved")
        p.click_canvas(0.88, 1 - 0.05)
        p.wait_console("button reset", timeout=10)
        p.no_errors()
        return f"slider -> {val}"


@check("static-figure", "Runtime", "Static figure: shown once, Run (not Stop) offered, a dropdown change re-runs the script")
def _(h):
    with h.page("/examples/static_plot/") as p:
        st = p.wait_state("static")
        ensure(st["run"] == "Run" and st["pauseDisabled"], f"buttons: {st}")
        a = p.canvas_hash()
        p.js("""() => { const s = document.querySelector('.pyd-ctl[data-param="SHAPE"] select');
                        s.value = 'gaussian'; s.dispatchEvent(new Event('change', { bubbles: true })); }""")
        p.wait("(a) => window.pydDemo.runSeq > 1 && window.pydDemo.state === 'static'", None, 30, "re-run")
        p.page.wait_for_timeout(500)
        ensure(p.canvas_hash() != a, "figure unchanged after SHAPE changed")
        p.no_errors()
        return "re-ran on change"


@check("anim-save-finish", "Runtime", "anim.save() is skipped with a friendly note; a finite animation ends in 'finished' and can be replayed")
def _(h):
    with h.page(demo(f"{FIX}/anim_save/")) as p:
        p.wait_state("running", "finished")
        p.wait_console(r"anim\.save\('wave\.gif'\) skipped")
        p.wait_state("finished", timeout=60)
        st = p.state()
        ensure(st["run"] == "Run" and st["status"] == "Animation finished", f"{st}")
        p.page.click(".pyd-player button[aria-label='Restart']")
        p.wait_state("running")
        p.wait_frames(3)
        p.no_errors()
        return f"finished at frame {st['frame']}"


@check("bundle", "Projects", "A project shipped as one ZIP (deflated, wrapped in a folder) loads; junk and ../ entries are dropped")
def _(h):
    with h.page(demo(f"{FIX}/bundle_demo/")) as p:
        p.wait_state("static")
        files = p.js("() => [...document.querySelectorAll('.pyd-tab[role=tab], .pyd-tree-file')].map(t => t.title)")
        ensure(set(files) == {"main.py", "pkg/__init__.py", "pkg/util.py", "data/values.csv"}, f"files {files}")
        ensure(any("skipped unsafe entry" in w for w in p.warnings()), "no warning about the ../ entry")
        ensure(p.control("WINDOW"), "WINDOW control missing")
        p.no_errors()
        return f"files {sorted(files)}"


@check("editable", "Projects", "Editing a module and re-running re-detects its parameters; Reset restores the original")
def _(h):
    with h.page(demo(f"{FIX}/editable/")) as p:
        p.wait_state("static")
        pid = p.js("() => window.pydDemo.det.params.find(x => x.name === 'EXPONENT').id")
        ensure(p.js("(id) => window.pydDemo.det.params.find(x => x.id === id).default", pid) == 2, "EXPONENT default")
        p.page.click(".pyd-tab[title='model.py']")
        p.page.click("button[title='Edit the code']")
        p.js("""() => { const ta = document.querySelector('.pyd-editor-input');
                        ta.value = ta.value.replace('EXPONENT = 2', 'EXPONENT = 3');
                        ta.dispatchEvent(new Event('input', { bubbles: true })); }""")
        p.page.click("text=Run edited")
        p.wait("(id) => { const x = window.pydDemo.det.params.find(q => q.id === id); return x && x.default === 3 && window.pydDemo.state === 'static'; }",
               pid, 60, "re-detection after the edit")
        ensure(p.control(pid)["value"] == "3", f"control shows {p.control(pid)}")
        p.page.click("button[title='Restore the original files']")
        p.wait("(id) => { const x = window.pydDemo.det.params.find(q => q.id === id); return x && x.default === 2 && window.pydDemo.state === 'static'; }",
               pid, 60, "reset")
        p.no_errors()
        return "edit -> 3 -> reset -> 2"


@check("fps", "Performance", "Measured frame rate is shown (full redraw and blitting examples)")
def _(h):
    out = {}
    for name, path in (("oscillator", OSC), ("cli_style (blit)", "/examples/cli_style/"), ("heat_map", "/examples/heat_map/")):
        with h.page(path) as p:
            p.wait_state("running")
            p.wait_frames(10)
            p.page.wait_for_timeout(4000)
            fps = p.state()["fps"]
            ms = p.js("() => window.pydDemo.stats ? window.pydDemo.stats.ms : null")
            m = re.match(r"(\d+) fps", fps)
            ensure(m, f"no fps readout: {fps!r}")
            out[name] = (int(m.group(1)), ms)
    ensure(all(v[0] >= 5 for v in out.values()), f"fps too low: {out}")
    return "; ".join(f"{k}: {v[0]} fps ({v[1]:.0f} ms/tick)" for k, v in out.items())


# --- C. explainer ---------------------------------------------------------------
@check("explainer-hostile", "Security", "A hostile explainer (XSS, mXSS, javascript:/data: links, KaTeX \\href, CSS) renders inert; bad demo: links degrade to text")
def _(h):
    with h.page(demo(f"{FIX}/hostile_markdown/")) as p:
        p.wait("() => document.querySelectorAll('.pyd-pane-explainer h2').length >= 4", timeout=30, what="explainer")
        p.wait_state("static")
        p.page.wait_for_timeout(1500)
        res = p.js("""() => {
            const root = document.querySelector('.pyd-pane-explainer');
            const bad = [];
            for (const el of root.querySelectorAll('*')) for (const a of el.attributes) {
              if (/^on/i.test(a.name)) bad.push(el.nodeName + '[' + a.name + ']');
              if (/^\\s*(javascript|vbscript|data):/i.test(a.value)) bad.push(el.nodeName + '[' + a.name + ']=' + a.value);
              if (a.name === 'style' && !el.closest('.katex')) bad.push(el.nodeName + '[style]');
            }
            for (const t of ['script','iframe','object','embed','form','input','base','meta','link','template','noscript','svg:not(.pyd-icon):not(.katex *)'])
              for (const el of root.querySelectorAll(t)) if (!el.closest('.katex') && !el.classList.contains('pyd-icon')) bad.push('<' + el.nodeName + '>');
            const styleEls = [...root.querySelectorAll('style')].length;
            return { bad, styleEls, pwned: window.__pwned || null, bodyDisplay: getComputedStyle(document.body).display,
              h1: document.querySelector('[data-pyd-title]').textContent, h1imgs: document.querySelectorAll('[data-pyd-title] img').length,
              dead: [...root.querySelectorAll('.pyd-demo-dead')].map(s => s.textContent.trim()),
              live: [...root.querySelectorAll('.pyd-demo-link')].map(s => s.textContent.trim()),
              blocked: [...root.querySelectorAll('.pyd-blocked')].length,
              ext: [...root.querySelectorAll('a.pyd-external')].map(a => [a.target, a.rel]) }; }""")
        ensure(not res["pwned"] and not p.dialogs, f"payload executed: {res['pwned']} {p.dialogs}")
        ensure(not res["bad"] and not res["styleEls"], f"dangerous markup survived: {res['bad'][:8]}")
        ensure(res["bodyDisplay"] != "none", "page CSS was hijacked")
        ensure("<img" in res["h1"] and res["h1imgs"] == 0, f"title not shown as text: {res['h1']!r}")
        for t in ("unknown action", "unknown parameter", "bad value", "missing file", "path traversal", "unknown preset", "javascript in a demo link"):
            ensure(t in res["dead"], f"'{t}' should be plain text; dead={res['dead']}")
        ensure(res["live"] == ["valid demo link"], f"live demo links {res['live']}")
        ensure(res["blocked"] >= 2, "external images were not blocked")
        ensure(res["ext"] and all(t == "_blank" and "noopener" in r for t, r in res["ext"]), f"external links {res['ext']}")
        warns = " ".join(p.warnings())
        ensure("frobnicate" in warns and "NOPE" in warns, "no console warnings for bad demo: links")
        ensure(not [r for r in p.requests if urllib.parse.urlsplit(r).hostname == "evil.example"], "fetched from evil.example")
        p.page.click(".pyd-demo-link")
        p.wait("() => window.pydDemo.values.LEVEL === 5 && window.pydDemo.state === 'static' && window.pydDemo.runSeq > 1", None, 30, "valid link")
        p.shot("explainer-hostile")
        p.no_errors(allow=[r"Failed to load resource.*404"])  # <img src="x"> is a same-origin image that doesn't exist
        return f"{len(res['dead'])} demo: links degraded, {res['blocked']} images blocked"


@check("unit-browser", "Security", "Browser unit tests: sanitiser (real DOMPurify), Markdown pipeline, demo: links, manifests, paths, ZIP")
def _(h):
    with h.page("/_pyd_tests/browser/unit.html") as p:
        p.wait("() => !!window.__unitResults", timeout=60, what="unit tests")
        r = p.js("() => window.__unitResults")
        ensure(r["failed"] == 0, f"failures: {r['failures']}")
        ensure(not p.dialogs, "dialog opened")
        p.no_errors()
        return f"{r['passed']}/{r['total']} passed"


@check("unit-node", "Security", "Node unit tests for the demo: link parser (node --test)")
def _(h):
    node = shutil.which("node")
    if not node:
        raise Skip("node not installed")
    r = subprocess.run([node, "--test", str(HERE / "js" / "demolinks.test.mjs")], capture_output=True, text=True, timeout=120)
    passed = re.search(r"# pass (\d+)", r.stdout)
    ensure(r.returncode == 0, f"node tests failed:\n{r.stdout[-1500:]}{r.stderr[-500:]}")
    return f"{passed.group(1) if passed else '?'} passed"


@check("deep-link", "Explainer", "A #heading in the URL scrolls the explainer to that section (split layout and phone-width tabs)")
def _(h):
    with h.page(OSC) as p:
        p.wait("() => document.querySelectorAll('.pyd-pane-explainer h2').length >= 3", timeout=30, what="explainer")
        hid = p.js("() => [...document.querySelectorAll('.pyd-pane-explainer h2')][1].id")
    with h.page(OSC + "#" + hid) as p:
        p.wait("(id) => !!document.getElementById(id)", hid, 30, "heading")
        p.page.wait_for_timeout(2000)
        r = p.js("""(id) => { const b = document.querySelector('.pyd-pane-explainer .pyd-pane-body');
            return { off: document.getElementById(id).getBoundingClientRect().top - b.getBoundingClientRect().top,
                     atEnd: b.scrollTop + b.clientHeight >= b.scrollHeight - 2, scrolled: b.scrollTop, page: window.scrollY }; }""", hid)
        ensure(r["scrolled"] > 0 and (-5 <= r["off"] <= 120 or r["atEnd"]), f"heading {hid}: {r}")
        ensure(r["page"] == 0, "the page scrolled instead of the explainer")
    # phone width: tabs, and the page itself scrolls
    with h.page(OSC + "#" + hid, viewport={"width": 390, "height": 844}, mobile=True) as p:
        p.wait("(id) => !!document.getElementById(id)", hid, 30, "heading")
        p.page.wait_for_timeout(2000)
        n = p.js("""(id) => ({ top: document.getElementById(id).getBoundingClientRect().top,
            bar: document.querySelector('.pyd-tabbar').getBoundingClientRect().bottom,
            tab: window.pydDemo.layout.activeTab, page: window.scrollY })""", hid)
        ensure(n["tab"] == "explainer" and n["page"] > 0, f"narrow: {n}")
        ensure(n["bar"] - 2 <= n["top"] <= n["bar"] + 150, f"narrow: heading not just below the tab bar: {n}")
        return f"#{hid} at {r['off']:.0f}px from the top of the explainer; at phone width {n['top'] - n['bar']:.0f}px below the tab bar"


# --- D. layout -------------------------------------------------------------------
@check("layout-resize-persist", "Layout", "Dividers drag (mouse and keyboard); sizes and collapsed panes persist; Maximise and Esc work")
def _(h):
    with h.page(OSC) as p:
        p.wait_state("running")
        w0 = p.js("() => document.querySelector('.pyd-pane-explainer').getBoundingClientRect().width")
        box = p.page.locator(".pyd-divider-col").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + 60
        p.page.mouse.move(x, y)
        p.page.mouse.down()
        p.page.mouse.move(x + 75, y, steps=4)
        p.page.mouse.move(x + 150, y, steps=4)
        p.page.mouse.up()
        w1 = p.js("() => document.querySelector('.pyd-pane-explainer').getBoundingClientRect().width")
        ensure(120 <= w1 - w0 <= 180, f"drag moved the divider by {w1 - w0:.0f}px")
        p.page.focus(".pyd-divider-row")
        v0 = int(p.js("() => document.querySelector('.pyd-divider-row').getAttribute('aria-valuenow')"))
        p.page.keyboard.press("ArrowDown")
        v1 = int(p.js("() => document.querySelector('.pyd-divider-row').getAttribute('aria-valuenow')"))
        ensure(v1 == v0 + 2, f"keyboard resize {v0} -> {v1}")
        p.page.click(".pyd-pane-code .pyd-collapse")
        ensure(p.js("() => document.querySelector('.pyd-pane-code .pyd-pane-body').hidden"), "code pane did not collapse")
        p.page.reload()
        p.wait("() => !!document.querySelector('.pyd-pane-explainer')", timeout=30)
        w2 = p.js("() => document.querySelector('.pyd-pane-explainer').getBoundingClientRect().width")
        ensure(abs(w2 - w1) < 6, f"width not persisted: {w1:.0f} -> {w2:.0f}")
        ensure(p.js("() => document.querySelector('.pyd-pane-code .pyd-pane-body').hidden"), "collapse not persisted")
        p.page.click(".pyd-pane-sim .pyd-focus")
        ensure(p.js("() => document.querySelector('.pyd-app').dataset.focus") == "sim", "Maximise")
        ensure(p.js("() => document.querySelector('.pyd-pane-explainer').getBoundingClientRect().width") == 0, "other panes still visible")
        p.page.keyboard.press("Escape")
        ensure(p.js("() => document.querySelector('.pyd-app').dataset.focus") == "", "Esc did not restore")
        p.page.click(".pyd-pane-code .pyd-collapse")
        p.no_errors()
        return f"explainer {w0:.0f}px -> {w1:.0f}px, persisted"


@check("layout-options", "Layout", "panes= and layout= (split, columns, stacked, tabs, single) are honoured")
def _(h):
    res = {}
    for q, expect in (("panes=sim,code", "split"), ("layout=columns", "columns"), ("layout=stacked", "stacked"),
                      ("layout=tabs", "tabs"), ("panes=sim", "single")):
        with h.page(demo(OSC, q + "&load=click")) as p:
            p.wait("() => !!document.querySelector('.pyd-app')", timeout=30)
            info = p.js("""() => ({ layout: document.querySelector('.pyd-app').dataset.layout,
                panes: [...document.querySelectorAll('.pyd-pane')].map(x => x.dataset.pane),
                tops: [...document.querySelectorAll('.pyd-pane')].map(x => Math.round(x.getBoundingClientRect().top)),
                lefts: [...document.querySelectorAll('.pyd-pane')].map(x => Math.round(x.getBoundingClientRect().left)),
                tabbar: !document.querySelector('.pyd-tabbar').hidden })""")
            ensure(info["layout"] == expect, f"{q}: layout {info['layout']}")
            if q == "panes=sim,code":
                ensure(info["panes"] == ["sim", "code"], f"{q}: panes {info['panes']}")
            if expect == "columns":
                ensure(len(set(info["tops"])) == 1 and info["lefts"] == sorted(info["lefts"]), f"columns not side by side {info}")
            if expect == "stacked":
                ensure(info["tops"] == sorted(info["tops"]) and len(set(info["lefts"])) == 1, f"not stacked {info}")
            if expect == "tabs":
                ensure(info["tabbar"], "tab bar hidden")
            if expect == "single":
                ensure(info["panes"] == ["sim"], f"{info}")
            res[q] = info["layout"]
            p.no_errors()
    return ", ".join(f"{k} -> {v}" for k, v in res.items())


@check("narrow-tabs-mini", "Layout", "Phone width: tabs; the mini-player shows the live figure and controls playback from other tabs")
def _(h):
    with h.page(OSC, viewport={"width": 390, "height": 844}, mobile=True) as p:
        p.wait_state("running")
        p.wait_frames(3)
        ensure(p.js("() => document.querySelector('.pyd-app').dataset.layout") == "tabs", "not in tabs mode")
        p.page.click(".pyd-tabbar-tab[data-pane=explainer]")
        p.wait("() => !document.querySelector('.pyd-mini').hidden", timeout=5, what="mini-player")
        p.page.wait_for_timeout(600)
        lit = p.js("""() => { const c = document.querySelector('.pyd-mini-thumb'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                              let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; }""")
        ensure(lit > 100, "mini-player thumbnail is empty")
        p.page.click(".pyd-mini button[aria-label='Pause']")
        ensure(not p.state()["playing"], "mini pause did not pause")
        p.page.click(".pyd-mini button[aria-label='Resume']")
        ensure(p.state()["playing"], "mini resume did not resume")
        p.page.click(".pyd-demo-set >> nth=0")
        p.page.wait_for_timeout(400)
        ensure(p.js("() => window.pydDemo.layout.activeTab") == "explainer", "a demo:set link left the explainer tab")
        p.shot("narrow-explainer-mini")
        p.page.click(".pyd-mini-open")
        ensure(p.js("() => window.pydDemo.layout.activeTab") == "sim", "mini 'Simulator' did not switch tab")
        p.page.wait_for_timeout(300)
        p.shot("narrow-sim")
        p.no_errors()
        return "tabs + mini-player"


@check("reduced-motion", "Layout", "prefers-reduced-motion: animations wait for an explicit play")
def _(h):
    with h.page(OSC, reduced_motion="reduce") as p:
        p.wait_state("running")
        p.page.wait_for_timeout(2000)
        st = p.state()
        ensure(not st["playing"] and st["frame"] <= 2, f"animation autoplayed: {st}")
        p.page.click(".pyd-player button[aria-label='Resume']")
        p.wait_frames(5)
        p.no_errors()
        return "paused until Resume"


@check("a11y-basics", "Accessibility", "Named buttons and inputs, labelled regions, keyboard control of the canvas")
def _(h):
    with h.page(OSC) as p:
        p.wait_state("running")
        p.wait_frames(3)
        r = p.js("""() => {
            const root = document.getElementById('pyd-root');
            const unnamed = [...root.querySelectorAll('button')].filter(b => !(b.getAttribute('aria-label') || b.textContent.trim() || b.title)).map(b => b.className);
            const unlabeled = [...root.querySelectorAll('input, select, textarea')].filter(i => !(i.labels && i.labels.length) && !i.getAttribute('aria-label') && !i.getAttribute('aria-labelledby')).map(i => i.className);
            const regions = [...root.querySelectorAll('section.pyd-pane')].every(s => s.getAttribute('role') === 'region' && document.getElementById(s.getAttribute('aria-labelledby')));
            const canvas = document.querySelector('.pyd-canvas');
            return { unnamed, unlabeled, regions, canvasRole: canvas.getAttribute('role'), canvasLabel: canvas.getAttribute('aria-label'),
                     separators: [...root.querySelectorAll('[role=separator]')].every(s => s.hasAttribute('aria-valuenow')) }; }""")
        ensure(not r["unnamed"], f"buttons without a name: {r['unnamed'][:5]}")
        ensure(not r["unlabeled"], f"inputs without a label: {r['unlabeled'][:5]}")
        ensure(r["regions"] and r["separators"], f"regions/separators: {r}")
        ensure(r["canvasRole"] == "img" and "Space pauses" in (r["canvasLabel"] or ""), f"canvas: {r}")
        p.page.focus(".pyd-canvas")
        p.page.keyboard.press(" ")
        p.page.wait_for_timeout(300)
        ensure(not p.state()["playing"], "Space did not pause")
        f = p.state()["frame"]
        p.page.keyboard.press("ArrowRight")
        p.wait("(f) => (parseInt(window.pydDemo.sim.frameOut.textContent.replace(/\\D+/g, ''), 10) || 0) === f + 1", f, 10, "keyboard step")
        p.page.keyboard.press(" ")
        p.wait_frames(3)
        p.no_errors()
        return "ok"


# --- E. theme ----------------------------------------------------------------------
@check("dark-mode", "Theme", "?theme=dark styles panes and figures; colours the script set are kept (a figure with its own light background stays readable); a live theme switch recolours a running figure")
def _(h):
    with h.page(demo(f"{FIX}/explicit_style/", "theme=dark")) as p:
        p.wait_state("static")
        p.page.wait_for_timeout(300)
        bg = p.js("() => getComputedStyle(document.querySelector('.pyd-pane')).backgroundColor")
        corner = p.pixel(0.01, 0.02)
        ax1 = p.pixel(0.20, 0.30)   # inside the left axes, away from its red line
        ax2 = p.pixel(0.75, 0.75)   # inside the right axes, away from its line
        ensure(max(corner) - min(corner) < 60 and abs(corner[0] - 0xFF) < 8 and abs(corner[1] - 0xEE) < 8, f"explicit figure colour lost: {corner}")
        ensure(abs(ax1[0] - 0xCC) < 10 and abs(ax1[2] - 0xFF) < 10, f"explicit axes colour lost: {ax1}")
        # the script chose a light figure background, so its default-coloured
        # axes and labels follow matplotlib's light defaults, not the dark page
        ensure(min(ax2) > 240, f"default axes on the script's light figure should be white: {ax2}")
        lum = [int(x) for x in re.findall(r"\d+", bg)[:3]]
        ensure(sum(lum) < 200, f"pane background not dark: {bg}")
        p.shot("dark-explicit-style")
        p.no_errors()
    with h.page("/examples/heat_map/") as p:
        p.wait_state("running")
        p.wait_frames(3)
        light = p.pixel(0.005, 0.01)
        p.js("() => document.documentElement.setAttribute('data-theme', 'dark')")
        p.wait("() => { const c = document.querySelector('.pyd-canvas'); const d = c.getContext('2d').getImageData(1, 1, 1, 1).data; return d[0] + d[1] + d[2] < 200; }",
               timeout=15, what="figure recoloured")
        dark = p.pixel(0.005, 0.01)
        p.shot("dark-heat-map")
        p.no_errors()
    return f"figure corner {light} -> {dark}; explicit colours kept"


# --- F. embedding --------------------------------------------------------------------
@check("embed-page", "Embedding", "Page with three <py-demo> embeds: panes= respected, iframes resize to content, theme follows the parent, lazy loading")
def _(h):
    with h.page("/examples/embeds.html", viewport={"width": 1200, "height": 900}) as p:
        p.wait("() => document.querySelectorAll('py-demo iframe').length === 3", timeout=20, what="three iframes")
        frames = p.page.frames
        p.page.wait_for_timeout(1500)
        # scroll each into view so lazy iframes load
        for i in range(3):
            p.js("(i) => document.querySelectorAll('py-demo iframe')[i].scrollIntoView({block: 'center'})", i)
            p.page.wait_for_timeout(1500)
        p.wait("() => [...document.querySelectorAll('py-demo iframe')].every(f => f.contentWindow && f.contentWindow.pydDemo && f.contentWindow.pydDemo.layout)",
               timeout=60, what="demos in all iframes")
        # the first two start by themselves (load=visible) and draw frames
        p.wait("() => [...document.querySelectorAll('py-demo iframe')].slice(0, 2).every(f => f.contentWindow.pydDemo.sim.frames.size > 0)",
               timeout=180, what="frames in the first two embeds")
        p.page.wait_for_timeout(1500)
        info = p.js("""() => [...document.querySelectorAll('py-demo iframe')].map(f => ({
            h: f.getBoundingClientRect().height, doc: Math.ceil(f.contentDocument.documentElement.getBoundingClientRect().height),
            panes: [...f.contentDocument.querySelectorAll('.pyd-pane')].map(x => x.dataset.pane),
            embed: f.contentDocument.documentElement.classList.contains('pyd-embed'),
            header: !!f.contentDocument.querySelector('body > header, .site-header') && getComputedStyle(f.contentDocument.querySelector('body > header, .site-header')).display !== 'none',
            state: f.contentWindow.pydDemo.state }))""")
        ensure([i["panes"] for i in info] == [["sim"], ["sim", "code"], ["explainer", "sim"]], f"panes {[i['panes'] for i in info]}")
        ensure(all(i["embed"] and not i["header"] for i in info), f"embed chrome: {info}")
        ensure(all(abs(i["h"] - i["doc"]) <= 4 for i in info), f"iframe heights don't match content: {[(i['h'], i['doc']) for i in info]}")
        ensure(info[2]["state"] in ("idle",), f"load=click embed started by itself: {info[2]['state']}")
        p.js("() => document.documentElement.setAttribute('data-theme', 'dark')")
        p.wait("() => [...document.querySelectorAll('py-demo iframe')].every(f => f.contentDocument.documentElement.getAttribute('data-theme') === 'dark')",
               timeout=10, what="theme sync")
        p.wait("""() => { const f = document.querySelectorAll('py-demo iframe')[1]; const c = f.contentDocument.querySelector('.pyd-canvas');
                  const d = c.getContext('2d').getImageData(1, 1, 1, 1).data; return d[0] + d[1] + d[2] < 200; }""",
               timeout=20, what="embedded figure recoloured")
        p.page.wait_for_timeout(800)
        p.shot("embeds-dark", full_page=True)
        p.no_errors()
        return f"heights {[round(i['h']) for i in info]}"


@check("embed-cross-origin", "Security", "A page on another origin cannot drive an embed (theme messages ignored, no messages posted to it)")
def _(h):
    with h.page(None) as p:
        p.goto(h.alt_base + "/_pyd_tests/browser/foreign_parent.html?target=" + urllib.parse.quote(h.base))
        p.wait("() => window.__pydForeign && window.__pydForeign.loaded", timeout=30, what="foreign parent")
        p.page.wait_for_timeout(1500)
        p.js("() => window.__pydForeign.sendTheme('dark')")
        p.page.wait_for_timeout(1000)
        netloc = urllib.parse.urlsplit(h.base).netloc
        frame = [f for f in p.page.frames if urllib.parse.urlsplit(f.url).netloc == netloc][0]
        theme = frame.evaluate("() => document.documentElement.getAttribute('data-theme')")
        got = p.js("() => window.__pydForeign.received")
        ensure(theme != "dark", "foreign theme message was obeyed")
        ensure(not got, f"embed posted to a foreign parent: {got}")
        return "ignored"


# --- G. static checks -------------------------------------------------------------------
@check("static-scan", "Security", "No innerHTML/eval/new Function/document.write in the demonstrator; CDN scripts pinned with integrity hashes")
def _(h):
    bad = []
    for f in sorted(JS_DIR.glob("*.js")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//")[0]
            if re.search(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write|\beval\s*\(|new Function\s*\(|setTimeout\s*\(\s*['\"]", code):
                bad.append(f"{f.name}:{n}")
    ensure(not bad, f"unsafe DOM/eval uses: {bad}")
    cfg = (JS_DIR / "config.js").read_text(encoding="utf-8")
    libs = re.findall(r"/npm/(@?[\w.-]+(?:/[\w.-]+)?)@([^/]+)/", cfg)
    ensure(libs and all(re.match(r"\d+\.\d+\.\d+$", v) for _, v in libs), f"unpinned library versions: {libs}")
    ensure(re.search(r'PYODIDE_VERSION = "\d+\.\d+\.\d+"', cfg), "Pyodide version not pinned")
    ensure(cfg.count("integrity:") >= 3, "missing SRI hashes")
    pinned = sorted({f"{n}@{v}" for n, v in libs})
    return f"{len(list(JS_DIR.glob('*.js')))} modules scanned; pinned: {', '.join(pinned)}"


# ===========================================================================
def build_site(out: Path):
    cmd = [sys.executable, "-m", "pelican", "content", "-o", str(out), "-s", "pelicanconf.py"]
    print("building:", " ".join(cmd))
    r = subprocess.run(cmd, cwd=QUICK, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-3000:], r.stderr[-3000:])
        raise SystemExit("Pelican build failed")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--site", help="built site folder (e.g. quick-start/output)")
    src.add_argument("--build", action="store_true", help="build the site with Pelican into a temporary folder first")
    ap.add_argument("--only", help="comma-separated check ids")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--slow", type=float, default=1.0, help="multiply timeouts (slow machines)")
    ap.add_argument("--cdn-mirror")
    ap.add_argument("--python-fetch")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--report")
    ap.add_argument("--shots", default=None, help="folder for screenshots")
    ap.add_argument("--serve", action="store_true", help="only start the server and print URLs")
    ap.add_argument("--list", action="store_true", help="list the checks")
    args = ap.parse_args()

    if args.list:
        for c in CHECKS:
            print(f"{c['id']:24} {c['area']:14} {c['title']}")
        return 0

    tmp = None
    if args.site:
        site = Path(args.site).resolve()
    else:
        tmp = Path(tempfile.mkdtemp(prefix="pyd-e2e-"))
        site = tmp / "site"
        build_site(site)
    if not (site / "demonstrator.html").exists():
        raise SystemExit(f"{site} does not look like a built site (no demonstrator.html); use --build")

    server, base = start_server(site, HERE)
    alt_server, alt_base = start_server(site, HERE)  # a second origin for cross-origin tests
    print(f"serving {site} at {base} (second origin {alt_base})")
    if args.serve:
        print(f"  demos:      {base}/demonstrator.html")
        print(f"  examples:   {base}/examples/oscillator/  ...")
        print(f"  fixtures:   {base}/demonstrator.html?demo={FIX}/<name>/")
        print(f"  unit tests: {base}/_pyd_tests/browser/unit.html")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    wanted = set(args.only.split(",")) if args.only else None
    unknown = (wanted or set()) - {c["id"] for c in CHECKS}
    if unknown:
        raise SystemExit(f"unknown check ids: {sorted(unknown)}")
    harness = Harness(args, base, alt_base)
    results = []
    t_all = time.time()
    try:
        for c in CHECKS:
            if wanted and c["id"] not in wanted:
                continue
            t0 = time.time()
            status, detail = "PASS", ""
            try:
                detail = c["fn"](harness) or ""
            except Skip as e:
                status, detail = "SKIP", str(e)
            except CheckFailed as e:
                status, detail = "FAIL", str(e)
            except Exception as e:  # noqa: BLE001
                status, detail = "FAIL", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
            dt = time.time() - t0
            results.append({**{k: c[k] for k in ("id", "area", "title")}, "status": status, "detail": detail, "seconds": round(dt, 1)})
            print(f"[{status}] {c['id']:24} {dt:6.1f}s  {detail.splitlines()[0][:150] if detail else ''}", flush=True)
    finally:
        harness.close()
        server.shutdown()
        alt_server.shutdown()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    n = {s: sum(r["status"] == s for r in results) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\n{n['PASS']} passed, {n['FAIL']} failed, {n['SKIP']} skipped in {time.time() - t_all:.0f} s")
    if args.report:
        write_report(Path(args.report), results, args)
    return 1 if n["FAIL"] else 0


def write_report(path: Path, results, args):
    import platform
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        version = b.version
        b.close()
    lines = [
        "# Demonstrator end-to-end checklist: results",
        "",
        f"Run {time.strftime('%Y-%m-%d %H:%M %Z')} on {platform.system()} {platform.machine()}, "
        f"Python {platform.python_version()}, headless Chromium {version}"
        + (", CDN served from a local mirror" if args.cdn_mirror else "")
        + (f", {args.python_fetch} fetched via Python" if args.python_fetch else "") + ".",
        "",
        "| # | Check | Area | Result | Time | Details |",
        "|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        detail = (r["detail"] or "").splitlines()[0].replace("|", "\\|")[:220] if r["detail"] else ""
        lines.append(f"| {i} | **{r['id']}**: {r['title']} | {r['area']} | {r['status']} | {r['seconds']} s | {detail} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report written to {path}")


if __name__ == "__main__":
    sys.exit(main())
