/* Python demonstrator -- the application: wires the panes, the worker and
 * the controls together.
 *
 * Life cycle
 *   1. read options (page data attributes, demo.json, query string)
 *   2. build the panes; render the explainer (no Python needed)
 *   3. when the demo nears the viewport (or Run is clicked): start the
 *      worker, write the project files, detect parameters (controls appear),
 *      install packages, run the script
 *   4. frames stream from the worker; the Player paces animations
 *   5. control changes: live values are written into the running modules
 *      between frames; restart values re-run the script in the warm worker
 */
import { DEFAULTS, EMBED_PARENT_ORIGINS } from "./config.js";
import { ConsolePane } from "./console.js";
import { CodePane } from "./codepane.js";
import { ControlPanel } from "./controls.js";
import { coerceForParam, findParam } from "./demolinks.js";
import { Explainer, parseFrontMatter, preloadExplainerLibs } from "./explainer.js";
import { Layout } from "./layout.js";
import { DemoError, explainerCandidates, fetchProjectFiles, mergeOptions, normalisePresets, resolveDemo } from "./manifest.js";
import { Player, RuntimeClient } from "./runtime.js";
import { Simulator } from "./simulator.js";
import { applyThemeOption, syncSchemeAttr, themeRc, watchTheme } from "./theme.js";
import { copyText, debounce, h, icon, prefersReducedMotion } from "./util.js";

const STAGES = [
  ["pyodide", "Download Python"],
  ["files", "Load the project"],
  ["detect", "Detect parameters"],
  ["packages", "Install packages"],
  ["start", "Start the script"],
];
const STAGE_PROGRESS = { pyodide: 0.08, runtime: 0.3, files: 0.4, detect: 0.45, packages: 0.6, start: 0.9 };

function withTimeout(promise, ms) {
  return Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms))]);
}

export class DemoApp {
  constructor(root, page) {
    this.root = root;
    this.page = page;
    this.embed = Boolean(page.embed);
    this.runtime = new RuntimeClient({ version: page.version, noJspi: page.debugJspi === "0" });
    this.player = new Player((dt, force, params, paramsOnly, snap) => this.tick(dt, force, params, paramsOnly, snap));
    this.values = {};
    this.defaults = {};
    this.det = null;
    this.files = null;
    this.state = "idle";
    this.scriptRunning = false;
    this.userPaused = false;
    this.booted = null;
    this.queuedActions = [];
    this.themeRc = null;
    this.reducedMotion = prefersReducedMotion();
    this.restartSoon = debounce((why) => this.restart(why), 350);
  }

  /* ======================================================================
   * start-up
   * ==================================================================== */
  async start() {
    const setVw = () => document.documentElement.style.setProperty("--pyd-vw", `${document.documentElement.clientWidth}px`);
    setVw();
    window.addEventListener("resize", setVw);
    syncSchemeAttr();
    preloadExplainerLibs();
    this.root.classList.add("pyd-root");
    const status = h("p", { class: "pyd-boot", role: "status" }, "Loading the demo…");
    this.mount = this.root.querySelector("[data-pyd-mount]") || this.root;
    this.mount.replaceChildren(status);
    let demo;
    try {
      demo = await resolveDemo(this.page);
    } catch (e) {
      this.fail(e);
      return;
    }
    this.demo = demo;
    for (const w of demo.warnings) console.warn("[pyd]", w);
    const warned = demo.warnings.length;
    this.opts = mergeOptions(this.page, demo.manifest);
    applyThemeOption(this.opts.theme);
    if (this.embed) this.setupEmbed();

    // explainer text first (small), project files in parallel
    const wantExplainer = this.opts.panes.includes("explainer");
    const explainerP = wantExplainer ? this.fetchExplainer(explainerCandidates(demo)) : Promise.resolve(null);
    this.filesP = fetchProjectFiles(demo).then(
      (files) => (this.files = files),
      (e) => {
        throw e;
      }
    );
    this.filesP.catch(() => {});
    let explainer = null;
    try {
      explainer = await explainerP;
    } catch (e) {
      explainer = { error: e.message };
    }
    this.applyMeta(demo.manifest, explainer && explainer.text ? parseFrontMatter(explainer.text).meta : {});
    const panes = this.opts.panes.filter((p) => p !== "explainer" || explainer);
    if (!panes.length) panes.push("sim");
    this.buildPanes(panes);
    if (explainer) this.renderExplainer(explainer);

    try {
      await this.filesP;
    } catch (e) {
      this.fail(e);
      return;
    }
    for (const w of demo.warnings.slice(warned)) console.warn("[pyd]", w);
    this.buildCodePane();
    this.presets = normalisePresets(demo.manifest.presets);
    this.scheduleBoot();
    this.watchTheme();
    if (location.hash) this.deepLink(location.hash.slice(1));
    window.addEventListener("hashchange", () => this.deepLink(location.hash.slice(1)));
  }

  async fetchExplainer(candidates) {
    for (const url of candidates) {
      let res;
      try {
        res = await fetch(url, { cache: "no-cache" });
      } catch (e) {
        throw new Error("The explainer could not be downloaded (network error).");
      }
      if (res.ok) return { url, text: await res.text() };
      if (res.status !== 404) throw new Error(`The explainer could not be downloaded (HTTP ${res.status}).`);
    }
    return null;
  }

