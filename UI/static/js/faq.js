/* ═══════════════════════════════════════════════════════════
   DnDTools — FAQ page behaviour (pill tabs)
   ═══════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
    const pills = document.querySelectorAll('.faq-pill');
    const sections = document.querySelectorAll('.faq-section');

    pills.forEach(pill => {
        pill.addEventListener('click', () => {
            const cat = pill.dataset.cat;

            pills.forEach(p => p.classList.toggle('active', p === pill));
            sections.forEach(s => s.classList.toggle('active', s.dataset.section === cat));
        });
    });
});
