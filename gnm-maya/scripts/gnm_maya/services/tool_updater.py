"""Check GitHub for updates to THIS tool (gnm-maya) and self-update in place.

Distinct from ``updater.py``, which tracks the vendored google/GNM model repo.
This tracks the enriquevelmai/gnm-maya tool itself: its own Python code,
README/NOTICE, and the shape gallery docs. The installed version is recorded
in ``TOOL_VERSION.json`` at the module root; update = download the repo zip
from GitHub and sync its ``gnm-maya/`` subfolder onto the live install.

Two update channels (chosen in the update dialog, saved in TOOL_VERSION.json):

* ``release`` (default) — follows tagged GitHub Releases (e.g. v1.0.0).
  Stable: only versions the author explicitly published.
* ``dev`` — follows the tip of the ``master`` branch by commit id. Gets
  fixes as soon as they are pushed, before they are packaged in a release.

The separately-managed ``runtime/`` and ``external/gnm_repo/`` (and
``external/GNM_version.json``) are NEVER touched by this updater — they are
not even present in the tracked repo zip, since both are gitignored and
downloaded by their own bootstrap/updater flows.

Unlike the GNM model (which lives in a subprocess and reloads automatically),
this tool's own code runs directly in Maya's Python process and is cached in
``sys.modules`` once imported — updating the files on disk does NOT change
already-running code. **Restarting Maya is required** for a tool update to
take effect; the post-update dialog makes this the recommended action.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import tempfile
import urllib.request
import zipfile

from gnm_maya.core import config

logger = logging.getLogger(__name__)

REPO = "enriquevelmai/gnm-maya"
BRANCH = "master"
_UA = {"User-Agent": "gnm-maya-tool-updater"}

CHANNEL_RELEASE = "release"  # follow tagged GitHub Releases (default)
CHANNEL_DEV = "dev"          # follow master-branch commits

_VERSION_FILE = os.path.join(config.MODULE_ROOT, "TOOL_VERSION.json")

# Subfolders that are entirely tool-owned: replaced wholesale (handles
# renamed/deleted files correctly, unlike a plain overlay copy). The runtime
# regenerates icons/_cache on demand, so shipping the SVGs is enough.
_REPLACE_DIRS = ("scripts", "tests", "docs", "icons")

# external/ mixes tool code (*.py) with the separately-managed gnm_repo/ and
# GNM_version.json — only the *.py files are synced (added/updated/removed).
_EXTERNAL_PRESERVE = ("gnm_repo", "GNM_version.json", "__pycache__")


def short(version):
  """Human label for a version: release tags as-is, commit shas truncated."""
  v = version or "unknown"
  return v if len(v) <= 12 else v[:8]


def installed_info():
  try:
    with open(_VERSION_FILE) as f:
      return json.load(f)
  except Exception:
    return {}


def get_channel():
  ch = installed_info().get("channel")
  return ch if ch in (CHANNEL_RELEASE, CHANNEL_DEV) else CHANNEL_RELEASE


def set_channel(channel):
  """Persist the update channel in TOOL_VERSION.json (keeps other fields)."""
  if channel not in (CHANNEL_RELEASE, CHANNEL_DEV):
    raise ValueError("unknown channel: %r" % channel)
  info = installed_info()
  info.setdefault("repo", REPO)
  info["channel"] = channel
  with open(_VERSION_FILE, "w") as f:
    json.dump(info, f, indent=2)


def latest_commit(timeout=15):
  url = "https://api.github.com/repos/%s/commits/%s" % (REPO, BRANCH)
  req = urllib.request.Request(url, headers=_UA)
  with urllib.request.urlopen(req, timeout=timeout) as r:
    data = json.load(r)
  return {"version": data["sha"],
          "date": data["commit"]["committer"]["date"],
          "zip_url": "https://github.com/%s/archive/refs/heads/%s.zip"
                     % (REPO, BRANCH)}


def latest_release(timeout=15):
  url = "https://api.github.com/repos/%s/releases/latest" % REPO
  req = urllib.request.Request(url, headers=_UA)
  with urllib.request.urlopen(req, timeout=timeout) as r:
    data = json.load(r)
  tag = data["tag_name"]
  return {"version": tag,
          "date": (data.get("published_at") or "")[:10] or None,
          "zip_url": "https://github.com/%s/archive/refs/tags/%s.zip"
                     % (REPO, tag)}


def check():
  """Return dict with installed/latest version+date and update_available.

  ``release`` channel compares release tags; ``dev`` compares commit shas.
  If this install has no recorded version (e.g. an older download from
  before this feature existed), the update is reported as available so the
  user can bring their copy fully up to date.
  """
  channel = get_channel()
  inst = installed_info()
  installed = inst.get("tag") if channel == CHANNEL_RELEASE else inst.get("sha")
  latest = latest_release() if channel == CHANNEL_RELEASE else latest_commit()
  return {
      "channel": channel,
      "installed_sha": installed,          # generic "version" (tag or sha);
      "installed_date": inst.get("date"),  # _sha keys kept for the shared
      "latest_sha": latest["version"],     # async-check helper in ui.panel
      "latest_date": latest["date"],
      "zip_url": latest["zip_url"],
      "update_available": installed != latest["version"],
  }


def _replace_dir(src_dir, dst_dir):
  """Wholesale directory replace: rename old aside, move new in, drop old."""
  if not os.path.isdir(src_dir):
    return  # nothing of this kind shipped upstream; leave the local one alone
  old = dst_dir + "_old"
  if os.path.isdir(old):
    shutil.rmtree(old, ignore_errors=True)
  if os.path.isdir(dst_dir):
    os.rename(dst_dir, old)
  shutil.move(src_dir, dst_dir)
  shutil.rmtree(old, ignore_errors=True)


def _sync_external_py(src_external, dst_external):
  """Sync loose *.py files directly under external/, add/update/remove.

  Never touches gnm_repo/ or GNM_version.json (not *.py, so a plain glob is
  naturally safe), preserving the separately-managed vendored model.
  """
  os.makedirs(dst_external, exist_ok=True)
  src_names = set()
  if os.path.isdir(src_external):
    for fname in os.listdir(src_external):
      if fname.endswith(".py") and fname not in _EXTERNAL_PRESERVE:
        src_names.add(fname)
        shutil.copy2(os.path.join(src_external, fname),
                    os.path.join(dst_external, fname))
  for fname in list(os.listdir(dst_external)):
    if (fname.endswith(".py") and fname not in _EXTERNAL_PRESERVE
        and fname not in src_names):
      os.remove(os.path.join(dst_external, fname))  # removed upstream


def _sync_top_level(src_root, dst_root):
  """Overwrite loose top-level files (README, NOTICE, LICENSE, installers).

  Overlay-only (no deletion) since this is a small, well-known set and the
  risk of an overlay leaving one stale file is far lower than accidentally
  deleting something unrelated a user placed at the module root.
  """
  for entry in os.listdir(src_root):
    src_path = os.path.join(src_root, entry)
    if os.path.isdir(src_path) or entry.startswith("."):
      continue  # directories handled separately; dotfiles left alone
    shutil.copy2(src_path, os.path.join(dst_root, entry))


def download_and_install(timeout=180):
  """Download the channel's repo zip and sync gnm-maya/ onto this install."""
  if not os.access(config.MODULE_ROOT, os.W_OK):
    raise RuntimeError("Module folder is not writable: %s" % config.MODULE_ROOT)

  channel = get_channel()
  latest = latest_release() if channel == CHANNEL_RELEASE else latest_commit()
  url = latest["zip_url"]
  logger.info("Downloading %s ...", url)
  req = urllib.request.Request(url, headers=_UA)
  with urllib.request.urlopen(req, timeout=timeout) as r:
    blob = r.read()

  tmp = tempfile.mkdtemp(prefix="gnm_tool_update_")
  try:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
      z.extractall(tmp)
    roots = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
    if not roots:
      raise RuntimeError("Downloaded archive was empty.")
    repo_root = os.path.join(tmp, roots[0])
    module_src = os.path.join(repo_root, "gnm-maya")
    if not os.path.isdir(os.path.join(module_src, "scripts", "gnm_maya")):
      raise RuntimeError("Downloaded archive is not a gnm-maya repo.")

    for name in _REPLACE_DIRS:
      _replace_dir(os.path.join(module_src, name),
                  os.path.join(config.MODULE_ROOT, name))
    _sync_external_py(os.path.join(module_src, "external"), config.EXTERNAL_DIR)
    _sync_top_level(module_src, config.MODULE_ROOT)
  finally:
    shutil.rmtree(tmp, ignore_errors=True)

  stamp = {"repo": REPO, "branch": BRANCH, "channel": channel,
           "tag": None, "sha": None, "date": latest["date"]}
  stamp["tag" if channel == CHANNEL_RELEASE else "sha"] = latest["version"]
  with open(_VERSION_FILE, "w") as f:
    json.dump(stamp, f, indent=2)

  try:
    from gnm_maya.core import worker
    worker.shutdown_worker()
  except Exception:
    pass

  logger.info("Updated gnm-maya tool to %s (%s)", short(latest["version"]),
              latest["date"])
  return latest


