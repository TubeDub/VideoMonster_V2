/* studio.js — логика страницы Студия */

let timingMap = [];
let studioSegments = [];
window.studioSegments = studioSegments;

const TIMING_HINTS = {
  exact: "💡 Каждый сегмент размещается строго на своём тайм-коде из SRT.",
  preserve_pauses: "💡 Паузы между сегментами сохраняются пропорционально оригиналу.",
  match_total: "💡 Вся дорожка растягивается/сжимается под указанную общую длину.",
};

function _workBusy(on) {
  if (typeof vmSetWorkBusy === "function") vmSetWorkBusy(on);
}

function initCounts() {
  ["source-box", "translated-box"].forEach(id => {
    const el = document.getElementById(id);
    const cntId = id === "source-box" ? "source-chars" : "translated-chars";
    if (el) el.addEventListener("input", () => updateCount(id, cntId));
  });
}

function clearAll() {
  ["source-box", "translated-box"].forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.value = ""; el.dispatchEvent(new Event("input")); }
  });
  timingMap = [];
  studioSegments = [];
  renderSegmentsEditor();
  const dl = document.getElementById("detected-lang");
  if (dl) dl.textContent = "Язык: не определён";
  setStatus("Очищено");
}

function triggerSrtLoad() {
  document.getElementById("srt-input")?.click();
}

function applyStudioImport(data) {
  if (!data) return;
  if (data.segments && data.segments.length) {
    studioSegments = data.segments.map((s, i) => ({
      index: s.index || i + 1,
      start_ms: s.start_ms || 0,
      end_ms: s.end_ms || (s.start_ms || 0) + 3000,
      text: s.text || "",
    }));
    timingMap = data.timing_map || studioSegments.map(s => ({ start: s.start_ms, end: s.end_ms }));
    renderSegmentsEditor();
    applySegmentsToSource();
  } else if (data.text) {
    const box = document.getElementById("source-box");
    if (box) { box.value = data.text; box.dispatchEvent(new Event("input")); }
    syncSegmentsFromSource();
  }
  setStatus(`Импорт: ${data.filename || "файл"} · сегментов: ${studioSegments.length}`);
}

function loadSubtitleFile(input) {
  const file = input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  setStatus("Импорт субтитров…");
  fetch("/api/studio/import", { method: "POST", body: fd })
    .then(r => r.json())
    .then(d => {
      if (d.error) { setStatus("Ошибка: " + d.error); return; }
      applyStudioImport({ ...d, filename: file.name });
    })
    .catch(e => setStatus("Ошибка загрузки: " + e));
  input.value = "";
}

function loadUniversalImport() {
  if (typeof vmConsumeUniversalImport !== "function") return;
  vmConsumeUniversalImport({
    subtitles: (d) => applyStudioImport(d),
  });
}

function renderSegmentsEditor() {
  const root = document.getElementById("segments-editor");
  if (!root) return;
  if (!studioSegments.length) {
    root.innerHTML = '<div style="font-size:12px;color:var(--text2);">Загрузите SRT/VTT/ASS или нажмите «Из текста»</div>';
    return;
  }
  root.innerHTML = studioSegments.map((seg, i) => `
    <div class="segment-row" style="display:flex;gap:8px;align-items:flex-start;margin-bottom:8px;">
      <span style="font-size:11px;color:var(--text2);min-width:28px;">${i + 1}</span>
      <textarea data-seg-index="${i}" class="input-control" rows="2" style="flex:1;min-height:44px;">${escapeHtml(seg.text)}</textarea>
    </div>
  `).join("");
  root.querySelectorAll("textarea[data-seg-index]").forEach(el => {
    el.addEventListener("input", () => {
      const idx = parseInt(el.getAttribute("data-seg-index"), 10);
      if (studioSegments[idx]) studioSegments[idx].text = el.value;
    });
  });
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function syncSegmentsFromSource() {
  const text = document.getElementById("source-box")?.value.trim();
  if (!text) { setStatus("Нет текста"); return; }
  fetch("/api/clean", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  })
    .then(r => r.json())
    .then(d => {
      const lines = (d.cleaned || text).split("\n").filter(l => l.trim());
      const map = d.timing_map || [];
      studioSegments = lines.map((line, i) => {
        const tm = map[i];
        let start_ms = i * 3200;
        let end_ms = start_ms + 3000;
        if (tm && typeof tm === "object" && tm.start != null) {
          start_ms = tm.start;
          end_ms = tm.end || start_ms + 3000;
        }
        return { index: i + 1, start_ms, end_ms, text: line.trim() };
      });
      timingMap = studioSegments.map(s => ({ start: s.start_ms, end: s.end_ms }));
      renderSegmentsEditor();
      window.studioSegments = studioSegments;
      if (window._studioTimeline) window._studioTimeline.syncFromStudioJs();
      setStatus(`Сегментов: ${studioSegments.length}`);
    });
}

