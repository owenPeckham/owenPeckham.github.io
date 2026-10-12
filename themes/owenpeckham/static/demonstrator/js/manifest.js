/* Python demonstrator -- options, demo.json manifests and project files.
 *
 * Options come from (later wins):
 *   config.DEFAULTS < data-* attributes on the page root (Pelican page
 *   metadata) < demo.json < query parameters.
 *
 * A demo is either a folder with an optional demo.json
 *     ?demo=demos/heat_equation/
 * or a bare script plus extra files
 *     ?script=path/main.py&files=helper.py,data/x.csv&explainer=explainer.md
 *
 * All paths are treated as data: they must stay inside the demo folder, and
 * URLs must be same-origin (or on config.ALLOWED_ORIGINS).
 */
import { ALLOWED_ORIGINS, DEFAULTS } from "./config.js";
import { isAllowedOrigin, looksBinary, safeRelPath } from "./util.js";
import { readZip } from "./zip.js";

export const MANIFEST_NAME = "demo.json";
export const INDEX_NAME = "demo.index.json";
const MAX_FILE_BYTES = 20 * 1024 * 1024;
const MAX_TOTAL_BYTES = 80 * 1024 * 1024;
const PANES = ["explainer", "sim", "code"];
const LAYOUTS = ["auto", "split", "columns", "stacked", "tabs"];

/** Error with a user-facing message (shown in the page, not just the console). */
export class DemoError extends Error {
  constructor(message, detail) {
    super(message);
    this.name = "DemoError";
    this.detail = detail || "";
  }
}

const KNOWN_KEYS = new Set([
  "title", "description", "main", "explainer", "files", "bundle", "packages", "parameter_overrides",
  "buttons", "presets", "back", "layout", "panes", "fps", "quality", "editable", "autorun", "load",
  "timeout", "theme", "toc", "version", "micropip", "max_controls", "max_other", "summary", "author",
  "date", "tags", "image", "$schema", "notes", "tour",
]);

/* ---------- reading options ---------------------------------------------- */

function parseBool(v) {
  if (typeof v === "boolean") return v;
  if (v === undefined || v === null) return undefined;
  const s = String(v).trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(s)) return true;
  if (["0", "false", "no", "off"].includes(s)) return false;
  return undefined;
}

function parsePanes(v) {
  if (v === undefined || v === null || v === "") return undefined;
  const list = Array.isArray(v) ? v : String(v).split(/[,\s]+/);
  const out = list.map((s) => String(s).trim().toLowerCase()).map((s) => (s === "simulator" ? "sim" : s)).filter((s) => PANES.includes(s));
  return out.length ? [...new Set(out)] : undefined;
}

/** Options from the page element's data attributes and the query string. */
export function readPageOptions(root, search = location.search) {
  const q = new URLSearchParams(search);
  const d = root ? root.dataset : {};
  const pick = (key, dataKey = key) => (q.has(key) ? q.get(key) : d[dataKey] !== undefined && d[dataKey] !== "" ? d[dataKey] : undefined);
  const values = Object.create(null);
  for (const [k, v] of q.entries()) if (k.startsWith("p.") && k.length > 2) values[k.slice(2)] = v;
  return {
    demo: pick("demo"),
    script: pick("script"),
    files: pick("files"),
    explainer: pick("explainer"),
    panes: parsePanes(pick("panes")),
    layout: pick("layout"),
    embed: parseBool(pick("embed")) || false,
    load: pick("load"),
    autorun: parseBool(pick("autorun")),
    fps: pick("fps"),
    quality: pick("quality"),
    editable: parseBool(pick("editable")),
    theme: pick("theme"),
    toc: pick("toc"),
    back: pick("back", "backUrl"),
    backLabel: pick("back_label", "backLabel"),
    height: pick("height"),
    preset: q.get("preset") || undefined,
    values,
    title: d.title || undefined,
    siteRoot: d.siteRoot || "/",
    siteName: d.siteName || "",
    version: d.version || "",
    instance: q.get("instance") || "",
    debugJspi: q.get("jspi"),
  };
}

