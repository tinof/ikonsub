import unittest

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Providers.Clients.ChatGPTClient import ChatGPTClient
from PySubtrans.Providers.Clients.OpenAIReasoningClient import OpenAIReasoningClient
from PySubtrans.Providers.Provider_OpenAI import OpenAiProvider
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleError import ProviderError


class TestOpenAiProviderClientSelection(LoggedTestCase):
    """GetTranslationClient picks a client by model name, and rejects -instruct models clearly"""

    def _provider(self, model: str) -> OpenAiProvider:
        return OpenAiProvider(SettingsType({"api_key": "sk-test", "model": model}))

    def test_instruct_model_raises_clear_error(self):
        provider = self._provider("gpt-3.5-turbo-instruct")

        with self.assertRaises(ProviderError) as context:
            provider.GetTranslationClient(SettingsType({"instructions": "Translate."}))

        message = str(context.exception)
        self.assertLoggedIn(
            "instruct error names the problem",
            "Instruct models are no longer supported",
            message,
        )
        self.assertLoggedIn("instruct error names the model", "gpt-3.5-turbo-instruct", message)

    def test_reasoning_model_uses_reasoning_client(self):
        client = self._provider("gpt-5-mini").GetTranslationClient(SettingsType({"instructions": "Translate."}))
        self.assertLoggedIsInstance("reasoning client", client, OpenAIReasoningClient)

    def test_chat_model_uses_chat_client(self):
        client = self._provider("gpt-4o-mini").GetTranslationClient(SettingsType({"instructions": "Translate."}))
        self.assertLoggedIsInstance("chat client", client, ChatGPTClient)


if __name__ == "__main__":
    unittest.main()
