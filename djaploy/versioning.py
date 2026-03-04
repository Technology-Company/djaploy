"""
Git versioning utilities for djaploy
"""

import re
import subprocess
from pathlib import Path
from typing import Optional, List


def get_latest_version_tag(git_dir: Path) -> Optional[str]:
    """
    Find the latest semantic version tag (v*.*.*)

    Args:
        git_dir: Path to the git repository

    Returns:
        Latest version tag string (e.g., "v1.2.3") or None if no version tags exist
    """
    try:
        # Get all tags that match semantic versioning pattern
        result = subprocess.run(
            ["git", "tag", "-l", "v*.*.*", "--sort=-v:refname"],
            capture_output=True,
            check=True,
            text=True,
            cwd=git_dir,
        )

        tags = result.stdout.strip().split('\n')

        # Filter to only valid semver tags
        semver_pattern = re.compile(r'^v\d+\.\d+\.\d+$')
        for tag in tags:
            tag = tag.strip()
            if tag and semver_pattern.match(tag):
                return tag

        return None

    except subprocess.CalledProcessError:
        return None


def parse_version(version: str) -> tuple:
    """
    Parse a semantic version string into components.

    Args:
        version: Version string (e.g., "v1.2.3")

    Returns:
        Tuple of (major, minor, patch) as integers
    """
    match = re.match(r'^v?(\d+)\.(\d+)\.(\d+)$', version)
    if not match:
        raise ValueError(f"Invalid version format: {version}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def increment_version(version: Optional[str], increment_type: str = "patch") -> str:
    """
    Increment semantic version.

    Args:
        version: Current version (e.g., "v1.0.0") or None
        increment_type: "major", "minor", or "patch"

    Returns:
        New version string (e.g., "v1.0.1")
        If version is None, returns "v1.0.0"
    """
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


def create_git_tag(
    git_dir: Path,
    tag: str,
    message: Optional[str] = None,
    push: bool = True
) -> bool:
    """
    Create annotated git tag and optionally push to origin.

    Args:
        git_dir: Path to the git repository
        tag: Tag name (e.g., "v1.0.0")
        message: Tag message (optional, defaults to tag name)
        push: Whether to push the tag to origin

    Returns:
        True if successful, False otherwise
    """
    try:
        # Create annotated tag
        tag_message = message or tag
        subprocess.run(
            ["git", "tag", "-a", tag, "-m", tag_message],
            capture_output=True,
            check=True,
            text=True,
            cwd=git_dir,
        )

        if push:
            # Push tag to origin
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


def get_commits_since_tag(
    git_dir: Path,
    tag: Optional[str],
    limit: int = 50,
    format_string: str = "%s"
) -> str:
    """
    Get commit messages since tag (or last N commits if no tag).

    Args:
        git_dir: Path to the git repository
        tag: Tag to get commits since (or None for last N commits)
        limit: Maximum number of commits to return
        format_string: Git log format string (default: subject only)

    Returns:
        Formatted commit list string (one commit per line)
    """
    try:
        if tag:
            # Get commits since tag
            cmd = [
                "git", "log",
                f"--format={format_string}",
                f"-n{limit}",
                f"{tag}..HEAD"
            ]
        else:
            # Get last N commits
            cmd = [
                "git", "log",
                f"--format={format_string}",
                f"-n{limit}"
            ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            text=True,
            cwd=git_dir,
        )

        return result.stdout.strip()

    except subprocess.CalledProcessError:
        return ""


def get_current_commit_hash(git_dir: Path, short: bool = False) -> Optional[str]:
    """
    Get current HEAD commit hash.

    Args:
        git_dir: Path to the git repository
        short: If True, return short hash (7 chars)

    Returns:
        Commit hash string or None if not in a git repo
    """
    try:
        cmd = ["git", "rev-parse"]
        if short:
            cmd.append("--short")
        cmd.append("HEAD")

        result = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            text=True,
            cwd=git_dir,
        )

        return result.stdout.strip()

    except subprocess.CalledProcessError:
        return None


def get_commit_count_since_tag(git_dir: Path, tag: Optional[str]) -> int:
    """
    Get the number of commits since a tag.

    Args:
        git_dir: Path to the git repository
        tag: Tag to count commits since (or None for all commits)

    Returns:
        Number of commits
    """
    try:
        if tag:
            cmd = ["git", "rev-list", "--count", f"{tag}..HEAD"]
        else:
            cmd = ["git", "rev-list", "--count", "HEAD"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            text=True,
            cwd=git_dir,
        )

        return int(result.stdout.strip())

    except (subprocess.CalledProcessError, ValueError):
        return 0


def tag_exists(git_dir: Path, tag: str) -> bool:
    """
    Check if a git tag exists.

    Args:
        git_dir: Path to the git repository
        tag: Tag name to check

    Returns:
        True if tag exists, False otherwise
    """
    try:
        subprocess.run(
            ["git", "rev-parse", tag],
            capture_output=True,
            check=True,
            cwd=git_dir,
        )
        return True
    except subprocess.CalledProcessError:
        return False
