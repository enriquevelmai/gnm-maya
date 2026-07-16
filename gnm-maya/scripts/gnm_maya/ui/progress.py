"""Native Maya progress bar for long main-thread tasks (bake, crowd, fit).

Wraps mc.progressWindow as a context manager; no-ops in batch/mayapy so
headless tests and scripts run unchanged.

  with MayaProgress("Baking rig", maximum=25) as p:
    for i, item in enumerate(items):
      p.set(i, "target %s" % item)
      ...

The window is force-painted when it opens and on every update. A progress
window created right before a long, main-thread blocking call (e.g. the
first-run download during drag-and-drop install) otherwise shows up blank
white, because Maya's Qt event loop never gets a turn to paint it before the
work starts. Pumping the event queue a few times gives it that turn.
"""

from __future__ import annotations

import logging

from maya import cmds as mc

logger = logging.getLogger(__name__)


def _pump(times=3):
  """Let Qt paint/layout the progress window before we block the main thread."""
  try:
    from PySide6 import QtWidgets
  except ImportError:
    try:
      from PySide2 import QtWidgets
    except ImportError:
      QtWidgets = None
  app = QtWidgets.QApplication.instance() if QtWidgets else None
  for _ in range(max(1, times)):
    try:
      mc.refresh(force=True)
    except Exception:
      pass
    if app is not None:
      app.processEvents()


class MayaProgress(object):

  def __init__(self, title, maximum=100):
    self.title = title
    self.maximum = max(1, int(maximum))
    self.enabled = not mc.about(batch=True)

  def __enter__(self):
    if self.enabled:
      try:
        mc.progressWindow(title=self.title, status="%s…" % self.title, min=0,
                            max=self.maximum, progress=0, isInterruptable=False)
        # Give the freshly-created window room to actually paint before the
        # caller starts blocking the main thread (otherwise it shows white).
        _pump()
      except Exception:
        self.enabled = False
    return self

  def set(self, value, status=None):
    if not self.enabled:
      return
    try:
      kwargs = {"edit": True, "progress": min(int(value), self.maximum)}
      if status:
        kwargs["status"] = status
      mc.progressWindow(**kwargs)
      _pump(times=1)  # repaint the new value/status before we block again
    except Exception:
      pass

  def __exit__(self, *exc):
    if self.enabled:
      try:
        mc.progressWindow(endProgress=True)
      except Exception:
        pass
    return False
