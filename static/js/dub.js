/* dub.js — авто-дубляж VideoMonster V2 */

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const dubStylesState = {
  styles: [],
  sections: [],
  defaultId: 'modern',
  loaded: false,
  loading: false,
  error: '',
  localOnly: true,
  regionalPack: null,
  selectedId: null,
};

function getStylesLocalOnly() {
  const cb = document.getElementById('dub-styles-local-only');
  if (cb) return cb.checked;
  const saved = typeof loadSettings === 'function' ? loadSettings() : {};
  return saved.dubStylesLocalOnly !== false;
}

function setStylesLocalOnly(on) {
  dubStylesState.localOnly = !!on;
  const cb = document.getElementById('dub-styles-local-only');
  if (cb) cb.checked = !!on;
  try {
    const s = typeof loadSettings === 'function' ? loadSettings() : {};
    s.dubStylesLocalOnly = !!on;
    localStorage.setItem('vm_settings', JSON.stringify(s));
  } catch (_) {}
}

const DUB_STEPS = [
  { key: 'preparing',       i18n: 'dub.step_preparing' },
  { key: 'extract_audio',   i18n: 'dub.step_extract' },
  { key: 'transcribe',      i18n: 'dub.step_whisper' },
  { key: 'translate',       i18n: 'dub.step_translate' },
  { key: 'tts',             i18n: 'dub.step_tts' },
  { key: 'studio',          i18n: 'dub.step_studio_preparing' },
  { key: 'timing',          i18n: 'dub.step_timing' },
  { key: 'dub',             i18n: 'dub.step_export' },
  { key: 'done',            i18n: 'dub.step_done' },
];

const state = {
  filename: null,
  videoName: null,
  taskId: null,
  polling: null,
  uploading: false,
  running: false,
  starting: false,
  lastProgress: 0,
  lastProgressAt: 0,
  stallNotified: false,
  lastSlotFitLogIdx: -1,
  redub: null,
  selectedVoice: null,
  voiceCatalog: {},
  reviewFontSize: 16,
  diagnosticsShown: false,
  reviewPauseNotified: false,
  reviewOverlayAutoOpened: false,
  devInspectorAvailable: false,
  statusFetchFailures: 0,
  statusCheckInFlight: false,
  pipelineCheckpoint: null,
  cancelledTask: false,
  translateStartedAt: 0,
};

let VM_VOICE_CATALOG = {};

const WIZARD_STEP_IDS = ['video', 'lang', 'voice', 'content', 'style', 'start'];

const wizardState = {
  stepIndex: 0,
  screen: 'flow',
};

const WIZARD_TICKER_TIPS = [
  { key: 'dub.tip_quality', fallback: 'Для лучшего результата используйте видео с чистой речью без сильного фонового шума.' },
  { key: 'dub.tip_review', fallback: 'Перед озвучкой можно проверить и отредактировать перевод в режиме контроля.' },
  { key: 'dub.tip_studio', fallback: 'После дубляжа откройте Dub Studio для точной настройки тайминга.' },
  { key: 'dub.tip_formats', fallback: 'Поддерживаются MP4, MKV, MOV, AVI и WebM.' },
  { key: 'dub.tip_pro', fallback: 'Pro-функции: расширенные модели Whisper и дополнительные стили озвучки.' },
  { key: 'dub.tip_original', fallback: 'В настройках стиля можно оставить часть оригинальной дорожки в финальном видео.' },
];

function wizardGoToStep(index) {
  const idx = Math.max(0, Math.min(WIZARD_STEP_IDS.length - 1, index));
  wizardState.stepIndex = idx;
  wizardShowScreen('flow');
  document.querySelectorAll('.wizard-step').forEach((el, i) => {
    const on = i === idx;
    el.classList.toggle('active', on);
    el.hidden = !on;
  });
  document.querySelectorAll('.wizard-dot').forEach((dot, i) => {
    dot.classList.toggle('active', i === idx);
    dot.classList.toggle('done', i < idx);
  });
  if (WIZARD_STEP_IDS[idx] === 'lang') renderWizardLangGrid();
  else if (WIZARD_STEP_IDS[idx] === 'voice') updateVoiceList();
  else if (WIZARD_STEP_IDS[idx] === 'content') renderWizardContentGrid();
  else if (WIZARD_STEP_IDS[idx] === 'style') {
    ensureDubStylesLoaded();
    renderWizardStyleGrid();
  }
  else if (idx === WIZARD_STEP_IDS.length - 1) updateWizardSummary();
  updateWizardNav();
}

function canWizardAdvance(stepKey) {
  switch (stepKey) {
    case 'video':
      return !!state.filename;
    case 'lang': {
      if (!document.getElementById('target-lang')?.value) return false;
      const manual = document.getElementById('source-manual')?.checked;
      if (manual && !document.getElementById('source-lang')?.value) return false;
      return true;
    }
    case 'voice':
      return !!(state.selectedVoice || document.getElementById('voice-select')?.value);
    case 'content':
      return !!document.getElementById('content-mode')?.value;
    case 'style':
      return !!getSelectedDubStyle();
    default:
      return false;
  }
}

function wizardGoNext() {
  const stepKey = WIZARD_STEP_IDS[wizardState.stepIndex];
  if (!canWizardAdvance(stepKey)) {
    vmNotify(
      t('dub.wizard_next_blocked', 'Завершите текущий шаг, прежде чем продолжить.'),
      'warning',
      4000
    );
    return;
  }
  wizardAdvanceFrom(stepKey);
}

function updateWizardNav() {
  const backBtn = document.getElementById('btn-wizard-back');
  const cancelBtn = document.getElementById('btn-wizard-cancel');
  const nav = document.getElementById('wizard-nav');
  const screen = wizardState.screen;
  const onFlow = screen === 'flow';
  const idx = wizardState.stepIndex;
  const busy = state.running || state.starting;
  const onStartStep = WIZARD_STEP_IDS[idx] === 'start';

  if (nav) {
    nav.hidden = !onFlow;
    nav.style.display = onFlow ? 'flex' : 'none';
  }

  if (backBtn) {
    const showBack = onFlow && idx > 0 && !busy;
    backBtn.hidden = !showBack;
    backBtn.style.display = showBack ? '' : 'none';
    backBtn.disabled = busy;
  }
  const nextBtn = document.getElementById('btn-wizard-next');
  if (nextBtn) {
    const showNext = onFlow && !onStartStep && !busy;
    nextBtn.hidden = !showNext;
    nextBtn.style.display = showNext ? '' : 'none';
    nextBtn.disabled = busy;
  }
  const startBtn = document.getElementById('btn-start-dub');
  if (startBtn) {
    if (onFlow && onStartStep) {
      const ready =
        !!state.filename &&
        canWizardAdvance('lang') &&
        canWizardAdvance('voice') &&
        canWizardAdvance('content') &&
        canWizardAdvance('style');
      startBtn.disabled = busy || !ready;
    }
  }
  if (cancelBtn) {
    const showCancel = onFlow || (screen === 'progress' && busy);
    cancelBtn.hidden = !showCancel;
    cancelBtn.style.display = showCancel ? '' : 'none';
    cancelBtn.disabled = false;
  }
}

function wizardCancelSetup() {
  if (state.running || state.starting) {
    cancelDubOperation();
    return;
  }
  if (wizardState.stepIndex > 0) {
    wizardGoToStep(0);
    vmNotify(
      t('dub.wizard_cancel_setup', 'Настройки сохранены. Вы вернулись к выбору видео.'),
      'info',
      5000
    );
    return;
  }
  if (state.filename) {
    state.filename = null;
    state.videoName = null;
    const info = document.getElementById('video-info');
    if (info) info.style.display = 'none';
    const startBtn = document.getElementById('btn-start-dub');
    if (startBtn) startBtn.disabled = true;
    updateWizardNav();
    vmNotify(t('dub.wizard_cancel_video', 'Выбор видео отменён'), 'info', 3000);
  }
}

function wizardGoBack() {
  if (wizardState.screen === 'progress' && (state.running || state.starting)) {
    cancelDubOperation();
    return;
  }
  if (wizardState.stepIndex > 0) {
    wizardGoToStep(wizardState.stepIndex - 1);
    if (typeof vmUiSound === 'function') vmUiSound('select');
  }
}

async function cancelDubOperation() {
  if (state.polling) {
    clearInterval(state.polling);
    state.polling = null;
  }

  let checkpoint = state.pipelineCheckpoint;
  if (state.taskId && (state.running || state.starting)) {
    try {
      const r = await fetch(`/api/auto_dub/cancel/${encodeURIComponent(state.taskId)}`, {
        method: 'POST',
      });
      if (r.ok) {
        const d = await r.json();
        checkpoint = d.checkpoint || checkpoint;
        state.pipelineCheckpoint = checkpoint;
        state.cancelledTask = true;
      }
    } catch (_) {}
  }

  state.running = false;
  state.starting = false;
  _dubBusy(false);
  showDubStarting(false);
  document.getElementById('btn-start-dub').disabled = false;

  vmNotify(
    t(
      'dub.cancelled',
      'Обработка остановлена. Настройки сохранены — измените параметры и запустите снова.'
    ),
    'info',
    7000
  );

  wizardGoToStep(WIZARD_STEP_IDS.length - 1);
  updateWizardSummary();
  if (checkpoint) {
    const hint = document.getElementById('wizard-summary');
    if (hint && !hint.querySelector('.wizard-checkpoint-hint')) {
      const note = document.createElement('p');
      note.className = 'wizard-checkpoint-hint char-count';
      note.textContent = t(
        'dub.checkpoint_hint',
        'Прогресс сохранён. Можно изменить язык или голос и продолжить без полного перезапуска.'
      );
      hint.appendChild(note);
    }
  }
  updateWizardNav();
}

function wizardShowScreen(screen) {
  const valid = ['flow', 'progress', 'result', 'error'];
  const next = valid.includes(screen) ? screen : 'flow';
  wizardState.screen = next;

  const flow = document.getElementById('wizard-flow');
  const progress = document.getElementById('wizard-screen-progress');
  const result = document.getElementById('wizard-screen-result');
  const error = document.getElementById('wizard-screen-error');

  if (flow) {
    flow.hidden = next !== 'flow';
    flow.style.display = next === 'flow' ? '' : 'none';
  }
  if (progress) {
    progress.hidden = next !== 'progress';
    progress.style.display = next === 'progress' ? '' : 'none';
  }
  if (result) {
    result.hidden = next !== 'result';
    result.style.display = next === 'result' ? '' : 'none';
  }
  if (error) {
    error.hidden = next !== 'error';
    error.style.display = next === 'error' ? '' : 'none';
  }

  if (next !== 'flow') {
    document.querySelectorAll('.wizard-step').forEach(el => {
      el.hidden = true;
      el.classList.remove('active');
    });
    const dots = document.getElementById('wizard-step-dots');
    if (dots) dots.hidden = true;
  } else {
    const dots = document.getElementById('wizard-step-dots');
    if (dots) dots.hidden = false;
    const idx = wizardState.stepIndex;
    document.querySelectorAll('.wizard-step').forEach((el, i) => {
      const on = i === idx;
      el.hidden = !on;
      el.classList.toggle('active', on);
    });
  }

  if (next === 'progress') {
    startWizardTicker();
    refreshProgressPhaseTooltips();
  } else {
    stopWizardTicker();
  }
  updateWizardNav();
  requestAnimationFrame(() => bindTruncatedButtonTooltips(document.getElementById('dub-wizard-shell')));

  const errPanel = document.getElementById('error-panel');
  if (errPanel && next === 'error') {
    errPanel.style.display = '';
  }
}

function wizardAdvanceFrom(stepId) {
  const idx = WIZARD_STEP_IDS.indexOf(stepId);
  if (idx >= 0 && idx < WIZARD_STEP_IDS.length - 1) {
    wizardGoToStep(idx + 1);
    if (typeof vmUiSound === 'function') vmUiSound('select');
  }
}

function getTargetLangEntries() {
  const select = document.getElementById('target-lang');
  if (!select) return [];
  return Array.from(select.options)
    .filter(opt => opt.value)
    .map(opt => ({
      name: String(opt.textContent || '').trim(),
      code: String(opt.value || '').trim(),
    }));
}

function renderWizardLangGrid() {
  const grid = document.getElementById('wizard-lang-grid');
  const select = document.getElementById('target-lang');
  if (!grid || !select) return;
  const entries = getTargetLangEntries();
  const current = select.value;
  grid.innerHTML = entries.map(({ name, code }) => {
    const sel = code === current;
    return `<button type="button" class="wizard-tile wizard-lang-tile${sel ? ' selected' : ''}" data-lang-code="${escHtml(code)}" role="option" aria-selected="${sel}" data-delay-tooltip="${escHtml(name)}">
      <span>${escHtml(name)}</span>
    </button>`;
  }).join('');
  grid.dataset.langCount = String(entries.length);
  grid.querySelectorAll('.wizard-lang-tile').forEach(btn => {
    btn.addEventListener('click', () => wizardPickLang(btn.dataset.langCode));
  });
  bindWizardTileTooltips(grid);
}

function wizardPickLang(code) {
  const select = document.getElementById('target-lang');
  if (!select || !code) return;
  select.value = code;
  select.dispatchEvent(new Event('change'));
  renderWizardLangGrid();
  updateVoiceList();
  loadDubStyles();
  wizardAdvanceFrom('lang');
}

function renderWizardContentGrid() {
  const grid = document.getElementById('wizard-content-grid');
  const select = document.getElementById('content-mode');
  if (!grid || !select) return;
  const current = select.value;
  grid.innerHTML = Array.from(select.options).map(opt => {
    const val = opt.value;
    const label = opt.textContent || val;
    const sel = val === current;
    return `<button type="button" class="wizard-tile${sel ? ' selected' : ''}" data-content-mode="${escHtml(val)}" role="option" aria-selected="${sel}" data-delay-tooltip="${escHtml(label)}">
      <span>${escHtml(label)}</span>
    </button>`;
  }).join('');
  grid.querySelectorAll('.wizard-tile').forEach(btn => {
    btn.addEventListener('click', () => wizardPickContent(btn.dataset.contentMode));
  });
  bindWizardTileTooltips(grid);
}

function wizardPickContent(mode) {
  const select = document.getElementById('content-mode');
  if (!select || !mode) return;
  select.value = mode;
  renderWizardContentGrid();
  wizardAdvanceFrom('content');
  ensureDubStylesLoaded();
}

function ensureDubStylesLoaded() {
  if (dubStylesState.loaded || dubStylesState.loading) return;
  loadDubStyles();
}

function renderWizardStyleGrid() {
  const grid = document.getElementById('wizard-style-grid');
  if (!grid) return;
  if (dubStylesState.loading && !dubStylesState.styles.length) {
    grid.innerHTML = `<div class="char-count">${escHtml(t('dub.styles_loading', 'Загрузка режимов…'))}</div>`;
    return;
  }
  if (dubStylesState.error && !dubStylesState.styles.length) {
    grid.innerHTML = `<div class="char-count wizard-style-error">${escHtml(dubStylesState.error)}</div>
      <button type="button" class="btn btn-secondary btn-sm" id="wizard-reload-styles">${escHtml(t('dub.retry', 'Повторить'))}</button>`;
    grid.querySelector('#wizard-reload-styles')?.addEventListener('click', () => {
      dubStylesState.loaded = false;
      dubStylesState.error = '';
      loadDubStyles();
    });
    return;
  }
  const styles = dubStylesState.styles || [];
  const selected = getSelectedDubStyle();
  if (!styles.length) {
    grid.innerHTML = `<div class="char-count">${escHtml(t('dub.styles_empty', 'Нет стилей'))}</div>`;
    return;
  }
  grid.innerHTML = styles.map(style => {
    const nameKey = style.i18n_key || `dub.style_${style.id}`;
    const hintKey = `${nameKey}_hint`;
    const name = t(nameKey, style.id);
    const hint = t(hintKey, '');
    const sel = style.id === selected;
    return `<button type="button" class="wizard-tile${sel ? ' selected' : ''}" data-style-id="${escHtml(style.id)}" data-delay-tooltip="${escHtml(hint || name)}">
      <span>${escHtml(name)}</span>
      ${hint ? `<span class="wizard-tile-desc">${escHtml(hint)}</span>` : ''}
    </button>`;
  }).join('');
  grid.querySelectorAll('.wizard-tile').forEach(btn => {
    btn.addEventListener('click', () => wizardPickStyle(btn.dataset.styleId));
  });
  bindWizardTileTooltips(grid);
}

function wizardPickStyle(styleId) {
  if (!styleId) return;
  const radio = document.querySelector(`input[name="dub-style"][value="${String(styleId).replace(/"/g, '\\"')}"]`);
  if (radio) {
    radio.checked = true;
    radio.dispatchEvent(new Event('change'));
  } else {
    dubStylesState.selectedId = styleId;
    try {
      const s = typeof loadSettings === 'function' ? loadSettings() : {};
      s.dubStyle = styleId;
      localStorage.setItem('vm_settings', JSON.stringify(s));
    } catch (_) {}
    applyStyleVolumePreset(styleId, true);
    updateDubStyleUI();
  }
  renderWizardStyleGrid();
  wizardAdvanceFrom('style');
}

function syncWizardModelSize(fromUi) {
  const hidden = document.getElementById('model-size');
  const ui = document.getElementById('wizard-model-size');
  if (!hidden || !ui) return;
  const allowed = ['tiny', 'base', 'small', 'medium', 'large'];
  // Stage 8: Simple default is small (was medium — multi-minute STT).
  const DEFAULT_WHISPER = 'small';
  if (fromUi) {
    let v = String(ui.value || DEFAULT_WHISPER);
    if (!allowed.includes(v)) v = DEFAULT_WHISPER;
    hidden.value = v;
    ui.value = v;
    try {
      const s = typeof loadSettings === 'function' ? loadSettings() : {};
      s.whisperSize = v;
      localStorage.setItem('vm_settings', JSON.stringify(s));
    } catch (_) {}
  } else {
    let v = String(hidden.value || DEFAULT_WHISPER);
    try {
      const s = typeof loadSettings === 'function' ? loadSettings() : {};
      if (s.whisperSize && allowed.includes(String(s.whisperSize))) {
        v = String(s.whisperSize);
      }
    } catch (_) {}
    if (!allowed.includes(v)) v = DEFAULT_WHISPER;
    // Migrate legacy UI default medium → small for Happy Path wizard.
    if (v === 'medium') v = DEFAULT_WHISPER;
    hidden.value = v;
    ui.value = v;
  }
}

function updateWizardSummary() {
  const box = document.getElementById('wizard-summary');
  if (!box) return;
  syncWizardModelSize(false);
  const langs = window.VM_LANGUAGES || {};
  const tgt = document.getElementById('target-lang')?.value || '';
  const langName = Object.entries(langs).find(([, code]) => code === tgt)?.[0] || tgt;
  const voiceId = state.selectedVoice || document.getElementById('voice-select')?.value || '';
  const voiceMeta = VM_VOICE_CATALOG[voiceId] || {};
  const voiceName = voiceMeta.title || voiceId;
  const contentSel = document.getElementById('content-mode');
  const contentLabel = contentSel?.selectedOptions?.[0]?.textContent || '';
  const styleId = getSelectedDubStyle();
  const styleRow = dubStylesState.styles.find(s => s.id === styleId);
  const styleName = styleRow
    ? t(styleRow.i18n_key || `dub.style_${styleId}`, styleId)
    : styleId;
  const whisper = document.getElementById('model-size')?.value || 'small';
  box.innerHTML = `
    <dl class="wizard-summary-list">
      <dt>${escHtml(t('dub.video', 'Видео'))}</dt>
      <dd data-delay-tooltip="${escHtml(state.videoName || '—')}">${escHtml(state.videoName || '—')}</dd>
      <dt>${escHtml(t('dub.target_lang', 'Язык'))}</dt>
      <dd data-delay-tooltip="${escHtml(langName)}">${escHtml(langName)}</dd>
      <dt>${escHtml(t('dub.voice', 'Голос'))}</dt>
      <dd data-delay-tooltip="${escHtml(voiceName)}">${escHtml(voiceName)}</dd>
      <dt>${escHtml(t('dub.content_mode', 'Контент'))}</dt>
      <dd data-delay-tooltip="${escHtml(contentLabel)}">${escHtml(contentLabel)}</dd>
      <dt>${escHtml(t('dub.voice_style', 'Стиль'))}</dt>
      <dd data-delay-tooltip="${escHtml(styleName)}">${escHtml(styleName)}</dd>
      <dt>${escHtml(t('dub.whisper_model', 'Whisper'))}</dt>
      <dd data-delay-tooltip="${escHtml(whisper)}">${escHtml(whisper)}</dd>
    </dl>`;
  bindDelayedTooltips(box);
}

function updateWizardPhases(step, status) {
  const order = ['translate', 'tts', 'sync', 'export'];
  const stepMap = {
    preparing: 'translate',
    extract_audio: 'translate',
    transcribe: 'translate',
    translate: 'translate',
    translation_review: 'translate',
    tts: 'tts',
    studio: 'sync',
    timing: 'sync',
    dub: 'export',
    done: 'export',
  };
  const current = status === 'done' ? 'done' : (stepMap[step] || 'translate');
  const currentIdx = status === 'done' ? order.length : order.indexOf(current);

  order.forEach((phaseKey, idx) => {
    const phaseDone = status === 'done' || idx < currentIdx;
    const phaseActive = status !== 'done' && phaseKey === current;
    const row = document.querySelector(`.wizard-phase[data-phase="${phaseKey}"]`);
    const stateEl = document.getElementById(`wizard-phase-${phaseKey}`);
    if (row) {
      row.classList.toggle('done', phaseDone);
      row.classList.toggle('active', phaseActive);
    }
    if (stateEl) {
      let full = '';
      let short = '';
      if (status === 'done' || phaseDone) {
        full = t('dub.wizard_phase_done', 'Готово');
        short = '✓';
      } else if (phaseActive) {
        full = t('dub.wizard_phase_active', 'Выполняется…');
        short = '…';
      } else {
        full = t('dub.wizard_phase_wait', 'Ожидание');
        short = '—';
      }
      stateEl.textContent = short;
      stateEl.setAttribute('data-delay-tooltip', full);
    }
  });

  bindDelayedTooltips(document.getElementById('wizard-phase-list'));

  const pctEl = document.getElementById('wizard-progress-pct');
  if (pctEl && status === 'done') pctEl.textContent = '100%';

  const progressEl = document.getElementById('wizard-screen-progress');
  const fillEl = document.getElementById('progress-fill');
  const translateRunning = status === 'running' && step === 'translate';
  if (progressEl) progressEl.classList.toggle('is-translate-active', translateRunning);
  if (fillEl) fillEl.classList.toggle('is-pulse', translateRunning);
}

const TRANSLATION_SUB_KEYS = ['marian_mt', 'llm_adaptation', 'post_processing'];

const TRANSLATION_SUB_LABELS = {
  marian_mt: { key: 'dub.sub_marian', fallback: 'Marian MT' },
  llm_adaptation: { key: 'dub.sub_llm', fallback: 'Qwen / LLM Adaptation' },
  post_processing: { key: 'dub.sub_post', fallback: 'Post-processing' },
};

const TRANSLATION_SUBPHASE_BUCKET = {
  marian_mt: 'marian_mt',
  post_mt_restore: 'marian_mt',
  naturalizer_rules: 'llm_adaptation',
  llm_adaptation: 'llm_adaptation',
  validation: 'post_processing',
  post_processing: 'post_processing',
};

function formatDurationHms(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

function formatDurationVerbose(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m > 0) {
    return t('dub.duration_min_sec', `${m} мин ${r} сек`).replace('${m}', m).replace('${r}', r);
  }
  return t('dub.duration_sec', `${r} сек`).replace('${r}', r);
}

