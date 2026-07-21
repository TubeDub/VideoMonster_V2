/**
 * Dub Studio timeline — post-dub editor + legacy text-studio timeline.
 */
(function (global) {
  function _t(key, fallback) {
    if (typeof global.vmT === "function") return global.vmT(key, fallback);
    return fallback || key;
  }

  const EDITOR_TRACKS = [
    { id: "original", label: "Original Voice", i18n_key: "studio.track.original", color: "#22c55e" },
    { id: "user_voice", label: "User Voice", i18n_key: "studio.track.user_voice", color: "#ec4899" },
    { id: "music", label: "Music", i18n_key: "studio.track.music", color: "#a855f7" },
    { id: "sfx", label: "SFX", i18n_key: "studio.track.sfx", color: "#64748b" },
    { id: "tts", label: "Dub Voice", i18n_key: "studio.track.dub", color: "#f59e0b" },
  ];

  const LEGACY_TRACKS = [
    { id: "video", label: "Video", color: "#6366f1" },
    { id: "original", label: "Original", color: "#22c55e" },
    { id: "translated", label: "Translated", color: "#38bdf8" },
    { id: "tts", label: "TTS", color: "#f59e0b" },
    { id: "user_voice", label: "User Voice", color: "#ec4899" },
    { id: "music", label: "Music", color: "#a855f7" },
    { id: "fx", label: "FX", color: "#64748b" },
  ];

  function statusColor(st) {
    if (st === "green") return "#22c55e";
    if (st === "yellow") return "#eab308";
    return "#ef4444";
  }

  function StudioTimeline(container, options) {
    this.container = container;
    this.inspectorEl = options.inspectorEl || null;
    this.pluginsEl = options.pluginsEl || null;
    this.videoEl = options.videoEl || null;
    this.durationMs = options.durationMs || 120000;
    this.sessionId = options.sessionId || options.taskId || "default";
    this.taskId = options.taskId || this.sessionId;
    this.editorMode = !!options.editorMode;
    this.segments = [];
    this.sourceSegments = [];
    this.pluginOrder = [];
    this.plugins = [];
    this.outputFile = null;
    this.taskStatus = null;
    this.videoPreview = null;
    this.tracks = (this.editorMode ? EDITOR_TRACKS : LEGACY_TRACKS).map(function (t) {
      return Object.assign({}, t, { muted: false, solo: false, volume: 1 });
    });
    this.selectedId = null;
    this.selectedIds = [];
    this.zoomLevel = 1;
    this._waveformCache = {};
    this.voice = options.voice || "ru-RU-DmitryNeural";
    this.playheadMs = 0;
    this._drag = null;
    this._pollTimer = null;
    this._previewAudio = null;
    this._previewReady = false;
    this._exporting = false;
    this._dirty = false;
    this._rasm = null;
    this._renderShell();
    this._bindTransport();
    this._bindZoom();
    this._initRasm();
  };

  StudioTimeline.prototype._initRasm = function () {
    const self = this;
    if (!this.editorMode || !global.RasmPlayer) return;
    this._rasm = new global.RasmPlayer({
      taskId: this.taskId,
      videoEl: this.videoEl,
      getDubAudio: function () { return self._previewAudio; },
    });
    const host = document.getElementById("rasm-panel-host");
    this._rasm.loadSettings().then(function () {
      if (host) self._rasm.mountPanel(host);
      self._rasm.bindAbHotkey();
    });
    const btn = document.getElementById("se-btn-sync-qc");
    if (btn) {
      btn.onclick = function () {
        if (!self._rasm) return;
        const on = self._rasm.toggle();
        btn.classList.toggle("active", on);
        if (on) {
          self._rasm.fetchStatus().then(function (st) {
            if (!st) return;
            if (!st.original_available) {
              self._rasm.setHint(
                _t(
                  "studio.rasm.no_original",
                  "Original audio unavailable — re-run dub (RASM keeps reference track)."
                )
              );
            } else {
              self._rasm.setHint(
                _t(
                  "studio.rasm.hint_r0",
                  "Sync QC: Dual listen · Heatmap · Next Red · Report. Hold B = A/B."
                )
              );
            }
          });
          self._syncPreviewAudio(false).then(function () {
            if (self.videoEl && !self.videoEl.paused) {
              self._rasm.syncPlay(true);
            }
          });
        }
      };
    }
  };

  StudioTimeline.prototype._trackLabel = function (track) {
    return _t(track.i18n_key, track.label);
  };

  StudioTimeline.prototype._bindZoom = function () {
    const self = this;
    if (!this.container) return;
    this.container.addEventListener("wheel", function (e) {
      const isTimelineWheel = (e.target && e.target.closest && e.target.closest(".studio-timeline")) || false;
      if (!isTimelineWheel) return;
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      self.zoomLevel = Math.max(0.5, Math.min(4, (self.zoomLevel || 1) * delta));
      if (self.trackArea) {
        self.trackArea.style.minWidth = Math.round(600 * self.zoomLevel) + "px";
      }
      self._renderAll();
    }, { passive: false });
  };

  StudioTimeline.prototype._loadWaveform = function (trackId) {
    const self = this;
    if (!this.editorMode || !this.taskId) return Promise.resolve(null);
    if (self._waveformCache[trackId]) return Promise.resolve(self._waveformCache[trackId]);
    return fetch(
      "/api/studio/waveform/" + encodeURIComponent(this.taskId) + "/" + encodeURIComponent(trackId)
    )
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.peaks) {
          self._waveformCache[trackId] = data;
          return data;
        }
        return null;
      })
      .catch(function () { return null; });
  };

  StudioTimeline.prototype._drawWaveformCanvas = function (lane, trackId, color) {
    const self = this;
    const canvas = document.createElement("canvas");
    canvas.className = "st-wave-canvas";
    canvas.width = Math.max(400, Math.round(600 * (self.zoomLevel || 1)));
    canvas.height = 30;
    canvas.style.cssText = "position:absolute;inset:2px;width:100%;height:100%;pointer-events:none;opacity:.55;";
    lane.appendChild(canvas);
    self._loadWaveform(trackId).then(function (data) {
      if (!data || !data.peaks) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = color || "#fff";
      const peaks = data.peaks;
      const step = w / peaks.length;
      for (let i = 0; i < peaks.length; i++) {
        const ph = Math.max(1, peaks[i] * (h - 4));
        ctx.fillRect(i * step, (h - ph) / 2, Math.max(1, step * 0.8), ph);
      }
    });
  };

  StudioTimeline.prototype._formatTime = function (ms) {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    return m + ":" + String(s % 60).padStart(2, "0");
  };

  StudioTimeline.prototype._esc = function (s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  };

  StudioTimeline.prototype._renderShell = function () {
    if (!this.container) return;
    this.container.innerHTML = "";
    this.container.classList.add("studio-timeline");
    this.rulerEl = document.createElement("div");
    this.rulerEl.className = "st-ruler";
    this.playheadEl = document.createElement("div");
    this.playheadEl.className = "st-playhead";
    this.rulerEl.appendChild(this.playheadEl);
    this.container.appendChild(this.rulerEl);
    this.trackArea = document.createElement("div");
    this.trackArea.className = "st-tracks";
    this.container.appendChild(this.trackArea);
  };

  StudioTimeline.prototype._bindTransport = function () {
    const self = this;
    const video = this.videoEl;
    const scrub = document.getElementById("se-scrubber");
    const btnPlay = document.getElementById("se-btn-play");
    const btnExport = document.getElementById("se-btn-export");

    if (btnPlay && video) {
      btnPlay.onclick = function () {
        if (video.paused) {
          self._syncPreviewAudio(true).then(function () {
            if (self._rasm && self._rasm.active) {
              return self._rasm.syncPlay(true).then(function () {
                video.play();
                btnPlay.textContent = _t("studio.pause", "⏸ Pause");
              });
            }
            video.play();
            btnPlay.textContent = _t("studio.pause", "⏸ Pause");
          });
        } else {
          video.pause();
          if (self._previewAudio) self._previewAudio.pause();
          if (self._rasm) self._rasm.pause();
          btnPlay.textContent = _t("studio.play", "▶ Play");
        }
      };
    }

    if (video) {
      video.muted = true;
      video.addEventListener("play", function () {
        self._syncPreviewAudio(true).then(function () {
          if (self._rasm && self._rasm.active) self._rasm.syncPlay(true);
        });
      });
      video.addEventListener("pause", function () {
        if (self._previewAudio) self._previewAudio.pause();
        if (self._rasm) self._rasm.pause();
      });
      video.addEventListener("seeked", function () {
        self._alignPreviewToVideo();
        if (self._rasm && self._rasm.active) self._rasm.alignToMaster();
      });
      video.addEventListener("timeupdate", function () {
        self.playheadMs = Math.round(video.currentTime * 1000);
        self._updatePlayhead();
        if (scrub && video.duration) {
          scrub.value = String(Math.round((video.currentTime / video.duration) * 1000));
        }
        const cur = document.getElementById("se-time-current");
        if (cur) cur.textContent = self._formatTime(self.playheadMs);
      });
    }

    if (scrub && video) {
      scrub.addEventListener("input", function () {
        if (!video.duration) return;
        video.currentTime = (parseInt(scrub.value, 10) / 1000) * video.duration;
      });
    }

    if (btnExport) {
      btnExport.onclick = function () {
        self._exportMp4({ download: true });
      };
    }

    const btnMix = document.getElementById("se-btn-mix");
    if (btnMix) {
      btnMix.onclick = function () {
        self._mixProject();
      };
    }

    const backLink = document.querySelector("#studio-editor-root a[href='/dub']");
    if (backLink && this.editorMode) {
      backLink.addEventListener("click", function (e) {
        e.preventDefault();
        self._exportMp4({ navigate: "/dub" });
      });
    }

    if (this.editorMode) {
      window.addEventListener("beforeunload", function () {
        if (!self._dirty || self._exporting || !self.taskId) return;
        try {
          navigator.sendBeacon(
            "/api/studio/export/" + encodeURIComponent(self.taskId),
            new Blob([JSON.stringify({ remux: true })], { type: "application/json" })
          );
        } catch (_e) { /* ignore */ }
      });
    }
  };

  StudioTimeline.prototype._alignPreviewToVideo = function () {
    if (!this.videoEl || !this._previewAudio) return;
    const t = this.videoEl.currentTime || 0;
    if (Math.abs(this._previewAudio.currentTime - t) > 0.15) {
      this._previewAudio.currentTime = t;
    }
    if (this._rasm && this._rasm.active) this._rasm.alignToMaster();
  };

  StudioTimeline.prototype._syncPreviewAudio = function (play) {
    const self = this;
    if (!this.editorMode || !this.taskId) return Promise.resolve();

    const ensure = function () {
      if (self._previewAudio) return Promise.resolve();
      self._previewAudio = document.createElement("audio");
      self._previewAudio.preload = "auto";
      self._previewAudio.src =
        "/api/studio/preview/" + encodeURIComponent(self.taskId) + "?t=" + Date.now();
      return new Promise(function (resolve) {
        self._previewAudio.addEventListener("canplaythrough", function onReady() {
          self._previewAudio.removeEventListener("canplaythrough", onReady);
          self._previewReady = true;
          resolve();
        });
        self._previewAudio.addEventListener("error", function () { resolve(); });
        self._previewAudio.load();
        setTimeout(resolve, 4000);
      });
    };

    return ensure().then(function () {
      if (!self._previewAudio) return;
      self._alignPreviewToVideo();
      if (play && self.videoEl && !self.videoEl.paused) {
        const p = self._previewAudio.play();
        if (p && typeof p.catch === "function") p.catch(function () {});
      }
    });
  };

  StudioTimeline.prototype._invalidatePreview = function () {
    this._previewReady = false;
    if (this._previewAudio) {
      this._previewAudio.pause();
      this._previewAudio.removeAttribute("src");
      this._previewAudio.load();
      this._previewAudio = null;
    }
    this._dirty = true;
  };

  StudioTimeline.prototype._exportMp4 = function (opts) {
    const self = this;
    opts = opts || {};
    if (self._exporting) return Promise.resolve();
    self._exporting = true;

    const statusEl = document.getElementById("se-export-status");
    const btnExport = document.getElementById("se-btn-export");
    if (statusEl) statusEl.textContent = "Сборка MP4…";
    if (btnExport) btnExport.disabled = true;

    self._persist();

    return fetch("/api/studio/export/" + encodeURIComponent(self.taskId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: self.sessionId,
        segments: self.segments,
        timing_map: self.segments.map(function (s) {
          return { start: s.start_ms, end: s.end_ms };
        }),
        remux: true,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        self._exporting = false;
        self._dirty = false;
        if (btnExport) btnExport.disabled = false;
        if (d.ok) {
          self.outputFile = d.output_file || self.outputFile;
          self.taskStatus = "done";
          self._invalidatePreview();
          if (statusEl) statusEl.textContent = "✓ MP4 готов";
          if (opts.download && d.download) {
            window.location.href = d.download;
          } else if (typeof vmNotify === "function") {
            vmNotify("MP4 собран", "success", 3000);
          }
          if (opts.navigate) {
            window.location.href = opts.navigate;
          }
        } else {
          if (statusEl) statusEl.textContent = d.error || "Ошибка экспорта";
          if (typeof vmNotify === "function") {
            vmNotify(d.error || "Ошибка экспорта MP4", "error", 5000);
          }
        }
        return d;
      })
      .catch(function (e) {
        self._exporting = false;
        if (btnExport) btnExport.disabled = false;
        if (statusEl) statusEl.textContent = "Ошибка сети";
        if (typeof vmNotify === "function") vmNotify(String(e), "error", 4000);
      });
  };

  StudioTimeline.prototype._updatePlayhead = function () {
    const pct = (this.playheadMs / Math.max(this.durationMs, 1)) * 100;
    if (this.playheadEl) this.playheadEl.style.left = pct + "%";
  };

  StudioTimeline.prototype._renderRuler = function () {
    if (!this.rulerEl) return;
    this.rulerEl.innerHTML = "";
    this.rulerEl.appendChild(this.playheadEl);
    const ticks = Math.max(4, Math.ceil(this.durationMs / 15000));
    for (let i = 0; i <= ticks; i++) {
      const ms = (this.durationMs / ticks) * i;
      const tick = document.createElement("span");
      tick.className = "st-ruler-tick";
      tick.style.left = (ms / Math.max(this.durationMs, 1)) * 100 + "%";
      tick.textContent = this._formatTime(ms);
      this.rulerEl.appendChild(tick);
    }
  };

  StudioTimeline.prototype.loadFromServer = function () {
    const self = this;
    const url = this.editorMode
      ? "/api/studio/session/" + encodeURIComponent(this.sessionId)
      : null;

    const chain = url
      ? fetch(url).then(function (r) { return r.json(); })
      : Promise.resolve(null);

    chain
      .then(function (sessionData) {
        if (sessionData && sessionData.ok && sessionData.session) {
          const s = sessionData.session;
          if (sessionData.ui_lang && typeof global.setUiLang === "function") {
            global.setUiLang(sessionData.ui_lang);
          }
          self.segments = s.segments || [];
          self.sourceSegments = s.source_segments || [];
          self.durationMs = s.duration_ms || self.durationMs;
          self.voice = s.voice || self.voice;
          self.outputFile = s.output_file || null;
          self.taskStatus = s.task_status || null;
          self.videoPreview = s.video_preview || null;
          if (s.tracks) {
            self.tracks.forEach(function (t) {
              const st = s.tracks[t.id];
              if (st) Object.assign(t, st);
            });
          }
          self.loadVideo(s.video_preview, s.video_path);
          self._renderAll();
          self._loadPlugins();
          self._syncPreviewAudio(false);
          self._updateMixButton();
          if (self.outputFile) {
            const el = document.getElementById("se-export-status");
            if (el) el.textContent = "✓ MP4 готов";
          } else if (self.taskStatus === "studio_ready" || self.taskStatus === null) {
            // Пайплайн готов / studio_ready → опрос не нужен, кнопка Свести видна
          } else {
            self._pollTaskStatus();
          }
          return null;
        }
        return fetch("/api/studio/tracks?session=" + encodeURIComponent(self.sessionId));
      })
      .then(function (r) {
        if (!r) return null;
        return r.json();
      })
      .then(function (data) {
        if (!data) return null;
        if (data.duration_ms) self.durationMs = data.duration_ms;
        if (data.tracks) {
          self.tracks.forEach(function (t) {
            const st = data.tracks[t.id];
            if (st) Object.assign(t, st);
          });
        }
        return fetch("/api/studio/segments?session=" + encodeURIComponent(self.sessionId));
      })
      .then(function (r) {
        if (!r) return null;
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        if (data.segments && data.segments.length) {
          self.segments = data.segments;
          self.sourceSegments = data.source_segments || [];
        } else if (global.studioSegments && global.studioSegments.length) {
          self.syncFromStudioJs();
        }
        if (data.duration_ms) self.durationMs = data.duration_ms;
        self._renderAll();
        self._loadPlugins();
      })
      .catch(function () {
        if (self.inspectorEl) {
          self.inspectorEl.innerHTML = '<div style="color:var(--danger);padding:10px;">Не удалось загрузить сессию</div>';
        }
      });
  };

  StudioTimeline.prototype.loadVideo = function (preview, videoPath) {
    if (!this.videoEl) return;
    const video = this.videoEl;
    const self = this;
    video.muted = true;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "");
    video.preload = "auto";

    let src = "";
    if (preview) {
      src = "/api/dub/preview_video/" + encodeURIComponent(preview);
    } else if (this.editorMode && this.taskId) {
      src = "/api/studio/video/" + encodeURIComponent(this.taskId);
    } else if (videoPath) {
      const name = String(videoPath).split(/[/\\]/).pop();
      if (name) src = "/api/dub/preview_video/" + encodeURIComponent(name);
    }
    if (!src) return;

    this.videoPreview = preview || this.videoPreview;
    video.src = src + (src.indexOf("?") >= 0 ? "&" : "?") + "t=" + Date.now();
    video.load();

    const total = document.getElementById("se-time-total");
    const onMeta = function () {
      if (self.videoEl && self.videoEl.duration && isFinite(self.videoEl.duration)) {
        self.durationMs = Math.max(self.durationMs, Math.round(self.videoEl.duration * 1000));
        if (total) total.textContent = self._formatTime(self.durationMs);
        self._renderRuler();
        self._renderTracks();
      }
    };
    video.addEventListener("loadedmetadata", onMeta, { once: true });
    video.addEventListener("error", function () {
      if (self.editorMode && self.taskId && src.indexOf("/api/studio/video/") < 0) {
        video.src = "/api/studio/video/" + encodeURIComponent(self.taskId) + "?t=" + Date.now();
        video.load();
      }
    }, { once: true });
  };

  StudioTimeline.prototype._setupVideo = function () {
    this.loadVideo(this.videoPreview, null);
  };

  StudioTimeline.prototype._pollTaskStatus = function () {
    const self = this;
    if (!this.taskId) return;
    if (this._pollTimer) clearInterval(this._pollTimer);
    this._pollTimer = setInterval(function () {
      fetch("/api/auto_dub/status/" + encodeURIComponent(self.taskId) + "?lite=1")
        .then(function (r) { return r.json(); })
        .then(function (d) {
          const el = document.getElementById("se-export-status");
          if (d.status === "done" && d.output_file) {
            self.outputFile = d.output_file;
            if (el) el.textContent = "✓ MP4 готов";
            clearInterval(self._pollTimer);
            self._updateMixButton();
          } else if (d.status === "studio_ready") {
            // Пайплайн остановлен, Studio ждёт сведения — прекращаем опрос
            clearInterval(self._pollTimer);
            self._updateMixButton();
          } else if (d.status === "running") {
            if (el) el.textContent = (d.step_label || "Сборка") + "…";
          }
        })
        .catch(function () {});
    }, 2000);
  };

  StudioTimeline.prototype._loadPlugins = function () {
    const self = this;
    if (!this.pluginsEl) return;
    fetch("/api/studio/plugins?session=" + encodeURIComponent(this.sessionId))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        self.plugins = data.plugins || [];
        self.pluginOrder = data.order || [];
        self._renderPlugins();
      })
      .catch(function () {});
  };

  StudioTimeline.prototype._renderPlugins = function () {
    const self = this;
    if (!this.pluginsEl) return;
    const byId = {};
    (self.plugins || []).forEach(function (p) { byId[p.id || p.plugin_id] = p; });
    const order = self.pluginOrder.length ? self.pluginOrder : (self.plugins || []).map(function (p) { return p.id || p.plugin_id; });
    this.pluginsEl.innerHTML = order
      .map(function (pid, idx) {
        const p = byId[pid] || { label: pid, id: pid };
        const label = p.i18n_key ? _t(p.i18n_key, p.label || pid) : (p.label || pid);
        return (
          '<div class="st-plugin-item" draggable="true" data-idx="' + idx + '" data-pid="' + self._esc(pid) + '">' +
          '<span>' + self._esc(label) + "</span>" +
          '<span style="opacity:.5;">⋮⋮</span></div>'
        );
      })
      .join("");

    this.pluginsEl.querySelectorAll(".st-plugin-item").forEach(function (el) {
      el.addEventListener("dragstart", function (e) {
        e.dataTransfer.setData("text/plain", el.dataset.idx);
      });
      el.addEventListener("dragover", function (e) {
        e.preventDefault();
        el.classList.add("drag-over");
      });
      el.addEventListener("dragleave", function () { el.classList.remove("drag-over"); });
      el.addEventListener("drop", function (e) {
        e.preventDefault();
        el.classList.remove("drag-over");
        const from = parseInt(e.dataTransfer.getData("text/plain"), 10);
        const to = parseInt(el.dataset.idx, 10);
        if (isNaN(from) || isNaN(to) || from === to) return;
        const next = order.slice();
        const item = next.splice(from, 1)[0];
        next.splice(to, 0, item);
        self.pluginOrder = next;
        self._renderPlugins();
        fetch("/api/studio/plugins/order", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: self.sessionId, order: next }),
        }).catch(function () {});
      });
    });
  };

  StudioTimeline.prototype.syncFromStudioJs = function () {
    const segs = global.studioSegments || [];
    const srcBox = document.getElementById("source-box");
    const srcLines = srcBox ? srcBox.value.split("\n").filter(function (l) { return l.trim(); }) : [];
    this.sourceSegments = srcLines;
    this.segments = segs.map(function (s, i) {
      return {
        id: String(i),
        index: i,
        start_ms: s.start_ms || i * 3200,
        end_ms: s.end_ms || i * 3200 + 3000,
        text: s.text || "",
        original: srcLines[i] || "",
        overflow_pct: s.overflow_pct || 0,
        container_status: s.container_status || "green",
        emotion: s.emotion || "neutral",
      };
    });
    const last = this.segments[this.segments.length - 1];
    if (last) this.durationMs = Math.max(this.durationMs, last.end_ms + 2000);
    this._persist();
  };

  StudioTimeline.prototype._persist = function () {
    this._dirty = true;
    fetch("/api/studio/segments/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: this.sessionId,
        segments: this.segments,
        timing_map: this.segments.map(function (s) {
          return { start: s.start_ms, end: s.end_ms };
        }),
        source_segments: this.sourceSegments,
        duration_ms: this.durationMs,
        voice: this.voice,
      }),
    }).catch(function () {});
  };

  StudioTimeline.prototype._renderAll = function () {
    this._renderRuler();
    this._renderTracks();
    this._updatePlayhead();
    this._updateMixButton();
  };

  StudioTimeline.prototype._renderTracks = function () {
    const self = this;
    if (!this.trackArea) return;
    this.trackArea.innerHTML = "";

    this.tracks.forEach(function (track) {
      const row = document.createElement("div");
      row.className = "st-track-row";

      const labelCol = document.createElement("div");
      labelCol.className = "st-track-label";
      labelCol.innerHTML =
        "<span>" + self._trackLabel(track) + "</span>" +
        '<div style="display:flex;gap:2px;align-items:center;">' +
        '<button type="button" class="btn btn-sm st-ctl" data-tid="' + track.id + '" data-act="0" title="' + _t("studio.mute", "Mute") + '" style="padding:0 5px;font-size:10px;">M</button>' +
        '<button type="button" class="btn btn-sm st-ctl" data-tid="' + track.id + '" data-act="1" title="' + _t("studio.solo", "Solo") + '" style="padding:0 5px;font-size:10px;">S</button>' +
        '<input type="range" min="0" max="100" value="' + Math.round((track.volume || 1) * 100) + '" class="st-vol" data-tid="' + track.id + '" style="width:48px;height:4px;" title="' + _t("studio.volume", "Volume") + '"/>' +
        "</div>";

      const lane = document.createElement("div");
      lane.className = "st-track-lane";
      lane.style.background = track.color + "12";
      lane.style.minWidth = Math.round(600 * (self.zoomLevel || 1)) + "px";

      if (track.id === "original" || track.id === "music" || track.id === "sfx") {
        self._drawWaveformCanvas(lane, track.id === "original" ? "original" : track.id, track.color);
      }

      if (track.id === "tts" || track.id === "translated" || track.id === "dub") {
        self.segments.forEach(function (seg) {
          const leftPct = (seg.start_ms / Math.max(self.durationMs, 1)) * 100;
          const wPct = Math.max(0.4, ((seg.end_ms - seg.start_ms) / Math.max(self.durationMs, 1)) * 100);
          const ttsFailed = seg.tts_status === "failed";
          let st = seg.container_status || "green";
          if (ttsFailed) st = "red";
          const blk = document.createElement("div");
          const isSelected = self.selectedIds.indexOf(String(seg.id)) >= 0 || String(self.selectedId) === String(seg.id);
          blk.className = "st-seg-block" + (isSelected ? " selected" : "") + (ttsFailed ? " st-seg-tts-failed" : "");
          blk.dataset.segId = seg.id;
          blk.title = ttsFailed
            ? (_t("studio.tts_failed", "TTS error") + ": " + (seg.text || "").slice(0, 80))
            : (seg.text || "").slice(0, 120);
          blk.style.left = leftPct + "%";
          blk.style.width = wPct + "%";
          blk.style.background = statusColor(st) + "99";
          blk.style.border = ttsFailed ? "2px solid #e53935" : ("1px solid " + statusColor(st));
          blk.style.boxShadow = ttsFailed ? "0 0 6px rgba(229,57,53,.6)" : "";
          blk.textContent = String((seg.index || 0) + 1);

          if (track.id === "tts" || track.id === "dub" || track.id === "translated") {
            self._attachDrag(blk, seg);
          }

          blk.onclick = function (e) {
            e.stopPropagation();
            if (e.shiftKey) {
              const sid = String(seg.id);
              const pos = self.selectedIds.indexOf(sid);
              if (pos >= 0) self.selectedIds.splice(pos, 1);
              else self.selectedIds.push(sid);
              self.selectedId = sid;
              self._renderTracks();
              return;
            }
            self.selectedIds = [String(seg.id)];
            self.selectSegment(seg.id);
            if (ttsFailed && seg.tts_error) {
              self._showTtsFailureModal(seg);
            } else if (st === "red" || (seg.overflow_pct || 0) > 15) {
              self._showOverflowModal(seg);
            }
          };
          lane.appendChild(blk);
        });
        if (track.id === "tts" || track.id === "dub" || track.id === "translated") {
          self._drawWaveformCanvas(lane, "tts", track.color);
        }
      } else if (track.id !== "original" && track.id !== "music" && track.id !== "sfx") {
        const bar = document.createElement("div");
        bar.style.cssText = "position:absolute;inset:2px;background:" + track.color + "33;border-radius:2px;";
        lane.appendChild(bar);
      }

      row.appendChild(labelCol);
      row.appendChild(lane);
      self.trackArea.appendChild(row);
    });

    this.trackArea.querySelectorAll(".st-ctl").forEach(function (btn) {
      btn.onclick = function () {
        self._toggleTrack(btn.dataset.tid, parseInt(btn.dataset.act, 10));
      };
    });
    this.trackArea.querySelectorAll(".st-vol").forEach(function (inp) {
      inp.onchange = function () {
        self._setVolume(inp.dataset.tid, parseInt(inp.value, 10) / 100);
      };
    });
  };

  StudioTimeline.prototype._attachDrag = function (el, seg) {
    const self = this;
    el.addEventListener("mousedown", function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      const lane = el.parentElement;
      const laneRect = lane.getBoundingClientRect();
      const startX = e.clientX;
      const origStart = seg.start_ms;
      const origEnd = seg.end_ms;
      const dur = origEnd - origStart;
      el.classList.add("dragging");

      function onMove(ev) {
        const dx = ev.clientX - startX;
        const dMs = Math.round((dx / laneRect.width) * self.durationMs);
        let newStart = Math.max(0, origStart + dMs);
        let newEnd = newStart + dur;
        if (newEnd > self.durationMs) {
          newEnd = self.durationMs;
          newStart = Math.max(0, newEnd - dur);
        }
        seg.start_ms = newStart;
        seg.end_ms = newEnd;
        el.style.left = (newStart / Math.max(self.durationMs, 1)) * 100 + "%";
      }

      function onUp() {
        el.classList.remove("dragging");
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        fetch("/api/studio/segment/" + encodeURIComponent(seg.id) + "/move", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: self.sessionId,
            start_ms: seg.start_ms,
            end_ms: seg.end_ms,
          }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.segment) Object.assign(seg, data.segment);
            self._persist();
          })
          .catch(function () { self._persist(); });
        self._invalidatePreview();
      }

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  };

  StudioTimeline.prototype._toggleTrack = function (trackId, action) {
    const t = this.tracks.find(function (x) { return x.id === trackId; });
    if (!t) return;
    if (action === 0) t.muted = !t.muted;
    if (action === 1) {
      t.solo = !t.solo;
      if (t.solo) {
        this.tracks.forEach(function (x) {
          if (x.id !== trackId) x.solo = false;
        });
      }
    }
    fetch("/api/studio/track/" + trackId + "/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: this.sessionId,
        muted: t.muted,
        solo: t.solo,
        volume: t.volume,
      }),
    }).catch(function () {});
    this._renderTracks();
  };

  StudioTimeline.prototype._setVolume = function (trackId, vol) {
    const t = this.tracks.find(function (x) { return x.id === trackId; });
    if (!t) return;
    t.volume = vol;
    fetch("/api/studio/track/" + trackId + "/volume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, volume: vol }),
    }).catch(function () {});
  };

  StudioTimeline.prototype.selectSegment = function (segId) {
    this.selectedId = String(segId);
    if (this.selectedIds.indexOf(this.selectedId) < 0) {
      this.selectedIds = [this.selectedId];
    }
    const seg = this.segments.find(function (s) { return String(s.id) === String(segId); });
    if (!seg || !this.inspectorEl) return;
    const self = this;
    const idx = seg.index != null ? seg.index : segId;
    const orig = seg.original || this.sourceSegments[idx] || "—";

    this.inspectorEl.innerHTML =
      '<div class="st-inspector">' +
      "<h4 style=\"margin:0 0 8px;\">" + _t("studio.segment_title", "Segment") + " #" + (parseInt(idx, 10) + 1) + "</h4>" +
      "<div><b>" + _t("studio.original", "Original") + ":</b><br/><span style=\"color:var(--text2);\">" + self._esc(orig) + "</span></div>" +
      '<div style="margin-top:8px;"><b>' + _t("studio.translation", "Translation") + ":</b><br/>' +
      '<textarea id="st-insp-text" class="input-control" rows="4" style="width:100%;margin-top:4px;">' + self._esc(seg.text || "") + "</textarea></div>" +
      "<div style=\"margin-top:8px;display:flex;gap:12px;flex-wrap:wrap;\">" +
      "<span>⏱ " + self._formatTime(seg.start_ms) + " – " + self._formatTime(seg.end_ms) + "</span>" +
      "<span style=\"color:" + statusColor(seg.container_status) + ";\">" + _t("studio.overflow", "overflow") + " " + (seg.overflow_pct || 0) + "%</span>" +
      "</div>" +
      '<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap;">' +
      '<button type="button" class="btn btn-sm btn-primary" id="st-btn-autofix">' + _t("studio.btn_autofix", "Исправить автоматически") + '</button>' +
      '<button type="button" class="btn btn-sm btn-primary" id="st-btn-regen">' + _t("studio.btn_regen", "Исправить вручную") + '</button>' +
      '<button type="button" class="btn btn-sm" id="st-btn-stretch">' + _t("studio.btn_stretch", "Time Stretch") + '</button>' +
      '<button type="button" class="btn btn-sm" id="st-btn-split">' + _t("studio.btn_split", "Split") + '</button>' +
      '<button type="button" class="btn btn-sm" id="st-btn-copy">' + _t("studio.btn_copy", "Copy") + '</button>' +
      '<button type="button" class="btn btn-sm" id="st-btn-delete">' + _t("studio.btn_delete", "Delete") + '</button>' +
      (self.selectedIds.length > 1
        ? ('<button type="button" class="btn btn-sm" id="st-btn-merge">' + _t("studio.btn_merge", "Merge selected") + '</button>')
        : "") +
      "</div>" +
      '<div id="st-insp-status" style="margin-top:8px;font-size:11px;color:var(--text2);"></div>' +
      "</div>";

    document.getElementById("st-btn-autofix").onclick = function () {
      seg.text = document.getElementById("st-insp-text").value;
      self._autoFix(seg.id);
    };
    document.getElementById("st-btn-regen").onclick = function () {
      seg.text = document.getElementById("st-insp-text").value;
      self._regenerate(seg.id);
    };
    document.getElementById("st-btn-stretch").onclick = function () {
      self._timeStretch(seg.id);
    };
    document.getElementById("st-btn-split").onclick = function () {
      self._splitSegment(seg.id);
    };
    document.getElementById("st-btn-copy").onclick = function () {
      self._copySegment(seg.id);
    };
    document.getElementById("st-btn-delete").onclick = function () {
      self._deleteSegment(seg.id);
    };
    const mergeBtn = document.getElementById("st-btn-merge");
    if (mergeBtn) {
      mergeBtn.onclick = function () {
        self._mergeSelected();
      };
    }
    document.getElementById("st-insp-text").addEventListener("change", function () {
      seg.text = this.value;
      self._persist();
    });

    if (this.videoEl) {
      this.videoEl.currentTime = (seg.start_ms || 0) / 1000;
    }
    this._renderTracks();
  };

  StudioTimeline.prototype._countRedSegments = function () {
    return (this.segments || []).filter(function (s) {
      return s.container_status === "red" || parseFloat(s.overflow_pct || 0) > 15;
    }).length;
  };

  StudioTimeline.prototype._updateMixButton = function () {
    const btnMix = document.getElementById("se-btn-mix");
    const dlLink = document.getElementById("se-download-link");
    const mixStatus = document.getElementById("se-mix-status");

    if (!btnMix) return;

    if (this.outputFile) {
      // MP4 уже готов — показываем кнопку скачивания
      btnMix.style.display = "none";
      if (dlLink) {
        dlLink.style.display = "inline-flex";
        dlLink.href = "/api/dub/download/" + encodeURIComponent(this.outputFile);
        dlLink.download = this.outputFile;
      }
      if (mixStatus) mixStatus.textContent = "✓ " + _t("studio.mix_done", "MP4 готов");
      return;
    }

    // Показываем кнопку с количеством проблем
    if (dlLink) dlLink.style.display = "none";
    btnMix.style.display = "inline-flex";
    btnMix.disabled = false;

    const red = this._countRedSegments();
    if (red > 0) {
      btnMix.textContent = "🎬 " + _t("studio.mix_project", "Свести проект") +
        " (" + red + " " + _t("studio.mix_problems", "проблемных") + ")";
      btnMix.classList.add("se-mix-has-issues");
    } else {
      btnMix.textContent = "🎬 " + _t("studio.mix_project", "Свести проект");
      btnMix.classList.remove("se-mix-has-issues");
    }
    if (mixStatus) mixStatus.textContent = "";
  };

  StudioTimeline.prototype._mixProject = function () {
    const self = this;
    if (!this.taskId) return;
    if (self._mixing) return;

    const red = self._countRedSegments();
    const btnMix = document.getElementById("se-btn-mix");
    const mixStatus = document.getElementById("se-mix-status");

    const doMix = function (force) {
      self._mixing = true;
      if (btnMix) { btnMix.disabled = true; btnMix.textContent = "⏳ " + _t("studio.mixing", "Сведение…"); }
      if (mixStatus) mixStatus.textContent = _t("studio.mixing", "Сведение…");

      fetch("/api/studio/mix/" + encodeURIComponent(self.taskId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: force }),
      })
        .then(function (r) { return r.json().then(function (d) { return { r: r, d: d }; }); })
        .then(function (pair) {
          const r = pair.r; const d = pair.d;
          self._mixing = false;
          if (r.status === 409 && d.error_code === "overflow_segments") {
            // Показываем подтверждение
            const count = d.overflow_count || 0;
            const msg = _t("studio.mix_confirm", "Есть {n} переполненных сегментов. Свести всё равно?")
              .replace("{n}", String(count));
            if (window.confirm(msg)) {
              doMix(true);
            } else {
              if (btnMix) btnMix.disabled = false;
              self._updateMixButton();
              if (mixStatus) mixStatus.textContent = "";
            }
            return;
          }
          if (d.ok) {
            self.outputFile = d.output_file || self.outputFile;
            self.taskStatus = "done";
            self._invalidatePreview();
            self._updateMixButton();
            if (mixStatus) mixStatus.textContent = "✓ " + _t("studio.mix_done", "MP4 готов");
            if (typeof vmNotify === "function") {
              vmNotify(_t("studio.mix_done", "MP4 готов"), "success", 4000);
            }
          } else {
            if (btnMix) btnMix.disabled = false;
            self._updateMixButton();
            const err = d.error || _t("studio.mix_error", "Ошибка сведения");
            if (mixStatus) mixStatus.textContent = "✗ " + err;
            if (typeof vmNotify === "function") vmNotify(err, "error", 6000);
          }
        })
        .catch(function (e) {
          self._mixing = false;
          if (btnMix) btnMix.disabled = false;
          self._updateMixButton();
          const msg = String(e) || _t("studio.mix_error", "Ошибка сети");
          if (mixStatus) mixStatus.textContent = "✗ " + msg;
          if (typeof vmNotify === "function") vmNotify(msg, "error", 5000);
        });
    };

    doMix(false);
  };

  StudioTimeline.prototype._showTtsFailureModal = function (seg) {
    const self = this;
    const err = seg.tts_error || {};
    const diag =
      err.diagnostic_block ||
      [
        err.error_type || "VoiceGenerationError",
        "segment_id: " + (err.segment_id || seg.segment_id || seg.id),
        "engine: " + (err.engine || err.engine_id || "?"),
        'text: "' + String(err.tts_text || err.text || seg.text || "").slice(0, 200) + '"',
        "reason: " + (err.reason || err.error_message || ""),
        "stage: " + (err.stage || "TTS Generation"),
        "pipeline_state: " + (err.pipeline_state || "PARTIAL"),
      ].join("\n");

    const backdrop = document.createElement("div");
    backdrop.className = "se-modal-backdrop";
    backdrop.innerHTML =
      '<div class="se-modal" style="max-width:520px;">' +
      '<h3 style="margin:0 0 8px;">' + _t("studio.tts_failed_title", "Ошибка озвучки (TTS)") + '</h3>' +
      '<pre style="font-size:11px;white-space:pre-wrap;background:var(--bg2,#111);padding:10px;border-radius:6px;max-height:240px;overflow:auto;margin:0 0 12px;">' +
      this._esc(diag) +
      "</pre>" +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
      '<button type="button" class="btn btn-primary" id="tts-regen">' + _t("studio.btn_regen", "Исправить вручную") + '</button>' +
      '<button type="button" class="btn btn-sm" id="tts-close">' + _t("studio.close", "Закрыть") + '</button>' +
      "</div></div>";
    document.body.appendChild(backdrop);
    backdrop.querySelector("#tts-close").onclick = function () { backdrop.remove(); };
    backdrop.querySelector("#tts-regen").onclick = function () {
      backdrop.remove();
      self._regenerate(seg.id);
    };
  };

  StudioTimeline.prototype._showOverflowModal = function (seg) {
    const self = this;
    const slotMs = Math.max(1, (seg.end_ms || 0) - (seg.start_ms || 0));
    const ttsMs = seg.tts_ms || seg.fitted_ms || 0;
    const pct = parseFloat(seg.overflow_pct || 0).toFixed(1);
    const infoLine =
      _t("studio.overflow_desc", "TTS exceeds container by") + " <strong>" + pct + "%</strong>" +
      " · " + _t("studio.overflow_modal.slot_ms", "слот") + ": " + slotMs + " мс" +
      " · " + _t("studio.overflow_modal.tts_ms", "TTS") + ": " + ttsMs + " мс";

    const backdrop = document.createElement("div");
    backdrop.className = "se-modal-backdrop";
    backdrop.innerHTML =
      '<div class="se-modal">' +
      '<h3 style="margin:0 0 8px;">' + _t("studio.overflow_title", "Переполнение слота") + '</h3>' +
      '<p style="font-size:13px;color:var(--text2);margin:0 0 14px;">' + infoLine + '</p>' +
      '<div style="display:flex;flex-direction:column;gap:8px;">' +
      '<button type="button" class="btn btn-primary" id="ov-autoshorten">' + _t("studio.btn_autoshorten", "Автосократить") + '</button>' +
      '<button type="button" class="btn btn-secondary" id="ov-regen">' + _t("studio.overflow_modal.btn_regen_tts", "Повторить TTS") + '</button>' +
      '<button type="button" class="btn btn-secondary" id="ov-stretch">' + _t("studio.btn_stretch", "Time Stretch") + '</button>' +
      '<button type="button" class="btn btn-secondary" id="ov-manual">' + _t("studio.btn_edit_text", "Редактировать текст") + '</button>' +
      '<button type="button" class="btn btn-sm" id="ov-keep">' + _t("studio.overflow_modal.btn_keep", "Оставить как есть") + '</button>' +
      '</div></div>';
    document.body.appendChild(backdrop);
    backdrop.querySelector("#ov-keep").onclick = function () { backdrop.remove(); };
    backdrop.querySelector("#ov-autoshorten").onclick = function () {
      backdrop.remove();
      self._autoFix(seg.id);
    };
    backdrop.querySelector("#ov-regen").onclick = function () {
      backdrop.remove();
      self._regenerate(seg.id);
    };
    backdrop.querySelector("#ov-manual").onclick = function () {
      backdrop.remove();
      self.selectSegment(seg.id);
      setTimeout(function () {
        const ta = document.getElementById("st-insp-text");
        if (ta) ta.focus();
      }, 50);
    };
    backdrop.querySelector("#ov-stretch").onclick = function () {
      backdrop.remove();
      self._timeStretch(seg.id);
    };
  };

  StudioTimeline.prototype._regenerate = function (segId) {
    const self = this;
    const status = document.getElementById("st-insp-status");
    if (status) status.textContent = "Генерация TTS…";
    const seg = this.segments.find(function (s) { return String(s.id) === String(segId); });
    fetch("/api/studio/segment/" + segId + "/regenerate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: this.sessionId,
        voice: this.voice,
        text: (seg || {}).text,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.segment) {
          const i = self.segments.findIndex(function (s) { return String(s.id) === String(segId); });
          if (i >= 0) self.segments[i] = data.segment;
        }
        if (status) {
          status.textContent = data.ok
            ? "Готово · overflow " + (data.overflow_pct || 0) + "%"
            : (data.error || "ошибка");
        }
        self._invalidatePreview();
        self._renderAll();
        self.selectSegment(segId);
      })
      .catch(function () {
        if (status) status.textContent = "Ошибка сети";
      });
  };

  StudioTimeline.prototype._autoFix = function (segId) {
    const self = this;
    const status = document.getElementById("st-insp-status");
    if (status) status.textContent = "AI Исправить (soft sync)…";
    fetch("/api/studio/segment/" + segId + "/auto-fix", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, voice: this.voice }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.segment) {
          const i = self.segments.findIndex(function (s) { return String(s.id) === String(segId); });
          if (i >= 0) self.segments[i] = data.segment;
        }
        if (status) {
          status.textContent = data.ok
            ? "Исправлено · " + (data.overflow_pct || 0) + "%"
            : "Частично · " + (data.overflow_pct || 0) + "%";
        }
        self._invalidatePreview();
        self._renderAll();
        self.selectSegment(segId);
      });
  };

  StudioTimeline.prototype._timeStretch = function (segId) {
    const self = this;
    const status = document.getElementById("st-insp-status");
    if (status) status.textContent = "Time stretch…";
    fetch("/api/studio/segment/" + segId + "/time-stretch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.segment) {
          const i = self.segments.findIndex(function (s) { return String(s.id) === String(segId); });
          if (i >= 0) self.segments[i] = data.segment;
        }
        if (status) status.textContent = "Stretch · overflow " + (data.overflow_pct || 0) + "%";
        self._invalidatePreview();
        self._renderAll();
        self.selectSegment(segId);
      });
  };

  StudioTimeline.prototype._splitSegment = function (segId) {
    const self = this;
    fetch("/api/studio/segment/" + segId + "/split", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        if (Array.isArray(data.segments)) self.segments = data.segments;
        self._invalidatePreview();
        self._renderAll();
        self.selectSegment(segId);
      });
  };

  StudioTimeline.prototype._copySegment = function (segId) {
    const self = this;
    fetch("/api/studio/segment/" + segId + "/copy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        if (Array.isArray(data.segments)) self.segments = data.segments;
        self._invalidatePreview();
        self._renderAll();
      });
  };

  StudioTimeline.prototype._deleteSegment = function (segId) {
    const self = this;
    fetch("/api/studio/segment/" + segId + "/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        if (Array.isArray(data.segments)) self.segments = data.segments;
        self.selectedIds = [];
        self.selectedId = null;
        self._invalidatePreview();
        self._renderAll();
      });
  };

  StudioTimeline.prototype._mergeSelected = function () {
    const self = this;
    const ids = (self.selectedIds || []).slice();
    if (ids.length < 2) return;
    fetch("/api/studio/segments/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: this.sessionId, segment_ids: ids }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        if (Array.isArray(data.segments)) self.segments = data.segments;
        self.selectedIds = [String((data.segment || {}).id || ids[0])];
        self.selectedId = self.selectedIds[0];
        self._invalidatePreview();
        self._renderAll();
        self.selectSegment(self.selectedId);
      });
  };

  StudioTimeline.prototype.setDuration = function (ms) {
    this.durationMs = ms;
    this._renderAll();
  };

  StudioTimeline.prototype.setSegments = function (segments, sourceSegments) {
    this.segments = segments;
    if (sourceSegments) this.sourceSegments = sourceSegments;
    this._persist();
    this._renderAll();
  };

  global.StudioTimeline = StudioTimeline;

  function taskIdFromUrl() {
    const p = new URLSearchParams(global.location.search);
    return (p.get("task_id") || p.get("task") || "").trim();
  }

  function initStudioEditor() {
    const taskId = taskIdFromUrl();
    const editorRoot = document.getElementById("studio-editor-root");
    const textRoot = document.getElementById("studio-text-root");
    if (!taskId || !editorRoot) return false;

    if (textRoot) textRoot.style.display = "none";
    editorRoot.style.display = "flex";
    const label = document.getElementById("se-task-label");
    if (label) label.textContent = "task: " + taskId.slice(0, 12) + "…";

    const timelineEl = document.getElementById("studio-timeline");
    if (!timelineEl) return false;

    global._studioEditor = new StudioTimeline(timelineEl, {
      sessionId: taskId,
      taskId: taskId,
      durationMs: 120000,
      inspectorEl: document.getElementById("studio-inspector"),
      pluginsEl: document.getElementById("studio-plugins-list"),
      videoEl: document.getElementById("se-video"),
      editorMode: true,
    });
    global._studioEditor.loadFromServer();
    if (typeof global.applyI18n === "function") global.applyI18n();
    return true;
  }

  document.addEventListener("DOMContentLoaded", function () {
    initStudioEditor();
  });
})(window);
