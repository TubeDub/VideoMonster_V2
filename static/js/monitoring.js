/* Monitoring Center dashboard (TZ #8) */
(function () {
  const DEV = window.VM_MONITORING_DEV === true;
  const POLL_MS = DEV ? 2000 : 3000;

  function $(id) { return document.getElementById(id); }
  function fmtEta(s) {
    if (!s || s <= 0) return '—';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return m > 0 ? `${m}м ${sec}с` : `${sec}с`;
  }
  function setBar(id, pct) {
    const el = $(id);
    if (el) el.style.width = Math.min(100, pct) + '%';
  }

  async function fetchJson(url) {
    const r = await fetch(url);
    return r.json();
  }

  async function refresh() {
    try {
      const dashUrl = '/api/monitor/dashboard' + (DEV ? '?developer=1' : '');
      const [dashRes, resRes] = await Promise.all([
        fetchJson(dashUrl),
        fetchJson('/api/monitor/resources'),
      ]);
      if (!dashRes.ok) return;
      const d = dashRes.dashboard || {};

      $('mc-progress').textContent = (d.progress_percent || 0).toFixed(0) + '%';
      setBar('mc-progress-bar', d.progress_percent || 0);
      $('mc-eta').textContent = fmtEta(d.eta_seconds);
      $('mc-stage').textContent = d.current_stage || '—';
      $('mc-speed').textContent = (d.processing_speed || 0).toFixed(2) + ' seg/s';

      const warns = $('mc-warnings');
      warns.innerHTML = '';
      (d.warnings || []).forEach(w => {
        const div = document.createElement('div');
        div.className = 'mc-warn';
        div.textContent = '⚠ ' + (w.message || w);
        warns.appendChild(div);
      });

      if (resRes.ok && resRes.resources) {
        const r = resRes.resources;
        $('mc-cpu').textContent = (r.cpu?.percent || 0).toFixed(0) + '%';
        $('mc-gpu').textContent = (r.gpu?.percent || 0).toFixed(0) + '%';
        $('mc-ram').textContent = (r.ram?.percent || 0).toFixed(0) + '%';
        $('mc-vram').textContent = (r.gpu?.vram_percent || 0).toFixed(0) + '%';
        setBar('mc-cpu-bar', r.cpu?.percent || 0);
        setBar('mc-gpu-bar', r.gpu?.percent || 0);
        setBar('mc-ram-bar', r.ram?.percent || 0);
        setBar('mc-vram-bar', r.gpu?.vram_percent || 0);
      }

      if (!DEV) return;

      const [pipeRes, agentRes, modelRes, queueRes, diagRes, tlRes, plugRes] = await Promise.all([
        fetchJson('/api/monitor/pipeline'),
        fetchJson('/api/monitor/agents'),
        fetchJson('/api/monitor/models'),
        fetchJson('/api/monitor/queues'),
        fetchJson('/api/monitor/diagnostics'),
        fetchJson('/api/monitor/timeline?limit=50'),
        fetchJson('/api/plugins/diagnostics'),
      ]);

      renderPipeline(pipeRes.pipeline);
      renderAgents(agentRes.agents || []);
      renderModels(modelRes.models || []);
      renderQueues(queueRes.queues || []);
      renderBottleneck(diagRes.bottleneck || {});
      renderTimeline(tlRes.timeline || d.timeline || []);
      renderPlugins(plugRes.plugins || []);
    } catch (e) {
      console.warn('[monitoring]', e);
    }
  }

  const STAGES = ['whisper','cleaner','translator','review','timing','voice','mix','export'];

  function renderPipeline(pipe) {
    const el = $('mc-pipeline');
    if (!el || !pipe) return;
    el.innerHTML = '';
    const stageMap = {};
    (pipe.stages || []).forEach(s => { stageMap[s.stage] = s; });
    STAGES.forEach((name, i) => {
      const s = stageMap[name] || {};
      const div = document.createElement('div');
      div.className = 'mc-stage' + (s.errors > 0 ? ' err' : s.load_percent > 50 ? ' active' : '');
      div.title = `wait:${s.waiting||0} err:${s.errors||0} load:${s.load_percent||0}%`;
      div.textContent = name + (s.waiting ? ` (${s.waiting})` : '');
      el.appendChild(div);
      if (i < STAGES.length - 1) {
        const arr = document.createElement('span');
        arr.className = 'mc-arrow';
        arr.textContent = '↓';
        el.appendChild(arr);
      }
    });
  }

  function renderAgents(agents) {
    const el = $('mc-agents');
    if (!el) return;
    el.innerHTML = agents.map(a =>
      `<div style="padding:3px 0">${a.name}: <b>${a.state}</b> · ${a.chunks_processed} chunks · ${a.success_rate}% ok</div>`
    ).join('') || '<div>Нет активных агентов</div>';
  }

  function renderModels(models) {
    const el = $('mc-models');
    if (!el) return;
    el.innerHTML = models.map(m =>
      `<div style="padding:3px 0">${m.name}: ${m.in_use ? '🟢' : '⚪'} ${m.avg_latency_ms}ms · ${m.requests} req · ${m.errors} err</div>`
    ).join('') || '<div>Нет моделей</div>';
  }

  function renderQueues(queues) {
    const el = $('mc-queues');
    if (!el) return;
    el.innerHTML = queues.map(q =>
      `<div style="padding:3px 0">${q.name}: ${q.current_size}/${q.max_size||'?'} · wait ${q.avg_wait_s}s · peak ${q.peak_load}</div>`
    ).join('') || '<div>Очереди пусты</div>';
  }

  function renderBottleneck(bn) {
    const el = $('mc-bottleneck');
    if (!el) return;
  const stages = bn.stages || [];
    el.innerHTML = stages.slice(0, 5).map(s =>
      `<div style="padding:3px 0">${s.label}: <b>${s.percent}%</b> (${s.duration_s}s)</div>`
    ).join('') || '<div>Нет данных</div>';
    if (bn.primary) {
      el.innerHTML = `<div style="margin-bottom:6px;color:#f59e0b">⚡ ${bn.primary}: ${bn.primary_percent}%</div>` + el.innerHTML;
    }
  }

  function renderPlugins(plugins) {
    let el = document.getElementById('mc-plugins');
    if (!el) {
      const grid = document.querySelector('.mc-dev-grid');
      if (!grid) return;
      const card = document.createElement('div');
      card.className = 'mc-card mc-dev';
      card.innerHTML = '<h3 style="font-size:14px;">Plugins</h3><div id="mc-plugins" style="font-size:12px;"></div>';
      grid.appendChild(card);
      el = document.getElementById('mc-plugins');
    }
    if (!el) return;
    el.innerHTML = plugins.map(p =>
      `<div style="padding:3px 0">${p.name}: ${p.state} · ${(p.capabilities||[]).join(', ')}</div>`
    ).join('') || '<div>Нет плагинов</div>';
  }

  function renderTimeline(items) {
    const el = $('mc-timeline');
    if (!el) return;
    el.innerHTML = items.slice(-30).map(t =>
      `<div class="mc-tl-row"><span class="mc-tl-time">${t.time||''}</span><span>${t.message||''}</span></div>`
    ).join('');
  }

  async function exportReport(fmt) {
    const r = await fetch('/api/monitor/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ format: fmt }),
    });
    if (fmt === 'zip') {
      const j = await r.json();
      if (j.ok && j.path) alert('Отчёт сохранён: ' + j.path);
      return;
    }
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'diagnostic_report.' + fmt;
    a.click();
  }

  $('mc-export-zip')?.addEventListener('click', () => exportReport('zip'));
  $('mc-export-json')?.addEventListener('click', () => exportReport('json'));
  $('mc-export-html')?.addEventListener('click', () => exportReport('html'));

  refresh();
  setInterval(refresh, POLL_MS);
})();
