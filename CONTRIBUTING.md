# Contributing to GNM-Maya

Thanks for your interest! Bug reports, feature ideas and pull requests are
all welcome.

## Reporting bugs

Open an issue using the **Bug report** template. The most useful reports
include:

- Your OS and Maya version (e.g. Windows 11 / Maya 2025)
- The gnm-maya version (`GNM ▸ Check for gnm-maya Tool Updates` shows it) or
  the commit id if you run the dev channel
- The full error text from Maya's **Script Editor** (every gnm-maya failure
  also pops an error dialog — paste what it says)

## Suggesting features

Open an issue with the **Feature request** template. Screenshots or links to
reference behavior (other tools, papers, posts) help a lot.

## Pull requests

1. Fork and branch from `master`.
2. Keep the layering: `core/` (model/state, no Qt), `scene/` (Maya scene
   building), `ui/` (Qt panels), `services/` (bootstrap/updates). Model code
   that needs numpy lives in `external/` and runs in the module's own
   runtime, never inside Maya's Python.
3. Follow the existing style: 2-space indent, docstrings that explain *why*,
   tooltips for every new UI control, errors surfaced via dialogs.
4. Test before pushing:
   - Headless: `mayapy -c "import sys; sys.path.insert(0, r'<repo>/gnm-maya/scripts'); from gnm_maya import smoke_test; smoke_test.run()"`
   - In Maya: run `gnm-maya/tests/gui_smoke_test.py` from the Script Editor
     (it cleans up after itself).
5. Update the README if you add or change user-facing behavior.

## Project scope

The tool wraps [Google GNM](https://github.com/google/GNM) (Apache-2.0) for
Autodesk Maya. Model weights and runtime dependencies are downloaded on first
run and are never committed to the repo — keep it that way in PRs (no binary
blobs; the repo ships code, docs and compact preview images only).
