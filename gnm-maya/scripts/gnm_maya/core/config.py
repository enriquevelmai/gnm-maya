"""Filesystem layout resolution for the GNM-Maya module.

The module root is the folder that contains ``scripts/``, ``venv/`` and
``external/``. We derive it from this file's location so the module works no
matter where the user drops it, as long as the internal layout is preserved.
"""

from __future__ import annotations

import os

# .../<module>/scripts/gnm_maya/core/config.py  ->  <module>
_HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))

EXTERNAL_DIR = os.path.join(MODULE_ROOT, "external")
GENERATE_SCRIPT = os.path.join(EXTERNAL_DIR, "generate.py")


def venv_python() -> str:
  """Absolute path to the module's bundled Python interpreter.

  Prefers the self-contained ``runtime/`` (Windows: portable embeddable
  Python; Linux/macOS: a venv built on first run). Falls back to a
  locally-built ``venv/`` if present.
  """
  candidates = [
      os.path.join(MODULE_ROOT, "runtime", "python.exe"),          # win portable
      os.path.join(MODULE_ROOT, "runtime", "bin", "python3"),      # posix venv
      os.path.join(MODULE_ROOT, "runtime", "bin", "python"),
      os.path.join(MODULE_ROOT, "venv", "Scripts", "python.exe"),  # dev venv
      os.path.join(MODULE_ROOT, "venv", "bin", "python"),
  ]
  for c in candidates:
    if os.path.isfile(c):
      return c
  raise RuntimeError(
      "GNM bundled Python not found. Expected one of:\n  "
      + "\n  ".join(candidates)
      + "\nRe-run build_module.py to (re)create it."
  )


def check_install() -> None:
  """Raise a clear error if a required piece of the module is missing."""
  if not os.path.isfile(GENERATE_SCRIPT):
    raise RuntimeError(f"Missing generator script: {GENERATE_SCRIPT}")
  venv_python()  # raises with guidance if absent
