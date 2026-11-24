import importlib.resources as resources
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import regex


class TranslationMode(str, Enum):
    """Supported translation modes"""

    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    GEMINI = "gemini"


# The default model of each provider. Every script and the OpenAI and Claude provider
# fallbacks read these, so change a default here only. OPENAI_MODEL, CLAUDE_MODEL and
# GEMINI_MODEL override them.
OPENAI_DEFAULT_MODEL = "gpt-6-luna"
CLAUDE_DEFAULT_MODEL = "claude-haiku-5-5"
GEMINI_DEFAULT_MODEL = "gemini-4-hydrogen"

# Centralized mappings to reduce duplication
MODE_TO_DEFAULT_MODEL = {
    TranslationMode.CHATGPT: OPENAI_DEFAULT_MODEL,
    TranslationMode.CLAUDE: CLAUDE_DEFAULT_MODEL,
    TranslationMode.GEMINI: GEMINI_DEFAULT_MODEL,
}

MODE_TO_ENV = {
    TranslationMode.CHATGPT: "OPENAI_API_KEY",
    TranslationMode.CLAUDE: "CLAUDE_API_KEY",
    TranslationMode.GEMINI: "GEMINI_API_KEY",
}

# Also accepted in place of the MODE_TO_ENV key
MODE_TO_ALT_ENV = {
    TranslationMode.CLAUDE: "ANTHROPIC_API_KEY",
}

# Must match AnthropicClient.DEFAULT_EFFORT (kept here so the scripts can show it
# without importing the optional anthropic SDK)
CLAUDE_DEFAULT_EFFORT = "medium"


def claude_effort() -> str:
    """The Claude effort level the provider will use (CLAUDE_EFFORT or the default)"""
    return os.getenv("CLAUDE_EFFORT") or CLAUDE_DEFAULT_EFFORT


# Fast mode: the same model at up to 2.5 times the output speed and twice the token
# price. Opt-in through CLAUDE_SPEED=fast, and only on the models listed here; the
# client sends nothing for any other model. Fast and standard requests share no prompt
# cache, so the speed is fixed for a run.
CLAUDE_FAST_MODEL_PATTERN = regex.compile(r"^claude-opus-5-5$", regex.IGNORECASE)


def claude_speed(model: str | None) -> str | None:
    """Return "fast" when CLAUDE_SPEED asks for it and the model has fast mode"""
    requested = (os.getenv("CLAUDE_SPEED") or "").strip().lower()
    if requested == "fast" and CLAUDE_FAST_MODEL_PATTERN.match(model or ""):
        return "fast"
    return None


# exsubs and transubs request thinking level low on Gemini 4 unless GEMINI_THINKING_LEVEL
# is set (kept here so the scripts can use it without importing the google-genai SDK)
GEMINI_4_MODEL_PATTERN = regex.compile(r"gemini-[4-9]", regex.IGNORECASE)
GEMINI_4_THINKING_LEVEL = "low"


def gemini_thinking_level(model: str | None) -> str | None:
    """The Gemini thinking level the scripts request (GEMINI_THINKING_LEVEL, else low on
    Gemini 4), or None to leave it to the provider"""
    level = (os.getenv("GEMINI_THINKING_LEVEL") or "").strip()
    if level:
        return level
    if GEMINI_4_MODEL_PATTERN.search(model or ""):
        return GEMINI_4_THINKING_LEVEL
    return None


MODE_TO_RATE_LIMIT = {
    TranslationMode.CHATGPT: "5000",
    TranslationMode.CLAUDE: "600",
    TranslationMode.GEMINI: "1000",
}


@dataclass
class MKVConfig:
    """Configuration for MKV subtitle extraction and translation"""

    instruction_file: Path | None = None
    target_language: str = "Finnish"
    default_translation_mode: TranslationMode = TranslationMode.GEMINI

    SUPPORTED_LANGUAGES = [
        "Finnish",
        "Greek",
        "German",
        "French",
        "Spanish",
        "Italian",
        "English",
    ]

    def __post_init__(self):
        """Initialize configurable paths from environment or defaults"""
        if self.instruction_file is not None:
            return

        # Check environment variable first
        env_path = os.getenv("LLMSUBTRANS_INSTRUCTION_FILE")
        if env_path:
            self.instruction_file = Path(env_path)
            return

        # Check for instructions in the local repository 'instructions' folder first
        # This allows running from source without installing to ~/.config
        lang_code = self.get_language_code(self.target_language)
        repo_root = Path(__file__).parent.parent.parent
        local_instruction_path = (
            repo_root / "instructions" / f"instructions_{lang_code}.txt"
        )

        if local_instruction_path.exists():
            self.instruction_file = local_instruction_path
            return

        # Default to user's config directory and try to seed from packaged instructions
        config_dir = Path.home() / ".config" / "llm-subtrans"
        lang_code = self.get_language_code(self.target_language)
        default_path = config_dir / f"instructions_{lang_code}.txt"

        # Ensure directory exists
        try:
            os.makedirs(config_dir, exist_ok=True)
        except Exception:
            # If directory cannot be created, still set the intended path
            pass

        # Attempt to seed from packaged resource if not already present
        # Primary request: use the repository file 'instructions/instructions_fi.txt' as default
        try:
            pkg = "instructions"
            resource_name = f"instructions_{lang_code}.txt"
            traversable = resources.files(pkg).joinpath(resource_name)
            if not default_path.exists() and traversable.is_file():
                with resources.as_file(traversable) as src_path:
                    content = Path(src_path).read_text(encoding="utf-8")
                    try:
                        default_path.write_text(content, encoding="utf-8")
                    except Exception:
                        # If writing fails, ignore and fall back to non-existent path
                        pass
        except Exception:
            # If packaged resource is unavailable, continue silently
            pass

        self.instruction_file = default_path

    @staticmethod
    def get_language_code(lang: str) -> str:
        """Get ISO 639-1 language code from language name"""
        codes = {
            "Finnish": "fi",
            "Greek": "el",
            "German": "de",
            "French": "fr",
            "Spanish": "es",
            "Italian": "it",
            "English": "en",
        }
        return codes.get(lang, lang.lower()[:2])
