"""Run tests without generating reports or datasets."""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
if (ROOT / '.testdeps').exists():
    # Prefer installed Windows wheels over old vendored native extensions.
    sys.path.append(str(ROOT / '.testdeps'))
import unittest
result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover(str(ROOT / 'tests')))
raise SystemExit(0 if result.wasSuccessful() else 1)
