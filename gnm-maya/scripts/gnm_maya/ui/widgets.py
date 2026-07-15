"""Reusable slider widgets for the GNM panel.

TickSlider: vertical slider that resets to zero on double-click.
VSlider: labeled vertical slider with optional min/max shape thumbnails.
CoeffGroup: one body-part group of coefficient sliders with lazy 'Show all'.

The widgets are dumb views: they render state and forward user input through
callbacks; all decisions live in the panel (controller) / core.head (model).
"""

from __future__ import annotations

try:
  from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:  # Maya 2022-2024
  from PySide2 import QtWidgets, QtCore, QtGui

MAX_PER_GROUP = 12      # sliders shown per body-part group before "Show all"
COEFF_RANGE = 300       # +/- 3.0 sigma for identity/expression
POSE_RANGE = 157        # +/- ~90 deg (radians * 100)
TRANS_RANGE = 500       # +/- 5.0 units


class TickSlider(QtWidgets.QSlider):
  """Vertical slider that resets to zero on double-click."""

  def mouseDoubleClickEvent(self, event):
    self.setValue(0)  # fires valueChanged -> callback
    super(TickSlider, self).mouseDoubleClickEvent(event)


class VSlider(QtWidgets.QWidget):
  """A labeled vertical slider: optional shape thumbnail + title on top,
  value below, live callback. Double-click the slider to return it to 0.
  """

  def __init__(self, title, rng, divisor, decimals, on_change, tooltip="",
               icon_path=None, icon_path_min=None):
    super(VSlider, self).__init__()
    self._div = float(divisor)
    self._dec = decimals
    self._on_change = on_change

    lay = QtWidgets.QVBoxLayout(self)
    lay.setContentsMargins(2, 2, 2, 2)
    lay.setSpacing(2)

    # Range visuals: MAX image above the slider (drag up -> +3 goes there),
    # MIN image below it (drag down -> -3). Built empty; sized by
    # set_icon_size so the panel can live-resize/hide them.
    self._icon_path = icon_path        # max (+3)
    self._icon_path_min = icon_path_min
    self._pic = None                   # max, above
    self._pic_min = None               # min, below
    if icon_path:
      self._pic = QtWidgets.QLabel()
      self._pic.setAlignment(QtCore.Qt.AlignHCenter)
      lay.addWidget(self._pic)

    self.title = QtWidgets.QLabel(title)
    self.title.setAlignment(QtCore.Qt.AlignHCenter)
    self.s = TickSlider(QtCore.Qt.Vertical)
    self.s.setRange(-rng, rng)
    self.s.setValue(0)
    self.s.setFixedHeight(130)
    self.s.setTickPosition(QtWidgets.QSlider.TicksBothSides)
    self.s.setTickInterval(rng)
    self.s.setPageStep(max(1, rng // 10))
    self.val = QtWidgets.QLabel("0." + "0" * decimals)
    self.val.setAlignment(QtCore.Qt.AlignHCenter)

    if tooltip:
      if tooltip.lstrip().startswith("<"):  # rich-text tooltip, hint included
        self.setToolTip(tooltip)
      else:
        self.setToolTip(tooltip + "\n(double-click slider to reset)")

    lay.addWidget(self.title)
    lay.addWidget(self.s, 0, QtCore.Qt.AlignHCenter)
    if icon_path_min:
      self._pic_min = QtWidgets.QLabel()
      self._pic_min.setAlignment(QtCore.Qt.AlignHCenter)
      lay.addWidget(self._pic_min)
    lay.addWidget(self.val)

    self.s.valueChanged.connect(self._changed)

  def _fmt(self, fv):
    return ("%." + str(self._dec) + "f") % fv

  def _changed(self, v):
    fv = v / self._div
    self.val.setText(self._fmt(fv))
    self._on_change(fv)

  def set_value_silent(self, fv):
    self.s.blockSignals(True)
    self.s.setValue(int(round(fv * self._div)))
    self.val.setText(self._fmt(fv))
    self.s.blockSignals(False)

  def set_icon_size(self, px):
    """Resize (or hide, px=0) the min/max shape thumbnails."""
    for pic, path in ((self._pic, self._icon_path),
                      (self._pic_min, self._icon_path_min)):
      if not pic:
        continue
      if px <= 0 or not path:
        pic.setVisible(False)
        continue
      pm = QtGui.QPixmap(path)
      if pm.isNull():
        pic.setVisible(False)
        continue
      pic.setPixmap(pm.scaledToWidth(int(px), QtCore.Qt.SmoothTransformation))
      pic.setVisible(True)

  def reset(self):
    self.set_value_silent(0.0)


class CoeffGroup(QtWidgets.QGroupBox):
  """One body-part group of coefficient sliders with lazy 'Show all' expand.

  Sliders wrap into a grid (MAX_PER_GROUP columns) so expanded groups read as a
  block, not one very wide row.
  """

  def __init__(self, panel, kind, label, start, end):
    super(CoeffGroup, self).__init__(label)
    self.panel = panel
    self.kind = kind
    self.start = start
    self.end = end
    self.total = end - start + 1
    self.shown = min(MAX_PER_GROUP, self.total)
    self._cols = MAX_PER_GROUP
    self._widgets = []        # every slider in this group, in order
    self._extra_built = False
    self._expanded = False

    self.setToolTip("%d modes, ordered by importance (m0 = largest variation)."
                    % self.total)

    v = QtWidgets.QVBoxLayout(self)

    # Buttons at the TOP so they stay visible when the grid expands downward.
    btns = QtWidgets.QHBoxLayout()
    if self.total > self.shown:
      self.toggle_btn = QtWidgets.QPushButton("Show all %d ▸" % self.total)
      self.toggle_btn.setToolTip(
          "Show every mode in this group (%d total)." % self.total)
      self.toggle_btn.clicked.connect(self._toggle)
      btns.addWidget(self.toggle_btn)
    else:
      self.toggle_btn = None
    reset_btn = QtWidgets.QPushButton("Reset")
    reset_btn.clicked.connect(self.reset)
    btns.addWidget(reset_btn)
    btns.addStretch(1)
    v.addLayout(btns)

    # Sliders live in their own grid so the buttons above never get pushed off.
    self._grid = QtWidgets.QGridLayout()
    self._grid.setHorizontalSpacing(2)
    self._grid.setVerticalSpacing(4)
    v.addLayout(self._grid)
    v.addStretch(1)  # keep the grid packed at the top of the group
    for k in range(self.shown):
      self._add_slider(start + k)

  def _add_slider(self, idx):
    k = len(self._widgets)
    w = self.panel._make_coeff_slider(
        self.kind, idx, title="m%d" % (idx - self.start))
    self._grid.addWidget(w, k // self._cols, k % self._cols)
    self._widgets.append(w)

  def _all_sliders(self):
    return list(self._widgets)

  def _toggle(self):
    if not self._extra_built:
      for idx in range(self.start + self.shown, self.end + 1):
        self._add_slider(idx)
      self._extra_built = True
      self._expanded = True
      self.panel._sync_sliders_from_head()  # fill freshly-built sliders
    else:
      self._expanded = not self._expanded
      for w in self._widgets[self.shown:]:
        w.setVisible(self._expanded)
    self.toggle_btn.setText(
        ("Show first %d ◂" % self.shown) if self._expanded
        else ("Show all %d ▸" % self.total))

  def reset(self):
    for w in self._widgets:
      w.reset()
    self.panel._clear_range(self.kind, self.start, self.end)
