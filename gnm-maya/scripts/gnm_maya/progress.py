"""Native Maya progress bar for long main-thread tasks (bake, crowd, fit).

Wraps cmds.progressWindow as a context manager; no-ops in batch/mayapy so
headless tests and scripts run unchanged.

  with MayaProgress("Baking rig", maximum=25) as p:
    for i, item in enumerate(items):
      p.set(i, "target %s" % item)
      ...
"""

from __future__ import annotations

import logging

import maya.cmds as cmds

logger = logging.getLogger(__name__)


class MayaProgress(object):

  def __init__(self, title, maximum=100):
    self.title = title
    self.maximum = max(1, int(maximum))
    self.enabled = not cmds.about(batch=True)

  def __enter__(self):
    if self.enabled:
      try:
        cmds.progressWindow(title=self.title, status=self.title, min=0,
                            max=self.maximum, progress=0, isInterruptable=False)
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
      cmds.progressWindow(**kwargs)
      cmds.refresh(suspend=False)
    except Exception:
      pass

  def __exit__(self, *exc):
    if self.enabled:
      try:
        cmds.progressWindow(endProgress=True)
      except Exception:
        pass
    return False
