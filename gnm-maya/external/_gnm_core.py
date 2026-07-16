"""Shared GNM model loading, evaluation, and topology export.

Used by both the one-shot CLI (generate.py) and the persistent worker
(server.py). Runs only in the module venv (needs numpy), never in mayapy.

Wire formats (all little-endian, read by the pure-Python Maya reader):
  vertices.bin    float32  [V*3]           per-vertex xyz
  quads.bin       int32    [Q*4]           quad vertex indices
  quad_uvs.bin    float32  [Q*4*2]         per-face-vertex uv
  comp_<name>.bin int32    [k]             quad indices belonging to a component
  topology.json   metadata: counts, group ranges, joints, components
"""

from __future__ import annotations

import json
import os
import sys

# The GNM package is vendored as the full upstream repo under external/gnm_repo
# (the name avoids a case-insensitive clash with the `gnm` package inside it),
# so the importable `gnm` package lives at external/gnm_repo/gnm.
_GNM_REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gnm_repo")
if _GNM_REPO not in sys.path:
  sys.path.insert(0, _GNM_REPO)

import numpy as np
from gnm.shape import gnm_numpy as G


def load_model():
  return G.GNM.from_local(
      version=G.GNMMajorVersion.V3,
      variant=G.GNMVariant.HEAD,
  )


def _group_ranges(names):
  """Contiguous [start, end] index range per name prefix (e.g. 'head_017')."""
  ranges = {}
  order = []
  for i, n in enumerate(names):
    prefix = n.rsplit("_", 1)[0]
    if prefix not in ranges:
      ranges[prefix] = [i, i]
      order.append(prefix)
    ranges[prefix][1] = i
  return [(p, ranges[p][0], ranges[p][1]) for p in order]


def _mirror_pairs(names):
  """[[left_idx, right_idx], ...] for names differing only by left<->right."""
  idx = {n: i for i, n in enumerate(names)}
  pairs = []
  for i, n in enumerate(names):
    if "left" in n:
      r = n.replace("left", "right")
      if r in idx:
        pairs.append([i, idx[r]])
  return pairs


def eval_vertices(model, identity=None, expression=None, rotations=None,
                  translation=None):
  """Evaluate the mesh; returns float32 (V, 3)."""
  def arr(x, dim):
    if x is None:
      return None
    a = np.asarray(x, dtype=np.float32)
    return a if a.size else None

  verts = model(
      identity=arr(identity, model.identity_dim),
      expression=arr(expression, model.expression_dim),
      rotations=None if rotations is None else np.asarray(rotations, np.float32),
      translation=None if translation is None else np.asarray(translation, np.float32),
  )
  return np.asarray(verts, dtype=np.float32).reshape(-1, 3)


def write_vertices(verts, path):
  np.asarray(verts, dtype="<f4").reshape(-1).tofile(path)


def export_topology(model, out_dir):
  """Write constant topology (quads, uvs, components) + topology.json once."""
  os.makedirs(out_dir, exist_ok=True)

  quads = np.asarray(model.quads, dtype="<i4")
  quads.reshape(-1).tofile(os.path.join(out_dir, "quads.bin"))

  quad_uvs = np.asarray(model.quad_uvs, dtype="<f4")  # (Q,4,2)
  quad_uvs.reshape(-1).tofile(os.path.join(out_dir, "quad_uvs.bin"))

  components = []
  for name in model.mesh_component_names:
    idx = np.asarray(model.quad_indices_for_group(name), dtype="<i4")
    fname = "comp_%s.bin" % name
    idx.reshape(-1).tofile(os.path.join(out_dir, fname))
    components.append({"name": name, "file": fname, "count": int(idx.size)})

  meta = {
      "format": "gnm-topology/1",
      "num_vertices": int(model.num_vertices),
      "num_quads": int(quads.shape[0]),
      "identity_dim": int(model.identity_dim),
      "expression_dim": int(model.expression_dim),
      "identity_groups": _group_ranges(list(model.identity_names)),
      "expression_groups": _group_ranges(list(model.expression_names)),
      # Full per-mode names, so the UI can label/tooltip each slider.
      "identity_names": list(model.identity_names),
      "expression_names": list(model.expression_names),
      "joint_names": list(model.joint_names),
      "num_joints": int(model.num_joints),
      "components": components,
      # Left<->right pairs for the UI symmetry toggle.
      "expression_mirror": _mirror_pairs(list(model.expression_names)),
      "identity_mirror": _mirror_pairs(list(model.identity_names)),
      "joint_mirror": _mirror_pairs(list(model.joint_names)),
      "semantic": _semantic_meta(),
  }

  with open(os.path.join(out_dir, "topology.json"), "w") as f:
    json.dump(meta, f, indent=2)
  return meta


# GNM semantic expression -> ARKit-52 blendshape name(s). One GNM name may
# map to an ARKit Left/Right PAIR: the baked delta is then split into two
# targets with a smooth mask across the head's midline, so mocap can drive
# each side independently (Live Link Face & friends match by these names).
# Curated: only credible correspondences; unmapped GNM shapes keep their own
# names, unmapped ARKit shapes are simply absent (retargeting tolerates that).
ARKIT_MAP = {
    "wink_left": ["eyeBlinkLeft"],
    "wink_right": ["eyeBlinkRight"],
    "smile_wide": ["mouthSmileLeft", "mouthSmileRight"],
    "happy": ["cheekSquintLeft", "cheekSquintRight"],
    "corners_down": ["mouthFrownLeft", "mouthFrownRight"],
    "squint": ["eyeSquintLeft", "eyeSquintRight"],
    "snarl": ["noseSneerLeft", "noseSneerRight"],
    "pucker": ["mouthPucker"],
    "funneler": ["mouthFunnel"],
    "blow": ["cheekPuff"],
    "lips_roll_in": ["mouthRollLower", "mouthRollUpper"],
    "mouth_left": ["mouthLeft"],
    "mouth_right": ["mouthRight"],
    "stretch_face": ["mouthStretchLeft", "mouthStretchRight"],
    "compress_face": ["mouthPressLeft", "mouthPressRight"],
    "tongue_center": ["tongueOut"],
    "surprise": ["browInnerUp"],
}


