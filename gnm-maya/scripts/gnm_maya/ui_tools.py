"""Small dialogs for the tool-box features: presets, crowd, FBX, landmarks.

Kept separate from the main panel (ui.py) so each stays lean. All of these
operate on the panel's head when one exists, else on a selected/any GNM head.
"""

from __future__ import annotations

import logging
import os

try:
  from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
  from PySide2 import QtWidgets, QtCore, QtGui

from maya import cmds as mc

from gnm_maya import api

logger = logging.getLogger(__name__)


def _run(dlg):
  return (getattr(dlg, "exec_", None) or dlg.exec)()


def _current_head():
  """The panel's head if open, else adopt a selected/any GNM head."""
  from gnm_maya import ui
  panel = getattr(ui, "_WINDOW", None)
  if panel is not None and getattr(panel, "head", None):
    try:
      if mc.objExists(panel.head.transform):
        return panel.head
    except Exception:
      pass
  heads = api.find_heads(selected_only=True) or api.find_heads()
  if heads:
    return api.GnmHead.adopt(heads[0])
  raise RuntimeError("No GNM head in the scene. Create one first (GNM menu).")


# --- presets ----------------------------------------------------------------

def preset_browser():
  from gnm_maya import presets

  dlg = QtWidgets.QDialog(None)
  dlg.setWindowTitle("GNM Presets")
  dlg.resize(420, 380)
  v = QtWidgets.QVBoxLayout(dlg)

  listw = QtWidgets.QListWidget()
  v.addWidget(listw, 1)

  def refresh():
    listw.clear()
    for p in presets.list_presets():
      it = QtWidgets.QListWidgetItem(p["name"])
      it.setData(QtCore.Qt.UserRole, p)
      thumb = p.get("thumbnail")
      if thumb:
        it.setIcon(QtGui.QIcon(thumb))
      listw.addItem(it)

  try:
    refresh()
  except Exception:
    logger.exception("preset listing failed")

  row = QtWidgets.QHBoxLayout()
  save_btn = QtWidgets.QPushButton("Save current…")
  load_btn = QtWidgets.QPushButton("Load")
  del_btn = QtWidgets.QPushButton("Delete")
  folder_btn = QtWidgets.QPushButton("Folder…")
  folder_btn.setToolTip("Choose where presets are stored.")
  close_btn = QtWidgets.QPushButton("Close")
  for b in (save_btn, load_btn, del_btn, folder_btn, close_btn):
    row.addWidget(b)
  v.addLayout(row)

  def do_save():
    name, ok = QtWidgets.QInputDialog.getText(dlg, "Save preset", "Name:")
    if not ok or not name.strip():
      return
    try:
      presets.save_preset(_current_head(), name.strip())
      refresh()
    except Exception as e:
      QtWidgets.QMessageBox.warning(dlg, "Save failed", str(e))

  def do_load():
    it = listw.currentItem()
    if not it:
      return
    try:
      presets.load_preset(_current_head(), it.text())
    except Exception as e:
      QtWidgets.QMessageBox.warning(dlg, "Load failed", str(e))

  def do_delete():
    it = listw.currentItem()
    if not it:
      return
    try:
      presets.delete_preset(it.text())
      refresh()
    except Exception as e:
      QtWidgets.QMessageBox.warning(dlg, "Delete failed", str(e))

  def do_folder():
    from gnm_maya import settings
    d = QtWidgets.QFileDialog.getExistingDirectory(
        dlg, "Choose the presets folder", settings.presets_dir())
    if d:
      settings.set_presets_dir(d)
      dlg.setWindowTitle("GNM Presets — %s" % d)
      refresh()

  save_btn.clicked.connect(do_save)
  load_btn.clicked.connect(do_load)
  del_btn.clicked.connect(do_delete)
  folder_btn.clicked.connect(do_folder)
  close_btn.clicked.connect(dlg.accept)
  dlg.show()
  return dlg


# --- crowd -------------------------------------------------------------------