/** The validated, merged option set used by the app. */
export function mergeOptions(page, manifest) {
  const m = manifest || {};
  const num = (v, lo, hi, dflt) => {
    const n = Number(v);
    return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : dflt;
  };
  const pick = (k) => (page[k] !== undefined ? page[k] : m[k] !== undefined ? m[k] : DEFAULTS[k]);
  const layout = String(pick("layout") || "auto").toLowerCase();
  const panes = page.panes || parsePanes(m.panes) || DEFAULTS.panes;
  return {
    layout: LAYOUTS.includes(layout) ? layout : "auto",
    panes,
    load: ["visible", "click", "eager"].includes(String(pick("load"))) ? String(pick("load")) : "visible",
    autorun: parseBool(pick("autorun")) !== false,
    fps: num(pick("fps"), 1, 60, DEFAULTS.fps),
    quality: ["auto", "low", "medium", "high"].includes(String(pick("quality"))) ? String(pick("quality")) : "auto",
    editable: parseBool(pick("editable")) === true,
    timeout: num(pick("timeout"), 3, 600, DEFAULTS.timeout),
    theme: ["site", "auto", "light", "dark"].includes(String(pick("theme"))) ? String(pick("theme")) : "site",
    toc: String(pick("toc")),
    micropip: parseBool(pick("micropip")) !== false,
    maxControls: num(m.max_controls, 1, 100, DEFAULTS.max_controls),
    maxOther: num(m.max_other, 0, 100, DEFAULTS.max_other),
  };
}

/* ---------- manifest ---------------------------------------------------------- */

function addSlash(u) {
  return u.endsWith("/") ? u : u + "/";
}

async function fetchChecked(url, what, init = {}) {
  let res;
  try {
    res = await fetch(url, init);
  } catch (e) {
    throw new DemoError(`Could not download ${what}: network error.`, `${url}\n${e.message}`);
  }
  return res;
}

/**
 * Validate a parsed demo.json.  Returns {manifest, warnings}; throws
 * DemoError for problems that make the demo unusable.
 */
export function validateManifest(raw, source = MANIFEST_NAME) {
  const warnings = [];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new DemoError(`${source} must contain a JSON object ({ ... }).`);
  }
  const m = {};
  for (const k of Object.keys(raw)) {
    if (!KNOWN_KEYS.has(k)) warnings.push(`${source}: unknown key "${k}" ignored`);
  }
  const str = (k) => {
    if (raw[k] === undefined || raw[k] === null) return;
    if (typeof raw[k] !== "string") warnings.push(`${source}: "${k}" should be a string`);
    else m[k] = raw[k];
  };
  ["title", "description", "summary", "author", "date", "layout", "quality", "load", "theme", "version", "image"].forEach(str);
  if (raw.main !== undefined) {
    const p = safeRelPath(raw.main);
    if (!p || !/\.pyw?$/.test(p)) throw new DemoError(`${source}: "main" must be a relative path to a .py file inside the demo folder (got ${JSON.stringify(raw.main)}).`);
    m.main = p;
  }
  if (raw.explainer === false) m.explainer = false;
  else if (raw.explainer !== undefined) {
    const p = safeRelPath(raw.explainer);
    if (!p) warnings.push(`${source}: "explainer" must be a relative path inside the demo folder`);
    else m.explainer = p;
  }
  if (raw.bundle !== undefined) {
    const p = safeRelPath(raw.bundle);
    if (!p || !/\.zip$/i.test(p)) throw new DemoError(`${source}: "bundle" must be a relative path to a .zip file.`);
    m.bundle = p;
  }
  if (raw.files !== undefined) {
    if (!Array.isArray(raw.files)) throw new DemoError(`${source}: "files" must be a list of paths or globs.`);
    m.files = [];
    for (const f of raw.files) {
      const p = safeRelPath(f);
      if (!p) throw new DemoError(`${source}: unsafe or invalid path in "files": ${JSON.stringify(f)} (paths must stay inside the demo folder).`);
      m.files.push(p);
    }
  }
  if (raw.packages !== undefined) {
    if (!Array.isArray(raw.packages) || raw.packages.some((p) => typeof p !== "string")) warnings.push(`${source}: "packages" must be a list of names`);
    else m.packages = raw.packages.filter((p) => /^[A-Za-z0-9._\-\[\],<>=!~ ]{1,80}$/.test(p));
  }
  for (const k of ["parameter_overrides", "presets"]) {
    if (raw[k] === undefined) continue;
    if (!raw[k] || typeof raw[k] !== "object" || Array.isArray(raw[k])) warnings.push(`${source}: "${k}" must be an object`);
    else m[k] = raw[k];
  }
  if (raw.buttons !== undefined) {
    if (!Array.isArray(raw.buttons)) warnings.push(`${source}: "buttons" must be a list`);
    else m.buttons = raw.buttons.filter((b) => b && typeof b === "object" && typeof b.call === "string" && /^[A-Za-z_]\w*$/.test(b.call));
  }
  if (raw.back !== undefined) {
    if (typeof raw.back === "string") m.back = { url: raw.back };
    else if (raw.back && typeof raw.back === "object" && typeof raw.back.url === "string") m.back = { url: raw.back.url, label: typeof raw.back.label === "string" ? raw.back.label : undefined };
    else warnings.push(`${source}: "back" must be a URL or {"label", "url"}`);
  }
  for (const k of ["fps", "timeout", "max_controls", "max_other"]) if (typeof raw[k] === "number") m[k] = raw[k];
  for (const k of ["editable", "autorun", "micropip"]) if (typeof raw[k] === "boolean") m[k] = raw[k];
  if (raw.toc !== undefined) m.toc = String(raw.toc);
  if (raw.panes !== undefined) m.panes = parsePanes(raw.panes);
  if (raw.tags !== undefined && Array.isArray(raw.tags)) m.tags = raw.tags.map(String);
  if (raw.tour !== undefined) m.tour = raw.tour;
  return { manifest: m, warnings };
}

