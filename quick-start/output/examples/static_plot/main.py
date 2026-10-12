"""Four classic line shapes on one static figure, with one highlighted."""
import numpy as np
import matplotlib.pyplot as plt

SHAPE = "lorentzian"   # the line shape to highlight

SHAPES = {
    "sinc": lambda x: np.sinc(x),
    "gaussian": lambda x: np.exp(-0.5 * x**2),
    "lorentzian": lambda x: 1 / (1 + x**2),
    "sech2": lambda x: 1 / np.cosh(x) ** 2,
}

x = np.linspace(-6, 6, 600)
fig, ax = plt.subplots(figsize=(7, 4))
for name, f in SHAPES.items():
    if name != SHAPE:
        ax.plot(x, f(x), lw=1.2, alpha=0.45, label=name)
ax.plot(x, SHAPES[SHAPE](x), lw=3, label=f"{SHAPE} (highlighted)")
ax.axhline(0, lw=0.8, color="0.5")
ax.legend(loc="upper right")
ax.set_title(f"Line shapes: {SHAPE}")
ax.set_xlabel("x / width")
plt.show()
