"""Pelican plugin for the Python demonstrator (see DEMONSTRATOR.md).

What it does on every build (``make html``, ``make devserver``, ``make publish``):

* For every demo folder under ``DEMONSTRATOR_ROOTS`` (default ``demos`` and
  ``examples`` in the output), writes ``demo.index.json``: the list of files
  with sizes and SHA-1 hashes.  The page uses it to expand wildcards in
  demo.json ``files``, to find files when there is no list at all, and to
  cache-bust (each file is requested as ``name.py?v=<hash>``, so edits show up
  immediately while unchanged files stay cached).
* Writes ``<root>/index.json`` catalogues, listed by the bare
  /demonstrator.html page.
* Checks every demo.json and logs a warning for invalid JSON or a missing
  main file, so mistakes show up at build time.
* Sets ``DEMONSTRATOR_VERSION`` (a hash of the demonstrator's own assets) for
  cache-busting the JS/CSS links in the templates.
* Writes ``.nojekyll`` at the site root: without it GitHub Pages runs Jekyll,
  which drops files and folders starting with "_" (``__init__.py``!) and
  rewrites Markdown files that have front matter.
"""
import hashlib
import json
import logging
import os
import posixpath

from pelican import signals

logger = logging.getLogger(__name__)

SKIP_DIRS = {"__pycache__", ".git", ".ipynb_checkpoints", "node_modules"}
SKIP_FILES = {"demo.index.json", "index.html", ".DS_Store", "Thumbs.db"}
_PAGES = {}   # demo path -> page URL (pages using the demonstrator template)


def _settings_paths(pelican):
    theme_static = os.path.join(pelican.settings["THEME"], "static", "demonstrator")
    return theme_static


def _hash_tree(folder):
    h = hashlib.sha1()
    for dirpath, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if name.startswith(".") or name.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, name)
            h.update(os.path.relpath(full, folder).replace(os.sep, "/").encode())
            with open(full, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()[:12]


def initialized(pelican):
    settings = pelican.settings
    settings.setdefault("DEMONSTRATOR_ROOTS", ["demos", "examples"])
    settings.setdefault("DEMONSTRATOR_NOJEKYLL", True)
    if not settings.get("DEMONSTRATOR_VERSION"):
        folder = _settings_paths(pelican)
        settings["DEMONSTRATOR_VERSION"] = _hash_tree(folder) if os.path.isdir(folder) else "dev"


def collect_pages(generator):
    """Remember which pages show which demo, for the catalogue."""
    pages = list(getattr(generator, "pages", [])) + list(getattr(generator, "hidden_pages", []))
    for page in pages:
        demo = getattr(page, "demo", None)
        if getattr(page, "template", "") == "demonstrator" and demo:
            key = demo.strip().strip("/")
            _PAGES[key] = "/" + page.url.lstrip("/")


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _demo_folders(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        if "demo.json" in files or "main.py" in files:
            yield dirpath
            # a demo's own subfolders are part of it, not separate demos
            dirs[:] = []


def index_folder(folder):
    """Files of one demo folder: [{path, size, sha1}], sorted, no timestamps
    (so rebuilding an unchanged site leaves the output unchanged)."""
    entries = []
    for dirpath, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if name in SKIP_FILES or name.startswith(".") or name.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, folder).replace(os.sep, "/")
            entries.append({"path": rel, "size": os.path.getsize(full), "sha1": _sha1(full)})
    return entries


def finalized(pelican):
    settings = pelican.settings
    write_indexes(pelican.output_path, settings.get("DEMONSTRATOR_ROOTS", ["demos", "examples"]),
                  nojekyll=settings.get("DEMONSTRATOR_NOJEKYLL", True))


def write_indexes(out, roots, nojekyll=True):
    """Write demo.index.json for every demo folder under each root in the
    output directory ``out``, plus <root>/index.json catalogues."""
    if nojekyll:
        with open(os.path.join(out, ".nojekyll"), "w", encoding="utf-8") as fh:
            fh.write("")
    for root_name in roots:
        root = os.path.join(out, root_name)
        # an empty catalogue for an empty root (saves the page a 404)
        os.makedirs(root, exist_ok=True)
        catalogue = []
        for folder in _demo_folders(root):
            rel_folder = posixpath.join(root_name, os.path.relpath(folder, root).replace(os.sep, "/")).rstrip("/.")
            manifest = {}
            mpath = os.path.join(folder, "demo.json")
            if os.path.exists(mpath):
                try:
                    with open(mpath, encoding="utf-8") as fh:
                        manifest = json.load(fh)
                    if not isinstance(manifest, dict):
                        raise ValueError("must be a JSON object")
                except (ValueError, OSError) as exc:
                    logger.warning("demonstrator: %s/demo.json is not valid: %s", rel_folder, exc)
                    manifest = {}
            main = manifest.get("main", "main.py") if isinstance(manifest.get("main", "main.py"), str) else "main.py"
            files = index_folder(folder)
            if main not in {f["path"] for f in files} and not manifest.get("bundle"):
                logger.warning("demonstrator: %s has no %s", rel_folder, main)
            index = {"version": 1, "main": main, "files": files}
            with open(os.path.join(folder, "demo.index.json"), "w", encoding="utf-8") as fh:
                json.dump(index, fh, indent=1)
            catalogue.append({
                "path": rel_folder + "/",
                "title": manifest.get("title") if isinstance(manifest.get("title"), str) else os.path.basename(folder),
                "description": manifest.get("description") if isinstance(manifest.get("description"), str) else "",
                "page": _PAGES.get(rel_folder.strip("/")),
            })
        with open(os.path.join(root, "index.json"), "w", encoding="utf-8") as fh:
            json.dump({"demos": catalogue}, fh, indent=1)
        logger.info("demonstrator: indexed %d demo(s) in %s/", len(catalogue), root_name)


def register():
    signals.initialized.connect(initialized)
    signals.page_generator_finalized.connect(collect_pages)
    signals.finalized.connect(finalized)
