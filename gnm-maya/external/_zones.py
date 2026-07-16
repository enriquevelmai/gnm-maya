"""Feature-zone masked randomization that STAYS in coefficient space.

The model's own basis groups are coarse (head/eyes identity; per-eye +
lower-face expression). For finer areas (nose, mouth, jaw, brows) there is no
dedicated basis, so a plain coefficient mask can't isolate them. This module
does it geometrically WITHOUT leaving the coefficient system:

  1. Build a smooth per-vertex zone weight w (1 inside the feature,
     tapering to 0) from the 68 sparse landmarks' positions.
  2. Draw a random full-basis candidate r, form the masked target
     verts(cur) + w * (verts(r) - verts(cur)).
  3. Ridge-solve the basis (which is LINEAR: verts = t + coeffs @ B) back to
     one coefficient vector that best reproduces that target, with a prior
     anchored at the CURRENT coefficients so unrelated modes stay put.

The result is a normal coefficient vector — sliders, presets and rig baking
all stay consistent — whose effect is confined to the zone (softly: the ridge
finds the best in-model approximation). scale=0 targets NEUTRAL inside the
zone, giving a zone-local reset. Runs in the module runtime (numpy).
"""

from __future__ import annotations

import numpy as np

import _fitting

# iBUG-68 landmark index groups (row sets are order-invariant, so the GNM
# left-jaw permutation does not matter here).
ZONES = {
    "jaw":   list(range(0, 17)),
    "brows": list(range(17, 27)),
    "nose":  list(range(27, 36)),
    "eyes":  list(range(36, 48)),
    "mouth": list(range(48, 68)),
}

# Per-zone falloff radii in model units (meters): full effect within r0 of a
# zone landmark, smoothstep to zero at r1. Tuned on the ~0.19 m GNM head.
RADII = {
    "jaw":   (0.020, 0.065),
    "brows": (0.012, 0.042),
    "nose":  (0.014, 0.045),
    "eyes":  (0.014, 0.042),
    "mouth": (0.018, 0.055),
}

# Interior mesh components that belong to a zone even though they sit far
# from its (skin) landmarks — without this the solver would anchor the tongue
# and lower teeth while opening the mouth/jaw around them.
ZONE_COMPONENTS = {
    "mouth": ("tongue", "lower_teeth_and_gums"),
    "jaw":   ("tongue", "lower_teeth_and_gums"),
    "eyes":  ("left_eye", "right_eye"),
}

_weights_cache = {}   # tuple(sorted(zones)) -> (V,) float32
_landmarks_cache = {"pts": None}


def _template_landmarks(model):
  if _landmarks_cache["pts"] is None:
    import _gnm_core as core
    _landmarks_cache["pts"] = _fitting.landmarks_3d(core.eval_vertices(model))
  return _landmarks_cache["pts"]


def zone_weights(model, zones):
  """Smooth (V,) weight field: 1 inside the union of zones, 0 far away."""
  key = tuple(sorted(zones))
  cached = _weights_cache.get(key)
  if cached is not None:
    return cached
  import _gnm_core as core
  verts = core.eval_vertices(model)                    # neutral template (V,3)
  lm = _template_landmarks(model)                      # (68, 3)
  w = np.zeros(verts.shape[0], np.float32)
  quads = np.asarray(model.quads, np.int64)
  for z in zones:
    if z not in ZONES:
      raise ValueError("unknown zone %r (have: %s)" % (z, sorted(ZONES)))
    pts = lm[ZONES[z]]                                 # (n, 3)
    d = np.sqrt(((verts[:, None, :] - pts[None, :, :]) ** 2).sum(-1)).min(1)
    r0, r1 = RADII[z]
    t = np.clip((r1 - d) / max(r1 - r0, 1e-9), 0.0, 1.0)
    w = np.maximum(w, (t * t * (3.0 - 2.0 * t)).astype(np.float32))
    # Interior components (tongue, teeth, eyeballs) ride with their zone.
    for comp in ZONE_COMPONENTS.get(z, ()):
      qidx = np.asarray(model.quad_indices_for_group(comp), np.int64)
      vids = np.unique(quads[qidx].reshape(-1))
      w[vids] = 1.0
  _weights_cache[key] = w
  return w


