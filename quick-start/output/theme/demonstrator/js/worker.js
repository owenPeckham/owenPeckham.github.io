/* Python demonstrator -- Web Worker hosting Pyodide.
 *
 * Runs Python off the main thread so the page never freezes, and so "Stop"
 * can always terminate a runaway script.  It is a *classic* worker (loaded
 * with importScripts) for the widest browser support.  The Python half of
 * the runtime lives in ../py/ (pyd_runtime.py, pyd_backend.py, pyd_detect.py,
 * demo_params.py); this file only moves messages and bytes.
 *
 * MESSAGE PROTOCOL
 * ----------------
 * Every page -> worker message is {type, id?, ...}.  Messages with an id get
 * exactly one {type: "reply", id, ok, result | error} back.
 *
 *   page -> worker
 *   init        {pyodideBase, runtimeBase, version, root, noJspi}  load Pyodide + runtime -> {python, pyodide, jspi}
 *   load        {files: [{path, data}]}                     write project files     -> [paths]
 *   detect      {main, overrides, options}                  static parameter scan   -> detection result
 *   install     {packages, micropip}                        packages for imports    -> {missing: {name: hint}}
 *   run         {config}                                    run main as __main__    -> {outcome, figures, timers, next_due_in}
 *   tick        {dt, force, params?, snap?}                 advance animations      -> {fired, sent, running, next_due_in, error}
 *   set-params  {values}                                    live parameter writes   -> {applied, errors}
 *   pause       {paused} / step {}                          for plt.pause()-style loops
 *   stop        {}                                          stop timers / unwind a suspended script -> {graceful}
 *   speed       {speed} / fps {fps}
 *   viewport    {width, height, dpr, quality}               re-fit figure dpi
 *   visible     {ids}                                       which figures are on screen
 *   pointer     {fig, kind, x, y, button, ...}              forwarded mouse events (widgets)
 *   button      {call, file}                                manifest button -> script function
 *   theme       {old, new}                                  matplotlib colours for a new theme
 *   frame-ack   {n}                                         backpressure for streamed frames
 *
 *   worker -> page (unsolicited)
 *   status   {stage, message, progress?}    loading stages and run state
 *   console  {stream: stdout|stderr|note, text}
 *   figures  {figures: [{id, title, width_in, height_in, animated, closed}]}
 *   frame    {fig, width, height, n, buffer}  RGBA pixels (ArrayBuffer, transferred)
 *   error    {kind, exc_type, message, traceback, frames: [{file, line, name}], hint}
 *   fatal    {message}                      the worker cannot continue
 */
"use strict";

let pyodide = null;
let rt = null;            // pyd_runtime.RT (PyProxy)
let jspi = false;
let runActive = null;     // promise of the current script run
let consoleBuf = [];
let consoleTimer = 0;
let lastConsoleFlush = 0;
const CONSOLE_FLUSH_MS = 50;
const CONSOLE_MAX = 64 * 1024;

function post(msg, transfer) {
  self.postMessage(msg, transfer || []);
}

function status(stage, message, extra) {
  post(Object.assign({ type: "status", stage, message }, extra || {}));
}

/* ---- console capture (throttled, works during long synchronous runs) ---- */
function writeConsole(stream, text) {
  consoleBuf.push([stream, text]);
  const now = performance.now();
  if (now - lastConsoleFlush > CONSOLE_FLUSH_MS) flushConsole();
  else if (!consoleTimer) consoleTimer = setTimeout(flushConsole, CONSOLE_FLUSH_MS);
}

function flushConsole() {
  clearTimeout(consoleTimer);
  consoleTimer = 0;
  lastConsoleFlush = performance.now();
  if (!consoleBuf.length) return;
  // merge consecutive chunks of the same stream
  const out = [];
  for (const [stream, text] of consoleBuf) {
    const last = out[out.length - 1];
    if (last && last.stream === stream) last.text += text;
    else out.push({ stream, text });
  }
  consoleBuf = [];
  for (const m of out) {
    let text = m.text;
    if (text.length > CONSOLE_MAX) {
      text = text.slice(0, CONSOLE_MAX) + `\n… ${text.length - CONSOLE_MAX} more characters not shown\n`;
    }
    post({ type: "console", stream: m.stream, text });
  }
}

/* ---- the host object handed to pyd_runtime ------------------------------ */
const host = {
  post(text) {
    let msg;
    try {
      msg = JSON.parse(text);
    } catch (e) {
      return;
    }
    if (msg.type === "console") {
      writeConsole(msg.stream || "note", msg.text || "");
      return;
    }
    if (msg.type === "error") flushConsole();
    post(msg);
  },
  frame(figId, width, height, n, buf) {
    let pb = null;
    try {
      pb = buf.getBuffer("u8");
      const pixels = new Uint8ClampedArray(pb.data); // copy out of WASM memory
      post({ type: "frame", fig: figId, width, height, n, buffer: pixels.buffer }, [pixels.buffer]);
    } finally {
      if (pb) pb.release();
      if (buf && buf.destroy) buf.destroy();
    }
  },
  now() {
    return performance.now();
  },
  sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)));
  },
};

