"""Test fixture: an exception raised inside a helper module after a few frames."""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from helpers import shape

BREAK_AT = 5    # frame at which helpers.shape fails

x = np.linspace(0, 1, 50)
fig, ax = plt.subplots(figsize=(4, 3))
(line,) = ax.plot(x, x)
ax.set_ylim(-1.1, 1.1)


def update(frame):
    line.set_ydata(shape(x, frame, BREAK_AT))
    return (line,)


anim = FuncAnimation(fig, update, interval=50, cache_frame_data=False)
plt.show()
