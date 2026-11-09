// Language preference is optional: navigation still works when storage or JS is unavailable.
let savedLanguage;
try { savedLanguage = localStorage.getItem('bidash-language'); } catch {}
const language = document.documentElement.lang === 'en' ? 'en' : 'mk';
for (const link of document.querySelectorAll('[data-language]')) {
  link.addEventListener('click', () => {
    try { localStorage.setItem('bidash-language', link.dataset.language); } catch {}
    if (location.hash) link.hash = location.hash;
  });
}
if (location.pathname === '/' && savedLanguage === 'en') {
  location.replace('/en/' + location.search + location.hash);
} else {
  try { localStorage.setItem('bidash-language', language); } catch {}
}
