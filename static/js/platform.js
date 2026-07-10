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
    if (!j.ok && j.error) {
      log.textContent += "Error: " + j.error + "\n";
      return;
    }
    const sid = j.session_id;
    log.textContent += "session=" + sid + "\n";
    const es = new EventSource("/api/platform/live/stream/" + sid);
    es.onmessage = (ev) => {
      log.textContent += ev.data + "\n";
      log.scrollTop = log.scrollHeight;
      const d = JSON.parse(ev.data);
      if (d.type === "session_end" || d.type === "completed") es.close();
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

  loadStatus();
})();
