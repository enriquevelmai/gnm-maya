"""Maya startup hook: add a 'GNM' menu to the main window.

Placed on PYTHONPATH by GNM.mod, so Maya auto-executes it at launch. Uses
executeDeferred so the main window exists before we build the menu, and stays
silent in batch/mayapy (no UI) sessions.
"""

import maya.utils
import maya.cmds as cmds


def build_menu():
  if cmds.about(batch=True):
    return  # no UI in mayapy/batch
  gmain = "MayaWindow"
  if not cmds.window(gmain, exists=True):
    return
  if cmds.menu("gnmMenu", exists=True):
    cmds.deleteUI("gnmMenu")
  cmds.menu("gnmMenu", label="GNM", parent=gmain, tearOff=True)
  cmds.menuItem(
      label="GNM Head Panel...",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.show_ui()",
  )
  cmds.menuItem(divider=True, parent="gnmMenu")
  cmds.menuItem(
      label="Quick: Random Head",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.generate_head()",
  )
  cmds.menuItem(
      label="Quick: Template Head",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.generate_template()",
  )
  cmds.menuItem(divider=True, parent="gnmMenu")
  cmds.menuItem(
      label="Presets...",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.preset_browser()",
  )
  cmds.menuItem(
      label="Generate Crowd...",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.crowd_dialog()",
  )
  cmds.menuItem(
      label="Export Selected Rig (FBX)",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.export_selected_fbx()",
  )
  cmds.menuItem(
      label="Landmarks: Create",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.create_landmarks()",
  )
  cmds.menuItem(
      label="Landmarks: Update",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.update_landmarks()",
  )
  cmds.menuItem(
      label="Landmarks: Toggle L/R Mirror",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.toggle_landmark_mirror()",
  )
  cmds.menuItem(
      label="Landmarks: Fit Head to Locators",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.fit_head_to_landmarks()",
  )
  cmds.menuItem(
      label="Shape Gallery (min/max images)",
      parent="gnmMenu",
      command="from gnm_maya import ui_tools; ui_tools.open_shape_gallery()",
  )
  cmds.menuItem(divider=True, parent="gnmMenu")
  cmds.menuItem(
      label="Add Shelf Button",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.add_shelf_button()",
  )
  cmds.menuItem(
      label="Check for Updates...",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.check_updates()",
  )
  cmds.menuItem(
      label="Run GUI Test",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.run_gui_test()",
  )
  cmds.menuItem(
      label="Licenses...",
      parent="gnmMenu",
      command="import gnm_maya; gnm_maya.show_licenses()",
  )


try:
  maya.utils.executeDeferred(build_menu)
except Exception as e:
  import sys
  sys.stderr.write("GNM menu setup failed: %s\n" % e)
