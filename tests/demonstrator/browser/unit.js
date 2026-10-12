/* Browser unit tests for the demonstrator's security-relevant pure code:
 * the HTML sanitiser (with the real DOMPurify), the Markdown pipeline,
 * demo: links, manifest validation, path and origin checks, and ZIP files.
 *
 * Open /_pyd_tests/browser/unit.html on the test server
 *   python tests/demonstrator/e2e_checklist.py --serve
 * or let the checklist run it (check "unit-browser").  Results are shown in
 * the page and exposed as window.__unitResults.
 */
import { LIBS } from "/theme/demonstrator/js/config.js";
import { coerceForParam, findParam, parseDemoHref, validateDemoAction } from "/theme/demonstrator/js/demolinks.js";
import { parseFrontMatter, renderMarkdown } from "/theme/demonstrator/js/explainer.js";
import { DemoError, globToRegExp, mergeOptions, normalisePresets, readPageOptions, scanImports, validateManifest } from "/theme/demonstrator/js/manifest.js";
import { sanitizeExplainerHtml } from "/theme/demonstrator/js/sanitize.js";
import { isAllowedOrigin, safeRelPath } from "/theme/demonstrator/js/util.js";
import { readZip, writeZip } from "/theme/demonstrator/js/zip.js";

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assertion failed");
}
function eq(a, b, msg) {
  const ja = JSON.stringify(a);
  const jb = JSON.stringify(b);
  if (ja !== jb) throw new Error(`${msg ? msg + ": " : ""}expected ${jb}, got ${ja}`);
}
function throws(fn, cls, msg) {
  try {
    fn();
  } catch (e) {
    if (cls && !(e instanceof cls)) throw new Error(`${msg || "throws"}: wrong error type ${e && e.name}`);
    return e;
  }
  throw new Error(`${msg || "expected an exception"}`);
}

const purify = (await import(LIBS.dompurify)).default;
const BASE = new URL("/demos/x/", location.href).href;
const sanitize = (html, opts = {}) => sanitizeExplainerHtml(purify, html, { baseUrl: BASE, ...opts });
const holder = (frag) => {
  const d = document.createElement("div");
  d.appendChild(frag);
  return d;
};
/** Every attribute of every element, for blanket checks. */
function allAttrs(root) {
  const out = [];
  for (const el of root.querySelectorAll("*")) for (const a of el.attributes) out.push([el.nodeName, a.name, a.value]);
  return out;
}
function assertInert(root, label) {
  for (const [tag, name, value] of allAttrs(root)) {
    assert(!/^on/i.test(name), `${label}: event handler ${tag}[${name}]`);
    assert(!/^\s*(javascript|vbscript|data):/i.test(value), `${label}: dangerous URL in ${tag}[${name}]=${value}`);
    assert(name !== "style", `${label}: style attribute on ${tag}`);
    assert(name !== "srcdoc", `${label}: srcdoc on ${tag}`);
  }
  for (const tag of ["script", "style", "iframe", "object", "embed", "form", "input", "button", "base", "meta", "link", "template", "noscript", "svg", "frame", "frameset"]) {
    assert(!root.querySelector(tag), `${label}: <${tag}> survived`);
  }
}

/* ---------------- sanitiser ---------------- */

const PAYLOADS = [
  `<script>top.__pwned='script'</script><p>ok</p>`,
  `<img src=x onerror="top.__pwned='img'">`,
  `<svg onload="top.__pwned='svg'"><circle r=5></circle></svg>`,
  `<iframe src="javascript:top.__pwned='iframe'"></iframe>`,
  `<iframe srcdoc="<script>top.__pwned='srcdoc'</script>"></iframe>`,
  `<object data="javascript:top.__pwned='object'"></object><embed src="javascript:top.__pwned='embed'">`,
  `<form action="javascript:top.__pwned='form'"><button formaction="javascript:x">b</button><input onfocus=x autofocus></form>`,
  `<details open ontoggle="top.__pwned='details'"><summary>s</summary>x</details>`,
  `<math><mtext><table><mglyph><style><img src=x onerror="top.__pwned='mxss'"></style></mglyph></table></mtext></math>`,
  `<noscript><p title="</noscript><img src=x onerror=top.__pwned='noscript'>"></p></noscript>`,
  `<style>body{display:none}</style><div style="position:fixed;inset:0">x</div>`,
  `<base href="https://evil.example/"><meta http-equiv="refresh" content="0;url=https://evil.example/"><link rel=stylesheet href="https://evil.example/x.css">`,
  `<template><img src=x onerror="top.__pwned='template'"></template>`,
  `<a href="javascript:top.__pwned='a'">a</a><a href="JaVaScRiPt:x">b</a><a href="&#106;avascript:x">c</a><a href=" javascript:x">d</a><a href="java&#x09;script:x">e</a>`,
  `<a href="data:text/html,<script>x</script>">data</a><a href="vbscript:msgbox(1)">vb</a>`,
  `<img src="x" srcset="javascript:x 1x"><video poster="javascript:x"></video><audio src="javascript:x"></audio>`,
  `<a href="#x" data-pyd-action="run" ping="https://evil.example/">ping</a>`,
  `<table background="javascript:x"><tr><td>t</td></tr></table>`,
  `<div id="pyd-root">clobber</div><img name="getElementById"><form name="x"></form>`,
];

