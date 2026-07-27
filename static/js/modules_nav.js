/** Dynamic sidebar from module_registry.json — SVG icons, no emoji chrome */

(function () {
  const SVG_ATTR =
    'viewBox="0 0 24 24" aria-hidden="true" focusable="false"';

  function path(d) {
    return `<path d="${d}"/>`;
  }
  function circ(cx, cy, r) {
    return `<circle cx="${cx}" cy="${cy}" r="${r}"/>`;
  }
  function line(x1, y1, x2, y2) {
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  }
  function poly(pts) {
    return `<polyline points="${pts}"/>`;
  }
  function rect(x, y, w, h, rx) {
    return `<rect x="${x}" y="${y}" width="${w}" height="${h}"${rx ? ` rx="${rx}"` : ""}/>`;
  }

  const ICONS = {
    home:
      path("M3 10.5 12 3l9 7.5") +
      path("M5 10v10h5v-6h4v6h5V10"),
    mic:
      rect(9, 3, 6, 11, 3) +
      path("M5 11a7 7 0 0 0 14 0") +
      line(12, 18, 12, 21) +
      line(8, 21, 16, 21),
    speaker:
      path("M11 5 6 9H3v6h3l5 4V5z") +
      path("M15.5 8.5a4 4 0 0 1 0 7") +
      path("M18 6a7 7 0 0 1 0 12"),
    globe:
      circ(12, 12, 9) +
      path("M3 12h18") +
      path("M12 3a14 14 0 0 1 0 18") +
      path("M12 3a14 14 0 0 0 0 18"),
    film:
      rect(3, 5, 18, 14, 2) +
      line(8, 5, 8, 19) +
      line(16, 5, 16, 19) +
      line(3, 10, 21, 10) +
      line(3, 14, 21, 14),
    bolt: path("M13 2 4 14h7l-1 8 10-14h-7l0-6z"),
    monitor:
      rect(2, 4, 20, 13, 2) +
      line(8, 21, 16, 21) +
      line(12, 17, 12, 21),
    broadcast:
      path("M5 12a7 7 0 0 1 14 0") +
      path("M8.5 12a3.5 3.5 0 0 1 7 0") +
      circ(12, 12, 1.2),
    record: circ(12, 12, 8) + circ(12, 12, 3.5),
    sliders:
      line(4, 7, 20, 7) +
      line(4, 12, 20, 12) +
      line(4, 17, 20, 17) +
      circ(8, 7, 1.6) +
      circ(15, 12, 1.6) +
      circ(11, 17, 1.6),
    waveform:
      line(4, 12, 4, 12) +
      line(7, 8, 7, 16) +
      line(10, 5, 10, 19) +
      line(13, 9, 13, 15) +
      line(16, 6, 16, 18) +
      line(19, 10, 19, 14),
    music:
      path("M9 18V6l12-2v12") +
      circ(7, 18, 2.5) +
      circ(19, 16, 2.5),
    spark:
      path("M12 2l1.5 6.5L20 10l-6.5 1.5L12 18l-1.5-6.5L4 10l6.5-1.5L12 2z"),
    book:
      path("M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2V5z") +
      path("M6 3v16"),
    folder:
      path("M3 7h6l2 2h10v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z") +
      path("M3 7V5a2 2 0 0 1 2-2h4l2 2"),
    settings:
      circ(12, 12, 3) +
      path(
        "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9c.3.6.9 1 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"
      ),
    brain:
      path(
        "M9.5 4a3.5 3.5 0 0 0-3.4 4.2A3.5 3.5 0 0 0 4 11.5 3.5 3.5 0 0 0 6.2 15 3.5 3.5 0 0 0 8 20.5h8A3.5 3.5 0 0 0 17.8 15 3.5 3.5 0 0 0 20 11.5a3.5 3.5 0 0 0-2.1-3.3A3.5 3.5 0 0 0 14.5 4 3.5 3.5 0 0 0 12 5.2 3.5 3.5 0 0 0 9.5 4z"
      ) +
      path("M12 8v8") +
      path("M9 12h6"),
    cpu:
      rect(7, 7, 10, 10, 2) +
      line(12, 3, 12, 7) +
      line(12, 17, 12, 21) +
      line(3, 12, 7, 12) +
      line(17, 12, 21, 12) +
      line(5, 7, 7, 7) +
      line(17, 7, 19, 7) +
      line(5, 17, 7, 17) +
      line(17, 17, 19, 17),
    clapper:
      path("M3 9h18v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9z") +
      path("M3 9l4-6 4 6 4-6 4 6"),
    cloud:
      path(
        "M7 18h11a4 4 0 0 0 .4-8 5.5 5.5 0 0 0-10.6-1.5A3.5 3.5 0 0 0 7 18z"
      ),
    store:
      path("M4 9h16l-1 11H5L4 9z") +
      path("M4 9l2-5h12l2 5") +
      path("M9 13v4") +
      path("M15 13v4"),
    import:
      path("M12 3v12") +
      poly("8 11 12 15 16 11") +
      path("M4 19h16"),
    grid:
      rect(3, 3, 7, 7, 1.5) +
      rect(14, 3, 7, 7, 1.5) +
      rect(3, 14, 7, 7, 1.5) +
      rect(14, 14, 7, 7, 1.5),
    flag: path("M5 21V4") + path("M5 4h12l-2.5 4L17 12H5"),
    knobs:
      circ(8, 8, 2) +
      circ(16, 16, 2) +
      line(8, 10, 8, 20) +
      line(16, 4, 16, 14) +
      line(4, 8, 6, 8) +
      line(10, 8, 20, 8) +
      line(4, 16, 14, 16) +
      line(18, 16, 20, 16),
    inspect:
      circ(10, 10, 6) +
      line(14.5, 14.5, 20, 20) +
      path("M8 10h4") +
      path("M10 8v4"),
    layers:
      path("M12 3 3 8l9 5 9-5-9-5z") +
      path("M3 12l9 5 9-5") +
      path("M3 16l9 5 9-5"),
    default: circ(12, 12, 3),
  };

  // Legacy emoji → key (local overrides / old caches)
  const EMOJI_TO_KEY = {
    "🏠": "home",
    "🎙️": "mic",
    "🎤": "mic",
    "🌍": "globe",
    "🎬": "film",
    "⚡": "bolt",
    "📺": "monitor",
    "📡": "broadcast",
    "🔴": "record",
    "🎚️": "sliders",
    "🗣️": "waveform",
    "🎵": "music",
    "🌐": "spark",
    "📖": "book",
    "📁": "folder",
    "⚙️": "settings",
    "🧠": "brain",
    "🤖": "cpu",
    "☁️": "cloud",
    "🛍️": "store",
    "📥": "import",
    "🧩": "grid",
    "🚩": "flag",
    "🎛️": "knobs",
    "🔬": "inspect",
    "🏗️": "layers",
    "🔊": "speaker",
  };

  function resolveIconKey(item) {
    const raw = String(item.icon || "").trim();
    if (ICONS[raw]) return raw;
    if (EMOJI_TO_KEY[raw]) return EMOJI_TO_KEY[raw];
    if (item.id && ICONS[item.id]) return item.id;
    // strip variation selectors / ZWJ leftovers
    const stripped = raw.replace(/\uFE0F/g, "");
    if (EMOJI_TO_KEY[stripped]) return EMOJI_TO_KEY[stripped];
    return "default";
  }

  function iconHtml(item) {
    const key = resolveIconKey(item);
    const inner = ICONS[key] || ICONS.default;
    return `<span class="nav-icon"><svg ${SVG_ATTR}>${inner}</svg></span>`;
  }

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
    const pathName = window.location.pathname.replace(/\/$/, "") || "/";

    items.forEach((item) => {
      const active =
        (item.route &&
          (pathName === item.route.replace(/\/$/, "") || pathName === item.route)) ||
        (item.route === "/" && pathName === "/");

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
    const soonBadge =
      item.coming_soon && !developerMode
        ? `<span class="mod-soon-badge" data-i18n="nav.soon">Скоро</span>`
        : "";
    const i18n = item.i18n_key ? ` data-i18n="${item.i18n_key}"` : "";
    return (
      iconHtml(item) +
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
  window.vmNavIconHtml = iconHtml;

  const _setMode = window.setMode;
  if (typeof _setMode === "function") {
    window.setMode = function (mode) {
      _setMode(mode);
      loadNav();
    };
  }
})();
