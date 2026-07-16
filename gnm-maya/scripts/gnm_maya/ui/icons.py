"""Material-style icon loading for the GNM UI.

The module ships a set of monochrome SVGs in ``<module>/icons/`` (see that
folder). They are authored in a dark ink colour and *tinted at load time* to a
light grey that reads on Maya's dark theme — so one SVG serves normal and
disabled states, and the same art works on light themes if the colour is
overridden.

Two consumers:

* Qt widgets (buttons, the window) take a ``QIcon`` from :func:`icon` /
  :func:`decorate` / :func:`window_icon`.
* Maya's ``shelfButton``/``menuItem`` want an image *file path*, not a QIcon,
  so :func:`image_file` rasterises the tinted SVG to a cached PNG under
  ``icons/_cache/`` and returns its path (regenerated only when missing).

Everything degrades gracefully: if Qt, the SVG, or the cache dir is
unavailable, callers get a null QIcon or an empty path and the UI simply shows
no icon rather than raising.
"""

from __future__ import annotations

import logging
import os

try:
  from PySide6 import QtGui, QtCore
except ImportError:  # Maya 2022-2024
  from PySide2 import QtGui, QtCore

from gnm_maya.core import config

logger = logging.getLogger(__name__)

ICON_DIR = os.path.join(config.MODULE_ROOT, "icons")
_CACHE_DIR = os.path.join(ICON_DIR, "_cache")

# Maya's dark theme: a light grey icon reads best next to the button label.
DEFAULT_COLOR = "#cfcfcf"
DISABLED_COLOR = "#7a7a7a"

_qicon_cache = {}   # (name, size, color) -> QIcon
_png_cache = {}     # (name, size, color) -> path (in-process memo)


def _svg_path(name):
  p = os.path.join(ICON_DIR, name + ".svg")
  return p if os.path.isfile(p) else None


def _render_svg(path, size):
  """Rasterise an SVG to a QPixmap at ``size`` px (crisp, not upscaled)."""
  try:
    try:
      from PySide6 import QtSvg
    except ImportError:
      from PySide2 import QtSvg
    renderer = QtSvg.QSvgRenderer(path)
    img = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(img)
    renderer.render(painter)
    painter.end()
    return QtGui.QPixmap.fromImage(img)
  except Exception:
    # No QtSvg module/plugin: fall back to QPixmap's own SVG loader (present in
    # Maya via the qsvg image-format plugin), scaled to the target size.
    px = QtGui.QPixmap(path)
    if px.isNull():
      return px
    return px.scaled(size, size, QtCore.Qt.KeepAspectRatio,
                     QtCore.Qt.SmoothTransformation)


def _tint(pixmap, color):
  """Recolour every opaque pixel to ``color``, preserving the alpha edges."""
  if pixmap.isNull():
    return pixmap
  out = QtGui.QPixmap(pixmap.size())
  out.fill(QtCore.Qt.transparent)
  painter = QtGui.QPainter(out)
  painter.drawPixmap(0, 0, pixmap)
  painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
  painter.fillRect(out.rect(), QtGui.QColor(color))
  painter.end()
  return out


def icon(name, size=16, color=None):
  """A tinted ``QIcon`` for ``name`` (a file stem in ``icons/``).

  Returns a null QIcon if the SVG or Qt rendering is unavailable — callers can
  ``setIcon`` it unconditionally; Qt just shows nothing.
  """
  color = color or DEFAULT_COLOR
  key = (name, int(size), color)
  cached = _qicon_cache.get(key)
  if cached is not None:
    return cached
  path = _svg_path(name)
  if not path:
    ic = QtGui.QIcon()
  else:
    # Render at 2x so the icon stays sharp on high-DPI displays.
    base = _render_svg(path, int(size) * 2)
    ic = QtGui.QIcon()
    normal = _tint(base, color)
    if not normal.isNull():
      ic.addPixmap(normal, QtGui.QIcon.Normal)
      ic.addPixmap(_tint(base, DISABLED_COLOR), QtGui.QIcon.Disabled)
  _qicon_cache[key] = ic
  return ic


def decorate(widget, name, size=16, color=None):
  """Set ``name`` as ``widget``'s icon (a QPushButton/QToolButton). No-op if
  the icon can't be built. Returns the widget for chaining."""
  ic = icon(name, size, color)
  if not ic.isNull():
    widget.setIcon(ic)
    widget.setIconSize(QtCore.QSize(int(size), int(size)))
  return widget


def window_icon(color="#e6e6e6"):
  """Multi-resolution GNM app icon for ``setWindowIcon``."""
  ic = QtGui.QIcon()
  path = _svg_path("gnm")
  if path:
    for s in (16, 24, 32, 48, 64):
      px = _tint(_render_svg(path, s), color)
      if not px.isNull():
        ic.addPixmap(px)
  return ic


def image_file(name, size=24, color="#d6d6d6"):
  """Path to a tinted PNG of ``name`` for Maya ``menuItem``/``shelfButton``
  ``image=`` (which want a file, not a QIcon). Cached on disk under
  ``icons/_cache``; returns "" if it cannot be produced (caller then omits the
  image and Maya shows a text-only item)."""
  key = (name, int(size), color)
  memo = _png_cache.get(key)
  if memo is not None:
    return memo
  result = ""
  try:
    path = _svg_path(name)
    if path:
      if not os.path.isdir(_CACHE_DIR):
        os.makedirs(_CACHE_DIR)
      safe = color.lstrip("#")
      out = os.path.join(_CACHE_DIR, "%s_%d_%s.png" % (name, int(size), safe))
      if not os.path.isfile(out):
        px = _tint(_render_svg(path, int(size)), color)
        if not px.isNull():
          px.save(out, "PNG")
      if os.path.isfile(out):
        result = out
  except Exception:
    logger.debug("icon PNG export failed for %s", name, exc_info=True)
    result = ""
  _png_cache[key] = result
  return result
