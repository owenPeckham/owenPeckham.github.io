"""The model: try a different EXPONENT."""
EXPONENT = 2     # power-law exponent


def curve(n):
    return [i ** EXPONENT for i in range(n)]
