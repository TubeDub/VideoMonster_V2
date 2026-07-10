/* i18n.js — локализация UI VideoMonster V2 */

let _i18nDict = {};
let _i18nLang = 'en';

const _SUPPORTED_LANGS = ['ru', 'uk', 'en', 'de'];

function detectUiLang() {
  const saved = localStorage.getItem('vm_ui_lang');
  if (saved && _SUPPORTED_LANGS.includes(saved)) return saved;
  const nav = (navigator.language || navigator.userLanguage || 'en').toLowerCase();
  if (nav.startsWith('uk')) return 'uk';
  if (nav.startsWith('ru')) return 'ru';
  if (nav.startsWith('de')) return 'de';
  if (nav.startsWith('en')) return 'en';
  return 'en';
}

async function loadI18n(lang) {
  _i18nLang = lang;
  try {
    const r = await fetch('/static/i18n/' + lang + '.json');
    if (r.ok) {
      _i18nDict = await r.json();
    }
  } catch (_) {
    _i18nDict = {};
  }
  applyI18n();
}

function vmT(key, fallback) {
  return _i18nDict[key] || fallback || key;
}
window.vmT = vmT;

function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    const text = vmT(key, el.textContent);
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
      el.placeholder = text;
    } else {
      el.textContent = text;
    }
  });
  document.documentElement.lang = _i18nLang;
  const sel = document.getElementById('ui-lang-select');
  if (sel) sel.value = _i18nLang;
}
window.applyI18n = applyI18n;

function setUiLang(lang) {
  if (!_SUPPORTED_LANGS.includes(lang)) return;
  localStorage.setItem('vm_ui_lang', lang);
  loadI18n(lang);
}

function getUiLang() {
  return _i18nLang || detectUiLang();
}
window.getUiLang = getUiLang;
window.setUiLang = setUiLang;

document.addEventListener('DOMContentLoaded', () => {
  loadI18n(detectUiLang());
});
