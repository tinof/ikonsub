import logging
import os

from PySubtrans import init_translator
from PySubtrans.Options import Options
from PySubtrans.SubtitleProject import SubtitleProject
from scripts.check_imports import check_required_imports
from scripts.subtrans_common import (
    CreateArgParser,
    CreateOptions,
    CreateProject,
    InitLogger,
)


def main() -> int:
    """Main entry point for gpt-subtrans command"""
    check_required_imports(["PySubtrans", "openai"], "openai")

    # Provider configuration
    provider = "OpenAI"
    default_model = os.getenv("OPENAI_MODEL") or "gpt-5-mini"

    parser = CreateArgParser("Translates subtitles using an OpenAI model")
    parser.add_argument(
        "-k",
        "--apikey",
        type=str,
        default=None,
        help="Your OpenAI API Key (https://platform.openai.com/account/api-keys)",
    )
    parser.add_argument(
        "-b",
        "--apibase",
        type=str,
        default="https://api.openai.com/v1",
        help="API backend base address.",
    )
    parser.add_argument(
        "-m", "--model", type=str, default=None, help="The model to use for translation"
    )
    parser.add_argument(
        "--httpx",
        action="store_true",
        help="Use the httpx library for custom api_base requests. May help if you receive a 307 redirect error.",
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="Proxy URL (e.g., socks5://127.0.0.1:1089 or http://127.0.0.1:8080)",
    )
    args = parser.parse_args()

    InitLogger("gpt-subtrans", args.debug)

    try:
        options: Options = CreateOptions(
            args,
            provider,
            use_httpx=args.httpx,
            api_base=args.apibase,
            proxy=args.proxy,
            model=args.model or default_model,
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
