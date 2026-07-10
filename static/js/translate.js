/* TubeDub — Translate section (Universal Translation Pipeline) */

const translateState = {
  sessionId: '',
  timingMap: [],
  sourceLang: '',
  targetLang: 'ru',
  inspectorData: null,
  inspectorIndex: 0,
};

function trEsc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function trSetStatus(msg) {
  const el = document.getElementById('status-text');
  if (el) el.textContent = msg;
}

function trUpdateDevButtons() {
  const insp = document.getElementById('btn-translate-inspector');
  const dev = typeof isDevMode === 'function' && isDevMode();
  if (insp) insp.style.display = dev ? 'inline-flex' : 'none';
}

function triggerSrt() {
  document.getElementById('srt-input')?.click();
}

function triggerVideoStt() {
  document.getElementById('video-input')?.click();
}

function triggerAudioStt() {
  document.getElementById('audio-input')?.click();
}

function loadSrt(input) {
  const file = input.files?.[0];
  if (!file) return;
  const rd = new FileReader();
  rd.onload = e => {
    document.getElementById('source-box').value = e.target.result;
    trUpdateCounts();
    trSetStatus('SRT загружен — нажмите «Перевести»');
    translateState.timingMap = [];
  };
  rd.readAsText(file, 'utf-8');
  input.value = '';
}

async function uploadStt(file, kind) {
  if (!file) return;
  const prepared = await runLanguagePackPrepare({
    source_lang: translateState.sourceLang || 'en',
    target_lang: translateState.targetLang || 'ru',
    feature: 'stt',
  });
  if (!prepared) return;
  const url = kind === 'video' ? '/api/translate/stt/video' : '/api/translate/stt/audio';
  trSetStatus(kind === 'video' ? '⏳ Извлечение аудио и распознавание…' : '⏳ Распознавание речи…');
  if (typeof vmSetWorkBusy === 'function') vmSetWorkBusy(true);
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(url, { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || 'STT failed');
    document.getElementById('source-box').value = d.text || '';
    translateState.timingMap = d.timing_map || [];
    translateState.sourceLang = d.detected || '';
    if (d.detected_name) {
      document.getElementById('detected-lang').textContent = '🌐 ' + d.detected_name;
    }
    trUpdateCounts();
    trSetStatus('✅ Текст распознан — нажмите «Перевести»');
    vmNotify('Текст помещён в исходное окно', 'success', 2500);
  } catch (e) {
    const msg = typeof vmFriendlyError === 'function' ? vmFriendlyError(String(e.message || e)) : String(e);
    trSetStatus('⚠️ ' + msg);
    vmNotify(msg, 'error');
  } finally {
    if (typeof vmSetWorkBusy === 'function') vmSetWorkBusy(false);
  }
}

function onVideoSelected(input) {
  const f = input.files?.[0];
  uploadStt(f, 'video');
  input.value = '';
}

function onAudioSelected(input) {
  const f = input.files?.[0];
  uploadStt(f, 'audio');
  input.value = '';
}

