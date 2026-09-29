"""
Blueprint for consolidating bot dependency update PRs (e.g. red-hat-konflux[bot])
into per-ecosystem PRs for easier review and merging.
"""

import logging

from flask import Blueprint, jsonify, render_template, request

from blueprints.deployments import get_all_deployments
from services.dependency_consolidator_service import DependencyConsolidator

logger = logging.getLogger(__name__)

bot_dependency_consolidator_bp = Blueprint(
    "bot_dependency_consolidator", __name__, url_prefix="/bot-dependency-consolidator"
)


def get_deployment_repo_names() -> list[str]:
    """Deduplicated, sorted 'owner/repo' list from the same data the Deployments page uses."""
    deployments = get_all_deployments()
    return sorted({d["repo_name"] for d in deployments.values() if d.get("repo_name")})


@bot_dependency_consolidator_bp.route("/")
def index():
    """Bot dependency consolidation page."""
    return render_template(
        "bot_dependency_consolidator.html",
        default_bot_author=DependencyConsolidator.BOT_AUTHOR,
        known_repos=get_deployment_repo_names(),
    )


@bot_dependency_consolidator_bp.route("/discover", methods=["POST"])
def discover():
    """Find open bot PRs for the given repo and group them by ecosystem (read-only)."""
    data = request.get_json() or {}

    upstream_repo = (data.get("upstream_repo") or "").strip()
    if not upstream_repo:
        return jsonify({"success": False, "error": "Repository is required"}), 400

    bot_author = (data.get("bot_author") or "").strip()

    try:
        consolidator = DependencyConsolidator(
            upstream_repo=upstream_repo,
            bot_author=bot_author,
        )
        result = consolidator.discover()
        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code
    except Exception:
        logger.exception("Error discovering bot dependency PRs")
        return jsonify(
            {"success": False, "error": "An internal error has occurred."}
        ), 500


@bot_dependency_consolidator_bp.route("/consolidate", methods=["POST"])
def consolidate():
    """Run the consolidation - clones the repo, creates branches, commits, and
    (unless dry-run) pushes and opens PRs."""
    data = request.get_json() or {}

    upstream_repo = (data.get("upstream_repo") or "").strip()
    if not upstream_repo:
        return jsonify({"success": False, "error": "Repository is required"}), 400

    bot_author = (data.get("bot_author") or "").strip()
    dry_run = bool(data.get("dry_run", False))
    close_originals = bool(data.get("close_originals", False))
    regenerate_locks = bool(data.get("regenerate_locks", True))

    try:
        consolidator = DependencyConsolidator(
            upstream_repo=upstream_repo,
            bot_author=bot_author,
            dry_run=dry_run,
            close_originals=close_originals,
            regenerate_locks=regenerate_locks,
        )
        result = consolidator.run()

        return jsonify(
            {
                "success": result.success,
                "error": result.error,
                "pr_url": result.pr_url,
                "pr_urls": result.pr_urls,
                "consolidated_count": result.consolidated_count,
                "log": result.log,
            }
        ), (200 if result.success else 400)
    except Exception:
        logger.exception("Error consolidating bot dependency PRs")
        return jsonify(
            {"success": False, "error": "An internal error has occurred."}
        ), 500
