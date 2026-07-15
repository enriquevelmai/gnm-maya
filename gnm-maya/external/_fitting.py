"""Photo -> GNM identity fitting (numpy-only least squares).

Fits GNM identity coefficients to 68 detected 2D facial landmarks with a
weak-perspective (scaled orthographic) camera. A Procrustes alignment of the
template landmarks initializes the camera; damped Gauss-Newton then solves
JOINTLY for identity coefficients and camera (rotation, scale, translation)
with a Tikhonov prior on the coefficients. Joint solving matters: alternating
align/solve converges an order of magnitude slower because camera and shape
are strongly coupled. Runs only in the module venv (needs numpy), never in
mayapy.

GNM landmark definition (external/gnm_repo/gnm/shape/data/landmarks/
head_sparse_68.txt): 68 rows of 3 (vertex_index, barycentric_weight) pairs;
each landmark is a barycentric combination of 3 mesh vertices. The ordering
follows the standard dlib/iBUG-68 layout (jaw 0-16, brows 17-26, nose 27-35,
eyes 36-47, mouth 48-67) EXCEPT that the left-jaw segment 2-6 is stored in
reverse: GNM row 2 is chin-adjacent where iBUG point 2 is ear-adjacent
(GNM 2<->iBUG 6, 3<->5, 4=4, 5<->3, 6<->2; GNM stores the left jaw
mirror-symmetric to the right jaw instead of running ear->chin).
``IBUG_TO_GNM`` below converts between the two orders.

Optional: ``detect_landmarks(image_path)`` uses mediapipe FaceMesh
(legacy solutions API, models bundled in the wheel; requires the
mediapipe 0.10.14 install in runtime/Lib/site-packages) with a documented
468 -> 68 index mapping.
"""

from __future__ import annotations

import os
import sys

_GNM_REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gnm_repo")
if _GNM_REPO not in sys.path:
  sys.path.insert(0, _GNM_REPO)

import numpy as np
from gnm.shape import gnm_landmarks as _L

# Permutation converting iBUG-68 ordered points to GNM row order:
# gnm_points = ibug_points[IBUG_TO_GNM]. Identity except left-jaw 2-6,
# which GNM stores reversed (see module docstring). Involution, so the
# same array also converts GNM order back to iBUG order.
IBUG_TO_GNM = np.arange(68)
IBUG_TO_GNM[[2, 3, 5, 6]] = [6, 5, 3, 2]

# mediapipe FaceMesh 468-vertex -> iBUG-68 index mapping (community-standard
# correspondence; jaw points approximate the occluding contour, which
# mediapipe models as fixed mesh vertices rather than a true silhouette).
MEDIAPIPE_TO_IBUG68 = [
    # jaw 0-16
    162, 234, 93, 58, 172, 136, 149, 148, 152,
    377, 378, 365, 397, 288, 323, 454, 389,
    # right brow 17-21 (image left), left brow 22-26
    70, 63, 105, 66, 107,
    336, 296, 334, 293, 300,
    # nose bridge 27-30, nose base 31-35
    168, 197, 5, 4,
    75, 97, 2, 326, 305,
    # right eye 36-41, left eye 42-47
    33, 160, 158, 133, 153, 144,
    362, 385, 387, 263, 373, 380,
    # outer lips 48-59, inner lips 60-67
    61, 39, 37, 0, 267, 269, 291, 405, 314, 17, 84, 181,
    78, 82, 13, 312, 308, 317, 14, 87,
]


def load_landmarks_config():
  """Loads the head_sparse_68 barycentric landmark definition.

  Returns:
    LandmarksConfiguration with .indices (68, 3) int32 vertex indices and
    .weights (68, 3) float32 barycentric weights (rows sum to ~1).
  """
  return _L.load_landmarks(_L.GNMLandmarksType.HEAD_SPARSE_68)


def landmarks_3d(vertices, cfg=None):
  """Evaluates the 68 3D landmark positions on a (V, 3) vertex array."""
  if cfg is None:
    cfg = load_landmarks_config()
  vertices = np.asarray(vertices, np.float32)
  return (cfg.weights[:, :, None] * vertices[cfg.indices]).sum(axis=1)


def landmark_matrices(model, cfg=None):
  """Precomputes the landmark-restricted template and identity basis.

  Args:
    model: A gnm_numpy.GNM instance (HEAD variant).
    cfg: Optional LandmarksConfiguration; loaded if omitted.

  Returns:
    (t0, B): t0 float32 (68, 3) template landmark positions; B float32
    (identity_dim, 68, 3) per-coefficient landmark displacement basis, so
    landmarks(identity) = t0 + einsum('k,kld->ld', identity, B).
  """
  if cfg is None:
    cfg = load_landmarks_config()
  t0 = landmarks_3d(model(), cfg)
  basis = np.asarray(model.vertex_identity_basis, np.float32)  # (I, V, 3)
  # Restrict to the landmark support vertices, then blend barycentrically.
  sub = basis[:, cfg.indices]                                  # (I, 68, 3, 3)
  B = np.einsum("kljd,lj->kld", sub, cfg.weights)
  return t0.astype(np.float32), B.astype(np.float32)


