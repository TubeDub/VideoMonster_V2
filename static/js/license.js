/* license.js — клиентская лицензия VideoMonster V2 */

let _licenseCache = null;

async function fetchLicenseStatus(force = false) {
  if (_licenseCache && !force) return _licenseCache;
  try {
    const r = await fetch('/api/license/status');
    _licenseCache = await r.json();
    return _licenseCache;
  } catch {
    return { tier: 'basic', is_basic: true, features: {} };
  }
}

async function syncLicense() {
  try {
    const r = await fetch('/api/license/sync', { method: 'POST' });
    _licenseCache = await r.json();
    return _licenseCache;
  } catch {
    return fetchLicenseStatus(true);
  }
}

function applyLicenseBanner(status) {
  const el = document.getElementById('license-banner');
  if (!el) return;

  const path = window.location.pathname || '';
  const onDubOrStudio = path.startsWith('/dub') || path.startsWith('/studio');

  if (status.demo_expired && (status.is_local_install || onDubOrStudio)) {
    el.style.display = 'none';
    return;
  }

  let msg = '';
  if (status.demo_expired) {
    msg = status.message || 'Демо завершено. Доступен базовый режим.';
  } else if (status.sync_warning) {
    msg = status.sync_warning;
  } else if (status.server_message) {
    msg = status.server_message;
  }

  if (!msg) {
    el.style.display = 'none';
    return;
  }

  el.style.display = 'block';
  el.classList.toggle('license-banner-info', !!status.demo_expired);
  el.querySelector('.license-banner-text').textContent = msg;
  const icon = el.querySelector('.license-banner-icon');
  if (icon) icon.textContent = status.demo_expired ? 'ℹ️' : '⏳';
}

function applyLicenseFeatures(status) {
  document.body.dataset.licenseTier = status.tier || 'basic';

  const premiumLocked = !(status.features && status.features.auto_dub);
  document.querySelectorAll('.feat-premium-item, .feat-pro-item').forEach(el => {
    el.style.color = premiumLocked ? 'var(--text2)' : 'var(--success)';
    if (premiumLocked && !el.dataset.orig) {
      el.dataset.orig = el.textContent;
      if (!el.textContent.startsWith('🔒')) el.textContent = '🔒 ' + el.textContent.replace(/^🔒\s*/, '');
    } else if (!premiumLocked && el.dataset.orig) {
      el.textContent = el.dataset.orig.replace(/^🔒\s*/, '✅ ');
    }
  });
}

async function initLicenseUI() {
  const status = await fetchLicenseStatus();
  applyLicenseBanner(status);
  applyLicenseFeatures(status);
  return status;
}

async function ensureFeature(feature, friendlyMsg) {
  const s = await fetchLicenseStatus();
  if (s.features && s.features[feature]) return true;
  vmNotify(friendlyMsg || s.message || 'Функция недоступна с текущей лицензией.', 'warning', 6000);
  return false;
}

document.addEventListener('DOMContentLoaded', () => {
  initLicenseUI();
  syncLicense().then(s => {
    applyLicenseBanner(s);
    applyLicenseFeatures(s);
  });
  setInterval(() => syncLicense().then(s => {
    applyLicenseBanner(s);
    applyLicenseFeatures(s);
  }), 6 * 60 * 60 * 1000);
});