function resolveSubstepStatus(timing, key, subphase) {
  const phaseStatus = timing.phase_status || {};
  if (phaseStatus[key]) return phaseStatus[key];
  const bucket = TRANSLATION_SUBPHASE_BUCKET[subphase] || '';
  const idx = TRANSLATION_SUB_KEYS.indexOf(key);
  const activeIdx = bucket ? TRANSLATION_SUB_KEYS.indexOf(bucket) : -1;
  if (subphase === 'done') return 'done';
  if (activeIdx >= 0 && idx < activeIdx) return 'done';
  if (bucket === key) return 'active';
  return 'pending';
}

function isSimpleMtUi(d, timing) {
  if (timing && timing.simple_mt_locked) return true;
  if (timing && timing.llm_adaptation_used === false && timing.hidden_buckets) return true;
  if (d && (d.simple_mt_locked || d.simple_pipeline || d.happy_path)) return true;
  if (d && d.llm_adaptation_used === false && (d.translate_method === 'marian_batch' || d.translate_method === 'mt_cache')) {
    return true;
  }
  return false;
}

function updateTranslationSubsteps(d) {
  const wrap = document.getElementById('wizard-translate-substeps');
  if (!wrap) return;

  const pd = d.progress_detail || {};
  const step = d.step || '';
  const show = (d.status === 'running' || d.status === 'translation_review')
    && (step === 'translate' || pd.phase === 'translate');
  wrap.hidden = !show;
  if (!show) return;

  const timing = pd.translation_timing || d.translation_timing || {};
  const buckets = timing.ui_buckets || {};
  const labels = timing.ui_labels || {};
  const stats = timing.segment_stats || {};
  const subphase = pd.translation_subphase || timing.current_subphase || '';
  const totalSeg = Number(pd.total_segments || timing.segment_count || 0);
  const hidden = new Set(timing.hidden_buckets || []);
  const simpleMt = isSimpleMtUi(d, timing);
  if (simpleMt) {
    hidden.add('llm_adaptation');
    // Light post only if backend explicitly enables it (default: hide in Simple).
    if ((timing.phase_status || {}).post_processing === 'skipped') {
      hidden.add('post_processing');
    } else if (!timing.phase_status) {
      hidden.add('post_processing');
    }
  }

  const secs = TRANSLATION_SUB_KEYS.map((key) => {
    const row = stats[key] || {};
    return Number(row.sec ?? buckets[key] ?? 0);
  });
  const maxSec = Math.max(...secs, 1);

  TRANSLATION_SUB_KEYS.forEach((key, idx) => {
    const row = wrap.querySelector(`.wizard-substep[data-sub="${key}"]`);
    if (!row) return;
    const hideRow = hidden.has(key);
    row.hidden = hideRow;
    row.classList.toggle('is-hidden-simple', hideRow);
    if (hideRow) return;
    const labelEl = row.querySelector('.wizard-substep-label');
    const timeEl = row.querySelector('.wizard-substep-time');
    const statusEl = row.querySelector('.wizard-substep-status');
    const fillEl = row.querySelector('.wizard-substep-fill');
    const labelDef = TRANSLATION_SUB_LABELS[key] || { key: '', fallback: key };
    const customLabel = labels[key];
    if (labelEl) {
      labelEl.textContent = customLabel || t(labelDef.key, labelDef.fallback);
    }

    let status = resolveSubstepStatus(timing, key, subphase);
    if (status === 'skipped') {
      row.classList.add('skipped');
      row.classList.remove('done', 'active', 'pending');
      if (timeEl) timeEl.textContent = '—';
      if (statusEl) {
        statusEl.textContent = '';
        statusEl.title = t('dub.sub_skipped', 'Пропущено в Simple');
      }
      if (fillEl) fillEl.style.width = '0%';
      return;
    }
    const sec = secs[idx];
    const segStat = stats[key] || {};
    let segDone = Number(segStat.segments || 0);
    if (!segDone && status === 'active') {
      if (key === 'marian_mt') segDone = Number(timing.marian_segments_done || pd.segments_done || 0);
      if (key === 'llm_adaptation') segDone = Number(timing.llm_segments_done || 0);
    }
    const segTotal = totalSeg || segDone;

    row.classList.toggle('done', status === 'done');
    row.classList.toggle('active', status === 'active');
    row.classList.toggle('pending', status === 'pending');
    row.classList.remove('skipped');

    if (timeEl) {
      const avg = Number(segStat.avg_sec_per_segment || 0);
      if (sec > 0) {
        let line = formatDurationHms(sec);
        if (segDone > 0 && avg > 0) {
          line += ` · ${segDone} ${t('dub.segments_short', 'сегм.')} · ${avg.toFixed(1)}s/${t('dub.seg_short', 'сег')}`;
        }
        timeEl.textContent = line;
      } else if (status === 'active') {
        timeEl.textContent = '00:00:00';
      } else {
        timeEl.textContent = '—';
      }
    }
    if (statusEl) {
      if (status === 'done') {
        statusEl.textContent = '✓';
        statusEl.title = t('dub.sub_done', 'Готово');
      } else if (status === 'active') {
        statusEl.textContent = '←';
        statusEl.title = t('dub.sub_running', 'Сейчас выполняется');
      } else {
        statusEl.textContent = '';
        statusEl.title = '';
      }
    }
    if (fillEl) {
      let pct = 0;
      if (status === 'done' && sec > 0) {
        pct = Math.max(4, (sec / maxSec) * 100);
      } else if (status === 'active') {
        if (segTotal > 0 && segDone > 0) {
          pct = Math.max(6, Math.min(96, (segDone / segTotal) * 100));
        } else if (sec > 0) {
          pct = Math.max(6, Math.min(96, (sec / maxSec) * 100));
        } else {
          pct = 8;
        }
      }
      fillEl.style.width = `${pct}%`;
    }
  });
}

function updateConveyorTimingDebug(d) {
  const el = document.getElementById('wizard-conveyor-timing');
  if (!el) return;
  const ct = d.pipeline_conveyor_timing || (d.info && d.info.pipeline_conveyor_timing) || null;
  const dev = typeof isDevMode === 'function' && isDevMode();
  if (!ct || !dev) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  const n = Number(ct.segment_count || 0);
  const lines = [
    ['Whisper', ct.whisper_sec],
    ['Marian', ct.marian_sec],
    ['LLM', ct.llm_sec],
    ['Post', ct.post_sec || 0],
    ['TTS', ct.tts_sec],
  ];
  el.innerHTML = lines.map(([name, sec]) => {
    const s = Number(sec || 0);
    const avg = n > 0 ? (s / n).toFixed(1) : '—';
    return `<div class="conveyor-timing-row"><span>${name}</span><span>${formatDurationHms(s)}</span><span>${n} seg · ${avg}s/seg</span></div>`;
  }).join('');
}

function startWizardTicker() {
  startInfoTickerRotation();
}

function stopWizardTicker() {
  stopInfoTickerRotation();
}

const infoTickerState = {
  timer: null,
  tipIndex: 0,
  liveMessage: '',
};

function getInfoTipText() {
  const item = WIZARD_TICKER_TIPS[infoTickerState.tipIndex % WIZARD_TICKER_TIPS.length];
  return t(item.key, item.fallback);
}

function renderInfoTicker() {
  const el = document.getElementById('wizard-info-ticker');
  if (!el) return;
  const text = infoTickerState.liveMessage || getInfoTipText()
    || t('dub.tip_working', 'Обработка видео…');
  el.textContent = text;
  el.classList.remove('is-marquee');
  requestAnimationFrame(() => {
    const wrap = el.parentElement;
    if (wrap && el.scrollWidth > wrap.clientWidth + 4) {
      el.classList.add('is-marquee');
      el.style.setProperty('--marquee-duration', `${Math.max(12, text.length * 0.35)}s`);
    }
  });
}

function setProgressInfoMessage(msg) {
  infoTickerState.liveMessage = String(msg || '').trim();
  renderInfoTicker();
}

function startInfoTickerRotation() {
  stopInfoTickerRotation();
  infoTickerState.tipIndex = 0;
  renderInfoTicker();
  infoTickerState.timer = setInterval(() => {
    if (infoTickerState.liveMessage) return;
    infoTickerState.tipIndex = (infoTickerState.tipIndex + 1) % WIZARD_TICKER_TIPS.length;
    renderInfoTicker();
  }, 9000);
}

function stopInfoTickerRotation() {
  if (infoTickerState.timer) {
    clearInterval(infoTickerState.timer);
    infoTickerState.timer = null;
  }
  infoTickerState.liveMessage = '';
}

function buildProgressInfoLine(d) {
  const pd = d.progress_detail || {};
  if (pd.live_message) {
    return String(pd.live_message);
  }
  if (pd.slow_segment_notice) {
    return String(pd.slow_segment_notice);
  }

  const parts = [];
  const phase = pd.phase || d.step || '';
  const cur = pd.current_segment || d.current_segment || 0;
  const total = pd.total_segments || 0;
  const done = pd.segments_done != null ? pd.segments_done : 0;
  const remaining = pd.segments_remaining != null ? pd.segments_remaining : Math.max(0, total - done);
  const sub = pd.timing_substep || pd.tts_substep || '';

  if (phase === 'voice_verification' || sub === 'voice_verify') {
    parts.push(t('dub.progress_voice_verify', 'Перепроверка озвучки'));
    if (cur > 0 && total > 0) {
      parts.push(t('dub.progress_segment', 'Сегмент') + ' ' + cur + '/' + total);
    }
    if (pd.verification_attempt) {
      parts.push(
        t('dub.progress_verify_attempt', 'попытка') + ' ' + pd.verification_attempt
      );
    }
    if (pd.verification_route && pd.verification_route !== 'voice') {
      parts.push(String(pd.verification_route));
    }
  } else if (phase === 'tts' || d.step === 'tts') {
    if (pd.tts_engine || pd.tts_engine_id) {
      parts.push('TTS: ' + String(pd.tts_engine || pd.tts_engine_id));
    }
    if (pd.voice) {
      const v = String(pd.voice);
      parts.push(v.includes('Neural') ? v.replace('Neural', '').split('-').pop() : v);
    }
    if (pd.llm_model || pd.translation_model) {
      parts.push('LLM: ' + String(pd.llm_model || pd.translation_model));
    }
    if (cur > 0 && total > 0) {
      parts.push(t('dub.progress_segment', 'Сегмент') + ' ' + cur + '/' + total);
    }
    if (pd.char_count || pd.text_chars) {
      parts.push(String(pd.char_count || pd.text_chars) + ' ' + t('dub.chars', 'симв.'));
    }
    if (pd.segment_duration_ms || pd.slot_ms) {
      parts.push(Math.round(Number(pd.segment_duration_ms || pd.slot_ms) / 1000) + 's');
    }
  }

  if (phase === 'translate' || d.step === 'translate') {
    const subKey = pd.translation_subphase || '';
    const bucket = TRANSLATION_SUBPHASE_BUCKET[subKey];
    if (bucket && TRANSLATION_SUB_LABELS[bucket]) {
      const def = TRANSLATION_SUB_LABELS[bucket];
      parts.push(t(def.key, def.fallback));
    }
    parts.push(t('dub.translate_hint', 'Перевод может занять несколько минут — это нормально'));
    if (d.status === 'running') {
      if (!state.translateStartedAt) state.translateStartedAt = Date.now();
      const sec = Math.floor((Date.now() - state.translateStartedAt) / 1000);
      if (sec >= 45) {
        const min = Math.max(1, Math.round(sec / 60));
        parts.push(t('dub.translate_running', `Идёт перевод — уже ${min} мин`));
      }
    }
    if (done > 0 && total > 0) {
      parts.push(`${done}/${total}`);
      if (remaining > 0) {
        parts.push(`${t('dub.remaining', 'осталось')}: ${remaining}`);
      }
    }
  } else if (!parts.length) {
    state.translateStartedAt = 0;
    if (d.step_label) {
      const label = String(d.step_label).trim();
      const skip = /^(перевод|translation|translate)$/i.test(label);
      if (!skip) parts.push(label);
    }
  } else {
    state.translateStartedAt = 0;
  }

  if (phase === 'timing' && sub === 'adapt') {
    parts.push(t('dub.timing_adapt', 'подгонка текста'));
    if (cur > 0) parts.push(`${cur}/${total || '?'}`);
  } else if (phase === 'timing' && sub === 'mix' && cur > 0) {
    parts.push(`${cur}/${total || '?'}`);
  } else if (phase === 'timing' && sub === 'export') {
    parts.push(t('dub.timing_export', 'сохранение дорожки'));
  } else if (total > 0 && cur > 0 && phase !== 'tts') {
    parts.push(`${t('dub.progress_segment', 'Сегмент')}: ${cur}/${total}`);
    if (done > 0) {
      parts.push(`${t('dub.progress_done', 'готово')}: ${done}`);
    }
    if (remaining > 0) {
      parts.push(`${t('dub.remaining', 'осталось')}: ${remaining}`);
    }
  }

  const segElapsedServer = pd.segment_elapsed_sec;
  let segElapsed = segElapsedServer;
  if (pd.segment_started_at && d.status === 'running') {
    segElapsed = Math.max(Number(segElapsedServer) || 0, Date.now() / 1000 - Number(pd.segment_started_at));
  }
  if (segElapsed != null && segElapsed > 0 && cur > 0) {
    const sec = Math.floor(Number(segElapsed));
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    const durStr = m > 0 ? `${m}:${String(s).padStart(2, '0')}` : `${s}s`;
    parts.push(durStr);
    const avg = Number(pd.avg_segment_sec) || 0;
    const slowThreshold = Math.max(phase === 'tts' ? 45 : 90, avg * 2);
    if (segElapsed >= slowThreshold && !pd.slow_segment_notice) {
      const op = pd.operation || (phase === 'tts' ? t('dub.tts_gen', 'генерация речи') : t('dub.processing', 'обработка'));
      return t(
        'dub.slow_segment',
        `Сегмент №${cur} обрабатывается дольше обычного (${durStr}). Идёт ${op}. Процесс продолжается.`
      ).replace('№${cur}', '№' + cur).replace('${durStr}', durStr).replace('${op}', op);
    }
  }

  if (pd.avg_segment_sec != null && pd.avg_segment_sec > 0) {
    parts.push(`${t('dub.avg_seg', 'ср.')}: ${Math.round(pd.avg_segment_sec)}s`);
  }

  if (pd.stage_progress_pct != null && pd.stage_progress_pct > 0) {
    parts.push(`${Math.round(pd.stage_progress_pct)}%`);
  }

  if (pd.eta_sec != null && pd.eta_sec > 0) {
    parts.push(`~${Math.ceil(pd.eta_sec / 60)} ${t('dub.min_left', 'мин')}`);
  }
  if (pd.operation && !parts.some(p => String(p).toLowerCase().includes(String(pd.operation).toLowerCase()))) {
    parts.push(String(pd.operation));
  }
  if (pd.last_tts_error) parts.push(String(pd.last_tts_error));
  if (d.ai_core && d.ai_core.active_model && d.ai_core.active_model.display_name) {
    parts.push('AI: ' + d.ai_core.active_model.display_name);
  }
  return parts.filter(Boolean).join(' · ');
}

function bindDelayedTooltips(root) {
  const scope = root || document;
  scope.querySelectorAll('[data-delay-tooltip]').forEach(el => {
    if (el.dataset.tooltipBound) return;
    el.dataset.tooltipBound = '1';
    let timer = null;
    let tipEl = null;
    const show = () => {
      const text = el.getAttribute('data-delay-tooltip') || el.textContent || '';
      if (!text.trim()) return;
      tipEl = document.createElement('div');
      tipEl.className = 'vm-delay-tooltip';
      tipEl.textContent = text.trim();
      document.body.appendChild(tipEl);
      const r = el.getBoundingClientRect();
      tipEl.style.left = `${Math.max(8, r.left)}px`;
      tipEl.style.top = `${Math.max(8, r.bottom + 6)}px`;
    };
    const hide = () => {
      if (timer) clearTimeout(timer);
      timer = null;
      if (tipEl) {
        tipEl.remove();
        tipEl = null;
      }
    };
    el.addEventListener('mouseenter', () => {
      hide();
      timer = setTimeout(show, 2000);
    });
    el.addEventListener('mouseleave', hide);
    el.addEventListener('blur', hide);
  });
}

function bindWizardTileTooltips(root) {
  const scope = root || document.getElementById('dub-wizard-shell');
  if (!scope) return;
  scope.querySelectorAll('.wizard-tile').forEach(tile => {
    const labelSpan = tile.querySelector(':scope > span:first-of-type');
    const text = (labelSpan?.textContent || tile.textContent || '').trim();
    if (!text) {
      tile.removeAttribute('data-delay-tooltip');
      return;
    }
    const measure = labelSpan || tile;
    const truncated =
      measure.scrollWidth > measure.clientWidth + 2 ||
      tile.scrollWidth > tile.clientWidth + 2;
    if (truncated) tile.setAttribute('data-delay-tooltip', text);
    else tile.removeAttribute('data-delay-tooltip');
  });
  bindDelayedTooltips(scope);
}

function bindTruncatedButtonTooltips(root) {
  const scope = root || document.getElementById('dub-wizard-shell');
  if (!scope) return;
  scope.querySelectorAll('.btn').forEach(btn => {
    const text = (btn.textContent || '').trim();
    if (!text) return;
    if (btn.scrollWidth > btn.clientWidth + 2) {
      btn.setAttribute('data-delay-tooltip', text);
    } else if (btn.getAttribute('data-delay-tooltip') === text) {
      btn.removeAttribute('data-delay-tooltip');
    }
  });
  bindDelayedTooltips(scope);
}

function refreshProgressPhaseTooltips() {
  document.querySelectorAll('#wizard-phase-list .wizard-phase-name').forEach(el => {
    const label = el.textContent || '';
    if (label.trim()) el.setAttribute('data-delay-tooltip', label.trim());
  });
  bindDelayedTooltips(document.getElementById('wizard-phase-list'));
}

function resetWizard() {
  if (state.polling) clearInterval(state.polling);
  state.taskId = null;
  state.running = false;
  state.starting = false;
  state.filename = null;
  state.videoName = null;
  state.lastProgress = 0;
  state.statusFetchFailures = 0;
  state.redub = null;
  wizardGoToStep(0);
  document.getElementById('video-info').style.display = 'none';
  document.getElementById('btn-start-dub').disabled = true;
}

function initWizard() {
  const main = document.querySelector('.main-content');
  if (main && document.querySelector('.dub-wizard-page')) {
    main.classList.add('main-content--dub-wizard');
  }
  renderWizardLangGrid();
  renderWizardContentGrid();
  wizardGoToStep(0);
  bindDelayedTooltips(document.getElementById('dub-wizard-shell'));
  bindTruncatedButtonTooltips(document.getElementById('dub-wizard-shell'));
  refreshProgressPhaseTooltips();

  const drop = document.getElementById('drop-zone');
  if (drop) {
    drop.addEventListener('click', e => {
      if (e.target.closest('#btn-pick-video')) return;
      document.getElementById('video-input')?.click();
    });
  }

  document.getElementById('btn-wizard-new')?.addEventListener('click', resetWizard);
  document.getElementById('btn-wizard-retry')?.addEventListener('click', () => {
    wizardGoToStep(WIZARD_STEP_IDS.length - 1);
  });
}

function _dubBusy(on) {
  if (typeof vmSetWorkBusy === 'function') vmSetWorkBusy(on);
}

function t(key, fallback) {
  if (typeof window.vmT === 'function') return window.vmT(key, fallback);
  return fallback || key;
}

function warnIfOutputFilename(name) {
  if (!name || !/_OUTPUT_/i.test(name)) return;
  vmNotify(
    t(
      'dub.warn_output_file',
      'Это готовый дубляж (_OUTPUT_). Используйте оригинал, не готовый дубляж — иначе возможен двойной голос.'
    ),
    'warning',
    9000
  );
}

const UK_TTS_BACKEND_VOICES = {
  'edge-offline': [
    { id: 'uk-UA-OstapNeural', name: 'Остап (Edge, чол.)' },
    { id: 'uk-UA-PolinaNeural', name: 'Поліна (Edge, жін.)' },
  ],
  edge: [
    { id: 'uk-UA-OstapNeural', name: 'Остап (Edge, чол.)' },
    { id: 'uk-UA-PolinaNeural', name: 'Поліна (Edge, жін.)' },
  ],
  tts_uk: [
    { id: 'mykyta', name: 'Микита (tts_uk, чол.) — рекомендований' },
    { id: 'lada', name: 'Лада (tts_uk, жін.)' },
    { id: 'tetiana', name: 'Тетяна (tts_uk, жін.)' },
  ],
  piper: [
    { id: 'uk_UA-mykyta-high', name: 'Микита (Piper high, чол.)' },
    { id: 'uk_UA-oleksa-high', name: 'Олекса (Piper high, чол.)' },
    { id: 'uk_UA-lada-high', name: 'Лада (Piper high, жін.)' },
    { id: 'uk_UA-tetiana-high', name: 'Тетяна (Piper high, жін.)' },
  ],
};

function syncMykytaWizardVisibility(backendId) {
  const box = document.getElementById('wizard-mykyta-controls');
  if (!box) return;
  box.hidden = (backendId || currentTtsBackend()) !== 'tts_uk';
}

function readMykytaControls() {
  const settings = typeof loadSettings === 'function' ? loadSettings() : {};
  const num = (id, key, fallback) => {
    const el = document.getElementById(id);
    if (el && el.value !== '') return Number(el.value);
    if (settings[key] != null) return Number(settings[key]);
    return fallback;
  };
  return {
    mykyta_rate: num('wizard-mykyta-rate', 'mykyta_rate', 0.97),
    mykyta_pitch: num('wizard-mykyta-pitch', 'mykyta_pitch', 0),
    mykyta_volume: num('wizard-mykyta-volume', 'mykyta_volume', 1.05),
    mykyta_length_scale: num('wizard-mykyta-length', 'mykyta_length_scale', 1.05),
  };
}

function bindMykytaWizardSliders() {
  const pairs = [
    ['wizard-mykyta-rate', 'wizard-mykyta-rate-label', false],
    ['wizard-mykyta-pitch', 'wizard-mykyta-pitch-label', true],
    ['wizard-mykyta-volume', 'wizard-mykyta-volume-label', false],
    ['wizard-mykyta-length', 'wizard-mykyta-length-label', false],
  ];
  const settings = typeof loadSettings === 'function' ? loadSettings() : {};
  const apply = (el, lbl, intish) => {
    if (!el || !lbl) return;
    const v = Number(el.value);
    lbl.textContent = intish ? String(v) : v.toFixed(2);
  };
  const mapSaved = {
    'wizard-mykyta-rate': 'mykyta_rate',
    'wizard-mykyta-pitch': 'mykyta_pitch',
    'wizard-mykyta-volume': 'mykyta_volume',
    'wizard-mykyta-length': 'mykyta_length_scale',
  };
  pairs.forEach(([id, lid, intish]) => {
    const el = document.getElementById(id);
    const lbl = document.getElementById(lid);
    if (!el) return;
    const sk = mapSaved[id];
    if (sk && settings[sk] != null) el.value = String(settings[sk]);
    apply(el, lbl, intish);
    el.addEventListener('input', () => {
      apply(el, lbl, intish);
      try {
        const s = typeof loadSettings === 'function' ? loadSettings() : {};
        const ctrl = readMykytaControls();
        Object.assign(s, ctrl);
        localStorage.setItem('vm_settings', JSON.stringify(s));
      } catch (_) {}
    });
  });
}

function syncTtsEngineControls(backendId) {
  const id = backendId || 'edge-offline';
  const hidden = document.getElementById('tts-engine');
  const wiz = document.getElementById('wizard-tts-backend');
  if (hidden) hidden.value = id;
  if (wiz && wiz.value !== id) wiz.value = id;
  syncMykytaWizardVisibility(id);
  try {
    const s = typeof loadSettings === 'function' ? loadSettings() : {};
    s.ttsEngine = id;
    s.tts_engine = id;
    localStorage.setItem('vm_settings', JSON.stringify(s));
  } catch (_) {}
}

