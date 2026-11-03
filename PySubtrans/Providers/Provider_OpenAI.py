import importlib.util
import logging
import os

from PySubtrans.Options import env_float
from PySubtrans.SettingsType import SettingsType

if not importlib.util.find_spec("openai"):
    from PySubtrans.Helpers.Localization import _

    logging.debug(
        _("OpenAI SDK is not installed. OpenAI provider will not be available")
    )
else:
    try:
        import openai  # type: ignore

        from PySubtrans.Helpers.Localization import _
        from PySubtrans.Providers.Clients.ChatGPTClient import ChatGPTClient
        from PySubtrans.Providers.Clients.OpenAIReasoningClient import (
            OpenAIReasoningClient,
        )
        from PySubtrans.SubtitleError import ProviderError
        from PySubtrans.TranslationClient import TranslationClient
        from PySubtrans.TranslationProvider import TranslationProvider

        class OpenAiProvider(TranslationProvider):
            name = "OpenAI"

            def __init__(self, settings: SettingsType):
                super().__init__(
                    self.name,
                    SettingsType(
                        {
                            "api_key": settings.get_str(
                                "api_key", os.getenv("OPENAI_API_KEY")
                            ),
                            "api_base": settings.get_str(
                                "api_base", os.getenv("OPENAI_API_BASE")
                            ),
                            "model": settings.get_str(
                                "model", os.getenv("OPENAI_MODEL", "gpt-5-mini")
                            ),
                            "temperature": settings.get_float(
                                "temperature", env_float("OPENAI_TEMPERATURE", 0.0)
                            ),
                            "rate_limit": settings.get_float(
                                "rate_limit", env_float("OPENAI_RATE_LIMIT")
                            ),
                            "free_plan": settings.get_bool(
                                "free_plan", os.getenv("OPENAI_FREE_PLAN") == "True"
                            ),
                            "max_instruct_tokens": settings.get_int(
                                "max_instruct_tokens",
                                int(os.getenv("MAX_INSTRUCT_TOKENS", "2048")),
                            ),
                            "use_httpx": settings.get_bool(
                                "use_httpx",
                                os.getenv("OPENAI_USE_HTTPX", "False") == "True",
                            ),
                            "reasoning_effort": settings.get_str(
                                "reasoning_effort",
                                os.getenv("OPENAI_REASONING_EFFORT", "low"),
                            ),
                            "stream_responses": settings.get_bool(
                                "stream_responses",
                                os.getenv("OPENAI_STREAM_RESPONSES", "False") == "True",
                            ),
                            "proxy": settings.get_str("proxy"),
                        }
                    ),
                )

                self.valid_model_types = ["gpt", "o1", "o3", "o4"]
                self.excluded_model_types = [
                    "vision",
                    "image",
                    "realtime",
                    "audio",
                    "instruct",
                    "search",
                    "transcribe",
                    "tts",
                    "deep-research",
                    "codex",
                ]
                self.non_reasoning_models = ["gpt-3", "gpt-4", "gpt-5-chat"]

            @property
            def api_key(self) -> str | None:
                return self.settings.get_str("api_key")

            @property
            def api_base(self) -> str | None:
                return self.settings.get_str("api_base")

            @property
            def is_instruct_model(self) -> bool:
                return self.selected_model is not None and self.model_is_instruct_model(
                    self.selected_model
                )

            @property
            def is_reasoning_model(self) -> bool:
                return (
                    self.selected_model is not None
                    and self.model_is_reasoning_model(self.selected_model)
                )

            def model_is_instruct_model(self, model_name: str) -> bool:
                return model_name.find("instruct") >= 0

            def model_is_reasoning_model(self, model_name: str) -> bool:
                return not any(
                    model_name.startswith(model) for model in self.non_reasoning_models
                )

            def GetTranslationClient(self, settings: SettingsType) -> TranslationClient:
                client_settings = SettingsType(self.settings.copy())
                client_settings.update(settings)
                if self.is_instruct_model:
                    raise ProviderError(
                        _(
                            "Instruct models are no longer supported (model: {model})"
                        ).format(model=self.selected_model),
                        provider=self,
                    )
                elif self.is_reasoning_model:
                    return OpenAIReasoningClient(client_settings)
                else:
                    return ChatGPTClient(client_settings)

            def GetAvailableModels(self) -> list[str]:
                """
                Returns a list of possible values for the LLM model
                """
                try:
                    if not self.api_key:
                        logging.debug("No OpenAI API key provided")
                        return []

                    client = openai.OpenAI(
                        api_key=self.api_key, base_url=self.api_base or None
                    )
                    response = client.models.list()

                    if not response or not response.data:
                        return []

                    model_list = [
                        model.id
                        for model in response.data
                        if any(
                            model.id.startswith(prefix)
                            for prefix in self.valid_model_types
                        )
                    ]

                    model_list = [
                        model
                        for model in model_list
                        if not any(
                            [
                                model.find(exclude) >= 0
                                for exclude in self.excluded_model_types
                            ]
                        )
                    ]

                    # Maybe this isn't really an OpenAI endpoint, just return all the available models
                    if not model_list:
                        model_list = [model.id for model in response.data]

                    return sorted(model_list)

                except Exception as e:
                    logging.error(
                        _("Unable to retrieve available AI models: {error}").format(
                            error=str(e)
                        )
                    )
                    return []

            def ValidateSettings(self) -> bool:
                """
                Validate the settings for the provider
                """
                if not self.api_key:
                    self.validation_message = _("API Key is required")
                    return False

                return True

    except ImportError:
        from PySubtrans.Helpers.Localization import _

        logging.info(
            _("Failed to initialise OpenAI SDK. OpenAI provider will not be available")
        )
