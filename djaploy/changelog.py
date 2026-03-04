"""
Changelog generators for djaploy
"""

import json
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class ChangelogGenerator(ABC):
    """Base class for changelog generators"""

    @abstractmethod
    def generate(self, commits: str) -> str:
        """
        Generate changelog from commit messages.

        Args:
            commits: Raw commit messages (one per line)

        Returns:
            Formatted changelog string
        """
        pass


class SimpleChangelogGenerator(ChangelogGenerator):
    """
    Returns commits as bullet list (default).

    This is the simplest changelog generator that formats
    commit messages as a bulleted list.
    """

    def generate(self, commits: str) -> str:
        """
        Generate changelog as a simple bullet list.

        Args:
            commits: Raw commit messages (one per line)

        Returns:
            Formatted changelog with bullet points
        """
        if not commits or not commits.strip():
            return "No changes"

        lines = commits.strip().split('\n')
        return '\n'.join(f'- {line.strip()}' for line in lines if line.strip())


class LLMChangelogGenerator(ChangelogGenerator):
    """
    Uses LLM API to summarize commits.

    Supports Anthropic (Claude) and OpenAI APIs.
    Falls back to SimpleChangelogGenerator if API call fails.
    """

    DEFAULT_ANTHROPIC_MODEL = "claude-3-haiku-20240307"
    DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

    ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
    OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

    DEFAULT_PROMPT = """You are a technical writer creating a changelog for a software release.

Given the following git commit messages, create a concise, user-friendly changelog summary.

Guidelines:
- Group related changes together
- Use clear, non-technical language where possible
- Focus on what changed from a user's perspective
- Use bullet points
- Keep it brief (3-7 bullet points max)
- Don't include commit hashes or technical details
- Start each point with a verb (Added, Fixed, Updated, Improved, Removed)

Commit messages:
{commits}

Changelog:"""

    def __init__(
        self,
        api_key: str,
        provider: str = "anthropic",
        model: Optional[str] = None,
        prompt_template: Optional[str] = None,
    ):
        """
        Initialize LLM changelog generator.

        Args:
            api_key: API key for the LLM service
            provider: "anthropic" or "openai" (default: "anthropic")
            model: Model to use (optional, uses default for provider)
            prompt_template: Custom prompt template with {commits} placeholder
        """
        self.api_key = str(api_key)  # Convert StringLike (OpSecret) to string
        self.provider = provider.lower()

        if self.provider == "anthropic":
            self.model = model or self.DEFAULT_ANTHROPIC_MODEL
            self.api_url = self.ANTHROPIC_API_URL
        elif self.provider == "openai":
            self.model = model or self.DEFAULT_OPENAI_MODEL
            self.api_url = self.OPENAI_API_URL
        else:
            raise ValueError(f"Unsupported provider: {provider}. Use 'anthropic' or 'openai'")

        self.prompt_template = prompt_template or self.DEFAULT_PROMPT
        self._fallback = SimpleChangelogGenerator()

    def generate(self, commits: str) -> str:
        """
        Generate changelog using LLM API.

        Falls back to SimpleChangelogGenerator if API call fails.

        Args:
            commits: Raw commit messages (one per line)

        Returns:
            LLM-generated changelog or simple bullet list on failure
        """
        if not commits or not commits.strip():
            return "No changes"

        try:
            prompt = self.prompt_template.format(commits=commits)

            if self.provider == "anthropic":
                return self._call_anthropic(prompt)
            else:
                return self._call_openai(prompt)

        except Exception as e:
            print(f"[CHANGELOG] Warning: LLM API call failed, falling back to simple generator: {e}")
            return self._fallback.generate(commits)

    def _call_anthropic(self, prompt: str) -> str:
        """Call Anthropic Claude API"""
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        data = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }

        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )

        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result["content"][0]["text"].strip()

    def _call_openai(self, prompt: str) -> str:
        """Call OpenAI API"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        data = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }

        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )

        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result["choices"][0]["message"]["content"].strip()


def get_changelog_generator(
    generator_type: str = "simple",
    config: Optional[Dict[str, Any]] = None
) -> ChangelogGenerator:
    """
    Factory function to create a changelog generator.

    Args:
        generator_type: "simple" or "llm"
        config: Configuration dict for the generator
            For 'llm': api_key (required), provider, model, prompt_template

    Returns:
        ChangelogGenerator instance
    """
    config = config or {}

    if generator_type == "simple":
        return SimpleChangelogGenerator()

    elif generator_type == "llm":
        api_key = config.get("api_key")
        if not api_key:
            print("[CHANGELOG] Warning: No API key provided for LLM generator, using simple generator")
            return SimpleChangelogGenerator()

        return LLMChangelogGenerator(
            api_key=api_key,
            provider=config.get("provider", "anthropic"),
            model=config.get("model"),
            prompt_template=config.get("prompt_template"),
        )

    else:
        print(f"[CHANGELOG] Warning: Unknown generator type '{generator_type}', using simple generator")
        return SimpleChangelogGenerator()
