"""MODEL layer: parameter state and access to the GNM model process.

Pure data/process code — no Qt and no Maya UI. ``head.GnmHead`` owns the
coefficient state (identity/expression/pose) and talks to the resident model
worker (``worker``), which runs GNM in the bundled runtime and returns meshes
read by the pure-python ``meshio`` readers. ``config`` resolves the module's
on-disk layout; ``settings`` persists user preferences via Maya optionVars.

One deliberate MVC exception, documented at the call site: ``head.GnmHead``
pushes evaluated vertices straight into the scene via ``scene.build`` so every
edit path shares a single update funnel.
"""
