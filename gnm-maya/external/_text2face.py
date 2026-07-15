"""Text -> semantic face parameters, in two tiers.

Tier 1 (always available): a hand-crafted lexicon. `parse_description` maps a
free-text description onto GNM's semantic classes with lowercase n-gram
matching — no dependencies beyond `_semantic`'s label lists.

Tier 2 (optional): a local Ollama server. `ollama_available` probes
http://localhost:11434 with a short timeout; `ollama_parse` asks whatever
model is installed to emit the same structure as strict JSON. Any failure
raises, so callers fall back to the lexicon. `describe` glues both together.

Runs in the module runtime (embeddable CPython), never in mayapy. HTTP is
stdlib urllib only — no new packages.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _semantic import ETHNICITY, EXPRESSION, GENDER  # noqa: E402

DEFAULT_HOST = "http://localhost:11434"

INTENSITY_LOW = 0.4
INTENSITY_DEFAULT = 0.7
INTENSITY_HIGH = 1.0
WEIGHT_CAP = 1.5

# ---------------------------------------------------------------------------
# Tier 1: lexicon
# ---------------------------------------------------------------------------

# Synonym phrases per GNM expression class. Multi-word phrases are matched
# longest-first, so e.g. "big smile" wins over "smile" and "winking right"
# wins over the bare-"wink" default (wink_left).
_EXPRESSION_PHRASES = {
    "surprise": [
        "surprise", "surprised", "shocked", "shock", "astonished", "amazed",
        "astounded", "startled", "stunned", "gasping", "gasp", "wide eyed",
        "jaw dropped",
    ],
    "disgust": [
        "disgust", "disgusted", "grossed out", "grossed", "gross", "revolted",
        "repulsed", "nauseated", "sickened", "eww", "yuck", "icky",
    ],
    "suck": [
        "sucking cheeks", "sucked cheeks", "sucked in cheeks",
        "sucking in cheeks", "hollow cheeks", "fish face", "sucking", "suck",
    ],
    "compress_face": [
        "compressed face", "compress face", "compressed", "scrunched",
        "scrunching", "scrunched up", "squished face", "squeezed face",
        "pinched face",
    ],
    "stretch_face": [
        "stretched face", "stretch face", "stretched", "stretching face",
        "elongated face", "yawning", "yawn",
    ],
    "happy": [
        "happy", "happiness", "joyful", "cheerful", "glad", "smiling",
        "smile", "pleased", "delighted", "content", "joy", "merry", "upbeat",
        "jolly",
    ],
    "squint": [
        "squint", "squinting", "suspicious", "skeptical", "wary",
        "narrowed eyes", "peering", "doubtful", "squinty",
    ],
    "platysma": [
        "platysma", "tense neck", "straining neck", "strained neck",
        "neck strain", "grimace", "grimacing",
    ],
    "blow": [
        "puffed cheeks", "puffing cheeks", "puffed up cheeks", "blowing",
        "blow", "puffed", "puffing", "puffy cheeks",
    ],
    "funneler": [
        "funneler", "funnel lips", "funneled lips", "whistling", "whistle",
        "ooh", "oo lips",
    ],
    "smile_wide": [
        "grin", "grinning", "beaming", "big smile", "wide smile",
        "huge smile", "broad smile", "ear to ear", "toothy smile",
    ],
    "corners_down": [
        "sad", "sadness", "frowning", "frown", "unhappy", "glum", "gloomy",
        "miserable", "sorrowful", "upset", "dejected", "downcast",
        "melancholy", "depressed", "mopey",
    ],
    "pucker": [
        "kiss", "kissing", "smooch", "smooching", "pucker", "puckered",
        "puckering", "kissy face", "kissy", "blowing a kiss",
    ],
    "wink_left": [
        "winking left", "wink left", "left wink", "left eye wink",
        "winking left eye", "winking with left eye", "wink", "winking",
    ],
    "wink_right": [
        "winking right", "wink right", "right wink", "right eye wink",
        "winking right eye", "winking with right eye",
    ],
    "mouth_left": [
        "mouth left", "mouth to the left", "mouth pulled left",
        "mouth shifted left",
    ],
    "mouth_right": [
        "mouth right", "mouth to the right", "mouth pulled right",
        "mouth shifted right",
    ],
    "lips_roll_in": [
        "lips rolled in", "lips roll in", "rolled lips", "rolling lips in",
        "pressed lips", "lips pressed", "biting lips", "tight lipped",
    ],
    "snarl": [
        "angry", "anger", "snarl", "snarling", "aggressive", "growling",
        "growl", "furious", "mad", "enraged", "rage", "fierce", "scowl",
        "scowling", "irritated", "annoyed", "hostile",
    ],
    "tongue_center": [
        "tongue out", "sticking tongue out", "stick tongue out",
        "sticking out tongue", "sticking tongue", "tongue",
    ],
}

_GENDER_PHRASES = {
    0: ["woman", "women", "female", "she", "her", "hers", "girl", "lady",
        "gal", "feminine"],
    1: ["man", "men", "male", "he", "him", "his", "guy", "boy", "gentleman",
        "dude", "masculine"],
}

_ETHNICITY_PHRASES = {
    0: ["middle eastern", "middle east", "arab", "arabic", "persian",
        "turkish", "levantine"],
    1: ["asian", "east asian", "chinese", "japanese", "korean", "vietnamese",
        "southeast asian"],
    2: ["white", "caucasian", "european"],
    3: ["black", "african", "african american", "afro"],
}

# Intensity modifiers apply to the NEXT expression phrase that matches.
_LOW_WORDS = {
    "slightly", "slight", "subtle", "subtly", "mildly", "mild", "somewhat",
    "faintly", "faint", "barely", "little", "bit", "touch", "hint", "gently",
    "softly",
}
_HIGH_WORDS = {
    "very", "extremely", "super", "really", "intensely", "intense",
    "totally", "hugely", "strongly", "deeply", "wildly", "massively", "big",
    "wide", "huge", "utterly",
}


def _build_table():
  """Map token-tuples -> ("expr"|"gender"|"ethnicity", class index)."""
  table = {}
  for name, phrases in _EXPRESSION_PHRASES.items():
    idx = EXPRESSION.index(name)
    for phrase in phrases:
      table[tuple(phrase.split())] = ("expr", idx)
  for idx, phrases in _GENDER_PHRASES.items():
    for phrase in phrases:
      table[tuple(phrase.split())] = ("gender", idx)
  for idx, phrases in _ETHNICITY_PHRASES.items():
    for phrase in phrases:
      table[tuple(phrase.split())] = ("ethnicity", idx)
  return table


_TABLE = _build_table()
_MAX_NGRAM = max(len(key) for key in _TABLE)


def _tokenize(text):
  """Lowercase word list; punctuation and apostrophes become separators."""
  return re.findall(r"[a-z0-9]+", text.lower())


def parse_description(text):
  """Parse a face description with the hand-crafted lexicon.

  Matches n-grams longest-first over the lowercased, punctuation-stripped
  token stream. Intensity modifiers ("slightly", "very", ...) apply to the
  next expression phrase; repeated mentions of a class accumulate. Unknown
  words ("old", "with", ...) are ignored.

  Args:
    text: Free-text description, e.g. "a very happy asian woman".

  Returns:
    Dict with keys "expression_weights" ({class index: weight}), "gender"
    (int or None), "ethnicity" (int or None) and "intensity" (float, the
    strongest applied weight, or the default when nothing matched).
  """
  tokens = _tokenize(text)
  weights = {}
  gender = None
  ethnicity = None
  pending = None  # intensity for the next expression match
  applied = []
  i = 0
  while i < len(tokens):
    hit = None
    for n in range(min(_MAX_NGRAM, len(tokens) - i), 0, -1):
      hit = _TABLE.get(tuple(tokens[i:i + n]))
      if hit is not None:
        i += n
        break
    if hit is None:
      word = tokens[i]
      if word in _LOW_WORDS:
        pending = INTENSITY_LOW
      elif word in _HIGH_WORDS:
        pending = INTENSITY_HIGH
      i += 1
      continue
    kind, value = hit
    if kind == "expr":
      w = pending if pending is not None else INTENSITY_DEFAULT
      weights[value] = min(weights.get(value, 0.0) + w, WEIGHT_CAP)
      applied.append(w)
      pending = None
    elif kind == "gender":
      if gender is None:
        gender = value
    elif ethnicity is None:  # kind == "ethnicity"
      ethnicity = value
  return {
      "expression_weights": weights,
      "gender": gender,
      "ethnicity": ethnicity,
      "intensity": max(applied) if applied else INTENSITY_DEFAULT,
  }


# ---------------------------------------------------------------------------
# Tier 2: local Ollama (optional)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You convert a description of a person's face into JSON. Respond with "
    "ONLY one JSON object, no prose, no markdown fences:\n"
    '{"expressions": {"<name>": <weight>}, "gender": "female"|"male"|null, '
    '"ethnicity": "middle_eastern"|"asian"|"white"|"black"|null}\n'
    "Valid expression names, use ONLY these: " + ", ".join(EXPRESSION) + ".\n"
    "Weights are floats in [0, 1]: 0.4 for subtle, 0.7 for normal, 1.0 for "
    "strong. Include only expressions the description implies. Use null for "
    "gender/ethnicity when the description does not specify them."
)


def _http_json(url, payload=None, timeout=5.0):
  """GET (payload None) or POST JSON to url, return decoded JSON."""
  data = None
  headers = {}
  if payload is not None:
    data = json.dumps(payload).encode("utf-8")
    headers["Content-Type"] = "application/json"
  req = urllib.request.Request(url, data=data, headers=headers)
  with urllib.request.urlopen(req, timeout=timeout) as resp:
    return json.loads(resp.read().decode("utf-8"))


def ollama_available(host=DEFAULT_HOST):
  """True if a local Ollama server answers /api/tags within 0.5 s."""
  try:
    _http_json(host.rstrip("/") + "/api/tags", timeout=0.5)
    return True
  except Exception:
    return False


def _extract_json(text):
  """Return the first balanced {...} block in text, parsed."""
  start = text.find("{")
  if start < 0:
    raise ValueError("no JSON object in model output")
  depth = 0
  for i in range(start, len(text)):
    if text[i] == "{":
      depth += 1
    elif text[i] == "}":
      depth -= 1
      if depth == 0:
        return json.loads(text[start:i + 1])
  raise ValueError("unbalanced JSON object in model output")


def _label_index(value, labels):
  """Map a label string to its index in labels, or None."""
  if value is None:
    return None
  key = str(value).strip().lower().replace(" ", "_").replace("-", "_")
  if key in ("", "null", "none", "unspecified", "unknown"):
    return None
  if key in labels:
    return labels.index(key)
  return None


def ollama_parse(text, model=None, host=DEFAULT_HOST, timeout=60.0):
  """Parse a face description with a local Ollama model.

  Args:
    text: Free-text description.
    model: Ollama model name; defaults to the first model in /api/tags.
    host: Ollama base URL.
    timeout: Chat request timeout in seconds.

  Returns:
    Same dict shape as parse_description.

  Raises:
    Exception: on any HTTP, JSON, or validation failure — callers are
      expected to fall back to the lexicon tier.
  """
  base = host.rstrip("/")
  if model is None:
    tags = _http_json(base + "/api/tags", timeout=2.0)
    models = tags.get("models") or []
    if not models:
      raise RuntimeError("ollama reachable but no models installed")
    model = models[0]["name"]
  resp = _http_json(base + "/api/chat", {
      "model": model,
      "stream": False,
      "format": "json",
      "messages": [
          {"role": "system", "content": _SYSTEM_PROMPT},
          {"role": "user", "content": text},
      ],
  }, timeout=timeout)
  content = (resp.get("message") or {}).get("content", "")
  raw = _extract_json(content)

  weights = {}
  for name, w in (raw.get("expressions") or {}).items():
    key = str(name).strip().lower().replace(" ", "_")
    if key not in EXPRESSION:
      continue
    try:
      w = float(w)
    except (TypeError, ValueError):
      continue
    if w > 0.0:
      weights[EXPRESSION.index(key)] = min(w, WEIGHT_CAP)
  return {
      "expression_weights": weights,
      "gender": _label_index(raw.get("gender"), GENDER),
      "ethnicity": _label_index(raw.get("ethnicity"), ETHNICITY),
      "intensity": max(weights.values()) if weights else INTENSITY_DEFAULT,
  }


def describe(text, prefer_ollama=True, model=None, host=DEFAULT_HOST):
  """Parse text with Ollama when available, else the lexicon.

  Args:
    text: Free-text face description.
    prefer_ollama: Try the local Ollama server first when it responds.
    model: Optional Ollama model name.
    host: Ollama base URL.

  Returns:
    parse_description-shaped dict plus "source": "ollama" or "lexicon".
  """
  if prefer_ollama and ollama_available(host):
    try:
      result = ollama_parse(text, model=model, host=host)
      result["source"] = "ollama"
      return result
    except Exception:
      pass  # fall back to lexicon below
  result = parse_description(text)
  result["source"] = "lexicon"
  return result
