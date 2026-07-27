/* main.js — общие утилиты VideoMonster */

/* ══════════════════════════════════════════
   УВЕДОМЛЕНИЯ (Toast / Notify)
══════════════════════════════════════════ */
let _notifyContainer = null;

function vmNotify(message, type = 'info', duration = 4000) {
  if (!_notifyContainer) {
    _notifyContainer = document.getElementById('vm-notify-container');
  }
  if (!_notifyContainer) return;

  const icons   = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
  const borders = { success: '#34d399', error: '#f87171', warning: '#fbbf24', info: '#60a5fa' };
  const color   = borders[type] || borders.info;

  const toast = document.createElement('div');
  toast.className = 'vm-toast';
  toast.style.cssText = [
    'display:flex;align-items:flex-start;gap:10px',
    'background:rgba(13,17,23,.92)',
    'backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px)',
    `border:1px solid rgba(255,255,255,.08);border-left:3px solid ${color}`,
    'border-radius:12px;padding:13px 16px',
    'min-width:260px;max-width:380px',
    `box-shadow:0 8px 32px rgba(0,0,0,.5),0 0 0 0 ${color}22`,
    'animation:vmToastIn .25s cubic-bezier(.4,0,.2,1)',
    'cursor:pointer;transition:opacity .2s',
  ].join(';');

  toast.innerHTML =
    `<span style="font-size:18px;line-height:1.2">${icons[type]}</span>` +
    `<span style="flex:1;font-size:13px;line-height:1.5;color:var(--text,#e8e8f5)">${message}</span>` +
    `<span style="font-size:18px;opacity:.5;padding-left:4px" onclick="event.stopPropagation();this.closest('.vm-toast').remove()">×</span>`;

  toast.addEventListener('click', () => _dismissToast(toast));
  _notifyContainer.appendChild(toast);

  if (duration > 0) {
    setTimeout(() => _dismissToast(toast), duration);
  }
  return toast;
}

function _dismissToast(toast) {
  if (!toast || !toast.parentNode) return;
  toast.style.animation = 'vmToastOut .2s ease forwards';
  setTimeout(() => { if (toast.parentNode) toast.remove(); }, 220);
}

/* ══════════════════════════════════════════
   ПРОСТОЙ / ПРОФЕССИОНАЛЬНЫЙ РЕЖИМ
══════════════════════════════════════════ */
function getMode()    { return localStorage.getItem('vm_mode') || 'simple'; }
function isProMode()  { const m = getMode(); return m === 'pro' || m === 'dev'; }
function isDevMode()  { return getMode() === 'dev'; }

/* ══════════════════════════════════════════
   БЛОКИРОВКА ОБНОВЛЕНИЙ ВО ВРЕМЯ РАБОТЫ
══════════════════════════════════════════ */
let _workBusyCount = 0;

function vmSetWorkBusy(on) {
  _workBusyCount = Math.max(0, _workBusyCount + (on ? 1 : -1));
  window.__vmWorkBusy = _workBusyCount > 0;
  if (typeof window.vmRefreshUpdateUI === 'function') window.vmRefreshUpdateUI();
}

function vmIsWorkBusy() {
  if (_workBusyCount > 0 || window.__vmWorkBusy) return true;
  try {
    if (localStorage.getItem('vm_active_task')) return true;
  } catch (_) { /* ignore */ }
  return false;
}

const MODE_LABELS = {
  simple: { btn: 'Просто', title: 'Режим пользователя — только основные функции' },
  pro:    { btn: 'Про',   title: 'Профессиональный режим — расширенные настройки' },
  dev:    { btn: 'Dev',   title: 'Режим разработчика — логи, pipeline, диагностика' },
};

function setMode(mode) {
  if (!MODE_LABELS[mode]) mode = 'simple';
  localStorage.setItem('vm_mode', mode);
  syncModeCookies(mode);
  applyMode(mode);
}

