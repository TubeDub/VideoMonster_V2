/* AI Director page */
(function () {
  function qs(id) { return document.getElementById(id); }

  function safeJson(j) {
    try { return JSON.stringify(j, null, 2); } catch (_) { return String(j); }
  }

  window.directorLoadRecent = async function () {
    const el = qs("dir-recent");
    if (!el) return;
    el.textContent = "Loading…";
    try {
      const r = await fetch("/api/director/list");
      const j = await r.json();
      const reports = (j && j.reports) || [];
      if (!reports.length) {
        el.innerHTML = "<em>Нет director_report.json на диске — сначала запустите auto-dub с AI Director.</em>";
        return;
      }
      el.innerHTML =
        "<ul style='margin:0;padding-left:18px;'>" +
        reports.slice(0, 20).map(function (row) {
          const tid = row.task_id || row.id || "";
          const label = row.title || row.project_uuid || tid || "report";
          return (
            "<li style='margin:4px 0;'><a href='#' data-tid='" +
            String(tid).replace(/'/g, "") +
            "'>" +
            label +
            "</a>" +
            (tid ? " <code style='font-size:10px'>" + tid.slice(0, 10) + "…</code>" : "") +
            "</li>"
          );
        }).join("") +
        "</ul>";
      el.querySelectorAll("a[data-tid]").forEach(function (a) {
        a.addEventListener("click", function (ev) {
          ev.preventDefault();
          const tid = a.getAttribute("data-tid");
          if (qs("dir-task-id")) qs("dir-task-id").value = tid;
          directorLoad();
        });
      });
    } catch (e) {
      el.textContent = String(e);
    }
  };

  window.directorLoad = async function () {
    const tid = ((qs("dir-task-id") && qs("dir-task-id").value) || "").trim();
    if (!tid) {
      if (qs("dir-status")) qs("dir-status").textContent = "Укажите task id";
      return;
    }
    if (qs("dir-status")) qs("dir-status").textContent = "Loading…";
    try {
      const r = await fetch("/api/director/" + encodeURIComponent(tid));
      const j = await r.json().catch(function () { return { ok: false, error: "invalid JSON" }; });
      if (qs("dir-status")) qs("dir-status").textContent = safeJson(j);
      if (!j || j.ok === false) {
        if (qs("dir-briefs")) {
          qs("dir-briefs").innerHTML =
            "<em>" + (j && (j.error || j.message) || ("HTTP " + r.status)) + "</em>";
        }
        return;
      }
      const briefs = j.creative_briefs || (j.director_report && j.director_report.per_segment) || [];
      const issues = (j.director_report && j.director_report.issues) || [];
      let html = "";
      if (issues.length) {
        html += "<h4>Issues</h4><ul>" + issues.map(function (i) {
          return "<li><b>" + (i.code || i.severity || "") + "</b> " +
            (i.message || safeJson(i)) + "</li>";
        }).join("") + "</ul>";
      }
      if (briefs.length) {
        html += "<h4>Briefs</h4><ol>" + briefs.slice(0, 50).map(function (b) {
          return "<li>" + (typeof b === "string" ? b : safeJson(b)) + "</li>";
        }).join("") + "</ol>";
      }
      if (qs("dir-briefs")) {
        qs("dir-briefs").innerHTML = html || "<em>Нет briefs в отчёте</em>";
      }
    } catch (e) {
      if (qs("dir-status")) qs("dir-status").textContent = String(e);
    }
  };

  window.directorValidateLocal = async function () {
    const src = prompt("Source segments (one per line)") || "";
    const tgt = prompt("Translated segments (one per line)") || "";
    if (!src.trim() || !tgt.trim()) return;
    const source_segments = src.split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
    const translated_segments = tgt.split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
    const timing_map = source_segments.map(function (_, i) {
      return { start: i * 2000, end: i * 2000 + 1800 };
    });
    try {
      const r = await fetch("/api/director/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_segments: source_segments, translated_segments: translated_segments, timing_map: timing_map }),
      });
      const j = await r.json();
      if (qs("dir-status")) qs("dir-status").textContent = safeJson(j);
      if (qs("dir-briefs")) qs("dir-briefs").innerHTML = "";
    } catch (e) {
      if (qs("dir-status")) qs("dir-status").textContent = String(e);
    }
  };

  // Prefill from ?task= and load recent list
  try {
    directorLoadRecent();
    const u = new URL(location.href);
    const t = u.searchParams.get("task");
    if (t && qs("dir-task-id")) {
      qs("dir-task-id").value = t;
      directorLoad();
    }
  } catch (_) {}
})();
