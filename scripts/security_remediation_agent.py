#!/usr/bin/env python3
"""Dependabot security remediation agent for uv projects."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SEVERITY_ORDER = {"low": 1, "moderate": 2, "high": 3, "critical": 4}
DEFAULT_TIMEOUT_SECONDS = 30


def _log(message: str) -> None:
    """Write a status message to stderr."""
    print(message, file=sys.stderr)


def _run(command: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its text output."""
    return subprocess.run(command, text=True, capture_output=True, check=check)


def _normalize_package_name(name: str) -> str:
    """Normalize a Python package name for comparisons."""
    return re.sub(r"[-_.]+", "-", (name or "").strip().lower())


def _parse_semver(version: str | None) -> tuple[int, int, int] | None:
    """Parse a semantic version string into numeric parts."""
    if not version:
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _determine_update_type(old_version: str | None, new_version: str | None) -> str:
    """Classify the version change between two package versions."""
    old_semver = _parse_semver(old_version)
    new_semver = _parse_semver(new_version)
    if not old_semver or not new_semver:
        return "unknown"
    if new_semver[0] > old_semver[0]:
        return "major"
    if new_semver[1] > old_semver[1]:
        return "minor"
    if new_semver[2] > old_semver[2]:
        return "patch"
    return "unknown"


def _load_lock_versions(lock_path: Path) -> dict[str, str]:
    """Load package versions from a uv lock file."""
    if not lock_path.exists():
        return {}
    with lock_path.open("rb") as lock_file:
        content = tomllib.load(lock_file)
    result: dict[str, str] = {}
    for package in content.get("package", []):
        package_name = _normalize_package_name(package.get("name", ""))
        package_version = package.get("version")
        if package_name and isinstance(package_version, str):
            result[package_name] = package_version
    return result


def _choose_alert_vulnerability(alert: dict[str, Any]) -> dict[str, Any]:
    """Choose the vulnerability payload from a Dependabot alert."""
    vulnerabilities = alert.get("security_vulnerabilities")
    if isinstance(vulnerabilities, list) and vulnerabilities:
        return vulnerabilities[0]
    vulnerability = alert.get("security_vulnerability")
    if isinstance(vulnerability, dict):
        return vulnerability
    return {}


def _extract_alert_fields(alert: dict[str, Any]) -> dict[str, Any]:
    """Flatten Dependabot alert fields used by remediation."""
    vulnerability = _choose_alert_vulnerability(alert)
    dependency = alert.get("dependency", {}) or {}
    dep_package = dependency.get("package", {}) or {}
    vuln_package = vulnerability.get("package", {}) or {}
    first_patched = vulnerability.get("first_patched_version", {}) or {}
    advisory = alert.get("security_advisory", {}) or {}

    package_name = dep_package.get("name") or vuln_package.get("name") or ""
    ecosystem = (dep_package.get("ecosystem") or vuln_package.get("ecosystem") or "").lower()

    return {
        "alert_id": alert.get("number") or alert.get("id"),
        "state": alert.get("state", ""),
        "severity": str(advisory.get("severity", "")).lower(),
        "package": package_name,
        "ecosystem": ecosystem,
        "manifest_path": dependency.get("manifest_path") or "",
        "summary": advisory.get("summary") or "",
        "ghsa_id": advisory.get("ghsa_id") or "",
        "cve_id": advisory.get("cve_id") or "",
        "first_patched_version": first_patched.get("identifier") or "",
        "html_url": alert.get("html_url") or "",
    }


def _passes_filters(fields: dict[str, Any], min_severity: str) -> bool:
    """Return whether an alert matches remediation filters."""
    severity = fields.get("severity", "")
    ecosystem = fields.get("ecosystem", "")
    state = fields.get("state", "")
    if state != "open":
        return False
    if ecosystem != "pip":
        return False
    severity_rank = SEVERITY_ORDER.get(severity, 0)
    threshold_rank = SEVERITY_ORDER.get(min_severity, SEVERITY_ORDER["high"])
    return severity_rank >= threshold_rank


def _api_get_json(repo: str, token: str, path: str, params: dict[str, Any] | None = None) -> Any:
    """Fetch JSON from the GitHub API."""
    payload, _ = _api_get_json_with_headers(repo, token, path, params=params)
    return payload


