(function () {
  let _project = null;
  let _selectedSeg = null;
  let _plugins = [];

  async function api(url, opts) {
    const r = await fetch(url, opts);
    return r.json();
  }

  function statusDot(st) {
    const cls = st === "red" ? "red" : st === "yellow" ? "yellow" : "green";
    return '<span class="ds-dot ' + cls + '" title="' + st + '"></span>';
  }

  function renderTracks() {
    const el = document.getElementById("ds-tracks");
    if (!el || !_project) return;
    el.innerHTML = (_project.tracks || [])
      .map(function (t) {
        return (
          '<div class="ds-track">' +
          '<button class="btn btn-sm" onclick="dsSolo(\'' +
          t.track_id +
          "')\">S</button>" +
          "<span>" +
          (t.solo ? "🔊 " : "") +
          t.label +
          "</span>" +
          '<span style="opacity:.6;margin-left:auto;">' +
          t.kind +
          "</span></div>"
        );
      })
      .join("");
  }

  function renderSegments() {
    const el = document.getElementById("ds-segments");
    if (!el || !_project) return;
    el.innerHTML = (_project.segments || [])
      .map(function (s) {
        const active = _selectedSeg && _selectedSeg.segment_id === s.segment_id;
        const fill = (s.meta && s.meta.fill_percent) || 0;
        return (
          '<div class="ds-seg' +
          (active ? " active" : "") +
          '" onclick="dsSelectSeg(\'' +
          s.segment_id +
          "')\">" +
          statusDot(s.container_status) +
          " <strong>#" +
          (s.index + 1) +
          "</strong> " +
          (s.text || "").slice(0, 60) +
          (fill ? " · " + fill + "%" : "") +
          "</div>"
        );
      })
      .join("");
    const tl = document.getElementById("ds-timeline");
    if (tl && _project.segments) {
      tl.innerHTML = _project.segments
        .map(function (s) {
          const w = Math.max(4, Math.round(((s.end_ms - s.start_ms) / Math.max(_project.duration_ms, 1)) * 100));
          const left = Math.round((s.start_ms / Math.max(_project.duration_ms, 1)) * 100);
          return (
            '<div style="display:inline-block;height:28px;width:' +
            w +
            "%;margin-left:" +
            left +
            '%;background:var(--accent);opacity:.5;border-radius:3px;" title="' +
            (s.text || "").replace(/"/g, "") +
            '"></div>'
          );
        })
        .join("");
    }
  }

  function renderInspector() {
    const el = document.getElementById("ds-inspector");
    if (!el) return;
    if (!_selectedSeg) {
      el.innerHTML = "<em>Выберите сегмент</em>";
      return;
    }
    const s = _selectedSeg;
    const versions = (s.versions || [])
      .map(function (v) {
        const sel = v.version_id === s.active_version_id ? " ✓" : "";
        return (
          '<option value="' +
          v.version_id +
          '"' +
          (sel ? " selected" : "") +
          ">" +
          v.label +
          " (" +
          v.source +
          ")</option>"
        );
      })
      .join("");
    el.innerHTML =
      "<div><strong>Текст</strong><br>" +
      (s.text || "") +
      "</div>" +
      '<div style="margin-top:8px;">Anchor: ' +
      s.hard_anchor_ms +
      " ms · Container: " +
      s.container_ms +
      " ms</div>" +
      '<div style="margin-top:8px;">TTS: ' +
      s.tts_ms +
      " ms · Stretch: " +
      (s.stretch_ratio || 1).toFixed(2) +
      "x " +
      statusDot(s.container_status) +
      "</div>" +
      '<div style="margin-top:10px;"><label>Эмоция<br><select id="ds-emotion" class="select-control" style="width:100%">' +
      ["NEUTRAL", "HAPPY", "ANGRY", "SAD", "WHISPER", "SHOUTING", "IRONIC"]
        .map(function (e) {
          return '<option value="' + e + '"' + (s.emotion === e ? " selected" : "") + ">" + e + "</option>";
        })
        .join("") +
      '</select></div>' +
      '<button class="btn btn-sm" style="margin-top:6px;" onclick="dsSetEmotion()">Применить эмоцию</button>' +
      '<button class="btn btn-sm" style="margin-top:6px;" onclick="dsStretch()">Time Stretch</button>' +
      '<div style="margin-top:10px;"><label>Версии A/B/C<br><select id="ds-version" class="select-control" style="width:100%" onchange="dsSelectVersion(this.value)">' +
      versions +
      "</select></div>";
  }

  function renderPlugins() {
    const el = document.getElementById("ds-plugins");
    if (!el) return;
    el.innerHTML = _plugins
      .map(function (p) {
        return '<div class="ds-fx-item">' + p.label + " <code>" + p.plugin_id + "</code></div>";
      })
      .join("");
  }

  window.dsSelectSeg = function (id) {
    _selectedSeg = (_project.segments || []).find(function (s) {
      return s.segment_id === id;
    });
    renderSegments();
    renderInspector();
  };

  window.dsSolo = async function (trackId) {
    if (!_project) return;
    const j = await api("/api/dub-studio/projects/" + _project.project_id + "/tracks/" + trackId + "/solo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ solo: true }),
    });
    if (j.ok) {
      _project = j.project;
      renderTracks();
    }
  };

  window.dsSetEmotion = async function () {
    if (!_project || !_selectedSeg) return;
    const emotion = document.getElementById("ds-emotion").value;
    const j = await api(
      "/api/dub-studio/projects/" +
        _project.project_id +
        "/segments/" +
        _selectedSeg.segment_id +
        "/emotion",
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ emotion: emotion, regenerate: true }),
      }
    );
    if (j.ok) {
      if (j.segment && j.segment.meta && j.segment.meta.regenerate_error) {
        vmNotify("TTS: " + j.segment.meta.regenerate_error, "warn", 6000);
      } else {
        vmNotify("Эмоция → " + emotion + " (TTS обновлён)", "success");
      }
      await dsLoadProject(_project.project_id);
      dsSelectSeg(_selectedSeg.segment_id);
    }
  };

  window.dsStretch = async function () {
    if (!_project || !_selectedSeg) return;
    const j = await api(
      "/api/dub-studio/projects/" +
        _project.project_id +
        "/segments/" +
        _selectedSeg.segment_id +
        "/stretch",
      { method: "POST" }
    );
    vmNotify(j.applied ? "Stretch " + j.ratio.toFixed(2) + "x" : "Stretch не нужен", "info");
    await dsLoadProject(_project.project_id);
    dsSelectSeg(_selectedSeg.segment_id);
  };

  window.dsSelectVersion = async function (versionId) {
    if (!_project || !_selectedSeg) return;
    const j = await api(
      "/api/dub-studio/projects/" +
        _project.project_id +
        "/segments/" +
        _selectedSeg.segment_id +
        "/versions/" +
        versionId +
        "/select",
      { method: "POST" }
    );
    if (j.ok) {
      _selectedSeg = j.segment;
      renderInspector();
    }
  };

  window.dsNewProject = async function () {
    const j = await api("/api/dub-studio/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "Dub Studio " + new Date().toLocaleString("ru") }),
    });
    if (j.ok) {
      await dsRefresh();
      dsLoadProject(j.project.project_id);
    }
  };

  window.dsExport = async function () {
    if (!_project) return;
    const j = await api("/api/dub-studio/projects/" + _project.project_id + "/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format: "wav" }),
    });
    if (j.ok && j.file) {
      vmNotify("Экспорт готов", "success");
      window.open("/api/dub-studio/download/" + j.file, "_blank");
    } else vmNotify(j.error || "Export failed", "error");
  };

  window.dsAnalyzeEmotions = async function () {
    if (!_project) return;
    const j = await api("/api/dub-studio/projects/" + _project.project_id + "/analyze-emotions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (j.ok) {
      vmNotify("Эмоции: " + (j.analyzed || 0) + " сегментов", "success");
      _project = j.project;
      renderSegments();
      renderInspector();
    }
  };

  window.dsImportReview = async function () {
    const path = prompt("Имя review JSON из output/ (например video_xxx_review.json):");
    if (!path) return;
    const j = await api("/api/dub-studio/projects/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ review_path: path }),
    });
    if (j.ok) {
      vmNotify("Импорт OK", "success");
      await dsRefresh();
      dsLoadProject(j.project.project_id);
    } else vmNotify(j.error || "Error", "error");
  };

  window.dsLoadProject = async function (pid) {
    if (!pid) return;
    const j = await api("/api/dub-studio/projects/" + encodeURIComponent(pid));
    if (!j.ok) return;
    _project = j.project;
    _selectedSeg = null;
    renderTracks();
    renderSegments();
    renderInspector();
  };

  window.dsRefresh = async function () {
    const st = await api("/api/dub-studio/status");
    if (!st.ok) {
      document.getElementById("ds-off").style.display = "block";
      document.getElementById("ds-panel").style.display = "none";
      return;
    }
    document.getElementById("ds-off").style.display = "none";
    document.getElementById("ds-panel").style.display = "block";
    _plugins = st.plugins || [];
    renderPlugins();
    const pj = await api("/api/dub-studio/projects");
    const sel = document.getElementById("ds-project-select");
    if (sel) {
      sel.innerHTML = (pj.projects || [])
        .map(function (p) {
          return '<option value="' + p.project_id + '">' + (p.title || p.project_id) + "</option>";
        })
        .join("");
      if (pj.projects && pj.projects[0]) dsLoadProject(pj.projects[0].project_id);
    }
  };

  document.addEventListener("DOMContentLoaded", dsRefresh);
})();
