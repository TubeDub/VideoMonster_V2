/** Dynamic sidebar from module_registry.json */

(function () {
  function isDevMode() {
    try {
      return (localStorage.getItem("vm_mode") || "simple") === "dev";
    } catch (_) {
      return false;
    }
  }

  function userMode() {
    try {
      const m = localStorage.getItem("vm_mode") || "simple";
      if (m === "dev") return "developer";
      if (m === "pro") return "pro";
      return "basic";
    } catch (_) {
      return "basic";
    }
  }

  function uiLang() {
    try {
      return (localStorage.getItem("vm_ui_lang") || "ru").split("-")[0];
    } catch (_) {
      return "ru";
    }
  }

  function closeSidebar() {
    if (typeof window.closeSidebar === "function") window.closeSidebar();
  }

  function renderNav(items, developerMode) {
    const nav = document.getElementById("sidebar-nav-dynamic");
    if (!nav) return;
    nav.innerHTML = "";
    const path = window.location.pathname.replace(/\/$/, "") || "/";

    items.forEach((item) => {
      const active =
        (item.route && (path === item.route.replace(/\/$/, "") || path === item.route)) ||
        (item.route === "/" && path === "/");

      if (item.kind === "action" && item.action) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "nav-item" + (item.pro_only ? " pro-only" : "");
        btn.style.cssText =
          "width:100%;border:none;background:transparent;cursor:pointer;text-align:left;";
        btn.innerHTML = navItemHtml(item, developerMode, false);
        btn.onclick = function () {
          if (typeof window[item.action] === "function") window[item.action]();
          closeSidebar();
        };
        nav.appendChild(btn);
        return;
      }

      if (!item.route) return;
      const a = document.createElement("a");
      a.href = item.route;
      a.className =
        "nav-item" +
        (active ? " active" : "") +
        (item.pro_only ? " pro-only" : "") +
        (item.coming_soon && !developerMode ? " soon-item" : "");
      a.onclick = function () {
        closeSidebar();
      };
      a.innerHTML = navItemHtml(item, developerMode, active);
      nav.appendChild(a);
    });
  }

  function navItemHtml(item, developerMode, active) {
    const dot =
      developerMode && item.status
        ? `<span class="mod-status-dot" style="background:${item.status_color}" title="${item.status_label}"></span>`
        : "";
    const exp =
      item.experimental && !developerMode
        ? `<span class="mod-exp-badge">β</span>`
        : "";
    const soonBadge = item.coming_soon && !developerMode
      ? `<span class="mod-soon-badge" data-i18n="nav.soon">Скоро</span>`
      : "";
    const i18n = item.i18n_key
      ? ` data-i18n="${item.i18n_key}"`
      : "";
    return (
      `<span class="nav-icon">${item.icon || "•"}</span>` +
      `<span class="nav-label nav-label-mod"${i18n}>${item.label}${exp}</span>` +
      soonBadge +
      dot
    );
  }

  async function loadNav() {
    try {
      const r = await fetch("/api/modules/nav", {
        headers: {
          "X-VM-Client-Dev-Mode": isDevMode() ? "1" : "0",
          "X-VM-User-Mode": userMode(),
          "X-VM-UI-Lang": uiLang(),
        },
      });
      const j = await r.json();
      renderNav(j.items || [], !!j.developer_mode);
      if (typeof window.applyI18n === "function") window.applyI18n();
    } catch (e) {
      console.warn("modules_nav:", e);
    }
  }

  document.addEventListener("DOMContentLoaded", loadNav);
  window.vmReloadModuleNav = loadNav;

  const _setMode = window.setMode;
  if (typeof _setMode === "function") {
    window.setMode = function (mode) {
      _setMode(mode);
      loadNav();
    };
  }
})();
