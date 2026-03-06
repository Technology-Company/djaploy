"""
Changelog generators for djaploy
"""

import json
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from .certificates import OpSecret


class ChangelogGenerator(ABC):
    """Base class for changelog generators"""

    @abstractmethod
    def generate(self, commits: str) -> str:
        pass


class SimpleChangelogGenerator(ChangelogGenerator):
    """Returns commits as a simple summary"""

    def generate(self, commits: str) -> str:
        if not commits or not commits.strip():
            return "No changes"

        lines = [line.strip() for line in commits.strip().split('\n') if line.strip()]
        if len(lines) == 1:
            return lines[0]
        elif len(lines) <= 3:
            return ". ".join(lines) + "."
        else:
            return ". ".join(lines[:3]) + f". And {len(lines) - 3} more changes."


class LLMChangelogGenerator(ChangelogGenerator):
    """Uses LLM API to summarize commits (OpenAI-compatible endpoints)"""

    DEFAULT_API_URL = "https://api.mistral.ai/v1/chat/completions"
    DEFAULT_MODEL = "devstral-small-latest"

    DEFAULT_PROMPT = """You are a technical writer creating a changelog summary for a software release.

Given the following git commit messages, create a concise paragraph summarizing the changes.

Rules:
1. Format: Output ONLY a brief paragraph (2-5 sentences). No bullet points, no lists, no headers.
2. Style: Write in a natural, flowing prose style that summarizes the key changes.
3. Focus: Highlight the most important changes and group related updates together.
4. Filtering: Ignore trivial commits, merge commits, WIPs, and minor typos.
5. Language: Use simple, clear language. Do not include commit hashes, file names, or deep technical jargon.
6. Tone: Professional but accessible. Focus on what users/stakeholders care about.

Example output:
"This release adds user authentication with OAuth2 support and improves dashboard performance. Several bug fixes address issues with data export and notification delivery."

<commits>
{commits}
</commits>

Summary:"""

    def __init__(self, api_key: str, model: Optional[str] = None, prompt_template: Optional[str] = None, api_url: Optional[str] = None):
        self.api_key = str(OpSecret(api_key))
        self.api_url = api_url or self.DEFAULT_API_URL
        self.model = model or self.DEFAULT_MODEL
        self.prompt_template = prompt_template or self.DEFAULT_PROMPT

    def generate(self, commits: str) -> str:
        if not commits or not commits.strip():
            return "No changes"

        prompt = self.prompt_template.format(commits=commits)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        data = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}]
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


def get_changelog_generator(generator_type: str = "simple", config: Optional[Dict[str, Any]] = None) -> ChangelogGenerator:
    """Factory function to create a changelog generator"""
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
            model=config.get("model"),
            prompt_template=config.get("prompt_template"),
            api_url=config.get("api_url"),
        )

    else:
        print(f"[CHANGELOG] Warning: Unknown generator type '{generator_type}', using simple generator")
        return SimpleChangelogGenerator()