  applyMeta(manifest, fm) {
    const title = this.page.title || manifest.title || fm.title || this.demo.id.split("/").pop().replace(/[_-]+/g, " ");
    this.title = title;
    const desc = manifest.description || fm.summary || "";
    if (!this.page.title || this.page.generic) {
      document.title = this.page.siteName ? `${title} · ${this.page.siteName}` : title;
      const h1 = document.querySelector("[data-pyd-title]");
      if (h1) h1.textContent = title;
      const setMeta = (sel, val) => {
        const m = document.querySelector(sel);
        if (m && val) m.setAttribute("content", val);
      };
      setMeta('meta[name="description"]', desc);
      setMeta('meta[property="og:title"]', title);
      setMeta('meta[property="og:description"]', desc);
    }
    const sub = document.querySelector("[data-pyd-summary]");
    if (sub && desc && (this.page.generic || !sub.textContent.trim())) sub.textContent = desc;
    this.setupBack(manifest);
  }

  /** The site's back-link pattern; target from the manifest, the query
   * string or page metadata, else history/referrer, else /#projects. */
  setupBack(manifest) {
    const a = document.querySelector("[data-pyd-back]");
    if (!a) return;
    const siteRoot = new URL(this.page.siteRoot || "/", location.href);
    const safe = (u) => {
      try {
        const url = new URL(u, siteRoot);
        return (url.protocol === "http:" || url.protocol === "https:") && url.origin === location.origin ? url.href : null;
      } catch (e) {
        return null;
      }
    };
    const explicit = (this.page.back && safe(this.page.back)) || (manifest.back && safe(manifest.back.url));
    const label = this.page.backLabel || (manifest.back && manifest.back.label);
    if (explicit) {
      a.href = explicit;
      if (label) a.lastChild.textContent = ` ${label}`;
      return;
    }
    let ref = null;
    try {
      ref = document.referrer && new URL(document.referrer).origin === location.origin ? document.referrer : null;
    } catch (e) {
      ref = null;
    }
    if (ref && ref !== location.href) {
      a.href = ref;
      a.lastChild.textContent = " Back";
      a.addEventListener("click", (e) => {
        if (history.length > 1) {
          e.preventDefault();
          history.back();
        }
      });
    }
  }

  fail(e) {
    console.error("[pyd]", e);
    const msg = e instanceof DemoError ? e.message : `Something went wrong while loading the demo: ${e.message || e}`;
    const box = h(
      "div",
      { class: "pyd-error-box", role: "alert" },
      h("p", { class: "pyd-error-title" }, "This demo couldn't be loaded"),
      h("p", null, msg),
      e.detail ? h("p", { class: "pyd-error-detail" }, h("code", null, e.detail)) : null
    );
    this.mount.replaceChildren(box);
    this.postSize();
  }

  /* ======================================================================
   * panes
   * ==================================================================== */
  buildPanes(panes) {
    this.mount.replaceChildren();
    this.layout = new Layout(this.mount, {
      panes,
      layout: this.opts.layout,
      storageKey: `pyd:v1:layout:${this.demo.id}`,
      embed: this.embed,
      miniEnabled: () => Boolean(this.booted),
      onChange: (mode) => {
        this.postSize();
        if (this.explainer && mode !== this._lastMode) this.explainer.relayout();
        this._lastMode = mode;
      },
    });
    const L = this.layout;
    const hgt = parseInt(this.page.height, 10);
    if (hgt > 0) L.app.style.setProperty("--pyd-app-h", `${Math.max(300, Math.min(2000, hgt))}px`);
    if (L.panes.explainer) {
      L.panes.explainer.body.appendChild(h("p", { class: "pyd-loading-text" }, "Loading the explainer…"));
    }
    if (L.panes.sim) {
      this.sim = new Simulator(
        L.panes.sim.body,
        {
          onRun: () => this.onRun(),
          onStop: () => this.stop(),
          onPause: () => this.pause(),
          onResume: () => this.resume(),
          onStep: () => this.step(),
          onRestart: () => this.restart("manual"),
          onSpeed: (v) => this.setSpeed(v),
          onViewport: (v) => this.onViewport(v),
          onPointer: (m) => this.onPointer(m),
          onSelectFigure: (id) => this.runtime.alive && this.runtime.send("visible", { ids: [id] }),
          wheelEnabled: () => document.activeElement === this.sim.canvas,
        },
        { title: this.title }
      );
      this.sim.onPainted = () => this.layout.paintMini(this.sim.canvas);
      this.controls = new ControlPanel(this.sim.paramsEl, {
        onChange: (id, v, phase) => this.onControl(id, v, phase),
        onHover: (p) => this.codePane && this.codePane.showParam(p, Boolean(p)),
        onLocate: (p) => this.showCode(p.file, p.line, p.end_line || p.line),
        onReset: () => this.resetValues(),
        onPreset: (name) => this.applyPreset(name),
        onButton: (b) => this.callButton(b),
        onCopyLink: (btn) => this.copyLink(btn),
      });
      L.miniPlay.addEventListener("click", () => (this.player.playing ? this.pause() : this.resume()));
      L.miniStep.addEventListener("click", () => this.step());
      L.miniRestart.addEventListener("click", () => this.restart("manual"));
      this.player.on("state", () => this.syncPlayerUi());
    }
    // console: under the code if there is a code pane, else under the simulator
    const consoleHost = h("div", { class: "pyd-console" });
    if (L.panes.code) L.panes.code.body.appendChild(consoleHost);
    else if (L.panes.sim) this.sim.simEl.parentNode.appendChild(consoleHost);
    this.console = new ConsolePane(consoleHost, {
      onLocate: (file, line) => this.showCode(file, line, line, "is-error"),
      onToggle: () => this.postSize(),
    });
    this.wireRuntimeEvents();
    if (this.embed) this.postSize();
  }

