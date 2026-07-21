/**
 * RASM R0–R5 — dual playback + metrics + detectors + timeline + reports.
 * Master clock = Studio video currentTime.
 */
(function (global) {
  function _t(key, fallback) {
    if (typeof global.vmT === "function") return global.vmT(key, fallback);
    return fallback || key;
  }

  function RasmPlayer(options) {
    this.taskId = options.taskId || "";
    this.videoEl = options.videoEl || null;
    this.getDubAudio = options.getDubAudio || function () { return null; };
    this.getSegments = options.getSegments || function () { return []; };
    this.onSeekMs = options.onSeekMs || null;
    this.onSelectSegment = options.onSelectSegment || null;
    this.onModeChange = options.onModeChange || function () {};
    this.enabled = false;
    this.active = false;
    this.settings = {
      reference_audio_volume: 0.15,
      dub_volume: 1.0,
      listen_mode: "dual",
      ab_hotkey_enabled: true,
      playback_rate: 1.0,
      auto_pause_on_error: true,
      show_timeline: true,
      show_colors: true,
    };
    this.analysis = null;
    this.filter = "all"; // all | warn | red
    this._origAudio = null;
    this._origReady = false;
    this._abHold = false;
    this._savedMode = null;
    this._driftTimer = null;
    this._panelEl = null;
    this._pausedSegs = {};
    this._loop = { on: false, times: 1, left: 0, segIndex: -1 };
    this._soloIndex = -1;
    this._currentIndex = -1;
    this._liveTimer = null;
  }

  RasmPlayer.prototype.loadSettings = function () {
    const self = this;
    return fetch("/api/studio/rasm/settings")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.ok && d.settings) {
          self.settings = Object.assign({}, self.settings, d.settings);
          self.enabled = !!d.enabled;
        }
        return self.settings;
      })
      .catch(function () { return self.settings; });
  };

  RasmPlayer.prototype.fetchStatus = function () {
    if (!this.taskId) return Promise.resolve(null);
    return fetch("/api/studio/rasm/status/" + encodeURIComponent(this.taskId))
      .then(function (r) { return r.json(); })
      .catch(function () { return null; });
  };

  RasmPlayer.prototype.analyze = function () {
    const self = this;
    if (!this.taskId) return Promise.resolve(null);
    return fetch("/api/studio/rasm/analyze/" + encodeURIComponent(this.taskId) + "?write=1")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.ok) {
          self.analysis = d;
          self._renderAnalysis();
        }
        return d;
      })
      .catch(function () { return null; });
  };

  RasmPlayer.prototype.ensureOriginal = function () {
    const self = this;
    if (!this.taskId) return Promise.resolve(false);
    if (this._origAudio && this._origReady) return Promise.resolve(true);
    if (!this._origAudio) {
      this._origAudio = document.createElement("audio");
      this._origAudio.preload = "auto";
      this._origAudio.src =
        "/api/studio/original/" + encodeURIComponent(this.taskId) + "?t=" + Date.now();
    }
    return new Promise(function (resolve) {
      const a = self._origAudio;
      function done(ok) {
        self._origReady = !!ok;
        resolve(!!ok);
      }
      a.addEventListener("canplaythrough", function onReady() {
        a.removeEventListener("canplaythrough", onReady);
        done(true);
      });
      a.addEventListener("error", function () { done(false); });
      a.load();
      setTimeout(function () { done(self._origReady || a.readyState >= 2); }, 5000);
    });
  };

  RasmPlayer.prototype.setActive = function (on) {
    this.active = !!on;
    if (this._panelEl) this._panelEl.style.display = this.active ? "flex" : "none";
    if (!this.active) {
      this._stopDriftWatch();
      this._stopLiveWatch();
      if (this._origAudio) this._origAudio.pause();
      this._applyVolumes();
      return;
    }
    const self = this;
    this.ensureOriginal().then(function () {
      self._applyMode();
      self._applyVolumes();
      self._applyRate();
      self._startDriftWatch();
      self._startLiveWatch();
      self.alignToMaster();
      self.analyze();
    });
  };

  RasmPlayer.prototype.toggle = function () {
    this.setActive(!this.active);
    return this.active;
  };

  RasmPlayer.prototype.setMode = function (mode) {
    const m = String(mode || "dual").toLowerCase();
    if (["dub", "original", "dual", "ab"].indexOf(m) < 0) return;
    this.settings.listen_mode = m;
    this._applyMode();
    this.onModeChange(m);
    this._persistPartial({ listen_mode: m });
    this._highlightMode();
  };

  RasmPlayer.prototype.setVolumes = function (refVol, dubVol) {
    if (typeof refVol === "number") {
      this.settings.reference_audio_volume = Math.max(0, Math.min(1, refVol));
    }
    if (typeof dubVol === "number") {
      this.settings.dub_volume = Math.max(0, Math.min(1, dubVol));
    }
    this._applyVolumes();
    this._persistPartial({
      reference_audio_volume: this.settings.reference_audio_volume,
      dub_volume: this.settings.dub_volume,
    });
  };

  RasmPlayer.prototype.setRate = function (rate) {
    this.settings.playback_rate = rate;
    this._applyRate();
    this._persistPartial({ playback_rate: rate });
  };

  RasmPlayer.prototype._persistPartial = function (patch) {
    fetch("/api/studio/rasm/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).catch(function () {});
  };

  RasmPlayer.prototype.masterTime = function () {
    return this.videoEl ? (this.videoEl.currentTime || 0) : 0;
  };

  RasmPlayer.prototype.alignToMaster = function () {
    const t = this.masterTime();
    if (this._origAudio && this._origReady && Math.abs(this._origAudio.currentTime - t) > 0.12) {
      try { this._origAudio.currentTime = t; } catch (_e) { /* ignore */ }
    }
    const dub = this.getDubAudio();
    if (dub && Math.abs(dub.currentTime - t) > 0.12) {
      try { dub.currentTime = t; } catch (_e2) { /* ignore */ }
    }
  };

  RasmPlayer.prototype._effectiveMode = function () {
    if (this._abHold) {
      return this._savedMode === "original" ? "dub" : "original";
    }
    return this.settings.listen_mode || "dual";
  };

  RasmPlayer.prototype._applyMode = function () {
    if (!this.active) return;
    const mode = this._effectiveMode();
    const dub = this.getDubAudio();
    const wantOrig = mode === "original" || mode === "dual";
    const wantDub = mode === "dub" || mode === "dual";
    if (this._origAudio) this._origAudio.muted = !wantOrig;
    if (dub) dub.muted = !wantDub;
    this._applyVolumes();
  };

  RasmPlayer.prototype._applyVolumes = function () {
    const dub = this.getDubAudio();
    if (this.active && this._origAudio) {
      this._origAudio.volume = this.settings.reference_audio_volume;
    }
    if (dub) dub.volume = this.active ? this.settings.dub_volume : 1.0;
  };

  RasmPlayer.prototype._applyRate = function () {
    const rate = this.settings.playback_rate || 1.0;
    [this.videoEl, this._origAudio, this.getDubAudio()].forEach(function (el) {
      if (!el) return;
      try { el.playbackRate = rate; } catch (_e) { /* ignore */ }
    });
  };

  RasmPlayer.prototype.syncPlay = function (play) {
    if (!this.active) return Promise.resolve();
    const self = this;
    return this.ensureOriginal().then(function () {
      self.alignToMaster();
      self._applyMode();
      self._applyVolumes();
      self._applyRate();
      if (play && self._origAudio && self._origReady && !self._origAudio.muted) {
        const p = self._origAudio.play();
        if (p && typeof p.catch === "function") p.catch(function () {});
      } else if (self._origAudio) {
        self._origAudio.pause();
      }
    });
  };

  RasmPlayer.prototype.pause = function () {
    if (this._origAudio) this._origAudio.pause();
  };

  RasmPlayer.prototype._startDriftWatch = function () {
    const self = this;
    this._stopDriftWatch();
    this._driftTimer = setInterval(function () {
      if (!self.active || !self.videoEl || self.videoEl.paused) return;
      self.alignToMaster();
    }, 250);
  };

  RasmPlayer.prototype._stopDriftWatch = function () {
    if (this._driftTimer) {
      clearInterval(this._driftTimer);
      this._driftTimer = null;
    }
  };

  RasmPlayer.prototype._startLiveWatch = function () {
    const self = this;
    this._stopLiveWatch();
    this._liveTimer = setInterval(function () {
      if (!self.active) return;
      self._updateLiveSegment();
    }, 200);
  };

  RasmPlayer.prototype._stopLiveWatch = function () {
    if (this._liveTimer) {
      clearInterval(this._liveTimer);
      this._liveTimer = null;
    }
  };

  RasmPlayer.prototype._rows = function () {
    return (this.analysis && this.analysis.segments) || [];
  };

  RasmPlayer.prototype._filteredRows = function () {
    const rows = this._rows();
    if (this.filter === "red") return rows.filter(function (r) { return r.status === "red"; });
    if (this.filter === "warn") {
      return rows.filter(function (r) { return r.status === "red" || r.status === "yellow"; });
    }
    return rows;
  };

  RasmPlayer.prototype._updateLiveSegment = function () {
    const rows = this._rows();
    if (!rows.length || !this.videoEl) return;
    const ms = Math.round((this.videoEl.currentTime || 0) * 1000);
    let idx = -1;
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (ms >= r.original_start_ms && ms < r.original_end_ms) {
        idx = i;
        break;
      }
    }
    if (idx < 0) {
      for (let j = 0; j < rows.length; j++) {
        if (ms < rows[j].original_start_ms) {
          idx = Math.max(0, j - 1);
          break;
        }
      }
      if (idx < 0) idx = rows.length - 1;
    }

    // Solo: pause when leaving segment
    if (this._soloIndex >= 0 && idx !== this._soloIndex && this.videoEl && !this.videoEl.paused) {
      const solo = rows[this._soloIndex];
      if (solo && ms >= solo.original_end_ms) {
        this.videoEl.pause();
        this.pause();
        const dub = this.getDubAudio();
        if (dub) dub.pause();
      }
    }

    // Loop
    if (this._loop.on && this._loop.segIndex >= 0) {
      const lr = rows[this._loop.segIndex];
      if (lr && ms >= lr.original_end_ms - 30) {
        if (this._loop.left > 1) {
          this._loop.left -= 1;
          this.seekToMs(lr.original_start_ms);
          if (this.videoEl && this.videoEl.paused) this.videoEl.play();
        } else {
          this._loop.on = false;
          this.videoEl.pause();
          this.pause();
        }
      }
    }

    if (idx !== this._currentIndex) {
      this._currentIndex = idx;
      this._renderCurrent(rows[idx]);
      this._highlightTimeline(idx);
      // Auto-pause on Red once per segment
      if (
        this.settings.auto_pause_on_error &&
        rows[idx] &&
        rows[idx].status === "red" &&
        !this._pausedSegs[rows[idx].segment_id] &&
        this.videoEl &&
        !this.videoEl.paused
      ) {
        this._pausedSegs[rows[idx].segment_id] = true;
        this.videoEl.pause();
        this.pause();
        const dub = this.getDubAudio();
        if (dub) dub.pause();
        this.setHint(
          _t("studio.rasm.auto_pause", "Auto-paused on RED:") +
            " #" + rows[idx].index + " " + (rows[idx].flags || []).join(", ")
        );
      }
    }
  };

  RasmPlayer.prototype.seekToMs = function (ms) {
    if (this.videoEl) {
      this.videoEl.currentTime = Math.max(0, ms / 1000);
    }
    this.alignToMaster();
    if (typeof this.onSeekMs === "function") this.onSeekMs(ms);
  };

  RasmPlayer.prototype.jumpNext = function (kind) {
    const rows = this._rows();
    if (!rows.length) return;
    const start = Math.max(0, this._currentIndex);
    const wantRed = kind === "red";
    for (let step = 1; step <= rows.length; step++) {
      const i = (start + step) % rows.length;
      const r = rows[i];
      if (wantRed && r.status !== "red") continue;
      if (!wantRed && r.status !== "red" && r.status !== "yellow") continue;
      this.seekToMs(r.original_start_ms);
      this._currentIndex = i;
      this._renderCurrent(r);
      this._highlightTimeline(i);
      if (typeof this.onSelectSegment === "function") this.onSelectSegment(i);
      return;
    }
    this.setHint(_t("studio.rasm.no_more_errors", "No more issues found."));
  };

  RasmPlayer.prototype.playSolo = function () {
    const rows = this._rows();
    const i = this._currentIndex >= 0 ? this._currentIndex : 0;
    if (!rows[i]) return;
    this._soloIndex = i;
    this.seekToMs(rows[i].original_start_ms);
    if (this.videoEl) {
      const p = this.videoEl.play();
      if (p && p.catch) p.catch(function () {});
    }
    this.syncPlay(true);
  };

  RasmPlayer.prototype.loopCurrent = function (times) {
    const rows = this._rows();
    const i = this._currentIndex >= 0 ? this._currentIndex : 0;
    if (!rows[i]) return;
    this._loop = { on: true, times: times || 3, left: times || 3, segIndex: i };
    this.seekToMs(rows[i].original_start_ms);
    if (this.videoEl) {
      const p = this.videoEl.play();
      if (p && p.catch) p.catch(function () {});
    }
    this.syncPlay(true);
    this.setHint(_t("studio.rasm.looping", "Looping segment") + " ×" + (times || 3));
  };

  RasmPlayer.prototype.bindAbHotkey = function () {
    const self = this;
    if (!this.settings.ab_hotkey_enabled) return;
    document.addEventListener("keydown", function (e) {
      if (!self.active) return;
      if (e.code !== "KeyB" || e.repeat) return;
      if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable)) {
        return;
      }
      e.preventDefault();
      self._abHold = true;
      self._savedMode = self.settings.listen_mode;
      self._applyMode();
    });
    document.addEventListener("keyup", function (e) {
      if (e.code !== "KeyB") return;
      if (!self._abHold) return;
      self._abHold = false;
      self._applyMode();
    });
  };

  RasmPlayer.prototype.mountPanel = function (hostEl) {
    if (!hostEl) return;
    const self = this;
    const panel = document.createElement("div");
    panel.id = "rasm-panel";
    panel.className = "rasm-panel";
    panel.style.display = "none";
    panel.innerHTML =
      '<div class="rasm-panel-title">' + _t("studio.rasm.title", "Sync QC") + "</div>" +
      '<div class="rasm-modes">' +
      '<button type="button" class="btn btn-sm rasm-mode" data-mode="dub">' + _t("studio.rasm.mode_dub", "Dub") + "</button>" +
      '<button type="button" class="btn btn-sm rasm-mode" data-mode="original">' + _t("studio.rasm.mode_orig", "Original") + "</button>" +
      '<button type="button" class="btn btn-sm rasm-mode" data-mode="dual">' + _t("studio.rasm.mode_dual", "Dual") + "</button>" +
      '<button type="button" class="btn btn-sm rasm-mode" data-mode="ab" title="Hold B">' + _t("studio.rasm.mode_ab", "A/B (B)") + "</button>" +
      "</div>" +
      '<label class="rasm-fader">' + _t("studio.rasm.vol_orig", "Original") +
      ' <input type="range" id="rasm-vol-orig" min="0" max="100" value="15"/>' +
      ' <span id="rasm-vol-orig-val">15%</span></label>' +
      '<label class="rasm-fader">' + _t("studio.rasm.vol_dub", "Dub") +
      ' <input type="range" id="rasm-vol-dub" min="0" max="100" value="100"/>' +
      ' <span id="rasm-vol-dub-val">100%</span></label>' +
      '<div class="rasm-speed">' +
      '<span>' + _t("studio.rasm.speed", "Speed") + "</span>" +
      '<button type="button" class="btn btn-sm rasm-rate" data-rate="0.5">0.5×</button>' +
      '<button type="button" class="btn btn-sm rasm-rate" data-rate="0.75">0.75×</button>' +
      '<button type="button" class="btn btn-sm rasm-rate active" data-rate="1">1×</button>' +
      '<button type="button" class="btn btn-sm rasm-rate" data-rate="1.25">1.25×</button>' +
      "</div>" +
      '<div class="rasm-nav">' +
      '<button type="button" class="btn btn-sm" id="rasm-next-red">' + _t("studio.rasm.next_red", "Next Red") + "</button>" +
      '<button type="button" class="btn btn-sm" id="rasm-next-warn">' + _t("studio.rasm.next_warn", "Next Warn") + "</button>" +
      '<button type="button" class="btn btn-sm" id="rasm-solo">' + _t("studio.rasm.solo", "Solo") + "</button>" +
      '<button type="button" class="btn btn-sm" id="rasm-loop">' + _t("studio.rasm.loop", "Loop×3") + "</button>" +
      "</div>" +
      '<div class="rasm-filter">' +
      '<button type="button" class="btn btn-sm rasm-filt active" data-filt="all">All</button>' +
      '<button type="button" class="btn btn-sm rasm-filt" data-filt="warn">Y+R</button>' +
      '<button type="button" class="btn btn-sm rasm-filt" data-filt="red">Red</button>' +
      '<button type="button" class="btn btn-sm" id="rasm-refresh">' + _t("studio.rasm.refresh", "Analyze") + "</button>" +
      '<a class="btn btn-sm" id="rasm-report-link" href="#" target="_blank">' + _t("studio.rasm.report", "Report") + "</a>" +
      "</div>" +
      '<div class="rasm-stats" id="rasm-stats"></div>' +
      '<div class="rasm-heatmap" id="rasm-heatmap" title="Heatmap"></div>' +
      '<div class="rasm-lanes" id="rasm-lanes"></div>' +
      '<div class="rasm-current" id="rasm-current"></div>' +
      '<label class="rasm-check"><input type="checkbox" id="rasm-autopause" checked/> ' +
      _t("studio.rasm.autopause", "Auto pause on Red") + "</label>" +
      '<div class="rasm-hint" id="rasm-hint"></div>';
    hostEl.appendChild(panel);
    this._panelEl = panel;

    panel.querySelectorAll(".rasm-mode").forEach(function (btn) {
      btn.addEventListener("click", function () {
        self.setMode(btn.getAttribute("data-mode"));
      });
    });
    panel.querySelectorAll(".rasm-rate").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const rate = parseFloat(btn.getAttribute("data-rate"));
        self.setRate(rate);
        panel.querySelectorAll(".rasm-rate").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });
      });
    });
    panel.querySelectorAll(".rasm-filt").forEach(function (btn) {
      btn.addEventListener("click", function () {
        self.filter = btn.getAttribute("data-filt") || "all";
        panel.querySelectorAll(".rasm-filt").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });
        self._renderTimeline();
        self._renderHeatmap();
      });
    });

    const vo = panel.querySelector("#rasm-vol-orig");
    const vd = panel.querySelector("#rasm-vol-dub");
    if (vo) {
      vo.value = String(Math.round((this.settings.reference_audio_volume || 0.15) * 100));
      vo.addEventListener("input", function () {
        const pct = parseInt(vo.value, 10) || 0;
        const label = panel.querySelector("#rasm-vol-orig-val");
        if (label) label.textContent = pct + "%";
        self.setVolumes(pct / 100, null);
      });
    }
    if (vd) {
      vd.value = String(Math.round((this.settings.dub_volume || 1) * 100));
      vd.addEventListener("input", function () {
        const pct = parseInt(vd.value, 10) || 0;
        const label = panel.querySelector("#rasm-vol-dub-val");
        if (label) label.textContent = pct + "%";
        self.setVolumes(null, pct / 100);
      });
    }

    const ap = panel.querySelector("#rasm-autopause");
    if (ap) {
      ap.checked = !!this.settings.auto_pause_on_error;
      ap.addEventListener("change", function () {
        self.settings.auto_pause_on_error = !!ap.checked;
        self._persistPartial({ auto_pause_on_error: self.settings.auto_pause_on_error });
      });
    }

    panel.querySelector("#rasm-next-red").onclick = function () { self.jumpNext("red"); };
    panel.querySelector("#rasm-next-warn").onclick = function () { self.jumpNext("warn"); };
    panel.querySelector("#rasm-solo").onclick = function () { self.playSolo(); };
    panel.querySelector("#rasm-loop").onclick = function () { self.loopCurrent(3); };
    panel.querySelector("#rasm-refresh").onclick = function () { self.analyze(); };

    const link = panel.querySelector("#rasm-report-link");
    if (link && this.taskId) {
      link.href = "/api/studio/rasm/report/" + encodeURIComponent(this.taskId) + "?format=html";
    }

    this._highlightMode();
  };

  RasmPlayer.prototype._highlightMode = function () {
    if (!this._panelEl) return;
    const mode = this.settings.listen_mode;
    this._panelEl.querySelectorAll(".rasm-mode").forEach(function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-mode") === mode);
    });
  };

  RasmPlayer.prototype._renderAnalysis = function () {
    if (!this._panelEl || !this.analysis) return;
    const st = this.analysis.stats || {};
    const el = this._panelEl.querySelector("#rasm-stats");
    if (el) {
      el.innerHTML =
        '<span class="g">G ' + (st.green || 0) + "</span> " +
        '<span class="y">Y ' + (st.yellow || 0) + "</span> " +
        '<span class="r">R ' + (st.red || 0) + "</span> · " +
        "avgΔ " + (st.avg_reserve_ms || 0) + "ms · maxOv " + (st.max_overflow_ms || 0) + "ms";
    }
    this._renderHeatmap();
    this._renderTimeline();
    if (this._currentIndex >= 0 && this.analysis.segments[this._currentIndex]) {
      this._renderCurrent(this.analysis.segments[this._currentIndex]);
    } else if (this.analysis.segments && this.analysis.segments[0]) {
      this._renderCurrent(this.analysis.segments[0]);
    }
  };

  RasmPlayer.prototype._renderHeatmap = function () {
    const host = this._panelEl && this._panelEl.querySelector("#rasm-heatmap");
    if (!host) return;
    const self = this;
    const rows = this._filteredRows();
    host.innerHTML = "";
    rows.forEach(function (r) {
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "rasm-hm rasm-hm-" + r.status;
      cell.title = "#" + r.index + " " + r.status + " " + (r.flags || []).join(",");
      cell.onclick = function () {
        self.seekToMs(r.original_start_ms);
        self._currentIndex = r.index;
        self._renderCurrent(r);
        self._highlightTimeline(r.index);
      };
      host.appendChild(cell);
    });
  };

  RasmPlayer.prototype._renderTimeline = function () {
    const host = this._panelEl && this._panelEl.querySelector("#rasm-lanes");
    if (!host || !this.analysis) return;
    const rows = this._filteredRows();
    if (!rows.length) {
      host.innerHTML = "";
      return;
    }
    const minT = Math.min.apply(null, rows.map(function (r) { return r.original_start_ms; }));
    const maxT = Math.max.apply(null, rows.map(function (r) {
      return Math.max(r.original_end_ms, r.dub_end_ms);
    }));
    const span = Math.max(1, maxT - minT);
    const self = this;
    let html = '<div class="rasm-lane-label">Original</div><div class="rasm-lane" id="rasm-lane-orig">';
    rows.forEach(function (r) {
      const left = ((r.original_start_ms - minT) / span) * 100;
      const width = Math.max(0.4, ((r.original_end_ms - r.original_start_ms) / span) * 100);
      html +=
        '<div class="rasm-bar rasm-bar-orig" data-idx="' + r.index + '" style="left:' +
        left + "%;width:" + width + '%"></div>';
    });
    html += '</div><div class="rasm-lane-label">Dub</div><div class="rasm-lane" id="rasm-lane-dub">';
    rows.forEach(function (r) {
      const left = ((r.dub_start_ms - minT) / span) * 100;
      const width = Math.max(0.4, ((r.dub_end_ms - r.dub_start_ms) / span) * 100);
      const cls = "rasm-bar rasm-bar-dub rasm-bar-" + r.status;
      html +=
        '<div class="' + cls + '" data-idx="' + r.index + '" style="left:' +
        left + "%;width:" + width + '%"></div>';
    });
    html += "</div>";
    host.innerHTML = html;
    host.querySelectorAll(".rasm-bar").forEach(function (bar) {
      bar.addEventListener("click", function () {
        const idx = parseInt(bar.getAttribute("data-idx"), 10);
        const r = self._rows()[idx];
        if (!r) return;
        self.seekToMs(r.original_start_ms);
        self._currentIndex = idx;
        self._renderCurrent(r);
        self._highlightTimeline(idx);
      });
    });
  };

  RasmPlayer.prototype._highlightTimeline = function (idx) {
    if (!this._panelEl) return;
    this._panelEl.querySelectorAll(".rasm-bar").forEach(function (bar) {
      bar.classList.toggle("current", parseInt(bar.getAttribute("data-idx"), 10) === idx);
    });
  };

  RasmPlayer.prototype._renderCurrent = function (r) {
    const el = this._panelEl && this._panelEl.querySelector("#rasm-current");
    if (!el || !r) return;
    const reserveLabel =
      r.overflow_ms > 0
        ? "Overflow −" + r.overflow_ms + " ms"
        : "Reserve +" + r.reserve_ms + " ms";
    el.innerHTML =
      '<div class="rasm-cur-head"><span class="rasm-dot rasm-dot-' + r.status + '"></span>' +
      "Segment #" + r.index + " · " + r.status.toUpperCase() +
      (r.sync_qc ? " · " + r.sync_qc : "") +
      "</div>" +
      "<div>Original: " + r.original_start_ms + "–" + r.original_end_ms +
      " (" + r.original_duration_ms + " ms)</div>" +
      "<div>Dub: " + r.dub_start_ms + "–" + r.dub_end_ms +
      " (" + r.dub_duration_ms + " ms)</div>" +
      "<div>" + reserveLabel + "</div>" +
      "<div>Flags: " + ((r.flags || []).join(", ") || "—") + "</div>";
  };

  RasmPlayer.prototype.setHint = function (text) {
    if (!this._panelEl) return;
    const el = this._panelEl.querySelector("#rasm-hint");
    if (el) el.textContent = text || "";
  };

  RasmPlayer.prototype.destroy = function () {
    this.setActive(false);
    this._stopDriftWatch();
    this._stopLiveWatch();
    if (this._origAudio) {
      this._origAudio.pause();
      this._origAudio.removeAttribute("src");
      this._origAudio.load();
      this._origAudio = null;
    }
    if (this._panelEl && this._panelEl.parentNode) {
      this._panelEl.parentNode.removeChild(this._panelEl);
    }
    this._panelEl = null;
  };

  global.RasmPlayer = RasmPlayer;
})(typeof window !== "undefined" ? window : globalThis);
