"""Testler hem `py -m unittest discover -s tests` hem `py -m unittest tests.test_x` hem de pytest ile çalışır."""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)  # `import _ortak`
