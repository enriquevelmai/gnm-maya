"""Tabbed GNM control panel (Identity / Expression / Pose / Translation).

Per-coefficient vertical sliders grouped by body part, driving a live GnmHead.
Parented to the Maya main window, with an optional left<->right symmetry toggle.
Works with PySide6 (Maya 2025+) or PySide2 (Maya 2022-2024).
"""

from __future__ import annotations

import logging
import os

try:
  from PySide6 import QtWidgets, QtCore, QtGui
  from shiboken6 import wrapInstance
except ImportError:  # Maya 2022-2024
  from PySide2 import QtWidgets, QtCore, QtGui
  from shiboken2 import wrapInstance

from maya import OpenMayaUI as omui
from maya import cmds as mc

from gnm_maya.core.head import GnmHead, find_heads
from gnm_maya.core import config
from gnm_maya.ui import icons

logger = logging.getLogger(__name__)

# Gallery ships inside the module (survives copied-module installs); the
# repo-root location is kept as a fallback for older checkouts.
_GALLERY_DIRS = [
    os.path.join(config.MODULE_ROOT, "docs", "shapes"),
    os.path.normpath(os.path.join(config.MODULE_ROOT, "..", "docs", "shapes")),
]


def _gallery_dir():
  for d in _GALLERY_DIRS:
    if os.path.isfile(os.path.join(d, "manifest.json")):
      return d
  return None


def _load_gallery():
  """Manifest of pre-rendered min/max shape images, if present."""
  import json
  d = _gallery_dir()
  if not d:
    return None
  try:
    with open(os.path.join(d, "manifest.json")) as f:
      m = json.load(f)
    m["_dir"] = d
    return m
  except Exception:
    return None


def gallery_page_path():
  d = _gallery_dir()
  if not d:
    return None
  page = os.path.join(d, "index.html")
  return page if os.path.isfile(page) else None

_WINDOW = None
_OBJECT_NAME = "gnmHeadPanel"
_TITLE = "GNM Head (Generative aNthropometric Model)"

from gnm_maya.ui.widgets import (TickSlider, VSlider, CoeffGroup,
                                 CollapsibleFrame, MAX_PER_GROUP,
                                 COEFF_RANGE, POSE_RANGE, TRANS_RANGE)
# Explains what the sliders are (surfaced via the "?" info button and tooltips).
PCA_INFO = (
    "GNM's Identity and Expression controls are a statistical shape basis, "
    "ordered by importance — much like PCA components.\n\n"
    "• Lower-numbered modes (m0, m1, …) capture the largest, most "
    "meaningful variation in shape/expression.\n"
    "• Higher-numbered modes are progressively finer, subtler "
    "adjustments.\n\n"
    "Each region shows its first %d modes to stay usable (there are 253 "
    "identity + 383 expression modes in total). Use 'Show all N' on a group to "
    "reveal the rest.\n\n"
    "Every mode is also drivable from script by its global index, e.g.:\n"
    "    head.set_expression(97, 2.0)\n\n"
    "Create tab:\n"
    "• Sample draws a fresh random face/expression for the chosen class "
    "(repeated clicks vary); Strength scales the sampled identity.\n"
    "• Blend fixes the random latent, so dragging Mix interpolates smoothly "
    "between two classes; Re-roll picks a new latent.\n"
    "• Areas restrict Randomize/Variants/Reset to checked regions (model "
    "basis groups), feature zones (geometric masks) and your own painted "
    "maps (Painted… ▸ New, then paint white = 1 with the vertex-colour brush)."
) % MAX_PER_GROUP


def maya_main_window():
  ptr = omui.MQtUtil.mainWindow()
  return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None



