/* Python demonstrator -- entry point for demonstrator pages.
 *
 * The Pelican template (templates/demonstrator.html) renders
 *   <div id="pyd-root" data-demo="/examples/oscillator/" ...>
 * inside the site's normal page layout and loads this module.  Without a
 * demo (the generic /demonstrator.html page opened bare) it lists the demos
 * the build plugin found.
 */
import { DemoApp } from "./app.js";
import { readPageOptions } from "./manifest.js";
import { h } from "./util.js";

async function listDemos(root, page) {
  const mount = root.querySelector("[data-pyd-mount]") || root;
  const siteRoot = new URL(page.siteRoot || "/", location.href);
  const roots = (root.dataset.catalogues || "demos,examples").split(",").map((s) => s.trim()).filter(Boolean);
  const sections = [];
  for (const r of roots) {
    try {
      const res = await fetch(new URL(`${r}/index.json`, siteRoot), { cache: "no-cache" });
      if (!res.ok) continue;
      const data = await res.json();
      if (!Array.isArray(data.demos) || !data.demos.length) continue;
      const ul = h("ul", { class: "pyd-catalogue" });
      const sameSite = (u) => {
        try {
          const url = new URL(u, siteRoot);
          return url.origin === location.origin ? url.pathname + url.search + url.hash : null;
        } catch (e) {
          return null;
        }
      };
      for (const d of data.demos) {
        if (typeof d.path !== "string" || /^[a-z]+:|^\/\//i.test(d.path)) continue;
        const href = (typeof d.page === "string" && sameSite(d.page)) || `${new URL("demonstrator.html", siteRoot).pathname}?demo=${encodeURIComponent("/" + d.path.replace(/^\/+/, ""))}`;
        ul.appendChild(h("li", null, h("a", { href }, d.title || d.path), d.description ? h("span", { class: "pyd-catalogue-desc" }, ` — ${d.description}`) : null));
      }
      sections.push(h("h2", { class: "section-heading" }, r === "examples" ? "Examples" : "Demos"), ul);
    } catch (e) {
      /* no catalogue */
    }
  }
  mount.replaceChildren(
    h("p", null, "Choose a demo, or open this page with ", h("code", null, "?demo=path/to/demo/"), "."),
    ...(sections.length ? sections : [h("p", { class: "pyd-boot" }, "No demos were found.")])
  );
}

const root = document.getElementById("pyd-root");
if (root) {
  const page = readPageOptions(root);
  page.generic = !root.dataset.demo && !root.dataset.script;
  if (!page.demo && !page.script) listDemos(root, page);
  else {
    const app = new DemoApp(root, page);
    window.pydDemo = app;
    app.start().catch((e) => app.fail(e));
  }
}
