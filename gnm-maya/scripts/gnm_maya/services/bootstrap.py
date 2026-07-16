"""First-run bootstrap: set up everything that is fetched/built on demand.

The repo ships only code + docs. Two heavyweight pieces are produced on demand
(same pattern as the photo-fitting deps):

  runtime/            the module's own Python 3 with numpy/h5py/etils/...
                      - Windows: portable embeddable CPython 3.11.9
                        (python.org, ~11 MB) + pip via get-pip.py
                      - Linux/macOS: a venv built from the system ``python3``
                        (or Maya's own mayapy as a fallback)
  external/gnm_repo/  the google/GNM repo zip (~40 MB) via the updater

Everything installs inside the module folder; nothing touches the system.
(The posix venv references the host interpreter it was built from — if you
move the module folder afterwards, delete ``runtime/`` and let it rebuild.)
"""

from __future__ import annotations

import glob
import logging
import os
import subprocess
import sys
import urllib.request

from gnm_maya.core import config

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"

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

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


def runtime_python():
  """The bundled runtime's interpreter path for this platform (may not exist
  yet — this is where the bootstrap puts it)."""
  if IS_WINDOWS:
    return os.path.join(RUNTIME_DIR, "python.exe")
  for name in ("python3", "python"):  # venv creates both; prefer python3
    p = os.path.join(RUNTIME_DIR, "bin", name)
    if os.path.isfile(p):
      return p
  return os.path.join(RUNTIME_DIR, "bin", "python3")


def site_packages_dirs():
  """Candidate site-packages dirs inside the runtime (existing ones only)."""
  if IS_WINDOWS:
    dirs = [os.path.join(RUNTIME_DIR, "Lib", "site-packages")]
  else:  # venv layout: lib/python3.X/site-packages
    dirs = glob.glob(os.path.join(RUNTIME_DIR, "lib", "python3*",
                                  "site-packages"))
  return [d for d in dirs if os.path.isdir(d)]


def runtime_available():
  if not os.path.isfile(runtime_python()):
    return False
  return any(os.path.isdir(os.path.join(d, "numpy"))
             for d in site_packages_dirs())


def gnm_repo_available():
  return os.path.isdir(os.path.join(GNM_REPO_DIR, "gnm"))


GALLERY_DIR = os.path.join(config.MODULE_ROOT, "docs", "shapes")
GALLERY_FULL_SIZE = 192  # the repo ships a compact (96px) set for GitHub docs


def gallery_available():
  """True only for a full-quality, up-to-date local gallery.

  The repo ships compact 96px images (small enough to browse on GitHub);
  those, or a gallery marked stale by a GNM model update (which may add
  shapes), trigger a local full-size re-render on first panel open.
  """
  import json
  try:
    with open(os.path.join(GALLERY_DIR, "manifest.json")) as f:
      m = json.load(f)
  except Exception:
    return False
  return m.get("size", 0) >= GALLERY_FULL_SIZE and not m.get("stale")


def all_available():
  return runtime_available() and gnm_repo_available() and gallery_available()


def _download(url, timeout=300):
  logger.info("Downloading %s", url)
  req = urllib.request.Request(url, headers={"User-Agent": "gnm-maya"})
  with urllib.request.urlopen(req, timeout=timeout) as r:
    return r.read()


def _run(cmd, what):
  r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     universal_newlines=True, creationflags=_NO_WINDOW)
  if r.returncode != 0:
    raise RuntimeError("%s failed:\n%s" % (what, r.stdout[-1500:]))
  return r


def _host_python():
  """A Python 3.9+ on this machine that can create the module's venv.

  Prefers a system ``python3``; falls back to the interpreter we are running
  in (mayapy — Maya ships a full CPython that can build venvs).
  """
  import shutil
  for name in ("python3", "python"):
    exe = shutil.which(name)
    if not exe:
      continue
    try:
      r = subprocess.run(
          [exe, "-c", "import sys; print(sys.version_info[:2] >= (3, 9))"],
          stdout=subprocess.PIPE, universal_newlines=True, timeout=15)
      if r.stdout.strip() == "True":
        return exe
    except Exception:
      continue
  return sys.executable  # mayapy


