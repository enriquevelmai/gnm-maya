"""High-level GNM head controller used by the UI and user scripts.

Holds the full coefficient state in plain Python (no numpy in mayapy), drives
the resident worker, and pushes results into one Maya mesh.
"""

from __future__ import annotations

import json
import logging
import random

from maya import cmds as mc

from gnm_maya import worker as _worker
from gnm_maya import build as _build

logger = logging.getLogger(__name__)

# Dynamic string attribute where each head stores its coefficient state, so a
# panel can adopt an existing head and restore its exact sliders.
STATE_ATTR = "gnmState"


def find_heads(selected_only=False):
  """GNM head transforms in the scene (identified by the gnmState attr)."""
  nodes = mc.ls(selection=True, long=False) if selected_only \
      else mc.ls(type="transform", long=False)
  return [n for n in (nodes or [])
          if mc.attributeQuery(STATE_ATTR, node=n, exists=True)]


class GnmHead(object):
  """A live GNM head: mutate coefficients, mesh updates in place."""

  def __init__(self, name="gnm_head"):
    self._init_common()
    verts = self.worker.eval()  # neutral template
    self.transform = _build.build_mesh(self.topology, verts, name=name)
    self._save_state()
    logger.info("Built GNM head '%s'", self.transform)

  @classmethod
  def adopt(cls, transform):
    """Bind a controller to an EXISTING head, restoring its saved state."""
    self = cls.__new__(cls)
    self._init_common()
    self.transform = transform
    restored = self._load_state()
    if restored:
      self._update()  # make sure the mesh matches the restored coefficients
    logger.info("Adopted GNM head '%s' (%s)",
                transform, "state restored" if restored else "no saved state")
    return self

  def _init_common(self):
    self.worker = _worker.get_worker()
    topo = self.worker.topology
    self.topology = topo
    self.identity = [0.0] * topo.meta["identity_dim"]
    self.expression = [0.0] * topo.meta["expression_dim"]
    self.num_joints = topo.meta["num_joints"]
    self.rotations = [[0.0, 0.0, 0.0] for _ in range(self.num_joints)]
    self.translation = [0.0, 0.0, 0.0]
    # Symmetric left<->right maps (both directions) for the UI symmetry toggle.
    self.expression_mirror = self._sym_map(topo.meta.get("expression_mirror", []))
    self.joint_mirror = self._sym_map(topo.meta.get("joint_mirror", []))

  @staticmethod
  def _sym_map(pairs):
    m = {}
    for a, b in pairs:
      m[a] = b
      m[b] = a
    return m

  # --- state persistence ---------------------------------------------------

  def _save_state(self):
    if not mc.objExists(self.transform):
      return
    attr = "%s.%s" % (self.transform, STATE_ATTR)
    if not mc.attributeQuery(STATE_ATTR, node=self.transform, exists=True):
      mc.addAttr(self.transform, longName=STATE_ATTR, dataType="string")
    data = json.dumps({
        "identity": self.identity,
        "expression": self.expression,
        "rotations": self.rotations,
        "translation": self.translation,
    })
    mc.setAttr(attr, data, type="string")

  def _load_state(self):
    if not mc.attributeQuery(STATE_ATTR, node=self.transform, exists=True):
      return False
    raw = mc.getAttr("%s.%s" % (self.transform, STATE_ATTR))
    if not raw:
      return False
    try:
      d = json.loads(raw)
      self.identity = [float(x) for x in d["identity"]]
      self.expression = [float(x) for x in d["expression"]]
      self.rotations = [[float(a) for a in r] for r in d["rotations"]]
      self.translation = [float(x) for x in d["translation"]]
      return True
    except Exception:
      logger.exception("Ignoring corrupt %s on '%s'", STATE_ATTR, self.transform)
      return False

  # --- state mutation ------------------------------------------------------

  def _update(self):
    verts = self.worker.eval(
        identity=self.identity,
        expression=self.expression,
        rotations=self.rotations,
        translation=self.translation,
    )
    if not mc.objExists(self.transform):
      # The user deleted the mesh: self-heal by rebuilding it with the
      # current coefficients instead of erroring on every button press.
      old = self.transform
      self.transform = _build.build_mesh(self.topology, verts,
                                         name=old.split("|")[-1])
      logger.info("Mesh '%s' was deleted — recreated as '%s'", old,
                  self.transform)
    else:
      _build.set_points(self.transform, verts)
    self._save_state()

  def refresh(self):
    """Re-evaluate and push the mesh from the current coefficient state.

    Lets callers stage several `set_*(..., update=False)` edits and repaint
    once (the UI uses this to throttle mesh updates during slider drags)."""
    self._update()

  def set_identity(self, i, value, update=True):
    self.identity[i] = float(value)
    if update:
      self._update()

  def set_expression(self, i, value, symmetry=False, update=True):
    """Set expression coeff ``i``; if ``symmetry`` also set its L/R mirror.

    Returns the list of coeff indices that changed (for UI slider sync).
    """
    changed = [i]
    self.expression[i] = float(value)
    if symmetry and i in self.expression_mirror:
      j = self.expression_mirror[i]
      self.expression[j] = float(value)
      changed.append(j)
    if update:
      self._update()
    return changed

  def set_rotation(self, joint, axis, value, symmetry=False, update=True):
    """Set one joint rotation axis; if ``symmetry`` mirror to the paired joint.

    Returns the list of (joint, axis) pairs that changed.
    """
    changed = [(joint, axis)]
    self.rotations[joint][axis] = float(value)
    if symmetry and joint in self.joint_mirror:
      mj = self.joint_mirror[joint]
      self.rotations[mj][axis] = float(value)
      changed.append((mj, axis))
    if update:
      self._update()
    return changed

  def set_translation(self, axis, value, update=True):
    self.translation[axis] = float(value)
    if update:
      self._update()

  # --- bulk ops ------------------------------------------------------------

  def randomize_identity(self, scale=1.0, seed=None):
    rng = random.Random(seed)
    self.identity = [rng.gauss(0.0, 1.0) * scale for _ in self.identity]
    self._update()
    logger.info("Randomized identity (scale=%.2f) on '%s'", scale, self.transform)

  def randomize_expression(self, scale=1.0, seed=None, symmetric=False):
    rng = random.Random(seed)
    vals = [rng.gauss(0.0, 1.0) * scale for _ in self.expression]
    if symmetric:
      # Copy each left value onto its right mirror so the face stays symmetric.
      for a, b in self.expression_mirror.items():
        if a < b:
          vals[b] = vals[a]
    self.expression = vals
    self._update()
    logger.info("Randomized expression (scale=%.2f, symmetric=%s) on '%s'",
                scale, symmetric, self.transform)

  def randomize_pose(self, scale=0.3, seed=None, symmetric=False):
    rng = random.Random(seed)
    rot = [[rng.gauss(0.0, 1.0) * scale for _ in range(3)]
           for _ in range(self.num_joints)]
    if symmetric:
      for a, b in self.joint_mirror.items():
        if a < b:
          rot[b] = list(rot[a])
    self.rotations = rot
    self._update()
    logger.info("Randomized pose (scale=%.2f, symmetric=%s) on '%s'",
                scale, symmetric, self.transform)

  def reset_mesh_to_template(self, transform):
    """Reset ANY GNM-topology mesh in the scene to the neutral template.

    Geometry-only (coefficients unknown for foreign meshes); used so the panel's
    Reset can act on whichever GNM head the user has selected, not just its own.
    """
    verts = self.worker.eval()
    _build.set_points(transform, verts)
    logger.info("Reset mesh '%s' to template", transform)

  def is_gnm_head(self, transform):
    """True if ``transform`` is a mesh with GNM's vertex count."""
    try:
      return mc.polyEvaluate(transform, vertex=True) == \
          self.topology.num_vertices
    except Exception:
      return False

  def semantic_identity(self, gender, ethnicity, seed=None):
    """Set identity from the semantic sampler (gender + ethnicity classes)."""
    vec = self.worker.sample_identity(gender, ethnicity, seed)
    self.identity = [float(x) for x in vec]
    self._update()
    logger.info("Semantic identity g=%s e=%s on '%s'", gender, ethnicity,
                self.transform)

  def semantic_expression(self, class_index, seed=None):
    """Set expression from the semantic sampler (named expression class)."""
    vec = self.worker.sample_expression(class_index, seed)
    self.expression = [float(x) for x in vec]
    self._update()
    logger.info("Semantic expression cls=%s on '%s'", class_index,
                self.transform)

  def describe(self, text, seed=None, prefer_ollama=True):
    """Apply a natural-language description ('a very happy asian woman').

    Uses a local lexicon (always available) or a local Ollama LLM if one is
    running. Returns the parsed interpretation dict for UI feedback.
    """
    out = self.worker.text2face(text, seed=seed, prefer_ollama=prefer_ollama)
    if out.get("expression"):
      self.expression = [float(x) for x in out["expression"]]
    if out.get("identity"):
      self.identity = [float(x) for x in out["identity"]]
    self._update()
    logger.info("Described '%s' -> %s (source=%s)", text,
                out.get("parsed", {}).get("expression_weights"),
                out.get("parsed", {}).get("source"))
    return out.get("parsed", {})

  def fit_photo(self, image_path, lam=2.0):
    """Fit this head's identity to a face photo (MediaPipe + least squares)."""
    vec = self.worker.fit_photo(image_path=image_path, lam=lam)
    self.identity = [float(x) for x in vec]
    self._update()
    logger.info("Fitted identity from photo '%s' on '%s'", image_path,
                self.transform)

  def blend_expression(self, weights, seed=None):
    """Set expression from a weighted blend of expression classes."""
    vec = self.worker.blend_expression(weights, seed)
    self.expression = [float(x) for x in vec]
    self._update()

  def blend_identity(self, gender_weights, ethnicity_weights, seed=None):
    """Set identity from a weighted blend of gender/ethnicity classes."""
    vec = self.worker.blend_identity(gender_weights, ethnicity_weights, seed)
    self.identity = [float(x) for x in vec]
    self._update()

  def clear(self, kind, indices):
    """Zero a set of coefficient indices ('identity' or 'expression')."""
    target = self.identity if kind == "identity" else self.expression
    for i in indices:
      target[i] = 0.0
    self._update()

  def reset_identity(self):
    self.identity = [0.0] * len(self.identity); self._update()

  def reset_expression(self):
    self.expression = [0.0] * len(self.expression); self._update()

  def reset_pose(self):
    self.rotations = [[0.0, 0.0, 0.0] for _ in range(self.num_joints)]
    self._update()

  def reset_translation(self):
    self.translation = [0.0, 0.0, 0.0]
    self._update()

  def reset_all(self):
    self.reset_identity(); self.reset_expression()
    self.reset_pose(); self.reset_translation()
    logger.info("Reset all coefficients on '%s'", self.transform)


# --- convenience one-shots (no persistent controller) ----------------------

def generate_head(seed=None, identity_scale=1.0, name="gnm_head"):
  """Build a random head and return its GnmHead controller.

  ``seed=None`` (the default, used by the menu) seeds from system entropy, so
  each call yields a different face. Pass an int for a reproducible shape.
  """
  h = GnmHead(name=name)
  h.randomize_identity(scale=identity_scale, seed=seed)
  return h


def generate_template(name="gnm_template"):
  """Build the neutral template head; returns its GnmHead controller."""
  return GnmHead(name=name)
