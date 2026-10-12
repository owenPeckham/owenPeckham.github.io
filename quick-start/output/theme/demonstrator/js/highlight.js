/* Python demonstrator -- syntax highlighting (highlight.js, loaded lazily).
 *
 * Shared by the code pane and the explainer so both look the same.  Only
 * languages from an allowlist are ever loaded (language names come from
 * Markdown fences, which are data).
 */
import { HIGHLIGHT_LANGUAGES, LIBS } from "./config.js";

const ALLOWED = new Set(["python", "json", "markdown", "plaintext", "yaml", "ini", "bash", "javascript", "xml", "css"]);
const ALIASES = { py: "python", python3: "python", sh: "bash", shell: "bash", console: "bash", yml: "yaml", js: "javascript", html: "xml", md: "markdown", text: "plaintext", txt: "plaintext", toml: "ini" };

let corePromise = null;
const langPromises = new Map();

export function languageFor(nameOrPath) {
  const s = String(nameOrPath || "").toLowerCase();
  const ext = s.includes(".") ? s.split(".").pop() : null;
  let lang = ext ? HIGHLIGHT_LANGUAGES[ext] : ALIASES[s] || s;
  if (!ALLOWED.has(lang)) lang = "plaintext";
  return lang;
}

async function hljsFor(lang) {
  if (!corePromise) {
    corePromise = import(LIBS.hljsCore).then((m) => m.default || m);
    corePromise.catch(() => {
      corePromise = null;
    });
  }
  const hljs = await corePromise;
  if (lang !== "plaintext" && !hljs.getLanguage(lang)) {
    if (!langPromises.has(lang)) {
      langPromises.set(
        lang,
        import(LIBS.hljsLanguage(lang))
          .then((m) => hljs.registerLanguage(lang, m.default || m))
          .catch(() => null)
      );
    }
    await langPromises.get(lang);
  }
  return hljs;
}

/** Highlight highlight.js-escaped HTML for `text`, or null if unavailable. */
export async function highlightHtml(text, lang) {
  const name = languageFor(lang);
  if (name === "plaintext") return null;
  let hljs;
  try {
    hljs = await hljsFor(name);
  } catch (e) {
    return null;
  }
  if (!hljs.getLanguage(name)) return null;
  return hljs.highlight(text, { language: name, ignoreIllegals: true }).value;
}

/**
 * highlight.js returns HTML (the escaped source wrapped in <span class>).
 * It is never assigned to innerHTML: it is parsed into an inert document
 * (DOMParser runs no scripts and loads nothing) and only its text and the
 * class names of its spans are copied into the page.
 */
function parseHighlighted(html) {
  return new DOMParser().parseFromString(`<!doctype html><body>${html}`, "text/html").body;
}

function rebuild(src, dst) {
  for (const child of src.childNodes) {
    if (child.nodeType === Node.TEXT_NODE) dst.appendChild(document.createTextNode(child.textContent));
    else if (child.nodeType === Node.ELEMENT_NODE) {
      const span = document.createElement("span");
      if (typeof child.className === "string" && child.className) span.className = child.className;
      rebuild(child, span);
      dst.appendChild(span);
    }
  }
  return dst;
}

/** Highlight a <code> element in place (explainer code blocks). */
export async function highlightCode(codeEl, lang) {
  const html = await highlightHtml(codeEl.textContent, lang);
  if (html === null) return;
  codeEl.replaceChildren(rebuild(parseHighlighted(html), document.createDocumentFragment()));
  codeEl.classList.add("hljs");
}

/**
 * Split highlighted HTML into one DocumentFragment per source line, closing
 * and re-opening spans that cross line breaks (multi-line strings/comments).
 */
export function splitLines(html, text) {
  const lines = [document.createDocumentFragment()];
  if (html === null) {
    const parts = text.split("\n");
    return parts.map((p) => {
      const f = document.createDocumentFragment();
      if (p) f.appendChild(document.createTextNode(p));
      return f;
    });
  }
  const body = parseHighlighted(html);
  const appendText = (parents, str) => {
    let target = lines[lines.length - 1];
    for (const p of parents) {
      const clone = document.createElement("span");
      clone.className = p.className;
      target.appendChild(clone);
      target = clone;
    }
    target.appendChild(document.createTextNode(str));
  };
  const walk = (node, parents) => {
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        const parts = child.textContent.split("\n");
        parts.forEach((part, i) => {
          if (i > 0) lines.push(document.createDocumentFragment());
          if (part) appendText(parents, part);
        });
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        walk(child, [...parents, child]);
      }
    }
  };
  walk(body, []);
  return lines;
}
