"""Create a Maya shelf button that opens the GNM Head panel."""

from __future__ import annotations

import logging

from maya import cmds as mc
import maya.mel as mel

logger = logging.getLogger(__name__)

_LABEL = "GNM Head"
_COMMAND = "import gnm_maya; gnm_maya.show_ui()"


def _current_shelf():
  top = mel.eval("$tmp = $gShelfTopLevel")
  return mc.tabLayout(top, query=True, selectTab=True)


def add_shelf_button(shelf=None):
  """Add (or replace) a 'GNM Head' button on the given/active shelf.

  Returns the button's name. Safe to call repeatedly — it removes a prior GNM
  button on that shelf first so you never stack duplicates.
  """
  if mc.about(batch=True):
    logger.info("Batch mode: skipping shelf button.")
    return None

  shelf = shelf or _current_shelf()

  for child in (mc.shelfLayout(shelf, query=True, childArray=True) or []):
    try:
      if mc.shelfButton(child, query=True, exists=True) and \
         mc.shelfButton(child, query=True, label=True) == _LABEL:
        mc.deleteUI(child)
    except Exception:
      pass

  # Prefer our bundled GNM emblem; fall back to a stock Maya icon if the PNG
  # can't be produced (no Qt / missing SVG). The overlay label keeps it
  # recognisable either way.
  image = "commandButton.png"
  try:
    from gnm_maya.ui import icons
    png = icons.image_file("gnm", 32, color="#dcdcdc")
    if png:
      image = png
  except Exception:
    logger.debug("GNM shelf icon unavailable; using stock image",
                 exc_info=True)

  btn = mc.shelfButton(
      parent=shelf,
      label=_LABEL,
      annotation="Open the GNM Head (Generative aNthropometric Model) panel",
      image=image,
      imageOverlayLabel="GNM",        # text drawn on the button
      command=_COMMAND,
      sourceType="python",
  )
  logger.info("Added GNM shelf button on shelf '%s'", shelf)
  return btn
