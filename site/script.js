/* MacSweep landing page — sidebar nav active state + live version pull. */

(function() {

  // ── Active sidebar item based on scroll position ────────────────────
  const sections = Array.from(document.querySelectorAll('.module'));
  const navItems = Array.from(document.querySelectorAll('.snav'));
  const byTarget = new Map(navItems.map(n => [n.dataset.target, n]));
  const canvas = document.querySelector('.canvas');

  function updateActive() {
    // Find the section whose top is closest to (but not past) the canvas
    // viewport's top + small offset.
    const scrollTop = canvas ? canvas.scrollTop : window.scrollY;
    const offset = 80;
    let current = sections[0];
    for (const s of sections) {
      const top = s.offsetTop;
      if (top <= scrollTop + offset) current = s;
    }
    if (!current) return;
    navItems.forEach(n => n.classList.remove('active'));
    const active = byTarget.get(current.id);
    if (active) active.classList.add('active');
  }

  if (canvas) {
    canvas.addEventListener('scroll', updateActive, { passive: true });
  } else {
    window.addEventListener('scroll', updateActive, { passive: true });
  }
  updateActive();

  // ── Smooth scroll within the canvas (rather than the page) ──────────
  navItems.forEach(n => {
    n.addEventListener('click', (e) => {
      const targetId = n.dataset.target;
      const target = document.getElementById(targetId);
      if (!target || !canvas) return;
      e.preventDefault();
      canvas.scrollTo({ top: target.offsetTop - 12, behavior: 'smooth' });
    });
  });

  // Also intercept in-page anchor links so they scroll the canvas
  document.querySelectorAll('a[href^="#"]').forEach(a => {
    if (a.classList.contains('snav')) return;
    a.addEventListener('click', (e) => {
      const id = a.getAttribute('href').slice(1);
      const target = document.getElementById(id);
      if (!target || !canvas) return;
      e.preventDefault();
      canvas.scrollTo({ top: target.offsetTop - 12, behavior: 'smooth' });
    });
  });

  // ── Live version pull from GitHub Releases ──────────────────────────
  // Fails silently if offline / rate-limited / repo doesn't exist yet.
  fetch('https://api.github.com/repos/polistician/macsweep/releases/latest')
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d || !d.tag_name) return;
      const v = d.tag_name.replace(/^v/, '');
      document.querySelectorAll('[data-latest-version]').forEach(el => {
        // Preserve the "v" prefix if it was there in the original text
        const txt = el.textContent;
        el.textContent = txt.startsWith('v') ? `v${v}` : v;
      });
    })
    .catch(() => {});
})();
