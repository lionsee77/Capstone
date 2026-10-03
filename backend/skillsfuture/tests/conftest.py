"""Test configuration for the SkillsFuture scripts."""
from __future__ import annotations

import sys
from pathlib import Path

SKILLSFUTURE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(SKILLSFUTURE_DIR))