function currentTtsBackend() {
  const wiz = document.getElementById('wizard-tts-backend');
  const el = document.getElementById('tts-engine');
  const settings = typeof loadSettings === 'function' ? loadSettings() : {};
  return (wiz && wiz.value)
    || (el && el.value)
    || settings.ttsEngine
    || settings.tts_engine
    || 'edge-offline';
}

function updateVoiceList() {
  const targetLang = document.getElementById('target-lang').value;
  const voiceSel = document.getElementById('voice-select');
  const voiceList = document.getElementById('voice-list');
  const wizardGrid = document.getElementById('wizard-voice-grid');
  const backend = currentTtsBackend();
  syncTtsEngineControls(backend);
  const backendRow = document.getElementById('wizard-tts-backend-row');
  if (backendRow) backendRow.hidden = targetLang !== 'uk';
  let voices = (window.VM_VOICES && window.VM_VOICES[targetLang]) || [];
  if (targetLang === 'uk' && UK_TTS_BACKEND_VOICES[backend]) {
    voices = UK_TTS_BACKEND_VOICES[backend];
  } else if (targetLang === 'uk') {
    voices = UK_TTS_BACKEND_VOICES['edge-offline'];
  }
  const settings = typeof loadSettings === 'function' ? loadSettings() : {};
  const preferred = settings.voice
    || (targetLang === 'uk' && backend === 'tts_uk' ? 'mykyta' : null)
    || (targetLang === 'uk' && backend === 'piper' ? 'uk_UA-mykyta-high' : null)
    || (targetLang === 'uk' ? 'uk-UA-OstapNeural' : null)
    || 'ru-RU-DmitryNeural';

  voiceSel.innerHTML = '';

  const renderWizardVoices = () => {
    if (!wizardGrid) return;
    if (!voices.length) {
      wizardGrid.innerHTML = '<div class="char-count">—</div>';
      return;
    }
    wizardGrid.innerHTML = voices.map(v => {
      const meta = VM_VOICE_CATALOG[v.id] || {};
      const title = meta.title || v.name;
      const desc = meta.description || v.name;
      const selected = v.id === (state.selectedVoice || preferred);
      return `
        <button type="button" class="wizard-tile wizard-voice-tile${selected ? ' selected' : ''}" data-voice-id="${escHtml(v.id)}" role="option" aria-selected="${selected}" data-delay-tooltip="${escHtml(desc)}">
          <span class="wizard-tile-label">${escHtml(title)}</span>
          <span class="wizard-tile-desc">${escHtml(desc)}</span>
          <span class="wizard-voice-preview" data-preview-voice="${escHtml(v.id)}">🔊 ${escHtml(t('dub.voice_preview', 'Прослушать'))}</span>
        </button>`;
    }).join('');
    wizardGrid.querySelectorAll('.wizard-voice-tile').forEach(el => {
      el.addEventListener('click', e => {
        if (e.target.closest('[data-preview-voice]')) return;
        selectVoice(el.dataset.voiceId, true);
      });
    });
    wizardGrid.querySelectorAll('[data-preview-voice]').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        previewVoice(btn.dataset.previewVoice);
      });
    });
    bindWizardTileTooltips(wizardGrid);
  };

  if (!voiceList && !wizardGrid) return;

  if (!voices.length) {
    if (voiceList) voiceList.innerHTML = '<div class="char-count" style="padding:12px;">—</div>';
    renderWizardVoices();
    return;
  }

  if (voiceList) {
    voiceList.innerHTML = voices.map(v => {
      const meta = VM_VOICE_CATALOG[v.id] || {};
      const title = meta.title || v.name;
      const desc = meta.description || v.name;
      const useCase = meta.use_case || '';
      const selected = v.id === (state.selectedVoice || preferred);
      return `
        <div class="voice-item ${selected ? 'selected' : ''}" data-voice-id="${escHtml(v.id)}" role="option" aria-selected="${selected}">
          <div class="voice-item-body">
            <div class="voice-item-title">${escHtml(title)}</div>
            <div class="voice-item-desc">${escHtml(desc)}</div>
            ${useCase ? `<div class="voice-item-use">${escHtml(useCase)}</div>` : ''}
          </div>
          <button type="button" class="btn btn-secondary btn-sm voice-preview-btn" data-preview-voice="${escHtml(v.id)}" title="${escHtml(t('dub.voice_preview', 'Прослушать'))}">🔊</button>
        </div>`;
    }).join('');
    voiceList.querySelectorAll('.voice-item').forEach(el => {
      el.addEventListener('click', e => {
        if (e.target.closest('[data-preview-voice]')) return;
        selectVoice(el.dataset.voiceId);
      });
    });
    voiceList.querySelectorAll('[data-preview-voice]').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        previewVoice(btn.dataset.previewVoice);
      });
    });
  }

  renderWizardVoices();

  voices.forEach(v => {
    const opt = document.createElement('option');
    opt.value = v.id;
    opt.textContent = v.name;
    if (v.id === (state.selectedVoice || preferred)) {
      opt.selected = true;
      state.selectedVoice = v.id;
    }
    voiceSel.appendChild(opt);
  });
}

function selectVoice(voiceId, autoAdvance) {
  state.selectedVoice = voiceId;
  const voiceSel = document.getElementById('voice-select');
  if (voiceSel) voiceSel.value = voiceId;
  document.querySelectorAll('.voice-item').forEach(el => {
    const on = el.dataset.voiceId === voiceId;
    el.classList.toggle('selected', on);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  document.querySelectorAll('.wizard-voice-tile').forEach(el => {
    const on = el.dataset.voiceId === voiceId;
    el.classList.toggle('selected', on);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  try {
    const s = typeof loadSettings === 'function' ? loadSettings() : {};
    s.voice = voiceId;
    localStorage.setItem('vm_settings', JSON.stringify(s));
  } catch (_) {}
  updateWizardNav();
  if (autoAdvance) wizardAdvanceFrom('voice');
}

async function previewVoice(voiceId) {
  const targetLang = document.getElementById('target-lang').value;
  const styleId = dubStylesState.selectedId || dubStylesState.defaultId || 'modern';
  try {
    const r = await fetch('/api/auto_dub/preview_style', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        voice: voiceId,
        target_lang: targetLang,
        dub_style: styleId,
        tts_engine: currentTtsBackend(),
        engine_id: currentTtsBackend(),
      }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'preview failed');
    const audio = new Audio('/api/download/' + encodeURIComponent(d.file));
    audio.play();
  } catch (e) {
    vmNotify(t('dub.voice_preview_fail', 'Не удалось прослушать голос'), 'warning');
  }
}

function initLanguagePicker() {
  const combobox = document.getElementById('target-lang-combobox');
  const trigger = document.getElementById('target-lang-trigger');
  const dropdown = document.getElementById('target-lang-dropdown');
  const label = document.getElementById('target-lang-label');
  const list = document.getElementById('target-lang-list');
  const search = document.getElementById('target-lang-search');
  const select = document.getElementById('target-lang');
  if (!list || !select || !trigger || !dropdown) return;

  const langs = window.VM_LANGUAGES || {};
  const entries = Object.entries(langs);
  const nameByCode = Object.fromEntries(entries.map(([name, code]) => [code, name]));

  function syncLabel() {
    if (label) label.textContent = nameByCode[select.value] || select.value || '—';
  }

  function closeDropdown() {
    dropdown.hidden = true;
    dropdown.classList.remove('open');
    trigger.setAttribute('aria-expanded', 'false');
  }

  function openDropdown() {
    dropdown.hidden = false;
    requestAnimationFrame(() => dropdown.classList.add('open'));
    trigger.setAttribute('aria-expanded', 'true');
    if (typeof vmUiSound === 'function') vmUiSound('open');
    if (search) {
      search.value = '';
      search.focus();
      render('');
    } else {
      render('');
    }
  }

  function toggleDropdown() {
    if (dropdown.classList.contains('open')) closeDropdown();
    else openDropdown();
  }

  function pickLang(code) {
    select.value = code;
    syncLabel();
    render(search ? search.value : '');
    updateVoiceList();
    loadDubStyles();
    closeDropdown();
    if (typeof vmUiSound === 'function') vmUiSound('select');
  }

  function render(filter) {
    const q = (filter || '').trim().toLowerCase();
    list.innerHTML = entries
      .filter(([name, code]) => !q || name.toLowerCase().includes(q) || code.toLowerCase().includes(q))
      .map(([name, code]) => {
        const sel = select.value === code;
        return `<div class="lang-item ${sel ? 'selected' : ''}" data-lang-code="${escHtml(code)}" role="option" aria-selected="${sel}">
          <div><div class="lang-item-name">${escHtml(name)}</div><div class="lang-item-code">${escHtml(code)}</div></div>
        </div>`;
      }).join('');
    list.querySelectorAll('.lang-item').forEach(el => {
      el.addEventListener('click', () => pickLang(el.dataset.langCode));
    });
  }

  trigger.addEventListener('click', e => {
    e.stopPropagation();
    toggleDropdown();
  });

  if (search) search.addEventListener('input', () => render(search.value));

  document.addEventListener('click', e => {
    if (!combobox || !combobox.contains(e.target)) closeDropdown();
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeDropdown();
  });

  select.addEventListener('change', () => {
    syncLabel();
    render(search ? search.value : '');
  });

  syncLabel();
  render('');
  closeDropdown();
}

function showDubStarting(on) {
  const btn = document.getElementById('btn-start-dub');
  const ind = document.getElementById('dub-start-indicator');
  if (btn) btn.classList.toggle('is-starting', !!on);
  if (ind) ind.style.display = on ? 'flex' : 'none';
  if (on && typeof vmUiSound === 'function') vmUiSound('start');
}

async function loadVoiceCatalog() {
  try {
    const r = await fetch('/api/auto_dub/voice_catalog');
    const d = await r.json();
    if (d.ok && d.voices) VM_VOICE_CATALOG = d.voices;
  } catch (_) {}
}

function setSourceMode(mode) {
  const sel = document.getElementById('source-lang');
  const detectedRow = document.getElementById('detected-lang-row');
  const isAuto = mode === 'auto';
  const autoRadio = document.getElementById('source-auto');
  const manualRadio = document.getElementById('source-manual');
  if (autoRadio) autoRadio.checked = isAuto;
  if (manualRadio) manualRadio.checked = !isAuto;
  if (sel) {
    sel.disabled = isAuto;
    sel.style.display = isAuto ? 'none' : 'inline-block';
  }
  if (detectedRow && isAuto && detectedRow.dataset.langCode) {
    detectedRow.style.display = 'block';
  } else if (detectedRow && !isAuto) {
    detectedRow.style.display = 'none';
  }
  const wizAuto = document.getElementById('wizard-source-auto');
  const wizManual = document.getElementById('wizard-source-manual');
  const wizSel = document.getElementById('wizard-source-lang-select');
  const wizDetected = document.getElementById('wizard-detected-lang');
  if (wizAuto) wizAuto.checked = isAuto;
  if (wizManual) wizManual.checked = !isAuto;
  if (wizSel) {
    wizSel.style.display = isAuto ? 'none' : 'block';
    wizSel.disabled = isAuto;
    if (sel && sel.value) wizSel.value = sel.value;
  }
  if (wizDetected) {
    wizDetected.style.display = (isAuto && detectedRow && detectedRow.dataset.langCode) ? 'block' : 'none';
  }
}

function updateDetectedLangDisplay(code, name) {
  if (!code) return;
  const row = document.getElementById('detected-lang-row');
  const label = document.getElementById('detected-lang-name');
  const sel = document.getElementById('source-lang');
  if (!row || !label) return;

  const displayName = name || code;
  label.textContent = displayName;
  row.dataset.langCode = code;
  row.style.display = document.getElementById('source-auto')?.checked ? 'block' : 'none';

  if (sel) {
    const opt = Array.from(sel.options).find(o => o.value === code);
    if (opt) sel.value = code;
  }
  const wizName = document.getElementById('wizard-detected-lang-name');
  const wizDetected = document.getElementById('wizard-detected-lang');
  const wizSel = document.getElementById('wizard-source-lang-select');
  if (wizName) wizName.textContent = displayName;
  if (wizDetected) {
    wizDetected.style.display = document.getElementById('source-auto')?.checked ? 'block' : 'none';
  }
  if (wizSel && sel) wizSel.value = sel.value;
}

function initWizardSourceLang() {
  const hidden = document.getElementById('source-lang');
  const wizSel = document.getElementById('wizard-source-lang-select');
  if (hidden && wizSel && !wizSel.options.length) {
    Array.from(hidden.options).forEach(opt => {
      const o = document.createElement('option');
      o.value = opt.value;
      o.textContent = opt.textContent;
      wizSel.appendChild(o);
    });
    if (hidden.value) wizSel.value = hidden.value;
  }
  const wizAuto = document.getElementById('wizard-source-auto');
  const wizManual = document.getElementById('wizard-source-manual');
  if (wizAuto) {
    wizAuto.addEventListener('change', () => {
      if (wizAuto.checked) setSourceMode('auto');
    });
  }
  if (wizManual) {
    wizManual.addEventListener('change', () => {
      if (wizManual.checked) setSourceMode('manual');
    });
  }
  if (wizSel) {
    wizSel.addEventListener('change', () => {
      if (hidden) {
        hidden.value = wizSel.value;
        hidden.dispatchEvent(new Event('change'));
      }
      setSourceMode('manual');
    });
  }
}

function setupUpload() {
  const drop = document.getElementById('drop-zone');
  const input = document.getElementById('video-input');
  const pickBtn = document.getElementById('btn-pick-video');

  pickBtn.addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    if (input.files && input.files[0]) uploadVideo(input.files[0]);
  });

  ['dragenter', 'dragover'].forEach(ev => {
    drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('drag-over'); });
  });
  ['dragleave', 'drop'].forEach(ev => {
    drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('drag-over'); });
  });
  drop.addEventListener('drop', e => {
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) uploadVideo(f);
  });
}

async function uploadVideo(file) {
  if (state.uploading || state.running) return;
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['mp4', 'mkv', 'mov', 'avi', 'webm'].includes(ext)) {
    vmNotify(t('dub.err_format', 'Неподдерживаемый формат видео'), 'error');
    return;
  }

  state.uploading = true;
  document.getElementById('btn-start-dub').disabled = true;

  const fd = new FormData();
  fd.append('file', file);

  try {
    const r = await fetch('/api/dub/upload_video', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Upload failed');

    // ── Project isolation: reset all per-project state on new upload ──────
    // Without this, state.taskId from the previous project persists and the
    // status panel can briefly display old segments/audio.
    if (state.polling) clearInterval(state.polling);
    state.taskId = null;
    state.running = false;
    state.starting = false;
    state.lastProgress = 0;
    state.lastSlotFitLogIdx = -1;
    state.redub = null;
    // ──────────────────────────────────────────────────────────────────────
    state.filename = 'uploads/' + d.filename;
    state.videoName = file.name;
    warnIfOutputFilename(file.name);

    document.getElementById('video-info').style.display = 'flex';
    document.getElementById('video-name').textContent = file.name;
    document.getElementById('video-size').textContent = (d.size_mb || '?') + ' МБ';
    document.getElementById('btn-start-dub').disabled = false;
    updateWizardNav();
    vmNotify(t('dub.video_ready', 'Видео загружено'), 'success', 2500);
    wizardAdvanceFrom('video');
  } catch (e) {
    vmNotify(vmFriendlyError(e.message), 'error');
  } finally {
    state.uploading = false;
  }
}

function renderProgressSteps(currentStep, status) {
  const ul = document.getElementById('progress-steps');
  ul.innerHTML = DUB_STEPS.map(s => {
    const label = t(s.i18n, s.key);
    let cls = '';
    const idx = DUB_STEPS.findIndex(x => x.key === currentStep);
    const sIdx = DUB_STEPS.findIndex(x => x.key === s.key);
    if (status === 'done') cls = 'done';
    else if (s.key === currentStep) cls = 'active';
    else if (idx > sIdx) cls = 'done';
    return `<li class="${cls}">${label}</li>`;
  }).join('');
}

function getStyleVolumePct(styleId) {
  const row = dubStylesState.styles.find(s => s.id === styleId);
  if (row && row.original_volume_pct != null) return Number(row.original_volume_pct) || 0;
  // full_dub styles default to mute — never invent 20%
  if (styleId === 'cinematic' || styleId === 'modern' || styleId === 'professional'
      || styleId === 'cinema' || styleId === 'full_dub') {
    return 0;
  }
  return 20;
}

function persistOriginalVolumePct(pct) {
  const v = Math.max(0, Math.min(100, Number(pct) || 0));
  try {
    const s = typeof loadSettings === 'function' ? loadSettings() : {};
    s.originalVolume = v;
    localStorage.setItem('vm_settings', JSON.stringify(s));
  } catch (_) {}
  return v;
}

function loadSavedOriginalVolumePct() {
  try {
    const s = typeof loadSettings === 'function' ? loadSettings() : {};
    if (s.originalVolume != null && Number.isFinite(Number(s.originalVolume))) {
      return Math.max(0, Math.min(100, Number(s.originalVolume)));
    }
  } catch (_) {}
  return null;
}

function getReviewOriginalVolumePct() {
  const el = document.getElementById('tr-original-volume');
  if (el) return Number(el.value) || 0;
  const wiz = document.getElementById('wizard-original-volume');
  if (wiz) return Number(wiz.value) || 0;
  return loadSavedOriginalVolumePct() ?? 20;
}

function setReviewOriginalVolumePct(pct, { persist = true } = {}) {
  const v = Math.max(0, Math.min(100, Number(pct) || 0));
  const el = document.getElementById('tr-original-volume');
  const lbl = document.getElementById('tr-original-volume-label');
  if (el) el.value = String(v);
  if (lbl) lbl.textContent = v + '%';
  const wiz = document.getElementById('wizard-original-volume');
  const wizLbl = document.getElementById('wizard-original-volume-label');
  if (wiz) wiz.value = String(v);
  if (wizLbl) wizLbl.textContent = v + '%';
  // Keep wizard/main slider in sync and mark custom so style change won't wipe to 0%.
  setOriginalVolumePct(v, false);
  if (persist) persistOriginalVolumePct(v);
}

function bindReviewOriginalMixControls() {
  const el = document.getElementById('tr-original-volume');
  if (el && !el.dataset.bound) {
    el.dataset.bound = '1';
    el.addEventListener('input', () => setReviewOriginalVolumePct(el.value));
  }
  document.querySelectorAll('.tr-vol-preset').forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => setReviewOriginalVolumePct(btn.dataset.pct));
  });
  const wiz = document.getElementById('wizard-original-volume');
  if (wiz && !wiz.dataset.bound) {
    wiz.dataset.bound = '1';
    wiz.addEventListener('input', () => setReviewOriginalVolumePct(wiz.value));
  }
  document.querySelectorAll('.wiz-vol-preset').forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => setReviewOriginalVolumePct(btn.dataset.pct));
  });
}

function getStyleHintKey(styleId) {
  const row = dubStylesState.styles.find(s => s.id === styleId);
  return row && row.i18n_key ? `${row.i18n_key}_hint` : '';
}

async function loadDubStyles() {
  const list = document.getElementById('dub-style-list');
  const targetLang = document.getElementById('target-lang')?.value || 'ru';
  const localOnly = getStylesLocalOnly();
  dubStylesState.localOnly = localOnly;
  dubStylesState.loading = true;
  dubStylesState.error = '';
  renderWizardStyleGrid();
  const abortCtl = new AbortController();
  const abortTimer = setTimeout(() => abortCtl.abort(), 45000);
  try {
    const qs = new URLSearchParams({
      target_lang: targetLang,
      local_only: localOnly ? '1' : '0',
    });
    const r = await fetch('/api/auto_dub/styles?' + qs.toString(), {
      signal: abortCtl.signal,
    });
    clearTimeout(abortTimer);
    const raw = await r.text();
    let d;
    try {
      d = raw ? JSON.parse(raw) : {};
    } catch (_) {
      throw new Error('invalid styles response');
    }
    if (!r.ok) throw new Error(d.error || 'styles failed');
    dubStylesState.styles = d.styles || [];
    dubStylesState.sections = d.sections || [];
    dubStylesState.defaultId = d.default || 'modern';
    dubStylesState.regionalPack = d.regional_pack || null;
    dubStylesState.loaded = true;
    dubStylesState.loading = false;
    dubStylesState.error = '';

    const saved = typeof loadSettings === 'function' ? loadSettings() : {};
    const prevSelected = dubStylesState.selectedId || saved.dubStyle;
    const ids = new Set(dubStylesState.styles.map(s => s.id));
    const preferred = ids.has(prevSelected) ? prevSelected
      : (ids.has(saved.dubStyle) ? saved.dubStyle : dubStylesState.defaultId);

    if (list) {
      const bySection = {};
      dubStylesState.styles.forEach(style => {
        const sec = style.is_regional ? (style.region_pack || 'regional') : 'base';
        if (!bySection[sec]) bySection[sec] = [];
        bySection[sec].push(style);
      });

      const sectionOrder = (d.sections || []).map(s => s.id);
      if (!sectionOrder.length) sectionOrder.push('base');

      let html = '';
      sectionOrder.forEach(secId => {
        const items = bySection[secId];
        if (!items || !items.length) return;
        const secMeta = (d.sections || []).find(s => s.id === secId);
        const secLabel = secMeta && secMeta.label_key
          ? t(secMeta.label_key, secId)
          : (secId === 'base' ? t('dub.styles_section_base', 'Универсальные') : secId);
        html += `<div class="dub-style-section"><div class="dub-style-section-title">${escHtml(secLabel)}</div><div class="dub-style-section-items">`;
        html += items.map(style => {
          const nameKey = style.i18n_key || `dub.style_${style.id}`;
          const hintKey = `${nameKey}_hint`;
          const name = t(nameKey, style.id);
          const hint = t(hintKey, '');
          const checked = style.id === preferred ? ' checked' : '';
          return `<label class="dub-style-option">
          <input type="radio" name="dub-style" value="${style.id}"${checked} />
          <span class="dub-style-card">
            <span class="dub-style-name">${escHtml(name)}</span>
            <span class="dub-style-desc">${escHtml(hint)}</span>
          </span>
        </label>`;
        }).join('');
        html += '</div></div>';
      });

      list.innerHTML = html || `<div class="char-count">${escHtml(t('dub.styles_empty', 'Нет стилей'))}</div>`;
    }

    bindDubStyleControls();
    // Prefer saved Settings volume; don't force style default over user choice.
    applyStyleVolumePreset(getSelectedDubStyle(), false);
    updateDubStyleUI();
    renderWizardStyleGrid();
  } catch (e) {
    clearTimeout(abortTimer);
    dubStylesState.loading = false;
    dubStylesState.loaded = false;
    const timedOut = e && e.name === 'AbortError';
    dubStylesState.error = timedOut
      ? t('dub.styles_timeout', 'Загрузка режимов заняла слишком много времени')
      : t('dub.styles_error', 'Не удалось загрузить режимы');
    if (list) {
      list.innerHTML = `<div class="char-count">${escHtml(dubStylesState.error)}</div>`;
    }
    console.warn('loadDubStyles', e);
    renderWizardStyleGrid();
  }
}

function getSelectedDubStyle() {
  const checked = document.querySelector('input[name="dub-style"]:checked');
  const id = (checked && checked.value) || dubStylesState.defaultId || 'modern';
  dubStylesState.selectedId = id;
  return id;
}

function getOriginalVolumePct() {
  const el = document.getElementById('original-volume');
  return el ? Number(el.value) : 0;
}

function setOriginalVolumePct(pct, fromPreset) {
  const el = document.getElementById('original-volume');
  const lbl = document.getElementById('original-volume-label');
  if (!el) return;
  el.value = String(Math.max(0, Math.min(100, pct)));
  if (lbl) lbl.textContent = el.value + '%';
  if (!fromPreset) el.dataset.custom = '1';
}