/**
 * Work out where the demo lives and read its manifest and file index.
 * Returns {id, baseUrl, manifest, main, explicitFiles, index, explainer, warnings}.
 */
export async function resolveDemo(page) {
  const siteRoot = new URL(page.siteRoot || "/", location.href).href;
  const warnings = [];
  let baseUrl;
  let main;
  let manifest = {};
  let explicitFiles = null;
  if (page.demo) {
    if (/^[a-z][a-z0-9+.-]*:/i.test(page.demo) && !/^https?:/i.test(page.demo)) throw new DemoError("The demo address must be an http(s) URL or a path.");
    baseUrl = addSlash(new URL(page.demo, siteRoot).href);
  } else if (page.script) {
    if (/^[a-z][a-z0-9+.-]*:/i.test(page.script) && !/^https?:/i.test(page.script)) throw new DemoError("The script address must be an http(s) URL or a path.");
    const scriptUrl = new URL(page.script, siteRoot);
    if (!/\.pyw?$/.test(scriptUrl.pathname)) throw new DemoError(`"script" must point to a .py file (got ${page.script}).`);
    baseUrl = new URL("./", scriptUrl).href;
    main = decodeURIComponent(scriptUrl.pathname.split("/").pop());
  } else {
    throw new DemoError("No demo selected. Add ?demo=path/to/demo/ (a folder with main.py and an optional demo.json) to the address.");
  }
  if (!isAllowedOrigin(baseUrl, ALLOWED_ORIGINS)) {
    throw new DemoError("Demos can only be loaded from this site (or an origin listed in config.ALLOWED_ORIGINS).", baseUrl);
  }
  const id = new URL(baseUrl).pathname.replace(/^\/|\/$/g, "") + (page.script && !page.demo ? "/" + main : "");

  // The build-time index (if present) says whether demo.json exists, which
  // avoids a noisy 404 for demos without a manifest.
  const ires = await fetchChecked(new URL(INDEX_NAME, baseUrl).href, "file index", { cache: "no-cache" }).catch(() => null);
  let indexData = null;
  if (ires && ires.ok) {
    try {
      indexData = await ires.json();
    } catch (e) {
      indexData = null;
    }
  }
  const listed = indexData && Array.isArray(indexData.files) ? new Set(indexData.files.map((f) => f.path)) : null;
  const mres = page.demo && (!listed || listed.has(MANIFEST_NAME)) ? await fetchChecked(new URL(MANIFEST_NAME, baseUrl).href, MANIFEST_NAME, { cache: "no-cache" }) : null;
  if (mres) {
    if (mres.ok) {
      let raw;
      const text = await mres.text();
      try {
        raw = JSON.parse(text);
      } catch (e) {
        throw new DemoError(`${MANIFEST_NAME} is not valid JSON: ${e.message}`, new URL(MANIFEST_NAME, baseUrl).href);
      }
      const v = validateManifest(raw);
      manifest = v.manifest;
      warnings.push(...v.warnings);
    } else if (mres.status !== 404) {
      throw new DemoError(`Could not read ${MANIFEST_NAME} (HTTP ${mres.status}).`, new URL(MANIFEST_NAME, baseUrl).href);
    }
  }
  const index = listed ? indexData : null;
  main = main || manifest.main || (index && index.main) || "main.py";
  if (page.files) {
    explicitFiles = [];
    for (const f of String(page.files).split(",")) {
      if (!f.trim()) continue;
      const p = safeRelPath(f.trim());
      if (!p) throw new DemoError(`Unsafe or invalid path in "files": ${JSON.stringify(f)}.`);
      explicitFiles.push(p);
    }
  }
  let explainer = manifest.explainer;
  if (page.explainer !== undefined) {
    if (["0", "false", "none", "off"].includes(String(page.explainer).toLowerCase())) explainer = false;
    else {
      const p = safeRelPath(page.explainer);
      if (!p) warnings.push(`ignored unsafe explainer path ${JSON.stringify(page.explainer)}`);
      else explainer = p;
    }
  }
  return { id, baseUrl, manifest, main, explicitFiles, index, explainer, warnings };
}

