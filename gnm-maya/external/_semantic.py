"""Lightweight numpy re-implementation of GNM's semantic sampler.

GNM ships Keras `.h5` conditional-VAE decoders that map (latent z + one-hot
class label) -> coefficient vector. Rather than depend on TensorFlow (~1 GB) to
run them, we read the decoder weights with h5py and evaluate the small MLP
forward pass in numpy: concat(z, onehot) -> Dense*4 (relu) -> Dense (linear).

Runs in the module runtime (numpy + h5py), never in mayapy.
"""

from __future__ import annotations

import os
import re

import numpy as np

# Class labels — mirror the enums in gnm/shape/semantic_sampler.py (which we do
# not import because it pulls in TensorFlow).
GENDER = ["female", "male"]
ETHNICITY = ["middle_eastern", "asian", "white", "black"]
EXPRESSION = [
    "surprise", "disgust", "suck", "compress_face", "stretch_face", "happy",
    "squint", "platysma", "blow", "funneler", "smile_wide", "corners_down",
    "pucker", "wink_left", "wink_right", "mouth_left", "mouth_right",
    "lips_roll_in", "snarl", "tongue_center",
]

LATENT_DIM = 64


def decoder_path(repo_dir, which):
  return os.path.join(repo_dir, "gnm", "shape", "data", "semantic_sampler",
                      "%s_decoder_model.h5" % which)


def available(repo_dir):
  """True if h5py is importable and both decoder files are present."""
  try:
    import h5py  # noqa: F401
  except Exception:
    return False
  return all(os.path.isfile(decoder_path(repo_dir, w))
             for w in ("identity", "expression"))


def _load_layers(path):
  """Return the Dense layers as (kernel, bias) in forward order."""
  import h5py
  f = h5py.File(path, "r")
  mw = f["model_weights"]
  layers = []
  for name in mw:
    kernel = bias = None

    def _visit(n, obj):
      nonlocal kernel, bias
      if hasattr(obj, "shape"):
        if n.endswith("kernel:0"):
          kernel = obj[()]
        elif n.endswith("bias:0"):
          bias = obj[()]

    mw[name].visititems(_visit)
    if kernel is not None:
      order = int(re.findall(r"\d+", name)[-1])  # Keras dense_<n> ordering
      layers.append((order, np.asarray(kernel, "float32"),
                     np.asarray(bias, "float32")))
  layers.sort(key=lambda t: t[0])
  return [(k, b) for _, k, b in layers]


class _Decoder(object):

  def __init__(self, path):
    self.layers = _load_layers(path)
    self.in_dim = self.layers[0][0].shape[0]
    self.out_dim = self.layers[-1][0].shape[1]

  def __call__(self, z, onehot):
    x = np.concatenate([z, onehot], axis=-1).astype("float32")
    n = len(self.layers)
    for i, (kernel, bias) in enumerate(self.layers):
      x = x @ kernel + bias
      if i < n - 1:
        x = np.maximum(x, 0.0)  # relu
    return x


class Sampler(object):
  """Loads both decoders and samples coefficient vectors from semantic labels."""

  def __init__(self, repo_dir):
    self._expr = _Decoder(decoder_path(repo_dir, "expression"))
    self._iden = _Decoder(decoder_path(repo_dir, "identity"))

  def sample_expression(self, class_index, seed=None):
    oh = np.zeros((1, len(EXPRESSION)), "float32")
    oh[0, int(class_index)] = 1.0
    z = np.random.default_rng(seed).normal(size=(1, LATENT_DIM)).astype("float32")
    return self._expr(z, oh)[0]

  def sample_identity(self, gender, ethnicity, seed=None):
    oh = np.zeros((1, len(GENDER) + len(ETHNICITY)), "float32")
    oh[0, int(gender)] = 1.0
    oh[0, len(GENDER) + int(ethnicity)] = 1.0
    z = np.random.default_rng(seed).normal(size=(1, LATENT_DIM)).astype("float32")
    return self._iden(z, oh)[0]

  @staticmethod
  def _as_pairs(weights):
    """Normalize {idx: w} or [[idx, w], ...] into a list of (idx, w) pairs.

    Pairs preserve duplicates: blending a class WITH ITSELF draws two distinct
    latents, so a mix slider still morphs (a dict would collapse to weight 1).
    """
    if hasattr(weights, "items"):
      pairs = [(int(k), float(v)) for k, v in weights.items()]
    else:
      pairs = [(int(k), float(v)) for k, v in weights]
    total = sum(w for _, w in pairs)
    if total <= 0:
      raise ValueError("weights sum to 0")
    return [(k, w / total) for k, w in pairs]

  def blend_expression(self, weights, seed=None):
    """Weighted blend of expression classes ({idx: w} or [[idx, w], ...]).

    Matches GNM: accumulate a per-entry latent AND one-hot, weighted &
    normalised, then decode. A fixed seed makes dragging a mix interpolate.
    """
    rng = np.random.default_rng(seed)
    z = np.zeros((1, LATENT_DIM), "float32")
    oh = np.zeros((1, len(EXPRESSION)), "float32")
    for idx, wn in self._as_pairs(weights):
      z += rng.normal(size=(1, LATENT_DIM)).astype("float32") * wn
      oh[0, idx] += wn
    return self._expr(z, oh)[0]

  def blend_identity(self, gender_weights, ethnicity_weights, seed=None):
    """Weighted blend of gender and ethnicity classes.

    Accepts {idx: w} or [[idx, w], ...]. The latent is blended per ethnicity
    entry (like blend_expression) so an ethnicity mix morphs even between two
    samples of the same class; gender one-hots are blended directly.
    """
    g = np.zeros((1, len(GENDER)), "float32")
    for idx, wn in self._as_pairs(gender_weights):
      g[0, idx] += wn
    rng = np.random.default_rng(seed)
    e = np.zeros((1, len(ETHNICITY)), "float32")
    z = np.zeros((1, LATENT_DIM), "float32")
    for idx, wn in self._as_pairs(ethnicity_weights):
      z += rng.normal(size=(1, LATENT_DIM)).astype("float32") * wn
      e[0, idx] += wn
    oh = np.concatenate([g, e], axis=1)
    return self._iden(z, oh)[0]
