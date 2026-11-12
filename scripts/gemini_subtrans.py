import logging
import os

from PySubtrans import init_translator
from PySubtrans.MKV.Config import gemini_thinking_level
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
    """Main entry point for gemini-subtrans command"""
    check_required_imports(["PySubtrans", "google.genai"], "gemini")

    # Provider configuration
    provider = "Gemini"
    default_model = os.getenv("GEMINI_MODEL") or "gemini-4-hydrogen"

    parser = CreateArgParser("Translates subtitles using a Google Gemini model")
    parser.add_argument(
        "-k",
        "--apikey",
        type=str,
        default=None,
        help="Your Gemini API Key (https://makersuite.google.com/app/apikey)",
    )
    parser.add_argument(
        "-m", "--model", type=str, default=None, help="The model to use for translation"
    )
    parser.add_argument(
        "--vertex",
        action="store_true",
        help="Use Vertex AI Gemini with Application Default Credentials",
    )
    parser.add_argument(
        "--vertex-project", type=str, default=None, help="Vertex AI project ID"
    )
    parser.add_argument(
        "--vertex-location",
        type=str,
        default=None,
        help="Vertex AI location (default global)",
    )
    args = parser.parse_args()

    InitLogger("gemini-subtrans", args.debug)

    try:
        model = args.model or default_model
        options: Options = CreateOptions(
            args,
            provider,
            model=model,
            # GEMINI_THINKING_LEVEL, else low on Gemini 4; Options drops a None level
            thinking_level=gemini_thinking_level(model),
            use_vertex=args.vertex,
            vertex_project=args.vertex_project,
            vertex_location=args.vertex_location,
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
