"""Make the engine importable in tests without an install step.

Adds ``engine/src`` (for ``import m3diff``) and ``engine/tests`` (for
``import fixtures.builder``) to ``sys.path``.
"""
import sys
from pathlib import Path

_tests_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_tests_dir))
sys.path.insert(0, str(_tests_dir.parent / "src"))