test("sanitiser: hostile HTML ends up inert", () => {
  for (const html of PAYLOADS) {
    const { fragment } = sanitize(html);
    assertInert(holder(fragment), html.slice(0, 40));
  }
  assert(!window.__pwned, `payload ran: ${window.__pwned}`);
});

test("sanitiser: harmless markup is kept", () => {
  const root = holder(sanitize(`<p>Hi <kbd>Ctrl</kbd> <sub>2</sub> <abbr title="x">y</abbr></p><details><summary>s</summary>t</details><table><tr><td>1</td></tr></table>`).fragment);
  for (const tag of ["kbd", "sub", "abbr", "details", "summary", "table", "td"]) assert(root.querySelector(tag), `<${tag}> was removed`);
  eq(root.querySelector("abbr").getAttribute("title"), "x");
});

test("sanitiser: links (external get a new tab, demo:/#/mailto kept, relative made absolute)", () => {
  const root = holder(sanitize(`<a id=e href="https://example.org/x">e</a><a id=r href="../y/">r</a><a id=d href="demo:set?A=1&B=2">d</a><a id=f href="#sec">f</a><a id=m href="mailto:a@b.c">m</a><a id=s href="https://example.org/" target="_self">s</a>`).fragment);
  const a = (id) => root.querySelector(`#${id}`);
  eq(a("e").getAttribute("target"), "_blank");
  eq(a("e").getAttribute("rel"), "noopener noreferrer");
  eq(a("s").getAttribute("target"), "_blank", "external _self is overridden");
  eq(a("r").getAttribute("href"), new URL("../y/", BASE).href);
  assert(!a("r").hasAttribute("target"), "same-origin link opens in place");
  eq(a("d").getAttribute("href"), "demo:set?A=1&B=2");
  eq(a("f").getAttribute("href"), "#sec");
  eq(a("m").getAttribute("href"), "mailto:a@b.c");
});

test("sanitiser: allowlist link policy drops off-site links", () => {
  const r = sanitize(`<a id=e href="https://example.org/">e</a><a id=g href="https://ok.example/">g</a>`, { linkPolicy: "allowlist", allowedOrigins: ["https://ok.example"] });
  const root = holder(r.fragment);
  assert(!root.querySelector("#e").hasAttribute("href"), "off-site link kept");
  assert(root.querySelector("#g").hasAttribute("href"), "allowlisted link dropped");
  eq(r.blocked.map((b) => b.kind), ["link"]);
});

test("sanitiser: images only from this site or the allowlist", () => {
  const r = sanitize(`<img id=a src="fig.svg" alt="ok"><img src="https://evil.example/p.png" alt="pixel"><img src="https://cdn.ok.example/a.png" alt="cdn"><img src="data:image/png;base64,iVBORw0KGgo=">`, { allowedOrigins: ["https://cdn.ok.example"] });
  const root = holder(r.fragment);
  eq(root.querySelector("#a").getAttribute("src"), new URL("fig.svg", BASE).href);
  eq(root.querySelectorAll("img").length, 2, "external and data: images replaced");
  eq(root.querySelectorAll(".pyd-blocked").length, 2);
  assert(root.textContent.includes("[image not shown: pixel]"), "blocked image shows its alt text");
  eq(r.missingAlt, 1, "missing alt text is counted");
});

test("sanitiser: iframes need opt-in and the same origin, and are sandboxed", () => {
  eq(holder(sanitize(`<iframe src="/x.html"></iframe>`).fragment).querySelectorAll("iframe").length, 0, "no opt-in");
  const root = holder(sanitize(`<iframe src="/x.html"></iframe><iframe src="https://evil.example/"></iframe>`, { allowIframes: true }).fragment);
  const frames = root.querySelectorAll("iframe");
  eq(frames.length, 1, "only the same-origin iframe");
  eq(frames[0].getAttribute("sandbox"), "allow-scripts allow-same-origin");
});

