/* Python demonstrator -- pane layout.
 *
 * Arrangements (demo.json "layout" / ?layout=):
 *   split    explainer left; simulator top-right, code bottom-right (default)
 *   columns  explainer | simulator | code side by side (wide screens)
 *   stacked  one column, each pane at its natural height
 *   tabs     one pane at a time (used automatically on narrow screens)
 *   auto     split, switching to tabs below config.NARROW_BREAKPOINT
 * Dividers are draggable (mouse, touch, keyboard arrows); each pane can be
 * collapsed or "focused" (maximised).  Sizes, collapsed panes and the last
 * tab persist per demo in localStorage (when available).
 * When the simulator is out of sight (other tab, or scrolled away) a compact
 * sticky mini-player keeps playback reachable.
 */
import { NARROW_BREAKPOINT } from "./config.js";
import { clamp, h, icon, nextId, storage } from "./util.js";

const TITLES = { explainer: "Explainer", sim: "Simulator", code: "Code" };

export class Layout {
  /**
   * @param {HTMLElement} root
   * @param {object} opts {panes: [...], layout, storageKey, embed, onChange()}
   */
  constructor(root, opts) {
    this.root = root;
    this.opts = opts;
    this.panes = {};
    this.mode = null;
    const saved = storage.get(opts.storageKey, {}) || {};
    this.state = {
      col: clamp(Number(saved.col) || 38, 18, 75),
      col2: clamp(Number(saved.col2) || 50, 20, 80),
      row: clamp(Number(saved.row) || 58, 20, 85),
      collapsed: saved.collapsed && typeof saved.collapsed === "object" ? saved.collapsed : {},
      tab: saved.tab || "sim",
    };
    this.focus = null;
    this.build();
  }

