"""Test fixture: explicit colours that the site theme must not override."""
import matplotlib.pyplot as plt

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6, 3))
fig.patch.set_facecolor("#ffeecc")    # explicit figure background
ax1.set_facecolor("#ccddff")           # explicit axes background
ax1.plot([0, 1], [0, 1], color="red", lw=4)
ax2.plot([0, 1], [1, 0], lw=4)         # default colours: follow the theme
plt.show()
