"""Test fixture: the main file is fine but a module it imports has a syntax error."""
import matplotlib.pyplot as plt

POINTS = 20       # number of points
SCALE = 2.5       # vertical scale

from broken import curve  # noqa: E402

fig, ax = plt.subplots()
ax.plot(curve(POINTS, SCALE))
plt.show()
