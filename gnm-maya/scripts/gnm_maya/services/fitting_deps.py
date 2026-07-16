"""On-demand installer for the photo-fitting dependencies.

MediaPipe + OpenCV weigh ~290 MB, so they are NOT shipped with the module
(everything else works offline out of the box). This installs them once into
the bundled runtime using the runtime's own pip. Version set mirrors what was
validated against the runtime's numpy 2.x: mediapipe 0.10.14 (bundles its
FaceMesh models; no network needed at fit time) with --no-deps plus an explicit
dependency list, protecting the already-installed numpy/absl.
"""

from __future__ import annotations

import logging
import os
import subprocess

from gnm_maya.core import config

logger = logging.getLogger(__name__)

# Validated against the runtime's numpy 2.4.6 — do not bump casually.
_PACKAGES = [
    "--no-deps", "mediapipe==0.10.14",
    "protobuf==4.25.9", "attrs", "flatbuffers",
    "opencv-python-headless", "matplotlib",
]


def available():
  """True if the fitting stack is importable from the runtime."""
  from gnm_maya.services import bootstrap
  return any(os.path.isdir(os.path.join(d, "mediapipe"))
             for d in bootstrap.site_packages_dirs())


def install():
  """Install the fitting deps into the runtime (~290 MB download). Blocking."""
  from gnm_maya.services import bootstrap
  py = config.venv_python()
  if os.name == "nt":
    # Embeddable runtime: install into its site-packages explicitly.
    site = os.path.join(config.MODULE_ROOT, "runtime", "Lib", "site-packages")
    cmd = [py, "-m", "pip", "install", "--target", site, *_PACKAGES]
  else:
    # Posix venv: pip already targets the venv's own site-packages.
    cmd = [py, "-m", "pip", "install", *_PACKAGES]
  logger.info("Installing photo-fitting deps: %s", " ".join(cmd))
  creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
  proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        universal_newlines=True, creationflags=creationflags)
  if proc.returncode != 0:
    raise RuntimeError("pip install failed:\n%s" % proc.stdout[-2000:])
  logger.info("Photo-fitting deps installed.")
  # The resident worker must restart to see the new packages.
  try:
    from gnm_maya.core import worker
    worker.shutdown_worker()
  except Exception:
    pass
  return True


def install_with_dialog():
  """Confirm + install behind a wait cursor; report via dialogs."""
  from maya import cmds as mc
  if available():
    mc.confirmDialog(title="GNM Photo Fitting",
                       message="Photo-fitting dependencies are already "
                               "installed.", button=["OK"])
    return True
  ans = mc.confirmDialog(
      title="GNM Photo Fitting",
      message="Photo fitting needs MediaPipe + OpenCV (~290 MB download,\n"
              "installed into the module's bundled runtime). Install now?",
      button=["Install", "Cancel"], defaultButton="Install",
      cancelButton="Cancel", dismissString="Cancel")
  if ans != "Install":
    return False
  from gnm_maya.ui.progress import MayaProgress
  try:
    with MayaProgress("Installing photo-fitting deps (~290 MB)",
                      maximum=2) as prog:
      prog.set(1, "Downloading + installing MediaPipe/OpenCV… (a few minutes)")
      install()
    msg = "Installed. 'Fit from Photo' is ready."
    ok = True
  except Exception as e:
    logger.exception("fitting deps install failed")
    msg = "Install failed:\n%s" % e
    ok = False
  mc.confirmDialog(title="GNM Photo Fitting", message=msg, button=["OK"],
                   icon="information" if ok else "critical")
  return ok
