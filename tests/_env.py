# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared setup of the headless tests: imports the add-on from this checkout (whatever its folder is called) as the
package `EasyMold`, and gives OUT, the folder for the renders the tests write (tests/out/)."""
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = str(ROOT / "tests" / "out")
os.makedirs(OUT, exist_ok=True)

sys.path.insert(0, str(ROOT.parent))
EasyMold = importlib.import_module(ROOT.name)
sys.modules["EasyMold"] = EasyMold  # `from EasyMold import utils` works in a clone named otherwise