/* ---- start-up ------------------------------------------------------------- */
async function init(msg) {
  if (typeof WebAssembly !== "object") {
    throw new Error("This browser does not support WebAssembly, which Python in the browser needs.");
  }
  status("pyodide", "Downloading Python (Pyodide)…", { progress: 0.05 });
  try {
    importScripts(msg.pyodideBase + "pyodide.js");
  } catch (e) {
    throw new Error(`Could not load Pyodide from ${msg.pyodideBase} (${e.message}). Check your connection or the CDN setting.`);
  }
  pyodide = await self.loadPyodide({
    indexURL: msg.pyodideBase,
    stdout: () => {},
    stderr: () => {},
  });
  pyodide.setStdout({ batched: (s) => writeConsole("stdout", s + "\n") });
  pyodide.setStderr({ batched: (s) => writeConsole("stderr", s + "\n") });
  status("runtime", "Starting the demonstrator runtime…", { progress: 0.3 });

  const dir = "/home/pyodide/.pyd";
  pyodide.FS.mkdirTree(dir);
  const names = ["pyd_detect.py", "pyd_backend.py", "pyd_runtime.py", "demo_params.py"];
  const v = msg.version ? `?v=${encodeURIComponent(msg.version)}` : "";
  await Promise.all(
    names.map(async (name) => {
      const res = await fetch(msg.runtimeBase + name + v);
      if (!res.ok) throw new Error(`Runtime file ${name} could not be loaded (HTTP ${res.status}).`);
      pyodide.FS.writeFile(`${dir}/${name}`, await res.text());
    })
  );
  pyodide.runPython(`import sys\nsys.path.insert(0, ${JSON.stringify(dir)})`);
  const mod = pyodide.pyimport("pyd_runtime");
  rt = mod.RT;
  const info = JSON.parse(rt.init(host, JSON.stringify({ root: msg.root || "/home/pyodide/project" })));

  // Can a running script suspend (JS Promise Integration)?  Only true when
  // entered through callPromising, so test it that way.
  try {
    pyodide.runPython(
      "def _pyd_probe():\n    from pyodide.ffi import can_run_sync\n    return bool(can_run_sync())\n"
    );
    const probe = pyodide.globals.get("_pyd_probe");
    jspi = probe.callPromising ? Boolean(await probe.callPromising()) : false;
    probe.destroy();
  } catch (e) {
    jspi = false;
  }
  // ?jspi=0 on the page: behave like a browser without JSPI (for testing).
  if (msg.noJspi) jspi = false;
  return { python: info.python, pyodide: pyodide.version, jspi };
}

async function install(msg) {
  // 1. Pyodide packages for every import in the project (numpy, matplotlib...).
  const sources = JSON.parse(rt.read_sources_json());
  const code = Object.values(sources).join("\n\n");
  const loading = [];
  status("packages", "Installing packages…", { progress: 0.55 });
  await pyodide.loadPackagesFromImports(code, {
    messageCallback: (m) => {
      if (/^Loading /.test(m)) {
        loading.push(m.replace(/^Loading /, ""));
        status("packages", "Installing " + m.replace(/^Loading /, ""), { progress: 0.6 });
      }
    },
    errorCallback: (m) => writeConsole("note", m + "\n"),
  });
  // matplotlib is always needed by the bridge, even for odd scripts.
  await pyodide.loadPackage(["matplotlib"], { messageCallback: () => {} });

  // 2. Anything still missing: manifest packages and pure-Python wheels.
  let missing = JSON.parse(rt.missing_imports());
  const wanted = new Set((msg.packages || []).filter((p) => typeof p === "string" && /^[A-Za-z0-9._\-\[\],<>=!~ ]+$/.test(p)));
  const auto = msg.micropip !== false;
  const unavailable = JSON.parse(rt.unavailable_json());
  for (const name of Object.keys(missing)) if (auto && !unavailable[name]) wanted.add(pipName(name));
  const failed = {};
  if (wanted.size) {
    status("packages", "Installing " + [...wanted].join(", ") + " from PyPI…", { progress: 0.7 });
    await pyodide.loadPackage("micropip", { messageCallback: () => {} });
    const micropip = pyodide.pyimport("micropip");
    for (const pkg of wanted) {
      try {
        await micropip.install(pkg);
      } catch (e) {
        failed[pkg] = String(e.message || e).split("\n").slice(-3).join(" ").slice(0, 400);
      }
    }
    micropip.destroy();
    missing = JSON.parse(rt.missing_imports());
  }
  const report = {};
  for (const name of Object.keys(missing)) {
    report[name] = unavailable[name] || failed[pipName(name)] || "not available as a Pyodide package or a pure-Python wheel";
  }
  return { missing: report, loaded: loading };
}

