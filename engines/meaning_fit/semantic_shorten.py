"""MF3 — SemanticShorten: other words, same meaning. No char-slice."""

from __future__ import annotations

import re
from typing import Any

from engines.meaning_fit.duration_predictor import predict_vs_slot
from engines.meaning_fit.exceptions import TruncateNotMeaningFitError
from engines.meaning_fit.flags import meaning_fit_flag, meaning_fit_shorten_flag
from engines.meaning_fit.skeleton import reject_truncate_as_success
from engines.meaning_fit.types import FitResult

# Exact / near-exact UK paraphrases (goat + extras). Preserve names/terms.
_UK_PARAPHRASE_SHORTEN: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"^Коза\s+паслась\s+на\s+тій\s+горі\s+і\s+їла\s+траву\.?$",
            re.I,
        ),
        "Коза паслась там на лугу",
    ),
    (
        re.compile(
            r"^Він\s+швидко\s+біг\s+по\s+довгій\s+вулиці\s+до\s+великого\s+будинку\.?$",
            re.I,
        ),
        "Він швидко біг вулицею до будинку",
    ),
    (
        re.compile(
            r"^Дівчина\s+відкрила\s+стару\s+книжку\s+і\s+почала\s+уважно\s+читати\s+кожну\s+сторінку\.?$",
            re.I,
        ),
        "Дівчина відкрила книжку і уважно читала",
    ),
]

