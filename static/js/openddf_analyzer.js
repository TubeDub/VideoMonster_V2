/**
 * OpenDDF Analyzer 2.0 — all data loaded dynamically from OpenDDF JSON (no embedded arrays).
 */
(function () {
  'use strict';

  let report = null;
  let activeTab = 'overview';
  let selectedSegmentIndex = null;
  let logLevelFilter = 'ALL';
  let searchQuery = '';

  const app = document.getElementById('oda-app');
  const mainEl = document.getElementById('oda-main');
  const statusEl = document.getElementById('oda-status');
  const taskIdBoot = app ? app.getAttribute('data-task-id') || '' : '';

  function esc(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function bandClass(band) {
    if (band === 'green') return 'oda-band-green';
    if (band === 'yellow') return 'oda-band-yellow';
    return 'oda-band-red';
  }

  function renderDiffHtml(diff) {
    if (!diff) return '';
    const parts = [];
    (diff.removed_words || []).forEach((w) => parts.push(`<del>${esc(w)}</del>`));
    (diff.added_words || []).forEach((w) => parts.push(`<ins>${esc(w)}</ins>`));
    (diff.replaced_words || []).forEach((r) =>
      parts.push(`<del>${esc(r.from)}</del>→<ins>${esc(r.to)}</ins>`)
    );
    if (!parts.length) return '<span class="oda-muted">без изменений</span>';
    return `<span class="oda-diff">${parts.join(' ')}</span>`;
  }

  function segments() {
    return (report && report.segments) || [];
  }

  function filterSegments(list) {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return list;
    return list.filter((seg) => {
      const blob = [
        seg.index,
        seg.original_text,
        seg.translated_text,
        seg.final_tts_text,
        seg.algorithm_reason,
        JSON.stringify(seg.integrity_checks),
        JSON.stringify(seg.entities),
      ]
        .join(' ')
        .toLowerCase();
      return blob.includes(q) || String(seg.index) === q;
    });
  }

  function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg;
  }

  function setReport(r) {
    report = r;
    selectedSegmentIndex = segments().length ? segments()[0].index : null;
    setStatus(
      `v${r.analyzer_version || '?'} · task=${r.task_id || '?'} · segments=${segments().length}`
    );
    render();
  }

  async function loadByTaskId(taskId) {
    setStatus('Загрузка…');
    const r = await fetch(`/api/openddf_analyzer/report/${encodeURIComponent(taskId)}`);
    const d = await r.json();
    if (!r.ok || !d.ok) {
      setStatus(d.error || 'Ошибка загрузки');
      mainEl.innerHTML = `<div class="oda-empty">${esc(d.error || 'Не удалось загрузить')}</div>`;
      return;
    }
    setReport(d.report);
  }

  async function loadFromFile(file) {
    const fd = new FormData();
    fd.append('file', file);
    setStatus('Парсинг JSON…');
    const r = await fetch('/api/openddf_analyzer/load_json', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok || !d.ok) {
      setStatus(d.error || 'Ошибка');
      return;
    }
    setReport(d.report);
  }

  async function exportSave(kind) {
    if (!report) return;
    const r = await fetch(`/api/openddf_analyzer/export/${kind}/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ report }),
    });
    const d = await r.json();
    if (d.cancelled) return;
    if (!r.ok || !d.success) {
      alert(d.error || d.note || 'Экспорт не выполнен');
      return;
    }
    setStatus(`Сохранено: ${d.path || d.filename}${d.note ? ' — ' + d.note : ''}`);
  }

  function audioUrl(kind, idx) {
    const tid = report && report.task_id;
    if (!tid) return null;
    return `/api/openddf_analyzer/audio/${encodeURIComponent(tid)}/${idx}/${kind}`;
  }

  function renderOverview() {
    const stats = report.statistics || {};
    const flags = (report.flags || []).filter(Boolean);
    const segMap = segments();
    return `
      <div class="oda-grid oda-grid-3">
        <div class="oda-card"><h3>Сегментов</h3><div class="oda-stat-value">${stats.segment_count || 0}</div></div>
        <div class="oda-card"><h3>Средний overflow</h3><div class="oda-stat-value ${bandClass(
          stats.avg_overflow_pct <= 5 ? 'green' : stats.avg_overflow_pct <= 15 ? 'yellow' : 'red'
        )}">${stats.avg_overflow_pct || 0}%</div></div>
        <div class="oda-card"><h3>Max overflow</h3><div class="oda-stat-value oda-band-red">${stats.max_overflow_pct || 0}%</div></div>
        <div class="oda-card"><h3>English leak</h3><div class="oda-stat-value">${stats.english_leak_count || 0}</div></div>
        <div class="oda-card"><h3>Entity loss</h3><div class="oda-stat-value">${stats.entity_loss_count || 0}</div></div>
        <div class="oda-card"><h3>Failed adapt</h3><div class="oda-stat-value">${stats.failed_adaptation_count || 0}</div></div>
      </div>
      ${flags.length ? `<div class="oda-card" style="margin-top:16px"><h3>Flags</h3><p>${esc(flags.join(' · '))}</p></div>` : ''}
      <div class="oda-card" style="margin-top:16px">
        <h3>Карта сегментов</h3>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">
          ${segMap
            .map((s) => {
              const td = s.timing_detail || {};
              const band = td.overflow_band || 'green';
              return `<div class="oda-map-cell oda-band-${band}" data-goto-seg="${s.index}" title="overflow ${td.overflow_pct}%">#${s.index}</div>`;
            })
            .join('')}
        </div>
      </div>`;
  }

  function renderSegmentDetail(seg) {
    const td = seg.timing_detail || {};
    const stages = seg.pipeline_stages || [];
    const attempts = seg.adaptation_attempts || [];
    const entities = seg.entities || [];
    const checks = seg.integrity_checks || [];
    const audio = seg.audio || {};
    const links = seg.editor_links || {};

    const stageHtml = stages
      .map((st) => {
        const cls = st.status === 'ok' ? 'ok' : st.status === 'warning' ? 'warn' : st.status === 'missing' ? 'err' : '';
        return `
          <div class="oda-stage ${cls}">
            <div class="oda-stage-head">
              <span>${esc(st.name)}</span>
              <span class="oda-stage-module">${esc(st.module)} · ${st.duration_ms || 0}ms</span>
            </div>
            ${st.decision_reason ? `<div style="font-size:0.75rem;color:var(--oda-muted)">Причина: ${esc(st.decision_reason)}</div>` : ''}
            <div style="font-size:0.75rem;margin-top:4px"><b>in:</b> ${esc((st.input_text || '').slice(0, 200))}</div>
            <div style="font-size:0.75rem"><b>out:</b> ${esc((st.output_text || '').slice(0, 200))}</div>
            <div style="margin-top:4px">${renderDiffHtml(st.diff)}</div>
          </div>`;
      })
      .join('');

    const attemptHtml = attempts
      .map(
        (a) =>
          `<div style="font-size:0.8rem;margin-bottom:6px">
            <b>Attempt ${a.attempt}</b> ${esc(a.algorithm)} — <span class="${a.status === 'rejected' ? 'oda-check-fail' : 'oda-check-ok'}">${esc(a.status)}</span>
            ${a.reason ? `<br><span style="color:var(--oda-muted)">${esc(a.reason)}</span>` : ''}
            ${a.rejected_reason ? `<br><span class="oda-check-fail">${esc(a.rejected_reason)}</span>` : ''}
          </div>`
      )
      .join('');

    const entityHtml = entities
      .map(
        (e) =>
          `<div class="oda-card ${e.critical ? 'oda-entity-critical' : ''}" style="margin-bottom:8px;padding:10px">
            <div><b>${esc(e.category)}</b>: ${esc(e.value)}</div>
            <div style="font-size:0.75rem">Original → ${esc(e.original)} · Translation → ${esc(e.translation)} · Final → ${esc(e.final)}</div>
            ${e.critical ? '<div class="oda-check-fail">CRITICAL</div>' : ''}
          </div>`
      )
      .join('');

    const checkHtml = checks
      .map(
        (c) =>
          `<div class="${c.ok ? 'oda-check-ok' : 'oda-check-fail'}" style="font-size:0.8rem">${c.ok ? '✓' : '✗'} ${esc(c.code)}: ${esc(c.message)}</div>`
      )
      .join('');

    function audioBtn(label, meta, kind) {
      if (!meta || !meta.exists) return `<span style="color:var(--oda-muted)">${label}: нет</span>`;
      const url = audioUrl(kind, seg.index);
      return `<div><b>${label}</b><br><code style="font-size:0.7rem">${esc(meta.path || meta.filename)}</code>
        ${url ? `<audio controls preload="none" src="${esc(url)}" style="width:100%;margin-top:4px"></audio>` : ''}
        <span style="font-size:0.7rem;color:var(--oda-muted)">${meta.size_bytes || 0} bytes</span></div>`;
    }

    return `
      <div class="oda-card">
        <h3>Сегмент #${seg.index}</h3>
        <p style="font-size:0.8rem;color:var(--oda-muted)">${esc(seg.algorithm_reason || '')}</p>
        <div class="oda-grid oda-grid-3" style="margin-top:12px">
          <div>Slot: <b>${td.slot_duration_ms}ms</b></div>
          <div>TTS: <b>${td.playback_duration_ms}ms</b></div>
          <div class="${bandClass(td.overflow_band)}">Overflow: <b>${td.overflow_pct}%</b></div>
          <div>Underflow: ${td.underflow_ms}ms</div>
          <div>Gap absorb: ${esc((td.gap_absorb && td.gap_absorb.mode) || '—')}</div>
          <div>Block merge: ${td.block_merge && td.block_merge.block_merged_with_next ? 'yes' : 'no'}</div>
        </div>
        <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
          ${links.translation ? `<a class="btn btn-secondary btn-sm" href="${esc(links.translation)}" target="_blank">Редактор перевода</a>` : ''}
          ${links.tts ? `<a class="btn btn-secondary btn-sm" href="${esc(links.tts)}" target="_blank">Редактор TTS</a>` : ''}
          ${links.timeline ? `<a class="btn btn-secondary btn-sm" href="${esc(links.timeline)}" target="_blank">Timeline</a>` : ''}
        </div>
      </div>
      <div class="oda-card" style="margin-top:16px"><h3>Pipeline Inspector</h3><div class="oda-pipeline-chain">${stageHtml}</div></div>
      <div class="oda-card" style="margin-top:16px"><h3>История адаптации</h3>${attemptHtml || '<span class="oda-muted">нет данных</span>'}</div>
      <div class="oda-card" style="margin-top:16px"><h3>Named Entities</h3>${entityHtml || '<span class="oda-muted">нет</span>'}</div>
      <div class="oda-card" style="margin-top:16px"><h3>Целостность</h3>${checkHtml}</div>
      <div class="oda-card" style="margin-top:16px"><h3>Аудио</h3>
        <div class="oda-audio-row">
          ${audioBtn('Original', audio.original, 'original')}
          ${audioBtn('TTS', audio.tts, 'tts')}
          ${audioBtn('Fitted', audio.fitted, 'fitted')}
        </div>
      </div>`;
  }

  function renderSegments() {
    const list = filterSegments(segments());
    const sel = list.find((s) => s.index === selectedSegmentIndex) || list[0];
    if (sel) selectedSegmentIndex = sel.index;

    const listHtml = list
      .map((s) => {
        const td = s.timing_detail || {};
        return `<div class="oda-seg-item ${s.index === selectedSegmentIndex ? 'active' : ''}" data-seg="${s.index}">
          <strong>#${s.index}</strong>
          <span class="${bandClass(td.overflow_band)}">${td.overflow_pct || 0}%</span>
          <div style="font-size:0.75rem;color:var(--oda-muted);margin-top:4px">${esc((s.final_tts_text || '').slice(0, 80))}</div>
        </div>`;
      })
      .join('');

    return `<div class="oda-layout-2">
      <div class="oda-card"><h3>Сегменты (${list.length})</h3><div class="oda-seg-list">${listHtml}</div></div>
      <div>${sel ? renderSegmentDetail(sel) : '<div class="oda-empty">Нет сегментов</div>'}</div>
    </div>`;
  }

  function renderTimeline() {
    const tl = report.pipeline_timeline || [];
    const max = Math.max(...tl.map((t) => t.duration_ms || 0), 1);
    const bars = tl
      .map((t) => {
        const h = Math.max(4, Math.round(((t.duration_ms || 0) / max) * 100));
        return `<div class="oda-timeline-col">
          <div class="oda-timeline-fill" style="height:${h}px" title="${t.duration_ms}ms"></div>
          <div style="font-size:0.65rem;margin-top:4px">${esc(t.label || t.id)}</div>
          <div style="font-size:0.6rem;color:var(--oda-muted)">${t.duration_ms || 0}ms</div>
        </div>`;
      })
      .join('');
    return `<div class="oda-card"><h3>Pipeline Timeline</h3>
      <p style="font-size:0.8rem;color:var(--oda-muted)">STT → Translation → Semantic → Adaptation → TTS → Slot Fit → Mix → MP4</p>
      <div class="oda-timeline-bar">${bars}</div></div>`;
  }

  function renderLogs() {
    let logs = (report.runtime_logs || []).slice();
    if (logLevelFilter !== 'ALL') logs = logs.filter((l) => l.level === logLevelFilter);
    const q = searchQuery.trim().toLowerCase();
    if (q) logs = logs.filter((l) => JSON.stringify(l).toLowerCase().includes(q));

    const levels = ['ALL', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
    const filters = levels
      .map(
        (lv) =>
          `<button type="button" class="btn btn-secondary btn-sm oda-log-filter" data-level="${lv}" style="${logLevelFilter === lv ? 'border-color:var(--oda-accent)' : ''}">${lv}</button>`
      )
      .join(' ');

    const rows = logs
      .map(
        (l) =>
          `<div class="oda-log-row oda-log-${l.level}" data-log-seg="${l.segment_index != null ? l.segment_index : ''}">
            <span>${esc(l.time)}</span> [${esc(l.level)}] <b>${esc(l.module)}</b> ${esc(l.message)}
          </div>`
      )
      .join('');

    return `<div class="oda-card"><h3>Runtime Log (${logs.length})</h3>
      <div style="margin-bottom:12px">${filters}</div>
      <div style="max-height:480px;overflow-y:auto">${rows || '<div class="oda-empty">нет логов</div>'}</div></div>`;
  }

  function renderStatistics() {
    const s = report.statistics || {};
    const rows = Object.entries(s)
      .map(([k, v]) => `<tr><td>${esc(k)}</td><td><b>${esc(v)}</b></td></tr>`)
      .join('');
    return `<div class="oda-card"><h3>Статистика</h3>
      <table style="width:100%;font-size:0.85rem;border-collapse:collapse">${rows}</table></div>`;
  }

  function render() {
    if (!report) return;
    let html = '';
    switch (activeTab) {
      case 'overview':
        html = renderOverview();
        break;
      case 'segments':
        html = renderSegments();
        break;
      case 'timeline':
        html = renderTimeline();
        break;
      case 'logs':
        html = renderLogs();
        break;
      case 'statistics':
        html = renderStatistics();
        break;
      default:
        html = renderOverview();
    }
    mainEl.innerHTML = html;
    bindMainEvents();
  }

  function bindMainEvents() {
    mainEl.querySelectorAll('[data-seg]').forEach((el) => {
      el.addEventListener('click', () => {
        selectedSegmentIndex = parseInt(el.getAttribute('data-seg'), 10);
        activeTab = 'segments';
        document.getElementById('oda-tab-select').value = 'segments';
        render();
      });
    });
    mainEl.querySelectorAll('[data-goto-seg]').forEach((el) => {
      el.addEventListener('click', () => {
        selectedSegmentIndex = parseInt(el.getAttribute('data-goto-seg'), 10);
        activeTab = 'segments';
        document.getElementById('oda-tab-select').value = 'segments';
        render();
      });
    });
    mainEl.querySelectorAll('.oda-log-filter').forEach((btn) => {
      btn.addEventListener('click', () => {
        logLevelFilter = btn.getAttribute('data-level');
        render();
      });
    });
    mainEl.querySelectorAll('[data-log-seg]').forEach((row) => {
      row.addEventListener('click', () => {
        const idx = row.getAttribute('data-log-seg');
        if (idx) {
          selectedSegmentIndex = parseInt(idx, 10);
          activeTab = 'segments';
          document.getElementById('oda-tab-select').value = 'segments';
          render();
        }
      });
    });
  }

  document.getElementById('oda-tab-select')?.addEventListener('change', (e) => {
    activeTab = e.target.value;
    render();
  });

  document.getElementById('oda-search')?.addEventListener('input', (e) => {
    searchQuery = e.target.value;
    render();
  });

  document.getElementById('oda-btn-load-file')?.addEventListener('click', () => {
    document.getElementById('oda-file-input').click();
  });

  document.getElementById('oda-file-input')?.addEventListener('change', (e) => {
    const f = e.target.files && e.target.files[0];
    if (f) loadFromFile(f);
  });

  document.getElementById('oda-btn-export-json')?.addEventListener('click', () => exportSave('json'));
  document.getElementById('oda-btn-export-html')?.addEventListener('click', () => exportSave('html'));
  document.getElementById('oda-btn-export-pdf')?.addEventListener('click', () => exportSave('pdf'));
  document.getElementById('oda-btn-export-zip')?.addEventListener('click', () => exportSave('zip'));

  if (taskIdBoot) {
    loadByTaskId(taskIdBoot);
  }
})();
