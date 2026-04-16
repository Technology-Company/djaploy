"""Tests for versioning and changelog utilities."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from djaploy.versioning import (
    parse_version,
    increment_version,
    extract_changelog_from_tag,
    get_latest_version_tag,
    get_previous_version_tag,
    get_version_tags,
    get_commits_since_tag,
    get_current_commit_hash,
    get_commit_count_since_tag,
    tag_exists,
    get_tag_message,
    create_git_tag,
    get_default_remote,
)
from djaploy.changelog import (
    SimpleChangelogGenerator,
    get_changelog_generator,
)


# ---------------------------------------------------------------------------
# Pure functions (no git repo needed)
# ---------------------------------------------------------------------------

class TestParseVersion(unittest.TestCase):

    def test_with_v_prefix(self):
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))

    def test_without_v_prefix(self):
        self.assertEqual(parse_version("1.2.3"), (1, 2, 3))

    def test_zero_version(self):
        self.assertEqual(parse_version("v0.0.0"), (0, 0, 0))

    def test_large_numbers(self):
        self.assertEqual(parse_version("v10.200.3000"), (10, 200, 3000))

    def test_invalid_format_raises(self):
        for bad in ("v1.2", "v1", "hello", "", "v1.2.3.4", "v1.2.x"):
            with self.assertRaises(ValueError, msg=f"Should reject: {bad}"):
                parse_version(bad)


class TestIncrementVersion(unittest.TestCase):

    def test_patch(self):
        self.assertEqual(increment_version("v1.2.3", "patch"), "v1.2.4")

    def test_minor(self):
        self.assertEqual(increment_version("v1.2.3", "minor"), "v1.3.0")

    def test_major(self):
        self.assertEqual(increment_version("v1.2.3", "major"), "v2.0.0")

    def test_none_returns_v1_0_0(self):
        self.assertEqual(increment_version(None), "v1.0.0")

    def test_invalid_increment_type(self):
        with self.assertRaises(ValueError):
            increment_version("v1.0.0", "hotfix")

    def test_minor_resets_patch(self):
        self.assertEqual(increment_version("v1.5.9", "minor"), "v1.6.0")

    def test_major_resets_minor_and_patch(self):
        self.assertEqual(increment_version("v3.7.12", "major"), "v4.0.0")


class TestExtractChangelogFromTag(unittest.TestCase):

    def test_empty_message(self):
        self.assertEqual(extract_changelog_from_tag(""), "")

    def test_single_line(self):
        self.assertEqual(extract_changelog_from_tag("Release v1.0.0"), "Release v1.0.0")

    def test_multiline_strips_title(self):
        msg = "Release v1.0.0\n\nAdded feature X.\nFixed bug Y."
        result = extract_changelog_from_tag(msg)
        self.assertEqual(result, "Added feature X.\nFixed bug Y.")

    def test_separator_cuts_commits(self):
        msg = "Release v1.0.0\n\nChangelog summary\n\n---\nCommits:\nabc\ndef"
        result = extract_changelog_from_tag(msg)
        self.assertEqual(result, "Changelog summary")

    def test_only_separator(self):
        # When title is the only line before ---, it's returned as-is
        msg = "Release v1.0.0\n---\nraw commits"
        result = extract_changelog_from_tag(msg)
        self.assertEqual(result, "Release v1.0.0")


# ---------------------------------------------------------------------------
# Git-dependent tests (use a temp repo)
# ---------------------------------------------------------------------------

class _TempGitRepo:
    """Context manager that creates a temporary git repo with some commits."""

    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        self.git_dir = Path(self.tmpdir)
        self._run("git", "init")
        self._run("git", "config", "user.email", "test@test.com")
        self._run("git", "config", "user.name", "Test")
        # Initial commit
        (self.git_dir / "README.md").write_text("init")
        self._run("git", "add", ".")
        self._run("git", "commit", "-m", "initial commit")
        return self

    def __exit__(self, *args):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run(self, *cmd):
        subprocess.run(cmd, cwd=self.tmpdir, capture_output=True, check=True)

    def commit(self, message: str):
        (self.git_dir / "README.md").write_text(message)
        self._run("git", "add", ".")
        self._run("git", "commit", "-m", message)

    def tag(self, name: str, message: str = ""):
        self._run("git", "tag", "-a", name, "-m", message or name)


class TestGitVersionTags(_TempGitRepo, unittest.TestCase):
    """Override setUp/tearDown to use the temp repo as a test fixture."""

    def setUp(self):
        self.__enter__()

    def tearDown(self):
        self.__exit__(None, None, None)

    def test_no_tags(self):
        self.assertEqual(get_version_tags(self.git_dir), [])
        self.assertIsNone(get_latest_version_tag(self.git_dir))

    def test_single_tag(self):
        self.tag("v1.0.0")
        self.assertEqual(get_latest_version_tag(self.git_dir), "v1.0.0")

    def test_tags_sorted_newest_first(self):
        self.tag("v1.0.0")
        self.commit("second")
        self.tag("v1.1.0")
        self.commit("third")
        self.tag("v2.0.0")
        tags = get_version_tags(self.git_dir)
        self.assertEqual(tags, ["v2.0.0", "v1.1.0", "v1.0.0"])

    def test_previous_version_tag(self):
        self.tag("v1.0.0")
        self.commit("second")
        self.tag("v1.1.0")
        self.assertEqual(get_previous_version_tag(self.git_dir), "v1.0.0")

    def test_previous_version_tag_none_when_single(self):
        self.tag("v1.0.0")
        self.assertIsNone(get_previous_version_tag(self.git_dir))

    def test_non_semver_tags_ignored(self):
        self.tag("v1.0.0")
        self.commit("second")
        self.tag("release-candidate")
        self.commit("third")
        self.tag("v1.0.1")
        tags = get_version_tags(self.git_dir)
        self.assertEqual(tags, ["v1.0.1", "v1.0.0"])

    def test_limit(self):
        for i in range(5):
            self.commit(f"commit {i}")
            self.tag(f"v1.0.{i}")
        tags = get_version_tags(self.git_dir, limit=3)
        self.assertEqual(len(tags), 3)
        self.assertEqual(tags[0], "v1.0.4")


class TestGitCommitUtils(_TempGitRepo, unittest.TestCase):

    def setUp(self):
        self.__enter__()

    def tearDown(self):
        self.__exit__(None, None, None)

    def test_get_current_commit_hash(self):
        h = get_current_commit_hash(self.git_dir)
        self.assertIsNotNone(h)
        self.assertEqual(len(h), 40)

    def test_get_current_commit_hash_short(self):
        h = get_current_commit_hash(self.git_dir, short=True)
        self.assertIsNotNone(h)
        self.assertTrue(len(h) < 40)

    def test_commits_since_tag(self):
        self.tag("v1.0.0")
        self.commit("feat: add X")
        self.commit("fix: bug Y")
        commits = get_commits_since_tag(self.git_dir, "v1.0.0")
        self.assertIn("feat: add X", commits)
        self.assertIn("fix: bug Y", commits)

    def test_commits_since_tag_none(self):
        # No tag → should return all commits
        commits = get_commits_since_tag(self.git_dir, None)
        self.assertIn("initial commit", commits)

    def test_commit_count_since_tag(self):
        self.tag("v1.0.0")
        self.commit("a")
        self.commit("b")
        self.commit("c")
        self.assertEqual(get_commit_count_since_tag(self.git_dir, "v1.0.0"), 3)

    def test_commit_count_since_tag_zero(self):
        self.tag("v1.0.0")
        self.assertEqual(get_commit_count_since_tag(self.git_dir, "v1.0.0"), 0)

    def test_tag_exists(self):
        self.tag("v1.0.0")
        self.assertTrue(tag_exists(self.git_dir, "v1.0.0"))
        self.assertFalse(tag_exists(self.git_dir, "v9.9.9"))

    def test_get_tag_message(self):
        self.tag("v1.0.0", message="Release v1.0.0\n\nBig changes here.")
        msg = get_tag_message(self.git_dir, "v1.0.0")
        self.assertIn("Big changes here", msg)

    def test_get_tag_message_nonexistent(self):
        msg = get_tag_message(self.git_dir, "v9.9.9")
        self.assertIsNone(msg)


class TestCreateGitTag(_TempGitRepo, unittest.TestCase):

    def setUp(self):
        self.__enter__()

    def tearDown(self):
        self.__exit__(None, None, None)

    def test_create_tag_no_push(self):
        ok = create_git_tag(self.git_dir, "v1.0.0", message="release", push=False)
        self.assertTrue(ok)
        self.assertTrue(tag_exists(self.git_dir, "v1.0.0"))

    def test_create_duplicate_tag_fails(self):
        create_git_tag(self.git_dir, "v1.0.0", push=False)
        ok = create_git_tag(self.git_dir, "v1.0.0", push=False)
        self.assertFalse(ok)


class TestGetDefaultRemote(_TempGitRepo, unittest.TestCase):

    def setUp(self):
        self.__enter__()

    def tearDown(self):
        self.__exit__(None, None, None)

    def test_falls_back_to_origin_when_no_remote(self):
        remote = get_default_remote(self.git_dir)
        self.assertEqual(remote, "origin")


# ---------------------------------------------------------------------------
# Changelog generators
# ---------------------------------------------------------------------------

class TestSimpleChangelogGenerator(unittest.TestCase):

    def setUp(self):
        self.gen = SimpleChangelogGenerator()

    def test_empty(self):
        self.assertEqual(self.gen.generate(""), "No changes")
        self.assertEqual(self.gen.generate("  \n  "), "No changes")

    def test_single_line(self):
        self.assertEqual(self.gen.generate("fix: bug"), "fix: bug")

    def test_few_lines_joined(self):
        result = self.gen.generate("fix: bug\nfeat: new thing")
        self.assertEqual(result, "fix: bug. feat: new thing.")

    def test_many_lines_truncated(self):
        commits = "\n".join([f"commit {i}" for i in range(10)])
        result = self.gen.generate(commits)
        self.assertIn("And 7 more changes", result)


class TestGetChangelogGenerator(unittest.TestCase):

    def test_simple_is_default(self):
        gen = get_changelog_generator()
        self.assertIsInstance(gen, SimpleChangelogGenerator)

    def test_explicit_simple(self):
        gen = get_changelog_generator("simple")
        self.assertIsInstance(gen, SimpleChangelogGenerator)

    def test_unknown_type_falls_back_to_simple(self):
        gen = get_changelog_generator("unknown_type")
        self.assertIsInstance(gen, SimpleChangelogGenerator)

    def test_llm_without_api_key_falls_back(self):
        gen = get_changelog_generator("llm", {})
        self.assertIsInstance(gen, SimpleChangelogGenerator)


if __name__ == "__main__":
    unittest.main()
