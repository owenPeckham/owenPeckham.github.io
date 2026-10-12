/* Python demonstrator -- the code pane.
 *
 * Shows every project file exactly as fetched (and executed): tabs for a few
 * files, a file tree for larger projects, highlight.js colouring, line
 * numbers, copy / raw / download-zip buttons, and optional editing with a
 * textarea overlay ("Run edited" re-runs the project, "Reset" restores the
 * fetched files).  Lines can be highlighted from outside (demo:code links,
 * tracebacks, parameter hover).
 */
import { highlightHtml, languageFor, splitLines } from "./highlight.js";
import { copyText, debounce, downloadBlob, h, icon, nextId } from "./util.js";
import { writeZip } from "./zip.js";

const TREE_THRESHOLD = 6;

export class CodePane {
  /**
   * @param {HTMLElement} el      pane body
   * @param {HTMLElement} tools   container for header buttons
   * @param {object} opts {files, main, editable, zipName, onRunEdited(files), onLineHover(file, line|null), onLineActivate(file, line)}
   */
  constructor(el, tools, opts) {
    this.el = el;
    this.tools = tools;
    this.opts = opts;
    this.files = opts.files;           // [{path, text, binary, size, url, bytes}]
    this.main = opts.main;
    this.original = new Map(this.files.map((f) => [f.path, f.text]));
    this.current = new Map(this.original);
    this.views = new Map();            // path -> {el, lines: [lineEl], built}
    this.paramLines = new Map();       // path -> Map(line -> [param ids])
    this.active = null;
    this.editing = false;
    this.build();
  }

  /* ---------- structure ---------------------------------------------------- */
  build() {
    const id = nextId("pyd-code");
    this.nav = h("div", { class: "pyd-files" });
    const many = this.files.length > TREE_THRESHOLD || (this.files.some((f) => f.path.includes("/")) && this.files.length > 3);
    if (many) this.buildTree();
    else this.buildTabs(id);
    this.viewport = h("div", { class: "pyd-code-views" });
    this.el.replaceChildren(h("div", { class: `pyd-code-shell ${many ? "has-tree" : "has-tabs"}` }, this.nav, this.viewport));

    this.copyBtn = h("button", { type: "button", class: "pyd-tool", title: "Copy this file", onclick: () => this.copy() }, icon("copy", "Copy file"));
    this.rawLink = h("a", { class: "pyd-tool", target: "_blank", rel: "noopener", title: "Open the raw file" }, icon("raw", "Raw file"));
    this.zipBtn = h("button", { type: "button", class: "pyd-tool", title: "Download the project (.zip)", onclick: () => this.downloadZip() }, icon("download", "Download project"));
    const buttons = [this.copyBtn, this.rawLink, this.zipBtn];
    if (this.opts.editable) {
      this.editBtn = h("button", { type: "button", class: "pyd-tool", "aria-pressed": "false", title: "Edit the code", onclick: () => this.toggleEdit() }, icon("edit", "Edit"));
      this.runEditedBtn = h("button", { type: "button", class: "pyd-tool pyd-tool-primary", hidden: true, onclick: () => this.runEdited() }, icon("play"), h("span", null, "Run edited"));
      this.resetBtn = h("button", { type: "button", class: "pyd-tool", hidden: true, title: "Restore the original files", onclick: () => this.resetEdits() }, icon("reload", "Reset to original"));
      buttons.push(this.editBtn, this.runEditedBtn, this.resetBtn);
    }
    this.tools.replaceChildren(...buttons);
    this.show(this.main);
  }

  buildTabs(id) {
    const list = h("div", { class: "pyd-tabs", role: "tablist", "aria-label": "Project files" });
    this.tabs = new Map();
    for (const f of this.files) {
      const tab = h(
        "button",
        { type: "button", role: "tab", class: "pyd-tab", id: `${id}-${this.tabs.size}`, "aria-selected": "false", tabindex: "-1", title: f.path },
        f.path === this.main ? h("span", { class: "pyd-main-mark", title: "Main file" }, "▶") : null,
        h("span", { class: "pyd-tab-name" }, f.path),
        h("span", { class: "pyd-edited-dot", hidden: true, title: "Edited" }, "●")
      );
      tab.addEventListener("click", () => this.show(f.path));
      tab.addEventListener("keydown", (e) => {
        const keys = { ArrowRight: 1, ArrowLeft: -1 };
        if (!(e.key in keys)) return;
        e.preventDefault();
        const paths = this.files.map((x) => x.path);
        const i = (paths.indexOf(f.path) + keys[e.key] + paths.length) % paths.length;
        this.show(paths[i]);
        this.tabs.get(paths[i]).focus();
      });
      this.tabs.set(f.path, tab);
      list.appendChild(tab);
    }
    this.nav.appendChild(list);
  }

