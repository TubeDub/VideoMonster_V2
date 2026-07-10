/**
 * Shared language-pack prepare flow (lazy download on user confirm).
 * User-facing text only — no HuggingFace / Python details.
 */
let _prepareAbortRequested = false;

function requestPrepareAbort() {
  _prepareAbortRequested = true;
}

function formatPrepareStatus(st, elapsedSec) {
  const label = st.current_label || 'Подготовка компонентов…';
  const detail = st.current_detail || '';
  let line = detail ? `${label} — ${detail}` : label;
  const pct = st.percent || 0;
  if (st.status === 'running' && elapsedSec >= 6 && pct < 8) {
    line += ` (${elapsedSec} с — идёт загрузка, это не зависание)`;
  }
  return line;
}

async function runLanguagePackPrepare(opts) {
  const options = opts || {};
  const sourceLang = options.source_lang || 'en';
  const targetLang = options.target_lang || 'ru';
  const whisperSize = options.whisper_size || 'tiny';
  const feature = options.feature || 'translate';
  const uiLang = options.ui_lang || localStorage.getItem('vm_ui_lang') || 'ru';

  const overlay = document.getElementById('prepare-overlay');
  const fill = document.getElementById('prepare-fill');
  const pctEl = document.getElementById('prepare-percent');
  const statusEl = document.getElementById('prepare-status-text');
  const errEl = document.getElementById('prepare-error');
  const cancelBtn = document.getElementById('prepare-cancel-btn');
  const dev = typeof isDevMode === 'function' && isDevMode();

  _prepareAbortRequested = false;
  if (cancelBtn) {
    cancelBtn.onclick = () => {
      _prepareAbortRequested = true;
      if (overlay) overlay.style.display = 'none';
      if (fill) fill.classList.remove('prepare-active');
    };
  }

  const body = {
    source_lang: sourceLang,
    target_lang: targetLang,
    whisper_size: whisperSize,
    feature,
    ui_lang: uiLang,
  };

  let checkData;
  try {
    const chk = await fetch('/api/prepare/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    checkData = await chk.json();
    if (checkData.ready) return true;

    const mb = Math.round(checkData.estimated_download_mb || 0);
    const msg =
      `Для данного языка необходимо загрузить языковой пакет.\n\n` +
      `Размер: ~${mb} МБ\n\n` +
      `Скачать сейчас?`;
    if (!window.confirm(msg)) return false;
  } catch (_) {
    return false;
  }

  if (!overlay) return false;
  overlay.style.display = 'flex';
  if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }
  if (fill) {
    fill.style.width = '0%';
    fill.classList.add('prepare-active');
  }
  if (pctEl) pctEl.textContent = '0%';
  if (statusEl) {
    statusEl.textContent = feature === 'stt'
      ? 'Идёт загрузка языкового пакета…'
      : 'Идёт подготовка компонентов…';
  }
  const listEl = document.getElementById('prepare-list');
  if (listEl && !dev) listEl.style.display = 'none';

  const pollStarted = Date.now();

  try {
    const r = await fetch('/api/prepare/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Prepare failed');
    const jobId = d.job_id;

    for (let i = 0; i < 600; i++) {
      if (_prepareAbortRequested) {
        overlay.style.display = 'none';
        if (fill) fill.classList.remove('prepare-active');
        return false;
      }
      await new Promise(res => setTimeout(res, 500));
      const sr = await fetch(`/api/prepare/status/${jobId}`);
      const st = await sr.json();
      const pct = st.percent || 0;
      const elapsedSec = st.elapsed_sec != null
        ? st.elapsed_sec
        : Math.floor((Date.now() - pollStarted) / 1000);
      const visualPct = pct < 1 && elapsedSec > 4 && st.status === 'running'
        ? Math.min(12, 4 + elapsedSec * 0.4)
        : pct;
      if (fill) fill.style.width = visualPct + '%';
      if (pctEl) pctEl.textContent = Math.round(pct) + '%';
      if (statusEl) {
        statusEl.textContent = formatPrepareStatus(st, elapsedSec);
      }
      if (dev && listEl && st.components && st.components.length) {
        listEl.style.display = 'block';
        listEl.innerHTML = st.components.map(c => {
          const mark = c.status === 'ready' ? '✓' : c.status === 'working' ? '…' : '○';
          return `<li>${mark} ${c.label || c.id}</li>`;
        }).join('');
      }
      if (st.status === 'done' && st.ready) {
        if (fill) fill.classList.remove('prepare-active');
        overlay.style.display = 'none';
        return true;
      }
      if (st.status === 'error') {
        if (fill) fill.classList.remove('prepare-active');
        if (errEl) {
          errEl.style.display = 'block';
          errEl.textContent = st.error || 'Не удалось загрузить языковой пакет';
        }
        return false;
      }
    }
    if (fill) fill.classList.remove('prepare-active');
    if (errEl) {
      errEl.style.display = 'block';
      errEl.textContent = 'Подготовка заняла слишком много времени. Проверьте интернет и перезапустите.';
    }
    return false;
  } catch (e) {
    if (fill) fill.classList.remove('prepare-active');
    if (errEl) {
      errEl.style.display = 'block';
      errEl.textContent = typeof vmFriendlyError === 'function'
        ? vmFriendlyError(e.message)
        : e.message;
    }
    return false;
  } finally {
    if (fill) fill.classList.remove('prepare-active');
    _prepareAbortRequested = false;
  }
}