class GnmPanel(QtWidgets.QWidget):

  def __init__(self, parent=None, adopt_transform=None):
    super(GnmPanel, self).__init__(parent)
    self.setObjectName(_OBJECT_NAME)
    self.setWindowFlags(QtCore.Qt.Window)
    self.setWindowTitle(_TITLE)
    self.setWindowIcon(icons.window_icon())
    self.setMinimumSize(700, 380)
    self.resize(860, 460)

    # --- state ---
    self.head = None
    self._symmetry = False
    self._sliders = []                 # all, for Reset All
    self._id_sliders = {}              # identity coeff index -> VSlider
    self._expr_sliders = {}            # expression coeff index -> VSlider
    self._pose_sliders = {}            # (joint, axis) -> VSlider
    self._trans_sliders = {}           # axis -> VSlider

    # Coalesce rapid slider edits: stage the coefficient immediately (cheap),
    # repaint the mesh at most every REFRESH_MS so drags stay responsive.
    self._refresh_timer = QtCore.QTimer(self)
    self._refresh_timer.setSingleShot(True)
    self._refresh_timer.setInterval(25)
    self._pending_action = None
    self._blend_expr_seed = 0   # fixed so dragging a mix interpolates smoothly
    self._blend_iden_seed = 0
    self._gallery = _load_gallery()  # pre-rendered min/max shape thumbnails
    self._coeff_meta = []            # (slider, kind, idx) for live thumb resize
    self._hist = []                  # coefficient-state ladder (undo/redo)
    self._hist_i = -1
    self._hist_max = 25              # hard cap: oldest looks fall off
    from gnm_maya.core import settings as _settings
    self._thumb_px = _settings.thumb_size()

    # --- user-modifiable widgets (created here, laid out in populate_ui) ---
    self.status = QtWidgets.QLabel("Building head...")
    self.status.setWordWrap(True)
    self.sym_chk = QtWidgets.QCheckBox("Symmetry (L/R)")
    self.sym_chk.setToolTip(
        "<b>Left/right symmetry</b><br>Mirror every edit across the left and "
        "right eye regions and eye joints, so both sides stay matched while "
        "you sculpt or randomize.")
    self.tex_chk = QtWidgets.QCheckBox("Texture")
    self.tex_chk.setToolTip(
        "<b>Show texture</b><br>Apply a PNG colour map to the head. Defaults "
        "to the bundled GNM edgeflow map; use the folder button to pick your "
        "own.")
    self.tex_browse = QtWidgets.QPushButton()
    self.tex_browse.setFixedWidth(28)
    self.tex_browse.setToolTip(
        "<b>Choose texture…</b><br>Pick a custom PNG to apply to the head.")
    icons.decorate(self.tex_browse, "folder_open", 16)
    self._texture_path = None  # None -> bundled edgeflow texture
    self.info_btn = QtWidgets.QPushButton()
    self.info_btn.setFixedWidth(28)
    self.info_btn.setToolTip(
        "<b>How the sliders work</b><br>A short explainer of GNM's PCA-style "
        "shape basis (which modes do what).")
    icons.decorate(self.info_btn, "info", 16)
    self.thumb_combo = QtWidgets.QComboBox()
    for label, px in (("No images", 0), ("Small", 40), ("Medium", 56),
                      ("Large", 84), ("Huge", 128)):
      self.thumb_combo.addItem("%s" % label, px)
    self.thumb_combo.setToolTip(
        "Size of the shape images on the sliders (tooltips scale too).")
    best = min(range(self.thumb_combo.count()),
               key=lambda i: abs(self.thumb_combo.itemData(i) - self._thumb_px))
    self.thumb_combo.setCurrentIndex(best)
    self.tabs = QtWidgets.QTabWidget()
    # One synced "random scale" spinbox per randomize-capable tab.
    self._scale_value = 1.0
    self._scale_spins = []
    self.reset_btn = QtWidgets.QPushButton("Reset Selected / All")
    self.reset_btn.setToolTip(
        "<b>Reset to neutral</b><br>Return the selected GNM head(s) to the "
        "average template — identity, expression, pose and translation all "
        "zeroed.<br>With nothing selected, resets this panel's head.")
    icons.decorate(self.reset_btn, "restart", 16)
    self.fit_btn = QtWidgets.QPushButton("Fit from Photo…")
    self.fit_btn.setToolTip(
        "<b>Fit from photo</b><br>Detect 68 facial landmarks in a photo "
        "(MediaPipe, runs locally) and solve the identity coefficients to "
        "match them.<br>Produces a likeness, not an exact scan.")
    icons.decorate(self.fit_btn, "photo_camera", 16)
    self.bake_btn = QtWidgets.QPushButton("Bake Rig")
    self.bake_btn.setToolTip(
        "<b>Bake rig</b><br>Convert this head into a self-sufficient rigged "
        "mesh:<br>• blendShape with the 20 named expressions (keyframable)"
        "<br>• neck/head/eye joints skinned with GNM's weights<br>The result "
        "needs no GNM runtime and exports to FBX.")
    icons.decorate(self.bake_btn, "cube", 16)

    try:
      if adopt_transform and mc.objExists(adopt_transform):
        self.head = GnmHead.adopt(adopt_transform)
        self.status.setText("Adopted: %s" % self.head.transform)
      else:
        self.head = GnmHead()
        self.status.setText("Head: %s" % self.head.transform)
    except Exception as e:
      self._show_error("Failed to build GNM head", e)

    self.populate_ui()
    self.register_controllers()
    self._sync_sliders_from_head()
    self._push_history()  # ladder starts at the head's initial state

  # --- history ladder --------------------------------------------------------

  def _snapshot(self):
    h = self.head
    return {"identity": list(h.identity), "expression": list(h.expression),
            "rotations": [list(r) for r in h.rotations],
            "translation": list(h.translation)}

  def _push_history(self):
    """Record the current state after a discrete action (randomize, sample,
    variant, reset, fit...). Slider drags are deliberately NOT recorded —
    the ladder steps between meaningful looks, not every mouse move."""
    if not self.head:
      return
    snap = self._snapshot()
    if 0 <= self._hist_i < len(self._hist) and self._hist[self._hist_i] == snap:
      return  # no-op action; don't duplicate
    del self._hist[self._hist_i + 1:]          # a new action drops redo tail
    self._hist.append(snap)
    if len(self._hist) > self._hist_max:
      self._hist.pop(0)
    self._hist_i = len(self._hist) - 1
    self._update_hist_buttons()

  def _apply_snapshot(self, snap):
    h = self.head
    h.identity = list(snap["identity"])
    h.expression = list(snap["expression"])
    h.rotations = [list(r) for r in snap["rotations"]]
    h.translation = list(snap["translation"])
    h.refresh()
    self._sync_sliders_from_head()

  def _hist_step(self, delta):
    if not self.head or not self._hist:
      return
    j = self._hist_i + delta
    if not (0 <= j < len(self._hist)):
      return
    try:
      self._hist_i = j
      self._apply_snapshot(self._hist[j])
      self.status.setText("History: state %d / %d."
                          % (j + 1, len(self._hist)))
      self._update_hist_buttons()
    except Exception as e:
      self._show_error("History step failed", e)

  def _update_hist_buttons(self):
    if hasattr(self, "hist_back_btn"):
      self.hist_back_btn.setEnabled(self._hist_i > 0)
      self.hist_fwd_btn.setEnabled(self._hist_i < len(self._hist) - 1)

  # --- error reporting -------------------------------------------------------

  def _show_error(self, context, err):
    """Surface a caught error: log the traceback, show it in the status bar,
    and pop a critical message box so the user always knows what happened.

    The same message is not re-popped back-to-back — a failing slider drag
    fires the identical error dozens of times and one dialog is enough (the
    status bar and Script Editor still show every occurrence).
    """
    logger.exception(context)
    msg = str(err) or err.__class__.__name__
    self.status.setText("Error: %s" % msg)
    key = (context, msg)
    if key == getattr(self, "_last_error", None):
      return
    self._last_error = key
    QtWidgets.QMessageBox.critical(
        self, "GNM — %s" % context,
        "%s\n\n%s\n\nSee the Script Editor for the full traceback."
        % (context, msg))

  # --- layout --------------------------------------------------------------

  def populate_ui(self):
    outer = QtWidgets.QVBoxLayout(self)

    topbar = QtWidgets.QHBoxLayout()
    topbar.addWidget(self.status, 1)
    topbar.addWidget(self.sym_chk)
    topbar.addWidget(self.tex_chk)
    topbar.addWidget(self.tex_browse)
    topbar.addWidget(self.thumb_combo)
    topbar.addWidget(self.info_btn)
    outer.addLayout(topbar)

    outer.addWidget(self.tabs, 1)

    if self.head is not None:
      meta = self.head.topology.meta
      self.tabs.addTab(self._create_tab(), "Create")
      self.tabs.addTab(self._coeff_tab(meta["identity_groups"], "identity"),
                       "Identity")
      self.tabs.addTab(self._coeff_tab(meta["expression_groups"], "expression"),
                       "Expression")
      self.tabs.addTab(self._pose_tab(meta["joint_names"]), "Pose")
      self.tabs.addTab(self._animate_tab(), "Animate")

    bottom = QtWidgets.QHBoxLayout()
    self.hist_back_btn = QtWidgets.QPushButton()
    self.hist_back_btn.setFixedWidth(30)
    self.hist_back_btn.setToolTip(
        "<b>Previous look</b><br>Step back through the ladder of randomize / "
        "sample / variant / reset states (last %d kept; slider drags aren't "
        "recorded)." % self._hist_max)
    icons.decorate(self.hist_back_btn, "arrow_back", 15)
    self.hist_fwd_btn = QtWidgets.QPushButton()
    self.hist_fwd_btn.setFixedWidth(30)
    self.hist_fwd_btn.setToolTip("<b>Next look</b><br>Step forward again.")
    icons.decorate(self.hist_fwd_btn, "arrow_forward", 15)
    bottom.addWidget(self.hist_back_btn)
    bottom.addWidget(self.hist_fwd_btn)
    self.lmk_chk = QtWidgets.QPushButton("Landmarks")
    self.lmk_chk.setCheckable(True)
    self.lmk_chk.setToolTip(
        "<b>Landmarks</b><br>Show/hide the 68 facial landmark locators on "
        "this head (created on first use). Drag them to sculpt; hide to "
        "declutter the viewport.")
    icons.decorate(self.lmk_chk, "scatter", 15)
    self.sculpt_chk = QtWidgets.QPushButton("Live Sculpt")
    self.sculpt_chk.setCheckable(True)
    self.sculpt_chk.setToolTip(
        "<b>Live Sculpt</b><br>Refit the head automatically whenever a "
        "landmark locator drag ends (turns Landmarks on if needed). Camera "
        "moves never re-trigger the fit.")
    icons.decorate(self.sculpt_chk, "tune", 15)
    bottom.addWidget(self.lmk_chk)
    bottom.addWidget(self.sculpt_chk)
    bottom.addStretch(1)
    self.display_btn = QtWidgets.QPushButton("Display")
    self.display_btn.setToolTip(
        "<b>Display</b><br>Show/hide anatomical parts (eyes, teeth, tongue) "
        "of every GNM head in the viewport.")
    icons.decorate(self.display_btn, "tune", 15)
    self.display_btn.setMenu(self._display_menu())
    bottom.addWidget(self.display_btn)
    bottom.addWidget(self.reset_btn)
    outer.addLayout(bottom)

  def _display_menu(self):
    from gnm_maya.scene import material
    menu = QtWidgets.QMenu(self)
    for label in material.COMPONENT_GROUPS:
      act = menu.addAction(label)
      act.setCheckable(True)
      act.setChecked(True)
      act.toggled.connect(
          lambda on, l=label: self._set_component_visible(l, on))

    def _sync():  # reflect the real shader state each time it opens
      for act in menu.actions():
        act.blockSignals(True)
        act.setChecked(material.component_visible(act.text()))
        act.blockSignals(False)
    menu.aboutToShow.connect(_sync)
    return menu

  def _set_component_visible(self, label, on):
    from gnm_maya.scene import material
    try:
      material.set_component_visible(label, on)
      self.status.setText("%s %s." % (label, "shown" if on else "hidden"))
    except Exception as e:
      self._show_error("Display toggle failed", e)

  def register_controllers(self):
    self.sym_chk.toggled.connect(self._on_symmetry_toggled)
    self.tex_chk.toggled.connect(self._on_texture_toggled)
    self.tex_browse.clicked.connect(self._browse_texture)
    self.reset_btn.clicked.connect(self._reset_all)
    self.fit_btn.clicked.connect(self._fit_photo)
    self.bake_btn.clicked.connect(self._bake_rig)
    self.thumb_combo.currentIndexChanged.connect(self._on_thumb_size)
    self.info_btn.clicked.connect(self._show_info)
    self.hist_back_btn.clicked.connect(lambda: self._hist_step(-1))
    self.hist_fwd_btn.clicked.connect(lambda: self._hist_step(+1))
    self.lmk_chk.toggled.connect(self._on_landmarks_toggled)
    self.sculpt_chk.toggled.connect(self._on_live_sculpt_toggled)
    self._refresh_timer.timeout.connect(self._do_refresh)
    # Per-tab and per-slider callbacks are wired where those widgets are built
    # (they depend on the head metadata, not known until construction).

  # --- widget factories ----------------------------------------------------

  def _coeff_name(self, kind, idx):
    names = self.head.topology.meta.get(kind + "_names") or []
    return names[idx] if idx < len(names) else "%s_%d" % (kind, idx)

  def _slider_visuals(self, kind, idx, px):
    """(icon_max, icon_min, tooltip) for a coeff slider at thumb size ``px``."""
    name = self._coeff_name(kind, idx)
    tip = ("%s\n%s mode #%d — lower modes = broader shape, "
           "higher = finer detail." % (name, kind, idx))
    icon_max = icon_min = None
    if self._gallery:
      entry = (self._gallery.get("images") or {}).get(name)
      if entry:
        d = self._gallery["_dir"]
        icon_max = os.path.join(d, entry["max"])
        icon_min = os.path.join(d, entry["min"])
        tip_w = max(96, px * 2)  # tooltip min/max pair scales with thumb size
        tip = ("<b>%s</b> (%s mode #%d)<br/>"
               "<img src='%s' width='%d'> <img src='%s' width='%d'><br/>"
               "min (-3) / max (+3) — double-click slider to reset"
               % (name, kind, idx,
                  icon_min.replace("\\", "/"), tip_w,
                  icon_max.replace("\\", "/"), tip_w))
    return icon_max, icon_min, tip

  def _make_coeff_slider(self, kind, idx, title):
    px = self._thumb_px
    icon_max, icon_min, tip = self._slider_visuals(kind, idx, px)
    w = VSlider(title, COEFF_RANGE, 100.0, 1,
                 lambda v, i=idx, kd=kind: self._on_coeff(kd, i, v),
                 tooltip=tip, icon_path=icon_max, icon_path_min=icon_min)
    w.set_icon_size(px)
    self._coeff_meta.append((w, kind, idx))
    self._sliders.append(w)
    (self._expr_sliders if kind == "expression" else self._id_sliders)[idx] = w
    return w

  def _tab_header(self, buttons):
    bar = QtWidgets.QHBoxLayout()
    for b in buttons:
      bar.addWidget(b)
    bar.addStretch(1)
    host = QtWidgets.QWidget()
    host.setLayout(bar)
    return host

  def _make_scale_controls(self):
    """A 'random scale' label+spinbox; all instances stay in sync."""
    lbl = QtWidgets.QLabel("random scale")
    spin = QtWidgets.QDoubleSpinBox()
    spin.setRange(0.0, 3.0)
    spin.setSingleStep(0.1)
    spin.setMinimumWidth(80)
    spin.setValue(self._scale_value)
    spin.setToolTip("Std-dev multiplier for the Randomize buttons "
                    "(synced across tabs).")
    spin.valueChanged.connect(self._on_scale_changed)
    self._scale_spins.append(spin)
    return [lbl, spin]

  def _on_scale_changed(self, value):
    self._scale_value = float(value)
    for s in self._scale_spins:
      if abs(s.value() - value) > 1e-9:
        s.blockSignals(True)
        s.setValue(value)
        s.blockSignals(False)

  def _scroll(self, inner):
    sc = QtWidgets.QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(inner)
    return sc

  def _create_tab(self):
    """Sample / Blend / Areas as Maya-style collapsible frames."""
    container = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(container)
    v.setSpacing(4)
    sem = self.head.topology.meta.get("semantic", {})

    if not sem.get("available"):
      msg = QtWidgets.QLabel(
          "Semantic sampler unavailable — it needs the decoder models in the "
          "vendored GNM repo and 'h5py' in the runtime. Update GNM or rebuild "
          "the runtime to enable Sample/Blend.")
      msg.setWordWrap(True)
      v.addWidget(msg)
      areas = CollapsibleFrame("Areas", expanded=True)
      areas.content_layout().addWidget(self._area_box())
      v.addWidget(areas)
      v.addStretch(1)
      return self._scroll(container)

    def _pretty(x):
      return x.replace("_", " ").title()

    def _small(text, tip, icon_name, slot):
      b = QtWidgets.QPushButton(text)
      b.setToolTip(tip)
      icons.decorate(b, icon_name, 15)
      b.clicked.connect(slot)
      return b

    # --- Sample ---------------------------------------------------------------
    sample = CollapsibleFrame("Sample", expanded=True)
    g = QtWidgets.QGridLayout()
    g.setHorizontalSpacing(8)
    self.sem_gender = QtWidgets.QComboBox()
    self.sem_gender.addItems([_pretty(x) for x in sem["gender"]])
    self.sem_ethnicity = QtWidgets.QComboBox()
    self.sem_ethnicity.addItems([_pretty(x) for x in sem["ethnicity"]])
    self.sem_strength = QtWidgets.QDoubleSpinBox()
    self.sem_strength.setRange(0.0, 2.0)
    self.sem_strength.setSingleStep(0.1)
    self.sem_strength.setValue(1.0)
    self.sem_strength.setToolTip(
        "<b>Identity strength</b><br>Scales a sampled identity toward "
        "(0) or past (>1) the average head — tone a face down or push it.")
    id_btn = _small("Sample Identity",
                    "<b>Sample identity</b><br>Draw a random face for the "
                    "chosen gender × ethnicity. Click again for another.",
                    "face", self._sample_identity)
    id_rst = _small("", "<b>Reset identity</b><br>Back to the average head.",
                    "restart", self._reset_semantic_identity)
    id_rst.setFixedWidth(28)
    self.sem_expr = QtWidgets.QComboBox()
    self.sem_expr.addItems([_pretty(x) for x in sem["expression"]])
    ex_btn = _small("Sample Expression",
                    "<b>Sample expression</b><br>Apply the selected named "
                    "expression with a fresh random variation.",
                    "mood", self._sample_expression)
    ex_rst = _small("", "<b>Reset expression</b><br>Back to neutral.",
                    "restart", self._reset_semantic_expression)
    ex_rst.setFixedWidth(28)
    g.addWidget(QtWidgets.QLabel("Gender"), 0, 0)
    g.addWidget(self.sem_gender, 0, 1)
    g.addWidget(QtWidgets.QLabel("Ethnicity"), 0, 2)
    g.addWidget(self.sem_ethnicity, 0, 3)
    g.addWidget(QtWidgets.QLabel("Strength"), 0, 4)
    g.addWidget(self.sem_strength, 0, 5)
    g.addWidget(id_btn, 0, 6)
    g.addWidget(id_rst, 0, 7)
    g.addWidget(QtWidgets.QLabel("Expression"), 1, 0)
    g.addWidget(self.sem_expr, 1, 1)
    g.addWidget(ex_btn, 1, 6)
    g.addWidget(ex_rst, 1, 7)
    self.desc_edit = QtWidgets.QLineEdit()
    self.desc_edit.setPlaceholderText(
        "Describe: e.g. 'a very happy asian woman, winking left'")
    self.desc_edit.setToolTip(
        "<b>Describe a face</b><br>Natural-language description → identity "
        "and expression (local lexicon, or Ollama if running). Enter applies.")
    desc_btn = _small("Apply", "<b>Apply description</b>", "sparkle",
                      self._apply_description)
    self.desc_edit.returnPressed.connect(self._apply_description)
    g.addWidget(self.desc_edit, 2, 0, 1, 6)
    g.addWidget(desc_btn, 2, 6, 1, 2)
    g.addWidget(self.fit_btn, 3, 6, 1, 2)
    g.setColumnStretch(1, 1)
    g.setColumnStretch(3, 1)
    sample.content_layout().addLayout(g)
    v.addWidget(sample)

    # --- Blend (collapsed: a power feature) --------------------------------------
    blend = CollapsibleFrame("Blend", expanded=False)
    blend.content_layout().addWidget(self._blend_box(sem, _pretty))
    v.addWidget(blend)

    # --- Areas ----------------------------------------------------------------
    areas = CollapsibleFrame("Areas  (randomize only what's checked)",
                             expanded=True)
    areas.content_layout().addWidget(self._area_box())
    v.addWidget(areas)
    v.addStretch(1)
    return self._scroll(container)

  def _mix_slider(self):
    s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    s.setRange(0, 100)
    s.setValue(0)
    lbl = QtWidgets.QLabel("0.00")
    lbl.setFixedWidth(34)
    s.valueChanged.connect(lambda v: lbl.setText("%.2f" % (v / 100.0)))
    return s, lbl

  def _blend_box(self, sem, pretty):
    box = QtWidgets.QWidget()
    box.setToolTip("Drag a Mix slider to interpolate between two classes; "
                   "the random latent stays fixed so it morphs smoothly.")
    g = QtWidgets.QGridLayout(box)
    g.setContentsMargins(0, 0, 0, 0)

    # Expression blend
    self.blend_expr1 = QtWidgets.QComboBox()
    self.blend_expr1.addItems([pretty(e) for e in sem["expression"]])
    self.blend_expr2 = QtWidgets.QComboBox()
    self.blend_expr2.addItems([pretty(e) for e in sem["expression"]])
    self.blend_expr2.setCurrentIndex(min(10, self.blend_expr2.count() - 1))
    self.blend_expr_mix, ex_lbl = self._mix_slider()
    for w in (self.blend_expr1, self.blend_expr2):
      w.currentIndexChanged.connect(lambda _=0: self._schedule(self._apply_expr_blend))
    self.blend_expr_mix.valueChanged.connect(
        lambda _=0: self._schedule(self._apply_expr_blend))
    g.addWidget(QtWidgets.QLabel("Expr 1"), 0, 0)
    g.addWidget(self.blend_expr1, 0, 1)
    g.addWidget(QtWidgets.QLabel("Expr 2"), 0, 2)
    g.addWidget(self.blend_expr2, 0, 3)
    g.addWidget(QtWidgets.QLabel("Mix"), 0, 4)
    g.addWidget(self.blend_expr_mix, 0, 5)
    g.addWidget(ex_lbl, 0, 6)

    # Identity (ethnicity) blend — gender comes from the categorical Gender box
    self.blend_ethn1 = QtWidgets.QComboBox()
    self.blend_ethn1.addItems([pretty(e) for e in sem["ethnicity"]])
    self.blend_ethn2 = QtWidgets.QComboBox()
    self.blend_ethn2.addItems([pretty(e) for e in sem["ethnicity"]])
    self.blend_ethn2.setCurrentIndex(min(2, self.blend_ethn2.count() - 1))
    self.blend_ethn_mix, et_lbl = self._mix_slider()
    for w in (self.blend_ethn1, self.blend_ethn2):
      w.currentIndexChanged.connect(lambda _=0: self._schedule(self._apply_iden_blend))
    self.blend_ethn_mix.valueChanged.connect(
        lambda _=0: self._schedule(self._apply_iden_blend))
    g.addWidget(QtWidgets.QLabel("Ethn 1"), 1, 0)
    g.addWidget(self.blend_ethn1, 1, 1)
    g.addWidget(QtWidgets.QLabel("Ethn 2"), 1, 2)
    g.addWidget(self.blend_ethn2, 1, 3)
    g.addWidget(QtWidgets.QLabel("Mix"), 1, 4)
    g.addWidget(self.blend_ethn_mix, 1, 5)
    g.addWidget(et_lbl, 1, 6)

    reset = QtWidgets.QPushButton("Reset Mixes")
    reset.setToolTip(
        "<b>Reset mixes</b><br>Snap both Mix sliders back to 0 (full Expr 1 / "
        "Ethn 1).")
    icons.decorate(reset, "restart", 15)
    reset.clicked.connect(self._reset_mixes)
    reroll = QtWidgets.QPushButton("Re-roll")
    reroll.setToolTip(
        "<b>Re-roll latent</b><br>Pick a new random latent for the blends, "
        "keeping the current Mix positions.")
    icons.decorate(reroll, "shuffle", 15)
    reroll.clicked.connect(self._reroll_blend)
    g.addWidget(reset, 2, 1)
    g.addWidget(reroll, 2, 3)
    return box

  def _area_box(self):
    """Per-area mask: randomize/reset only the checked face regions.

    Regions come from the model's identity_groups/expression_groups metadata
    (left eye, right eye, lower face, head, …). Randomizing touches ONLY the
    checked regions' coefficients — everything else keeps its current values —
    so you can e.g. lock a face you like and explore just the nose/jaw area.
    """
    # label -> {"identity": (start, end), "expression": (start, end)}
    meta = self.head.topology.meta
    areas = {}
    for kind in ("identity", "expression"):
      for label, start, end in meta.get(kind + "_groups", []):
        areas.setdefault(label, {})[kind] = (start, end)
    self._area_ranges = areas
    self._area_checks = {}

    box = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(3)

    def _nice(label):
      # "left_eye_region" -> "Left Eye"
      return label.replace("_region", "").replace("_", " ").title()

    # Row 1: model regions (basis groups — exact coefficient masks).
    r1 = QtWidgets.QHBoxLayout()
    rlbl = QtWidgets.QLabel("Regions")
    rlbl.setFixedWidth(52)
    rlbl.setToolTip("<b>Model regions</b><br>GNM's own basis groups: an "
                    "exact coefficient mask (only these modes change).")
    r1.addWidget(rlbl)
    for label in areas:
      cb = QtWidgets.QCheckBox(_nice(label))
      cb.setToolTip("<b>%s</b><br>%s modes." % (_nice(label),
                    " + ".join(sorted(areas[label]))))
      r1.addWidget(cb)
      self._area_checks[label] = cb
    r1.addStretch(1)
    v.addLayout(r1)

    # Row 2: feature zones (geometric masks solved back to coefficients).
    self._zone_checks = {}
    r2 = QtWidgets.QHBoxLayout()
    zlbl = QtWidgets.QLabel("Zones")
    zlbl.setFixedWidth(52)
    zlbl.setToolTip(
        "<b>Feature zones</b><br>Geometry masks for areas the model has no "
        "dedicated modes for; a fit solver confines the change to the zone "
        "(softly — a natural falloff halo remains).")
    r2.addWidget(zlbl)
    for zone in ("nose", "mouth", "jaw", "brows", "eyes", "ears", "back_head"):
      cb = QtWidgets.QCheckBox(zone.replace("_", " ").title())
      cb.setToolTip("<b>%s</b><br>Geometric mask with smooth falloff."
                    % zone.replace("_", " ").title())
      r2.addWidget(cb)
      self._zone_checks[zone] = cb
    r2.addStretch(1)
    v.addLayout(r2)

    # Row 3: painted maps (your own vertex-colour masks) + preview.
    self._map_checks = {}
    self._map_row = QtWidgets.QHBoxLayout()
    plbl = QtWidgets.QLabel("Painted")
    plbl.setFixedWidth(52)
    plbl.setToolTip(
        "<b>Painted maps</b><br>Your own per-vertex masks, painted in Maya "
        "as vertex colours (white = 1). Painted… ▸ New creates one and opens "
        "the brush; Save bakes the strokes; they persist across scenes.")
    self._map_row.addWidget(plbl)
    self._map_row.addStretch(1)
    paint_btn = QtWidgets.QPushButton("Painted…")
    paint_btn.setToolTip(
        "<b>Painted maps</b><br>New / Edit / Save / Delete your painted "
        "zone maps.")
    icons.decorate(paint_btn, "sparkle", 15)
    paint_btn.setMenu(self._painted_menu())
    self._map_row.addWidget(paint_btn)
    self.mask_show_btn = QtWidgets.QPushButton("Show")
    self.mask_show_btn.setCheckable(True)
    self.mask_show_btn.setToolTip(
        "<b>Show mask</b><br>Spotlight the checked zones + painted maps on "
        "the head as vertex colours (white = fully affected).")
    icons.decorate(self.mask_show_btn, "grid", 15)
    self.mask_show_btn.toggled.connect(self._toggle_mask_preview)
    self._map_row.addWidget(self.mask_show_btn)
    v.addLayout(self._map_row)
    self._rebuild_painted_checks()

    # Row 4: actions.
    row = QtWidgets.QHBoxLayout()
    rl = QtWidgets.QLabel("Randomize")
    rl.setFixedWidth(60)
    row.addWidget(rl)
    rid_btn = QtWidgets.QPushButton("Identity")
    rid_btn.setToolTip(
        "<b>Randomize identity</b> in the checked regions/zones/maps only "
        "(scaled by 'random scale'). Nothing checked = whole head.")
    icons.decorate(rid_btn, "dice", 15)
    rid_btn.clicked.connect(lambda: self._randomize_areas("identity"))
    rex_btn = QtWidgets.QPushButton("Expression")
    rex_btn.setToolTip(
        "<b>Randomize expression</b> in the checked areas only. Honors "
        "Symmetry (L/R).")
    icons.decorate(rex_btn, "dice", 15)
    rex_btn.clicked.connect(lambda: self._randomize_areas("expression"))
    var_btn = QtWidgets.QPushButton("Variants…")
    var_btn.setToolTip(
        "<b>Variants</b><br>A 3×3 contact sheet of candidates for the checked "
        "areas — click a face to apply it.")
    icons.decorate(var_btn, "grid", 15)
    var_btn.clicked.connect(self._open_variants)
    rst_btn = QtWidgets.QPushButton("Reset")
    rst_btn.setToolTip(
        "<b>Reset checked areas</b><br>Zero the checked regions and solve "
        "the checked zones/maps back toward neutral; the rest is untouched.")
    icons.decorate(rst_btn, "restart", 15)
    rst_btn.clicked.connect(self._reset_areas)
    none_btn = QtWidgets.QPushButton("None")
    none_btn.setFixedWidth(48)
    none_btn.setToolTip("Uncheck every region, zone and map.")
    none_btn.clicked.connect(lambda: self._set_all_areas(False))
    row.addWidget(rid_btn)
    row.addWidget(rex_btn)
    row.addWidget(var_btn)
    row.addSpacing(8)
    for w in self._make_scale_controls():
      row.addWidget(w)
    row.addStretch(1)
    row.addWidget(rst_btn)
    row.addWidget(none_btn)
    v.addLayout(row)
    return box

  def _set_all_areas(self, on):
    for cb in list(self._area_checks.values()) + \
        list(self._zone_checks.values()) + list(self._map_checks.values()):
      cb.setChecked(bool(on))

  def _checked_areas(self):
    return [label for label, cb in self._area_checks.items() if cb.isChecked()]

  def _checked_zones(self):
    return [z for z, cb in getattr(self, "_zone_checks", {}).items()
            if cb.isChecked()]

  # --- painted maps ----------------------------------------------------------

  def _checked_maps(self):
    """File paths of the checked painted maps, with this head's latest brush
    strokes baked first so the solver always uses what you see."""
    from gnm_maya.scene import zones as zn
    names = [n for n, cb in getattr(self, "_map_checks", {}).items()
             if cb.isChecked()]
    if names and self.head:
      zn.sync_from_mesh(self.head, names)
    return [zn.map_path(n) for n in names]

  def _rebuild_painted_checks(self):
    from gnm_maya.scene import zones as zn
    checked = {n for n, cb in self._map_checks.items() if cb.isChecked()}
    for cb in self._map_checks.values():
      self._map_row.removeWidget(cb)
      cb.deleteLater()
    self._map_checks = {}
    for i, name in enumerate(zn.list_maps()):
      cb = QtWidgets.QCheckBox(name)
      cb.setToolTip("<b>%s</b><br>Painted map (vertex colours → weights)."
                    % name)
      cb.setChecked(name in checked)
      self._map_row.insertWidget(1 + i, cb)
      self._map_checks[name] = cb

  def _painted_menu(self):
    menu = QtWidgets.QMenu(self)

    def _fill():
      from gnm_maya.scene import zones as zn
      menu.clear()
      menu.addAction("New map…", self._new_painted_map)
      names = zn.list_maps()
      edit = menu.addMenu("Edit (paint)")
      dele = menu.addMenu("Delete")
      for n in names:
        edit.addAction(n, lambda n=n: self._edit_painted_map(n))
        dele.addAction(n, lambda n=n: self._delete_painted_map(n))
      edit.setEnabled(bool(names))
      dele.setEnabled(bool(names))
      menu.addAction("Save paint (bake strokes)", self._save_painted_maps)
      menu.addAction("Stop painting", self._stop_painting)
    menu.aboutToShow.connect(_fill)
    return menu

  def _new_painted_map(self):
    from gnm_maya.scene import zones as zn
    name, ok = QtWidgets.QInputDialog.getText(
        self, "New painted map", "Name (e.g. forehead, cheeks):")
    name = "".join(c for c in name.strip() if c.isalnum() or c in "_-")
    if not ok or not name:
      return
    try:
      zn.save_map(name, [0.0] * self.head.topology.num_vertices)
      self._rebuild_painted_checks()
      self._map_checks[name].setChecked(True)
      zn.start_paint(self.head, name)
      self.status.setText("Painting '%s': brush white where the zone is, "
                          "then Painted… ▸ Save paint." % name)
    except Exception as e:
      self._show_error("New painted map failed", e)

  def _edit_painted_map(self, name):
    from gnm_maya.scene import zones as zn
    try:
      zn.start_paint(self.head, name)
      self.status.setText("Painting '%s' — Painted… ▸ Save paint when done."
                          % name)
    except Exception as e:
      self._show_error("Edit painted map failed", e)

  def _save_painted_maps(self):
    from gnm_maya.scene import zones as zn
    try:
      saved = zn.sync_from_mesh(self.head, zn.list_maps())
      self.status.setText("Saved painted maps: %s." % (", ".join(saved)
                                                       or "none on this head"))
    except Exception as e:
      self._show_error("Save paint failed", e)

  def _stop_painting(self):
    from gnm_maya.scene import zones as zn
    try:
      zn.sync_from_mesh(self.head, zn.list_maps())
      zn.stop_paint(self.head)
      self.status.setText("Paint saved; vertex colours hidden.")
    except Exception as e:
      self._show_error("Stop painting failed", e)

  def _delete_painted_map(self, name):
    from gnm_maya.scene import zones as zn
    ans = QtWidgets.QMessageBox.question(
        self, "Delete painted map", "Delete the painted map '%s'?" % name)
    if ans != QtWidgets.QMessageBox.Yes:
      return
    try:
      zn.delete_map(name)
      self._rebuild_painted_checks()
      self.status.setText("Deleted painted map '%s'." % name)
    except Exception as e:
      self._show_error("Delete painted map failed", e)

  def _toggle_mask_preview(self, on):
    """Spotlight the checked zones + maps as vertex colours on the head."""
    from gnm_maya.scene import zones as zn
    if not self.head:
      return
    try:
      if not on:
        zn.clear_preview(self.head)
        self.status.setText("Mask preview off.")
        return
      zones = self._checked_zones()
      maps = self._checked_maps()
      if not zones and not maps:
        self.status.setText("Check a zone or painted map to preview it.")
        self.mask_show_btn.setChecked(False)
        return
      w = self.head.worker.zone_weights(zones, maps=maps)
      zn.preview(self.head, w)
      self.status.setText("Mask preview: %s." % ", ".join(
          zones + [os.path.basename(m)[:-4] for m in maps]))
    except Exception as e:
      self._show_error("Mask preview failed", e)

  def _randomize_areas(self, kind):
    """Randomize only the checked regions/zones' ``kind`` coefficients."""
    if not self.head:
      return
    labels = self._checked_areas()
    zones = self._checked_zones()
    maps = self._checked_maps()
    ranges = [(label, self._area_ranges[label][kind]) for label in labels
              if kind in self._area_ranges[label]]
    if not labels and not zones and not maps:
      # Nothing checked = the whole head (same as the tab's Randomize).
      self._randomize_kind(kind)
      return
    if not ranges and not zones and not maps:
      self.status.setText("Checked areas have no %s modes." % kind)
      return
    try:
      for _label, (start, end) in ranges:
        self.head.randomize_range(kind, start, end, scale=self._scale_value,
                                  seed=self._rand_seed(),
                                  symmetric=self._symmetry, update=False)
      if zones or maps:
        self.head.randomize_zones(kind, zones, scale=self._scale_value,
                                  seed=self._rand_seed(),
                                  symmetric=self._symmetry, update=False,
                                  maps=maps)
      self.head.refresh()
      self._sync_sliders_from_head()
      mc.select(self.head.transform, replace=True)
      what = ([l for l, _r in ranges] + zones
              + [os.path.basename(m)[:-4] for m in maps])
      self.status.setText("Randomized %s: %s (scale=%.2f)."
                          % (kind, ", ".join(what), self._scale_value))
      self._push_history()
    except Exception as e:
      self._show_error("Area randomize failed", e)

  def _reset_areas(self):
    """Zero the checked regions/zones (identity + expression), leave the rest."""
    if not self.head:
      return
    labels = self._checked_areas()
    zones = self._checked_zones()
    maps = self._checked_maps()
    if not labels and not zones and not maps:
      self.status.setText("Reset: nothing checked (use the bottom Reset for "
                          "the whole head).")
      return
    try:
      for kind in ("identity", "expression"):
        idxs = []
        for label in labels:
          if kind in self._area_ranges[label]:
            start, end = self._area_ranges[label][kind]
            idxs.extend(range(start, end + 1))
        if idxs:
          self.head.clear(kind, idxs)
        if zones or maps:  # scale=0 solves the zones back toward neutral
          self.head.randomize_zones(kind, zones, scale=0.0, update=False,
                                    maps=maps)
      if zones or maps:
        self.head.refresh()
      self._sync_sliders_from_head()
      self.status.setText("Reset areas: %s." % ", ".join(
          labels + zones + [os.path.basename(m)[:-4] for m in maps]))
      self._push_history()
    except Exception as e:
      self._show_error("Area reset failed", e)

  # --- variant contact sheet -------------------------------------------------

  def _make_candidate(self, kind, seed):
    """One candidate (identity, expression) pair: the current head with the
    checked areas/zones (or everything, if nothing is checked) re-rolled."""
    import random as _random
    rng = _random.Random(seed)
    cid = list(self.head.identity)
    cex = list(self.head.expression)
    vec = cid if kind == "identity" else cex
    labels = self._checked_areas()
    zones = self._checked_zones()
    maps = self._checked_maps()
    ranges = [self._area_ranges[l][kind] for l in labels
              if kind in self._area_ranges[l]]
    if not ranges and not zones and not maps:  # nothing checked: full re-roll
      ranges = [(0, len(vec) - 1)]
    for start, end in ranges:
      for i in range(start, end + 1):
        vec[i] = rng.gauss(0.0, 1.0) * self._scale_value
    if kind == "expression" and self._symmetry:
      for a, b in self.head.expression_mirror.items():
        if a < b:
          vec[b] = vec[a]
    if zones or maps:
      out = self.head.worker.zone_randomize(
          kind, zones, identity=cid, expression=cex,
          scale=self._scale_value, seed=seed, maps=maps)
      vec[:] = [float(x) for x in out]
      if kind == "expression" and self._symmetry:
        for a, b in self.head.expression_mirror.items():
          if a < b:
            vec[b] = vec[a]
    return cid, cex

  def _open_variants(self):
    """3x3 contact sheet of candidate randomizations; click one to apply."""
    if not self.head:
      return
    import tempfile

    dlg = QtWidgets.QDialog(self)
    dlg.setWindowTitle("GNM — Variants")
    dlg.setWindowIcon(icons.window_icon())
    v = QtWidgets.QVBoxLayout(dlg)

    top = QtWidgets.QHBoxLayout()
    top.addWidget(QtWidgets.QLabel("Randomize"))
    kind_combo = QtWidgets.QComboBox()
    kind_combo.addItems(["Identity", "Expression"])
    kind_combo.setToolTip("Which basis the variants re-roll (the mask comes "
                          "from the checked areas/zones).")
    top.addWidget(kind_combo)
    reroll = QtWidgets.QPushButton("Re-roll")
    icons.decorate(reroll, "dice", 15)
    reroll.setToolTip("Generate 9 fresh candidates.")
    top.addWidget(reroll)
    top.addStretch(1)
    hint = QtWidgets.QLabel("click a face to apply it")
    top.addWidget(hint)
    v.addLayout(top)

    grid = QtWidgets.QGridLayout()
    v.addLayout(grid)
    tmpdir = tempfile.mkdtemp(prefix="gnm_variants_")
    cells = []
    for n in range(9):
      btn = QtWidgets.QToolButton()
      btn.setIconSize(QtCore.QSize(150, 150))
      btn.setAutoRaise(True)
      grid.addWidget(btn, n // 3, n % 3)
      cells.append(btn)

    state = {"cands": [None] * 9}

    def apply_candidate(n):
      cand = state["cands"][n]
      if cand is None or not self.head:
        return
      try:
        cid, cex = cand
        self.head.identity = list(cid)
        self.head.expression = list(cex)
        self.head.refresh()
        self._sync_sliders_from_head()
        self.status.setText("Applied variant %d." % (n + 1))
        self._push_history()
      except Exception as e:
        self._show_error("Apply variant failed", e)

    def generate():
      kind = kind_combo.currentText().lower()
      mc.waitCursor(state=True)
      try:
        for n, btn in enumerate(cells):
          seed = self._rand_seed()
          cid, cex = self._make_candidate(kind, seed)
          png = os.path.join(tmpdir, "var_%d_%d.png" % (n, seed))
          self.head.worker.render(png, identity=cid, expression=cex, size=150)
          btn.setIcon(QtGui.QIcon(png))
          state["cands"][n] = (cid, cex)
          QtWidgets.QApplication.processEvents()
      except Exception as e:
        self._show_error("Variant generation failed", e)
      finally:
        mc.waitCursor(state=False)

    for n, btn in enumerate(cells):
      btn.clicked.connect(lambda _=False, i=n: apply_candidate(i))
    reroll.clicked.connect(generate)
    kind_combo.currentIndexChanged.connect(lambda _i: generate())

    def cleanup():
      import shutil
      shutil.rmtree(tmpdir, ignore_errors=True)
    dlg.finished.connect(lambda _r: cleanup())

    generate()
    dlg.show()
    return dlg

  # --- blend actions -------------------------------------------------------

  def _apply_expr_blend(self):
    if not self.head:
      return
    i1 = self.blend_expr1.currentIndex()
    i2 = self.blend_expr2.currentIndex()
    mix = self.blend_expr_mix.value() / 100.0
    # Pairs (not a dict): same class on both sides still morphs via 2 latents.
    self.head.blend_expression([[i1, 1.0 - mix], [i2, mix]],
                               seed=self._blend_expr_seed)
    self._sync_sliders_from_head()
    self.status.setText("Blend expr %s/%s @ %.2f" % (
        self.blend_expr1.currentText(), self.blend_expr2.currentText(), mix))

  def _apply_iden_blend(self):
    if not self.head:
      return
    gender = [[self.sem_gender.currentIndex(), 1.0]]
    e1 = self.blend_ethn1.currentIndex()
    e2 = self.blend_ethn2.currentIndex()
    mix = self.blend_ethn_mix.value() / 100.0
    self.head.blend_identity(gender, [[e1, 1.0 - mix], [e2, mix]],
                             seed=self._blend_iden_seed)
    self._sync_sliders_from_head()
    self.status.setText("Blend ethnicity %s/%s @ %.2f" % (
        self.blend_ethn1.currentText(), self.blend_ethn2.currentText(), mix))

  def _reset_mixes(self):
    for s in (self.blend_expr_mix, self.blend_ethn_mix):
      s.setValue(0)

  def _reroll_blend(self):
    import random
    self._blend_expr_seed = random.randint(0, 1 << 30)
    self._blend_iden_seed = random.randint(0, 1 << 30)
    self._apply_expr_blend()
    self._apply_iden_blend()

  def _sample_identity(self):
    if not self.head:
      return
    import random
    try:
      self.head.semantic_identity(self.sem_gender.currentIndex(),
                                  self.sem_ethnicity.currentIndex(),
                                  seed=random.randint(0, 1 << 30))
      strength = float(getattr(self, "sem_strength", None).value()
                       if hasattr(self, "sem_strength") else 1.0)
      if abs(strength - 1.0) > 1e-6:
        self.head.identity = [x * strength for x in self.head.identity]
        self.head.refresh()
      self._sync_sliders_from_head()
      mc.select(self.head.transform, replace=True)
      self.status.setText("Sampled identity: %s / %s" % (
          self.sem_gender.currentText(), self.sem_ethnicity.currentText()))
      self._push_history()
    except Exception as e:
      self._show_error("Sample identity failed", e)

  def _apply_description(self):
    if not self.head:
      return
    text = self.desc_edit.text().strip()
    if not text:
      return
    import random
    try:
      self._busy_status("Interpreting description…")
      parsed = self.head.describe(text, seed=random.randint(0, 1 << 30))
      self._sync_sliders_from_head()
      names = self.head.topology.meta.get("semantic", {}).get("expression", [])
      picks = ", ".join("%s %.1f" % (names[int(k)], w)
                        for k, w in (parsed.get("expression_weights") or {}).items()
                        if int(k) < len(names))
      self.status.setText("Described (%s): %s" % (
          parsed.get("source", "?"), picks or "identity only"))
      self._push_history()
    except Exception as e:
      self._show_error("Describe failed", e)

  def _sample_expression(self):
    if not self.head:
      return
    import random
    try:
      self.head.semantic_expression(self.sem_expr.currentIndex(),
                                    seed=random.randint(0, 1 << 30))
      self._sync_sliders_from_head()
      self.status.setText("Sampled expression: %s"
                          % self.sem_expr.currentText())
      self._push_history()
    except Exception as e:
      self._show_error("Sample expression failed", e)

  def _coeff_tab(self, groups, kind):
    container = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(container)

    rnd = QtWidgets.QPushButton("Randomize %s" % kind.capitalize())
    rnd.setToolTip(
        "<b>Randomize %s</b><br>Draw random values for every %s mode, scaled "
        "by 'random scale'. Higher scale = more extreme." % (kind, kind))
    icons.decorate(rnd, "dice", 16)
    rnd.clicked.connect(lambda: self._randomize_kind(kind))
    rst = QtWidgets.QPushButton("Reset %s" % kind.capitalize())
    rst.setToolTip("<b>Reset %s</b><br>Zero every %s mode." % (kind, kind))
    icons.decorate(rst, "restart", 16)
    rst.clicked.connect(lambda: self._reset_kind(kind))
    header = [rnd] + self._make_scale_controls() + [rst]
    v.addWidget(self._tab_header(header))

    host = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(host)
    row.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
    for (label, start, end) in groups:
      row.addWidget(CoeffGroup(self, kind, label, start, end))
    v.addWidget(self._scroll(host), 1)
    return container

  def _pose_tab(self, joint_names):
    """Joint rotations + global translation in one tab."""
    container = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(container)

    rnd = QtWidgets.QPushButton("Randomize Pose")
    rnd.setToolTip(
        "<b>Randomize pose</b><br>Jitter the neck/head/eye joint rotations, "
        "scaled by 'random scale'.")
    icons.decorate(rnd, "dice", 16)
    rnd.clicked.connect(self._randomize_pose)
    rst = QtWidgets.QPushButton("Reset Pose")
    rst.setToolTip("<b>Reset pose</b><br>Return all joints to their rest "
                   "rotation.")
    icons.decorate(rst, "restart", 16)
    rst.clicked.connect(self._reset_pose)
    rst_t = QtWidgets.QPushButton("Reset Translation")
    rst_t.setToolTip("<b>Reset translation</b><br>Move the head back to the "
                     "world origin.")
    icons.decorate(rst_t, "restart", 16)
    rst_t.clicked.connect(self._reset_translation)
    v.addWidget(self._tab_header([rnd] + self._make_scale_controls()
                                 + [rst, rst_t]))

    host = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(host)
    row.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
    for j, jname in enumerate(joint_names):
      box = QtWidgets.QGroupBox(jname)
      bl = QtWidgets.QHBoxLayout(box)
      for axis, aname in enumerate(("rx", "ry", "rz")):
        w = VSlider(aname, POSE_RANGE, 100.0, 2,
                     lambda v, jj=j, ax=axis: self._on_pose(jj, ax, v),
                     tooltip="%s %s (radians)" % (jname, aname))
        bl.addWidget(w)
        self._sliders.append(w)
        self._pose_sliders[(j, axis)] = w
      row.addWidget(box)
    box = QtWidgets.QGroupBox("translation")
    bl = QtWidgets.QHBoxLayout(box)
    for axis, aname in enumerate(("tx", "ty", "tz")):
      w = VSlider(aname, TRANS_RANGE, 100.0, 2,
                   lambda v, ax=axis: self._on_translation(ax, v),
                   tooltip="Global translation %s" % aname)
      bl.addWidget(w)
      self._sliders.append(w)
      self._trans_sliders[axis] = w
    row.addWidget(box)
    v.addWidget(self._scroll(host), 1)
    return container

  def _animate_tab(self):
    """Bake Rig / Lip Sync / Idle / Export as collapsible frames."""
    container = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(container)
    v.setSpacing(4)
    groups = self.head.topology.meta.get("expression_groups", [])
    max_group = max((end - start + 1 for _n, start, end in groups), default=150)

    # --- Bake Rig ---------------------------------------------------------------
    bake = CollapsibleFrame("Bake Rig", expanded=True)
    g = QtWidgets.QGridLayout()
    self.bake_sem_chk = QtWidgets.QCheckBox("20 named expressions")
    self.bake_sem_chk.setChecked(True)
    self.bake_sem_chk.setToolTip("One blendShape target per semantic "
                                 "expression (happy, wink_left, …).")
    self.bake_arkit_chk = QtWidgets.QCheckBox("ARKit-52 names")
    self.bake_arkit_chk.setToolTip(
        "<b>ARKit-52 target names</b><br>Rename the expressions to Live Link "
        "Face blendshapes (eyeBlinkLeft, jawOpen, mouthSmileLeft/Right, …), "
        "region-masked and L/R-split so mocap can drive them by name.")
    self.bake_visemes_chk = QtWidgets.QCheckBox("Visemes (lip-sync)")
    self.bake_visemes_chk.setChecked(True)
    self.bake_visemes_chk.setToolTip(
        "<b>Visemes</b><br>7 mouth-shape targets (viseme_B…H) that Lip Sync "
        "below keys from audio.")
    self.bake_modes_spin = QtWidgets.QSpinBox()
    self.bake_modes_spin.setRange(0, max_group)
    self.bake_modes_spin.setToolTip(
        "Also bake the first N basis modes of EACH region as targets "
        "(left_eye_region_000, …). 0 = none.")
    g.addWidget(self.bake_sem_chk, 0, 0)
    g.addWidget(self.bake_arkit_chk, 0, 1)
    g.addWidget(self.bake_visemes_chk, 0, 2)
    g.addWidget(QtWidgets.QLabel("Basis modes / region"), 1, 0)
    g.addWidget(self.bake_modes_spin, 1, 1)
    g.addWidget(self.bake_btn, 1, 2)
    g.setColumnStretch(3, 1)
    bake.content_layout().addLayout(g)
    v.addWidget(bake)

    # --- Lip Sync -----------------------------------------------------------------
    lip = CollapsibleFrame("Lip Sync  (audio → mouth keys on a baked rig)",
                           expanded=True)
    g = QtWidgets.QGridLayout()
    self.audio_edit = QtWidgets.QLineEdit()
    self.audio_edit.setPlaceholderText("dialogue .wav / .ogg")
    self.audio_edit.setToolTip("Recording to analyse (Rhubarb Lip Sync, "
                               "downloaded on first use, ~85 MB).")
    audio_btn = QtWidgets.QPushButton()
    audio_btn.setFixedWidth(28)
    icons.decorate(audio_btn, "folder_open", 15)
    audio_btn.setToolTip("Choose the audio file.")
    audio_btn.clicked.connect(self._browse_audio)
    self.dialog_edit = QtWidgets.QLineEdit()
    self.dialog_edit.setPlaceholderText(
        "optional: the spoken text (improves phoneme timing)")
    lip_btn = QtWidgets.QPushButton("Generate Keys")
    lip_btn.setToolTip(
        "<b>Lip sync</b><br>Analyse the audio, key the selected rig's viseme "
        "targets, and drop the clip on the time slider.")
    icons.decorate(lip_btn, "mood", 15)
    lip_btn.clicked.connect(self._lip_sync)
    g.addWidget(QtWidgets.QLabel("Audio"), 0, 0)
    g.addWidget(self.audio_edit, 0, 1)
    g.addWidget(audio_btn, 0, 2)
    g.addWidget(QtWidgets.QLabel("Text"), 1, 0)
    g.addWidget(self.dialog_edit, 1, 1, 1, 2)
    g.addWidget(lip_btn, 0, 3, 2, 1)
    g.setColumnStretch(1, 1)
    lip.content_layout().addLayout(g)
    v.addWidget(lip)

    # --- Idle -----------------------------------------------------------------------
    idle = CollapsibleFrame("Idle motion", expanded=False)
    h = QtWidgets.QHBoxLayout()
    self.idle_blink_chk = QtWidgets.QCheckBox("Blinks")
    self.idle_blink_chk.setChecked(True)
    self.idle_sway_chk = QtWidgets.QCheckBox("Head sway")
    self.idle_sway_chk.setChecked(True)
    idle_btn = QtWidgets.QPushButton("Add Idle Keys")
    idle_btn.setToolTip(
        "<b>Idle motion</b><br>Key random blinks and a gentle head sway over "
        "the playback range on the selected rig.")
    icons.decorate(idle_btn, "shuffle", 15)
    idle_btn.clicked.connect(self._idle_keys)
    h.addWidget(self.idle_blink_chk)
    h.addWidget(self.idle_sway_chk)
    h.addStretch(1)
    h.addWidget(idle_btn)
    idle.content_layout().addLayout(h)
    v.addWidget(idle)

    # --- Export -----------------------------------------------------------------------
    exp = CollapsibleFrame("Export", expanded=True)
    h = QtWidgets.QHBoxLayout()
    fbx_btn = QtWidgets.QPushButton("Export Rig (FBX)…")
    fbx_btn.setToolTip("<b>Export FBX</b><br>Selected baked rig → FBX with "
                       "blendshapes + skin.")
    icons.decorate(fbx_btn, "download", 15)
    fbx_btn.clicked.connect(self._export_fbx)
    copy_btn = QtWidgets.QPushButton("Static Copy")
    copy_btn.setToolTip("<b>Static copy</b><br>Duplicate the current head as "
                        "a plain mesh (no GNM link) — e.g. to keep a variant.")
    icons.decorate(copy_btn, "cube", 15)
    copy_btn.clicked.connect(self._static_copy)
    h.addWidget(fbx_btn)
    h.addWidget(copy_btn)
    h.addStretch(1)
    exp.content_layout().addLayout(h)
    v.addWidget(exp)
    v.addStretch(1)
    return self._scroll(container)

  # --- animate actions -------------------------------------------------------

  def _target_rig(self):
    """The rig to animate: selected baked rig, else the last one baked."""
    from gnm_maya.scene import animate
    for s in (mc.ls(selection=True, long=False) or []):
      try:
        animate.blendshape_of(s)
        return s
      except Exception:
        continue
    last = getattr(self, "_last_rig", None)
    if last and mc.objExists(last):
      return last
    raise RuntimeError("Select a baked GNM rig (or bake one first).")

  def _browse_audio(self):
    from gnm_maya.core import settings
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        self, "Choose dialogue audio", settings.last_photo_dir(),
        "Audio (*.wav *.ogg)")
    if path:
      self.audio_edit.setText(path)
      settings.set_last_photo_dir(path)

  def _lip_sync(self):
    from gnm_maya.scene import animate
    from gnm_maya.services import rhubarb
    audio = self.audio_edit.text().strip()
    if not audio:
      self._browse_audio()
      audio = self.audio_edit.text().strip()
      if not audio:
        return
    try:
      rig = self._target_rig()
      if not rhubarb.available():
        ans = mc.confirmDialog(
            title="Lip Sync", icon="question",
            message="Lip sync needs Rhubarb Lip Sync (~85 MB, MIT), "
                    "installed inside the module folder. Download now?",
            button=["Download", "Cancel"], defaultButton="Download",
            cancelButton="Cancel", dismissString="Cancel")
        if ans != "Download":
          return
        from gnm_maya.ui.progress import MayaProgress
        with MayaProgress("Rhubarb Lip Sync", maximum=1) as prog:
          rhubarb.ensure(lambda m: prog.set(1, m))
      self._busy_status("Analysing audio (Rhubarb)…")
      data = rhubarb.run(audio, self.dialog_edit.text().strip() or None)
      cues = data.get("mouthCues", [])
      n = animate.lip_sync(rig, cues)
      animate.attach_audio(audio)
      self.status.setText("Lip sync: %d mouth cues keyed on %s (%.1fs)."
                          % (n, rig, data.get("metadata", {})
                             .get("duration", 0.0)))
    except Exception as e:
      self._show_error("Lip Sync failed", e)

  def _idle_keys(self):
    from gnm_maya.scene import animate
    try:
      rig = self._target_rig()
      start = mc.playbackOptions(query=True, minTime=True)
      end = mc.playbackOptions(query=True, maxTime=True)
      n = animate.idle(rig, start, end,
                       blinks=self.idle_blink_chk.isChecked(),
                       sway=self.idle_sway_chk.isChecked(),
                       seed=self._rand_seed())
      self.status.setText("Idle: %d keys on %s (frames %d–%d)."
                          % (n, rig, start, end))
    except Exception as e:
      self._show_error("Idle keys failed", e)

  def _export_fbx(self):
    from gnm_maya.ui import tools as ui_tools
    if not (mc.ls(selection=True) or []):
      last = getattr(self, "_last_rig", None)
      if last and mc.objExists(last):
        mc.select(last, replace=True)
    ui_tools.export_selected_fbx()

  def _static_copy(self):
    if not self.head:
      return
    try:
      dup = mc.duplicate(self.head.transform,
                         name=self.head.transform + "_copy")[0]
      mc.select(dup, replace=True)
      self.status.setText("Static copy: %s" % dup)
    except Exception as e:
      self._show_error("Static copy failed", e)

  # --- slider value sync ---------------------------------------------------

  def _sync_sliders_from_head(self):
    """Set every visible slider to match the head's current coefficients."""
    if not self.head:
      return
    h = self.head
    for idx, w in self._id_sliders.items():
      w.set_value_silent(h.identity[idx])
    for idx, w in self._expr_sliders.items():
      w.set_value_silent(h.expression[idx])
    for (j, ax), w in self._pose_sliders.items():
      w.set_value_silent(h.rotations[j][ax])
    for ax, w in self._trans_sliders.items():
      w.set_value_silent(h.translation[ax])

  # --- slider callbacks ----------------------------------------------------

  def _schedule(self, action=None):
    """Throttle: run ``action`` (or a plain mesh refresh) on the next tick."""
    self._pending_action = action
    if not self._refresh_timer.isActive():
      self._refresh_timer.start()

  def _schedule_refresh(self):
    self._schedule(None)

  def _do_refresh(self):
    action = self._pending_action
    self._pending_action = None
    try:
      if action is not None:
        action()
      elif self.head:
        self.head.refresh()
    except Exception as e:
      self._show_error("Slider update failed", e)

  def _on_coeff(self, kind, idx, value):
    if not self.head:
      return
    try:
      # Stage the coefficient now (cheap); throttle the mesh repaint.
      if kind == "identity":
        self.head.set_identity(idx, value, update=False)
      else:
        changed = self.head.set_expression(idx, value, symmetry=self._symmetry,
                                           update=False)
        for j in changed:
          if j != idx and j in self._expr_sliders:
            self._expr_sliders[j].set_value_silent(value)
      self._schedule_refresh()
    except Exception as e:
      self._show_error("Coefficient edit failed", e)

  def _on_pose(self, joint, axis, value):
    if not self.head:
      return
    try:
      changed = self.head.set_rotation(joint, axis, value,
                                       symmetry=self._symmetry, update=False)
      for (mj, ax) in changed:
        if (mj, ax) != (joint, axis) and (mj, ax) in self._pose_sliders:
          self._pose_sliders[(mj, ax)].set_value_silent(value)
      self._schedule_refresh()
    except Exception as e:
      self._show_error("Pose edit failed", e)

  def _on_translation(self, axis, value):
    if not self.head:
      return
    try:
      self.head.set_translation(axis, value, update=False)
      self._schedule_refresh()
    except Exception as e:
      self._show_error("Translation edit failed", e)

  # --- per-tab actions -----------------------------------------------------

  def _rand_seed(self):
    import random
    return random.randint(0, 1 << 30)

  def _randomize_kind(self, kind):
    if not self.head:
      return
    scale = self._scale_value
    try:
      if kind == "identity":
        self.head.randomize_identity(scale=scale, seed=self._rand_seed())
      else:
        self.head.randomize_expression(scale=scale, seed=self._rand_seed(),
                                       symmetric=self._symmetry)
      self._sync_sliders_from_head()
      mc.select(self.head.transform, replace=True)
      self.status.setText("Randomized %s (scale=%.2f%s)."
                          % (kind, scale, ", symmetric" if self._symmetry
                             and kind == "expression" else ""))
      self._push_history()
    except Exception as e:
      self._show_error("Randomize %s failed" % kind, e)

  def _reset_kind(self, kind):
    if not self.head:
      return
    try:
      if kind == "identity":
        self.head.reset_identity()
      else:
        self.head.reset_expression()
      self._sync_sliders_from_head()
      self.status.setText("Reset %s." % kind)
      self._push_history()
    except Exception as e:
      self._show_error("Reset %s failed" % kind, e)

  @staticmethod
  def _zero_slider_silent(slider):
    """Snap a mix slider back to 0 without firing its blend callback."""
    if slider is None:
      return
    slider.blockSignals(True)
    slider.setValue(0)
    slider.blockSignals(False)

  def _reset_semantic_identity(self):
    """Semantic-tab 'Reset': neutral identity + clear the ethnicity mix."""
    self._reset_kind("identity")
    self._zero_slider_silent(getattr(self, "blend_ethn_mix", None))

  def _reset_semantic_expression(self):
    """Semantic-tab 'Reset': neutral expression + clear the expression mix."""
    self._reset_kind("expression")
    self._zero_slider_silent(getattr(self, "blend_expr_mix", None))

  def _randomize_pose(self):
    if not self.head:
      return
    # Pose is in radians; scale down so a scale of 1.0 stays a natural range.
    scale = self._scale_value * 0.3
    try:
      self.head.randomize_pose(scale=scale, seed=self._rand_seed(),
                               symmetric=self._symmetry)
      self._sync_sliders_from_head()
      self.status.setText("Randomized pose%s."
                          % (" (symmetric)" if self._symmetry else ""))
      self._push_history()
    except Exception as e:
      self._show_error("Randomize pose failed", e)

  def _reset_pose(self):
    if not self.head:
      return
    try:
      self.head.reset_pose()
      self._sync_sliders_from_head()
      self.status.setText("Reset pose.")
      self._push_history()
    except Exception as e:
      self._show_error("Reset pose failed", e)

  def _reset_translation(self):
    if not self.head:
      return
    try:
      self.head.reset_translation()
      self._sync_sliders_from_head()
      self.status.setText("Reset translation.")
      self._push_history()
    except Exception as e:
      self._show_error("Reset translation failed", e)

  # --- shared / global actions ---------------------------------------------

  def _on_symmetry_toggled(self, on):
    self._symmetry = bool(on)
    logger.info("Symmetry %s", "ON" if on else "OFF")
    self.status.setText("Symmetry %s" % ("ON" if on else "OFF"))

  def _on_texture_toggled(self, on):
    if not self.head:
      return
    from gnm_maya.scene import material
    try:
      if on:
        f = material.apply_texture(self.head.transform, self._texture_path)
        material.set_viewport_textured(True)  # so the map is actually visible
        self.status.setText("Texture applied.")
        logger.info("Applied texture via '%s'", f)
      else:
        material.remove_texture(self.head.transform)
        material.set_viewport_textured(False)
        self.status.setText("Texture removed.")
    except Exception as e:
      self._show_error("Texture toggle failed", e)
      self.tex_chk.blockSignals(True)
      self.tex_chk.setChecked(False)
      self.tex_chk.blockSignals(False)

  def _browse_texture(self):
    from gnm_maya.core import settings
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        self, "Choose a texture image", settings.last_photo_dir(),
        "Images (*.png *.jpg *.jpeg *.tif *.tiff *.exr)")
    if not path:
      return
    settings.set_last_photo_dir(path)
    self._texture_path = path
    self.tex_chk.blockSignals(True)
    self.tex_chk.setChecked(True)
    self.tex_chk.blockSignals(False)
    self._on_texture_toggled(True)

  def _show_info(self):
    QtWidgets.QMessageBox.information(self, "How the sliders work", PCA_INFO)

  def _on_thumb_size(self, _index):
    """Live-resize every slider thumbnail (and its tooltip images)."""
    from gnm_maya.core import settings
    px = self.thumb_combo.currentData()
    self._thumb_px = int(px)
    settings.set_thumb_size(self._thumb_px)
    for w, kind, idx in self._coeff_meta:
      _mx, _mn, tip = self._slider_visuals(kind, idx, self._thumb_px)
      w.setToolTip(tip)
      w.set_icon_size(self._thumb_px)
    self.status.setText("Shape images: %s" % self.thumb_combo.currentText())

  def _bake_rig(self):
    if not self.head:
      return
    semantic = self.bake_sem_chk.isChecked()
    arkit = self.bake_arkit_chk.isChecked() and semantic
    visemes = self.bake_visemes_chk.isChecked() and semantic
    num_modes = self.bake_modes_spin.value()
    groups = self.head.topology.meta.get("expression_groups", [])
    if not semantic and num_modes == 0:
      self.status.setText("Nothing to bake (no targets selected).")
      return
    from gnm_maya.scene import rig
    try:
      n_targets = ((20 if semantic else 0) + (7 if visemes else 0)
                   + num_modes * max(1, len(groups)))
      self._busy_status("Baking rig (~%d targets + joints)…" % n_targets)
      name = rig.bake_rig(self.head, num_modes=num_modes, semantic=semantic,
                          arkit=arkit, visemes=visemes)
      self._last_rig = name
      self.status.setText("Baked rig: %s (%s)" % (name, ", ".join(
          x for x, on in (("expressions", semantic), ("ARKit names", arkit),
                          ("visemes", visemes)) if on) or "modes"))
    except Exception as e:
      self._show_error("Bake Rig failed", e)

  def _busy_status(self, msg):
    self.status.setText(msg)
    QtWidgets.QApplication.processEvents()

  def _on_landmarks_toggled(self, on):
    """Show/hide (creating on first use) the 68 landmark locators."""
    if not self.head:
      return
    from gnm_maya.scene import landmarks as lmk
    group = self.head.transform + "_landmarks"
    try:
      if on:
        if not mc.objExists(group):
          lmk.create_landmark_locators(self.head)
        mc.setAttr(group + ".visibility", 1)
        self.status.setText("Landmarks shown (drag to sculpt).")
      else:
        if mc.objExists(group):
          mc.setAttr(group + ".visibility", 0)
        if self.sculpt_chk.isChecked():
          self.sculpt_chk.setChecked(False)  # sculpting hidden pins = surprise
        self.status.setText("Landmarks hidden.")
    except Exception as e:
      self._show_error("Landmarks toggle failed", e)

  def _on_live_sculpt_toggled(self, on):
    """Arm/disarm the drag-release refit; needs the landmarks visible."""
    if not self.head:
      return
    from gnm_maya.ui import tools as ui_tools
    try:
      if on and not self.lmk_chk.isChecked():
        self.lmk_chk.setChecked(True)  # creates/shows the locators first
      if bool(on) != ui_tools.live_landmark_fit_active():
        ui_tools.toggle_live_landmark_fit()
      self.status.setText("Live Sculpt %s." % ("ON — drag a landmark; the "
                          "head follows on release" if on else "off"))
    except Exception as e:
      self.sculpt_chk.blockSignals(True)
      self.sculpt_chk.setChecked(False)
      self.sculpt_chk.blockSignals(False)
      self._show_error("Live Sculpt toggle failed", e)

  def _fit_photo(self):
    if not self.head:
      return
    from gnm_maya.services import fitting_deps
    if not fitting_deps.available():
      if not fitting_deps.install_with_dialog():
        return
    from gnm_maya.core import settings
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        self, "Choose a face photo", settings.last_photo_dir(),
        "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
    if not path:
      return
    settings.set_last_photo_dir(path)
    try:
      self._busy_status("Detecting landmarks + fitting identity…")
      self.head.fit_photo(path)
      self._sync_sliders_from_head()
      mc.select(self.head.transform, replace=True)
      self.status.setText("Fitted identity from photo (likeness, front-view "
                          "modes only).")
      self._push_history()
    except Exception as e:
      self._show_error("Fit from Photo failed", e)

  def _clear_range(self, kind, start, end):
    """Zero a contiguous coefficient range (used by a group's Reset button)."""
    if not self.head:
      return
    try:
      self.head.clear(kind, range(start, end + 1))
      logger.info("Reset %s group [%d..%d]", kind, start, end)
      self.status.setText("Reset %s group." % kind)
    except Exception as e:
      self._show_error("Group reset failed", e)

  def _selected_gnm_heads(self):
    return [n for n in (mc.ls(selection=True, long=False) or [])
            if self.head.is_gnm_head(n)]

  def _reset_all(self):
    """Reset the selected GNM head(s), or this panel's head if none selected."""
    if not self.head:
      return
    try:
      targets = self._selected_gnm_heads()
      reset_panel = (not targets) or (self.head.transform in targets)
      if reset_panel:
        for w in self._sliders:
          w.reset()
        self.head.reset_all()
      for m in targets:
        if m != self.head.transform:
          self.head.reset_mesh_to_template(m)
      keep = targets if targets else [self.head.transform]
      mc.select(keep, replace=True)
      logger.info("Reset %s", keep)
      self.status.setText("Reset: %s" % ", ".join(keep))
      self._push_history()
    except Exception as e:
      self._show_error("Reset failed", e)


