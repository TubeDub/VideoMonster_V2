# -*- coding: utf-8 -*-
from pathlib import Path
import re

ROOT = Path(r"C:\Users\serhii\Desktop\VideoMonster_V2")

FILES = [
    "templates/platform.html",
    "templates/cloud.html",
    "templates/plugins.html",
    "templates/director.html",
    "templates/studio.html",
    "templates/pipeline_dev.html",
    "templates/error.html",
    "templates/ai_settings.html",
    "templates/dev_pipeline.html",
    "templates/dev_architecture.html",
    "templates/dub_studio.html",
    "templates/ai_sources.html",
    "templates/monitoring.html",
    "templates/soon.html",
]


def clean(text: str) -> str:
    # leading space after opening heading/button after emoji removal
    text = re.sub(r"(<(?:h[1-6]|button|span|a|strong|p|label)[^>]*>)\s+", r"\1", text)
    text = re.sub(r"(<(?:h[1-6]|button)[^>]*>)[ \t]+", r"\1", text)
    return text


for rel in FILES:
    p = ROOT / rel
    if not p.exists():
        continue
    old = p.read_text(encoding="utf-8")
    new = clean(old)
    if new != old:
        p.write_text(new, encoding="utf-8")
        print("cleaned", rel)
    else:
        print("ok", rel)

# tip / recovery icon CSS in index
idx = ROOT / "templates" / "index.html"
t = idx.read_text(encoding="utf-8")
if ".tip-icon { font-size: 16px;" in t and ".tip-icon svg" not in t:
    t = t.replace(
        ".tip-icon { font-size: 16px; flex-shrink: 0; margin-top: 1px; opacity: .75; }",
        ".tip-icon { flex-shrink: 0; margin-top: 1px; color: var(--accent); opacity: .9; }\n"
        ".tip-icon.ui-ico { width: 18px; height: 18px; }\n"
        ".recovery-icon.ui-ico { width: 20px; height: 20px; color: var(--accent); }",
    )
    idx.write_text(t, encoding="utf-8")
    print("index tip css")

# platform card density
plat = ROOT / "templates" / "platform.html"
pt = plat.read_text(encoding="utf-8")
pt2 = pt.replace("<h1> AI Media Platform</h1>", "<h1>AI Media Platform</h1>")
pt2 = re.sub(r"<h3>\s+", "<h3>", pt2)
if pt2 != pt:
    plat.write_text(pt2, encoding="utf-8")
    print("platform titles")

print("done")
