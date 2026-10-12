/* Python demonstrator -- console: stdout, stderr, notes and tracebacks.
 *
 * Everything is inserted as text (never HTML).  Tracebacks get clickable
 * file:line links that open the file in the code pane.
 */
import { h, icon } from "./util.js";

const MAX_LINES = 2000;

export class ConsolePane {
  /**
   * @param {HTMLElement} el      container
   * @param {object} cb {onLocate(file, line), onToggle(open)}
   */
  constructor(el, cb) {
    this.el = el;
    this.cb = cb;
    this.lines = 0;
    this.errors = 0;
    this.out = h("pre", { class: "pyd-console-out", tabindex: "0", role: "log", "aria-live": "polite", "aria-label": "Python output" });
    this.badge = h("span", { class: "pyd-console-badge", hidden: true });
    this.toggle = h("button", { type: "button", class: "pyd-console-toggle", "aria-expanded": "false" }, h("span", null, "Console"), this.badge);
    this.clearBtn = h("button", { type: "button", class: "pyd-tool", title: "Clear the console" }, icon("close", "Clear"));
    this.toggle.addEventListener("click", () => this.setOpen(!this.open));
    this.clearBtn.addEventListener("click", () => this.clear());
    this.body = h("div", { class: "pyd-console-body", hidden: true }, this.out);
    el.replaceChildren(h("div", { class: "pyd-console-head" }, this.toggle, h("span", { class: "pyd-spacer" }), this.clearBtn), this.body);
    this.open = false;
  }

  setOpen(open) {
    this.open = open;
    this.body.hidden = !open;
    this.toggle.setAttribute("aria-expanded", String(open));
    this.el.classList.toggle("is-open", open);
    if (open) this.out.scrollTop = this.out.scrollHeight;
    if (this.cb.onToggle) this.cb.onToggle(open);
  }

  write(stream, text) {
    if (!text) return;
    const span = h("span", { class: `pyd-con-${stream}` }, text);
    this.out.appendChild(span);
    this.lines += (text.match(/\n/g) || []).length;
    this.trim();
    if (stream === "stderr") this.bump();
    this.stick();
  }

  error(err) {
    const block = h("span", { class: "pyd-con-error" });
    const title = `${err.exc_type || "Error"}: ${err.message || ""}\n`;
    const tb = (err.traceback || "").trimEnd();
    if (tb) {
      // Turn 'File "physics.py", line 12' into links for project files.
      const projectFiles = new Set((err.frames || []).map((f) => f.file));
      const re = /File "([^"]+)", line (\d+)/g;
      let last = 0;
      let m;
      while ((m = re.exec(tb))) {
        block.appendChild(document.createTextNode(tb.slice(last, m.index)));
        if (projectFiles.has(m[1])) {
          const file = m[1];
          const line = Number(m[2]);
          const a = h("button", { type: "button", class: "pyd-con-link", title: `Show ${file} line ${line}` }, `File "${file}", line ${line}`);
          a.addEventListener("click", () => this.cb.onLocate(file, line));
          block.appendChild(a);
        } else block.appendChild(document.createTextNode(m[0]));
        last = re.lastIndex;
      }
      block.appendChild(document.createTextNode(tb.slice(last) + "\n"));
    } else block.appendChild(document.createTextNode(title));
    if (err.hint) block.appendChild(h("span", { class: "pyd-con-hint" }, `→ ${err.hint}\n`));
    this.out.appendChild(block);
    this.errors += 1;
    this.bump();
    this.setOpen(true);
    this.stick();
  }

  note(text) {
    this.write("note", text.endsWith("\n") ? text : text + "\n");
  }

  bump() {
    this.badge.hidden = false;
    this.badge.textContent = String(this.errors || "!");
    this.badge.title = `${this.errors} error${this.errors === 1 ? "" : "s"}`;
  }

  stick() {
    const o = this.out;
    if (o.scrollHeight - o.scrollTop - o.clientHeight < 80) o.scrollTop = o.scrollHeight;
  }

  trim() {
    while (this.lines > MAX_LINES && this.out.firstChild) {
      const t = this.out.firstChild.textContent;
      this.lines -= (t.match(/\n/g) || []).length;
      this.out.firstChild.remove();
    }
  }

  clear() {
    this.out.replaceChildren();
    this.lines = 0;
    this.errors = 0;
    this.badge.hidden = true;
  }
}
