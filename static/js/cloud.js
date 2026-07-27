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
        const meta = p.meta || {};
        const oauth = meta.oauth_status || "";
        const remoteOk = meta.oauth_connected === true;
        // Green only for real OAuth; local mirror uses amber/gray — never fake "cloud connected"
        const dot = remoteOk ? "#3ecf8e" : oauth === "needs_auth" ? "#f59e0b" : "#6b7280";
        let statusLabel;
        if (remoteOk) {
          statusLabel = "OAuth ✓";
        } else if (oauth === "needs_auth") {
          statusLabel = "OAuth: нужна авторизация";
        } else if (oauth === "not_configured" || meta.oauth_remote_gated) {
          const miss = (meta.oauth_missing || []).join(", ");
          statusLabel = "OAuth hard-gate" + (miss ? " (" + miss + ")" : "") + " · локальное зеркало";
          if (meta.message_ru) statusLabel = meta.message_ru;
        } else if (p.connected) {
          statusLabel = meta.mode === "local_mirror" || !oauth ? "локальное зеркало ✓" : (p.error || "локальное зеркало");
        } else {
          statusLabel = p.error || "не подключено";
        }
        const free = p.free_bytes != null ? fmtBytes(p.free_bytes) + " free" : "";
        const up = p.upload_bps ? fmtBps(p.upload_bps) : "";
        const dn = p.download_bps ? fmtBps(p.download_bps) : "";
        const authBtn =
          oauth === "needs_auth" || oauth === "not_configured"
            ? ' <button class="btn" style="font-size:10px;padding:2px 6px;" data-oauth="' +
              (p.provider_id || "") +
              '">OAuth</button>'
            : remoteOk
              ? ' <button class="btn" style="font-size:10px;padding:2px 6px;" data-oauth-disconnect="' +
                (p.provider_id || "") +
                '">Disconnect</button>'
              : "";
        return (
          '<div style="padding:8px 0;border-bottom:1px solid var(--border);font-size:13px;">' +
          '<span class="cloud-dot" style="background:' +
          dot +
          '"></span><strong>' +
          (p.label || p.provider_id) +
          "</strong> " +
          statusLabel +
          authBtn +
          '<div style="font-size:11px;opacity:.7;margin-top:2px;">' +
          [free, up && "↑ " + up, dn && "↓ " + dn, meta.mode && "mode=" + meta.mode]
            .filter(Boolean)
            .join(" · ") +
          "</div></div>"
        );
      })
      .join("");

    el.querySelectorAll("[data-oauth]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const pid = btn.getAttribute("data-oauth");
        try {
          const r = await fetch("/api/cloud/oauth/" + encodeURIComponent(pid) + "/authorize");
          const j = await r.json();
          if (j.ok && j.url) {
            window.open(j.url, "_blank", "noopener");
            if (window.vmNotify) vmNotify("Откройте окно OAuth и завершите авторизацию", "info", 4000);
          } else if (window.vmNotify) {
            vmNotify(j.message || j.error || "OAuth недоступен", "error", 5000);
          }
        } catch (e) {
          if (window.vmNotify) vmNotify(String(e), "error");
        }
      });
    });
    el.querySelectorAll("[data-oauth-disconnect]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const pid = btn.getAttribute("data-oauth-disconnect");
        await fetch("/api/cloud/oauth/" + encodeURIComponent(pid) + "/disconnect", { method: "POST" });
        cloudRefresh();
      });
    });
  }

  function renderStats(data) {
    const el = document.getElementById("cloud-stats");
    if (!el) return;
    const connected = (data.providers || []).filter(function (p) {
      return p.meta && p.meta.oauth_connected;
    }).length;
    const mirrors = (data.providers || []).filter(function (p) {
      return p.connected;
    }).length;
    el.innerHTML =
      stat("Проекты", data.projects_count || 0) +
      stat("OAuth", connected + " remote") +
      stat("Зеркала", mirrors + " / " + (data.providers || []).length) +
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
