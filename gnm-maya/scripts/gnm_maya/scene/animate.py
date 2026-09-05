"""Keyframe helpers for baked GNM rigs: audio lip-sync and idle motion.

Works on the blendShape + joints that scene/rig.bake_rig creates (targets are
addressed by their alias names, e.g. ``viseme_C`` or ``eyeBlinkLeft``).
"""

from __future__ import annotations

import logging
import random

from maya import cmds as mc
import maya.mel as mel

logger = logging.getLogger(__name__)

VISEME_NAMES = ["viseme_B", "viseme_C", "viseme_D", "viseme_E", "viseme_F",
                "viseme_G", "viseme_H"]

_FPS = {"game": 15, "film": 24, "pal": 25, "ntsc": 30, "show": 48,
        "palf": 50, "ntscf": 60}


def scene_fps():
  unit = mc.currentUnit(query=True, time=True)
  if unit in _FPS:
    return _FPS[unit]
  try:
    return float(unit.replace("fps", ""))
  except ValueError:
    return 24.0


def blendshape_of(transform):
  nodes = mc.ls(mc.listHistory(transform) or [], type="blendShape")
  if not nodes:
    raise RuntimeError("%s has no blendShape — bake a rig first (Animate "
                       "tab)." % transform)
  return nodes[0]


def target_names(blend):
  return [a for a in (mc.aliasAttr(blend, query=True) or [])[::2]]


def _key(blend, alias, frame, value):
  mc.setKeyframe(blend, attribute=alias, time=frame, value=float(value),
                 inTangentType="linear", outTangentType="linear")


# --- lip-sync ------------------------------------------------------------------

def lip_sync(transform, cues, start_frame=None, hold_frames=1):
  """Key the rig's viseme targets from Rhubarb mouth cues.

  Each cue sets its viseme to 1 and every other viseme to 0 at the cue start
  (Rhubarb cues are contiguous, so the previous shape releases as the next
  one starts). 'A'/'X' are the closed rest pose = all visemes 0.
  Returns the number of cues keyed.
  """
  blend = blendshape_of(transform)
  have = set(target_names(blend))
  visemes = [v for v in VISEME_NAMES if v in have]
  if not visemes:
    raise RuntimeError("Rig has no viseme targets — bake it with 'Visemes' "
                       "enabled.")
  fps = scene_fps()
  start = mc.currentTime(query=True) if start_frame is None else start_frame
  n = 0
  for cue in cues:
    frame = start + float(cue["start"]) * fps
    active = "viseme_%s" % cue["value"]
    for v in visemes:
      _key(blend, v, frame, 1.0 if v == active else 0.0)
    n += 1
  if cues:  # release at the end
    end = start + float(cues[-1]["end"]) * fps + hold_frames
    for v in visemes:
      _key(blend, v, end, 0.0)
  logger.info("Lip-sync: %d cues keyed on %s", n, blend)
  return n


def attach_audio(path):
  """Load the clip into the scene and show its waveform on the time slider."""
  node = mc.sound(file=path, name="gnm_dialogue")
  try:
    slider = mel.eval("$tmp = $gPlayBackSlider")
    mc.timeControl(slider, edit=True, sound=node, displaySound=True)
  except Exception:
    pass
  return node


# --- idle motion ------------------------------------------------------------------

def idle(transform, start, end, blinks=True, sway=True, seed=0):
  """Procedural idle: periodic blinks (eyeBlink*/wink_* targets) and a gentle
  head sway on the rig's head joint. Returns the number of keys set."""
  rng = random.Random(seed)
  fps = scene_fps()
  keys = 0
  if blinks:
    blend = blendshape_of(transform)
    names = target_names(blend)
    eyes = [a for a in ("eyeBlinkLeft", "eyeBlinkRight", "wink_left",
                        "wink_right") if a in names]
    t = float(start) + rng.uniform(0.5, 2.0) * fps
    while t < end and eyes:
      close, open_ = max(2.0, fps * 0.08), max(3.0, fps * 0.14)
      for a in eyes:
        _key(blend, a, t, 0.0)
        _key(blend, a, t + close, 1.0)
        _key(blend, a, t + close + open_, 0.0)
        keys += 3
      t += rng.uniform(2.0, 5.5) * fps
  if sway:
    head = [j for j in (mc.ls(transform + "_head", type="joint") or [])]
    if head:
      j = head[0]
      t = float(start)
      while t <= end:
        for axis, amp in (("rotateX", 1.5), ("rotateY", 2.5), ("rotateZ", 1.0)):
          mc.setKeyframe(j, attribute=axis, time=t,
                         value=rng.uniform(-amp, amp),
                         inTangentType="spline", outTangentType="spline")
          keys += 1
        t += rng.uniform(1.5, 3.0) * fps
  logger.info("Idle: %d keys on %s", keys, transform)
  return keys