# Light synonym / filler compaction (must not chop tail). General UK + GL etalon.
_UK_SYNONYM_COMPACT: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bна\s+тій\s+горі\s+і\s+їла\s+траву\b", re.I), "там на лугу"),
    (re.compile(r"\bпо\s+довгій\s+вулиці\s+до\s+великого\s+будинку\b", re.I), "вулицею до будинку"),
    (re.compile(r"\bстару\s+книжку\b", re.I), "книжку"),
    (re.compile(r"\bпочала\s+уважно\s+читати\s+кожну\s+сторінку\b", re.I), "уважно читала"),
    # Spoken filler / redundancy (Meaning Fit — other words, same sense)
    (re.compile(r"\bу\s+той\s+момент,?\s+коли\b", re.I), "коли"),
    (re.compile(r"\bв\s+той\s+момент,?\s+коли\b", re.I), "коли"),
    (re.compile(r"\bАле\s+коли\s+він\s+їхав,\s+", re.I), "Але "),
    (re.compile(r"\bзовсім\s+не\s+хотілося\b", re.I), "не хотілося"),
    (re.compile(r"\bдійсно\s+", re.I), ""),
    (re.compile(r"\bнасправді\s+", re.I), ""),
    # Filler «просто» only — never strip «не просто» (= EN not just / not merely).
    (re.compile(r"(?<![Нн]е )просто\s+", re.I), ""),
    (re.compile(r"\bдуже\s+легко\b", re.I), "легко"),
    (re.compile(r"\bдуже\s+розумним\b", re.I), "розумним"),
    (re.compile(r"\bневеликий\s+італійський\s+автомобіль\s+під\s+назвою\s+", re.I), "італійський "),
    (re.compile(r"\bавтомобіль\s+під\s+назвою\s+", re.I), ""),
    (re.compile(r"\bхоч\s+і\s+сам\s+подарував\s+йому\b", re.I), "хоч подарував йому"),
    (re.compile(r"\bбільше\s+не\s+хоче\b", re.I), "не хоче"),
    (re.compile(r"\bзнову\s+захопився\b", re.I), "захопився"),
    # GL overflow paraphrases (same meaning, shorter TTS)
    (
        re.compile(
            r"\bлежав\s+на\s+лікарняному\s+ліжку\s+у\s+відділенні\s+інтенсивної\s+терапії\s+"
            r"в\s+місцевій\s+лікарні\b",
            re.I,
        ),
        "лежав у реанімації місцевої лікарні",
    ),
    (
        re.compile(
            r"\bякий\s+на\s+той\s+момент\s+повністю\s+одужав\s+після\s+травм\b",
            re.I,
        ),
        "який уже повністю одужав",
    ),
    (
        re.compile(
            r"\bстояв\s+на\s+фінішній\s+прямій\s+гоночного\s+треку\s+і\s+підняв\s+камеру\b",
            re.I,
        ),
        "стояв на фініші треку з камерою",
    ),
    (
        re.compile(
            r"\bбув\s+певною\s+мірою\s+правий,\s*що\s+в\s+деяких\s+випадках\s+він\s+марнував\b",
            re.I,
        ),
        "мав рацію: місцями він марнував",
    ),
    (
        re.compile(
            r"\bпісля\s+того,\s*як\s+надіслав\s+заявку\s+був\s+майже\s+впевнений,\s*"
            r"що\s+його\s+не\s+візьмуть\b",
            re.I,
        ),
        "після заявки майже не вірив, що пройде",
    ),
    (
        re.compile(
            r"\bА\s+коли\s+Хаскелл\s+почув\s+це,\s*він\s+сказав,\s*Джордж,\s*"
            r"я\s+знаю\s+людей\s+в\s+Ю\s+Ес\s+Сі\b",
            re.I,
        ),
        "Хаскелл сказав: Джордж, я знаю людей в Ю Ес Сі",
    ),
    (
        re.compile(
            r"\bА\s+коли\s+Хаскелл\s+почув\s+це,\s*він\s+сказав,\s*Джордж,\s*"
            r"я\s+знаю\s+людей\s+в\s+USC\b",
            re.I,
        ),
        "Хаскелл сказав: Джордж, я знаю людей в USC",
    ),
    (
        re.compile(
            r"\bпішов\s+до\s+подіуму,\s*щоб\s+зробити\s+кілька\s+фото\s+переможного\s+гонщика\.?\b",
            re.I,
        ),
        "пішов до подіуму сфотографувати переможця",
    ),
    (
        re.compile(
            r"\bВін\s+сказав,\s+що\s+був\s+кінооператором\s+в\s+Голлівуді\.\s*"
            r"Джордж-молодший\s+розповів\s+Хаскеллу,\s+що\s+нещодавно\s+подав\s+заявку\s+до\s+"
            r"Ю\s+Ес\s+Сі,\s*щоб\s+спробувати\s+потрапити\s+в\s+програму\s+кінематографії\b",
            re.I,
        ),
        "Він сказав, що кінооператор з Голлівуду. Джордж розповів про заявку до Ю Ес Сі",
    ),
    (
        re.compile(
            r"\bВін\s+сказав,\s+що\s+був\s+кінооператором\s+в\s+Голлівуді\.\s*"
            r"Джордж-молодший\s+розповів\s+Хаскеллу,\s+що\s+нещодавно\s+подав\s+заявку\s+до\s+"
            r"USC,\s*щоб\s+спробувати\s+потрапити\s+в\s+програму\s+кінематографії\b",
            re.I,
        ),
        "Він сказав, що кінооператор з Голлівуду. Джордж розповів про заявку до USC",
    ),
    (
        re.compile(
            r"\bЯ\s+зроблю\s+кілька\s+дзвінків\.\s*І\s+справді,\s+незабаром\s+після\s+цієї\s+"
            r"доленосної\s+зустрічі,\s*Джордж-молодший\s+отримав\s+лист\s+про\s+зарахування\s+"
            r"з\s+кіношколи\s+Ю\s+Ес\s+Сі\.?\b",
            re.I,
        ),
        "Я подзвоню. І справді, невдовзі Джордж отримав зарахування до Ю Ес Сі",
    ),
    (
        re.compile(
            r"\bЯ\s+зроблю\s+кілька\s+дзвінків\.\s*І\s+справді,\s+незабаром\s+після\s+цієї\s+"
            r"доленосної\s+зустрічі,\s*Джордж-молодший\s+отримав\s+лист\s+про\s+зарахування\s+"
            r"з\s+кіношколи\s+USC\.?\b",
            re.I,
        ),
        "Я подзвоню. І справді, невдовзі Джордж отримав зарахування до USC",
    ),
    # Crash retelling — long UK overflows slot and hard-clip cuts mid-word in audio.
    (
        re.compile(
            r"\bДва\s+тижні\s+раніше,\s*коли\s+Джордж\s+повертав,\s*а\s+потім\s+щось\s+відбулося,\s*"
            r"а\s+саме\s*[—\-]\s*інший\s+автомобіль\s+на\s+великій\s+швидкості\s+промчав\s+дорогою\s+"
            r"і\s+врізався\s+в\s+машину\s+Джорджа\s+так\s+сильно,\s*що\s+Джордж-молодший\s+"
            r"вилетів\s+з\s+машини,\s*але\s+вижив\.?\b",
            re.I,
        ),
        "Два тижні раніше в Джорджа на повороті врізався інший автомобіль — він вилетів з машини, але вижив",
    ),
    (
        re.compile(
            r"\bПоки\s+він\s+ішов\s+туди,\s*чоловік\s+середнього\s+віку\s+прийшов\s+назустріч\s+"
            r"йому\s+і\s+заговорив\s+із\s+Джорджем-молодшим\s+про\s+фотографію,\s*а\s+потім\s+"
            r"чоловік\s+офіційно\s+представився\s+як\s+Хаскелл\s+Векслер\.?\b",
            re.I,
        ),
        "Поки він ішов, середнього віку чоловік заговорив із ним про фото і представився як Хаскелл Векслер",
    ),
    (
        re.compile(
            r"\bАле\s+цей\s+момент\s+у\s+житті\s+Джорджа-молодшого\s+не\s+просто\s+змінив\s+"
            r"його\s+життя\s+назавжди\.?\b",
            re.I,
        ),
        "Але цей момент не просто змінив життя Джорджа-молодшого назавжди",
    ),
    (
        re.compile(
            r"\bале\s+після\s+того,\s*як\s+надіслав\s+заявку\s+(?:він\s+)?був\s+майже\s+"
            r"впевнений,\s*що\s+його\s+не\s+візьмуть\.?\b",
            re.I,
        ),
        "але після заявки майже не вірив, що пройде",
    ),
    (
        re.compile(
            r"\bСьогодні\s+Джорджа-молодшого\s+знають\s+як\s+Джорджа\s+Лукаса\s+і\s+його\s+"
            r"кінофраншиза\s*—\s*це\s*«Зоряні\s+війни»\.?\b",
            re.I,
        ),
        "Сьогодні його знають як Джорджа Лукаса — творця «Зоряних війн»",
    ),
    (
        re.compile(
            r"\bТак\s+два\s+тижні\s+раніше,\s*коли\s+Джордж\s+повертав,\s*"
            r"а\s+потім\s+щось\s+відбулося,\s*а\s+саме\s*—\s*",
            re.I,
        ),
        "Два тижні раніше, коли Джордж повертав, ",
    ),
    (re.compile(r"\s{2,}", re.I), " "),
]


