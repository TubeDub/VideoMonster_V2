# -*- coding: utf-8 -*-
from pathlib import Path
import re

ROOT = Path(r"C:\Users\serhii\Desktop\VideoMonster_V2")
EMOJI = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0000FE0F"
    "\U0000200D"
    "]+"
)

def scrub_file(rel: str) -> None:
    p = ROOT / rel
    t = p.read_text(encoding="utf-8")
    new = EMOJI.sub("", t)
    new = re.sub(r"(<(?:h[1-6]|button|span|label|a|option|strong|p|div)[^>]*>)\s{2,}", r"\1", new)
    new = re.sub(r">\s{2,}([^<\s])", r"> \1", new)
    new = re.sub(r"([^\s>])\s{2,}([^<\s])", r"\1 \2", new)
    # indigo → sky
    new = new.replace("rgba(99,102,241,.35)", "rgba(91,156,245,.35)")
    new = new.replace("rgba(99,102,241,.08)", "rgba(91,156,245,.08)")
    new = new.replace("rgba(99,102,241,.15)", "var(--accent-dim)")
    new = new.replace("#a5b4fc", "var(--accent)")
    new = new.replace("#6b7eff", "#5b9cf5")
    # Inter → Jakarta
    new = new.replace(
        "family=Inter:wght@400;600;700",
        "family=Plus+Jakarta+Sans:wght@400;500;600;700",
    )
    new = new.replace(
        "family=Inter:wght@400;500;600;700",
        "family=Plus+Jakarta+Sans:wght@400;500;600;700",
    )
    new = new.replace('"Inter"', '"Plus Jakarta Sans"')
    new = new.replace("Inter, ", '"Plus Jakarta Sans", ')
    if new != t:
        p.write_text(new, encoding="utf-8")
        print("ok", rel, abs(len(t) - len(new)))
    else:
        print("noop", rel)

for f in [
    "templates/settings.html",
    "templates/soon.html",
    "templates/mini_base.html",
    "templates/reader.html",
    "templates/cloud.html",
]:
    scrub_file(f)

print("done")
