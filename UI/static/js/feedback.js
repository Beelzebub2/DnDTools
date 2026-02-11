/* ═══════════════════════════════════════════════════════════
   DnDTools — Feedback page behaviour
   ═══════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
    const FEEDBACK_URL = 'https://dndtools.rrmtools.uk/api/feedback';

    let selectedType = 'idea';

    // DOM refs
    const typeButtons = document.querySelectorAll('.feedback-type-btn');
    const labelText = document.getElementById('feedback-label-text');
    const messageEl = document.getElementById('feedback-message');
    const contactEl = document.getElementById('feedback-contact');
    const charCount = document.getElementById('feedback-chars');
    const submitBtn = document.getElementById('feedback-submit');
    const successEl = document.getElementById('feedback-success');
    const formCard = document.querySelector('.feedback-form-card');
    const typeRow = document.querySelector('.feedback-type-row');
    const resetBtn = document.getElementById('feedback-reset');

    const labels = {
        idea: { label: 'Describe your idea', placeholder: "Tell us what you'd like to see or what could be improved…" },
        bug: { label: 'Describe the bug', placeholder: 'What happened? What did you expect to happen instead?' },
    };

    // Type selector
    typeButtons.forEach((btn) => {
        btn.addEventListener('click', () => {
            selectedType = btn.dataset.type;
            typeButtons.forEach((b) => b.classList.toggle('active', b === btn));
            labelText.textContent = labels[selectedType].label;
            messageEl.placeholder = labels[selectedType].placeholder;
        });
    });

    // Character counter
    messageEl.addEventListener('input', () => {
        charCount.textContent = messageEl.value.length;
    });

    // Submit
    submitBtn.addEventListener('click', async () => {
        const message = messageEl.value.trim();
        if (!message || message.length < 3) {
            showNotification('Please write at least a few words.', 'warning');
            return;
        }

        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="material-icons" style="animation:spin .8s linear infinite">hourglass_top</span> Sending…';

        try {
            // Try to get app version from pywebview API
            let appVersion = null;
            try {
                if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_app_version === 'function') {
                    appVersion = await window.pywebview.api.get_app_version();
                }
            } catch (_) { /* ignore */ }

            const body = {
                type: selectedType,
                message,
                contact: contactEl.value.trim() || null,
                appVersion,
            };

            const res = await fetch(FEEDBACK_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });

            const data = await res.json();

            if (data.success) {
                formCard.style.display = 'none';
                typeRow.style.display = 'none';
                successEl.style.display = 'flex';
            } else {
                showNotification(data.error || 'Something went wrong. Please try again.', 'error');
            }
        } catch (err) {
            console.error('Feedback submit error:', err);
            showNotification('Could not reach the server. Check your internet connection.', 'error');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<span class="material-icons">send</span> <span>Submit Feedback</span>';
        }
    });

    // Reset form
    resetBtn.addEventListener('click', () => {
        messageEl.value = '';
        contactEl.value = '';
        charCount.textContent = '0';
        successEl.style.display = 'none';
        formCard.style.display = '';
        typeRow.style.display = '';
    });

    // Simple notification helper (uses app.js notification system if available)
    function showNotification(message, type) {
        if (typeof window.showNotification === 'function') {
            window.showNotification(message, type);
            return;
        }
        alert(message);
    }
});
