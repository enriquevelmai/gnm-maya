#!/usr/bin/env python
"""Reproducibly (re)build the module's ``runtime/`` interpreter (dev helper).

End users never run this — the same logic runs automatically on first use
(scripts/gnm_maya/services/bootstrap.py, which this script simply calls). It
exists so developers can rebuild the runtime without Maya. Run it with any
Python 3.8+; the runtime it builds is the portable Python 3.12 the bootstrap
downloads (Windows: python.org embeddable zip; macOS/Linux:
python-build-standalone), so the host interpreter's version does not matter:

    python build_module.py

The existing ``runtime/`` is deleted first, then a template head is generated
through the new runtime as a self-test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))

# gnm_maya/__init__.py imports maya.cmds; register a bare package instead so
# its Maya-free submodules (config, bootstrap) import outside Maya.
_pkg = types.ModuleType("gnm_maya")
_pkg.__path__ = [os.path.join(HERE, "scripts", "gnm_maya")]
sys.modules.setdefault("gnm_maya", _pkg)

from gnm_maya.services import bootstrap  # noqa: E402


def main():
  shutil.rmtree(bootstrap.RUNTIME_DIR, ignore_errors=True)
  bootstrap.ensure_runtime(status=print)
  if not bootstrap.runtime_available():
    sys.exit("Runtime build finished but it is not usable: %s"
             % bootstrap.RUNTIME_DIR)

  print("Self-test: generating a template head via the runtime ...")
  out = os.path.join(HERE, "_buildtest")
  subprocess.check_call(
      [bootstrap.runtime_python(),
       os.path.join(HERE, "external", "generate.py"), "--out", out,
       "--template"])
  shutil.rmtree(out, ignore_errors=True)

  print("\nDone. Runtime (Python %d.%d) ready at: %s"
        % (bootstrap.runtime_version() + (bootstrap.RUNTIME_DIR,)))
  print("Ship: GNM.mod + the gnm-maya/ folder. Users drop both into "
        "~/Documents/maya/modules (or just drag drag_and_drop_install.py "
        "into Maya).")


if __name__ == "__main__":
  main()
