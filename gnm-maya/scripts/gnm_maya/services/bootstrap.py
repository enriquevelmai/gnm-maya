"""First-run bootstrap: set up everything that is fetched/built on demand.

The repo ships only code + docs. Two heavyweight pieces are produced on demand
(same pattern as the photo-fitting deps):

  runtime/            the module's own Python 3.12 with numpy/h5py/etils/...
                      - Windows: portable embeddable CPython (python.org)
                        + pip via get-pip.py
                      - Linux/macOS: portable relocatable CPython
                        (astral-sh/python-build-standalone, the build uv
                        uses); a venv from a local Python 3.12+ is only the
                        fallback if that download fails
  external/gnm_repo/  the google/GNM repo zip (~40 MB) via the updater

Everything installs inside the module folder; nothing touches the system.

google/GNM needs Python >= 3.12 (PEP 695 generics + jaxtyping since
2026-11-10). A runtime older than that, or missing a core package, counts as
unavailable and is rebuilt by the next first-run setup.
"""

from __future__ import annotations

import glob
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request

from gnm_maya.core import config

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"

# Oldest Python the vendored google/GNM imports on (PEP 695 syntax).
MIN_PY = (3, 12)

PY_VER = "3.12.10"  # last 3.12 with an official Windows embeddable zip
EMBED_URL = ("https://www.python.org/ftp/python/%s/"
             "python-%s-embed-amd64.zip" % (PY_VER, PY_VER))
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# Portable CPython for macOS/Linux (pinned release; pip included, relocatable).
PBS_RELEASE = "20261110"
PBS_PY_VER = "3.12.16"
PBS_URL = ("https://github.com/astral-sh/python-build-standalone/releases/"
           "download/{rel}/cpython-{ver}%2B{rel}-{triple}-install_only.tar.gz")

# Core deps for the numpy GNM head path (validated set; h5py = semantic
# sampler, jaxtyping = GNM's array annotations). The heavy photo-fitting
# stack stays separate (fitting_deps.py).
CORE_PACKAGES = ["numpy", "absl-py", "etils", "immutabledict", "einops",
                 "typing_extensions", "h5py", "jaxtyping"]
# Packages that must be present for the runtime to count as usable.
_REQUIRED_IMPORTS = ("numpy", "jaxtyping")

_PTH = "python312.zip\n.\nLib\\site-packages\n\nimport site\n"

RUNTIME_DIR = os.path.join(config.MODULE_ROOT, "runtime")
GNM_REPO_DIR = os.path.join(config.EXTERNAL_DIR, "gnm_repo")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


def runtime_python():
  """The bundled runtime's interpreter path for this platform (may not exist
  yet — this is where the bootstrap puts it)."""
  if IS_WINDOWS:
    return os.path.join(RUNTIME_DIR, "python.exe")
  for name in ("python3", "python"):  # both layouts ship both; prefer python3
    p = os.path.join(RUNTIME_DIR, "bin", name)
    if os.path.isfile(p):
      return p
  return os.path.join(RUNTIME_DIR, "bin", "python3")


def site_packages_dirs():
  """Candidate site-packages dirs inside the runtime (existing ones only)."""
  if IS_WINDOWS:
    dirs = [os.path.join(RUNTIME_DIR, "Lib", "site-packages")]
  else:  # lib/python3.X/site-packages (portable build and venv alike)
    dirs = glob.glob(os.path.join(RUNTIME_DIR, "lib", "python3*",
                                  "site-packages"))
  return [d for d in dirs if os.path.isdir(d)]


def runtime_version():
  """(major, minor) of the installed runtime, read from its file layout
  (no subprocess), or None if it cannot be told."""
  if IS_WINDOWS:
    pat, rx = os.path.join(RUNTIME_DIR, "python3*.dll"), r"^python3(\d+)\.dll$"
  else:
    pat, rx = os.path.join(RUNTIME_DIR, "lib", "python3.*"), r"^python3\.(\d+)$"
  found = []
  for path in glob.glob(pat):
    m = re.match(rx, os.path.basename(path))
    if m:
      found.append((3, int(m.group(1))))
  return max(found) if found else None


