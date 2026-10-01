'use strict';
let currentLanguage = 'pt-BR';
function selectLanguage(language) {
  currentLanguage = language;
  document.documentElement.lang = language;
  const key = language === 'en' ? 'en' : 'pt';
  document.querySelectorAll('[data-pt][data-en]').forEach(element => {element.innerHTML = element.dataset[key];});
  document.querySelectorAll('[data-lang]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.lang === language)));
  document.title = language === 'en' ? 'QuotaLantern — Public source preview' : 'QuotaLantern — Prévia pública';
  const status = document.getElementById('demo-status');
  if (status) status.textContent = '';
}
document.querySelectorAll('[data-lang]').forEach(button => button.addEventListener('click', () => selectLanguage(button.dataset.lang)));
document.querySelectorAll('[data-demo-refresh]').forEach(button => button.addEventListener('click', () => {
  const status = document.getElementById('demo-status');
  if (!status) return;
  const provider = button.dataset.provider;
  status.textContent = currentLanguage === 'en'
    ? `${provider ? provider + ': ' : ''}Demo refresh complete. No provider request was sent.`
    : `${provider ? provider + ': ' : ''}Atualização de exemplo concluída. Nenhuma consulta ao provider foi enviada.`;
}));