function applySegmentsToSource() {
  const text = studioSegments.map(s => s.text).join("\n");
  const box = document.getElementById("source-box");
  if (box) { box.value = text; box.dispatchEvent(new Event("input")); }
  timingMap = studioSegments.map(s => ({ start: s.start_ms, end: s.end_ms }));
  _updateTimingBadge();
}

function exportSubs(format) {
  if (!studioSegments.length) syncSegmentsFromSource();
  const segments = studioSegments.length
    ? studioSegments
    : (document.getElementById("translated-box")?.value || "")
        .split("\n")
        .filter(l => l.trim())
        .map((text, i) => ({ index: i + 1, start_ms: i * 3200, end_ms: i * 3200 + 3000, text: l.trim() }));

  if (!segments.length) { setStatus("Нет сегментов для экспорта"); return; }

  fetch("/api/studio/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format, segments, name: "studio" }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.error) { setStatus("Ошибка: " + d.error); return; }
      if (d.download) window.location.href = d.download;
      setStatus(`Экспорт ${format.toUpperCase()} готов`);
    });
}

function goRedub() {
  applySegmentsToSource();
  const sourceText = document.getElementById("source-box")?.value || "";
  const translatedText = document.getElementById("translated-box")?.value || "";
  fetch("/api/studio/prepare_redub", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_text: sourceText,
      translated_text: translatedText,
      segments: studioSegments,
      timing_map: timingMap,
      skip_translate: Boolean(translatedText.trim()),
    }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.error) { setStatus("Ошибка: " + d.error); return; }
      window.location.href = d.route || `/dub?redub=${d.redub_id}`;
    });
}

function doTranslateOnly() {
  _runTranslate(false);
}

function doCleanAndTranslate() {
  _runTranslate(true);
}

function doCleanOnly() {
  const text = document.getElementById("source-box")?.value.trim();
  if (!text) { setStatus("Нет текста"); return; }
  setStatus("Очистка SRT...");
  _workBusy(true);
  fetch("/api/clean", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  })
    .then(r => r.json())
    .then(d => {
      const box = document.getElementById("source-box");
      if (box) { box.value = d.cleaned; box.dispatchEvent(new Event("input")); }
      timingMap = d.timing_map || [];
      syncSegmentsFromSource();
      setStatus(`Очищено: ${d.lines} строк, тайм-кодов: ${timingMap.length}`);
    })
    .finally(() => _workBusy(false));
}

function _runTranslate(clean) {
  const text = document.getElementById("source-box")?.value.trim();
  if (!text) { setStatus("Нет текста"); return; }
  const target = document.getElementById("target-lang")?.value
    || getDefaultTargetLang();
  setStatus("Перевод...");
  _workBusy(true);
  fetch("/api/translate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, target, clean }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.error) { setStatus("Ошибка: " + d.error); return; }
      const tb = document.getElementById("translated-box");
      if (tb) { tb.value = d.translated; tb.dispatchEvent(new Event("input")); }
      if (clean && d.cleaned) {
        const sb = document.getElementById("source-box");
        if (sb) { sb.value = d.cleaned; sb.dispatchEvent(new Event("input")); }
      }
      timingMap = d.timing_map || timingMap;
      if (Array.isArray(timingMap) && timingMap.length && typeof timingMap[0] === "string") {
        studioSegments = (d.translated || text).split("\n").filter(l => l.trim()).map((line, i) => ({
          index: i + 1,
          start_ms: i * 3200,
          end_ms: i * 3200 + 3000,
          text: line.trim(),
        }));
      }
      renderSegmentsEditor();
      _updateTimingBadge();
      const dl = document.getElementById("detected-lang");
      if (dl && d.detected_name)
        dl.textContent = "Язык: " + d.detected_name;
      setStatus(
        `Переведено${d.review_count ? " (убрано " + d.review_count + " строк SRT)" : ""}` +
        (timingMap.length ? ` · тайм-кодов: ${timingMap.length}` : "")
      );
    })
    .catch(e => setStatus("Ошибка: " + e))
    .finally(() => _workBusy(false));
}

function toggleTimingOptions() {
  const on = document.getElementById("use-timing").checked;
  document.getElementById("timing-options").style.display = on ? "flex" : "none";
}

function onTimingModeChange() {
  const mode = document.getElementById("timing-mode").value;
  const hint = document.getElementById("timing-mode-hint");
  if (hint) hint.textContent = TIMING_HINTS[mode] || "";
  const durGroup = document.getElementById("total-duration-group");
  if (durGroup) durGroup.style.display = mode === "match_total" ? "flex" : "none";
}

function _updateTimingBadge() {
  const hint = document.getElementById("timing-mode-hint");
  const count = Array.isArray(timingMap) ? timingMap.length : 0;
  if (hint && count > 0) {
    const mode = document.getElementById("timing-mode")?.value || "exact";
    hint.textContent = (TIMING_HINTS[mode] || "") +
      ` (${count} тайм-кодов загружено)`;
  }
}