function syncModeCookies(mode) {
  if (!MODE_LABELS[mode]) mode = 'simple';
  const maxAge = 60 * 60 * 24 * 400;
  const dev = mode === 'dev' ? '1' : '0';
  const userMode = mode === 'dev' ? 'developer' : (mode === 'pro' ? 'pro' : 'basic');
  document.cookie = `vm_client_dev_mode=${dev};path=/;max-age=${maxAge};SameSite=Lax`;
  document.cookie = `vm_user_mode=${userMode};path=/;max-age=${maxAge};SameSite=Lax`;
}

function applyMode(mode) {
  if (!MODE_LABELS[mode]) mode = 'simple';
  document.body.classList.toggle('mode-pro',    mode === 'pro' || mode === 'dev');
  document.body.classList.toggle('mode-dev',    mode === 'dev');
  document.body.classList.toggle('mode-simple', mode === 'simple');
  const lbl = MODE_LABELS[mode] || MODE_LABELS.simple;
  const btn = document.getElementById('mode-toggle-btn');
  if (btn) {
    btn.textContent = lbl.btn;
    btn.title = lbl.title;
  }
  const sbBtn = document.getElementById('sidebar-mode-btn');
  if (sbBtn) sbBtn.textContent = lbl.btn;
}

function toggleMode() {
  const order = ['simple', 'pro', 'dev'];
  const cur = getMode();
  const idx = order.indexOf(cur);
  const next = order[(idx + 1) % order.length];
  setMode(next);
  const names = { simple: 'Режим пользователя', pro: 'Профессиональный режим', dev: 'Режим разработчика' };
  vmNotify(names[next] || 'Режим изменён', 'info', 2500);
}

/* ══════════════════════════════════════════
   АВТОСОХРАНЕНИЕ
══════════════════════════════════════════ */
function autoSaveData(key, data) {
  try {
    const payload = { data, savedAt: Date.now() };
    localStorage.setItem('vm_autosave_' + key, JSON.stringify(payload));
  } catch (e) { /* full storage */ }
}

