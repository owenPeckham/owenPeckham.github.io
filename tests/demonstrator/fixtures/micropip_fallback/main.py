"""Test fixture: needs a pure-Python package that Pyodide does not ship
(tabulate), so the demonstrator has to fetch it from PyPI with micropip."""
import matplotlib.pyplot as plt
from tabulate import tabulate

rows = [["alpha", 1], ["beta", 2]]
print(tabulate(rows, headers=["name", "value"], tablefmt="github"))

fig, ax = plt.subplots(figsize=(4, 3))
ax.bar([r[0] for r in rows], [r[1] for r in rows])
plt.show()
