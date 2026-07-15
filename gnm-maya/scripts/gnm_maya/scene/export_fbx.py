"""Export a baked GNM rig (mesh + blendshapes + skeleton) to FBX.

Works on transforms produced by :func:`gnm_maya.rig.bake_rig`; the exported
file carries the skinCluster and blendShape targets, so it opens in game
engines / other DCCs with no GNM runtime.
"""

from __future__ import annotations

import logging
import os

from maya import cmds as mc
import maya.mel as mel

logger = logging.getLogger(__name__)

from gnm_maya.core import settings


def _exports_dir():
  """User-configurable export folder (see gnm_maya.settings)."""
  return settings.exports_dir()


def export_rigged_fbx(transform, path=None):
  """Export ``transform`` (a baked rig) and its skeleton to an FBX file.

  Args:
    transform: Transform of the rigged mesh (output of ``rig.bake_rig``).
    path: Destination ``.fbx`` path. Defaults to the current scene's folder,
      or ``~/Documents/maya/gnm_exports/<transform>.fbx`` for unsaved scenes.

  Returns:
    The absolute path of the written FBX file.
  """
  if not mc.objExists(transform):
    raise ValueError("Transform does not exist: %s" % transform)
  mc.loadPlugin("fbxmaya", quiet=True)

  if path is None:
    scene = mc.file(query=True, sceneName=True)
    out_dir = os.path.dirname(scene) if scene else _exports_dir()
    if not os.path.isdir(out_dir):
      os.makedirs(out_dir)
    path = os.path.join(out_dir, "%s.fbx" % transform)
  path = os.path.abspath(path)

  joints = _skin_joints(transform)
  mc.select([transform] + joints, replace=True)

  mel.eval("FBXResetExport")
  mel.eval("FBXExportSmoothingGroups -v true")
  mel.eval("FBXExportShapes -v true")   # blendshape targets
  mel.eval("FBXExportSkins -v true")    # skinCluster
  mel.eval("FBXExportEmbeddedTextures -v false")
  mel.eval('FBXExport -f "%s" -s' % path.replace("\\", "/"))

  logger.info("Exported '%s' (+%d joints) -> %s", transform, len(joints), path)
  return path


def _skin_joints(transform):
  """Joints influencing ``transform``'s skinCluster(s), via history."""
  history = mc.listHistory(transform) or []
  joints = []
  for skin in mc.ls(history, type="skinCluster") or []:
    for j in mc.skinCluster(skin, query=True, influence=True) or []:
      if j not in joints:
        joints.append(j)
  if not joints:
    # Fallback: bake_rig names joints '<transform>_<joint>'.
    joints = [j for j in mc.ls(type="joint") or []
              if j.startswith(transform + "_")]
  if not joints:
    logger.warning("No skin joints found for '%s'; exporting mesh only.",
                   transform)
  return joints
