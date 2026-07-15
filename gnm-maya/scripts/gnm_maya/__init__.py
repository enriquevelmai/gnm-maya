"""GNM-Maya: generate Google GNM head meshes inside Autodesk Maya.

The heavy GNM model runs in a self-contained portable Python runtime shipped
with this module (``<module>/runtime``); Maya only reads the resulting mesh and
builds it natively via OpenMaya. Nothing here imports numpy, so it is safe under
mayapy.

Public entry points:
    generate_head(...)    -> builds a random head, returns a GnmHead controller
    generate_template()   -> builds the neutral template head
    show_ui()             -> opens the tabbed slider panel
    show_licenses()       -> opens the bundled-licenses viewer
"""

import logging as _logging

from gnm_maya.api import generate_head, generate_template

__all__ = ["generate_head", "generate_template", "show_ui", "show_licenses",
           "add_shelf_button", "check_updates", "run_gui_test", "logger"]

__version__ = "1.0.0"


def _configure_logger():
  """One INFO stream handler so actions surface in Maya's Script Editor."""
  lg = _logging.getLogger("gnm_maya")
  if not lg.handlers:
    h = _logging.StreamHandler()
    h.setFormatter(_logging.Formatter("[GNM] %(levelname)s: %(message)s"))
    lg.addHandler(h)
    lg.setLevel(_logging.INFO)
    lg.propagate = False
  return lg


logger = _configure_logger()


def show_ui():
  """Lazy import so importing the package never requires Qt."""
  from gnm_maya import ui
  return ui.show()


def show_licenses():
  """Open a dialog listing this module's and all bundled licenses."""
  from gnm_maya import licenses
  return licenses.show()


def add_shelf_button(shelf=None):
  """Add a 'GNM Head' button to the active (or named) Maya shelf."""
  from gnm_maya import shelf as _shelf
  return _shelf.add_shelf_button(shelf)


def check_updates():
  """Check google/GNM for updates and offer to download them."""
  from gnm_maya import updater
  return updater.show_update_dialog()


def run_gui_test():
  """Execute the GUI smoke test in this Maya session (opens the panel)."""
  import os
  from gnm_maya import config
  path = os.path.join(config.MODULE_ROOT, "tests", "gui_smoke_test.py")
  with open(path) as f:
    code = f.read()
  namespace = {"__file__": path, "__name__": "__gnm_gui_test__"}
  exec(compile(code, path, "exec"), namespace)
  return namespace.get("_PANEL")
