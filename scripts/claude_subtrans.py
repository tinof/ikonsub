import os
import logging

from scripts.check_imports import check_required_imports
from scripts.subtrans_common import (
    InitLogger,
    CreateArgParser,
    CreateOptions,
    CreateProject,
)

from PySubtrans import init_translator
from PySubtrans.Options import Options
from PySubtrans.SubtitleProject import SubtitleProject


def main() -> int:
    """Main entry point for claude-subtrans command"""
    check_required_imports(["PySubtrans", "anthropic"], "claude")

    # Provider configuration
    provider = "Claude"
    default_model = os.getenv("CLAUDE_MODEL") or "claude-haiku-5-5"

    parser = CreateArgParser("Translates subtitles using Anthropic's Claude AI")
    parser.add_argument(
        "-k",
        "--apikey",
        type=str,
        default=None,
        help="Your Anthropic API Key (https://console.anthropic.com/settings/keys)",
    )
    parser.add_argument(
        "-m", "--model", type=str, default=None, help="The model to use for translation"
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="SOCKS proxy URL (e.g., socks://127.0.0.1:1089)",
    )
    args = parser.parse_args()

    InitLogger("claude-subtrans", args.debug)

    try:
        options: Options = CreateOptions(
            args, provider, model=args.model or default_model, proxy=args.proxy
        )

        # Create a project for the translation
        project: SubtitleProject = CreateProject(options, args)

        # Translate the subtitles
        translator = init_translator(options)
        project.TranslateSubtitles(translator)

        if project.use_project_file:
            logging.info(f"Writing project data to {str(project.projectfile)}")
            project.SaveProjectFile()

        return 0

    except Exception as e:
        print("Error:", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
