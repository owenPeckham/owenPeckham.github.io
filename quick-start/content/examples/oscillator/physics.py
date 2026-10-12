"""Equations of motion and an RK4 integrator for the oscillator demo."""
import numpy as np

NONLINEARITY = 0.0  # cubic (Duffing) stiffness; 0 gives a linear spring


def acceleration(x, v, t, freq, damping, drive, drive_freq):
    """x'' for a damped oscillator pushed by drive * cos(2*pi*drive_freq*t)."""
    w0 = 2 * np.pi * freq
    wd = 2 * np.pi * drive_freq
    spring = w0**2 * (x + NONLINEARITY * x**3)
    return -2 * damping * w0 * v - spring + drive * w0**2 * np.cos(wd * t)


def derivatives(state, t, *args):
    x, v = state
    return np.array([v, acceleration(x, v, t, *args)])


def rk4_step(state, t, dt, *args):
    """Advance ``state = (x, v)`` by one fourth-order Runge-Kutta step."""
    k1 = derivatives(state, t, *args)
    k2 = derivatives(state + 0.5 * dt * k1, t + 0.5 * dt, *args)
    k3 = derivatives(state + 0.5 * dt * k2, t + 0.5 * dt, *args)
    k4 = derivatives(state + dt * k3, t + dt, *args)
    return state + dt / 6 * (k1 + 2 * k2 + 2 * k3 + k4)


def normalised_energy(state, freq):
    """x**2 + (v/w0)**2: 1.0 for the starting amplitude, 0 at rest."""
    x, v = state
    w0 = 2 * np.pi * freq
    return x**2 + (v / w0) ** 2
