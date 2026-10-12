// Unit tests for the `demo:` link parser (themes/.../js/demolinks.js).
//
//   node --test tests/demonstrator/js/
//
// demolinks.js is a plain ES module with no imports and no DOM use, so it is
// loaded from its source text; this works on any Node version with
// `node:test` (18+), whatever the package type settings.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const SRC = new URL("../../../themes/owenpeckham/static/demonstrator/js/demolinks.js", import.meta.url);
const code = await readFile(SRC, "utf8");
const { parseDemoHref, findParam, coerceForParam, validateDemoAction, describeAction } = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);

const PARAMS = [
  { id: "FREQ", name: "FREQ", type: "float", default: 1.0 },
  { id: "STEPS", name: "STEPS", type: "int", default: 10 },
  { id: "SHOW_GRID", name: "SHOW_GRID", type: "bool", default: true },
  { id: "CMAP", name: "CMAP", type: "choice", choices: ["viridis", "magma"], default: "viridis" },
  { id: "LABEL", name: "LABEL", type: "str", default: "x" },
  { id: "SIZE", name: "SIZE", type: "tuple", length: 2, elem_type: "int", default: [3, 4] },
  { id: "physics.py:K", name: "K", type: "float", default: 2 },
  { id: "a.py:N", name: "N", type: "int", default: 1 },
  { id: "b.py:N", name: "N", type: "int", default: 2 },
];
const CTX = { params: PARAMS, presets: [{ name: "resonance" }], files: { "main.py": 80, "pkg/model.py": 12 } };

test("playback actions take no arguments", () => {
  for (const verb of ["run", "pause", "restart", "step", "stop", "reset"]) {
    assert.deepEqual(parseDemoHref(`demo:${verb}`), { type: verb });
    assert.equal(parseDemoHref(`demo:${verb}?x=1`).type, "invalid");
    assert.equal(parseDemoHref(`demo:${verb}=now`).type, "invalid");
  }
  assert.deepEqual(parseDemoHref("  DEMO:Run  "), { type: "run" }, "case and surrounding space are tolerated");
});

test("set parses NAME=value pairs and decodes them as data", () => {
  assert.deepEqual(parseDemoHref("demo:set?FREQ=2&STEPS=5"), { type: "set", values: { FREQ: "2", STEPS: "5" } });
  assert.deepEqual(parseDemoHref("demo:set?LABEL=hello+world%21"), { type: "set", values: { LABEL: "hello world!" } });
  assert.deepEqual(parseDemoHref("demo:set?physics.py:K=3"), { type: "set", values: { "physics.py:K": "3" } });
  assert.deepEqual(parseDemoHref("demo:set?FREQ=2&"), { type: "set", values: { FREQ: "2" } }, "trailing & is ignored");
  assert.deepEqual(parseDemoHref("demo:set?CMAP=a=b"), { type: "set", values: { CMAP: "a=b" } }, "first = splits");
});

test("set rejects malformed input", () => {
  for (const bad of [
    "demo:set", "demo:set?", "demo:set=FREQ", "demo:set?FREQ", "demo:set?=1", "demo:set?1X=2",
    "demo:set?FR EQ=1", "demo:set?FREQ=%E0%A4%A", "demo:set?__proto__x(=1", "demo:set?__proto__=1",
    "demo:set?constructor=1",
    `demo:set?FREQ=${"9".repeat(201)}`,
    "demo:set?" + Array.from({ length: 51 }, (_, i) => `P${i}=1`).join("&"),
  ]) {
    assert.equal(parseDemoHref(bad).type, "invalid", bad);
  }
});

test("preset, param and code", () => {
  assert.deepEqual(parseDemoHref("demo:preset=resonance"), { type: "preset", name: "resonance" });
  assert.deepEqual(parseDemoHref("demo:preset?name=resonance"), { type: "preset", name: "resonance" });
  assert.equal(parseDemoHref("demo:preset").type, "invalid");
  assert.equal(parseDemoHref("demo:preset=no spaces").type, "invalid");
  assert.deepEqual(parseDemoHref("demo:param=FREQ"), { type: "param", name: "FREQ" });
  assert.equal(parseDemoHref("demo:param?FREQ").type, "invalid");
  assert.deepEqual(parseDemoHref("demo:code=main.py"), { type: "code", file: "main.py", from: null, to: null });
  assert.deepEqual(parseDemoHref("demo:code=main.py:42"), { type: "code", file: "main.py", from: 42, to: 42 });
  assert.deepEqual(parseDemoHref("demo:code=pkg/model.py:3-9"), { type: "code", file: "pkg/model.py", from: 3, to: 9 });
  assert.deepEqual(parseDemoHref("demo:code=./pkg//model.py:9-3"), { type: "code", file: "pkg/model.py", from: 9, to: 9 }, "reversed range collapses");
  assert.equal(parseDemoHref("demo:code=main.py:0").type, "invalid");
});

test("code links cannot leave the project", () => {
  for (const bad of [
    "demo:code=../secret.py", "demo:code=a/../../b.py", "demo:code=/etc/passwd", "demo:code=C:\\x.py",
    "demo:code=a\\b.py", "demo:code=file:///etc/passwd", "demo:code=https://evil.example/x.py",
    "demo:code=%2e%2e/x.py", "demo:code=",
  ]) {
    assert.equal(parseDemoHref(bad).type, "invalid", bad);
  }
});

