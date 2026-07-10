(function () {
  function devHeaders() {
    return {
      "Content-Type": "application/json",
      "X-VM-Client-Dev-Mode": "1",
    };
  }

  function isDevMode() {
    return (localStorage.getItem("vm_mode") || "") === "dev";
  }

  async function ffPatch(id, action) {
    const r = await fetch("/api/features/" + encodeURIComponent(id), {
      method: "PATCH",
      headers: devHeaders(),
      body: JSON.stringify({ action }),
    });
    const j = await r.json();
    if (!j.ok) {
      vmNotify(j.error || "Error", "error");
      return;
    }
    vmNotify(id + " → " + action, "success", 2000);
    loadPanel();
    if (typeof window.vmReloadModuleNav === "function") window.vmReloadModuleNav();
  }

  window.ffToggle = function (id, on) {
    ffPatch(id, on ? "enable" : "disable");
  };

  function statHtml(label, val, sub) {
    return (
      '<div class="ff-stat"><div style="font-size:11px;opacity:.7">' +
      label +
      '</div><div class="val">' +
      val +
      "</div>" +
      (sub ? '<div style="font-size:10px;opacity:.6">' + sub + "</div>" : "") +
      "</div>"
    );
  }

  function rowHtml(f) {
    const on = f.runtime_enabled ? "ON" : "OFF";
    const auto = f.auto_disabled
      ? '<br><span style="color:#f56565;font-size:10px">auto-disabled</span>'
      : "";
    return (
      "<tr>" +
      "<td><strong>" +
      f.label +
      "</strong><br><code style='font-size:10px'>" +
      f.id +
      "</code>" +
      auto +
      "</td>" +
      "<td><span class='ff-dot' style='background:" +
      f.status_color +
      "'></span>" +
      f.status +
      " (" +
      f.readiness_pct +
      "%)</td>" +
      "<td>" +
      on +
      "</td>" +
      "<td>v" +
      f.version +
      "</td>" +
      "<td><code style='font-size:9px'>" +
      f.env_key +
      "</code></td>" +
      "<td class='ff-actions'>" +
      '<button onclick="ffToggle(\'' +
      f.id +
      "',true)\">ON</button>" +
      '<button onclick="ffToggle(\'' +
      f.id +
      "',false)\">OFF</button>" +
      '<button onclick="ffPatch(\'' +
      f.id +
      '\',"ready")\'>READY</button>' +
      '<button onclick="ffPatch(\'' +
      f.id +
      '\',"beta")\'>BETA</button>' +
      "</td>" +
      "</tr>"
    );
  }

  function renderLog(events) {
    const el = document.getElementById("ff-log");
    if (!el) return;
    el.textContent = (events || [])
      .slice()
      .reverse()
      .map(function (e) {
        return (
          new Date(e.ts_ms).toISOString().slice(11, 23) +
          " " +
          e.event +
          " " +
          (e.feature_id || "") +
          " " +
          (e.message || "") +
          (e.error ? " ERR" : "")
        );
      })
      .join("\n");
  }

  async function loadPanel() {
    const blocked = document.getElementById("ff-blocked");
    const panel = document.getElementById("ff-panel");
    if (!isDevMode()) {
      if (blocked) blocked.style.display = "block";
      if (panel) panel.style.display = "none";
      return;
    }
    try {
      const r = await fetch("/api/features/panel", { headers: devHeaders() });
      const j = await r.json();
      if (!j.ok) {
        if (blocked) blocked.style.display = "block";
        if (panel) panel.style.display = "none";
        return;
      }
      if (blocked) blocked.style.display = "none";
      if (panel) panel.style.display = "block";

      const feats = j.features || [];
      const enabled = feats.filter(function (x) {
        return x.runtime_enabled;
      }).length;
      const stats = document.getElementById("ff-stats");
      if (stats) {
        stats.innerHTML =
          statHtml("Version", j.app_version || "—") +
          statHtml("Memory", (j.memory_mb || "—") + " MB") +
          statHtml("Features ON", enabled + " / " + feats.length) +
          statHtml("Mode", j.user_mode || "developer");
      }
      const body = document.getElementById("ff-table-body");
      if (body) body.innerHTML = feats.map(rowHtml).join("");
      renderLog(j.dev_log || []);
    } catch (e) {
      console.warn("feature_flags_panel:", e);
    }
  }

  window.ffPatch = ffPatch;
  document.addEventListener("DOMContentLoaded", loadPanel);
})();
