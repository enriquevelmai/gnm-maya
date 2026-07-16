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
                                 MAX_PER_GROUP, COEFF_RANGE,
                                 POSE_RANGE, TRANS_RANGE)
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
    "    head.set_expression(97, 2.0)"
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
    from gnm_maya.core import settings as _settings
    self._thumb_px = _settings.thumb_size()

    # --- user-modifiable widgets (created here, laid out in populate_ui) ---
    self.status = QtWidgets.QLabel("Building head...")
    self.status.setWordWrap(True)
    self.sym_chk = QtWidgets.QCheckBox("Symmetry (L/R)")
    self.sym_chk.setToolTip(
        "Mirror edits across the left/right eye regions and eye joints.")
    self.tex_chk = QtWidgets.QCheckBox("Texture")
    self.tex_chk.setToolTip("Apply a PNG texture to the head (default: the "
                            "bundled GNM edgeflow map).")
    self.tex_browse = QtWidgets.QPushButton("…")
    self.tex_browse.setFixedWidth(28)
    self.tex_browse.setToolTip("Choose a custom PNG texture.")
    self._texture_path = None  # None -> bundled edgeflow texture
    self.info_btn = QtWidgets.QPushButton("ⓘ")
    self.info_btn.setFixedWidth(28)
    self.info_btn.setToolTip("How these sliders work (PCA-style basis).")
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
        "Reset the selected GNM head(s) to the neutral template.\n"
        "With nothing selected, resets this panel's head.")
    self.fit_btn = QtWidgets.QPushButton("Fit from Photo…")
    self.fit_btn.setToolTip(
        "Detect 68 facial landmarks in a photo (MediaPipe, local) and fit the\n"
        "identity coefficients to them. Gives a likeness, not a scan match.")
    self.bake_btn = QtWidgets.QPushButton("Bake Rig")
    self.bake_btn.setToolTip(
        "Bake this head into a self-sufficient rigged mesh:\n"
        "- blendShape with the 20 named expressions (keyframable)\n"
        "- neck/head/eye joints skinned with GNM's weights\n"
        "The result needs no GNM runtime and exports to FBX.")

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
      self.tabs.addTab(self._semantic_tab(), "Semantic")
      self.tabs.addTab(self._coeff_tab(meta["identity_groups"], "identity"),
                       "Identity")
      self.tabs.addTab(self._coeff_tab(meta["expression_groups"], "expression"),
                       "Expression")
      self.tabs.addTab(self._pose_tab(meta["joint_names"]), "Pose")
      self.tabs.addTab(self._translation_tab(), "Translation")

    bottom = QtWidgets.QHBoxLayout()
    bottom.addStretch(1)
    bottom.addWidget(self.fit_btn)
    bottom.addWidget(self.bake_btn)
    bottom.addWidget(self.reset_btn)
    outer.addLayout(bottom)

  def register_controllers(self):
    self.sym_chk.toggled.connect(self._on_symmetry_toggled)
    self.tex_chk.toggled.connect(self._on_texture_toggled)
    self.tex_browse.clicked.connect(self._browse_texture)
    self.reset_btn.clicked.connect(self._reset_all)
    self.fit_btn.clicked.connect(self._fit_photo)
    self.bake_btn.clicked.connect(self._bake_rig)
    self.thumb_combo.currentIndexChanged.connect(self._on_thumb_size)
    self.info_btn.clicked.connect(self._show_info)
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

  def _semantic_tab(self):
    """Sample identity (gender x ethnicity) and named expressions."""
    container = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(container)
    sem = self.head.topology.meta.get("semantic", {})

    if not sem.get("available"):
      msg = QtWidgets.QLabel(
          "Semantic sampler unavailable.\n\nIt needs the decoder models in the "
          "vendored GNM repo and the 'h5py' package in the bundled runtime. "
          "Rebuild the runtime (build_module.py) or update GNM to enable it.")
      msg.setWordWrap(True)
      msg.setAlignment(QtCore.Qt.AlignTop)
      v.addWidget(msg)
      v.addStretch(1)
      return container

    def _pretty(x):
      return x.replace("_", " ").title()

    idbox = QtWidgets.QGroupBox("Identity")
    il = QtWidgets.QFormLayout(idbox)
    self.sem_gender = QtWidgets.QComboBox()
    self.sem_gender.addItems([_pretty(g) for g in sem["gender"]])
    self.sem_ethnicity = QtWidgets.QComboBox()
    self.sem_ethnicity.addItems([_pretty(e) for e in sem["ethnicity"]])
    id_btn = QtWidgets.QPushButton("Sample Identity")
    id_btn.clicked.connect(self._sample_identity)
    il.addRow("Gender", self.sem_gender)
    il.addRow("Ethnicity", self.sem_ethnicity)
    il.addRow(id_btn)

    exbox = QtWidgets.QGroupBox("Expression")
    el = QtWidgets.QFormLayout(exbox)
    self.sem_expr = QtWidgets.QComboBox()
    self.sem_expr.addItems([_pretty(e) for e in sem["expression"]])
    ex_btn = QtWidgets.QPushButton("Sample Expression")
    ex_btn.clicked.connect(self._sample_expression)
    el.addRow("Expression", self.sem_expr)
    el.addRow(ex_btn)

    # Natural-language description (local lexicon, or local Ollama if running).
    descbox = QtWidgets.QGroupBox("Describe")
    dl = QtWidgets.QHBoxLayout(descbox)
    self.desc_edit = QtWidgets.QLineEdit()
    self.desc_edit.setPlaceholderText(
        "e.g. 'a very happy asian woman, winking left'")
    desc_btn = QtWidgets.QPushButton("Apply")
    desc_btn.clicked.connect(self._apply_description)
    self.desc_edit.returnPressed.connect(self._apply_description)
    dl.addWidget(self.desc_edit, 1)
    dl.addWidget(desc_btn)
    v.addWidget(descbox)

    row = QtWidgets.QHBoxLayout()
    row.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
    row.addWidget(idbox)
    row.addWidget(exbox)
    v.addLayout(row)

    v.addWidget(self._blend_box(sem, _pretty))

    note = QtWidgets.QLabel(
        "Categorical sampling draws a fresh random latent (repeated clicks "
        "vary). Blending fixes the latent, so dragging a Mix slider "
        "interpolates smoothly between the two chosen classes; 'Re-roll' picks "
        "a new latent. Sliders on the other tabs update to match.")
    note.setWordWrap(True)
    v.addWidget(note)
    v.addStretch(1)
    return container

  def _mix_slider(self):
    s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    s.setRange(0, 100)
    s.setValue(0)
    lbl = QtWidgets.QLabel("0.00")
    lbl.setFixedWidth(34)
    s.valueChanged.connect(lambda v: lbl.setText("%.2f" % (v / 100.0)))
    return s, lbl

  def _blend_box(self, sem, pretty):
    box = QtWidgets.QGroupBox("Blending  (drag Mix to interpolate)")
    g = QtWidgets.QGridLayout(box)

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
    reset.clicked.connect(self._reset_mixes)
    reroll = QtWidgets.QPushButton("Re-roll")
    reroll.setToolTip("Pick a new random latent for the blends.")
    reroll.clicked.connect(self._reroll_blend)
    g.addWidget(reset, 2, 1)
    g.addWidget(reroll, 2, 3)
    return box

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
      self._sync_sliders_from_head()
      mc.select(self.head.transform, replace=True)
      self.status.setText("Sampled identity: %s / %s" % (
          self.sem_gender.currentText(), self.sem_ethnicity.currentText()))
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
    except Exception as e:
      self._show_error("Sample expression failed", e)

  def _coeff_tab(self, groups, kind):
    container = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(container)

    rnd = QtWidgets.QPushButton("Randomize %s" % kind.capitalize())
    rnd.clicked.connect(lambda: self._randomize_kind(kind))
    rst = QtWidgets.QPushButton("Reset %s" % kind.capitalize())
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
    container = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(container)

    rnd = QtWidgets.QPushButton("Randomize Pose")
    rnd.clicked.connect(self._randomize_pose)
    rst = QtWidgets.QPushButton("Reset Pose")
    rst.clicked.connect(self._reset_pose)
    v.addWidget(self._tab_header([rnd] + self._make_scale_controls() + [rst]))

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
    v.addWidget(self._scroll(host), 1)
    return container

  def _translation_tab(self):
    container = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(container)

    rst = QtWidgets.QPushButton("Reset Translation")
    rst.clicked.connect(self._reset_translation)
    v.addWidget(self._tab_header([rst]))

    host = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(host)
    row.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
    box = QtWidgets.QGroupBox("global")
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
    except Exception as e:
      self._show_error("Reset %s failed" % kind, e)

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
    except Exception as e:
      self._show_error("Randomize pose failed", e)

  def _reset_pose(self):
    if not self.head:
      return
    try:
      self.head.reset_pose()
      self._sync_sliders_from_head()
      self.status.setText("Reset pose.")
    except Exception as e:
      self._show_error("Reset pose failed", e)

  def _reset_translation(self):
    if not self.head:
      return
    try:
      self.head.reset_translation()
      self._sync_sliders_from_head()
      self.status.setText("Reset translation.")
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
    # Options dialog: semantic set and/or N individual basis-mode targets.
    dlg = QtWidgets.QDialog(self)
    dlg.setWindowTitle("Bake Rig options")
    form = QtWidgets.QFormLayout(dlg)
    sem_chk = QtWidgets.QCheckBox()
    sem_chk.setChecked(True)
    sem_chk.setToolTip("One target per named expression (happy, wink_left, …).")
    groups = self.head.topology.meta.get("expression_groups", [])
    max_group = max((end - start + 1 for _n, start, end in groups), default=150)
    modes_spin = QtWidgets.QSpinBox()
    modes_spin.setRange(0, max_group)
    modes_spin.setValue(0)
    modes_spin.setToolTip(
        "Additionally bake the first N basis modes OF EACH region\n"
        "(%d regions: %s) as individual targets, named by basis\n"
        "(e.g. left_eye_region_000, lower_face_region_000, ...)."
        % (len(groups), ", ".join(n for n, _s, _e in groups)))
    form.addRow("20 semantic expressions", sem_chk)
    form.addRow("Basis modes per region", modes_spin)
    btns = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    form.addRow(btns)
    run_dialog = getattr(dlg, "exec_", None) or dlg.exec  # PySide2 vs 6
    if not run_dialog():
      return
    semantic = sem_chk.isChecked()
    num_modes = modes_spin.value()
    if not semantic and num_modes == 0:
      self.status.setText("Nothing to bake (no targets selected).")
      return

    from gnm_maya.scene import rig
    try:
      n_targets = (20 if semantic else 0) + num_modes * max(1, len(groups))
      self._busy_status("Baking rig (~%d targets + joints)…" % n_targets)
      name = rig.bake_rig(self.head, num_modes=num_modes, semantic=semantic)
      self.status.setText("Baked rig: %s (sliders + joints)" % name)
    except Exception as e:
      self._show_error("Bake Rig failed", e)

  def _busy_status(self, msg):
    self.status.setText(msg)
    QtWidgets.QApplication.processEvents()

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
