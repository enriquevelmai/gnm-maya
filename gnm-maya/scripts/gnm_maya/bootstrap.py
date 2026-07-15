"""First-run bootstrap: download everything that is downloadable.

The repo ships only code + docs. Two heavyweight pieces are fetched on demand
(same pattern as the photo-fitting deps):

  runtime/            portable CPython 3.11.9 (python.org, ~11 MB) + pip
                      (get-pip.py) + the core wheels (numpy/h5py/etils/...)
  external/gnm_repo/  the google/GNM repo zip (~40 MB) via the updater

Everything installs inside the module folder; nothing touches the system.
"""

from __future__ import annotations

import logging
import os
import subprocess
import urllib.request

from gnm_maya import config

logger = logging.getLogger(__name__)

PY_VER = "3.11.9"
EMBED_URL = ("https://www.python.org/ftp/python/%s/"
             "python-%s-embed-amd64.zip" % (PY_VER, PY_VER))
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# Core deps for the numpy GNM head path (validated set; h5py = semantic
# sampler). The heavy photo-fitting stack stays separate (fitting_deps.py).
CORE_PACKAGES = ["numpy", "absl-py", "etils", "immutabledict", "einops",
                 "typing_extensions", "h5py"]

_PTH = "python311.zip\n.\nLib\\site-packages\n\nimport site\n"

RUNTIME_DIR = os.path.join(config.MODULE_ROOT, "runtime")
GNM_REPO_DIR = os.path.join(config.EXTERNAL_DIR, "gnm_repo")


def runtime_available():
  py = os.path.join(RUNTIME_DIR, "python.exe")
  numpy_dir = os.path.join(RUNTIME_DIR, "Lib", "site-packages", "numpy")
  return os.path.isfile(py) and os.path.isdir(numpy_dir)


def gnm_repo_available():
  return os.path.isdir(os.path.join(GNM_REPO_DIR, "gnm"))


GALLERY_DIR = os.path.join(config.MODULE_ROOT, "docs", "shapes")


def gallery_available():
  return os.path.isfile(os.path.join(GALLERY_DIR, "manifest.json"))


def all_available():
  return runtime_available() and gnm_repo_available() and gallery_available()


def _download(url, timeout=300):
  logger.info("Downloading %s", url)
  req = urllib.request.Request(url, headers={"User-Agent": "gnm-maya"})
  with urllib.request.urlopen(req, timeout=timeout) as r:
    return r.read()


def ensure_runtime(status=lambda msg: None):
  """Download + assemble the portable runtime if it is missing."""
  if runtime_available():
    return False
  import io
  import zipfile

  status("Downloading portable Python %s…" % PY_VER)
  blob = _download(EMBED_URL)
  os.makedirs(RUNTIME_DIR, exist_ok=True)
  with zipfile.ZipFile(io.BytesIO(blob)) as z:
    z.extractall(RUNTIME_DIR)
  with open(os.path.join(RUNTIME_DIR, "python311._pth"), "w") as f:
    f.write(_PTH)

  py = os.path.join(RUNTIME_DIR, "python.exe")
  creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

  status("Installing pip…")
  get_pip = os.path.join(RUNTIME_DIR, "get-pip.py")
  with open(get_pip, "wb") as f:
    f.write(_download(GET_PIP_URL))
  r = subprocess.run([py, get_pip, "--no-warn-script-location"],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     universal_newlines=True, creationflags=creationflags)
  if r.returncode != 0:
    raise RuntimeError("get-pip failed:\n%s" % r.stdout[-1500:])
  os.remove(get_pip)

  status("Installing core packages (numpy, h5py, …)…")
  site = os.path.join(RUNTIME_DIR, "Lib", "site-packages")
  r = subprocess.run([py, "-m", "pip", "install", "--target", site,
                      *CORE_PACKAGES],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     universal_newlines=True, creationflags=creationflags)
  if r.returncode != 0:
    raise RuntimeError("pip install failed:\n%s" % r.stdout[-1500:])
  logger.info("Runtime bootstrapped at %s", RUNTIME_DIR)
  return True


def ensure_gnm_repo(status=lambda msg: None):
  """Download the google/GNM repo if it is missing (via the updater)."""
  if gnm_repo_available():
    return False
  status("Downloading google/GNM (~40 MB)…")
  from gnm_maya import updater
  updater.download_and_install()
  logger.info("GNM repo bootstrapped at %s", GNM_REPO_DIR)
  return True


def ensure_gallery(status=lambda msg: None):
  """Render the shape-image gallery locally (~5 min, one time). Non-fatal:
  the panel works without images, so failures only log."""
  if gallery_available():
    return False
  status("Rendering shape images (one time, ~5 min)…")
  py = os.path.join(RUNTIME_DIR, "python.exe")
  script = os.path.join(config.EXTERNAL_DIR, "gen_gallery.py")
  creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
  r = subprocess.run([py, script, "--out", GALLERY_DIR,
                      "--modes-per-group", "400"],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     universal_newlines=True, creationflags=creationflags)
  if r.returncode != 0:
    logger.warning("Gallery generation failed (panel works without images):\n%s",
                   r.stdout[-1000:])
    return False
  logger.info("Shape gallery rendered at %s", GALLERY_DIR)
  return True


def ensure_all(status=lambda msg: None):
  """Bootstrap whatever is missing. Returns list of what was installed."""
  done = []
  if ensure_runtime(status):
    done.append("runtime")
  if ensure_gnm_repo(status):
    done.append("gnm_repo")
  if ensure_gallery(status):  # needs runtime + gnm_repo; keep last
    done.append("gallery")
  return done


def ensure_all_with_dialog():
  """Maya-facing: confirm + bootstrap behind a progress dialog."""
  import maya.cmds as cmds
  missing = []
  if not runtime_available():
    missing.append("portable Python runtime (~70 MB download)")
  if not gnm_repo_available():
    missing.append("google/GNM model repo (~40 MB download)")
  if not gallery_available():
    missing.append("shape images (rendered locally, ~5 min one time)")
  if not missing:
    return True
  ans = cmds.confirmDialog(
      title="GNM first-run setup",
      message="GNM needs to download:\n- %s\n\nEverything installs inside "
              "the module folder. Continue?" % "\n- ".join(missing),
      button=["Download", "Cancel"], defaultButton="Download",
      cancelButton="Cancel", dismissString="Cancel")
  if ans != "Download":
    return False
  from gnm_maya.progress import MayaProgress
  steps = {"n": 0}
  try:
    with MayaProgress("GNM first-run setup", maximum=len(missing) + 1) as prog:
      def status(m):
        logger.info("%s", m)
        steps["n"] += 1
        prog.set(steps["n"], m)
      ensure_all(status=status)
    ok = True
    msg = "Setup complete — GNM is ready."
  except Exception as e:
    logger.exception("bootstrap failed")
    ok = False
    msg = "Setup failed:\n%s" % e
  cmds.confirmDialog(title="GNM first-run setup", message=msg, button=["OK"])
  return ok
