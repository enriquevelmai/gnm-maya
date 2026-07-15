"""Preset library: save/load named GNM head coefficient snapshots.

Presets are plain JSON files (identity/expression/rotations/translation) in
``~/Documents/maya/gnm_presets``, optionally paired with a 128px viewport
thumbnail. Loading tolerates dimension mismatches (e.g. presets saved with an
older model) by copying the overlapping coefficient range only.
"""

from __future__ import annotations

import datetime
import json
import logging
import os

import maya.cmds as cmds

logger = logging.getLogger(__name__)

from gnm_maya import settings


def _presets_dir():
  """User-configurable preset folder (see gnm_maya.settings)."""
  return settings.presets_dir()
_THUMB_SIZE = 128


def _preset_path(name):
  return os.path.join(_presets_dir(), "%s.json" % name)


def _thumbnail_path(name):
  return os.path.join(_presets_dir(), "%s.png" % name)


def save_preset(head, name):
  """Save ``head``'s coefficients (and a viewport thumbnail) as ``name``.

  Args:
    head: A ``GnmHead`` controller.
    name: Preset name (used as the file stem inside :data:`_presets_dir()`).

  Returns:
    The path of the written JSON preset file.
  """
  if not os.path.isdir(_presets_dir()):
    os.makedirs(_presets_dir())
  path = _preset_path(name)
  data = {
      "identity": [float(x) for x in head.identity],
      "expression": [float(x) for x in head.expression],
      "rotations": [[float(a) for a in r] for r in head.rotations],
      "translation": [float(x) for x in head.translation],
      "saved": datetime.datetime.now().isoformat(timespec="seconds"),
  }
  with open(path, "w") as f:
    json.dump(data, f, indent=2)

  # Thumbnail is best-effort: batch/mayapy has no viewport, and a failed
  # playblast must never fail the save itself.
  try:
    frame = cmds.currentTime(query=True)
    cmds.playblast(
        frame=[frame],
        format="image",
        compression="png",
        completeFilename=_thumbnail_path(name),
        widthHeight=[_THUMB_SIZE, _THUMB_SIZE],
        showOrnaments=False,
        viewer=False,
        percent=100,
        forceOverwrite=True,
    )
  except Exception:
    logger.info("No thumbnail for preset '%s' (no viewport?)", name)

  logger.info("Saved preset '%s' -> %s", name, path)
  return path


def list_presets():
  """All saved presets, sorted by name.

  Returns:
    A list of dicts with keys ``name``, ``path``, ``saved`` and (when a
    thumbnail image exists) ``thumbnail``.
  """
  if not os.path.isdir(_presets_dir()):
    return []
  out = []
  for fname in sorted(os.listdir(_presets_dir())):
    if not fname.endswith(".json"):
      continue
    name = fname[:-len(".json")]
    path = os.path.join(_presets_dir(), fname)
    saved = ""
    try:
      with open(path) as f:
        saved = json.load(f).get("saved", "")
    except Exception:
      logger.warning("Skipping unreadable preset file: %s", path)
      continue
    entry = {"name": name, "path": path, "saved": saved}
    thumb = _thumbnail_path(name)
    if os.path.isfile(thumb):
      entry["thumbnail"] = thumb
    out.append(entry)
  return out


def load_preset(head, name_or_path):
  """Apply a saved preset to ``head`` (updates the mesh in place).

  Dimension mismatches (preset saved against a different model build) are
  tolerated: only the overlapping coefficient range is copied, the rest keeps
  the head's current values.

  Args:
    head: A ``GnmHead`` controller to receive the coefficients.
    name_or_path: Preset name (looked up in :data:`_presets_dir()`) or a direct
      path to a preset JSON file.

  Returns:
    The path of the preset file that was loaded.
  """
  path = name_or_path if os.path.isfile(name_or_path) \
      else _preset_path(name_or_path)
  if not os.path.isfile(path):
    raise ValueError("Preset not found: %s" % name_or_path)
  with open(path) as f:
    data = json.load(f)

  _copy_into(head.identity, data.get("identity", []))
  _copy_into(head.expression, data.get("expression", []))
  _copy_into(head.translation, data.get("translation", []))
  rotations = data.get("rotations", [])
  for j in range(min(len(head.rotations), len(rotations))):
    _copy_into(head.rotations[j], rotations[j])

  head._update()
  logger.info("Loaded preset '%s' onto '%s'", path, head.transform)
  return path


def delete_preset(name):
  """Delete preset ``name`` (JSON + thumbnail). Returns True if removed."""
  removed = False
  for path in (_preset_path(name), _thumbnail_path(name)):
    if os.path.isfile(path):
      os.remove(path)
      removed = True
  if removed:
    logger.info("Deleted preset '%s'", name)
  else:
    logger.warning("Preset '%s' not found in %s", name, _presets_dir())
  return removed


def _copy_into(dst, src):
  """Element-wise copy of ``src`` floats into ``dst``, min-length truncated."""
  n = min(len(dst), len(src))
  for i in range(n):
    dst[i] = float(src[i])
  return n
