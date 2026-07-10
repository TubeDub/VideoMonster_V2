/* beta.js — автообновление, диагностика, отчёты об ошибках, отзывы */

(function () {
  'use strict';

  const FEEDBACK_KEY = 'vm_feedback_shown';
  const UPDATE_STATE_KEY = 'vm_update_state';
  let _updateState = null;
  let _updateChecking = false;
  let _updateApplying = false;

  /* ── Обновления (только вручную) ─────────────────── */
  async function loadUpdateState() {
    try {
      const r = await fetch('/api/system/update-state');
      const d = await r.json();
      _updateState = d;
      try { localStorage.setItem(UPDATE_STATE_KEY, JSON.stringify(d)); } catch (_) {}
      renderAllUpdateUI();
      return d;
    } catch (_) {
      try {
        const raw = localStorage.getItem(UPDATE_STATE_KEY);
        if (raw) {
          _updateState = JSON.parse(raw);
          renderAllUpdateUI();
          return _updateState;
        }
      } catch (e) { /* ignore */ }
      return null;
    }
  }

  function formatCheckDate(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch (_) { return ''; }
  }

  function renderAllUpdateUI() {
    document.querySelectorAll('[data-vm-update-widget]').forEach(renderUpdateWidget);
    renderUpdateBadge();
  }

  function renderUpdateWidget(slot) {
    if (!slot || !_updateState) return;
    const style = slot.dataset.vmUpdateStyle || 'default';
    const available = !!_updateState.update_available && _updateState.latest_version;
    const busy = typeof vmIsWorkBusy === 'function' && vmIsWorkBusy();
    const metaId = slot.dataset.vmUpdateMeta;
    const metaEl = metaId ? document.getElementById(metaId) : slot.querySelector('.vm-update-meta');

    slot.innerHTML = '';
    let btn;

    if (available) {
      btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-primary vm-update-btn-apply';
      btn.textContent = '🟢 Обновить до версии ' + _updateState.latest_version;
      btn.disabled = busy || _updateApplying;
      btn.addEventListener('click', () => applyAvailableUpdate(btn));
    } else {
      btn = document.createElement('button');
      btn.type = 'button';
      btn.className = style === 'owner' ? 'btn btn-secondary btn-sm' : 'btn btn-secondary';
      btn.textContent = '⬆ Проверить обновления';
      btn.disabled = _updateChecking;
      btn.addEventListener('click', () => manualCheckUpdates(btn));
    }
    slot.appendChild(btn);

    if (metaEl) {
      const parts = [];
      if (_updateState.last_checked_at) {
        parts.push('Последняя проверка: ' + formatCheckDate(_updateState.last_checked_at));
      }
      if (_updateState.last_error && !_updateState.last_check_ok) {
        parts.push('Ошибка: ' + _updateState.last_error);
      } else if (!available && _updateState.last_check_ok && _updateState.last_checked_at) {
        parts.push('Установлена актуальная версия');
      }
      metaEl.textContent = parts.join(' · ');
    }
  }

  function _bindUpdateBadge(el) {
    if (!el || !_updateState) return;
    if (_updateState.update_available && _updateState.latest_version) {
      el.style.display = el.id === 'sidebar-update-hint' ? 'flex' : 'inline-flex';
      const ver = _updateState.latest_version;
      el.textContent = el.id === 'sidebar-update-hint'
        ? '🟢 Обновить ' + ver
        : '⬆ Доступно обновление ' + ver;
      el.title = 'Нажмите, чтобы обновить до ' + ver;
      el.onclick = () => {
        if (typeof vmIsWorkBusy === 'function' && vmIsWorkBusy()) {
          vmNotify('Дождитесь завершения текущей обработки', 'warning');
          return;
        }
        applyAvailableUpdate(el);
      };
    } else {
      el.style.display = 'none';
      el.onclick = null;
    }
  }

  function renderUpdateBadge() {
    _bindUpdateBadge(document.getElementById('vm-update-badge'));
    _bindUpdateBadge(document.getElementById('sidebar-update-hint'));
  }

  async function manualCheckUpdates(triggerBtn) {
    if (_updateChecking) return;
    if (typeof vmIsWorkBusy === 'function' && vmIsWorkBusy()) {
      vmNotify('Сначала дождитесь завершения дубляжа или обработки', 'warning', 4000);
      return;
    }
    _updateChecking = true;
    if (triggerBtn) {
      triggerBtn.disabled = true;
      triggerBtn.textContent = 'Проверка…';
    }
    try {
      const r = await fetch('/api/system/check-updates', { method: 'POST' });
      const d = await r.json();
      if (d.state) {
        _updateState = d.state;
        try { localStorage.setItem(UPDATE_STATE_KEY, JSON.stringify(d.state)); } catch (_) {}
      } else if (d.ok !== false) {
        await loadUpdateState();
      }
      renderAllUpdateUI();

      if (!r.ok || d.ok === false) {
        vmNotify(vmFriendlyError(d.error || d.last_error || 'Не удалось проверить обновления'), 'error');
        return d;
      }
      if (d.update_available) {
        vmNotify('Доступна версия ' + d.latest, 'success', 5000);
      } else {
        vmNotify('У вас установлена актуальная версия', 'success', 3500);
      }
      return d;
    } catch (e) {
      vmNotify(vmFriendlyError(e.message), 'error');
      return null;
    } finally {
      _updateChecking = false;
      renderAllUpdateUI();
    }
  }

  async function applyAvailableUpdate(triggerBtn) {
    if (_updateApplying) return;
    if (typeof vmIsWorkBusy === 'function' && vmIsWorkBusy()) {
      vmNotify('Обновление нельзя запускать во время дубляжа или обработки видео', 'warning', 5000);
      return;
    }
    if (!_updateState || !_updateState.update_available) {
      vmNotify('Сначала проверьте обновления', 'info');
      return;
    }

    const ver = _updateState.latest_version || '';
    const notes = _updateState.notes ? '\n\n' + _updateState.notes : '';
    if (!confirm('Установить TubeDub ' + ver + '?' + notes)) return;

    _updateApplying = true;
    if (triggerBtn) triggerBtn.disabled = true;
    vmNotify('Загрузка обновления…', 'info', 8000);

    try {
      const r = await fetch('/api/system/apply-update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ download_url: _updateState.download_url || '' }),
      });
      const ad = await r.json();
      if (ad.ok) {
        vmNotify(ad.message || 'Установщик запущен. Перезапустите TubeDub после завершения.', 'success', 12000);
        if (_updateState) {
          _updateState.update_pending_install = true;
          try { localStorage.setItem(UPDATE_STATE_KEY, JSON.stringify(_updateState)); } catch (_) {}
        }
      } else {
        vmNotify(vmFriendlyError(ad.error || 'Не удалось установить обновление'), 'error');
      }
      return ad;
    } catch (e) {
      vmNotify(vmFriendlyError(e.message), 'error');
      return null;
    } finally {
      _updateApplying = false;
      renderAllUpdateUI();
    }
  }

  /* ── Диагностика ────────────────────────────────── */
  async function runSystemDiagnostics(containerId) {
    const box = containerId ? document.getElementById(containerId) : null;
    if (box) {
      box.innerHTML = '<div style="color:var(--text2);font-size:13px;">Проверка системы…</div>';
    }
    try {
      const r = await fetch('/api/system/diagnostics');
      const d = await r.json();
      if (box) renderDiagnostics(box, d);
      return d;
    } catch (e) {
      if (box) box.innerHTML = `<div style="color:var(--danger);">${esc(e.message)}</div>`;
      throw e;
    }
  }

  function renderDiagnostics(container, d) {
    const problems = (d.problems || []).map(p =>
      `<li><strong>${esc(p.label)}</strong>${p.detail ? ` — ${esc(p.detail)}` : ''}${p.hint ? `<br><span style="color:var(--text2);font-size:11px;">${esc(p.hint)}</span>` : ''}</li>`
    ).join('');

    const checks = (d.checks || []).map(c => {
      const icon = c.ok ? '🟢' : (c.critical ? '🔴' : '🟡');
      return `<div style="display:flex;gap:8px;align-items:flex-start;font-size:13px;margin-bottom:6px;">
        <span>${icon}</span>
        <div><strong>${esc(c.label)}</strong><br><span style="color:var(--text2);font-size:11px;">${esc(c.detail || '')}</span></div>
      </div>`;
    }).join('');

    container.innerHTML = `
      <div style="font-size:15px;font-weight:600;margin-bottom:12px;">${esc(d.summary || '')}</div>
      ${problems ? `<ul style="margin:0 0 14px 18px;color:var(--text);font-size:13px;">${problems}</ul>` : ''}
      <div style="border-top:1px solid var(--border);padding-top:12px;">${checks}</div>
      <div style="font-size:11px;color:var(--text2);margin-top:12px;">${esc(d.app?.display || '')} · ${esc(d.platform || '')}</div>
    `;
  }

  /* ── Отчёт об ошибке ────────────────────────────── */
  async function submitErrorReport(opts) {
    opts = opts || {};
    const comment = opts.comment;
    let userComment = comment;
    if (userComment === undefined) {
      userComment = prompt(
        'Опишите проблему (необязательно):\n\nБудут приложены версия TubeDub, Windows, логи и pipeline_audit.',
        opts.defaultComment || ''
      );
      if (userComment === null) return null;
    }

    vmNotify('Сбор отчёта…', 'info', 5000);
    const taskId = opts.taskId || window.__vmLastTaskId || null;
    if (taskId) {
      try {
        await fetch(`/api/auto_dub/diagnostics/${encodeURIComponent(taskId)}/zip`, { method: 'HEAD' });
      } catch (_) { /* ensure archive via GET on download */ }
    }
    const body = {
      task_id: opts.taskId || window.__vmLastTaskId || null,
      error_message: opts.errorMessage || window.__vmLastError || '',
      comment: userComment || '',
      page: window.location.pathname,
      diagnostic: opts.diagnostic || window.__vmLastDiagnostic || '',
    };

    const r = await fetch('/api/support/error-report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'report failed');

    vmNotify('Отчёт сохранён. Скачайте архив и отправьте разработчику.', 'success', 6000);
    if (d.download_url) {
      const a = document.createElement('a');
      a.href = d.download_url;
      a.download = d.filename || 'error_report.zip';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
    return d;
  }

  /* ── Отзыв после дубляжа ────────────────────────── */
  function showFeedbackModal(taskId) {
    if (document.getElementById('vm-feedback-modal')) return;
    const key = FEEDBACK_KEY + ':' + (taskId || 'last');
    if (sessionStorage.getItem(key)) return;

    const overlay = document.createElement('div');
    overlay.id = 'vm-feedback-modal';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10000;display:flex;align-items:center;justify-content:center;padding:16px;';
    overlay.innerHTML = `
      <div style="background:var(--panel,#1a1a2e);border:1px solid var(--border);border-radius:14px;padding:24px;max-width:420px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,.5);">
        <h3 style="margin:0 0 8px;font-size:17px;">Как прошёл дубляж?</h3>
        <p style="margin:0 0 14px;font-size:12px;color:var(--text2);">Ваш отзыв поможет улучшить TubeDub в бета-тестировании.</p>
        <div id="vm-feedback-stars" style="font-size:28px;letter-spacing:4px;cursor:pointer;margin-bottom:14px;">☆☆☆☆☆</div>
        <label style="font-size:12px;color:var(--text2);">Что понравилось?</label>
        <textarea id="vm-feedback-liked" rows="2" style="width:100%;margin:4px 0 10px;resize:vertical;" class="input-control"></textarea>
        <label style="font-size:12px;color:var(--text2);">Что можно улучшить?</label>
        <textarea id="vm-feedback-improve" rows="2" style="width:100%;margin:4px 0 14px;resize:vertical;" class="input-control"></textarea>
        <div style="display:flex;gap:8px;justify-content:flex-end;">
          <button type="button" class="btn btn-secondary" id="vm-feedback-skip">Пропустить</button>
          <button type="button" class="btn btn-primary" id="vm-feedback-send">Отправить</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    let stars = 0;
    const starsEl = overlay.querySelector('#vm-feedback-stars');
    function paintStars(n) {
      starsEl.textContent = '★'.repeat(n) + '☆'.repeat(5 - n);
    }
    starsEl.addEventListener('mousemove', (e) => {
      const rect = starsEl.getBoundingClientRect();
      const n = Math.min(5, Math.max(1, Math.ceil((e.clientX - rect.left) / (rect.width / 5))));
      paintStars(n);
    });
    starsEl.addEventListener('click', (e) => {
      const rect = starsEl.getBoundingClientRect();
      stars = Math.min(5, Math.max(1, Math.ceil((e.clientX - rect.left) / (rect.width / 5))));
      paintStars(stars);
    });
    starsEl.addEventListener('mouseleave', () => paintStars(stars));

    function closeModal() {
      overlay.remove();
    }

    overlay.querySelector('#vm-feedback-skip').addEventListener('click', () => {
      sessionStorage.setItem(key, '1');
      closeModal();
    });

    overlay.querySelector('#vm-feedback-send').addEventListener('click', async () => {
      if (stars < 1) {
        vmNotify('Выберите оценку от 1 до 5 звёзд', 'warning');
        return;
      }
      try {
        await fetch('/api/beta/feedback', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            stars,
            liked: overlay.querySelector('#vm-feedback-liked').value,
            improve: overlay.querySelector('#vm-feedback-improve').value,
            task_id: taskId || null,
          }),
        });
        sessionStorage.setItem(key, '1');
        vmNotify('Спасибо за отзыв!', 'success');
        closeModal();
      } catch (e) {
        vmNotify(vmFriendlyError(e.message), 'error');
      }
    });
  }

  function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  window.vmCheckForUpdates = manualCheckUpdates;
  window.vmApplyUpdate = applyAvailableUpdate;
  window.vmLoadUpdateState = loadUpdateState;
  window.vmRefreshUpdateUI = renderAllUpdateUI;
  window.vmRunDiagnostics = runSystemDiagnostics;
  window.vmSubmitErrorReport = submitErrorReport;
  window.vmShowFeedbackModal = showFeedbackModal;

  document.addEventListener('DOMContentLoaded', () => {
    loadUpdateState();
  });
})();