/* ---------------- Markdown pipeline ---------------- */

test("markdown: javascript: links stay text, demo: links survive", async () => {
  const { fragment } = await renderMarkdown("[x](javascript:top.__pwned=1) [y](demo:set?FREQ=2&DAMPING=0.5) [z](vbscript:x) <https://example.org>", { baseUrl: BASE });
  const root = holder(fragment);
  assertInert(root, "md links");
  eq(root.querySelector('a[href^="demo:"]').getAttribute("href"), "demo:set?FREQ=2&DAMPING=0.5");
  assert(root.querySelector('a[href="https://example.org/"]'), "autolink kept");
});

test("markdown: raw HTML in the source is sanitised", async () => {
  const md = PAYLOADS.join("\n\n") + "\n\n| a | b |\n|---|---|\n| <img src=x onerror=top.__pwned=1> | <script>x</script> |\n\nfoot[^1]\n\n[^1]: <img src=x onerror=top.__pwned=2>";
  const { fragment } = await renderMarkdown(md, { baseUrl: BASE });
  assertInert(holder(fragment), "markdown");
  assert(!window.__pwned, `payload ran: ${window.__pwned}`);
});

test("markdown: code is shown as text, maths left for KaTeX", async () => {
  const { fragment, hasMath } = await renderMarkdown("```html\n<script>top.__pwned=1</script>\n```\n\nInline $x^2$ and `<b>code</b>`.", { baseUrl: BASE });
  const root = holder(fragment);
  assert(root.querySelector("pre code").textContent.includes("<script>"), "code text kept");
  assert(!root.querySelector("script"), "no script element");
  assert(hasMath && root.querySelector(".pyd-math"), "maths placeholder");
  eq(root.querySelector("p code").textContent, "<b>code</b>");
});

test("front matter: small YAML subset, no prototype pollution", () => {
  const { meta, body } = parseFrontMatter("---\ntitle: Hello <b>x</b>\ntour: auto\nn: 3\n__proto__: {polluted: 1}\nconstructor: x\ntags:\n  - a\n  - b\n---\n# Body\n");
  eq(meta.title, "Hello <b>x</b>");
  eq(meta.n, 3);
  eq(meta.tags, ["a", "b"]);
  assert(!("polluted" in {}), "Object.prototype polluted");
  assert(!Object.prototype.hasOwnProperty.call(meta, "constructor"), "constructor key kept");
  eq(body, "# Body\n");
});

/* ---------------- demo: links ---------------- */

test("demo: links parse as data and are checked against the demo", () => {
  eq(parseDemoHref("demo:set?FREQ=2"), { type: "set", values: { FREQ: "2" } });
  eq(parseDemoHref("demo:code=../x.py:1").type, "invalid");
  eq(parseDemoHref("demo:alert(1)").type, "invalid");
  const params = [{ id: "FREQ", name: "FREQ", type: "float" }];
  eq(validateDemoAction(parseDemoHref("demo:set?FREQ=abc"), { params }).ok, false);
  eq(findParam(params, "freq").id, "FREQ");
  eq(coerceForParam(params[0], "1e3"), { ok: true, value: 1000 });
});

/* ---------------- paths, origins, manifests ---------------- */

test("safeRelPath keeps paths inside the demo folder", () => {
  eq(safeRelPath("a/b.py"), "a/b.py");
  eq(safeRelPath("./a//b.py"), "a/b.py");
  for (const bad of ["../a.py", "a/../../b", "/etc/passwd", "C:\\x.py", "a\\b.py", "http://x/a.py", "file:///a", "~/x", "", " ", "a/ b /c", "x\u0000.py", "a".repeat(301)]) {
    eq(safeRelPath(bad), null, JSON.stringify(bad));
  }
});

test("isAllowedOrigin: same origin or allowlist, http(s) only", () => {
  assert(isAllowedOrigin("/x/main.py"), "relative");
  assert(isAllowedOrigin(location.origin + "/x"), "same origin");
  assert(!isAllowedOrigin("https://evil.example/x"), "other origin");
  assert(isAllowedOrigin("https://ok.example/x", ["https://ok.example"]), "allowlisted");
  assert(!isAllowedOrigin("javascript:alert(1)"), "javascript:");
  assert(!isAllowedOrigin("data:text/plain,x"), "data:");
  assert(!isAllowedOrigin("//evil.example/x"), "protocol-relative");
});

