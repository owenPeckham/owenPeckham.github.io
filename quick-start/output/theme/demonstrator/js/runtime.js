/* Python demonstrator -- page-side client for the Pyodide worker, and the
 * frame pacer.
 *
 * RuntimeClient wraps worker.js: request/response by id, unsolicited events
 * (status, console, frame, figures, error) re-emitted to the app.
 *
 * Player paces animations from the main thread: it "ticks" the worker using
 * the animation's own interval (scaled by the speed control, capped by the
 * fps setting).  Only one tick is in flight at a time, so the worker is never
 * flooded and control messages (live parameters) are handled between frames;
 * when rendering is slower than the interval, frames are skipped rather than
 * queued.
 */
import { PYODIDE_BASE, RUNTIME_PY_BASE, WORKER_URL } from "./config.js";
import { Emitter } from "./util.js";

/** A frame may be ticked this many ms before it is due.  Display frames come
 * every ~16.7 ms, so without this a 33 ms animation would often miss its
 * second display frame by a fraction of a millisecond and wait for a third. */
const SNAP_MS = 6;

export class RuntimeClient extends Emitter {
  constructor({ version = "", noJspi = false } = {}) {
    super();
    this.version = version;
    this.noJspi = Boolean(noJspi);   // testing: behave like a browser without JSPI
    this.worker = null;
    this.pending = new Map();
    this.seq = 0;
    this.info = null;
    this.ready = null;
    this.generation = 0;
  }

  static supported() {
    if (typeof Worker === "undefined") return "This browser can't run Web Workers, which the Python demonstrator needs.";
    if (typeof WebAssembly !== "object") return "This browser doesn't support WebAssembly, which Python in the browser needs.";
    return null;
  }

  /** Start a fresh worker and load Pyodide in it. */
  spawn() {
    this.terminate();
    this.generation += 1;
    const gen = this.generation;
    const url = WORKER_URL + (this.version ? `?v=${encodeURIComponent(this.version)}` : "");
    const w = new Worker(url);
    this.worker = w;
    w.onmessage = (e) => {
      if (gen === this.generation) this.onMessage(e.data);
    };
    w.onerror = (e) => {
      if (gen !== this.generation) return;
      e.preventDefault && e.preventDefault();
      this.emit("fatal", { message: e.message || "The Python worker crashed." });
    };
    this.ready = this.request("init", { pyodideBase: PYODIDE_BASE, runtimeBase: RUNTIME_PY_BASE, version: this.version, noJspi: this.noJspi }).then((info) => {
      this.info = info;
      return info;
    });
    return this.ready;
  }

  onMessage(msg) {
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "reply") {
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      if (msg.ok) p.resolve(msg.result);
      else p.reject(new Error(msg.error));
      return;
    }
    this.emit(msg.type, msg);
  }

  request(type, payload = {}, transfer = []) {
    if (!this.worker) return Promise.reject(new Error("The Python worker is not running."));
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, type });
      this.worker.postMessage({ ...payload, type, id }, transfer);
    });
  }

  send(type, payload = {}) {
    if (this.worker) this.worker.postMessage({ ...payload, type });
  }

  /** Kill the worker immediately (Stop for a busy script). */
  terminate() {
    if (this.worker) {
      this.worker.terminate();
      this.worker = null;
    }
    for (const p of this.pending.values()) p.reject(Object.assign(new Error("stopped"), { stopped: true }));
    this.pending.clear();
    this.info = null;
  }

  get alive() {
    return Boolean(this.worker);
  }
}

/** Frame pacing for host-driven animations. */
export class Player extends Emitter {
  /**
   * @param {(dt:number, force:boolean, params:object, paramsOnly:boolean, snap:number) => Promise<object>} tick
   */
  constructor(tick) {
    super();
    this.tickFn = tick;
    this.playing = false;
    this.speed = 1;
    this.fps = 30;
    this.inflight = false;
    this.timers = false;      // does the script have running animation timers?
    this.nextAt = 0;
    this.lastTick = 0;
    this.pending = {};
    this.raf = 0;
    this.painted = [];
    this.loop = this.loop.bind(this);
  }

  setTimers(running, nextDueIn) {
    this.timers = Boolean(running);
    if (nextDueIn !== null && nextDueIn !== undefined) this.nextAt = performance.now() + Math.max(nextDueIn / this.speed, 0);
    if (this.timers && this.playing) this.kick();
  }

  play() {
    this.playing = true;
    this.lastTick = performance.now();
    this.kick();
    this.emit("state", this.state());
  }

  pause() {
    this.playing = false;
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.emit("state", this.state());
  }

  stop() {
    this.pause();
    this.timers = false;
    this.pending = {};
  }

  state() {
    return { playing: this.playing, timers: this.timers, speed: this.speed };
  }

  kick() {
    if (!this.raf) this.raf = requestAnimationFrame(this.loop);
  }

  /** Queue live parameter values; they ride along with the next tick. */
  queueParams(values) {
    Object.assign(this.pending, values);
    if (!this.playing || !this.timers) this.flushParams();
  }

  async flushParams() {
    if (this.inflight || !Object.keys(this.pending).length) return;
    const values = this.pending;
    this.pending = {};
    this.inflight = true;
    try {
      await this.tickFn(0, false, values, true);
    } catch (e) {
      /* reported by the app */
    } finally {
      this.inflight = false;
      if (Object.keys(this.pending).length) this.flushParams();
      else if (this.playing) this.kick();
    }
  }

  async step() {
    if (this.inflight) return;
    await this.doTick(performance.now(), true);
  }

  loop(now) {
    this.raf = 0;
    if (!this.playing || !this.timers) return;
    if (!this.inflight && now >= this.nextAt - SNAP_MS) {
      this.doTick(now, false);
    }
    if (this.playing && this.timers) this.raf = requestAnimationFrame(this.loop);
  }

  async doTick(now, force) {
    this.inflight = true;
    const elapsed = Math.min(now - this.lastTick, 250);
    this.lastTick = now;
    const params = this.pending;
    this.pending = {};
    let r = null;
    try {
      r = await this.tickFn(force ? 0 : Math.max(0, elapsed) * this.speed, force, params, false, SNAP_MS * this.speed);
    } catch (e) {
      r = null;
    } finally {
      this.inflight = false;
    }
    if (!r) return;
    const minGap = 1000 / Math.max(1, this.fps);
    const due = r.next_due_in === null || r.next_due_in === undefined ? minGap : r.next_due_in / this.speed;
    this.nextAt = now + Math.max(due, minGap);
    if (r.error) {
      this.pause();
      this.emit("error", r);
    }
    if (!r.running) {
      this.timers = false;
      this.emit("finished", r);
    }
    if (this.playing && this.timers) this.kick();
  }

  /** Called by the app whenever a frame is painted (for the FPS readout). */
  notePainted() {
    const now = performance.now();
    this.painted.push(now);
    while (this.painted.length && now - this.painted[0] > 1000) this.painted.shift();
  }

  measuredFps() {
    const n = this.painted.length;
    if (n < 2) return 0;
    const span = (this.painted[n - 1] - this.painted[0]) / 1000;
    return span > 0 ? (n - 1) / span : 0;
  }
}
