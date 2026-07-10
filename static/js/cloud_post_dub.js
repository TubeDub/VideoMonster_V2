/** Post-dub cloud save dialog — loaded when Cloud Platform enabled. */
(function () {
  async function cloudEnabled() {
    try {
      const r = await fetch("/api/cloud/status");
      const j = await r.json();
      return !!j.ok && !!j.enabled;
    } catch (_) {
      return false;
    }
  }

  function ensureModal() {
    if (document.getElementById("cloud-post-dub-modal")) return;
    const wrap = document.createElement("div");
    wrap.id = "cloud-post-dub-modal";
    wrap.style.cssText =
      "display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;align-items:center;justify-content:center;";
    wrap.innerHTML =
      '<div class="card" style="max-width:420px;padding:20px;margin:16px;">' +
      "<h3 style='margin:0 0 8px;'>Что сделать с проектом?</h3>" +
      "<p style='font-size:13px;opacity:.8;margin:0 0 14px;'>Дубляж завершён. Выберите, где хранить результат.</p>" +
      '<div style="display:flex;flex-direction:column;gap:8px;">' +
      '<button type="button" class="btn" data-action="keep_local">💻 Оставить на компьютере</button>' +
      '<button type="button" class="btn" data-action="cloud">☁️ Сохранить в облако</button>' +
      '<button type="button" class="btn" data-action="both">💻☁️ Сохранить в обоих местах</button>' +
      '<button type="button" class="btn" data-action="cloud_and_delete_local">☁️ В облако и удалить локальную копию</button>' +
      '<button type="button" class="btn btn-secondary" data-action="skip">Пропустить</button>' +
      "</div></div>";
    document.body.appendChild(wrap);
    wrap.addEventListener("click", function (e) {
      if (e.target === wrap) hide();
    });
    wrap.querySelectorAll("button[data-action]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const action = btn.getAttribute("data-action");
        hide();
        if (action !== "skip") submit(action);
      });
    });
  }

  let _pending = null;

  function show(outputFile, subtitleFile) {
    ensureModal();
    _pending = { outputFile: outputFile, subtitleFile: subtitleFile || null };
    const m = document.getElementById("cloud-post-dub-modal");
    if (m) m.style.display = "flex";
  }

  function hide() {
    const m = document.getElementById("cloud-post-dub-modal");
    if (m) m.style.display = "none";
  }

  async function submit(action) {
    if (!_pending) return;
    try {
      const r = await fetch("/api/cloud/post-dub", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: action,
          filename: _pending.outputFile,
          subtitle_file: _pending.subtitleFile,
        }),
      });
      const j = await r.json();
      if (j.ok) {
        const msg =
          action === "keep_local"
            ? "Проект сохранён локально"
            : action === "cloud_and_delete_local"
              ? "Загрузка в облако начата, локальная копия будет удалена после синхронизации"
              : "Синхронизация с облаком запущена";
        vmNotify(msg, "success", 5000);
      }
    } catch (e) {
      console.warn("cloud post-dub:", e);
    }
    _pending = null;
  }

  window.vmCloudPostDubPrompt = async function (outputFile, subtitleFile) {
    if (!(await cloudEnabled())) return;
    setTimeout(function () {
      show(outputFile, subtitleFile);
    }, 1200);
  };
})();
