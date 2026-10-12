"""Heat spreading across a metal plate with a few hot and cold spots.

The plate starts near room temperature with a little random noise.  The
heat sources listed in data/sources.csv are held at fixed temperatures, and
the explicit finite-difference solver in the ``heatsim`` package spreads the
heat a few steps every frame.

Run it on your own computer with:  python main.py
"""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from heatsim import HeatPlate
from heatsim.sources import load_sources

GRID = 64              # cells along each side of the plate
SEED = 7               # random seed for the starting noise
NOISE = 0.05           # amplitude of the starting temperature noise
DIFFUSIVITY = 0.2      # how fast heat spreads (stable below 0.25)
STEPS_PER_FRAME = 4    # solver steps between animation frames
COLORMAP = "inferno"   # colour map, one of: inferno, viridis, coolwarm, magma

rng = np.random.default_rng(SEED)
sources = load_sources("data/sources.csv", GRID)
plate = HeatPlate(GRID, sources, noise=NOISE, rng=rng)

fig, ax = plt.subplots(figsize=(5.4, 4.6))
image = ax.imshow(plate.temperature, cmap=COLORMAP, vmin=0.0, vmax=1.0,
                  origin="lower", interpolation="bilinear")
fig.colorbar(image, ax=ax, label="temperature")
ax.set_title(f"Heat diffusion on a {GRID} × {GRID} plate")
ax.set_xticks([])
ax.set_yticks([])
step_text = ax.text(0.02, 0.95, "", transform=ax.transAxes, color="white", va="top")


def update(frame):
    for _ in range(STEPS_PER_FRAME):
        plate.step(DIFFUSIVITY)
    image.set_data(plate.temperature)
    step_text.set_text(f"step {plate.steps}")
    return image, step_text


anim = FuncAnimation(fig, update, frames=None, interval=40, cache_frame_data=False)
plt.show()
