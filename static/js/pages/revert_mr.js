/**
 * Revert MR Creation functionality
 * Mirrors deployment_mr.js but calls the revert endpoints.
 */

document.addEventListener('DOMContentLoaded', function() {
    const revertMrModalEl = document.getElementById('revertMrModal');
    if (!revertMrModalEl) return;

    let revertMrModal = null;
    let revertCreatedSuccessfully = false;
    let activeRevertBtn = null;
    let hadOriginalRevertCommit = false;

    function getOrCreateModal() {
        if (!revertMrModal) {
            revertMrModal = new bootstrap.Modal(revertMrModalEl);
        }
        return revertMrModal;
    }

    // Use event delegation so buttons inside hidden rows work too
    document.addEventListener('click', function(event) {
        const btn = event.target.closest('#revertMrBtn, .revert-mr-btn');
        if (btn) {
            handleRevertClick.call(btn);
        }
    });

    function handleRevertClick() {
        activeRevertBtn = this;
        const revertToCommit = this.getAttribute('data-revert-to-commit');
        hadOriginalRevertCommit = !!revertToCommit;

        getOrCreateModal().show();

        if (revertToCommit) {
            // Commit is known — go straight to preview
            showRevertModalState('loading');
            fetchRevertPreview();
        } else {
            // No target commit — ask the user to enter one
            showRevertModalState('commitInput');
        }
    }

    // Handle the "Continue" button and Enter key from the commit input form
    document.addEventListener('click', function(event) {
        if (event.target.closest('#revertCommitContinueBtn')) {
            submitRevertCommitInput();
        }
    });

    document.addEventListener('keydown', function(event) {
        if (event.key === 'Enter' && event.target.id === 'revertCommitSha') {
            event.preventDefault();
            submitRevertCommitInput();
        }
    });

    function submitRevertCommitInput() {
        const input = document.getElementById('revertCommitSha');
        if (!input) {
            console.error('Revert: commit input element not found');
            return;
        }
        const sha = input.value.trim();
        console.log('Revert: submitted commit SHA:', sha);
        if (sha.length < 7) {
            input.classList.add('is-invalid');
            return;
        }
        input.classList.remove('is-invalid');
        activeRevertBtn.dataset.revertToCommit = sha;
        showRevertModalState('loading');
        fetchRevertPreview();
    }

    function fetchRevertPreview() {
        const deploymentName = activeRevertBtn.dataset.deploymentName;
        const currentCommit = activeRevertBtn.dataset.currentCommit;
        const revertToCommit = activeRevertBtn.dataset.revertToCommit;

        console.log('Revert: fetching preview', { deploymentName, currentCommit, revertToCommit });
        const previewUrl = `/release_notes/${deploymentName}/preview_revert_mr?current_commit=${currentCommit}&revert_to_commit=${revertToCommit}`;

        fetch(previewUrl)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    restoreRevertModalContent();
                    populateRevertModalContent(data.data);
                    showRevertModalState('content');
                } else {
                    if (data.error_type === 'vpn_required') {
                        showRevertVpnError(data.error);
                    } else {
                        showRevertModalError(data.error);
                    }
                }
            })
            .catch(error => {
                console.error('Error fetching revert MR preview:', error);
                const errorStr = error.toString().toLowerCase();
                if (errorStr.includes('failed to fetch') || errorStr.includes('network error') ||
                    errorStr.includes('connection') || error.name === 'TypeError' || !navigator.onLine) {
                    showRevertVpnError('Network connection failed. Please ensure you are connected to the company VPN.');
                } else {
                    showRevertModalError('Failed to fetch deployment information. Please try again.');
                }
            });
    }

    function showRevertModalState(state) {
        const commitInputDiv = document.getElementById('revertCommitInput');
        const loadingDiv = document.getElementById('revertModalLoading');
        const contentDiv = document.getElementById('revertModalContent');
        const errorDiv = document.getElementById('revertModalError');
        const confirmBtn = document.getElementById('confirmRevertMr');

        commitInputDiv.style.display = 'none';
        loadingDiv.style.display = 'none';
        contentDiv.style.display = 'none';
        errorDiv.style.display = 'none';
        confirmBtn.style.display = 'none';

        if (state === 'commitInput') {
            commitInputDiv.style.display = 'block';
        } else if (state === 'loading') {
            loadingDiv.style.display = 'block';
        } else if (state === 'content') {
            contentDiv.style.display = 'block';
            confirmBtn.style.display = 'inline-block';
        } else if (state === 'error') {
            errorDiv.style.display = 'block';
        }
    }

    function populateRevertModalContent(data) {
        document.getElementById('revertBranchName').textContent = data.branch_name;
        document.getElementById('revertMrTitle').textContent = data.mr_title;
        const fileName = data.deploy_file_path.split('/').pop();
        document.getElementById('revertDeployFileName').textContent = fileName;

        const deployFileUrl = `https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/${data.deploy_file_path}`;
        document.getElementById('revertDeployFileLink').href = deployFileUrl;
        document.getElementById('revertCurrentCommit').textContent = data.current_commit;
        document.getElementById('revertToCommit').textContent = data.new_commit;

        const validationStatus = document.getElementById('revertValidationStatus');

        if (data.validation_success) {
            validationStatus.className = 'alert alert-success';
            validationStatus.style.setProperty('border', 'none', 'important');
            validationStatus.innerHTML = '<i class="bi bi-check-circle me-2"></i><strong>Validation Status:</strong> <span id="revertValidationMessage"></span>';
        } else {
            validationStatus.className = 'alert alert-warning';
            validationStatus.style.setProperty('border', 'none', 'important');
            validationStatus.innerHTML = '<i class="bi bi-exclamation-triangle me-2"></i><strong>Validation Status:</strong> <span id="revertValidationMessage"></span>';
        }

        document.getElementById('revertValidationMessage').textContent = data.validation_message;

        updateRevertGitlabStatus(data);
        updateRevertButtonState(data);
    }

    function updateRevertGitlabStatus(data) {
        const gitlabStatus = document.getElementById('revertGitlabStatus');
        const gitlabError = document.getElementById('revertGitlabError');

        if (data.gitlab_connected) {
            gitlabStatus.innerHTML = `
                <i class="bi bi-check-circle text-success me-2"></i>
                <span class="text-success">VPN connected, GitLab API ready</span>
            `;
            gitlabError.style.display = 'none';
        } else {
            gitlabStatus.innerHTML = `
                <i class="bi bi-exclamation-triangle text-warning me-2"></i>
                <span class="text-warning">GitLab connection issue</span>
            `;
            gitlabError.style.display = 'block';
            gitlabError.innerHTML = '<small class="text-danger">' + data.gitlab_error + '</small>';
        }
    }

    function updateRevertButtonState(data) {
        const confirmBtn = document.getElementById('confirmRevertMr');

        if (data.validation_success && data.can_create_mr) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i class="bi bi-arrow-counterclockwise me-2"></i>Create Revert MR';
            confirmBtn.className = 'btn btn-warning';
        } else {
            confirmBtn.disabled = true;
            if (!data.gitlab_connected) {
                confirmBtn.innerHTML = '<i class="bi bi-exclamation-triangle me-2"></i>VPN Required';
                confirmBtn.className = 'btn btn-secondary';
            } else {
                confirmBtn.innerHTML = '<i class="bi bi-exclamation-triangle me-2"></i>Issues Found';
                confirmBtn.className = 'btn btn-secondary';
            }
        }
    }

    function showRevertModalError(errorMessage) {
        document.getElementById('revertErrorMessage').textContent = errorMessage;
        showRevertModalState('error');
    }

    function showRevertVpnError(errorMessage) {
        const contentDiv = document.getElementById('revertModalContent');
        contentDiv.innerHTML = `
            <div class="text-center py-4">
                <i class="bi bi-wifi-off text-warning" style="font-size: 3rem;"></i>
                <h4 class="mt-3 text-warning">VPN Connection Required</h4>
                <p class="text-muted mb-4">You need to be connected to the company VPN to access GitLab and create revert MRs.</p>
                <div class="card border-0 bg-light mb-3">
                    <div class="card-body">
                        <h6 class="card-title text-dark">
                            <i class="bi bi-info-circle me-2"></i>Next steps:
                        </h6>
                        <ol class="mb-0" style="list-style-position: inside; padding-left: 0; margin-left: 0;">
                            <li style="padding-left: 0; margin-bottom: 0.25rem;">Connect to the company VPN</li>
                            <li style="padding-left: 0; margin-bottom: 0;">Click "Retry" below to try again</li>
                        </ol>
                    </div>
                </div>
                <button type="button" class="btn btn-primary" id="retryRevertConnectionBtn">
                    <i class="bi bi-arrow-clockwise me-2"></i>Retry Connection
                </button>
            </div>
        `;
        showRevertModalState('content');

        const confirmBtn = document.getElementById('confirmRevertMr');
        confirmBtn.style.display = 'none';

        document.getElementById('retryRevertConnectionBtn').addEventListener('click', function() {
            retryRevertPreview();
        });
    }

    function restoreRevertModalContent() {
        const contentDiv = document.getElementById('revertModalContent');
        contentDiv.innerHTML = `
            <div class="alert alert-warning mb-3">
                <i class="bi bi-exclamation-triangle me-2"></i>
                <strong>Warning:</strong> This will create an MR to revert the production deployment to a previous commit.
            </div>

            <h6 class="mb-3">
                <i class="bi bi-info-circle me-2"></i>
                Revert MR Preview
            </h6>

            <div class="card border-0 bg-light mb-3">
                <div class="card-body">
                    <div class="row">
                        <div class="col-md-6">
                            <p class="mb-1"><strong>Branch name:</strong></p>
                            <code id="revertBranchName" class="small"></code>
                            <p class="mb-1 mt-2"><strong>File:</strong></p>
                            <a id="revertDeployFileLink" href="#" target="_blank" rel="noopener noreferrer" style="text-decoration: underline;">
                                <code id="revertDeployFileName" class="small"></code>
                            </a>
                        </div>
                        <div class="col-md-6">
                            <p class="mb-1"><strong>Current PROD commit:</strong></p>
                            <code id="revertCurrentCommit" class="small"></code>
                            <p class="mb-1 mt-2"><strong>Revert to commit:</strong></p>
                            <code id="revertToCommit" class="small text-warning"></code>
                        </div>
                    </div>
                    <div class="row mt-2">
                        <div class="col-12">
                            <p class="mb-1"><strong>MR title:</strong></p>
                            <code id="revertMrTitle" class="small"></code>
                        </div>
                    </div>
                </div>
            </div>

            <div id="revertValidationStatus" class="alert alert-success" role="alert" style="border: none !important;">
                <i class="bi bi-check-circle me-2"></i>
                <strong>Validation Status:</strong> <span id="revertValidationMessage">All checks passed!</span>
            </div>

            <div class="card border-0 bg-light mb-3">
                <div class="card-body">
                    <h6 class="card-title">GitLab Connectivity</h6>
                    <div id="revertGitlabStatus" class="d-flex align-items-center">
                        <div class="spinner-border spinner-border-sm text-muted me-2" role="status">
                            <span class="visually-hidden">Checking...</span>
                        </div>
                        <span class="text-muted">Checking VPN connection...</span>
                    </div>
                    <div id="revertGitlabError" style="display: none;" class="mt-2">
                        <small class="text-danger"></small>
                    </div>
                </div>
            </div>
        `;
    }

    function retryRevertPreview() {
        showRevertModalState('loading');

        const deploymentName = activeRevertBtn.dataset.deploymentName;
        const currentCommit = activeRevertBtn.dataset.currentCommit;
        const revertToCommit = activeRevertBtn.dataset.revertToCommit;

        const previewUrl = `/release_notes/${deploymentName}/preview_revert_mr?current_commit=${currentCommit}&revert_to_commit=${revertToCommit}`;

        fetch(previewUrl)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    restoreRevertModalContent();
                    populateRevertModalContent(data.data);
                    showRevertModalState('content');
                } else {
                    if (data.error_type === 'vpn_required') {
                        showRevertVpnError(data.error);
                    } else {
                        showRevertModalError(data.error);
                    }
                }
            })
            .catch(error => {
                console.error('Error retrying revert MR preview:', error);
                const errorStr = error.toString().toLowerCase();
                if (errorStr.includes('failed to fetch') || errorStr.includes('network error') ||
                    errorStr.includes('connection') || error.name === 'TypeError' || !navigator.onLine) {
                    showRevertVpnError('Network connection failed. Please ensure you are connected to the company VPN.');
                } else {
                    showRevertModalError('Failed to fetch deployment information. Please try again.');
                }
            });
    }

    // Confirm revert MR creation
    document.addEventListener('click', function(event) {
        if (!event.target.closest('#confirmRevertMr')) return;
        const btn = document.getElementById('confirmRevertMr');
        if (!btn || btn.disabled) return;

        const deploymentName = activeRevertBtn.dataset.deploymentName;
        const currentCommit = activeRevertBtn.dataset.currentCommit;
        const revertToCommit = activeRevertBtn.dataset.revertToCommit;

        btn.disabled = true;
        btn.innerHTML = '<div class="spinner-border spinner-border-sm me-2" role="status"><span class="visually-hidden">Creating...</span></div>Creating Revert MR...';

        fetch(`/release_notes/${deploymentName}/create_revert_mr`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                current_commit: currentCommit,
                revert_to_commit: revertToCommit
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showRevertCreatedSuccess(data.data);
            } else {
                showRevertCreationError(data.error);
            }
        })
        .catch(error => {
            console.error('Error creating revert MR:', error);
            showRevertCreationError('Failed to create revert MR. Please try again.');
        });
    });

    function showRevertCreatedSuccess(data) {
        const contentDiv = document.getElementById('revertModalContent');
        revertCreatedSuccessfully = true;

        const isCreationUrl = data.mr_url && data.mr_url.includes('/merge_requests/new');

        if (isCreationUrl) {
            contentDiv.innerHTML = `
                <div class="text-center py-4">
                    <i class="bi bi-check-circle text-success" style="font-size: 3rem;"></i>
                    <h4 class="mt-3 text-success">Revert merge request data prepared!</h4>
                    <p class="text-muted mb-4">File updated with previous commit reference.</p>
                    <div class="d-grid gap-2">
                        <a href="${data.mr_url}" target="_blank" class="btn btn-warning">
                            <i class="bi bi-arrow-counterclockwise me-2"></i>
                            Click here to create the Revert MR
                        </a>
                    </div>
                    <small class="text-muted mt-3 d-block">
                        GitLab will open with all fields pre-filled. Just review and create!
                    </small>
                    <p class="text-info mt-3 mb-0">
                        <i class="bi bi-info-circle me-1"></i>
                        <small>This page will reload when you close this modal.</small>
                    </p>
                </div>
            `;
        } else {
            contentDiv.innerHTML = `
                <div class="text-center py-4">
                    <i class="bi bi-check-circle text-success" style="font-size: 3rem;"></i>
                    <h4 class="mt-3 text-success">Revert MR Created Successfully!</h4>
                    <p class="text-muted mb-4">${data.message}</p>
                    <div class="d-grid gap-2">
                        <a href="${data.mr_url}" target="_blank" class="btn btn-primary">
                            <i class="bi bi-box-arrow-up-right me-2"></i>
                            View MR in GitLab
                        </a>
                    </div>
                    <p class="text-info mt-3 mb-0">
                        <i class="bi bi-info-circle me-1"></i>
                        <small>This page will reload when you close this modal.</small>
                    </p>
                </div>
            `;
        }

        const confirmBtn = document.getElementById('confirmRevertMr');
        confirmBtn.style.display = 'none';
    }

    function showRevertCreationError(errorMessage) {
        const validationStatus = document.getElementById('revertValidationStatus');
        validationStatus.className = 'alert alert-danger';
        validationStatus.style.setProperty('border', 'none', 'important');
        validationStatus.innerHTML = `
            <i class="bi bi-exclamation-triangle me-2"></i>
            <strong>Revert MR Creation Failed:</strong> ${errorMessage}
        `;

        const confirmBtn = document.getElementById('confirmRevertMr');
        confirmBtn.disabled = false;
        confirmBtn.innerHTML = '<i class="bi bi-arrow-counterclockwise me-2"></i>Retry Create Revert MR';
        confirmBtn.className = 'btn btn-danger';
    }

    // Clean up modal on close
    revertMrModalEl.addEventListener('hidden.bs.modal', function(event) {
        const backdrops = document.querySelectorAll('.modal-backdrop');
        backdrops.forEach(backdrop => backdrop.remove());

        document.body.classList.remove('modal-open');
        document.body.style.overflow = '';
        document.body.style.paddingRight = '';

        const modal = event.target;
        modal.removeAttribute('aria-hidden');
        modal.style.display = 'none';

        showRevertModalState('loading');

        // Clear dynamically-set revert commit for buttons that didn't have one originally
        if (activeRevertBtn && !hadOriginalRevertCommit) {
            activeRevertBtn.removeAttribute('data-revert-to-commit');
        }
        const commitInput = document.getElementById('revertCommitSha');
        if (commitInput) {
            commitInput.value = '';
            commitInput.classList.remove('is-invalid');
        }

        if (revertCreatedSuccessfully) {
            revertCreatedSuccessfully = false;
            window.location.reload();
        }
    });

    revertMrModalEl.addEventListener('show.bs.modal', function() {
        revertCreatedSuccessfully = false;
    });
});
