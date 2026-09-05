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
  if on:
    try:  # vertex colours replace the shading colour (not just ambient)
      mc.setAttr(shape + ".displayColorChannel", "Diffuse", type="string")
    except Exception:
      pass
  try:
    mc.polyOptions(transform, colorShadedDisplay=bool(on))
  except Exception:
    pass


# --- display material -----------------------------------------------------------

PAINT_SG = "gnm_paintDisplaySG"


def _paint_display(head, on):
  """While painting/previewing, shade the WHOLE head with a plain white
  lambert so the vertex colours are what you see.

  Viewport 2.0 only displays vertex colours through lambert/blinn/standard
  surface shaders — Arnold's aiStandardSurface (used for the head whenever
  mtoa is loaded) ignores them, which made painting look like a no-op. The
  per-part materials are restored afterwards from the topology."""
  from gnm_maya.scene import build as _build
  if on:
    if not mc.objExists(PAINT_SG):
      sh = mc.shadingNode("lambert", asShader=True, name="gnm_paintDisplay_mat")
      mc.setAttr(sh + ".color", 1.0, 1.0, 1.0, type="double3")
      mc.setAttr(sh + ".diffuse", 1.0)
      sg = mc.sets(renderable=True, noSurfaceShader=True, empty=True,
                   name=PAINT_SG)
      mc.connectAttr(sh + ".outColor", sg + ".surfaceShader", force=True)
    mc.sets(head.transform, edit=True, forceElement=PAINT_SG)
  else:
    _build._assign_materials(head.transform, head.topology)


# --- painting workflow ----------------------------------------------------------

PAINT_CTX = "artAttrColorPerVertexContext"   # Maya's Paint Vertex Color ctx


def _arm_brush(white=True):
  """Point the Paint Vertex Color tool at a white (or black) Replace brush.

  The tool remembers whatever colour was used last (often black), and
  painting black onto a black map changes nothing — so always set it."""
  rgb = (1.0, 1.0, 1.0) if white else (0.0, 0.0, 0.0)
  for ctx in (PAINT_CTX, mc.currentCtx()):
    try:
      if not mc.artAttrPaintVertexCtx(ctx, query=True, exists=True):
        continue
      mc.artAttrPaintVertexCtx(ctx, edit=True, colorRGBValue=rgb,
                               value=1.0 if white else 0.0,
                               selectedattroper="absolute",  # Replace
                               paintVertexFace=False)        # per vertex
      break
    except Exception:
      continue
  try:  # refresh the Tool Settings window so the swatch reflects it
    mel.eval("artAttrColorPerVertexValues %s;" % PAINT_CTX)
  except Exception:
    pass


def start_paint(head, name, white=True):
  """Put the map's colour set on the head (from file if it exists, else black)
  and open Maya's Paint Vertex Color tool with a white Replace brush."""
  cset = PREFIX + name
  n = _fn(head.transform).numVertices
  weights = load_map(name) if os.path.isfile(map_path(name)) else [0.0] * n
  write_colorset(head.transform, cset, weights)
  # The paint tool reads the CURRENT colour set through the DG, so set it
  # with the command too (the API call alone isn't always picked up).
  try:
    mc.polyColorSet(head.transform, currentColorSet=True, colorSet=cset)
  except Exception:
    pass
  show_colors(head.transform, True)
  _paint_display(head, True)
  mc.select(head.transform, replace=True)
  mel.eval("PaintVertexColorTool;")
  _arm_brush(white)
  try:  # show the Tool Settings so the white swatch / Replace mode are visible
    mc.toolPropertyWindow()
  except Exception:
    pass
  logger.info("Painting zone map '%s' on %s (colour set %s)", name,
              head.transform, cset)


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
  _paint_display(head, False)


# --- preview ("spotlight") -----------------------------------------------------

def preview(head, weights):
  write_colorset(head.transform, PREVIEW_SET, weights)
  try:
    mc.polyColorSet(head.transform, currentColorSet=True, colorSet=PREVIEW_SET)
  except Exception:
    pass
  show_colors(head.transform, True)
  _paint_display(head, True)


def clear_preview(head):
  show_colors(head.transform, False)
  _paint_display(head, False)
  try:
    fn = _fn(head.transform)
    if PREVIEW_SET in fn.getColorSetNames():
      fn.deleteColorSet(PREVIEW_SET)
  except Exception:
    pass
