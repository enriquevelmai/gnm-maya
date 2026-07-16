"""GUI smoke test for the GNM panel — run INSIDE a running Maya (GUI) session.

This needs a full Maya (for Qt); it will NOT run under headless mayapy.

How to run
----------
Easiest: in Maya, open this file in the Script Editor and press "Execute All".

Or, from a Python tab, point exec() at wherever you extracted the module:

    exec(open(r"<path-to>/gnm-maya/tests/gui_smoke_test.py").read())

If the module is already installed (via the drag-and-drop installer or a .mod),
you can instead just run:

    import gnm_maya; gnm_maya.show_ui()

It creates a new scene, opens the panel, drives sliders/randomize/reset/
symmetry/show-all/licenses programmatically, prints PASS/FAIL for each, then
closes the windows it opened and deletes every node it created.
"""

import os
import sys


def _bootstrap():
  """Make `gnm_maya` importable whether or not the module is installed."""
  try:
    import gnm_maya  # already on the path (installed via .mod)
    return
  except ImportError:
    pass
  # Resolve the module's scripts dir relative to THIS file (../scripts).
  try:
    here = os.path.dirname(os.path.abspath(__file__))
    scripts = os.path.normpath(os.path.join(here, "..", "scripts"))
    if os.path.isdir(os.path.join(scripts, "gnm_maya")):
      sys.path.insert(0, scripts)
      return
  except NameError:
    pass  # pasted into the Script Editor without a __file__
  raise RuntimeError(
      "gnm_maya is not importable. Either install the module (drag-and-drop "
      "installer) or run this file by its path so it can locate ../scripts.")


_bootstrap()

from maya import cmds as mc
import gnm_maya
from gnm_maya.ui import panel as ui


_results = []


def check(name, cond):
  ok = bool(cond)
  _results.append((name, ok))
  print(("[PASS] " if ok else "[FAIL] ") + name)
  return ok