def weak_perspective_align(points_3d, points_2d):
  """Aligns 3D points to 2D targets with a scaled-orthographic camera.

  Solves the unconstrained 2x3 affine map first, then projects it to the
  nearest scaled pair of orthonormal rows (classic weak-perspective POS).
  Handedness is unconstrained, which lets the map absorb the image y-down
  convention.

  Args:
    points_3d: (N, 3) model-space points.
    points_2d: (N, 2) image-space targets (pixels, y down is fine).

  Returns:
    (A, t): A float64 (2, 3) = s * [r1; r2] with orthonormal r1, r2 and
    scale s > 0; t float64 (2,) translation. Projection: p = X @ A.T + t.
  """
  X = np.asarray(points_3d, np.float64)
  Y = np.asarray(points_2d, np.float64)
  mx, my = X.mean(0), Y.mean(0)
  Xc, Yc = X - mx, Y - my
  # Least-squares 2x3 affine: Xc @ A.T ~= Yc.
  A_aff, *_ = np.linalg.lstsq(Xc, Yc, rcond=None)  # (3, 2)
  U, S, Vt = np.linalg.svd(A_aff.T, full_matrices=False)  # (2,2)(2,)(2,3)
  s = float(S.mean())
  A = s * (U @ Vt)
  t = my - mx @ A.T
  return A, t


def project(points_3d, A, t):
  """Applies a weak-perspective camera: (N, 3) -> (N, 2)."""
  return np.asarray(points_3d, np.float64) @ np.asarray(A).T + np.asarray(t)


def _skew(v):
  """3x3 cross-product matrix of a 3-vector."""
  return np.array([
      [0.0, -v[2], v[1]],
      [v[2], 0.0, -v[0]],
      [-v[1], v[0], 0.0],
  ])


def _orthonormalize(R):
  """Projects a near-rotation 3x3 matrix onto the closest orthonormal one."""
  U, _, Vt = np.linalg.svd(R)
  return U @ Vt


def fit_identity(landmarks_2d, model, iterations=3, lam=2.0, clamp=3.0,
                 order="ibug", return_camera=False):
  """Fits GNM identity coefficients to 68 2D facial landmarks.

  Initializes a weak-perspective camera by Procrustes-aligning the template
  landmarks, then runs damped Gauss-Newton jointly over the identity
  coefficients and the camera (rotation 3, log-scale 1, translation 2).
  Each step solves a Tikhonov-regularized normal system; the prior is
  normalized by the mean column norm of the shape Jacobian, so ``lam`` is
  dimensionless and invariant to image resolution and face size in pixels.
  Coefficients are ~N(0, 1) by construction, so the prior pulls toward the
  template; the result is clamped to [-clamp, clamp].

  Args:
    landmarks_2d: (68, 2) detected points in pixels (y down is fine).
    model: A gnm_numpy.GNM instance (HEAD variant).
    iterations: Gauss-Newton steps (3 is close to converged; 5+ squeezes
      out a little more on clean landmarks).
    lam: Dimensionless Tikhonov strength (units of mean per-coefficient
      landmark leverage). Tuned on synthetic round-trips: ~0.7 maximizes
      recovery on near-noise-free landmarks; 2.0 (default) is robust to
      ~1%-of-face-height landmark noise (typical detectors); larger values
      bias toward the template.
    clamp: Coefficient clamp magnitude (identity coeffs are ~N(0, 1)).
    order: "ibug" if landmarks follow the dlib/iBUG-68 layout (detectors),
      "gnm" if they already follow GNM's row order (see module docstring;
      they differ only in jaw points 2-6).
    return_camera: If True, also return the final camera as (A, t) with
      A = scale * first-two-rows-of-R, i.e. projection = X @ A.T + t.

  Returns:
    identity float32 (identity_dim,), and (A, t) if ``return_camera``.
  """
  pts = np.asarray(landmarks_2d, np.float64)
  if pts.shape != (68, 2):
    raise ValueError(f"landmarks_2d must be (68, 2), got {pts.shape}")
  if order == "ibug":
    pts = pts[IBUG_TO_GNM]
  elif order != "gnm":
    raise ValueError(f"order must be 'ibug' or 'gnm', got {order!r}")

  t0, B = landmark_matrices(model)
  t0 = t0.astype(np.float64)
  Bd = B.astype(np.float64)                     # (I, 68, 3)
  dim = Bd.shape[0]

  # Camera init: Procrustes on the template landmarks. Complete the two
  # scaled-orthonormal rows to a full rotation (handedness free, which
  # absorbs the image y-down flip).
  A, t = weak_perspective_align(t0, pts)
  s = float(np.linalg.norm(A[0]))
  R = _orthonormalize(np.vstack([A / s, np.cross(A[0], A[1]) / (s * s)]))
  coeffs = np.zeros(dim)

  eye2 = np.tile(np.eye(2), (68, 1))            # d residual / d translation
  for _ in range(int(iterations)):
    L = t0 + np.einsum("k,kld->ld", coeffs, Bd)  # (68, 3)
    RL = L @ R.T                                 # (68, 3) camera-space
    res = (pts - (s * RL[:, :2] + t)).reshape(-1)
    # Jacobian blocks (136 x (dim + 6)): [shape | rot w | log s | t].
    Jc = s * np.einsum("ed,kld->lek", R[:2], Bd).reshape(136, dim)
    Jw = np.stack(
        [s * (RL @ _skew(e).T)[:, :2] for e in np.eye(3)],
        axis=2).reshape(136, 3)
    Js = (s * RL[:, :2]).reshape(136, 1)
    J = np.hstack([Jc, Jw, Js, eye2])
    # Dimensionless prior: normalize by the shape Jacobian's scale.
    cbar = float(np.linalg.norm(Jc, axis=0).mean())
    reg = np.full(dim + 6, (lam * cbar) ** 2)
    reg[dim:] = 1e-9 * cbar * cbar               # tiny damping on camera
    rhs = J.T @ res
    rhs[:dim] -= reg[:dim] * coeffs              # prior pulls coeffs to 0
    step = np.linalg.solve(J.T @ J + np.diag(reg), rhs)
    coeffs = np.clip(coeffs + step[:dim], -clamp, clamp)
    R = _orthonormalize((np.eye(3) + _skew(step[dim:dim + 3])) @ R)
    s *= float(np.exp(step[dim + 3]))
    t = t + step[dim + 4:]

  identity = coeffs.astype(np.float32)
  if return_camera:
    return identity, (s * R[:2], t)
  return identity


