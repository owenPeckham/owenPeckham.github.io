"""Test fixture: animation written as a plt.pause() loop (no FuncAnimation)."""
import numpy as np
import matplotlib.pyplot as plt

RATE = 1.0        # how fast the dot goes round
STEPS = 100000    # loop length

fig, ax = plt.subplots(figsize=(4, 4))
ax.set_xlim(-1.2, 1.2)
ax.set_ylim(-1.2, 1.2)
ax.set_aspect("equal")
(dot,) = ax.plot([1], [0], "o", ms=12)
title = ax.set_title("")

angle = 0.0
last_rate = None
for i in range(STEPS):
    angle += 0.05 * RATE
    dot.set_data([np.cos(angle)], [np.sin(angle)])
    title.set_text(f"step {i}")
    if RATE != last_rate:
        last_rate = RATE
        print(f"loop RATE={RATE} step={i}")
    plt.pause(0.03)
