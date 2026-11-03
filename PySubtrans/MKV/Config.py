import os
import importlib.resources as resources
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class TranslationMode(str, Enum):
    """Supported translation modes"""

    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    GEMINI = "gemini"


# Centralized mappings to reduce duplication
MODE_TO_DEFAULT_MODEL = {
    TranslationMode.CHATGPT: "gpt-4o-mini",
    TranslationMode.CLAUDE: "claude-haiku-5-5",
    TranslationMode.GEMINI: "gemini-3.1-flash-lite",
}

MODE_TO_ENV = {
    TranslationMode.CHATGPT: "OPENAI_API_KEY",
    TranslationMode.CLAUDE: "CLAUDE_API_KEY",
    TranslationMode.GEMINI: "GEMINI_API_KEY",
}

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