async function doTranslate(clean) {
  const text = document.getElementById('source-box')?.value.trim();
  if (!text) {
    vmNotify('Введите текст для перевода', 'warning', 2500);
    return;
  }
  const target = document.getElementById('target-lang')?.value || 'ru';
  translateState.targetLang = target;

  const srcForPrepare = translateState.sourceLang || 'en';
  const prepared = await runLanguagePackPrepare({
    source_lang: srcForPrepare,
    target_lang: target,
    feature: 'translate',
  });
  if (!prepared) return;

  trSetStatus('⏳ Universal Translation Pipeline…');
  if (typeof vmSetWorkBusy === 'function') vmSetWorkBusy(true);

  const body = {
    text,
    target,
    clean: clean !== false,
    timing_map: translateState.timingMap,
    source: translateState.sourceLang || undefined,
  };

  try {
    const r = await fetch('/api/translate/pipeline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || 'translate failed');

    document.getElementById('result-box').value = d.translated || '';
    if (d.cleaned) document.getElementById('source-box').value = d.cleaned;
    if (d.timing_map?.length) translateState.timingMap = d.timing_map;
    translateState.sessionId = d.session_id || '';
    translateState.sourceLang = d.detected || translateState.sourceLang;
    updateTranslateLogHint(d.session_id, d.log_path);

    if (d.detected_name) {
      document.getElementById('detected-lang').textContent = '🌐 ' + d.detected_name;
    }
    trUpdateCounts();
    const eng = (d.engines || []).join(', ');
    trSetStatus(
      d.segment_count
        ? `✅ Переведено ${d.segment_count} сегм.${eng ? ' · ' + eng : ''}`
        : '✅ Готово'
    );
    vmNotify('✅ Перевод готов', 'success', 2500);

    const inspBtn = document.getElementById('btn-translate-inspector');
    if (inspBtn && d.session_id && typeof isDevMode === 'function' && isDevMode()) {
      inspBtn.disabled = false;
    }
  } catch (e) {
    const msg = typeof vmFriendlyError === 'function' ? vmFriendlyError(String(e.message || e)) : String(e);
    trSetStatus('⚠️ ' + msg);
    vmNotify(msg, 'error');
  } finally {
    if (typeof vmSetWorkBusy === 'function') vmSetWorkBusy(false);
  }
}

function updateTranslateLogHint(sessionId, logPath) {
  const el = document.getElementById('translate-log-hint');
  if (!el) return;
  if (sessionId) {
    const tail = logPath ? logPath.split(/[/\\]/).pop() : sessionId.slice(0, 8);
    el.textContent = `Последний лог: ${tail}`;
  } else {
    el.textContent = '';
  }
}

async function openLastTranslateLog() {
  try {
    const r = await fetch('/api/translate/logs/open-last', { method: 'POST' });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'no_logs');
    vmNotify('Лог открыт', 'success', 2000);
  } catch (e) {
    vmNotify(typeof vmFriendlyError === 'function' ? vmFriendlyError(e.message) : e.message, 'warning', 3500);
  }
}

async function openTranslateLogsFolder() {
  try {
    const r = await fetch('/api/translate/logs/open-folder', { method: 'POST' });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'failed');
  } catch (e) {
    vmNotify(typeof vmFriendlyError === 'function' ? vmFriendlyError(e.message) : e.message, 'error');
  }
}

async function copyFullTranslateReport() {
  const sid = translateState.sessionId;
  if (!sid) {
    vmNotify('Сначала выполните перевод', 'warning', 2500);
    return;
  }
  try {
    const r = await fetch('/api/translate/logs/report?session_id=' + encodeURIComponent(sid));
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'export failed');
    const text = d.text || '';
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    vmNotify('Полный отчёт скопирован', 'success', 2500);
  } catch (e) {
    vmNotify(typeof vmFriendlyError === 'function' ? vmFriendlyError(e.message) : e.message, 'error');
  }
}

async function clearTranslateLogs() {
  if (!confirm('Удалить все сохранённые логи перевода?')) return;
  try {
    const r = await fetch('/api/translate/logs/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed: true }),
    });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'failed');
    updateTranslateLogHint('', '');
    vmNotify('Логи очищены', 'success', 2500);
  } catch (e) {
    vmNotify(typeof vmFriendlyError === 'function' ? vmFriendlyError(e.message) : e.message, 'error');
  }
}

async function openInReader() {
  const source = document.getElementById('source-box')?.value.trim() || '';
  const translated = document.getElementById('result-box')?.value.trim() || '';
  if (!translated) {
    vmNotify('Сначала выполните перевод', 'warning', 2500);
    return;
  }
  try {
    const r = await fetch('/api/translate/reader', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_text: source,
        translated_text: translated,
        timing_map: translateState.timingMap,
        source_lang: translateState.sourceLang,
        target_lang: translateState.targetLang,
      }),
    });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || 'reader failed');
    window.location.href = d.reader_url || '/reader';
  } catch (e) {
    vmNotify(typeof vmFriendlyError === 'function' ? vmFriendlyError(e.message) : e.message, 'error');
  }
}

function _tiStageClass(st) {
  if (!st.ok || (st.issues && st.issues.length)) {
    const bad = (st.issues || []).some(i =>
      /placeholder|encoding|missing_entity|english|chinese|integrity|nonsense/i.test(i)
    );
    return bad || !st.ok ? 'ti-error' : 'ti-warn';
  }
  return 'ti-ok';
}

