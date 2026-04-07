"""
Artifact creation for deployments.

Builds a tar.gz from the project source and returns a temp path.
The caller is responsible for copying/renaming per app_name.
"""

import gzip
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from django.conf import settings


def _get_git_dir() -> Path:
    return Path(settings.GIT_DIR)


def _get_artifact_dir() -> Path:
    git_dir = _get_git_dir()
    artifact_dir_name = getattr(settings, 'ARTIFACT_DIR', 'deployment')
    artifact_dir = git_dir / artifact_dir_name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir


def create_artifact(mode: str = "latest",
                    release_tag: Optional[str] = None,
                    artifact_conf: Optional[dict] = None) -> Path:
    """Create a deployment artifact.

    Returns a path to a temp-named tar.gz in the artifact directory.
    The caller copies it to ``{app_name}.{ref}.tar.gz`` per host.
    """
    artifact_dir = _get_artifact_dir()
    extra_files = (artifact_conf or {}).get("extra_files", [])

    if mode == "local":
        return _create_local_artifact(artifact_dir, extra_files)
    elif mode == "latest":
        return _create_latest_artifact(artifact_dir, extra_files)
    elif mode == "release":
        if not release_tag:
            raise ValueError("release_tag is required when mode is 'release'")
        return _create_release_artifact(artifact_dir, release_tag, extra_files)
    else:
        raise ValueError(f"Invalid deployment mode: {mode}")


def copy_artifact_for_host(artifact_path: Path, app_name: str) -> Path:
    """Copy an artifact with the host's app_name in the filename.

    Given ``deployment/_build.abc123.tar.gz`` returns
    ``deployment/myapp.abc123.tar.gz``.
    """
    # Extract the ref from the temp filename: _build.{ref}.tar.gz -> {ref}
    stem = artifact_path.name
    if stem.startswith("_build."):
        ref_part = stem[len("_build."):]  # "abc123.tar.gz"
    else:
        ref_part = stem

    dest = artifact_path.parent / f"{app_name}.{ref_part}"
    shutil.copy2(str(artifact_path), str(dest))
    return dest


def _create_local_artifact(artifact_dir: Path, extra_files: list = None) -> Path:
    """Create artifact from local uncommitted files."""
    git_dir = _get_git_dir()
    artifact_file = artifact_dir / "_build.local.tar.gz"

    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--cached"],
        capture_output=True,
        check=True,
        text=True,
        cwd=git_dir,
    )
    file_list = [f for f in result.stdout.splitlines() if f.strip()]

    for extra_file in (extra_files or []):
        if (git_dir / extra_file).exists() and extra_file not in file_list:
            file_list.append(extra_file)

    import tarfile
    with tarfile.open(str(artifact_file), "w:gz") as tar:
        for f in file_list:
            full_path = git_dir / f
            if full_path.exists():
                tar.add(str(full_path), arcname=f)

    return artifact_file


def _create_latest_artifact(artifact_dir: Path, extra_files: list = None) -> Path:
    """Create artifact from latest git commit."""
    git_dir = _get_git_dir()
    git_hash = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
        cwd=git_dir,
    ).stdout.strip()

    return _create_git_artifact(artifact_dir, git_hash, extra_files)


def _create_release_artifact(artifact_dir: Path, release_tag: str, extra_files: list = None) -> Path:
    """Create artifact from a specific release tag."""
    git_dir = _get_git_dir()
    try:
        subprocess.run(
            ["git", "rev-parse", release_tag],
            capture_output=True,
            check=True,
            cwd=git_dir,
        )
    except subprocess.CalledProcessError:
        raise ValueError(f"Release tag '{release_tag}' does not exist")

    return _create_git_artifact(artifact_dir, release_tag, extra_files)


def _create_git_artifact(artifact_dir: Path, git_ref: str, extra_files: list = None) -> Path:
    """Create artifact from a git reference."""
    git_dir = _get_git_dir()
    artifact_tar = artifact_dir / f"_build.{git_ref}.tar"
    artifact_file = artifact_dir / f"_build.{git_ref}.tar.gz"

    cmd = ["git", "archive", "--format=tar", "-o", str(artifact_tar), git_ref]

    extra_files = extra_files or []
    files_to_unstage = []
    if extra_files:
        print(f"[ARTIFACT] Adding {len(extra_files)} extra file(s) to archive")

    for extra_file in extra_files:
        extra_file_path = git_dir / extra_file
        if extra_file_path.exists():
            subprocess.run(["git", "add", "-f", extra_file], check=True, cwd=git_dir)
            files_to_unstage.append(extra_file)
        else:
            print(f"[ARTIFACT] WARNING: File not found: {extra_file}")

    try:
        if files_to_unstage:
            tree_hash = subprocess.run(
                ["git", "write-tree"],
                capture_output=True,
                check=True,
                text=True,
                cwd=git_dir,
            ).stdout.strip()

            cmd = ["git", "archive", "--format=tar", "-o", str(artifact_tar), tree_hash]
            subprocess.run(cmd, check=True, cwd=git_dir)

            for extra_file in files_to_unstage:
                subprocess.run(["git", "reset", "HEAD", extra_file], check=True,
                             capture_output=True, cwd=git_dir)
        else:
            subprocess.run(cmd, check=True, cwd=git_dir)
    finally:
        # Ensure unstaged files are cleaned up even on failure
        for extra_file in files_to_unstage:
            subprocess.run(["git", "reset", "HEAD", extra_file],
                         capture_output=True, cwd=git_dir)

    with open(str(artifact_tar), 'rb') as f_in:
        with gzip.open(str(artifact_file), 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(str(artifact_tar))

    return artifact_file
