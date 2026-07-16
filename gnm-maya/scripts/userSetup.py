"""Maya startup hook: add a 'GNM' menu to the main window.

Placed on PYTHONPATH by GNM.mod, so Maya auto-executes it at launch. Uses
executeDeferred so the main window exists before we build the menu, and stays
silent in batch/mayapy (no UI) sessions.
"""

import maya.utils
from maya import cmds as mc


def _menu_image(name):
  """Cached PNG path for a menu icon, or "" if it can't be produced."""
  try:
    from gnm_maya.ui import icons
    return icons.image_file(name, 22)
  except Exception:
    return ""


def _item(label, command, icon=None, annotation="", **kw):
  """Add a GNM menu item, attaching a bundled icon + tooltip when available."""
  kwargs = dict(label=label, parent="gnmMenu", command=command)
  if annotation:
    kwargs["annotation"] = annotation
  img = _menu_image(icon) if icon else ""
  if img:
    kwargs["image"] = img
  kwargs.update(kw)
  return mc.menuItem(**kwargs)


def build_menu():
  if mc.about(batch=True):
    return  # no UI in mayapy/batch
  gmain = "MayaWindow"
  if not mc.window(gmain, exists=True):
    return
  if mc.menu("gnmMenu", exists=True):
    mc.deleteUI("gnmMenu")
  mc.menu("gnmMenu", label="GNM", parent=gmain, tearOff=True)
  _item("GNM Head Panel...", "import gnm_maya; gnm_maya.show_ui()",
        icon="tune",
        annotation="Open the GNM Head control panel (sliders, semantic, rig).")
  mc.menuItem(divider=True, parent="gnmMenu")
  _item("Quick: Random Head", "import gnm_maya; gnm_maya.generate_head()",
        icon="dice",
        annotation="Create a new head with random identity coefficients.")
  _item("Quick: Template Head",
        "import gnm_maya; gnm_maya.generate_template()", icon="face",
        annotation="Create a new head at the neutral (average) template.")
  mc.menuItem(divider=True, parent="gnmMenu")
  _item("Presets...",
        "from gnm_maya.ui import tools as ui_tools; ui_tools.preset_browser()",
        icon="bookmark",
        annotation="Browse, save and load saved head presets.")
  _item("Generate Crowd...",
        "from gnm_maya.ui import tools as ui_tools; ui_tools.crowd_dialog()",
        icon="groups",
        annotation="Populate the scene with many varied heads on a grid.")
  _item("Export Selected Rig (FBX)",
        "from gnm_maya.ui import tools as ui_tools; ui_tools.export_selected_fbx()",
        icon="download",
        annotation="Export the selected baked GNM rig to an FBX file.")
  _item("Landmarks: Create",
        "from gnm_maya.ui import tools as ui_tools; ui_tools.create_landmarks()",
        icon="scatter",
        annotation="Add 68 editable facial landmark locators to the head.")
  _item("Landmarks: Update",
        "from gnm_maya.ui import tools as ui_tools; ui_tools.update_landmarks()",
        icon="scatter",
        annotation="Re-snap the landmark locators to the current head shape.")
  _item("Landmarks: Toggle L/R Mirror",
        "from gnm_maya.ui import tools as ui_tools; ui_tools.toggle_landmark_mirror()",
        icon="shuffle",
        annotation="Toggle mirrored left/right editing of the landmarks.")
  _item("Landmarks: Fit Head to Locators",
        "from gnm_maya.ui import tools as ui_tools; ui_tools.fit_head_to_landmarks()",
        icon="face",
        annotation="Solve identity so the head's landmarks match the "
                   "edited locators.")
  _item("Landmarks: Toggle Live Fit (drag-to-sculpt)",
        "from gnm_maya.ui import tools as ui_tools; ui_tools.toggle_live_landmark_fit()",
        icon="tune",
        annotation="Refit the head automatically every time a landmark "
                   "locator drag ends.")
  _item("Shape Gallery (min/max images)",
        "from gnm_maya.ui import tools as ui_tools; ui_tools.open_shape_gallery()",
        icon="grid",
        annotation="Open the browsable gallery of what each mode does.")
  mc.menuItem(divider=True, parent="gnmMenu")
  _item("Add Shelf Button", "import gnm_maya; gnm_maya.add_shelf_button()",
        icon="add_box",
        annotation="Add a GNM Head button to the current shelf.")
  _item("Check for GNM Model Updates...",
        "import gnm_maya; gnm_maya.check_updates()", icon="update",
        annotation="Check GitHub for a newer vendored google/GNM model.")
  _item("Check for gnm-maya Tool Updates...",
        "import gnm_maya; gnm_maya.check_tool_updates()", icon="update",
        annotation="Check GitHub for a newer version of this tool.")
  _item("Run GUI Test", "import gnm_maya; gnm_maya.run_gui_test()", icon="bug",
        annotation="Run the in-Maya panel smoke test (self-cleans after).")
  _item("Licenses...", "import gnm_maya; gnm_maya.show_licenses()",
        icon="description",
        annotation="View GNM, MIT and third-party licence texts.")


try:
  maya.utils.executeDeferred(build_menu)
except Exception as e:
  import sys
  sys.stderr.write("GNM menu setup failed: %s\n" % e)
