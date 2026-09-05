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

import os

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

# Procedural (non-landmark) zones, derived from the template geometry.
PROC_ZONES = ("ears", "back_head")

_weights_cache = {}   # (zone key, shrink, maps key) -> (V,) float32
_landmarks_cache = {"pts": None}


def _skin_vertex_ids(model):
  quads = np.asarray(model.quads, np.int64)
  qidx = np.asarray(model.quad_indices_for_group("skin"), np.int64)
  return np.unique(quads[qidx].reshape(-1))


def _proc_zone_weight(model, verts, zone, shrink):
  """Ears: falloff from the extreme-|x| skin verts (ear tips). Back of head:
  everything behind the ear plane, blending in over a few cm."""
  skin = _skin_vertex_ids(model)
  sx = verts[skin]
  xmax = float(np.abs(sx[:, 0]).max())
  tips = skin[np.abs(sx[:, 0]) > 0.92 * xmax]
  if zone == "ears":
    pts = verts[tips]
    d = np.sqrt(((verts[:, None, :] - pts[None, :, :]) ** 2).sum(-1)).min(1)
    r0, r1 = 0.015 * shrink, 0.045 * shrink
    t = np.clip((r1 - d) / max(r1 - r0, 1e-9), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)
  # back_head: behind the ears' mean z (the head faces +z in GNM space)
  z_ear = float(verts[tips][:, 2].mean())
  band = 0.04 * shrink
  t = np.clip((z_ear - 0.01 - verts[:, 2]) / band, 0.0, 1.0)
  w = (t * t * (3.0 - 2.0 * t)).astype(np.float32)
  w[np.setdiff1d(np.arange(verts.shape[0]), skin)] = 0.0  # skin only
  return w


def _maps_key(maps):
  key = []
  for p in maps or ():
    try:
      key.append((p, os.path.getmtime(p), os.path.getsize(p)))
    except OSError:
      key.append((p, 0, 0))
  return tuple(key)


def _load_map(path, n):
  w = np.fromfile(path, "<f4")
  if w.size != n:
    raise ValueError("painted map %s has %d values, mesh has %d vertices"
                     % (path, w.size, n))
  return np.clip(w, 0.0, 1.0).astype(np.float32)


def _template_landmarks(model):
  if _landmarks_cache["pts"] is None:
    import _gnm_core as core
    _landmarks_cache["pts"] = _fitting.landmarks_3d(core.eval_vertices(model))
  return _landmarks_cache["pts"]


def zone_weights(model, zones, shrink=1.0, maps=None):
  """Smooth (V,) weight field: 1 inside the union of zones, 0 far away.

  ``zones`` may mix landmark zones (ZONES), procedural ones (PROC_ZONES) and
  ``maps`` — paths to user-PAINTED float32 [V] weight files (Maya vertex
  colours baked by scene/zones.py); all are max-combined. ``shrink`` scales
  the falloff radii (<1 = tighter): the sculpt zones use the default, the
  ARKit target masks a tighter field so neighboring regions barely overlap.
  """
  key = (tuple(sorted(zones)), round(float(shrink), 3), _maps_key(maps))
  cached = _weights_cache.get(key)
  if cached is not None:
    return cached
  import _gnm_core as core
  verts = core.eval_vertices(model)                    # neutral template (V,3)
  lm = _template_landmarks(model)                      # (68, 3)
  w = np.zeros(verts.shape[0], np.float32)
  quads = np.asarray(model.quads, np.int64)
  for p in maps or ():
    w = np.maximum(w, _load_map(p, verts.shape[0]))
  for z in zones:
    if z in PROC_ZONES:
      w = np.maximum(w, _proc_zone_weight(model, verts, z, shrink))
      continue
    if z not in ZONES:
      raise ValueError("unknown zone %r (have: %s)"
                       % (z, sorted(ZONES) + list(PROC_ZONES)))
    pts = lm[ZONES[z]]                                 # (n, 3)
    d = np.sqrt(((verts[:, None, :] - pts[None, :, :]) ** 2).sum(-1)).min(1)
    r0, r1 = RADII[z]
    r0, r1 = r0 * shrink, r1 * shrink
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


def _solver(model, kind, zones, maps=None):
  key = (kind, tuple(sorted(zones)), _maps_key(maps))
  hit = _solver_cache.pop(key, None)
  if hit is None:
    B = _basis(model, kind)
    K = B.shape[0]
    w = zone_weights(model, zones, maps=maps)
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
                   scale=1.0, seed=None, clamp=4.0, maps=None,
                   return_draw=False, sculpt_prev=None):
  """One new full ``kind`` coefficient vector, changed only inside ``zones``
  (+ painted ``maps``).

  scale > 0: the zone moves toward a fresh N(0, scale) random draw.
  scale = 0: the zone moves toward NEUTRAL (a zone-local reset).

  Isolation is SOFT by nature: the basis is anatomically correlated, so a
  small, smoothly decaying halo around the zone remains (measured ~1 mm at
  the falloff ring for a ~4 mm nose change, <0.3 mm far away) — reads as the
  face staying plausible rather than as leakage.
  """
  rng = np.random.default_rng(seed)
  s = _solver(model, kind, zones, maps)
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
  if sculpt_prev is not None:  # what the head REALLY looks like now
    d_cur = d_cur + np.asarray(sculpt_prev, np.float32)[s["idx"]].reshape(-1)
  # Inside the mask the candidate REPLACES the current shape (like an
  # unmasked randomize replaces the whole face); outside it stays. Blending
  # from the true current surface (incl. the old layer) is what keeps
  # repeated clicks from accumulating.
  y = (1.0 - s["wv"]) * d_cur + s["wv"] * d_r

  b = s["Bw"] @ y + s["lam"] * cur
  x = s["Ainv"] @ b.astype(np.float64)
  x = np.clip(x, -clamp, clamp)
  out = [float(v) for v in x]
  if return_draw:
    return out, r
  return out