def _api_get_json_with_headers(
    repo: str,
    token: str,
    path: str,
    params: dict[str, Any] | None = None,
    url: str | None = None,
) -> tuple[Any, dict[str, str]]:
    """Fetch JSON and response headers from the GitHub API."""
    request_url = url
    if not request_url:
        encoded_params = urllib.parse.urlencode(params or {})
        request_url = f"https://api.github.com/repos/{repo}{path}"
        if encoded_params:
            request_url = f"{request_url}?{encoded_params}"

    request = urllib.request.Request(request_url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8")), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        hint = ""
        if exc.code == 403 and "Resource not accessible by integration" in detail:
            hint = (
                " Hint: this token cannot read Dependabot alerts. "
                "Use a token with Dependabot alerts read permission "
                "(for example, set DEPENDABOT_ALERTS_TOKEN secret)."
            )
        raise RuntimeError(f"GitHub API request failed ({exc.code}): {detail}{hint}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub API request failed: {exc.reason}") from exc


def _extract_next_link_url(link_header: str) -> str | None:
    """Extract the pagination next URL from a Link header."""
    if not link_header:
        return None
    for raw_part in link_header.split(","):
        part = raw_part.strip()
        if ";" not in part:
            continue
        url_part, *attrs = [section.strip() for section in part.split(";")]
        if 'rel="next"' not in attrs:
            continue
        if url_part.startswith("<") and url_part.endswith(">"):
            return url_part[1:-1]
    return None


def _fetch_open_alerts(repo: str, token: str) -> list[dict[str, Any]]:
    """Fetch all open Dependabot alerts for a repository."""
    alerts: list[dict[str, Any]] = []
    next_url: str | None = None
    while True:
        params = {"state": "open", "per_page": 100} if next_url is None else None
        payload, headers = _api_get_json_with_headers(
            repo,
            token,
            "/dependabot/alerts",
            params=params,
            url=next_url,
        )
        if not isinstance(payload, list):
            break
        alerts.extend(payload)
        next_url = _extract_next_link_url(headers.get("Link", ""))
        if not next_url:
            break
    return alerts


def _safe_branch_component(value: str) -> str:
    """Sanitize text for use in a branch name component."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-")
    return cleaned or "dependency"


def _build_fallback_constraint(first_patched_version: str) -> str | None:
    """Build a major-bounded package constraint from a patched version."""
    semver = _parse_semver(first_patched_version)
    if not semver:
        return None
    major, minor, patch = semver
    return f">={major}.{minor}.{patch},<{major + 1}.0.0"


def _format_pr_body(fields: dict[str, Any], result: dict[str, Any], old_version: str | None, new_version: str | None) -> str:
    """Render the pull request body for an automated remediation."""
    lines = [
        "## Automated Security Remediation",
        "",
        f"- Alert: `{fields['alert_id']}`",
        f"- Package: `{fields['package']}`",
        f"- Severity: `{fields['severity']}`",
        f"- GHSA: `{fields['ghsa_id'] or 'n/a'}`",
        f"- CVE: `{fields['cve_id'] or 'n/a'}`",
        f"- First patched version: `{fields['first_patched_version'] or 'unknown'}`",
        f"- Previous version: `{old_version or 'unknown'}`",
        f"- New version: `{new_version or 'unknown'}`",
        f"- Update type: `{result['update_type']}`",
        "",
        "### Validation",
        "Executed:",
        "- `uv sync`",
        "- `uv run pytest`",
        "",
        "### Source Alert",
        fields["html_url"] or "Unavailable",
    ]
    return "\n".join(lines)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write an indented JSON payload to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _list_alerts(args: argparse.Namespace) -> int:
    """List remediation-eligible Dependabot alerts."""
    alerts = _fetch_open_alerts(args.repo, args.token)
    filtered = []
    for alert in alerts:
        fields = _extract_alert_fields(alert)
        if not _passes_filters(fields, args.severity_threshold):
            continue
        filtered.append(
            {
                "alert_id": fields["alert_id"],
                "package": fields["package"],
                "severity": fields["severity"],
                "ecosystem": fields["ecosystem"],
                "first_patched_version": fields["first_patched_version"],
                "html_url": fields["html_url"],
            }
        )

    filtered = filtered[: args.max_alerts]
    payload = {"alerts": filtered, "count": len(filtered)}
    _write_json(Path(args.output), payload)
    _log(f"Selected {len(filtered)} alert(s) for remediation.")
    return 0


def _find_alert(repo: str, token: str, alert_id: int) -> dict[str, Any] | None:
    """Find an open Dependabot alert by id or number."""
    for alert in _fetch_open_alerts(repo, token):
        current_id = alert.get("number") or alert.get("id")
        if int(current_id) == alert_id:
            return alert
    return None


def _remediate_alert(args: argparse.Namespace) -> int:
    """Attempt to remediate one Dependabot alert."""
    alert = _find_alert(args.repo, args.token, args.alert_id)
    if not alert:
        payload = {"status": "skipped", "reason": f"Alert {args.alert_id} is not open or not found."}
        _write_json(Path(args.output), payload)
        return 0

    fields = _extract_alert_fields(alert)
    if not _passes_filters(fields, args.severity_threshold):
        payload = {"status": "skipped", "reason": f"Alert {args.alert_id} does not match filters.", "alert": fields}
        _write_json(Path(args.output), payload)
        return 0

    package_name = fields["package"]
    if not package_name:
        payload = {"status": "skipped", "reason": "Alert has no package name.", "alert": fields}
        _write_json(Path(args.output), payload)
        return 0

    branch = f"secfix/alert-{fields['alert_id']}-{_safe_branch_component(package_name)}"
    title = f"fix(security): remediate alert {fields['alert_id']} for {package_name}"

    base_result: dict[str, Any] = {
        "status": "failed",
        "alert": fields,
        "branch": branch,
        "title": title,
        "labels": ["security", "dependabot", "auto-remediation"],
        "update_type": "unknown",
        "auto_merge_eligible": False,
        "commands": [],
        "reason": "",
    }

    if args.dry_run:
        base_result["status"] = "dry_run"
        base_result["reason"] = "Dry run requested; no dependency updates performed."
        _write_json(Path(args.output), base_result)
        return 0

    lock_path = Path("uv.lock")
    before_versions = _load_lock_versions(lock_path)
    package_key = _normalize_package_name(package_name)
    old_version = before_versions.get(package_key)

    update_command = ["uv", "lock", "--upgrade-package", package_name]
    base_result["commands"].append(" ".join(shlex.quote(part) for part in update_command))
    update_run = _run(update_command)
    remediation_attempts = [update_run]

    if update_run.returncode != 0 and fields["first_patched_version"]:
        constraint = _build_fallback_constraint(fields["first_patched_version"])
        if constraint:
            add_command = ["uv", "add", f"{package_name}{constraint}"]
            base_result["commands"].append(" ".join(shlex.quote(part) for part in add_command))
            remediation_attempts.append(_run(add_command))

    changed_files = _run(["git", "status", "--porcelain", "--", "pyproject.toml", "uv.lock"])
    if changed_files.returncode != 0:
        base_result["reason"] = "Failed to inspect git status after remediation."
        _write_json(Path(args.output), base_result)
        return 0

    if not changed_files.stdout.strip():
        stderr_text = "\n".join([attempt.stderr.strip() for attempt in remediation_attempts if attempt.stderr.strip()])
        if not fields["first_patched_version"]:
            base_result["status"] = "skipped"
            base_result["reason"] = "No resolvable first patched version in alert metadata."
        else:
            base_result["status"] = "skipped"
            base_result["reason"] = (
                "Remediation produced no dependency changes."
                + (f" Last error: {stderr_text}" if stderr_text else "")
            )
        _write_json(Path(args.output), base_result)
        return 0

    quality_commands = [
        ["uv", "sync"],
        ["uv", "run", "pytest"],
    ]

    for command in quality_commands:
        base_result["commands"].append(" ".join(shlex.quote(part) for part in command))
        completed = _run(command)
        if completed.returncode != 0:
            base_result["status"] = "validation_failed"
            base_result["reason"] = (
                f"Quality gate failed: {' '.join(command)}\n"
                f"stdout:\n{completed.stdout.strip()}\n"
                f"stderr:\n{completed.stderr.strip()}"
            )
            _write_json(Path(args.output), base_result)
            return 0

    after_versions = _load_lock_versions(lock_path)
    new_version = after_versions.get(package_key)
    update_type = _determine_update_type(old_version, new_version)
    base_result["update_type"] = update_type
    base_result["auto_merge_eligible"] = update_type in {"patch", "minor"}
    base_result["status"] = "remediated"
    base_result["old_version"] = old_version
    base_result["new_version"] = new_version
    base_result["pr_body"] = _format_pr_body(fields, base_result, old_version, new_version)
    _write_json(Path(args.output), base_result)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description="Dependabot security remediation agent")
    parser.add_argument("--repo", required=True, help="Repository in owner/name format.")
    parser.add_argument("--token", required=True, help="GitHub token for API access.")
    parser.add_argument(
        "--severity-threshold",
        choices=["high", "critical"],
        default="high",
        help="Minimum alert severity to remediate.",
    )
    parser.add_argument("--output", default="security-remediation-output.json", help="Path for output JSON.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List alerts that match remediation criteria.")
    list_parser.add_argument("--max-alerts", type=int, default=10, help="Maximum alerts to return.")
    list_parser.set_defaults(func=_list_alerts)

    remediate_parser = subparsers.add_parser("remediate", help="Remediate a single alert.")
    remediate_parser.add_argument("--alert-id", type=int, required=True, help="Dependabot alert id/number.")
    remediate_parser.add_argument("--dry-run", action="store_true", help="Plan actions without changing dependencies.")
    remediate_parser.set_defaults(func=_remediate_alert)

    return parser


def main() -> int:
    """Run the security remediation command-line interface."""
    parser = _build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
