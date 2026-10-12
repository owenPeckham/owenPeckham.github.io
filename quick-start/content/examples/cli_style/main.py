"""A scrolling waveform generator configured from the command line.

Examples:
    python main.py
    python main.py --freq 3 --wave square --grid
    python main.py --points 120 --wave sawtooth --harmonics 3
"""
import argparse

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


def make_parser():
    parser = argparse.ArgumentParser(description="Scrolling waveform generator")
    parser.add_argument("--freq", type=float, default=1.5,
                        help="signal frequency in Hz (default: %(default)s)")
    parser.add_argument("--points", type=int, default=300,
                        help="number of samples across the window")
    parser.add_argument("--wave", choices=["sine", "square", "sawtooth", "triangle"],
                        default="sine", help="waveform shape")
    parser.add_argument("--harmonics", type=int, default=7,
                        help="Fourier terms used to build the non-sine shapes")
    parser.add_argument("--grid", action="store_true", help="draw grid lines")
    parser.add_argument("--title", default="Waveform generator", help="plot title")
    return parser


def waveform(kind, phase, harmonics):
    """Band-limited waveforms built from a few Fourier terms."""
    if kind == "sine":
        return np.sin(phase)
    y = np.zeros_like(phase)
    for k in range(1, harmonics + 1):
        if kind == "square" and k % 2:
            y += 4 / (np.pi * k) * np.sin(k * phase)
        elif kind == "sawtooth":
            y += 2 / np.pi * (-1) ** (k + 1) / k * np.sin(k * phase)
        elif kind == "triangle" and k % 2:
            y += 8 / np.pi**2 * (-1) ** ((k - 1) // 2) / k**2 * np.sin(k * phase)
    return y


def main(args):
    x = np.linspace(0.0, 2.0, args.points)
    fig, ax = plt.subplots(figsize=(8, 3.4))
    (line,) = ax.plot(x, np.zeros_like(x), lw=2)
    ax.set_xlim(0, 2)
    ax.set_ylim(-1.5, 1.5)
    ax.set_xlabel("time [s]")
    ax.set_title(args.title)
    if args.grid:
        ax.grid(alpha=0.4)

    def update(frame):
        phase = 2 * np.pi * args.freq * (x + frame / 60)
        line.set_ydata(waveform(args.wave, phase, args.harmonics))
        return (line,)

    anim = FuncAnimation(fig, update, frames=None, interval=33, blit=True,
                         cache_frame_data=False)
    plt.show()
    return anim


if __name__ == "__main__":
    main(make_parser().parse_args())
