"""Collect and display all licenses — this module's own plus every bundled /
vendored component (GNM, CPython runtime, and each pip dependency).

`collect()` is pure (no Qt) and testable headlessly; `show()` opens a Qt viewer.
"""

from __future__ import annotations

import glob
import logging
import os

from gnm_maya import config

logger = logging.getLogger(__name__)

_ROOT = config.MODULE_ROOT
_PARENT = os.path.dirname(_ROOT)  # repo root (holds the .md docs)
_SITE = os.path.join(_ROOT, "runtime", "Lib", "site-packages")


def _read(path):
  try:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
      return f.read()
  except Exception as e:
    return None


def _read_first(name):
  """Read ``name`` from the module root or the repo root (whichever exists)."""
  for base in (_ROOT, _PARENT):
    text = _read(os.path.join(base, name))
    if text:
      return text
  return None


def _dep_licenses():
  """(title, text) for each dist-info under the runtime's site-packages."""
  out = []
  for info in sorted(glob.glob(os.path.join(_SITE, "*.dist-info"))):
    name = os.path.basename(info).replace(".dist-info", "")
    text = None
    # Prefer an explicit LICENSE file (top-level or under licenses/).
    for pat in ("LICENSE*", "COPYING*", os.path.join("licenses", "*")):
      hits = glob.glob(os.path.join(info, pat))
      if hits:
        text = _read(hits[0])
        if text:
          break
    if not text:
      # Fall back to the License field / classifiers in METADATA.
      meta = _read(os.path.join(info, "METADATA")) or ""
      lines = [ln for ln in meta.splitlines()
               if ln.startswith("License:")
               or "License ::" in ln]
      text = "\n".join(lines) or "(license text not found; see METADATA)"
    out.append(("Dependency: %s" % name, text))
  return out


def collect():
  """Ordered list of (title, text) covering own + all bundled licenses."""
  items = []
  # Required: this module's own MIT license (always ships inside gnm-maya).
  mit = _read_first("LICENSE")
  if mit:
    items.append(("This module (MIT)", mit))
  else:
    logger.warning("MIT LICENSE not found")
  # Optional: NOTICE.md is a repo-root doc; it isn't shipped when the module is
  # installed on its own, so its absence is expected — don't warn.
  notice = _read_first("NOTICE.md")
  if notice:
    items.append(("NOTICE (attributions)", notice))
  else:
    logger.debug("NOTICE.md not bundled with the module")
  # GNM's license comes straight from the vendored upstream repo.
  gnm_lic = _read(os.path.join(_ROOT, "external", "gnm_repo", "LICENSE"))
  if gnm_lic:
    items.append(("Google GNM (Apache-2.0)", gnm_lic))
  runtime_lic = _read(os.path.join(_ROOT, "runtime", "LICENSE.txt"))
  if runtime_lic:
    items.append(("CPython runtime (PSF)", runtime_lic))
  items.extend(_dep_licenses())
  logger.info("Collected %d license documents", len(items))
  return items


# --- Qt viewer -------------------------------------------------------------

def show(parent=None):
  try:
    from PySide6 import QtWidgets
  except ImportError:
    from PySide2 import QtWidgets

  if parent is None:
    from gnm_maya import ui
    parent = ui.maya_main_window()

  items = collect()

  dlg = QtWidgets.QDialog(parent)
  dlg.setWindowTitle("GNM — Licenses")
  dlg.resize(760, 560)
  lay = QtWidgets.QHBoxLayout(dlg)

  listw = QtWidgets.QListWidget()
  listw.setMaximumWidth(260)
  text = QtWidgets.QPlainTextEdit()
  text.setReadOnly(True)
  text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
  try:
    from PySide6 import QtGui
    text.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
  except Exception:
    pass

  for title, _ in items:
    listw.addItem(title)

  def _select(row):
    if 0 <= row < len(items):
      text.setPlainText(items[row][1])

  listw.currentRowChanged.connect(_select)
  lay.addWidget(listw)
  lay.addWidget(text, 1)
  if items:
    listw.setCurrentRow(0)

  dlg.show()
  return dlg
