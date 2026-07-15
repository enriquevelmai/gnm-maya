"""Tiny numpy software rasterizer for GNM shape-gallery thumbnails.

Pure numpy + stdlib (zlib/struct): renders an orthographic, flat-shaded,
z-buffered front view of the head and writes PNGs without any imaging
dependency. Runs only in the module runtime (needs numpy), never in mayapy.

The rasterizer is fully vectorized: triangles are bucketed by screen-space
bounding-box size, candidate pixels for a whole bucket are tested with
barycentric coordinates at once, and the z-buffer is resolved with a single
lexsort (nearest fragment per pixel wins). Rendering is done at 2x
supersampling and box-downsampled for antialiasing.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

# Warm light gray head on a dark neutral background.
BASE_COLOR = np.array([228.0, 218.0, 205.0], np.float32)
BG_COLOR = np.array([28.0, 29.0, 33.0], np.float32)

# Directional lights in view space (+x right, +y up, +z toward viewer).
_LIGHT_KEY = np.array([-0.45, 0.60, 0.66], np.float32)
_LIGHT_KEY = _LIGHT_KEY / np.linalg.norm(_LIGHT_KEY)


def _view_transform(vertices, yaw_deg):
  """Rotate around Y into view space; +z points toward the viewer."""
  yaw = np.deg2rad(float(yaw_deg))
  c, s = np.cos(yaw), np.sin(yaw)
  x, y, z = vertices[:, 0], vertices[:, 1], vertices[:, 2]
  return np.stack([c * x + s * z, y, -s * x + c * z], axis=1)


def compute_frame(vertices, size=192, yaw_deg=15, fill=0.82):
  """Framing params (shared across renders so all images are comparable).

  Args:
    vertices: (V, 3) float32, typically the NEUTRAL head.
    size: output image size in pixels.
    yaw_deg: yaw used for rendering (framing is computed in view space).
    fill: fraction of the image the head's larger extent should span.

  Returns:
    Dict with center/scale/size/yaw, pass to render_head(frame=...).
  """
  v = _view_transform(np.asarray(vertices, np.float32), yaw_deg)
  lo, hi = v.min(axis=0), v.max(axis=0)
  cx, cy = 0.5 * (lo[0] + hi[0]), 0.5 * (lo[1] + hi[1])
  extent = max(hi[0] - lo[0], hi[1] - lo[1])
  return {
      "size": int(size),
      "yaw_deg": float(yaw_deg),
      "cx": float(cx),
      "cy": float(cy),
      "scale": float(size) * float(fill) / max(extent, 1e-9),
  }


def render_head(vertices, triangles, size=192, yaw_deg=15, frame=None,
                supersample=2):
  """Render an orthographic flat-shaded view; returns uint8 (size, size, 3).

  Args:
    vertices: (V, 3) float32 positions.
    triangles: (T, 3) int32 vertex indices.
    size: output resolution (square).
    yaw_deg: rotation around Y (ignored if ``frame`` provides one).
    frame: optional dict from compute_frame(); pass the SAME frame for every
      shape so all gallery images share identical scale/offset.
    supersample: integer AA factor (rendered at size*ss, box-filtered down).

  Returns:
    (size, size, 3) uint8 RGB image.
  """
  vertices = np.asarray(vertices, np.float32)
  triangles = np.asarray(triangles, np.int64)
  if frame is None:
    frame = compute_frame(vertices, size=size, yaw_deg=yaw_deg)
  size = int(frame["size"])
  ss = max(1, int(supersample))
  S = size * ss

  v = _view_transform(vertices, frame["yaw_deg"])
  # Screen coords (pixel units at supersampled res); +y down in image space.
  px = (v[:, 0] - frame["cx"]) * frame["scale"] * ss + 0.5 * S
  py = 0.5 * S - (v[:, 1] - frame["cy"]) * frame["scale"] * ss
  pz = v[:, 2]  # larger = closer to viewer

  a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
  ax, ay, az = px[a], py[a], pz[a]
  bx, by, bz = px[b], py[b], pz[b]
  cx_, cy_, cz = px[c], py[c], pz[c]

  # Flat shading from view-space geometric normals; flip toward the viewer so
  # visible (front-facing) fragments are shaded correctly for either winding.
  e1 = v[b] - v[a]
  e2 = v[c] - v[a]
  n = np.cross(e1, e2)
  norm = np.linalg.norm(n, axis=1)
  norm[norm == 0] = 1.0
  n /= norm[:, None]
  n[n[:, 2] < 0] *= -1.0
  lambert_head = np.clip(n[:, 2], 0.0, 1.0)          # headlight (0,0,1)
  lambert_key = np.clip(n @ _LIGHT_KEY, 0.0, 1.0)    # top-left key
  intensity = np.clip(0.16 + 0.52 * lambert_head + 0.40 * lambert_key, 0, 1)
  tri_color = (BASE_COLOR[None, :] * intensity[:, None]).astype(np.float32)

  # Integer bounding boxes, clipped to screen.
  x0 = np.clip(np.floor(np.minimum(np.minimum(ax, bx), cx_)), 0, S - 1)
  x1 = np.clip(np.ceil(np.maximum(np.maximum(ax, bx), cx_)), 0, S - 1)
  y0 = np.clip(np.floor(np.minimum(np.minimum(ay, by), cy_)), 0, S - 1)
  y1 = np.clip(np.ceil(np.maximum(np.maximum(ay, by), cy_)), 0, S - 1)
  x0, x1, y0, y1 = (t.astype(np.int64) for t in (x0, x1, y0, y1))

  # Signed double area (edge-function denominator); drop degenerates and
  # triangles entirely off screen.
  d = (by - cy_) * (ax - cx_) + (cx_ - bx) * (ay - cy_)
  alive = (d != 0) & (x1 >= x0) & (y1 >= y0)
  alive &= (x0 <= S - 1) & (y0 <= S - 1) & (x1 >= 0) & (y1 >= 0)

  bmax = np.maximum(x1 - x0, y1 - y0) + 1

  frag_pix, frag_z, frag_tri = [], [], []
  prev_k = 0
  for K in (2, 4, 8, 16, 32, 64, 128, 256):
    sel = np.nonzero(alive & (bmax > prev_k) & (bmax <= K))[0]
    prev_k = K
    if sel.size == 0:
      continue
    oy, ox = np.divmod(np.arange(K * K, dtype=np.int64), K)
    xs = x0[sel, None] + ox[None, :]           # (n, K*K)
    ys = y0[sel, None] + oy[None, :]
    inside_box = (xs <= x1[sel, None]) & (ys <= y1[sel, None])

    # Barycentric at pixel centers, normalized by the signed area so the
    # inside test (all weights in [0, 1]) works for both windings.
    fx = xs + 0.5
    fy = ys + 0.5
    dd = d[sel, None]
    w0 = ((by[sel, None] - cy_[sel, None]) * (fx - cx_[sel, None])
          + (cx_[sel, None] - bx[sel, None]) * (fy - cy_[sel, None])) / dd
    w1 = ((cy_[sel, None] - ay[sel, None]) * (fx - cx_[sel, None])
          + (ax[sel, None] - cx_[sel, None]) * (fy - cy_[sel, None])) / dd
    w2 = 1.0 - w0 - w1
    hit = inside_box & (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    if not hit.any():
      continue
    z = w0 * az[sel, None] + w1 * bz[sel, None] + w2 * cz[sel, None]
    tri_idx = np.broadcast_to(sel[:, None], hit.shape)
    pix = ys * S + xs
    frag_pix.append(pix[hit])
    frag_z.append(z[hit].astype(np.float32))
    frag_tri.append(tri_idx[hit])

  img = np.empty((S * S, 3), np.float32)
  img[:] = BG_COLOR
  if frag_pix:
    pix = np.concatenate(frag_pix)
    z = np.concatenate(frag_z)
    tri = np.concatenate(frag_tri)
    # Z-buffer: sort by pixel then nearest-first, keep first per pixel.
    order = np.lexsort((-z, pix))
    pix = pix[order]
    tri = tri[order]
    first = np.empty(pix.shape, bool)
    first[0] = True
    first[1:] = pix[1:] != pix[:-1]
    img[pix[first]] = tri_color[tri[first]]

  img = img.reshape(S, S, 3)
  if ss > 1:
    img = img.reshape(size, ss, size, ss, 3).mean(axis=(1, 3))
  return np.clip(img + 0.5, 0, 255).astype(np.uint8)


def write_png(path, img_uint8):
  """Write an RGB uint8 image as PNG (pure stdlib: zlib + struct)."""
  img = np.asarray(img_uint8, np.uint8)
  if img.ndim != 3 or img.shape[2] != 3:
    raise ValueError("write_png expects (H, W, 3) uint8, got %r" % (img.shape,))
  h, w = img.shape[:2]

  def chunk(tag, payload):
    out = struct.pack(">I", len(payload)) + tag + payload
    return out + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

  # Filter type 0 (None) prepended to each scanline.
  rows = np.zeros((h, 1 + w * 3), np.uint8)
  rows[:, 1:] = img.reshape(h, w * 3)
  ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
  with open(path, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n")
    f.write(chunk(b"IHDR", ihdr))
    f.write(chunk(b"IDAT", zlib.compress(rows.tobytes(), 6)))
    f.write(chunk(b"IEND", b""))