def crowd_dialog():
  from gnm_maya import crowd

  dlg = QtWidgets.QDialog(None)
  dlg.setWindowTitle("Generate Crowd")
  form = QtWidgets.QFormLayout(dlg)
  count = QtWidgets.QSpinBox(); count.setRange(1, 200); count.setValue(10)
  cols = QtWidgets.QSpinBox(); cols.setRange(1, 50); cols.setValue(5)
  spacing = QtWidgets.QDoubleSpinBox(); spacing.setRange(0.1, 10); spacing.setValue(0.5)
  idscale = QtWidgets.QDoubleSpinBox(); idscale.setRange(0, 3); idscale.setValue(1.0)
  exscale = QtWidgets.QDoubleSpinBox(); exscale.setRange(0, 3); exscale.setValue(0.0)
  bake = QtWidgets.QCheckBox(); bake.setToolTip("Bake each head into a rig (slow: ~15s/head).")
  form.addRow("Heads", count)
  form.addRow("Columns", cols)
  form.addRow("Spacing", spacing)
  form.addRow("Identity scale", idscale)
  form.addRow("Expression scale", exscale)
  form.addRow("Bake rigs", bake)
  btns = QtWidgets.QDialogButtonBox(
      QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
  btns.accepted.connect(dlg.accept)
  btns.rejected.connect(dlg.reject)
  form.addRow(btns)
  if not _run(dlg):
    return
  made = crowd.generate_crowd(
      count=count.value(), columns=cols.value(), spacing=spacing.value(),
      identity_scale=idscale.value(), expression_scale=exscale.value(),
      bake=bake.isChecked())
  mc.select(made, replace=True)
  logger.info("Crowd: created %d heads", len(made))
  return made


# --- fbx / landmarks ----------------------------------------------------------

def export_selected_fbx():
  from gnm_maya import export_fbx, settings
  sel = mc.ls(selection=True) or []
  if not sel:
    mc.confirmDialog(title="Export FBX",
                       message="Select a baked GNM rig first.", button=["OK"])
    return
  # Let the user pick the destination (folder remembered across sessions).
  picked = mc.fileDialog2(fileMode=0, caption="Export GNM rig as FBX",
                            fileFilter="FBX (*.fbx)",
                            startingDirectory=settings.exports_dir(),
                            dialogStyle=2) or []
  if not picked:
    return None  # user cancelled
  path = picked[0]
  settings.set_exports_dir(os.path.dirname(path))
  path = export_fbx.export_rigged_fbx(sel[0], path=path)
  mc.confirmDialog(title="Export FBX", message="Exported:\n%s" % path,
                     button=["OK"])
  return path


def open_shape_gallery():
  """Open the pre-rendered min/max shape gallery in the browser."""
  import webbrowser
  from gnm_maya import ui
  page = ui.gallery_page_path()
  if page:
    webbrowser.open("file:///" + page.replace("\\", "/"))
    return page
  mc.confirmDialog(
      title="Shape Gallery",
      message="Gallery not generated yet. From the gnm-maya folder run:\n"
              "runtime\\python.exe external\\gen_gallery.py --out docs\\shapes",
      button=["OK"])
  return None


def create_landmarks():
  from gnm_maya import landmarks
  grp = landmarks.create_landmark_locators(_current_head())
  logger.info("Landmarks group: %s", grp)
  return grp


def update_landmarks():
  from gnm_maya import landmarks
  landmarks.update_landmark_locators(_current_head())


def fit_head_to_landmarks():
  """Reshape the head so its landmarks match the edited locators."""
  from gnm_maya import landmarks
  head = _current_head()
  name = landmarks.fit_head_to_locators(head)
  mc.inViewMessage(assistMessage="GNM: head fitted to landmarks",
                     position="topCenter", fade=True)
  # Refresh the panel sliders if it is open on this head.
  from gnm_maya import ui
  panel = getattr(ui, "_WINDOW", None)
  if panel and getattr(panel, "head", None) and \
     panel.head.transform == head.transform:
    panel.head.identity = list(head.identity)
    panel._sync_sliders_from_head()
  return name


def toggle_landmark_mirror():
  """Toggle L<->R mirrored landmark editing (returns new state)."""
  from gnm_maya import landmarks
  if landmarks._mirror_state["jobs"]:
    landmarks.disable_mirror()
    mc.inViewMessage(assistMessage="GNM landmark mirror: OFF",
                       position="topCenter", fade=True)
    return False
  n = landmarks.enable_mirror(_current_head())
  mc.inViewMessage(assistMessage="GNM landmark mirror: ON (%d locators)" % n,
                     position="topCenter", fade=True)
  return True
