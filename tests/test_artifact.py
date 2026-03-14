"""Integration tests for artifact creation using a real temporary git repo."""

import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from djaploy.config import DjaployConfig
from djaploy.artifact import create_artifact


def _git(repo_dir, *args):
    """Run git command with signing disabled."""
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo_dir, check=True, capture_output=True,
    )


def _init_git_repo(repo_dir: Path):
    """Create a minimal git repo with one commit."""
    _git(repo_dir, "init")
    _git(repo_dir, "config", "user.email", "test@test.com")
    _git(repo_dir, "config", "user.name", "Test")
    _git(repo_dir, "config", "commit.gpgsign", "false")

    # Create project files
    (repo_dir / "manage.py").write_text("# manage.py")
    (repo_dir / "myapp").mkdir()
    (repo_dir / "myapp" / "__init__.py").write_text("")
    (repo_dir / "myapp" / "models.py").write_text("# models")

    _git(repo_dir, "add", ".")
    _git(repo_dir, "commit", "-m", "Initial commit")


class TestCreateArtifactLatest(unittest.TestCase):
    """Test artifact creation from latest commit."""

    def test_creates_tar_gz_from_latest_commit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)

            config = DjaployConfig(
                project_name="testproject",
                git_dir=repo,
                djaploy_dir=repo / "infra",
            )

            artifact_path = create_artifact(config, mode="latest")

            self.assertTrue(artifact_path.exists())
            self.assertTrue(str(artifact_path).endswith(".tar.gz"))

            # Verify archive contents
            with tarfile.open(str(artifact_path), "r:gz") as tar:
                names = tar.getnames()
                self.assertIn("manage.py", names)
                self.assertIn("myapp/__init__.py", names)
                self.assertIn("myapp/models.py", names)

    def test_artifact_dir_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)

            config = DjaployConfig(
                project_name="testproject",
                git_dir=repo,
                djaploy_dir=repo / "infra",
            )

            artifact_path = create_artifact(config, mode="latest")
            self.assertTrue(artifact_path.parent.exists())
            self.assertEqual(artifact_path.parent.name, "deployment")


class TestCreateArtifactLocal(unittest.TestCase):
    """Test artifact creation from local (uncommitted) files."""

    def test_includes_uncommitted_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)

            # Add uncommitted file
            (repo / "new_file.py").write_text("# new")
            _git(repo, "add", "new_file.py")

            config = DjaployConfig(
                project_name="testproject",
                git_dir=repo,
                djaploy_dir=repo / "infra",
            )

            artifact_path = create_artifact(config, mode="local")

            with tarfile.open(str(artifact_path), "r:gz") as tar:
                names = tar.getnames()
                self.assertIn("new_file.py", names)
                self.assertIn("manage.py", names)


class TestCreateArtifactRelease(unittest.TestCase):
    """Test artifact creation from a release tag."""

    def test_creates_from_tag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)

            # Create a tag
            _git(repo, "tag", "-a", "v1.0.0", "-m", "Release v1.0.0")

            config = DjaployConfig(
                project_name="testproject",
                git_dir=repo,
                djaploy_dir=repo / "infra",
            )

            artifact_path = create_artifact(config, mode="release", release_tag="v1.0.0")
            self.assertTrue(artifact_path.exists())

            with tarfile.open(str(artifact_path), "r:gz") as tar:
                names = tar.getnames()
                self.assertIn("manage.py", names)

    def test_nonexistent_tag_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)

            config = DjaployConfig(
                project_name="testproject",
                git_dir=repo,
                djaploy_dir=repo / "infra",
            )

            with self.assertRaises(ValueError):
                create_artifact(config, mode="release", release_tag="v99.0.0")

    def test_release_mode_without_tag_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)

            config = DjaployConfig(
                project_name="testproject",
                git_dir=repo,
                djaploy_dir=repo / "infra",
            )

            with self.assertRaises(ValueError):
                create_artifact(config, mode="release")


class TestCreateArtifactInvalidMode(unittest.TestCase):
    """Test invalid mode raises."""

    def test_invalid_mode_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)

            config = DjaployConfig(
                project_name="testproject",
                git_dir=repo,
                djaploy_dir=repo / "infra",
            )

            with self.assertRaises(ValueError):
                create_artifact(config, mode="invalid")


class TestCreateArtifactExtraFiles(unittest.TestCase):
    """Test artifact creation with extra_files config."""

    def test_extra_files_included_in_local_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_git_repo(repo)

            # Create a gitignored file
            (repo / ".gitignore").write_text("build/\n")
            (repo / "build").mkdir()
            (repo / "build" / "output.css").write_text("body{}")
            _git(repo, "add", ".gitignore")
            _git(repo, "commit", "-m", "Add gitignore")

            config = DjaployConfig(
                project_name="testproject",
                git_dir=repo,
                djaploy_dir=repo / "infra",
                module_configs={"artifact": {"extra_files": ["build/output.css"]}},
            )

            artifact_path = create_artifact(config, mode="local")

            with tarfile.open(str(artifact_path), "r:gz") as tar:
                names = tar.getnames()
                self.assertIn("build/output.css", names)


if __name__ == "__main__":
    unittest.main()
