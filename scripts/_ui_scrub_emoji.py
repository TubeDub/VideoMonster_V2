# -*- coding: utf-8 -*-
"""Strip emoji chrome from UI templates (one-shot polish helper)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F1E0-\U0001F1FF"
    "\U0000FE0F"
    "\U0000200D"
    "]+"
)

# Keep useful symbols: ✓ ✕ · → ← — …
KEEP = set("✓✕·→←—…⏱")

FILES = [
    "templates/settings.html",
    "templates/voice.html",
    "templates/translate.html",
    "templates/studio.html",
    "templates/director.html",
    "templates/plugins.html",
    "templates/cloud.html",
    "templates/platform.html",
    "templates/error.html",
    "templates/ai_settings.html",
    "templates/dev_architecture.html",
    "templates/dev_pipeline.html",
    "templates/download_center.html",
    "templates/reader.html",
    "templates/dub.html",
    "templates/index.html",
    "templates/projects.html",
    "templates/monitoring.html",
    "templates/soon.html",
    "static/js/modules_dev.js",
    "static/js/pipeline_dev.js",
]


def scrub(text: str) -> str:
    def repl(m: re.Match) -> str:
        chunk = m.group(0)
        kept = "".join(ch for ch in chunk if ch in KEEP)
        return kept

    out = EMOJI_RE.sub(repl, text)
    # tidy spaces after removals in common patterns
    out = re.sub(r"(<(?:h[1-6]|button|span|label|a|option|strong|p|div)[^>]*>)\s{2,}", r"\1", out)
    out = re.sub(r">\s{2,}([^<\s])", r"> \1", out)
    out = re.sub(r"([^\s])\s{2,}([^<\s])", r"\1 \2", out)
    out = re.sub(r" +\n", "\n", out)
    return out


def main() -> None:
    for rel in FILES:
        path = ROOT / rel
        if not path.exists():
            print("missing", rel)
            continue
        old = path.read_text(encoding="utf-8")
        new = scrub(old)
        if new != old:
            path.write_text(new, encoding="utf-8")
            print(f"cleaned {rel} ({len(old) - len(new)} bytes)")
        else:
            print(f"unchanged {rel}")


if __name__ == "__main__":
    main()
