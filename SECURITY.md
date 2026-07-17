# Security Policy

## Supported versions

Only the latest [release](https://github.com/enriquevelmai/gnm-maya/releases)
and the current `master` branch are supported with fixes.

## What this tool downloads and runs

For transparency, gnm-maya fetches at first run (all over HTTPS, all into the
module's own folder):

- The official python.org embeddable CPython (Windows) or builds a venv from
  your local `python3` (Linux/macOS)
- Python packages from PyPI (numpy, h5py, and optionally the
  MediaPipe/OpenCV photo-fitting stack)
- The [google/GNM](https://github.com/google/GNM) repository (model weights)
- Its own updates from this repository (GitHub Releases by default)

It never runs code from anywhere else, and never sends data anywhere — the
optional "Describe" Ollama integration talks only to `localhost`.

## Reporting a vulnerability

If you find a vulnerability (e.g. in the update/download paths above),
please **do not open a public issue**. Use GitHub's
[private vulnerability reporting](https://github.com/enriquevelmai/gnm-maya/security/advisories/new)
instead. You should receive a response within a week. Please include steps to
reproduce and the affected version/commit.
