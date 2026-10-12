"""A damped, driven harmonic oscillator.

Integrates

    x'' + 2*zeta*w0*x' + w0**2 * (x + k3*x**3) = F * w0**2 * cos(wd*t)

with a fourth-order Runge-Kutta step (see physics.py) and animates the
position over time next to the phase-space trajectory.

Run it on your own computer with:  python main.py
"""
from collections import deque

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from physics import rk4_step, normalised_energy

FREQ = 1.0          # natural frequency [Hz]
DAMPING = 0.08      # damping ratio (0 = undamped, 1 = critically damped)
DRIVE = 0.0         # driving amplitude, as a static displacement
DRIVE_FREQ = 0.9    # driving frequency [Hz]
SPEED = 1.0         # simulation speed, in multiples of real time
WINDOW = 8.0        # seconds of history shown in the left plot [s]
DT = 1 / 240        # integration time step [s]
TRAIL = 240         # number of points kept in the phase-space trail
INTERVAL_MS = 33    # time between animation frames [ms]

state = np.array([1.0, 0.0])        # position, velocity
t = 0.0
history_t = deque(maxlen=TRAIL)
history_x = deque(maxlen=TRAIL)
history_v = deque(maxlen=TRAIL)

fig, (ax_x, ax_phase) = plt.subplots(
    1, 2, figsize=(9, 3.6), gridspec_kw={"width_ratios": [1.7, 1]}
)
fig.suptitle("Damped, driven oscillator")

(line_x,) = ax_x.plot([], [], lw=2, label="x(t)")
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


def update(frame):
    """Advance the simulation by one animation frame and redraw."""
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
