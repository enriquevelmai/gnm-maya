"""Check the upstream google/GNM repo for updates and refresh the vendored copy.

The vendored repo lives at ``external/gnm_repo`` and its installed commit is
recorded in ``external/GNM_version.json``. Update = download the branch zip from
GitHub, replace ``gnm_repo``, and rewrite the version file. Uses only stdlib
(urllib/json/zipfile), so it runs directly under mayapy.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

from gnm_maya import config

logger = logging.getLogger(__name__)

REPO = "google/GNM"
BRANCH = "main"
_UA = {"User-Agent": "gnm-maya-updater"}

_VERSION_FILE = os.path.join(config.EXTERNAL_DIR, "GNM_version.json")
_VENDOR_DIR = os.path.join(config.EXTERNAL_DIR, "gnm_repo")


def short(sha):
  return (sha or "unknown")[:8]


def installed_info():
  try:
    with open(_VERSION_FILE) as f:
      return json.load(f)
  except Exception:
    return {}


def latest_commit(timeout=15):
  url = "https://api.github.com/repos/%s/commits/%s" % (REPO, BRANCH)
  req = urllib.request.Request(url, headers=_UA)
  with urllib.request.urlopen(req, timeout=timeout) as r:
    data = json.load(r)
  return {"sha": data["sha"],
          "date": data["commit"]["committer"]["date"]}


def check():
  """Return dict with installed/latest sha+date and update_available."""
  inst = installed_info()
  latest = latest_commit()
  return {
      "installed_sha": inst.get("sha"),
      "installed_date": inst.get("date"),
      "latest_sha": latest["sha"],
      "latest_date": latest["date"],
      "update_available": inst.get("sha") != latest["sha"],
  }


def download_and_install(timeout=180):
  """Download the branch zip and replace the vendored repo. Returns latest info."""
  if not os.access(config.EXTERNAL_DIR, os.W_OK):
    raise RuntimeError("Module folder is not writable: %s" % config.EXTERNAL_DIR)

  url = "https://github.com/%s/archive/refs/heads/%s.zip" % (REPO, BRANCH)
  logger.info("Downloading %s ...", url)
  req = urllib.request.Request(url, headers=_UA)
  with urllib.request.urlopen(req, timeout=timeout) as r:
    blob = r.read()

  tmp = tempfile.mkdtemp(prefix="gnm_update_")
  try:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
      z.extractall(tmp)
    roots = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
    if not roots:
      raise RuntimeError("Downloaded archive was empty.")
    src = os.path.join(tmp, roots[0])
    if not os.path.isdir(os.path.join(src, "gnm")):
      raise RuntimeError("Downloaded archive is not a GNM repo (no gnm/).")

    # Replace the vendored repo atomically-ish: move new in, then drop old.
    old = _VENDOR_DIR + "_old"
    if os.path.isdir(old):
      shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(_VENDOR_DIR):
      os.rename(_VENDOR_DIR, old)
    shutil.move(src, _VENDOR_DIR)
    shutil.rmtree(old, ignore_errors=True)
  finally:
    shutil.rmtree(tmp, ignore_errors=True)

  latest = latest_commit()
  with open(_VERSION_FILE, "w") as f:
    json.dump({"repo": REPO, "branch": BRANCH,
               "sha": latest["sha"], "date": latest["date"]}, f, indent=2)

  # Drop the resident model worker so the next use loads the new files.
  try:
    from gnm_maya import worker
    worker.shutdown_worker()
  except Exception:
    pass

  # A model update can change/add shapes: mark the local shape gallery stale
  # so the next panel open re-renders it (bootstrap.gallery_available()).
  try:
    from gnm_maya import bootstrap
    manifest_path = os.path.join(bootstrap.GALLERY_DIR, "manifest.json")
    with open(manifest_path) as f:
      m = json.load(f)
    m["stale"] = True
    with open(manifest_path, "w") as f:
      json.dump(m, f, indent=2)
    logger.info("Shape gallery marked stale (re-renders on next panel open).")
  except Exception:
    pass  # no gallery yet — nothing to invalidate

  logger.info("Updated GNM to %s (%s)", short(latest["sha"]), latest["date"])
  return latest


def show_update_dialog(parent=None):
  """Interactive check + optional download, via Maya confirm dialogs."""
  import maya.cmds as cmds

  try:
    info = check()
  except Exception as e:
    logger.exception("update check failed")
    cmds.confirmDialog(title="GNM Update",
                       message="Could not check for updates:\n%s" % e,
                       button=["OK"])
    return

  if not info["update_available"]:
    cmds.confirmDialog(
        title="GNM Update",
        message="You already have the latest GNM.\n\nVendored: %s (%s)"
                % (short(info["installed_sha"]), info["installed_date"] or "?"),
        button=["OK"])
    return

  ans = cmds.confirmDialog(
      title="GNM Update available",
      message=("A newer GNM is available.\n\n"
               "Installed: %s (%s)\nLatest:    %s (%s)\n\n"
               "Download (~40 MB) and install now?"
               % (short(info["installed_sha"]), info["installed_date"] or "?",
                  short(info["latest_sha"]), info["latest_date"])),
      button=["Download", "Cancel"], defaultButton="Download",
      cancelButton="Cancel", dismissString="Cancel")
  if ans != "Download":
    return

  cmds.waitCursor(state=True)
  try:
    latest = download_and_install()
  except Exception as e:
    logger.exception("update download failed")
    cmds.waitCursor(state=False)
    cmds.confirmDialog(title="GNM Update", message="Update failed:\n%s" % e,
                       button=["OK"])
    return
  cmds.waitCursor(state=False)
  _post_update_dialog(latest)


def _post_update_dialog(latest):
  """Shown after a successful download: offer restart + suggest running tests."""
  import maya.cmds as cmds

  msg = (
      "GNM updated to %s (%s).\n\n"
      "The model reloads automatically on next use, so a restart usually "
      "isn't required — but restarting Maya guarantees a clean state.\n\n"
      "Recommended: run the tests to verify the new version works "
      "(GNM ▸ Run GUI Test, or gnm-maya/tests/gui_smoke_test.py)."
      % (short(latest["sha"]), latest["date"]))
  ans = cmds.confirmDialog(
      title="GNM updated",
      message=msg,
      button=["Restart Maya", "Run GUI Test", "Done"],
      defaultButton="Run GUI Test", cancelButton="Done", dismissString="Done")
  if ans == "Restart Maya":
    restart_maya()
  elif ans == "Run GUI Test":
    try:
      from gnm_maya import run_gui_test
      run_gui_test()
    except Exception as e:
      logger.exception("running GUI test failed")
      cmds.confirmDialog(title="GUI Test", message="Could not run test:\n%s" % e,
                         button=["OK"])


def maya_binary():
  """Path to the running Maya executable, via MAYA_LOCATION."""
  loc = os.environ.get("MAYA_LOCATION")
  if not loc:
    return None
  exe = "maya.exe" if os.name == "nt" else "maya"
  path = os.path.join(loc, "bin", exe)
  return path if os.path.isfile(path) else None


def restart_maya():
  """Best-effort: launch a fresh Maya, then quit this one (handles unsaved)."""
  import maya.cmds as cmds

  binp = maya_binary()
  if not binp:
    cmds.confirmDialog(
        title="Restart Maya",
        message="Couldn't locate the Maya executable (MAYA_LOCATION unset).\n"
                "Please restart Maya manually.", button=["OK"])
    return

  if cmds.file(query=True, modified=True):
    ans = cmds.confirmDialog(
        title="Unsaved changes",
        message="Your scene has unsaved changes that may be lost on restart.",
        button=["Save & Restart", "Restart Anyway", "Cancel"],
        cancelButton="Cancel", dismissString="Cancel")
    if ans == "Cancel":
      return
    if ans == "Save & Restart":
      if not cmds.file(query=True, sceneName=True):
        cmds.confirmDialog(
            title="Save first",
            message="Scene is untitled — save it manually, then restart.",
            button=["OK"])
        return
      try:
        cmds.file(save=True)
      except Exception as e:
        cmds.confirmDialog(title="Save failed", message=str(e), button=["OK"])
        return

  try:
    subprocess.Popen([binp])
  except Exception as e:
    logger.exception("relaunch failed")
    cmds.confirmDialog(title="Restart Maya",
                       message="Could not launch a new Maya:\n%s" % e,
                       button=["OK"])
    return
  # Quit after the new instance has been spawned; force since save is handled.
  cmds.evalDeferred(lambda: cmds.quit(force=True))