def runtime_available():
  if not os.path.isfile(runtime_python()):
    return False
  ver = runtime_version()
  if ver is None or ver < MIN_PY:
    return False
  return any(all(os.path.isdir(os.path.join(d, mod))
                 for mod in _REQUIRED_IMPORTS)
             for d in site_packages_dirs())


def runtime_outdated():
  """True if a runtime exists but must be rebuilt (old Python / missing deps)."""
  return os.path.exists(RUNTIME_DIR) and not runtime_available()


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


def _py_ok(exe):
  """True if ``exe`` runs and is at least MIN_PY."""
  try:
    r = subprocess.run(
        [exe, "-c", "import sys; print(sys.version_info[:2] >= %r)"
         % (MIN_PY,)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        universal_newlines=True, timeout=30, creationflags=_NO_WINDOW)
    return r.stdout.strip() == "True"
  except Exception:
    return False


def _host_python():
  """A Python >= MIN_PY on this machine that can build a venv, or None.

  Maya started from the Dock/launcher has a minimal PATH (no Homebrew), so
  the usual install locations are probed explicitly, then Maya's mayapy.
  """
  names = ["python3.%d" % m for m in range(14, MIN_PY[1] - 1, -1)]
  names += ["python3", "python"]
  cands = [shutil.which(n) for n in names]
  for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
    cands += [os.path.join(d, n) for n in names]
  cands += sorted(glob.glob(
      "/Library/Frameworks/Python.framework/Versions/3.*/bin/python3"),
      reverse=True)
  loc = os.environ.get("MAYA_LOCATION")
  if loc:
    cands.append(os.path.join(loc, "bin", "mayapy"))
  seen = set()
  for exe in cands:
    if exe and exe not in seen and os.path.isfile(exe):
      seen.add(exe)
      if _py_ok(exe):
        return exe
  return None


def _pbs_triple():
  """python-build-standalone target triple for this machine, or None."""
  arch = {"arm64": "aarch64", "aarch64": "aarch64",
          "x86_64": "x86_64", "amd64": "x86_64"}.get(platform.machine().lower())
  if not arch:
    return None
  if sys.platform == "darwin":
    return "%s-apple-darwin" % arch
  if sys.platform.startswith("linux"):
    return "%s-unknown-linux-gnu" % arch
  return None


def _clear_runtime():
  """Remove an old/partial runtime so it can be rebuilt from scratch."""
  if not os.path.exists(RUNTIME_DIR):
    return
  try:  # the resident worker runs from the runtime; release it first
    from gnm_maya.core import worker
    worker.shutdown_worker()
  except Exception:
    pass
  shutil.rmtree(RUNTIME_DIR, ignore_errors=True)
  if os.path.exists(RUNTIME_DIR):
    raise RuntimeError(
        "Could not remove the old Python runtime at\n%s\nClose Maya, delete "
        "that folder by hand and start Maya again." % RUNTIME_DIR)


def _ensure_runtime_windows(status):
  """Portable embeddable CPython: self-contained, relocatable, no host deps."""
  import io
  import zipfile

  status("Downloading portable Python %s…" % PY_VER)
  blob = _download(EMBED_URL)
  os.makedirs(RUNTIME_DIR, exist_ok=True)
  with zipfile.ZipFile(io.BytesIO(blob)) as z:
    z.extractall(RUNTIME_DIR)
  with open(os.path.join(RUNTIME_DIR, "python312._pth"), "w") as f:
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


def _install_portable_posix(status):
  """Download + unpack the relocatable CPython into runtime/. Raises on any
  failure (the caller then falls back to a venv from a local Python)."""
  import io
  import tarfile

  triple = _pbs_triple()
  if not triple:
    raise RuntimeError("no portable Python build for %s/%s"
                       % (sys.platform, platform.machine()))
  status("Downloading portable Python %s…" % PBS_PY_VER)
  blob = _download(PBS_URL.format(rel=PBS_RELEASE, ver=PBS_PY_VER,
                                  triple=triple))
  tmp = RUNTIME_DIR + "_tmp"
  shutil.rmtree(tmp, ignore_errors=True)
  os.makedirs(tmp)
  try:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
      if hasattr(tarfile, "data_filter"):
        t.extractall(tmp, filter="data")
      else:
        t.extractall(tmp)
    os.rename(os.path.join(tmp, "python"), RUNTIME_DIR)  # strip "python/"
  finally:
    shutil.rmtree(tmp, ignore_errors=True)
  if not _py_ok(runtime_python()):
    raise RuntimeError("the downloaded Python does not run on this machine")


def _ensure_runtime_posix(status):
  """macOS/Linux: portable CPython inside the module; venv as a fallback."""
  try:
    _install_portable_posix(status)
  except Exception as e:
    logger.warning("Portable Python unavailable (%s); trying a venv from a "
                   "local Python %d.%d+.", e, MIN_PY[0], MIN_PY[1])
    shutil.rmtree(RUNTIME_DIR, ignore_errors=True)
    host = _host_python()
    if not host:
      raise RuntimeError(
          "Could not download the portable Python (%s), and no Python "
          "%d.%d or newer was found on this machine.\nInstall Python 3.12+ "
          "from python.org (or Homebrew) and run the setup again."
          % (e, MIN_PY[0], MIN_PY[1]))
    status("Creating Python environment (venv)…")
    try:
      _run([host, "-m", "venv", RUNTIME_DIR], "venv creation")
    except RuntimeError as e2:
      raise RuntimeError(
          "Could not create the module's Python environment with %r.\n"
          "On Linux, install your distro's python3-venv package and retry."
          "\n\n%s" % (host, e2))

  py = runtime_python()
  status("Installing core packages (numpy, h5py, …)…")
  _run([py, "-m", "pip", "install", "--upgrade", "pip"], "pip upgrade")
  _run([py, "-m", "pip", "install", *CORE_PACKAGES], "pip install")


def ensure_runtime(status=lambda msg: None):
  """Build/download the module's own Python runtime if it is missing or too
  old for the vendored GNM (an old one is replaced)."""
  if runtime_available():
    return False
  _clear_runtime()
  try:
    if IS_WINDOWS:
      _ensure_runtime_windows(status)
    else:
      _ensure_runtime_posix(status)
  except Exception:
    shutil.rmtree(RUNTIME_DIR, ignore_errors=True)  # never keep a half-built one
    raise
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


_gallery_deferred = {"later": False}  # "Later" pressed this session


def ensure_all_with_dialog():
  """Maya-facing: confirm + bootstrap behind a progress dialog.

  The runtime and the model are required; the full-quality shape gallery is
  optional (the panel works with the compact images or none), so when only
  the gallery is missing the user can press Later and keep working."""
  from maya import cmds as mc
  if (runtime_available() and gnm_repo_available()
      and not gallery_available()):
    if _gallery_deferred["later"]:
      return True
    ans = mc.confirmDialog(
        title="GNM shape images", icon="question",
        message="The full-quality shape images (slider thumbnails) are not "
                "rendered on this install yet.\nRendering takes ~5 min, "
                "once. Render now, or later from GNM > Shape Gallery?",
        button=["Render now", "Later"], defaultButton="Later",
        cancelButton="Later", dismissString="Later")
    if ans != "Render now":
      _gallery_deferred["later"] = True
      return True
  missing = []
  if not runtime_available():
    missing.append(
        "Python %d.%d runtime (~70 MB download)%s" % (
            MIN_PY[0], MIN_PY[1],
            " — replaces the older one the current GNM can't run on"
            if runtime_outdated() else ""))
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
