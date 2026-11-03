from unittest.mock import patch

from google.genai import errors as genai_errors

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Providers.Provider_Gemini import GeminiProvider
from PySubtrans.SettingsType import SettingsType


class GeminiProviderOptionsTests(LoggedTestCase):
    """GetOptions reports google-genai API errors instead of raising them."""

    def _make_provider(self) -> GeminiProvider:
        return GeminiProvider(SettingsType({"api_key": "test-key", "use_vertex": False}))

    def test_api_error_while_listing_models_is_reported(self) -> None:
        provider = self._make_provider()
        error = genai_errors.APIError(503, {"error": {"message": "service unavailable"}})

        with patch.object(GeminiProvider, "GetAvailableModels", side_effect=error):
            options = provider.GetOptions(SettingsType())

        self.assertLoggedIn("model option", "model", options)
        model_choices, model_message = options["model"]
        self.assertLoggedEqual("model choices", ["Unable to access the Gemini API"], model_choices)
        self.assertLoggedEqual("model message", str(error), model_message)

    def test_models_listed_when_available(self) -> None:
        provider = self._make_provider()

        with patch.object(GeminiProvider, "GetAvailableModels", return_value=["Gemini Test"]):
            options = provider.GetOptions(SettingsType())

        model_choices, _ = options["model"]
        self.assertLoggedEqual("model choices", ["Gemini Test"], model_choices)