def _channel_dialog():
  """Let the user pick the update channel; returns True if it changed."""
  from maya import cmds as mc

  current = get_channel()
  ans = mc.confirmDialog(
      title="gnm-maya update channel",
      message=("Choose which updates to follow (current: %s).\n\n"
               "Releases — stable, published versions only (recommended).\n"
               "Dev — the latest commits on master, before they are\n"
               "packaged in a release." % current),
      button=["Releases (stable)", "Dev (latest commits)", "Cancel"],
      defaultButton="Releases (stable)",
      cancelButton="Cancel", dismissString="Cancel")
  picked = {"Releases (stable)": CHANNEL_RELEASE,
            "Dev (latest commits)": CHANNEL_DEV}.get(ans)
  if picked is None or picked == current:
    return False
  set_channel(picked)
  logger.info("gnm-maya update channel set to '%s'", picked)
  return True


def show_update_dialog(parent=None):
  """Interactive check + optional download, via Maya confirm dialogs."""
  from maya import cmds as mc

  channel = get_channel()
  try:
    info = check()
  except Exception as e:
    logger.exception("tool update check failed")
    mc.confirmDialog(title="gnm-maya Update", icon="critical",
                     message="Could not check for updates:\n%s" % e,
                     button=["OK"])
    return

  if not info["update_available"]:
    ans = mc.confirmDialog(
        title="gnm-maya Update",
        message="You already have the latest gnm-maya tool.\n\nInstalled: "
                "%s (%s)\nChannel: %s"
                % (short(info["installed_sha"]),
                   info["installed_date"] or "?", channel),
        button=["OK", "Change Channel..."], defaultButton="OK",
        cancelButton="OK", dismissString="OK")
    if ans == "Change Channel..." and _channel_dialog():
      show_update_dialog(parent)  # re-check against the new channel
    return

  ans = mc.confirmDialog(
      title="gnm-maya Update available",
      message=("A newer gnm-maya tool is available on the '%s' channel.\n\n"
               "Installed: %s (%s)\nLatest:    %s (%s)\n\n"
               "Download and install now? A Maya restart is required "
               "afterwards for the new code to take effect."
               % (channel,
                  short(info["installed_sha"]), info["installed_date"] or "?",
                  short(info["latest_sha"]), info["latest_date"])),
      button=["Download", "Change Channel...", "Cancel"],
      defaultButton="Download",
      cancelButton="Cancel", dismissString="Cancel")
  if ans == "Change Channel...":
    if _channel_dialog():
      show_update_dialog(parent)  # re-check against the new channel
    return
  if ans != "Download":
    return

  mc.waitCursor(state=True)
  try:
    latest = download_and_install()
  except Exception as e:
    logger.exception("tool update download failed")
    mc.waitCursor(state=False)
    mc.confirmDialog(title="gnm-maya Update", icon="critical",
                     message="Update failed:\n%s" % e, button=["OK"])
    return
  mc.waitCursor(state=False)
  _post_update_dialog(latest)


def _post_update_dialog(latest):
  """Shown after a successful download: RESTART is the recommended action.

  Unlike the GNM model (an external subprocess that reloads on next use),
  this tool's own code is already imported into Maya's Python process, so a
  restart is required — not just recommended — for the update to apply.
  """
  from maya import cmds as mc
  from gnm_maya.services import updater  # reuse restart_maya

  msg = (
      "gnm-maya updated to %s (%s).\n\n"
      "This tool's code is already loaded in this Maya session, so the "
      "update will NOT take effect until you restart Maya.\n\n"
      "Restart now?" % (short(latest["version"]), latest["date"]))
  ans = mc.confirmDialog(
      title="gnm-maya updated", message=msg,
      button=["Restart Maya", "Later"],
      defaultButton="Restart Maya", cancelButton="Later", dismissString="Later")
  if ans == "Restart Maya":
    updater.restart_maya()