function _normalizeTimingForTts(map) {
  if (!Array.isArray(map) || !map.length) return map;
  if (typeof map[0] === "object" && map[0].start != null) return map;
  return map.map((item, i) => {
    if (typeof item === "object" && item.start != null) return item;
    const start = i * 3200;
    return { start, end: start + 3000 };
  });
}

function _buildTTSPayload(text) {
  const voice = document.getElementById("voice-select")?.value || getDefaultVoice();
  const useTiming = document.getElementById("use-timing")?.checked || false;
  const timingMode = document.getElementById("timing-mode")?.value || "exact";
  const totalDuration = document.getElementById("total-duration")?.value || "";
  return {
    text,
    voice,
    timing_map: _normalizeTimingForTts(timingMap),
    use_timing: useTiming,
    timing_mode: timingMode,
    total_duration: totalDuration,
  };
}

function doTTS() {
  const text = document.getElementById("translated-box")?.value.trim();
  if (!text) { setStatus("Нет текста для озвучки"); return; }
  setStatus("Генерация аудио...");
  showProgress(true);
  _workBusy(true);
  fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(_buildTTSPayload(text)),
  })
    .then(r => r.json())
    .then(d => {
      showProgress(false);
      if (d.error) { setStatus("Ошибка: " + d.error); return; }
      showAudioResult(d);
      showTimedAudio(d);
      const msg = `Озвучка завершена! Файлов: ${d.count}` +
        (d.timed_file ? " + тайминговая дорожка" : "");
      setStatus(msg);
    })
    .catch(e => { showProgress(false); setStatus("Ошибка TTS: " + e); })
    .finally(() => _workBusy(false));
}

function showTimedAudio(d) {
  const section = document.getElementById("timed-section");
  const container = document.getElementById("timed-player");
  const warningsEl = document.getElementById("timing-warnings");
  if (!section || !container) return;
  if (!d.timed_file) { section.style.display = "none"; return; }

  container.innerHTML = "";
  const wrapper = document.createElement("div");
  wrapper.className = "audio-item";

  const label = document.createElement("span");
  label.className = "audio-label";
  label.textContent = "Тайминг";

  const audio = document.createElement("audio");
  audio.controls = true;
  audio.className = "audio-player";
  audio.src = d.timed_stream || `/api/stream/${d.timed_file}`;

  const dl = document.createElement("a");
  dl.href = d.timed_download || `/api/download/${d.timed_file}`;
  dl.download = "";
  dl.className = "btn btn-sm";
  dl.textContent = "⬇ Скачать";

  wrapper.append(label, audio, dl);
  container.appendChild(wrapper);

  if (warningsEl) {
    warningsEl.textContent = (d.warnings || []).join(" | ");
  }
  section.style.display = "block";
}

function showProgress(on) {
  const bar = document.getElementById("progress-bar");
  if (bar) bar.style.display = on ? "block" : "none";
  if (on) {
    const fill = document.getElementById("progress-fill");
    if (fill) { fill.style.width = "0%"; _animateProgress(fill); }
  }
}

function _animateProgress(fill) {
  let pct = 0;
  const timer = setInterval(() => {
    pct = Math.min(pct + Math.random() * 8, 90);
    fill.style.width = pct + "%";
    if (pct >= 90) clearInterval(timer);
  }, 300);
  fill._timer = timer;
}

function saveDocument() {
  const sourceText = document.getElementById("source-box")?.value || "";
  const translatedText = document.getElementById("translated-box")?.value || "";
  const voice = document.getElementById("voice-select")?.value || getDefaultVoice();
  const useTiming = document.getElementById("use-timing")?.checked || false;
  const timingMode = document.getElementById("timing-mode")?.value || "exact";
  fetch("/api/save_vmr", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_text: sourceText,
      translated_text: translatedText,
      timing_map: _normalizeTimingForTts(timingMap),
      voice,
      use_timing: useTiming,
      timing_mode: timingMode,
    }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.download) window.location.href = d.download;
    });
}

function downloadTxt() {
  const text = document.getElementById("translated-box")?.value || "";
  if (!text) return;
  downloadTextBlob(text, "translation.txt");
}

function initStudioTextPage() {
  initCounts();
  renderSegmentsEditor();
  loadUniversalImport();
  const dev = document.cookie.includes("vm_client_dev_mode=1") ||
    localStorage.getItem("vm_client_dev_mode") === "1";
  fetch("/api/features/check/dub_studio").then(function (r) { return r.json(); }).then(function (data) {
    const on = dev || (data && data.enabled);
    if (!on) return;
    if (window.StudioTimeline && document.getElementById("studio-timeline")) {
      window._studioTimeline = new StudioTimeline(document.getElementById("studio-timeline"), {
        durationMs: 120000,
        inspectorEl: document.getElementById("studio-inspector"),
        voice: document.getElementById("voice-select")?.value,
      });
    }
  }).catch(function () {});
}

document.addEventListener("DOMContentLoaded", function () {
  const p = new URLSearchParams(window.location.search);
  if (!(p.get("task_id") || p.get("task"))) {
    initStudioTextPage();
  }
});