def detect_landmarks(image_path, order="gnm"):
  """Detects 68 facial landmarks in a photo via mediapipe FaceMesh.

  Requires mediapipe (legacy solutions API; 0.10.14 is installed in
  runtime/Lib/site-packages with bundled models, so no network access is
  needed). The 468-vertex FaceMesh output is reduced to 68 points via
  ``MEDIAPIPE_TO_IBUG68``.

  Args:
    image_path: Path to an image file readable by OpenCV.
    order: "gnm" (default, ready for fit_identity(order='gnm')) or "ibug".

  Returns:
    float32 (68, 2) pixel coordinates (x right, y down).

  Raises:
    RuntimeError: If no face is found in the image.
  """
  import cv2
  import mediapipe as mp

  bgr = cv2.imread(str(image_path))
  if bgr is None:
    raise RuntimeError(f"could not read image: {image_path}")
  rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
  h, w = rgb.shape[:2]

  with mp.solutions.face_mesh.FaceMesh(
      static_image_mode=True, refine_landmarks=False,
      max_num_faces=1) as face_mesh:
    result = face_mesh.process(rgb)
  if not result.multi_face_landmarks:
    raise RuntimeError(f"no face detected in: {image_path}")

  mesh = result.multi_face_landmarks[0].landmark
  pts = np.array(
      [[mesh[i].x * w, mesh[i].y * h] for i in MEDIAPIPE_TO_IBUG68],
      dtype=np.float32)
  if order == "gnm":
    pts = pts[IBUG_TO_GNM]
  elif order != "ibug":
    raise ValueError(f"order must be 'gnm' or 'ibug', got {order!r}")
  return pts


def fit_identity_3d(targets, model, expression=None, lam=1.0, clamp=3.0):
  """Fits identity coefficients to 68 TARGET 3D landmark positions.

  Used by the 'fit head to edited landmark locators' feature: the artist drags
  the locators in 3D and we ridge-solve the identity basis so the head's
  landmarks match. Linear (no camera), so it is exact up to regularization.

  Args:
    targets: (68, 3) desired landmark positions, GNM landmark order, in the
      model's object space.
    model: gnm_numpy.GNM head model.
    expression: optional current expression coefficients; its landmark
      contribution is subtracted so identity absorbs only identity-shaped
      change.
    lam: Tikhonov strength, normalized by the basis column scale (coefficients
      are ~N(0,1)).
    clamp: clamp the solved coefficients to [-clamp, clamp].

  Returns:
    float32 (identity_dim,) identity coefficients.
  """
  targets = np.asarray(targets, np.float32).reshape(68, 3)
  cfg = load_landmarks_config()
  t0, B = landmark_matrices(model, cfg)
  base = t0
  if expression is not None:
    expr = np.asarray(expression, np.float32).reshape(-1)
    base = landmarks_3d(model(expression=expr[None, :])[0], cfg)
  r = (targets - base).reshape(-1)                    # (204,)
  A = B.reshape(B.shape[0], -1).T                     # (204, I)
  scale = float(np.linalg.norm(A, axis=0).mean())
  reg = (lam * scale) ** 2 * np.eye(A.shape[1], dtype=np.float64)
  sol = np.linalg.solve(A.T.astype(np.float64) @ A.astype(np.float64) + reg,
                        A.T.astype(np.float64) @ r.astype(np.float64))
  return np.clip(sol, -clamp, clamp).astype(np.float32)


def fit_from_photo(image_path, model, iterations=3, lam=2.0):
  """Convenience: detect landmarks in a photo and fit identity coefficients."""
  pts = detect_landmarks(image_path, order="gnm")
  return fit_identity(pts, model, iterations=iterations, lam=lam, order="gnm")
