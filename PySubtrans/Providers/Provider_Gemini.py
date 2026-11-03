import importlib.util
import logging
import os
import subprocess

from PySubtrans.Options import env_float, env_int
from PySubtrans.SettingsType import SettingsType

if not importlib.util.find_spec("google"):
    from PySubtrans.Helpers.Localization import _

    logging.debug(
        _(
            "Google SDK (google-genai) is not installed. Gemini provider will not be available"
        )
    )
else:
    try:
        from collections import defaultdict

        from google import genai
        from google.genai.types import ListModelsConfig

        from PySubtrans.Helpers.Localization import _
        from PySubtrans.Providers.Clients.GeminiClient import GeminiClient
        from PySubtrans.TranslationClient import TranslationClient
        from PySubtrans.TranslationProvider import TranslationProvider

        def _default_vertex_project() -> str | None:
            # Derive default project from common envs or gcloud config
            for key in (
                "VERTEX_PROJECT",
                "GEMINI_VERTEX_PROJECT",
                "GOOGLE_CLOUD_PROJECT",
                "GCLOUD_PROJECT",
            ):
                value = os.getenv(key)
                if value:
                    return value
            try:
                result = subprocess.run(
                    ["gcloud", "config", "get-value", "project", "--quiet"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                project = (result.stdout or "").strip()
                if project and project != "(unset)":
                    return project
            except Exception:
                pass
            return None

        class GeminiProvider(TranslationProvider):
            name = "Gemini"

            def __init__(self, settings: SettingsType):
                # 0 is a valid budget (thinking off), so only None falls back to -1
                thinking_budget = settings.get_int(
                    "thinking_budget", env_int("GEMINI_THINKING_BUDGET", -1)
                )
                super().__init__(
                    self.name,
                    SettingsType(
                        {
                            "api_key": settings.get_str("api_key")
                            or os.getenv("GEMINI_API_KEY"),
                            "model": settings.get_str("model")
                            or os.getenv("GEMINI_MODEL"),
                            "stream_responses": settings.get_bool(
                                "stream_responses",
                                os.getenv("GEMINI_STREAM_RESPONSES", "True") == "True",
                            ),
                            "enable_thinking": settings.get_bool(
                                "enable_thinking",
                                os.getenv("GEMINI_ENABLE_THINKING", "False") == "True",
                            ),
                            "large_context_mode": settings.get_bool(
                                "large_context_mode",
                                os.getenv("GEMINI_LARGE_CONTEXT_MODE", "True")
                                == "True",
                            ),
                            "thinking_budget": -1
                            if thinking_budget is None
                            else thinking_budget,
                            "thinking_level": settings.get_str("thinking_level")
                            or os.getenv("GEMINI_THINKING_LEVEL"),
                            "temperature": settings.get_float(
                                "temperature", env_float("GEMINI_TEMPERATURE", 0.0)
                            ),
                            "rate_limit": settings.get_float(
                                "rate_limit", env_float("GEMINI_RATE_LIMIT", 60.0)
                            ),
                            "use_vertex": settings.get_bool(
                                "use_vertex",
                                os.getenv("GEMINI_USE_VERTEX", "False") == "True",
                            ),
                            "vertex_project": settings.get_str("vertex_project")
                            or _default_vertex_project(),
                            "vertex_location": settings.get_str("vertex_location")
                            or os.getenv("VERTEX_LOCATION")
                            or os.getenv("GEMINI_VERTEX_LOCATION")
                            or "europe-west1",
                            # Seconds one request may wait for the response (streamed: between
                            # chunks). A stalled request is retried instead of hanging the run.
                            "request_timeout": settings.get_float(
                                "request_timeout", env_float("GEMINI_REQUEST_TIMEOUT", 180.0)
                            ),
                            # "model@location" targets tried in order once the current one has
                            # failed attempts_per_target times, e.g. "gemini-4-argon@us".
                            "fallbacks": settings.get_str("fallbacks")
                            or os.getenv("GEMINI_FALLBACKS", ""),
                            "attempts_per_target": settings.get_int(
                                "attempts_per_target", env_int("GEMINI_ATTEMPTS_PER_TARGET", 2)
                            ),
                        }
                    ),
                )

                self.gemini_models = []

            @property
            def api_key(self) -> str | None:
                return self.settings.get_str("api_key")

            @property
            def use_vertex(self) -> bool:
                return self.settings.get_bool("use_vertex", False)

            @property
            def vertex_project(self) -> str | None:
                return self.settings.get_str("vertex_project")

            @property
            def vertex_location(self) -> str | None:
                return self.settings.get_str("vertex_location")

            def GetTranslationClient(self, settings: SettingsType) -> TranslationClient:
                client_settings = SettingsType(self.settings.copy())
                client_settings.update(settings)
                client_settings.update(
                    {
                        "model": self._get_true_name(self.selected_model),
                        "supports_streaming": True,
                        "supports_conversation": False,  # Actually it does support conversation
                        "supports_system_messages": False,  # This is what it doesn't support
                        "supports_system_prompt": True,
                        "use_vertex": self.use_vertex,
                        "vertex_project": self.vertex_project,
                        "vertex_location": self.vertex_location,
                    }
                )
                return GeminiClient(client_settings)

            def GetAvailableModels(self) -> list[str]:
                if not self.gemini_models:
                    self.gemini_models = self._get_gemini_models()

                return sorted([m.display_name for m in self.gemini_models])

            def ValidateSettings(self) -> bool:
                """
                Validate the settings for the provider
                """
                if not self.use_vertex and not self.api_key:
                    self.validation_message = _("API Key is required")
                    return False

                if self.use_vertex:
                    if not self.vertex_project:
                        self.validation_message = _("Vertex AI project ID is required")
                        return False

                    if not self.vertex_location:
                        self.validation_message = _("Vertex AI location is required")
                        return False

                # Some regions do not list Gemini models even when generation works.
                # Treat model listing failures as non-fatal for CLI use.
                if not self.GetAvailableModels():
                    logging.warning(
                        _(
                            "Unable to retrieve Gemini model list; proceeding with configured model"
                        )
                    )
                    return True

                return True

            def _get_gemini_models(self):
                try:
                    if not self.use_vertex and not self.api_key:
                        return []

                    gemini_client = self._create_client()
                    config = (
                        ListModelsConfig(query_base=True)
                        if not self.use_vertex
                        else ListModelsConfig()
                    )
                    all_models = gemini_client.models.list(config=config)

                    if self.use_vertex:
                        vertex_models = []
                        for model in all_models:
                            model_name = getattr(model, "name", "") or ""
                            if "models/gemini" not in model_name:
                                continue

                            if "embedding" in model_name:
                                continue

                            if getattr(model, "display_name", None) is None:
                                try:
                                    model.display_name = model_name.split("/")[-1]
                                except Exception:
                                    model.display_name = model_name

                            vertex_models.append(model)

                        return sorted(
                            {m.display_name: m for m in vertex_models}.values(),
                            key=lambda m: m.display_name,
                        )

                    generate_models = [
                        m
                        for m in all_models
                        if m.supported_actions
                        and "generateContent" in m.supported_actions
                    ]
                    text_models = [
                        m
                        for m in generate_models
                        if m.display_name
                        and "Vision" not in m.display_name
                        and "TTS" not in m.display_name
                    ]

                    return self._deduplicate_models(text_models)

                except Exception as e:
                    logging.error(
                        _("Unable to retrieve Gemini model list: {error}").format(
                            error=str(e)
                        )
                    )
                    return []

            def _get_true_name(self, name: str | None) -> str:
                if not self.gemini_models:
                    self.gemini_models = self._get_gemini_models()

                if not name:
                    return self.gemini_models[0].name if self.gemini_models else ""

                for m in self.gemini_models:
                    if m.display_name == name:
                        return m.name

                    if name and m.name.endswith(name):
                        return m.name

                    if m.name == f"models/{name}" or m.name == name:
                        return m.name

                # Fallback: trust the provided name if the model list is unavailable
                return name or ""

            def _create_client(self):
                if self.use_vertex:
                    if not self.vertex_project:
                        raise ValueError(_("Vertex AI project ID not configured"))

                    return genai.Client(
                        vertexai=True,
                        project=self.vertex_project,
                        location=self.vertex_location,
                    )

                return genai.Client(
                    api_key=self.api_key, http_options={"api_version": "v1alpha"}
                )

            def _deduplicate_models(self, models: list) -> list:
                """Deduplicate models by display name, preferring -latest versions"""
                # Group models by display name
                model_groups = defaultdict(list)
                for model in models:
                    model_groups[model.display_name].append(model)

                # Select best model for each display name
                selected_models = [
                    latest_models[0]
                    if (
                        latest_models := [
                            m for m in models if m.name.endswith("-latest")
                        ]
                    )
                    else min(models, key=lambda m: len(m.name))
                    for models in model_groups.values()
                ]

                return selected_models

    except ImportError:
        from PySubtrans.Helpers.Localization import _

        logging.info(
            _(
                "Latest Google AI SDK (google-genai) is not installed. Gemini provider will not be available. Run installer or `pip install google-genai` to fix."
            )
        )