  buildCodePane() {
    const L = this.layout;
    if (!L.panes.code) return;
    const holder = h("div", { class: "pyd-code-holder" });
    L.panes.code.body.prepend(holder);
    this.codePane = new CodePane(holder, L.panes.code.extra, {
      files: this.files,
      main: this.demo.main,
      editable: this.opts.editable,
      zipName: this.demo.id.split("/").filter(Boolean).pop() || "project",
      onRunEdited: (files, how) => this.runEdited(files, how),
      onLineHover: (file, line) => {
        const p = line && this.det ? this.det.params.find((x) => x.file === file && x.line === line) : null;
        if (this.controls) this.controls.highlightParam(p ? p.id : null, Boolean(p));
      },
      onLineActivate: (file, line) => {
        const p = this.det && this.det.params.find((x) => x.file === file && x.line === line);
        if (p) this.focusParam(p.id);
      },
    });
  }

  async renderExplainer(found) {
    const L = this.layout;
    const pane = L.panes.explainer;
    if (!pane) return;
    if (found.error) {
      pane.body.replaceChildren(h("p", { class: "pyd-loading-text is-error" }, found.error));
      return;
    }
    pane.body.replaceChildren();
    pane.body.classList.add("is-scroller");
    this.explainer = new Explainer(pane.body, {
      tocMode: this.opts.toc,
      stickyOffset: () => (this.layout.mode === "tabs" ? this.layout.tabbar.getBoundingClientRect().height + 12 : 0),
      onAction: (action, el) => this.onDemoAction(action, el),
      onSectionShown: () => {},
    });
    // tour toggle in the pane header
    try {
      await this.explainer.renderText(found.text, found.url);
    } catch (e) {
      console.error("[pyd] explainer", e);
      pane.body.replaceChildren(h("p", { class: "pyd-loading-text is-error" }, "The explainer could not be shown: " + (e.message || e)));
      return;
    }
    if (this.explainer.steps.length) {
      const tour = h("button", { type: "button", class: "pyd-tool", "aria-pressed": "false", title: "Guided tour: apply each step as you scroll" }, icon("tour"), h("span", { class: "pyd-tool-label" }, "Tour"));
      tour.addEventListener("click", () => {
        const on = tour.getAttribute("aria-pressed") !== "true";
        tour.setAttribute("aria-pressed", String(on));
        this.explainer.setTour(on);
      });
      pane.extra.appendChild(tour);
      const tourMode = this.explainer.meta.tour !== undefined ? this.explainer.meta.tour : this.demo.manifest.tour;
      if (String(tourMode).toLowerCase() === "auto") tour.click();
    }
    if (this.det) this.validateExplainer();
    if (location.hash) this.deepLink(location.hash.slice(1));
    this.postSize();
  }

  async deepLink(id) {
    if (!id || !this.explainer) return;
    let target;
    try {
      target = decodeURIComponent(id);
    } catch (e) {
      return;
    }
    if (this.layout.mode === "tabs") this.layout.showTab("explainer", false);
    if (!this.explainer.scrollTo(target, true, false)) return;
    // Images above the heading may still be loading and push it down:
    // jump again once they have loaded, unless the reader has scrolled.
    const body = this.explainer.body;
    const pending = [...body.querySelectorAll("img")].filter((i) => !i.complete);
    if (!pending.length) return;
    let moved = false;
    const mark = () => (moved = true);
    for (const ev of ["wheel", "touchstart", "keydown", "pointerdown"]) body.addEventListener(ev, mark, { once: true, passive: true });
    for (const img of pending) img.loading = "eager";
    await Promise.race([Promise.all(pending.map((i) => i.decode().catch(() => {}))), new Promise((r) => setTimeout(r, 5000))]);
    if (!moved) this.explainer.scrollTo(target, false, false);
  }

  validateExplainer() {
    if (!this.explainer || !this.det) return;
    this.explainer.validateLinks({
      params: this.det.params,
      presets: this.presets || [],
      files: this.codePane ? this.codePane.lineCounts() : Object.fromEntries((this.files || []).map((f) => [f.path, f.text ? f.text.split("\n").length : 0])),
    });
  }

  /* ======================================================================
   * booting the runtime (lazy)
   * ==================================================================== */
  scheduleBoot() {
    if (!this.sim) return;
    const unsupported = RuntimeClient.supported();
    if (unsupported) {
      this.sim.showMessage("Python can't run here", unsupported, { kind: "error" });
      this.sim.setState("error-start");
      this.controls.renderError("Controls need Python, which this browser can't run.");
      return;
    }
    const note = "Runs Python in your browser with Pyodide (about 20 MB the first time; cached afterwards).";
    this.sim.showStart({ title: this.title, note, onStart: () => this.onRun() });
    this.controls.renderSkeleton("Controls appear once Python has read the code.");
    if (this.opts.load === "eager") this.boot(this.opts.autorun);
    else if (this.opts.load === "visible" && "IntersectionObserver" in window) {
      const io = new IntersectionObserver(
        (entries) => {
          if (entries.some((e) => e.isIntersecting)) {
            io.disconnect();
            this.boot(this.opts.autorun);
          }
        },
        { rootMargin: "200px 0px" }
      );
      io.observe(this.layout.panes.sim.el);
    }
  }

  onRun() {
    if (!this.booted) {
      this.boot(true);
      return;
    }
    this.booted.then(() => this.run({ play: true })).catch(() => {});
  }

