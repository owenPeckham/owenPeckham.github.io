"""The damped, driven oscillator again, annotated with ``# demo:`` hints.

The hints are ordinary comments: they change nothing when you run this file
with ``python main.py``, but the browser demonstrator reads them to choose
slider ranges, labels and units.  ``kick()`` is bound to a button in
demo.json.
"""
from collections import deque

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from physics import rk4_step, normalised_energy

FREQ = 1.0          # demo: range=0.2..4 step=0.05 label="Natural frequency" unit=Hz mode=live
DAMPING = 0.08      # demo: range=0..2 step=0.01 label="Damping ratio ζ"
DRIVE = 0.0         # demo: range=0..1 step=0.01 label="Drive amplitude"
DRIVE_FREQ = 0.9    # demo: range=0.2..4 step=0.05 unit=Hz label="Drive frequency"
SPEED = 1.0         # demo: range=0.25..4 scale=log label="Speed" unit=×
WINDOW = 8.0        # demo: hide
DT = 1 / 240        # demo: ignore
TRAIL = 240         # demo: range=20..2000 step=10 label="Trail length" group="Display"
INTERVAL_MS = 33    # demo: ignore

state = np.array([1.0, 0.0])        # position, velocity
t = 0.0
history_t = deque(maxlen=TRAIL)
history_x = deque(maxlen=TRAIL)
history_v = deque(maxlen=TRAIL)

fig, (ax_x, ax_phase) = plt.subplots(
    1, 2, figsize=(9, 3.6), gridspec_kw={"width_ratios": [1.7, 1]}
)
fig.suptitle("Damped, driven oscillator (with hints)")

(line_x,) = ax_x.plot([], [], lw=2)
(mass,) = ax_x.plot([], [], "o", ms=9)
ax_x.set_xlabel("time [s]")
ax_x.set_ylabel("position x")
ax_x.set_ylim(-2.4, 2.4)
ax_x.grid(alpha=0.3)
energy_text = ax_x.text(0.02, 0.9, "", transform=ax_x.transAxes)

(trail,) = ax_phase.plot([], [], lw=1.5, alpha=0.8)
(dot,) = ax_phase.plot([], [], "o", ms=7)
ax_phase.set_xlabel("x")
ax_phase.set_ylabel("v / ω₀")
ax_phase.set_xlim(-2.4, 2.4)
ax_phase.set_ylim(-2.4, 2.4)
ax_phase.set_aspect("equal")
ax_phase.grid(alpha=0.3)


def kick():
    """Give the mass a sudden push (bound to a button in demo.json)."""
    global state
    state = state + np.array([0.0, 2.0 * np.pi * FREQ])


def update(frame):
    global state, t
    steps = max(1, round(SPEED * INTERVAL_MS / 1000 / DT))
    for _ in range(steps):
        state = rk4_step(state, t, DT, FREQ, DAMPING, DRIVE, DRIVE_FREQ)
        t += DT

    w0 = 2 * np.pi * FREQ
    history_t.append(t)
    history_x.append(state[0])
    history_v.append(state[1] / w0)

    line_x.set_data(history_t, history_x)
    mass.set_data([t], [state[0]])
    ax_x.set_xlim(max(0.0, t - WINDOW), max(WINDOW, t))
    trail.set_data(history_x, history_v)
    dot.set_data([state[0]], [state[1] / w0])
    energy_text.set_text(f"t = {t:5.1f} s    energy = {normalised_energy(state, FREQ):.3f}")
    return line_x, mass, trail, dot, energy_text


anim = FuncAnimation(fig, update, frames=None, interval=INTERVAL_MS, cache_frame_data=False)
fig.tight_layout()
plt.show()
