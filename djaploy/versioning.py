"""
Git versioning utilities for djaploy
"""

import re
import subprocess
from pathlib import Path
from typing import Optional


def get_default_remote(git_dir: Path) -> str:
    """Get the default remote for pushing tags.

    Tries to detect the remote in this order:
    1. Remote that the current branch tracks
    2. First available remote
    3. Falls back to 'origin'
    """
    # Try to get the remote the current branch tracks
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            capture_output=True,
            check=True,
            text=True,
            cwd=git_dir,
        )
        # Returns something like "origin/main" - extract remote name
        upstream = result.stdout.strip()
        if "/" in upstream:
            return upstream.split("/")[0]
    except subprocess.CalledProcessError:
        pass

    try:
        result = subprocess.run(
            ["git", "remote"],
            capture_output=True,
            check=True,
            text=True,
            cwd=git_dir,
        )
        remotes = result.stdout.strip().split("\n")
        if remotes and remotes[0]:
            return remotes[0]
    except subprocess.CalledProcessError:
        pass

    return "origin"


def get_version_tags(git_dir: Path, limit: int = 10) -> list:
    """Get list of semantic version tags sorted by version (newest first)"""
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

        version_tags = []
        for tag in tags:
            tag = tag.strip()
            if tag and semver_pattern.match(tag):
                version_tags.append(tag)
                if len(version_tags) >= limit:
                    break

        return version_tags

    except subprocess.CalledProcessError:
        return []


def get_latest_version_tag(git_dir: Path) -> Optional[str]:
    """Find the latest semantic version tag (v*.*.*)"""
    tags = get_version_tags(git_dir, limit=1)
    return tags[0] if tags else None


def get_previous_version_tag(git_dir: Path) -> Optional[str]:
    """Find the second-latest semantic version tag"""
    tags = get_version_tags(git_dir, limit=2)
    return tags[1] if len(tags) >= 2 else None


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
    """Create annotated git tag and optionally push to the detected remote"""
    print(f"[VERSIONING] Creating tag '{tag}' in {git_dir}")

    try:
        # Create tag locally
        tag_message = message or tag
        subprocess.run(
            ["git", "tag", "-a", tag, "-m", tag_message],
            capture_output=True,
            check=True,
            text=True,
            cwd=git_dir,
        )
        print(f"[VERSIONING] Tag '{tag}' created locally")

        if push:
            # Detect the remote to push to
            remote = get_default_remote(git_dir)
            print(f"[VERSIONING] Detected remote: '{remote}'")

            # Show git remote for debugging
            remote_result = subprocess.run(
                ["git", "remote", "-v"],
                capture_output=True,
                text=True,
                cwd=git_dir,
            )
            print(f"[VERSIONING] Git remotes:\n{remote_result.stdout.strip()}")

            # Push tag to detected remote
            push_result = subprocess.run(
                ["git", "push", remote, tag],
                capture_output=True,
                check=True,
                text=True,
                cwd=git_dir,
            )
            print(f"[VERSIONING] Tag '{tag}' pushed to '{remote}'")
            if push_result.stderr:
                print(f"[VERSIONING] Push output: {push_result.stderr.strip()}")

        return True

    except subprocess.CalledProcessError as e:
        print(f"[VERSIONING] ERROR: Failed to create/push tag '{tag}'")
        print(f"[VERSIONING] Command: {e.cmd}")
        print(f"[VERSIONING] Return code: {e.returncode}")
        if e.stdout:
            print(f"[VERSIONING] Stdout: {e.stdout}")
        if e.stderr:
            print(f"[VERSIONING] Stderr: {e.stderr}")
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


def get_tag_message(git_dir: Path, tag: str) -> Optional[str]:
    """Get the message/description of an annotated git tag"""
    try:
        result = subprocess.run(
            ["git", "tag", "-l", "--format=%(contents)", tag],
            capture_output=True,
            check=True,
            text=True,
            cwd=git_dir,
        )
        message = result.stdout.strip()
        return message if message else None
    except subprocess.CalledProcessError:
        return None


def extract_changelog_from_tag(tag_message: str) -> str:
    """Extract the changelog summary from a tag message.

    """
    if not tag_message:
        return ""

    if "---" in tag_message:
        return tag_message.split("---")[0].strip()

    return tag_message.strip()
