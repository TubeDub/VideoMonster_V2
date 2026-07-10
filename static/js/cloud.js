(function () {
  let _projects = [];
  let _pollTimer = null;

  function fmtBytes(n) {
    if (n == null || isNaN(n)) return "—";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
    return (n / 1024 / 1024 / 1024).toFixed(2) + " GB";
  }

  function fmtBps(n) {
    if (!n) return "—";
    return fmtBytes(n) + "/s";
  }

  async function cloudFetch(url, opts) {
    const r = await fetch(url, opts);
    return r.json();
  }

  function renderProviders(providers) {
    const el = document.getElementById("cloud-providers");
    if (!el) return;
    el.innerHTML = (providers || [])
      .map(function (p) {
        const dot = p.connected ? "#3ecf8e" : "#6b7280";
        const free = p.free_bytes != null ? fmtBytes(p.free_bytes) + " free" : "";
        const up = p.upload_bps ? fmtBps(p.upload_bps) : "";
        const dn = p.download_bps ? fmtBps(p.download_bps) : "";
        return (
          '<div style="padding:8px 0;border-bottom:1px solid var(--border);font-size:13px;">' +
          '<span class="cloud-dot" style="background:' +
          dot +
          '"></span><strong>' +
          (p.label || p.provider_id) +
          "</strong> " +
          (p.connected ? "✓" : p.error || "не подключено") +
          '<div style="font-size:11px;opacity:.7;margin-top:2px;">' +
          [free, up && "↑ " + up, dn && "↓ " + dn].filter(Boolean).join(" · ") +
          "</div></div>"
        );
      })
      .join("");
  }

  function renderStats(data) {
    const el = document.getElementById("cloud-stats");
    if (!el) return;
    const connected = (data.providers || []).filter(function (p) {
      return p.connected;
    }).length;
    el.innerHTML =
      stat("Проекты", data.projects_count || 0) +
      stat("Облака", connected + " / " + (data.providers || []).length) +
      stat("Очередь", (data.sync_queue || []).length) +
      stat("Режим", data.default_storage_mode || "local_only");
  }

  function stat(label, val) {
    return (
      '<div class="cloud-stat"><div style="font-size:11px;opacity:.7">' +
      label +
      '</div><div class="val">' +
      val +
      "</div></div>"
    );
  }

  function renderProjects(projects) {
    _projects = projects || [];
    cloudFilterProjects();
  }

  window.cloudFilterProjects = function () {
    const q = (document.getElementById("cloud-search")?.value || "").toLowerCase();
    const body = document.getElementById("cloud-projects-body");
    if (!body) return;
    const rows = _projects.filter(function (p) {
      return !q || (p.title || "").toLowerCase().includes(q) || (p.project_id || "").includes(q);
    });
    body.innerHTML = rows
      .map(function (p) {
        return (
          "<tr>" +
          "<td><strong>" +
          (p.title || p.project_id) +
          "</strong><br><code style='font-size:10px'>" +
          p.project_id.slice(0, 8) +
          "…</code></td>" +
          "<td>" +
          p.storage_mode +
          "</td>" +
          "<td>" +
          p.provider_id +
          "</td>" +
          "<td>" +
          p.sync_state +
          "</td>" +
          "<td>" +
          (p.versions || []).length +
          "</td></tr>"
        );
      })
      .join("");
  };

  function renderSyncQueue(tasks) {
    const body = document.getElementById("cloud-sync-body");
    if (!body) return;
    body.innerHTML = (tasks || [])
      .map(function (t) {
        const pct = Math.round((t.progress || 0) * 100);
        return (
          "<tr>" +
          "<td><code style='font-size:10px'>" +
          (t.task_id || "").slice(0, 8) +
          "…</code></td>" +
          "<td>" +
          t.kind +
          "</td>" +
          "<td>" +
          t.state +
          (t.error ? "<br><span style='color:#f56565;font-size:10px'>" + t.error + "</span>" : "") +
          "</td>" +
          "<td><div class='cloud-sync-bar'><div class='cloud-sync-fill' style='width:" +
          pct +
          "%'></div></div>" +
          pct +
          "%</td>" +
          "<td>" +
          fmtBps(t.speed_bps) +
          "</td></tr>"
        );
      })
      .join("");
  }

  function applySettings(settings) {
    if (!settings) return;
    const set = function (id, val) {
      const el = document.getElementById(id);
      if (el && val != null) el.value = val;
    };
    set("cloud-default-mode", settings.default_storage_mode);
    set("cloud-backup-schedule", settings.backup_schedule);
    set("cloud-cache-policy", settings.cache_policy);
    set("cloud-default-provider", settings.default_provider);
  }

  window.cloudSaveSettings = async function () {
    const payload = {
      default_storage_mode: document.getElementById("cloud-default-mode")?.value,
      backup_schedule: document.getElementById("cloud-backup-schedule")?.value,
      cache_policy: document.getElementById("cloud-cache-policy")?.value,
      default_provider: document.getElementById("cloud-default-provider")?.value,
    };
    const j = await cloudFetch("/api/cloud/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (j.ok) vmNotify("Настройки сохранены", "success", 2000);
    else vmNotify(j.error || "Error", "error");
  };

  window.cloudRunBackup = async function () {
    const j = await cloudFetch("/api/cloud/backup/run", { method: "POST" });
    if (j.ok) {
      vmNotify("Backup поставлен в очередь", "success");
      cloudRefresh();
    }
  };

  window.cloudRefresh = async function () {
    try {
      const j = await cloudFetch("/api/cloud/status");
      if (!j.ok) {
        document.getElementById("cloud-off").style.display = "block";
        document.getElementById("cloud-panel").style.display = "none";
        return;
      }
      document.getElementById("cloud-off").style.display = "none";
      document.getElementById("cloud-panel").style.display = "block";
      renderStats(j);
      renderProviders(j.providers);
      renderSyncQueue(j.sync_queue);
      const pj = await cloudFetch("/api/cloud/projects");
      if (pj.ok) renderProjects(pj.projects);
    } catch (e) {
      console.warn("cloud:", e);
    }
  };

  document.addEventListener("DOMContentLoaded", function () {
    cloudRefresh();
    _pollTimer = setInterval(cloudRefresh, 5000);
  });

  window.addEventListener("beforeunload", function () {
    if (_pollTimer) clearInterval(_pollTimer);
  });
})();
