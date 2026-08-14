"""Private subprocess boundary for bounded Python self-serve adapters."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path


def _resolve(spec: str) -> Callable[[str], str]:
    if ":" not in spec:
        raise ValueError("adapter spec must be 'module:func' or 'path.py:func'")
    target, func_name = spec.rsplit(":", 1)
    if target.endswith(".py") or "/" in target:
        path = Path(target).resolve()
        module_spec = importlib.util.spec_from_file_location(path.stem, path)
        if module_spec is None or module_spec.loader is None:
            raise ImportError("cannot load adapter module")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    func = getattr(module, func_name, None)
    if not callable(func):
        raise AttributeError("adapter target is not callable")
    return func


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    try:
        func = _resolve(sys.argv[1])
        value = func(sys.stdin.read())
    except Exception:  # candidate details stay in the parent runner's digest-only stderr
        return 3
    if not isinstance(value, str):
        return 4
    sys.stdout.write(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