test("anything else is invalid, never evaluated", () => {
  for (const bad of [
    "demo:", "demo:frobnicate", "demo:eval?x=1", "demo:javascript:alert(1)", "javascript:alert(1)",
    "demo:set?FREQ=1&alert(1)//x=1", "demo:run()", "", null, undefined, 42, {},
  ]) {
    assert.equal(parseDemoHref(bad).type, "invalid", String(bad));
  }
});

test("findParam: ids, unambiguous bare names, case-insensitive fallback", () => {
  assert.equal(findParam(PARAMS, "FREQ").id, "FREQ");
  assert.equal(findParam(PARAMS, "K").id, "physics.py:K");
  assert.equal(findParam(PARAMS, "physics.py:K").id, "physics.py:K");
  assert.equal(findParam(PARAMS, "N"), null, "ambiguous bare name");
  assert.equal(findParam(PARAMS, "b.py:N").id, "b.py:N");
  assert.equal(findParam(PARAMS, "freq").id, "FREQ");
  assert.equal(findParam(PARAMS, "nope"), null);
  assert.equal(findParam(null, "FREQ"), null);
});

test("coerceForParam converts and validates by type", () => {
  const p = (id) => PARAMS.find((x) => x.id === id);
  assert.deepEqual(coerceForParam(p("FREQ"), " 2.5 "), { ok: true, value: 2.5 });
  assert.deepEqual(coerceForParam(p("FREQ"), 3), { ok: true, value: 3 });
  assert.equal(coerceForParam(p("FREQ"), "abc").ok, false);
  assert.equal(coerceForParam(p("FREQ"), "").ok, false);
  assert.equal(coerceForParam(p("FREQ"), "Infinity").ok, false);
  assert.deepEqual(coerceForParam(p("STEPS"), "4.6"), { ok: true, value: 5 });
  assert.deepEqual(coerceForParam(p("SHOW_GRID"), "off"), { ok: true, value: false });
  assert.deepEqual(coerceForParam(p("SHOW_GRID"), "YES"), { ok: true, value: true });
  assert.equal(coerceForParam(p("SHOW_GRID"), "maybe").ok, false);
  assert.deepEqual(coerceForParam(p("CMAP"), "magma"), { ok: true, value: "magma" });
  assert.equal(coerceForParam(p("CMAP"), "jet").ok, false);
  assert.deepEqual(coerceForParam(p("LABEL"), "<b>hi</b>"), { ok: true, value: "<b>hi</b>" }, "strings stay data");
  assert.deepEqual(coerceForParam(p("SIZE"), "(5, 6)"), { ok: true, value: [5, 6] });
  assert.deepEqual(coerceForParam(p("SIZE"), [7.4, 8.6]), { ok: true, value: [7, 9] });
  assert.equal(coerceForParam(p("SIZE"), "1,2,3").ok, false);
  assert.equal(coerceForParam({ id: "X", type: "weird" }, "1").ok, false);
});

test("validateDemoAction checks links against the demo", () => {
  const ok = validateDemoAction(parseDemoHref("demo:set?freq=2&K=0.5"), CTX);
  assert.deepEqual(ok, { ok: true, action: { type: "set", values: { FREQ: 2, "physics.py:K": 0.5 } } });
  assert.equal(validateDemoAction(parseDemoHref("demo:set?NOPE=1"), CTX).ok, false);
  assert.equal(validateDemoAction(parseDemoHref("demo:set?FREQ=abc"), CTX).ok, false);
  assert.equal(validateDemoAction(parseDemoHref("demo:set?N=1"), CTX).ok, false, "ambiguous name");
  assert.equal(validateDemoAction(parseDemoHref("demo:preset=resonance"), CTX).ok, true);
  assert.equal(validateDemoAction(parseDemoHref("demo:preset=missing"), CTX).ok, false);
  assert.deepEqual(validateDemoAction(parseDemoHref("demo:param=k"), CTX), { ok: true, action: { type: "param", name: "physics.py:K" } });
  assert.equal(validateDemoAction(parseDemoHref("demo:code=missing.py:1"), CTX).ok, false);
  assert.equal(validateDemoAction(parseDemoHref("demo:code=pkg/model.py:13"), CTX).ok, false, "past the end");
  assert.deepEqual(validateDemoAction(parseDemoHref("demo:code=pkg/model.py:10-99"), CTX).action.to, 12, "range clipped");
  assert.equal(validateDemoAction(parseDemoHref("demo:run"), CTX).ok, true);
  assert.equal(validateDemoAction(parseDemoHref("demo:bogus"), CTX).ok, false);
  assert.equal(validateDemoAction(null, CTX).ok, false);
});

test("describeAction gives readable text for tooltips", () => {
  assert.equal(describeAction({ type: "set", values: { FREQ: 2 } }), "Set FREQ = 2");
  assert.equal(describeAction({ type: "code", file: "main.py", from: 3, to: 9 }), "Show main.py, lines 3–9");
  assert.equal(describeAction({ type: "code", file: "main.py", from: 3, to: 3 }), "Show main.py, line 3");
  assert.equal(describeAction({ type: "code", file: "main.py", from: null, to: null }), "Show main.py");
  assert.equal(describeAction({ type: "preset", name: "x" }), 'Apply the "x" preset');
  assert.equal(describeAction({ type: "run" }), "Run the simulation");
});