  buildTree() {
    this.tabs = new Map();
    const root = { dirs: new Map(), files: [] };
    for (const f of this.files) {
      const parts = f.path.split("/");
      let node = root;
      for (const d of parts.slice(0, -1)) {
        if (!node.dirs.has(d)) node.dirs.set(d, { dirs: new Map(), files: [] });
        node = node.dirs.get(d);
      }
      node.files.push(f);
    }
    const render = (node, depth) => {
      const ul = h("ul", { class: "pyd-tree-list" });
      for (const f of node.files.sort((a, b) => (a.path === this.main ? -1 : b.path === this.main ? 1 : a.path.localeCompare(b.path)))) {
        const name = f.path.split("/").pop();
        const btn = h(
          "button",
          { type: "button", class: "pyd-tree-file", "aria-current": "false", title: f.path, style: { paddingLeft: `${0.5 + depth * 0.9}rem` } },
          icon("file"),
          h("span", null, name),
          f.path === this.main ? h("span", { class: "pyd-main-mark", title: "Main file" }, "main") : null,
          h("span", { class: "pyd-edited-dot", hidden: true, title: "Edited" }, "●")
        );
        btn.addEventListener("click", () => this.show(f.path));
        this.tabs.set(f.path, btn);
        ul.appendChild(h("li", null, btn));
      }
      for (const [d, child] of [...node.dirs.entries()].sort()) {
        const det = h("details", { open: true, class: "pyd-tree-dir" }, h("summary", { style: { paddingLeft: `${0.5 + depth * 0.9}rem` } }, icon("folder"), h("span", null, d + "/")), render(child, depth + 1));
        ul.appendChild(h("li", null, det));
      }
      return ul;
    };
    this.nav.appendChild(h("nav", { class: "pyd-tree", "aria-label": "Project files" }, render(root, 0)));
  }

  /* ---------- views -------------------------------------------------------- */
  async show(path) {
    const file = this.files.find((f) => f.path === path);
    if (!file) return false;
    if (this.editing && this.active !== path) this.stopEditing();
    this.active = path;
    for (const [p, tab] of this.tabs) {
      const on = p === path;
      if (tab.getAttribute("role") === "tab") {
        tab.setAttribute("aria-selected", String(on));
        tab.tabIndex = on ? 0 : -1;
      } else tab.setAttribute("aria-current", String(on));
      tab.classList.toggle("is-active", on);
    }
    let view = this.views.get(path);
    if (!view) {
      view = this.makeView(file);
      this.views.set(path, view);
      this.viewport.appendChild(view.el);
    }
    for (const [p, v] of this.views) v.el.hidden = p !== path;
    this.rawLink.href = file.url;
    this.copyBtn.disabled = file.binary;
    if (this.editBtn) this.editBtn.disabled = file.binary;
    if (!view.built) await this.fill(view, file);
    return true;
  }

  makeView(file) {
    const el = h("div", { class: "pyd-code", role: "region", "aria-label": `${file.path} source code`, tabindex: "0" });
    return { el, lines: [], built: false, path: file.path };
  }

  async fill(view, file) {
    if (file.binary) {
      view.el.replaceChildren(h("p", { class: "pyd-binary" }, `Binary file (${(file.size / 1024).toFixed(1)} KB) — use “Raw file” to download it.`));
      view.built = true;
      return;
    }
    const text = this.current.get(file.path) || "";
    const big = text.length > 400000;
    const html = big ? null : await highlightHtml(text, languageFor(file.path));
    const frags = splitLines(html, text);
    const code = h("code", { class: "pyd-code-lines hljs" });
    view.lines = [];
    frags.forEach((frag, i) => {
      const n = i + 1;
      const line = h("div", { class: "pyd-line", "data-line": n }, h("span", { class: "pyd-ln", "aria-hidden": "true" }, String(n)), h("span", { class: "pyd-lc" }, frag));
      view.lines.push(line);
      code.appendChild(line);
    });
    code.addEventListener("mouseover", (e) => this.onHover(view, e));
    code.addEventListener("mouseleave", () => this.opts.onLineHover && this.opts.onLineHover(view.path, null));
    code.addEventListener("click", (e) => {
      const line = e.target.closest(".pyd-line.is-param");
      if (line && e.target.closest(".pyd-ln")) this.opts.onLineActivate && this.opts.onLineActivate(view.path, Number(line.dataset.line));
    });
    view.el.replaceChildren(h("pre", { class: "pyd-pre" }, code));
    view.built = true;
    this.applyParamMarks(view);
  }

