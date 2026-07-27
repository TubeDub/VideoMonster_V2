(function () {
  let _project = null;
  let _selectedSeg = null;
  let _selectedTrackId = null;
  let _plugins = [];
  let _previewAudio = null;
  let _previewPoll = null;

  async function api(url, opts) {
    const r = await fetch(url, opts);
    return r.json();
  }

  function statusDot(st) {
    const cls = st === "red" ? "red" : st === "yellow" ? "yellow" : "green";
    return '<span class="ds-dot ' + cls + '" title="' + st + '"></span>';
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function findTrack(trackId) {
    return (_project && _project.tracks || []).find(function (t) {
      return t.track_id === trackId;
    });
  }

  function defaultFxTrackId() {
    if (_selectedTrackId && findTrack(_selectedTrackId)) return _selectedTrackId;
    const tracks = (_project && _project.tracks) || [];
    const tts = tracks.find(function (t) { return t.kind === "tts"; });
    return (tts || tracks[0] || {}).track_id || null;
  }

  function applyTrackUpdate(trackId, track) {
    if (!_project || !track) return;
    const idx = (_project.tracks || []).findIndex(function (t) {
      return t.track_id === trackId;
    });
    if (idx >= 0) _project.tracks[idx] = track;
    renderTracks();
    renderFxChain();
    renderPlugins();
  }

  function renderTracks() {
    const el = document.getElementById("ds-tracks");
    if (!el || !_project) return;
    el.innerHTML = (_project.tracks || [])
      .map(function (t) {
        const active = _selectedTrackId === t.track_id;
        const volPct = Math.round(Math.max(0, Math.min(2, Number(t.volume) || 1)) * 100);
        const fxCount = (t.fx_chain || t.plugin_slots || []).length;
        const fxNames = (t.fx_chain || [])
          .map(function (f) { return f.plugin_id; })
          .filter(Boolean)
          .join(", ");
        return (
          '<div class="ds-track' + (active ? " ds-track-active" : "") + '" data-tid="' + esc(t.track_id) + '">' +
          '<div class="ds-track-row">' +
          '<button type="button" class="btn btn-sm ds-ctl' + (t.muted ? " ds-ctl-active" : "") +
          '" data-act="mute" title="Mute" style="' + (t.muted ? "background:var(--danger,#f87171);color:#111;" : "") +
          '">M</button>' +
          '<button type="button" class="btn btn-sm ds-ctl' + (t.solo ? " ds-ctl-active" : "") +
          '" data-act="solo" title="Solo" style="' + (t.solo ? "background:var(--amber,#fbbf24);color:#111;" : "") +
          '">S</button>' +
          '<button type="button" class="btn btn-sm ds-ctl" data-act="select" title="Выбрать для FX">' +
          esc(t.label || t.track_id) +
          "</button>" +
          '<span class="ds-track-kind">' + esc(t.kind) + "</span>" +
          "</div>" +
          '<div class="ds-track-vol">' +
          '<input type="range" min="0" max="200" value="' + volPct +
          '" data-act="volume" title="Volume ' + volPct + '%" />' +
          '<span class="ds-vol-lbl">' + volPct + "%</span>" +
          "</div>" +
          (fxCount
            ? '<div class="ds-track-fx" title="' + esc(fxNames) + '">FX: ' + esc(fxNames || String(fxCount)) + "</div>"
            : "") +
          "</div>"
        );
      })
      .join("");

    el.querySelectorAll(".ds-track").forEach(function (row) {
      const tid = row.getAttribute("data-tid");
      row.querySelectorAll("[data-act]").forEach(function (ctl) {
        const act = ctl.getAttribute("data-act");
        if (act === "volume") {
          ctl.addEventListener("input", function () {
            const lbl = row.querySelector(".ds-vol-lbl");
            if (lbl) lbl.textContent = ctl.value + "%";
          });
          ctl.addEventListener("change", function () {
            dsSetVolume(tid, parseInt(ctl.value, 10) / 100);
          });
        } else if (act === "mute") {
          ctl.addEventListener("click", function (e) {
            e.stopPropagation();
            dsToggleMute(tid);
          });
        } else if (act === "solo") {
          ctl.addEventListener("click", function (e) {
            e.stopPropagation();
            dsToggleSolo(tid);
          });
        } else if (act === "select") {
          ctl.addEventListener("click", function () {
            _selectedTrackId = tid;
            renderTracks();
            renderFxChain();
            renderPlugins();
          });
        }
      });
    });
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
          esc((s.text || "").slice(0, 60)) +
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
            esc(s.text || "") +
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
          esc(v.version_id) +
          '"' +
          (sel ? " selected" : "") +
          ">" +
          esc(v.label) +
          " (" +
          esc(v.source) +
          ")</option>"
        );
      })
      .join("");
    el.innerHTML =
      "<div><strong>Текст</strong><br>" +
      esc(s.text || "") +
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

  function renderFxChain() {
    const el = document.getElementById("ds-fx-chain");
    if (!el) return;
    const tid = defaultFxTrackId();
    const track = tid ? findTrack(tid) : null;
    const chain = (track && track.fx_chain) || [];
    const targetLabel = track ? (track.label || track.track_id) : "—";

    let slotsHtml;
    if (!track) {
      slotsHtml = '<div class="ds-fx-empty">Нет дорожки</div>';
    } else if (!chain.length) {
      slotsHtml = '<div class="ds-fx-empty">Цепочка пуста — добавьте плагин ниже</div>';
    } else {
      slotsHtml = chain
        .map(function (slot, idx) {
          const on = slot.enabled !== false;
          return (
            '<div class="ds-fx-slot' + (on ? "" : " off") + '" data-idx="' + idx + '">' +
            '<button type="button" class="btn btn-sm ds-ctl" data-fx="up" title="Выше" ' +
            (idx === 0 ? "disabled" : "") + ">↑</button>" +
            '<button type="button" class="btn btn-sm ds-ctl" data-fx="down" title="Ниже" ' +
            (idx >= chain.length - 1 ? "disabled" : "") + ">↓</button>" +
            '<button type="button" class="btn btn-sm ds-ctl' + (on ? " ds-ctl-active" : "") +
            '" data-fx="toggle" title="Вкл/Выкл" style="' +
            (on ? "background:var(--accent);color:#111;" : "") + '">' +
            (on ? "ON" : "OFF") +
            "</button>" +
            '<span class="ds-fx-slot-name" title="' + esc(slot.plugin_id) + '">' +
            esc(slot.plugin_id) +
            "</span>" +
            '<button type="button" class="btn btn-sm ds-ctl" data-fx="remove" title="Удалить">×</button>' +
            "</div>"
          );
        })
        .join("");
    }

    el.innerHTML =
      '<div class="ds-fx-chain">' +
      "<h4>Цепочка → " + esc(targetLabel) + "</h4>" +
      slotsHtml +
      '<div class="ds-fx-actions">' +
      '<button type="button" class="btn btn-sm btn-primary" id="ds-fx-preview"' +
      (!track || !chain.length ? " disabled" : "") +
      ">Preview FX</button>" +
      '<button type="button" class="btn btn-sm" id="ds-fx-stop">Stop</button>' +
      "</div></div>";

    el.querySelectorAll(".ds-fx-slot").forEach(function (row) {
      const idx = parseInt(row.getAttribute("data-idx"), 10);
      row.querySelectorAll("[data-fx]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          const act = btn.getAttribute("data-fx");
          if (act === "up") dsReorderFx(tid, idx, idx - 1);
          else if (act === "down") dsReorderFx(tid, idx, idx + 1);
          else if (act === "toggle") dsToggleFx(tid, idx);
          else if (act === "remove") dsRemoveFx(tid, idx);
        });
      });
    });
    const prevBtn = document.getElementById("ds-fx-preview");
    if (prevBtn) prevBtn.addEventListener("click", function () { dsPreviewFx(); });
    const stopBtn = document.getElementById("ds-fx-stop");
    if (stopBtn) stopBtn.addEventListener("click", function () { dsStopPreview(); });
  }

  function renderPlugins() {
    const el = document.getElementById("ds-plugins");
    if (!el) return;
    const tid = defaultFxTrackId();
    const track = tid ? findTrack(tid) : null;
    const targetLabel = track ? (track.label || track.track_id) : "—";
    el.innerHTML =
      '<div class="ds-fx-target">FX → <strong>' + esc(targetLabel) + "</strong></div>" +
      (_plugins || [])
        .map(function (p) {
          return (
            '<button type="button" class="ds-fx-item" data-pid="' +
            esc(p.plugin_id) +
            '" title="Добавить на выбранную дорожку">' +
            esc(p.label || p.plugin_id) +
            " <code>" +
            esc(p.plugin_id) +
            "</code></button>"
          );
        })
        .join("");
    el.querySelectorAll(".ds-fx-item").forEach(function (btn) {
      btn.addEventListener("click", function () {
        dsAddFx(btn.getAttribute("data-pid"));
      });
    });
  }

  async function patchTrack(trackId, body) {
    if (!_project) return null;
    const j = await api(
      "/api/dub-studio/projects/" + _project.project_id + "/tracks/" + encodeURIComponent(trackId),
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      }
    );
    if (j.ok && j.track) {
      applyTrackUpdate(trackId, j.track);
    } else if (j.error) {
      vmNotify(j.error, "error");
    }
    return j;
  }

  window.dsToggleMute = async function (trackId) {
    const t = findTrack(trackId);
    if (!t) return;
    await patchTrack(trackId, { muted: !t.muted });
  };

  window.dsToggleSolo = async function (trackId) {
    const t = findTrack(trackId);
    if (!t) return;
    const j = await patchTrack(trackId, { solo: !t.solo });
    // Solo clears others server-side — reload full project so all solo flags refresh
    if (j && j.ok && _project) await dsLoadProject(_project.project_id);
  };

  window.dsSetVolume = async function (trackId, volume) {
    await patchTrack(trackId, { volume: volume });
  };

  window.dsAddFx = async function (pluginId) {
    if (!_project || !pluginId) return;
    const trackId = defaultFxTrackId();
    if (!trackId) {
      vmNotify("Нет дорожки для FX", "warn");
      return;
    }
    _selectedTrackId = trackId;
    const j = await api(
      "/api/dub-studio/projects/" +
        _project.project_id +
        "/tracks/" +
        encodeURIComponent(trackId) +
        "/plugins",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plugin_id: pluginId }),
      }
    );
    if (j.ok && j.track) {
      applyTrackUpdate(trackId, j.track);
      vmNotify("FX «" + pluginId + "» → " + (j.track.label || trackId), "success");
    } else {
      vmNotify(j.error || "FX error", "error");
    }
  };

  window.dsReorderFx = async function (trackId, fromIdx, toIdx) {
    if (!_project || !trackId) return;
    if (toIdx < 0) return;
    const j = await api("/api/dub-studio/projects/" + _project.project_id + "/fx/reorder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: trackId, from_idx: fromIdx, to_idx: toIdx }),
    });
    if (j.ok && j.project) {
      _project = j.project;
      renderTracks();
      renderFxChain();
      renderPlugins();
    } else {
      vmNotify((j && j.error) || "Reorder failed", "error");
    }
  };

  window.dsToggleFx = async function (trackId, index) {
    if (!_project || !trackId) return;
    const track = findTrack(trackId);
    const slot = track && track.fx_chain && track.fx_chain[index];
    if (!slot) return;
    const j = await api(
      "/api/dub-studio/projects/" +
        _project.project_id +
        "/tracks/" +
        encodeURIComponent(trackId) +
        "/plugins/" +
        index,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !(slot.enabled !== false) }),
      }
    );
    if (j.ok && j.track) applyTrackUpdate(trackId, j.track);
    else vmNotify((j && j.error) || "Toggle failed", "error");
  };

  window.dsRemoveFx = async function (trackId, index) {
    if (!_project || !trackId) return;
    const j = await api(
      "/api/dub-studio/projects/" +
        _project.project_id +
        "/tracks/" +
        encodeURIComponent(trackId) +
        "/plugins/" +
        index,
      { method: "DELETE" }
    );
    if (j.ok && j.track) applyTrackUpdate(trackId, j.track);
    else vmNotify((j && j.error) || "Remove failed", "error");
  };

  window.dsStopPreview = function () {
    if (_previewPoll) {
      clearTimeout(_previewPoll);
      _previewPoll = null;
    }
    if (_previewAudio) {
      try {
        _previewAudio.pause();
        _previewAudio.src = "";
      } catch (e) {}
      _previewAudio = null;
    }
  };

  window.dsPreviewFx = async function () {
    if (!_project) return;
    const trackId = defaultFxTrackId();
    const track = trackId ? findTrack(trackId) : null;
    if (!track || !(track.fx_chain || []).length) {
      vmNotify("Добавьте FX на дорожку", "warn");
      return;
    }
    dsStopPreview();
    vmNotify("Рендер Preview FX…", "info");
    const body = {
      track_id: trackId,
      fx_chain: track.fx_chain || [],
    };
    if (_selectedSeg && _selectedSeg.segment_id) {
      body.segment_id = _selectedSeg.segment_id;
    }
    const j = await api(
      "/api/dub-studio/projects/" + _project.project_id + "/preview-fx",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }
    );
    if (!j.ok || !j.job_id) {
      vmNotify(j.error || "Preview failed", "error");
      return;
    }
    const jobId = j.job_id;
    let tries = 0;
    function poll() {
      fetch("/api/dub-studio/preview/" + encodeURIComponent(jobId))
        .then(function (r) {
          if (r.status === 202) {
            tries += 1;
            if (tries > 40) {
              vmNotify("Preview timeout", "error");
              return null;
            }
            _previewPoll = setTimeout(poll, 250);
            return null;
          }
          if (!r.ok) throw new Error("preview HTTP " + r.status);
          return r.blob();
        })
        .then(function (blob) {
          if (!blob) return;
          const url = URL.createObjectURL(blob);
          _previewAudio = new Audio(url);
          _previewAudio.onended = function () {
            URL.revokeObjectURL(url);
          };
          _previewAudio.play().catch(function () {
            vmNotify("Не удалось воспроизвести preview", "warn");
          });
          vmNotify("Preview FX", "success");
        })
        .catch(function (err) {
          vmNotify(String(err.message || err), "error");
        });
    }
    poll();
  };

  window.dsSelectSeg = function (id) {
    _selectedSeg = (_project.segments || []).find(function (s) {
      return s.segment_id === id;
    });
    renderSegments();
    renderInspector();
  };

  window.dsSolo = async function (trackId) {
    await dsToggleSolo(trackId);
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
    if (!_selectedTrackId || !findTrack(_selectedTrackId)) {
      _selectedTrackId = defaultFxTrackId();
    }
    renderTracks();
    renderSegments();
    renderInspector();
    renderFxChain();
    renderPlugins();
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
    renderFxChain();
    const pj = await api("/api/dub-studio/projects");
    const sel = document.getElementById("ds-project-select");
    if (sel) {
      sel.innerHTML = (pj.projects || [])
        .map(function (p) {
          return '<option value="' + esc(p.project_id) + '">' + esc(p.title || p.project_id) + "</option>";
        })
        .join("");
      if (pj.projects && pj.projects[0]) dsLoadProject(pj.projects[0].project_id);
    }
  };

  document.addEventListener("DOMContentLoaded", dsRefresh);
})();
