/* Python demonstrator -- embedding helper for other pages.
 *
 *   <py-demo demo="/examples/oscillator/" panes="sim" height="520"></py-demo>
 *   <script type="module" src="/theme/demonstrator/js/embed.js"></script>
 *
 * (On this site the script is added automatically to any page whose content
 * contains <py-demo>.)  The element creates a lazily loaded <iframe> of the
 * demonstrator page in embed mode, resizes it to fit its content, and keeps
 * its theme in step with the host page -- all through origin-checked
 * postMessage.  Plain iframes marked with data-pyd-embed get the same
 * resizing and theme sync.
 *
 * Attributes: demo | script (+ files, explainer), panes, layout, height,
 * load (visible|click|eager), autorun, fps, quality, editable, theme, title,
 * page (URL of the demonstrator page; defaults to the site's /demonstrator.html).
 */
const DEFAULT_PAGE = new URL("../../../demonstrator.html", import.meta.url).href;
const PASS = ["demo", "script", "files", "explainer", "panes", "layout", "load", "autorun", "fps", "quality", "editable", "toc"];
let counter = 0;
const frames = new Map(); // instance id -> iframe

function hostTheme() {
  const root = document.documentElement;
  const t = root.getAttribute("data-theme");
  if (t === "dark" || t === "light") return t;
  // otherwise use the page's actual background colour
  const bg = getComputedStyle(document.body).backgroundColor;
  const m = /rgba?\((\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(bg || "");
  if (!m) return "light";
  const [r, g, b] = m.slice(1).map(Number);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 100 ? "dark" : "light";
}

function sendTheme(iframe) {
  try {
    const origin = new URL(iframe.src, location.href).origin;
    if (iframe.contentWindow) iframe.contentWindow.postMessage({ type: "pyd:theme", theme: hostTheme() }, origin);
  } catch (e) {
    /* not loaded yet */
  }
}

window.addEventListener("message", (e) => {
  const d = e.data;
  if (!d || typeof d !== "object" || typeof d.type !== "string" || !d.type.startsWith("pyd:")) return;
  for (const iframe of frames.values()) {
    if (e.source !== iframe.contentWindow) continue;
    let origin;
    try {
      origin = new URL(iframe.src, location.href).origin;
    } catch (err) {
      return;
    }
    if (e.origin !== origin) return;
    if (d.type === "pyd:resize" && Number.isFinite(d.height)) {
      if (!iframe.hasAttribute("data-fixed-height")) iframe.style.height = `${Math.max(160, Math.min(4000, Math.ceil(d.height)))}px`;
    } else if (d.type === "pyd:hello") {
      sendTheme(iframe);
    }
  }
});

const observer = new MutationObserver(() => {
  for (const f of frames.values()) sendTheme(f);
});
observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
if (window.matchMedia) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const fn = () => {
    for (const f of frames.values()) sendTheme(f);
  };
  if (mq.addEventListener) mq.addEventListener("change", fn);
}

function register(iframe) {
  if (!iframe.dataset.pydInstance) iframe.dataset.pydInstance = `e${++counter}`;
  frames.set(iframe.dataset.pydInstance, iframe);
  iframe.addEventListener("load", () => sendTheme(iframe));
}

class PyDemo extends HTMLElement {
  static get observedAttributes() {
    return [...PASS, "height", "theme", "title", "page"];
  }

  connectedCallback() {
    this.render();
  }

  attributeChangedCallback() {
    if (this.isConnected && this.iframe) this.render();
  }

  disconnectedCallback() {
    if (this.iframe) frames.delete(this.iframe.dataset.pydInstance);
  }

  render() {
    const page = new URL(this.getAttribute("page") || DEFAULT_PAGE, location.href);
    if (page.origin !== new URL(DEFAULT_PAGE).origin) {
      this.textContent = "py-demo: the demonstrator page must be on this site.";
      return;
    }
    const src = new URL(page.href);
    for (const k of PASS) if (this.hasAttribute(k)) src.searchParams.set(k, this.getAttribute(k));
    src.searchParams.set("embed", "1");
    if (this.hasAttribute("theme")) src.searchParams.set("theme", this.getAttribute("theme"));
    const inst = this.iframe ? this.iframe.dataset.pydInstance : `e${++counter}`;
    src.searchParams.set("instance", inst);
    const iframe = this.iframe || document.createElement("iframe");
    iframe.dataset.pydInstance = inst;
    iframe.title = this.getAttribute("title") || "Interactive Python demo";
    iframe.loading = "lazy";
    iframe.allow = "clipboard-write";
    iframe.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    iframe.style.cssText = "display:block;width:100%;border:0;";
    const height = this.getAttribute("height");
    iframe.style.height = height ? `${parseInt(height, 10) || 520}px` : "520px";
    if (height && this.hasAttribute("fixed")) iframe.setAttribute("data-fixed-height", "");
    if (iframe.src !== src.href) iframe.src = src.href;
    if (!this.iframe) {
      this.iframe = iframe;
      this.replaceChildren(iframe);
      register(iframe);
    }
  }
}

if (!customElements.get("py-demo")) customElements.define("py-demo", PyDemo);

// Plain iframes: <iframe data-pyd-embed src="/demonstrator.html?demo=...&embed=1">
for (const f of document.querySelectorAll("iframe[data-pyd-embed]")) register(f);
