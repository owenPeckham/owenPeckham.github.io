"""Explicit Euler time stepping for the heat equation u_t = alpha * lap(u)."""
from .grid import make_grid, laplacian


class HeatPlate:
    """Temperature field on a square plate with fixed-temperature sources."""

    def __init__(self, n, sources, noise=0.0, rng=None):
        self.n = n
        self.sources = sources
        self.temperature = make_grid(n, noise, rng)
        self.steps = 0
        self._hold_sources()

    def _hold_sources(self):
        for mask, value in self.sources:
            self.temperature[mask] = value

    def step(self, alpha):
        """Advance one explicit Euler step (stable for alpha < 0.25)."""
        self.temperature = self.temperature + alpha * laplacian(self.temperature)
        self._hold_sources()
        self.steps += 1
