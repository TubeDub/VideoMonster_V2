#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.translation_naturalizer import polish_segment_detailed
from engines.professional_dubbing.prosody import build_prosody_plan

orig5 = (
    "he just didn't get his son's obsession with cars, like why aren't you able to take "
    "that focus and apply it to other things, so we'll get your real job. And so basically "
    "every dinner these days, if he came this huge argument"
)
raw5 = (
    "просто не зрозумів одержимість свого сина автомобілями, наприклад, чому ви не можете "
    "зосередитись і застосувати це до інших речей, тож ми отримаємо твою справжню роботу. "
    "І так практично кожного обіду в ці дні, якщо він прийшов, ця велика"
)
r5 = polish_segment_detailed(raw5, original=orig5, tgt_lang="uk")
print("SEG5:", r5.text)
assert "отримаєш справжню роботу" in r5.text, r5.text
assert "суперечку між батьком і сином" in r5.text, r5.text

orig6 = "between father and son. And so George, he came to this intersection"
raw6 = "суперечка між батьком і сином. І тому Джордж, він підійшov до цього перехрестя"
raw6 = raw6.replace("підійшov", "підійшов")
r6 = polish_segment_detailed(raw6, original=orig6, tgt_lang="uk")
print("SEG6:", r6.text)
assert not r6.text.lower().startswith("суперечка"), r6.text

p = build_prosody_plan(r5.text, segment_ms=12720, lang="uk")
print("accents:", p.accents)
assert any("справжн" in str(a.get("word", "")) for a in p.accents)
assert "emphasis" in p.text_for_tts
print("All OK")
