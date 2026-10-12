/* Python demonstrator -- the auto-generated parameter panel.
 *
 * Builds labelled controls from the detector's output (pyd_detect.py):
 *   bool -> switch, int/float -> slider + number box (log scale when the
 *   range spans decades), choice -> dropdown, str -> text box,
 *   small tuples -> grouped number boxes.
 * Each control shows whether a change applies live or restarts the run, why
 * (tooltip), where the value is defined (file:line), and its current value.
 */
import { clamp, fmtNum, h, icon, nextId } from "./util.js";

const LOG_STEPS = 1000;

/** Number-box text that fits the box: ints in full, floats with up to 6
 * significant digits but no more than 8 characters where possible (1/240
 * shows as 0.004167).  The value itself keeps full precision. */
function boxText(v, isInt) {
  if (!Number.isFinite(v)) return "";
  if (isInt) return String(Math.round(v));
  for (const digits of [6, 5, 4, 3]) {
    const t = String(Number(v.toPrecision(digits)));
    if (t.length <= 8) return t;
  }
  return v.toExponential(2);
}

function sig3(v) {
  if (!Number.isFinite(v) || v === 0) return v;
  return Number(v.toPrecision(3));
}

export class ControlPanel {
  /**
   * @param {HTMLElement} el
   * @param {object} cb {onChange(id, value, phase), onHover(param|null),
   *   onLocate(param), onReset(), onPreset(name), onButton(btn), onCopyLink()}
   */
  constructor(el, cb) {
    this.el = el;
    this.cb = cb;
    this.params = [];
    this.byId = new Map();
    this.controls = new Map();      // id -> {root, set(value), focus(), badge}
    this.values = {};
    this.presets = [];
    this.collapsed = false;
    this.renderSkeleton();
  }

  renderSkeleton(text = "Looking for parameters…") {
    this.el.replaceChildren(h("div", { class: "pyd-params-empty", role: "status" }, text));
  }

  renderError(message) {
    this.el.replaceChildren(h("div", { class: "pyd-params-empty is-error", role: "status" }, message));
  }

  /**
   * @param {object} det       detection result (params, buttons, derived, skipped, errors, notes)
   * @param {object} values    current values by id
   * @param {Array} presets    [{name, label, values}]
   */
  setParams(det, values, presets) {
    this.det = det;
    this.params = det.params || [];
    this.byId = new Map(this.params.map((p) => [p.id, p]));
    this.values = { ...values };
    this.presets = presets || [];
    this.controls.clear();

    const header = this.renderHeader();
    const body = h("div", { class: "pyd-params-body", id: nextId("pyd-params") });
    this.body = body;
    header.querySelector(".pyd-params-toggle").setAttribute("aria-controls", body.id);
    this.presetsRow = header.querySelector(".pyd-presets-row");

    const buttons = (det.buttons || []).filter((b) => b);
    if (buttons.length) {
      const row = h("div", { class: "pyd-actions", role: "group", "aria-label": "Simulation actions" });
      for (const b of buttons) {
        const btn = h(
          "button",
          { type: "button", class: "pyd-btn pyd-btn-secondary", disabled: !b.available, title: b.available ? b.help || `Calls ${b.call}() in ${b.file}` : b.reason },
          b.label
        );
        btn.addEventListener("click", () => this.cb.onButton(b));
        row.appendChild(btn);
      }
      body.appendChild(row);
    }

    const visible = this.params.filter((p) => !p.hidden);
    if (!visible.length) {
      body.appendChild(h("p", { class: "pyd-params-none" }, "No adjustable parameters were found in this script."));
    }
    const groups = new Map();
    for (const p of visible) {
      const g = p.group || "Parameters";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(p);
    }
    const order = [...groups.keys()].sort((a, b) => this.groupRank(a) - this.groupRank(b));
    for (const g of order) {
      const closed = g === "Other detected values" || g === "More parameters";
      const det2 = h("details", { class: "pyd-group", open: !closed }, h("summary", null, h("span", null, g), h("span", { class: "pyd-count" }, String(groups.get(g).length))));
      const grid = h("div", { class: "pyd-group-body" });
      for (const p of groups.get(g)) grid.appendChild(this.makeControl(p));
      det2.appendChild(grid);
      body.appendChild(det2);
    }
    body.appendChild(this.renderWhy(det));
    this.el.replaceChildren(header, body);
    this.setCollapsed(this.collapsed);
  }

