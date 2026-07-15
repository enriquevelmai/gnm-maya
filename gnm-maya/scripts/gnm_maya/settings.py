"""User-configurable paths, persisted across Maya sessions via optionVar.

Keys: gnmPresetsDir, gnmExportsDir, gnmLastPhotoDir. All getters return an
existing default when unset so callers never deal with empty values.
"""

from __future__ import annotations

import logging
import os

from maya import cmds as mc

logger = logging.getLogger(__name__)

_PRESETS_KEY = "gnmPresetsDir"
_EXPORTS_KEY = "gnmExportsDir"
_PHOTO_KEY = "gnmLastPhotoDir"
_THUMB_KEY = "gnmThumbSize"


def thumb_size():
  """Slider-thumbnail width in px (0 = hidden). Persisted across sessions."""
  if mc.optionVar(exists=_THUMB_KEY):
    try:
      return max(0, int(mc.optionVar(query=_THUMB_KEY)))
    except (TypeError, ValueError):
      pass
  return 56


def set_thumb_size(px):
  mc.optionVar(intValue=(_THUMB_KEY, int(px)))
  logger.info("%s = %s", _THUMB_KEY, px)


def _get(key, default):
  if mc.optionVar(exists=key):
    val = mc.optionVar(query=key)
    if val and os.path.isdir(val):
      return val
  return default


def _set(key, value):
  mc.optionVar(stringValue=(key, value))
  logger.info("%s = %s", key, value)


def presets_dir():
  return _get(_PRESETS_KEY, os.path.expanduser("~/Documents/maya/gnm_presets"))


def set_presets_dir(path):
  _set(_PRESETS_KEY, path)


def exports_dir():
  return _get(_EXPORTS_KEY, os.path.expanduser("~/Documents/maya/gnm_exports"))


def set_exports_dir(path):
  _set(_EXPORTS_KEY, path)


def last_photo_dir():
  """Last folder a photo/texture was picked from; sensible first-run default."""
  pictures = os.path.join(os.path.expanduser("~"), "Pictures")
  default = pictures if os.path.isdir(pictures) else os.path.expanduser("~")
  return _get(_PHOTO_KEY, default)


def set_last_photo_dir(path_or_file):
  d = path_or_file
  if os.path.isfile(d):
    d = os.path.dirname(d)
  if os.path.isdir(d):
    _set(_PHOTO_KEY, d)
