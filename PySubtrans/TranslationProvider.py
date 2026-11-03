from typing import cast
from PySubtrans.Options import Options
from PySubtrans.SettingsType import SettingsType
from PySubtrans.TranslationClient import TranslationClient


class TranslationProvider:
    """
    Base class for translation service providers.
    """

    def __init__(self, name: str, settings: SettingsType):
        self.name: str = name
        self.settings: SettingsType = settings
        self._available_models: list[str] = []
        self.validation_message: str | None = None

    @property
    def available_models(self) -> list[str]:
        """
        list of available models for the provider
        """
        if not self._available_models:
            self._available_models = self.GetAvailableModels()

        return self._available_models

    @property
    def selected_model(self) -> str | None:
        """
        The currently selected model for the provider
        """
        name: str | None = self.settings.get_str("model")
        return name.strip() if name else None

    def GetAvailableModels(self) -> list[str]:
        """
        Returns a list of possible model for the provider
        """
        raise NotImplementedError

    def GetTranslationClient(self, settings: SettingsType) -> TranslationClient:
        """
        Returns a new instance of the translation client for this provider
        """
        raise NotImplementedError

    def ValidateSettings(self) -> bool:
        """
        Validate the settings for the provider
        """
        return True

    def UpdateSettings(self, settings: SettingsType):
        """
        Update the settings for the provider
        """
        if isinstance(settings, Options):
            options = cast(Options, settings)
            options.InitialiseProviderSettings(self.name, self.settings)
            settings = options.provider_settings[self.name]

        # Update the settings
        for k, v in settings.items():
            if k in self.settings:
                self.settings[k] = v

    @classmethod
    def get_providers(cls) -> dict:
        """
        Return a dictionary of all available providers
        """
        if not cls.__subclasses__():
            # Import the providers package, which will trigger explicit imports
            import PySubtrans.Providers  # noqa: F401

        providers = {
            cast(TranslationProvider, provider).name: provider
            for provider in cls.__subclasses__()
        }

        return providers

    @classmethod
    def get_provider(cls, options: Options):
        """
        Create a new instance of the provider with the given name
        """
        if not isinstance(options, Options):
            raise ValueError("Options object required")

        if not options.provider:
            raise ValueError("No provider set")

        provider_settings = options.current_provider_settings or SettingsType()

        translation_provider: TranslationProvider = cls.create_provider(
            options.provider, provider_settings
        )
        if not translation_provider:
            raise ValueError(
                f"Unable to create translation provider '{options.provider}'"
            )

        translation_provider.UpdateSettings(options)

        return translation_provider

    @classmethod
    def create_provider(cls, name, provider_settings):
        providers = cls.get_providers().items()
        for provider_name, provider in providers:
            if provider_name == name:
                return provider(provider_settings)

        raise ValueError(f"Unknown translation provider: {name}")
