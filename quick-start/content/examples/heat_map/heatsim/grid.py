"""Grid helpers: the starting temperature field and a discrete Laplacian."""
import numpy as np

AMBIENT = 0.3  # starting temperature of the plate (0 = cold, 1 = hot)


def make_grid(n, noise=0.0, rng=None):
    """An n x n plate at the ambient temperature, plus optional noise."""
    u = np.full((n, n), AMBIENT)
    if noise and rng is not None:
        u = u + noise * rng.standard_normal((n, n))
    return np.clip(u, 0.0, 1.0)


def cell_centres(n):
    """Coordinates of the cell centres as fractions of the plate width."""
    c = (np.arange(n) + 0.5) / n
    return np.meshgrid(c, c)


def laplacian(u):
    """Five-point Laplacian with insulated (zero-flux) edges."""
    p = np.pad(u, 1, mode="edge")
    return p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] - 4 * u