/** Candidate explainer URLs in order (empty when explicitly disabled). */
export function explainerCandidates(demo) {
  if (demo.explainer === false) return [];
  if (demo.explainer) return [new URL(demo.explainer, demo.baseUrl).href];
  const names = ["explainer.md", "README.md"];
  if (demo.index) {
    const have = new Set(demo.index.files.map((f) => f.path));
    return names.filter((n) => have.has(n)).map((n) => new URL(n, demo.baseUrl).href);
  }
  return names.map((n) => new URL(n, demo.baseUrl).href);
}

/* ---------- project files --------------------------------------------------------- */

export function globToRegExp(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*") {
      if (glob[i + 1] === "*") {
        const slash = glob[i + 2] === "/";
        re += slash ? "(?:.*/)?" : ".*";
        i += slash ? 2 : 1;
      } else re += "[^/]*";
    } else if (c === "?") re += "[^/]";
    else if ("\\^$.|+(){}[]".includes(c)) re += "\\" + c;
    else re += c;
  }
  return new RegExp("^" + re + "$");
}

const isGlob = (p) => /[*?]/.test(p);

const NOT_PROJECT = new Set([MANIFEST_NAME, INDEX_NAME, "index.html", "explainer.md", "README.md"]);

const KNOWN_MODULES = new Set(
  ("sys os math time json re random itertools functools collections dataclasses typing argparse pathlib csv abc enum copy " +
    "string textwrap io warnings logging datetime statistics fractions decimal heapq bisect operator contextlib threading " +
    "asyncio struct array glob shutil tempfile zipfile base64 hashlib uuid pickle pprint inspect ast types unittest cmath " +
    "numbers queue secrets signal socket urllib http xml html email sqlite3 traceback weakref gc platform locale colorsys " +
    "numpy matplotlib mpl_toolkits scipy pandas sympy networkx PIL skimage sklearn seaborn cv2 yaml requests tqdm " +
    "demo_params __future__ builtins dis tokenize keyword symtable importlib pkgutil codecs unicodedata difflib timeit").split(/\s+/)
);

