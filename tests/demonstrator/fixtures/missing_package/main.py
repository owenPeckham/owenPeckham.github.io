"""Test fixture: imports a package that exists nowhere."""
import matplotlib.pyplot as plt
import definitely_not_a_real_package_xyz as nope

fig, ax = plt.subplots()
ax.plot([0, 1], [0, nope.VALUE])
plt.show()
