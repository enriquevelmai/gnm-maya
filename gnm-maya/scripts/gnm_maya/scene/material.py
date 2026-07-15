"""Per-component materials for the GNM head.

Gives each anatomical part its own shader (skin/eyes/teeth/tongue) so the mesh
reads correctly instead of flat default gray. Prefers aiStandardSurface when
Arnold is loaded, else falls back to lambert (always available).
"""

from __future__ import annotations

import os

from maya import cmds as mc

from gnm_maya.core import config

# name -> (linear-ish RGB, is_glossy)
_PALETTE = {
    "skin": ((0.82, 0.62, 0.52), False),
    "left_eye": ((0.90, 0.90, 0.92), True),
    "right_eye": ((0.90, 0.90, 0.92), True),
    "upper_teeth_and_gums": ((0.92, 0.88, 0.82), False),
    "lower_teeth_and_gums": ((0.92, 0.88, 0.82), False),
    "tongue": ((0.80, 0.42, 0.45), False),
}
_DEFAULT = ((0.72, 0.74, 0.78), False)


def _arnold_available():
  try:
    return mc.pluginInfo("mtoa", query=True, loaded=True)
  except Exception:
    return False


def get_or_create(component):
  """Return a shading group name for ``component`` (created once, reused)."""
  color, glossy = _PALETTE.get(component, _DEFAULT)
  shader_name = "gnm_%s_mat" % component
  sg_name = shader_name + "SG"
  if mc.objExists(sg_name):
    return sg_name

  if _arnold_available():
    shader = mc.shadingNode("aiStandardSurface", asShader=True, name=shader_name)
    mc.setAttr(shader + ".baseColor", *color, type="double3")
    mc.setAttr(shader + ".specular", 0.4 if glossy else 0.1)
    mc.setAttr(shader + ".specularRoughness", 0.15 if glossy else 0.55)
  else:
    shader = mc.shadingNode("lambert", asShader=True, name=shader_name)
    mc.setAttr(shader + ".color", *color, type="double3")

  sg = mc.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg_name)
  mc.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
  return sg


# --- texturing -------------------------------------------------------------

_TEX_FILE = "gnm_texture_file"
_TEX_P2D = "gnm_texture_place2d"
_TEX_TINT = "gnm_texture_tint"       # multiplyDivide: texture * tint colour
_DEFAULT_TINT = _PALETTE["skin"][0]  # skin tone so the B/W map isn't white

# Standard place2dTexture -> file connections so UV tiling/wrap behave normally.
_P2D_LINKS = [
    ("coverage", "coverage"), ("translateFrame", "translateFrame"),
    ("rotateFrame", "rotateFrame"), ("mirrorU", "mirrorU"),
    ("mirrorV", "mirrorV"), ("stagger", "stagger"), ("wrapU", "wrapU"),
    ("wrapV", "wrapV"), ("repeatUV", "repeatUV"), ("offset", "offset"),
    ("rotateUV", "rotateUV"), ("noiseUV", "noiseUV"),
    ("vertexUvOne", "vertexUvOne"), ("vertexUvTwo", "vertexUvTwo"),
    ("vertexUvThree", "vertexUvThree"), ("vertexCameraOne", "vertexCameraOne"),
    ("outUV", "uvCoord"), ("outUvFilterSize", "uvFilterSize"),
]


def bundled_texture_path():
  """The edgeflow PNG that ships in the vendored GNM repo, if present."""
  p = os.path.join(config.EXTERNAL_DIR, "gnm_repo", "gnm", "shape", "data",
                   "textures", "edgeflow_bw_4k.png")
  return p if os.path.isfile(p) else None


def _color_attr(shader):
  return "baseColor" if mc.nodeType(shader) == "aiStandardSurface" else "color"


def _shaders_of(transform):
  shapes = mc.listRelatives(transform, shapes=True, type="mesh",
                              fullPath=True) or []
  shaders = []
  for shape in shapes:
    for sg in set(mc.listConnections(shape, type="shadingEngine") or []):
      shaders += mc.listConnections(sg + ".surfaceShader") or []
  return list(dict.fromkeys(shaders))  # de-dup, keep order


def _skin_shaders(transform):
  """Just the head-surface (skin) shader(s) — not eyes/teeth/tongue."""
  return [s for s in _shaders_of(transform) if "skin" in s]


def _ensure_file_node(image_path):
  if not mc.objExists(_TEX_FILE):
    f = mc.shadingNode("file", asTexture=True, isColorManaged=True,
                         name=_TEX_FILE)
    p2d = mc.shadingNode("place2dTexture", asUtility=True, name=_TEX_P2D)
    for src, dst in _P2D_LINKS:
      mc.connectAttr(p2d + "." + src, f + "." + dst, force=True)
  mc.setAttr(_TEX_FILE + ".fileTextureName", image_path, type="string")
  return _TEX_FILE


def _ensure_tint_node(tint):
  """multiplyDivide that multiplies the texture by ``tint`` (so it's not B/W)."""
  if not mc.objExists(_TEX_TINT):
    md = mc.shadingNode("multiplyDivide", asUtility=True, name=_TEX_TINT)
    mc.setAttr(md + ".operation", 1)  # multiply
    mc.connectAttr(_TEX_FILE + ".outColor", md + ".input1", force=True)
  mc.setAttr(_TEX_TINT + ".input2", *tint, type="double3")
  return _TEX_TINT


def apply_texture(transform, image_path=None, tint=None):
  """Texture only the head surface (skin), multiplied by a tint colour.

  ``image_path`` defaults to the bundled edgeflow PNG; ``tint`` defaults to a
  skin tone so the black/white map doesn't read as plain white.

  Note: the skin shader is shared across GNM heads, so this textures every
  head's skin in the scene, not just this one.
  """
  image_path = image_path or bundled_texture_path()
  if not image_path or not os.path.isfile(image_path):
    raise RuntimeError("Texture image not found: %s" % image_path)
  tint = tint or _DEFAULT_TINT
  _ensure_file_node(image_path)
  md = _ensure_tint_node(tint)
  for sh in _skin_shaders(transform):
    mc.connectAttr(md + ".output", sh + "." + _color_attr(sh), force=True)
  return md


def set_viewport_textured(enabled):
  """Turn textured display on/off in every model panel (so the map shows)."""
  for panel in (mc.getPanel(type="modelPanel") or []):
    try:
      mc.modelEditor(panel, edit=True, displayTextures=bool(enabled))
      if enabled:
        mc.modelEditor(panel, edit=True, displayAppearance="smoothShaded")
    except Exception:
      pass


def remove_texture(transform):
  """Disconnect the texture so the skin reverts to its flat colour."""
  skin_color = _PALETTE["skin"][0]
  for sh in _skin_shaders(transform):
    attr = sh + "." + _color_attr(sh)
    for src in (mc.listConnections(attr, plugs=True, source=True,
                                     destination=False) or []):
      if src.startswith(_TEX_TINT + ".") or src.startswith(_TEX_FILE + "."):
        mc.disconnectAttr(src, attr)
    # Disconnecting a colour compound leaves its RGB children at 0 (black),
    # so explicitly restore the flat skin colour.
    mc.setAttr(attr, *skin_color, type="double3")
