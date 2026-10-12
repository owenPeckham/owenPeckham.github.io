"""Test fixture: two figures, one animated and one static."""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

FREQ = 2.0      # wave frequency

fig1, ax1 = plt.subplots(figsize=(5, 3), num="Wave")
x = np.linspace(0, 1, 200)
(line,) = ax1.plot(x, np.sin(2 * np.pi * FREQ * x))
ax1.set_ylim(-1.2, 1.2)


def update(frame):
    line.set_ydata(np.sin(2 * np.pi * FREQ * (x - frame / 50)))
    return (line,)


anim = FuncAnimation(fig1, update, interval=40, cache_frame_data=False)

fig2, ax2 = plt.subplots(figsize=(4, 4), num="Histogram")
ax2.hist(np.random.default_rng(0).normal(size=500), bins=30, color="tab:green")
ax2.set_title("A static histogram")
plt.show()
