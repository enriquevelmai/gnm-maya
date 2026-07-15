"""Build a quad Maya mesh from GNM topology, with UVs and per-part materials.

Fixed topology means we build once, then animate via :func:`set_points`.
"""

from __future__ import annotations

import maya.api.OpenMaya as om2
from maya import cmds as mc

from gnm_maya.scene import material


def _points(verts_flat):
  # Building the MPointArray from a list of (x,y,z) tuples in one call is ~40%
  # faster than appending MPoint objects in a Python loop (matters per drag).
  xs = verts_flat[0::3]
  ys = verts_flat[1::3]
  zs = verts_flat[2::3]
  return om2.MPointArray([(xs[i], ys[i], zs[i]) for i in range(len(xs))])


def build_mesh(topology, verts_flat, name="gnm_head"):
  """Create the quad mesh, assign UVs + per-component materials, soft-shade.

  Returns the transform name.
  """
  num_q = topology.num_quads
  points = _points(verts_flat)

  poly_counts = om2.MIntArray()
  poly_counts.setLength(num_q)
  for i in range(num_q):
    poly_counts[i] = 4  # all quads

  poly_connects = om2.MIntArray()
  poly_connects.setLength(num_q * 4)
  q = topology.quads
  for i in range(num_q * 4):
    poly_connects[i] = q[i]

  fn = om2.MFnMesh()
  fn.create(points, poly_counts, poly_connects)

  # --- UVs: one UV per face-vertex (handles seams correctly) ---------------
  uv = topology.quad_uvs  # flat [u,v, ...] in face-vertex order
  num_fv = num_q * 4
  us = om2.MFloatArray(); us.setLength(num_fv)
  vs = om2.MFloatArray(); vs.setLength(num_fv)
  for i in range(num_fv):
    us[i] = uv[i * 2]
    vs[i] = uv[i * 2 + 1]
  uv_counts = om2.MIntArray(); uv_counts.copy(poly_counts)
  uv_ids = om2.MIntArray(); uv_ids.setLength(num_fv)
  for i in range(num_fv):
    uv_ids[i] = i
  fn.setUVs(us, vs)
  fn.assignUVs(uv_counts, uv_ids)

  # Name the transform.
  shape_path = fn.getPath(); shape_path.pop()
  transform = om2.MFnDagNode(shape_path.node()).name()
  transform = mc.rename(transform, name)

  _assign_materials(transform, topology)

  # Soft normals but keep hard part boundaries (eyes/teeth) via 60-deg angle.
  mc.polySoftEdge(transform, angle=60, constructionHistory=False)
  mc.select(transform, replace=True)
  return transform


def _assign_materials(transform, topology):
  # Face index == quad index (we built purely from quads).
  assigned = set()
  for name, quad_idx in topology.components:
    sg = material.get_or_create(name)
    faces = ["%s.f[%d]" % (transform, i) for i in quad_idx]
    if faces:
      mc.sets(faces, edit=True, forceElement=sg)
      assigned.update(quad_idx)
  # Any unassigned faces -> skin, so nothing stays on the default SG.
  leftover = [i for i in range(topology.num_quads) if i not in assigned]
  if leftover:
    sg = material.get_or_create("skin")
    mc.sets(["%s.f[%d]" % (transform, i) for i in leftover],
              edit=True, forceElement=sg)


def set_points(transform, verts_flat):
  """Push new vertex positions into an existing GNM mesh (topology unchanged)."""
  sel = om2.MSelectionList(); sel.add(transform)
  fn = om2.MFnMesh(sel.getDagPath(0))
  n = len(verts_flat) // 3
  if fn.numVertices != n:
    raise ValueError("vertex count mismatch: %d vs %d" % (fn.numVertices, n))
  fn.setPoints(_points(verts_flat), om2.MSpace.kObject)
