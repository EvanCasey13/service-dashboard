/**
 * Bot Dependency Consolidation page functionality.
 */

document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('consolidatorForm');
    const findPrsBtn = document.getElementById('findPrsBtn');
    const discoveryResults = document.getElementById('discoveryResults');
    const discoverySummary = document.getElementById('discoverySummary');
    const ecosystemGroups = document.getElementById('ecosystemGroups');
    const dryRunBtn = document.getElementById('dryRunBtn');
    const runConsolidationBtn = document.getElementById('runConsolidationBtn');

    const consolidationModalEl = document.getElementById('consolidationModal');
    const consolidationModal = new bootstrap.Modal(consolidationModalEl);

    const upstreamRepoSelect = document.getElementById('upstreamRepoSelect');

    const ECOSYSTEM_ICONS = {
        python: 'bi-filetype-py',
        npm: 'bi-filetype-json',
        go: 'bi-box-seam',
    };

    function getFormPayload() {
        return {
            upstream_repo: upstreamRepoSelect.value,
            bot_author: document.getElementById('botAuthor').value.trim(),
            regenerate_locks: document.getElementById('regenerateLocks').checked,
            close_originals: document.getElementById('closeOriginals').checked,
        };
    }

    form.addEventListener('submit', function(event) {
        event.preventDefault();

        findPrsBtn.disabled = true;
        findPrsBtn.innerHTML = '<div class="spinner-border spinner-border-sm me-2" role="status"><span class="visually-hidden">Searching...</span></div>Searching...';
        discoveryResults.style.display = 'none';

        const payload = getFormPayload();

        fetch('/bot-dependency-consolidator/discover', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    renderDiscoveryResults(data);
                } else {
                    showFlashMessage(data.error || 'Failed to discover PRs', 'danger');
                }
            })
            .catch(error => {
                console.error('Error discovering bot PRs:', error);
                showFlashMessage('Failed to discover PRs. Please try again.', 'danger');
            })
            .finally(() => {
                findPrsBtn.disabled = false;
                findPrsBtn.innerHTML = '<i class="bi bi-search me-1"></i>Find bot PRs';
            });
    });

    function renderDiscoveryResults(data) {
        const total = data.total || 0;

        document.getElementById('discoveryLog').textContent = (data.log || []).join('\n');

        if (total === 0) {
            discoverySummary.textContent = `No open PRs found from ${document.getElementById('botAuthor').value.trim()}.`;
            ecosystemGroups.innerHTML = renderExistingConsolidationSection(data.existing_consolidation_prs || []);
            dryRunBtn.style.display = 'none';
            runConsolidationBtn.style.display = 'none';
            discoveryResults.style.display = 'block';
            return;
        }

        discoverySummary.textContent = `Found ${total} open PR(s) in ${data.upstream_repo}.`;
        dryRunBtn.style.display = 'inline-block';
        runConsolidationBtn.style.display = 'inline-block';

        let html = '';
        for (const [ecosystem, prs] of Object.entries(data.groups)) {
            const icon = ECOSYSTEM_ICONS[ecosystem] || 'bi-box';
            html += renderPrGroupCard(`<i class="bi ${icon} me-2"></i>${capitalize(ecosystem)} (${prs.length})`, prs);
        }
        html += renderExistingConsolidationSection(data.existing_consolidation_prs || []);
        ecosystemGroups.innerHTML = html;
        discoveryResults.style.display = 'block';
    }

    function renderExistingConsolidationSection(prs) {
        if (prs.length === 0) return '';
        return `
            <hr>
            <h6 class="text-muted">
                <i class="bi bi-robot me-2"></i>Already handled by the dependency-bump workflow
            </h6>
            <p class="small text-muted mb-2">
                Open "chore(deps): consolidate" PR(s) from the automated workflow for this repo - no need to
                consolidate these again.
            </p>
            ${renderPrGroupCard(`<i class="bi bi-check2-square me-2"></i>Existing consolidation PRs (${prs.length})`, prs)}
        `;
    }

    function renderPrGroupCard(headingHtml, prs) {
        return `
            <div class="card border-0 bg-light mb-2">
                <div class="card-body">
                    <h6 class="card-title">${headingHtml}</h6>
                    <ul class="list-unstyled mb-0">
                        ${prs.map(pr => `
                            <li class="mb-1">
                                <a href="${pr.url}" target="_blank" rel="noopener noreferrer">#${pr.number}</a>
                                ${escapeHtml(pr.title)}
                            </li>
                        `).join('')}
                    </ul>
                </div>
            </div>
        `;
    }

    function capitalize(str) {
        return str.charAt(0).toUpperCase() + str.slice(1);
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    dryRunBtn.addEventListener('click', function() {
        runConsolidation(true);
    });

    runConsolidationBtn.addEventListener('click', function() {
        if (!confirm('This will create branches, commit changes, push, and open real pull requests. Continue?')) {
            return;
        }
        runConsolidation(false);
    });

    function runConsolidation(dryRun) {
        const payload = getFormPayload();
        payload.dry_run = dryRun;

        showModalState('loading');
        consolidationModal.show();

        fetch('/bot-dependency-consolidator/consolidate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showResultContent(data, dryRun);
                } else {
                    showModalError(data.error || 'Consolidation failed');
                }
            })
            .catch(error => {
                console.error('Error running consolidation:', error);
                showModalError('Failed to run consolidation. Please try again.');
            });
    }

    function showModalState(state) {
        const loadingDiv = document.getElementById('consolidationModalLoading');
        const contentDiv = document.getElementById('consolidationModalContent');
        const errorDiv = document.getElementById('consolidationModalError');

        loadingDiv.style.display = state === 'loading' ? 'block' : 'none';
        contentDiv.style.display = state === 'content' ? 'block' : 'none';
        errorDiv.style.display = state === 'error' ? 'block' : 'none';
    }

    function showModalError(message) {
        document.getElementById('consolidationErrorMessage').textContent = message;
        showModalState('error');
    }

    function showResultContent(data, dryRun) {
        const contentDiv = document.getElementById('consolidationModalContent');

        let prLinksHtml = '';
        if (data.pr_urls && data.pr_urls.length > 0) {
            prLinksHtml = `
                <div class="d-grid gap-2 mb-3">
                    ${data.pr_urls.map(url => `
                        <a href="${url}" target="_blank" rel="noopener noreferrer" class="btn btn-primary">
                            <i class="bi bi-box-arrow-up-right me-2"></i>${url}
                        </a>
                    `).join('')}
                </div>
            `;
        }

        const logHtml = (data.log || []).map(line => escapeHtml(line)).join('\n');

        contentDiv.innerHTML = `
            <div class="text-center py-2">
                <i class="bi bi-check-circle text-success" style="font-size: 2.5rem;"></i>
                <h5 class="mt-2 text-success">
                    ${dryRun ? 'Dry run complete' : `Consolidated ${data.consolidated_count} PR(s)`}
                </h5>
            </div>
            ${prLinksHtml}
            <details>
                <summary class="text-muted mb-2">Run log</summary>
                <pre class="small bg-light p-2 rounded" style="max-height: 300px; overflow-y: auto;">${logHtml}</pre>
            </details>
        `;
        showModalState('content');
    }

    function showFlashMessage(message, category) {
        const container = document.getElementById('flash-messages');
        if (!container) return;
        const alert = document.createElement('div');
        alert.className = `alert alert-${category} alert-dismissible fade show`;
        alert.role = 'alert';
        alert.innerHTML = `${escapeHtml(message)}<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>`;
        container.appendChild(alert);
    }

    // Fix modal backdrop issue - clean up when modal is hidden
    consolidationModalEl.addEventListener('hidden.bs.modal', function(event) {
        const backdrops = document.querySelectorAll('.modal-backdrop');
        backdrops.forEach(backdrop => backdrop.remove());
        document.body.classList.remove('modal-open');
        document.body.style.overflow = '';
        document.body.style.paddingRight = '';
        showModalState('loading');
    });
});
