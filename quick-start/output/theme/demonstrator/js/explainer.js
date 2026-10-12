/* Python demonstrator -- the explainer pane (Markdown write-up).
 *
 * Pipeline: fetch .md -> front matter -> markdown-it (+ footnotes, maths,
 * guided-tour step comments) -> DOMPurify (sanitize.js) -> post-processing
 * in the DOM (heading anchors, figures, callouts, KaTeX, highlight.js,
 * demo: action links, table of contents).
 *
 * Libraries load lazily and in parallel with Python, so visitors can read
 * the explainer before the simulator is ready.  KaTeX only loads when the
 * text contains maths, highlight.js only when it has code blocks.
 */
import { ALLOWED_ORIGINS, LIBS } from "./config.js";
import { describeAction, parseDemoHref, validateDemoAction } from "./demolinks.js";
import { sanitizeExplainerHtml } from "./sanitize.js";
import { copyText, h, icon, loadScript, loadStyle, prefersReducedMotion, slugify } from "./util.js";
import { highlightCode } from "./highlight.js";

/* ---------- front matter (a small, safe YAML subset) ---------------------- */

function yamlScalar(v) {
  const s = v.trim();
  if (!s) return "";
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) {
    return s.slice(1, -1).replace(/\\"/g, '"');
  }
  if (/^(true|yes|on)$/i.test(s)) return true;
  if (/^(false|no|off)$/i.test(s)) return false;
  if (/^(null|~)$/i.test(s)) return null;
  if (/^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$/.test(s)) return Number(s);
  if (s.startsWith("[") && s.endsWith("]")) {
    return s.slice(1, -1).split(",").map((x) => yamlScalar(x)).filter((x) => x !== "");
  }
  return s;
}

/** Split `---\nkey: value\n---\n` front matter off a Markdown document. */
export function parseFrontMatter(text) {
  const src = String(text).replace(/^﻿/, "");
  const m = /^---[ \t]*\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/.exec(src);
  if (!m) return { meta: {}, body: src, lines: 0 };
  const meta = {};
  let listKey = null;
  for (const raw of m[1].split(/\r?\n/)) {
    if (!raw.trim() || raw.trim().startsWith("#")) continue;
    const item = /^\s*-\s+(.*)$/.exec(raw);
    if (item && listKey) {
      if (!Array.isArray(meta[listKey])) meta[listKey] = [];
      meta[listKey].push(yamlScalar(item[1]));
      continue;
    }
    const kv = /^([A-Za-z_][\w-]*)\s*:\s*(.*)$/.exec(raw);
    if (!kv) continue;
    const key = kv[1].toLowerCase();
    if (key === "__proto__" || key === "constructor" || key === "prototype") continue;
    if (kv[2].trim() === "") {
      listKey = key;
      meta[key] = [];
    } else {
      listKey = null;
      meta[key] = yamlScalar(kv[2]);
    }
  }
  const lines = m[0].split("\n").length - 1;
  return { meta, body: src.slice(m[0].length), lines };
}

/* ---------- markdown-it plugins -------------------------------------------- */

