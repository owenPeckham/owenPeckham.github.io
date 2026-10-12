/* Python demonstrator -- configuration constants.
 *
 * Everything that pins an external version or a URL lives here.
 *
 * Bumping Pyodide: change PYODIDE_VERSION, then run the test checklist in
 * DEMONSTRATOR.md (at least `node tests/demonstrator/run_pyodide_tests.mjs`
 * with the new version and the browser tests).  The matplotlib version
 * changes with Pyodide, so check the animation bridge tests.
 *
 * Self-hosting: download the Pyodide "full" distribution (or just the core
 * files plus the wheels you need) to e.g. /pyodide/v0.29.5/full/ and set
 * PYODIDE_BASE to that URL.  Same for CDN_BASE and the libraries below.
 */

export const PYODIDE_VERSION = "0.29.5";

/** Base URL of the CDN; change this one constant to self-host everything. */
export const CDN_BASE = "https://cdn.jsdelivr.net";

/** Where pyodide.js, the lock file and the package wheels live. */
export const PYODIDE_BASE = `${CDN_BASE}/pyodide/v${PYODIDE_VERSION}/full/`;

/** Front-end libraries, all pinned.  Scripts and stylesheets loaded with a
 * <script>/<link> tag carry Subresource Integrity hashes. */
export const LIBS = {
  markdownIt: {
    url: `${CDN_BASE}/npm/markdown-it@14.3.2/dist/markdown-it.min.js`,
    integrity: "sha384-hxAvKEbHozQqowCqu4B3mOihRnvATKzN8DX89pnQPo00/TFaxvQpmBgbE77Zjv61",
  },
  footnote: {
    url: `${CDN_BASE}/npm/markdown-it-footnote@4.0.0/dist/markdown-it-footnote.min.js`,
    integrity: "sha384-7mE/Iobn0ulfWG/11wQHPJLIV11r3mVxTdSnuNIL71f/DM1wFjtMREh+nxliUVdY",
  },
  dompurify: `${CDN_BASE}/npm/dompurify@3.4.16/dist/purify.es.mjs`,
  // Same KaTeX version as the PCLS demonstrator page, so the browser cache is shared.
  katex: `${CDN_BASE}/npm/katex@0.16.11/dist/katex.mjs`,
  katexCss: {
    url: `${CDN_BASE}/npm/katex@0.16.11/dist/katex.min.css`,
    integrity: "sha384-nB0miv6/jRmo5UMMR1wu3Gz6NLsoTkbqJghGIsx//Rlm+ZU03BU6SQNC66uf4l5+",
  },
  hljsCore: `${CDN_BASE}/npm/@highlightjs/cdn-assets@11.12.0/es/core.min.js`,
  hljsLanguage: (name) => `${CDN_BASE}/npm/@highlightjs/cdn-assets@11.12.0/es/languages/${name}.min.js`,
};

/** Languages registered with highlight.js (by file extension). */
export const HIGHLIGHT_LANGUAGES = {
  py: "python", pyw: "python", json: "json", md: "markdown", txt: "plaintext", csv: "plaintext",
  tsv: "plaintext", yaml: "yaml", yml: "yaml", toml: "ini", ini: "ini", cfg: "ini", sh: "bash",
  js: "javascript", html: "xml", xml: "xml", css: "css",
};

/** Origins (besides this site) that demo files, explainer images and links
 * may be loaded from, e.g. ["https://raw.githubusercontent.com"]. */
export const ALLOWED_ORIGINS = [];

/** Origins allowed to embed demos in an <iframe> and talk to them
 * (theme, resize).  This site's own origin is always allowed. */
export const EMBED_PARENT_ORIGINS = [];

/** Defaults for demo.json display options (query parameters override). */
export const DEFAULTS = {
  layout: "auto",          // auto | split | columns | stacked | tabs
  panes: ["explainer", "sim", "code"],
  load: "visible",         // visible: load Python when the demo nears the viewport; click: wait for Run
  autorun: true,           // run the script as soon as Python is ready
  fps: 30,                 // frame-rate cap for animations
  quality: "auto",         // auto | low | medium | high (render resolution vs device pixels)
  editable: false,         // allow editing the code and re-running
  timeout: 20,             // seconds before warning that a script is blocking
  micropip: true,          // try PyPI for imports Pyodide doesn't ship
  theme: "site",           // site | auto | light | dark
  toc: "auto",             // explainer table of contents: auto (3+ headings) | true | false
  max_controls: 14,
  max_other: 8,
};

/** Width (px) below which the panes become tabs. */
export const NARROW_BREAKPOINT = 860;

/** Pixel budget for one rendered figure (keeps big screens fast). */
export const MAX_FRAME_PIXELS = 1600000;

/** Root of the demonstrator's own files, derived from this module's URL. */
export const ASSET_BASE = new URL("../", import.meta.url).href;
export const WORKER_URL = new URL("./worker.js", import.meta.url).href;
export const RUNTIME_PY_BASE = new URL("../py/", import.meta.url).href;
