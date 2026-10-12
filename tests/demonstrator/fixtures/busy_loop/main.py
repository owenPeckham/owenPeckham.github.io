"""Test fixture: a script that never yields (an accidental endless loop)."""
n = 0
while True:
    n += 1