/** $inline$, $$display$$, \(inline\), \[display\] -> elements KaTeX renders later. */
function mathPlugin(md) {
  const isWS = (c) => c === 0x20 || c === 0x09 || c === 0x0a;

  md.inline.ruler.after("escape", "pyd_math_inline", (state, silent) => {
    const src = state.src;
    const start = state.pos;
    let open;
    let close;
    let display = false;
    if (src.startsWith("$$", start)) {
      open = "$$";
      close = "$$";
      display = true;
    } else if (src[start] === "$") {
      open = "$";
      close = "$";
      if (isWS(src.charCodeAt(start + 1))) return false;
    } else if (src.startsWith("\\(", start)) {
      open = "\\(";
      close = "\\)";
    } else if (src.startsWith("\\[", start)) {
      open = "\\[";
      close = "\\]";
      display = true;
    } else return false;
    let pos = start + open.length;
    while (true) {
      const found = src.indexOf(close, pos);
      if (found < 0) return false;
      if (src[found - 1] === "\\" && close === "$") {
        pos = found + 1;
        continue;
      }
      if (open === "$") {
        if (isWS(src.charCodeAt(found - 1))) return false;
        const after = src.charCodeAt(found + 1);
        if (after >= 0x30 && after <= 0x39) return false; // "$5 and $6" is money
      }
      const content = src.slice(start + open.length, found);
      if (!content.trim()) return false;
      if (!silent) {
        const token = state.push(display ? "pyd_math_display_inline" : "pyd_math_inline", "", 0);
        token.content = content;
      }
      state.pos = found + close.length;
      return true;
    }
  });

  md.block.ruler.before("fence", "pyd_math_block", (state, startLine, endLine, silent) => {
    const pos = state.bMarks[startLine] + state.tShift[startLine];
    const max = state.eMarks[startLine];
    const first = state.src.slice(pos, max);
    let open;
    let close;
    if (first.startsWith("$$")) {
      open = "$$";
      close = "$$";
    } else if (first.startsWith("\\[")) {
      open = "\\[";
      close = "\\]";
    } else return false;
    if (silent) return true;
    let content = first.slice(open.length);
    let line = startLine;
    let found = false;
    if (content.trimEnd().endsWith(close) && content.trim().length > close.length - 1) {
      content = content.trimEnd().slice(0, -close.length);
      found = true;
    }
    while (!found) {
      line += 1;
      if (line >= endLine) return false;
      const lp = state.bMarks[line] + state.tShift[line];
      const lm = state.eMarks[line];
      const text = state.src.slice(lp, lm);
      if (text.trimEnd().endsWith(close)) {
        content += "\n" + text.trimEnd().slice(0, -close.length);
        found = true;
      } else content += "\n" + text;
    }
    state.line = line + 1;
    const token = state.push("pyd_math_block", "div", 0);
    token.block = true;
    token.content = content.trim();
    token.map = [startLine, state.line];
    return true;
  });

  const esc = md.utils.escapeHtml;
  md.renderer.rules.pyd_math_inline = (tokens, i) => `<span class="pyd-math pyd-math-inline">${esc(tokens[i].content)}</span>`;
  md.renderer.rules.pyd_math_display_inline = (tokens, i) => `<span class="pyd-math pyd-math-display">${esc(tokens[i].content)}</span>`;
  md.renderer.rules.pyd_math_block = (tokens, i) => `<div class="pyd-math pyd-math-display">${esc(tokens[i].content)}</div>\n`;
}

/** `<!-- step: preset=resonance code=main.py:12-20 -->` after a heading
 * marks that heading as a guided-tour step. */
function stepPlugin(md, steps) {
  md.core.ruler.push("pyd_steps", (state) => {
    const toks = state.tokens;
    for (let i = 0; i < toks.length; i++) {
      const t = toks[i];
      if (t.type !== "html_block") continue;
      const m = /^\s*<!--\s*step\b:?([\s\S]*?)-->\s*$/i.exec(t.content);
      if (!m) continue;
      // nearest preceding heading
      for (let j = i - 1; j >= 0; j--) {
        if (toks[j].type === "heading_open") {
          const n = steps.length;
          steps.push(m[1].trim());
          toks[j].attrJoin("class", `pyd-step pyd-step-${n}`);
          break;
        }
      }
      t.content = "";
    }
  });
}

/* ---------- library loading ------------------------------------------------------ */

let mdLibs = null;
function loadMarkdownLibs() {
  if (!mdLibs) {
    mdLibs = Promise.all([
      loadScript(LIBS.markdownIt).then(() => loadScript(LIBS.footnote)),
      import(LIBS.dompurify).then((m) => m.default || m),
    ]).then(([, purify]) => ({ markdownit: window.markdownit, footnote: window.markdownitFootnote, purify }));
    mdLibs.catch(() => {
      mdLibs = null;
    });
  }
  return mdLibs;
}

