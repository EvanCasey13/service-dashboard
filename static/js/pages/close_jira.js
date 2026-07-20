document.addEventListener('DOMContentLoaded', function() {
    const modalEl = document.getElementById('closeJiraModal');
    if (!modalEl) return;

    const modal = new bootstrap.Modal(modalEl);
    const loadingEl = document.getElementById('jiraTicketsLoading');
    const emptyEl = document.getElementById('jiraTicketsEmpty');
    const listEl = document.getElementById('jiraTicketsList');
    const checkboxesEl = document.getElementById('jiraTicketsCheckboxes');
    const selectAllEl = document.getElementById('jiraSelectAll');
    const closeBtn = document.getElementById('closeSelectedJiraBtn');

    let activeDeploymentName = null;
    let activeBtn = null;

    function statusBadgeClass(status) {
        const s = (status || '').toLowerCase();
        if (s === 'closed' || s === 'done' || s === 'resolved') return 'bg-success';
        if (s === 'in progress' || s === 'in review') return 'bg-primary';
        return 'bg-secondary';
    }

    function updateCloseBtn() {
        const checked = checkboxesEl.querySelectorAll('.jira-ticket-cb:checked:not([data-closed="true"])');
        closeBtn.disabled = checked.length === 0;
        const count = checked.length;
        closeBtn.innerHTML = `<i class="bi bi-check2-square me-1"></i>Close Selected (${count})`;
    }

    // Select All toggle
    selectAllEl.addEventListener('change', function() {
        checkboxesEl.querySelectorAll('.jira-ticket-cb:not([data-closed="true"])').forEach(cb => {
            cb.checked = selectAllEl.checked;
        });
        updateCloseBtn();
    });

    // Open modal on button click
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.close-jira-btn');
        if (!btn) return;

        activeDeploymentName = btn.getAttribute('data-deployment-name');
        activeBtn = btn;

        loadingEl.style.display = '';
        emptyEl.style.display = 'none';
        listEl.style.display = 'none';
        checkboxesEl.innerHTML = '';
        closeBtn.disabled = true;
        selectAllEl.checked = true;

        modal.show();

        fetch(`/deployments/jira_tickets/${encodeURIComponent(activeDeploymentName)}`)
        .then(r => r.json())
        .then(data => {
            loadingEl.style.display = 'none';
            if (!data.success || !data.tickets || !data.tickets.length) {
                emptyEl.style.display = '';
                return;
            }

            listEl.style.display = '';
            let html = '';
            data.tickets.forEach((t, i) => {
                const isClosed = ['closed', 'done', 'resolved'].includes((t.status || '').toLowerCase());
                html += `
                <div class="form-check mb-2">
                    <input class="form-check-input jira-ticket-cb" type="checkbox"
                           id="jiraCb${i}" value="${t.ticket_id}"
                           ${isClosed ? 'data-closed="true" disabled' : 'checked'}>
                    <label class="form-check-label" for="jiraCb${i}">
                        <a href="https://redhat.atlassian.net/browse/${t.ticket_id}" target="_blank"
                           class="text-decoration-none fw-semibold">${t.ticket_id}</a>
                        <span class="badge ${statusBadgeClass(t.status)}" style="font-size: 0.7rem;">${t.status || 'Unknown'}</span>
                        <span class="text-muted ms-1">${t.title || ''}</span>
                        ${t.assigned_user ? `<small class="text-muted"> (${t.assigned_user})</small>` : ''}
                    </label>
                </div>`;
            });
            checkboxesEl.innerHTML = html;

            checkboxesEl.querySelectorAll('.jira-ticket-cb').forEach(cb => {
                cb.addEventListener('change', updateCloseBtn);
            });
            updateCloseBtn();
        })
        .catch(err => {
            loadingEl.style.display = 'none';
            emptyEl.textContent = 'Error loading Jira tickets.';
            emptyEl.style.display = '';
            console.error(err);
        });
    });

    // Close selected tickets
    closeBtn.addEventListener('click', function() {
        const selectedIds = [];
        checkboxesEl.querySelectorAll('.jira-ticket-cb:checked:not([data-closed="true"])').forEach(cb => {
            selectedIds.push(cb.value);
        });

        if (!selectedIds.length) return;

        closeBtn.disabled = true;
        closeBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Closing...';

        fetch(`/deployments/close_jira_tickets/${encodeURIComponent(activeDeploymentName)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticket_ids: selectedIds })
        })
        .then(r => r.json())
        .then(result => {
            if (result.success) {
                const closedCount = result.closed.length;
                const failedCount = result.failed.length;

                // Update checkboxes to reflect closed status
                result.closed.forEach(tid => {
                    const cb = checkboxesEl.querySelector(`.jira-ticket-cb[value="${tid}"]`);
                    if (cb) {
                        cb.checked = false;
                        cb.disabled = true;
                        cb.setAttribute('data-closed', 'true');
                        const badge = cb.closest('.form-check').querySelector('.badge');
                        if (badge) {
                            badge.className = 'badge bg-success';
                            badge.style.fontSize = '0.7rem';
                            badge.textContent = 'Closed';
                        }
                    }
                });

                let msg = `Closed ${closedCount} ticket(s).`;
                if (failedCount > 0) {
                    msg += ` Failed: ${result.failed.map(f => f.ticket_id).join(', ')}`;
                }
                alert(msg);
                updateCloseBtn();

                if (activeBtn) {
                    activeBtn.innerHTML = `<i class="bi bi-check-circle me-1"></i>Jira Closed (${closedCount})`;
                    activeBtn.classList.remove('btn-outline-success');
                    activeBtn.classList.add('btn-success');
                }
            } else {
                alert('Error: ' + (result.error || 'Unknown error'));
                closeBtn.disabled = false;
                updateCloseBtn();
            }
        })
        .catch(err => {
            console.error(err);
            alert('Error closing Jira tickets');
            closeBtn.disabled = false;
            updateCloseBtn();
        });
    });
});
