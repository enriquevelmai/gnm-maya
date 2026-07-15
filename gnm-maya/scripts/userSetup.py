"""Maya startup hook: add a 'GNM' menu to the main window.

Placed on PYTHONPATH by GNM.mod, so Maya auto-executes it at launch. Uses
executeDeferred so the main window exists before we build the menu, and stays
silent in batch/mayapy (no UI) sessions.
"""

import maya.utils
from maya import cmds as mc


def build_menu():
  if mc.about(batch=True):
    return  # no UI in mayapy/batch
  gmain = "MayaWindow"
  if not mc.window(gmain, exists=True):
    return
  if mc.menu("gnmMenu", exists=True):
    mc.deleteUI("gnmMenu")
  mc.menu("gnmMenu", label="GNM", parent=gmain, tearOff=True)
  mc.menuItem(
      label="GNM Head Panel...",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.show_ui()",
  )
  mc.menuItem(divider=True, parent="gnmMenu")
  mc.menuItem(
      label="Quick: Random Head",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.generate_head()",
  )
  mc.menuItem(
      label="Quick: Template Head",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.generate_template()",
  )
  mc.menuItem(divider=True, parent="gnmMenu")
  mc.menuItem(
      label="Presets...",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.preset_browser()",
  )
  mc.menuItem(
      label="Generate Crowd...",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.crowd_dialog()",
  )
  mc.menuItem(
      label="Export Selected Rig (FBX)",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.export_selected_fbx()",
  )
  mc.menuItem(
      label="Landmarks: Create",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.create_landmarks()",
  )
  mc.menuItem(
      label="Landmarks: Update",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.update_landmarks()",
  )
  mc.menuItem(
      label="Landmarks: Toggle L/R Mirror",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.toggle_landmark_mirror()",
  )
  mc.menuItem(
      label="Landmarks: Fit Head to Locators",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.fit_head_to_landmarks()",
  )
  mc.menuItem(
      label="Shape Gallery (min/max images)",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.open_shape_gallery()",
  )
  mc.menuItem(divider=True, parent="gnmMenu")
  mc.menuItem(
      label="Add Shelf Button",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.add_shelf_button()",
  )
  mc.menuItem(
      label="Check for GNM Model Updates...",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.check_updates()",
  )
  mc.menuItem(
      label="Check for gnm-maya Tool Updates...",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.check_tool_updates()",
  )
  mc.menuItem(
      label="Run GUI Test",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.run_gui_test()",
  )
  mc.menuItem(
      label="Licenses...",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.show_licenses()",
  )


try:
  maya.utils.executeDeferred(build_menu)
except Exception as e:
  import sys
  sys.stderr.write("GNM menu setup failed: %s\n" % e)
