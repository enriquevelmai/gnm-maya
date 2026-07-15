#!/usr/bin/env python
"""Shape-gallery generator: PNG thumbnails for every UI slider shape.

Renders the MIN (-3) and MAX (+3) of the first N modes of each identity and
expression basis group, one image per semantic expression, and a neutral
reference — all with IDENTICAL framing so differences are visually
comparable. Also writes a self-contained index.html gallery and a
manifest.json the Maya UI can use for slider tooltips.

Run with the module runtime (needs numpy; h5py for semantic shapes):

  ./runtime/python.exe external/gen_gallery.py --out docs/shapes \
      [--size 192] [--modes-per-group 12]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gnm_core as core
import _render

COEFF = 3.0  # slider min/max coefficient rendered for every basis mode
_REPO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gnm_repo")


def _log(*a):
  print(*a, file=sys.stderr, flush=True)


def _group_modes(names, per_group):
  """[(basis_name, mode_index), ...] for the first N modes of each prefix group."""
  out = []
  for prefix, start, end in core._group_ranges(list(names)):
    for k in range(start, min(start + per_group, end + 1)):
      out.append((names[k], k))
  return out


def _write_index_html(path, size, sections, semantic_rows):
  """Self-contained dark gallery page (no external assets)."""
  thumb = int(size)
  css = """
  body { background:#17181c; color:#d7d9de; font:14px/1.5 -apple-system,
         "Segoe UI", Roboto, sans-serif; margin:0; padding:24px 32px; }
  h1 { font-size:20px; font-weight:600; color:#f0f1f4; margin:0 0 4px; }
  h2 { font-size:15px; font-weight:600; color:#aeb2bb; margin:32px 0 12px;
       border-bottom:1px solid #2a2c33; padding-bottom:6px; }
  p.sub { color:#8b8f99; margin:0 0 8px; }
  .row { display:flex; align-items:center; gap:10px; padding:6px 0; }
  .row img { width:%(t)dpx; height:%(t)dpx; border-radius:6px;
             background:#1c1d22; display:block; }
  .row .name { font-family:Consolas, monospace; font-size:13px;
               color:#e4e6ea; margin-left:14px; }
  .lbl { font-size:11px; color:#6f7480; text-align:center; width:%(t)dpx; }
  .cell { display:flex; flex-direction:column; gap:3px; }
  .grid { display:flex; flex-wrap:wrap; gap:14px; }
  .grid .cell .lbl { color:#aeb2bb; font-size:12px;
                     font-family:Consolas, monospace; }
  nav { position:sticky; top:0; background:#17181cee; backdrop-filter:blur(4px);
        padding:10px 0; margin:0 0 8px; border-bottom:1px solid #2a2c33;
        display:flex; flex-wrap:wrap; gap:6px 10px; z-index:10; }
  nav a { color:#8ab4f8; text-decoration:none; font-size:12px;
          padding:3px 9px; background:#22242b; border-radius:12px;
          white-space:nowrap; }
  nav a:hover { background:#2d3039; color:#c2d7fb; }
  h2 { scroll-margin-top:64px; }
  a.top { position:fixed; right:22px; bottom:18px; background:#22242b;
          color:#8ab4f8; text-decoration:none; padding:8px 12px;
          border-radius:18px; font-size:13px; border:1px solid #2a2c33; }
  a.top:hover { background:#2d3039; }
  """ % {"t": thumb}

  def _anchor(title):
    return "".join(c if c.isalnum() else "-" for c in title.lower())

  parts = [
      "<!DOCTYPE html>",
      "<html><head><meta charset='utf-8'>",
      "<title>GNM head shape gallery</title>",
      "<style>%s</style></head><body id='top'>" % css,
      "<h1>GNM head &mdash; shape gallery</h1>",
      "<p class='sub'>Each basis mode rendered at coefficient "
      "&minus;%g (min) and +%g (max); identical framing throughout.</p>"
      % (COEFF, COEFF),
  ]
  # Sticky navigation: one chip per section (with mode counts) + semantic.
  nav = ["<nav>"]
  for title, _rows, _neutral in sections:
    nav.append("<a href='#%s'>%s</a>"
               % (_anchor(title), html.escape(title.split(" (")[0])))
  if semantic_rows:
    nav.append("<a href='#semantic'>semantic (%d)</a>" % len(semantic_rows))
  nav.append("</nav>")
  parts.append("".join(nav))

  for title, rows, neutral_file in sections:
    parts.append("<h2 id='%s'>%s</h2>" % (_anchor(title), html.escape(title)))
    for name, fmin, fmax in rows:
      parts.append(
          "<div class='row'>"
          "<div class='cell'><img src='%s' loading='lazy'>"
          "<div class='lbl'>min</div></div>"
          "<div class='cell'><img src='%s' loading='lazy'>"
          "<div class='lbl'>neutral</div></div>"
          "<div class='cell'><img src='%s' loading='lazy'>"
          "<div class='lbl'>max</div></div>"
          "<span class='name'>%s</span></div>"
          % (html.escape(fmin), html.escape(neutral_file),
             html.escape(fmax), html.escape(name)))
  if semantic_rows:
    parts.append("<h2 id='semantic'>Semantic expressions</h2>")
    parts.append("<div class='grid'>")
    for name, fname in semantic_rows:
      parts.append(
          "<div class='cell'><img src='%s' loading='lazy'>"
          "<div class='lbl'>%s</div></div>"
          % (html.escape(fname), html.escape(name)))
    parts.append("</div>")
  parts.append("<a class='top' href='#top'>&uarr; top</a>")
  parts.append("</body></html>")
  with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(parts))


def _rebuild_html_from_manifest(out_dir):
  """Regenerate index.html from manifest.json (no image rendering).

  Sections are reconstructed from the image filenames, which encode the kind
  (identity_/expression_) that the manifest keys alone don't carry.
  """
  import json
  with open(os.path.join(out_dir, "manifest.json")) as f:
    m = json.load(f)

  # Group manifest keys by (kind, prefix), preserving mode order.
  groups = {}  # (kind, prefix) -> [(name, fmin, fmax)]
  order = []
  for name, entry in m["images"].items():
    kind = entry["min"].split("_", 1)[0]  # identity_... / expression_...
    prefix = name.rsplit("_", 1)[0]
    key = (kind, prefix)
    if key not in groups:
      groups[key] = []
      order.append(key)
    groups[key].append((name, entry["min"], entry["max"]))

  neutrals = m.get("neutrals", {})
  sections = [("%s / %s (%d modes)" % (kind, prefix, len(groups[(kind, prefix)])),
               groups[(kind, prefix)],
               neutrals.get(prefix, "neutral.png"))
              for kind, prefix in order]
  semantic_rows = sorted(m.get("semantic", {}).items())
  _write_index_html(os.path.join(out_dir, "index.html"), m.get("size", 192),
                    sections, semantic_rows)
  _log("Rebuilt index.html (%d sections, %d semantic)"
       % (len(sections), len(semantic_rows)))
  print("OK %s" % out_dir)
  return 0


def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument("--out", required=True)
  p.add_argument("--size", type=int, default=192)
  p.add_argument("--modes-per-group", type=int, default=12)
  p.add_argument("--html-only", action="store_true",
                 help="Rebuild index.html from the existing manifest without "
                      "re-rendering any image.")
  args = p.parse_args()

  if args.html_only:
    return _rebuild_html_from_manifest(args.out)

  import numpy as np

  os.makedirs(args.out, exist_ok=True)

  _log("Loading GNM V3 HEAD...")
  model = core.load_model()
  triangles = np.asarray(model.triangles, np.int32)

  neutral_verts = core.eval_vertices(model)
  frame = _render.compute_frame(neutral_verts, size=args.size)

  t_start = time.time()
  n_images = 0

  def render_to(fname, verts):
    nonlocal n_images
    img = _render.render_head(verts, triangles, frame=frame)
    _render.write_png(os.path.join(args.out, fname), img)
    n_images += 1
    return img

  neutral_img = render_to("neutral.png", neutral_verts).astype(np.float32)
  diffs = []  # (label, mean abs pixel diff vs neutral)

  # 20 semantic expressions (one image each; no min/max).
  semantic = {}
  try:
    import _semantic
    sampler = _semantic.Sampler(_REPO_DIR)
  except Exception as exc:  # h5py or decoder files missing
    sampler = None
    _log("Semantic sampler unavailable, skipping: %s" % exc)
  if sampler is not None:
    for i, name in enumerate(_semantic.EXPRESSION):
      expr = sampler.sample_expression(i, seed=0)
      fname = "semantic_%s.png" % name
      img = render_to(fname, core.eval_vertices(model, expression=expr))
      semantic[name] = fname
      diffs.append(("semantic_" + name,
                    float(np.abs(img - neutral_img).mean())))
      _log("  semantic %-16s" % name)

  # Groups whose geometry is hidden inside the head in a front view get a
  # ZOOMED render of just their mesh components (tongue alone; eyes for the
  # pupil modes) so their thumbnails actually show the change.
  # pupils: the iris/pupil discs sit BEHIND the opaque cornea surface, so
  # render the eye interiors — and darken the pupil triangles (an overlay
  # pass), because dilation is a colour boundary sliding in a coplanar disc,
  # invisible in a purely geometric render.
  ZOOM_GROUPS = {
      "tongue": {"comps": ("tongue",)},
      # One eye's interior disc, pupil triangles darkened: dilation reads
      # clearly as the dark disc growing/shrinking.
      "pupils": {"intersect": ("eye_interiors", "left_eye"),
                 "overlay": "pupils"},
      # Teeth are hidden behind the closed lips/skin in a full-head render,
      # so isolate just the upper/lower teeth+gums mesh.
      "teeth": {"comps": ("upper_teeth_and_gums", "lower_teeth_and_gums")},
  }

  def _tri_idx(*groups):
    return np.concatenate(
        [np.asarray(model.triangle_indices_for_group(g)) for g in groups])

  def _tris_of(spec_comps=None, spec_intersect=None):
    if spec_intersect:
      idx = np.asarray(model.triangle_indices_for_group(spec_intersect[0]))
      for g in spec_intersect[1:]:
        idx = np.intersect1d(
            idx, np.asarray(model.triangle_indices_for_group(g)))
    else:
      idx = _tri_idx(*spec_comps)
    return triangles[idx]

  def zoom_render(verts, z):
    img = _render.render_head(verts, z["tri"], frame=z["frame"]).astype(
        np.float32)
    if z.get("otri") is not None:
      ov = _render.render_head(verts, z["otri"], frame=z["frame"]).astype(
          np.float32)
      mask = np.abs(ov - _render.BG_COLOR[None, None, :]).sum(-1) > 12.0
      img[mask] *= 0.35  # darken the overlay region (e.g. the pupil disc)
    return img.astype(np.uint8)

  zooms = {}  # prefix -> dict(tri, otri, frame, fname, img)
  for prefix, spec in ZOOM_GROUPS.items():
    tri_sub = _tris_of(spec_comps=spec.get("comps"),
                       spec_intersect=spec.get("intersect"))
    used = np.unique(tri_sub.reshape(-1))
    z = {"tri": tri_sub,
         "otri": (triangles[_tri_idx(spec["overlay"])]
                  if spec.get("overlay") else None),
         "frame": _render.compute_frame(neutral_verts[used], size=args.size),
         "fname": "neutral_%s.png" % prefix}
    zimg = zoom_render(neutral_verts, z)
    _render.write_png(os.path.join(args.out, z["fname"]), zimg)
    n_images += 1
    z["img"] = zimg.astype(np.float32)
    zooms[prefix] = z
    _log("  zoom group %-8s -> %s (%d tris)" % (prefix, z["fname"],
                                                len(tri_sub)))

  # Identity and expression basis modes at coefficient -3 / +3.
  images = {}
  sections = []
  neutrals = {}  # group prefix -> neutral image used in its rows
  kinds = [
      ("identity", list(model.identity_names), model.identity_dim),
      ("expression", list(model.expression_names), model.expression_dim),
  ]
  for kind, names, dim in kinds:
    for prefix, start, end in core._group_ranges(names):
      zoom = zooms.get(prefix)
      group_neutral = zoom["fname"] if zoom else "neutral.png"
      ref_img = zoom["img"] if zoom else neutral_img
      neutrals[prefix] = group_neutral
      rows = []
      for k in range(start, min(start + args.modes_per_group, end + 1)):
        name = names[k]
        vec = np.zeros(dim, np.float32)
        entry = {}
        for sign, key in ((-COEFF, "min"), (COEFF, "max")):
          vec[k] = sign
          verts = core.eval_vertices(model, **{kind: vec})
          fname = "%s_%s_%s.png" % (kind, name, key)
          if zoom:
            img = zoom_render(verts, zoom)
            _render.write_png(os.path.join(args.out, fname), img)
            n_images += 1
          else:
            img = render_to(fname, verts)
          entry[key] = fname
          diffs.append((fname, float(np.abs(img - ref_img).mean())))
        images[name] = entry
        rows.append((name, entry["min"], entry["max"]))
      sections.append(("%s / %s (%d modes)" % (kind, prefix, end - start + 1),
                       rows, group_neutral))
      _log("  %s %-20s %d modes" % (kind, prefix, len(rows)))

  _write_index_html(os.path.join(args.out, "index.html"), args.size,
                    sections, sorted(semantic.items()))

  manifest = {"size": args.size, "images": images, "semantic": semantic,
              "neutrals": neutrals}
  with open(os.path.join(args.out, "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)

  # ---- report ----
  elapsed = time.time() - t_start
  total_bytes = sum(
      os.path.getsize(os.path.join(args.out, f))
      for f in os.listdir(args.out) if f.endswith(".png"))
  _log("Rendered %d images in %.1fs (%.3fs/image), %.2f MB"
       % (n_images, elapsed, elapsed / max(n_images, 1),
          total_bytes / 1e6))
  threshold = 0.5
  near_identical = [(n, d) for n, d in diffs if d <= threshold]
  _log("Diff vs neutral: %d/%d images above %.2f mean-abs-pixel threshold"
       % (len(diffs) - len(near_identical), len(diffs), threshold))
  for n, d in sorted(near_identical, key=lambda t: t[1]):
    _log("  near-identical: %-45s meanabsdiff=%.3f" % (n, d))
  print("OK %s" % args.out)
  return 0


if __name__ == "__main__":
  sys.exit(main())
