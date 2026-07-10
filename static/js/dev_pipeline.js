/** Dev Pipeline UI — TubeDub 2.0 */
(function () {
  const STAGES = [
    "preparing", "extract_audio", "transcribe", "translate", "tts", "timing", "dub", "done",
  ];

  function devMode() {
  return document.cookie.includes("vm_client_dev_mode=1") ||
    localStorage.getItem("vm_client_dev_mode") === "1";
  }

  function showPanel() {
    const blocked = document.getElementById("dp-blocked");
    const panel = document.getElementById("dp-panel");
    if (devMode()) {
      blocked.style.display = "none";
      panel.style.display = "block";
    } else {
      blocked.style.display = "block";
      panel.style.display = "none";
    }
  }

  async function loadTask(taskId) {
    if (!taskId) return;
    const res = await fetch(`/api/dev/pipeline/${encodeURIComponent(taskId)}`);
    const data = await res.json();
    if (!data.ok) {
      alert(data.error || "Ошибка загрузки");
      return;
    }
    const stagesEl = document.getElementById("dp-stages");
    stagesEl.innerHTML = "";
    const cur = data.step || "";
    STAGES.forEach((s) => {
      const div = document.createElement("div");
      div.className = "dp-stage " + (STAGES.indexOf(s) <= STAGES.indexOf(cur) ? "done" : "pending");
      div.textContent = s + (s === cur ? " ←" : "");
      stagesEl.appendChild(div);
    });

    const director = data.director || {};
    document.getElementById("dp-director").textContent =
      data.director && director.score !== undefined
        ? `Score: ${(director.score * 100).toFixed(0)}% | block_export: ${director.block_export}\n` +
          (director.issues || []).map((i) => `[${i.severity}] ${i.code}: ${i.message}`).join("\n")
        : "—";

    const wordsEl = document.getElementById("dp-words");
    wordsEl.innerHTML = "";
    const maps = (data.word_timing && data.word_timing.maps) || [];
    maps.slice(0, 3).forEach((m) => {
      (m.words || []).slice(0, 20).forEach((w) => {
        const span = document.createElement("span");
        span.textContent = `${w.text} [${w.start_ms}-${w.end_ms}]`;
        wordsEl.appendChild(span);
      });
    });

    const rep = await fetch(`/api/dev/pipeline/${encodeURIComponent(taskId)}/report`);
    const repData = await rep.json();
    document.getElementById("dp-log").value = repData.text || JSON.stringify(repData.report, null, 2);
  }

  document.getElementById("dp-load")?.addEventListener("click", () => {
    const tid = document.getElementById("dp-task-id").value.trim();
    loadTask(tid);
  });

  document.getElementById("dp-copy")?.addEventListener("click", () => {
    const log = document.getElementById("dp-log");
    log.select();
    document.execCommand("copy");
  });

  async function loadModules() {
    try {
      const res = await fetch("/api/dev/modules/readiness");
      const data = await res.json();
      const el = document.getElementById("dp-modules");
      if (!el || !data.modules) return;
      el.innerHTML = data.modules
        .filter((m) => m.readiness !== "GREEN")
        .slice(0, 12)
        .map((m) => `<div style="font-size:11px;margin-bottom:4px;">${m.readiness} ${m.label}</div>`)
        .join("");
    } catch (_) {}
  }

  showPanel();
  loadModules();
  const preset = document.getElementById("dp-task-id").value.trim();
  if (preset) loadTask(preset);
})();
