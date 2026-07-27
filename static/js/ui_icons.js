/** Shared SVG icon kit — Plus Jakarta / sky-blue UI, no emoji chrome */
(function (global) {
  const ATTR =
    'viewBox="0 0 24 24" aria-hidden="true" focusable="false"';

  function p(d) {
    return `<path d="${d}"/>`;
  }
  function c(cx, cy, r) {
    return `<circle cx="${cx}" cy="${cy}" r="${r}"/>`;
  }
  function l(x1, y1, x2, y2) {
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  }
  function r(x, y, w, h, rx) {
    return `<rect x="${x}" y="${y}" width="${w}" height="${h}"${rx ? ` rx="${rx}"` : ""}/>`;
  }
  function poly(pts) {
    return `<polyline points="${pts}"/>`;
  }

  const PATHS = {
    home: p("M3 10.5 12 3l9 7.5") + p("M5 10v10h5v-6h4v6h5V10"),
    film:
      r(3, 5, 18, 14, 2) +
      l(8, 5, 8, 19) +
      l(16, 5, 16, 19) +
      l(3, 10, 21, 10),
    mic:
      r(9, 3, 6, 11, 3) +
      p("M5 11a7 7 0 0 0 14 0") +
      l(12, 18, 12, 21) +
      l(8, 21, 16, 21),
    speaker:
      p("M11 5 6 9H3v6h3l5 4V5z") +
      p("M15.5 8.5a4 4 0 0 1 0 7"),
    globe:
      c(12, 12, 9) +
      p("M3 12h18") +
      p("M12 3a14 14 0 0 1 0 18") +
      p("M12 3a14 14 0 0 0 0 18"),
    book: p("M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2V5z") + p("M6 3v16"),
    folder:
      p("M3 7h6l2 2h10v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z") +
      p("M3 7V5a2 2 0 0 1 2-2h4l2 2"),
    settings:
      c(12, 12, 3) +
      p(
        "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9c.3.6.9 1 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"
      ),
    cloud: p("M7 18h11a4 4 0 0 0 .4-8 5.5 5.5 0 0 0-10.6-1.5A3.5 3.5 0 0 0 7 18z"),
    bolt: p("M13 2 4 14h7l-1 8 10-14h-7l0-6z"),
    spark:
      p("M12 2l1.5 6.5L20 10l-6.5 1.5L12 18l-1.5-6.5L4 10l6.5-1.5L12 2z"),
    grid:
      r(3, 3, 7, 7, 1.5) +
      r(14, 3, 7, 7, 1.5) +
      r(3, 14, 7, 7, 1.5) +
      r(14, 14, 7, 7, 1.5),
    clapper: p("M3 9h18v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9z") + p("M3 9l4-6 4 6 4-6 4 6"),
    monitor: r(2, 4, 20, 13, 2) + l(8, 21, 16, 21) + l(12, 17, 12, 21),
    broadcast: p("M5 12a7 7 0 0 1 14 0") + p("M8.5 12a3.5 3.5 0 0 1 7 0") + c(12, 12, 1.2),
    music: p("M9 18V6l12-2v12") + c(7, 18, 2.5) + c(19, 16, 2.5),
    waveform:
      l(4, 12, 4, 12) +
      l(7, 8, 7, 16) +
      l(10, 5, 10, 19) +
      l(13, 9, 13, 15) +
      l(16, 6, 16, 18) +
      l(19, 10, 19, 14),
    layers: p("M12 3 3 8l9 5 9-5-9-5z") + p("M3 12l9 5 9-5") + p("M3 16l9 5 9-5"),
    inspect: c(10, 10, 6) + l(14.5, 14.5, 20, 20) + p("M8 10h4") + p("M10 8v4"),
    key: p("M21 2l-2 2m-7.6 7.6a5 5 0 1 1-2.8 2.8L3 21l-1 1 3 1 1-1 1-3 2-2") + c(16, 8, 1.2),
    package: p("M16.5 9.4 7.5 4.2") + p("M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z") + p("M3.3 7 12 12l8.7-5") + l(12, 12, 12, 22),
    trash: p("M3 6h18") + p("M8 6V4h8v2") + p("M19 6l-1 14H6L5 6") + l(10, 10, 10, 16) + l(14, 10, 14, 16),
    clipboard: r(8, 2, 8, 4, 1) + p("M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"),
    clock: c(12, 12, 9) + p("M12 7v5l3 2"),
    refresh: p("M21 12a9 9 0 1 1-2.6-6.4") + poly("21 3 21 9 15 9"),
    check: p("M20 6 9 17l-5-5"),
    x: l(6, 6, 18, 18) + l(18, 6, 6, 18),
    alert: p("M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z") + l(12, 9, 12, 13) + c(12, 17, 0.8),
    info: c(12, 12, 9) + l(12, 8, 12, 8.01) + l(12, 12, 12, 16),
    tip: c(12, 12, 9) + p("M9 10a3 3 0 1 1 4.5 2.6c-.7.5-1.5 1.1-1.5 2.4") + c(12, 18, 0.6),
    keyboard: r(2, 6, 20, 12, 2) + l(6, 10, 6, 10.01) + l(10, 10, 10, 10.01) + l(14, 10, 14, 10.01) + l(18, 10, 18, 10.01) + l(8, 14, 16, 14),
    download: p("M12 3v12") + poly("8 11 12 15 16 11") + p("M4 19h16"),
    upload: p("M12 21V9") + poly("8 13 12 9 16 13") + p("M4 5h16"),
    chart: l(4, 19, 20, 19) + l(6, 17, 6, 10) + l(12, 17, 12, 6) + l(18, 17, 18, 12),
    flask: p("M9 3h6") + p("M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3"),
    wrench: p("M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-2.5 2.5-2.5z"),
    star: p("M12 2l2.9 6.9L22 10l-5 4.4L18.2 22 12 18.3 5.8 22 7 14.4 2 10l7.1-1.1L12 2z"),
    sliders:
      l(4, 7, 20, 7) +
      l(4, 12, 20, 12) +
      l(4, 17, 20, 17) +
      c(8, 7, 1.6) +
      c(15, 12, 1.6) +
      c(11, 17, 1.6),
    mail: r(3, 5, 18, 14, 2) + p("M3 7l9 6 9-6"),
    paint: p("M12 2a5 5 0 0 1 5 5c0 3-5 9-5 9S7 10 7 7a5 5 0 0 1 5-5z") + c(12, 7, 1.2),
    cpu:
      r(7, 7, 10, 10, 2) +
      l(12, 3, 12, 7) +
      l(12, 17, 12, 21) +
      l(3, 12, 7, 12) +
      l(17, 12, 21, 12),
    checkCircle: c(12, 12, 9) + p("M8.5 12.5 11 15l4.5-5"),
    xCircle: c(12, 12, 9) + l(9, 9, 15, 15) + l(15, 9, 9, 15),
    spinner: p("M12 3a9 9 0 1 1-9 9"),
    default: c(12, 12, 3),
  };

  const EMOJI_ALIAS = {
    "🏠": "home",
    "🎬": "film",
    "🎙️": "mic",
    "🎤": "mic",
    "🔊": "speaker",
    "🌍": "globe",
    "🌐": "globe",
    "📖": "book",
    "📁": "folder",
    "📂": "folder",
    "⚙️": "settings",
    "☁️": "cloud",
    "⚡": "bolt",
    "✨": "spark",
    "🧩": "grid",
    "📺": "monitor",
    "📡": "broadcast",
    "🎵": "music",
    "🗣️": "waveform",
    "🔬": "inspect",
    "🔑": "key",
    "📦": "package",
    "🗑": "trash",
    "🗑️": "trash",
    "📋": "clipboard",
    "🕐": "clock",
    "⏱": "clock",
    "🔄": "refresh",
    "✅": "checkCircle",
    "❌": "xCircle",
    "⚠️": "alert",
    "💡": "tip",
    "⌨️": "keyboard",
    "📊": "chart",
    "🧪": "flask",
    "🔧": "wrench",
    "⭐": "star",
    "🎚️": "sliders",
    "🎛": "sliders",
    "📨": "mail",
    "🎨": "paint",
    "🤖": "cpu",
    "📝": "clipboard",
    "🔍": "inspect",
  };

  function resolve(key) {
    const raw = String(key || "").trim();
    if (PATHS[raw]) return raw;
    if (EMOJI_ALIAS[raw]) return EMOJI_ALIAS[raw];
    const stripped = raw.replace(/\uFE0F/g, "");
    if (EMOJI_ALIAS[stripped]) return EMOJI_ALIAS[stripped];
    return "default";
  }

  function svg(key) {
    const k = resolve(key);
    return `<svg ${ATTR}>${PATHS[k] || PATHS.default}</svg>`;
  }

  function html(key, className) {
    const cls = className ? `ui-ico ${className}` : "ui-ico";
    return `<span class="${cls}" data-ico="${resolve(key)}">${svg(key)}</span>`;
  }

  function statusDot(level) {
    const map = { ok: "ok", success: "ok", warn: "warn", warning: "warn", error: "err", fail: "err", pending: "pending", loading: "pending" };
    const cls = map[level] || "pending";
    return `<span class="status-dot status-dot--${cls}" aria-hidden="true"></span>`;
  }

  global.vmIcon = { svg, html, resolve, statusDot, PATHS };
})(typeof window !== "undefined" ? window : globalThis);
