(function () { "use strict"; const $ = (s) => document.querySelector(s); const blocked = $("#pp-blocked"); const panel = $("#pp-panel"); function devHeaders() { const h = { "Content-Type": "application/json" }; try { if (localStorage.getItem("vm_client_dev_mode") === "1") { h["X-VM-Dev-Mode"] = "1"; }
      const mode = localStorage.getItem("vm_user_mode"); if (mode) h["X-VM-User-Mode"] = mode; } catch (_) {} return h; }

  async function checkDev() { try { const r = await fetch("/api/modules/status", { headers: devHeaders() }); const j = await r.json(); if (j.developer_mode) { blocked.style.display = "none"; panel.style.display = ""; loadModules(); return; }
    } catch (_) {} blocked.style.display = ""; panel.style.display = "none"; }

  async function loadModules() { const el = $("#pp-modules"); if (!el) return; try { const r = await fetch("/api/pipeline/platform/status", { headers: devHeaders() }); const j = await r.json(); if (!j.ok) return; el.innerHTML = (j.stages || []) .map((s) => { const cls = s.status === "ok" ? "stable" : "dev"; const emoji = s.status === "ok" ? "" : ""; return `<div class="pp-mod ${cls}">${emoji} ${s.label || s.stage_id}</div>`; }) .join(""); } catch (_) {} }

  function renderDiff(diff) { if (!diff || !diff.length) return "<span>—</span>"; return diff .map((d) => { const cls = d.tag === "delete" ? "pp-diff-del" : "pp-diff-add"; return `<span class="${cls}">${escapeHtml(d.text)}</span>`; }) .join(" "); }

  function escapeHtml(s) { return String(s || "") .replace(/&/g, "&amp;") .replace(/</g, "&lt;") .replace(/>/g, "&gt;"); }

  function renderView(view) { const segs = $("#pp-segments"); const log = $("#pp-log"); if (!view) return; log.value = view.copy_text || ""; segs.innerHTML = (view.segments || []) .map((seg) => { const stages = (seg.chain || []) .map((st, i) => { const sid = `st-${seg.segment_index}-${i}`; return `
            <div class="pp-stage" id="${sid}"><div class="pp-stage-h" data-target="${sid}"><span>${escapeHtml(st.label)} — <em>${escapeHtml(st.status)}</em></span>
                <span>${st.processing_ms || 0}ms · Q=${st.quality_score != null ? st.quality_score : "—"}</span>
              </div>
              <div class="pp-stage-b"><div>engine: ${escapeHtml(st.engine || "—")}</div>
                <div>duration: ${st.duration_ms || 0}ms</div>
                <div>text: ${escapeHtml(st.text || "")}</div> ${st.audio_path ? `<div>audio: ${escapeHtml(st.audio_path)}</div>` : ""} ${st.rules_applied && st.rules_applied.length ? `<div>rules: ${escapeHtml(st.rules_applied.join(", "))}</div>` : ""} ${st.warnings && st.warnings.length ? `<div>warnings: ${escapeHtml(st.warnings.join(", "))}</div>` : ""} ${st.errors && st.errors.length ? `<div>errors: ${escapeHtml(st.errors.join(", "))}</div>` : ""}
                <div>diff: ${renderDiff(st.diff_from_previous)}</div>
              </div>
            </div>`; }) .join(""); return `
        <div class="pp-seg"><div class="pp-seg-h">Сегмент #${seg.segment_index}: ${escapeHtml(seg.original_text || "")}</div> ${stages}
        </div>`; }) .join(""); segs.querySelectorAll(".pp-stage-h").forEach((h) => { h.addEventListener("click", () => { const t = document.getElementById(h.dataset.target); if (t) t.classList.toggle("open"); }); }); }

  async function loadTrace() { const taskId = ($("#pp-task-id") || {}).value || ""; if (!taskId) return; const r = await fetch(`/api/pipeline/platform/task/${encodeURIComponent(taskId)}`, { headers: devHeaders(), }); const j = await r.json(); if (j.ok) renderView(j.view); }

  async function testSegment() { const text = prompt("Текст сегмента для теста:", "Hello, this is a test segment."); if (!text) return; const r = await fetch("/api/pipeline/platform/dev-view", { method: "POST", headers: devHeaders(), body: JSON.stringify({ info: { segments_data: [{ index: 0, text: text, source_text: text }], source_lang: "en", target_lang: "uk", translation_audits: [ { index: 0, source_text: text, raw_translation: text, final_text: text }, ], }, }), }); const j = await r.json(); if (j.ok) renderView(j.view); }

  function copyLog() { const log = $("#pp-log"); if (!log || !log.value) return; navigator.clipboard.writeText(log.value).catch(() => { log.select(); document.execCommand("copy"); }); }

  $("#pp-load")?.addEventListener("click", loadTrace); $("#pp-test")?.addEventListener("click", testSegment); $("#pp-copy")?.addEventListener("click", copyLog); checkDev();
})();
