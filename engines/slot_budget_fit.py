"""Slot budget fit — одна задача: ужать или развернуть фразу под слот.

Класть в проект как engines/slot_budget_fit.py
Вызывать ДО TTS и ДО translation_locked.
Модуль не озвучивает, не меняет segment_id и не пишет чужой текст в сегмент.

Работает без LLM. Если передан llm_call — использует его только как переписчик
одной фразы и всё равно проверяет бюджет сам.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# Эта строка должна быть видна в отчёте дубляжа и в логе.
# Если её нет — файл не вызывался.
VISIBLE_MARK = "SLOT_BUDGET_FIT работает"
MODULE_VERSION = "2026-10-02"

# Спокойная речь uk/ru: ~14 знаков в секунду. Не магическое число пайплайна —
# только потолок для решения «ужать / склеить / отдать в TTS».
CPS = 14.0
OVERFLOW_RATIO = 1.15
UNDERFILL_RATIO = 0.75
MIN_SLOT_MS = 800
HARD_OVERFLOW_MS = 1500

_FILLER = (
    "действительно",
    "насправді",
    "в тот момент",
    "у той момент",
    "в цей момент",
    "так сказать",
    "как бы",
    "просто",
    "просто-напросто",
    "буквально",
    "фактически",
    "фактично",
    "в общем",
    "загалом",
    "по сути",
    "по суті",
    "очень",
    "дуже",
    "совершенно",
    "цілком",
    "на самом деле",
    "насправді",
)


@dataclass
class SlotFit:
    segment_id: str
    text: str
    fit: str  # ok | shorten | expand | merge_needed
    reason: str
    slot_ms: int
    predicted_ms: int
    max_chars: int
    source_untouched: str

    def as_dict(self) -> dict:
        return asdict(self)


def max_chars(slot_ms: int) -> int:
    if slot_ms <= 0:
        return 0
    return max(8, int(slot_ms / 1000.0 * CPS))


def predict_ms(text: str) -> int:
    clean = _plain(text)
    if not clean:
        return 0
    return int(len(clean) / CPS * 1000)


def _plain(text: str) -> str:
    text = re.sub(r"[\u0301`´]", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", _plain(text).lower(), flags=re.UNICODE)


def _drop_filler(text: str) -> str:
    out = _plain(text)
    for phrase in sorted(_FILLER, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(phrase)}\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([.!?])", r"\1", out)
    return out.strip(" ,")


def _drop_aside(text: str) -> str:
    out = re.sub(r"\([^)]*\)", "", text)
    out = re.sub(r",\s*(который|який|що|что)\s+[^,.]{0,80}", "", out, flags=re.IGNORECASE)
    return _plain(out)


def _keep_entities(source: str, candidate: str) -> bool:
    """Имена и числа из черновика нельзя потерять при ужатии."""
    nums = set(re.findall(r"\d+", source))
    if nums and not nums.issubset(set(re.findall(r"\d+", candidate))):
        return False
    names = set(re.findall(r"\b[A-ZА-ЯІЇЄҐ][\w’'-]{2,}", source))
    cand = candidate
    for name in names:
        if name.lower() not in cand.lower():
            return False
    return True


def _rule_shorten(text: str, limit: int) -> str:
    cur = _plain(text)
    if len(cur) <= limit:
        return cur
    cur = _drop_filler(cur)
    if len(cur) <= limit:
        return cur
    cur = _drop_aside(cur)
    if len(cur) <= limit:
        return cur
    # Режем по предложениям с конца, пока не влезем. Первое предложение оставляем.
    parts = re.split(r"(?<=[.!?])\s+", cur)
    while len(parts) > 1 and len(" ".join(parts)) > limit:
        parts.pop()
    cur = " ".join(parts).strip()
    if len(cur) <= limit:
        return cur
    # Жёсткий потолок по словам, без обрыва посередине слова.
    words = cur.split()
    acc = []
    for w in words:
        trial = (" ".join(acc + [w])).strip()
        if len(trial) > limit:
            break
        acc.append(w)
    return " ".join(acc).strip() or words[0]


def _llm_prompt(source: str, draft: str, slot_ms: int, lang: str) -> str:
    limit = max_chars(slot_ms)
    return (
        "Ты подгоняешь одну реплику дубляжа под время. "
        "Не озвучивай. Не добавляй фактов. Не трогай другие сегменты.\n"
        f"Язык: {lang}\n"
        f"Слот: {slot_ms} мс. Потолок: {limit} знаков.\n"
        f"Оригинал: {source}\n"
        f"Черновик: {draft}\n"
        "Верни только готовую фразу короче потолка. "
        "Имена, числа и кто кому что сделал сохрани. "
        "Если фразу нельзя сказать за это время без потери смысла — верни ровно MERGE."
    )


def fit_line(
    *,
    segment_id: str,
    source: str,
    draft: str,
    slot_ms: int,
    lang: str = "uk",
    llm_call=None,
) -> SlotFit:
    """Подогнать одну фразу. llm_call(prompt) -> str, необязателен."""
    draft = _plain(draft)
    source = _plain(source)
    limit = max_chars(slot_ms)
    predicted = predict_ms(draft)
    words = _words(draft)

    if slot_ms < MIN_SLOT_MS and (len(words) >= 8 or predicted > slot_ms + 400):
        return SlotFit(
            segment_id=segment_id,
            text=draft,
            fit="merge_needed",
            reason=f"slot {slot_ms} ms слишком короткий для {len(words)} слов",
            slot_ms=slot_ms,
            predicted_ms=predicted,
            max_chars=limit,
            source_untouched=source,
        )

    if predicted > max(slot_ms * OVERFLOW_RATIO, slot_ms + 200):
        candidate = draft
        reason = "rule shorten"
        if llm_call is not None:
            try:
                raw = _plain(llm_call(_llm_prompt(source, draft, slot_ms, lang)))
            except Exception:
                raw = ""
            if raw.upper() == "MERGE":
                return SlotFit(
                    segment_id=segment_id,
                    text=draft,
                    fit="merge_needed",
                    reason="llm asked merge",
                    slot_ms=slot_ms,
                    predicted_ms=predicted,
                    max_chars=limit,
                    source_untouched=source,
                )
            if raw and _keep_entities(draft, raw) and len(raw) <= int(limit * 1.05):
                candidate = raw
                reason = "llm shorten"
        if candidate == draft or predict_ms(candidate) > slot_ms * OVERFLOW_RATIO:
            shortened = _rule_shorten(draft, limit)
            if shortened and _keep_entities(draft, shortened):
                candidate = shortened
                reason = "rule shorten"
            elif predict_ms(draft) - slot_ms > HARD_OVERFLOW_MS:
                return SlotFit(
                    segment_id=segment_id,
                    text=draft,
                    fit="merge_needed",
                    reason="нельзя ужать без потери имени или числа",
                    slot_ms=slot_ms,
                    predicted_ms=predicted,
                    max_chars=limit,
                    source_untouched=source,
                )
        return SlotFit(
            segment_id=segment_id,
            text=candidate,
            fit="shorten",
            reason=reason,
            slot_ms=slot_ms,
            predicted_ms=predict_ms(candidate),
            max_chars=limit,
            source_untouched=source,
        )

    if predicted < slot_ms * UNDERFILL_RATIO and slot_ms >= MIN_SLOT_MS:
        return SlotFit(
            segment_id=segment_id,
            text=draft,
            fit="expand",
            reason="фраза короче слота, разворачивать только без новых фактов",
            slot_ms=slot_ms,
            predicted_ms=predicted,
            max_chars=limit,
            source_untouched=source,
        )

    return SlotFit(
        segment_id=segment_id,
        text=draft,
        fit="ok",
        reason="в бюджете",
        slot_ms=slot_ms,
        predicted_ms=predicted,
        max_chars=limit,
        source_untouched=source,
    )


def status_line(rows: list[dict]) -> str:
    """Короткая строка для экрана, JSON и лога."""
    counts = {"ok": 0, "shorten": 0, "expand": 0, "merge_needed": 0}
    for row in rows:
        key = str(row.get("slot_budget_fit") or "")
        if key in counts:
            counts[key] += 1
    return (
        f"{VISIBLE_MARK} v{MODULE_VERSION}: "
        f"ужал {counts['shorten']}, "
        f"коротких {counts['expand']}, "
        f"склеить {counts['merge_needed']}, "
        f"уже влезло {counts['ok']}"
    )


def write_visible_log(line: str, log_path: str | None = None) -> str:
    """Пишет строку в output/slot_budget_fit.log рядом с программой."""
    path = Path(log_path) if log_path else Path("output") / "slot_budget_fit.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {line}\n")
    return str(path)


def fit_before_tts(segments: list[dict], *, lang: str = "uk", llm_call=None) -> list[dict]:
    """Сегменты не переставлять. Писать только в тот же segment_id.

    Ожидаемые поля: segment_id, original_text, translated_text, slot_ms.
    """
    out = []
    for seg in segments:
        sid = str(seg.get("segment_id") or "")
        result = fit_line(
            segment_id=sid,
            source=str(seg.get("original_text") or ""),
            draft=str(seg.get("translated_text") or seg.get("text") or ""),
            slot_ms=int(seg.get("slot_ms") or 0),
            lang=lang,
            llm_call=llm_call,
        )
        row = dict(seg)
        row["slot_budget_fit"] = result.fit
        row["slot_budget_reason"] = result.reason
        row["predicted_ms"] = result.predicted_ms
        if result.fit in ("ok", "shorten", "expand"):
            row["text_for_tts"] = result.text
        else:
            row["text_for_tts"] = ""
            row["needs_merge"] = True
        row["slot_budget_visible"] = VISIBLE_MARK
        row["algorithm_reason"] = f"{VISIBLE_MARK}: {result.fit} — {result.reason}"
        out.append(row)
    line = status_line(out)
    write_visible_log(line)
    if out:
        out[0]["slot_budget_status_line"] = line
    return out
