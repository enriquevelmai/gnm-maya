"""On-demand Rhubarb Lip Sync (MIT) for audio -> mouth-shape cues.

Rhubarb (https://github.com/DanielSWolf/rhubarb-lip-sync) turns a WAV/OGG
dialogue recording into a timeline of mouth shapes A–H/X. Like the other
heavy optional pieces it is downloaded into the module folder on first use
(~85 MB, bundles its speech models) and never touches the system.
"""

from __future__ import annotations

import io
import json
import logging
import os
import stat
import subprocess
import sys
import zipfile

from gnm_maya.core import config

logger = logging.getLogger(__name__)

VERSION = "1.14.0"
_PLATFORM = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")
URL = ("https://github.com/DanielSWolf/rhubarb-lip-sync/releases/download/"
       "v%s/Rhubarb-Lip-Sync-%s-%s.zip" % (VERSION, VERSION, _PLATFORM))
TOOLS_DIR = os.path.join(config.MODULE_ROOT, "tools", "rhubarb")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def executable():
  exe = "rhubarb.exe" if os.name == "nt" else "rhubarb"
  for root, _dirs, files in os.walk(TOOLS_DIR):
    if exe in files:
      return os.path.join(root, exe)
  return None


def available():
  return executable() is not None


def ensure(status=lambda msg: None):
  """Download + unpack Rhubarb if missing. Returns the executable path."""
  exe = executable()
  if exe:
    return exe
  from gnm_maya.services import bootstrap
  status("Downloading Rhubarb Lip Sync %s (~85 MB)…" % VERSION)
  blob = bootstrap._download(URL, timeout=900)
  os.makedirs(TOOLS_DIR, exist_ok=True)
  with zipfile.ZipFile(io.BytesIO(blob)) as z:
    z.extractall(TOOLS_DIR)
  exe = executable()
  if not exe:
    raise RuntimeError("Rhubarb archive did not contain the executable.")
  if os.name != "nt":
    os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR | stat.S_IXGRP)
  logger.info("Rhubarb ready at %s", exe)
  return exe


def run(audio_path, dialog_text=None, timeout=900):
  """Analyse ``audio_path`` (WAV/OGG). Returns Rhubarb's JSON dict:
  {"metadata": {"duration": s}, "mouthCues": [{"start", "end", "value"}]}."""
  exe = ensure()
  if not os.path.isfile(audio_path):
    raise RuntimeError("Audio file not found: %s" % audio_path)
  ext = os.path.splitext(audio_path)[1].lower()
  if ext not in (".wav", ".ogg"):
    raise RuntimeError("Rhubarb reads WAV or OGG only (got %s). Convert the "
                       "audio first." % ext)
  cmd = [exe, "-f", "json", "--machineReadable", audio_path]
  if dialog_text:
    import tempfile
    fd, txt = tempfile.mkstemp(suffix=".txt", prefix="gnm_dialog_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
      f.write(dialog_text)
    cmd += ["-d", txt]
  logger.info("Rhubarb: %s", " ".join(cmd))
  r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     universal_newlines=True, timeout=timeout,
                     creationflags=_NO_WINDOW)
  if r.returncode != 0:
    raise RuntimeError("Rhubarb failed:\n%s" % r.stderr[-1500:])
  return json.loads(r.stdout)