function renderTranslateInspector(report) {
  const body = document.getElementById('tr-inspector-body');
  const summaryEl = document.getElementById('tr-inspector-summary');
  const sel = document.getElementById('tr-inspector-seg');
  if (!body || !report) return;

  const segs = report.segments || [];
  if (sel) {
    sel.innerHTML = segs.map((s, i) =>
      `<option value="${i}"${i === translateState.inspectorIndex ? ' selected' : ''}>#${s.index}</option>`
    ).join('');
  }

  const failed = report.failed_transitions || [];
  if (summaryEl) {
    summaryEl.innerHTML = failed.length
      ? `<div class="ti-summary-err">${failed.length} проблем(а) — красные этапы</div>`
      : `<div class="ti-summary-ok">Проверки целостности пройдены</div>`;
  }

  const seg = segs[translateState.inspectorIndex];
  if (!seg) {
    body.innerHTML = '<p class="char-count">Нет сегментов</p>';
    return;
  }

  let html = `<div class="ti-seg-title">Сегмент #${seg.index}</div>`;
  html += `<div class="ti-kv"><label>Original</label><div class="ti-text">${trEsc(seg.original)}</div></div>`;

  (seg.stages || []).forEach(st => {
    const cls = _tiStageClass(st);
    html += `<div class="ti-stage ${cls}"><div class="ti-stage-head">${trEsc(st.name || st.id)}`;
    if (st.ms) html += ` <span class="ti-ms">${st.ms} ms</span>`;
    html += '</div>';

    if (st.id === 'translation_request') {
      const m = st.meta || {};
      html += `<div class="ti-meta">`;
      html += `<div><strong>Router</strong></div>`;
      html += `<div>Route: ${trEsc(m.route || seg.route || '')}</div>`;
      html += `<div>Engine: ${trEsc(m.engine || seg.engine || '')}</div>`;
      html += `<div>Model: ${trEsc(m.model || '')}</div>`;
      html += `<div>Причина: ${trEsc(m.router_reason || '')}</div>`;
      html += `</div>`;
    } else if (st.id === 'raw_mt') {
      html += `<div class="ti-kv"><label>Raw MT</label><div class="ti-text">${trEsc(st.text)}</div></div>`;
    } else if (st.id === 'serialization') {
      html += `<div class="ti-kv"><label>Placeholder Serialize</label><div class="ti-text">${trEsc(st.text)}</div></div>`;
      if (st.integrity?.placeholder_count) {
        html += `<div class="ti-meta">Placeholders: ${st.integrity.placeholder_count}</div>`;
      }
    } else if (st.id === 'restore') {
      html += `<div class="ti-kv"><label>Placeholder Restore</label><div class="ti-text">${trEsc(st.text)}</div></div>`;
    } else if (st.id === 'natural') {
      html += `<div class="ti-kv"><label>Natural Translation</label><div class="ti-text">${trEsc(st.text)}</div></div>`;
    } else if (st.id === 'final') {
      html += `<div class="ti-kv"><label>Final Translation</label><div class="ti-text">${trEsc(st.text)}</div></div>`;
    } else if (st.id === 'entity_detection') {
      const ents = st.entities || String(st.text || '').split('\n').filter(Boolean);
      html += `<div class="ti-entities">${ents.map(e => `<div>${trEsc(e)}</div>`).join('')}</div>`;
    } else if (st.text) {
      html += `<div class="ti-text">${trEsc(st.text)}</div>`;
    }

    if (st.issues?.length) {
      html += `<div class="ti-issues">${st.issues.map(i => `<span>${trEsc(i)}</span>`).join(' ')}</div>`;
    }
    html += '</div>';
  });

  const q = seg.quality || {};
  html += `<div class="ti-quality"><strong>Quality Score</strong>`;
  Object.entries(q).forEach(([k, v]) => { html += `<div>${trEsc(k)}: ${v}</div>`; });
  if (q.enterprise) {
    html += `<div class="ti-meta">Enterprise Translation: да</div>`;
  }
  html += '</div>';

  const warns = seg.warnings || [];
  if (warns.length) {
    html += `<div class="ti-warns"><strong>Warnings</strong>`;
    warns.forEach(w => {
      const code = typeof w === 'object' ? w.code : w;
      html += `<div>${trEsc(code)}</div>`;
    });
    html += '</div>';
  }

  (seg.transitions || []).forEach(tr => {
    if (!tr.ok) {
      html += `<div class="ti-transition ti-error">${trEsc(tr.from)} → ${trEsc(tr.to)}: ${trEsc((tr.issues || []).join(', '))}</div>`;
    }
  });

  body.innerHTML = html;
}