  boot(runAfter) {
    if (this.booted) {
      if (runAfter) this.booted.then(() => this.run({ play: true })).catch(() => {});
      return this.booted;
    }
    this.setState("loading");
    this.sim.showLoading(STAGES, "pyodide", 0.03);
    this.booted = (async () => {
      const t0 = performance.now();
      await this.runtime.spawn();
      this.console.note(`Python ${this.runtime.info.python} (Pyodide ${this.runtime.info.pyodide}) ready in ${((performance.now() - t0) / 1000).toFixed(1)} s${this.runtime.info.jspi ? "" : " — this browser can't pause blocking plt.pause() loops"}.`);
      this.loadingStage("files");
      await this.filesP;
      await this.runtime.request("load", { files: this.fileMessages(this.files) });
      await this.detect();
      this.loadingStage("packages");
      const inst = await this.runtime.request("install", { packages: this.demo.manifest.packages || [], micropip: this.opts.micropip });
      this.reportMissing(inst.missing);
    })();
    this.booted.then(
      () => {
        this.setState("ready");
        this.layout.updateMini();
        const autoplay = runAfter && !(this.reducedMotion && !this.explicitRun);
        if (runAfter) this.run({ play: autoplay });
        else {
          this.sim.showStart({ title: this.title, note: "Python is ready.", onStart: () => this.run({ play: true }) });
          this.sim.setState("stopped");
        }
      },
      (e) => this.bootFailed(e)
    );
    return this.booted;
  }

  bootFailed(e) {
    if (e && e.stopped) return;
    console.error("[pyd] boot", e);
    this.setState("error-start");
    const retry = h("button", { type: "button", class: "pyd-btn pyd-btn-primary" }, "Try again");
    retry.addEventListener("click", () => {
      this.booted = null;
      this.boot(true);
    });
    this.sim.showMessage("Python could not start", (e && e.message) || String(e), { kind: "error", actions: [retry] });
    if (!this.det) this.controls.renderError("Controls unavailable: Python did not start.");
  }

  loadingStage(stage, message) {
    if (this.state !== "loading") return;
    this.sim.showLoading(STAGES, stage, STAGE_PROGRESS[stage] || 0.5);
    if (message) this.sim.setStatus(message);
  }

  fileMessages(files) {
    return files.map((f) => ({ path: f.path, data: f.binary ? f.bytes.slice().buffer : f.text }));
  }

  async detect() {
    this.loadingStage("detect");
    let det;
    try {
      det = await this.runtime.request("detect", {
        main: this.demo.main,
        overrides: { parameter_overrides: this.demo.manifest.parameter_overrides || {}, buttons: this.demo.manifest.buttons || [] },
        options: { max_controls: this.opts.maxControls, max_other: this.opts.maxOther },
      });
    } catch (e) {
      // detection must fail soft: the demo still runs without controls
      console.warn("[pyd] parameter detection failed", e);
      const last = String((e && e.message) || e).trim().split("\n").pop();
      det = { params: [], buttons: [], derived: [], skipped: [], errors: [{ file: this.demo.main, line: 0, message: `detection failed (${last})` }], notes: [] };
    }
    this.setDetection(det);
    return det;
  }

  setDetection(det, keepValues = true) {
    const old = this.values;
    const oldDefaults = this.defaults;
    const same = (a, b) => (Array.isArray(a) || Array.isArray(b) ? JSON.stringify(a) === JSON.stringify(b) : a === b);
    this.det = det;
    this.defaults = {};
    const values = {};
    for (const p of det.params) {
      this.defaults[p.id] = p.default;
      // Keep a value the visitor chose, unless the code itself now says
      // something different (an edited literal wins over the old control).
      const chosen = old[p.id] !== undefined && !same(old[p.id], oldDefaults[p.id]);
      const codeChanged = p.id in oldDefaults && !same(p.default, oldDefaults[p.id]);
      values[p.id] = keepValues && chosen && !codeChanged ? old[p.id] : p.default;
    }
    if (!this.initialApplied) {
      this.initialApplied = true;
      if (this.page.preset) {
        const pr = (this.presets || []).find((x) => x.name === this.page.preset);
        if (pr) Object.assign(values, this.presetValues(pr));
      }
      for (const [k, raw] of Object.entries(this.page.values || {})) {
        const p = findParam(det.params, k);
        if (!p) {
          console.warn(`[pyd] link value for unknown parameter ${k} ignored`);
          continue;
        }
        const c = coerceForParam(p, raw);
        if (c.ok) values[p.id] = c.value;
        else console.warn(`[pyd] ${c.reason}`);
      }
    }
    this.values = values;
    if (this.controls) this.controls.setParams(det, values, this.presets || []);
    if (this.codePane) this.codePane.setParamLines(det.params);
    for (const e of det.errors || []) this.console.note(`Parameter detection: ${e.file}${e.line ? `:${e.line}` : ""}: ${e.message}`);
    // Without JSPI a plt.pause() loop can't take live values: re-run instead.
    if (this.runtime.info && this.runtime.info.jspi === false && det.blocking_loops && det.blocking_loops.length) this.downgradeLoopParams();
    this.validateExplainer();
    const q = this.queuedActions;
    this.queuedActions = [];
    for (const [a, el] of q) this.onDemoAction(a, el);
  }

  reportMissing(missing) {
    const names = Object.keys(missing || {});
    if (!names.length) return;
    for (const n of names) this.console.note(`Package "${n}" is not available in the browser: ${missing[n]}`);
  }

  /* ======================================================================
   * running
   * ==================================================================== */
  setState(s) {
    this.state = s;
    if (!this.sim) return;
    this.sim.setState(s, this.playerFlags());
    const labels = { loading: "Loading…", ready: "Ready", running: "Running", static: "Static figure", finished: "Animation finished", stopped: "Stopped", error: "Error", restarting: "Restarting…", "error-start": "Not running" };
    this.sim.setStatus(labels[s] || "");
    if (this.layout) this.layout.app.dataset.state = s;
  }