  build() {
    const present = this.opts.panes;
    this.app = h("div", { class: "pyd-app" });
    this.tabbar = h("div", { class: "pyd-tabbar", role: "tablist", "aria-label": "Demo panes", hidden: true });
    this.paneWrap = h("div", { class: "pyd-panes" });
    for (const key of ["explainer", "sim", "code"]) {
      if (!present.includes(key)) continue;
      this.panes[key] = this.makePane(key);
      const tab = h("button", { type: "button", role: "tab", class: "pyd-tabbar-tab", "aria-selected": "false", "data-pane": key }, TITLES[key]);
      tab.addEventListener("click", () => this.showTab(key));
      this.panes[key].tab = tab;
      this.tabbar.appendChild(tab);
    }
    // explainer | right column (sim + code)
    const left = this.panes.explainer ? this.panes.explainer.el : null;
    const rightPanes = ["sim", "code"].filter((k) => this.panes[k]).map((k) => this.panes[k].el);
    this.right = h("div", { class: "pyd-right" });
    this.divCol = h("div", { class: "pyd-divider pyd-divider-col", role: "separator", "aria-orientation": "vertical", tabindex: "0", "aria-label": "Resize the explainer", "aria-valuemin": "18", "aria-valuemax": "75" });
    this.divRow = h("div", { class: "pyd-divider pyd-divider-row", role: "separator", "aria-orientation": "horizontal", tabindex: "0", "aria-label": "Resize the simulator", "aria-valuemin": "20", "aria-valuemax": "85" });
    if (rightPanes.length === 2) this.right.append(rightPanes[0], this.divRow, rightPanes[1]);
    else this.right.append(...rightPanes);
    if (left && rightPanes.length) this.paneWrap.append(left, this.divCol, this.right);
    else if (left) this.paneWrap.append(left);
    else this.paneWrap.append(this.right);
    this.paneWrap.classList.toggle("is-one-col", !(left && rightPanes.length));
    this.mini = this.buildMini();
    this.app.append(this.tabbar, this.paneWrap, this.mini);
    this.root.appendChild(this.app);
    this.bindDivider(this.divCol, "col", "x");
    this.bindDivider(this.divRow, "row", "y");
    this.applySizes();
    this.ro = new ResizeObserver(() => this.updateMode());
    this.ro.observe(this.root);
    this.updateMode();
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.focus) this.setFocus(null);
    });
    if ("IntersectionObserver" in window && this.panes.sim) {
      this.io = new IntersectionObserver(
        (entries) => {
          for (const e of entries) this.simVisible = e.isIntersecting;
          this.updateMini();
        },
        { threshold: 0.15 }
      );
      this.io.observe(this.panes.sim.el);
    }
  }

  makePane(key) {
    const id = nextId(`pyd-${key}`);
    const title = h("h2", { class: "pyd-pane-title", id: `${id}-title` }, TITLES[key]);
    const extra = h("div", { class: "pyd-pane-tools" });
    const collapse = h("button", { type: "button", class: "pyd-tool pyd-collapse", "aria-expanded": "true", "aria-controls": `${id}-body`, title: `Collapse the ${TITLES[key].toLowerCase()}` }, icon("collapse", `Collapse ${TITLES[key]}`));
    const focusBtn = h("button", { type: "button", class: "pyd-tool pyd-focus", "aria-pressed": "false", title: `Maximise the ${TITLES[key].toLowerCase()}` }, icon("focus", `Maximise ${TITLES[key]}`));
    const head = h("header", { class: "pyd-pane-head" }, title, extra, collapse, focusBtn);
    const body = h("div", { class: "pyd-pane-body", id: `${id}-body` });
    const el = h("section", { class: `pyd-pane pyd-pane-${key}`, role: "region", "aria-labelledby": `${id}-title`, "data-pane": key }, head, body);
    collapse.addEventListener("click", () => this.setCollapsed(key, !this.state.collapsed[key]));
    focusBtn.addEventListener("click", () => this.setFocus(this.focus === key ? null : key));
    title.addEventListener("dblclick", () => this.setFocus(this.focus === key ? null : key));
    return { el, head, body, title, extra, collapse, focusBtn };
  }

  buildMini() {
    this.miniCanvas = h("canvas", { class: "pyd-mini-thumb", width: "96", height: "60", "aria-hidden": "true" });
    this.miniPlay = h("button", { type: "button", class: "pyd-btn", "aria-label": "Pause" }, icon("pause"));
    this.miniStep = h("button", { type: "button", class: "pyd-btn", "aria-label": "Step" }, icon("step"));
    this.miniRestart = h("button", { type: "button", class: "pyd-btn", "aria-label": "Restart" }, icon("restart"));
    this.miniOpen = h("button", { type: "button", class: "pyd-btn pyd-mini-open" }, h("span", null, "Simulator"));
    this.miniOpen.addEventListener("click", () => {
      if (this.mode === "tabs") this.showTab("sim");
      else this.panes.sim.el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return h("div", { class: "pyd-mini", role: "toolbar", "aria-label": "Simulator mini-player", hidden: true }, this.miniCanvas, this.miniPlay, this.miniStep, this.miniRestart, this.miniOpen);
  }

  /** Mirror the latest frame into the mini-player thumbnail. */
  paintMini(sourceCanvas) {
    if (this.mini.hidden || !sourceCanvas.width) return;
    const c = this.miniCanvas;
    const ctx = c.getContext("2d");
    const ar = sourceCanvas.width / sourceCanvas.height;
    c.width = Math.round(60 * ar);
    ctx.drawImage(sourceCanvas, 0, 0, c.width, c.height);
  }

  /* ---------- modes ---------------------------------------------------------- */
  effectiveMode() {
    const want = this.opts.layout;
    const count = Object.keys(this.panes).length;
    const narrow = this.root.clientWidth < NARROW_BREAKPOINT;
    if (count === 1) return "single";
    if (want === "tabs") return "tabs";
    if (want === "stacked") return "stacked";
    if (narrow && (want === "auto" || want === "split" || want === "columns")) return "tabs";
    if (want === "columns" && this.panes.explainer && this.panes.sim && this.panes.code) return "columns";
    return "split";
  }

  updateMode() {
    const mode = this.effectiveMode();
    if (mode === this.mode) return;
    this.mode = mode;
    this.app.dataset.layout = mode;
    this.tabbar.hidden = mode !== "tabs";
    if (mode === "columns") {
      // explainer | sim | code: move code beside the sim
      this.right.classList.add("is-columns");
    } else this.right.classList.remove("is-columns");
    if (mode === "tabs") this.showTab(this.panes[this.state.tab] ? this.state.tab : "sim", false);
    else for (const p of Object.values(this.panes)) p.el.hidden = false;
    for (const [k, p] of Object.entries(this.panes)) this.applyCollapsed(k, Boolean(this.state.collapsed[k]) && mode !== "tabs" && mode !== "single");
    this.applySizes();
    this.updateMini();
    if (this.opts.onChange) this.opts.onChange(mode);
  }

  showTab(key, persist = true) {
    if (!this.panes[key]) return;
    if (this.mode !== "tabs") {
      this.panes[key].el.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    for (const [k, p] of Object.entries(this.panes)) {
      const on = k === key;
      p.el.hidden = !on;
      p.tab.setAttribute("aria-selected", String(on));
      p.tab.tabIndex = on ? 0 : -1;
    }
    this.activeTab = key;
    if (persist) {
      this.state.tab = key;
      this.save();
      // show the new pane from its top, not wherever the old one was scrolled to
      const top = this.paneWrap.getBoundingClientRect().top - this.tabbar.getBoundingClientRect().height - 16;
      if (top < 0) window.scrollBy({ top, behavior: "auto" });
    }
    this.updateMini();
    if (this.opts.onChange) this.opts.onChange(this.mode);
  }

  updateMini() {
    if (!this.panes.sim) return;
    const hiddenTab = this.mode === "tabs" && this.activeTab !== "sim";
    const scrolledAway = (this.mode === "stacked" || this.mode === "tabs") && this.simVisible === false && this.activeTab === "sim";
    const show = Boolean(this.opts.miniEnabled && this.opts.miniEnabled()) && (hiddenTab || scrolledAway);
    this.mini.hidden = !show;
  }

  /* ---------- collapse / focus --------------------------------------------- */
  setCollapsed(key, collapsed) {
    this.state.collapsed[key] = collapsed;
    this.applyCollapsed(key, collapsed);
    this.applySizes();
    this.save();
    if (this.opts.onChange) this.opts.onChange(this.mode);
  }

  applyCollapsed(key, collapsed) {
    const p = this.panes[key];
    if (!p) return;
    p.el.classList.toggle("is-collapsed", collapsed);
    p.body.hidden = collapsed;
    p.collapse.setAttribute("aria-expanded", String(!collapsed));
    p.collapse.replaceChildren(icon(collapsed ? "expand" : "collapse", `${collapsed ? "Expand" : "Collapse"} ${TITLES[key]}`));
    p.collapse.title = `${collapsed ? "Expand" : "Collapse"} the ${TITLES[key].toLowerCase()}`;
    this.app.classList.toggle(`is-${key}-collapsed`, collapsed);
  }

  setFocus(key) {
    this.focus = key;
    this.app.dataset.focus = key || "";
    for (const [k, p] of Object.entries(this.panes)) {
      const on = k === key;
      p.focusBtn.setAttribute("aria-pressed", String(on));
      p.focusBtn.replaceChildren(icon(on ? "unfocus" : "focus", on ? `Restore layout` : `Maximise ${TITLES[k]}`));
      p.focusBtn.title = on ? "Restore the layout (Esc)" : `Maximise the ${TITLES[k].toLowerCase()}`;
    }
    if (key && this.state.collapsed[key]) this.applyCollapsed(key, false);
    if (!key) for (const k of Object.keys(this.panes)) this.applyCollapsed(k, Boolean(this.state.collapsed[k]));
    if (this.opts.onChange) this.opts.onChange(this.mode);
  }

  /* ---------- sizes ------------------------------------------------------------ */
  applySizes() {
    const s = this.app.style;
    s.setProperty("--pyd-col", `${this.state.col}%`);
    s.setProperty("--pyd-col2", `${this.state.col2}%`);
    s.setProperty("--pyd-row", `${this.state.row}%`);
    const cols = this.mode === "columns";
    this.divCol.setAttribute("aria-valuenow", String(Math.round(this.state.col)));
    this.divRow.setAttribute("aria-valuenow", String(Math.round(cols ? this.state.col2 : this.state.row)));
    this.divRow.setAttribute("aria-orientation", cols ? "vertical" : "horizontal");
    this.divRow.setAttribute("aria-valuemin", cols ? "20" : "20");
    this.divRow.setAttribute("aria-valuemax", cols ? "80" : "85");
  }

  bindDivider(div, baseKey, baseAxis) {
    // In "columns" mode the sim/code divider is vertical and sets col2.
    const spec = () => (baseKey === "row" && this.mode === "columns" ? ["col2", "x"] : [baseKey, baseAxis]);
    const limits = (key) => (key === "col" ? [18, 75] : key === "col2" ? [20, 80] : [20, 85]);
    const container = (key) => (key === "col" ? this.paneWrap : this.right);
    const setFrom = (clientX, clientY) => {
      const [key, axis] = spec();
      const r = container(key).getBoundingClientRect();
      const pct = axis === "x" ? ((clientX - r.left) / r.width) * 100 : ((clientY - r.top) / r.height) * 100;
      const [lo, hi] = limits(key);
      this.state[key] = clamp(pct, lo, hi);
      this.applySizes();
    };
    const done = () => {
      this.save();
      if (this.opts.onChange) this.opts.onChange(this.mode);
    };
    div.addEventListener("pointerdown", (e) => {
      if (this.mode === "tabs" || this.mode === "stacked") return;
      e.preventDefault();
      div.setPointerCapture(e.pointerId);
      div.classList.add("is-dragging");
      this.app.classList.add("is-resizing");
      const move = (ev) => setFrom(ev.clientX, ev.clientY);
      const up = () => {
        div.classList.remove("is-dragging");
        this.app.classList.remove("is-resizing");
        div.removeEventListener("pointermove", move);
        div.removeEventListener("pointerup", up);
        div.removeEventListener("pointercancel", up);
        done();
      };
      div.addEventListener("pointermove", move);
      div.addEventListener("pointerup", up);
      div.addEventListener("pointercancel", up);
    });
    div.addEventListener("keydown", (e) => {
      const dir = { ArrowLeft: -2, ArrowUp: -2, ArrowRight: 2, ArrowDown: 2, PageUp: -10, PageDown: 10 }[e.key];
      if (dir === undefined && e.key !== "Home" && e.key !== "End") return;
      e.preventDefault();
      const [key] = spec();
      const [lo, hi] = limits(key);
      if (e.key === "Home") this.state[key] = lo;
      else if (e.key === "End") this.state[key] = hi;
      else this.state[key] = clamp(this.state[key] + dir, lo, hi);
      this.applySizes();
      done();
    });
    div.addEventListener("dblclick", () => {
      const [key] = spec();
      this.state[key] = key === "col" ? 38 : key === "col2" ? 50 : 58;
      this.applySizes();
      done();
    });
  }

  save() {
    storage.set(this.opts.storageKey, { col: this.state.col, col2: this.state.col2, row: this.state.row, collapsed: this.state.collapsed, tab: this.state.tab });
  }
}
