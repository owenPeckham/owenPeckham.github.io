"""A tiny explicit finite-difference heat solver used by the heat-map demo."""
from .grid import make_grid, laplacian
from .solver import HeatPlate

__all__ = ["HeatPlate", "make_grid", "laplacian"]
