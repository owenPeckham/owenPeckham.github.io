"""Test fixture: matplotlib's own widgets, driven by forwarded pointer events."""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Button, Slider

fig, ax = plt.subplots(figsize=(6, 4))
fig.subplots_adjust(bottom=0.3)
x = np.linspace(0, 2 * np.pi, 300)
(line,) = ax.plot(x, np.sin(x))
ax.set_ylim(-1.1, 1.1)

ax_freq = fig.add_axes([0.2, 0.12, 0.6, 0.05])
freq = Slider(ax_freq, "freq", 0.5, 5.0, valinit=1.0)
ax_reset = fig.add_axes([0.82, 0.02, 0.12, 0.06])
reset = Button(ax_reset, "Reset")


def changed(val):
    line.set_ydata(np.sin(val * x))
    print(f"slider freq={val:.2f}")
    fig.canvas.draw_idle()


def on_reset(event):
    freq.reset()
    print("button reset")


freq.on_changed(changed)
reset.on_clicked(on_reset)
plt.show()
