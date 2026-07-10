# Этап 3A — Стабилизация таймингов (Conflict Resolver)

**Версия:** TubeDub 1.7 (Stage 3A)  
**Статус:** Реализовано  
**Scope:** только модули расчёта таймингов; mix/ducking/UI не изменялись.

---

## 1. Реализация Conflict Resolver

### Модуль

`engines/conflict_resolver.py` — детерминированный локальный resolver наложений после per-segment fit.

### Точка интеграции

`engines/timing_fit.py` → `build_gap_adjusted_track()`:

- после цикла `fit_segment_audio()` для всех сегментов;
- **до** FFmpeg-микса;
- вызывается `apply_resolver_to_fitted()`.

### Приоритетная цепочка (строго по порядку)

При перекрытии сегмента **i** и **i+1** корректируется только **сегмент i** (чей конец наезжает на следующий). Сегмент **i+1** и все последующие **не сдвигаются** — инвариант «No Domino Effect».

| # | Стратегия | Код | Действие |
|---|-----------|-----|----------|
| 1 | Free Window | `free_window` | Сдвиг `place_start` влево в свободный зазор (до 250 ms) |
| 2 | Local Shift | `local_shift` | Микро-сдвиг ±10…250 ms с оценкой lip-sync (`_score_shift`) |
| 3 | Local Reflow | `local_reflow` | Виртуальное сжатие пауз (до 185 ms, как в `timing_fit`) |
| 4 | Safe Time Stretch | `safe_stretch` | Планирование `atempo` в пределах 0.92–1.05 |
| 5 | OVERFLOW | `overflow` | Статус для ручной правки в Dub Studio |

### Политики

- **Natural Timing:** сдвиг/reflow всегда раньше stretch; stretch только если предыдущие шаги не уложили фразу.
- **Lip Timing:** clamp раннего старта (−120 ms) и позднего (+180 ms); при выборе сдвига минимизируется `|place_start − original_start|`.
- **Project Rhythm:** сегменты без конфликта остаются `status=intact`, `strategy=none`.

### Константы (согласованы с `timing_fit.py`)

```text
MIN_GAP_MS = 80
OVERLAP_TOLERANCE_MS = 40
MAX_LOCAL_SHIFT_MS = 250
SAFE_ATEMPO_MAX = 1.05
MAX_REFLOW_MS = 185
```

---

## 2. Метрики качества (Intervention Map)

После каждого прогона resolver формирует `intervention_map`:

| Метрика | Поле |
|---------|------|
| Всего сегментов | `total_segments` |
| Нетронутые | `intact_pct` |
| Только сдвиг/reflow | `shift_only_pct` |
| Stretch/reflow | `stretch_only_pct` |
| OVERFLOW | `overflow_pct` |
| Дрейф таймлайна | `timeline_drift_ms` |
| Устранённые overlap | `overlaps_resolved` |

Данные сохраняются в:

- `overlap_report["conflict_resolver"]` (пайплайн timing);
- `ProjectSession` ключи `conflict_resolver`, `conflict_resolver_profile`, `conflict_strategy_counts`.

---

## 3. Профилирование

`profile` в результате resolver:

- `total_ms` — суммарное время модуля;
- `avg_segment_ms` / `max_segment_ms` — на пару сегментов;
- `segment_count`.

Лог: `[tubedub.conflict_resolver] conflict_resolver task=… resolved=… intact=…% overflow=…% drift=…ms`.

---

## 4. Observability

### Decision Tracing

При `VM_DEV_MODE=1` или `VM_ARCHITECT_MODE=1`:

- собирается `decision_path` на каждый сегмент;
- пишется `output/sessions/<UUID>/conflict_resolver_report.json` (или `output/dev/`).

### Пример структуры JSON

```json
{
  "task_id": "abc12345",
  "overlaps_resolved": 2,
  "strategy_counts": {
    "free_window": 1,
    "local_shift": 1,
    "local_reflow": 0,
    "safe_stretch": 0,
    "overflow": 0,
    "intact": 8
  },
  "intervention_map": {
    "total_segments": 10,
    "intact_pct": 80.0,
    "shift_only_pct": 20.0,
    "stretch_only_pct": 0.0,
    "overflow_pct": 0.0,
    "timeline_drift_ms": 0,
    "overlaps_resolved": 2
  },
  "profile": {
    "total_ms": 1.234,
    "avg_segment_ms": 0.15,
    "max_segment_ms": 0.42,
    "segment_count": 10
  },
  "segments": [
    {
      "idx": 1,
      "original_start_ms": 920,
      "place_start_ms": 820,
      "duration_ms": 840,
      "atempo": 1.0,
      "status": "reflow",
      "strategy": "local_reflow",
      "decision_path": ["conflict:overlap=160ms with idx=2", "local_reflow:save=160ms"],
      "lip_delta_ms": 100
    }
  ]
}
```

### Детерминированность

- без `random`;
- сортировка по `(place_start_ms, idx)`;
- фиксированный шаг local_shift (10 ms);
- unit-тест `test_identical_results_on_repeat` подтверждает 100% повторяемость.

---

## 5. Golden Regression Suite

Эталонные видео (6 сценариев из ТЗ) **не включены в репозиторий**. Автоматическое сравнение drift/OVERFLOW по Golden-пачке **не выполнялось** — требуется ручной прогон на стороне заказчика.

Рекомендуемый чеклист при наличии Golden Dataset:

1. Прогнать все 6 роликов до и после 3A.
2. Сравнить `conflict_resolver_report.json`: OVERFLOW, drift, avg atempo.
3. Субъективно проверить липсинк и ритм на видео №2 (быстрый диалог) и №6 (длинная сессия, drift=0).

---

## 6. Автотесты

**Новый файл:** `tests/test_conflict_resolver.py` (11 тестов)

Покрытие:

- устранение overlap без domino;
- детерминизм;
- приоритет shift перед stretch;
- OVERFLOW при невозможности укладки;
- Lip Timing clamp;
- Intervention Map;
- `apply_resolver_to_fitted` / JSON report.

**Полный прогон:** `python -m pytest tests/ -q` — **218 passed** (207 существующих + 11 новых).

---

## 7. DoD Этапа 3A (чеклист)

| # | Критерий | Статус |
|---|----------|--------|
| 1 | Нет наложений (локальный resolver) | ✅ алгоритм + post-fit detect |
| 2 | Нет каскадного сдвига | ✅ правка только seg[i], не i+1 |
| 3 | Приоритет естественного темпа | ✅ shift/reflow до stretch |
| 4 | Lip-sync | ✅ lip clamp + score_shift |
| 5 | Минимум вмешательств | ✅ intact % в Intervention Map |
| 6 | OVERFLOW ≤ 5% на Golden | ⏳ Golden видео не в repo |
| 7 | Детерминированность | ✅ тест + фиксированный порядок |
| 8 | 207+ автотестов | ✅ 218 passed |
| 9 | Субъективное качество | ⏳ ручная проверка на эталонах |
| 10 | Производительность | ✅ профилирование в profile |

---

## 8. Исключено из scope (3B / 3C)

- Crossfade / Rubber Band
- Audio Ducking / mix balance
- Изменения UI, translation, TTS

---

## 9. Изменённые файлы

| Файл | Изменение |
|------|-----------|
| `engines/conflict_resolver.py` | **NEW** — Conflict Resolver |
| `engines/timing_fit.py` | интеграция после fit, поля `original_start_ms` / `slot_end_ms` |
| `tests/test_conflict_resolver.py` | **NEW** — 11 unit-тестов |
| `STAGE3A_REPORT.md` | этот отчёт |