def _open_progress(parent, text):
  """A busy progress dialog, force-painted so it never shows up blank/white.

  QProgressDialog only paints once the event loop spins; on a cold first load
  the heavy work starts immediately after show(), so we pump the loop and
  repaint explicitly before returning.
  """
  dlg = QtWidgets.QProgressDialog(text, None, 0, 0, parent)
  dlg.setWindowTitle("GNM")
  dlg.setWindowModality(QtCore.Qt.WindowModal)
  dlg.setMinimumDuration(0)
  dlg.setMinimumWidth(320)
  dlg.setCancelButton(None)
  dlg.show()
  for _ in range(3):  # let show/layout/paint events land before heavy work
    QtWidgets.QApplication.processEvents()
  dlg.repaint()
  return dlg


def _prewarm_worker(dlg):
  """Start the model worker (the ~1s cost) while ``dlg`` keeps animating.

  Returns without raising on failure (the panel build surfaces errors)."""
  import threading
  from gnm_maya.core import worker as _worker

  done = {"ok": False, "err": None}

  def run():
    try:
      _worker.get_worker()
      done["ok"] = True
    except Exception as e:  # surfaced by the panel build later
      done["err"] = e

  th = threading.Thread(target=run)
  th.start()
  while th.is_alive():           # keep the dialog animating + Maya responsive
    th.join(0.03)
    QtWidgets.QApplication.processEvents()
  if done["err"]:
    logger.warning("worker pre-warm failed: %s", done["err"])


