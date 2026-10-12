"""Test fixture: saves its animation to a file before showing it, and the
animation has a fixed number of frames (so it finishes)."""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

fig, ax = plt.subplots(figsize=(4, 3))
x = np.linspace(0, 2 * np.pi, 100)
(line,) = ax.plot(x, np.sin(x))


def update(frame):
    line.set_ydata(np.sin(x + frame / 5))
    return (line,)


anim = FuncAnimation(fig, update, frames=40, interval=40, repeat=False)
anim.save("wave.gif", writer="pillow")
plt.show()
