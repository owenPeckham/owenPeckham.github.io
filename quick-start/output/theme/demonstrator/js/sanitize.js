/* Python demonstrator -- HTML sanitising for rendered explainers.
 *
 * Markdown may contain raw HTML; everything goes through DOMPurify with a
 * strict allowlist:
 *   - no <script>, <style>, forms, <object>/<embed>, <base>/<meta>/<link>;
 *   - no inline event handlers (DOMPurify default) and no style attributes;
 *   - <iframe> only when the demo opts in, and only same-origin;
 *   - URLs: http(s), mailto, tel, relative, #fragment and demo: actions;
 *   - images only from this site (or config.ALLOWED_ORIGINS);
 *   - external links open in a new tab with rel="noopener noreferrer".
 * Pure enough to test in a browser page (tests/demonstrator/browser/).
 */

export const URI_RE = /^(?:(?:https?|mailto|tel|demo):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i;

export const PURIFY_CONFIG = {
  USE_PROFILES: { html: true },
  FORBID_TAGS: [
    "style", "script", "iframe", "frame", "frameset", "object", "embed", "form", "input", "button",
    "textarea", "select", "option", "base", "meta", "link", "template", "noscript", "dialog", "portal",
  ],
  FORBID_ATTR: ["style", "srcset", "formaction", "ping", "background", "action"],
  ALLOW_DATA_ATTR: false,
  ALLOWED_URI_REGEXP: URI_RE,
  RETURN_DOM_FRAGMENT: true,
};

function originOf(url) {
  try {
    return new URL(url).origin;
  } catch (e) {
    return null;
  }
}

/**
 * Sanitise explainer HTML.
 *   purify   -- the DOMPurify instance
 *   html     -- rendered Markdown
 *   opts     -- {baseUrl, pageOrigin, allowedOrigins, allowIframes, linkPolicy}
 * Returns {fragment, blocked: [{kind, url}], missingAlt: number}.
 */
export function sanitizeExplainerHtml(purify, html, opts = {}) {
  const baseUrl = opts.baseUrl || document.baseURI;
  const pageOrigin = opts.pageOrigin || location.origin;
  const allowed = new Set([pageOrigin, ...(opts.allowedOrigins || [])]);
  const blocked = [];
  let missingAlt = 0;
  const config = { ...PURIFY_CONFIG };
  if (opts.allowIframes) {
    config.FORBID_TAGS = config.FORBID_TAGS.filter((t) => t !== "iframe");
    config.ADD_TAGS = ["iframe"];
    config.ADD_ATTR = ["allowfullscreen", "loading"];
  }

  purify.removeAllHooks();
  purify.addHook("afterSanitizeAttributes", (node) => {
    const tag = node.nodeName;
    if (tag === "A" && node.hasAttribute("href")) {
      const href = node.getAttribute("href").trim();
      if (/^demo:/i.test(href) || href.startsWith("#")) return;
      if (/^(mailto|tel):/i.test(href)) return;
      let abs;
      try {
        abs = new URL(href, baseUrl).href;
      } catch (e) {
        node.removeAttribute("href");
        return;
      }
      const origin = originOf(abs);
      if (origin !== pageOrigin) {
        if (opts.linkPolicy === "allowlist" && !allowed.has(origin)) {
          blocked.push({ kind: "link", url: abs });
          node.removeAttribute("href");
          return;
        }
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
        node.classList.add("pyd-external");
      } else {
        node.removeAttribute("target");
      }
      node.setAttribute("href", abs);
    } else if (tag === "IFRAME") {
      const src = node.getAttribute("src") || "";
      let abs = null;
      try {
        abs = new URL(src, baseUrl).href;
      } catch (e) {
        abs = null;
      }
      if (abs && originOf(abs) === pageOrigin) {
        node.setAttribute("src", abs);
        node.setAttribute("sandbox", "allow-scripts allow-same-origin");
        node.setAttribute("loading", "lazy");
      } else {
        blocked.push({ kind: "iframe", url: src });
        node.className = "pyd-drop";
      }
    } else if (tag === "IMG") {
      const src = node.getAttribute("src") || "";
      let abs = null;
      try {
        abs = new URL(src, baseUrl).href;
      } catch (e) {
        abs = null;
      }
      if (!abs || !/^https?:/i.test(abs) || !allowed.has(originOf(abs))) {
        blocked.push({ kind: "image", url: src });
        node.setAttribute("data-pyd-blocked", "");
        node.removeAttribute("src");
      } else {
        node.setAttribute("src", abs);
      }
      if (!node.hasAttribute("alt") || !node.getAttribute("alt").trim()) {
        missingAlt += 1;
        if (!node.hasAttribute("alt")) node.setAttribute("alt", "");
        node.classList.add("pyd-missing-alt");
      }
      node.setAttribute("loading", "lazy");
      node.setAttribute("decoding", "async");
    }
  });
  let fragment;
  try {
    fragment = purify.sanitize(html, config);
  } finally {
    purify.removeAllHooks();
  }
  for (const f of fragment.querySelectorAll("iframe.pyd-drop")) f.remove();
  // Blocked images become their alt text so the prose still reads.
  for (const img of fragment.querySelectorAll("img[data-pyd-blocked]")) {
    const span = document.createElement("span");
    span.className = "pyd-blocked";
    span.textContent = `[image not shown: ${img.getAttribute("alt") || "external image"}]`;
    img.replaceWith(span);
  }
  return { fragment, blocked, missingAlt };
}
