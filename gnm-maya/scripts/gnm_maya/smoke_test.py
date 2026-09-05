"""Headless validation under mayapy of the quad/material/expression pipeline.

    mayapy -c "import sys; sys.path.insert(0, r'<module>/scripts'); \
               from gnm_maya import smoke_test; smoke_test.run_headless()"
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


def run_headless():
  """Entry point for CI: create the GUI QApplication BEFORE Maya standalone
  initializes (the other order aborts), then run the full test."""
  import sys as _sys
  try:
    from PySide6 import QtWidgets
  except ImportError:
    from PySide2 import QtWidgets
  QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
  try:
    _sys.stdout.reconfigure(line_buffering=True)
  except Exception:
    pass
  return run()


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

    # feature-zone randomize (worker roundtrip; confinement is unit-tested
    # in external/_zones — here we verify the wiring end to end)
    head.reset_all()
    head.randomize_zones("identity", ["nose"], scale=1.0, seed=3)
    assert any(abs(x) > 1e-6 for x in head.identity), \
        "zone randomize produced no coefficients"
    zoned = _sum_points(name)
    head.randomize_zones("identity", ["nose"], scale=0.0)  # zone reset
    print("[ok] feature-zone randomize + reset roundtrip (delta=%.4f)"
          % (zoned - base))

    # painted zone maps: Maya colour set <-> float32 file <-> solver
    import os
    import tempfile
    from gnm_maya.scene import zones as zn
    from gnm_maya.core import settings as _settings
    _settings_dir = _settings.zones_dir()
    nv = head.topology.num_vertices
    paint = [1.0 if i % 7 == 0 else 0.0 for i in range(nv)]  # fake strokes
    zn.write_colorset(name, zn.PREFIX + "smoketest", paint)
    back = zn.read_colorset(name, zn.PREFIX + "smoketest")
    assert sum(abs(a - b) for a, b in zip(paint, back)) < 1e-3, \
        "colour set roundtrip lost the painted weights"
    zn.save_paint(head, "smoketest")
    assert os.path.isfile(zn.map_path("smoketest")), "map file not written"
    head.reset_all()
    head.randomize_zones("identity", [], scale=1.0, seed=2,
                         maps=[zn.map_path("smoketest")])
    assert any(abs(x) > 1e-6 for x in head.identity), \
        "painted-map randomize produced nothing"
    w = worker.get_worker().zone_weights(["ears"],
                                         maps=[zn.map_path("smoketest")])
    assert len(w) == nv and max(w) > 0.99, "zone_weights preview op failed"
    # selection-based painting (soft selection off here -> weights of 1)
    mc.select(["%s.vtx[%d]" % (name, i) for i in range(0, 300)], replace=True)
    painted = zn.apply_selection(head, "smoketest", "replace")
    assert painted == 300, "apply_selection painted %d, expected 300" % painted
    mc.select(["%s.vtx[%d]" % (name, i) for i in range(0, 100)], replace=True)
    painted = zn.apply_selection(head, "smoketest", "remove")
    assert painted == 200, "remove selection left %d, expected 200" % painted
    mc.select(clear=True)
    # texture preview: UV-space PNG on a temporary lambert (VP2-safe)
    png = zn.preview_mask(head, ["nose"], maps=[zn.map_path("smoketest")])
    assert os.path.isfile(png) and os.path.getsize(png) > 1000, "mask PNG missing"
    assert (mc.sets(zn.PAINT_SG, query=True) or []),         "preview did not swap to the display material"
    assert mc.listConnections("gnm_paintDisplay_mat.color", source=True),         "mask texture not connected to the display material"
    zn.clear_mask_preview(head)
    assert (mc.sets("gnm_skin_matSG", query=True) or []),         "per-part materials not restored after preview"
    zn.delete_map("smoketest")
    head.reset_all()
    print("[ok] painted zone map: colour set/selection -> file -> solver -> texture preview")

    # thumbnail render op (drives the Variants contact sheet)
    png = os.path.join(tempfile.gettempdir(), "gnm_smoke_thumb.png")
    from gnm_maya.core import worker as _worker_mod
    _worker_mod.get_worker().render(png, identity=head.identity, size=96)
    assert os.path.isfile(png) and os.path.getsize(png) > 1000, \
        "render op produced no thumbnail"
    os.remove(png)
    print("[ok] worker render op writes thumbnails")

    # ARKit bake: semantic targets renamed/split to ARKit-52 conventions
    meta_rig = _worker_mod.get_worker().bake(identity=head.identity,
                                             semantic=True, arkit=True)
    names = {t["name"] for t in meta_rig["targets"]}
    for expect in ("eyeBlinkLeft", "eyeBlinkRight", "mouthSmileLeft",
                   "mouthSmileRight", "mouthPucker", "cheekPuff",
                   "tongueOut"):
      assert expect in names, "missing ARKit target %s (have %s)" % (
          expect, sorted(names))
    assert "wink_left" not in names, "wink_left should be renamed for ARKit"
    print("[ok] ARKit bake: %d targets incl. L/R splits" % len(names))

    # Build the real panel headlessly (mayapy ships PySide): catches missing
    # slots / broken layouts that only show up when the window is created.
    try:
      try:
        from PySide6 import QtWidgets
      except ImportError:
        from PySide2 import QtWidgets
      app = QtWidgets.QApplication.instance()
      # Creating a QApplication AFTER maya.standalone.initialize aborts the
      # process, so the runner must create it first (see run_headless below);
      # a bare QCoreApplication can't host widgets either.
      if not isinstance(app, QtWidgets.QApplication):
        raise ImportError("no GUI QApplication")
      from gnm_maya.ui import panel as ui
      p = ui.GnmPanel(parent=None, adopt_transform=name)
      assert p.tabs.count() == 5, "panel tabs: %d" % p.tabs.count()
      p._set_all_areas(False)
      p._reset_areas()            # no-op path; must exist and not raise
      p._randomize_areas("identity")  # nothing checked -> full randomize
      assert any(abs(x) > 1e-6 for x in p.head.identity),           "panel randomize no-op"
      p._reset_all()
      p.close(); p.deleteLater()
      print("[ok] GnmPanel builds headlessly (5 tabs, area actions wired)")
    except ImportError:
      print("[skip] Qt not available: panel construction not exercised")

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
    run_headless()
  except Exception as e:
    sys.stderr.write("SMOKE TEST FAILED: %s\n" % e)
    sys.exit(1)
