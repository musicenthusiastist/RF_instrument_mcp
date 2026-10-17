"""Versioned rules and a deliberately limited virtual analyzer profile."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT/'requirements/obue_rules.json').exists():
    ROOT = Path(sys.prefix)/'share/ee594-obue'
RULES = json.loads((ROOT / 'requirements/obue_rules.json').read_text())
CAPABILITY = json.loads((ROOT / 'requirements/analyzer_capability.json').read_text())
