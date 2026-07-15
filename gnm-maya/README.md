# GNM-Maya

Generate [Google GNM](https://github.com/google/GNM) parametric human **head**
meshes directly inside Autodesk Maya.

GNM ("Generative aNthropometric Model") is a 3D Morphable Model: a neutral
template plus linear identity (253-dim) and expression (383-dim) bases and a
4-joint pose (neck, head, left/right eye), over a fixed 17,821-vertex /
**17,662-quad** topology with UVs and 6 anatomical parts (skin, left/right eye,
upper/lower teeth+gums, tongue).

The Maya mesh is built as **quads** (not triangulated), carries the model's
**UVs**, and gets **per-part materials** (skin/eyes/teeth/tongue) so it renders
correctly out of the box. A tabbed panel gives live **Identity / Expression /
Pose / Translation** sliders.

## How it's contained

The GNM model needs numpy + the 51 MB `.npz` basis, which don't belong inside
`mayapy`. So this module ships a **self-contained portable Python runtime** and
runs the model there. Maya only reads tiny binary mesh files with a
**pure-Python** reader (no numpy in Maya) and builds the mesh natively with
OpenMaya 2.

For interactive sliders, a **persistent worker** (`external/server.py`) keeps
the model resident so each edit resolves in ~15 ms (vs ~0.5 s if the model
reloaded per change); Maya updates only the vertex positions (topology is
fixed). `external/generate.py` remains for one-shot/batch use.

The bundled interpreter is a **portable embeddable CPython** (`runtime/`), not a
venv, so it runs on any Windows machine **with no Python install** and is
**Maya-version agnostic** (2022–2027+) with no ABI concerns.

```
GNM.mod                     <- module descriptor (copy this into modules/)
gnm-maya/                   <- the Maya module (docs included)
  README.md / NOTICE.md     <- this documentation
  drag_and_drop_install.py  <- drag into Maya to install
  docs/shapes/              <- shape gallery images (~1300 renders, included)
  scripts/
    userSetup.py            <- adds the "GNM" menu on launch
    gnm_maya/               <- Maya-side package (pure python, Qt-optional)
  external/
    server.py / generate.py <- run inside the downloaded runtime
    gnm_repo/                <- google/GNM repo (downloaded on first run)
  runtime/                  <- portable Python (downloaded on first run)
  build_module.py           <- dev: rebuilds runtime/ without the dialog
```

## Install (Windows)

**Easiest — drag and drop:**

1. Download this repo (Code ▸ Download ZIP) and extract it anywhere.
2. Drag `gnm-maya/drag_and_drop_install.py` from Explorer into a running Maya
   viewport.

It registers the module in place (no copying), adds the **GNM** menu + shelf
button, and runs the **first-run setup**: the repo ships code only, so the
portable Python runtime (~70 MB) and the google/GNM model repo (~40 MB) are
downloaded once into the module folder, and the shape images are rendered
locally (~5 min, one time). After that everything runs offline.
(Photo fitting adds an optional ~290 MB MediaPipe/OpenCV download on first use.)

**Manual alternative — copy into your modules folder:** copy both `GNM.mod` and
the `gnm-maya/` folder into `C:\Users\<you>\Documents\maya\modules\`, then
restart Maya; opening the panel offers the same first-run setup.

> Dev rebuild of the runtime without the dialog: `py -3.11 gnm-maya/build_module.py`

## Use

- **GNM ▸ GNM Head Panel…** — opens *GNM Head (Generative aNthropometric
  Model)*, docked to the Maya main window. A **Semantic** tab samples identity
  by **Gender × Ethnicity** and **20 named expressions** (Smile Wide, Happy,
  Wink Left, …) — GNM's conditional-VAE decoders, run in numpy (no TensorFlow).
  Then tabs for **Identity / Expression / Pose / Translation** with
  per-coefficient sliders grouped by body part
  (double-click a slider to reset it). Each tab has its own **Randomize** and
  **Reset** buttons; each group has **Reset** and a **Show all N** button to
  expand past the first 12 modes. Plus a **Symmetry (L/R)** toggle (mirrors
  edits across the left/right eye regions and eye joints), a **random scale**
  (next to Randomize Identity), a **Texture** toggle (applies the bundled GNM
  edgeflow PNG, or **…** to pick your own), and **Reset Selected / All** (resets
  the GNM head(s) you have selected, or the panel's head if none). Re-opening
  the panel adopts an existing/selected head rather than spawning a new one.
- **Bake Rig** (panel button) — converts the current head into a
  **self-sufficient rigged asset**: a blendShape node with the 20 named
  expressions and/or the **first N basis modes of each region** as keyframable
  sliders (happy, smile_wide, left_eye_region_000, …) plus a neck → head → eyes
  joint chain skinned with GNM's own weights. The result needs no GNM runtime
  and exports to FBX. (Note: Maya's plain LBS skips GNM's pose correctives, so
  extreme neck/eye rotations deviate slightly.)
- **Fit from Photo…** (panel button) — detects 68 facial landmarks with
  **MediaPipe (fully local)** and least-squares fits the identity coefficients
  (damped Gauss-Newton over shape + weak-perspective camera). Produces a
  *likeness* from front-view information, not a scan match. The ~290 MB
  MediaPipe/OpenCV stack is **not bundled**; the button offers a one-click
  install into the module runtime on first use.
- **GNM ▸ Presets…** — save/load/delete named heads (JSON + viewport
  thumbnail). The **Folder…** button picks where presets are stored
  (default `~/Documents/maya/gnm_presets`; remembered across sessions).
- **GNM ▸ Generate Crowd…** — N varied heads laid out in a grid (optional
  random expressions, optional per-head rig bake).
- **GNM ▸ Export Selected Rig (FBX)** — pick the destination in a save
  dialog (folder remembered; default `~/Documents/maya/gnm_exports`), with
  fbxmaya settings for blendshapes + skins.
- **GNM ▸ Landmarks: Create / Update / Fit Head to Locators** — GNM's 68
  facial landmarks as locators. Drag them (optionally with the L/R mirror
  toggle) and **Fit Head to Locators** ridge-solves the identity so the head
  reshapes to match — the landmarks are a sculpting skeleton.
- **Describe** (Semantic tab) — type "a very happy asian woman, winking left"
  and Apply. Local synonym lexicon (instant, offline); if a local **Ollama**
  server is running it is used automatically for free-form phrasing.
- **GNM ▸ Shape Gallery** — a page ([`docs/shapes/index.html`](docs/shapes/index.html))
  of **min/max renders of every UI slider shape** plus the 20 semantic
  expressions (1293 images covering ALL 636 modes, included in this repo).
  Occluded groups (tongue, teeth, pupils) render an isolated zoom of that part
  instead of the full head, so their change is actually visible. The same
  images appear on the sliders and in tooltips; pick their size with the
  panel's image-size dropdown (No images / Small / Medium / Large / Huge).
  Regenerate with `runtime\python.exe external\gen_gallery.py --out docs\shapes`.
- **GNM ▸ Quick: Random Head** / **Template Head** — one-click.
- **GNM ▸ Add Shelf Button** — drops a **GNM** button on the active shelf that
  opens the panel. The drag-and-drop installer also adds this automatically.
- **GNM ▸ Licenses…** — view this module's license and every bundled/vendored
  license (GNM, CPython, numpy, …).

To add a shelf button from script (targets the active shelf):

```python
import gnm_maya; gnm_maya.add_shelf_button()
```

Actions are logged to the Script Editor via the `gnm_maya` logger.

### How the Identity / Expression sliders work (PCA-style basis)

GNM's Identity (253) and Expression (383) controls are a **statistical shape
basis, ordered by importance — like PCA components**, not hand-named blendshapes
such as "smile" or "jaw open":

- **Lower-numbered modes** (`m0`, `m1`, …) capture the largest, most meaningful
  variation in shape/expression.
- **Higher-numbered modes** are progressively finer, subtler adjustments.

The panel groups modes by region (e.g. `head`, `left_eye_region`) and shows the
first 12 per region to stay usable — 253 + 383 = 636 sliders would be
unmanageable. Each slider is labelled `m<local index>`; hover it to see its full
basis name (e.g. `head_017`). Use **Show all N** on a group to reveal the rest.

Every mode is fully drivable from script by its global index regardless of what
the panel shows:

```python
head.set_expression(97, 2.0)   # 98th expression mode
head.set_identity(200, -1.5)   # a fine teeth-region identity mode
```

The **ⓘ** button in the panel shows this same explanation.

From script:

```python
import gnm_maya
panel = gnm_maya.show_ui()                 # tabbed slider panel

head = gnm_maya.generate_template()        # a live GnmHead controller
head.set_identity(0, 2.0)                  # drive coefficient i0
head.set_expression(5, 3.0)                # drive expression 5
head.set_rotation(2, 1, 0.3)              # left_eye, ry (gaze)
head.randomize_identity(scale=1.0)
head.reset_all()
```

Because topology is fixed, edits only push new vertex positions into the
existing mesh (fast) — and the same property makes the output a natural fit for
Maya **blendshape** rigs.

## Test

**Headless core** (no GUI — builds a head, checks quads/UVs/materials/deform):

```
mayapy -c "import sys; sys.path.insert(0, r'<...>/gnm-maya/scripts'); \
           from gnm_maya import smoke_test; smoke_test.run()"
```

**GUI panel** (run inside a running Maya session — Qt needs the full app). In
the Script Editor's **Python** tab:

```python
exec(open(r"<...>/gnm-maya/tests/gui_smoke_test.py").read())
```

It opens the panel, drives sliders/randomize/reset/symmetry/show-all/licenses,
prints PASS/FAIL for each, and leaves the panel open for hand inspection.

## Updating GNM

The full upstream repo is vendored at `gnm-maya/external/gnm_repo`. Use
**GNM ▸ Check for Updates** to compare against `google/GNM` on GitHub and, if a
newer commit exists, download and replace that folder in place. After a
successful download you're offered to **Restart Maya** (for a clean state) and
to **Run GUI Test** to verify the new version. You can also run the test any
time via **GNM ▸ Run GUI Test**.

## Citation

If you use GNM (via this tool) in your work, please cite the GNM Ecosystem as
requested by its authors — see the
[Citation section of the upstream README](https://github.com/google/GNM#citation).
As of the vendored version, the official **GNM Head** BibTeX entry is marked
*"coming soon"* upstream; check there for the final entry. This Maya
integration is not affiliated with Google.

## Licensing

GNM is Apache-2.0 — its license ships with the downloaded repo at
`external/gnm_repo/LICENSE`, and the model asset is redistributed under it.
The bundled CPython runtime (PSF) and its packages (numpy/etils/absl-py/…)
retain their own licenses — see `NOTICE.md` and the in-app **GNM ▸ Licenses…**
viewer.