async function openTranslateInspector() {
  if (typeof isDevMode !== 'function' || !isDevMode()) return;
  if (!translateState.sessionId) {
    vmNotify('Сначала выполните перевод', 'warning', 2500);
    return;
  }
  const overlay = document.getElementById('tr-inspector-overlay');
  if (overlay) overlay.style.display = 'flex';
  const body = document.getElementById('tr-inspector-body');
  if (body) body.innerHTML = '<div class="char-count">Загрузка…</div>';

  try {
    const r = await fetch('/api/translate/inspector/' + encodeURIComponent(translateState.sessionId));
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'load failed');
    translateState.inspectorData = d.inspector;
    translateState.inspectorIndex = 0;
    renderTranslateInspector(d.inspector);
  } catch (e) {
    if (body) body.innerHTML = '<p class="ti-summary-err">' + trEsc(e.message) + '</p>';
  }
}

function closeTranslateInspector() {
  const overlay = document.getElementById('tr-inspector-overlay');
  if (overlay) overlay.style.display = 'none';
}

function selectTranslateInspectorSeg(idx) {
  translateState.inspectorIndex = parseInt(idx, 10) || 0;
  if (translateState.inspectorData) renderTranslateInspector(translateState.inspectorData);
}

async function copyInspectorReport() {
  if (!translateState.sessionId) return;
  try {
    const r = await fetch('/api/translate/inspector/' + encodeURIComponent(translateState.sessionId) + '/export');
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'export failed');
    const text = d.text || '';
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    vmNotify('Отчёт скопирован в буфер обмена', 'success', 2500);
  } catch (e) {
    vmNotify(typeof vmFriendlyError === 'function' ? vmFriendlyError(e.message) : e.message, 'error');
  }
}

function clearTranslate() {
  ['source-box', 'result-box'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  translateState.sessionId = '';
  translateState.timingMap = [];
  updateTranslateLogHint('', '');
  document.getElementById('detected-lang').textContent = 'Язык: определится автоматически';
  trUpdateCounts();
  trSetStatus('Готов к работе');
}

function downloadTranslation() {
  const text = document.getElementById('result-box')?.value;
  if (!text) return;
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'translation.txt';
  a.click();
  URL.revokeObjectURL(a.href);
}

function trUpdateCounts() {
  const sc = document.getElementById('source-chars');
  const rc = document.getElementById('result-chars');
  const sb = document.getElementById('source-box');
  const rb = document.getElementById('result-box');
  if (sc && sb) sc.textContent = sb.value.length + ' симв.';
  if (rc && rb) rc.textContent = rb.value.length + ' симв.';
}

document.addEventListener('DOMContentLoaded', () => {
  trUpdateDevButtons();
  fetch('/api/translate/logs/last')
    .then(r => r.ok ? r.json() : null)
    .then(d => { if (d?.ok) updateTranslateLogHint(d.session_id, d.path); })
    .catch(() => {});
  if (typeof window.addEventListener === 'function') {
    window.addEventListener('vm-mode-changed', trUpdateDevButtons);
  }

  const _TR_KEY = 'vm_translate_src';
  try {
    const saved = localStorage.getItem(_TR_KEY);
    if (saved) {
      const sb = document.getElementById('source-box');
      if (sb) { sb.value = saved; trUpdateCounts(); }
    }
  } catch (e) { /* ignore */ }

  let detectTimer = null;
  const sb = document.getElementById('source-box');
  if (sb) {
    sb.addEventListener('input', () => {
      trUpdateCounts();
      clearTimeout(detectTimer);
      const text = sb.value.trim();
      try { localStorage.setItem(_TR_KEY, text.slice(0, 12000)); } catch (e) { /* ignore */ }
      if (text.length > 35) {
        detectTimer = setTimeout(() => {
          fetch('/api/detect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text.slice(0, 300) }),
          })
            .then(r => r.json())
            .then(d => {
              if (d.name || d.lang) {
                document.getElementById('detected-lang').textContent = '🌐 ' + (d.name || d.lang);
                translateState.sourceLang = d.lang || '';
              }
            })
            .catch(() => {});
        }, 750);
      }
    });
    sb.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        doTranslate(true);
      }
    });
  }

  const rb = document.getElementById('result-box');
  if (rb) rb.addEventListener('input', trUpdateCounts);
});
