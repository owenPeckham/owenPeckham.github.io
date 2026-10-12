/* Python demonstrator -- small shared helpers (DOM, URLs, storage). */

/**
 * Create an element without ever touching innerHTML.
 *   h("button", {class: "x", type: "button", onclick: fn, "aria-label": "Run"}, "Run")
 * Children may be strings (text nodes), nodes, arrays, null/false (skipped).
 */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, String(v));
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

/** Inline SVG icon from a fixed set of path data (no user input involved). */
const ICONS = {
  play: "M6 4l12 8-12 8z",
  pause: "M6 4h4v16H6zM14 4h4v16h-4z",
  stop: "M6 6h12v12H6z",
  step: "M5 4l10 8-10 8zM16 4h3v16h-3z",
  restart: "M12 5a7 7 0 1 1-6.6 4.7M5 3v6h6",
  collapse: "M6 15l6-6 6 6",
  expand: "M6 9l6 6 6-6",
  focus: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  unfocus: "M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5",
  copy: "M9 9h10v11H9zM5 15V4h10",
  download: "M12 4v11M7 10l5 5 5-5M5 20h14",
  raw: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  live: "M13 3L5 13h6l-1 8 8-10h-6z",
  reload: "M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5",
  info: "M12 8h.01M11 12h1v5h1M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z",
  close: "M6 6l12 12M18 6L6 18",
  menu: "M4 6h16M4 12h16M4 18h16",
  file: "M6 3h8l4 4v14H6zM14 3v4h4",
  folder: "M3 6h6l2 2h10v11H3z",
  sliders: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4",
  tour: "M5 4v16M5 5h11l-2 4 2 4H5",
};

export function icon(name, label) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "pyd-icon");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", ICONS[name] || ICONS.info);
  svg.appendChild(path);
  if (!label) return svg;
  const wrap = document.createElement("span");
  wrap.className = "pyd-icon-wrap";
  wrap.append(svg, h("span", { class: "pyd-sr" }, label));
  return wrap;
}

export function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

export function debounce(fn, ms) {
  let t = 0;
  const wrapped = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(t);
  wrapped.flush = (...args) => {
    clearTimeout(t);
    fn(...args);
  };
  return wrapped;
}

/** Format a number for a readout (3-4 significant figures, no exponent noise). */
export function fmtNum(v, type) {
  if (typeof v !== "number" || !Number.isFinite(v)) return String(v);
  if (type === "int") return String(Math.round(v));
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return v.toExponential(2);
  if (a >= 100) return String(Math.round(v * 10) / 10);
  return String(Number(v.toPrecision(3)));
}

/** Slug for heading ids: "Damping & resonance" -> "damping-resonance". */
export function slugify(text) {
  return (
    String(text)
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, "")
      .trim()
      .replace(/[\s-]+/g, "-")
      .slice(0, 64) || "section"
  );
}

/** localStorage that never throws (private mode, blocked storage...). */
export const storage = {
  get(key, fallback = null) {
    try {
      const raw = window.localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) {
      return fallback;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      /* storage unavailable: settings just won't persist */
    }
  },
  remove(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (e) {
      /* ignore */
    }
  },
};

const scriptCache = new Map();

/** Load a classic script once (optionally with SRI). */
export function loadScript(spec) {
  const { url, integrity } = typeof spec === "string" ? { url: spec } : spec;
  if (scriptCache.has(url)) return scriptCache.get(url);
  const p = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = url;
    s.async = true;
    s.crossOrigin = "anonymous";
    if (integrity) s.integrity = integrity;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error(`could not load ${url}`));
    document.head.appendChild(s);
  });
  scriptCache.set(url, p);
  p.catch(() => scriptCache.delete(url));
  return p;
}

/** Load a stylesheet once (optionally with SRI). */
export function loadStyle(spec) {
  const { url, integrity } = typeof spec === "string" ? { url: spec } : spec;
  if (scriptCache.has(url)) return scriptCache.get(url);
  const p = new Promise((resolve, reject) => {
    const l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = url;
    l.crossOrigin = "anonymous";
    if (integrity) l.integrity = integrity;
    l.onload = () => resolve();
    l.onerror = () => reject(new Error(`could not load ${url}`));
    document.head.appendChild(l);
  });
  scriptCache.set(url, p);
  return p;
}

/**
 * Validate a project-relative path from a manifest or query string.
 * Returns the normalised POSIX path, or null for anything that could escape
 * the demo folder (absolute paths, "..", schemes, backslashes, NUL...).
 */
export function safeRelPath(p) {
  if (typeof p !== "string") return null;
  const s = p.trim();
  if (!s || s.length > 300) return null;
  if (/[\\\0]/.test(s) || /^[a-z][a-z0-9+.-]*:/i.test(s) || s.startsWith("/") || s.startsWith("~")) return null;
  const out = [];
  for (const seg of s.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === "..") return null;
    if (/^\s|\s$/.test(seg)) return null;
    out.push(seg);
  }
  return out.length ? out.join("/") : null;
}

/** Is this URL same-origin or on the allowlist? */
export function isAllowedOrigin(url, allowed = []) {
  try {
    const u = new URL(url, document.baseURI);
    if (u.protocol !== "http:" && u.protocol !== "https:") return false;
    return u.origin === location.origin || allowed.includes(u.origin);
  } catch (e) {
    return false;
  }
}

/** Resolve `ref` against `base` (both may be relative to the page). */
export function resolveUrl(ref, base) {
  return new URL(ref, new URL(base, document.baseURI)).href;
}

export function prefersReducedMotion() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

/** Copy text to the clipboard (with a fallback for older browsers). */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const ta = h("textarea", { style: { position: "fixed", opacity: "0" } });
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (err) {
      ok = false;
    }
    ta.remove();
    return ok;
  }
}

/** Trigger a download of a Blob with a file name. */
export function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** Tiny event emitter. */
export class Emitter {
  constructor() {
    this._handlers = new Map();
  }
  on(type, fn) {
    if (!this._handlers.has(type)) this._handlers.set(type, new Set());
    this._handlers.get(type).add(fn);
    return () => this._handlers.get(type).delete(fn);
  }
  emit(type, data) {
    for (const fn of this._handlers.get(type) || []) {
      try {
        fn(data);
      } catch (e) {
        console.error("[pyd]", e);
      }
    }
  }
}

/** Is the text likely binary (for the code pane)? */
export function looksBinary(bytes) {
  const n = Math.min(bytes.length, 4096);
  for (let i = 0; i < n; i++) if (bytes[i] === 0) return true;
  return false;
}

let uid = 0;
export function nextId(prefix = "pyd") {
  uid += 1;
  return `${prefix}-${uid}`;
}