function autoLoadData(key) {
  try {
    const raw = localStorage.getItem('vm_autosave_' + key);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch { return null; }
}

function clearAutoSave(key) {
  localStorage.removeItem('vm_autosave_' + key);
}

/* ══════════════════════════════════════════
   ДРУЖЕСТВЕННЫЕ ОШИБКИ (Правило 8, VM6)
   Пользователь не должен видеть Python-ошибки
══════════════════════════════════════════ */
function vmFriendlyError(msg) {
  if (!msg) return 'Что-то пошло не так. Попробуйте ещё раз.';
  const m = String(msg);

  // Audio extraction diagnostics (PIPELINE_CRITICAL / extract_audio)
  if (
    m.includes('FFmpeg не найден') ||
    m.includes('нет аудиодорожки') ||
    m.includes('Не удалось записать файл') ||
    m.includes('Скачайте диагностику') ||
    (m.includes('PIPELINE_CRITICAL') && m.includes('Audio Extraction')) ||
    (m.includes('code=FFMPEG_') && m.includes('stage=Audio Extraction'))
  ) {
    if (m.includes('Скачайте диагностику')) return m;
    if (m.includes('FFmpeg не найден')) return m + ' Скачайте диагностику (ZIP) для подробностей.';
    if (m.includes('нет аудиодорожки')) return m + ' Проверьте исходное видео или скачайте диагностику (ZIP).';
    return m + ' Скачайте диагностику (ZIP) для подробностей.';
  }

  // Voice clone readiness — show «нужен движок», not bare 503
  if (
    m.includes('CLONE_ENGINE_MISSING') ||
    m.includes('voice_clone_unavailable') ||
    m.includes('Клонирование голоса недоступно') ||
    m.includes('нужен движок') ||
    m.includes('Voice cloning adapter unavailable') ||
    m.includes('Voice cloning unavailable')
  ) {
    if (m.includes('нужен движок') || m.includes('Клонирование голоса')) return m;
    return 'Клонирование голоса недоступно — нужен движок xtts/coqui, openvoice, fishspeech или cosyvoice';
  }

  // TTS / Pipeline diagnostics v1.0 — never replace with generic message
  if (
    m.includes('DubbingError stage=') ||
    m.includes('VoiceGenerationError') ||
    m.includes('segment_id=') ||
    m.startsWith('TTS ошибка') ||
    m.startsWith('TTS error') ||
    m.startsWith('TTS помилка') ||
    m.startsWith('Ошибка дубляжа') ||
    (m.includes('engine=') && m.includes('segment'))
  ) {
    return m;
  }

  // Python traceback — скрыть полностью
  if (m.includes('Traceback') || m.includes('File "') || m.includes('  line '))
    return 'Внутренняя ошибка. Попробуйте ещё раз или перезапустите приложение.';

  // Файл не найден
  if (m.includes('FileNotFoundError') || m.includes('No such file') || m.includes('not found'))
    return 'Файл не найден. Возможно, он был удалён или перемещён.';

  // FFmpeg
  if (m.toLowerCase().includes('ffmpeg'))
    return 'Ошибка обработки видео. Убедитесь, что FFmpeg установлен (ffmpeg.org).';

  // Whisper / STT
  if (m.includes('whisper') || m.includes('faster_whisper'))
    return 'Распознавание речи недоступно. Установите faster-whisper: pip install faster-whisper';

  // Сегменты / тайминг
  if (m.includes('split_by_timing_map') || m.includes('timing_map=') || m.includes('segment_mismatch'))
    return 'Не удалось согласовать перевод с таймингом видео. Попробуйте ещё раз или выберите модель Whisper «tiny».';

  // TTS timeout
  if (m.includes('TTS timeout') || m.includes('tts_timeout') || m.includes('превысил лимит'))
    return 'Озвучка заняла слишком много времени. Проверьте интернет и попробуйте снова.';

  // Сеть / Edge-TTS
  if (m.includes('edge_tts') || m.includes('edge-tts') || m.includes('ClientConnectorError'))
    return 'Ошибка озвучки. Проверьте интернет-соединение.';

  // Переводчик
  if (m.includes('Translator') || m.includes('googletrans') || m.includes('deep_translator'))
    return 'Ошибка перевода. Проверьте интернет-соединение.';

  // Сетевые ошибки JS
  if (m.includes('Failed to fetch') || m.includes('NetworkError') || m.includes('ECONNREFUSED'))
    return 'Нет связи с сервером. Проверьте, запущено ли приложение.';

  // Права доступа
  if (m.includes('PermissionError') || m.includes('Access is denied'))
    return 'Нет прав доступа к файлу. Попробуйте выбрать другую папку.';

  // Память
  if (m.includes('MemoryError') || m.includes('out of memory') || m.includes('Cannot allocate'))
    return 'Недостаточно памяти. Попробуйте файл меньшего размера.';

  // Технические пути и имена файлов — скрыть, но не generic-only
  if (m.match(/Error:|Exception:|error:/i) && m.length > 120)
    return 'Ошибка дубляжа. Нажмите «Подробнее» для технических сведений.';

  // Если строка короткая и на русском/понятном — показать как есть
  if (m.length < 100) return m;

  return 'Ошибка дубляжа. Нажмите «Подробнее» для технических сведений.';
}

/* ══════════════════════════════════════════
   БАЗОВЫЕ УТИЛИТЫ
══════════════════════════════════════════ */
function setStatus(msg, id = 'status-text') {
  const el = document.getElementById(id);
  if (el) el.textContent = msg;
}

function updateCount(textareaId, countId) {
  const ta  = document.getElementById(textareaId);
  const cnt = document.getElementById(countId);
  if (ta && cnt) cnt.textContent = ta.value.length + ' симв.';
}

async function pasteText(targetId) {
  try {
    const text = await navigator.clipboard.readText();
    const el   = document.getElementById(targetId);
    if (el) { el.value = text; el.dispatchEvent(new Event('input')); }
  } catch {
    setStatus('Нажмите Ctrl+V для вставки');
  }
}

function copyText(sourceId) {
  const el = document.getElementById(sourceId);
  if (!el || !el.value.trim()) return;
  navigator.clipboard.writeText(el.value).then(() => {
    vmNotify('Скопировано в буфер обмена', 'success', 2000);
  });
}

function closeAudio() {
  const s = document.getElementById('audio-section');
  if (s) s.style.display = 'none';
}

function showAudioResult(data) {
  const section    = document.getElementById('audio-section');
  const container  = document.getElementById('audio-players');
  if (!section || !container) return;

  container.innerHTML = '';
  const files     = data.files     || [];
  const streams   = data.streams   || (data.stream   ? [data.stream]   : []);
  const downloads = data.downloads || (data.download ? [data.download] : []);

  files.forEach((f, i) => {
    const wrapper  = document.createElement('div');
    wrapper.className = 'audio-item';

    const label  = document.createElement('span');
    label.className   = 'audio-label';
    label.textContent = files.length > 1 ? `Сегм. ${i + 1}` : 'Аудио';

    const audio  = document.createElement('audio');
    audio.controls = true;
    audio.className = 'audio-player';
    audio.src = streams[i] || `/api/stream/${f}`;

    const dl  = document.createElement('a');
    dl.href       = downloads[i] || `/api/download/${f}`;
    dl.download   = '';
    dl.className  = 'btn btn-sm';
    dl.textContent = '⬇ Скачать';

    wrapper.append(label, audio, dl);
    container.appendChild(wrapper);
  });

  section.style.display = 'block';
}

function downloadTextBlob(text, filename = 'document.txt') {
  const blob = new Blob([text], { type: 'text/plain; charset=utf-8' });
  const a    = document.createElement('a');
  a.href     = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function vmUniversalImport() {
  const input = document.getElementById('vm-universal-import-input');
  if (!input) return;
  input.value = '';
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/import/upload', { method: 'POST', body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'upload failed');
      const route = d.route || '/';
      if (d.import_id) {
        sessionStorage.setItem('vm_import_file', JSON.stringify({
          import_id: d.import_id,
          route,
          filename: d.filename || file.name,
        }));
      }
      const qs = d.import_id ? `?import=${encodeURIComponent(d.import_id)}` : '';
      window.location.href = `${route}${qs}`;
    } catch (e) {
      vmNotify(vmFriendlyError(e.message), 'error');
    }
  };
  input.click();
}