/** Very small import scanner used when there is neither a file list nor an index. */
export function scanImports(source) {
  const out = [];
  const re = /^[ \t]*(?:from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+\(?([\w*, \t]+)|import[ \t]+([\w., \t]+))/gm;
  let m;
  while ((m = re.exec(source))) {
    if (m[4] !== undefined) {
      for (const part of m[4].split(",")) {
        const name = part.trim().split(/\s+as\s+/)[0].trim();
        if (name) out.push({ level: 0, module: name, names: [] });
      }
    } else {
      const names = m[3].split(",").map((s) => s.trim().split(/\s+as\s+/)[0].trim()).filter(Boolean);
      out.push({ level: m[1].length, module: m[2], names });
    }
  }
  return out;
}

const DATA_LITERAL = /["']((?:[\w.-]+\/)*[\w.-]+\.(?:csv|tsv|txt|json|npy|npz|dat|ya?ml|png|jpe?g|gif|wav|h5|xlsx?|toml))["']/g;

async function inferFiles(demo, fetchOne) {
  const found = new Map();
  const queue = [demo.main];
  const tried = new Set();
  const tryPath = async (p) => {
    if (tried.has(p)) return found.has(p);
    tried.add(p);
    const f = await fetchOne(p, true);
    if (f) {
      found.set(p, f);
      if (p.endsWith(".py")) queue.push(p);
      return true;
    }
    return false;
  };
  const mainFile = await fetchOne(demo.main, false);
  found.set(demo.main, mainFile);
  tried.add(demo.main);
  while (queue.length) {
    const path = queue.shift();
    const f = found.get(path);
    if (!f || f.binary) continue;
    const dir = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    const pkg = path.endsWith("__init__.py") ? dir : dir;
    for (const imp of scanImports(f.text)) {
      let modPath;
      if (imp.level > 0) {
        let base = pkg.split("/").filter(Boolean);
        base = base.slice(0, base.length - (imp.level - 1));
        modPath = [...base, ...imp.module.split(".").filter(Boolean)].join("/");
      } else {
        if (KNOWN_MODULES.has(imp.module.split(".")[0])) continue;
        modPath = imp.module.split(".").join("/");
      }
      const parts = modPath.split("/").filter(Boolean);
      // parent packages
      for (let i = 1; i < parts.length; i++) await tryPath(parts.slice(0, i).join("/") + "/__init__.py");
      if (parts.length) {
        if (!(await tryPath(modPath + ".py"))) await tryPath(modPath + "/__init__.py");
      }
      for (const n of imp.names) {
        if (n === "*") continue;
        const sub = (modPath ? modPath + "/" : "") + n;
        if (found.has(modPath + "/__init__.py") || imp.level > 0) await tryPath(sub + ".py");
      }
    }
    DATA_LITERAL.lastIndex = 0;
    let m;
    while ((m = DATA_LITERAL.exec(f.text))) {
      const p = safeRelPath(m[1]);
      if (p) await tryPath(p);
    }
  }
  return [...found.values()].filter(Boolean);
}

function decodeFile(path, bytes) {
  if (looksBinary(bytes)) return { text: null, binary: true };
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return { text: text.replace(/^﻿/, ""), binary: false };
  } catch (e) {
    return { text: null, binary: true };
  }
}

/**
 * Fetch every project file.  Returns [{path, url, bytes, text, binary, size}]
 * with the main file first.
 */
export async function fetchProjectFiles(demo, onProgress) {
  const { baseUrl, manifest, index } = demo;
  const hashes = new Map((index ? index.files : []).map((f) => [f.path, f.sha1]));
  const version = manifest.version;
  let total = 0;

  const fileUrl = (path) => {
    const u = new URL(path.split("/").map(encodeURIComponent).join("/"), baseUrl);
    const h = hashes.get(path);
    if (h) u.searchParams.set("v", h);
    else if (version) u.searchParams.set("v", version);
    return u.href;
  };
  const fetchOne = async (path, optional) => {
    const url = fileUrl(path);
    const cache = hashes.has(path) || version ? "default" : "no-cache";
    const res = await fetchChecked(url, path, { cache });
    if (!res.ok) {
      if (optional) return null;
      throw new DemoError(res.status === 404 ? `Project file not found: ${path}` : `Could not download ${path} (HTTP ${res.status}).`, url);
    }
    const buf = new Uint8Array(await res.arrayBuffer());
    if (buf.length > MAX_FILE_BYTES) throw new DemoError(`${path} is too large for the browser demonstrator (${(buf.length / 1e6).toFixed(1)} MB).`);
    total += buf.length;
    if (total > MAX_TOTAL_BYTES) throw new DemoError("The project is too large to load in the browser (over 80 MB).");
    if (onProgress) onProgress(path);
    return { path, url: new URL(path.split("/").map(encodeURIComponent).join("/"), baseUrl).href, bytes: buf, size: buf.length, ...decodeFile(path, buf) };
  };

  let files;
  if (manifest.bundle) {
    const url = fileUrl(manifest.bundle);
    const res = await fetchChecked(url, manifest.bundle, { cache: hashes.has(manifest.bundle) || version ? "default" : "no-cache" });
    if (!res.ok) throw new DemoError(`Project bundle not found: ${manifest.bundle} (HTTP ${res.status}).`, url);
    let entries;
    try {
      entries = await readZip(await res.arrayBuffer());
    } catch (e) {
      throw new DemoError(`${manifest.bundle} could not be unpacked: ${e.message}`, url);
    }
    // skip archive junk, then unwrap a single top-level folder
    const junk = (p) =>
      p.startsWith("__MACOSX/") || /(^|\/)__pycache__\//.test(p) || /(^|\/)(Thumbs\.db|desktop\.ini)$/i.test(p) ||
      p.split("/").some((seg) => seg.startsWith(".") && seg !== "." && seg !== ".."); // dotfiles, .git/ ...
    const real = entries.filter((e) => !junk(e.path));
    const tops = new Set(real.map((e) => e.path.split("/")[0]));
    const strip = tops.size === 1 && real.every((e) => e.path.includes("/")) ? [...tops][0] + "/" : "";
    files = [];
    for (const e of real) {
      const p = safeRelPath(e.path.slice(strip.length));
      if (!p) {
        demo.warnings.push(`${manifest.bundle}: skipped unsafe entry ${JSON.stringify(e.path)}`);
        continue;
      }
      files.push({ path: p, url: url + "#" + p, bytes: e.data, size: e.data.length, ...decodeFile(p, e.data) });
    }
    if (!files.some((f) => f.path === demo.main)) throw new DemoError(`The bundle ${manifest.bundle} has no ${demo.main}.`);
  } else {
    const listed = demo.explicitFiles || manifest.files || null;
    let paths = [];
    if (listed) {
      const globs = listed.filter(isGlob);
      paths = listed.filter((p) => !isGlob(p));
      if (globs.length) {
        if (!index) {
          throw new DemoError(
            `"files" uses wildcards (${globs.join(", ")}), which need the file index ${INDEX_NAME}. Rebuild the site with the demonstrator plugin enabled, or list the files explicitly.`
          );
        }
        for (const g of globs) {
          const re = globToRegExp(g);
          const hits = index.files.map((f) => f.path).filter((p) => re.test(p));
          if (!hits.length) demo.warnings.push(`files: "${g}" matched nothing`);
          paths.push(...hits);
        }
      }
    } else if (index) {
      paths = index.files.map((f) => f.path).filter((p) => !NOT_PROJECT.has(p) && !p.endsWith(".zip"));
    }
    if (listed || index) {
      paths = [demo.main, ...paths.filter((p) => p !== demo.main)];
      paths = [...new Set(paths)];
      files = await Promise.all(paths.map((p) => fetchOne(p, false)));
    } else {
      files = await inferFiles(demo, fetchOne);
    }
  }
  files.sort((a, b) => (a.path === demo.main ? -1 : b.path === demo.main ? 1 : a.path.localeCompare(b.path)));
  if (!files.length || files[0].path !== demo.main) throw new DemoError(`Main file ${demo.main} not found.`);
  if (files[0].binary) throw new DemoError(`${demo.main} is not a UTF-8 text file.`);
  return files;
}

/** Presets from demo.json: {name: {values}} or {name: {label, values}}. */
export function normalisePresets(raw) {
  const out = [];
  if (!raw || typeof raw !== "object") return out;
  for (const [name, spec] of Object.entries(raw)) {
    if (!/^[\w.-]{1,64}$/.test(name) || !spec || typeof spec !== "object") continue;
    const values = spec.values && typeof spec.values === "object" ? spec.values : spec;
    const clean = {};
    for (const [k, v] of Object.entries(values)) {
      if (k === "label" || k === "description") continue;
      if (["number", "string", "boolean"].includes(typeof v) || Array.isArray(v)) clean[k] = v;
    }
    out.push({ name, label: typeof spec.label === "string" ? spec.label : name.replace(/[_-]+/g, " "), description: typeof spec.description === "string" ? spec.description : "", values: clean });
  }
  return out;
}
