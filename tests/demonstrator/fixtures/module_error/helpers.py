"""Helper module whose function fails once the frame number is high enough."""
import numpy as np


def shape(x, frame, break_at):
    scale = 1.0
    if frame >= break_at:
        scale = 1 // 0      # deliberate ZeroDivisionError (line 8)
    return scale * np.sin(x * (frame + 1))
