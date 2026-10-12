"""Test fixture: how fast does a live parameter reach the running script?

The first frame that sees a new GAIN prints the wall-clock time, which the
browser test compares with the moment it changed the control.
"""
import time

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

GAIN = 1.0          # amplitude of the wave
INTERVAL_MS = 33    # time between frames [ms]

x = np.linspace(0, 2 * np.pi, 200)
fig, ax = plt.subplots(figsize=(5, 3))
(line,) = ax.plot(x, np.sin(x))
ax.set_ylim(-3, 3)
seen = None


def update(frame):
    global seen
    if GAIN != seen:
        seen = GAIN
        print(f"probe GAIN={GAIN} wall_ms={time.time() * 1000:.0f} frame={frame}")
    line.set_ydata(GAIN * np.sin(x + frame / 10))
    return (line,)


anim = FuncAnimation(fig, update, interval=INTERVAL_MS, cache_frame_data=False)
plt.show()
