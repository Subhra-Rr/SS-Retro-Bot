"""
Pytest configuration and environment setup.
Ensures project root directory is on Python's module search path.
"""

import sys
from pathlib import Path

# Add project root (D:\SS Retro Bot) to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