  groupRank(g) {
    if (g === "Other detected values") return 100;
    if (g === "More parameters") return 90;
    const first = this.params.find((p) => (p.group || "Parameters") === g);
    return first ? first.rank / 1000 : 50;
  }

  renderHeader() {
    const toggle = h("button", { type: "button", class: "pyd-tool pyd-params-toggle", "aria-expanded": "true", title: "Hide or show the controls" }, icon("sliders"), h("span", { class: "pyd-params-title" }, "Parameters"));
    toggle.addEventListener("click", () => this.setCollapsed(!this.collapsed));
    const bits = [toggle, h("span", { class: "pyd-spacer" })];
    bits.push(
      h("button", { type: "button", class: "pyd-tool", title: "Reset all parameters to their defaults", "aria-label": "Reset all parameters", onclick: () => this.cb.onReset() }, icon("reload"), h("span", { class: "pyd-tool-label" }, "Reset")),
      h("button", { type: "button", class: "pyd-tool", title: "Copy a link to this exact setup", "aria-label": "Copy a link to this setup", onclick: (e) => this.cb.onCopyLink(e.currentTarget) }, icon("link"), h("span", { class: "pyd-tool-label" }, "Link"))
    );
    const head = h("div", { class: "pyd-params-head" }, ...bits);
    if (!this.presets.length) return head;
    const sel = h("select", { class: "pyd-select pyd-presets", "aria-label": "Apply a preset" }, h("option", { value: "" }, "Presets…"), ...this.presets.map((p) => h("option", { value: p.name, title: p.description || "" }, p.label)));
    sel.addEventListener("change", () => {
      if (sel.value) this.cb.onPreset(sel.value);
      sel.value = "";
    });
    return h("div", { class: "pyd-params-headwrap" }, head, h("div", { class: "pyd-presets-row" }, sel));
  }

  setCollapsed(c) {
    this.collapsed = c;
    if (!this.body) return;
    this.body.hidden = c;
    if (this.presetsRow) this.presetsRow.hidden = c;
    const t = this.el.querySelector(".pyd-params-toggle");
    if (t) t.setAttribute("aria-expanded", String(!c));
  }

  renderWhy(det) {
    const items = [];
    const bySource = {};
    for (const p of this.params) bySource[p.source] = (bySource[p.source] || 0) + 1;
    const names = { argparse: "command-line options (argparse)", "config-dict": "a settings dictionary", dataclass: "a @dataclass config", namedtuple: "a NamedTuple config", function: "default arguments of the entry function", constant: "constants at the top of the files", demo_params: "the demo_params helper", manifest: "demo.json" };
    const found = Object.entries(bySource).map(([k, n]) => `${n} from ${names[k] || k}`).join(", ");
    items.push(
      h("p", null, "These controls were generated by reading the Python source (without running it). ", found ? `Found ${found}.` : ""),
      h("p", null, h("strong", null, "Live"), " values are read every frame, so changes appear immediately. ", h("strong", null, "Restarts"), " values are used while the script sets up, so changing them re-runs it (the code you see is never edited: the new value is substituted when the file is loaded).")
    );
    if (det.derived && det.derived.length) {
      items.push(h("p", null, "Derived (follow other values): ", det.derived.map((d) => `${d.name} ← ${d.depends.join(", ")}`).join("; "), "."));
    }
    if (det.skipped && det.skipped.length) {
      const ul = h("ul", null, ...det.skipped.slice(0, 30).map((s) => h("li", null, h("code", null, s.name), ` (${s.file}:${s.line}) — ${s.reason}`)));
      items.push(h("p", null, "Not shown:"), ul);
    }
    for (const e of det.errors || []) items.push(h("p", { class: "pyd-why-error" }, `Could not analyse ${e.file}${e.line ? `:${e.line}` : ""}: ${e.message}`));
    for (const n of det.notes || []) items.push(h("p", { class: "pyd-why-note" }, n));
    items.push(h("p", { class: "pyd-why-note" }, "The page author can rename, re-range, hide or add controls in demo.json or with # demo: comments."));
    return h("details", { class: "pyd-why" }, h("summary", null, icon("info"), h("span", null, "Why these controls?")), h("div", { class: "pyd-why-body" }, ...items));
  }

