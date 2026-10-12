/* Python demonstrator -- the simulator view: figure canvas, figure tabs,
 * overlays (start, loading, restarting, errors) and the playback bar.
 *
 * Frames arrive from the worker as RGBA buffers sized for the device pixel
 * ratio; they are painted 1:1 into the canvas backing store and the canvas
 * is scaled with CSS to fit the stage while keeping the figure's aspect.
 */
import { clamp, debounce, h, icon, nextId } from "./util.js";

export class Simulator {
  /**
   * @param {HTMLElement} el
   * @param {object} cb {onRun, onStop, onPause, onResume, onStep, onRestart, onSpeed(v),
   *   onViewport({width, height, dpr}), onPointer(msg), onSelectFigure(id), onShowError()}
   */
  constructor(el, cb, { title = "Simulation" } = {}) {
    this.el = el;
    this.cb = cb;
    this.title = title;
    this.figures = [];
    this.activeFig = null;
    this.frames = new Map();     // fig id -> {image: ImageData, n}
    this.state = "idle";
    this.build();
  }

  build() {
    const id = nextId("pyd-sim");
    this.tabs = h("div", { class: "pyd-figtabs", role: "tablist", "aria-label": "Figures", hidden: true });
    this.canvas = h("canvas", { class: "pyd-canvas", role: "img", tabindex: "0", "aria-label": `${this.title}: live figure (not started)` });
    this.ctx = this.canvas.getContext("2d", { alpha: true });
    this.overlay = h("div", { class: "pyd-overlay" });
    this.notice = h("div", { class: "pyd-notice", role: "alert", hidden: true });
    this.stage = h("div", { class: "pyd-stage", id: `${id}-stage` }, this.canvas, this.overlay, this.notice);

    const btn = (cls, label, ico, onclick) => h("button", { type: "button", class: `pyd-btn ${cls}`, onclick, title: label, "aria-label": label }, icon(ico), h("span", { class: "pyd-btn-label" }, label));
    this.runBtn = btn("pyd-btn-primary", "Run", "play", () => (this.active ? this.cb.onStop() : this.cb.onRun()));
    this.pauseBtn = btn("", "Pause", "pause", () => (this.paused ? this.cb.onResume() : this.cb.onPause()));
    this.stepBtn = btn("", "Step", "step", () => this.cb.onStep());
    this.restartBtn = btn("", "Restart", "restart", () => this.cb.onRestart());
    this.speedSel = h(
      "select",
      { class: "pyd-select pyd-speed", "aria-label": "Speed", title: "Playback speed" },
      ...[0.25, 0.5, 1, 1.5, 2, 4].map((s) => h("option", { value: String(s), selected: s === 1 }, `${s}×`))
    );
    this.speedSel.addEventListener("change", () => this.cb.onSpeed(Number(this.speedSel.value)));
    this.fpsOut = h("span", { class: "pyd-fps", title: "Measured frames per second" }, "– fps");
    this.frameOut = h("span", { class: "pyd-frame", title: "Frames shown" }, "frame –");
    this.statusOut = h("span", { class: "pyd-status", role: "status", "aria-live": "polite" }, "");
    this.player = h(
      "div",
      { class: "pyd-player", role: "toolbar", "aria-label": "Playback controls" },
      this.runBtn,
      this.pauseBtn,
      this.stepBtn,
      this.restartBtn,
      h("label", { class: "pyd-speed-wrap" }, h("span", { class: "pyd-sr" }, "Speed"), this.speedSel),
      h("span", { class: "pyd-readouts" }, this.statusOut, this.fpsOut, this.frameOut)
    );
    this.paramsEl = h("div", { class: "pyd-params", role: "group", "aria-label": "Simulation parameters" });
    // .pyd-sim-cq is the size container: the panel sits beside the figure
    // when the pane is wide enough, below it otherwise.
    this.simEl = h("div", { class: "pyd-sim" }, h("div", { class: "pyd-sim-main" }, this.tabs, this.stage, this.player), this.paramsEl);
    this.el.replaceChildren(h("div", { class: "pyd-sim-cq" }, this.simEl));

    this.bindPointer();
    this.bindKeys();
    const report = debounce(() => this.reportViewport(), 150);
    this.ro = new ResizeObserver(() => {
      this.layoutCanvas();
      report();
    });
    this.ro.observe(this.stage);
    this.setState("idle");
  }

