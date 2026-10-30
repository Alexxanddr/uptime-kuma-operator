"""Compatibility entrypoint for existing ``kopf run operator.py`` deployments.

When Python discovers this file as stdlib ``operator``, delegate to the complete
stdlib implementation, including dunder aliases used by pip and other tools.
Kopf loads scripts under a distinct module name and therefore loads handlers.
"""

if __name__ == "operator":
    import sys as _sys

    _stdlib = _sys._stdlib_dir
    with open(f"{_stdlib}/operator.py", encoding="utf-8") as _source:
        exec(compile(_source.read(), f"{_stdlib}/operator.py", "exec"))
else:
    from kuma_operator.handlers import *  # noqa: F403
