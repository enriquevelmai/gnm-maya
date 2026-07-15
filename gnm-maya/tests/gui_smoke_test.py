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
symmetry/show-all/licenses programmatically, prints PASS/FAIL for each, and
leaves the panel open so you can inspect it by hand.
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
from gnm_maya import ui


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
  big = [g for g in panel.findChildren(ui._CoeffGroup) if g.total > g.shown]
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

  # Licenses viewer (non-modal).
  dlg = gnm_maya.show_licenses()
  check("licenses dialog opens", dlg is not None)

  # --- newer features -------------------------------------------------------

  # Semantic tab: categorical sampling + blend mix slider + describe field.
  if hasattr(panel, "sem_expr"):
    panel.head.reset_all()
    panel.sem_expr.setCurrentIndex(5)  # happy
    panel._sample_expression()
    check("semantic sample expression drives coeffs",
          any(abs(x) > 1e-6 for x in panel.head.expression))
    panel.head.reset_all()
    panel.blend_expr2.setCurrentIndex(10)  # smile_wide
    panel.blend_expr_mix.setValue(50)      # schedules throttled blend
    panel._do_refresh()                    # flush the pending action now
    check("blend mix slider drives coeffs",
          any(abs(x) > 1e-6 for x in panel.head.expression))
    panel.desc_edit.setText("very happy, slightly surprised")
    panel._apply_description()
    check("describe field applies expression",
          any(abs(x) > 1e-6 for x in panel.head.expression))
  else:
    print("[skip] semantic tab unavailable")

  # Presets roundtrip through the backend the browser uses.
  from gnm_maya import presets
  panel.head.randomize_identity(scale=1.0, seed=99)
  before = list(panel.head.identity)
  presets.save_preset(panel.head, "_guitest")
  panel.head.reset_all()
  presets.load_preset(panel.head, "_guitest")
  check("preset save/load restores identity",
        all(abs(a - b) < 1e-6 for a, b in zip(before, panel.head.identity)))
  presets.delete_preset("_guitest")

  # Landmarks: 68 locators.
  from gnm_maya import landmarks as lmk
  grp = lmk.create_landmark_locators(panel.head)
  n_loc = len(mc.listRelatives(grp, children=True) or [])
  check("68 landmark locators created", n_loc == 68)

  # Crowd: 3 heads at distinct positions.
  from gnm_maya import crowd
  made = crowd.generate_crowd(count=3, columns=3, spacing=0.6, seed=5)
  xs = {round(mc.xform(t, query=True, translation=True, worldSpace=True)[0], 3)
        for t in made}
  check("crowd creates 3 heads at distinct X", len(made) == 3 and len(xs) == 3)

  # Shape gallery tooltips (only if the gallery was generated).
  from gnm_maya import ui as ui_mod
  if panel._gallery:
    tip = panel._id_sliders[0].toolTip()
    check("gallery tooltip embeds images", "<img" in tip)
  else:
    print("[skip] shape gallery not generated (docs/shapes)")

  npass = sum(1 for _, ok in _results if ok)
  print("\n=== GNM GUI SMOKE: %d/%d checks passed ===" % (npass, len(_results)))
  print("Panel left open. Inspect by hand: tabs, m0..mN slider labels + "
        "tooltips, the 'Show all N' buttons, per-tab Randomize/Reset, the "
        "Symmetry toggle, and the ⓘ info button.")
  return panel


# Auto-run when executed via exec(open(...).read()) or Execute All.
_PANEL = run()
