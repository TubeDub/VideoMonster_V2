# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(r"C:\Users\serhii\Desktop\VideoMonster_V2")
TOKENS = [
    "🔑", "🔒", "📦", "🌐", "🧠", "🧹", "💾", "📖", "✅", "❌", "⚠️",
    "📴", "🗂", "📁", "📂", "🎬", "🎙️", "🎤", "🔊", "🌍", "⚙️", "☁️",
    "⚡", "✨", "🧩", "📡", "🎵", "🗣️", "🖥️", "🔬", "⭐", "🧪", "🔧",
    "🎨", "🤖", "📝", "🔍", "🏠", "🆓", "🛠", "🏗", "🚩", "📥", "🛍️",
    "🎙", "🎧", "⛩", "💡", "🕐", "🔄", "📋", "🗑", "🗑️", "⌨️", "⌨",
    "🐍", "❓", "🟡", "📨", "🎛", "🎛️", "🚀", "▶", "⏱", "⏳",
]

FILES = [
    "templates/settings.html",
    "templates/index.html",
    "templates/dub.html",
    "templates/voice.html",
    "templates/studio.html",
    "templates/translate.html",
    "templates/projects.html",
    "templates/soon.html",
    "templates/pipeline_dev.html",
    "templates/mini_base.html",
    "templates/reader.html",
    "templates/download_center.html",
]

for rel in FILES:
    p = ROOT / rel
    if not p.exists():
        continue
    t = p.read_text(encoding="utf-8")
    n = 0
    for tok in TOKENS:
        c = t.count(tok)
        if c:
            t = t.replace(tok, "")
            n += c
    if n:
        # tidy double spaces in text nodes lightly
        while "  " in t and "  " in t[t.find("  ")-5:t.find("  ")+20] if "  " in t else False:
            break
        p.write_text(t, encoding="utf-8")
        print(f"{rel}: removed {n}")
    else:
        print(f"{rel}: clean")
