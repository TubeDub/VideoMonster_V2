# -*- coding: utf-8 -*-
"""Round polish: inject SVG hooks + fix leftovers after emoji scrub."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILM = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><line x1="8" y1="5" x2="8" y2="19"/><line x1="16" y1="5" x2="16" y2="19"/><line x1="3" y1="10" x2="21" y2="10"/></svg>'
MIC = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><line x1="12" y1="18" x2="12" y2="21"/><line x1="8" y1="21" x2="16" y2="21"/></svg>'
SPEAKER = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4V5z"/><path d="M15.5 8.5a4 4 0 0 1 0 7"/></svg>'
GLOBE = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18"/><path d="M12 3a14 14 0 0 0 0 18"/></svg>'
BOOK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2V5z"/><path d="M6 3v16"/></svg>'
FOLDER = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7h6l2 2h10v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/><path d="M3 7V5a2 2 0 0 1 2-2h4l2 2"/></svg>'
TIP = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9 10a3 3 0 1 1 4.5 2.6c-.7.5-1.5 1.1-1.5 2.4"/><circle cx="12" cy="18" r="0.6"/></svg>'
KEYBOARD = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="6" width="20" height="12" rx="2"/><line x1="6" y1="10" x2="6" y2="10.01"/><line x1="10" y1="10" x2="10" y2="10.01"/><line x1="14" y1="10" x2="14" y2="10.01"/><line x1="18" y1="10" x2="18" y2="10.01"/><line x1="8" y1="14" x2="16" y2="14"/></svg>'
REFRESH = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.6-6.4"/><polyline points="21 3 21 9 15 9"/></svg>'
TRASH = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><line x1="10" y1="10" x2="10" y2="16"/><line x1="14" y1="10" x2="14" y2="16"/></svg>'
CLIP = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/></svg>'
CLOCK = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'
UPLOAD = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21V9"/><polyline points="8 13 12 9 16 13"/><path d="M4 5h16"/></svg>'


def patch_index() -> None:
    p = ROOT / "templates" / "index.html"
    t = p.read_text(encoding="utf-8")
    t = t.replace(
        '<div class="card-tag">⭐ Главное</div>',
        '<div class="card-tag">Главное</div>',
    )
    t = t.replace(
        '<a href="/dub" class="feature-card feature-card--highlight"><div class="card-icon"></div>',
        f'<a href="/dub" class="feature-card feature-card--highlight"><div class="card-icon">{FILM}</div>',
    )
    t = t.replace(
        '<a href="/studio" class="feature-card"><div class="card-icon"></div>',
        f'<a href="/studio" class="feature-card"><div class="card-icon">{MIC}</div>',
    )
    t = t.replace(
        '<a href="/voice" class="feature-card"><div class="card-icon"></div>',
        f'<a href="/voice" class="feature-card"><div class="card-icon">{SPEAKER}</div>',
    )
    t = t.replace(
        '<a href="/translate" class="feature-card"><div class="card-icon"></div>',
        f'<a href="/translate" class="feature-card"><div class="card-icon">{GLOBE}</div>',
    )
    t = t.replace(
        '<a href="/reader" class="feature-card"><div class="card-icon"></div>',
        f'<a href="/reader" class="feature-card"><div class="card-icon">{BOOK}</div>',
    )
    t = t.replace(
        '<a href="/projects" class="feature-card"><div class="card-icon"></div>',
        f'<a href="/projects" class="feature-card"><div class="card-icon">{FOLDER}</div>',
    )
    # recovery + tips icons
    t = t.replace(
        '<span class="recovery-icon"></span>',
        f'<span class="recovery-icon ui-ico ui-ico--md">{REFRESH}</span>',
    )
    # tip icons — first empty, second empty, third keyboard remnant
    t = t.replace(
        '<div class="tips-block" style="margin-top:28px;"><div class="tip-item"><span class="tip-icon"></span>',
        f'<div class="tips-block" style="margin-top:28px;"><div class="tip-item"><span class="tip-icon ui-ico ui-ico--md ui-ico--muted">{TIP}</span>',
        1,
    )
    t = t.replace(
        '<div class="tip-item"><span class="tip-icon"></span>\n    <div><strong>Работает без интернета</strong>',
        f'<div class="tip-item"><span class="tip-icon ui-ico ui-ico--md ui-ico--muted">{GLOBE}</span>\n    <div><strong>Работает без интернета</strong>',
        1,
    )
    t = t.replace(
        '<span class="tip-icon">⌨</span>',
        f'<span class="tip-icon ui-ico ui-ico--md ui-ico--muted">{KEYBOARD}</span>',
    )
    t = t.replace(
        '<h2 class="section-title" style="margin:0;"> Последние проекты</h2>',
        '<h2 class="section-title" style="margin:0;">Последние проекты</h2>',
    )
    t = t.replace(
        '<a href="/dub" class="btn btn-primary"> Попробовать прямо сейчас →</a>',
        '<a href="/dub" class="btn btn-primary">Попробовать прямо сейчас →</a>',
    )
    t = t.replace(
        '> Stress Test</button>',
        '>Stress Test</button>',
    )
    t = t.replace(
        '<button class="btn btn-sm" onclick="checkSystem()" id="sys-retry-btn" style="display:none;"> Повторить</button>',
        '<button class="btn btn-sm" onclick="checkSystem()" id="sys-retry-btn" style="display:none;">Повторить</button>',
    )
    # system icon placeholder
    t = t.replace(
        '<span id="sys-overall-icon">⏳</span>',
        '<span id="sys-overall-icon" class="status-dot status-dot--pending" aria-hidden="true"></span>',
    )
    if 'id="sys-overall-icon"' in t and "status-dot" not in t.split('id="sys-overall-icon"', 1)[1][:120]:
        t = t.replace(
            '<span id="sys-overall-icon"></span>',
            '<span id="sys-overall-icon" class="status-dot status-dot--pending" aria-hidden="true"></span>',
        )
        # after scrub may be empty or hourglass
        import re
        t = re.sub(
            r'<span id="sys-overall-icon"[^>]*>[^<]*</span>',
            '<span id="sys-overall-icon" class="status-dot status-dot--pending" aria-hidden="true"></span>',
            t,
            count=1,
        )

    # rewrite checkSystem icon usage
    old_check = None
    if "iconEl.textContent" in t:
        t = t.replace("iconEl.textContent = '⏳';", "iconEl.className = 'status-dot status-dot--pending'; iconEl.textContent = '';")
        t = t.replace(
            "iconEl.textContent = '✅'; msgEl.textContent = d.summary || 'Всё готово к работе';",
            "iconEl.className = 'status-dot status-dot--ok'; iconEl.textContent = ''; msgEl.textContent = d.summary || 'Всё готово к работе';",
        )
        t = t.replace(
            "iconEl.textContent = '⚠️';",
            "iconEl.className = 'status-dot status-dot--err'; iconEl.textContent = '';",
        )
        t = t.replace(
            "iconEl.textContent = '🟡'; msgEl.textContent = d.summary || 'Готово с предупреждениями';",
            "iconEl.className = 'status-dot status-dot--warn'; iconEl.textContent = ''; msgEl.textContent = d.summary || 'Готово с предупреждениями';",
        )
        t = t.replace(
            "iconEl.textContent = '❓'; msgEl.textContent = 'Не удалось проверить систему';",
            "iconEl.className = 'status-dot status-dot--err'; iconEl.textContent = ''; msgEl.textContent = 'Не удалось проверить систему';",
        )
        # chips without emoji
        t = t.replace(
            "const ico  = c.ok ? '✅' : (c.critical ? '❌' : '⚠️');",
            "const ico  = c.ok ? '' : '';",
        )
        t = t.replace(
            "return `<span class=\"sys-check-chip ${cls}\">${ico} ${c.label}${hint}</span>`;",
            "return `<span class=\"sys-check-chip ${cls}\"><span class=\"status-dot status-dot--${cls === 'ok' ? 'ok' : (cls === 'fail' ? 'err' : 'warn')}\"></span> ${c.label}${hint}</span>`;",
        )
        t = t.replace(
            "const diskIco   = d.disk_free_gb > 5 ? '✅' : '⚠️';\n      rowEl.innerHTML += `<span class=\"sys-check-chip ${diskClass}\">${diskIco} Диск: ${d.disk_free_gb} ГБ свободно</span>`;",
            "rowEl.innerHTML += `<span class=\"sys-check-chip ${diskClass}\"><span class=\"status-dot status-dot--${diskClass === 'ok' ? 'ok' : (diskClass === 'fail' ? 'err' : 'warn')}\"></span> Диск: ${d.disk_free_gb} ГБ свободно</span>`;",
        )
        t = t.replace(
            "rowEl.innerHTML += `<span class=\"sys-check-chip ok\" style=\"opacity:.6;\">🐍 Python ${d.python_ver}</span>`;",
            "rowEl.innerHTML += `<span class=\"sys-check-chip ok\" style=\"opacity:.6;\"><span class=\"status-dot status-dot--ok\"></span> Python ${d.python_ver}</span>`;",
        )
        # recent projects icon
        t = t.replace(
            '<div class="rmc-icon">${p.icon}</div>',
            '<div class="rmc-icon">${(window.vmIcon && vmIcon.html(p.icon, "ui-ico--md")) || p.icon}</div>',
        )

    p.write_text(t, encoding="utf-8")
    print("patched index")


def patch_projects() -> None:
    p = ROOT / "templates" / "projects.html"
    t = p.read_text(encoding="utf-8")
    t = t.replace(
        "grid.innerHTML = '<div style=\"grid-column:1/-1;text-align:center;padding:40px;color:var(--text2);\">⏳ Загрузка…</div>';",
        "grid.innerHTML = '<div style=\"grid-column:1/-1;text-align:center;padding:40px;color:var(--text2);\">Загрузка…</div>';",
    )
    t = t.replace(
        '<button class="pc-delete-btn" title="Удалить" onclick="deleteProject(\'${p.filename}\',this)"></button>\n      <div class="pc-icon">${p.icon}</div>',
        '<button class="pc-delete-btn" title="Удалить" onclick="deleteProject(\'${p.filename}\',this)">'
        + "${(window.vmIcon && vmIcon.html('trash','ui-ico--sm')) || '×'}</button>\n"
        + '      <div class="pc-icon">${(window.vmIcon && vmIcon.html(p.icon,\'ui-ico--md\')) || \'\'}</div>',
    )
    t = t.replace(
        "if (d.ok) vmNotify('📂 Папка открыта', 'success', 2000);",
        "if (d.ok) vmNotify('Папка открыта', 'success', 2000);",
    )
    t = t.replace(
        ".project-card .pc-icon { width: 36px; height: 36px; border-radius: 9px; background: var(--accent-dim); border: 1px solid rgba(91,156,245,.22); display: grid; place-items: center; font-size: 15px;\n}",
        ".project-card .pc-icon { width: 36px; height: 36px; border-radius: 9px; background: var(--accent-dim); border: 1px solid rgba(91,156,245,.22); display: grid; place-items: center; color: var(--accent);\n}\n.project-card .pc-icon .ui-ico { width: 18px; height: 18px; }\n.pc-delete-btn .ui-ico { width: 14px; height: 14px; }",
    )
    p.write_text(t, encoding="utf-8")
    print("patched projects")


def patch_dub() -> None:
    p = ROOT / "templates" / "dub.html"
    t = p.read_text(encoding="utf-8")
    t = t.replace(
        '<div class="wizard-drop-icon"></div>',
        f'<div class="wizard-drop-icon" aria-hidden="true">{UPLOAD}</div>',
    )
    # also if scrub left empty or folder remnant
    import re
    t = re.sub(
        r'<div class="wizard-drop-icon">[^<]*</div>',
        f'<div class="wizard-drop-icon" aria-hidden="true">{UPLOAD}</div>',
        t,
        count=1,
    )
    replacements = [
        ('data-phase="translate"', "clipboard", CLIP),
        ('data-phase="tts"', "mic", MIC),
        ('data-phase="sync"', "clock", CLOCK),
        ('data-phase="export"', "film", FILM),
    ]
    for marker, _key, svg in replacements:
        # replace next wizard-phase-icon after marker
        idx = t.find(marker)
        if idx < 0:
            continue
        sub = t[idx : idx + 220]
        sub2 = re.sub(
            r'<span class="wizard-phase-icon">[^<]*</span>',
            f'<span class="wizard-phase-icon" aria-hidden="true">{svg}</span>',
            sub,
            count=1,
        )
        t = t[:idx] + sub2 + t[idx + 220 :]
    # button labels — already scrubbed mostly
    t = t.replace("▶ Просмотр", "Просмотр")
    t = t.replace("▶ YouTube", "YouTube")
    p.write_text(t, encoding="utf-8")
    print("patched dub")


def patch_headers() -> None:
    fixes = {
        "templates/director.html": ("<h1>AI Director</h1>", "<h1>AI Director</h1>"),
        "templates/plugins.html": ("<h1>Plugin Manager</h1>", "<h1>Plugin Manager</h1>"),
        "templates/cloud.html": ("<h1>Cloud Platform</h1>", "<h1>Cloud Platform</h1>"),
        "templates/platform.html": ("<h1>AI Media Platform</h1>", "<h1>AI Media Platform</h1>"),
        "templates/voice.html": ("<h1>Озвучка текста</h1>", "<h1>Озвучка текста</h1>"),
        "templates/pipeline_dev.html": (
            "<h1>🔬 Pipeline Inspector</h1>",
            "<h1>Pipeline Inspector</h1>",
        ),
        "templates/dub_studio.html": ("<h1>🎛️ Dub Studio</h1>", "<h1>Dub Studio</h1>"),
        "templates/ai_sources.html": ("<h1>🧠 AI Sources</h1>", "<h1>AI Sources</h1>"),
        "templates/mini_base.html": (
            '<span class="logo">🎬 TubeDub</span>',
            '<span class="logo">TubeDub</span>',
        ),
    }
    for rel, (a, b) in fixes.items():
        p = ROOT / rel
        if not p.exists():
            continue
        t = p.read_text(encoding="utf-8")
        # generic leftover strip for headings
        import re
        t2 = re.sub(
            r"(<h1[^>]*>)\s*[^\wА-Яа-яA-Za-z0-9]*\s*",
            r"\1",
            t,
            count=3,
        ) if False else t
        for old, new in [(a, b)]:
            t = t.replace(old, new)
        # strip common leftovers in these files
        for ch in ["🔬", "🔧", "📋", "🎛️", "🧠", "🎬", "🆓", "⭐", "⌨"]:
            t = t.replace(ch, "")
        p.write_text(t, encoding="utf-8")
        print("headers", rel)


def patch_plugins_css() -> None:
    p = ROOT / "templates" / "plugins.html"
    t = p.read_text(encoding="utf-8")
    t = t.replace(
        ".plg-cap { font-size:10px; padding:2px 6px; border-radius:4px; background:rgba(99,102,241,.15); color:#a5b4fc; }",
        ".plg-cap { font-size:10px; padding:2px 6px; border-radius:4px; background:var(--accent-dim); color:var(--accent); }",
    )
    t = t.replace(
        ".plg-card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:14px; }",
        ".plg-card { background:var(--panel-solid); border:1px solid var(--border); border-radius:var(--radius-sm); padding:14px; }",
    )
    p.write_text(t, encoding="utf-8")
    print("patched plugins")


def patch_modules_dev() -> None:
    p = ROOT / "static" / "js" / "modules_dev.js"
    t = p.read_text(encoding="utf-8")
    t = t.replace(
        "\"<td>\" + m.status_emoji + \" \" + m.status_label + \"</td>\"",
        "\"<td><span class='status-dot' style='background:\" + m.status_color + \"'></span> \" + m.status_label + \"</td>\"",
    )
    # also single-quote variant from scrub collapse
    t = t.replace(
        "'<td>' + m.status_emoji + ' ' + m.status_label + '</td>'",
        "'<td><span class=\"status-dot\" style=\"background:' + m.status_color + '\"></span> ' + m.status_label + '</td>'",
    )
    if "status_emoji" in t:
        t = t.replace("m.status_emoji + \" \" + m.status_label", "m.status_label")
        t = t.replace("m.status_emoji + ' ' + m.status_label", "m.status_label")
    p.write_text(t, encoding="utf-8")
    print("patched modules_dev")


def patch_settings_license() -> None:
    p = ROOT / "templates" / "settings.html"
    t = p.read_text(encoding="utf-8")
    t = t.replace("🆓", "")
    t = t.replace("⭐", "")
    t = t.replace(
        "border-radius:20px;",
        "border-radius:var(--radius-sm);",
    )
    # mode desc strings may still have leading spaces from scrub
    p.write_text(t, encoding="utf-8")
    print("patched settings")


def patch_dub_css() -> None:
    p = ROOT / "static" / "css" / "dub.css"
    t = p.read_text(encoding="utf-8")
    # ensure drop icon renders SVG properly
    needle = ".wizard-drop-icon {"
    if needle in t and "stroke: currentColor" not in t[t.find(needle) : t.find(needle) + 400]:
        t = t.replace(
            ".wizard-drop-icon {",
            ".wizard-drop-icon {\n  display: inline-flex; align-items: center; justify-content: center;\n  color: var(--accent);\n  font-size: 0;\n}\n.wizard-drop-icon svg {\n  width: 40px; height: 40px; stroke: currentColor; fill: none;\n  stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round;\n}\n.wizard-drop-icon-legacy {",
            1,
        )
    if ".wizard-phase-icon {" in t and ".wizard-phase-icon svg" not in t:
        t = t.replace(
            ".wizard-phase-icon {",
            ".wizard-phase-icon {\n  display: inline-flex; align-items: center; justify-content: center;\n  width: 20px; height: 20px; color: var(--text2); font-size: 0;\n}\n.wizard-phase-icon svg {\n  width: 18px; height: 18px; stroke: currentColor; fill: none;\n  stroke-width: 1.75; stroke-linecap: round; stroke-linejoin: round;\n}\n.wizard-phase-icon-legacy {",
            1,
        )
    p.write_text(t, encoding="utf-8")
    print("patched dub.css")


def main() -> None:
    patch_index()
    patch_projects()
    patch_dub()
    patch_headers()
    patch_plugins_css()
    patch_modules_dev()
    patch_settings_license()
    patch_dub_css()


if __name__ == "__main__":
    main()