  onHover(view, e) {
    const line = e.target.closest(".pyd-line");
    if (!line || !this.opts.onLineHover) return;
    const n = Number(line.dataset.line);
    if (this._hoverLine === `${view.path}:${n}`) return;
    this._hoverLine = `${view.path}:${n}`;
    this.opts.onLineHover(view.path, line.classList.contains("is-param") ? n : null);
  }

  lineCounts() {
    const out = {};
    for (const f of this.files) out[f.path] = f.binary ? 0 : (this.current.get(f.path) || "").split("\n").length;
    return out;
  }

  /* ---------- highlights --------------------------------------------------- */
  async highlight(path, from, to, { scroll = true, kind = "is-highlight", flash = true } = {}) {
    if (!(await this.show(path))) return false;
    const view = this.views.get(path);
    for (const l of view.el.querySelectorAll(`.${kind}`)) l.classList.remove(kind, "is-flash");
    if (!from) return true;
    const last = Math.min(to || from, view.lines.length);
    if (from > view.lines.length) return false;
    for (let n = from; n <= last; n++) {
      const l = view.lines[n - 1];
      l.classList.add(kind);
      if (flash) {
        l.classList.remove("is-flash");
        void l.offsetWidth;
        l.classList.add("is-flash");
      }
    }
    if (scroll) {
      const target = view.lines[from - 1];
      const box = view.el;
      const top = target.offsetTop - box.clientHeight / 3;
      box.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    }
    return true;
  }

  clearHighlight(kind = "is-highlight") {
    for (const v of this.views.values()) for (const l of v.el.querySelectorAll(`.${kind}`)) l.classList.remove(kind, "is-flash");
  }

  /** Mark lines where parameters are defined (gutter dot + hover link). */
  setParamLines(params) {
    this.paramLines = new Map();
    for (const p of params) {
      if (!p.file || !p.line) continue;
      if (!this.paramLines.has(p.file)) this.paramLines.set(p.file, new Map());
      const m = this.paramLines.get(p.file);
      if (!m.has(p.line)) m.set(p.line, []);
      m.get(p.line).push(p.id);
    }
    for (const v of this.views.values()) this.applyParamMarks(v);
  }

  applyParamMarks(view) {
    for (const l of view.lines) {
      l.classList.remove("is-param");
      l.removeAttribute("title");
    }
    const m = this.paramLines.get(view.path);
    if (!m) return;
    for (const [line, ids] of m) {
      const l = view.lines[line - 1];
      if (!l) continue;
      l.classList.add("is-param");
      l.title = `Parameter${ids.length > 1 ? "s" : ""} ${ids.join(", ")} — click the line number to jump to the control`;
    }
  }

  /** Highlight the definition line of a parameter (control hover/focus). */
  async showParam(p, on) {
    if (!p || !p.file) return;
    if (!on) {
      this.clearHighlight("is-param-hover");
      return;
    }
    if (this.active !== p.file) return; // don't switch tabs on hover
    await this.highlight(p.file, p.line, p.end_line || p.line, { kind: "is-param-hover", flash: false });
  }

  /* ---------- actions -------------------------------------------------------- */
  async copy() {
    const ok = await copyText(this.current.get(this.active) || "");
    this.copyBtn.classList.toggle("is-done", ok);
    this.copyBtn.title = ok ? "Copied" : "Copy failed";
    setTimeout(() => {
      this.copyBtn.classList.remove("is-done");
      this.copyBtn.title = "Copy this file";
    }, 1500);
  }

  downloadZip() {
    const files = this.files.map((f) => ({ path: f.path, data: f.binary ? f.bytes : this.current.get(f.path) || "" }));
    downloadBlob(writeZip(files), (this.opts.zipName || "project") + (this.isEdited() ? "-edited" : "") + ".zip");
  }

  isEdited() {
    for (const [p, t] of this.current) if (t !== this.original.get(p)) return true;
    return false;
  }

