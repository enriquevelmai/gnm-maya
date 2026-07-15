"""Generate a grid of random GNM heads (optionally baked to rigs).

All heads share the one resident worker, so generation cost is one model
evaluation per head, not one process per head.
"""

from __future__ import annotations

import logging
import random

from maya import cmds as mc

from gnm_maya import api
from gnm_maya import rig as _rig

logger = logging.getLogger(__name__)


def generate_crowd(count=10, columns=5, spacing=0.5, identity_scale=1.0,
                   expression_scale=0.0, seed=None, bake=False):
  """Generate ``count`` random heads laid out on a grid.

  Args:
    count: Number of heads to generate.
    columns: Grid width; heads fill row by row.
    spacing: World-space distance between grid cells (Maya units).
    identity_scale: Std-dev scale for the random identity coefficients.
    expression_scale: If > 0, each head also gets a random expression with
      this scale.
    seed: Optional int for a reproducible crowd; ``None`` gives a fresh crowd
      per call.
    bake: If True, each head is baked to a self-sufficient rig
      (:func:`gnm_maya.rig.bake_rig`) and the live source head is deleted, so
      only rigged meshes remain. Roughly 15s per head.

  Returns:
    List of the crowd's mesh transform names (rig transforms when ``bake``).
  """
  from gnm_maya.progress import MayaProgress
  rng = random.Random(seed)
  transforms = []
  with MayaProgress("Generating crowd", maximum=count) as prog:
    transforms = _generate_crowd_loop(count, columns, spacing, identity_scale,
                                      expression_scale, bake, rng, prog)
  return transforms


def _generate_crowd_loop(count, columns, spacing, identity_scale,
                         expression_scale, bake, rng, prog):
  transforms = []
  for i in range(count):
    prog.set(i, "Head %d/%d%s" % (i + 1, count, " (baking)" if bake else ""))
    head_seed = rng.randrange(1 << 30)
    h = api.generate_head(seed=head_seed, identity_scale=identity_scale,
                          name="gnm_crowd_%02d" % i)
    if expression_scale > 0.0:
      h.randomize_expression(scale=expression_scale,
                             seed=rng.randrange(1 << 30))

    row, col = divmod(i, columns)
    position = [col * spacing, 0.0, row * spacing]

    if bake:
      rig_transform = _rig.bake_rig(h)
      mc.delete(h.transform)
      # Group the mesh with its skeleton and move the group. The skinCluster
      # already moves the deformed points when the joints move, so the mesh
      # transform must NOT also inherit the group's translation (that would
      # double-transform the skinned vertices).
      group = mc.group(
          [rig_transform] + _root_joints(rig_transform),
          name=rig_transform + "_grp")
      mc.setAttr(rig_transform + ".inheritsTransform", 0)
      mc.xform(group, translation=position)
      transforms.append(rig_transform)
    else:
      mc.xform(h.transform, translation=position)
      transforms.append(h.transform)

    logger.info("Crowd head %d/%d -> '%s' at %s", i + 1, count,
                transforms[-1], position)
  return transforms


def _root_joints(rig_transform):
  """Top-level joints of a baked rig (named '<rig>_<joint>' by bake_rig)."""
  prefix = rig_transform + "_"
  roots = []
  for j in mc.ls(type="joint") or []:
    if not j.startswith(prefix):
      continue
    parent = mc.listRelatives(j, parent=True, type="joint")
    if not parent:
      roots.append(j)
  return roots
