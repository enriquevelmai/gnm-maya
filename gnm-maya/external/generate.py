#!/usr/bin/env python
"""One-shot GNM head generator (module venv, not mayapy).

Writes topology (quads/uvs/components) + a vertices.bin for a single sample.
Used by the module self-test and for non-interactive/batch use. The Maya panel
uses server.py instead for live sliders.

  python generate.py --out DIR [--seed N] [--identity-scale F]
                     [--expression-scale F] [--template]

On success prints  ``OK <out>``  as the last stdout line.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gnm_core as core


def _log(*a):
  print(*a, file=sys.stderr, flush=True)


def main():
  p = argparse.ArgumentParser()
  p.add_argument("--out", required=True)
  p.add_argument("--seed", type=int, default=0)
  p.add_argument("--identity-scale", type=float, default=1.0)
  p.add_argument("--expression-scale", type=float, default=0.0)
  p.add_argument("--template", action="store_true")
  args = p.parse_args()

  import numpy as np

  _log("Loading GNM V3 HEAD...")
  model = core.load_model()
  core.export_topology(model, args.out)

  if args.template:
    identity = expression = None
  else:
    rng = np.random.default_rng(args.seed)
    identity = rng.standard_normal(model.identity_dim).astype(np.float32) * args.identity_scale
    expression = rng.standard_normal(model.expression_dim).astype(np.float32) * args.expression_scale

  verts = core.eval_vertices(model, identity=identity, expression=expression)
  core.write_vertices(verts, os.path.join(args.out, "vertices.bin"))

  _log("Wrote %d verts, %d quads to %s"
       % (model.num_vertices, model.quads.shape[0], args.out))
  print("OK %s" % args.out)
  return 0


if __name__ == "__main__":
  sys.exit(main())
