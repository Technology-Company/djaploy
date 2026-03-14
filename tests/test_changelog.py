"""Tests for djaploy changelog generators."""

import unittest
from unittest.mock import patch, MagicMock

from djaploy.changelog import SimpleChangelogGenerator, get_changelog_generator


class TestSimpleChangelogGenerator(unittest.TestCase):
    """Test SimpleChangelogGenerator output formatting."""

    def setUp(self):
        self.gen = SimpleChangelogGenerator()

    def test_empty_string(self):
        self.assertEqual(self.gen.generate(""), "No changes")

    def test_whitespace_only(self):
        self.assertEqual(self.gen.generate("   \n  \n  "), "No changes")

    def test_single_commit(self):
        self.assertEqual(self.gen.generate("Fix login bug"), "Fix login bug")

    def test_two_commits_joined(self):
        result = self.gen.generate("Fix login bug\nAdd signup page")
        self.assertEqual(result, "Fix login bug. Add signup page.")

    def test_three_commits_joined(self):
        result = self.gen.generate("A\nB\nC")
        self.assertEqual(result, "A. B. C.")

    def test_more_than_three_shows_count(self):
        result = self.gen.generate("A\nB\nC\nD\nE")
        self.assertIn("A", result)
        self.assertIn("B", result)
        self.assertIn("C", result)
        self.assertIn("And 2 more changes", result)

    def test_strips_whitespace(self):
        result = self.gen.generate("  A  \n  B  ")
        self.assertEqual(result, "A. B.")

    def test_skips_blank_lines(self):
        result = self.gen.generate("A\n\n\nB")
        self.assertEqual(result, "A. B.")


class TestGetChangelogGenerator(unittest.TestCase):
    """Test the factory function."""

    def test_simple_type(self):
        gen = get_changelog_generator("simple")
        self.assertIsInstance(gen, SimpleChangelogGenerator)

    def test_unknown_type_falls_back_to_simple(self):
        gen = get_changelog_generator("unknown_type")
        self.assertIsInstance(gen, SimpleChangelogGenerator)

    def test_llm_without_key_falls_back_to_simple(self):
        gen = get_changelog_generator("llm", {})
        self.assertIsInstance(gen, SimpleChangelogGenerator)

    def test_llm_with_key_creates_llm_generator(self):
        from djaploy.changelog import LLMChangelogGenerator
        with patch("djaploy.changelog.OpSecret", side_effect=lambda x: x):
            gen = get_changelog_generator("llm", {"api_key": "test-key"})
            self.assertIsInstance(gen, LLMChangelogGenerator)


if __name__ == "__main__":
    unittest.main()