def run():
  mc.file(new=True, force=True)

  panel = gnm_maya.show_ui()
  check("panel opens with a head", panel is not None and panel.head is not None)
  if not panel or not panel.head:
    print("Aborting: head failed to build (see Script Editor for errors).")
    return panel

  check("mesh created in scene", mc.objExists(panel.head.transform))
  check("five tabs present", panel.tabs.count() == 5)
  check("identity sliders built", len(panel._id_sliders) > 0)
  check("expression sliders built", len(panel._expr_sliders) > 0)

  # Drive an identity slider through its widget (fires the live callback).
  panel._id_sliders[0].s.setValue(200)  # 200 / 100 = 2.0
  check("identity slider drives the head",
        abs(panel.head.identity[0] - 2.0) < 1e-3)

  # Per-tab randomize.
  panel._randomize_kind("expression")
  check("randomize expression changed coefficients",
        any(abs(x) > 1e-6 for x in panel.head.expression))
  panel._randomize_pose()
  check("randomize pose changed rotations",
        any(abs(a) > 1e-6 for r in panel.head.rotations for a in r))

  # Symmetry toggle.
  panel.sym_chk.setChecked(True)
  check("symmetry toggles on", panel._symmetry is True)

  # Show-all expansion on the first large group.
  from gnm_maya.ui import widgets
  big = [g for g in panel.findChildren(widgets.CoeffGroup)
         if g.total > g.shown]
  if big:
    g = big[0]
    before = len(panel._sliders)
    g._toggle()
    check("Show all expands a group",
          len(panel._sliders) > before and g._extra_built)
  else:
    print("[skip] no group large enough to need Show all")

  # Reset everything.
  panel._reset_all()
  check("Reset zeros identity",
        all(abs(x) < 1e-6 for x in panel.head.identity))
  check("Reset zeros expression",
        all(abs(x) < 1e-6 for x in panel.head.expression))

  # Licenses viewer (non-modal). Kept for closing during cleanup.
  lic_dlg = gnm_maya.show_licenses()
  check("licenses dialog opens", lic_dlg is not None)

  # --- newer features -------------------------------------------------------

  # Semantic tab: categorical sampling + blend mix slider + describe field.
  if hasattr(panel, "sem_expr"):
    panel.head.reset_all()
    panel.sem_expr.setCurrentIndex(5)  # happy
    panel._sample_expression()
    check("semantic sample expression drives coeffs",
          any(abs(x) > 1e-6 for x in panel.head.expression))
    # Reset Expression from the semantic tab returns to neutral.
    panel._reset_semantic_expression()
    check("semantic reset expression zeros coeffs",
          all(abs(x) < 1e-6 for x in panel.head.expression))
    panel.sem_gender.setCurrentIndex(0)
    panel._sample_identity()
    check("semantic sample identity drives coeffs",
          any(abs(x) > 1e-6 for x in panel.head.identity))
    panel._reset_semantic_identity()
    check("semantic reset identity zeros coeffs",
          all(abs(x) < 1e-6 for x in panel.head.identity))
    panel.head.reset_all()
    panel.blend_expr2.setCurrentIndex(10)  # smile_wide
    panel.blend_expr_mix.setValue(50)      # schedules throttled blend
    panel._do_refresh()                    # flush the pending action now
    check("blend mix slider drives coeffs",
          any(abs(x) > 1e-6 for x in panel.head.expression))
    # Reset clears the mix slider silently (no re-blend fired).
    panel._reset_semantic_expression()
    check("semantic reset clears the expression mix slider",
          panel.blend_expr_mix.value() == 0
          and all(abs(x) < 1e-6 for x in panel.head.expression))
    panel.desc_edit.setText("very happy, slightly surprised")
    panel._apply_description()
    check("describe field applies expression",
          any(abs(x) > 1e-6 for x in panel.head.expression))
  else:
    print("[skip] semantic tab unavailable")

  # Presets roundtrip through the backend the browser uses.
  from gnm_maya.scene import presets
  panel.head.randomize_identity(scale=1.0, seed=99)
  before = list(panel.head.identity)
  presets.save_preset(panel.head, "_guitest")
  panel.head.reset_all()
  presets.load_preset(panel.head, "_guitest")
  check("preset save/load restores identity",
        all(abs(a - b) < 1e-6 for a, b in zip(before, panel.head.identity)))
  presets.delete_preset("_guitest")

  # Landmarks: 68 locators.
  from gnm_maya.scene import landmarks as lmk
  grp = lmk.create_landmark_locators(panel.head)
  n_loc = len(mc.listRelatives(grp, children=True) or [])
  check("68 landmark locators created", n_loc == 68)

  # Crowd: 3 heads at distinct positions.
  from gnm_maya.scene import crowd
  made = crowd.generate_crowd(count=3, columns=3, spacing=0.6, seed=5)
  xs = {round(mc.xform(t, query=True, translation=True, worldSpace=True)[0], 3)
        for t in made}
  check("crowd creates 3 heads at distinct X", len(made) == 3 and len(xs) == 3)

  # Shape gallery tooltips (only if the gallery was generated).
  from gnm_maya.ui import panel as ui_mod
  if panel._gallery:
    tip = panel._id_sliders[0].toolTip()
    check("gallery tooltip embeds images", "<img" in tip)
  else:
    print("[skip] shape gallery not generated (docs/shapes)")

  # --- cleanup: close windows and delete everything the test created --------
  try:
    if lic_dlg is not None:
      lic_dlg.close()
      lic_dlg.deleteLater()
    panel.close()
    panel.deleteLater()
    from gnm_maya.core import worker
    worker.shutdown_worker()
    to_delete = [t for t in ([panel.head.transform, grp] + list(made))
                 if t and mc.objExists(t)]
    # Crowd heads may have their own landmark-free names; also sweep leftovers.
    for pattern in ("gnm_head*", "gnm_crowd_*", "*_landmarks"):
      to_delete += [t for t in (mc.ls(pattern, type="transform") or [])
                    if t not in to_delete]
    if to_delete:
      mc.delete(to_delete)
    check("cleanup: windows closed + created items deleted",
          not (mc.ls("gnm_head*") or mc.ls("gnm_crowd_*")))
  except Exception as e:
    check("cleanup: windows closed + created items deleted (%s)" % e, False)

  npass = sum(1 for _, ok in _results if ok)
  print("\n=== GNM GUI SMOKE: %d/%d checks passed ===" % (npass, len(_results)))
  print("Scene cleaned up (test windows closed, created nodes deleted).")
  return None


# Auto-run when executed via exec(open(...).read()) or Execute All.
_PANEL = run()
