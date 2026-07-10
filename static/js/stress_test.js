/** Stress Test Center UI — Developer / Owner only */
(function () {
  'use strict';

  let pollTimer = null;
  let batchId = null;

  function uiModeHeader() {
    const mode = typeof getMode === 'function' ? getMode() : 'simple';
    return { 'X-VM-Ui-Mode': mode };
  }

  function fmtEta(sec) {
    if (sec == null || sec <= 0) return '—';
    const m = Math.ceil(sec / 60);
    if (m < 2) return Math.round(sec) + ' сек';
    return m + ' мин';
  }

  function fmtStage(step) {
    const map = {
      preparing: 'Preparing',
      extract_audio: 'Audio',
      transcribe: 'Whisper',
      translate: 'Translation',
      translation_review: 'Review',
      tts: 'TTS',
      timing: 'Timing',
      dub: 'Mux',
      done: 'Done',
      starting: 'Starting',
      idle: '—',
      next: '—',
    };
    return map[step] || step || '—';
  }

  async function stressAccessAllowed() {
    if (typeof isDevMode === 'function' && isDevMode()) return true;
    try {
      const r = await fetch('/api/stress-test/access', { headers: uiModeHeader() });
      const d = await r.json();
      return d.allowed === true;
    } catch (_) {
      return false;
    }
  }

  function el(id) {
    return document.getElementById(id);
  }

  function showOverlay(show) {
    const ov = el('stress-test-overlay');
    if (!ov) return;
    ov.style.display = show ? 'flex' : 'none';
    ov.setAttribute('aria-hidden', show ? 'false' : 'true');
  }

  function updateModal(data) {
    const total = data.total || 0;
    const current = data.current_index || 0;
    const pct = data.progress_pct != null ? data.progress_pct : (total ? (current / total) * 100 : 0);
    if (el('st-progress-fill')) el('st-progress-fill').style.width = Math.min(100, pct) + '%';
    if (el('st-video')) el('st-video').textContent = total ? current + ' из ' + total : '—';
    if (el('st-stage')) el('st-stage').textContent = fmtStage(data.current_stage);
    if (el('st-errors')) el('st-errors').textContent = String(data.errors_count || 0);
    if (el('st-passed')) el('st-passed').textContent = String(data.passed || 0);
    if (el('st-remaining')) el('st-remaining').textContent = String(data.remaining != null ? data.remaining : '—');
    if (el('st-eta')) el('st-eta').textContent = fmtEta(data.eta_sec);
    if (el('st-message')) el('st-message').textContent = data.message || data.current_video || '';
  }

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPoll() {
    stopPoll();
    pollTimer = setInterval(async () => {
      if (!batchId) return;
      try {
        const r = await fetch('/api/stress-test/status/' + batchId, { headers: uiModeHeader() });
        const d = await r.json();
        updateModal(d);
        if (d.status === 'done') {
          stopPoll();
          if (el('st-done-actions')) el('st-done-actions').style.display = 'flex';
          if (el('st-cancel')) el('st-cancel').style.display = 'none';
          if (el('st-message')) {
            el('st-message').textContent =
              'Готово: ' + (d.passed || 0) + ' успешно, ' + (d.failed || 0) + ' с ошибками';
          }
        }
      } catch (_) {}
    }, 1200);
  }

  async function startStressTest() {
    showOverlay(true);
    if (el('st-done-actions')) el('st-done-actions').style.display = 'none';
    if (el('st-cancel')) el('st-cancel').style.display = 'inline-block';
    updateModal({ total: 0, current_index: 0, current_stage: 'starting', message: 'Запуск…' });

    try {
      const r = await fetch('/api/stress-test/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...uiModeHeader() },
        body: '{}',
      });
      const d = await r.json();
      if (!r.ok) {
        updateModal({ message: d.error || 'Ошибка запуска' });
        return;
      }
      batchId = d.batch_id;
      updateModal(d);
      if (d.status === 'done') {
        if (el('st-done-actions')) el('st-done-actions').style.display = 'flex';
        if (el('st-cancel')) el('st-cancel').style.display = 'none';
      } else {
        startPoll();
      }
    } catch (e) {
      updateModal({ message: 'Ошибка: ' + e });
    }
  }

  function openReport() {
    window.open('/api/stress-test/report?format=html', '_blank');
    showOverlay(false);
  }

  window.initStressTestUI = async function initStressTestUI() {
    const wrap = el('stress-test-btn-wrap');
    const btn = el('btn-stress-test');
    if (!wrap || !btn) return;

    const allowed = await stressAccessAllowed();
    wrap.style.display = allowed ? 'block' : 'none';
    if (!allowed) return;

    btn.addEventListener('click', startStressTest);
    if (el('st-open-report')) el('st-open-report').addEventListener('click', openReport);
    if (el('st-close-modal')) el('st-close-modal').addEventListener('click', () => showOverlay(false));
    if (el('st-cancel')) el('st-cancel').addEventListener('click', () => {
      stopPoll();
      showOverlay(false);
    });
  };
})();