async function vmConsumeUniversalImport(handlers) {
  const params = new URLSearchParams(window.location.search);
  let importId = params.get('import');
  if (!importId) {
    try {
      const raw = sessionStorage.getItem('vm_import_file');
      if (raw) {
        const stored = JSON.parse(raw);
        importId = stored.import_id || null;
      }
    } catch (_) { /* ignore */ }
  }
  if (!importId) return null;

  try {
    const r = await fetch(`/api/import/load/${encodeURIComponent(importId)}`);
    const d = await r.json();
    if (!r.ok || d.error) {
      const msg = d.error || `HTTP ${r.status}`;
      if (typeof vmNotify === 'function') {
        vmNotify(typeof vmFriendlyError === 'function' ? vmFriendlyError(msg) : msg, 'error');
      }
      return null;
    }

    const handler = handlers && handlers[d.kind];
    if (typeof handler === 'function') await handler(d);

    sessionStorage.removeItem('vm_import_file');
    if (params.has('import')) {
      const url = new URL(window.location.href);
      url.searchParams.delete('import');
      const qs = url.searchParams.toString();
      window.history.replaceState({}, '', url.pathname + (qs ? `?${qs}` : ''));
    }
    return d;
  } catch (e) {
    const msg = e && e.message ? e.message : String(e);
    if (typeof vmNotify === 'function') {
      vmNotify(typeof vmFriendlyError === 'function' ? vmFriendlyError(msg) : msg, 'error');
    }
    return null;
  }
}

function loadSettings()       { return JSON.parse(localStorage.getItem('vm_settings') || '{}'); }
function getDefaultVoice()    { return loadSettings().voice      || 'ru-RU-DmitryNeural'; }
function getDefaultTargetLang() { return loadSettings().targetLang || 'ru'; }
function getAutoClean()       { const s = loadSettings(); return s.autoClean !== undefined ? s.autoClean : true; }
function getTranslateMode()   { return loadSettings().translateMode || 'auto'; }

/* ══════════════════════════════════════════
   ИНИЦИАЛИЗАЦИЯ
══════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  const mode = getMode();
  syncModeCookies(mode);
  applyMode(mode);
});