const PIP_NAMES = {
  sklearn: "scikit-learn", PIL: "pillow", cv2: "opencv-python", yaml: "pyyaml", skimage: "scikit-image",
  bs4: "beautifulsoup4", dateutil: "python-dateutil", Bio: "biopython", attr: "attrs", serial: "pyserial",
};
function pipName(name) {
  return PIP_NAMES[name] || name;
}

/* ---- message handling ------------------------------------------------------ */
const handlers = {
  async init(msg) {
    return init(msg);
  },
  load(msg) {
    const files = pyodide.toPy(
      msg.files.map((f) => [f.path, typeof f.data === "string" ? f.data : new Uint8Array(f.data)])
    );
    try {
      return JSON.parse(rt.write_files(files));
    } finally {
      files.destroy();
    }
  },
  detect(msg) {
    status("detect", "Detecting parameters…", { progress: 0.45 });
    const t0 = performance.now();
    const out = JSON.parse(rt.detect(msg.main, JSON.stringify(msg.overrides || {}), JSON.stringify(msg.options || {})));
    out.worker_ms = Math.round(performance.now() - t0);
    return out;
  },
  async install(msg) {
    return install(msg);
  },
  async run(msg) {
    flushConsole();
    status("start", "Starting the script…", { progress: 0.9 });
    const run = rt.run;
    const json = JSON.stringify(msg.config);
    // callPromising lets plt.pause()/time.sleep() suspend the script (JSPI),
    // so pause/step/stop and live parameters keep working during the loop.
    const p = jspi && run.callPromising ? run.callPromising(json) : Promise.resolve(run(json));
    runActive = p;
    try {
      const result = JSON.parse(await p);
      result.jspi = jspi;
      return result;
    } finally {
      run.destroy();
      if (runActive === p) runActive = null;
      flushConsole();
    }
  },
  tick(msg) {
    const t0 = performance.now();
    if (msg.params && Object.keys(msg.params).length) {
      const r = JSON.parse(rt.set_params(JSON.stringify(msg.params)));
      if (r.errors && r.errors.length) writeConsole("note", r.errors.join("\n") + "\n");
    }
    const out = JSON.parse(rt.tick(msg.dt || 0, Boolean(msg.force), Number(msg.snap) || 0));
    out.ms = Math.round((performance.now() - t0) * 10) / 10;
    return out;
  },
  "set-params"(msg) {
    return JSON.parse(rt.set_params(JSON.stringify(msg.values || {})));
  },
  pause(msg) {
    rt.set_paused(Boolean(msg.paused));
    return true;
  },
  step() {
    rt.step();
    return true;
  },
  async stop() {
    const r = JSON.parse(rt.request_abort("stop"));
    if (r.pending && runActive) {
      // wait (briefly) for the suspended script to unwind
      const done = await Promise.race([
        runActive.then(() => true, () => true),
        new Promise((res) => setTimeout(() => res(false), 400)),
      ]);
      return { graceful: done };
    }
    return { graceful: true };
  },
  speed(msg) {
    rt.set_speed(Number(msg.speed) || 1);
    return true;
  },
  fps(msg) {
    rt.set_fps(Number(msg.fps) || 30);
    return true;
  },
  viewport(msg) {
    return JSON.parse(rt.set_viewport(JSON.stringify(msg)));
  },
  visible(msg) {
    rt.set_visible(JSON.stringify(msg.ids || []));
    return true;
  },
  pointer(msg) {
    return JSON.parse(rt.pointer(JSON.stringify(msg)));
  },
  button(msg) {
    return JSON.parse(rt.call_button(String(msg.call), msg.file ? String(msg.file) : null));
  },
  theme(msg) {
    return JSON.parse(rt.retheme(JSON.stringify(msg.old || {}), JSON.stringify(msg.new || {})));
  },
  "frame-ack"(msg) {
    if (rt) rt.frame_ack(msg.n || 1);
    return true;
  },
};

self.onmessage = async (event) => {
  const msg = event.data || {};
  const fn = handlers[msg.type];
  if (!fn) {
    if (msg.id) post({ type: "reply", id: msg.id, ok: false, error: `unknown message type ${msg.type}` });
    return;
  }
  if (!rt && msg.type !== "init") {
    if (msg.id) post({ type: "reply", id: msg.id, ok: false, error: "runtime not ready" });
    return;
  }
  try {
    const result = await fn(msg);
    if (msg.id) post({ type: "reply", id: msg.id, ok: true, result });
  } catch (e) {
    const text = (e && e.message) || String(e);
    flushConsole();
    if (msg.type === "init") post({ type: "fatal", message: text });
    if (msg.id) post({ type: "reply", id: msg.id, ok: false, error: text });
    else post({ type: "error", kind: "worker", exc_type: "Error", message: text, traceback: "", frames: [] });
  }
};
