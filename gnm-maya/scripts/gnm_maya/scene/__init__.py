"""VIEW layer (Maya scene): everything that creates or edits scene content.

Meshes (``build``), shaders/textures (``material``), baked rigs (``rig``),
landmark locators (``landmarks``), crowds (``crowd``), FBX export
(``export_fbx``) and the preset library (``presets``). Modules here use
``maya.cmds``/OpenMaya but never Qt — panels and dialogs live in ``ui``.
"""