  /** Pause/step availability for the playback bar.  Animations driven by
   * timers pause in the page; a script still running (a plt.pause() loop)
   * can only be paused when it can suspend (JSPI). */
  playerFlags() {
    const looping = this.scriptRunning;
    const animating = this.player.timers || looping;
    const pausable = this.player.timers || (looping && Boolean(this.runtime.info && this.runtime.info.jspi));
    const paused = looping ? Boolean(this.blockingPaused) : !this.player.playing;
    return { paused: paused && animating, timers: animating && pausable };
  }

  syncPlayerUi() {
    if (!this.sim) return;
    const flags = this.playerFlags();
    const paused = flags.paused;
    const animating = this.player.timers || this.scriptRunning;
    this.sim.setState(this.state, flags);
    if (this.state === "running" && animating) this.sim.setStatus(paused ? "Paused" : "Running");
    if (paused) this.sim.setFps(0);
    const playing = this.player.playing || (this.scriptRunning && !this.blockingPaused);
    this.layout.miniPlay.replaceChildren(icon(playing ? "pause" : "play"));
    this.layout.miniPlay.setAttribute("aria-label", playing ? "Pause" : "Resume");
  }

  runConfig() {
    const vp = this.sim ? this.sim.viewport() : { width: 640, height: 400, dpr: 1 };
    this.themeRc = themeRc();
    return {
      main: this.demo.main,
      params: this.det ? this.det.params : [],
      values: this.values,
      viewport: { ...vp, quality: this.opts.quality },
      theme_rc: this.themeRc,
      speed: this.player.speed,
      fps: this.opts.fps,
    };
  }

  async run({ play = true } = {}) {
    const ticket = (this.runTicket = (this.runTicket || 0) + 1);
    for (let attempt = 0; attempt < 3; attempt++) {
      // A script still starting up (or in a plt.pause loop) is asked to stop
      // and given time to finish, so the warm worker can be reused...
      if (this.scriptRunning) await this.stopScript({ respawn: true, patience: this.workerBlocked() ? 300 : 6000 });
      // ...or it was killed, and a replacement worker is still starting.
      if (this.warm) await this.warm;
      if (!this.runtime.alive) await this.warmUp();
      if (ticket !== this.runTicket) return; // a newer run() took over
      if (!this.scriptRunning) break;
    }
    this.player.stop();
    this.player.fps = this.opts.fps;
    const seq = (this.runSeq = (this.runSeq || 0) + 1);
    this.firstFrameSeen = false;
    this.scriptRunning = true;
    this.blockingPaused = false;
    if (this.state !== "restarting") this.setState("running");
    this.syncPlayerUi();
    this.sim.hideOverlay();
    this.armTimeout(seq);
    let result;
    try {
      this.runPromise = this.runtime.request("run", { config: this.runConfig() });
      result = await this.runPromise;
    } catch (e) {
      this.scriptRunning = false;
      if (e.stopped) return;
      this.console.note(`Run failed: ${e.message}`);
      this.setState("error");
      return;
    } finally {
      if (seq === this.runSeq) {
        this.scriptRunning = false;
        clearTimeout(this.timeoutT);
      }
    }
    if (seq !== this.runSeq) return;
    if (result.jspi === false && this.det && this.det.blocking_loops && this.det.blocking_loops.length) this.downgradeLoopParams();
    if (result.outcome === "stop" || result.outcome === "restart") return;
    if (result.figures) this.setFigures(result.figures);
    if (this.sim.activeFig !== null) this.runtime.send("visible", { ids: [this.sim.activeFig] });
    const animated = result.timers > 0;
    this.player.setTimers(animated, result.next_due_in);
    if (result.outcome === "error") {
      this.setState("error");
      if (!animated) return;
    }
    if (animated) {
      this.setState(result.outcome === "error" ? "error" : "running");
      if (play && !this.userPaused) this.player.play();
      else this.player.pause();
    } else if (result.outcome !== "error") {
      this.setState(this.sim.figures.length ? "static" : "finished");
      if (!this.sim.figures.length) this.sim.showMessage("The script finished", "It didn't draw any figures. Its output is in the console.", { kind: "info" });
    }
    this.syncPlayerUi();
    this.sim.hideNotice();
  }

  armTimeout(seq) {
    clearTimeout(this.timeoutT);
    this.timeoutT = setTimeout(() => {
      if (seq !== this.runSeq || !this.scriptRunning || this.firstFrameSeen) return;
      const stop = h("button", { type: "button", class: "pyd-btn" }, "Stop it");
      stop.addEventListener("click", () => this.stop());
      this.sim.showNotice(`The script has been running for ${this.opts.timeout} s without showing anything; it may be stuck in a long loop.`, { kind: "warn", action: stop });
    }, this.opts.timeout * 1000);
  }

  /** Is the worker stuck in a blocking loop it can't suspend (no JSPI)? */
  workerBlocked() {
    return Boolean(this.scriptRunning && this.runtime.info && this.runtime.info.jspi === false && this.det && this.det.blocking_loops && this.det.blocking_loops.length);
  }

  downgradeLoopParams() {
    for (const p of this.det.params) {
      if (p.mode === "live" && p.live_via && p.live_via.length && p.live_via.every((v) => v === "loop")) {
        p.mode = "restart";
        p.mode_reason = "This browser can't update a running plt.pause() loop, so changes re-run the script.";
        this.controls.setMode(p.id, "restart", p.mode_reason);
      }
    }
  }

  async tick(dt, force, params, paramsOnly, snap = 0) {
    if (!this.runtime.alive) return null;
    try {
      if (paramsOnly) {
        const r = await this.runtime.request("set-params", { values: params });
        if (r.errors && r.errors.length) for (const e of r.errors) console.warn("[pyd]", e);
        return r;
      }
      const r = await this.runtime.request("tick", { dt, force, params, snap });
      if (r.fired) {
        const st = (this.stats = this.stats || { ticks: 0, ms: 0 });
        st.ticks += 1;
        st.ms = st.ms * 0.9 + (r.ms || 0) * 0.1;
      }
      if (r.error) this.setState("error");
      else if (!r.running && this.state === "running") {
        this.setState("finished");
        this.syncPlayerUi();
      }
      return r;
    } catch (e) {
      if (!e.stopped) console.warn("[pyd] tick", e);
      return null;
    }
  }

