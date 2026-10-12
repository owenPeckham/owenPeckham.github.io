Title: Embedded Python demos
Slug: demo-embeds
Save_as: examples/embeds.html
URL: examples/embeds.html
Status: hidden
Summary: Three Python demonstrator instances embedded in an ordinary page.

This is an ordinary page on the site with three demos embedded in it. Each one
is an `<iframe>` created by the `<py-demo>` element, and Python only loads when
you scroll a demo into view or press its Run button.

## Simulator only

The oscillator, showing just the simulator and its controls:

<py-demo demo="/examples/oscillator/" panes="sim" title="Damped oscillator (simulator only)"></py-demo>

## Simulator and code

A static figure with its code next to it:

<py-demo demo="/examples/static_plot/" panes="sim,code" height="620" title="Line shapes, with code"></py-demo>

## Explainer and simulator, loaded on click

The heat map only starts Python when you press Run:

<py-demo demo="/examples/heat_map/" panes="explainer,sim" load="click" height="640" title="Heat diffusion"></py-demo>