function getStylePayload() {
  // Prefer explicit user volume: review → wizard → hidden slider → settings.
  const styleId = getSelectedDubStyle();
  const styleDefault = getStyleVolumePct(styleId);
  let vol = null;
  if (document.getElementById('tr-original-volume')) {
    vol = getReviewOriginalVolumePct();
  } else if (document.getElementById('wizard-original-volume')) {
    vol = Number(document.getElementById('wizard-original-volume').value);
    if (!Number.isFinite(vol)) vol = null;
  } else {
    vol = getOriginalVolumePct();
  }
  const saved = loadSavedOriginalVolumePct();
  // Mute styles: ignore stale saved 20% unless user marked slider custom
  const slider = document.getElementById('original-volume');
  const userCustom = slider && slider.dataset.custom === '1';
  if (styleDefault <= 0 && !userCustom && (vol == null || vol <= 25)) {
    vol = 0;
  } else if ((vol == null || !Number.isFinite(vol)) && saved != null) {
    vol = saved;
  }
  if (vol == null || !Number.isFinite(vol)) vol = styleDefault;
  return {
    dub_style: styleId,
    original_volume: vol,
    keep_original_track: document.getElementById('keep-original-track').checked,
  };
}

function updateDubStyleUI() {
  const style = getSelectedDubStyle();
  const hintEl = document.getElementById('dub-style-hint');
  const volGroup = document.getElementById('original-volume-group');
  const volSlider = document.getElementById('original-volume');
  const previewBtn = document.getElementById('btn-preview-style');
  const hintKey = getStyleHintKey(style);
  if (hintEl) {
    hintEl.textContent = hintKey ? t(hintKey, '') : '';
  }
  const subsOnly = style === 'subtitles_only';
  const row = dubStylesState.styles.find(s => s.id === style);
  const previewOk = row && row.preview_available !== false && !subsOnly;
  if (volGroup) volGroup.style.opacity = subsOnly ? '0.45' : '1';
  if (volSlider) volSlider.disabled = subsOnly;
  if (previewBtn) previewBtn.disabled = !previewOk;
  document.querySelectorAll('.vol-preset').forEach(btn => {
    btn.disabled = subsOnly;
  });
}

let stylePreviewAudio = null;

async function previewDubStyle() {
  const btn = document.getElementById('btn-preview-style');
  if (!btn || btn.disabled) return;
  const voice = document.getElementById('voice-select')?.value;
  const targetLang = document.getElementById('target-lang')?.value;
  btn.disabled = true;
  try {
    const r = await fetch('/api/auto_dub/preview_style', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        dub_style: getSelectedDubStyle(),
        voice,
        target_lang: targetLang,
        ui_lang: typeof getUiLang === 'function' ? getUiLang() : 'ru',
      }),
    });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'preview failed');
    if (stylePreviewAudio) {
      stylePreviewAudio.pause();
      stylePreviewAudio = null;
    }
    stylePreviewAudio = new Audio('/output/' + encodeURIComponent(d.file) + '?t=' + Date.now());
    stylePreviewAudio.play();
    vmNotify(t('dub.preview_playing', 'Воспроизведение примера…'), 'info', 2500);
  } catch (e) {
    vmNotify(vmFriendlyError(e.message), 'error');
  } finally {
    btn.disabled = false;
    updateDubStyleUI();
  }
}

function applyStyleVolumePreset(styleId, force) {
  const saved = loadSavedOriginalVolumePct();
  if (!force && saved != null) {
    setReviewOriginalVolumePct(saved, { persist: false });
    return;
  }
  const vol = getStyleVolumePct(styleId);
  const slider = document.getElementById('original-volume');
  if (!force && slider && slider.dataset.custom === '1') return;
  setOriginalVolumePct(vol, true);
  setReviewOriginalVolumePct(vol, { persist: false });
  if (slider) delete slider.dataset.custom;
}

function bindDubStyleControls() {
  document.querySelectorAll('input[name="dub-style"]').forEach(radio => {
    radio.replaceWith(radio.cloneNode(true));
  });
  document.querySelectorAll('input[name="dub-style"]').forEach(radio => {
    radio.addEventListener('change', () => {
      dubStylesState.selectedId = radio.value;
      applyStyleVolumePreset(getSelectedDubStyle(), true);
      updateDubStyleUI();
      renderWizardStyleGrid();
      try {
        const s = typeof loadSettings === 'function' ? loadSettings() : {};
        s.dubStyle = radio.value;
        localStorage.setItem('vm_settings', JSON.stringify(s));
      } catch (_) {}
    });
  });

  const previewBtn = document.getElementById('btn-preview-style');
  if (previewBtn) {
    previewBtn.replaceWith(previewBtn.cloneNode(true));
    document.getElementById('btn-preview-style')?.addEventListener('click', previewDubStyle);
  }

  const vol = document.getElementById('original-volume');
  if (vol) {
    vol.addEventListener('input', () => {
      const lbl = document.getElementById('original-volume-label');
      if (lbl) lbl.textContent = vol.value + '%';
      vol.dataset.custom = '1';
      setReviewOriginalVolumePct(vol.value);
    });
  }

  document.querySelectorAll('.vol-preset').forEach(btn => {
    btn.addEventListener('click', () => {
      const pct = Number(btn.dataset.pct);
      if (!Number.isFinite(pct)) return;
      setOriginalVolumePct(pct, false);
      setReviewOriginalVolumePct(pct);
    });
  });
}

async function maybeRunStorageWizard() {
  const overlay = document.getElementById('storage-wizard-overlay');
  if (!overlay) return true;
  try {
    const r = await fetch('/api/prepare/storage');
    const d = await r.json();
    if (!d.needs_wizard) return true;

    const sel = document.getElementById('storage-wizard-drive');
    const info = document.getElementById('storage-wizard-info');
    if (sel) {
      sel.innerHTML = (d.drives || []).map((dr) =>
        `<option value="${dr.path}">${dr.label} — ${dr.free_gb} ГБ своб.</option>`
      ).join('');
    }
    if (info) {
      info.textContent = t(
        'dub.storage_wizard_hint',
        'Компоненты занимают до нескольких ГБ. Рекомендуем диск с наибольшим свободным местом.'
      );
    }

    overlay.style.display = 'flex';
    return await new Promise((resolve) => {
      const skipBtn = document.getElementById('storage-wizard-skip');
      const okBtn = document.getElementById('storage-wizard-ok');
      const cleanup = () => {
        skipBtn?.removeEventListener('click', onSkip);
        okBtn?.removeEventListener('click', onOk);
      };
      const onSkip = async () => {
        cleanup();
        overlay.style.display = 'none';
        await fetch('/api/prepare/storage/skip', { method: 'POST' });
        resolve(true);
      };
      const onOk = async () => {
        cleanup();
        const path = sel?.value;
        if (path) {
          await fetch('/api/prepare/storage/root', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
          });
        } else {
          await fetch('/api/prepare/storage/skip', { method: 'POST' });
        }
        overlay.style.display = 'none';
        resolve(true);
      };
      skipBtn?.addEventListener('click', onSkip);
      okBtn?.addEventListener('click', onOk);
    });
  } catch (_) {
    return true;
  }
}

async function confirmStorageCleanup(candidates) {
  const lines = (candidates || [])
    .slice(0, 5)
    .map((c) => `• ${c.label || c.id} (${c.size_mb || '?'} МБ)`)
    .join('\n');
  const msg = t(
    'dub.storage_lru_msg',
    'Компоненты занимают больше лимита. Освободить место?\n\n{items}'
  ).replace('{items}', lines);
  return window.confirm(msg);
}

async function maybeFreeStorage(checkData) {
  if (!checkData?.storage_needs_confirm || !(checkData.lru_candidates || []).length) {
    return true;
  }
  const ok = await confirmStorageCleanup(checkData.lru_candidates);
  if (!ok) return false;
  try {
    const r = await fetch('/api/prepare/lru', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed: true }),
    });
    const d = await r.json();
    return Boolean(d.ok);
  } catch (_) {
    return false;
  }
}

async function runComponentPrepare() {
  if (!(await maybeRunStorageWizard())) return false;

  const sourceAuto = document.getElementById('source-auto')?.checked;
  const uiLang = localStorage.getItem('vm_ui_lang') || 'ru';
  const opts = {
    source_lang: sourceAuto ? null : (document.getElementById('source-lang')?.value || null),
    target_lang: document.getElementById('target-lang')?.value || 'ru',
    whisper_size: (function () {
      if (typeof syncWizardModelSize === 'function') syncWizardModelSize(true);
      return document.getElementById('model-size')?.value || 'small';
    })(),
    feature: 'dub',
    ui_lang: uiLang,
  };

  try {
    const chk = await fetch('/api/prepare/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
    });
    const cd = await chk.json();
    if (cd.ready) return true;
    if (!(await maybeFreeStorage(cd))) {
      vmNotify(t('dub.storage_lru_cancel', 'Недостаточно места для подготовки компонентов'), 'warn', 8000);
      return false;
    }
    if (cd.disk_warning) {
      const proceed = window.confirm(
        cd.disk_warning_message ||
          t('dub.disk_warning', 'Мало места на диске. Продолжить загрузку?')
      );
      if (!proceed) return false;
    }
  } catch (_) {
    return false;
  }

  if (typeof runLanguagePackPrepare !== 'function') return false;
  return runLanguagePackPrepare(opts);
}