def _basis(model, kind):
  if kind == "identity":
    return np.asarray(model.vertex_identity_basis, np.float32)   # (I, V, 3)
  return np.asarray(model.expression_basis, np.float32)          # (E, V, 3)


# Solver-system cache: rebuilding Bs/A⁻¹ dominates the solve, and the
# variants contact sheet fires 9 solves with the same zones back to back.
# Tiny LRU (the matrices are tens of MB each).
_solver_cache = {}   # (kind, zone_key) -> dict(idx, Bs, Bw, Ainv, wv, lam)
_SOLVER_LRU = 2

# Tuned on the leak-vs-effect sweep (see repo history). Identity needs
# stiffer far-field anchoring than expression: its modes are globally
# correlated (a nose change rides on whole-head modes), while the expression
# basis is already region-decomposed. Result: a clearly visible zone effect
# with a smooth halo (~1 mm at the falloff ring, <0.3 mm at the ears/skull).
_ANCHOR_W = {"identity": 30.0, "expression": 10.0}
_ANCHOR_STEP = 2
_LAM_REL = 2e-4


def _solver(model, kind, zones):
  key = (kind, tuple(sorted(zones)))
  hit = _solver_cache.pop(key, None)
  if hit is None:
    B = _basis(model, kind)
    K = B.shape[0]
    w = zone_weights(model, zones)
    sel = np.flatnonzero(w > 1e-4)
    rest = np.setdiff1d(np.arange(w.size, dtype=np.int64),
                        sel)[::_ANCHOR_STEP]
    idx = np.concatenate([sel, rest])
    rw3 = np.repeat(np.concatenate([np.ones(sel.size, np.float32),
                                    np.full(rest.size, _ANCHOR_W[kind],
                                            np.float32)]), 3)
    Bs = B[:, idx, :].reshape(K, -1)                   # (K, 3n)
    Bw = Bs * rw3                                      # anchor-weighted copy
    A = Bw @ Bs.T
    lam = float(_LAM_REL) * float(np.trace(A)) / K
    A[np.diag_indices_from(A)] += lam
    hit = {"idx": idx, "Bs": Bs, "Bw": Bw, "wv": np.repeat(w[idx], 3),
           "Ainv": np.linalg.inv(A.astype(np.float64)), "lam": lam, "K": K}
  _solver_cache[key] = hit                             # refresh LRU position
  while len(_solver_cache) > _SOLVER_LRU:
    _solver_cache.pop(next(iter(_solver_cache)))
  return hit


def zone_randomize(model, kind, zones, identity=None, expression=None,
                   scale=1.0, seed=None, clamp=4.0):
  """One new full ``kind`` coefficient vector, changed only inside ``zones``.

  scale > 0: the zone moves toward a fresh N(0, scale) random draw.
  scale = 0: the zone moves toward NEUTRAL (a zone-local reset).

  Isolation is SOFT by nature: the basis is anatomically correlated, so a
  small, smoothly decaying halo around the zone remains (measured ~1 mm at
  the falloff ring for a ~4 mm nose change, <0.3 mm far away) — reads as the
  face staying plausible rather than as leakage.
  """
  rng = np.random.default_rng(seed)
  s = _solver(model, kind, zones)
  K = s["K"]

  cur = np.zeros(K, np.float32)
  src = identity if kind == "identity" else expression
  if src is not None:
    n = min(K, len(src))
    cur[:n] = np.asarray(src, np.float32)[:n]
  r = (rng.normal(0.0, 1.0, K).astype(np.float32) * float(scale)
       if scale > 0 else np.zeros(K, np.float32))

  d_cur = cur @ s["Bs"]                                # current delta field
  d_r = r @ s["Bs"]                                    # candidate delta field
  y = d_cur + s["wv"] * (d_r - d_cur)                  # masked target deltas

  b = s["Bw"] @ y + s["lam"] * cur
  x = s["Ainv"] @ b.astype(np.float64)
  x = np.clip(x, -clamp, clamp)
  return [float(v) for v in x]