test("validateManifest rejects unsafe paths and odd values", () => {
  for (const raw of [{ main: "../x.py" }, { main: "/abs.py" }, { main: "x.txt" }, { files: ["../secret.py"] }, { files: ["/etc/passwd"] }, { files: "main.py" }, { bundle: "x.tar" }, { bundle: "../x.zip" }, [], null, "x"]) {
    throws(() => validateManifest(raw), DemoError, JSON.stringify(raw));
  }
  const { manifest, warnings } = validateManifest({
    title: 5, unknown: 1, packages: ["numpy", "evil; rm -rf /", "scipy>=1.0"], buttons: [{ call: "__import__('os').system('x')", label: "x" }, { call: "kick", label: "Kick" }],
    back: "javascript:alert(1)", fps: 120, main: "src/main.py", explainer: "../x.md",
  });
  eq(manifest.packages, ["numpy", "scipy>=1.0"]);
  eq(manifest.buttons.map((b) => b.call), ["kick"]);
  eq(manifest.main, "src/main.py");
  assert(warnings.some((w) => w.includes("unknown")), "unknown key warned");
  assert(warnings.some((w) => w.includes('"title"')), "title type warned");
  assert(warnings.some((w) => w.includes("explainer")), "unsafe explainer warned");
});

test("query options are validated and clamped", () => {
  const root = document.createElement("div");
  const page = readPageOptions(root, "?demo=/x/&panes=sim,bogus,code&fps=999&layout=evil&p.FREQ=2&p.__proto__=1&embed=1&theme=hack");
  eq(page.panes, ["sim", "code"]);
  eq(page.values.FREQ, "2");
  assert(!("polluted" in {}), "prototype polluted");
  const o = mergeOptions(page, {});
  eq(o.fps, 60);
  eq(o.layout, "auto");
  eq(o.theme, "site");
  eq(page.embed, true);
});

test("globs and import scanning", () => {
  assert(globToRegExp("**/*.py").test("a/b/c.py") && globToRegExp("**/*.py").test("c.py"), "**");
  assert(!globToRegExp("*.py").test("a/b.py"), "* stays in one folder");
  assert(globToRegExp("data/*.csv").test("data/x.csv") && !globToRegExp("data/*.csv").test("data/x.csvx"), "anchored");
  assert(!globToRegExp("a.b").test("axb"), "dots are literal");
  eq(scanImports("from .grid import x, y as z\nimport numpy as np, os\nfrom pkg.mod import (a,\n"), [
    { level: 1, module: "grid", names: ["x", "y"] },
    { level: 0, module: "numpy", names: [] },
    { level: 0, module: "os", names: [] },
    { level: 0, module: "pkg.mod", names: ["a"] },
  ]);
});

test("presets are normalised as data", () => {
  const p = normalisePresets({ ok: { label: "OK", values: { A: 1, B: "x", C: [1, 2], D: { evil: 1 } } }, "bad name!": { A: 1 }, flat: { A: 2 } });
  eq(p.map((x) => x.name), ["ok", "flat"]);
  eq(p[0].values, { A: 1, B: "x", C: [1, 2] });
  eq(p[1].values, { A: 2 });
});

test("zip: round trip, names kept as data", async () => {
  const files = [
    { path: "main.py", data: "print('hi')\n" },
    { path: "pkg/ünïcode.py", data: "X = 1\n" },
    { path: "../evil.py", data: "boom" },
    { path: "img.bin", data: new Uint8Array([0, 1, 2, 255]) },
  ];
  const entries = await readZip(await writeZip(files).arrayBuffer());
  eq(entries.map((e) => e.path), files.map((f) => f.path));
  eq(new TextDecoder().decode(entries[1].data), "X = 1\n");
  eq([...entries[3].data], [0, 1, 2, 255]);
  eq(safeRelPath(entries[2].path), null, "the loader drops ../ entries");
});

/* ---------------- runner ---------------- */
const list = document.getElementById("results");
let passed = 0;
const failures = [];
for (const t of tests) {
  const li = document.createElement("li");
  try {
    await t.fn();
    passed += 1;
    li.className = "pass";
    li.textContent = `PASS ${t.name}`;
  } catch (e) {
    failures.push({ name: t.name, error: String(e && e.message ? e.message : e) });
    li.className = "fail";
    li.textContent = `FAIL ${t.name}: ${e && e.message ? e.message : e}`;
  }
  list.appendChild(li);
}
const summary = document.getElementById("summary");
summary.textContent = `${passed} passed, ${failures.length} failed`;
summary.className = failures.length ? "fail" : "pass";
window.__unitResults = { passed, failed: failures.length, failures, total: tests.length };