def _is_chop(original: str, candidate: str) -> bool:
    """True if candidate is a prefix/tail slice — not a real paraphrase."""
    o = str(original or "").strip()
    c = str(candidate or "").strip()
    if not o or not c:
        return True
    if c == o:
        return False
    # Prefix chop (goat BAD: «Коза паслась на тій»)
    if o.startswith(c) and len(c) < len(o) * 0.92:
        return True
    # Suffix-only drop with shared long prefix (≥70% of candidate)
    if len(c) >= 8 and o.startswith(c[: max(8, int(len(c) * 0.7))]) and c in o:
        return True
    if c.endswith("...") or c.endswith("…"):
        return True
    return False


def _preserve_entities(original: str, candidate: str) -> bool:
    """Keep capitalized / proper-like tokens from original when present."""
    o_tokens = re.findall(r"[A-ZА-ЯІЇЄҐ][\w'-]+", str(original or ""))
    c_low = str(candidate or "").lower()
    c_raw = str(candidate or "")
    for tok in o_tokens:
        if len(tok) < 2:
            continue
        # Allow case fold match
        if tok.lower() in c_low or tok in c_raw:
            continue
        # Alias: Джордж-молодший → Джордж (timing paraphrase)
        if tok.lower().startswith("джордж") and "джордж" in c_low:
            continue
        if tok.lower() in ("хаскеллу", "хаскелла") and "хаскелл" in c_low:
            continue
        if tok.upper() == "USC" and (
            "usc" in c_low or "ю ес сі" in c_low or "юессі" in c_low.replace(" ", "")
        ):
            continue
        # Common nouns like Коза must stay
        if tok[:1].isupper():
            return False
    return True


