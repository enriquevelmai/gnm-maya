"""Drag-and-drop installer for the GNM Maya module.

Usage: drag this file from Explorer into a running Maya viewport. Maya calls
``onMayaDroppedPythonFile`` below, which registers the module (in place, no
copying) and loads it into the current session — a GNM menu appears
immediately, and the module auto-loads on every future launch.

It writes a ``GNM.mod`` into your user modules directory pointing at wherever
you extracted this module, so keep the folder where it is after installing.

Lives inside the ``gnm-maya`` module folder, so this file's own directory is
the module root.
"""

from __future__ import annotations

import os


def _this_dir():
  # __file__ is normally defined during Maya's drag-drop exec; fall back if not.
  try:
    return os.path.dirname(os.path.abspath(__file__))
  except NameError:
    import inspect
    return os.path.dirname(os.path.abspath(inspect.getsourcefile(lambda: 0)))


def _write_mod(module_root, modules_dir):
  """Write a GNM.mod pointing at the absolute module location."""
  if not os.path.isdir(modules_dir):
    os.makedirs(modules_dir)
  mod_path = os.path.join(modules_dir, "GNM.mod")
  # Absolute module location so it works regardless of where the repo lives.
  content = (
      "+ GNM 1.0 %s\n"
      "scripts: scripts\n"
      "PYTHONPATH +:= scripts\n" % module_root.replace("\\", "/")
  )
  with open(mod_path, "w") as f:
    f.write(content)
  return mod_path


def onMayaDroppedPythonFile(*args):
  import maya.cmds as cmds

  module_root = _this_dir()  # this file lives in the module root
  scripts_dir = os.path.join(module_root, "scripts")

  if not os.path.isdir(scripts_dir):
    cmds.confirmDialog(
        title="GNM install failed",
        message="Could not find a 'scripts' folder next to this installer.\n"
                "Keep drag_and_drop_install.py inside the gnm-maya folder.",
        button=["OK"])
    return

  # Maya user app dir, e.g. .../Documents/maya/  -> modules subfolder.
  user_app = cmds.internalVar(userAppDir=True)
  modules_dir = os.path.join(user_app, "modules")

  try:
    mod_path = _write_mod(module_root, modules_dir)
  except Exception as e:
    cmds.confirmDialog(title="GNM install failed",
                       message="Could not write module file:\n%s" % e,
                       button=["OK"])
    return

  # Load into the current session without a restart.
  import sys
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
    # the GNM model repo are downloaded on demand.
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

  msg = "GNM module registered:\n%s\n\n" % mod_path
  if loaded:
    msg += ("Loaded into this session — see the 'GNM' menu and the new 'GNM'\n"
            "shelf button. It will also auto-load on future Maya launches.")
  else:
    msg += ("Registered, but live-load failed (%s).\n"
            "Restart Maya to finish loading." % load_err)
  cmds.confirmDialog(title="GNM installed", message=msg, button=["OK"])
