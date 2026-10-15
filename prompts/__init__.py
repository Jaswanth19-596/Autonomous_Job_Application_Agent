from pathlib import Path


PROMPTS_DIR = Path(__file__).parent


def load_prompt(filename: str, **values) -> str:
    """Load a prompt template and substitute its named placeholders."""
    template = (PROMPTS_DIR / filename).read_text(encoding="utf-8").rstrip("\n")
    template = template.format(**values) if values else template
    return template
