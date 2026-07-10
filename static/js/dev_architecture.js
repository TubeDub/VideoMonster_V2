(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  function devHeaders() {
    const h = {};
    try {
      if (localStorage.getItem("vm_client_dev_mode") === "1") h["X-VM-Dev-Mode"] = "1";
    } catch (_) {}
    return h;
  }
  async function checkDev() {
    const r = await fetch("/api/modules/status", { headers: devHeaders() });
    const j = await r.json();
    if (j.developer_mode) {
      $("#arch-blocked").style.display = "none";
      $("#arch-panel").style.display = "";
      load();
    } else {
      $("#arch-blocked").style.display = "";
      $("#arch-panel").style.display = "none";
    }
  }
  function render(d) {
    const mods = d.modules?.modules || [];
    $("#arch-modules").innerHTML = mods.map((m) => {
      const cls = m.release_channel === "RELEASE" ? "release" : m.release_channel === "DEVELOPER" ? "dev" : "off";
      const st = m.lifecycle_state || "?";
      const ok = m.health?.ok ? "🟢" : "🔴";
      return `<div class="mod ${cls}">${ok} <strong>${m.label}</strong><br><small>${m.release_channel} · ${st}</small></div>`;
    }).join("");
    const stages = (d.pipeline?.stages || []).map((s) => `→ ${s.label}`).join("<br>");
    const route = (d.pipeline?.data_route || []).join("<br>");
    $("#arch-pipeline").innerHTML = `<div class="arch-route">${stages}<hr>${route}</div>`;
    $("#arch-routes").textContent = (d.api_bus?.routes || []).map((r) => r.key + " ← " + r.module_id).join("\n");
    $("#arch-logs").textContent = (d.logs || []).slice(-40).join("\n") || "(no logs)";
    const ta = $("#arch-copy-area");
    ta.value = d.copy_text || "";
    ta.style.display = "block";
  }
  async function load() {
    const task = ($("#arch-task") || {}).value || "";
    const url = "/api/tubedub/platform/architecture" + (task ? "?task_id=" + encodeURIComponent(task) : "");
    const r = await fetch(url, { headers: devHeaders() });
    const j = await r.json();
    if (j.ok) render(j.dashboard);
  }
  $("#arch-refresh")?.addEventListener("click", load);
  $("#arch-copy")?.addEventListener("click", () => {
    const t = $("#arch-copy-area")?.value;
    if (t) navigator.clipboard.writeText(t).catch(() => {});
  });
  checkDev();
})();
