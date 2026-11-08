// Taal volgt Home Assistant: Nederlands als HA Nederlands is, anders Engels. ?lang=en of ?lang=nl overschrijft.
// In het Engels vertaalt deze laag alle Nederlandse teksten die op de pagina verschijnen, ook teksten die later worden getekend.
const LANG = (() => { const q = new URLSearchParams(location.search).get('lang'); if (q) return q.toLowerCase().startsWith('nl') ? 'nl' : 'en';
  let l = ''; try { if (window.top !== window) l = window.top.document.documentElement.lang || ''; } catch (e) {}
  if (!l) { try { l = JSON.parse(window.top.localStorage.getItem('selectedLanguage') || 'null') || ''; } catch (e) {} }
  if (!l) l = navigator.language || 'en';
  return String(l).toLowerCase().startsWith('nl') ? 'nl' : 'en'; })();
const LOCALE = LANG === 'en' ? 'en-GB' : 'nl-NL';
const T = (nl, en) => LANG === 'en' ? en : nl;
document.documentElement.lang = LANG;
const I18N = (() => {
  const exact = new Map(Object.entries(__I18N_EXACT__));
  const pats = (__I18N_PATTERNS__).map(([re, rep]) => [new RegExp('^' + re + '$', 's'), rep]);
  function tr(s){
    if (LANG !== 'en' || !s) return s;
    const k = s.replace(/\s+/g, ' ').trim(); if (!k || !/[A-Za-zÀ-ÿ]/.test(k)) return s;
    let r = exact.get(k);
    if (r === undefined) { for (const [re, rep] of pats) { if (re.test(k)) { r = k.replace(re, (...m) => rep.replace(/\$(\d)/g, (_, i) => tr(m[+i] || ''))); break; } } }
    if (r === undefined || r === k) return s;
    return s.match(/^\s*/)[0] + r + s.match(/\s*$/)[0];
  }
  const ATTRS = ['placeholder', 'aria-label', 'title', 'data-tip', 'data-l'];
  function el(e){
    for (const a of ATTRS) { const v = e.getAttribute && e.getAttribute(a); if (v) { const n = tr(v); if (n !== v) e.setAttribute(a, n); } }
  }
  function walk(root){
    if (LANG !== 'en' || !root) return;
    if (root.nodeType === 3) { const n = tr(root.nodeValue); if (n !== root.nodeValue) root.nodeValue = n; return; }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1) { if (['SCRIPT', 'STYLE'].includes(root.tagName)) return; el(root); }
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let n; while ((n = w.nextNode())) {
      if (n.nodeType === 3) { const p = n.parentElement; if (p && (p.tagName === 'SCRIPT' || p.tagName === 'STYLE')) continue; const v = tr(n.nodeValue); if (v !== n.nodeValue) n.nodeValue = v; }
      else el(n);
    }
  }
  if (LANG === 'en') {
    const start = () => { walk(document.body); document.title = tr(document.title);
      new MutationObserver(ms => { for (const m of ms) {
        if (m.type === 'characterData') walk(m.target);
        else if (m.type === 'attributes') el(m.target);
        else m.addedNodes.forEach(walk);
      } }).observe(document.body, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS}); };
    if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
  }
  return {tr, walk};
})();
