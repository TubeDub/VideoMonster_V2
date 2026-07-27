(function () {
  async function loadStatus() {
    const el = document.getElementById("platform-status");
    const off = document.getElementById("platform-off");
    try {
      const r = await fetch("/api/platform/status");
      const j = await r.json();
      el.textContent = JSON.stringify(j, null, 2);
      if (!j.platform_enabled) off.style.display = "block";
    } catch (e) {
      el.textContent = String(e);
    }
  }

  window.platformStreamingStart = async function () {
    const out = document.getElementById("stream-out");
    const fileEl = document.getElementById("stream-file");
    const input_file = (fileEl && fileEl.value || "").trim();
    const r = await fetch("/api/platform/streaming/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        microphone: document.getElementById("stream-mic").checked && !input_file,
        screen: document.getElementById("stream-screen").checked,
        rtmp_url: document.getElementById("stream-rtmp").value.trim(),
        input_file: input_file,
      }),
    });
    out.textContent = await r.text();
  };

  window.platformFileToRtmp = async function () {
    const out = document.getElementById("stream-out");
    const path = (document.getElementById("stream-file")?.value || "").trim();
    if (!path) {
      out.textContent = JSON.stringify({ ok: false, error: "Укажите путь к медиафайлу" });
      return;
    }
    const r = await fetch("/api/platform/streaming/file-to-rtmp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: path,
        rtmp_url: document.getElementById("stream-rtmp").value.trim(),
      }),
    });
    out.textContent = await r.text();
  };

  window.platformStreamingCaps = async function () {
    const out = document.getElementById("stream-out");
    const r = await fetch("/api/platform/streaming/capabilities");
    out.textContent = await r.text();
  };

  window.platformLiveStart = async function () {
    const source = document.getElementById("live-source").value.trim();
    const log = document.getElementById("live-events");
    log.textContent = "Starting…\n";
    const r = await fetch("/api/platform/live/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: source, tgt_lang: "ru" }),
    });
    const j = await r.json();
    if (!j.ok) {
      log.textContent += "Error: " + (j.error || r.status) + "\n";
      if (j.preflight) log.textContent += JSON.stringify(j.preflight, null, 2) + "\n";
      return;
    }
    const sid = j.session_id;
    log.textContent += "session=" + sid + "\n";
    const es = new EventSource("/api/platform/live/stream/" + sid);
    es.onmessage = (ev) => {
      log.textContent += ev.data + "\n";
      log.scrollTop = log.scrollHeight;
      try {
        const d = JSON.parse(ev.data);
        if (d.type === "session_end" || d.type === "completed" || d.type === "error") es.close();
      } catch (_) {}
    };
    es.onerror = () => es.close();
  };

  window.platformBrowserStart = async function () {
    const url = document.getElementById("browser-url").value.trim();
    const tgt = document.getElementById("browser-lang").value;
    const log = document.getElementById("browser-log");
    log.textContent = "Opening…";
    let r = await fetch("/api/platform/browser/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, tgt_lang: tgt }),
    });
    let j = await r.json();
    if (!j.ok) {
      log.textContent = j.error || "failed";
      return;
    }
    r = await fetch("/api/platform/browser/translate/" + j.session_id, { method: "POST" });
    j = await r.json();
    log.textContent = JSON.stringify(j, null, 2);
    if (j.events_url) {
      const es = new EventSource(j.events_url);
      es.onmessage = (ev) => {
        log.textContent += "\n" + ev.data;
      };
    }
  };

  window.platformVoiceAnalyze = async function () {
    const path = document.getElementById("voice-wav").value.trim();
    const script = document.getElementById("voice-script").value;
    const r = await fetch("/api/platform/voice-training/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wav_path: path, script }),
    });
    document.getElementById("voice-out").textContent = await r.text();
  };

  window.platformVocalAnalyze = async function () {
    const path = document.getElementById("vocal-wav").value.trim();
    const hz = document.getElementById("vocal-target-hz").value.trim();
    const body = { wav_path: path };
    if (hz) body.target_note_hz = parseFloat(hz);
    const r = await fetch("/api/platform/vocal-training/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    document.getElementById("vocal-out").textContent = await r.text();
  };

  window.platformRecordingSession = async function () {
    const out = document.getElementById("rec-out");
    const path = document.getElementById("rec-path").value.trim();
    let r = await fetch("/api/platform/recording/session", { method: "POST" });
    let j = await r.json();
    if (!j.ok) {
      out.textContent = JSON.stringify(j);
      return;
    }
    const sid = j.session_id;
    if (path) {
      r = await fetch("/api/platform/recording/import/" + sid, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      j = await r.json();
    }
    out.textContent = JSON.stringify({ session_id: sid, import: j }, null, 2);
  };

  window.platformPunchDemo = async function () {
    const out = document.getElementById("rec-out");
    let r = await fetch("/api/recording/punch-in", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    let j = await r.json();
    if (!j.ok) {
      out.textContent = JSON.stringify(j);
      return;
    }
    const sid = j.session.session_id;
    await new Promise((res) => setTimeout(res, 400));
    r = await fetch("/api/recording/punch-out", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid }),
    });
    out.textContent = await r.text();
  };

  const _micCapture = {
    stream: null,
    recorder: null,
    chunks: [],
    sessionId: null,
  };

  function _setMicUi(state, msg) {
    const status = document.getElementById("rec-mic-status");
    const startBtn = document.getElementById("rec-mic-start");
    const stopBtn = document.getElementById("rec-mic-stop");
    if (status) status.textContent = msg || state;
    if (startBtn) startBtn.disabled = state === "recording";
    if (stopBtn) stopBtn.disabled = state !== "recording";
  }

  function _pickRecorderMime() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
    for (const t of candidates) {
      if (MediaRecorder.isTypeSupported(t)) return t;
    }
    return "";
  }

  async function _stopMicTracks() {
    if (_micCapture.stream) {
      _micCapture.stream.getTracks().forEach((t) => t.stop());
      _micCapture.stream = null;
    }
  }

  window.platformMicPunchIn = async function () {
    const out = document.getElementById("rec-out");
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      out.textContent = JSON.stringify({
        ok: false,
        error: "getUserMedia_unavailable",
        hint: "Use HTTPS or localhost; browser mic permission required",
      });
      return;
    }
    if (!window.MediaRecorder) {
      out.textContent = JSON.stringify({ ok: false, error: "MediaRecorder_unavailable" });
      return;
    }
    try {
      _setMicUi("arming", "Requesting mic…");
      const r = await fetch("/api/recording/punch-in", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: "browser_mic" }),
      });
      const j = await r.json();
      if (!j.ok) {
        _setMicUi("idle", "Mic idle");
        out.textContent = JSON.stringify(j);
        return;
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
      const mime = _pickRecorderMime();
      const recorder = mime
        ? new MediaRecorder(stream, { mimeType: mime })
        : new MediaRecorder(stream);
      _micCapture.stream = stream;
      _micCapture.recorder = recorder;
      _micCapture.chunks = [];
      _micCapture.sessionId = j.session.session_id;
      recorder.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) _micCapture.chunks.push(ev.data);
      };
      recorder.start(250);
      _setMicUi("recording", "Recording… session " + _micCapture.sessionId);
      out.textContent = JSON.stringify(
        { ok: true, status: "recording", session_id: _micCapture.sessionId, mime: recorder.mimeType },
        null,
        2
      );
    } catch (e) {
      await _stopMicTracks();
      _setMicUi("idle", "Mic idle");
      out.textContent = JSON.stringify({ ok: false, error: String(e) });
    }
  };

  window.platformMicPunchOut = async function () {
    const out = document.getElementById("rec-out");
    const sid = _micCapture.sessionId;
    const recorder = _micCapture.recorder;
    if (!sid || !recorder) {
      out.textContent = JSON.stringify({ ok: false, error: "no_active_mic_session" });
      return;
    }
    _setMicUi("stopping", "Stopping…");
    const blob = await new Promise((resolve) => {
      recorder.onstop = () => {
        const type = recorder.mimeType || "audio/webm";
        resolve(new Blob(_micCapture.chunks, { type }));
      };
      try {
        if (recorder.state !== "inactive") recorder.stop();
        else resolve(new Blob(_micCapture.chunks, { type: "audio/webm" }));
      } catch (e) {
        resolve(new Blob(_micCapture.chunks, { type: "audio/webm" }));
      }
    });
    await _stopMicTracks();
    _micCapture.recorder = null;
    _micCapture.chunks = [];
    _micCapture.sessionId = null;

    const ext = (blob.type || "").includes("ogg")
      ? "ogg"
      : (blob.type || "").includes("mp4")
        ? "m4a"
        : "webm";
    const fd = new FormData();
    fd.append("session_id", sid);
    fd.append("apply_fx", "1");
    fd.append("file", blob, "mic_punch." + ext);
    try {
      const r = await fetch("/api/recording/punch-out", { method: "POST", body: fd });
      const text = await r.text();
      out.textContent = text;
      _setMicUi("idle", "Mic idle — uploaded");
    } catch (e) {
      out.textContent = JSON.stringify({ ok: false, error: String(e), session_id: sid });
      _setMicUi("idle", "Mic idle — upload failed");
    }
  };

  window.platformBroadcastStart = async function () {
    const out = document.getElementById("stream-out");
    const src = document.getElementById("broadcast-src").value.trim();
    const r = await fetch("/api/platform/broadcast-dub/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        audio_source: src,
        rtmp_url: document.getElementById("stream-rtmp").value.trim(),
        tgt_lang: "ru",
      }),
    });
    out.textContent = await r.text();
  };

  async function _startInterpMode(endpoint, sourceEl, outEl) {
    const source = document.getElementById(sourceEl).value.trim();
    const out = document.getElementById(outEl);
    out.textContent = "Starting…\n";
    const r = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: source, tgt_lang: "ru" }),
    });
    const j = await r.json();
    out.textContent = JSON.stringify(j, null, 2);
    if (j.ok && j.events_url) {
      const es = new EventSource(j.events_url);
      es.onmessage = (ev) => {
        out.textContent += "\n" + ev.data;
        out.scrollTop = out.scrollHeight;
      };
      es.onerror = () => es.close();
    }
  }

  window.platformInterpreterStart = function () {
    return _startInterpMode(
      "/api/platform/interpreter/start",
      "interp-source",
      "interp-out"
    );
  };

  window.platformScreenDubStart = function () {
    return _startInterpMode(
      "/api/platform/screen-dub/start",
      "screen-source",
      "screen-out"
    );
  };

  loadStatus();
})();