def _side_masks(neutral, left_positive):
  """Smooth left/right vertex masks across the midline (x=0), ~8 mm blend."""
  import numpy as np
  x = np.asarray(neutral, np.float32).reshape(-1, 3)[:, 0]
  s = np.clip((x / 0.008) * (1.0 if left_positive else -1.0) * 0.5 + 0.5,
              0.0, 1.0)
  s = s * s * (3.0 - 2.0 * s)
  return s, 1.0 - s          # (left_mask, right_mask), each (V,)


def export_rig_data(model, out_dir, identity, num_modes=0, sampler=None,
                    mode_scale=2.0, seed=0, arkit=False):
  """Write everything Maya needs to bake a self-sufficient rig.

  Targets are evaluated at the CURRENT identity with zero pose, so blendshape
  deltas layer correctly under Maya's own LBS. Written to ``out_dir``:

    rig_neutral.bin           float32 [V*3]  neutral (identity, no expression)
    target_<name>.bin         float32 [V*3]  one per blendshape target
    rig_skin_weights.bin      float32 [J*V]  GNM skinning weights
    rig.json                  joints (positions/parents/names), target list
  """
  import numpy as np  # local: keep module import cheap for availability probe

  identity = None if identity is None else np.asarray(identity, np.float32)
  neutral = eval_vertices(model, identity=identity)
  write_vertices(neutral, os.path.join(out_dir, "rig_neutral.bin"))

  targets = []

  def write_target(name, verts):
    fname = "target_%s.bin" % name
    write_vertices(verts, os.path.join(out_dir, fname))
    targets.append({"name": name, "file": fname})

  def add_target(name, expr_vec):
    write_target(name,
                 eval_vertices(model, identity=identity, expression=expr_vec))

  # 20 semantic expression targets (fixed per-class seed => reproducible bake).
  if sampler is not None:
    import _semantic
    if arkit:
      # Which world side is the performer's Left? Read it off wink_left's
      # displacement instead of assuming an axis convention.
      wl = _semantic.EXPRESSION.index("wink_left")
      d = (eval_vertices(model, identity=identity,
                         expression=sampler.sample_expression(
                             wl, seed=seed + wl)) - neutral)
      mag = np.linalg.norm(d, axis=1)
      cx = float((neutral[:, 0] * mag).sum() / max(float(mag.sum()), 1e-12))
      left_positive = cx > 0.0  # wink_left moves the performer's-left side
      lmask, rmask = _side_masks(neutral, left_positive)
    for i, name in enumerate(_semantic.EXPRESSION):
      expr_vec = sampler.sample_expression(i, seed=seed + i)
      if not arkit or name not in ARKIT_MAP:
        add_target(name, expr_vec)
        continue
      arkit_names = ARKIT_MAP[name]
      verts = eval_vertices(model, identity=identity, expression=expr_vec)
      if len(arkit_names) == 1:
        write_target(arkit_names[0], verts)
      else:  # split one symmetric shape into ARKit Left/Right halves
        delta = verts - neutral
        for out_name, mask in zip(arkit_names, (lmask, rmask)):
          write_target(out_name, neutral + mask[:, None] * delta)

  # Optional: the first N raw basis modes OF EACH REGION as individual
  # targets (unit coefficient * mode_scale), named by their basis names.
  # Per-region (not first-N-global) so a small N still covers every region —
  # the global ordering starts with 100 left_eye_region modes.
  expr_names = list(model.expression_names)
  if num_modes:
    for _prefix, start, end in _group_ranges(expr_names):
      for k in range(start, min(start + int(num_modes), end + 1)):
        vec = np.zeros(model.expression_dim, np.float32)
        vec[k] = float(mode_scale)
        add_target(expr_names[k], vec)

  # Joints at THIS identity: template + identity basis (what GNM's own LBS
  # uses — joint_regressor exists in the npz but is unused by the pipeline).
  tj = np.asarray(model.template_joint_positions, np.float32)   # (J, 3)
  if identity is not None:
    jib = np.asarray(model.joint_identity_basis, np.float32)    # (I, J, 3)
    tj = tj + np.einsum("i,ijk->jk", identity, jib)
  joints = tj.tolist()
  weights = np.asarray(model.skinning_weights, "<f4")            # (J, V)
  weights.reshape(-1).tofile(os.path.join(out_dir, "rig_skin_weights.bin"))

  meta = {
      "format": "gnm-rig/1",
      "num_vertices": int(model.num_vertices),
      "joint_names": list(model.joint_names),
      "joint_parents": [int(p) for p in np.asarray(model.joint_parent_indices)],
      "joint_positions": joints,
      "targets": targets,
  }
  with open(os.path.join(out_dir, "rig.json"), "w") as f:
    json.dump(meta, f, indent=2)
  return meta


def _semantic_meta():
  """Availability + label lists for the semantic sampler (empty if absent)."""
  try:
    import _semantic
    return {
        "available": bool(_semantic.available(_GNM_REPO)),
        "gender": list(_semantic.GENDER),
        "ethnicity": list(_semantic.ETHNICITY),
        "expression": list(_semantic.EXPRESSION),
    }
  except Exception:
    return {"available": False}