  /* ---------- state ---------------------------------------------------------- */
  setState(state, { paused = this.paused, timers = this.timers } = {}) {
    this.state = state;
    this.paused = paused;
    this.timers = timers;
    const started = !["idle", "stopped", "error-start", "loading"].includes(state);
    const animating = started && timers;
    // "Stop" while something is running; otherwise "Run" (re-runs a static
    // figure or a finished animation).
    this.active = state === "running" || state === "restarting" || (state === "error" && Boolean(timers));
    this.setBtn(this.runBtn, this.active ? "Stop" : "Run", this.active ? "stop" : "play");
    this.runBtn.classList.toggle("pyd-btn-primary", !this.active);
    this.runBtn.disabled = state === "loading";
    this.setBtn(this.pauseBtn, paused ? "Resume" : "Pause", paused ? "play" : "pause");
    this.pauseBtn.disabled = !animating;
    this.pauseBtn.setAttribute("aria-pressed", String(Boolean(paused)));
    this.stepBtn.disabled = !animating || !paused;
    this.restartBtn.disabled = !started && state !== "stopped";
    this.el.dataset.state = state;
  }

  setBtn(b, label, ico) {
    b.replaceChildren(icon(ico), h("span", { class: "pyd-btn-label" }, label));
    b.title = label;
    b.setAttribute("aria-label", label);
  }

  setStatus(text) {
    if (this.statusOut.textContent !== text) this.statusOut.textContent = text;
  }

  /* ---------- overlays ------------------------------------------------------- */
  showStart({ title, note, onStart }) {
    const go = h("button", { type: "button", class: "pyd-btn pyd-btn-primary pyd-start" }, icon("play"), h("span", null, "Run the demo"));
    go.addEventListener("click", onStart);
    this.overlay.replaceChildren(h("div", { class: "pyd-overlay-card" }, h("p", { class: "pyd-overlay-title" }, title), go, note ? h("p", { class: "pyd-overlay-note" }, note) : null));
    this.overlay.hidden = false;
  }

