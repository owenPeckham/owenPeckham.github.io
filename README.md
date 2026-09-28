# Personal Website

#### To Preview Locally (for testing changes before publishing)
`cd quick-start`

`make devserver`

Open `http://localhost:8000` in your browser. This rebuilds the site automatically whenever you edit a file under `content/` or `themes/owenpeckham/` — just refresh the browser tab to see the change (there's no auto-refresh of the page itself, only the underlying files).

Press `Ctrl + C` in the terminal to stop the dev server when you're done. `make clean` removes the generated `output/` folder if you want a completely fresh rebuild afterwards.

If pelican/Jinja2/Markdown aren't installed yet, run `pip install -r ../requirements.txt` first (ideally inside the `.venv` in the repo root).

#### To Deploy
`cd quick-start`

`make github`

`Ctrl + Shift + R` for a force refresh on the browser that bypasses the local cache.