  /* ---------- one control ---------------------------------------------------- */
  makeControl(p) {
    const id = nextId("pyd-p");
    const value = this.values[p.id] !== undefined ? this.values[p.id] : p.default;
    const reasonId = `${id}-why`;
    const live = p.mode === "live";
    const badge = h(
      "span",
      { class: `pyd-badge ${live ? "is-live" : "is-restart"}`, tabindex: "0", title: p.mode_reason || "", "aria-describedby": reasonId },
      icon(live ? "live" : "restart"),
      h("span", null, live ? "Live" : "Restarts")
    );
    const reason = h("span", { id: reasonId, class: "pyd-sr" }, p.mode_reason || "");
    const out = h("output", { class: "pyd-value", for: id, "aria-live": "off" });
    const loc = p.file && p.line ? h("button", { type: "button", class: "pyd-loc", title: `Show where ${p.name} is defined`, "aria-label": `Show ${p.name} in ${p.file} line ${p.line}` }, `${p.file}:${p.line}`) : null;
    if (loc) loc.addEventListener("click", () => this.cb.onLocate(p));
    const label = h("label", { for: id, class: "pyd-ctl-label" }, p.label || p.name, p.unit ? h("span", { class: "pyd-unit" }, ` (${p.unit})`) : null);
    const top = h("div", { class: "pyd-ctl-top" }, label, out, badge, reason);
    const root = h("div", { class: `pyd-ctl pyd-ctl-${p.type}`, "data-param": p.id });
    const input = this.makeInput(p, id, value, out);
    root.append(top, input.el);
    const foot = [];
    if (p.help) foot.push(h("span", { class: "pyd-ctl-help" }, p.help));
    if (loc) foot.push(loc);
    if (foot.length) root.appendChild(h("div", { class: "pyd-ctl-foot" }, ...foot));
    root.addEventListener("mouseenter", () => this.cb.onHover(p));
    root.addEventListener("mouseleave", () => this.cb.onHover(null));
    root.addEventListener("focusin", () => this.cb.onHover(p));
    root.addEventListener("focusout", (e) => {
      if (!root.contains(e.relatedTarget)) this.cb.onHover(null);
    });
    this.controls.set(p.id, { root, set: input.set, focus: input.focus, badge, reason });
    input.set(value);
    return root;
  }

  emit(p, value, phase) {
    this.values[p.id] = value;
    this.cb.onChange(p.id, value, phase);
  }

