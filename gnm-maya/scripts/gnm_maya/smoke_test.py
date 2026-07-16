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
    from maya import cmds as mc
    from gnm_maya.core.head import GnmHead
    from gnm_maya.core import worker

    mc.file(new=True, force=True)

    head = GnmHead(name="gnm_smoke")
    name = head.transform
    assert mc.objExists(name), "mesh not created"

    faces = mc.polyEvaluate(name, face=True)
    verts = mc.polyEvaluate(name, vertex=True)
    exp_q = head.topology.num_quads
    assert faces == exp_q, "face count %d != quads %d" % (faces, exp_q)
    print("[ok] quad mesh: %d verts, %d faces" % (verts, faces))

    vcount, nuv, nv = _poly0_vert_count(name)
    assert vcount == 4, "polygon 0 is not a quad (has %d verts)" % vcount
    assert nuv > 0, "no UVs assigned"
    print("[ok] topology is quads + %d UVs" % nuv)

    for sg in ("gnm_skin_matSG", "gnm_left_eye_matSG", "gnm_tongue_matSG"):
      assert mc.objExists(sg), "missing shading group %s" % sg
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

    # area mask: randomize_range must touch ONLY the given region's coeffs
    head.reset_all()
    groups = head.topology.meta.get("identity_groups", [])
    assert groups, "no identity groups in topology meta"
    label, start, end = groups[0]
    head.randomize_range("identity", start, end, scale=1.0, seed=7)
    inside = any(abs(x) > 1e-9 for x in head.identity[start:end + 1])
    outside = all(abs(head.identity[i]) < 1e-9
                  for i in range(len(head.identity))
                  if not (start <= i <= end))
    assert inside, "area randomize left the masked region untouched"
    assert outside, "area randomize leaked outside the mask"
    head.clear("identity", range(start, end + 1))
    assert all(abs(x) < 1e-9 for x in head.identity), "area clear incomplete"
    print("[ok] area mask randomize/reset confined to '%s' [%d..%d]"
          % (label, start, end))

    worker.shutdown_worker()
    print("SMOKE TEST PASSED")
  finally:
    try:
      from gnm_maya.core import worker
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
