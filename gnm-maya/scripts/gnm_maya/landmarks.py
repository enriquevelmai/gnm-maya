"""Create/refresh locators at GNM's 68 sparse facial landmarks.

The vendored landmark file (``head_sparse_68.txt``) has one landmark per line,
each defined as 3 ``vertex_index weight`` pairs — a barycentric blend of three
mesh vertices (weights sum to ~1), matching ``gnm/shape/gnm_landmarks.py``.
Each locator is placed at the weighted world-space blend of its 3 vertices.
"""

from __future__ import annotations

import logging
import os

import maya.cmds as cmds

from gnm_maya import config

logger = logging.getLogger(__name__)

LANDMARKS_FILE = os.path.join(
    config.MODULE_ROOT, "external", "gnm_repo", "gnm", "shape", "data",
    "landmarks", "head_sparse_68.txt")


def create_landmark_locators(head, scale=0.005):
  """Create one locator per landmark, grouped under '<head>_landmarks'.

  Locators are named ``gnmLmk_00 .. gnmLmk_67`` and positioned at the current
  world position of their landmark. Re-creating replaces any existing group.

  Args:
    head: A ``GnmHead`` controller.
    scale: Locator localScale (heads are small; default suits GNM units).

  Returns:
    The landmark group's transform name.
  """
  landmarks = _load_landmark_defs()
  _check_indices(landmarks, head.topology.num_vertices)

  group = head.transform + "_landmarks"
  if cmds.objExists(group):
    cmds.delete(group)
  group = cmds.group(empty=True, name=group)

  for i, pairs in enumerate(landmarks):
    # Full paths throughout: short names like gnmLmk_00Shape become ambiguous
    # as soon as a second head's landmark group exists in the scene.
    loc = cmds.spaceLocator(name="gnmLmk_%02d" % i)[0]
    loc = cmds.parent(loc, group)[0]
    loc = "%s|%s" % (group, loc.split("|")[-1])
    shape = cmds.listRelatives(loc, shapes=True, fullPath=True)[0]
    cmds.setAttr("%s.localScale" % shape, scale, scale, scale, type="double3")
    cmds.xform(loc, translation=_landmark_position(head.transform, pairs),
               worldSpace=True)

  logger.info("Created %d landmark locators under '%s'", len(landmarks), group)
  return group


# iBUG-68 left<->right symmetry pairs (center points 8, 27-30, 33, 51, 57,
# 62, 66 have no partner). GNM's landmark ordering follows this layout.
MIRROR_PAIRS = (
    [(i, 16 - i) for i in range(8)] +            # jaw
    [(17 + i, 26 - i) for i in range(5)] +       # brows
    [(31, 35), (32, 34)] +                       # nostrils
    [(36, 45), (37, 44), (38, 43), (39, 42), (40, 47), (41, 46)] +  # eyes
    [(48, 54), (49, 53), (50, 52), (55, 59), (56, 58),              # outer lips
     (60, 64), (61, 63), (65, 67)]                                  # inner lips
)

_mirror_state = {"jobs": [], "guard": False}


def _mirror_partner_map():
  m = {}
  for a, b in MIRROR_PAIRS:
    m[a] = b
    m[b] = a
  return m


def _on_landmark_moved(group, index, partner):
  """Copy a moved locator's local position to its partner, x-negated."""
  if _mirror_state["guard"]:
    return
  src = "%s|gnmLmk_%02d" % (group, index)
  dst = "%s|gnmLmk_%02d" % (group, partner)
  if not (cmds.objExists(src) and cmds.objExists(dst)):
    return
  _mirror_state["guard"] = True  # the setAttr below fires the partner's job
  try:
    t = cmds.getAttr(src + ".translate")[0]
    cmds.setAttr(dst + ".translate", -t[0], t[1], t[2], type="double3")
  finally:
    _mirror_state["guard"] = False


def enable_mirror(head):
  """Mirror landmark edits: moving a Left locator moves its Right partner
  (and vice versa), x-negated. Returns the number of locators watched."""
  disable_mirror()
  group = head.transform + "_landmarks"
  if not cmds.objExists(group):
    raise RuntimeError("No landmark group '%s'. Create landmarks first."
                       % group)
  count = 0
  for idx, partner in _mirror_partner_map().items():
    loc = "%s|gnmLmk_%02d" % (group, idx)
    if not cmds.objExists(loc):
      continue
    job = cmds.scriptJob(
        attributeChange=[loc + ".translate",
                         lambda i=idx, p=partner, g=group:
                         _on_landmark_moved(g, i, p)])
    _mirror_state["jobs"].append(job)
    count += 1
  logger.info("Landmark mirror ON (%d locators watched)", count)
  return count