  makeInput(p, id, value, out) {
    const show = (v) => {
      out.textContent = p.type === "bool" ? (v ? "on" : "off") : p.type === "tuple" ? `(${v.map((x) => fmtNum(x, p.elem_type)).join(", ")})` : typeof v === "number" ? fmtNum(v, p.type) : String(v);
    };
    if (p.type === "bool") {
      const cb = h("input", { type: "checkbox", id, role: "switch", class: "pyd-switch-input" });
      cb.addEventListener("change", () => {
        show(cb.checked);
        this.emit(p, cb.checked, "commit");
      });
      const el = h("label", { class: "pyd-switch" }, cb, h("span", { class: "pyd-switch-track", "aria-hidden": "true" }));
      return {
        el,
        set: (v) => {
          cb.checked = Boolean(v);
          show(Boolean(v));
        },
        focus: () => cb.focus(),
      };
    }
    if (p.type === "choice") {
      const sel = h("select", { id, class: "pyd-select" }, ...(p.choices || []).map((c) => h("option", { value: String(c) }, String(c))));
      sel.addEventListener("change", () => {
        const v = (p.choices || []).find((c) => String(c) === sel.value);
        show(v);
        this.emit(p, v, "commit");
      });
      return {
        el: sel,
        set: (v) => {
          sel.value = String(v);
          show(v);
        },
        focus: () => sel.focus(),
      };
    }
    if (p.type === "str") {
      const tx = h("input", { type: "text", id, class: "pyd-text", maxlength: "2000", spellcheck: "false" });
      tx.addEventListener("change", () => {
        show(tx.value);
        this.emit(p, tx.value, "commit");
      });
      return {
        el: tx,
        set: (v) => {
          tx.value = String(v ?? "");
          show(tx.value);
        },
        focus: () => tx.focus(),
      };
    }
    if (p.type === "tuple") {
      const n = p.length || (Array.isArray(p.default) ? p.default.length : 2);
      const boxes = [];
      const wrap = h("div", { class: "pyd-tuple", role: "group", "aria-labelledby": id });
      for (let i = 0; i < n; i++) {
        const b = h("input", { type: "number", class: "pyd-num", step: p.elem_type === "int" ? "1" : "any", id: i === 0 ? id : undefined, "aria-label": `${p.label || p.name} item ${i + 1}` });
        b.addEventListener("change", () => {
          const vals = boxes.map((x) => Number(x.value));
          if (vals.some((x) => !Number.isFinite(x))) return;
          const v = p.elem_type === "int" ? vals.map(Math.round) : vals;
          show(v);
          this.emit(p, v, "commit");
        });
        boxes.push(b);
        wrap.appendChild(b);
      }
      return {
        el: wrap,
        set: (v) => {
          (v || []).forEach((x, i) => boxes[i] && (boxes[i].value = String(x)));
          show(v || []);
        },
        focus: () => boxes[0] && boxes[0].focus(),
      };
    }
    // numeric: slider + number box
    const isInt = p.type === "int";
    const log = p.scale === "log" && p.min > 0 && p.max > p.min;
    const min = Number.isFinite(p.min) ? p.min : isInt ? 0 : -1;
    const max = Number.isFinite(p.max) ? p.max : isInt ? 100 : 1;
    const range = h("input", { type: "range", class: "pyd-range", "aria-label": p.label || p.name });
    if (log) {
      range.min = "0";
      range.max = String(LOG_STEPS);
      range.step = "1";
    } else {
      range.min = String(min);
      range.max = String(max);
      range.step = p.step ? String(p.step) : isInt ? "1" : "any";
    }
    const num = h("input", { type: "number", id, class: "pyd-num", step: isInt ? String(p.step || 1) : "any", inputmode: isInt ? "numeric" : "decimal" });
    const toSlider = (v) => (log ? (Math.log(clamp(v, min, max)) - Math.log(min)) / (Math.log(max) - Math.log(min)) * LOG_STEPS : clamp(v, min, max));
    const fromSlider = (s) => {
      let v = log ? Math.exp(Math.log(min) + (Number(s) / LOG_STEPS) * (Math.log(max) - Math.log(min))) : Number(s);
      if (isInt) v = Math.round(v);
      else if (log) v = sig3(v);
      return v;
    };
    const setAria = (v) => range.setAttribute("aria-valuetext", `${fmtNum(v, p.type)}${p.unit ? " " + p.unit : ""}`);
    range.addEventListener("input", () => {
      const v = fromSlider(range.value);
      num.value = boxText(v, isInt);
      show(v);
      setAria(v);
      this.emit(p, v, "input");
    });
    range.addEventListener("change", () => this.emit(p, fromSlider(range.value), "commit"));
    num.addEventListener("change", () => {
      let v = Number(num.value);
      if (!Number.isFinite(v) || num.value === "") {
        num.value = boxText(Number(this.values[p.id] ?? p.default), isInt);
        return;
      }
      if (isInt) v = Math.round(v);
      range.value = String(toSlider(v));
      show(v);
      setAria(v);
      this.emit(p, v, "commit");
    });
    const el = h("div", { class: "pyd-num-row" }, range, num);
    return {
      el,
      set: (v) => {
        const n = Number(v);
        if (!Number.isFinite(n)) return;
        range.value = String(toSlider(n));
        num.value = boxText(n, isInt);
        show(n);
        setAria(n);
      },
      focus: () => range.focus(),
    };
  }

  /* ---------- from outside ------------------------------------------------- */
  setValue(id, value) {
    this.values[id] = value;
    const c = this.controls.get(id);
    if (c) c.set(value);
  }

  setValues(values) {
    for (const [k, v] of Object.entries(values)) this.setValue(k, v);
  }

  setMode(id, mode, reason) {
    const c = this.controls.get(id);
    if (!c) return;
    const live = mode === "live";
    c.badge.className = `pyd-badge ${live ? "is-live" : "is-restart"}`;
    c.badge.replaceChildren(icon(live ? "live" : "restart"), h("span", null, live ? "Live" : "Restarts"));
    c.badge.title = reason || "";
    c.reason.textContent = reason || "";
  }

  /** demo:param links and code-line clicks: reveal and focus a control. */
  focusParam(id) {
    const c = this.controls.get(id);
    if (!c) return false;
    if (this.collapsed) this.setCollapsed(false);
    const det = c.root.closest("details");
    if (det) det.open = true;
    c.root.scrollIntoView({ block: "nearest", behavior: "smooth" });
    c.root.classList.remove("is-flash");
    void c.root.offsetWidth;
    c.root.classList.add("is-flash");
    c.focus();
    return true;
  }

  highlightParam(id, on) {
    for (const c of this.controls.values()) c.root.classList.remove("is-linked");
    const c = id && this.controls.get(id);
    if (c && on) c.root.classList.add("is-linked");
  }
}