  pause() {
    this.userPaused = true;
    if (this.scriptRunning) {
      this.blockingPaused = true;
      this.runtime.send("pause", { paused: true });
    }
    this.player.pause();
    this.syncPlayerUi();
  }

  resume() {
    this.userPaused = false;
    if (this.state === "finished" || this.state === "static" || this.state === "stopped") {
      this.restart("manual");
      return;
    }
    if (this.scriptRunning) {
      this.blockingPaused = false;
      this.runtime.send("pause", { paused: false });
    }
    if (this.player.timers) this.player.play();
    if (this.state === "error") this.setState("running");
    this.syncPlayerUi();
  }

  step() {
    if (this.scriptRunning) {
      this.runtime.send("step");
      return;
    }
    if (this.player.playing) this.pause();
    this.player.step();
  }

  setSpeed(v) {
    this.player.speed = v;
    if (this.runtime.alive) this.runtime.send("speed", { speed: v });
  }

  async stop() {
    this.player.stop();
    this.userPaused = false;
    await this.stopScript({ respawn: true });
    this.setState("stopped");
    this.sim.hideNotice();
    this.syncPlayerUi();
  }

  /** Stop a running script: politely if the worker answers within
   * `patience` ms (the reply comes after any script still executing has
   * finished), otherwise kill the worker and start a warm replacement in the
   * background. */
  async stopScript({ respawn, patience = 700 }) {
    this.runSeq = (this.runSeq || 0) + 1;
    clearTimeout(this.timeoutT);
    if (!this.runtime.alive) return;
    let graceful = false;
    try {
      const r = await withTimeout(this.runtime.request("stop"), patience);
      graceful = Boolean(r && r.graceful);
    } catch (e) {
      graceful = false;
    }
    if (!graceful) {
      this.runtime.terminate();
      this.scriptRunning = false;
      this.console.note("Stopped the script by restarting Python.");
      if (respawn) this.warm = this.warmUp().catch((e) => this.bootFailed(e));
    }
    this.scriptRunning = false;
  }

  /** Fresh worker with Pyodide, files and packages loaded, ready to run. */
  async warmUp() {
    if (this.warmP) return this.warmP;
    this.warmP = (async () => {
      await this.runtime.spawn();
      await this.runtime.request("load", { files: this.fileMessages(this.currentFiles || this.files) });
      await this.runtime.request("install", { packages: this.demo.manifest.packages || [], micropip: this.opts.micropip });
    })();
    try {
      await this.warmP;
    } finally {
      this.warmP = null;
    }
  }

  async restart(why) {
    this.restartSoon.cancel();
    if (!this.booted) return;
    try {
      await this.booted;
    } catch (e) {
      return;
    }
    if (this.warm) await this.warm.catch(() => {});
    const wasPaused = this.userPaused && why !== "manual";
    if (why === "manual") this.userPaused = false;
    this.setState("restarting");
    this.sim.showNotice(why && why !== "manual" ? `Restarting with ${why}…` : "Restarting…", { kind: "info" });
    await this.run({ play: !wasPaused });
  }

  /* ======================================================================
   * parameters
   * ==================================================================== */
  onControl(id, value, phase) {
    const p = this.det && this.det.params.find((x) => x.id === id);
    if (!p) return;
    this.values[id] = value;
    this.saveUrlState();
    if (!this.booted || this.state === "loading" || this.state === "idle") return;
    if (p.mode === "live") {
      if (this.state === "stopped" || this.state === "ready" || this.state === "error-start") return;
      this.player.queueParams({ [id]: value });
    } else if (phase === "input") {
      this.restartSoon(`${p.label || p.name} = ${Array.isArray(value) ? value.join(", ") : value}`);
    } else {
      this.restartSoon.cancel();
      this.restartSoon.flush(`${p.label || p.name} = ${Array.isArray(value) ? value.join(", ") : value}`);
    }
  }

  /** Apply several values at once (presets, demo:set links). */
  applyValues(values, why) {
    let restart = false;
    const live = {};
    for (const [id, v] of Object.entries(values)) {
      const p = this.det && this.det.params.find((x) => x.id === id);
      if (!p) continue;
      this.values[id] = v;
      if (this.controls) this.controls.setValue(id, v);
      if (p.mode === "live") live[id] = v;
      else restart = true;
    }
    this.saveUrlState();
    if (!this.booted || ["loading", "idle", "stopped", "ready", "error-start"].includes(this.state)) {
      if (!this.booted) this.boot(true);
      else if (["stopped", "ready"].includes(this.state)) this.run({ play: true });
      return;
    }
    if (restart) this.restart(why);
    else if (Object.keys(live).length) this.player.queueParams(live);
  }

  presetValues(preset) {
    const out = {};
    for (const [k, raw] of Object.entries(preset.values)) {
      const p = this.det && findParam(this.det.params, k);
      if (!p) {
        console.warn(`[pyd] preset ${preset.name}: unknown parameter ${k}`);
        continue;
      }
      const c = coerceForParam(p, raw);
      if (c.ok) out[p.id] = c.value;
      else console.warn(`[pyd] preset ${preset.name}: ${c.reason}`);
    }
    return out;
  }

  applyPreset(name) {
    const pr = (this.presets || []).find((x) => x.name === name);
    if (!pr) return;
    this.applyValues(this.presetValues(pr), `the “${pr.label}” preset`);
  }

