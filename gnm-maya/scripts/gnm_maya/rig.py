"""Bake a self-sufficient, keyframable Maya rig from a GNM head.

Produces a new mesh with:
  - a blendShape node whose targets are the 20 semantic expressions (and/or
    top-N raw basis modes) — native, keyframable, undo-able sliders
  - a joint chain (neck -> head -> eyes) skinned with GNM's own weights

The result needs no GNM runtime: it's plain Maya geometry + deformers, ready
for animation and FBX export.

Faithfulness note: GNM adds pose correctives (pose-dependent shape deltas)
before its LBS; Maya's plain LBS skips those, so large neck/eye rotations
deviate slightly from GNM's exact output.
"""

from __future__ import annotations

import array
import logging
import os

import maya.api.OpenMaya as om2
from maya import cmds as mc

from gnm_maya import build as _build
from gnm_maya import meshio

logger = logging.getLogger(__name__)


def _read_verts(session_dir, fname):
  return meshio.read_vertices(os.path.join(session_dir, fname))


def _read_weights(session_dir, num_joints, num_vertices):
  w = array.array("f")
  with open(os.path.join(session_dir, "rig_skin_weights.bin"), "rb") as f:
    w.frombytes(f.read())
  if len(w) != num_joints * num_vertices:
    raise ValueError("skin weights size mismatch: %d != %d*%d"
                     % (len(w), num_joints, num_vertices))
  return w  # joint-major: w[j*V + v]


def bake_rig(head, num_modes=0, semantic=True, name=None):
  """Bake ``head`` (a GnmHead) into a rigged mesh. Returns the new transform."""
  worker = head.worker
  topo = head.topology
  meta = worker.bake(identity=head.identity, num_modes=num_modes,
                     semantic=semantic)
  sess = worker.session_dir
  name = name or (head.transform + "_rig")

  # --- base mesh at the baked identity (expression/pose neutral) ------------
  neutral = _read_verts(sess, "rig_neutral.bin")
  transform = _build.build_mesh(topo, neutral, name=name)

  # --- blendShape with one target per expression -----------------------------
  from gnm_maya.progress import MayaProgress
  blend = None
  with MayaProgress("Baking GNM rig", maximum=len(meta["targets"]) + 2) as prog:
    if meta["targets"]:
      blend = mc.blendShape(transform, name=name + "_blendShape",
                              frontOfChain=True)[0]
      for i, tgt in enumerate(meta["targets"]):
        prog.set(i, "Target %d/%d: %s" % (i + 1, len(meta["targets"]),
                                          tgt["name"]))
        tgt_verts = _read_verts(sess, tgt["file"])
        tgt_transform = _build.build_mesh(topo, tgt_verts,
                                          name="%s_tgt_%s" % (name,
                                                              tgt["name"]))
        mc.blendShape(blend, edit=True,
                        target=(transform, i, tgt_transform, 1.0))
        mc.aliasAttr(tgt["name"], "%s.w[%d]" % (blend, i))
        mc.delete(tgt_transform)  # deltas stored on the blendShape node
      logger.info("Baked %d blendshape targets on '%s'", len(meta["targets"]),
                  blend)

    # --- joints + skinning ---------------------------------------------------
    prog.set(len(meta["targets"]), "Building joints…")
    joints = []
    mc.select(clear=True)
    for j, jname in enumerate(meta["joint_names"]):
      parent = meta["joint_parents"][j]
      if parent >= 0 and parent != j:
        mc.select(joints[parent], replace=True)
      else:
        mc.select(clear=True)
      joints.append(mc.joint(name="%s_%s" % (name, jname),
                               position=meta["joint_positions"][j]))

    prog.set(len(meta["targets"]) + 1, "Binding skin weights…")
    skin = mc.skinCluster(joints[0], transform, name=name + "_skinCluster",
                            toSelectedBones=False,
                            maximumInfluences=len(joints),
                            normalizeWeights=1)[0]
    _set_skin_weights(skin, transform, joints, sess,
                      len(meta["joint_names"]), meta["num_vertices"])
    logger.info("Baked skinCluster '%s' with %d joints", skin, len(joints))

  mc.select(transform, replace=True)
  return transform


def _set_skin_weights(skin, transform, joints, session_dir, num_joints,
                      num_vertices):
  """Bulk-load GNM's (J, V) weights into the skinCluster via the API."""
  w = _read_weights(session_dir, num_joints, num_vertices)

  sel = om2.MSelectionList()
  sel.add(skin)
  sel.add(transform)
  skin_obj = sel.getDependNode(0)
  dag = sel.getDagPath(1)
  dag.extendToShape()

  import maya.api.OpenMayaAnim as oma
  fn = oma.MFnSkinCluster(skin_obj)

  # Influence order in the cluster may differ from our joint order — map it.
  influences = [p.partialPathName() for p in fn.influenceObjects()]
  inf_index = {n: i for i, n in enumerate(influences)}
  order = [inf_index[j] for j in joints]

  # Vertex-major weight array in the cluster's influence order.
  flat = om2.MDoubleArray(num_vertices * num_joints, 0.0)
  for j in range(num_joints):
    dst = order[j]
    base = j * num_vertices
    for v in range(num_vertices):
      flat[v * num_joints + dst] = w[base + v]

  comps = om2.MFnSingleIndexedComponent()
  comp_obj = comps.create(om2.MFn.kMeshVertComponent)
  comps.addElements(list(range(num_vertices)))

  inf_indices = om2.MIntArray(list(range(num_joints)))
  fn.setWeights(dag, comp_obj, inf_indices, flat, normalize=False)
