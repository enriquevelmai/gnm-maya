"""User-painted zone maps + viewport mask preview (Maya side).

A zone map is a per-vertex weight in [0, 1] over the GNM head (V = 17821).
Artists paint it in Maya as a **vertex colour set** (``gnmZone_<name>``) with
the standard Paint Vertex Color tool — white = 1, black = 0 — and this module
bakes that colour set to a float32 file the solver reads
(``<zones_dir>/<name>.f32``). The same colour-set path doubles as the
"spotlight" preview of any zone (built-in or painted).

Maps persist per user (settings.zones_dir), independent of the scene, and are
re-applied to a head as a colour set whenever you edit them.
"""

from __future__ import annotations

import logging
import os

from maya import cmds as mc
import maya.mel as mel
import maya.api.OpenMaya as om2

from gnm_maya.core import settings

logger = logging.getLogger(__name__)

PREFIX = "gnmZone_"          # colour set name prefix for painted maps
PREVIEW_SET = "gnmMaskPreview"


# --- files -------------------------------------------------------------------

def map_path(name):
  return os.path.join(settings.zones_dir(), "%s.f32" % name)


def list_maps():
  """Names of all saved painted maps (sorted)."""
  d = settings.zones_dir()
  try:
    return sorted(f[:-4] for f in os.listdir(d) if f.endswith(".f32"))
  except OSError:
    return []


def load_map(name):
  import array
  a = array.array("f")
  with open(map_path(name), "rb") as f:
    a.frombytes(f.read())
  return list(a)


def save_map(name, weights):
  import array
  a = array.array("f", [max(0.0, min(1.0, float(w))) for w in weights])
  with open(map_path(name), "wb") as f:
    f.write(a.tobytes())
  return map_path(name)


def delete_map(name):
  p = map_path(name)
  if os.path.isfile(p):
    os.remove(p)


# --- mesh colour sets -----------------------------------------------------------

def _fn(transform):
  sel = om2.MSelectionList()
  sel.add(transform)
  return om2.MFnMesh(sel.getDagPath(0))


def _shape(transform):
  shapes = mc.listRelatives(transform, shapes=True, type="mesh",
                            fullPath=True) or []
  if not shapes:
    raise RuntimeError("No mesh under %s" % transform)
  return shapes[0]


def write_colorset(transform, cset, weights):
  """Store ``weights`` as a grey vertex colour set and make it current."""
  fn = _fn(transform)
  n = fn.numVertices
  if len(weights) != n:
    raise ValueError("map has %d values, mesh has %d vertices"
                     % (len(weights), n))
  if cset not in fn.getColorSetNames():
    fn.createColorSet(cset, False)
  colors = om2.MColorArray([om2.MColor((float(w), float(w), float(w), 1.0))
                            for w in weights])
  fn.setCurrentColorSetName(cset)
  fn.setVertexColors(colors, list(range(n)))


def read_colorset(transform, cset):
  """Per-vertex weight = red channel of the colour set (unset -> 0)."""
  fn = _fn(transform)
  if cset not in fn.getColorSetNames():
    raise RuntimeError("Colour set %s not on %s" % (cset, transform))
  colors = fn.getVertexColors(cset, om2.MColor((0.0, 0.0, 0.0, 1.0)))
  return [max(0.0, min(1.0, float(c.r))) for c in colors]


def has_colorset(transform, cset):
  try:
    return cset in _fn(transform).getColorSetNames()
  except Exception:
    return False


def show_colors(transform, on):
  shape = _shape(transform)
  mc.setAttr(shape + ".displayColors", 1 if on else 0)
  try:
    mc.polyOptions(transform, colorShadedDisplay=bool(on))
  except Exception:
    pass


# --- painting workflow ----------------------------------------------------------

def start_paint(head, name):
  """Put the map's colour set on the head (from file if it exists, else black)
  and open Maya's Paint Vertex Color tool with a white brush."""
  cset = PREFIX + name
  n = _fn(head.transform).numVertices
  weights = load_map(name) if os.path.isfile(map_path(name)) else [0.0] * n
  write_colorset(head.transform, cset, weights)
  show_colors(head.transform, True)
  mc.select(head.transform, replace=True)
  mel.eval("PaintVertexColorTool;")
  try:  # white brush = weight 1 (the artist can lower it in the tool)
    ctx = mc.currentCtx()
    mc.artAttrPaintVertexCtx(ctx, edit=True, colorRGBValue=(1.0, 1.0, 1.0))
  except Exception:
    pass
  logger.info("Painting zone map '%s' on %s", name, head.transform)


def save_paint(head, name):
  """Bake the map's colour set on this head to its file. Returns the path."""
  weights = read_colorset(head.transform, PREFIX + name)
  path = save_map(name, weights)
  logger.info("Saved zone map '%s' (%d verts, %.0f%% painted)", name,
              len(weights), 100.0 * sum(1 for w in weights if w > 0.05)
              / max(1, len(weights)))
  return path


def sync_from_mesh(head, names):
  """Save every listed map whose colour set exists on this head (so the
  latest brush strokes are what the solver uses)."""
  saved = []
  for name in names:
    if has_colorset(head.transform, PREFIX + name):
      save_paint(head, name)
      saved.append(name)
  return saved


def stop_paint(head):
  """Leave the paint tool and hide vertex colours again."""
  try:
    mc.setToolTo("selectSuperContext")
  except Exception:
    pass
  show_colors(head.transform, False)


# --- preview ("spotlight") -----------------------------------------------------

def preview(head, weights):
  write_colorset(head.transform, PREVIEW_SET, weights)
  show_colors(head.transform, True)


def clear_preview(head):
  show_colors(head.transform, False)
  try:
    fn = _fn(head.transform)
    if PREVIEW_SET in fn.getColorSetNames():
      fn.deleteColorSet(PREVIEW_SET)
  except Exception:
    pass
