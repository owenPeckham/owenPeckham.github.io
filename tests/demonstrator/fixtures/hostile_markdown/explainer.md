---
title: Hostile explainer <img src=x onerror="top.__pwned='front-matter'">
summary: "<script>top.__pwned='summary'</script>"
toc: true
---

# Hostile Markdown

Every payload below tries to run code or change the page. The test passes
when none of them does, the page still looks normal, and the ordinary
content (this paragraph, the table, the valid demo link) still renders.
A [valid demo link](demo:set?LEVEL=5) must keep working.

## Raw HTML

Script element: <script>top.__pwned = 'script'</script>

Image with onerror: <img src="x" alt="x" onerror="top.__pwned='img'">

SVG with onload: <svg onload="top.__pwned='svg'"><circle r="5"></circle></svg>

Iframe: <iframe src="javascript:top.__pwned='iframe'"></iframe>

Same-origin iframe without opt-in: <iframe src="/demonstrator.html"></iframe>

Object: <object data="javascript:top.__pwned='object'"></object>

Embed: <embed src="data:text/html,<script>top.__pwned='embed'</script>">

Form: <form action="javascript:top.__pwned='form'"><button>Submit</button></form>

Input: <input autofocus onfocus="top.__pwned='autofocus'">

Details: <details open ontoggle="top.__pwned='details'"><summary>details</summary>x</details>

Mutation XSS: <math><mtext><table><mglyph><style><img src=x onerror="top.__pwned='mxss'"></style></mglyph></table></mtext></math>

Noscript trick: <noscript><p title="</noscript><img src=x onerror=top.__pwned='noscript'>"></p></noscript>

Style element: <style>body { display: none !important; }</style>

Inline style: <div style="position:fixed;inset:0;background:red;z-index:99999">overlay</div>

Base: <base href="https://evil.example/">

Meta refresh: <meta http-equiv="refresh" content="0; url=https://evil.example/">

Link preload: <link rel="stylesheet" href="https://evil.example/x.css">

Template: <template><img src=x onerror="top.__pwned='template'"></template>

Data attribute: <span data-pyd-action="run">data attribute</span>

## Links

[javascript link](javascript:top.__pwned='md-link')

[data link](data:text/html;base64,PHNjcmlwdD50b3AuX19wd25lZD0nZGF0YSc8L3NjcmlwdD4=)

[vbscript link](vbscript:msgbox(1))

<a href="javascript:top.__pwned='raw-link'">raw javascript link</a>

<a href="JaVaScRiPt:top.__pwned='mixed-case'">mixed-case javascript link</a>

<a href="&#106;avascript:top.__pwned='entity'">entity-encoded javascript link</a>

<a href=" javascript:top.__pwned='space'">leading-space javascript link</a>

<a href="java&#x09;script:top.__pwned='tab'">tab-split javascript link</a>

[external link](https://example.org/) and [relative link](../other/)

<a href="https://example.org/" target="_self">external link asking for _self</a>

## Images

![javascript image](javascript:top.__pwned='md-img')

![tracking pixel](https://evil.example/pixel.png)

<img src="https://evil.example/pixel2.png" alt="raw external image">

![no alt text](data:image/png;base64,iVBORw0KGgo=)

## Maths

KaTeX with \href: $\href{javascript:top.__pwned='katex'}{click me}$

KaTeX with \url: $\url{javascript:top.__pwned='katex-url'}$

KaTeX with HTML: $\htmlClass{x}{y}$ and $\includegraphics{https://evil.example/a.png}$

## demo: links that must degrade to text

[unknown action](demo:frobnicate?x=1)

[unknown parameter](demo:set?NOPE=1)

[bad value](demo:set?LEVEL=abc)

[missing file](demo:code=missing.py:3)

[path traversal](demo:code=../../etc/passwd:1)

[unknown preset](demo:preset=nope)

[javascript in a demo link](demo:set?LEVEL=javascript:alert(1))

## Code

```html
<script>top.__pwned = 'code-block'</script>
```

`<img src=x onerror="top.__pwned='inline-code'">`

| Payload | In a table |
|---|---|
| <img src=x onerror="top.__pwned='table'"> | <script>top.__pwned='table-script'</script> |

Footnote with a payload.[^x]

[^x]: <img src=x onerror="top.__pwned='footnote'"> [js](javascript:top.__pwned='footnote-link')