def _check_updates_async_generic(mod, display_name, menu_hint):
  """Non-blocking upstream version check for either updater module; offers
  the update dialog if newer.

  ``mod`` is ``updater`` (GNM model) or ``tool_updater`` (this tool) — both
  expose check()/download_and_install()/short()/_post_update_dialog() with the
  same shapes, just pointed at different repos. Runs the network call on a
  thread and defers any UI to the main thread. All failures (offline,
  rate-limit) are silent — this is a courtesy check.
  """
  import threading
  import maya.utils

  def worker():
    try:
      info = mod.check()
    except Exception:
      return
    if not info.get("update_available"):
      logger.info("%s up to date (%s)", display_name,
                  mod.short(info["installed_sha"]))
      return

    def offer():
      from maya import cmds as mc
      ans = mc.confirmDialog(
          title="%s update available" % display_name,
          message=("A newer %s is available.\n\nInstalled: %s (%s)\n"
                   "Latest:    %s (%s)\n\nDownload now? (You can always use "
                   "%s later.)"
                   % (display_name,
                      mod.short(info["installed_sha"]),
                      info["installed_date"] or "?",
                      mod.short(info["latest_sha"]), info["latest_date"],
                      menu_hint)),
          button=["Download", "Skip"], defaultButton="Skip",
          cancelButton="Skip", dismissString="Skip")
      if ans != "Download":
        return
      mc.waitCursor(state=True)
      try:
        latest = mod.download_and_install()
      except Exception as e:
        mc.waitCursor(state=False)
        mc.confirmDialog(title="%s Update" % display_name, icon="critical",
                           message="Update failed:\n%s" % e, button=["OK"])
        return
      mc.waitCursor(state=False)
      mod._post_update_dialog(latest)

    maya.utils.executeDeferred(offer)

  threading.Thread(target=worker, daemon=True).start()


