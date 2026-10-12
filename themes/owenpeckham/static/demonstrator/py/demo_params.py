"""demo_params -- optional helper for scripts shown in the Python demonstrator.

You never *need* this module: the demonstrator finds parameters in ordinary
scripts by itself.  Use it when you want to guarantee that a value updates
live while the animation runs, or to react when the visitor changes it.

In the browser the demonstrator puts this module on ``sys.path`` and keeps
``params`` filled with the current control values.  On your own computer the
same script must still run, so either copy this file next to your script, or
use the usual fallback pattern::

    try:
        from demo_params import params, on_change
    except ImportError:                      # plain Python: just the defaults
        params = {"freq": 2.0, "method": "rk4"}
        def on_change(name, callback):
            pass

    def update(frame):
        w = 2 * np.pi * params["freq"]       # always the current slider value
        ...

    on_change("method", lambda value: reset_simulation())

The keys of the fallback dict become controls (their values are the
defaults).  ``params.declare()`` attaches extra information when you use the
real module (also available when this file is copied next to your script)::

    params.declare("freq", 2.0, min=0.1, max=10, step=0.1, unit="Hz", label="Frequency")

Parameters declared here take precedence over anything detected
automatically, and reads of ``params[...]`` inside functions that run every
frame are applied live.
"""

__all__ = ["params", "on_change", "Params", "IN_BROWSER"]

IN_BROWSER = False   # set to True by the demonstrator runtime


class Params(dict):
    """A dict of parameter values with a ``declare`` helper."""

    def declare(self, name, default, **info):
        """Declare a parameter with its default and optional control info
        (``min``, ``max``, ``step``, ``unit``, ``label``, ``help``,
        ``choices``, ``scale``, ``group``).  Returns the current value."""
        if name not in self:
            self[name] = default
        _INFO[name] = dict(info, default=default)
        return self[name]

    # ``params.param(...)`` reads naturally too.
    param = declare


params = Params()
_INFO = {}
_CALLBACKS = {}


def on_change(name, callback):
    """Call ``callback(value)`` whenever the visitor changes ``name``.

    ``on_change("*", callback)`` receives every change as
    ``callback(name, value)``.  Returns ``callback``.
    """
    if not callable(callback):
        raise TypeError("on_change(name, callback): callback must be callable")
    _CALLBACKS.setdefault(name, []).append(callback)
    return callback


def _pyd_reset(values):
    """Runtime hook: fresh values and no callbacks before each run."""
    global IN_BROWSER
    IN_BROWSER = True
    params.clear()
    params.update(values or {})
    _CALLBACKS.clear()


def _pyd_set(name, value):
    """Runtime hook: a live change from the page."""
    params[name] = value
    for cb in list(_CALLBACKS.get(name, ())):
        cb(value)
    for cb in list(_CALLBACKS.get("*", ())):
        cb(name, value)
