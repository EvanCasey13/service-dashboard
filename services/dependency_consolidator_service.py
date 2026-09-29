"""
Bot Dependency Consolidation service.

Consolidates multiple dependency update PRs from a bot (default:
red-hat-konflux[bot]) into per-ecosystem PRs for easier review and merging.

Adapted from a standalone CLI tool - there is no persisted local-clone
registry, so `run()` clones the repo into a throwaway temp directory for
the duration of the operation and cleans it up afterward.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BotPR:
    """Information about a bot-created PR."""
    number: int
    title: str
    branch: str
    url: str
    files: list = field(default_factory=list)


@dataclass
class ConsolidationResult:
    """Result of the consolidation operation."""
    success: bool
    pr_url: str = ""
    pr_urls: list = field(default_factory=list)  # Multiple PRs when split by ecosystem
    consolidated_count: int = 0
    error: str = ""
    log: list = field(default_factory=list)


class DependencyConsolidator:
    """Consolidates bot dependency PRs into per-ecosystem PRs."""

    BOT_AUTHOR = "red-hat-konflux[bot]"
    CONSOLIDATION_WORKFLOW_AUTHOR = "platex-rehor-bot"

    def __init__(
        self,
        upstream_repo: str,
        dry_run: bool = False,
        bot_author: str = "",
        close_originals: bool = False,
        regenerate_locks: bool = True,
    ):
        self.upstream_repo = upstream_repo
        self.repo_path = ""
        self._owns_repo_path = False
        self.dry_run = dry_run
        self.bot_author = bot_author or self.BOT_AUTHOR
        self.close_originals = close_originals
        self.regenerate_locks = regenerate_locks
        self.bot_prs: list[BotPR] = []
        self.log: list[str] = []

    def _emit(self, message: str):
        logger.info(message)
        self.log.append(message)

    # ------------------------------------------------------------------
    # Discovery (read-only, safe to call for a preview - no local clone needed)
    # ------------------------------------------------------------------

    def discover(self) -> dict:
        """Find open bot PRs and group them by ecosystem, without touching git."""
        self._emit(f"Finding {self.bot_author} PRs in {self.upstream_repo}...")
        self.bot_prs = self._find_bot_prs()

        existing_consolidation_prs = self._pr_list_json(
            self._find_existing_consolidation_prs()
        )

        if not self.bot_prs:
            self._emit(f"No open PRs found from {self.bot_author}")
            return {
                "success": True,
                "upstream_repo": self.upstream_repo,
                "total": 0,
                "groups": {},
                "existing_consolidation_prs": existing_consolidation_prs,
                "log": self.log,
            }

        grouped = self._group_prs_by_ecosystem()
        groups_json = {
            ecosystem: self._pr_list_json(prs) for ecosystem, prs in grouped.items()
        }
        return {
            "success": True,
            "upstream_repo": self.upstream_repo,
            "total": len(self.bot_prs),
            "groups": groups_json,
            "existing_consolidation_prs": existing_consolidation_prs,
            "log": self.log,
        }

    @staticmethod
    def _pr_list_json(prs: list["BotPR"]) -> list[dict]:
        return [
            {"number": pr.number, "title": pr.title, "branch": pr.branch, "url": pr.url}
            for pr in prs
        ]

    def _find_existing_consolidation_prs(self) -> list["BotPR"]:
        """Find open 'chore(deps): consolidate' PRs already created by the
        automated dependency-bump workflow (a separate bot from `bot_author`),
        so the UI can show what's already been handled without duplicating it."""
        try:
            result = self._run_gh(
                "pr", "list",
                "--repo", self.upstream_repo,
                "--author", self.CONSOLIDATION_WORKFLOW_AUTHOR,
                "--state", "open",
                "--json", "number,title,headRefName,url",
            )
        except subprocess.TimeoutExpired:
            self._emit("Timeout listing existing consolidation PRs")
            return []

        if result.returncode != 0:
            self._emit(f"Error listing existing consolidation PRs: {result.stderr}")
            return []

        try:
            prs_data = json.loads(result.stdout)
        except (json.JSONDecodeError, KeyError) as e:
            self._emit(f"Error parsing existing consolidation PR data: {e}")
            return []

        matched = [
            BotPR(
                number=pr["number"],
                title=pr["title"],
                branch=pr["headRefName"],
                url=pr["url"],
            )
            for pr in prs_data
            if pr.get("title", "").lower().startswith("chore(deps): consolidate")
        ]
        self._emit(
            f"Found {len(prs_data)} open PR(s) from {self.CONSOLIDATION_WORKFLOW_AUTHOR}, "
            f"{len(matched)} matching 'chore(deps): consolidate' title"
        )
        if prs_data and not matched:
            sample_titles = ", ".join(f'"{pr.get("title", "")}"' for pr in prs_data[:5])
            self._emit(f"Titles seen: {sample_titles}")
        return matched

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> ConsolidationResult:
        """Consolidate bot dependency PRs, grouped by ecosystem.

        Clones the repo into a throwaway temp directory - there is no
        persisted local-clone registry, so every run starts from a fresh
        checkout and cleans it up when done.
        """
        try:
            if not self._clone_to_temp_dir():
                return ConsolidationResult(
                    success=False,
                    error=f"Failed to clone {self.upstream_repo}",
                    log=self.log,
                )

            self._emit(f"Finding {self.bot_author} PRs in {self.upstream_repo}...")
            self.bot_prs = self._find_bot_prs()

            if not self.bot_prs:
                self._emit(f"No open PRs found from {self.bot_author}")
                return ConsolidationResult(success=True, consolidated_count=0, log=self.log)

            if len(self.bot_prs) < 2:
                self._emit("Only 1 PR found - nothing to consolidate")
                return ConsolidationResult(success=True, consolidated_count=1, log=self.log)

            grouped_prs = self._group_prs_by_ecosystem()
            for ecosystem, prs in grouped_prs.items():
                self._emit(f"{ecosystem}: {len(prs)} PR(s)")

            all_pr_urls = []
            total_consolidated = 0
            all_merged_prs = []

            for ecosystem, prs in grouped_prs.items():
                if not prs:
                    continue

                self._emit(f"Processing {ecosystem} dependencies ({len(prs)} PRs)")

                original_bot_prs = self.bot_prs
                self.bot_prs = prs
                result = self._process_ecosystem(ecosystem)
                self.bot_prs = original_bot_prs

                if result.success and result.pr_url:
                    all_pr_urls.append(result.pr_url)
                    total_consolidated += result.consolidated_count
                    all_merged_prs.extend(prs[: result.consolidated_count])

            if not all_pr_urls:
                return ConsolidationResult(
                    success=False, error="Failed to create any PRs", log=self.log
                )

            if self.close_originals and all_merged_prs:
                self._emit("Closing original bot PRs...")
                self._close_original_prs(all_merged_prs, ", ".join(all_pr_urls))

            return ConsolidationResult(
                success=True,
                pr_url=all_pr_urls[0] if len(all_pr_urls) == 1 else "",
                pr_urls=all_pr_urls,
                consolidated_count=total_consolidated,
                log=self.log,
            )
        finally:
            self._cleanup_temp_dir()

    def _clone_to_temp_dir(self) -> bool:
        """Clone the upstream repo into a fresh temp directory via the gh CLI."""
        temp_dir = tempfile.mkdtemp(prefix="bot-dep-consolidator-")
        self._emit(f"Cloning {self.upstream_repo} into {temp_dir}...")

        try:
            result = subprocess.run(
                ["gh", "repo", "clone", self.upstream_repo, temp_dir],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            self._emit("Timeout cloning repository (300s)")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return False

        if result.returncode != 0:
            self._emit(f"Clone failed: {result.stderr.strip()}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return False

        self.repo_path = temp_dir
        self._owns_repo_path = True
        return True

    def _cleanup_temp_dir(self):
        if self._owns_repo_path and self.repo_path and os.path.isdir(self.repo_path):
            shutil.rmtree(self.repo_path, ignore_errors=True)
            self._emit(f"Cleaned up temp clone {self.repo_path}")
            self.repo_path = ""
            self._owns_repo_path = False

    def _group_prs_by_ecosystem(self) -> dict[str, list[BotPR]]:
        """Group PRs by their dependency ecosystem."""
        groups: dict[str, list[BotPR]] = {
            "python": [],
            "npm": [],
            "go": [],
        }

        for pr in self.bot_prs:
            dep_type, _ = self._detect_dep_type(pr)
            if dep_type in groups:
                groups[dep_type].append(pr)
            else:
                groups["python"].append(pr)

        return {k: v for k, v in groups.items() if v}

    def _process_ecosystem(self, ecosystem: str) -> ConsolidationResult:
        """Process a single ecosystem's PRs."""
        if len(self.bot_prs) < 1:
            return ConsolidationResult(success=True, consolidated_count=0)

        self._emit(f"Creating {ecosystem} consolidation branch...")
        branch_name = self._create_consolidation_branch(ecosystem)
        if not branch_name:
            return ConsolidationResult(
                success=False, error=f"Failed to create {ecosystem} consolidation branch"
            )

        self._emit(f"Applying changes from {len(self.bot_prs)} PRs...")
        merged_prs = self._merge_bot_pr_changes(branch_name)

        if not merged_prs:
            self._cleanup_branch(branch_name)
            return ConsolidationResult(
                success=False, error=f"Failed to merge any {ecosystem} PR changes"
            )

        self._emit(f"Consolidated {len(merged_prs)} {ecosystem} PRs")

        if self.dry_run:
            self._emit(f"[Dry Run] Skipping push and PR creation for {ecosystem}")
            self._cleanup_branch(branch_name)
            return ConsolidationResult(success=True, consolidated_count=len(merged_prs))

        self._emit(f"Pushing {ecosystem} consolidation branch...")
        if not self._push_branch(branch_name):
            return ConsolidationResult(success=False, error="Failed to push branch")

        self._emit(f"Creating {ecosystem} consolidated PR...")
        return self._create_consolidated_pr(branch_name, merged_prs, ecosystem)

    # ------------------------------------------------------------------
    # Git / process helpers - all scoped to self.repo_path
    # ------------------------------------------------------------------

    def _run_git(self, *args, check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + list(args),
            capture_output=True,
            text=True,
            cwd=self.repo_path,
            check=check,
        )

    def _run_gh(self, *args, timeout: int = 60) -> subprocess.CompletedProcess:
        """Run gh CLI command with timeout to prevent hanging on network issues.

        Commands like `pr create` infer the head branch from the git repo in
        the current working directory, not from --repo, so this must run
        inside the cloned repo (once one exists) rather than the server's own cwd.
        """
        return subprocess.run(
            ["gh"] + list(args),
            capture_output=True,
            text=True,
            cwd=self.repo_path or None,
            timeout=timeout,
        )

    def _abs(self, *parts: str) -> str:
        return os.path.join(self.repo_path, *parts)

    def _run_pipenv_lock(self, rel_dir: str = ".") -> subprocess.CompletedProcess:
        """Run pipenv lock, preferring WSL if available for consistent resolution."""
        abs_cwd = self._abs(rel_dir)

        wsl_exe = shutil.which("wsl") or shutil.which("wsl.exe")
        if not wsl_exe:
            for candidate in [
                r"C:\Windows\System32\wsl.exe",
                r"C:\WINDOWS\system32\wsl.exe",
                os.path.expandvars(r"%SYSTEMROOT%\System32\wsl.exe"),
            ]:
                if os.path.exists(candidate):
                    wsl_exe = candidate
                    break

        if wsl_exe:
            try:
                wsl_check = subprocess.run(
                    [wsl_exe, "--status"], capture_output=True, timeout=5
                )
                if wsl_check.returncode == 0:
                    wsl_cwd = abs_cwd
                    if len(abs_cwd) >= 2 and abs_cwd[1] == ":":
                        drive = abs_cwd[0].lower()
                        wsl_cwd = f"/mnt/{drive}" + abs_cwd[2:].replace("\\", "/")

                    cmd = [wsl_exe, "bash", "-lc", f"cd '{wsl_cwd}' && pipenv lock"]
                    self._emit(f"Using WSL pipenv in {wsl_cwd}...")
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if result.returncode == 0:
                        return result
                    self._emit(f"WSL pipenv failed: {result.stderr[:100]}")
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                self._emit(f"WSL error: {e}")

        self._emit("Using native pipenv...")
        return subprocess.run(
            ["pipenv", "lock"],
            capture_output=True,
            text=True,
            cwd=abs_cwd,
            timeout=300,
        )

    def _run_npm_install(self, rel_dir: str = ".") -> subprocess.CompletedProcess:
        """Run npm install, preferring WSL if available."""
        abs_cwd = self._abs(rel_dir)

        node_modules = os.path.join(abs_cwd, "node_modules")
        if os.path.exists(node_modules):
            self._emit("Removing node_modules to avoid conflicts...")
            shutil.rmtree(node_modules, ignore_errors=True)

        wsl_exe = shutil.which("wsl") or shutil.which("wsl.exe")
        if not wsl_exe:
            for candidate in [
                r"C:\Windows\System32\wsl.exe",
                r"C:\WINDOWS\system32\wsl.exe",
                os.path.expandvars(r"%SYSTEMROOT%\System32\wsl.exe"),
            ]:
                if os.path.exists(candidate):
                    wsl_exe = candidate
                    break

        if wsl_exe:
            try:
                wsl_check = subprocess.run(
                    [wsl_exe, "--status"], capture_output=True, timeout=5
                )
                if wsl_check.returncode == 0:
                    wsl_cwd = abs_cwd
                    if len(abs_cwd) >= 2 and abs_cwd[1] == ":":
                        drive = abs_cwd[0].lower()
                        wsl_cwd = f"/mnt/{drive}" + abs_cwd[2:].replace("\\", "/")

                    cmd = [wsl_exe, "bash", "-lc", f"cd '{wsl_cwd}' && npm install"]
                    self._emit(f"Using WSL npm in {wsl_cwd}...")
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if result.returncode == 0:
                        return result
                    cmd = [wsl_exe, "bash", "-lc", f"cd '{wsl_cwd}' && npm install --legacy-peer-deps"]
                    self._emit("Retrying with --legacy-peer-deps...")
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if result.returncode == 0:
                        return result
                    self._emit(f"WSL npm failed: {result.stderr[:100]}")
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                self._emit(f"WSL error: {e}")

        self._emit("Using native npm...")
        result = subprocess.run(
            ["npm", "install"], capture_output=True, text=True, cwd=abs_cwd, timeout=300
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["npm", "install", "--legacy-peer-deps"],
                capture_output=True,
                text=True,
                cwd=abs_cwd,
                timeout=300,
            )
        return result

    def _find_bot_prs(self) -> list[BotPR]:
        try:
            result = self._run_gh(
                "pr", "list",
                "--repo", self.upstream_repo,
                "--author", self.bot_author,
                "--state", "open",
                "--json", "number,title,headRefName,url,labels",
            )
        except subprocess.TimeoutExpired:
            self._emit("Timeout listing PRs from GitHub (60s)")
            return []

        if result.returncode != 0:
            self._emit(f"Error listing PRs: {result.stderr}")
            return []

        try:
            prs_data = json.loads(result.stdout)
            bot_prs = []
            for pr in prs_data:
                labels = [lbl.get("name", "").lower() for lbl in pr.get("labels", [])]
                if any("do not merge" in lbl or "do-not-merge" in lbl for lbl in labels):
                    self._emit(f"Skipping #{pr['number']}: has DO NOT MERGE label")
                    continue
                bot_prs.append(BotPR(
                    number=pr["number"],
                    title=pr["title"],
                    branch=pr["headRefName"],
                    url=pr["url"],
                ))
            return bot_prs
        except (json.JSONDecodeError, KeyError) as e:
            self._emit(f"Error parsing PR data: {e}")
            return []

    def _create_consolidation_branch(self, ecosystem: str = "") -> Optional[str]:
        remote = "origin"
        remote_check = self._run_git("remote", "get-url", "upstream")
        if remote_check.returncode == 0:
            remote = "upstream"

        self._emit(f"Fetching latest from {remote}...")
        fetch_result = self._run_git("fetch", remote)
        if fetch_result.returncode != 0:
            self._emit(f"Warning: git fetch failed: {fetch_result.stderr.strip()}")

        base_ref = None
        for branch in ["main", "master"]:
            result = self._run_git("rev-parse", f"{remote}/{branch}")
            if result.returncode == 0:
                base_ref = result.stdout.strip()
                self._emit(f"Using {remote}/{branch} as base")
                break

        if not base_ref:
            self._emit(f"Error: Could not find {remote}/main or {remote}/master")
            return None

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        branch_name = (
            f"chore/consolidate-{ecosystem}-deps-{timestamp}"
            if ecosystem
            else f"chore/consolidate-deps-{timestamp}"
        )

        result = self._run_git("checkout", "-b", branch_name, base_ref)
        if result.returncode != 0:
            self._emit(f"Error creating branch: {result.stderr}")
            return None

        self._emit(f"Created branch: {branch_name}")
        return branch_name

    def _detect_dep_type_from_diff(self, pr_number: int) -> tuple[str, str]:
        """Detect dependency type/directory from which files the PR modifies."""
        diff_result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--repo", self.upstream_repo, "--name-only"],
            capture_output=True,
            text=True,
        )
        if diff_result.returncode != 0:
            return ("unknown", ".")

        files = diff_result.stdout.strip().split("\n")
        for f in files:
            basename = os.path.basename(f)
            dirname = os.path.dirname(f) or "."

            if basename in ("go.mod", "go.sum"):
                return ("go", dirname)
            if basename in ("Pipfile", "Pipfile.lock"):
                return ("python", dirname)
            if basename in ("package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"):
                return ("npm", dirname)
            if basename in ("pyproject.toml", "poetry.lock"):
                return ("python", dirname)
            if basename == "requirements.txt":
                return ("python", dirname)
        return ("unknown", ".")

    def _detect_dep_type(self, pr: BotPR) -> tuple[str, str]:
        """Detect which dependency system a PR targets (diff first, title as fallback)."""
        dep_type, directory = self._detect_dep_type_from_diff(pr.number)
        if dep_type != "unknown":
            return (dep_type, directory)

        title = pr.title.lower()

        if "@" in title and ("typespec" in title or "types/" in title):
            return ("npm", ".")
        if "module " in title and ("github.com/" in title or "golang.org/" in title):
            return ("go", ".")
        if "grpcio" in title or "grpcio-status" in title:
            return ("python", ".")
        if "update dependency" in title or "fix(deps)" in title:
            return ("python", ".")

        return ("unknown", ".")

    def _merge_bot_pr_changes(self, branch_name: str) -> list[BotPR]:
        """Apply semantic dependency updates from each PR onto the branch."""
        merged = []
        original_go_version = self._get_go_version() if self._has_go_mod() else None

        go_dirs: set[str] = set()
        python_dirs: set[str] = set()
        npm_dirs: set[str] = set()

        for pr in self.bot_prs:
            self._emit(f"Processing PR #{pr.number}: {pr.title[:50]}...")

            dep_type, dep_dir = self._detect_dep_type(pr)
            applied = False

            pipfile_path = self._abs(dep_dir, "Pipfile")
            package_json_path = self._abs(dep_dir, "package.json")
            go_mod_path = self._abs(dep_dir, "go.mod")

            if dep_type == "go" and os.path.exists(go_mod_path):
                dep_spec = self._extract_go_dep_from_title(pr.title)
                if not dep_spec:
                    self._emit("Title parsing failed, checking PR content...")
                    dep_spec = self._extract_go_dep_from_pr_content(pr.number)
                    if not dep_spec:
                        self._emit("Warning: Could not extract dep from title or PR content, skipping")
                        continue

                self._emit(f"Running go get {dep_spec} in {dep_dir}...")
                get_result = subprocess.run(
                    ["go", "get", dep_spec],
                    capture_output=True,
                    text=True,
                    cwd=self._abs(dep_dir),
                )
                if get_result.returncode != 0:
                    self._emit(f"Warning: go get failed: {get_result.stderr.strip()}")
                    continue
                if original_go_version and dep_dir == ".":
                    self._restore_go_version(original_go_version)
                applied = True
                go_dirs.add(dep_dir)

            elif dep_type == "python" and os.path.exists(pipfile_path):
                package, version = self._extract_python_dep_from_title(pr.title)
                if not package:
                    self._emit("Title parsing failed, checking PR content...")
                    package, version = self._extract_python_dep_from_pr_content(pr.number)
                    if not package:
                        self._emit("Warning: Could not extract dep from title or PR content, skipping")
                        continue

                self._emit(f"Updating {package} to {version} in {dep_dir}...")
                if not self._update_pipfile_dep(package, version, pipfile_path):
                    self._emit(f"Warning: Could not find {package} in {pipfile_path}, skipping")
                    continue
                applied = True
                python_dirs.add(dep_dir)

            elif dep_type == "npm" and os.path.exists(package_json_path):
                package, version = self._extract_npm_dep_from_title(pr.title)
                if not package:
                    self._emit("Title parsing failed, checking PR content...")
                    package, version = self._extract_npm_dep_from_pr_content(pr.number)
                    if not package:
                        self._emit("Warning: Could not extract npm dep from title or PR content, skipping")
                        continue

                self._emit(f"Updating {package} to {version} in {dep_dir}...")
                if not self._update_package_json_dep(package, version, package_json_path):
                    self._emit(f"Warning: Could not find {package} in {package_json_path}, skipping")
                    continue
                applied = True
                npm_dirs.add(dep_dir)

            else:
                try:
                    diff_result = self._run_gh("pr", "diff", str(pr.number), "--repo", self.upstream_repo)
                except subprocess.TimeoutExpired:
                    self._emit(f"Warning: Timeout getting diff for PR #{pr.number}")
                    continue
                if diff_result.returncode != 0:
                    self._emit(f"Warning: Could not get diff for PR #{pr.number}")
                    continue

                apply_result = subprocess.run(
                    ["git", "apply", "--3way"],
                    input=diff_result.stdout,
                    capture_output=True,
                    text=True,
                    cwd=self.repo_path,
                )
                if apply_result.returncode != 0:
                    self._emit(f"Warning: Patch failed for PR #{pr.number}, skipping")
                    self._run_git("checkout", "--", ".")
                    continue
                applied = True

            if applied:
                merged.append(pr)
                self._emit("Applied successfully")

        if self.regenerate_locks:
            for go_dir in go_dirs:
                self._emit(f"Running go mod tidy in {go_dir}...")
                subprocess.run(["go", "mod", "tidy"], capture_output=True, cwd=self._abs(go_dir))
                if original_go_version and go_dir == ".":
                    self._restore_go_version(original_go_version)

            for python_dir in python_dirs:
                self._emit(f"Running pipenv lock in {python_dir}...")
                lock_result = self._run_pipenv_lock(python_dir)
                if lock_result.returncode != 0:
                    self._emit(f"Warning: pipenv lock failed: {lock_result.stderr[:200]}")

            for npm_dir in npm_dirs:
                self._emit(f"Running npm install in {npm_dir}...")
                npm_result = self._run_npm_install(npm_dir)
                if npm_result.returncode != 0:
                    self._emit(f"Warning: npm install failed: {npm_result.stderr[:200]}")

        if merged:
            self._run_git("add", "-A")
            pr_list = "\n".join(f"- #{pr.number}: {pr.title}" for pr in merged)
            commit_msg = f"chore(deps): consolidate {len(merged)} dependency updates\n\nConsolidated PRs:\n{pr_list}"
            self._run_git("commit", "-m", commit_msg)

        return merged

    def _has_go_mod(self) -> bool:
        return os.path.exists(self._abs("go.mod"))

    def _get_go_version(self) -> str:
        try:
            with open(self._abs("go.mod")) as f:
                content = f.read()
            match = re.search(r"^go\s+([\d.]+)", content, re.MULTILINE)
            return match.group(1) if match else ""
        except (FileNotFoundError, IOError):
            return ""

    def _restore_go_version(self, version: str):
        try:
            go_mod_path = self._abs("go.mod")
            with open(go_mod_path) as f:
                content = f.read()
            new_content = re.sub(r"^go\s+[\d.]+", f"go {version}", content, count=1, flags=re.MULTILINE)
            if new_content != content:
                with open(go_mod_path, "w") as f:
                    f.write(new_content)
                self._emit(f"Restored go version to {version}")
        except (FileNotFoundError, IOError) as e:
            self._emit(f"Warning: Could not restore go version: {e}")

    def _extract_go_dep_from_title(self, title: str) -> str:
        match = re.search(
            r"(?:update|fix)\s+(?:module\s+)?([\w./-]+)\s+to\s+(v[\d.]+(?:-[\w.]+)?)",
            title,
            re.IGNORECASE,
        )
        if match:
            module, version = match.groups()
            return f"{module}@{version}"
        return ""

    def _extract_go_dep_from_pr_content(self, pr_number: int) -> str:
        try:
            diff_result = self._run_gh("pr", "diff", str(pr_number), "--repo", self.upstream_repo)
        except subprocess.TimeoutExpired:
            return ""
        if diff_result.returncode != 0:
            return ""

        for line in diff_result.stdout.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                match = re.match(r"^\+\s+([\w./-]+)\s+(v[\d.]+(?:-[\w.+-]+)?)", line)
                if match:
                    module, version = match.groups()
                    if "." in module and "/" in module:
                        return f"{module}@{version}"

        return ""

    def _extract_python_dep_from_title(self, title: str) -> tuple[str, str]:
        match = re.search(
            r"(?:update|fix)\s+dependency\s+([\w_-]+)\s+to\s+(?:v)?([\d.]+(?:[a-zA-Z][\w.]*)?)",
            title,
            re.IGNORECASE,
        )
        if match:
            package, version = match.groups()
            return (package, version)
        return ("", "")

    def _extract_python_dep_from_pr_content(self, pr_number: int) -> tuple[str, str]:
        try:
            diff_result = self._run_gh("pr", "diff", str(pr_number), "--repo", self.upstream_repo)
        except subprocess.TimeoutExpired:
            return ("", "")
        if diff_result.returncode != 0:
            return ("", "")

        for line in diff_result.stdout.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                match = re.match(
                    r'^\+\s*([\w_-]+)\s*=\s*["\'](?:[<>=~!]*)?(\d[\d.]*(?:[a-zA-Z][\w.]*)?)["\']',
                    line,
                )
                if match:
                    return match.groups()

        return ("", "")

    def _update_pipfile_dep(self, package: str, version: str, path: str) -> bool:
        try:
            with open(path) as f:
                content = f.read()

            pattern = rf'^(\s*{re.escape(package)}\s*=\s*["\'])([<>=~!]*)[\d.]+(?:[a-zA-Z][\w.]*)?(["\'])'
            new_content = re.sub(
                pattern, rf"\g<1>\g<2>{version}\3", content, flags=re.MULTILINE | re.IGNORECASE
            )

            if new_content == content:
                pattern = rf'^(\s*{re.escape(package)}\s*=\s*\{{\s*version\s*=\s*["\'])([<>=~!]*)[\d.]+(?:[a-zA-Z][\w.]*)?(["\'])'
                new_content = re.sub(
                    pattern, rf"\g<1>\g<2>{version}\3", content, flags=re.MULTILINE | re.IGNORECASE
                )

            if new_content != content:
                with open(path, "w") as f:
                    f.write(new_content)
                return True
            return False
        except (FileNotFoundError, IOError) as e:
            self._emit(f"Warning: Could not update {path}: {e}")
            return False

    def _extract_npm_dep_from_title(self, title: str) -> tuple[str, str]:
        match = re.search(
            r"(?:update|fix)\s+dependency\s+(@?[\w./-]+)\s+to\s+(?:v)?(\^?~?[\d.]+(?:-[\w.]+)?)",
            title,
            re.IGNORECASE,
        )
        if match:
            package, version = match.groups()
            return (package, version)
        return ("", "")

    def _extract_npm_dep_from_pr_content(self, pr_number: int) -> tuple[str, str]:
        try:
            diff_result = self._run_gh("pr", "diff", str(pr_number), "--repo", self.upstream_repo)
        except subprocess.TimeoutExpired:
            return ("", "")
        if diff_result.returncode != 0:
            return ("", "")

        skip_keys = {"version", "name", "description", "main", "scripts", "author", "license", "type"}

        for line in diff_result.stdout.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                match = re.match(
                    r'^\+\s*"(@?[\w./-]+)"\s*:\s*"(\^?~?[\d.]+(?:-[\w.]+)?)"', line
                )
                if match:
                    package, version = match.groups()
                    if package.lower() not in skip_keys:
                        return (package, version)

        return ("", "")

    def _update_package_json_dep(self, package: str, version: str, path: str) -> bool:
        try:
            with open(path) as f:
                data = json.load(f)

            updated = False
            for dep_type in ["dependencies", "devDependencies", "peerDependencies"]:
                if dep_type in data and package in data[dep_type]:
                    data[dep_type][package] = version
                    updated = True

            if updated:
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                    f.write("\n")
                return True
            return False
        except (FileNotFoundError, IOError, json.JSONDecodeError) as e:
            self._emit(f"Warning: Could not update {path}: {e}")
            return False

    def _push_branch(self, branch_name: str) -> bool:
        result = self._run_git("push", "-u", "origin", branch_name)
        if result.returncode != 0:
            self._emit(f"Push failed: {result.stderr}")
            return False
        self._emit("Push successful")
        return True

    def _create_consolidated_pr(
        self, branch_name: str, merged_prs: list[BotPR], ecosystem: str = ""
    ) -> ConsolidationResult:
        ecosystem_label = f" {ecosystem}" if ecosystem else ""
        title = f"chore(deps): consolidate {len(merged_prs)}{ecosystem_label} dependency updates"

        pr_list = "\n".join(f"- #{pr.number}: {pr.title}" for pr in merged_prs)

        body = f"""## Summary

Consolidates {len(merged_prs)}{ecosystem_label} dependency update PRs from `{self.bot_author}` into a single PR for easier review.

## Consolidated PRs

{pr_list}

## Why consolidate?

- Reduces CI/CD load from multiple small PRs
- Easier to review related dependency updates together
- Single merge commit instead of many

## Testing

- [ ] CI passes
- [ ] No breaking changes from dependency updates
"""

        try:
            result = self._run_gh(
                "pr", "create",
                "--repo", self.upstream_repo,
                "--title", title,
                "--body", body,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            self._emit("Timeout creating PR (120s)")
            return ConsolidationResult(success=False, error="Timeout creating PR")

        if result.returncode != 0:
            self._emit(f"Error creating PR: {result.stderr}")
            return ConsolidationResult(success=False, error=result.stderr)

        pr_url = result.stdout.strip()
        self._emit(f"Created PR: {pr_url}")

        return ConsolidationResult(success=True, pr_url=pr_url, consolidated_count=len(merged_prs))

    def _close_original_prs(self, merged_prs: list[BotPR], consolidated_pr_url: str):
        for pr in merged_prs:
            comment = f"Consolidated into {consolidated_pr_url}"
            try:
                self._run_gh(
                    "pr", "comment", str(pr.number), "--repo", self.upstream_repo, "--body", comment
                )
            except subprocess.TimeoutExpired:
                self._emit(f"Timeout commenting on PR #{pr.number}")

            try:
                self._run_gh("pr", "close", str(pr.number), "--repo", self.upstream_repo)
                self._emit(f"Closed PR #{pr.number}")
            except subprocess.TimeoutExpired:
                self._emit(f"Timeout closing PR #{pr.number}")

    def _cleanup_branch(self, branch_name: str):
        self._run_git("checkout", "-")
        self._run_git("branch", "-D", branch_name)
