/* Python demonstrator -- `demo:` links in explainers.
 *
 * Markdown links whose target starts with `demo:` control the live demo:
 *
 *   [heavy damping](demo:set?DAMPING=0.9&FREQ=2)   set parameter values
 *   [resonance](demo:preset=resonance)             apply a demo.json preset
 *   [this loop](demo:code=main.py:42-58)           show + highlight lines
 *   [watch it](demo:run)  demo:pause  demo:restart  demo:step  demo:reset
 *   [parameter FREQ](demo:param=FREQ)              focus that control
 *
 * Links are data: parsing never evaluates anything, and values are checked
 * against the detected parameters before they are applied.  This module is
 * pure (no DOM), so it is unit-tested with `node --test`.
 */

const NAME_RE = /^[A-Za-z_][\w.:/-]{0,120}$/;
const PRESET_RE = /^[\w.-]{1,64}$/;
const PLAYBACK = new Set(["run", "pause", "restart", "step", "stop", "reset"]);
const MAX_VALUE_LEN = 200;
const RESERVED = new Set(["__proto__", "constructor", "prototype"]);

function invalid(reason) {
  return { type: "invalid", reason };
}

function safePath(p) {
  if (!p || /[\\\0]/.test(p) || p.startsWith("/") || /^[a-z][a-z0-9+.-]*:/i.test(p)) return null;
  const parts = [];
  for (const seg of p.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === "..") return null;
    parts.push(seg);
  }
  return parts.length ? parts.join("/") : null;
}

function decode(s) {
  try {
    return decodeURIComponent(s.replace(/\+/g, " "));
  } catch (e) {
    return null;
  }
}

/** Parse a `demo:` href into an action object (or {type: "invalid"}). */
export function parseDemoHref(href) {
  if (typeof href !== "string") return invalid("not a string");
  const s = href.trim();
  if (!/^demo:/i.test(s)) return invalid("not a demo: link");
  const body = s.slice(5);
  const m = /^([a-z]+)(?:([?=])([\s\S]*))?$/i.exec(body);
  if (!m) return invalid(`cannot parse "${s}"`);
  const verb = m[1].toLowerCase();
  const sep = m[2];
  const rest = m[3] || "";

  if (PLAYBACK.has(verb)) {
    if (sep) return invalid(`demo:${verb} takes no arguments`);
    return { type: verb };
  }
  if (verb === "set") {
    if (sep !== "?" || !rest) return invalid("demo:set needs ?NAME=value pairs");
    const values = {};
    let n = 0;
    for (const pair of rest.split("&")) {
      if (!pair) continue;
      const eq = pair.indexOf("=");
      if (eq <= 0) return invalid(`demo:set: "${pair}" is not NAME=value`);
      const name = decode(pair.slice(0, eq));
      const value = decode(pair.slice(eq + 1));
      if (name === null || value === null) return invalid("demo:set: bad %-encoding");
      if (!NAME_RE.test(name) || RESERVED.has(name)) return invalid(`demo:set: invalid parameter name "${name}"`);
      if (value.length > MAX_VALUE_LEN) return invalid(`demo:set: value for ${name} is too long`);
      values[name] = value;
      n += 1;
      if (n > 50) return invalid("demo:set: too many values");
    }
    if (!n) return invalid("demo:set needs at least one NAME=value");
    return { type: "set", values };
  }
  if (verb === "preset") {
    const name = decode(sep === "?" ? rest.replace(/^name=/, "") : rest);
    if (!sep || !name || !PRESET_RE.test(name)) return invalid("demo:preset=NAME needs a preset name");
    return { type: "preset", name };
  }
  if (verb === "param") {
    const name = decode(rest);
    if (sep !== "=" || !name || !NAME_RE.test(name) || RESERVED.has(name)) return invalid("demo:param=NAME needs a parameter name");
    return { type: "param", name };
  }
  if (verb === "code") {
    const spec = decode(rest);
    if (sep !== "=" || !spec) return invalid("demo:code=FILE:LINES needs a file");
    const cm = /^(.+?)(?::(\d{1,6})(?:-(\d{1,6}))?)?$/.exec(spec);
    if (!cm) return invalid(`demo:code: cannot parse "${spec}"`);
    const file = safePath(cm[1]);
    if (!file) return invalid(`demo:code: invalid file "${cm[1]}"`);
    const from = cm[2] ? parseInt(cm[2], 10) : null;
    let to = cm[3] ? parseInt(cm[3], 10) : from;
    if (from !== null && to < from) to = from;
    if (from === 0) return invalid("demo:code: lines start at 1");
    return { type: "code", file, from, to };
  }
  return invalid(`unknown demo: action "${verb}"`);
}

