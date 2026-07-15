"""Pure-Python readers for the binary mesh/topology files (no numpy in mayapy).

Little-endian floats/ints, matching _gnm_core's writers.
"""

from __future__ import annotations

import array
import json
import os
import sys


def _read_array(path, typecode):
  a = array.array(typecode)
  with open(path, "rb") as f:
    a.frombytes(f.read())
  if sys.byteorder == "big":  # files are little-endian
    a.byteswap()
  return a


class Topology(object):
  """Constant mesh topology: quads, per-face-vertex UVs, part components."""

  __slots__ = ("quads", "quad_uvs", "components", "meta")

  def __init__(self, quads, quad_uvs, components, meta):
    self.quads = quads          # array('i'), flat [a,b,c,d, ...]
    self.quad_uvs = quad_uvs    # array('f'), flat [u,v, u,v, ...] per face-vertex
    self.components = components  # list of (name, array('i') quad indices)
    self.meta = meta            # topology.json dict

  @property
  def num_quads(self):
    return len(self.quads) // 4

  @property
  def num_vertices(self):
    return self.meta["num_vertices"]


def read_topology(session_dir):
  with open(os.path.join(session_dir, "topology.json")) as f:
    meta = json.load(f)
  quads = _read_array(os.path.join(session_dir, "quads.bin"), "i")
  quad_uvs = _read_array(os.path.join(session_dir, "quad_uvs.bin"), "f")

  components = []
  for c in meta.get("components", []):
    idx = _read_array(os.path.join(session_dir, c["file"]), "i")
    components.append((c["name"], idx))

  expected_q = meta["num_quads"] * 4
  if len(quads) != expected_q:
    raise ValueError("quads.bin truncated: %d != %d" % (len(quads), expected_q))
  return Topology(quads, quad_uvs, components, meta)


def read_vertices(path):
  """Flat array('f') of [x,y,z, ...]."""
  return _read_array(path, "f")