def disable_mirror():
  """Remove all landmark-mirror scriptJobs. Returns how many were removed."""
  for job in _mirror_state["jobs"]:
    try:
      if cmds.scriptJob(exists=job):
        cmds.scriptJob(kill=job, force=True)
    except Exception:
      pass
  n = len(_mirror_state["jobs"])
  _mirror_state["jobs"] = []
  if n:
    logger.info("Landmark mirror OFF (%d jobs removed)", n)
  return n


def fit_head_to_locators(head, lam=1.0):
  """Solve the head's identity so its landmarks match the (edited) locators.

  The '68 locators are the fitting skeleton' feature: drag locators (with the
  L/R mirror if you like), then call this — a ridge least-squares over the
  identity basis reshapes the head to meet them. Expression is held fixed;
  head/eye rotations are ignored (fit with a neutral pose for best results).

  Returns the head's transform name.
  """
  group = head.transform + "_landmarks"
  if not cmds.objExists(group):
    raise RuntimeError("No landmark group '%s'. Create landmarks first."
                       % group)
  if any(abs(a) > 1e-4 for r in head.rotations for a in r):
    logger.warning("Head pose rotations are non-zero; the landmark fit "
                   "assumes a neutral pose and may be off.")
  targets = []
  tx, ty, tz = head.translation
  for i in range(68):
    loc = "%s|gnmLmk_%02d" % (group, i)
    if not cmds.objExists(loc):
      raise RuntimeError("Missing locator gnmLmk_%02d." % i)
    p = cmds.xform(loc, query=True, translation=True, worldSpace=True)
    targets.append([p[0] - tx, p[1] - ty, p[2] - tz])

  vec = head.worker.fit_landmarks3d(targets, expression=head.expression,
                                    lam=lam)
  head.identity = [float(x) for x in vec]
  head._update()
  update_landmark_locators(head)  # show what the fit achieved
  logger.info("Fitted identity to landmark locators on '%s'", head.transform)
  return head.transform


def update_landmark_locators(head):
  """Re-snap the landmark locators after the head's shape/pose changed.

  Args:
    head: The ``GnmHead`` whose '<head>_landmarks' group should be refreshed.

  Returns:
    The landmark group's transform name.
  """
  group = head.transform + "_landmarks"
  if not cmds.objExists(group):
    raise ValueError("No landmark group '%s'; call create_landmark_locators "
                     "first." % group)
  landmarks = _load_landmark_defs()
  locators = cmds.listRelatives(group, children=True, type="transform",
                                fullPath=True) or []
  if len(locators) != len(landmarks):
    raise ValueError("Landmark group '%s' has %d locators, expected %d."
                     % (group, len(locators), len(landmarks)))
  for loc, pairs in zip(locators, landmarks):
    cmds.xform(loc, translation=_landmark_position(head.transform, pairs),
               worldSpace=True)
  logger.info("Updated %d landmark locators under '%s'", len(locators), group)
  return group


def _load_landmark_defs():
  """Parse the landmark file into [[(vertex_index, weight), ...], ...]."""
  if not os.path.isfile(LANDMARKS_FILE):
    raise RuntimeError("Landmark file missing: %s" % LANDMARKS_FILE)
  landmarks = []
  with open(LANDMARKS_FILE) as f:
    for line in f:
      parts = line.split()
      if not parts:
        continue
      if len(parts) % 2 != 0:
        raise ValueError("Malformed landmark line: %r" % line)
      landmarks.append([(int(parts[i]), float(parts[i + 1]))
                        for i in range(0, len(parts), 2)])
  return landmarks


def _check_indices(landmarks, num_vertices):
  top = max(idx for pairs in landmarks for idx, _ in pairs)
  if top >= num_vertices:
    raise ValueError("Landmark vertex index %d out of range (mesh has %d "
                     "vertices)." % (top, num_vertices))


def _landmark_position(transform, pairs):
  """Barycentric world position: sum(weight * vertex_world_pos)."""
  x = y = z = 0.0
  for idx, w in pairs:
    p = cmds.pointPosition("%s.vtx[%d]" % (transform, idx), world=True)
    x += w * p[0]
    y += w * p[1]
    z += w * p[2]
  return [x, y, z]
