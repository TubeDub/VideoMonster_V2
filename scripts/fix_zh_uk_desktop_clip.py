"""Repair zh→uk desktop clip: merge tight ASR cues + curated UK + Ostap TTS."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC_VIDEO = Path(r"C:\Users\serhii\Desktop\video_2026-06-24_19-32-02.mp4")
OUT_UK = Path(r"C:\Users\serhii\Desktop\video_2026-06-24_19-32-02_UK.mp4")
ASR_JSON = ROOT / "_tmp_uk_fix" / "asr_zh.json"
WORK = ROOT / "_tmp_uk_fix" / "rebuild"
VOICE = "uk-UA-OstapNeural"
DURATION_MS = 68333

_ASR_FIXES = {
    "我们陆家八代单纯": "我们陆家八代单传",
    "自私担保": "子嗣难保",
    "要是能一举得难": "要是能一举得男",
    "这是我一身的": "这是我一生的",
    "无论是人是你": "无论是男是女",
}

# Dialogue turns after merge (ZH joined) → UK
_TURN_UK = [
    (
        "我们陆家八代单传 子嗣难保 如今啊 你怀孕了 陆家有后了 要是能一举得男 那就更完美了",
        "У родині Лу вісім поколінь — один спадкоємець, рід ледь тримається. А тепер ти вагітна — у Лу нарешті є спадкоємець. Якби ще народився хлопчик — було б ідеально.",
    ),
    ("妈", "Мамо."),
    (
        "这是我一生的 无论是男是女 我都喜欢",
        "Це на все моє життя. Хоч хлопчик, хоч дівчинка — я любитиму.",
    ),
    ("喜欢哥哥", "Люблю братика."),
    (
        "我怀孕了 是一个月之前的一场意外",
        "Я вагітна. Це через випадок місяць тому.",
    ),
    (
        "一个月前 我和曲妃妃同时被绑架 那晚她迎合绑匪 所以 这个孩子是绑匪的",
        "Місяць тому нас із Цюй Фейфей викрали. Вона піддалась викрадачеві — дитина від нього.",
    ),
    (
        "一个月之前的意外 是那个绑架 这孩子是绑匪的",
        "Той випадок — викрадення. Дитина від викрадача.",
    ),
]


def _fix_zh(t: str) -> str:
    t = str(t or "").strip()
    return _ASR_FIXES.get(t, t)


def _merge_raw(raw: list[dict]) -> list[dict]:
    """Merge ASR cues with tiny gaps into speaking turns."""
    fixed = []
    for s in raw:
        zh = _fix_zh(s["text"])
        if not zh:
            continue
        fixed.append({"start": float(s["start"]), "end": float(s["end"]), "text": zh})

    if not fixed:
        return []

    break_before = {"妈", "喜欢哥哥", "我怀孕了", "一个月前", "一个月之前的意外"}
    turns: list[dict] = []
    cur = dict(fixed[0])
    for s in fixed[1:]:
        gap = s["start"] - cur["end"]
        force_break = s["text"] in break_before or cur["text"] in {"妈"}
        if gap > 0.45 or force_break:
            turns.append(cur)
            cur = dict(s)
        else:
            cur["end"] = max(cur["end"], s["end"])
            cur["text"] = f"{cur['text']} {s['text']}"
    turns.append(cur)
    return turns


def _assign_uk(turns: list[dict]) -> list[dict]:
    out = []
    for t in turns:
        key = " ".join(t["text"].split())
        uk = None
        for zh_pat, uk_line in _TURN_UK:
            if key == zh_pat:
                uk = uk_line
                break
        if uk is None:
            best = None
            best_score = 0
            toks = set(key.split())
            for zh_pat, uk_line in _TURN_UK:
                score = len(toks & set(zh_pat.split()))
                if score > best_score:
                    best_score = score
                    best = uk_line
            uk = best or key
        # Keep mother opening shorter for timing.
        if key.startswith("我们陆家八代单传"):
            uk = (
                "У родині Лу вісім поколінь — один спадкоємець. "
                "А тепер ти вагітна — у Лу є спадкоємець. "
                "Якби ще хлопчик — було б ідеально."
            )
        out.append({**t, "uk": uk})
    return out


def main() -> int:
    from engines.timing_engine import build_timed_audio
    from engines.tts import generate_audio, get_output_path

    raw = json.loads(ASR_JSON.read_text(encoding="utf-8"))
    turns = _assign_uk(_merge_raw(raw))
    WORK.mkdir(parents=True, exist_ok=True)

    # Stretch ends into following silence when next turn is short / late.
    for i, t in enumerate(turns):
        if i + 1 < len(turns):
            nxt = turns[i + 1]
            # Borrow up to half the next gap if next line is short
            borrow = 0.0
            nxt_span = max(0.1, nxt["end"] - nxt["start"])
            if len(str(nxt.get("uk") or "")) < 40 and nxt_span > 4:
                borrow = min(6.0, nxt_span * 0.45)
                nxt["start"] = min(nxt["end"] - 1.2, nxt["start"] + borrow)
            t["end"] = max(t["end"], min(nxt["start"] - 0.12, t["start"] + 14))
        else:
            t["end"] = min(DURATION_MS / 1000.0, max(t["end"], t["start"] + 4))

    # Soft-compress UK for tight early slots
    for t in turns:
        span = t["end"] - t["start"]
        uk = str(t["uk"])
        if span < 5.5 and len(uk) > 90:
            t["uk"] = uk.replace(" — ", ". ").replace(", ", ". ")

    for t in turns:
        print(f"{t['start']:.2f}-{t['end']:.2f} | {t['text']}\n  -> {t['uk']}")

    (WORK / "turns_uk.json").write_text(
        json.dumps(turns, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    files = generate_audio(
        text="",
        voice=VOICE,
        segments=[t["uk"] for t in turns],
        rate="+20%",
        output_dir=WORK,
        task_id="zhukfix4",
    )
    paths = []
    for fn in files:
        p = WORK / fn
        if not p.exists():
            alt = get_output_path(fn)
            if alt.exists():
                shutil.copy2(alt, p)
        paths.append(p)

    timing_map = [
        {"start": int(t["start"] * 1000), "end": int(t["end"] * 1000)} for t in turns
    ]
    final_audio, warnings = build_timed_audio(
        paths, timing_map, mode="exact", target_duration_ms=DURATION_MS
    )
    overflows = [w for w in warnings if "Превышение" in w]
    print("overflows", len(overflows), "/", len(turns))

    dub_wav = WORK / "dub_full.wav"
    final_audio.export(str(dub_wav), format="wav")
    tmp_out = WORK / "out_uk.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(SRC_VIDEO),
            "-i",
            str(dub_wav),
            "-c:v",
            "copy",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            str(tmp_out),
        ],
        check=True,
        capture_output=True,
    )
    shutil.copy2(tmp_out, OUT_UK)
    print("WROTE", OUT_UK, OUT_UK.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
