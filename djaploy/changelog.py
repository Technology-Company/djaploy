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
    """Returns commits as bullet list"""

    def generate(self, commits: str) -> str:
        if not commits or not commits.strip():
            return "No changes"

        lines = commits.strip().split('\n')
        return '\n'.join(f'- {line.strip()}' for line in lines if line.strip())


class LLMChangelogGenerator(ChangelogGenerator):
    """Uses LLM API to summarize commits (OpenAI-compatible endpoints)"""

    DEFAULT_API_URL = "https://api.mistral.ai/v1/chat/completions"
    DEFAULT_MODEL = "devstral-small-latest"

    DEFAULT_PROMPT = """You are a technical writer creating a changelog for a software release.

Given the following git commit messages, create a concise, user-friendly changelog summary.

Rules:
1. Format: Output ONLY raw bullet points. No introductions, no greetings, no conclusions.
2. Structure: Start every bullet point with a strong action verb followed by a colon (e.g., Added, Fixed, Updated, Improved, Removed, Resolved). 
   - Example: "- Added: Support for user avatars."
3. Distillation: Group similar changes together into a single, cohesive point.
4. Constraint: Maximum of 7 bullet points. Prioritize the most impactful changes.
5. Filtering: Ignore trivial commits, merge commits, WIPs, and minor typos.
6. Language: Use simple, clear language. Do not include commit hashes, file names, or deep technical jargon.

<commits>
{commits}
</commits>

Output:"""

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