  resetValues() {
    this.applyValues({ ...this.defaults }, "default values");
  }

  focusParam(id) {
    if (this.layout.mode === "tabs") this.layout.showTab("sim");
    if (this.layout.state.collapsed.sim) this.layout.setCollapsed("sim", false);
    this.controls.focusParam(id);
  }

  async callButton(b) {
    if (!this.runtime.alive || !["running", "static", "finished", "error"].includes(this.state)) {
      this.console.note(`“${b.label}” works while the simulation is running.`);
      return;
    }
    try {
      const r = await this.runtime.request("button", { call: b.call, file: b.file });
      if (r.ok && b.after === "restart") this.restart(b.label);
    } catch (e) {
      console.warn("[pyd] button", e);
    }
  }

  saveUrlState() {
    if (this.embed && !this.page.instance) return;
    clearTimeout(this._urlT);
    this._urlT = setTimeout(() => {
      try {
        history.replaceState(history.state, "", this.stateUrl());
      } catch (e) {
        /* sandboxed or file:// */
      }
    }, 400);
  }

  stateUrl() {
    const url = new URL(location.href);
    for (const k of [...url.searchParams.keys()]) if (k.startsWith("p.") || k === "preset") url.searchParams.delete(k);
    for (const [id, v] of Object.entries(this.values)) {
      const d = this.defaults[id];
      const same = Array.isArray(v) ? JSON.stringify(v) === JSON.stringify(d) : v === d;
      if (!same) url.searchParams.set(`p.${id}`, Array.isArray(v) ? v.join(",") : String(v));
    }
    return url.href;
  }

  async copyLink(btn) {
    let href = this.stateUrl();
    if (this.embed) {
      const u = new URL(href);
      u.searchParams.delete("embed");
      u.searchParams.delete("instance");
      href = u.href;
    }
    const ok = await copyText(href);
    if (btn) {
      btn.classList.toggle("is-done", ok);
      btn.title = ok ? "Link copied" : href;
      setTimeout(() => btn.classList.remove("is-done"), 1500);
    }
  }

  /* ======================================================================
   * explainer actions
   * ==================================================================== */
  onDemoAction(action, el) {
    if (["set", "preset", "param"].includes(action.type) && !this.det) {
      this.queuedActions.push([action, el]);
      if (!this.booted && this.sim) this.boot(this.opts.autorun);
      return;
    }
    switch (action.type) {
      case "set":
        this.applyValues(action.values, Object.entries(action.values).map(([k, v]) => `${k} = ${v}`).join(", "));
        break;
      case "preset":
        this.applyPreset(action.name);
        break;
      case "code":
        this.showCode(action.file, action.from, action.to);
        break;
      case "param":
        this.focusParam(action.name);
        break;
      case "run":
        this.explicitRun = true;
        if (!this.booted) this.boot(true);
        else if (this.state === "stopped" || this.state === "ready") this.run({ play: true });
        else if (!this.player.playing) this.resume();
        break;
      case "pause":
        this.pause();
        break;
      case "restart":
        this.restart("manual");
        break;
      case "step":
        this.step();
        break;
      case "stop":
        this.stop();
        break;
      case "reset":
        this.resetValues();
        break;
      default:
        break;
    }
    if (el && this.layout.mode !== "tabs" && ["set", "preset", "run", "restart"].includes(action.type) && this.layout.state.collapsed.sim) this.layout.setCollapsed("sim", false);
  }

  showCode(file, from, to, kind = "is-highlight") {
    if (!this.codePane) return;
    const L = this.layout;
    if (L.mode === "tabs") L.showTab("code");
    if (L.state.collapsed.code) L.setCollapsed("code", false);
    this.codePane.highlight(file, from, to, { kind }).then((ok) => {
      if (!ok) console.warn(`[pyd] code location ${file}:${from || ""} not found`);
    });
  }

  /* ======================================================================
   * worker events
   * ==================================================================== */
  wireRuntimeEvents() {
    const rt = this.runtime;
    rt.on("status", (m) => {
      if (this.state === "loading") this.loadingStage(m.stage === "runtime" ? "pyodide" : m.stage, m.message);
      if (m.message && this.state === "loading") this.sim.setStatus(m.message);
    });
    rt.on("console", (m) => this.console.write(m.stream, m.text));
    rt.on("frame", (m) => {
      if (!this.sim) return;
      const painted = this.sim.frame(m);
      rt.send("frame-ack", { n: 1 });
      if (painted) {
        this.player.notePainted();
        if (!this.firstFrameSeen) {
          this.firstFrameSeen = true;
          this.sim.hideOverlay();
          if (this.state === "restarting") this.setState("running");
          this.sim.hideNotice();
        }
        const now = performance.now();
        if (!this._fpsT || now - this._fpsT > 500) {
          this._fpsT = now;
          this.sim.setFps(this.player.measuredFps());
        }
      }
    });
    rt.on("figures", (m) => this.setFigures(m.figures));
    rt.on("error", (m) => this.onPythonError(m));
    rt.on("fatal", (m) => {
      this.player.stop();
      this.scriptRunning = false;
      if (this.state === "loading") this.bootFailed(new Error(m.message));
      else {
        this.console.note(`Python stopped: ${m.message}`);
        this.setState("error");
        this.runtime.terminate();
        this.warm = this.warmUp().catch((e) => this.bootFailed(e));
      }
    });
    this.player.on("error", () => this.syncPlayerUi());
    this.player.on("finished", () => {
      this.setState("finished");
      this.syncPlayerUi();
    });
  }