  showLoading(stages, current, progress) {
    const list = h("ol", { class: "pyd-stages" });
    let reached = false;
    for (const [key, label] of stages) {
      const state = key === current ? "is-current" : reached ? "" : "is-done";
      if (key === current) reached = true;
      list.appendChild(h("li", { class: state }, label));
    }
    if (!stages.some(([k]) => k === current)) for (const li of list.children) li.className = "";
    const bar = h("div", { class: "pyd-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round((progress || 0) * 100)), "aria-label": "Loading" }, h("div", { style: { width: `${Math.round(clamp(progress || 0, 0, 1) * 100)}%` } }));
    this.overlay.replaceChildren(h("div", { class: "pyd-overlay-card" }, h("p", { class: "pyd-overlay-title" }, "Starting Python…"), bar, list));
    this.overlay.hidden = false;
  }

  showMessage(title, text, { kind = "info", actions = [] } = {}) {
    this.overlay.replaceChildren(h("div", { class: `pyd-overlay-card is-${kind}` }, h("p", { class: "pyd-overlay-title" }, title), text ? h("p", { class: "pyd-overlay-note" }, text) : null, actions.length ? h("div", { class: "pyd-overlay-actions" }, ...actions) : null));
    this.overlay.hidden = false;
  }

  hideOverlay() {
    this.overlay.hidden = true;
    this.overlay.replaceChildren();
  }

  /** Small banner over the bottom of the stage (errors, restarting...). */
  showNotice(text, { kind = "info", action = null, timeout = 0 } = {}) {
    clearTimeout(this._noticeT);
    this.notice.className = `pyd-notice is-${kind}`;
    this.notice.replaceChildren(h("span", null, text), action);
    this.notice.hidden = false;
    if (timeout) this._noticeT = setTimeout(() => this.hideNotice(), timeout);
  }

  hideNotice() {
    clearTimeout(this._noticeT);
    this.notice.hidden = true;
  }

  /* ---------- figures & frames ----------------------------------------------- */
  setFigures(figs) {
    this.figures = figs.filter((f) => !f.closed || this.frames.has(f.id));
    if (!this.figures.some((f) => f.id === this.activeFig)) this.activeFig = this.figures.length ? this.figures[0].id : null;
    this.tabs.replaceChildren();
    this.tabs.hidden = this.figures.length < 2;
    for (const f of this.figures) {
      const tab = h("button", { type: "button", role: "tab", class: "pyd-figtab", "aria-selected": String(f.id === this.activeFig) }, f.title || `Figure ${f.id}`);
      tab.addEventListener("click", () => this.selectFigure(f.id));
      this.tabs.appendChild(tab);
    }
    this.updateAria();
  }

  selectFigure(id) {
    this.activeFig = id;
    for (const [i, tab] of [...this.tabs.children].entries()) tab.setAttribute("aria-selected", String(this.figures[i] && this.figures[i].id === id));
    const f = this.frames.get(id);
    if (f) this.paintImage(f.image);
    this.cb.onSelectFigure(id);
    this.updateAria();
  }

  updateAria() {
    const f = this.figures.find((x) => x.id === this.activeFig);
    const name = f ? f.title : "figure";
    this.canvas.setAttribute("aria-label", `${this.title}: ${name}${f && f.animated ? " (animated)" : ""}. Space pauses or resumes, right arrow steps.`);
  }

  /** A frame from the worker. */
  frame(msg) {
    const data = new Uint8ClampedArray(msg.buffer);
    if (data.length !== msg.width * msg.height * 4) return false;
    const image = new ImageData(data, msg.width, msg.height);
    this.frames.set(msg.fig, { image, n: msg.n });
    if (this.activeFig === null) this.activeFig = msg.fig;
    if (msg.fig !== this.activeFig) return false;
    this.paintImage(image);
    this.frameOut.textContent = `frame ${msg.n}`;
    return true;
  }

  paintImage(image) {
    if (this.canvas.width !== image.width || this.canvas.height !== image.height) {
      this.canvas.width = image.width;
      this.canvas.height = image.height;
      this.layoutCanvas();
    }
    this.ctx.putImageData(image, 0, 0);
    this.lastImage = image;
    if (this.onPainted) this.onPainted(image);
  }

  clearFrames() {
    this.frames.clear();
    this.activeFig = null;
  }

  /** Fit the canvas (CSS size) inside the stage, preserving its aspect ratio. */
  layoutCanvas() {
    const sw = this.stage.clientWidth;
    const sh = this.stage.clientHeight;
    const cw = this.canvas.width;
    const ch = this.canvas.height;
    if (!sw || !sh || !cw || !ch || cw <= 1) return;
    const scale = Math.min(sw / cw, sh / ch);
    this.canvas.style.width = `${Math.floor(cw * scale)}px`;
    this.canvas.style.height = `${Math.floor(ch * scale)}px`;
  }

  viewport() {
    const r = this.stage.getBoundingClientRect();
    return { width: Math.max(160, Math.round(r.width)), height: Math.max(120, Math.round(r.height)), dpr: window.devicePixelRatio || 1 };
  }

  reportViewport() {
    const v = this.viewport();
    const key = `${v.width}x${v.height}@${v.dpr}`;
    if (key === this._vpKey) return;
    this._vpKey = key;
    this.cb.onViewport(v);
  }

  setFps(fps) {
    this.fpsOut.textContent = fps ? `${Math.round(fps)} fps` : "– fps";
  }

  /* ---------- input ------------------------------------------------------------- */
  bindPointer() {
    const send = (e, kind) => {
      if (this.activeFig === null || !this.canvas.width) return;
      const r = this.canvas.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width;
      const y = (e.clientY - r.top) / r.height;
      if (kind !== "leave" && (x < 0 || x > 1 || y < 0 || y > 1)) return;
      this.cb.onPointer({ fig: this.activeFig, kind, x, y, button: e.button, buttons: e.buttons, shift: e.shiftKey, ctrl: e.ctrlKey, alt: e.altKey, dbl: e.detail === 2, step: e.deltaY ? -Math.sign(e.deltaY) : 0 });
    };
    this.canvas.addEventListener("pointerdown", (e) => {
      this.canvas.setPointerCapture(e.pointerId);
      send(e, "down");
    });
    this.canvas.addEventListener("pointerup", (e) => send(e, "up"));
    this.canvas.addEventListener("pointermove", (e) => send(e, "move"));
    this.canvas.addEventListener("pointerleave", (e) => send(e, "leave"));
    this.canvas.addEventListener("wheel", (e) => {
      if (this.cb.wheelEnabled && this.cb.wheelEnabled()) {
        e.preventDefault();
        send(e, "scroll");
      }
    }, { passive: false });
  }

  bindKeys() {
    this.canvas.addEventListener("keydown", (e) => {
      if (e.key === " " || e.key === "k") {
        e.preventDefault();
        if (this.pauseBtn.disabled) return;
        this.pauseBtn.click();
      } else if (e.key === "ArrowRight" || e.key === ".") {
        e.preventDefault();
        if (!this.stepBtn.disabled) this.stepBtn.click();
      } else if (e.key === "r") {
        if (!this.restartBtn.disabled) this.restartBtn.click();
      }
    });
  }
}
