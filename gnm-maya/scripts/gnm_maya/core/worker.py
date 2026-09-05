"""Manage the long-lived GNM server subprocess and talk to it.

One resident worker (singleton) keeps the 51 MB model loaded so slider drags
resolve in ~15 ms. Communication is one JSON line per request over stdin/stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading

from gnm_maya.core import config
from gnm_maya.core import meshio

_WORKER = None
_LOCK = threading.Lock()


class GnmWorker(object):

  def __init__(self):
    config.check_install()
    self.session_dir = tempfile.mkdtemp(prefix="gnm_session_")
    self._verts_path = os.path.join(self.session_dir, "vertices.bin")
    self._seq = 0

    server = os.path.join(config.EXTERNAL_DIR, "server.py")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    self.proc = subprocess.Popen(
        [config.venv_python(), server, "--session", self.session_dir],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1,
        creationflags=creationflags,
    )
    line = self.proc.stdout.readline().strip()
    if not line.startswith("READY"):
      err = self.proc.stderr.read() if self.proc.stderr else ""
      raise RuntimeError("GNM server failed to start:\n%s\n%s" % (line, err))

    self.topology = meshio.read_topology(self.session_dir)

  def alive(self):
    return self.proc.poll() is None

  def eval(self, identity=None, expression=None, rotations=None, translation=None):
    """Send params, block for the result, return a flat array('f') of verts."""
    if not self.alive():
      raise RuntimeError("GNM server has exited.")
    self._seq += 1
    # Round-robin two files so a pending read never races the next write.
    out = os.path.join(self.session_dir, "verts_%d.bin" % (self._seq % 2))
    req = {"out": out}
    if identity is not None:
      req["identity"] = list(identity)
    if expression is not None:
      req["expression"] = list(expression)
    if rotations is not None:
      req["rotations"] = [list(r) for r in rotations]
    if translation is not None:
      req["translation"] = list(translation)

    self.proc.stdin.write(json.dumps(req) + "\n")
    self.proc.stdin.flush()
    resp = self.proc.stdout.readline().strip()
    if not resp.startswith("OK"):
      raise RuntimeError("GNM eval failed: %s" % resp)
    return meshio.read_vertices(out)

  def _sample(self, req):
    if not self.alive():
      raise RuntimeError("GNM server has exited.")
    self.proc.stdin.write(json.dumps(req) + "\n")
    self.proc.stdin.flush()
    resp = self.proc.stdout.readline().strip()
    if not resp.startswith("COEFF "):
      raise RuntimeError("GNM semantic sample failed: %s" % resp)
    return json.loads(resp[len("COEFF "):])

  def sample_identity(self, gender, ethnicity, seed=None):
    req = {"cmd": "sample_identity", "gender": int(gender),
           "ethnicity": int(ethnicity)}
    if seed is not None:
      req["seed"] = int(seed)
    return self._sample(req)

  def sample_expression(self, class_index, seed=None):
    req = {"cmd": "sample_expression", "class": int(class_index)}
    if seed is not None:
      req["seed"] = int(seed)
    return self._sample(req)

  @staticmethod
  def _pairs(weights):
    """{idx: w} or [[idx, w], ...] -> [[idx, w], ...] (duplicates preserved,
    so blending a class with itself still morphs via two latents)."""
    items = weights.items() if hasattr(weights, "items") else weights
    return [[int(k), float(v)] for k, v in items]

  def blend_expression(self, weights, seed=None):
    req = {"cmd": "blend_expression", "weights": self._pairs(weights)}
    if seed is not None:
      req["seed"] = int(seed)
    return self._sample(req)

  def text2face(self, text, seed=None, prefer_ollama=True):
    """Parse a description into coefficients. Returns the T2F payload dict:
    {"expression": [383]|None, "identity": [253]|None, "parsed": {...}}."""
    if not self.alive():
      raise RuntimeError("GNM server has exited.")
    req = {"cmd": "text2face", "text": str(text),
           "prefer_ollama": bool(prefer_ollama)}
    if seed is not None:
      req["seed"] = int(seed)
    self.proc.stdin.write(json.dumps(req) + "\n")
    self.proc.stdin.flush()
    resp = self.proc.stdout.readline().strip()
    if not resp.startswith("T2F "):
      raise RuntimeError("text2face failed: %s" % resp)
    return json.loads(resp[len("T2F "):])

  def zone_randomize(self, kind, zones, identity=None, expression=None,
                     scale=1.0, seed=None, maps=None):
    """Zone-masked randomize (nose/mouth/jaw/...): returns a full coefficient
    vector for ``kind`` whose effect is confined to ``zones`` and painted
    ``maps`` (float32 [V] files). scale=0 resets the zones toward neutral."""
    req = {"cmd": "zone_randomize", "kind": str(kind),
           "zones": [str(z) for z in zones], "scale": float(scale)}
    if maps:
      req["maps"] = [str(p) for p in maps]
    if identity is not None:
      req["identity"] = [float(x) for x in identity]
    if expression is not None:
      req["expression"] = [float(x) for x in expression]
    if seed is not None:
      req["seed"] = int(seed)
    return self._sample(req)

  def mask_texture(self, out, zones, maps=None, size=1024):
    """Write a UV-space PNG of the zones/maps mask; returns the path."""
    if not self.alive():
      raise RuntimeError("GNM server has exited.")
    req = {"cmd": "mask_texture", "out": str(out), "size": int(size),
           "zones": [str(z) for z in zones]}
    if maps:
      req["maps"] = [str(p) for p in maps]
    self.proc.stdin.write(json.dumps(req) + "\n")
    self.proc.stdin.flush()
    resp = self.proc.stdout.readline().strip()
    if not resp.startswith("OK"):
      raise RuntimeError("GNM mask texture failed: %s" % resp)
    return out

  def zone_weights(self, zones, maps=None):
    """Per-vertex [V] weights of the given zones/maps (for viewport preview)."""
    req = {"cmd": "zone_weights", "zones": [str(z) for z in zones]}
    if maps:
      req["maps"] = [str(p) for p in maps]
    return self._sample(req)

  def render(self, out, identity=None, expression=None, size=128):
    """Render a head thumbnail PNG (orthographic, gallery framing) to ``out``.

    Returns the written path. Used by the Variants contact sheet."""
    if not self.alive():
      raise RuntimeError("GNM server has exited.")
    req = {"cmd": "render", "out": str(out), "size": int(size)}
    if identity is not None:
      req["identity"] = [float(x) for x in identity]
    if expression is not None:
      req["expression"] = [float(x) for x in expression]
    self.proc.stdin.write(json.dumps(req) + "\n")
    self.proc.stdin.flush()
    resp = self.proc.stdout.readline().strip()
    if not resp.startswith("OK"):
      raise RuntimeError("GNM render failed: %s" % resp)
    return out

  def fit_landmarks3d(self, targets, expression=None, lam=1.0, prior=None):
    """Fit identity to 68 3D landmark positions (GNM order, object space).
    ``prior`` = current identity: the solve keeps that face where the
    locators don't say otherwise."""
    req = {"cmd": "fit_landmarks3d", "lam": float(lam),
           "targets": [[float(a) for a in p] for p in targets]}
    if expression is not None:
      req["expression"] = [float(x) for x in expression]
    if prior is not None:
      req["prior"] = [float(x) for x in prior]
    return self._sample(req)

  def fit_photo(self, image_path=None, landmarks=None, order="ibug", lam=2.0):
    """Fit identity coefficients to a photo (or 68 pre-detected landmarks)."""
    req = {"cmd": "fit", "lam": float(lam)}
    if image_path:
      req["image"] = str(image_path)
    else:
      req["landmarks"] = [[float(a), float(b)] for a, b in landmarks]
      req["order"] = order
    return self._sample(req)

  def bake(self, identity=None, num_modes=0, semantic=True, seed=0,
           arkit=False, visemes=False):
    """Export rig data (targets/weights/joints) into the session dir.

    ``arkit`` renames/splits the semantic targets to ARKit-52 blendshape
    names (Live Link Face conventions); ``visemes`` adds the mouth-shape
    targets used by audio lip-sync. Returns the parsed rig.json metadata.
    """
    if not self.alive():
      raise RuntimeError("GNM server has exited.")
    req = {"cmd": "bake", "num_modes": int(num_modes),
           "semantic": bool(semantic), "seed": int(seed),
           "arkit": bool(arkit), "visemes": bool(visemes)}
    if identity is not None:
      req["identity"] = [float(x) for x in identity]
    self.proc.stdin.write(json.dumps(req) + "\n")
    self.proc.stdin.flush()
    resp = self.proc.stdout.readline().strip()
    if not resp.startswith("BAKED"):
      raise RuntimeError("GNM bake failed: %s" % resp)
    with open(os.path.join(self.session_dir, "rig.json")) as f:
      return json.load(f)

  def blend_identity(self, gender_weights, ethnicity_weights, seed=None):
    req = {"cmd": "blend_identity",
           "gender_weights": self._pairs(gender_weights),
           "ethnicity_weights": self._pairs(ethnicity_weights)}
    if seed is not None:
      req["seed"] = int(seed)
    return self._sample(req)

  def shutdown(self):
    try:
      if self.alive():
        self.proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
        self.proc.stdin.flush()
        self.proc.wait(timeout=5)
    except Exception:
      try:
        self.proc.kill()
      except Exception:
        pass


def get_worker():
  """Return the shared worker, (re)starting it if needed."""
  global _WORKER
  with _LOCK:
    if _WORKER is None or not _WORKER.alive():
      _WORKER = GnmWorker()
    return _WORKER


def shutdown_worker():
  global _WORKER
  with _LOCK:
    if _WORKER is not None:
      _WORKER.shutdown()
      _WORKER = None