async function startDub() {
  if (!state.filename || state.running || state.starting) return;

  if (state.videoName && /_OUTPUT_/i.test(state.videoName)) {
    vmNotify(
      t(
        'dub.err_output_file',
        'Файл с _OUTPUT_ в имени — это уже готовый дубляж. Выберите оригинальное видео без _OUTPUT_.'
      ),
      'error',
      9000
    );
    return;
  }

  state.starting = true;
  showDubStarting(true);
  document.getElementById('btn-start-dub').disabled = true;
  wizardShowScreen('progress');
  updateWizardPhases('preparing', 'running');
  setProgressInfoMessage(t('dub.step_preparing', 'Подготовка'));

  if (typeof ensureFeature === 'function') {
    const ok = await ensureFeature(
      'auto_dub',
      'Авто-дубляж доступен в тестовом или Premium-периоде. Введите ключ в Настройках.'
    );
    if (!ok) {
      state.starting = false;
      showDubStarting(false);
      document.getElementById('btn-start-dub').disabled = false;
      wizardGoToStep(WIZARD_STEP_IDS.length - 1);
      return;
    }
  }

  const aiProceed = await ensureAiModuleForDub();
  if (!aiProceed) {
    state.starting = false;
    showDubStarting(false);
    document.getElementById('btn-start-dub').disabled = false;
    wizardGoToStep(WIZARD_STEP_IDS.length - 1);
    return;
  }

  const prepared = await runComponentPrepare();
  if (!prepared) {
    state.starting = false;
    showDubStarting(false);
    document.getElementById('btn-start-dub').disabled = false;
    wizardGoToStep(WIZARD_STEP_IDS.length - 1);
    return;
  }

  if (state.polling) {
    clearInterval(state.polling);
    state.polling = null;
  }

  state.running = true;
  _dubBusy(true);
  state.lastProgress = 0;
  state.lastProgressAt = Date.now();
  state.stallNotified = false;
  state.reviewPauseNotified = false;
  state.reviewOverlayAutoOpened = false;
  state.diagnosticsShown = false;
  state.statusFetchFailures = 0;
  translationReviewState.preTts = false;
  document.getElementById('segments-panel').style.display = 'none';

  const sourceAuto = document.getElementById('source-auto').checked;
  const uiLang = localStorage.getItem('vm_ui_lang') || 'ru';

  const body = {
    video_path: state.filename,
    target_lang: document.getElementById('target-lang').value,
    source_lang: sourceAuto ? null : document.getElementById('source-lang').value,
    voice: state.selectedVoice || document.getElementById('voice-select').value,
    tts_engine: currentTtsBackend(),
    tts_backend: currentTtsBackend(),
    ...(currentTtsBackend() === 'tts_uk' ? readMykytaControls() : {}),
    model_size: (function () {
      if (typeof syncWizardModelSize === 'function') syncWizardModelSize(true);
      return document.getElementById('model-size')?.value || 'small';
    })(),
    ui_lang: uiLang,
    content_mode: (document.getElementById('content-mode') || {}).value || 'movie',
    segmentation_mode: (document.getElementById('segmentation-mode') || {}).value || 'adaptive',
    adaptive_segmentation: (function () {
      const pick = (uiId, hiddenId) =>
        document.getElementById(uiId) || document.getElementById(hiddenId);
      const enEl = pick('adaptive-seg-enabled-ui', 'adaptive-seg-enabled');
      const minEl = pick('adaptive-seg-min-s-ui', 'adaptive-seg-min-s');
      const maxEl = pick('adaptive-seg-max-s-ui', 'adaptive-seg-max-s');
      const prefEl = pick('adaptive-seg-preferred-s-ui', 'adaptive-seg-preferred-s');
      const aggEl = pick('adaptive-seg-aggressiveness-ui', 'adaptive-seg-aggressiveness');
      const meanEl = pick('adaptive-seg-meaning-ui', 'adaptive-seg-meaning');
      const forecastEl = pick('adaptive-seg-tts-forecast-ui', 'adaptive-seg-tts-forecast');
      const toMs = (el, fallbackS) => {
        const v = el ? Number(el.value) : fallbackS;
        return Math.round((Number.isFinite(v) ? v : fallbackS) * 1000);
      };
      const enabled = enEl ? !!enEl.checked : true;
      // Keep segmentation_mode coherent with the toggle
      const modeEl = document.getElementById('segmentation-mode');
      if (modeEl && enabled && modeEl.value === 'timing') {
        /* leave user's timing choice; adaptive still runs via flag/settings */
      }
      return {
        enabled,
        min_ms: toMs(minEl, 4.5),
        max_ms: toMs(maxEl, 16),
        preferred_ms: toMs(prefEl, 9),
        aggressiveness: aggEl
          ? Math.max(0, Math.min(1, Number(aggEl.value) / 100))
          : 0.65,
        use_meaning: meanEl ? !!meanEl.checked : true,
        use_tts_forecast: forecastEl ? !!forecastEl.checked : true,
      };
    })(),
    ocr_enabled: false,
    translation_review_before_tts: !!(document.getElementById('translation-review-before-tts') || {}).checked,
    strict_llm_adaptation: (document.getElementById('strict-llm-adaptation') || {}).value || 'automatic',
    ...getStylePayload(),
  };

  if (state.redub) {
    body.source_segments = state.redub.source_segments;
    body.timing_map = state.redub.timing_map;
    if (state.redub.skip_translate) {
      body.translated_segments = state.redub.translated_segments;
      body.skip_translate = true;
    }
  }

  try {
    let taskId = state.taskId;
    const useRestart = taskId && state.cancelledTask && state.pipelineCheckpoint;

    if (useRestart) {
      const rr = await fetch(`/api/auto_dub/restart/${encodeURIComponent(taskId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_lang: body.target_lang,
          voice: body.voice,
        }),
      });
      const rd = await rr.json();
      if (!rr.ok) throw new Error(rd.error || 'Restart failed');
      state.cancelledTask = false;
    } else {
      const r = await fetch('/api/auto_dub/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (r.status === 409 && d.error_code === 'prepare_required') {
        const reprepared = await runComponentPrepare();
        if (!reprepared) throw new Error(d.error || 'Prepare required');
        const r2 = await fetch('/api/auto_dub/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const d2 = await r2.json();
        if (!r2.ok) throw new Error(d2.error || 'Start failed');
        taskId = d2.task_id;
      } else if (!r.ok) {
        throw new Error(d.error || 'Start failed');
      } else {
        taskId = d.task_id;
      }
      state.taskId = taskId;
    }

    window.__vmLastTaskId = state.taskId;
    state.starting = false;
    showDubStarting(false);
    localStorage.setItem('vm_active_task', JSON.stringify({
      taskId: state.taskId,
      videoName: state.videoName,
      startedAt: Date.now(),
    }));

    pollStatus();
  } catch (e) {
    state.running = false;
    state.starting = false;
    showDubStarting(false);
    _dubBusy(false);
    document.getElementById('btn-start-dub').disabled = false;
    wizardGoToStep(WIZARD_STEP_IDS.length - 1);
    if (typeof vmUiSound === 'function') vmUiSound('error');
    vmNotify(vmFriendlyError(e.message), 'error');
  }
}

function formatDurationSec(sec) {
  const s = Math.max(0, parseInt(sec, 10) || 0);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m}:${String(r).padStart(2, '0')}` : `${r}s`;
}

function renderDeveloperPreview(payload) {
  const panel = document.getElementById('dev-preview-panel');
  if (!panel) return;
  panel.hidden = false;

  const preview = (payload && payload.preview) || {};
  const timing = (payload && payload.timing) || {};
  const timeline = (payload && payload.timeline) || [];
  const perf = (payload && payload.performance) || [];
  const err = (payload && payload.first_error) || preview.first_error;

  const timingEl = document.getElementById('dev-preview-timing');
  if (timingEl) {
    const parts = [
      `⏱ ${formatDurationSec(timing.elapsed_sec)}`,
      timing.eta_sec != null ? `ETA ~${formatDurationSec(timing.eta_sec)}` : null,
      timing.segments_ready != null ? `Seg ${timing.segments_ready}/${timing.segments_total || '?'}` : null,
      timing.avg_segment_ms ? `~${Math.round(timing.avg_segment_ms / 1000)}s/seg` : null,
    ].filter(Boolean);
    timingEl.textContent = parts.join(' · ');
  }

  const tlEl = document.getElementById('dev-preview-timeline');
  if (tlEl) {
    tlEl.innerHTML = timeline.map((row) => {
      const ms = row.duration_ms ? ` ${Math.round(row.duration_ms / 1000)}s` : '';
      return `<div class="dev-preview-agent ${escHtml(row.status || 'pending')}" title="${escHtml(row.label || row.agent)}">${escHtml(row.label || row.agent)}${ms}</div>`;
    }).join('');
  }

  const perfEl = document.getElementById('dev-preview-perf');
  if (perfEl) {
    perfEl.innerHTML = perf.length
      ? perf.map((r) => `${escHtml(r.label || r.agent)} — ${Number(r.duration_sec || 0).toFixed(1)}s`).join('<br>')
      : '';
  }

  const errEl = document.getElementById('dev-preview-error');
  if (errEl) {
    if (err && err.message) {
      errEl.hidden = false;
      errEl.textContent = (err.code ? `[${err.code}] ` : '') + err.message;
    } else {
      errEl.hidden = true;
      errEl.textContent = '';
    }
  }

  const video = document.getElementById('dev-preview-video');
  if (video && preview.url && preview.generation) {
    if (state.devPreviewGen !== preview.generation) {
      state.devPreviewGen = preview.generation;
      video.src = preview.url + '?t=' + Date.now();
    }
  }
}

function pollStatus() {
  if (state.polling) clearInterval(state.polling);
  state.polling = setInterval(checkStatus, 1000);
  checkStatus();
}

function updateDebugModeBadge(active) {
  const el = document.getElementById('debug-mode-badge');
  if (!el) return;
  el.hidden = !active;
  if (active) {
    el.textContent = t('dub.debug_mode_badge', 'Debug Mode');
  }
}

async function loadDebugModeFlag() {
  try {
    const r = await fetch('/api/auto_dub/debug_mode');
    if (!r.ok) return;
    const d = await r.json();
    updateDebugModeBadge(!!d.enabled);
  } catch (_) {}
}

async function checkStatus() {
  if (!state.taskId) return;
  // Avoid stacking polls when status is slow (heavy Whisper/TTS) — overlapping
  // fetches used to look like "Нет связи с сервером" after 4 failures.
  if (state.statusCheckInFlight) return;
  state.statusCheckInFlight = true;

  try {
    const uiLang = localStorage.getItem('vm_ui_lang') || 'ru';
    const dev = typeof isDevMode === 'function' && isDevMode();
    const lite = dev ? '' : '&lite=1';
    const r = await fetch(`/api/auto_dub/status/${state.taskId}?lang=${uiLang}${lite}`);
    if (!r.ok) {
      throw new Error(r.status === 404
        ? t('dub.task_not_found', 'Задача не найдена')
        : t('dub.server_error', 'Ошибка сервера'));
    }
    const d = await r.json();
    state.statusFetchFailures = 0;

    if (d.status === 'running' || d.status === 'translation_review' || d.status === 'studio_ready') {
      wizardShowScreen('progress');
    }

    const pct = Math.round(Number(d.progress) || 0);
    document.getElementById('progress-fill').style.width = pct + '%';
    document.getElementById('progress-percent').textContent = pct + '%';
    const pctHead = document.getElementById('wizard-progress-pct');
    if (pctHead) pctHead.textContent = pct + '%';
    setProgressInfoMessage(buildProgressInfoLine(d));

    if (typeof d.debug_learning_mode === 'boolean') {
      updateDebugModeBadge(d.debug_learning_mode);
    }

    if (pct !== state.lastProgress) {
      state.lastProgress = pct;
      state.lastProgressAt = Date.now();
      state.stallNotified = false;
    } else if (
      d.status === 'running' &&
      Date.now() - state.lastProgressAt > 120000 &&
      !state.stallNotified
    ) {
      state.stallNotified = true;
      vmNotify(
        t('dub.long_processing', 'Выполняется длительная обработка. Пожалуйста, подождите.'),
        'info',
        8000
      );
    }

    const step = d.status === 'done' ? 'done' : (d.step || 'preparing');
    renderProgressSteps(step, d.status);
    updateWizardPhases(step, d.status);
    updateTranslationSubsteps(d);
    updateConveyorTimingDebug(d);

    if (d.detected_lang) {
      updateDetectedLangDisplay(d.detected_lang, d.detected_lang_name);
    }

    if (d.segments_preview && d.segments_preview.length && typeof isDevMode === 'function' && isDevMode()) {
      showSegments(d.segments_preview);
    }
    if (d.developer_preview && typeof isDevMode === 'function' && isDevMode()) {
      renderDeveloperPreview(d.developer_preview);
    }
    state.devInspectorAvailable = !!d.dev_inspector_available;
    updateInspectorButtonVisibility();

    if (d.status === 'cancelled') {
      clearInterval(state.polling);
      state.polling = null;
      state.running = false;
      _dubBusy(false);
      state.pipelineCheckpoint = d.checkpoint || state.pipelineCheckpoint;
      state.cancelledTask = true;
      wizardGoToStep(WIZARD_STEP_IDS.length - 1);
      updateWizardNav();
      return;
    }

    if (d.status === 'stalled') {
      clearInterval(state.polling);
      state.polling = null;
      state.running = false;
      _dubBusy(false);
      document.getElementById('btn-start-dub').disabled = false;
      state.pipelineCheckpoint = d.checkpoint || state.pipelineCheckpoint;
      state.cancelledTask = !!d.checkpoint;
      const stall = d.stall_info || {};
      const pe = d.pipeline_error || {};
      const errMsg = pe.reason_short || pe.reason || stall.message
        || t('dub.stalled_generic', 'Обработка остановлена: этап завис. Проверьте диагностику и запустите снова.');
      finishError(errMsg, null, pe, dev ? stall : null);
      return;
    }

    if (d.status === 'studio_ready') {
      clearInterval(state.polling);
      state.polling = null;
      wizardShowScreen('progress');
      _autoMixAndFinish(state.taskId);
      return;
    }

    if (d.status === 'done') {
      clearInterval(state.polling);
      state.polling = null;
      finishSuccess(d.output_file, d.subtitle_file, d.studio_url);
      return;
    }

    if (d.status === 'error') {
      clearInterval(state.polling);
      state.polling = null;
      const pe = d.pipeline_error || null;
      const errMsg = pe
        ? (pe.reason_short || pe.reason)
        : (d.last_tts_error || (d.errors && d.errors[0]) || 'Unknown error');
      const diagBlock = dev
        ? (d.last_pipeline_diagnostic || d.last_tts_diagnostic)
        : null;
      const passiveOpenDdf = Object.assign(
        {},
        d.passive_openddf || {},
        {
          run_id: d.openddf_run_id || (d.passive_openddf && d.passive_openddf.run_id),
          diagnostic_zip: d.diagnostic_zip != null
            ? d.diagnostic_zip
            : (d.passive_openddf && d.passive_openddf.diagnostic_zip),
          diagnostic_zip_available: d.diagnostic_zip_available != null
            ? d.diagnostic_zip_available
            : (d.passive_openddf && d.passive_openddf.diagnostic_zip_available),
          diagnostic_zip_status: d.diagnostic_zip_status
            || (d.passive_openddf && d.passive_openddf.diagnostic_zip_status),
          diagnostic_zip_reason: d.diagnostic_zip_reason
            || (d.passive_openddf && d.passive_openddf.diagnostic_zip_reason),
          diagnostic_zip_reason_code: d.diagnostic_zip_reason_code
            || (d.passive_openddf && d.passive_openddf.diagnostic_zip_reason_code),
        }
      );
      finishError(
        errMsg,
        diagBlock,
        pe,
        dev ? d.pipeline_error_developer : null,
        d.openddf_artifacts || (passiveOpenDdf && passiveOpenDdf.artifacts),
        passiveOpenDdf,
        d.openddf_run_id
      );
      return;
    } else if (d.status === 'translation_review' || d.awaiting_translation_review) {
      translationReviewState.preTts = true;
      state.running = true;
      _dubBusy(true);
      if (typeof isDevMode === 'function' && isDevMode() && d.dev_diagnostics_available && !state.diagnosticsShown) {
        state.diagnosticsShown = true;
        openDeveloperDiagnostics();
      } else {
        const banner = document.getElementById('translation-review-banner');
        if (banner) banner.style.display = 'block';
      }
      const approveBtn = document.getElementById('btn-approve-translation');
      if (approveBtn) approveBtn.disabled = false;
      // Auto-open Review when TPS/manual hold — otherwise user waits and watchdog
      // used to kill the job as PIPELINE_STALLED after 2 min.
      if (!state.reviewOverlayAutoOpened) {
        state.reviewOverlayAutoOpened = true;
        try { openTranslationReview(); } catch (_) { /* overlay may not exist yet */ }
      }
      if (!state.reviewPauseNotified && !state.diagnosticsShown) {
        state.reviewPauseNotified = true;
        const manualN = Array.isArray(d.tps_manual_indices) ? d.tps_manual_indices.length : 0;
        vmNotify(
          manualN
            ? t('dub.review_manual_pause_notify', 'TPS: нужна ручная правка ({n} сегм.) — откройте проверку перевода.').replace('{n}', String(manualN))
            : t('dub.review_pause_notify', 'Перевод готов — проверьте текст перед озвучкой.'),
          'info',
          8000
        );
      }
    } else {
      translationReviewState.preTts = false;
      state.reviewPauseNotified = false;
      state.reviewOverlayAutoOpened = false;
      const banner = document.getElementById('translation-review-banner');
      if (banner) banner.style.display = 'none';
    }
  } catch (e) {
    state.statusFetchFailures = (state.statusFetchFailures || 0) + 1;
    // ~15s of consecutive failures (poll every 1s) before giving up — brief
    // stalls during Whisper/TTS must not abort a running dub.
    if (state.statusFetchFailures >= 15) {
      finishError(
        e.message || t('dub.server_unreachable', 'Сервер недоступен. Проверьте подключение и нажмите «Повторить».')
      );
    }
  } finally {
    state.statusCheckInFlight = false;
  }
}

function updateProgressLive(_d) {
  /* merged into buildProgressInfoLine / wizard-info-ticker */
}

function setReviewFontSize(px) {
  state.reviewFontSize = px;
  document.documentElement.style.setProperty('--tr-font-size', px + 'px');
}

function filterReviewSegments(q) {
  const query = (q || '').trim().toLowerCase();
  document.querySelectorAll('.tr-seg').forEach(el => {
    const ta = el.querySelector('textarea');
    const hay = ((ta && ta.value) || '') + (el.textContent || '');
    el.style.display = !query || hay.toLowerCase().includes(query) ? '' : 'none';
  });
}

function replaceInReview() {
  const from = (document.getElementById('tr-replace-from') || {}).value || '';
  const to = (document.getElementById('tr-replace-to') || {}).value || '';
  if (!from) return;
  document.querySelectorAll('.tr-edit textarea').forEach(ta => {
    if (ta.value.includes(from)) ta.value = ta.value.split(from).join(to);
  });
  vmNotify(t('dub.review_replaced', 'Замена выполнена в открытых полях'), 'success', 2500);
}

function showSegments(segments) {
  const panel = document.getElementById('segments-panel');
  const list = document.getElementById('segments-list');
  panel.style.display = 'block';
  const dev = typeof isDevMode === 'function' && isDevMode();
  list.innerHTML = segments.map((s, i) => {
    if (dev && (s.raw_mt || s.naturalized || s.engine)) {
      const health = s.pipeline_health || {};
      const healthTag = health.ok === false
        ? `<div class="tr-warn-tag">Pipeline Health: ${escHtml((health.issues || []).join('; ') || 'fail')}</div>`
        : '';
      const natReasons = (s.naturalizer_reasons || []).length
        ? `<div class="char-count">Naturalizer: ${escHtml(s.naturalizer_reasons.join(', '))}</div>` : '';
      const etScores = s.tournament_scores && Object.keys(s.tournament_scores).length
        ? `<div class="char-count">Tournament: ${escHtml(Object.entries(s.tournament_scores).map(([k,v]) => k + '=' + v).join(' · '))}</div>` : '';
      const fusionLine = s.fusion_reason
        ? `<div class="char-count">Fusion: ${escHtml(s.fusion_reason)}</div>` : '';
      return `<div class="segment-item seg-dev">
      <div class="seg-num">#${i + 1}</div>
      ${healthTag}
      <div class="seg-original"><span class="char-count">Original:</span> ${escHtml(s.original || s.whisper || '')}</div>
      <div class="seg-translated"><span class="char-count">Raw:</span> ${escHtml(s.raw_mt || '—')}</div>
      ${s.alternative ? `<div class="seg-translated"><span class="char-count">Alt:</span> ${escHtml(s.alternative)}</div>` : ''}
      <div class="seg-translated"><span class="char-count">Natural:</span> ${escHtml(s.naturalized || '—')}</div>
      <div class="seg-translated"><span class="char-count">TTS:</span> ${escHtml(s.translated || s.final || s.tts || '')}</div>
      <div class="char-count">${escHtml(s.engine || '')} · ${escHtml(s.route || '')} · Q=${s.quality_score ?? '—'}${s.mt_ms ? ' · ' + Math.round(s.mt_ms) + 'ms' : ''}${s.enterprise ? ' · Enterprise' : ''}</div>
      ${s.router_reason ? `<div class="char-count">${escHtml(s.router_reason)}</div>` : ''}
      ${etScores}
      ${fusionLine}
      ${natReasons}
    </div>`;
    }
    return `<div class="segment-item">
      <div class="seg-num">#${i + 1}</div>
      <div class="seg-original">${escHtml(s.original || '')}</div>
      <div class="seg-translated">${escHtml(s.translated || '')}</div>
    </div>`;
  }).join('');
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

const translationReviewState = { segments: [], loaded: false, preTts: false };
const devDiagnosticsState = { loaded: false, data: null };
const devInspectorState = { loaded: false, data: null, currentIndex: 0 };

function devInspectorAllowed() {
  return (typeof isDevMode === 'function' && isDevMode()) || !!state.devInspectorAvailable;
}

function updateInspectorButtonVisibility() {
  const btn = document.getElementById('btn-dev-inspector');
  if (btn) btn.style.display = devInspectorAllowed() ? 'inline-block' : 'none';
}

function renderDiff(chunks) {
  if (!chunks || !chunks.length) return '';
  return chunks.map(c => {
    const cls = c.tag === 'insert' || c.tag === 'replace' ? 'ins' : (c.tag === 'delete' ? 'del' : '');
    return cls ? `<span class="${cls}">${escHtml(c.text)}</span>` : escHtml(c.text + ' ');
  }).join(' ');
}

function toggleTdStage(id) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('open');
}

function toggleTdSegment(idx) {
  const el = document.getElementById('td-seg-' + idx);
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

async function openDeveloperDiagnostics() {
  if (typeof isDevMode !== 'function' || !isDevMode()) return;
  if (!state.taskId) return;
  const overlay = document.getElementById('dev-diagnostics-overlay');
  const body = document.getElementById('dev-diagnostics-body');
  const summary = document.getElementById('td-summary');
  if (!overlay || !body) return;
  overlay.style.display = 'flex';
  body.innerHTML = '<div class="char-count">Loading diagnostics…</div>';
  if (summary) summary.innerHTML = '';
  try {
    const r = await fetch('/api/auto_dub/translation_diagnostics/' + encodeURIComponent(state.taskId));
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'load failed');
    devDiagnosticsState.data = d.diagnostics;
    devDiagnosticsState.loaded = true;
    renderDeveloperDiagnostics(d.diagnostics);
  } catch (e) {
    body.innerHTML = '<p class="tr-warn-tag">' + escHtml(vmFriendlyError(e.message)) + '</p>';
  }
}

function closeDeveloperDiagnostics(openReview) {
  const overlay = document.getElementById('dev-diagnostics-overlay');
  if (overlay) overlay.style.display = 'none';
  if (openReview) {
    const banner = document.getElementById('translation-review-banner');
    if (banner) banner.style.display = 'block';
    if (!state.reviewPauseNotified) {
      state.reviewPauseNotified = true;
      vmNotify(t('dub.review_pause_notify', 'Перевод готов — проверьте текст перед озвучкой.'), 'info', 6000);
    }
  }
}

function renderDeveloperDiagnostics(diag) {
  const body = document.getElementById('dev-diagnostics-body');
  const summaryEl = document.getElementById('td-summary');
  if (!body || !diag) return;

  const sum = diag.summary || {};
  let summaryHtml = '<div><strong>Pipeline Status</strong></div><div class="td-summary-row">';
  (sum.stages || []).forEach(row => {
    const cls = row.ok ? 'ok' : 'fail';
    summaryHtml += `<span class="td-summary-item ${cls}">${row.icon || (row.ok ? '✔' : '❌')} ${escHtml(row.label || '')}</span>`;
  });
  summaryHtml += '</div>';
  if (sum.stopped) {
    summaryHtml = `<div class="td-summary-stopped">Pipeline stopped<br>Stage: ${escHtml(sum.stop_stage || '')}<br>Reason: ${escHtml(sum.stop_reason || '')}<br>Probable: ${escHtml(sum.probable_cause || '')}</div>` + summaryHtml;
  }
  if (summaryEl) summaryEl.innerHTML = summaryHtml;

  const segs = diag.segments || [];
  body.innerHTML = segs.map(seg => {
    const segFail = (seg.stages || []).some(s => !s.ok);
    const stagesHtml = (seg.stages || []).map((st, si) => {
      const sid = `td-st-${seg.index}-${si}`;
      const icon = st.ok ? '✔' : '❌';
      const failCls = st.ok ? '' : ' fail';
      const err = st.error || {};
      let detail = '';
      if (st.changed || !st.ok) {
        detail += `<div class="td-kv"><label>Input</label>${escHtml(st.input || '')}</div>`;
        detail += `<div class="td-kv"><label>Output</label>${escHtml(st.output || '')}</div>`;
        if (st.diff && st.diff.length) {
          detail += `<div class="td-diff"><label>Diff</label>${renderDiff(st.diff)}</div>`;
        }
      }
      if (st.ms) detail += `<div class="td-kv"><label>Time</label>${st.ms} ms</div>`;
      if (st.engine) detail += `<div class="td-kv"><label>Engine</label>${escHtml(st.engine)}</div>`;
      if (st.score != null) detail += `<div class="td-kv"><label>Score</label>${st.score}</div>`;
      if (st.reason) detail += `<div class="td-kv"><label>Reason</label>${escHtml(st.reason)}</div>`;
      if (!st.ok && err.reason) {
        detail += `<div class="td-kv"><label>Error</label>${escHtml(err.reason)}</div>`;
        if (err.probable_cause) detail += `<div class="td-kv"><label>Probable cause</label>${escHtml(err.probable_cause)}</div>`;
      }
      return `<div class="td-stage${failCls}">
        <div class="td-stage-head" onclick="toggleTdStage('${sid}')"><span class="td-icon">${icon}</span><span>${escHtml(st.name || st.id)}</span></div>
        <div class="td-stage-body" id="${sid}">${detail}</div>
      </div>`;
    }).join('');

    const tourn = seg.tournament || {};
    let tournHtml = '';
    if (tourn.scores && Object.keys(tourn.scores).length) {
      tournHtml = '<div class="td-tournament"><h4>Tournament</h4>';
      Object.entries(tourn.scores).forEach(([eng, sc]) => {
        const win = eng === tourn.winner ? ' winner' : '';
        tournHtml += `<div class="td-score-row${win}"><span>${escHtml(eng)}</span><span>${sc}${win ? ' ← Winner' : ''}</span></div>`;
      });
      if (tourn.fusion_reason) tournHtml += `<div class="td-kv"><label>Fusion</label>${escHtml(tourn.fusion_reason)}</div>`;
      tournHtml += '</div>';
    }

    return `<div class="td-segment">
      <div class="td-seg-head${segFail ? ' fail' : ''}" onclick="toggleTdSegment(${seg.index})">
        <span>Segment #${seg.index}</span><span>${escHtml((seg.final || '').slice(0, 60))}</span>
      </div>
      <div class="td-stages" id="td-seg-${seg.index}">${stagesHtml}${tournHtml}</div>
    </div>`;
  }).join('');
}

async function exportDeveloperDiagnostics() {
  if (!state.taskId) return;
  try {
    const r = await fetch('/api/auto_dub/translation_diagnostics/' + encodeURIComponent(state.taskId) + '/export');
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'export failed');
    const blob = new Blob([d.text || ''], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = d.filename || 'translation_diagnostics.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    vmNotify(vmFriendlyError(e.message), 'error');
  }
}

function _tiStageClass(st, transitions, stageIdx) {
  if (!st.ok || (st.issues && st.issues.length)) {
    const hasErr = (st.issues || []).some(i => /placeholder|encoding|missing_entity|integrity/i.test(i));
    if (hasErr || !st.ok) return 'ti-error';
    return 'ti-warn';
  }
  const stName = st.name || st.id;
  const badTr = (transitions || []).find(tr => !tr.ok && tr.regression && tr.to === stName);
  if (badTr) return 'ti-error';
  return 'ti-ok';
}

function _inspectorSegmentText(seg) {
  const lines = [];
  lines.push('='.repeat(30));
  lines.push('SEGMENT #' + seg.index);
  lines.push('');
  (seg.stages || []).forEach(st => {
    lines.push(st.name || st.id || '');
    if (st.id === 'translation_request') {
      const m = st.meta || {};
      lines.push('Engine: ' + (m.engine || ''));
      lines.push('Route: ' + (m.route || ''));
      lines.push('Model: ' + (m.model || ''));
    } else if (st.id === 'entity_detection') {
      (st.entities || (st.text || '').split('\n')).forEach(e => {
        if (String(e).trim()) lines.push('  ' + String(e).trim());
      });
    } else if (st.text) {
      lines.push(st.text);
    }
    if (st.ms) lines.push('Time: ' + st.ms + ' ms');
    if (st.issues && st.issues.length) lines.push('[' + st.issues.join(', ') + ']');
    lines.push('');
  });
  const q = seg.quality || {};
  lines.push('Quality: ' + JSON.stringify(q));
  return lines.join('\n');
}

async function openTranslationInspector() {
  if (!devInspectorAllowed()) return;
  if (!state.taskId) return;
  const overlay = document.getElementById('dev-inspector-overlay');
  const body = document.getElementById('dev-inspector-body');
  if (!overlay || !body) return;
  overlay.style.display = 'flex';
  body.innerHTML = '<div class="char-count">Loading inspector…</div>';
  try {
    const r = await fetch('/api/auto_dub/translation_inspector/' + encodeURIComponent(state.taskId));
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'load failed');
    devInspectorState.data = d.inspector;
    devInspectorState.loaded = true;
    devInspectorState.currentIndex = 0;
    renderTranslationInspector(d.inspector);
  } catch (e) {
    body.innerHTML = '<p class="tr-warn-tag">' + escHtml(vmFriendlyError(e.message)) + '</p>';
  }
}

function closeTranslationInspector() {
  const overlay = document.getElementById('dev-inspector-overlay');
  if (overlay) overlay.style.display = 'none';
}

function selectInspectorSegment(idx) {
  devInspectorState.currentIndex = parseInt(idx, 10) || 0;
  if (devInspectorState.data) renderTranslationInspector(devInspectorState.data);
}

function renderTranslationInspector(report) {
  const body = document.getElementById('dev-inspector-body');
  const summaryEl = document.getElementById('ti-summary');
  const sel = document.getElementById('ti-segment-select');
  if (!body || !report) return;
  const segs = report.segments || [];
  if (sel) {
    sel.innerHTML = segs.map((s, i) =>
      `<option value="${i}"${i === devInspectorState.currentIndex ? ' selected' : ''}>Segment #${s.index}</option>`
    ).join('');
  }
  const failed = report.failed_transitions || [];
  if (summaryEl) {
    summaryEl.innerHTML = failed.length
      ? `<div class="ti-summary-err">${failed.length} failed transition(s) — check red stages</div>`
      : `<div class="ti-summary-ok">All integrity checks passed</div>`;
  }
  const seg = segs[devInspectorState.currentIndex];
  if (!seg) {
    body.innerHTML = '<p class="char-count">No segments</p>';
    return;
  }
  const transitions = seg.transitions || [];
  let html = `<div class="ti-seg-title">SEGMENT #${seg.index}</div>`;
  html += `<div class="ti-kv"><label>Original</label><div class="ti-text">${escHtml(seg.original || '')}</div></div>`;
  (seg.stages || []).forEach((st, si) => {
    const cls = _tiStageClass(st, transitions, si);
    html += `<div class="ti-stage ${cls}">`;
    html += `<div class="ti-stage-head">${escHtml(st.name || st.id || '')}`;
    if (st.ms) html += ` <span class="ti-ms">${st.ms} ms</span>`;
    html += '</div>';
    if (st.id === 'translation_request') {
      const m = st.meta || {};
      html += `<div class="ti-meta"><div><b>Engine:</b> ${escHtml(m.engine || '—')}</div>`;
      html += `<div><b>Route:</b> ${escHtml(m.route || '—')}</div>`;
      html += `<div><b>Model:</b> ${escHtml(m.model || '—')}</div></div>`;
    } else if (st.id === 'entity_detection') {
      const ents = st.entities || (st.text || '').split('\n').filter(Boolean);
      html += '<ul class="ti-entities">' + ents.map(e => `<li>${escHtml(String(e))}</li>`).join('') + '</ul>';
    } else if (st.text) {
      html += `<div class="ti-text">${escHtml(st.text)}</div>`;
    }
    const integ = st.integrity || {};
    if (integ.char_count != null) {
      html += `<div class="ti-integ">chars: ${integ.char_count} · placeholders: ${integ.placeholder_count || 0} · entities: ${integ.entity_count || 0}</div>`;
    }
    if (st.issues && st.issues.length) {
      html += `<div class="ti-issues">${st.issues.map(i => escHtml(i)).join(' · ')}</div>`;
    }
    if (si < (seg.stages || []).length - 1) {
      const next = seg.stages[si + 1];
      const tr = transitions.find(t => t.from === (st.name || st.id) && t.to === (next.name || next.id));
      if (tr && !tr.ok) {
        html += `<div class="ti-transition ti-error">↓ ERROR: ${escHtml((tr.issues || []).join(', '))}</div>`;
      } else {
        html += '<div class="ti-transition ti-ok">↓</div>';
      }
    }
    html += '</div>';
  });
  const q = seg.quality || {};
  html += '<div class="ti-quality"><strong>Quality</strong>';
  Object.entries(q).forEach(([k, v]) => { html += `<div>${escHtml(k)}: ${escHtml(String(v))}</div>`; });
  html += '</div>';
  const timing = seg.timing_ms || {};
  if (Object.keys(timing).length) {
    html += '<div class="ti-timing"><strong>Timing</strong>';
    Object.entries(timing).forEach(([k, v]) => { html += `<div>${escHtml(k)}: ${v} ms</div>`; });
    html += '</div>';
  }
  const warns = seg.warnings || [];
  if (warns.length) {
    html += '<div class="ti-warns"><strong>Warnings</strong><ul>';
    warns.forEach(w => {
      const label = typeof w === 'object' ? (w.code || JSON.stringify(w)) : w;
      html += `<li>${escHtml(String(label))}</li>`;
    });
    html += '</ul></div>';
  }
  body.innerHTML = html;
}

function copyInspectorSegment() {
  const segs = (devInspectorState.data && devInspectorState.data.segments) || [];
  const seg = segs[devInspectorState.currentIndex];
  if (!seg) return;
  const text = _inspectorSegmentText(seg);
  navigator.clipboard.writeText(text).then(() => vmNotify('Segment copied', 'success', 2000)).catch(() => {});
}

async function copyInspectorFullReport() {
  if (!state.taskId) return;
  try {
    const r = await fetch('/api/auto_dub/translation_inspector/' + encodeURIComponent(state.taskId) + '/export');
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'export failed');
    await navigator.clipboard.writeText(d.text || '');
    vmNotify('Full report copied', 'success', 2000);
  } catch (e) {
    vmNotify(vmFriendlyError(e.message), 'error');
  }
}

async function exportTranslationInspector() {
  if (!state.taskId) return;
  try {
    const r = await fetch('/api/auto_dub/translation_inspector/' + encodeURIComponent(state.taskId) + '/export');
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'export failed');
    const blob = new Blob([d.text || ''], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = d.filename || 'translation_inspector.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    vmNotify(vmFriendlyError(e.message), 'error');
  }
}

async function saveTranslationInspectorJson() {
  if (!state.taskId) return;
  try {
    const r = await fetch('/api/auto_dub/translation_inspector/' + encodeURIComponent(state.taskId) + '/json');
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'json failed');
    const blob = new Blob([d.json || ''], { type: 'application/json;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = d.filename || 'translation_inspector.json';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    vmNotify(vmFriendlyError(e.message), 'error');
  }
}

async function openTranslationReview() {
  if (!state.taskId) {
    vmNotify(t('dub.review_no_task', 'Нет данных задачи'), 'warning');
    return;
  }
  const overlay = document.getElementById('translation-review-overlay');
  const body = document.getElementById('translation-review-body');
  const approveBtn = document.getElementById('tr-approve-btn');
  if (approveBtn) approveBtn.style.display = translationReviewState.preTts ? 'inline-block' : 'none';
  overlay.style.display = 'flex';
  body.innerHTML = '<div class="char-count">' + t('dub.review_loading', 'Загрузка…') + '</div>';
  try {
    const r = await fetch('/api/auto_dub/translation_review/' + encodeURIComponent(state.taskId));
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'load failed');
    translationReviewState.segments = d.segments || [];
    translationReviewState.loaded = true;
    translationReviewState.preTts = translationReviewState.preTts || d.status === 'translation_review' || d.awaiting_translation_review;
    if (approveBtn) approveBtn.style.display = translationReviewState.preTts ? 'inline-block' : 'none';
    updateInspectorButtonVisibility();
    renderTranslationReview(d);
  } catch (e) {
    body.innerHTML = '<p class="tr-warn-tag">' + escHtml(vmFriendlyError(e.message)) + '</p>';
  }
}

function closeTranslationReview() {
  const overlay = document.getElementById('translation-review-overlay');
  if (overlay) overlay.style.display = 'none';
}

function formatReviewWarning(w) {
  if (typeof w === 'string') return w;
  const code = w.code || '';
  if (code === 'proper_noun' || code === 'preserved_token') {
    const names = (w.names || w.tokens || []).join(', ');
    return t('dub.review_warn_proper_noun', 'Имя не сохранено: {names}').replace('{names}', names);
  }
  if (code === 'raw_empty') return t('dub.review_warn_raw_empty', 'Raw MT пустой');
  if (code === 'raw_equals_whisper') return t('dub.review_warn_raw_untranslated', 'Raw MT совпадает с оригиналом');
  if (code === 'empty_translation') return t('dub.review_warn_empty', 'Пустой перевод');
  if (code === 'possibly_untranslated') return t('dub.review_warn_untranslated', 'Возможно не переведено');
  if (code === 'nonsense') return t('dub.review_warn_nonsense', 'Подозрение на бессмыслицу');
  if (code === 'literal_construction') return t('dub.review_warn_literal', 'Возможная калька / дословный перевод');
  if (code === 'idiom') return t('dub.review_warn_idiom', 'Идиома переведена дословно');
  return code;
}

function _trFillBar(pct) {
  const p = Math.max(0, Math.min(160, Number(pct) || 0));
  const filled = Math.round((Math.min(p, 120) / 120) * 20);
  const bar = '█'.repeat(filled) + '░'.repeat(Math.max(0, 20 - filled));
  return bar + ' ' + (Math.round(p * 10) / 10) + '%';
}

function _trSpeechLines(speech) {
  const se = speech || {};
  const orig = Math.max(1, Number(se.original_duration_ms) || 1);
  const dub = Math.max(0, Number(se.dub_duration_ms) || 0);
  const max = Math.max(orig, dub, 1);
  const oLen = Math.max(1, Math.round((orig / max) * 24));
  const dLen = Math.max(1, Math.round((dub / max) * 24));
  return `
    <div class="tr-speech-end">
      <div class="tr-speech-row"><span>Оригинал</span><code>|${'='.repeat(oLen)}|</code></div>
      <div class="tr-speech-row"><span>TTS</span><code>|${'='.repeat(dLen)}|</code></div>
    </div>`;
}

function _trStatusEmoji(status) {
  if (status === 'green') return '🟢';
  if (status === 'yellow') return '🟡';
  if (status === 'orange') return '🟠';
  if (status === 'red') return '🔴';
  return '⚪';
}

function _trOverflowHtml(seg, editText) {
  const fits = seg.text_fits != null ? seg.text_fits : editText;
  const overflow = seg.text_overflow || '';
  if (!overflow) {
    return `<div class="tr-overflow-text"><span class="tr-text-fits">${escHtml(fits || editText || '')}</span></div>`;
  }
  return `<div class="tr-overflow-text"><span class="tr-text-fits">${escHtml(fits)}</span><span class="tr-text-overflow">${escHtml(overflow)}</span></div>`;
}

function _trQualityBlock(qb) {
  const q = qb || {};
  const rows = [
    ['Translation', q.translation],
    ['Naturalness', q.naturalness],
    ['Entities', q.entities],
    ['Timing', q.timing],
    ['TTS', q.tts],
    ['Overall', q.overall],
  ];
  return `<div class="tr-quality-grid">${rows.map(([k, v]) =>
    `<div class="tr-q-cell"><span class="tr-q-k">${k}</span><span class="tr-q-v">${v != null ? escHtml(String(v)) : '—'}</span></div>`
  ).join('')}</div>`;
}

function renderTranslationReview(data) {
  const body = document.getElementById('translation-review-body');
  const segs = data.segments || [];
  const dev = typeof isDevMode === 'function' && isDevMode();
  bindReviewOriginalMixControls();
  const savedVol = loadSavedOriginalVolumePct();
  setReviewOriginalVolumePct(
    savedVol != null ? savedVol : getReviewOriginalVolumePct(),
    { persist: false }
  );
  if (!segs.length) {
    body.innerHTML = '<p class="char-count">' + t('dub.review_empty', 'Нет сегментов для проверки') + '</p>';
    return;
  }
  const langLine = [data.source_lang, data.target_lang].filter(Boolean).join(' → ');
  const warnTotal = data.warning_count != null ? data.warning_count : segs.reduce((n, s) => n + (s.warnings || []).length, 0);
  const qaNote = data.qa_note || (data.qa_mode === 'advisory'
    ? 'Контроль перевода показывает рекомендации и не изменяет текст автоматически.'
    : '');
  const st = data.llm_status || {};
  let llmHtml = '';
  if (st.degraded) {
    const parts = [];
    parts.push('<strong>⚠ ' + t('dub.review_ai_degraded', 'AI-адаптация ограничена') + '</strong>');
    if (st.model) {
      const avg = st.avg_call_ms ? ' · ' + (st.avg_call_ms / 1000).toFixed(1) + 's/вызов' : '';
      parts.push('<div class="char-count">' + escHtml(t('dub.review_model', 'Модель') + ': ' + st.model + avg) + '</div>');
    }
    if (st.segments_without_adaptation) {
      parts.push('<div class="char-count">' + t('dub.review_no_adapt', 'Сегментов без AI-адаптации: {n}').replace('{n}', String(st.segments_without_adaptation)) + '</div>');
    }
    if (st.entity_risk_count) {
      parts.push('<div class="char-count">' + t('dub.review_entity_risk', 'Сегментов с риском потери имён: {n}').replace('{n}', String(st.entity_risk_count)) + '</div>');
    }
    if (st.recommendation) {
      parts.push('<div class="char-count" style="margin-top:4px;">💡 ' + escHtml(st.recommendation) + '</div>');
    }
    llmHtml = '<div class="tr-warn-tag" style="display:block;padding:8px 10px;line-height:1.5;">' + parts.join('') + '</div>';
  }
  const summaryHtml = [
    llmHtml,
    qaNote ? '<p class="char-count tr-qa-note">' + escHtml(qaNote) + '</p>' : '',
    warnTotal && !dev
      ? '<p class="tr-warn-tag">' + t('dub.review_warnings_count', 'Замечаний: {n}').replace('{n}', String(warnTotal)) + '</p>'
      : '',
  ].filter(Boolean).join('');
  const preTts = translationReviewState.preTts;
  const saveLabel = preTts
    ? t('dub.review_save_text', '💾 Сохранить')
    : t('dub.review_save', '💾 Сохранить и озвучить');
  const manualCount = segs.filter(s => s.needs_manual_review || s.manual_review_required).length;
  const sortedSegs = [...segs].sort((a, b) => {
    const am = (a.needs_manual_review || a.manual_review_required || a.fill_status === 'red') ? 0 : 1;
    const bm = (b.needs_manual_review || b.manual_review_required || b.fill_status === 'red') ? 0 : 1;
    if (am !== bm) return am - bm;
    return (a.index || 0) - (b.index || 0);
  });
  const manualBanner = manualCount
    ? `<p class="tr-warn-tag tr-manual-banner">${t('dub.review_manual_needed', 'Требуется ручная правка: {n} сегм.').replace('{n}', String(manualCount))} · ${t('dub.review_manual_hint', 'Одно поле = approved_text (Review = TTS)')}</p>`
    : '';
  body.innerHTML = (langLine ? '<p class="char-count">' + escHtml(langLine) + ' · ' + segs.length + '</p>' : '') +
    summaryHtml +
    manualBanner +
    sortedSegs.map(seg => {
      const warnLabels = (seg.warnings || []).map(formatReviewWarning);
      const editText = seg.approved_text || seg.final_text || seg.tts_text || '';
      const fillStatus = String(seg.fill_status || seg.dsal_band || 'green').toLowerCase();
      const fillPct = Number(seg.fill_pct != null ? seg.fill_pct : 0);
      const slotSec = (Number(seg.slot_ms || 0) / 1000).toFixed(1);
      const ttsSec = (Number(seg.tts_ms != null ? seg.tts_ms : seg.playback_duration_ms || 0) / 1000).toFixed(1);
      const overflowSec = (Number(seg.overflow_ms || 0) / 1000).toFixed(1);
      const statusLabel = seg.status_label || (
        fillStatus === 'green' ? t('dub.review_status_green', 'Отлично') :
        fillStatus === 'yellow' ? t('dub.review_status_yellow', 'Почти предел') :
        fillStatus === 'orange' ? t('dub.review_status_orange', 'Возможна проблема') :
        t('dub.review_status_red', 'Требует исправления')
      );
      const algos = (seg.algorithms || []).map(a =>
        `<span class="tr-algo-chip">${escHtml(a)}</span>`
      ).join('');
      const meaningWarn = seg.meaning_loss_risk
        ? `<div class="tr-warn-tag">⚠ ${t('dub.review_meaning_loss', 'Possible Meaning Loss')}</div>`
        : '';
      const entityWarn = seg.entity_risk
        ? `<div class="tr-warn-tag">⚠ ${t('dub.review_entity_removed', 'Important Entity Removed')}</div>`
        : '';
      const voiceLine = seg.voice_truncated
        ? `<div class="tr-voice-flag tr-voice-bad">Voice truncated: YES</div>`
        : `<div class="tr-voice-flag tr-voice-ok">Voice finished naturally: ${seg.voice_finished_naturally === false ? 'NO' : 'YES'}</div>`;
      const warnHtml = warnLabels.length ? '<div class="tr-warn-tag">⚠ ' + escHtml(warnLabels.join('; ')) + '</div>' : '';
      const ttsHint = (!translationReviewState.preTts && seg.text_for_tts && seg.text_for_tts !== editText)
        ? `<div class="char-count">${t('dub.review_tts_text', 'Озвучено')}: ${escHtml(seg.text_for_tts)}</div>`
        : '';
      const bandClass = 'tr-fill-' + fillStatus;
      const studioTag = seg.needs_studio
        ? `<span class="tr-warn-tag">Studio</span>`
        : '';
      const manualTag = (seg.needs_manual_review || seg.manual_review_required)
        ? `<span class="tr-manual-tag">${t('dub.review_manual_tag', 'Manual Review Required')}</span>`
        : '';
      const tpsTag = (seg.tqe_status || seg.tps_path)
        ? `<span class="char-count">TPS ${escHtml(seg.tps_path || '')} · ${escHtml(seg.tqe_status || '')}</span>`
        : '';
      const expTtsSec = (Number(seg.expected_tts_ms || 0) / 1000).toFixed(1);
      const wordCount = Number(seg.word_count || 0);
      const segAdvice = String(seg.seg_advice || '');
      const segStatus = String(seg.seg_status || statusLabel);
      const adviceHtml = segAdvice
        ? `<div class="tr-warn-tag tr-seg-advice">${escHtml(segAdvice)}${seg.seg_status ? ' · ' + escHtml(seg.seg_status) : ''}</div>`
        : '';
      const budgetSec = (Number(seg.slot_budget_ms || seg.slot_ms || 0) / 1000).toFixed(1);
      const marginSec = (Number(seg.safety_margin_ms || 0) / 1000).toFixed(1);
      const estSpeechSec = (Number(seg.estimated_speech_ms || seg.expected_tts_ms || 0) / 1000).toFixed(1);
      const origChars = Number(seg.original_char_len || (seg.original || '').length || 0);
      const trChars = Number(seg.translation_char_len || (editText || '').length || 0);
      const timingBadge = `
        <div class="tr-timing-badge ${bandClass}">
          <span>Original ${slotSec} s</span>
          <span>Slot Budget ${budgetSec} s</span>
          <span>Expected TTS ${expTtsSec > 0 ? expTtsSec : ttsSec} s</span>
          <span>Est. Speech ${estSpeechSec} s</span>
          <span>TTS ${ttsSec} s</span>
          <span>Overflow ${Number(overflowSec) > 0 ? '+' : ''}${overflowSec} s</span>
          <span>Safety Margin ${marginSec} s</span>
          <span>Words ${wordCount || '—'}</span>
          <span>Chars ${origChars}→${trChars}</span>
          <span>Fill ${fillPct}%</span>
        </div>
        <div class="tr-fill-bar ${bandClass}">${_trFillBar(fillPct)}</div>
        ${adviceHtml}
        <div class="char-count">Status: ${escHtml(segStatus)}${seg.sync_status ? ' · ' + escHtml(seg.sync_status) : ''}</div>`;
      const dsalLine = (seg.slot_ms || seg.dsal_band || seg.dsal_applied)
        ? `<div class="tr-dev-only tr-field"><label>DSAL detail</label><div class="tr-val">applied=${seg.dsal_applied?'yes':'no'} · clause=${escHtml(String(seg.clause_coverage||0))} · expand=${seg.expand_required?'yes':'no'} · locked=${seg.translation_locked?'yes':'no'} · Semantic Adapt=${seg.semantic_adapted?'yes':'no'}</div></div>`
        : '';
      const stageFields = `
          <div class="tr-field"><label>${t('dub.review_original', 'Оригинал')}</label><div class="tr-val">${escHtml(seg.original || '—')}</div></div>
          <div class="tr-field"><label>Raw MT</label><div class="tr-val">${escHtml(seg.raw_translation || '—')}</div></div>
          <div class="tr-field"><label>Naturalized</label><div class="tr-val">${escHtml(seg.naturalized_text || '—')}</div></div>
          <div class="tr-field"><label>Final</label><div class="tr-val">${_trOverflowHtml(seg, editText)}</div></div>
          <div class="tr-field"><label>Text for TTS</label><div class="tr-val">${escHtml(seg.text_for_tts || editText || '—')}</div></div>
          ${dev ? `<div class="tr-dev-only tr-field"><label>Engine / Route</label><div class="tr-val">${escHtml(seg.engine || '')} · ${escHtml(seg.route_label || seg.route || '')}</div></div>${dsalLine}` : ''}
        `;
      return `
        <div class="tr-seg ${warnLabels.length || seg.needs_studio || seg.voice_truncated ? 'tr-seg-warn' : ''} ${(seg.needs_manual_review || seg.manual_review_required) ? 'tr-seg-manual' : ''} ${bandClass}" data-idx="${seg.index}">
          <div class="tr-seg-head">
            <strong>#${seg.index}</strong>
            <span class="tr-status-pill ${_trStatusEmoji(fillStatus) ? 'tr-status-' + fillStatus : ''}">${_trStatusEmoji(fillStatus)} ${escHtml(statusLabel)}</span>
            ${manualTag} ${studioTag} ${tpsTag}
          </div>
          ${warnHtml}
          ${meaningWarn}
          ${entityWarn}
          ${timingBadge}
          ${_trSpeechLines(seg.speech_end)}
          ${voiceLine}
          ${algos ? `<div class="tr-algo-row">${algos}</div>` : ''}
          ${_trQualityBlock(seg.quality_breakdown)}
          ${stageFields}
          <div class="tr-edit">
            <div class="tr-final-label">${t('dub.review_final', 'Финальный текст')}</div>
            <textarea id="tr-edit-${seg.index}" rows="3" class="${(seg.needs_manual_review || seg.manual_review_required) ? 'tr-edit-manual' : ''}" oninput="onReviewTextInput(${seg.index})">${escHtml(editText)}</textarea>
            <div id="tr-live-overflow-${seg.index}" class="tr-overflow-text tr-live-overflow">${_trOverflowHtml(seg, editText)}</div>
            ${ttsHint}
            <button type="button" class="btn btn-secondary btn-sm" style="margin-top:8px;" onclick="saveTranslationSegment(${seg.index})">${saveLabel}</button>
          </div>
        </div>`;
    }).join('');
  setReviewFontSize(state.reviewFontSize || 16);
}

function onReviewTextInput(index) {
  const ta = document.getElementById('tr-edit-' + index);
  const live = document.getElementById('tr-live-overflow-' + index);
  if (!ta || !live) return;
  const seg = (translationReviewState.segments || []).find(s => Number(s.index) === Number(index));
  if (!seg) {
    live.innerHTML = `<span class="tr-text-fits">${escHtml(ta.value)}</span>`;
    return;
  }
  const text = ta.value;
  const slot = Number(seg.slot_ms || 0);
  const baseTts = Number(seg.tts_ms || seg.playback_duration_ms || 0);
  const baseLen = Math.max(1, String(seg.final_text || seg.text_for_tts || text).length);
  const estTts = baseTts > 0 ? Math.round(baseTts * (text.length / baseLen)) : Math.round(text.length / 13.5 * 1000);
  let fits = text;
  let overflow = '';
  if (slot > 0 && estTts > slot && text.length > 1) {
    const ratio = slot / estTts;
    let cut = Math.max(1, Math.min(text.length - 1, Math.round(text.length * ratio)));
    if (text[cut] && /[\w\u0400-\u04FF]/.test(text[cut])) {
      const sp = text.lastIndexOf(' ', cut);
      if (sp > cut * 0.5) cut = sp + 1;
    }
    fits = text.slice(0, cut);
    overflow = text.slice(cut);
  }
  live.innerHTML = overflow
    ? `<span class="tr-text-fits">${escHtml(fits)}</span><span class="tr-text-overflow">${escHtml(overflow)}</span>`
    : `<span class="tr-text-fits">${escHtml(fits)}</span>`;
  // Update fill bar in-place
  const row = ta.closest('.tr-seg');
  if (row) {
    const fillPct = slot > 0 ? (estTts / slot) * 100 : 0;
    const bar = row.querySelector('.tr-fill-bar');
    if (bar) bar.textContent = _trFillBar(fillPct);
  }
}
window.onReviewTextInput = onReviewTextInput;

async function saveTranslationSegment(index) {
  const ta = document.getElementById('tr-edit-' + index);
  if (!ta || !state.taskId) return;
  const newText = ta.value.trim();
  if (!newText) {
    vmNotify(t('dub.review_empty_text', 'Текст не может быть пустым'), 'warning');
    return;
  }

  if (translationReviewState.preTts) {
    try {
      const r = await fetch('/api/auto_dub/translation_review/' + encodeURIComponent(state.taskId) + '/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          segment_index: index,
          new_text: newText,
        }),
      });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.error || 'save failed');
      vmNotify(t('dub.review_text_saved', 'Текст сохранён'), 'success');
      await openTranslationReview();
    } catch (e) {
      vmNotify(vmFriendlyError(e.message), 'error');
    }
    return;
  }

  const voice = document.getElementById('voice-select').value;
  try {
    const r = await fetch('/api/auto_dub/regen_segment', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task_id: state.taskId,
        segment_index: index,
        new_text: newText,
        voice,
      }),
    });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'save failed');
    vmNotify(t('dub.review_saved', 'Сегмент обновлён'), 'success');
    if (d.output_file) {
      const dl = document.getElementById('download-link');
      if (dl) {
        dl.href = '/output/' + encodeURIComponent(d.output_file);
        dl.style.display = 'inline-block';
      }
    }
    if (d.warning === 'segment_saved_remux_failed') {
      vmNotify(t('dub.review_remux_failed', 'Аудио обновлено, но MP4 не пересобран'), 'warning', 5000);
    }
    await openTranslationReview();
  } catch (e) {
    vmNotify(vmFriendlyError(e.message), 'error');
  }
}