  currentFiles() {
    return this.files.map((f) => ({ path: f.path, data: f.binary ? f.bytes : this.current.get(f.path) }));
  }

  /* ---------- editing ------------------------------------------------------- */
  toggleEdit() {
    if (this.editing) this.stopEditing();
    else this.startEditing();
  }

  startEditing() {
    const file = this.files.find((f) => f.path === this.active);
    if (!file || file.binary) return;
    const view = this.views.get(file.path);
    this.editing = true;
    this.editBtn.setAttribute("aria-pressed", "true");
    const pre = h("pre", { class: "pyd-editor-hl", "aria-hidden": "true" });
    const ta = h("textarea", {
      class: "pyd-editor-input",
      spellcheck: "false",
      autocapitalize: "off",
      autocomplete: "off",
      "aria-label": `Edit ${file.path}. Press Escape, then Tab, to leave the editor.`,
      wrap: "off",
    });
    ta.value = this.current.get(file.path);
    const wrap = h("div", { class: "pyd-editor" }, pre, ta);
    const paint = async () => {
      const text = ta.value;
      const html = await highlightHtml(text + "\n", languageFor(file.path));
      pre.replaceChildren();
      if (html === null) pre.textContent = text + "\n";
      else {
        const frags = splitLines(html, text + "\n");
        frags.forEach((f, i) => {
          pre.appendChild(f);
          if (i < frags.length - 1) pre.appendChild(document.createTextNode("\n"));
        });
      }
    };
    const repaint = debounce(paint, 120);
    let escaped = false;
    ta.addEventListener("input", () => {
      this.current.set(file.path, ta.value);
      this.markEdited();
      repaint();
    });
    ta.addEventListener("scroll", () => {
      pre.scrollTop = ta.scrollTop;
      pre.scrollLeft = ta.scrollLeft;
    });
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        escaped = true;
        return;
      }
      if (e.key === "Tab" && !escaped && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        const { selectionStart: s, selectionEnd: en, value } = ta;
        if (e.shiftKey) {
          const lineStart = value.lastIndexOf("\n", s - 1) + 1;
          if (value.slice(lineStart, lineStart + 4) === "    ") {
            ta.value = value.slice(0, lineStart) + value.slice(lineStart + 4);
            ta.selectionStart = ta.selectionEnd = Math.max(lineStart, s - 4);
          }
        } else {
          ta.value = value.slice(0, s) + "    " + value.slice(en);
          ta.selectionStart = ta.selectionEnd = s + 4;
        }
        ta.dispatchEvent(new Event("input"));
      } else if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        this.runEdited();
      } else escaped = false;
    });
    view.el.replaceChildren(wrap);
    paint();
    ta.focus();
    this.textarea = ta;
  }

  stopEditing() {
    if (!this.editing) return;
    this.editing = false;
    this.editBtn.setAttribute("aria-pressed", "false");
    const view = this.views.get(this.active);
    const file = this.files.find((f) => f.path === this.active);
    if (view && file) {
      view.built = false;
      this.fill(view, file);
    }
  }

  markEdited() {
    const edited = this.isEdited();
    for (const [p, tab] of this.tabs) {
      const dot = tab.querySelector(".pyd-edited-dot");
      if (dot) dot.hidden = this.current.get(p) === this.original.get(p);
    }
    if (this.runEditedBtn) this.runEditedBtn.hidden = !edited;
    if (this.resetBtn) this.resetBtn.hidden = !edited;
  }

  runEdited() {
    if (this.opts.onRunEdited) this.opts.onRunEdited(this.currentFiles());
  }

  resetEdits() {
    this.current = new Map(this.original);
    const wasEditing = this.editing;
    this.stopEditing();
    for (const v of this.views.values()) v.built = false;
    this.views.forEach((v, p) => {
      const f = this.files.find((x) => x.path === p);
      if (f) this.fill(v, f);
    });
    this.markEdited();
    if (wasEditing) this.startEditing();
    if (this.opts.onRunEdited) this.opts.onRunEdited(this.currentFiles(), { reset: true });
  }

  /** After a successful edited run: rebuild views from the edited text. */
  refreshViews() {
    for (const [p, v] of this.views) {
      if (this.editing && p === this.active) continue;
      v.built = false;
      const f = this.files.find((x) => x.path === p);
      if (f && !v.el.hidden) this.fill(v, f);
    }
  }
}
