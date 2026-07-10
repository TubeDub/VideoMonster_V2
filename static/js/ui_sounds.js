/**
 * Light UI sounds — optional, off via settings (uiSounds: false).
 */
(function () {
  'use strict';

  let ctx = null;

  function enabled() {
    try {
      if (typeof loadSettings === 'function') {
        const s = loadSettings();
        return s.uiSounds !== false;
      }
    } catch (_) {}
    return true;
  }

  function audioCtx() {
    if (!ctx) {
      try {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
      } catch (_) {
        return null;
      }
    }
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
    return ctx;
  }

  function tone(freq, duration, gain, type) {
    const ac = audioCtx();
    if (!ac || !enabled()) return;
    const t0 = ac.currentTime;
    const osc = ac.createOscillator();
    const g = ac.createGain();
    osc.type = type || 'sine';
    osc.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);
    osc.connect(g);
    g.connect(ac.destination);
    osc.start(t0);
    osc.stop(t0 + duration + 0.02);
  }

  const presets = {
    click: () => tone(520, 0.06, 0.04, 'sine'),
    open: () => { tone(440, 0.05, 0.035); setTimeout(() => tone(560, 0.05, 0.03), 40); },
    select: () => tone(620, 0.07, 0.04, 'triangle'),
    start: () => { tone(380, 0.08, 0.045); setTimeout(() => tone(480, 0.1, 0.04), 60); },
    success: () => { tone(520, 0.08, 0.04); setTimeout(() => tone(660, 0.12, 0.035), 80); },
    error: () => tone(220, 0.14, 0.04, 'triangle'),
  };

  window.vmUiSound = function (name) {
    const fn = presets[name] || presets.click;
    try { fn(); } catch (_) {}
  };

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn, button, [role="button"]');
    if (!btn || btn.disabled) return;
    if (btn.dataset.noSound) return;
    vmUiSound('click');
  }, true);
})();
