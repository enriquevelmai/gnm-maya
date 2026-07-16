# NOTICE

This repo ships **code and documentation only**. The two pieces below are
**not stored in this repository** — the module downloads them into itself on
first run (see `gnm_maya/bootstrap.py`), so the notices here describe what
gets installed on your machine, not what's tracked in git.

## Google GNM (downloaded, unmodified)

On first run, `gnm-maya/external/gnm_repo/` is populated with a **complete,
unmodified copy** of Google's GNM ("Generative aNthropometric Model")
repository, including its `LICENSE` and the model asset
`gnm/shape/data/versions/v3_0/gnm_head.npz`.

- Source: https://github.com/google/GNM
- Copyright 2026 Google LLC
- Licensed under the Apache License, Version 2.0 — full text at
  https://github.com/google/GNM/blob/main/LICENSE (also present locally at
  `gnm-maya/external/gnm_repo/LICENSE` once downloaded).

Only the NumPy code path is imported at runtime; the other backends and data
ship with the repo but are simply not loaded. The **GNM ▸ Check for Updates**
menu item refreshes this folder from the upstream repo.

This project is **not affiliated with, sponsored by, or endorsed by Google**.
"GNM" and "Google" are used only to describe the model this tool loads
(Apache-2.0 grants no trademark rights, §6).

## Bundled Python runtime (set up on first run)

On first run, `gnm-maya/runtime/` is populated with the module's own Python.
On **Windows** this is the official **embeddable CPython 3.11.9**
(https://www.python.org/downloads/), redistributed under the **PSF License
Agreement** (full text at https://docs.python.org/3/license.html, also present
locally at `gnm-maya/runtime/LICENSE.txt` once downloaded), so the module runs
without a system Python install. On **Linux/macOS** it is a venv created from
the machine's own `python3` (nothing is redistributed).

Its site-packages contains these dependencies (installed by
`gnm_maya/bootstrap.py` / `gnm-maya/build_module.py`), each retaining its own
license: NumPy (BSD-3-Clause), etils (Apache-2.0), absl-py (Apache-2.0),
immutabledict (MIT), einops (MIT), typing_extensions (PSF-2.0), h5py
(BSD-3-Clause).

## In-app license viewer

**GNM ▸ Licenses…** lists this module's license plus every license actually
installed on your machine (the two above, and — if you've used **Fit from
Photo** — MediaPipe/OpenCV and their dependencies).
