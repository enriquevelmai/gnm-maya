#!/usr/bin/env python
"""Reproducibly (re)build the module's ``runtime/`` interpreter (dev helper).

End users never run this — the same logic runs automatically on first use
(scripts/gnm_maya/services/bootstrap.py). It exists so developers can rebuild
the runtime without Maya.

Windows — run with a **Python 3.11** interpreter (to match the embeddable
build and get cp311 numpy wheels):

    py -3.11 build_module.py

It downloads the official Windows embeddable CPython, enables site-packages,
and installs exactly the deps the numpy GNM head path needs. The result is
fully self-contained and relocatable. (Why embeddable instead of a venv: a
venv's ``pyvenv.cfg`` hardcodes the base interpreter's path, so a copied venv
breaks on any machine that lacks that exact Python.)

Linux/macOS — run with any Python 3.9+:

    python3 build_module.py

There is no embeddable CPython for posix, so a venv is created at ``runtime/``
instead. It references the interpreter that built it — rebuild (delete
runtime/ and re-run) if you move the module folder.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.join(HERE, "runtime")
EXTERNAL = os.path.join(HERE, "external")

IS_WINDOWS = os.name == "nt"

PY_VER = "3.11.9"
EMBED_URL = ("https://www.python.org/ftp/python/%s/"
             "python-%s-embed-amd64.zip" % (PY_VER, PY_VER))

# Minimal, verified dependency set for gnm.shape.gnm_numpy (the head path).
# scipy is intentionally excluded (unused by the head path; ~139 MB saved).
REQUIREMENTS = [
    "numpy",
    "absl-py",
    "etils",
    "immutabledict",
    "einops",
    "typing_extensions",
    "h5py",  # reads the semantic-sampler decoder weights (no TensorFlow needed)
]

_PTH = "python311.zip\n.\nLib\\site-packages\n\nimport site\n"


def _runtime_python():
  if IS_WINDOWS:
    return os.path.join(RUNTIME, "python.exe")
  return os.path.join(RUNTIME, "bin", "python3")


def _build_windows():
  if sys.version_info[:2] != (3, 11):
    sys.exit("Run this with Python 3.11 so bundled numpy matches "
             "the 3.11 embeddable runtime (got %d.%d)." % sys.version_info[:2])

  print("Downloading embeddable CPython %s ..." % PY_VER)
  data = urllib.request.urlopen(EMBED_URL).read()
  with zipfile.ZipFile(io.BytesIO(data)) as z:
    z.extractall(RUNTIME)

  # Enable site-packages on the runtime's import path.
  with open(os.path.join(RUNTIME, "python311._pth"), "w") as f:
    f.write(_PTH)

  site_packages = os.path.join(RUNTIME, "Lib", "site-packages")
  os.makedirs(site_packages, exist_ok=True)

  print("Installing dependencies into runtime ...")
  subprocess.check_call([
      sys.executable, "-m", "pip", "install", "--upgrade",
      "--target", site_packages, *REQUIREMENTS,
  ])


def _build_posix():
  if sys.version_info[:2] < (3, 9):
    sys.exit("Run this with Python 3.9+ (got %d.%d)." % sys.version_info[:2])

  print("Creating venv at %s ..." % RUNTIME)
  subprocess.check_call([sys.executable, "-m", "venv", RUNTIME])
  py = _runtime_python()

  print("Installing dependencies into the venv ...")
  subprocess.check_call([py, "-m", "pip", "install", "--upgrade", "pip"])
  subprocess.check_call([py, "-m", "pip", "install", *REQUIREMENTS])


def main():
  if os.path.isdir(RUNTIME):
    import shutil
    shutil.rmtree(RUNTIME)
  os.makedirs(RUNTIME)

  if IS_WINDOWS:
    _build_windows()
  else:
    _build_posix()

  print("Self-test: generating a template head via the runtime ...")
  out = os.path.join(HERE, "_buildtest")
  subprocess.check_call(
      [_runtime_python(),
       os.path.join(EXTERNAL, "generate.py"), "--out", out, "--template"])
  import shutil
  shutil.rmtree(out, ignore_errors=True)

  print("\nDone. Runtime ready at:", RUNTIME)
  print("Ship: GNM.mod + the gnm-maya/ folder. Users drop both into "
        "~/Documents/maya/modules (or just drag drag_and_drop_install.py "
        "into Maya).")


if __name__ == "__main__":
  main()
