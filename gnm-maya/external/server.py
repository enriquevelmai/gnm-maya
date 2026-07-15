#!/usr/bin/env python
"""Persistent GNM worker: load the model once, serve mesh evals on stdin.

Protocol (line-oriented, one JSON object per line):

  startup   -> writes topology into --session dir, prints:  READY <session_dir>
  request   <- {"identity":[...], "expression":[...], "rotations":[[..]x4],
                "translation":[...], "out":"<path/vertices.bin>"}
  response  -> OK <out>                 on success
               ERR <message>            on failure (request kept alive)
  shutdown  <- {"cmd":"quit"}  ->  prints BYE and exits

Missing params default to zeros. Keeping the 51 MB model resident makes each
eval ~15 ms, so Maya sliders update interactively via set_points.

Run inside the module venv, driven by gnm_maya.worker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gnm_core as core


def _log(*a):
  print(*a, file=sys.stderr, flush=True)


def main():
  p = argparse.ArgumentParser()
  p.add_argument("--session", required=True, help="Dir for topology + meshes.")
  args = p.parse_args()

  _log("Loading GNM V3 HEAD...")
  model = core.load_model()
  core.export_topology(model, args.session)

  sampler = {"obj": None}  # lazy: only built on first semantic request

  def get_sampler():
    if sampler["obj"] is None:
      import _semantic
      repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gnm_repo")
      sampler["obj"] = _semantic.Sampler(repo)
    return sampler["obj"]

  # Handshake: caller waits for this exact line.
  print("READY %s" % args.session, flush=True)

  for line in sys.stdin:
    line = line.strip()
    if not line:
      continue
    try:
      req = json.loads(line)
    except ValueError as e:
      print("ERR bad json: %s" % e, flush=True)
      continue

    cmd = req.get("cmd")
    if cmd == "quit":
      print("BYE", flush=True)
      return

    if cmd == "fit_landmarks3d":
      try:
        import numpy as np
        import _fitting
        vec = _fitting.fit_identity_3d(
            np.asarray(req["targets"], np.float64).reshape(68, 3), model,
            expression=req.get("expression"), lam=req.get("lam", 1.0))
        print("COEFF " + json.dumps([float(x) for x in vec]), flush=True)
      except Exception as e:
        print("ERR %s" % e, flush=True)
      continue

    if cmd == "fit":
      try:
        import numpy as np
        import _fitting
        if req.get("image"):
          vec = _fitting.fit_from_photo(req["image"], model,
                                        lam=req.get("lam", 2.0))
        else:
          lms = np.asarray(req["landmarks"], np.float64).reshape(68, 2)
          vec = _fitting.fit_identity(lms, model,
                                      order=req.get("order", "ibug"),
                                      lam=req.get("lam", 2.0))
        print("COEFF " + json.dumps([float(x) for x in vec]), flush=True)
      except Exception as e:
        print("ERR %s" % e, flush=True)
      continue

    if cmd == "bake":
      try:
        sampler_obj = get_sampler() if req.get("semantic", True) else None
        meta = core.export_rig_data(
            model, args.session,
            identity=req.get("identity"),
            num_modes=req.get("num_modes", 0),
            sampler=sampler_obj,
            seed=req.get("seed", 0),
        )
        print("BAKED %d" % len(meta["targets"]), flush=True)
      except Exception as e:
        print("ERR %s" % e, flush=True)
      continue

    if cmd in ("sample_identity", "sample_expression",
               "blend_identity", "blend_expression"):
      try:
        s = get_sampler()
        if cmd == "sample_expression":
          vec = s.sample_expression(req["class"], req.get("seed"))
        elif cmd == "sample_identity":
          vec = s.sample_identity(req["gender"], req["ethnicity"],
                                  req.get("seed"))
        elif cmd == "blend_expression":
          vec = s.blend_expression(req["weights"], req.get("seed"))
        else:  # blend_identity
          vec = s.blend_identity(req["gender_weights"], req["ethnicity_weights"],
                                 req.get("seed"))
        print("COEFF " + json.dumps([float(x) for x in vec]), flush=True)
      except Exception as e:
        print("ERR %s" % e, flush=True)
      continue

    if cmd == "text2face":
      try:
        import numpy as np
        import _text2face
        d = _text2face.describe(req["text"],
                                prefer_ollama=req.get("prefer_ollama", True))
        seed = req.get("seed")
        s = get_sampler()
        expression = None
        if d["expression_weights"]:
          expression = [float(x) for x in
                        s.blend_expression(d["expression_weights"], seed)]
        identity = None
        if d["gender"] is not None or d["ethnicity"] is not None:
          rng = np.random.default_rng(seed)
          gender = (d["gender"] if d["gender"] is not None
                    else int(rng.integers(len(_text2face.GENDER))))
          ethnicity = (d["ethnicity"] if d["ethnicity"] is not None
                       else int(rng.integers(len(_text2face.ETHNICITY))))
          identity = [float(x) for x in s.sample_identity(gender, ethnicity,
                                                          seed)]
        print("T2F " + json.dumps({"expression": expression,
                                   "identity": identity, "parsed": d}),
              flush=True)
      except Exception as e:
        print("ERR %s" % e, flush=True)
      continue

    try:
      out = req["out"]
      verts = core.eval_vertices(
          model,
          identity=req.get("identity"),
          expression=req.get("expression"),
          rotations=req.get("rotations"),
          translation=req.get("translation"),
      )
      core.write_vertices(verts, out)
      print("OK %s" % out, flush=True)
    except Exception as e:  # keep the server alive on a bad request
      print("ERR %s" % e, flush=True)


if __name__ == "__main__":
  main()
