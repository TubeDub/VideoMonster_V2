"""
Universal Translation Pipeline for TubeDub.

Single entry for all language pairs:
  Whisper text → direct MT (src→tgt, no pivot) → meaning-preserving naturalization → TTS-ready text

Principles:
  - Meaning over literal word-for-word translation
  - Full-sentence context via merge groups + prev-line naturalization
  - Same code path for every supported language
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, List, Sequence

from engines.translation_quality_log import SegmentTranslationAudit, TranslationQualityLog

logger = logging.getLogger("tubedub.engines.translation_pipeline")


@dataclass
class PipelineResult:
    segments: list[str]
    audits: list[SegmentTranslationAudit] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


from engines.utils.lang_utils import normalize_lang as _normalize_lang


def _fill_empty_raw_translations(
    raw_by_index: list[str],
    source_segments: list[str],
    *,
    preserved_by_index: dict[int, str] | None = None,
    src_lang: str = "",
    tgt_lang: str = "",
) -> None:
    """Never leave MT slots empty when source or prior translation exists."""
    preserved = preserved_by_index or {}
    same_lang = _normalize_lang(src_lang) == _normalize_lang(tgt_lang) and bool(src_lang)
    for i in range(len(source_segments)):
        if str(raw_by_index[i] or "").strip():
            continue
        kept = str(preserved.get(i) or "").strip()
        if kept:
            raw_by_index[i] = kept
            continue
        src = str(source_segments[i] or "").strip()
        if src and same_lang:
            raw_by_index[i] = src


class UniversalTranslationPipeline:
    """
    Единый конвейер перевода для всех языков.
    Не содержит отдельных веток «только RU» / «только UK» — только общие правила
    + языковые полировщики через naturalize_text().
    """

    def __init__(self, app_dir=None, task_id: str = ""):
        from pathlib import Path

        self.app_dir = Path(app_dir) if app_dir else Path(__file__).resolve().parent.parent
        self.task_id = task_id
        self.quality_log = TranslationQualityLog(self.app_dir)

    def translate_segments(
        self,
        segments: List[str],
        timing_map: Sequence[Any],
        src_lang: str,
        tgt_lang: str,
        *,
        translate_meta_out: list | None = None,
        progress_cb: Any = None,
    ) -> PipelineResult:
        from engines.cleaner import align_segments_to_timing_map, split_by_timing_map
        from engines.translation import translate_text_traced
        from engines.translation_naturalizer import (
            fix_phantom_cross_segment_repeats,
            merge_segments_for_translation,
            polish_lines,
            _llm_api_key,
            _natural_translation_enabled,
        )

        if not segments:
            return PipelineResult(segments=[], audits=[], meta={})

        src = _normalize_lang(src_lang)
        tgt = _normalize_lang(tgt_lang)
        if src == tgt:
            stripped = [str(s).strip() for s in segments]
            return PipelineResult(
                segments=stripped,
                audits=[
                    SegmentTranslationAudit(
                        index=i,
                        source_lang=src,
                        target_lang=tgt,
                        whisper_text=str(segments[i]),
                        raw_translation=str(segments[i]),
                        naturalized_text=str(segments[i]),
                        final_text=str(segments[i]),
                        tts_text=str(segments[i]),
                        engine="none",
                        route="direct",
                    )
                    for i in range(len(segments))
                ],
                meta={"direct": True, "src": src, "tgt": tgt, "engines": []},
            )

        from engines.mt.stable_translate import ensure_marian_ready, use_stable_mt
        from engines.translation_manager import use_translation_manager
        from engines.translation_stage_log import log_end, log_segment, log_start

        stable = use_stable_mt()
        if stable:
            stage_engine = "marian"
        elif use_translation_manager():
            stage_engine = "translation_manager"
        else:
            stage_engine = "router"
        stage_route = f"{src}→{tgt}"

        log_start(
            self.app_dir,
            engine=stage_engine,
            route=stage_route,
            src_lang=src,
            tgt_lang=tgt,
            segment_count=len(segments),
            task_id=self.task_id,
            mode="stable" if stable else "router",
        )
        stage_t0 = time.perf_counter()
        stage_error = ""
        progress = {"n": 0, "err": ""}

        try:
            if stable:
                ensure_marian_ready(self.app_dir, src, tgt)
            else:
                from engines.translation_manager import (
                    ensure_manager_ready,
                    use_translation_manager,
                )

                if use_translation_manager():
                    ensure_manager_ready(self.app_dir, src, tgt)
            return self._translate_segments_body(
                segments,
                timing_map,
                src_lang,
                tgt_lang,
                src,
                tgt,
                stable,
                stage_engine,
                stage_route,
                progress,
                translate_meta_out=translate_meta_out,
                progress_cb=progress_cb,
            )
        except Exception as exc:
            stage_error = str(exc)
            progress["err"] = stage_error
            raise
        finally:
            log_end(
                self.app_dir,
                engine=stage_engine,
                route=stage_route,
                elapsed_sec=time.perf_counter() - stage_t0,
                translated_ok=progress.get("n", 0),
                segment_count=len(segments),
                task_id=self.task_id,
                error=stage_error or progress.get("err", ""),
            )

    def _translate_segments_body(
        self,
        segments: List[str],
        timing_map: Sequence[Any],
        src_lang: str,
        tgt_lang: str,
        src: str,
        tgt: str,
        stable: bool,
        stage_engine: str,
        stage_route: str,
        translated_ok_ref: dict,
        *,
        translate_meta_out: list | None = None,
        progress_cb: Any = None,
    ) -> PipelineResult:
        from engines.cleaner import align_segments_to_timing_map, split_by_timing_map
        from engines.translation import translate_text_traced
        from engines.translation_naturalizer import (
            fix_phantom_cross_segment_repeats,
            merge_segments_for_translation,
            polish_lines,
            _llm_api_key,
            _natural_translation_enabled,
        )
        from engines.translation_stage_log import log_segment

        source_segments = list(segments)
        entity_maps: list[dict[str, str]] = [{} for _ in segments]
        mt_segments = source_segments

        from engines.naturalizer_v2.config import entity_mask_enabled
        from engines.broadcast.config import use_broadcast_pipeline
        from engines.enterprise_translation.config import use_enterprise_translation

        if entity_mask_enabled() and not use_enterprise_translation() and not use_broadcast_pipeline():
            from engines.naturalizer_v2.entity_tokens import mask_segments

            mt_segments, entity_maps = mask_segments(source_segments, app_dir=self.app_dir)

        inspector_traces: list[dict[str, Any]] = []
        for i in range(len(segments)):
            emap_i = entity_maps[i] if i < len(entity_maps) else {}
            inspector_traces.append(
                {
                    "original": str(source_segments[i] or ""),
                    "preprocessed": str(source_segments[i] or ""),
                    "entities": sorted(set(emap_i.values())),
                    "entity_map": dict(emap_i),
                    "masked_text": str(mt_segments[i] if i < len(mt_segments) else source_segments[i] or ""),
                    "timing_ms": {"preprocessing": 0.0, "entity": 0.0},
                }
            )

        groups = merge_segments_for_translation(mt_segments, timing_map)
        _hp_batch = False
        try:
            from engines.happy_path import happy_path_batch_translate
            from engines.translation_naturalizer import (
                merge_segments_for_translation_happy_path as _merge_hp,
            )

            _hp_batch = bool(happy_path_batch_translate(task_id=self.task_id))
            if _hp_batch:
                groups = _merge_hp(mt_segments, timing_map)
                logger.info(
                    "happy_path batch MT: segments_before=%d → translate_groups=%d "
                    "adaptation_path=happy_path",
                    len(mt_segments),
                    len(groups),
                )
                try:
                    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

                    if self.task_id:
                        with STATE_LOCK:
                            _t = AUTO_TASKS.get(str(self.task_id))
                            if isinstance(_t, dict):
                                _info = _t.setdefault("info", {})
                                _info["mt_batch_mode"] = "happy_path"
                                _info["mt_batch_segments"] = len(mt_segments)
                                _info["mt_batch_groups"] = len(groups)
                                _info["adaptation_path"] = _info.get(
                                    "adaptation_path"
                                ) or "happy_path"
                except Exception:
                    pass
        except Exception as _hp_batch_exc:
            logger.debug("happy_path batch MT skipped: %s", _hp_batch_exc)
        index_to_group: dict[int, tuple[int, ...]] = {}
        for group in groups:
            gt = tuple(group)
            for idx in group:
                index_to_group[idx] = gt
        raw_by_index: List[str] = [""] * len(segments)
        group_meta: dict[tuple[int, ...], dict[str, Any]] = {}
        engines_used: set[str] = set()
        use_llm = bool(_llm_api_key()) and _natural_translation_enabled()
        llm_model = os.getenv("VM_TRANSLATE_MODEL", "gpt-4o-mini") if use_llm else ""

        t_translate_start = time.perf_counter()
        from engines.translation_timing import init_live_timing, push_live_subphase

        init_live_timing(self.task_id, segment_count=len(segments))
        push_live_subphase(
            self.task_id,
            "marian_mt",
            segments_total=len(segments),
        )

        def _translate_group(
            group: list[int],
            prev_source: str,
        ) -> tuple[list[int], str, dict[str, Any], float]:
            phrase = " ".join(
                str(mt_segments[i] or "").strip()
                for i in group
                if str(mt_segments[i] or "").strip()
            ).strip()
            orig_phrase = " ".join(
                str(source_segments[i] or "").strip()
                for i in group
                if str(source_segments[i] or "").strip()
            ).strip()
            if not phrase:
                return group, "", {}, 0.0

            next_source = ""
            if group:
                last_idx = group[-1]
                if last_idx + 1 < len(mt_segments):
                    next_source = str(mt_segments[last_idx + 1] or "").strip()

            t0 = time.perf_counter()
            meta: dict[str, Any] = {}
            try:
                from engines.translation_split import (
                    merge_translated_parts,
                    split_for_translation,
                    split_meta,
                )

                parts = split_for_translation(phrase)
                if len(parts) > 1:
                    tr_parts: list[str] = []
                    part_meta: dict[str, Any] = {}
                    for part in parts:
                        tr_part, part_meta = translate_text_traced(
                            part,
                            src_lang,
                            tgt_lang,
                            context=prev_source or None,
                            next_context=next_source or None,
                            app_dir=self.app_dir,
                            segment_index=group[0] if group else -1,
                            source_original=orig_phrase or None,
                        )
                        tr_parts.append(tr_part)
                    tr_phrase = merge_translated_parts(tr_parts)
                    meta = dict(part_meta)
                    meta["split"] = split_meta(phrase, parts)
                else:
                    tr_phrase, meta = translate_text_traced(
                        phrase,
                        src_lang,
                        tgt_lang,
                        context=prev_source or None,
                        next_context=next_source or None,
                        app_dir=self.app_dir,
                        segment_index=group[0] if group else -1,
                        source_original=orig_phrase or None,
                    )
                # TZ Stage 4: never keep legacy [context: ...] pollution in MT text.
                try:
                    from engines.translation_naturalizer import strip_mt_context_prefix

                    tr_phrase = strip_mt_context_prefix(tr_phrase)
                except Exception:
                    pass
                if meta.get("engine"):
                    engines_used.add(str(meta["engine"]))
                if meta.get("route_label"):
                    engines_used.add(str(meta["route_label"]))
                if tr_phrase:
                    log_segment(
                        self.app_dir,
                        segment_index=group[0] if group else -1,
                        engine=str(meta.get("engine") or stage_engine),
                        route=str(meta.get("route_label") or stage_route),
                        elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                        ok=True,
                        text_len=len(tr_phrase),
                    )
            except Exception as e:
                from engines.model_manager.runtime import OfflineOnlyError
                from engines.mt.translate_guard import TranslationTimeoutError

                if isinstance(e, (TranslationTimeoutError, OfflineOnlyError)):
                    translated_ok_ref["err"] = str(e)
                    logger.error("[Pipeline] translation blocked seg=%s: %s", group, e)
                    log_segment(
                        self.app_dir,
                        segment_index=group[0] if group else -1,
                        engine=stage_engine,
                        route=stage_route,
                        elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                        ok=False,
                        text_len=0,
                    )
                    raise
                logger.warning("[Pipeline] group translate failed: %s", e)
                tr_phrase = ""
                meta = {"engine": "error", "route": "direct", "pivot": None, "mt_failed": True}
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return group, str(tr_phrase or "").strip(), meta, elapsed_ms

        prev_source_phrase = ""
        group_results: list[tuple[list[int], str, dict[str, Any], float]] = []
        _groups_total = max(1, len(groups))
        _groups_done = 0

        def _emit_progress() -> None:
            if progress_cb is None:
                return
            try:
                progress_cb(_groups_done, _groups_total)
            except Exception:
                pass
            try:
                push_live_subphase(
                    self.task_id,
                    "marian_mt",
                    segments_done=_groups_done,
                    segments_total=_groups_total,
                )
            except Exception:
                pass

        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = 1
        if not stable:
            env_workers = (os.getenv("VM_TRANSLATE_PARALLEL") or "").strip()
            if env_workers.isdigit():
                max_workers = max(1, min(6, int(env_workers)))
            elif len(groups) > 1:
                max_workers = min(4, len(groups))

        from engines.pipeline_orchestrator.translation_conveyor_runner import (
            conveyor_enabled,
            run_marian_conveyor,
        )

        if conveyor_enabled():
            conv = run_marian_conveyor(
                groups,
                mt_segments,
                source_segments,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                app_dir=self.app_dir,
                task_id=self.task_id,
                stable=stable,
                progress_cb=lambda d, t: _emit_progress(),
            )
            group_results = conv.group_results
            _groups_done = len(groups)
            _emit_progress()
            if conv.cache_hits:
                logger.info(
                    "[Pipeline] Marian conveyor cache_hits=%d report=%s",
                    conv.cache_hits,
                    conv.conveyor_report.get("metrics"),
                )
        elif max_workers <= 1:
            for group in groups:
                res = _translate_group(group, prev_source_phrase)
                group_results.append(res)
                _, phrase, _, _ = res
                if phrase:
                    prev_source_phrase = " ".join(
                        str(mt_segments[i] or "").strip() for i in group if str(mt_segments[i] or "").strip()
                    ).strip()
                _groups_done += 1
                _emit_progress()
        else:
            # Параллельный перевод: контекст — предыдущая группа по порядку (sequential context seed)
            ordered_contexts: list[str] = []
            ctx = ""
            for group in groups:
                ordered_contexts.append(ctx)
                ctx = " ".join(
                    str(mt_segments[i] or "").strip() for i in group if str(mt_segments[i] or "").strip()
                ).strip() or ctx

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_translate_group, g, ordered_contexts[i]): (i, g)
                    for i, g in enumerate(groups)
                }
                indexed: list[tuple[int, tuple]] = []
                for fut in as_completed(futures):
                    gi, _g = futures[fut]
                    indexed.append((gi, fut.result()))
                    _groups_done += 1
                    _emit_progress()
                indexed.sort(key=lambda x: x[0])
                group_results = [r for _, r in indexed]

        translation_sec = time.perf_counter() - t_translate_start

        from engines.translation_timing import push_live_subphase

        push_live_subphase(
            self.task_id,
            "post_mt_restore",
            elapsed_sec=translation_sec,
            segments_done=_groups_total,
            segments_total=_groups_total,
        )

        per_index_meta: dict[int, dict[str, Any]] = {}
        per_index_ms: dict[int, float] = {}

        for group, tr_phrase, meta, elapsed_ms in group_results:
            if tr_phrase:
                translated_ok_ref["n"] += len(group)
            if not tr_phrase:
                continue
            key = tuple(group)
            group_meta[key] = meta
            share_ms = elapsed_ms / max(len(group), 1)

            if len(group) == 1:
                raw_by_index[group[0]] = tr_phrase
                m = dict(meta)
                m["group_indices"] = list(group)
                per_index_meta[group[0]] = m
                per_index_ms[group[0]] = share_ms
            elif timing_map:
                sub_timing = [timing_map[i] for i in group if i < len(timing_map)]
                sub_parts = split_by_timing_map(tr_phrase, sub_timing)
                # Prefer source-proportional split when sentence-safe split left
                # empty tails (1 UK sentence → N Whisper fragments).
                nonempty = sum(1 for p in sub_parts if str(p or "").strip())
                if nonempty < len(group) and len(group) >= 2:
                    from engines.pipeline_orchestrator.translation_batch import (
                        TranslationBatch,
                        split_batch_translation,
                    )

                    batch = TranslationBatch(
                        batch_id=-1,
                        segment_indices=list(group),
                        source_texts=[
                            str(source_segments[i] or "") for i in group
                        ],
                    )
                    split_map = split_batch_translation(batch, tr_phrase)
                    sub_parts = [split_map.get(i, "") for i in group]
                for j, idx in enumerate(group):
                    part = sub_parts[j].strip() if j < len(sub_parts) else ""
                    raw_by_index[idx] = part
                    m = dict(meta)
                    m["group_indices"] = list(group)
                    if not part and tr_phrase:
                        m["split_empty_part"] = True
                    per_index_meta[idx] = m
                    per_index_ms[idx] = share_ms
            else:
                raw_by_index[group[0]] = tr_phrase
                m = dict(meta)
                m["group_indices"] = list(group)
                per_index_meta[group[0]] = m
                per_index_ms[group[0]] = elapsed_ms
                for idx in group[1:]:
                    m_tail = dict(meta)
                    m_tail["group_indices"] = list(group)
                    m_tail["non_head_group_index"] = True
                    per_index_meta[idx] = m_tail

        preserved_finals: dict[int, str] = {}
        if self.task_id:
            try:
                from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

                with STATE_LOCK:
                    info = dict((AUTO_TASKS.get(self.task_id) or {}).get("info") or {})
                for row in info.get("translation_audits") or []:
                    idx = int(row.get("index", -1))
                    if idx < 0:
                        continue
                    text = str(
                        row.get("final_text")
                        or row.get("tts_text")
                        or row.get("naturalized_text")
                        or row.get("raw_translation")
                        or ""
                    ).strip()
                    if text:
                        preserved_finals[idx] = text
                for idx, row in enumerate(info.get("segments_data") or []):
                    if idx in preserved_finals:
                        continue
                    text = str(
                        row.get("translation_text")
                        or row.get("text")
                        or row.get("plain_text")
                        or ""
                    ).strip()
                    if text:
                        preserved_finals[idx] = text
            except Exception:
                pass

        _fill_empty_raw_translations(
            raw_by_index,
            source_segments,
            preserved_by_index=preserved_finals,
            src_lang=src,
            tgt_lang=tgt,
        )

        # Debleed identical batch MT on incomplete EN cuts (Fiat, / but …).
        try:
            from engines.translation_naturalizer import debleed_adjacent_batch_copies

            # raw_by_index is a list (not dict) — .get() was silently killing debleed.
            _raw_list = [
                str(raw_by_index[i] if i < len(raw_by_index) else "")
                for i in range(len(segments))
            ]
            _debleeded = debleed_adjacent_batch_copies(
                [str(source_segments[i] or "") for i in range(len(segments))],
                _raw_list,
            )
            for i, text in enumerate(_debleeded):
                if text != _raw_list[i]:
                    raw_by_index[i] = text
        except Exception as _debleed_exc:
            logger.debug("raw debleed skipped: %s", _debleed_exc)

        from engines.translation_quality import diagnose_raw_mt

        for i in range(len(segments)):
            whisper_i = str(segments[i] or "").strip()
            raw_i = str(raw_by_index[i] or "").strip()
            if whisper_i and not raw_i:
                m = dict(per_index_meta.get(i, {}))
                diag = diagnose_raw_mt(whisper_i, raw_i, source_lang=src, target_lang=tgt, meta=m)
                if diag:
                    m["raw_mt_diagnosis"] = diag
                    per_index_meta[i] = m
                    if i < len(inspector_traces):
                        inspector_traces[i]["raw_mt_diagnosis"] = diag

        t_restore_start = time.perf_counter()
        for i in range(len(segments)):
            if i < len(inspector_traces):
                m = per_index_meta.get(i, {})
                inspector_traces[i]["raw_mt_response"] = str(raw_by_index[i] or "")
                inspector_traces[i]["mt_request"] = {
                    "engine": str(m.get("engine") or ""),
                    "route": f"{src}→{tgt}",
                    "route_label": str(m.get("route_label") or m.get("route") or ""),
                    "model": str(m.get("model") or "") or (llm_model if use_llm else ""),
                    "router_reason": str(m.get("router_reason") or ""),
                }
                inspector_traces[i]["timing_ms"]["mt"] = float(per_index_ms.get(i, 0))

        from engines.translation_memory import learn_from_segment

        for i in range(len(segments)):
            raw_i = str(raw_by_index[i] or "").strip()
            src_i = str(source_segments[i] or "").strip()
            if raw_i and src_i:
                learn_from_segment(
                    self.app_dir,
                    source=src_i,
                    translated=raw_i,
                    src_lang=src,
                    tgt_lang=tgt,
                )

        # Restore placeholders immediately after MT — before Naturalizer
        from engines.naturalizer_v2.entity_tokens import (
            entity_context_for_segment,
            restore_entities,
        )
        from engines.placeholder_guard import (
            has_mt_garbage,
            resolve_token_map_for_text,
        )

        health_by_index: list[dict[str, Any]] = [{} for _ in segments]
        for i in range(len(segments)):
            if per_index_meta.get(i, {}).get("enterprise_restored") or per_index_meta.get(i, {}).get(
                "broadcast_restored"
            ) or per_index_meta.get(i, {}).get("unmasked_fallback"):
                continue
            before = str(raw_by_index[i] or "")
            emap, orig = entity_context_for_segment(
                i,
                groups=groups,
                entity_maps=entity_maps,
                source_segments=source_segments,
            )
            group_maps = [
                entity_maps[j] for j in index_to_group.get(i, (i,)) if j < len(entity_maps)
            ]
            if has_mt_garbage(before):
                emap = {**emap, **resolve_token_map_for_text(before, group_maps)}
            if not emap and not has_mt_garbage(before):
                continue
            restored_text, _labels = restore_entities(
                before,
                emap,
                original=orig or source_segments[i],
                tgt_lang=tgt,
                app_dir=self.app_dir,
            )
            if restored_text != before:
                raw_by_index[i] = restored_text
            elif has_mt_garbage(before):
                restored_text, _labels = restore_entities(
                    before,
                    resolve_token_map_for_text(before, group_maps),
                    original=orig or source_segments[i],
                    tgt_lang=tgt,
                    app_dir=self.app_dir,
                )
                raw_by_index[i] = restored_text
            if i < len(inspector_traces):
                inspector_traces[i]["after_restore"] = str(raw_by_index[i] or "")
            from engines.pipeline_health import check_stage

            health_by_index[i] = check_stage(
                stage="post_mt_restore",
                text_in=before,
                text_out=raw_by_index[i],
                original=orig or source_segments[i],
                token_map=emap,
                src_lang=src,
                tgt_lang=tgt,
            )

        # Re-score and polish proper nouns on restored / unmasked MT output
        from engines.proper_nouns_dict import apply_proper_noun_polish
        from engines.translation_quality_score import compute_quality_score

        for i in range(len(segments)):
            raw_i = str(raw_by_index[i] or "")
            src_i = str(source_segments[i] or "").strip()
            if raw_i and src_i:
                from engines.naturalizer_v2.entity_fixup import sanitize_wrong_entity_substitutions

                raw_i = sanitize_wrong_entity_substitutions(
                    raw_i, original=src_i, tgt_lang=tgt
                )
                raw_by_index[i] = apply_proper_noun_polish(
                    src_i, raw_i, app_dir=self.app_dir, tgt_lang=tgt
                )
                if tgt == "ru":
                    from engines.translation_naturalizer import fix_ru_jr_suffix

                    raw_by_index[i] = fix_ru_jr_suffix(raw_by_index[i])
                if tgt == "uk":
                    from engines.naturalizer_v2.uk_name_forms import apply_uk_dub_name_polish

                    raw_by_index[i] = apply_uk_dub_name_polish(
                        raw_by_index[i], original=src_i
                    )
                score, qd = compute_quality_score(
                    src_i, raw_by_index[i], src_lang=src, tgt_lang=tgt
                )
                if has_mt_garbage(raw_by_index[i]):
                    score = 0.0
                    qd = {**qd, "placeholder_leak_count": max(1, int(qd.get("placeholder_leak_count") or 0))}
                meta_i = per_index_meta.setdefault(i, {})
                meta_i["quality_score"] = score
                meta_i["quality_details"] = qd

        restore_ms = (time.perf_counter() - t_restore_start) * 1000.0
        restore_sec = restore_ms / 1000.0
        per_index_restore_ms = restore_ms / max(len(segments), 1)
        for i in range(len(segments)):
            if i < len(inspector_traces):
                inspector_traces[i]["timing_ms"]["restore"] = per_index_restore_ms

        from engines.translation_stage_log import log_translation_stage_batch

        log_translation_stage_batch(
            self.task_id,
            stage="post_raw_mt",
            texts=[str(raw_by_index[i] or "") for i in range(len(segments))],
            source_lang=src,
            target_lang=tgt,
        )

        t_nat_start = time.perf_counter()
        push_live_subphase(
            self.task_id,
            "llm_adaptation" if use_llm else "naturalizer_rules",
            elapsed_sec=translation_sec + restore_sec,
            segments_total=len(segments),
        )

        def _nat_segment_progress(done: int, total: int) -> None:
            push_live_subphase(
                self.task_id,
                "llm_adaptation" if use_llm else "naturalizer_rules",
                segments_done=done,
                segments_total=total,
            )
        llm_ms_per_index: list[float] = [0.0] * len(segments)
        quality_scores = [
            float(per_index_meta.get(i, {}).get("quality_score") or 0.0)
            for i in range(len(segments))
        ]
        nat_reasons_per_index: list[list[str]] = []
        nat_meta_per_index: list[dict[str, Any]] = []
        from engines.pipeline_orchestrator.translation_conveyor_runner import (
            conveyor_enabled,
            run_llm_conveyor,
        )

        if conveyor_enabled():
            llm_conv = run_llm_conveyor(
                raw_by_index,
                source_segments,
                tgt_lang=tgt,
                src_lang=src,
                app_dir=self.app_dir,
                task_id=self.task_id,
                use_llm=use_llm,
                entity_maps=entity_maps,
                progress_cb=_nat_segment_progress,
            )
            naturalized = llm_conv.polished
            llm_ms_per_index = llm_conv.llm_ms
            nat_reasons_per_index = llm_conv.naturalizer_reasons
            nat_meta_per_index = llm_conv.naturalizer_meta
            logger.info(
                "[Pipeline] LLM conveyor workers=%s sec=%.1f",
                llm_conv.conveyor_report.get("workers"),
                llm_conv.llm_sec,
            )
        else:
            naturalized = polish_lines(
                raw_by_index,
                source_segments=source_segments,
                tgt_lang=tgt,
                src_lang=src,
                use_llm=use_llm,
                llm_ms_out=llm_ms_per_index,
                app_dir=self.app_dir,
                quality_scores=quality_scores,
                naturalizer_reasons_out=nat_reasons_per_index,
                entity_maps=entity_maps,
                naturalizer_meta_out=nat_meta_per_index,
                segment_progress_cb=_nat_segment_progress,
            )
        from engines.naturalizer_v2.entity_fixup import sanitize_wrong_entity_substitutions

        naturalized = [
            sanitize_wrong_entity_substitutions(
                str(naturalized[i] or ""),
                original=str(source_segments[i] or ""),
                tgt_lang=tgt,
            )
            if i < len(source_segments)
            else str(naturalized[i] or "")
            for i in range(len(naturalized))
        ]
        naturalized = fix_phantom_cross_segment_repeats(source_segments, naturalized)
        # TZ §8: drop duplicated sentences / repeated word-runs before TTS.
        from engines.repetition_guard import dedupe_segment_texts

        naturalized, _rep_changed_idx = dedupe_segment_texts(naturalized)
        if _rep_changed_idx:
            logger.info(
                "[Translation] repetition_guard removed repeats in %d segments: %s",
                len(_rep_changed_idx),
                _rep_changed_idx[:20],
            )
        post_naturalizer = list(naturalized)
        naturalizer_sec_partial = time.perf_counter() - t_nat_start
        per_index_nat_ms_partial = (naturalizer_sec_partial * 1000.0) / max(len(segments), 1)
        from engines.translation_stage_log import log_translation_stage, log_translation_stage_batch

        log_translation_stage_batch(
            self.task_id,
            stage="post_naturalizer",
            texts=post_naturalizer,
            source_lang=src,
            target_lang=tgt,
        )
        for i in range(len(segments)):
            if i < len(inspector_traces):
                inspector_traces[i]["after_naturalizer"] = str(
                    post_naturalizer[i] if i < len(post_naturalizer) else ""
                )
                inspector_traces[i]["timing_ms"]["naturalizer"] = per_index_nat_ms_partial + (
                    llm_ms_per_index[i] if i < len(llm_ms_per_index) else 0.0
                )

        t_quality_start = time.perf_counter()
        push_live_subphase(self.task_id, "validation", segments_total=len(segments))
        from engines.translation_quality import run_quality_validation

        validated, validation_warnings = run_quality_validation(
            segments,
            post_naturalizer,
            src_lang=src,
            tgt_lang=tgt,
            raw_segments=raw_by_index,
        )
        quality_pass_ms = (time.perf_counter() - t_quality_start) * 1000.0
        validation_sec = quality_pass_ms / 1000.0
        per_index_quality_ms = quality_pass_ms / max(len(segments), 1)
        log_translation_stage_batch(
            self.task_id,
            stage="post_translation_qa",
            texts=[str(validated[i] if i < len(validated) else "") for i in range(len(segments))],
            source_lang=src,
            target_lang=tgt,
            detail="validation_only",
        )
        for i in range(len(segments)):
            if i < len(inspector_traces):
                inspector_traces[i]["after_grammar"] = str(
                    validated[i] if i < len(validated) else ""
                )
                inspector_traces[i]["timing_ms"]["grammar"] = per_index_quality_ms

        t_sem_start = time.perf_counter()
        from engines.semantic_translation import apply_semantic_polish_lines

        semantic_out = apply_semantic_polish_lines(
            validated,
            target_lang=tgt,
            source_segments=segments,
        )
        semantic_ms = (time.perf_counter() - t_sem_start) * 1000.0
        semantic_sec = semantic_ms / 1000.0
        per_index_semantic_ms = semantic_ms / max(len(segments), 1)

        naturalized = semantic_out

        log_translation_stage_batch(
            self.task_id,
            stage="post_semantic_polish",
            texts=[str(naturalized[i] or "") for i in range(len(naturalized))],
            source_lang=src,
            target_lang=tgt,
        )

        # Final placeholder gate before TTS
        from engines.naturalizer_v2.entity_tokens import (
            entity_context_for_segment,
            restore_entities,
        )
        from engines.placeholder_guard import has_mt_garbage, resolve_token_map_for_text

        for i in range(len(naturalized)):
            before = str(naturalized[i] or "")
            emap, orig = entity_context_for_segment(
                i,
                groups=groups,
                entity_maps=entity_maps,
                source_segments=source_segments,
            )
            group_maps = [
                entity_maps[j] for j in index_to_group.get(i, (i,)) if j < len(entity_maps)
            ]
            if has_mt_garbage(before):
                emap = {**emap, **resolve_token_map_for_text(before, group_maps)}
            if not emap and not has_mt_garbage(before):
                continue
            final_text, _ = restore_entities(
                before,
                emap,
                original=orig or source_segments[i],
                tgt_lang=tgt,
                app_dir=self.app_dir,
            )
            if final_text != before:
                naturalized[i] = final_text
            elif has_mt_garbage(before):
                final_text, _ = restore_entities(
                    before,
                    resolve_token_map_for_text(before, group_maps),
                    original=orig or source_segments[i],
                    tgt_lang=tgt,
                    app_dir=self.app_dir,
                )
                naturalized[i] = final_text
            from engines.naturalizer_v2.entity_fixup import sanitize_wrong_entity_substitutions

            naturalized[i] = sanitize_wrong_entity_substitutions(
                str(naturalized[i] or ""),
                original=str(source_segments[i] or ""),
                tgt_lang=tgt,
            )
            if tgt == "uk":
                from engines.naturalizer_v2.uk_name_forms import apply_uk_dub_name_polish

                naturalized[i] = apply_uk_dub_name_polish(
                    naturalized[i],
                    original=str(source_segments[i] or ""),
                )
            elif tgt == "ru":
                from engines.translation_naturalizer import fix_ru_jr_suffix

                naturalized[i] = fix_ru_jr_suffix(str(naturalized[i] or ""))
            from engines.pipeline_health import check_stage, merge_health

            final_h = check_stage(
                stage="final_gate",
                text_in=before,
                text_out=naturalized[i],
                original=source_segments[i],
                token_map=emap,
                src_lang=src,
                tgt_lang=tgt,
            )
            prev = health_by_index[i] if i < len(health_by_index) else {}
            if prev:
                health_by_index[i] = {
                    "ok": prev.get("ok", True) and final_h.get("ok", True),
                    "stages": [prev, final_h],
                    "issues": list(prev.get("issues") or []) + list(final_h.get("issues") or []),
                }
            else:
                health_by_index[i] = final_h
            if i < len(inspector_traces):
                inspector_traces[i]["final"] = str(naturalized[i] or "")
                inspector_traces[i]["timing_ms"]["semantic"] = per_index_semantic_ms
                inspector_traces[i]["timing_ms"]["total"] = (
                    float(inspector_traces[i]["timing_ms"].get("mt") or 0)
                    + float(inspector_traces[i]["timing_ms"].get("restore") or 0)
                    + float(inspector_traces[i]["timing_ms"].get("naturalizer") or 0)
                    + float(inspector_traces[i]["timing_ms"].get("grammar") or 0)
                    + per_index_semantic_ms
                )

        if timing_map and len(naturalized) != len(timing_map):
            naturalized = align_segments_to_timing_map(naturalized, timing_map)

        timing_aware_records: list = []
        t_tat_start = time.perf_counter()
        if timing_map:
            from engines.timing_aware_translation import adapt_segments_to_timing

            naturalized, timing_aware_records = adapt_segments_to_timing(
                naturalized,
                timing_map,
                source_segments,
                src_lang=src,
                tgt_lang=tgt,
                task_id=self.task_id,
                raw_mt_segments=[str(raw_by_index[i] or "") for i in range(len(segments))],
            )
        timing_aware_sec = time.perf_counter() - t_tat_start if timing_map else 0.0
        push_live_subphase(self.task_id, "post_processing")

        from engines.semantic_meaning import SemanticValidationError, validate_transformation_chain
        from engines.pipeline_integrity.semantic_validation_openddf import (
            build_pipeline_stage_report,
        )
        from engines.translation_quality import build_quality_analysis

        pipeline_stages = build_pipeline_stage_report(
            raw_by_index=[str(raw_by_index[i] or "") for i in range(len(segments))],
            post_naturalizer=[
                str(post_naturalizer[i] if i < len(post_naturalizer) else "")
                for i in range(len(segments))
            ],
            naturalized=[
                str(naturalized[i] if i < len(naturalized) else "")
                for i in range(len(segments))
            ],
            timing_map=list(timing_map) if timing_map else None,
            timing_aware_records=timing_aware_records,
            natural_translation_enabled=_natural_translation_enabled(),
        )

        chain_failures: list[dict[str, Any]] = []
        quality_analyses: list[dict[str, Any]] = []
        for i in range(len(segments)):
            raw_mt = str(raw_by_index[i] or "")
            nat = str(post_naturalizer[i] if i < len(post_naturalizer) else "")
            sem = str(naturalized[i] if i < len(naturalized) else "")
            ok, reason, chain_details = validate_transformation_chain(
                original=str(segments[i] or ""),
                raw_mt=raw_mt,
                semantic=sem,
                final_tts=sem,
                source=str(segments[i] or ""),
                app_dir=self.app_dir,
            )
            qa = build_quality_analysis(
                original=str(segments[i] or ""),
                raw=raw_mt,
                naturalized=nat,
                final=sem,
                tts_text=sem,
                source_lang=src,
                target_lang=tgt,
                raw_meta=per_index_meta.get(i, {}),
            )
            quality_analyses.append(qa)
            if not ok:
                chain_failures.append(
                    {"index": i, "reason": reason, "details": chain_details, "qa": qa}
                )
            if i < len(inspector_traces):
                inspector_traces[i]["transformation_chain"] = chain_details

        semantic_problem_segments: list[dict[str, Any]] = []
        semantic_failure_payload: dict[str, Any] = {}
        if chain_failures:
            from engines.pipeline_integrity.semantic_validation_openddf import (
                build_semantic_failure_payload,
            )
            from engines.pipeline_language_gate import is_critical_language_mismatch
            from engines.translation_stage_log import log_translation_stage

            # TZ §6: a single failed segment must NOT abort the whole pipeline.
            # Recover per-segment: retry → Raw MT → mark "LLM adaptation required" →
            # continue. Never fall back to the English source (TZ §2).
            for fail in chain_failures:
                idx = int(fail.get("index", -1))
                if not (0 <= idx < len(naturalized)):
                    continue
                src_text = str(segments[idx] or "")
                cur = str(naturalized[idx] or "").strip()
                # Raw MT is used as a recovery fallback below, but it never went
                # through the repetition guard (that ran earlier on `naturalized`).
                # Clean it here too, or a doubled MT line ("X. X") reaches TTS.
                from engines.repetition_guard import remove_repeated_sentences

                raw_mt = str(raw_by_index[idx] or "").strip()
                raw_mt, _raw_rep = remove_repeated_sentences(raw_mt)

                cur_bad, cur_lang_reason = (
                    is_critical_language_mismatch(
                        cur, target_lang=tgt, original=src_text
                    )
                    if cur
                    else (True, "empty")
                )
                recovery = "kept_semantic"
                needs_llm = True
                from engines.semantic_meaning import should_prefer_semantic_over_raw_mt

                prefer_semantic = should_prefer_semantic_over_raw_mt(
                    semantic=cur,
                    raw_mt=raw_mt,
                    source=src_text,
                    fail_reason=str(fail.get("reason") or ""),
                    app_dir=self.app_dir,
                )

                if raw_mt:
                    raw_bad, _ = is_critical_language_mismatch(
                        raw_mt, target_lang=tgt, original=src_text
                    )
                    raw_ok, _raw_reason, _ = validate_transformation_chain(
                        original=src_text,
                        raw_mt=raw_mt,
                        semantic=raw_mt,
                        final_tts=raw_mt,
                        source=src_text,
                        app_dir=self.app_dir,
                    )
                    if prefer_semantic:
                        recovery = "kept_semantic_over_short_raw_mt"
                        needs_llm = True
                    elif not raw_bad and raw_ok:
                        naturalized[idx] = raw_mt
                        recovery = "raw_mt_fallback"
                        needs_llm = False
                    elif cur_bad and not raw_bad:
                        # Current output leaks English — Raw MT (target lang) is safer
                        # even if it does not fully pass semantic validation.
                        naturalized[idx] = raw_mt
                        recovery = "raw_mt_unverified"
                    elif cur_bad:
                        recovery = "marked_llm_adaptation_required"
                elif cur_bad:
                    recovery = "marked_llm_adaptation_required"

                problem = {
                    "index": idx,
                    "reason": str(fail.get("reason") or "unknown"),
                    "recovery": recovery,
                    "needs_llm_adaptation": needs_llm,
                    "language_issue": cur_lang_reason or "",
                }
                semantic_problem_segments.append(problem)

                if idx < len(inspector_traces):
                    inspector_traces[idx]["semantic_validation"] = {
                        "failed": True,
                        "reason": problem["reason"],
                        "recovery": recovery,
                        "needs_llm_adaptation": needs_llm,
                    }
                    inspector_traces[idx]["final"] = str(naturalized[idx] or "")

                log_translation_stage(
                    self.task_id,
                    stage="semantic_validation_recovery",
                    segment_index=idx,
                    text=str(naturalized[idx] or ""),
                    source_lang=src,
                    target_lang=tgt,
                    detail=f"reason={problem['reason']} recovery={recovery}",
                    changed=recovery.startswith("raw_mt"),
                )
                logger.warning(
                    "[Pipeline] semantic validation seg=%d reason=%s → recovery=%s "
                    "(pipeline continues)",
                    idx,
                    problem["reason"],
                    recovery,
                )

            semantic_failure_payload = build_semantic_failure_payload(
                chain_failures,
                segments=segments,
                raw_by_index=raw_by_index,
                post_naturalizer=post_naturalizer,
                naturalized=naturalized,
                source_lang=src,
                target_lang=tgt,
                per_index_meta=per_index_meta,
                timing_aware_records=timing_aware_records,
                pipeline_stages=pipeline_stages,
            )
            semantic_failure_payload["pipeline_aborted"] = False
            semantic_failure_payload["recovered_segments"] = semantic_problem_segments
            logger.warning(
                "[Pipeline] %d/%d segments failed semantic validation — recovered, "
                "pipeline NOT aborted (TZ §6): %s",
                len(semantic_problem_segments),
                len(segments),
                [p["index"] for p in semantic_problem_segments],
            )

        # Final safety net (TZ §8): the semantic-validation recovery above may have
        # swapped in raw MT for some segments, which can (re)introduce duplicated
        # sentences or phantom cross-segment repeats. Re-run the guards once more on
        # the final text so nothing doubled ever reaches TTS.
        naturalized = fix_phantom_cross_segment_repeats(source_segments, naturalized)
        naturalized, _final_rep_idx = dedupe_segment_texts(naturalized)
        if _final_rep_idx:
            logger.info(
                "[Translation] final repetition_guard cleaned %d segments: %s",
                len(_final_rep_idx),
                _final_rep_idx[:20],
            )

        log_translation_stage_batch(
            self.task_id,
            stage="pre_tts",
            texts=[str(naturalized[i] or "") for i in range(len(naturalized))],
            source_lang=src,
            target_lang=tgt,
            detail="pipeline_final",
        )

        for i in range(len(segments)):
            cur = str(naturalized[i] if i < len(naturalized) else "").strip()
            if cur:
                continue
            kept = str(
                preserved_finals.get(i)
                or raw_by_index[i]
                or (post_naturalizer[i] if i < len(post_naturalizer) else "")
                or ""
            ).strip()
            if kept:
                naturalized[i] = kept
            # Never bypass to English source when target language differs (TZ §3.2).

        # Final debleed gate (UK+RU): MT/LLM can re-copy a shared blob after the
        # early raw debleed. Split again before audits so Review Raw/Final diverge.
        try:
            from engines.dsal.clause_coverage import strip_cross_lang_clause_orphans
            from engines.translation_naturalizer import debleed_adjacent_batch_copies

            _src_final = [str(source_segments[i] or "") for i in range(len(segments))]
            _nat_list = [
                strip_cross_lang_clause_orphans(str(naturalized[i] or ""))
                for i in range(len(naturalized))
            ]
            _raw_list = [
                strip_cross_lang_clause_orphans(str(raw_by_index[i] or ""))
                for i in range(len(raw_by_index))
            ]
            _nat_db = debleed_adjacent_batch_copies(_src_final, _nat_list)
            _raw_db = debleed_adjacent_batch_copies(_src_final, _raw_list)
            _post_list = [
                strip_cross_lang_clause_orphans(str(post_naturalizer[i] or ""))
                for i in range(len(post_naturalizer))
            ]
            _post_db = debleed_adjacent_batch_copies(_src_final, _post_list)
            for i in range(len(segments)):
                if i < len(_nat_db) and _nat_db[i] != _nat_list[i]:
                    naturalized[i] = _nat_db[i]
                elif i < len(_nat_list):
                    naturalized[i] = _nat_list[i]
                if i < len(_raw_db) and _raw_db[i] != _raw_list[i]:
                    raw_by_index[i] = _raw_db[i]
                elif i < len(_raw_list):
                    raw_by_index[i] = _raw_list[i]
                if i < len(_post_db):
                    post_naturalizer[i] = _post_db[i]
                elif i < len(_post_list):
                    post_naturalizer[i] = _post_list[i]
        except Exception as _final_debleed_exc:
            logger.debug("final debleed gate skipped: %s", _final_debleed_exc)

        naturalizer_sec = time.perf_counter() - t_nat_start
        per_index_nat_ms = (naturalizer_sec * 1000.0) / max(len(segments), 1)

        audits: list[SegmentTranslationAudit] = []
        for i in range(len(segments)):
            m = per_index_meta.get(i, {})
            raw_mt = str(raw_by_index[i] or "")
            nat = str(post_naturalizer[i] if i < len(post_naturalizer) else "")
            sem = str(naturalized[i] if i < len(naturalized) else "")
            seg_warnings = (
                validation_warnings[i]
                if i < len(validation_warnings)
                else []
            )
            nat_meta = (
                nat_meta_per_index[i]
                if i < len(nat_meta_per_index)
                else {}
            )
            tat_rec = (
                timing_aware_records[i]
                if i < len(timing_aware_records)
                else None
            )
            nat_reasons = (
                nat_reasons_per_index[i]
                if i < len(nat_reasons_per_index)
                else []
            )
            nat_applied = bool(
                nat.strip()
                and raw_mt.strip()
                and nat.strip() != raw_mt.strip()
            ) or bool(nat_reasons)
            tat_applied = bool(tat_rec and tat_rec.adapted)
            tat_executed = bool(timing_map)
            qd_base = dict(m.get("quality_details") or {})
            qd_base["pipeline_health"] = (
                health_by_index[i] if i < len(health_by_index) else {}
            )
            qd_base["inspector"] = (
                inspector_traces[i] if i < len(inspector_traces) else {}
            )
            if tat_rec is not None:
                qd_base["timing_aware"] = tat_rec.to_dict()
            if i < len(quality_analyses):
                qd_base["quality_analysis"] = quality_analyses[i]
            from engines.semantic_optimizer import build_transformation_chain

            slot_ms = 0
            if timing_map and i < len(timing_map):
                from engines.timing_aware_translation import slot_ms_from_timing

                slot_ms = slot_ms_from_timing(timing_map, i)
            qd_base["transformation_chain"] = build_transformation_chain(
                original=str(segments[i] or ""),
                raw_mt=raw_mt,
                semantic=sem,
                final_tts=sem,
                slot_ms=slot_ms,
                tgt_lang=tgt,
            )
            audits.append(
                SegmentTranslationAudit(
                    index=i,
                    source_lang=src,
                    target_lang=tgt,
                    whisper_text=str(segments[i] or ""),
                    raw_translation=raw_mt,
                    naturalized_text=nat,
                    final_text=sem,
                    tts_text=sem,
                    quality_pass_before=nat,
                    quality_pass_after=nat,
                    semantic_text=sem,
                    engine=str(m.get("engine") or ""),
                    model=llm_model if use_llm else "",
                    route="direct" if m.get("direct", True) else str(m.get("route") or "direct"),
                    pivot=m.get("pivot"),
                    duration_ms=per_index_ms.get(i, 0.0),
                    naturalizer_ms=per_index_nat_ms,
                    llm_ms=llm_ms_per_index[i] if i < len(llm_ms_per_index) else 0.0,
                    quality_pass_ms=per_index_quality_ms,
                    semantic_ms=per_index_semantic_ms,
                    quality_score=float(m.get("quality_score") or 0.0),
                    mt_retries=int(m.get("mt_retries") or 0),
                    router_reason=str(m.get("router_reason") or ""),
                    route_label=str(m.get("route_label") or m.get("route") or "direct"),
                    quality_details=qd_base,
                    validation_warnings=seg_warnings,
                    naturalizer_reasons=(
                        nat_reasons_per_index[i]
                        if i < len(nat_reasons_per_index)
                        else []
                    ),
                    nat_quality_score=float(nat_meta.get("quality_score") or 0.0),
                    nat_mixed_language_pct=float(nat_meta.get("mixed_language_pct") or 0.0),
                    nat_retry_reason=str(nat_meta.get("retry_reason") or ""),
                    nat_problems=list(nat_meta.get("problems") or []),
                    nat_fix_count=int(nat_meta.get("fix_count") or 0),
                    nat_restored_entities=list(nat_meta.get("restored_entities") or []),
                    nat_warnings=list(nat_meta.get("warnings") or []),
                    nat_retried=bool(nat_meta.get("retried")),
                    alternative_translation=str(m.get("alternative_translation") or ""),
                    alternative_route=str(m.get("alternative_route") or ""),
                    alternative_engine=str(m.get("alternative_engine") or ""),
                    alternative_score=float(m.get("alternative_score") or 0.0),
                    routes_tried=list(m.get("routes_tried") or []),
                    enterprise=bool(m.get("enterprise")),
                    tournament_engines=list(m.get("tournament_engines") or []),
                    tournament_scores=dict(m.get("tournament_scores") or {}),
                    fusion_reason=str(m.get("fusion_reason") or ""),
                    architect=dict(m.get("architect") or {}),
                    whisper_len=len(str(segments[i] or "")),
                    raw_len=len(raw_mt),
                    naturalized_len=len(nat),
                    final_len=len(sem),
                    semantic_adapted=sem != nat
                    or bool(tat_rec and tat_rec.adapted),
                    naturalizer_applied=nat_applied,
                    naturalizer_executed=True,
                    timing_aware_applied=tat_applied,
                    timing_aware_executed=tat_executed,
                    group_indices=[i],
                )
            )

        self.quality_log.extend(audits)

        from engines.translation_trace import TranslationTraceLog

        trace = TranslationTraceLog(self.app_dir, task_id=self.task_id)
        for rec in audits:
            trace.upsert_from_audit(
                {
                    "index": rec.index,
                    "whisper_text": rec.whisper_text,
                    "raw_translation": rec.raw_translation,
                    "naturalized_text": rec.naturalized_text,
                    "quality_pass_before": rec.quality_pass_before,
                    "quality_pass_after": rec.quality_pass_after,
                    "semantic_text": rec.semantic_text,
                    "final_text": rec.final_text,
                    "tts_text": rec.tts_text,
                    "engine": rec.engine,
                    "route": rec.route,
                    "source_lang": rec.source_lang,
                    "target_lang": rec.target_lang,
                    "duration_ms": rec.duration_ms,
                    "naturalizer_ms": rec.naturalizer_ms,
                    "llm_ms": rec.llm_ms,
                    "quality_pass_ms": rec.quality_pass_ms,
                    "semantic_ms": rec.semantic_ms,
                    "quality_score": rec.quality_score,
                    "mt_retries": rec.mt_retries,
                    "router_reason": rec.router_reason,
                    "route_label": rec.route_label,
                    "quality_details": rec.quality_details,
                    "validation_warnings": rec.validation_warnings,
                    "naturalizer_reasons": rec.naturalizer_reasons,
                    "nat_quality_score": rec.nat_quality_score,
                    "nat_mixed_language_pct": rec.nat_mixed_language_pct,
                    "nat_retry_reason": rec.nat_retry_reason,
                    "nat_problems": rec.nat_problems,
                    "nat_fix_count": rec.nat_fix_count,
                    "nat_restored_entities": rec.nat_restored_entities,
                    "nat_warnings": rec.nat_warnings,
                    "alternative_translation": rec.alternative_translation,
                    "alternative_route": rec.alternative_route,
                    "alternative_engine": rec.alternative_engine,
                    "alternative_score": rec.alternative_score,
                    "routes_tried": rec.routes_tried,
                    "whisper_len": rec.whisper_len,
                    "raw_len": rec.raw_len,
                    "naturalized_len": rec.naturalized_len,
                    "final_len": rec.final_len,
                }
            )
        trace.flush(
            phase="post_naturalizer",
            extra={"src": src, "tgt": tgt, "pipeline": "universal_v2"},
        )

        meta_out = {
            "direct": True,
            "src": src,
            "tgt": tgt,
            "engines": sorted(engines_used),
            "groups": len(groups),
            "natural_polish": True,
            "llm_polish": use_llm,
            "llm_model": llm_model,
            "translation_sec": round(translation_sec, 3),
            "naturalizer_sec": round(naturalizer_sec, 3),
            "marian_sec": round(translation_sec, 3),
            "llm_adaptation_sec": round(sum(llm_ms_per_index) / 1000.0, 3),
            "validation_sec": round(validation_sec + semantic_sec, 3),
            "timing_aware_sec": round(timing_aware_sec, 3),
            "restore_sec": round(restore_sec, 3),
            "pipeline": "stable_v1" if stable else "manager_v1",
            "translation_trace_log": trace.path,
            "stable_mt": stable,
            "naturalizer_applied": bool(
                pipeline_stages.get("natural_translation", {}).get("applied")
            ),
            "naturalizer_executed": bool(
                pipeline_stages.get("natural_translation", {}).get("executed")
            ),
            "timing_aware_applied": bool(
                pipeline_stages.get("timing_aware_translation", {}).get("applied")
            ),
            "timing_aware_executed": bool(
                pipeline_stages.get("timing_aware_translation", {}).get("executed")
            ),
            "timing_aware_records": [
                r.to_dict() for r in timing_aware_records
            ],
            "pipeline_stages": pipeline_stages,
            "semantic_problem_segments": semantic_problem_segments,
            "semantic_validation_payload": semantic_failure_payload,
        }

        from engines.translation_timing import build_breakdown, push_live_subphase

        _llm_provider = ""
        try:
            from engines.llm_adaptation_mode import resolve_llm_endpoint

            _llm_provider = str(resolve_llm_endpoint().get("provider") or "")
        except Exception:
            pass
        timing_breakdown = build_breakdown(
            marian_sec=translation_sec,
            naturalizer_sec=naturalizer_sec,
            llm_ms_total=sum(llm_ms_per_index),
            restore_sec=restore_sec,
            validation_sec=validation_sec,
            semantic_sec=semantic_sec,
            timing_aware_sec=timing_aware_sec,
            llm_model=llm_model if use_llm else "",
            llm_provider=_llm_provider,
            segment_count=len(segments),
            marian_segments_done=len(segments),
            llm_segments_done=len(segments),
        )
        meta_out["translation_timing_breakdown"] = timing_breakdown.to_dict()
        push_live_subphase(self.task_id, "done", breakdown=timing_breakdown)
        from engines.translation_timing import log_translation_debug_breakdown

        log_translation_debug_breakdown(
            self.app_dir,
            self.task_id,
            timing_breakdown.to_dict(),
        )

        if translate_meta_out is not None:
            translate_meta_out.clear()
            translate_meta_out.append(meta_out)

        logger.info(
            "[Pipeline] universal translate %d segments %s→%s groups=%d engines=%s",
            len(segments),
            src,
            tgt,
            len(groups),
            ",".join(sorted(engines_used)) or "?",
        )

        return PipelineResult(segments=naturalized, audits=audits, meta=meta_out)

    def flush_quality_log(self, **extra) -> str:
        return self.quality_log.flush(task_id=self.task_id, extra=extra)


def translate_segments_universal(
    segments: List[str],
    timing_map: Sequence[Any],
    src_lang: str,
    tgt_lang: str,
    *,
    task_id: str = "",
    app_dir=None,
    translate_meta_out: list | None = None,
    write_quality_log: bool = True,
) -> List[str]:
    """Public API — drop-in replacement for translate_segments_natural."""
    pipe = UniversalTranslationPipeline(app_dir=app_dir, task_id=task_id)
    result = pipe.translate_segments(
        segments,
        timing_map,
        src_lang,
        tgt_lang,
        translate_meta_out=translate_meta_out,
    )
    if write_quality_log:
        pipe.flush_quality_log(
            src=result.meta.get("src"),
            tgt=result.meta.get("tgt"),
            engines=result.meta.get("engines"),
        )
    return result.segments
