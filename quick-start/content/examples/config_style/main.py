"""Particles bouncing around a box: a toy ideal gas.

The physical set-up lives in a dataclass; ``main()`` takes a few extra
knobs for the integration itself.  Run it with:  python main.py
"""
from dataclasses import dataclass
from typing import Literal

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


@dataclass
class GasConfig:
    n_particles: int = 120        # number of particles
    temperature: float = 1.0      # starting kinetic temperature
    box_size: float = 10.0        # side length of the box
    gravity: float = 0.0          # downward acceleration
    integrator: Literal["euler", "verlet"] = "verlet"
    colour_by_speed: bool = True  # colour particles by their speed


def initial_state(cfg, rng):
    pos = rng.uniform(0.0, cfg.box_size, size=(cfg.n_particles, 2))
    vel = rng.normal(0.0, np.sqrt(cfg.temperature), size=(cfg.n_particles, 2))
    return pos, vel


def step(pos, vel, cfg, dt):
    """Advance positions and velocities, bouncing off the walls."""
    acc = np.array([0.0, -cfg.gravity])
    if cfg.integrator == "euler":
        pos = pos + vel * dt
        vel = vel + acc * dt
    else:  # velocity Verlet (exact for constant acceleration)
        pos = pos + vel * dt + 0.5 * acc * dt**2
        vel = vel + acc * dt
    for axis in (0, 1):
        low = pos[:, axis] < 0
        high = pos[:, axis] > cfg.box_size
        pos[low, axis] *= -1
        pos[high, axis] = 2 * cfg.box_size - pos[high, axis]
        vel[low | high, axis] *= -1
    return pos, vel


def main(steps_per_frame: int = 3, dt: float = 0.01, seed: int = 0,
         cfg: GasConfig | None = None) -> FuncAnimation:
    """Animate the gas.

    Args:
        steps_per_frame: integration steps between animation frames
        dt: integration time step in seconds
        seed: random seed for the starting positions and velocities
        cfg: physical set-up (defaults to GasConfig())
    """
    cfg = cfg or GasConfig()
    rng = np.random.default_rng(seed)
    pos, vel = initial_state(cfg, rng)

    fig, ax = plt.subplots(figsize=(5, 5))
    scat = ax.scatter(pos[:, 0], pos[:, 1], s=18, c=np.linalg.norm(vel, axis=1),
                      cmap="plasma", vmin=0, vmax=3 * np.sqrt(cfg.temperature))
    ax.set_xlim(0, cfg.box_size)
    ax.set_ylim(0, cfg.box_size)
    ax.set_aspect("equal")
    ax.set_title(f"{cfg.n_particles} particles, {cfg.integrator} integrator")
    info = ax.text(0.02, 0.97, "", transform=ax.transAxes, va="top")

    def update(frame):
        nonlocal pos, vel
        for _ in range(steps_per_frame):
            pos, vel = step(pos, vel, cfg, dt)
        scat.set_offsets(pos)
        speed = np.linalg.norm(vel, axis=1)
        if cfg.colour_by_speed:
            scat.set_array(speed)
        energy = 0.5 * np.mean(speed**2) + cfg.gravity * np.mean(pos[:, 1])
        info.set_text(f"mean energy {energy:.2f}")
        return scat, info

    anim = FuncAnimation(fig, update, interval=30, cache_frame_data=False)
    plt.show()
    return anim


if __name__ == "__main__":
    main()
