"""Read heat sources from a CSV file and turn them into grid masks."""
import csv

from ..grid import cell_centres


def load_sources(path, n):
    """Return ``[(mask, temperature), ...]`` for an n x n grid.

    The CSV has columns x, y, radius, temperature; lines starting with '#'
    are comments.
    """
    xx, yy = cell_centres(n)
    sources = []
    with open(path, newline="") as fh:
        rows = csv.DictReader(line for line in fh if not line.startswith("#"))
        for row in rows:
            x, y, r, temp = (float(row[k]) for k in ("x", "y", "radius", "temperature"))
            mask = (xx - x) ** 2 + (yy - y) ** 2 <= r**2
            sources.append((mask, temp))
    return sources
