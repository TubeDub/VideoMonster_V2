/* Plugin management + local marketplace (TZ #9) */
(function () {
  async function load() {
    const summary = document.getElementById('plg-summary');
    const list = document.getElementById('plg-list');
    try {
      const r = await fetch('/api/plugins/status');
      const j = await r.json();
      if (!j.ok) {
        if (summary) summary.textContent = j.error || 'Plugins API unavailable';
        if (list) list.innerHTML = '<p class="subtitle">Не удалось загрузить плагины</p>';
        loadCatalog();
        return;
      }
      const st = j.status || {};
      if (summary) {
        summary.textContent =
          `API ${st.core_api_version || '?'} · ${st.enabled_count || 0}/${st.total || 0} активны`;
      }
      render(st.plugins || []);
    } catch (e) {
      console.warn('[plugins]', e);
      if (summary) summary.textContent = String(e);
      if (list) list.innerHTML = '<p class="subtitle">Ошибка загрузки</p>';
    }
    loadCatalog();
  }

  function cardHtml(p, opts) {
    opts = opts || {};
    const caps = (p.capabilities || []).map(function (c) {
      return `<span class="plg-cap">${c}</span>`;
    }).join('');
    const installed = !!p.installed;
    const name = p.name || p.id || '';
    const label = p.label || name;
    const actions = opts.marketplace
      ? (installed
          ? `<button class="btn" data-mp="remove" data-name="${name}">Remove</button>`
          : `<button class="btn btn-primary" data-mp="install" data-source="${p.path || p.source_hint || ''}" data-name="${name}">Install</button>`)
      : '';
    return `<div class="plg-card">
      <h3>${label}</h3>
      <div class="plg-meta">v${p.version || '?'} · ${installed ? 'installed' : 'available'}</div>
      <p class="subtitle" style="font-size:11px;margin:4px 0;">${p.description || ''}</p>
      <div class="plg-caps">${caps}</div>
      <div class="plg-actions">${actions}</div>
    </div>`;
  }

  async function loadCatalog() {
    const el = document.getElementById('plg-catalog');
    if (!el) return;
    try {
      const r = await fetch('/api/plugins/marketplace/catalog');
      const j = await r.json();
      if (!j.ok && j.error) {
        el.innerHTML = `<p class="subtitle">${j.error}</p>`;
        return;
      }
      const packages = j.packages || [];
      const curated = j.catalog || [];
      const seen = new Set(packages.map(function (p) { return p.name; }));
      const merged = packages.slice();
      curated.forEach(function (c) {
        const n = c.name || c.id;
        if (n && !seen.has(n)) {
          seen.add(n);
          merged.push(c);
        }
      });
      if (!merged.length) {
        el.innerHTML = '<p class="subtitle">Каталог пуст — положите плагины в plugins/ или data/plugin_marketplace_catalog.json</p>';
        return;
      }
      el.innerHTML = merged.map(function (p) { return cardHtml(p, { marketplace: true }); }).join('');
      el.querySelectorAll('button[data-mp]').forEach(function (btn) {
        btn.addEventListener('click', async function () {
          const action = btn.dataset.mp;
          const body = action === 'install'
            ? { source: btn.dataset.source, name: btn.dataset.name }
            : { name: btn.dataset.name };
          const res = await fetch(`/api/plugins/marketplace/${action}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
          const data = await res.json();
          if (!data.ok && window.showToast) {
            window.showToast(data.error || 'Marketplace error', 'error');
          } else if (!data.ok && window.vmNotify) {
            window.vmNotify(data.error || 'Marketplace error', 'error');
          }
          load();
        });
      });
    } catch (e) {
      console.warn('[plugins catalog]', e);
      el.innerHTML = `<p class="subtitle">${String(e)}</p>`;
    }
  }

  function render(plugins) {
    const el = document.getElementById('plg-list');
    if (!el) return;
    const rows = plugins || [];
    el.innerHTML = rows.map(function (p) {
      const state = p.state || 'unknown';
      const caps = (p.capabilities || []).map(function (c) {
        return `<span class="plg-cap">${c}</span>`;
      }).join('');
      const enabled = state === 'enabled';
      const author = (p.manifest && p.manifest.author) || '';
      return `<div class="plg-card">
        <h3>${p.name || '?'}</h3>
        <div class="plg-meta">v${p.version || '?'} · ${author}</div>
        <div class="plg-state ${state}">${String(state).toUpperCase()}${p.error ? ' — ' + p.error : ''}</div>
        <div class="plg-caps">${caps}</div>
        <div class="plg-actions">
          ${enabled
            ? `<button class="btn" data-action="disable" data-name="${p.name}">Отключить</button>`
            : `<button class="btn btn-primary" data-action="enable" data-name="${p.name}">Включить</button>`}
          <button class="btn" data-action="reload" data-name="${p.name}">Reload</button>
        </div>
      </div>`;
    }).join('') || '<p class="subtitle">Нет загруженных плагинов</p>';

    el.querySelectorAll('button[data-action]').forEach(function (btn) {
      btn.addEventListener('click', async function () {
        const name = btn.dataset.name;
        const action = btn.dataset.action;
        await fetch(`/api/plugins/${name}/${action}`, { method: 'POST' });
        load();
      });
    });
  }

  document.getElementById('plg-refresh')?.addEventListener('click', load);
  document.getElementById('plg-install-btn')?.addEventListener('click', async function () {
    const source = (document.getElementById('plg-source')?.value || '').trim();
    const name = (document.getElementById('plg-name')?.value || '').trim();
    if (!source) return;
    const res = await fetch('/api/plugins/marketplace/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: source, name: name }),
    });
    const data = await res.json();
    if (!data.ok && window.showToast) {
      window.showToast(data.error || 'Install failed', 'error');
    } else if (!data.ok && window.vmNotify) {
      window.vmNotify(data.error || 'Install failed', 'error');
    }
    load();
  });
  load();
})();