async function copyTranslationReview() {
  if (!state.taskId) return;
  try {
    const r = await fetch('/api/auto_dub/translation_review/' + encodeURIComponent(state.taskId) + '/export');
    const d = await r.json();
    if (!d.text) throw new Error('empty');
    await navigator.clipboard.writeText(d.text);
    vmNotify(t('dub.review_copied', 'Скопировано'), 'success', 2500);
  } catch (_) {
    vmNotify(t('dub.review_copy_fail', 'Не удалось скопировать'), 'error');
  }
}

async function exportTranslationReview() {
  if (!state.taskId) return;
  try {
    const r = await fetch('/api/auto_dub/translation_review/' + encodeURIComponent(state.taskId) + '/export');
    const d = await r.json();
    if (!d.text) throw new Error('empty');
    const blob = new Blob([d.text], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = d.filename || 'tubedub_review.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    vmNotify(vmFriendlyError(e.message), 'error');
  }
}

function _collectTranslationEdits() {
  const edits = [];
  document.querySelectorAll('.tr-seg[data-idx]').forEach(row => {
    const idx = Number(row.getAttribute('data-idx'));
    const ta = document.getElementById('tr-edit-' + idx);
    if (!ta) return;
    const text = ta.value.trim();
    if (text) edits.push({ index: idx, text });
  });
  return edits;
}

async function approveTranslationReview() {
  if (!state.taskId || !translationReviewState.preTts) return;
  const approveBtn = document.getElementById('btn-approve-translation');
  const overlayApprove = document.getElementById('tr-approve-btn');
  if (approveBtn) approveBtn.disabled = true;
  if (overlayApprove) overlayApprove.disabled = true;
  try {
    const edits = _collectTranslationEdits();
    const r = await fetch('/api/auto_dub/translation_review/' + encodeURIComponent(state.taskId) + '/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        edits,
        original_volume: getReviewOriginalVolumePct(),
      }),
    });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'approve failed');
    translationReviewState.preTts = false;
    closeTranslationReview();
    const banner = document.getElementById('translation-review-banner');
    if (banner) banner.style.display = 'none';
    vmNotify(t('dub.review_approved', 'Перевод одобрен — запуск TTS…'), 'success', 4000);
    if (!state.polling) pollStatus();
  } catch (e) {
    if (approveBtn) approveBtn.disabled = false;
    if (overlayApprove) overlayApprove.disabled = false;
    vmNotify(vmFriendlyError(e.message), 'error');
  }
}

window.openTranslationReview = openTranslationReview;
window.closeTranslationReview = closeTranslationReview;
window.saveTranslationSegment = saveTranslationSegment;
window.copyTranslationReview = copyTranslationReview;
window.exportTranslationReview = exportTranslationReview;
window.approveTranslationReview = approveTranslationReview;

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeTranslationReview();
});

function _suggestDownloadName(serverFilename) {
  try {
    const lang = (document.getElementById('target-lang')?.value || '').toUpperCase().slice(0, 6);
    const rawName = state.videoName || '';
    const base = rawName
      .replace(/\.[^.]+$/, '')
      .replace(/[\\/:*?"<>|]/g, '_')
      .replace(/\s+/g, '_')
      .replace(/_+/g, '_')
      .replace(/^_|_$/g, '')
      .slice(0, 50);
    if (base) return base + (lang ? '_' + lang : '_Dub') + '.mp4';
    const now = new Date();
    const dateStr = now.getFullYear() + '-' +
      String(now.getMonth() + 1).padStart(2, '0') + '-' +
      String(now.getDate()).padStart(2, '0') + '_' +
      String(now.getHours()).padStart(2, '0') + '-' +
      String(now.getMinutes()).padStart(2, '0');
    return 'Dub_' + dateStr + '.mp4';
  } catch (_) {
    return serverFilename || 'dub_output.mp4';
  }
}

async function _autoMixAndFinish(taskId) {
  const pbar = document.getElementById('progress-fill');
  const pct = document.getElementById('progress-percent');
  const pctHead = document.getElementById('wizard-progress-pct');
  wizardShowScreen('progress');
  updateWizardPhases('dub', 'running');
  setProgressInfoMessage(t('dub.step_export', 'Подготавливается MP4…'));
  if (pbar) pbar.style.width = '90%';
  if (pct) pct.textContent = '90%';
  if (pctHead) pctHead.textContent = '90%';
  _dubBusy(true);
  try {
    const r = await fetch('/api/studio/mix/' + encodeURIComponent(taskId), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: true }),
    });
    const d = await r.json();
    if (d.ok && (d.output_file || d.download)) {
      const outputFile = d.output_file || (d.download ? d.download.split('/').pop() : null);
      finishSuccess(outputFile, null, null);
    } else {
      finishError(d.error || t('dub.mix_error', 'Ошибка сборки MP4'));
    }
  } catch (e) {
    finishError(e.message || t('dub.server_unreachable', 'Сервер недоступен'));
  }
}

