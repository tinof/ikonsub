from __future__ import annotations

import openai

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Providers.Clients.ChatGPTClient import ChatGPTClient
from PySubtrans.Providers.Provider_OpenAI import OpenAiProvider
from PySubtrans.SettingsType import SettingsType


class OpenAIClientProxyTests(LoggedTestCase):
    """Validate that --proxy reaches the OpenAI client and builds a working http client."""

    def _make_client(self, **extra) -> ChatGPTClient:
        settings = SettingsType(
            {
                "api_key": "sk-test",
                "instructions": "Verify proxy settings.",
                "model": "gpt-5-mini",
                **extra,
            }
        )
        return ChatGPTClient(settings)

    def test_provider_keeps_proxy_setting(self) -> None:
        """The provider registers the proxy key, so GetSettings does not drop it."""
        provider = OpenAiProvider(
            SettingsType({"api_key": "sk-test", "proxy": "socks5://127.0.0.1:1089"})
        )
        self.assertLoggedEqual(
            "provider proxy", "socks5://127.0.0.1:1089", provider.settings.get_str("proxy")
        )

    def test_socks_proxy_client_is_created(self) -> None:
        """A SOCKS proxy builds the client without a network call (httpx 0.28 has no proxies=)."""
        client = self._make_client(proxy="socks5://127.0.0.1:1")
        client._create_client()
        self.assertLoggedIsNotNone("client", client.client)
        assert client.client is not None
        self.assertLoggedTrue(
            "http client type",
            isinstance(client.client._client, openai.DefaultHttpxClient),
        )
        client.client.close()

    def test_http_proxy_client_is_created(self) -> None:
        """An http:// proxy URL is accepted as well."""
        client = self._make_client(proxy="http://127.0.0.1:8080")
        client._create_client()
        self.assertLoggedIsNotNone("client", client.client)
        assert client.client is not None
        client.client.close()

    def test_use_httpx_sets_base_url(self) -> None:
        """The use_httpx path still builds a client with the custom API base."""
        client = self._make_client(use_httpx=True, api_base="http://localhost:1234/v1")
        client._create_client()
        assert client.client is not None
        self.assertLoggedEqual("base url", "http://localhost:1234/v1/", str(client.client.base_url))
        client.client.close()
