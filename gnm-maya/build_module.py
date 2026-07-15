#!/usr/bin/env python
"""Reproducibly (re)build the portable ``runtime/`` interpreter for the module.

Run with a **Python 3.11** interpreter (to match the embeddable build and get
cp311 numpy wheels):

    py -3.11 build_module.py

It downloads the official Windows embeddable CPython, enables site-packages,
and installs exactly the deps the numpy GNM head path needs. The result,
``runtime/``, is fully self-contained and relocatable — end users never run
this; they just drop the module into Maya's modules dir.

Why embeddable Python instead of a venv: a venv's ``pyvenv.cfg`` hardcodes the
path of the base interpreter, so a committed venv breaks on any machine that
lacks that exact Python. The embeddable package has its own DLLs and a relative
``._pth``, so it runs anywhere.
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


def main():
  if sys.version_info[:2] != (3, 11):
    sys.exit("Run this with Python 3.11 so bundled numpy matches "
             "the 3.11 embeddable runtime (got %d.%d)." % sys.version_info[:2])

  print("Downloading embeddable CPython %s ..." % PY_VER)
  data = urllib.request.urlopen(EMBED_URL).read()
  if os.path.isdir(RUNTIME):
    import shutil
    shutil.rmtree(RUNTIME)
  os.makedirs(RUNTIME)
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

  print("Self-test: generating a template head via the runtime ...")
  out = os.path.join(HERE, "_buildtest")
  subprocess.check_call(
      [os.path.join(RUNTIME, "python.exe"),
       os.path.join(EXTERNAL, "generate.py"), "--out", out, "--template"])
  import shutil
  shutil.rmtree(out, ignore_errors=True)

  print("\nDone. Portable runtime ready at:", RUNTIME)
  print("Ship: GNM.mod + the gnm-maya/ folder (including runtime/ and the "
        "npz asset). Users drop both into ~/Documents/maya/modules.")


if __name__ == "__main__":
  main()