def _check_updates_async():
  """Courtesy check for the vendored GNM model (external/gnm_repo)."""
  from gnm_maya.services import updater
  _check_updates_async_generic(updater, "google/GNM",
                               "GNM > Check for GNM Model Updates")


def _check_tool_updates_async():
  """Courtesy check for this tool itself (gnm-maya)."""
  from gnm_maya.services import tool_updater
  _check_updates_async_generic(tool_updater, "gnm-maya tool",
                               "GNM > Check for gnm-maya Tool Updates")


def show():
  global _WINDOW
  parent = maya_main_window()
  for w in QtWidgets.QApplication.topLevelWidgets():
    if w.objectName() == _OBJECT_NAME:
      w.close()
      w.deleteLater()
  # First run: the runtime and the GNM repo are downloaded, not shipped.
  from gnm_maya.services import bootstrap
  if not bootstrap.all_available():
    if not bootstrap.ensure_all_with_dialog():
      return None
  _check_updates_async()       # courtesy check for the GNM model; user chooses
  _check_tool_updates_async()  # courtesy check for this tool itself
  # One progress dialog spans BOTH slow phases (model load + panel build) so
  # the first open never shows a blank white window.
  dlg = _open_progress(parent, "Loading GNM model…")
  try:
    _prewarm_worker(dlg)
    heads = find_heads(selected_only=True) or find_heads()
    target = heads[0] if heads else None
    dlg.setLabelText("Building panel…")
    QtWidgets.QApplication.processEvents()
    _WINDOW = GnmPanel(parent=parent, adopt_transform=target)
  except Exception as e:
    logger.exception("Failed to open the GNM panel")
    QtWidgets.QMessageBox.critical(
        parent, "GNM — Failed to open panel",
        "%s\n\nSee the Script Editor for the full traceback." % e)
    return None
  finally:
    dlg.close()
  _WINDOW.show()
  logger.info("Opened GNM panel (%s)",
              "adopted %s" % target if target else "new head")
  return _WINDOW
