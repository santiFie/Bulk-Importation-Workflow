"""
Utility to load agent system prompts from the ``agent_prompts/`` directory at runtime.

Each agent has a dedicated Markdown file in ``agent_prompts/`` containing its full
system prompt (merged from the previous inline SystemMessage + the detailed docs).

Usage::

    from core.utils.prompt_loader import load_agent_prompt

    prompt = load_agent_prompt("bots_agent")
    # With dynamic substitutions (e.g. for github_agent):
    prompt = load_agent_prompt("github_agent",
                               WORKSPACE_PATH=config.WORKSPACE_PATH,
                               DEFAULT_REPO=config.DEFAULT_GITHUB_REPO,
                               DOWNLOADS_DIR=config.DOWNLOADS_DIR)
"""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_PROMPTS_DIR = _PROJECT_ROOT / "agent_prompts"


def load_agent_prompt(agent_name: str, **kwargs: str) -> str:
    """Load the system prompt for *agent_name* from ``agent_prompts/<agent_name>.md``.

    Args:
        agent_name: Stem of the Markdown file (e.g. ``"bots_agent"``).
        **kwargs:   Optional key-value pairs used for ``str.format_map()``
                    substitution of ``{PLACEHOLDER}`` tokens in the file.

    Returns:
        The prompt string, with placeholders replaced if *kwargs* were supplied.

    Raises:
        FileNotFoundError: If the corresponding ``.md`` file does not exist.
    """
    prompt_file = AGENT_PROMPTS_DIR / f"{agent_name}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            f"Agent prompt file not found: {prompt_file}\n"
            f"Create 'agent_prompts/{agent_name}.md' to fix this."
        )

    content = prompt_file.read_text(encoding="utf-8")
    if kwargs:
        content = content.format_map(kwargs)
    return content
