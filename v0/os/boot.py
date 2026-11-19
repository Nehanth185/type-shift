"""Loads this folder as the Python package `worker_os` and exposes its FastAPI router.

The folder is named `os`, which would shadow Python's own `os` module if imported by that name, so server.py
never imports it directly. It does:   os_router = runpy.run_path(".../os/boot.py")["router"]
Tests and scripts can do the same, or call load() to get the package.
"""
import importlib.util
import os as _os
import sys

HERE = _os.path.dirname(_os.path.abspath(__file__))
SERVER = _os.path.join(_os.path.dirname(HERE), "server")


def load():
    if "worker_os" in sys.modules:
        return sys.modules["worker_os"]
    if SERVER not in sys.path:
        sys.path.append(SERVER)  # the OS reuses the server modules (workapi, grounded, memory, retrieval)
    spec = importlib.util.spec_from_file_location("worker_os", _os.path.join(HERE, "__init__.py"),
                                                  submodule_search_locations=[HERE])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["worker_os"] = mod
    spec.loader.exec_module(mod)
    return mod


router = load().router
