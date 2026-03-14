"""Tests for djaploy versioning utilities (pure functions)."""

import unittest

from djaploy.versioning import parse_version, increment_version, extract_changelog_from_tag


class TestParseVersion(unittest.TestCase):
    """Test parse_version extracts (major, minor, patch) tuples."""

    def test_standard_version(self):
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))

    def test_without_v_prefix(self):
        self.assertEqual(parse_version("1.2.3"), (1, 2, 3))

    def test_zero_version(self):
        self.assertEqual(parse_version("v0.0.0"), (0, 0, 0))

    def test_large_numbers(self):
        self.assertEqual(parse_version("v100.200.300"), (100, 200, 300))

    def test_invalid_format_raises(self):
        with self.assertRaises(ValueError):
            parse_version("not-a-version")

    def test_partial_version_raises(self):
        with self.assertRaises(ValueError):
            parse_version("v1.2")

    def test_extra_parts_raises(self):
        with self.assertRaises(ValueError):
            parse_version("v1.2.3.4")

    def test_prerelease_tag_raises(self):
        with self.assertRaises(ValueError):
            parse_version("v1.2.3-beta")


class TestIncrementVersion(unittest.TestCase):
    """Test increment_version with different increment types."""

    def test_patch_increment(self):
        self.assertEqual(increment_version("v1.2.3", "patch"), "v1.2.4")

    def test_minor_increment(self):
        self.assertEqual(increment_version("v1.2.3", "minor"), "v1.3.0")

    def test_major_increment(self):
        self.assertEqual(increment_version("v1.2.3", "major"), "v2.0.0")

    def test_none_version_returns_v1_0_0(self):
        self.assertEqual(increment_version(None, "patch"), "v1.0.0")

    def test_none_version_ignores_increment_type(self):
        self.assertEqual(increment_version(None, "major"), "v1.0.0")

    def test_invalid_increment_type_raises(self):
        with self.assertRaises(ValueError):
            increment_version("v1.0.0", "hotfix")

    def test_patch_from_zero(self):
        self.assertEqual(increment_version("v0.0.0", "patch"), "v0.0.1")

    def test_minor_resets_patch(self):
        self.assertEqual(increment_version("v1.5.9", "minor"), "v1.6.0")

    def test_major_resets_minor_and_patch(self):
        self.assertEqual(increment_version("v3.7.12", "major"), "v4.0.0")


class TestExtractChangelogFromTag(unittest.TestCase):
    """Test extract_changelog_from_tag extracts summary from tag messages."""

    def test_empty_message(self):
        self.assertEqual(extract_changelog_from_tag(""), "")

    def test_none_message(self):
        self.assertEqual(extract_changelog_from_tag(None), "")

    def test_single_line(self):
        self.assertEqual(extract_changelog_from_tag("Single line"), "Single line")

    def test_multiline_skips_first_line(self):
        msg = "Release v1.0.0\n\nThis release adds feature X."
        self.assertEqual(extract_changelog_from_tag(msg), "This release adds feature X.")

    def test_separator_trims_after_dashes(self):
        msg = "Release v1.0.0\n\nChanges here\n\n---\nCommits:\nfoo\nbar"
        self.assertEqual(extract_changelog_from_tag(msg), "Changes here")

    def test_only_separator(self):
        msg = "v1.0.0\n---\nsome commits"
        result = extract_changelog_from_tag(msg)
        self.assertNotIn("some commits", result)


if __name__ == "__main__":
    unittest.main()