def masked_residual(model, kind, zones, identity, expression, x, r,
                    sculpt_prev=None, maps=None):
  """The EXACT-mask layer: everything the in-model solve could not confine.

  target   = (1-w) * (cur + sculpt_prev) + w * rand   (bind pose)
  new      = eval(x)                                  (the ridge solution)
  residual = target - new                             -> added on top

  So the final head equals the CURRENT surface wherever w == 0 (unpainted
  verts do not move at all, up to float precision) and a FRESH candidate
  inside the mask — repeated clicks re-roll the area, they don't pile up.
  Returns float32 (V, 3).
  """
  import _gnm_core as core
  cur_id = None if identity is None else np.asarray(identity, np.float32)
  cur_ex = None if expression is None else np.asarray(expression, np.float32)
  x = np.asarray(x, np.float32)
  r = np.asarray(r, np.float32)
  base = core.eval_bind(model, cur_id, cur_ex)
  if kind == "identity":
    rand = core.eval_bind(model, r, cur_ex)
    new = core.eval_bind(model, x, cur_ex)
  else:
    rand = core.eval_bind(model, cur_id, r)
    new = core.eval_bind(model, cur_id, x)
  w = zone_weights(model, zones, maps=maps)[:, None]
  cur_total = base
  if sculpt_prev is not None:
    cur_total = base + np.asarray(sculpt_prev, np.float32).reshape(base.shape)
  # target = (1-w) * current surface + w * fresh candidate  (no accumulation)
  target = (1.0 - w) * cur_total + w * rand
  residual = target - new
  return residual.astype(np.float32)


# --- UV-space mask texture (viewport preview) ------------------------------------

_texture_cache = {}   # (zone key, maps key, size) -> path


def mask_texture(model, zones, out_path, maps=None, size=1024):
  """Rasterise the per-vertex zone weights into a grey UV-space PNG.

  Viewport 2.0 shows textures reliably where vertex colours can be a
  no-show (e.g. through Arnold shaders), so the mask 'spotlight' is drawn
  as a texture on a temporary lambert. Quads are split into two triangles
  and the weights interpolated barycentrically; a 4 px dilation covers the
  UV seams.
  """
  import _render
  key = (tuple(sorted(zones)), _maps_key(maps), int(size))
  hit = _texture_cache.get(key)
  if hit and os.path.isfile(hit) and hit == out_path:
    return hit
  w = zone_weights(model, zones, maps=maps)
  quads = np.asarray(model.quads, np.int64)               # (Q, 4)
  uvs = np.asarray(model.quad_uvs, np.float32)             # (Q, 4, 2)
  S = int(size)
  img = np.zeros((S, S), np.float32)
  cover = np.zeros((S, S), bool)
  tri_v = np.concatenate([quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]], 0)
  tri_uv = np.concatenate([uvs[:, [0, 1, 2]], uvs[:, [0, 2, 3]]], 0)
  px = tri_uv[:, :, 0] * (S - 1)
  py = (1.0 - tri_uv[:, :, 1]) * (S - 1)                   # v up -> row down
  wt = w[tri_v]                                            # (T, 3)
  for t in range(tri_v.shape[0]):
    if wt[t].max() <= 0.0:
      continue
    x0, x1 = int(np.floor(px[t].min())), int(np.ceil(px[t].max()))
    y0, y1 = int(np.floor(py[t].min())), int(np.ceil(py[t].max()))
    if x1 < x0 or y1 < y0:
      continue
    xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
    xs = xs.astype(np.float32) + 0.5
    ys = ys.astype(np.float32) + 0.5
    (ax, bx, cx), (ay, by, cy) = px[t], py[t]
    det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if abs(det) < 1e-9:
      continue
    l1 = ((bx - xs) * (cy - ys) - (cx - xs) * (by - ys)) / det
    l2 = ((cx - xs) * (ay - ys) - (ax - xs) * (cy - ys)) / det
    l3 = 1.0 - l1 - l2
    inside = (l1 >= -0.002) & (l2 >= -0.002) & (l3 >= -0.002)
    if not inside.any():
      continue
    val = l1 * wt[t, 0] + l2 * wt[t, 1] + l3 * wt[t, 2]
    sub = img[y0:y1 + 1, x0:x1 + 1]
    np.maximum(sub, np.where(inside, val, 0.0), out=sub)
    cover[y0:y1 + 1, x0:x1 + 1] |= inside
  # dilate a few px into uncovered pixels so seams don't show as black lines
  for _ in range(4):
    grown = img.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
      shifted = np.roll(img, (dy, dx), axis=(0, 1))
      grown = np.where(cover, grown, np.maximum(grown, shifted))
    img = grown
    cover = cover | (img > 0)
  rgb = np.repeat((np.clip(img, 0, 1) * 255).astype(np.uint8)[:, :, None], 3, 2)
  _render.write_png(out_path, rgb)
  _texture_cache[key] = out_path
  return out_path
