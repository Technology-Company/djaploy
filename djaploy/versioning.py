"""
Git versioning utilities for djaploy
"""

import re
import subprocess
from pathlib import Path
from typing import Optional


def get_latest_version_tag(git_dir: Path) -> Optional[str]:
    """Find the latest semantic version tag (v*.*.*)"""
    try:
        result = subprocess.run(
            ["git", "tag", "-l", "v*.*.*", "--sort=-v:refname"],
            capture_output=True,
            check=True,
            text=True,
            cwd=git_dir,
        )

        tags = result.stdout.strip().split('\n')
        semver_pattern = re.compile(r'^v\d+\.\d+\.\d+$')

        for tag in tags:
            tag = tag.strip()
            if tag and semver_pattern.match(tag):
                return tag

        return None

    except subprocess.CalledProcessError:
        return None


def parse_version(version: str) -> tuple:
    """Parse a semantic version string into (major, minor, patch)"""
    match = re.match(r'^v?(\d+)\.(\d+)\.(\d+)$', version)
    if not match:
        raise ValueError(f"Invalid version format: {version}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def increment_version(version: Optional[str], increment_type: str = "patch") -> str:
    """Increment semantic version. Returns 'v1.0.0' if version is None."""
    if version is None:
        return "v1.0.0"

    major, minor, patch = parse_version(version)

    if increment_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif increment_type == "minor":
        minor += 1
        patch = 0
    elif increment_type == "patch":
        patch += 1
    else:
        raise ValueError(f"Invalid increment_type: {increment_type}. Must be 'major', 'minor', or 'patch'")

    return f"v{major}.{minor}.{patch}"


def create_git_tag(git_dir: Path, tag: str, message: Optional[str] = None, push: bool = True) -> bool:
    """Create annotated git tag and optionally push to origin"""
    try:
        tag_message = message or tag
        subprocess.run(
            ["git", "tag", "-a", tag, "-m", tag_message],
            capture_output=True,
            check=True,
            text=True,
            cwd=git_dir,
        )

        if push:
            subprocess.run(
                ["git", "push", "origin", tag],
                capture_output=True,
                check=True,
                text=True,
                cwd=git_dir,
            )

        return True

    except subprocess.CalledProcessError as e:
        print(f"[VERSIONING] Warning: Failed to create/push tag '{tag}': {e.stderr}")
        return False


def get_commits_since_tag(git_dir: Path, tag: Optional[str], limit: int = 50, format_string: str = "%s") -> str:
    """Get commit messages since tag (or last N commits if no tag)"""
    try:
        if tag:
            cmd = ["git", "log", f"--format={format_string}", f"-n{limit}", f"{tag}..HEAD"]
        else:
            cmd = ["git", "log", f"--format={format_string}", f"-n{limit}"]

        result = subprocess.run(cmd, capture_output=True, check=True, text=True, cwd=git_dir)
        return result.stdout.strip()

    except subprocess.CalledProcessError:
        return ""


def get_current_commit_hash(git_dir: Path, short: bool = False) -> Optional[str]:
    """Get current HEAD commit hash"""
    try:
        cmd = ["git", "rev-parse"]
        if short:
            cmd.append("--short")
        cmd.append("HEAD")

        result = subprocess.run(cmd, capture_output=True, check=True, text=True, cwd=git_dir)
        return result.stdout.strip()

    except subprocess.CalledProcessError:
        return None


def get_commit_count_since_tag(git_dir: Path, tag: Optional[str]) -> int:
    """Get the number of commits since a tag"""
    try:
        if tag:
            cmd = ["git", "rev-list", "--count", f"{tag}..HEAD"]
        else:
            cmd = ["git", "rev-list", "--count", "HEAD"]

        result = subprocess.run(cmd, capture_output=True, check=True, text=True, cwd=git_dir)
        return int(result.stdout.strip())

    except (subprocess.CalledProcessError, ValueError):
        return 0


def tag_exists(git_dir: Path, tag: str) -> bool:
    """Check if a git tag exists"""
    try:
        subprocess.run(["git", "rev-parse", tag], capture_output=True, check=True, cwd=git_dir)
        return True
    except subprocess.CalledProcessError:
        return False
