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

  async function modPatch(id, action) {
    const r = await fetch("/api/modules/registry/" + encodeURIComponent(id), {
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
    loadRegistry();
    if (typeof window.vmReloadModuleNav === "function") window.vmReloadModuleNav();
  }

  window.modSetBeta = async function (on) {
    await fetch("/api/modules/settings", {
      method: "POST",
      headers: devHeaders(),
      body: JSON.stringify({ show_beta_to_users: !!on }),
    });
    loadRegistry();
    if (typeof window.vmReloadModuleNav === "function") window.vmReloadModuleNav();
  };

  function rowHtml(m) {
    const users = m.visible_to_users ? "✓" : "—";
    const menu = m.show_in_menu ? "✓" : "—";
    return (
      "<tr>" +
      "<td><span class='mod-dot' style='background:" +
      m.status_color +
      "'></span><strong>" +
      m.label +
      "</strong><br><code style='font-size:10px'>" +
      m.id +
      "</code></td>" +
      "<td>" +
      m.status_emoji +
      " " +
      m.status_label +
      "</td>" +
      "<td>" +
      users +
      "</td>" +
      "<td>" +
      menu +
      "</td>" +
      "<td><div class='mod-actions'>" +
      "<button class='btn btn-sm' onclick=\"modPatch('" +
      m.id +
      "','stable')\">Stable</button>" +
      "<button class='btn btn-sm' onclick=\"modPatch('" +
      m.id +
      "','beta')\">Beta</button>" +
      "<button class='btn btn-sm' onclick=\"modPatch('" +
      m.id +
      "','development')\">Dev</button>" +
      "<button class='btn btn-sm' onclick=\"modPatch('" +
      m.id +
      "','disable')\">Disable</button>" +
      "<button class='btn btn-sm' onclick=\"modPatch('" +
      m.id +
      "','hide_users')\">Hide</button>" +
      "<button class='btn btn-sm' onclick=\"modPatch('" +
      m.id +
      "','show_users')\">Show</button>" +
      "</div></td></tr>"
    );
  }

  window.modPatch = modPatch;

  async function loadRegistry() {
    const panel = document.getElementById("mod-dev-panel");
    const blocked = document.getElementById("mod-dev-blocked");
    if (!isDevMode()) {
      blocked.style.display = "block";
      blocked.innerHTML = "<p>Включите режим <strong>🔧 Dev</strong> в Настройках.</p>";
      return;
    }
    const r = await fetch("/api/modules/registry", { headers: devHeaders() });
    if (r.status === 403) {
      blocked.style.display = "block";
      panel.style.display = "none";
      return;
    }
    const j = await r.json();
    blocked.style.display = "none";
    panel.style.display = "block";
    document.getElementById("mod-show-beta").checked = !!j.show_beta_to_users;
    const body = document.getElementById("mod-table-body");
    body.innerHTML = (j.modules || []).map(rowHtml).join("");
  }

  document.addEventListener("DOMContentLoaded", loadRegistry);
})();