let katexP = null;
function loadKatex() {
  if (!katexP) {
    katexP = Promise.all([import(LIBS.katex), loadStyle(LIBS.katexCss)]).then(([m]) => m.default || m);
    katexP.catch(() => {
      katexP = null;
    });
  }
  return katexP;
}

/** Start downloading the Markdown libraries early (called at page start). */
export function preloadExplainerLibs() {
  loadMarkdownLibs().catch(() => {});
}

/* ---------- rendering ------------------------------------------------------------ */

const CALLOUTS = { NOTE: "Note", TIP: "Tip", IMPORTANT: "Important", WARNING: "Warning", CAUTION: "Caution" };

/**
 * Render Markdown to a sanitised DocumentFragment.
 * Returns {fragment, meta, steps, blocked, missingAlt, hasMath}.
 */
export async function renderMarkdown(text, { baseUrl, allowIframes = false, linkPolicy = "any" } = {}) {
  const { meta, body } = parseFrontMatter(text);
  const libs = await loadMarkdownLibs();
  const steps = [];
  const md = libs.markdownit({ html: true, linkify: true, typographer: true });
  md.use(libs.footnote);
  md.use(mathPlugin);
  md.use((m) => stepPlugin(m, steps));
  // markdown-it validates link schemes; allow demo: and keep the defaults
  const validate = md.validateLink.bind(md);
  md.validateLink = (url) => /^demo:/i.test(url.trim()) || validate(url);
  // keep demo: links as written (no percent-encoding of & = etc.)
  const normalize = md.normalizeLink.bind(md);
  md.normalizeLink = (url) => (/^demo:/i.test(url.trim()) ? url.trim() : normalize(url));
  const html = md.render(body);
  const { fragment, blocked, missingAlt } = sanitizeExplainerHtml(libs.purify, html, {
    baseUrl,
    allowedOrigins: ALLOWED_ORIGINS,
    allowIframes,
    linkPolicy,
  });
  const hasMath = Boolean(fragment.querySelector(".pyd-math"));
  return { fragment, meta, steps, blocked, missingAlt, hasMath };
}

/** Wrap a lone image with a title in a <figure> with a caption. */
function makeFigures(root) {
  for (const p of root.querySelectorAll("p")) {
    const kids = [...p.childNodes].filter((n) => !(n.nodeType === 3 && !n.textContent.trim()));
    if (kids.length !== 1 || kids[0].nodeName !== "IMG") continue;
    const img = kids[0];
    const fig = h("figure", { class: "pyd-figure" });
    p.replaceWith(fig);
    fig.appendChild(img);
    const caption = img.getAttribute("title");
    if (caption) {
      img.removeAttribute("title");
      fig.appendChild(h("figcaption", null, caption));
    }
  }
}