/** Find a parameter by id, or by bare name when that is unambiguous. */
export function findParam(params, name) {
  if (!Array.isArray(params)) return null;
  const byId = params.find((p) => p.id === name);
  if (byId) return byId;
  const byName = params.filter((p) => p.name === name);
  if (byName.length === 1) return byName[0];
  const ci = params.filter((p) => p.id.toLowerCase() === name.toLowerCase() || p.name.toLowerCase() === name.toLowerCase());
  return ci.length === 1 ? ci[0] : null;
}

/** Convert a value (string from a link/URL, or JSON value) to the param's type. */
export function coerceForParam(p, raw) {
  const t = p.type;
  const s = typeof raw === "string" ? raw.trim() : raw;
  if (t === "float" || t === "int") {
    const n = typeof s === "number" ? s : s === "" || s === null ? NaN : Number(s);
    if (!Number.isFinite(n)) return { ok: false, reason: `${p.id} needs a number` };
    return { ok: true, value: t === "int" ? Math.round(n) : n };
  }
  if (t === "bool") {
    if (typeof s === "boolean") return { ok: true, value: s };
    const v = String(s).toLowerCase();
    if (["1", "true", "yes", "on"].includes(v)) return { ok: true, value: true };
    if (["0", "false", "no", "off"].includes(v)) return { ok: true, value: false };
    return { ok: false, reason: `${p.id} needs true or false` };
  }
  if (t === "choice") {
    const hit = (p.choices || []).find((c) => c === s || String(c) === String(s));
    return hit !== undefined ? { ok: true, value: hit } : { ok: false, reason: `${p.id} must be one of ${(p.choices || []).join(", ")}` };
  }
  if (t === "str") {
    const v = String(s ?? "");
    return v.length <= 2000 ? { ok: true, value: v } : { ok: false, reason: `${p.id}: text too long` };
  }
  if (t === "tuple") {
    const parts = Array.isArray(s) ? s : String(s).replace(/[()[\]]/g, "").split(/[,\s]+/).filter(Boolean);
    const nums = parts.map(Number);
    if (!nums.length || nums.some((n) => !Number.isFinite(n))) return { ok: false, reason: `${p.id} needs numbers` };
    if (p.length && nums.length !== p.length) return { ok: false, reason: `${p.id} needs ${p.length} numbers` };
    return { ok: true, value: p.elem_type === "int" ? nums.map(Math.round) : nums };
  }
  return { ok: false, reason: `${p.id} has an unsupported type` };
}

/**
 * Check an action against what the demo actually has.
 * ctx = {params: [...], presets: [{name}], files: {path: lineCount}}
 * Returns {ok: true, action} (values coerced, names resolved to ids) or
 * {ok: false, reason}.
 */
export function validateDemoAction(action, ctx) {
  if (!action || action.type === "invalid") return { ok: false, reason: action ? action.reason : "no action" };
  switch (action.type) {
    case "set": {
      const values = {};
      for (const [name, raw] of Object.entries(action.values)) {
        const p = findParam(ctx.params, name);
        if (!p) return { ok: false, reason: `unknown parameter "${name}"` };
        const c = coerceForParam(p, raw);
        if (!c.ok) return { ok: false, reason: c.reason };
        values[p.id] = c.value;
      }
      return { ok: true, action: { type: "set", values } };
    }
    case "preset": {
      const preset = (ctx.presets || []).find((x) => x.name === action.name);
      return preset ? { ok: true, action } : { ok: false, reason: `unknown preset "${action.name}"` };
    }
    case "param": {
      const p = findParam(ctx.params, action.name);
      return p ? { ok: true, action: { type: "param", name: p.id } } : { ok: false, reason: `unknown parameter "${action.name}"` };
    }
    case "code": {
      const files = ctx.files || {};
      if (!(action.file in files)) return { ok: false, reason: `no file "${action.file}" in this demo` };
      const n = files[action.file];
      if (action.from !== null && n && action.from > n) return { ok: false, reason: `${action.file} has only ${n} lines` };
      const to = action.to !== null && n ? Math.min(action.to, n) : action.to;
      return { ok: true, action: { ...action, to } };
    }
    default:
      return { ok: true, action };
  }
}

/** Human-readable description for tooltips / aria-descriptions. */
export function describeAction(action) {
  switch (action.type) {
    case "set":
      return "Set " + Object.entries(action.values).map(([k, v]) => `${k} = ${v}`).join(", ");
    case "preset":
      return `Apply the "${action.name}" preset`;
    case "code":
      return action.from ? `Show ${action.file}, line${action.to && action.to !== action.from ? `s ${action.from}–${action.to}` : ` ${action.from}`}` : `Show ${action.file}`;
    case "param":
      return `Go to the ${action.name} control`;
    case "run":
      return "Run the simulation";
    case "pause":
      return "Pause the simulation";
    case "restart":
      return "Restart the simulation";
    case "step":
      return "Advance one frame";
    case "stop":
      return "Stop the simulation";
    case "reset":
      return "Reset all parameters to their defaults";
    default:
      return "Demo action";
  }
}
