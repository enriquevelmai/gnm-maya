# GNM-Maya

Generate [Google GNM](https://github.com/google/GNM) parametric human head
meshes inside Autodesk Maya — semantic sampling, blending, text descriptions,
photo fitting, rig baking, crowds, and more.

- 📖 Full documentation: [gnm-maya/README.md](gnm-maya/README.md)
- 🖼 Shape gallery (what every slider does, browsable on GitHub):
  [gnm-maya/docs/shapes/README.md](gnm-maya/docs/shapes/README.md)
- ⚖ Licensing & attributions: [gnm-maya/NOTICE.md](gnm-maya/NOTICE.md)

## Quick install (Windows / Linux / macOS)

1. Code ▸ Download ZIP, extract anywhere.
2. Drag `drag_and_drop_install.py` (at the top of the extracted folder) into a
   running Maya viewport.
3. Accept the first-run setup (Python runtime + GNM model). On Linux/macOS a
   `python3` (3.9+, with the venv module) must be on PATH — Maya's own Python
   is used as a fallback.

The installer **copies the module into `~/Documents/maya/modules`**, so once
it finishes you can **delete the downloaded zip and the extracted folder**.
