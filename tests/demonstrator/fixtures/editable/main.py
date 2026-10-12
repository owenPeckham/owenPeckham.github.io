"""Test fixture: a project that can be edited in the browser."""
import matplotlib.pyplot as plt

from model import curve

POINTS = 30      # number of points

fig, ax = plt.subplots(figsize=(4, 3))
ax.plot(curve(POINTS))
ax.set_title(f"{POINTS} points")
plt.show()