function makeCallouts(root) {
  for (const bq of root.querySelectorAll("blockquote")) {
    const first = bq.firstElementChild;
    if (!first || first.nodeName !== "P") continue;
    const m = /^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/i.exec(first.textContent);
    if (!m) continue;
    const kind = m[1].toUpperCase();
    // strip the marker from the first text node
    const walker = document.createTreeWalker(first, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    let remaining = m[0].length;
    while (node && remaining > 0) {
      const take = Math.min(remaining, node.textContent.length);
      node.textContent = node.textContent.slice(take);
      remaining -= take;
      node = walker.nextNode();
    }
    if (first.firstChild && first.firstChild.nodeName === "BR") first.firstChild.remove();
    if (!first.textContent.trim() && !first.querySelector("img")) first.remove();
    bq.classList.add("pyd-callout", `pyd-callout-${kind.toLowerCase()}`);
    bq.prepend(h("p", { class: "pyd-callout-title" }, CALLOUTS[kind]));
  }
}

async function renderMath(root, katexPromise) {
  const els = root.querySelectorAll(".pyd-math");
  if (!els.length) return;
  let katex;
  try {
    katex = await katexPromise;
  } catch (e) {
    for (const el of els) el.classList.add("pyd-math-failed");
    console.warn("[pyd] KaTeX could not be loaded; showing TeX source", e);
    return;
  }
  for (const el of els) {
    const tex = el.textContent;
    try {
      katex.render(tex, el, {
        displayMode: el.classList.contains("pyd-math-display"),
        throwOnError: false,
        trust: false,
        strict: "ignore",
        maxExpand: 500,
        maxSize: 40, // em; keeps a stray \rule{} from blowing up the layout
        output: "htmlAndMathml",
      });
    } catch (e) {
      el.textContent = tex;
      el.classList.add("pyd-math-failed");
    }
  }
}

/* ---------- the pane ------------------------------------------------------------ */

export class Explainer {
  /**
   * @param {HTMLElement} body   scrolling container for the article
   * @param {object} opts {baseUrl, onAction(action, el), tocMode, onMeta(meta)}
   */
  constructor(body, opts) {
    this.body = body;
    this.opts = opts;
    this.links = [];          // {el, action}
    this.steps = [];          // {heading, actions}
    this.headings = [];
    this.tourOn = false;
    this.ctx = null;
    this._spy = null;
    this._tourObs = null;
  }

  /** Render Markdown text fetched from `url` into the pane. */
  async renderText(text, url) {
    this.url = url;
    const baseUrl = new URL("./", url).href;
    const out = await renderMarkdown(text, { baseUrl, allowIframes: this.opts.allowIframes });
    const katexP = out.hasMath ? loadKatex() : null;
    const article = h("article", { class: "pyd-article page-body" });
    article.appendChild(out.fragment);
    makeFigures(article);
    makeCallouts(article);
    for (const t of article.querySelectorAll("table")) {
      const wrap = h("div", { class: "pyd-table-wrap", tabindex: "0", role: "region", "aria-label": "Table" });
      t.replaceWith(wrap);
      wrap.appendChild(t);
    }
    this.prepareHeadings(article);
    this.prepareLinks(article);
    this.prepareSteps(article, out.steps);
    const codeBlocks = [...article.querySelectorAll("pre > code")];
    this.body.replaceChildren();
    const toc = this.buildToc(out.meta);
    if (toc) this.body.appendChild(toc);
    this.body.appendChild(article);
    this.article = article;
    if (out.blocked.length) console.warn("[pyd] explainer: blocked external content", out.blocked);
    if (out.missingAlt) console.warn(`[pyd] explainer: ${out.missingAlt} image(s) without alt text`);
    this.meta = out.meta;
    // decorations that need libraries
    const jobs = [];
    if (katexP) jobs.push(renderMath(article, katexP));
    for (const code of codeBlocks) {
      const lang = (/language-([\w-]+)/.exec(code.className) || [])[1] || "plaintext";
      jobs.push(highlightCode(code, lang).catch(() => {}));
    }
    await Promise.all(jobs);
    this.startScrollSpy();
    return { meta: out.meta, url };
  }

  prepareHeadings(article) {
    const used = new Set();
    this.headings = [];
    for (const hd of article.querySelectorAll("h1, h2, h3, h4")) {
      let id = slugify(hd.textContent);
      let n = 2;
      while (used.has(id) || document.getElementById(id)) id = `${slugify(hd.textContent)}-${n++}`;
      used.add(id);
      hd.id = id;
      hd.tabIndex = -1;
      const anchor = h(
        "a",
        { class: "pyd-anchor", href: `#${id}`, "aria-label": `Copy link to “${hd.textContent}”`, title: "Copy link to this section" },
        "#"
      );
      anchor.addEventListener("click", (e) => {
        e.preventDefault();
        const url = new URL(location.href);
        url.hash = id;
        history.replaceState(history.state, "", url.href);
        copyText(url.href).then((ok) => {
          anchor.dataset.copied = ok ? "1" : "";
          anchor.title = ok ? "Link copied" : "Copy link to this section";
          setTimeout(() => {
            delete anchor.dataset.copied;
          }, 1500);
        });
        this.scrollTo(id);
      });
      hd.appendChild(anchor);
      this.headings.push({ id, level: Number(hd.tagName[1]), text: hd.textContent.replace(/#$/, "").trim(), el: hd });
    }
  }

  buildToc(meta) {
    const mode = String(meta.toc !== undefined ? meta.toc : this.opts.tocMode || "auto").toLowerCase();
    const items = this.headings.filter((x) => x.level === 2 || x.level === 3);
    const show = mode === "true" || mode === "yes" || (mode === "auto" && items.filter((x) => x.level === 2).length >= 3);
    if (!show || !items.length) return null;
    const list = h("ol", { class: "pyd-toc-list" });
    for (const it of items) {
      const a = h("a", { href: `#${it.id}`, class: `pyd-toc-l${it.level}` }, it.text);
      a.addEventListener("click", (e) => {
        e.preventDefault();
        this.scrollTo(it.id, true);
      });
      it.tocLink = a;
      list.appendChild(h("li", null, a));
    }
    return h("nav", { class: "pyd-toc", "aria-label": "Contents of the explainer" }, h("details", { open: true }, h("summary", null, "Contents"), list));
  }

  /** Does the pane scroll by itself (split layouts), or grow with its
   * content so that the page scrolls (tabs, stacked)? */
  scrolls() {
    return this.body.scrollHeight > this.body.clientHeight + 2 && getComputedStyle(this.body).overflowY !== "visible";
  }

  /** Scroll the explainer to a heading: inside the pane when it scrolls,
   * otherwise the page (below any sticky bar such as the tab bar). */
  scrollTo(id, focus = false, smooth = true) {
    const el = this.body.querySelector(`#${CSS.escape(id)}`);
    if (!el) return false;
    const behavior = smooth && !prefersReducedMotion() ? "smooth" : "auto";
    if (this.scrolls()) {
      const top = el.getBoundingClientRect().top - this.body.getBoundingClientRect().top + this.body.scrollTop - 8;
      this.body.scrollTo({ top, behavior });
    } else {
      const offset = this.opts.stickyOffset ? this.opts.stickyOffset() : 0;
      window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - offset - 8, behavior });
    }
    if (focus) el.focus({ preventScroll: true });
    if (this.opts.onSectionShown) this.opts.onSectionShown(el);
    return true;
  }

  startScrollSpy() {
    if (this._spy) this._spy.disconnect();
    const linked = this.headings.filter((x) => x.tocLink);
    if (!linked.length || !("IntersectionObserver" in window)) return;
    const visible = new Set();
    this._spy = new IntersectionObserver(
      (entries) => {
        for (const e of entries) (e.isIntersecting ? visible.add(e.target.id) : visible.delete(e.target.id));
        let current = null;
        for (const x of linked) {
          if (visible.has(x.id)) {
            current = x;
            break;
          }
        }
        if (!current) {
          // the last heading above the top edge
          const top = this.scrolls() ? this.body.getBoundingClientRect().top : 0;
          for (const x of linked) if (x.el.getBoundingClientRect().top < top + 40) current = x;
        }
        for (const x of linked) {
          const on = current && x.id === current.id;
          x.tocLink.classList.toggle("is-current", Boolean(on));
          if (on) x.tocLink.setAttribute("aria-current", "location");
          else x.tocLink.removeAttribute("aria-current");
        }
      },
      { root: this.scrolls() ? this.body : null, rootMargin: "0px 0px -60% 0px", threshold: [0, 1] }
    );
    for (const x of linked) this._spy.observe(x.el);
  }

  /* -- demo: links ------------------------------------------------------------ */
  prepareLinks(article) {
    this.links = [];
    for (const a of article.querySelectorAll('a[href^="demo:" i]')) {
      const action = parseDemoHref(a.getAttribute("href"));
      if (action.type === "invalid") {
        console.warn(`[pyd] explainer: ${action.reason} — "${a.getAttribute("href")}" shown as plain text`);
        this.degrade(a);
        continue;
      }
      a.setAttribute("href", "#");
      a.setAttribute("role", "button");
      a.classList.add("pyd-demo-link", `pyd-demo-${action.type}`);
      a.title = describeAction(action);
      const ico = { run: "play", pause: "pause", restart: "restart", step: "step", code: "file", param: "sliders", set: "sliders", preset: "sliders", reset: "reload", stop: "stop" }[action.type];
      a.prepend(icon(ico));
      a.addEventListener("click", (e) => {
        e.preventDefault();
        this.trigger(action, a);
      });
      a.addEventListener("keydown", (e) => {
        if (e.key === " ") {
          e.preventDefault();
          this.trigger(action, a);
        }
      });
      this.links.push({ el: a, action });
    }
  }

  degrade(a) {
    const span = h("span", { class: "pyd-demo-dead" });
    while (a.firstChild) span.appendChild(a.firstChild);
    for (const svg of span.querySelectorAll(".pyd-icon")) svg.remove();
    a.replaceWith(span);
  }

  trigger(action, el) {
    if (this.ctx) {
      const v = validateDemoAction(action, this.ctx);
      if (!v.ok) {
        console.warn(`[pyd] explainer link ignored: ${v.reason}`);
        return;
      }
      action = v.action;
    }
    this.opts.onAction(action, el);
  }

  /** Called once parameters are known: links pointing at things that don't
   * exist degrade to plain text (with a console warning). */
  validateLinks(ctx) {
    this.ctx = ctx;
    this.links = this.links.filter(({ el, action }) => {
      const v = validateDemoAction(action, ctx);
      if (!v.ok) {
        console.warn(`[pyd] explainer: ${v.reason} — link "${el.textContent.trim()}" shown as plain text`);
        this.degrade(el);
        return false;
      }
      return true;
    });
    for (const s of this.steps) {
      s.actions = s.actions.filter((a) => {
        const v = validateDemoAction(a, ctx);
        if (!v.ok) console.warn(`[pyd] tour step "${s.heading.textContent.trim()}": ${v.reason}`);
        return v.ok;
      });
    }
  }

  /* -- guided tour ---------------------------------------------------------------- */
  prepareSteps(article, specs) {
    this.steps = [];
    specs.forEach((spec, n) => {
      const heading = article.querySelector(`.pyd-step-${n}`);
      if (!heading) return;
      const actions = [];
      for (const tok of spec.split(/\s+/).filter(Boolean)) {
        const a = parseDemoHref("demo:" + tok);
        if (a.type === "invalid") console.warn(`[pyd] tour step: ${a.reason}`);
        else actions.push(a);
      }
      if (!actions.length) return;
      const btn = h(
        "button",
        { type: "button", class: "pyd-step-btn", title: actions.map(describeAction).join("; ") },
        icon("tour"),
        h("span", null, "Show")
      );
      const step = { heading, actions, btn };
      btn.addEventListener("click", () => this.runStep(step));
      heading.appendChild(btn);
      this.steps.push(step);
    });
  }

  runStep(step) {
    if (!step) return;
    for (const s of this.steps) s.heading.classList.toggle("is-active-step", s === step);
    for (const a of step.actions) this.trigger(a, step.btn);
  }

  setTour(on) {
    this.tourOn = on;
    if (this._tourObs) this._tourObs.disconnect();
    if (!on || !this.steps.length) return;
    let last = null;
    this._tourObs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          const step = this.steps.find((s) => s.heading === e.target);
          if (step && step !== last) {
            last = step;
            this.runStep(step);
          }
        }
      },
      { root: this.scrolls() ? this.body : null, rootMargin: "0px 0px -70% 0px" }
    );
    for (const s of this.steps) this._tourObs.observe(s.heading);
  }

  /** The layout changed (e.g. split <-> tabs): observers follow the new scroller. */
  relayout() {
    if (!this.article) return;
    this.startScrollSpy();
    if (this.tourOn) this.setTour(true);
  }
}