function finishSuccess(outputFile, subtitleFile, studioUrl) {
  clearInterval(state.polling);
  state.polling = null;
  state.running = false;
  _dubBusy(false);
  state.outputFile = outputFile;
  state.subtitleFile = subtitleFile || null;
  localStorage.removeItem('vm_active_task');

  document.getElementById('btn-start-dub').disabled = false;
  wizardShowScreen('result');

  const link = document.getElementById('download-link');
  if (link) {
    link.style.display = outputFile ? '' : 'none';
    link.disabled = !outputFile;
  }

  const previewBtn = document.getElementById('btn-preview-output');
  if (previewBtn) {
    previewBtn.style.display = outputFile ? 'inline-block' : 'none';
    previewBtn.dataset.outputFile = outputFile || '';
  }

  const resolvedStudioUrl =
    studioUrl ||
    state.studioUrl ||
    (state.taskId ? ('/studio?task_id=' + encodeURIComponent(state.taskId)) : null);
  if (resolvedStudioUrl) state.studioUrl = resolvedStudioUrl;
  const studioBtn = document.getElementById('btn-open-studio');
  if (studioBtn) {
    if (resolvedStudioUrl) {
      studioBtn.style.display = 'inline-block';
      studioBtn.onclick = function () {
        window.location.href = resolvedStudioUrl;
      };
    } else {
      studioBtn.style.display = 'none';
      studioBtn.onclick = null;
    }
  }

  const subLink = document.getElementById('subtitle-download-link');
  if (subLink) {
    if (subtitleFile) {
      subLink.style.display = 'inline-block';
      subLink.href = '/api/dub/download/' + encodeURIComponent(subtitleFile);
      subLink.download = subtitleFile;
    } else {
      subLink.style.display = 'none';
      subLink.removeAttribute('href');
    }
  }

  renderProgressSteps('done', 'done');
  updateWizardPhases('done', 'done');
  document.getElementById('progress-fill').style.width = '100%';
  document.getElementById('progress-percent').textContent = '100%';
  setProgressInfoMessage(t('dub.step_done', 'Готово'));

  vmNotify(t('dub.done', 'Дубляж завершён!'), 'success');
  if (typeof vmUiSound === 'function') vmUiSound('success');
  const reviewBtn = document.getElementById('btn-translation-review');
  if (reviewBtn && state.taskId) reviewBtn.style.display = 'inline-block';
  const openddfBtn = document.getElementById('btn-openddf-report');
  if (openddfBtn && state.taskId) openddfBtn.style.display = 'inline-block';
  if (typeof vmShowFeedbackModal === 'function') {
    setTimeout(() => vmShowFeedbackModal(state.taskId), 800);
  }
  if (typeof vmCloudPostDubPrompt === 'function') {
    vmCloudPostDubPrompt(outputFile, subtitleFile);
  }
}

async function saveToFolder() {
  const name = state.outputFile;
  if (!name) {
    vmNotify(t('dub.wait_for_done', 'Сначала дождитесь завершения дубляжа'), 'warning');
    return;
  }
  try {
    const r = await fetch('/api/dub/save_to_folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: name, suggested_name: _suggestDownloadName(name) }),
    });
    const d = await r.json();
    if (d.cancelled) return;
    if (d.success) {
      vmNotify(t('dub.saved_to_folder', 'Видео сохранено: {path}').replace('{path}', d.path || d.folder || ''), 'success', 5000);
    } else {
      vmNotify(vmFriendlyError(d.error || 'Не удалось сохранить'), 'error');
    }
  } catch (e) {
    vmNotify(vmFriendlyError(e.message), 'error');
  }
}

function renderOpenDdfErrorMeta(passiveOpenDdf, openddfArtifacts, runId) {
  const box = document.getElementById('error-panel-openddf');
  const zipBtn = document.getElementById('error-panel-diagnostics-btn');
  const openddfBtn = document.getElementById('error-panel-openddf-btn');
  if (!box) return;
  const rid = runId || (passiveOpenDdf && passiveOpenDdf.run_id) || window.__vmLastTaskId || '—';
  const zip = (passiveOpenDdf && passiveOpenDdf.diagnostic_zip)
    || (openddfArtifacts && openddfArtifacts.diagnostic_zip)
    || '';
  const zipStatus = (passiveOpenDdf && passiveOpenDdf.diagnostic_zip_status)
    || (zip ? 'created' : '');
  const zipReason = (passiveOpenDdf && passiveOpenDdf.diagnostic_zip_reason) || '';
  const taskId = rid && rid !== '—' ? String(rid) : '';
  const statusLabels = {
    creating: 'Создание…',
    created: 'Создан',
    failed: 'Ошибка',
  };
  const lines = [
    `<div><span style="color:var(--text2);">Run ID:</span> ${escHtml(String(rid))}</div>`,
    `<div><span style="color:var(--text2);">Diagnostic ZIP:</span> `
    + `<strong>${escHtml(statusLabels[zipStatus] || zipStatus || '—')}</strong></div>`,
  ];
  if (zipStatus === 'failed' && zipReason) {
    lines.push(
      `<div style="color:var(--danger,#f66);"><span style="color:var(--text2);">Причина:</span> `
      + `${escHtml(zipReason)}</div>`
    );
  } else if (zipStatus === 'creating') {
    lines.push(
      `<div style="color:var(--text2);">Архив формируется, подождите…</div>`
    );
  } else if (zipStatus === 'created') {
    lines.push(
      `<div style="color:var(--text2);">Нажмите «Скачать диагностику», чтобы сохранить файл.</div>`
    );
  } else if (taskId) {
    // No archive yet — TZ §9: never leave a bare "—"; explain it is on-demand.
    lines.push(
      `<div style="color:var(--text2);">Архив ещё не сформирован — он будет создан `
      + `автоматически при нажатии «Скачать диагностику».</div>`
    );
  } else {
    lines.push(
      `<div style="color:var(--text2);">Run ID недоступен — диагностику нельзя сохранить.</div>`
    );
  }
  box.innerHTML = lines.join('');
  box.style.display = 'block';
  // TZ §9: buttons must always work when a Run ID exists; the archive is
  // generated on demand by the /save endpoint (ensure_diagnostic_archive).
  if (zipBtn) {
    zipBtn.style.display = taskId ? 'inline-flex' : 'none';
    zipBtn.disabled = !taskId;
    zipBtn.title = taskId
      ? 'Скачать diagnostic ZIP для Run ID ' + taskId
      : (zipReason || 'Run ID недоступен');
  }
  if (openddfBtn) {
    openddfBtn.style.display = taskId ? 'inline-flex' : 'none';
    openddfBtn.disabled = !taskId;
  }
}

async function vmDownloadOpenDdfZip() {
  const taskId = state.taskId || window.__vmLastTaskId || window.__vmLastOpenDdfRunId;
  if (!taskId) {
    vmNotify('Run ID / task не найден', 'warning');
    return;
  }
  try {
    const r = await fetch(
      '/api/auto_dub/diagnostics/' + encodeURIComponent(taskId) + '/save',
      { method: 'POST' }
    );
    const d = await r.json();
    if (d.cancelled) return;
    if (!r.ok || !d.success) {
      vmNotify(d.diagnostic_zip_reason || d.error || 'Не удалось сохранить диагностику', 'error');
      return;
    }
    vmNotify('Диагностика сохранена: ' + (d.path || d.filename), 'success', 5000);
  } catch (e) {
    vmNotify(vmFriendlyError(e.message), 'error');
  }
}
window.vmDownloadOpenDdfZip = vmDownloadOpenDdfZip;

let _openddfReportCache = null;

async function openOpenDdfReport() {
  const taskId = state.taskId || window.__vmLastTaskId || window.__vmLastOpenDdfRunId;
  if (!taskId) {
    vmNotify('Задача не найдена', 'warning');
    return;
  }
  const overlay = document.getElementById('openddf-overlay');
  const body = document.getElementById('openddf-body');
  const summary = document.getElementById('openddf-summary');
  if (!overlay || !body) return;
  overlay.style.display = 'flex';
  if (_openddfReportCache && _openddfReportCache.task_id === taskId) {
    renderOpenDdfReport(_openddfReportCache);
    return;
  }
  body.innerHTML = '<div class="char-count">Загрузка OpenDDF…</div>';
  summary.textContent = '';
  try {
    const r = await fetch('/api/auto_dub/openddf_report/' + encodeURIComponent(taskId));
    const d = await r.json();
    if (!r.ok || !d.ok) {
      body.innerHTML = '<div class="char-count">' + escHtml(d.error || 'Не удалось загрузить отчёт') + '</div>';
      return;
    }
    _openddfReportCache = d.report || {};
    _openddfReportCache.task_id = taskId;
    renderOpenDdfReport(_openddfReportCache);
  } catch (e) {
    body.innerHTML = '<div class="char-count">' + escHtml(vmFriendlyError(e.message)) + '</div>';
  }
}

function closeOpenDdfReport() {
  const overlay = document.getElementById('openddf-overlay');
  if (overlay) overlay.style.display = 'none';
}

function renderOpenDdfReport(report) {
  const body = document.getElementById('openddf-body');
  const summary = document.getElementById('openddf-summary');
  if (!body) return;
  const sm = report.summary || {};
  const flags = (report.flags || []).filter(Boolean);
  if (summary) {
    summary.innerHTML = [
      `<strong>${escHtml(sm.adaptation_status || '')}</strong> · `,
      escHtml(sm.overlap_status || ''),
      ' · ',
      escHtml(sm.timing_status || ''),
      flags.length ? ` · <span style="color:var(--warning,#fa0);">${escHtml(flags.join(' · '))}</span>` : '',
    ].join('');
  }
  const segments = report.segments || [];
  const html = segments.map((seg) => {
    const idx = seg.index;
    const adapt = seg.adaptation_status || 'ADAPTATION NOT EXECUTED';
    const algo = seg.algorithm_reason || '';
    const rw = [
      `rule:${seg.rule_rewrite_used ? 'yes' : 'no'}`,
      `llm:${seg.llm_rewrite_used ? 'yes' : 'no'}`,
      seg.requires_llm_adaptation ? 'REQUIRES_LLM' : '',
    ].filter(Boolean).join(' · ');
    const rwColor = seg.requires_llm_adaptation ? 'var(--warning,#fa0)' : 'var(--text2)';
    return (
      `<div class="tr-segment" style="margin-bottom:14px;padding:10px;border:1px solid var(--border);border-radius:8px;">`
      + `<div><strong>#${idx + 1}</strong> · ${escHtml(adapt)} · ${escHtml(algo)}</div>`
      + `<div class="char-count" style="color:${rwColor};">Rewrite: ${escHtml(rw)}`
      + (seg.llm_reason ? ` · ${escHtml(seg.llm_reason)}` : '') + `</div>`
      + `<div class="char-count" style="margin-top:6px;"><b>EN:</b> ${escHtml((seg.original_text || '').slice(0, 400))}</div>`
      + `<div class="char-count"><b>UA:</b> ${escHtml((seg.translated_text || '').slice(0, 400))}</div>`
      + `<div class="char-count"><b>After adapt:</b> ${escHtml((seg.text_after_adaptation || seg.final_tts_text || '').slice(0, 400))}</div>`
      + `<div class="char-count" style="margin-top:6px;">`
      + `orig=${seg.original_duration_ms}ms · first_tts=${seg.first_tts_duration_ms}ms · `
      + `final_tts=${seg.final_tts_duration_ms}ms · iter=${seg.adaptation_iterations || 0} · `
      + `start=${seg.start_time_ms} end=${seg.end_time_ms} · timing=${escHtml(seg.timing_source || '')}`
      + `</div>`
      + (seg.adaptation_reasons && seg.adaptation_reasons.length
        ? `<div class="char-count">adapt reasons: ${escHtml(seg.adaptation_reasons.join(', '))}</div>` : '')
      + `<div class="char-count" style="margin-top:6px;color:${(seg.llm_calls && seg.llm_calls.length) ? 'var(--text2)' : 'var(--warning,#fa0)'};">`
      + `<b>LLM called:</b> ${(seg.llm_calls && seg.llm_calls.length) ? 'YES (' + seg.llm_calls.length + ')' : 'NO'}`
      + (seg.llm_needed && !seg.llm_called ? ` · <span style="color:var(--danger,#f66);">LLM_NOT_CALLED: ${escHtml(seg.llm_skip_reason || 'unknown')}</span>` : '')
      + (seg.llm_no_rewrite ? ` · <span style="color:var(--warning,#fa0);">NO_REWRITE_PERFORMED</span>` : '')
      + `</div>`
      + ((seg.errors || []).map((e) =>
          `<div class="char-count" style="color:var(--danger,#f66);">✖ ${escHtml(e.code || '')}: ${escHtml(e.message || e.reason || '')}</div>`
        ).join(''))
      + ((seg.llm_calls || []).map((c) =>
          `<div class="char-count" style="margin-left:8px;border-left:2px solid var(--border);padding-left:6px;">`
          + `<b>→ sent:</b> ${escHtml((c.sent || '').slice(0, 300))}`
          + `<br><b>← recv:</b> ${escHtml((c.received || '').slice(0, 300))}`
          + `<br>finish=${escHtml(c.finish_reason || '')} · ${Math.round(c.ms || 0)}ms · `
          + `usable=${c.usable ? 'yes' : 'no'} · stage=${escHtml(c.stage || '')}</div>`
        ).join(''))
      + (seg.block_merge && seg.block_merge.merge_adjusted_start
        ? `<div class="char-count">block_merge start=${seg.block_merge.merge_adjusted_start}</div>` : '')
      + (seg.gap_absorb && seg.gap_absorb.mode
        ? `<div class="char-count">gap_absorb: ${escHtml(seg.gap_absorb.mode)}</div>` : '')
      + `</div>`
    );
  }).join('');
  const skipped = report.skipped_segments || [];
  const skippedHtml = skipped.length
    ? `<div class="char-count" style="margin-top:12px;"><b>Skipped/merged:</b> ${skipped.length}</div>`
    + skipped.map((s) => `<div class="char-count">#${s.index + 1} → merged_into #${(s.merged_into || 0) + 1}</div>`).join('')
    : '';
  const overlaps = report.overlaps || [];
  const overlapHtml = overlaps.length
    ? `<div style="margin-top:12px;color:var(--danger,#f66);"><b>OVERLAP DETECTED</b> (${overlaps.length})</div>`
    + overlaps.map((o) => `<div class="char-count">#${(o.index || 0) + 1}: ${escHtml(o.type || '')} ${o.overlap_ms || o.overflow_ms || ''}ms</div>`).join('')
    : '';
  const mode = report.adaptation_mode || null;
  let modeHtml = '';
  if (mode) {
    const gateC = mode.strict_gate_activated ? 'var(--danger,#f66)' : 'var(--text2)';
    modeHtml = `<div style="margin-bottom:14px;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--panel2);">`
      + `<div><strong>Режим адаптации:</strong> ${escHtml(mode.mode_label || mode.mode || '')}</div>`
      + `<div class="char-count">Базовая адаптация: ${mode.rule_rewrite_available ? 'доступна' : 'нет'} · `
      + `Интеллектуальная адаптация: ${mode.llm_rewrite_available ? 'доступна' : 'недоступна'}`
      + `</div>`
      + `<div class="char-count" style="color:${gateC};">Строгий контроль: ${mode.strict_gate_activated ? 'АКТИВИРОВАН — пайплайн остановлен' : 'не активирован'}</div>`
      + (mode.stop_reason ? `<div class="char-count" style="color:var(--danger,#f66);">Причина остановки: ${escHtml(mode.stop_reason)}</div>` : '')
      + (mode.user_warnings || []).map((w) => `<div class="char-count" style="color:var(--warning,#fa0);">⚠ ${escHtml(w.message || w)}</div>`).join('')
      + ((mode.stop_diagnostics && mode.stop_diagnostics.recommendations) || []).map((rc) => `<div class="char-count">→ ${escHtml(rc)}</div>`).join('')
      + `</div>`;
  }
  const cap = report.adaptation_capabilities || null;
  let capHtml = '';
  if (cap) {
    const llmC = cap.llm_rephrase_available ? 'var(--text2)' : 'var(--warning,#fa0)';
    const musC = cap.music_preserved ? 'var(--text2)' : 'var(--warning,#fa0)';
    capHtml = `<div style="margin-top:14px;padding-top:10px;border-top:1px solid var(--border);">`
      + `<div><strong>Возможности адаптации</strong></div>`
      + `<div class="char-count">Интеллектуальная адаптация: <span style="color:${llmC};">`
      + `${cap.llm_rephrase_available ? 'доступна' : 'недоступна'}</span> · `
      + `Музыка сохранена: <span style="color:${musC};">`
      + `${cap.music_preserved ? 'да' : 'нет'}</span>`
      + (cap.separation_method ? ` (${escHtml(cap.separation_method)})` : '') + `</div>`
      + (cap.notes || []).map((n) => `<div class="char-count" style="color:var(--warning,#fa0);">• ${escHtml(n)}</div>`).join('')
      + `</div>`;
  }
  const tts = report.tts_pipeline || null;
  let ttsHtml = '';
  if (tts && (tts.segments || []).length) {
    const missColor = tts.audio_missing > 0 ? 'var(--danger,#f66)' : 'var(--text2)';
    ttsHtml = `<div style="margin-top:16px;padding-top:10px;border-top:1px solid var(--border);">`
      + `<div><strong>TTS Pipeline</strong> · `
      + `<span style="color:${missColor};">present ${tts.audio_present}/${tts.expected_segments}, missing ${tts.audio_missing}</span></div>`
      + `<div class="char-count">session_dir: ${escHtml(tts.session_dir || '—')}</div>`
      + (tts.segments).map((s) => {
        const ex = s.exists ? 'ok' : 'MISSING';
        const exC = s.exists ? 'var(--text2)' : 'var(--danger,#f66)';
        return `<div class="char-count" style="margin-top:4px;">`
          + `#${(s.index || 0) + 1} · <span style="color:${exC};">${ex}</span> · `
          + `${escHtml(s.fitted_file || s.file || '—')} · ${s.size_bytes || 0}B`
          + `<br>path: ${escHtml(s.resolved_path || '—')}`
          + `<br><i>${escHtml((s.text_preview || ''))}</i></div>`;
      }).join('')
      + `</div>`;
  }
  const stor = report.storage_report || null;
  let storageHtml = '';
  if (stor) {
    storageHtml = `<div style="margin-top:14px;padding:10px;border-top:1px solid var(--border);">`
      + `<div><strong>Storage Report</strong></div>`
      + `<div class="char-count">Удалено: ${stor.files_deleted || 0} · освобождено: ${stor.mb_freed != null ? stor.mb_freed + ' МБ' : (stor.bytes_freed || 0) + ' B'}</div>`
      + (stor.directories_cleaned && stor.directories_cleaned.length
        ? `<div class="char-count">Очищено: ${stor.directories_cleaned.slice(0, 8).map(escHtml).join('; ')}</div>` : '')
      + (stor.directories_skipped && stor.directories_skipped.length
        ? `<div class="char-count">Пропущено (защита): ${stor.directories_skipped.length}</div>` : '')
      + (stor.note ? `<div class="char-count">${escHtml(stor.note)}</div>` : '')
      + `</div>`;
  }
  const aiInst = report.ai_installation || null;
  let aiHtml = '';
  if (aiInst) {
    aiHtml = `<div style="margin-top:14px;padding:10px;border-top:1px solid var(--border);">`
      + `<div><strong>AI Installation</strong></div>`
      + `<div class="char-count">Статус: ${escHtml(aiInst.status_label || aiInst.status || '—')}</div>`
      + `<div class="char-count">Модуль: ${escHtml(aiInst.backend_label || 'AI-модуль TubeDub')}</div>`
      + (aiInst.model ? `<div class="char-count">Модель: ${escHtml(aiInst.model)}</div>` : '')
      + (aiInst.installed_at ? `<div class="char-count">Установлен: ${escHtml(String(aiInst.installed_at).slice(0, 19))}</div>` : '')
      + (aiInst.verification && aiInst.verification.ok ? `<div class="char-count">Проверка: успешно</div>` : '')
      + (aiInst.retries ? `<div class="char-count">Повторных попыток: ${aiInst.retries}</div>` : '')
      + (aiInst.last_error ? `<div class="char-count" style="color:var(--danger,#f66);">${escHtml(aiInst.last_error)}</div>` : '')
      + `</div>`;
  }
  const eff = report.llm_effectiveness || null;
  let effHtml = '';
  if (eff) {
    const availC = eff.llm_available ? 'var(--text2)' : 'var(--warning,#fa0)';
    const notCalledC = (eff.llm_not_called_segments > 0) ? 'var(--danger,#f66)' : 'var(--text2)';
    effHtml = `<div style="margin-bottom:14px;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--panel2);">`
      + `<div><strong>AI Adaptation Report</strong> · <span style="color:${availC};">`
      + `${eff.llm_available ? 'LLM работал' : 'LLM не вызывался'}</span></div>`
      + `<div class="char-count">Сегментов: ${eff.segment_count} · только Rule Rewrite: ${eff.rule_rewrite_only} · `
      + `через LLM Rewrite: ${eff.llm_rewrite_used} · LLM реально улучшил: <b>${eff.llm_improved_segments}</b></div>`
      + `<div class="char-count">Повторных генераций: ${eff.regenerations} · среднее число попыток: ${eff.avg_attempts} · `
      + `средний Slot Fit: ${eff.avg_slot_fit}</div>`
      + `<div class="char-count" style="color:${notCalledC};">LLM не вызвана при необходимости: <b>${eff.llm_not_called_segments}</b> · `
      + `фиктивных (NO_REWRITE): ${eff.no_rewrite_segments} · ошибок: ${eff.error_count}</div>`
      + `<div class="char-count">Пустых сегментов: ${eff.empty_segments} · оборванных слов: ${eff.cut_word_segments} · `
      + `оборванных предложений: ${eff.cut_sentence_segments}</div>`
      + `<div class="char-count">Всего вызовов LLM: ${eff.llm_calls_total} · пригодных: ${eff.llm_calls_usable} · `
      + `обрезано токен-лимитом (отброшено): ${eff.llm_calls_truncated}</div>`
      + `</div>`;
  }
  const diag = report.llm_diagnostics || null;
  let diagHtml = '';
  if (diag) {
    const skips = Object.entries(diag.skip_reasons || {}).map(([k, v]) => `${escHtml(k)}: ${v}`).join(' · ');
    diagHtml = `<div style="margin-bottom:14px;padding:10px;border:1px solid var(--border);border-radius:8px;">`
      + `<div><strong>LLM Diagnostics</strong></div>`
      + `<div class="char-count">Вызовов: ${diag.call_count} · провайдер: ${escHtml((diag.providers || []).join(', ') || '—')} · `
      + `модель: ${escHtml((diag.models || []).join(', ') || '—')}</div>`
      + `<div class="char-count">Суммарное время генерации: ${Math.round(diag.total_generation_ms || 0)}ms · `
      + `среднее на вызов: ${Math.round(diag.avg_call_ms || 0)}ms</div>`
      + (skips ? `<div class="char-count" style="color:var(--warning,#fa0);">Причины пропуска LLM: ${skips}</div>` : '')
      + `</div>`;
  }
  const integ = report.pre_tts_integrity || null;
  let integHtml = '';
  if (integ && (integ.checked || integ.fixed)) {
    const fixC = integ.fixed > 0 ? 'var(--warning,#fa0)' : 'var(--text2)';
    integHtml = `<div style="margin-bottom:14px;padding:10px;border:1px solid var(--border);border-radius:8px;">`
      + `<div><strong>Sentence Integrity (перед TTS)</strong></div>`
      + `<div class="char-count" style="color:${fixC};">Проверено: ${integ.checked} · `
      + `исправлено обрывов/пустых: ${integ.fixed || 0}`
      + ((integ.fixed_indices && integ.fixed_indices.length)
        ? ` · сегменты: ${integ.fixed_indices.map((i) => '#' + (i + 1)).join(', ')}` : '')
      + `</div>`
      + ((integ.segments || []).slice(0, 30).map((s) =>
          `<div class="char-count" style="margin-left:8px;">#${(s.index || 0) + 1}: `
          + `${escHtml((s.issues || []).join(', ') || s.reason || '')} → ${escHtml(s.chosen || '')}</div>`
        ).join(''))
      + `</div>`;
  }
  body.innerHTML = (effHtml + diagHtml + integHtml + modeHtml + aiHtml + storageHtml + html + skippedHtml + overlapHtml + ttsHtml + capHtml) || '<div class="char-count">Нет сегментов</div>';
}

