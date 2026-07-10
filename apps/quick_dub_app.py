"""VideoMonster Quick Dub — отдельная точка входа экосистемы."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("VM_START_URL", "/dub")

from desktop import main

if __name__ == "__main__":
    main()
