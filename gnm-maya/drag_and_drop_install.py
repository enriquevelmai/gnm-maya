"""Drag-and-drop installer for the GNM Maya module.

Usage: drag this file from Explorer into a running Maya viewport. Maya calls
``onMayaDroppedPythonFile`` below, which COPIES the module into your Maya
``modules`` folder, registers it, and loads it into the current session — a
GNM menu appears immediately, and the module auto-loads on every future
launch.

Because the install is a copy (``~/Documents/maya/modules/gnm-maya``), you can
**delete the downloaded zip and the extracted folder afterwards** — nothing
keeps pointing at them. Any already-downloaded runtime / GNM model inside the
extracted folder is carried along so it isn't re-downloaded.

Lives inside the ``gnm-maya`` module folder, so this file's own directory is
the module root.
"""

from __future__ import annotations

import os
import shutil


def _this_dir():
  # __file__ is normally defined during Maya's drag-drop exec; fall back if not.
  try:
    return os.path.dirname(os.path.abspath(__file__))
  except NameError:
    import inspect
    return os.path.dirname(os.path.abspath(inspect.getsourcefile(lambda: 0)))


def _write_mod(module_root, modules_dir):
  """Write a GNM.mod pointing at the installed module location."""
  if not os.path.isdir(modules_dir):
    os.makedirs(modules_dir)
  mod_path = os.path.join(modules_dir, "GNM.mod")
  content = (
      "+ GNM 1.0 %s\n"
      "scripts: scripts\n"
      "PYTHONPATH +:= scripts\n" % module_root.replace("\\", "/")
  )
  with open(mod_path, "w") as f:
    f.write(content)
  return mod_path


def _copy_module(src_root, dst_root):
  """Copy the module into the modules folder (skipping caches/scratch)."""
  ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "_out*", "_sess*",
                                  "_buildtest", ".git")
  shutil.copytree(src_root, dst_root, ignore=ignore, dirs_exist_ok=True)


def onMayaDroppedPythonFile(*args):
  import maya.cmds as cmds

  src_root = _this_dir()  # this file lives in the module root
  if not os.path.isdir(os.path.join(src_root, "scripts")):
    cmds.confirmDialog(
        title="GNM install failed",
        message="Could not find a 'scripts' folder next to this installer.\n"
                "Keep drag_and_drop_install.py inside the gnm-maya folder.",
        button=["OK"])
    return

  # Maya user app dir, e.g. .../Documents/maya/  -> modules subfolder.
  user_app = cmds.internalVar(userAppDir=True)
  modules_dir = os.path.join(user_app, "modules")
  install_root = os.path.join(modules_dir, "gnm-maya")

  already_there = os.path.normcase(os.path.normpath(src_root)) == \
      os.path.normcase(os.path.normpath(install_root))

  if not already_there:
    if os.path.isdir(install_root):
      ans = cmds.confirmDialog(
          title="GNM install",
          message="A GNM install already exists at:\n%s\n\nReplace its "
                  "code/docs with this copy? (Downloaded runtime/model data "
                  "there is kept.)" % install_root,
          button=["Replace", "Cancel"], defaultButton="Replace",
          cancelButton="Cancel", dismissString="Cancel")
      if ans != "Replace":
        return
    try:
      _copy_module(src_root, install_root)
    except Exception as e:
      cmds.confirmDialog(title="GNM install failed",
                         message="Could not copy the module into:\n%s\n\n%s"
                                 % (install_root, e),
                         button=["OK"])
      return

  try:
    mod_path = _write_mod(install_root, modules_dir)
  except Exception as e:
    cmds.confirmDialog(title="GNM install failed",
                       message="Could not write module file:\n%s" % e,
                       button=["OK"])
    return

  # Load into the current session without a restart — from the INSTALLED copy.
  import sys
  scripts_dir = os.path.join(install_root, "scripts")
  if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
  try:
    import importlib
    import gnm_maya  # noqa: F401  (validates the package imports)
    userSetup = importlib.import_module("userSetup")
    userSetup.build_menu()
    try:
      gnm_maya.add_shelf_button()  # best-effort convenience
    except Exception:
      pass
    # First-run bootstrap: the repo ships code only; the portable runtime and
    # the GNM model repo are downloaded on demand (into the installed copy).
    try:
      from gnm_maya import bootstrap
      if not bootstrap.all_available():
        bootstrap.ensure_all_with_dialog()
    except Exception:
      pass  # opening the panel re-offers the bootstrap if still missing
    loaded = True
  except Exception as e:
    loaded = False
    load_err = str(e)

  msg = "GNM installed to:\n%s\n(module file: %s)\n\n" % (install_root,
                                                          mod_path)
  if not already_there:
    msg += ("This is a self-contained copy — you can now DELETE the "
            "downloaded zip and the extracted folder.\n\n")
  if loaded:
    msg += ("Loaded into this session — see the 'GNM' menu and the new 'GNM'\n"
            "shelf button. It will also auto-load on future Maya launches.")
  else:
    msg += ("Installed, but live-load failed (%s).\n"
            "Restart Maya to finish loading." % load_err)
  cmds.confirmDialog(title="GNM installed", message=msg, button=["OK"])
