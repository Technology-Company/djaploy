"""
Artifact creation for deployments
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .config import DjaployConfig


def create_artifact(config: DjaployConfig, 
                   mode: str = "latest",
                   release_tag: Optional[str] = None) -> Path:
    """
    Create deployment artifact based on mode
    
    Args:
        config: DjaployConfig instance
        mode: Deployment mode ("local", "latest", "release")
        release_tag: Release tag if mode is "release"
        
    Returns:
        Path to created artifact
    """
    
    # Ensure artifact directory exists
    artifact_dir = config.git_dir / config.artifact_dir
    artifact_dir.mkdir(parents=True, exist_ok=True)
    
    if mode == "local":
        return _create_local_artifact(config, artifact_dir)
    elif mode == "latest":
        return _create_latest_artifact(config, artifact_dir)
    elif mode == "release":
        if not release_tag:
            raise ValueError("release_tag is required when mode is 'release'")
        return _create_release_artifact(config, artifact_dir, release_tag)
    else:
        raise ValueError(f"Invalid deployment mode: {mode}")


def _create_local_artifact(config: DjaployConfig, artifact_dir: Path) -> Path:
    """Create artifact from local uncommitted files"""
    
    artifact_file = artifact_dir / f"{config.project_name}.local.tar.gz"
    
    # Change to git directory
    original_dir = os.getcwd()
    os.chdir(config.git_dir)
    
    try:
        # Create artifact from git files (including uncommitted changes)
        subprocess.run(
            "git ls-files --others --exclude-standard --cached | tar Tzcf - {}".format(artifact_file),
            shell=True,
            check=True,
        )
        
        return artifact_file
    finally:
        os.chdir(original_dir)


def _create_latest_artifact(config: DjaployConfig, artifact_dir: Path) -> Path:
    """Create artifact from latest git commit"""
    
    # Get git hash
    git_hash = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
        cwd=config.git_dir,
    ).stdout.strip()
    
    return _create_git_artifact(config, artifact_dir, git_hash)


def _create_release_artifact(config: DjaployConfig, artifact_dir: Path, release_tag: str) -> Path:
    """Create artifact from a specific release tag"""
    
    # Verify the tag exists
    try:
        subprocess.run(
            ["git", "rev-parse", release_tag],
            capture_output=True,
            check=True,
            cwd=config.git_dir,
        )
    except subprocess.CalledProcessError:
        raise ValueError(f"Release tag '{release_tag}' does not exist")
    
    return _create_git_artifact(config, artifact_dir, release_tag)


def _create_git_artifact(config: DjaployConfig, artifact_dir: Path, git_ref: str) -> Path:
    """Create artifact from a git reference"""

    artifact_tar = artifact_dir / f"{config.project_name}.{git_ref}.tar"
    artifact_file = artifact_dir / f"{config.project_name}.{git_ref}.tar.gz"

    # Change to git directory
    original_dir = os.getcwd()
    os.chdir(config.git_dir)

    try:
        # Build git archive command
        cmd = ["git", "archive", "--format=tar", "-o", str(artifact_tar), git_ref]

        # Check if there are extra files to add from config
        artifact_config = config.module_configs.get('artifact', {})
        extra_files = artifact_config.get('extra_files', [])

        # Add extra files to git index temporarily if they exist
        files_to_unstage = []
        if extra_files:
            print(f"[ARTIFACT] Adding {len(extra_files)} extra file(s) to archive")

        for extra_file in extra_files:
            extra_file_path = config.git_dir / extra_file
            if extra_file_path.exists():
                subprocess.run(["git", "add", "-f", extra_file], check=True)
                files_to_unstage.append(extra_file)
            else:
                print(f"[ARTIFACT] WARNING: File not found: {extra_file}")

        # If we added files to the index, use git write-tree to create archive
        if files_to_unstage:
            tree_hash = subprocess.run(
                ["git", "write-tree"],
                capture_output=True,
                check=True,
                text=True
            ).stdout.strip()

            # Use the tree object instead of git_ref
            cmd = ["git", "archive", "--format=tar", "-o", str(artifact_tar), tree_hash]

            # Create tar archive from git tree
            subprocess.run(cmd, check=True)

            # Unstage the files we temporarily added
            for extra_file in files_to_unstage:
                subprocess.run(["git", "reset", "HEAD", extra_file], check=True,
                             capture_output=True)  # Suppress output
        else:
            # No extra files, use original approach
            subprocess.run(cmd, check=True)

        # Compress the tar file
        subprocess.run(
            ["gzip", "-f", str(artifact_tar)],
            check=True,
        )

        return artifact_file
    finally:
        os.chdir(original_dir)