  setFigures(figs) {
    if (!this.sim) return;
    this.sim.setFigures(figs);
    const f = this.sim.figures.find((x) => x.id === this.sim.activeFig) || figs[0];
    if (f && f.width_in > 0 && f.height_in > 0) this.layout.app.style.setProperty("--pyd-fig-aspect", `${f.width_in} / ${f.height_in}`);
    this.postSize();
  }

  onPythonError(err) {
    this.console.error(err);
    const frame = (err.frames || [])[err.frames.length - 1];
    if (frame && this.codePane) this.codePane.highlight(frame.file, frame.line, frame.line, { kind: "is-error", scroll: true, flash: true });
    const where = frame ? ` in ${frame.file} line ${frame.line}` : "";
    const show = h("button", { type: "button", class: "pyd-btn" }, "Show details");
    show.addEventListener("click", () => {
      this.console.setOpen(true);
      if (frame) this.showCode(frame.file, frame.line, frame.line, "is-error");
    });
    if (this.sim) this.sim.showNotice(`${err.exc_type}${where}: ${err.message}`.slice(0, 240), { kind: "error", action: show });
    if (err.kind === "animation") {
      this.player.pause();
      this.userPaused = true;
      this.syncPlayerUi();
    }
  }

  onViewport(v) {
    if (this.runtime.alive && this.state !== "loading") this.runtime.request("viewport", { ...v, quality: this.opts.quality }).catch(() => {});
  }

  onPointer(msg) {
    if (!this.runtime.alive || this.pointerBusy || ["loading", "idle", "stopped"].includes(this.state)) {
      if (msg.kind === "move") return;
    }
    if (!this.runtime.alive) return;
    if (msg.kind === "move") {
      if (this.pointerBusy) {
        this.pendingMove = msg;
        return;
      }
    }
    this.pointerBusy = true;
    this.runtime
      .request("pointer", msg)
      .catch(() => {})
      .finally(() => {
        this.pointerBusy = false;
        if (this.pendingMove) {
          const m = this.pendingMove;
          this.pendingMove = null;
          this.onPointer(m);
        }
      });
  }

  /* ======================================================================
   * editing
   * ==================================================================== */
  async runEdited(files, how = {}) {
    if (!this.booted) {
      this.boot(false);
    }
    try {
      await this.booted;
    } catch (e) {
      return;
    }
    if (this.scriptRunning) await this.stopScript({ respawn: true, patience: this.workerBlocked() ? 300 : 6000 });
    if (this.warm) await this.warm.catch(() => {});
    this.currentFiles = files.map((f) => ({ path: f.path, text: typeof f.data === "string" ? f.data : null, bytes: f.data instanceof Uint8Array ? f.data : null, binary: !(typeof f.data === "string") }));
    this.setState("restarting");
    this.sim.showNotice(how.reset ? "Restoring the original code…" : "Running your edited code…", { kind: "info" });
    await this.runtime.request("load", { files: this.fileMessages(this.currentFiles) });
    const install = this.runtime.request("install", { packages: this.demo.manifest.packages || [], micropip: this.opts.micropip });
    await this.detect();
    this.reportMissing((await install).missing);
    if (this.codePane) this.codePane.refreshViews();
    await this.run({ play: true });
  }

  /* ======================================================================
   * theme
   * ==================================================================== */
  watchTheme() {
    watchTheme(({ rc }) => {
      const old = this.themeRc;
      this.themeRc = rc;
      if (old && this.runtime.alive && ["running", "static", "finished", "error"].includes(this.state)) {
        this.runtime.request("theme", { old, new: rc }).catch(() => {});
      }
    });
  }

  /* ======================================================================
   * embedding (inside an <iframe>)
   * ==================================================================== */
  setupEmbed() {
    document.documentElement.classList.add("pyd-embed");
    const parentOrigin = (() => {
      if (window.parent === window) return null;
      try {
        return window.parent.location.origin; // readable only when same-origin
      } catch (e) {
        /* cross-origin parent */
      }
      try {
        if (location.ancestorOrigins && location.ancestorOrigins.length) return location.ancestorOrigins[0];
        return document.referrer ? new URL(document.referrer).origin : null;
      } catch (e) {
        return null;
      }
    })();
    const allowed = new Set([location.origin, ...EMBED_PARENT_ORIGINS]);
    this.parentOrigin = parentOrigin && allowed.has(parentOrigin) ? parentOrigin : null;
    if (parentOrigin && !this.parentOrigin) console.info(`[pyd] embedded by ${parentOrigin}, which is not in EMBED_PARENT_ORIGINS: no auto-resize or theme sync.`);
    window.addEventListener("message", (e) => {
      if (e.source !== window.parent || !allowed.has(e.origin)) return;
      const d = e.data;
      if (!d || typeof d !== "object" || d.type !== "pyd:theme") return;
      if (d.theme === "dark" || d.theme === "light") document.documentElement.setAttribute("data-theme", d.theme);
      else if (d.theme === "site") document.documentElement.removeAttribute("data-theme");
    });
    const ro = new ResizeObserver(() => this.postSize());
    ro.observe(document.documentElement);
    // ask the parent for its theme
    this.postToParent({ type: "pyd:hello" });
  }

  postToParent(msg) {
    // Only to a parent whose origin is allowed (this site or EMBED_PARENT_ORIGINS).
    if (!this.embed || window.parent === window || !this.parentOrigin) return;
    window.parent.postMessage({ ...msg, instance: this.page.instance || "" }, this.parentOrigin);
  }

  postSize() {
    if (!this.embed) return;
    const h0 = Math.ceil(document.documentElement.getBoundingClientRect().height);
    if (h0 === this._lastH) return;
    this._lastH = h0;
    this.postToParent({ type: "pyd:resize", height: h0 });
  }
}

export { DEFAULTS };
