"""Test fixture: a harmless static figure; the attack is in explainer.md."""
import matplotlib.pyplot as plt

LEVEL = 3     # bar height

fig, ax = plt.subplots(figsize=(4, 3))
ax.bar(["a", "b"], [1, LEVEL])
plt.show()
