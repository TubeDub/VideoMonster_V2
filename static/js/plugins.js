/* Plugin management page (TZ #9) */
(function () {
  async function load() {
    try {
      const r = await fetch('/api/plugins/status');
      const j = await r.json();
      if (!j.ok) return;
      const st = j.status || {};
      document.getElementById('plg-summary').textContent =
        `API ${st.core_api_version || '?'} · ${st.enabled_count || 0}/${st.total || 0} активны`;
      render(st.plugins || []);
    } catch (e) {
      console.warn('[plugins]', e);
    }
  }

  function render(plugins) {
    const el = document.getElementById('plg-list');
    el.innerHTML = plugins.map(p => {
      const state = p.state || 'unknown';
      const caps = (p.capabilities || []).map(c =>
        `<span class="plg-cap">${c}</span>`).join('');
      const enabled = state === 'enabled';
      return `<div class="plg-card">
        <h3>${p.name}</h3>
        <div class="plg-meta">v${p.version || '?'} · ${p.manifest?.author || ''}</div>
        <div class="plg-state ${state}">${state.toUpperCase()}${p.error ? ' — ' + p.error : ''}</div>
        <div class="plg-caps">${caps}</div>
        <div class="plg-actions">
          ${enabled
            ? `<button class="btn" data-action="disable" data-name="${p.name}">Отключить</button>`
            : `<button class="btn btn-primary" data-action="enable" data-name="${p.name}">Включить</button>`}
          <button class="btn" data-action="reload" data-name="${p.name}">Reload</button>
        </div>
      </div>`;
    }).join('');

    el.querySelectorAll('button[data-action]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const name = btn.dataset.name;
        const action = btn.dataset.action;
        await fetch(`/api/plugins/${name}/${action}`, { method: 'POST' });
        load();
      });
    });
  }

  document.getElementById('plg-refresh')?.addEventListener('click', load);
  load();
})();