def _ensure_runtime_windows(status):
  """Portable embeddable CPython: self-contained, relocatable, no host deps."""
  import io
  import zipfile

  status("Downloading portable Python %s…" % PY_VER)
  blob = _download(EMBED_URL)
  os.makedirs(RUNTIME_DIR, exist_ok=True)
  with zipfile.ZipFile(io.BytesIO(blob)) as z:
    z.extractall(RUNTIME_DIR)
  with open(os.path.join(RUNTIME_DIR, "python311._pth"), "w") as f:
    f.write(_PTH)

  py = runtime_python()

  status("Installing pip…")
  get_pip = os.path.join(RUNTIME_DIR, "get-pip.py")
  with open(get_pip, "wb") as f:
    f.write(_download(GET_PIP_URL))
  _run([py, get_pip, "--no-warn-script-location"], "get-pip")
  os.remove(get_pip)

  status("Installing core packages (numpy, h5py, …)…")
  site = os.path.join(RUNTIME_DIR, "Lib", "site-packages")
  _run([py, "-m", "pip", "install", "--target", site, *CORE_PACKAGES],
       "pip install")


def _ensure_runtime_posix(status):
  """Linux/macOS: a venv inside the module, built from a host python3.

  There is no official embeddable CPython for posix, so a venv is the
  contained equivalent: all packages live under ``runtime/``; only the
  interpreter itself is referenced from the host.
  """
  host = _host_python()
  status("Creating Python environment (venv)…")
  try:
    _run([host, "-m", "venv", RUNTIME_DIR], "venv creation")
  except RuntimeError as e:
    raise RuntimeError(
        "Could not create the module's Python environment with %r.\n"
        "On Linux, install your distro's python3-venv package and retry.\n\n%s"
        % (host, e))

  py = runtime_python()
  status("Installing core packages (numpy, h5py, …)…")
  _run([py, "-m", "pip", "install", "--upgrade", "pip"], "pip upgrade")
  _run([py, "-m", "pip", "install", *CORE_PACKAGES], "pip install")


def ensure_runtime(status=lambda msg: None):
  """Build/download the module's own Python runtime if it is missing."""
  if runtime_available():
    return False
  if IS_WINDOWS:
    _ensure_runtime_windows(status)
  else:
    _ensure_runtime_posix(status)
  logger.info("Runtime bootstrapped at %s", RUNTIME_DIR)
  return True


def ensure_gnm_repo(status=lambda msg: None):
  """Download the google/GNM repo if it is missing (via the updater)."""
  if gnm_repo_available():
    return False
  status("Downloading google/GNM (~40 MB)…")
  from gnm_maya.services import updater
  updater.download_and_install()
  logger.info("GNM repo bootstrapped at %s", GNM_REPO_DIR)
  return True


def ensure_gallery(status=lambda msg: None):
  """Render the shape-image gallery locally (~5 min, one time). Non-fatal:
  the panel works without images, so failures only log."""
  if gallery_available():
    return False
  status("Rendering shape images (one time, ~5 min)…")
  py = runtime_python()
  script = os.path.join(config.EXTERNAL_DIR, "gen_gallery.py")
  r = subprocess.run([py, script, "--out", GALLERY_DIR,
                      "--modes-per-group", "400"],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     universal_newlines=True, creationflags=_NO_WINDOW)
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
  from maya import cmds as mc
  missing = []
  if not runtime_available():
    missing.append("portable Python runtime (~70 MB download)" if IS_WINDOWS
                   else "Python environment (venv from your python3, "
                        "~40 MB of packages)")
  if not gnm_repo_available():
    missing.append("google/GNM model repo (~40 MB download)")
  if not gallery_available():
    missing.append("shape images (rendered locally, ~5 min one time)")
  if not missing:
    return True
  ans = mc.confirmDialog(
      title="GNM first-run setup",
      message="GNM needs to download:\n- %s\n\nEverything installs inside "
              "the module folder. Continue?" % "\n- ".join(missing),
      button=["Download", "Cancel"], defaultButton="Download",
      cancelButton="Cancel", dismissString="Cancel")
  if ans != "Download":
    return False
  from gnm_maya.ui.progress import MayaProgress
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
  mc.confirmDialog(title="GNM first-run setup", message=msg, button=["OK"],
                   icon="information" if ok else "critical")
  return ok
