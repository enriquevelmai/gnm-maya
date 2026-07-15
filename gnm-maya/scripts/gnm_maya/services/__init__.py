"""SERVICES layer: downloads, updates, and optional-dependency installs.

``bootstrap`` performs the first-run setup (portable runtime, GNM model repo,
shape-gallery render). ``updater`` tracks the vendored google/GNM model;
``tool_updater`` self-updates this tool from its GitHub repo; ``fitting_deps``
installs the optional MediaPipe/OpenCV stack for photo fitting. All use
stdlib networking only and install strictly inside the module folder.
"""