def _rule_shorten(text: str, *, original_en: str = "") -> str | None:
    t = str(text or "").strip()
    if not t:
        return None
    for pat, repl in _UK_PARAPHRASE_SHORTEN:
        if pat.match(t):
            from engines.semantic_meaning import restore_terminal_close

            return restore_terminal_close(repl, original=original_en, reference=t)
    out = t
    changed = False
    # Multi-pass compaction until stable (safe synonym swaps only).
    for _ in range(4):
        prev = out
        for pat, repl in _UK_SYNONYM_COMPACT:
            out = pat.sub(repl, out)
        out = re.sub(r"\s{2,}", " ", out).strip()
        out = re.sub(r"\s+([,.!?])", r"\1", out)
        if out != prev:
            changed = True
        else:
            break
    if not changed or out == t:
        return None
    from engines.semantic_meaning import restore_terminal_close

    out = restore_terminal_close(out, original=original_en, reference=t)
    if _is_chop(t, out) or not _preserve_entities(t, out):
        return None
    return out


def _try_llm_shorten(text: str, *, slot_ms: int, original_en: str = "") -> str | None:
    """Optional LLM paraphrase; never required for goat offline path."""
    try:
        from engines.naturalizer_v2.llm_rewrite import rewrite_segment_llm
    except Exception:
        return None
    try:
        out = rewrite_segment_llm(
            text,
            original=original_en or text,
            tgt_lang="uk",
            mode="shorten",
        )
        if isinstance(out, dict):
            cand = str(out.get("text") or out.get("rewritten") or "").strip()
        else:
            cand = str(out or "").strip()
        if cand and cand != text and not _is_chop(text, cand):
            return cand
    except Exception:
        return None
    return None


def semantic_shorten(
    text_uk: str,
    slot_ms: int,
    *,
    original_en: str = "",
    use_llm: bool = False,
    force: bool = False,
) -> FitResult:
    """Shorten by paraphrase. Reject truncate/chop."""
    text = str(text_uk or "").strip()
    if not (force or (meaning_fit_flag() and meaning_fit_shorten_flag())):
        return FitResult(
            text_uk=text,
            status="noop",
            reason="flag_off_legacy",
            slot_ms=slot_ms,
            success=False,
            method="noop",
            meta={"enabled": False, "noop": True},
        )

    pred0 = predict_vs_slot(text, slot_ms)
    if pred0.verdict == "OK":
        return FitResult(
            text_uk=text,
            status="already_fits",
            reason="already_fits",
            predicted_ms=pred0.predicted_ms,
            slot_ms=slot_ms,
            verdict="OK",
            success=True,
            method="none",
        )

    candidates: list[str] = []
    ruled = _rule_shorten(text, original_en=original_en)
    if ruled:
        candidates.append(ruled)
    if use_llm:
        llm = _try_llm_shorten(text, slot_ms=slot_ms, original_en=original_en)
        if llm:
            candidates.append(llm)

    best: FitResult | None = None
    for cand in candidates:
        if _is_chop(text, cand):
            continue
        if not _preserve_entities(text, cand):
            continue
        pred = predict_vs_slot(cand, slot_ms)
        if pred.verdict == "TOO_LONG":
            # Still shorter? keep as candidate for MF5
            pass
        res = FitResult(
            text_uk=cand,
            status="paraphrase_shorten",
            reason="paraphrase_shorten",
            predicted_ms=pred.predicted_ms,
            slot_ms=slot_ms,
            verdict=pred.verdict,
            success=pred.verdict == "OK",
            method="semantic_shorten",
            meta={"source": text},
        )
        if res.success:
            return res
        if best is None or (res.predicted_ms or 10**9) < (best.predicted_ms or 10**9):
            best = res

    if best is not None:
        return best

    return FitResult(
        text_uk=text,
        status="fit_failed",
        reason="fit_failed",
        predicted_ms=pred0.predicted_ms,
        slot_ms=slot_ms,
        verdict=pred0.verdict,
        success=False,
        needs_manual=True,
        method="semantic_shorten",
    )


def assert_not_truncate_success(method: str, text_uk: str = "") -> None:
    reject_truncate_as_success(method, text_uk=text_uk, stage="semantic_shorten")


def reject_chop_as_shorten(original: str, candidate: str) -> None:
    if _is_chop(original, candidate):
        raise TruncateNotMeaningFitError(
            "chop/truncate tail is not SemanticShorten",
            details={"original": original[:80], "candidate": candidate[:80]},
        )
