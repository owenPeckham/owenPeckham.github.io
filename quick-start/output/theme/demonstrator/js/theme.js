/* Python demonstrator -- light/dark theme handling.
 *
 * The site's own CSS custom properties (--bg, --ink, --text, --muted,
 * --rule, --surface...) are the single source of colour.  The scheme is
 * whatever those tokens resolve to, so if site.css gains a dark palette the
 * demonstrator follows automatically.  Explicit choices use the same hook as
 * the PCLS tool: <html data-theme="light|dark">.
 *
 * Matplotlib colours are derived from the same tokens and handed to the
 * worker as rcParams before the script runs (the script's own styling always
 * wins), and swapped on existing figures when the theme changes.
 */

const TOKENS = ["--bg", "--surface", "--surface-border", "--ink", "--text", "--muted", "--rule", "--accent"];

let probe = null;

function cssColorToHex(value) {
  if (!probe) {
    probe = document.createElement("span");
    probe.style.display = "none";
    document.body.appendChild(probe);
  }
  probe.style.color = "";
  probe.style.color = value;
  const rgb = getComputedStyle(probe).color;
  const m = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+%?))?\s*\)/.exec(rgb);
  if (!m) return null;
  const hex = [m[1], m[2], m[3]].map((x) => Math.round(Number(x)).toString(16).padStart(2, "0")).join("");
  return "#" + hex;
}

/** Resolved token colours as hex strings. */
export function readTokens(el = document.documentElement) {
  const cs = getComputedStyle(el);
  const out = {};
  for (const t of TOKENS) {
    const v = cs.getPropertyValue(t).trim();
    out[t] = v ? cssColorToHex(v) : null;
  }
  if (!out["--bg"]) out["--bg"] = cssColorToHex(getComputedStyle(document.body).backgroundColor) || "#ffffff";
  return out;
}

function luminance(hex) {
  const n = parseInt((hex || "#ffffff").slice(1), 16);
  const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

export function currentScheme(tokens = readTokens()) {
  return luminance(tokens["--bg"]) < 0.4 ? "dark" : "light";
}

/** matplotlib rcParams matching the page. */
export function themeRc(tokens = readTokens()) {
  const t = (k, d) => tokens[k] || d;
  const dark = currentScheme(tokens) === "dark";
  const bg = t("--bg", dark ? "#0f1014" : "#ffffff");
  const ink = t("--ink", dark ? "#f2f3f5" : "#14151a");
  const text = t("--text", ink);
  const muted = t("--muted", dark ? "#9a9eab" : "#70747e");
  const rule = t("--rule", dark ? "#2a2c34" : "#eceef2");
  return {
    "figure.facecolor": bg,
    "figure.edgecolor": bg,
    "savefig.facecolor": bg,
    "axes.facecolor": bg,
    "axes.edgecolor": muted,
    "axes.labelcolor": text,
    "axes.titlecolor": ink,
    "text.color": ink,
    "xtick.color": muted,
    "ytick.color": muted,
    "xtick.labelcolor": text,
    "ytick.labelcolor": text,
    "grid.color": dark ? "#3a3d47" : "#d9dce3",
    "legend.facecolor": bg,
    "legend.edgecolor": rule,
    "legend.labelcolor": text,
    "boxplot.boxprops.color": text,
    "hatch.color": text,
  };
}

/**
 * Apply an explicit theme option:
 *   site  -- leave the page alone (follow the site's own CSS)
 *   light / dark -- set <html data-theme>
 *   auto  -- follow the operating system (prefers-color-scheme)
 */
export function applyThemeOption(option) {
  const root = document.documentElement;
  if (option === "light" || option === "dark") {
    root.setAttribute("data-theme", option);
  } else if (option === "auto" && window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const set = () => root.setAttribute("data-theme", mq.matches ? "dark" : "light");
    set();
    if (mq.addEventListener) mq.addEventListener("change", set);
    else if (mq.addListener) mq.addListener(set);
  }
  syncSchemeAttr();
}

/** Mirror the resolved scheme into <html data-pyd-scheme> for our CSS. */
export function syncSchemeAttr() {
  const scheme = currentScheme();
  if (document.documentElement.getAttribute("data-pyd-scheme") !== scheme) {
    document.documentElement.setAttribute("data-pyd-scheme", scheme);
  }
  return scheme;
}

/** Call `cb({scheme, rc})` whenever the page's colours change. */
export function watchTheme(cb) {
  let last = JSON.stringify(themeRc());
  const check = () => {
    requestAnimationFrame(() => {
      const scheme = syncSchemeAttr();
      const rc = themeRc();
      const key = JSON.stringify(rc);
      if (key !== last) {
        last = key;
        cb({ scheme, rc });
      }
    });
  };
  const mo = new MutationObserver(check);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class", "style"] });
  mo.observe(document.body, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    if (mq.addEventListener) mq.addEventListener("change", check);
    else if (mq.addListener) mq.addListener(check);
  }
  return () => mo.disconnect();
}