async function exportOpenDdfReportJson() {
  const taskId = state.taskId || window.__vmLastTaskId || window.__vmLastOpenDdfRunId;
  if (!taskId) {
    vmNotify('Задача не найдена', 'warning');
    return;
  }
  try {
    const r = await fetch(
      '/api/auto_dub/openddf_report/' + encodeURIComponent(taskId) + '/save',
      { method: 'POST' }
    );
    const d = await r.json();
    if (d.cancelled) return;
    if (!r.ok || !d.success) {
      vmNotify(d.error || 'Не удалось сохранить OpenDDF', 'error');
      return;
    }
    vmNotify('OpenDDF сохранён: ' + (d.path || d.filename), 'success', 5000);
  } catch (e) {
    vmNotify(vmFriendlyError(e.message), 'error');
  }
}

window.openOpenDdfReport = openOpenDdfReport;
window.closeOpenDdfReport = closeOpenDdfReport;
window.exportOpenDdfReportJson = exportOpenDdfReportJson;

function renderPipelineErrorPanel(pipelineError, fallbackMsg, diagnosticBlock, developerPayload, openddfArtifacts, passiveOpenDdf, runId) {
  const summaryEl = document.getElementById('error-panel-summary');
  const detailsBtn = document.getElementById('error-panel-details-btn');
  const detailsEl = document.getElementById('error-panel-details');
  const legacyEl = document.getElementById('error-panel-text');
  if (!summaryEl) return;

  const dev = typeof isDevMode === 'function' && isDevMode();

  if (pipelineError && pipelineError.stage) {
    const lines = [
      `<strong>${escHtml(pipelineError.title || 'Ошибка дубляжа')}</strong>`,
      `<div style="margin-top:8px;line-height:1.6;">`,
      `<div><span style="color:var(--text2);">Этап:</span> ${escHtml(pipelineError.stage || '—')}</div>`,
    ];
    if (pipelineError.error_code) {
      lines.push(`<div><span style="color:var(--text2);">Код ошибки:</span> ${escHtml(pipelineError.error_code)}</div>`);
    }
    const llm = pipelineError.llm_diagnostics || (developerPayload && developerPayload.llm_diagnostics) || null;
    if (llm && (llm.model_display || llm.model)) {
      lines.push(`<div><span style="color:var(--text2);">Модель:</span> ${escHtml(llm.model_display || llm.model)}</div>`);
    }
    if (llm && (llm.provider_label || llm.provider)) {
      lines.push(`<div><span style="color:var(--text2);">Провайдер:</span> ${escHtml(llm.provider_label || llm.provider)}</div>`);
    }
    if (llm && llm.segment && llm.total_segments) {
      lines.push(`<div><span style="color:var(--text2);">Сегмент:</span> №${escHtml(String(llm.segment))} из ${escHtml(String(llm.total_segments))}</div>`);
    } else if (pipelineError.segment) {
      lines.push(`<div><span style="color:var(--text2);">Сегмент:</span> ${escHtml(String(pipelineError.segment))}</div>`);
    }
    if (llm && llm.chars_sent != null) {
      lines.push(`<div><span style="color:var(--text2);">Отправлено символов:</span> ${escHtml(String(llm.chars_sent))}</div>`);
    }
    if (llm && llm.wait_sec != null) {
      lines.push(`<div><span style="color:var(--text2);">Время ожидания:</span> ${escHtml(String(Math.round(llm.wait_sec)))} с</div>`);
    }
    if (llm && llm.attempts != null) {
      lines.push(`<div><span style="color:var(--text2);">Попыток:</span> ${escHtml(String(llm.attempts))}</div>`);
    }
    if (llm && llm.timeout != null) {
      lines.push(`<div><span style="color:var(--text2);">Таймаут:</span> ${llm.timeout ? 'да' : 'нет'}</div>`);
    }
    if (llm && llm.ollama && (llm.ollama.status_code || llm.failure_phase_label || llm.failure_phase)) {
      const ollamaLabels = {
        loaded: 'модель загружена',
        responding: 'модель отвечает',
        busy: 'модель занята',
        connection_timeout: 'таймаут соединения',
        generation_timeout: 'модель приняла запрос, но не завершила генерацию',
        model_cold: 'модель не загружена (холодный старт)',
        model_missing: 'модель не установлена',
        server_down: 'сервер Ollama недоступен',
        out_of_memory: 'не хватило памяти',
        api_error: 'ошибка API',
        not_loaded: 'модель не загружена',
        unreachable: 'сервер недоступен',
      };
      const phase = llm.failure_phase_label
        || ollamaLabels[llm.failure_phase]
        || ollamaLabels[llm.ollama.status_code]
        || llm.ollama.diagnosis_ru
        || llm.ollama.status_code;
      lines.push(`<div><span style="color:var(--text2);">Ollama:</span> ${escHtml(String(phase))}</div>`);
    }
    if (llm && llm.models_tried && llm.models_tried.length > 1) {
      lines.push(`<div><span style="color:var(--text2);">Пробовали модели:</span> ${escHtml(llm.models_tried.join(', '))}</div>`);
    }
    if (dev && pipelineError.error_type) {
      lines.push(`<div><span style="color:var(--text2);">Ошибка:</span> ${escHtml(pipelineError.error_type)}</div>`);
    }
    if (dev && (pipelineError.segment || pipelineError.segment_id)) {
      lines.push(`<div><span style="color:var(--text2);">Сегмент (dev):</span> ${escHtml(pipelineError.segment || pipelineError.segment_id)}</div>`);
    }
    lines.push(
      `<div><span style="color:var(--text2);">Причина:</span> ${escHtml(pipelineError.reason_short || pipelineError.reason || '—')}</div>`,
      `</div>`
    );
    if (
      !dev &&
      (pipelineError.stage === 'Audio Extraction' ||
        /^(FFMPEG_|NO_AUDIO|AUDIO_)/.test(String(pipelineError.error_code || '')))
    ) {
      lines.push(
        '<p style="margin-top:10px;color:var(--text2);font-size:0.9em;">' +
        'Скачайте диагностику (ZIP): audio_extraction_report.json, ffmpeg_stderr.log, pipeline.log.' +
        '</p>'
      );
    }
    summaryEl.innerHTML = lines.join('');
    if (legacyEl) legacyEl.style.display = 'none';

    const detailText = dev
      ? (diagnosticBlock || pipelineError.detail_block || formatOpenDdfDeveloperText(developerPayload, openddfArtifacts))
      : '';
    if (detailsEl && detailsBtn && detailText) {
      detailsEl.textContent = detailText;
      detailsBtn.style.display = 'inline-flex';
      detailsBtn.onclick = () => {
        const open = detailsEl.style.display !== 'none';
        detailsEl.style.display = open ? 'none' : 'block';
        detailsBtn.textContent = open ? t('dub.error_details', 'Подробнее') : t('dub.error_details_hide', 'Скрыть');
      };
      detailsEl.style.display = 'none';
      detailsBtn.textContent = t('dub.error_details', 'Подробнее');
    } else if (detailsBtn) {
      detailsBtn.style.display = 'none';
      if (detailsEl) detailsEl.style.display = 'none';
    }
    renderOpenDdfErrorMeta(passiveOpenDdf, openddfArtifacts, runId);
    return;
  }

  const friendly = vmFriendlyError(fallbackMsg || '');
  summaryEl.textContent = dev && diagnosticBlock ? friendly + '\n\n' + diagnosticBlock : friendly;
  if (legacyEl) legacyEl.style.display = 'none';
  if (detailsBtn) detailsBtn.style.display = 'none';
  if (detailsEl) detailsEl.style.display = 'none';
  renderOpenDdfErrorMeta(passiveOpenDdf, openddfArtifacts, runId);
}

function formatOpenDdfDeveloperText(payload, artifacts) {
  if (!payload || typeof payload !== 'object') return '';
  const d = payload.developer_details || {};
  const diff = payload.snapshot_diff || {};
  const trace = payload.traceability || {};
  const policy = payload.mutation_policy || {};
  const hint = payload.recovery_hint || {};
  const arts = artifacts || payload.artifacts || {};

  const field = d.field || diff.field || '?';
  const oldVal = d.old_value != null ? d.old_value : (diff.old_value || '?');
  const newVal = d.new_value != null ? d.new_value : (diff.new_value || '?');
  const module = d.module || trace.module || '?';
  const fn = d.function || trace.function || '?';
  const file = d.file || trace.file_path || '?';
  const line = d.line != null ? d.line : (trace.line_number != null ? trace.line_number : '?');
  const allowed = d.allowed_mutations || policy.allowed || [];
  const recovery = d.recovery_hint || hint.text || '';

  const lines = [
    `Field:`,
    field,
    `Old Value:`,
    oldVal,
    `New Value:`,
    newVal,
    '',
    `Поле:`,
    field,
    `Изменилось:`,
    oldVal,
    '↓',
    newVal,
    '',
    `Module:`,
    module,
    `Function:`,
    fn,
    `File:`,
    file,
    `Line:`,
    line,
    '',
    `Источник:`,
    file,
    `Функция:`,
    fn,
    `Строка:`,
    line,
    '',
    'Allowed Mutations:',
    ...allowed,
    '',
    'Recovery Hint:',
    recovery,
  ];

  const artKeys = ['snapshot_before', 'snapshot_after', 'snapshot_diff', 'report', 'pipeline_log', 'stacktrace', 'diagnostic_zip', 'diagnostics_dir'];
  const hasArts = artKeys.some((k) => arts[k]);
  if (hasArts) {
    lines.push('', '— Diagnostics folder —');
    artKeys.forEach((k) => {
      if (arts[k]) lines.push(`${k}: ${arts[k]}`);
    });
  }
  return lines.join('\n');
}

function finishError(msg, diagnosticBlock, pipelineError, developerPayload, openddfArtifacts, passiveOpenDdf, runId) {
  clearInterval(state.polling);
  state.polling = null;
  state.running = false;
  _dubBusy(false);
  const dev = typeof isDevMode === 'function' && isDevMode();
  let userMsg = msg;
  if (!dev && msg && /60\s*секунд|60\s*second|timeout|TranslationTimeout|translation_timeout/i.test(msg)) {
    userMsg = t('dub.long_processing', 'Выполняется длительная обработка. Пожалуйста, подождите.');
  }
  window.__vmLastError = msg;
  window.__vmLastDiagnostic = diagnosticBlock || (pipelineError && pipelineError.detail_block) || '';
  window.__vmLastPipelineError = pipelineError || null;
  window.__vmLastOpenDdfDeveloper = developerPayload || null;
  window.__vmLastOpenDdfArtifacts = openddfArtifacts || null;
  window.__vmLastPassiveOpenDdf = passiveOpenDdf || null;
  window.__vmLastOpenDdfRunId = runId || (passiveOpenDdf && passiveOpenDdf.run_id) || state.taskId || null;
  window.__vmLastTaskId = state.taskId || window.__vmLastTaskId;
  localStorage.removeItem('vm_active_task');
  document.getElementById('btn-start-dub').disabled = false;

  renderPipelineErrorPanel(
    pipelineError,
    userMsg,
    diagnosticBlock,
    developerPayload,
    openddfArtifacts,
    passiveOpenDdf,
    runId
  );
  wizardShowScreen('error');

  const notifyMsg = pipelineError
    ? (pipelineError.reason_short || pipelineError.reason || userMsg)
    : vmFriendlyError(userMsg);
  vmNotify(notifyMsg, 'error');
}

function setupSegmentsToggle() {
  const toggle = document.getElementById('segments-toggle');
  const body = document.getElementById('segments-body');
  const chev = document.getElementById('segments-chevron');
  toggle.addEventListener('click', () => {
    body.classList.toggle('collapsed');
    chev.textContent = body.classList.contains('collapsed') ? '▶' : '▼';
  });
}

async function loadUniversalImport() {
  if (typeof vmConsumeUniversalImport !== 'function') return;
  await vmConsumeUniversalImport({
    video: async (d) => {
      const rel = d.path || (d.upload_filename ? `uploads/imports/${d.upload_filename}` : '');
      if (!rel) return;
      // Project isolation: reset per-project state on native file import too
      if (state.polling) clearInterval(state.polling);
      state.taskId = null;
      state.running = false;
      state.starting = false;
      state.lastProgress = 0;
      state.lastSlotFitLogIdx = -1;
      state.redub = null;
      state.filename = rel;
      state.videoName = d.original_filename || d.filename || rel.split('/').pop();
      warnIfOutputFilename(state.videoName);
      document.getElementById('video-info').style.display = 'flex';
      document.getElementById('video-name').textContent = state.videoName;
      document.getElementById('video-size').textContent = 'импорт';
      document.getElementById('btn-start-dub').disabled = false;
      vmNotify(t('dub.video_ready', 'Видео загружено'), 'success', 2500);
      wizardAdvanceFrom('video');
    },
  });
}

async function loadRedubFromUrl() {
  const id = new URLSearchParams(window.location.search).get('redub');
  if (!id) return;
  try {
    const r = await fetch('/api/studio/redub/' + encodeURIComponent(id));
    const d = await r.json();
    if (!r.ok || !d.ok) return;
    state.redub = d;
    vmNotify(
      t('dub.redub_loaded', 'Субтитры из Студии загружены. Выберите видео и нажмите «Дубляж».'),
      'info',
      5000
    );
  } catch (_) {}
}

function openOutputPreview() {
  const btn = document.getElementById('btn-preview-output');
  const file = (btn && btn.dataset.outputFile) || state.outputFile;
  if (!file) {
    vmNotify(t('dub.preview_no_file', 'Нет файла для просмотра'), 'warning');
    return;
  }
  const overlay = document.getElementById('dub-preview-overlay');
  const video = document.getElementById('dub-preview-video');
  if (!overlay || !video) return;
  video.src = '/api/dub/preview_output/' + encodeURIComponent(file) + '?t=' + Date.now();
  // Кнопка скачать внутри превью вызывает saveToFolder() напрямую через onclick
  overlay.style.display = 'flex';
  video.play().catch(() => {});
}

function closeOutputPreview() {
  const overlay = document.getElementById('dub-preview-overlay');
  const video = document.getElementById('dub-preview-video');
  if (video) {
    video.pause();
    video.removeAttribute('src');
  }
  if (overlay) overlay.style.display = 'none';
}

function tryResumeTask() {
  if (state.running || state.starting) return;
  try {
    const raw = localStorage.getItem('vm_active_task');
    if (!raw) return;
    const task = JSON.parse(raw);
    if (!task.taskId) return;
    fetch('/api/auto_dub/status/' + task.taskId + '?lite=1')
      .then(r => r.json())
      .then(d => {
        if (d.error || d.status === 'error') {
          const errMsg =
            d.error ||
            d.message ||
            d.reason ||
            t('dub.resume_failed', 'Предыдущий дубляж завершился с ошибкой.');
          state.taskId = task.taskId;
          try {
            finishError(
              errMsg,
              d.diagnostic_block || null,
              d.pipeline_error || null,
              d.openddf_developer || null,
              d.openddf_artifacts || null,
              d.passive_openddf || null,
              d.run_id || task.taskId
            );
          } catch (_) {
            localStorage.removeItem('vm_active_task');
          }
          return;
        }
        if (d.status === 'studio_ready') {
          state.taskId = task.taskId;
          wizardShowScreen('progress');
          _autoMixAndFinish(task.taskId);
          return;
        } else if (d.status === 'running' || d.status === 'translation_review') {
          state.taskId = task.taskId;
          state.videoName = task.videoName;
          if (d.studio_url) state.studioUrl = d.studio_url;
          wizardShowScreen('progress');
          state.running = true;
          _dubBusy(true);
          document.getElementById('btn-start-dub').disabled = true;
          pollStatus();
        } else if (d.status === 'done' && d.output_file) {
          state.taskId = task.taskId;
          finishSuccess(d.output_file, d.subtitle_file, d.studio_url);
        }
      })
      .catch(() => localStorage.removeItem('vm_active_task'));
  } catch (_) {}
}

document.addEventListener('DOMContentLoaded', async () => {
  setupUpload();
  setupSegmentsToggle();
  initWizard();
  await loadVoiceCatalog();

  const defLang = typeof getDefaultTargetLang === 'function' ? getDefaultTargetLang() : 'ru';
  const tgt = document.getElementById('target-lang');
  if (tgt) tgt.value = defLang;

  try {
    const saved = typeof loadSettings === 'function' ? loadSettings() : {};
    const be = saved.ttsEngine || saved.tts_engine || 'edge-offline';
    syncTtsEngineControls(be);
  } catch (_) {}
  const wizBackend = document.getElementById('wizard-tts-backend');
  if (wizBackend) {
    wizBackend.addEventListener('change', () => {
      syncTtsEngineControls(wizBackend.value);
      state.selectedVoice = null;
      updateVoiceList();
    });
  }
  bindMykytaWizardSliders();
  syncMykytaWizardVisibility(currentTtsBackend());

  updateVoiceList();
  renderWizardLangGrid();
  loadDubStyles();

  const localOnlyCb = document.getElementById('dub-styles-local-only');
  if (localOnlyCb) {
    localOnlyCb.checked = getStylesLocalOnly();
    localOnlyCb.addEventListener('change', () => {
      setStylesLocalOnly(localOnlyCb.checked);
      loadDubStyles();
    });
  }

  document.getElementById('target-lang').addEventListener('change', () => {
    updateVoiceList();
    loadDubStyles();
    renderWizardLangGrid();
  });
  document.getElementById('source-auto').addEventListener('change', () => setSourceMode('auto'));
  updateInspectorButtonVisibility();
  document.getElementById('source-manual').addEventListener('change', () => setSourceMode('manual'));
  initWizardSourceLang();
  setSourceMode(document.getElementById('source-auto').checked ? 'auto' : 'manual');
  document.getElementById('btn-start-dub').addEventListener('click', startDub);
  document.getElementById('btn-wizard-back')?.addEventListener('click', wizardGoBack);
  document.getElementById('btn-wizard-next')?.addEventListener('click', wizardGoNext);
  document.getElementById('btn-wizard-cancel')?.addEventListener('click', wizardCancelSetup);
  document.getElementById('btn-progress-cancel')?.addEventListener('click', cancelDubOperation);
  document.getElementById('btn-preview-output')?.addEventListener('click', openOutputPreview);
  document.getElementById('btn-close-preview')?.addEventListener('click', closeOutputPreview);

  const saved = typeof loadSettings === 'function' ? loadSettings() : {};
  if (saved.keepOriginalTrack) document.getElementById('keep-original-track').checked = true;
  const strictSel = document.getElementById('strict-llm-adaptation');
  if (strictSel && saved.strictLlmAdaptation) strictSel.value = saved.strictLlmAdaptation;
  if (strictSel) strictSel.addEventListener('change', checkAdaptationCapabilities);
  checkAdaptationCapabilities();
  syncWizardModelSize(false);
  document.getElementById('wizard-model-size')?.addEventListener('change', () => {
    syncWizardModelSize(true);
    updateWizardSummary();
  });

  bindReviewOriginalMixControls();
  const savedOrigVol = loadSavedOriginalVolumePct();
  if (savedOrigVol != null) {
    setReviewOriginalVolumePct(savedOrigVol, { persist: false });
  } else {
    setReviewOriginalVolumePct(20, { persist: false });
  }

  tryResumeTask();
  loadRedubFromUrl();
  loadUniversalImport();
  loadDebugModeFlag();
});

// TZ §3/§9: surface LLM availability so the user is never silently degraded.
let _adaptationCaps = null;
// TubeDub AI Manager — first-run dialog (no technical terms exposed)
let _aiModulePoll = null;

function _showAiModuleDialog(show) {
  const el = document.getElementById('ai-module-overlay');
  if (el) el.style.display = show ? 'flex' : 'none';
}

async function ensureAiModuleForDub() {
  try {
    const strictSel = document.getElementById('strict-llm-adaptation');
    const qualityMaximum = strictSel && strictSel.value === 'strict';
    const url = '/api/ai-module/prompt-needed' + (qualityMaximum ? '?quality=maximum' : '');
    const r = await fetch(url);
    const d = await r.json();
    if (!d.ok || !d.needed) return true;
    const sizeEl = document.getElementById('ai-module-size');
    if (sizeEl && d.estimated_download_gb) {
      sizeEl.textContent = '~' + d.estimated_download_gb + ' ГБ';
    }
    return new Promise((resolve) => {
      const installBtn = document.getElementById('ai-module-install-btn');
      const laterBtn = document.getElementById('ai-module-later-btn');
      const prog = document.getElementById('ai-module-progress');
      const progBar = document.getElementById('ai-module-progress-bar');
      const progMsg = document.getElementById('ai-module-progress-msg');
      _showAiModuleDialog(true);
      if (prog) prog.style.display = 'none';
      const onLater = async () => {
        await fetch('/api/ai-module/defer', { method: 'POST' });
        _showAiModuleDialog(false);
        resolve(true);
      };
      const onInstall = async () => {
        if (installBtn) installBtn.disabled = true;
        if (laterBtn) laterBtn.disabled = true;
        if (prog) prog.style.display = 'block';
        await fetch('/api/ai-module/install', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        _aiModulePoll = setInterval(async () => {
          try {
            const pr = await fetch('/api/ai-module/install/progress');
            const st = await pr.json();
            const p = st.install_progress || {};
            if (progBar) progBar.style.width = (p.percent || 0) + '%';
            if (progMsg) progMsg.textContent = p.message || st.status_label || '';
            if (st.status === 'ready') {
              clearInterval(_aiModulePoll);
              vmNotify('AI-модуль успешно установлен. Теперь доступна интеллектуальная адаптация текста.', 'success', 6000);
              _showAiModuleDialog(false);
              resolve(true);
            } else if (st.status === 'error') {
              clearInterval(_aiModulePoll);
              vmNotify(st.last_error || 'Ошибка установки AI-модуля', 'error');
              if (installBtn) installBtn.disabled = false;
              if (laterBtn) laterBtn.disabled = false;
            }
          } catch (_e) { /* keep polling */ }
        }, 2000);
      };
      if (laterBtn) laterBtn.onclick = onLater;
      if (installBtn) installBtn.onclick = onInstall;
    });
  } catch (_e) {
    return true;
  }
}

async function checkAdaptationCapabilities() {
  try {
    const r = await fetch('/api/ai-module/status');
    const st = await r.json();
    const strictSel = document.getElementById('strict-llm-adaptation');
    const strict = strictSel && strictSel.value === 'strict';
    if (!st.ready && strict) {
      vmNotify(
        'Строгий режим включён, но AI-модуль не установлен. Установите AI-модуль в настройках или выберите «Автоматический» режим.',
        'warning', 8000
      );
    } else if (!st.ready && !st.deferred) {
      vmNotify(
        'Для максимального качества дубляжа рекомендуется установить AI-модуль TubeDub. Можно продолжить в упрощённом режиме.',
        'info', 6000
      );
    }
  } catch (_e) { /* non-blocking */ }
}

window.checkAdaptationCapabilities = checkAdaptationCapabilities;
window.openOutputPreview = openOutputPreview;
window.closeOutputPreview = closeOutputPreview;
window.setReviewFontSize = setReviewFontSize;
window.filterReviewSegments = filterReviewSegments;
window.replaceInReview = replaceInReview;
