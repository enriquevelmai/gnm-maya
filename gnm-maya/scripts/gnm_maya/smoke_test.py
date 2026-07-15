"""Headless validation under mayapy of the quad/material/expression pipeline.

    mayapy -c "import sys; sys.path.insert(0, r'<module>/scripts'); \
               from gnm_maya import smoke_test; smoke_test.run()"
"""

from __future__ import annotations

import sys


def _poly0_vert_count(name):
  import maya.api.OpenMaya as om2
  sel = om2.MSelectionList(); sel.add(name)
  fn = om2.MFnMesh(sel.getDagPath(0))
  return fn.polygonVertexCount(0), fn.numUVs(), fn.numVertices


def _sum_points(name):
  import maya.api.OpenMaya as om2
  sel = om2.MSelectionList(); sel.add(name)
  fn = om2.MFnMesh(sel.getDagPath(0))
  pts = fn.getPoints()
  # Sum all vertices so localized deformations (e.g. one eye region) register.
  return sum(p.x + p.y + p.z for p in pts)


def run():
  import maya.standalone
  maya.standalone.initialize(name="python")
  try:
    import maya.cmds as cmds
    from gnm_maya import api, worker

    cmds.file(new=True, force=True)

    head = api.GnmHead(name="gnm_smoke")
    name = head.transform
    assert cmds.objExists(name), "mesh not created"

    faces = cmds.polyEvaluate(name, face=True)
    verts = cmds.polyEvaluate(name, vertex=True)
    exp_q = head.topology.num_quads
    assert faces == exp_q, "face count %d != quads %d" % (faces, exp_q)
    print("[ok] quad mesh: %d verts, %d faces" % (verts, faces))

    vcount, nuv, nv = _poly0_vert_count(name)
    assert vcount == 4, "polygon 0 is not a quad (has %d verts)" % vcount
    assert nuv > 0, "no UVs assigned"
    print("[ok] topology is quads + %d UVs" % nuv)

    for sg in ("gnm_skin_matSG", "gnm_left_eye_matSG", "gnm_tongue_matSG"):
      assert cmds.objExists(sg), "missing shading group %s" % sg
    print("[ok] per-part materials assigned")

    # expression must move geometry
    before = _sum_points(name)
    head.set_expression(0, 6.0)
    after_expr = _sum_points(name)
    assert abs(after_expr - before) > 1e-4, "expression did not change mesh"
    print("[ok] expression drives mesh (delta=%.4f)" % (after_expr - before))

    # identity must move geometry
    head.reset_all()
    base = _sum_points(name)
    head.set_identity(0, 6.0)
    after_id = _sum_points(name)
    assert abs(after_id - base) > 1e-4, "identity did not change mesh"
    print("[ok] identity drives mesh (delta=%.4f)" % (after_id - base))

    worker.shutdown_worker()
    print("SMOKE TEST PASSED")
  finally:
    try:
      from gnm_maya import worker
      worker.shutdown_worker()
    except Exception:
      pass
    maya.standalone.uninitialize()


if __name__ == "__main__":
  try:
    run()
  except Exception as e:
    sys.stderr.write("SMOKE TEST FAILED: %s\n" % e)
    sys.exit(1)
