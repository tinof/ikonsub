import logging
import threading
import time

from PySubtrans.Instructions import DEFAULT_TASK_TYPE
from PySubtrans.Options import Options
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleError import TranslationError
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.Translation import Translation
from PySubtrans.TranslationEvents import TranslationEvents
from PySubtrans.TranslationParser import TranslationParser
from PySubtrans.TranslationPrompt import TranslationPrompt, default_prompt_template
from PySubtrans.TranslationRequest import StreamingCallback, TranslationRequest

linesep = "\n"


class TranslationClient:
    """
    Handles communication with the translation provider
    """

    def __init__(self, settings: SettingsType):
        if isinstance(settings, Options):
            settings = settings.GetSettings()

        self.settings: SettingsType = SettingsType(settings)
        self.instructions: str | None = settings.get_str("instructions")
        self.retry_instructions: str | None = settings.get_str("retry_instructions")
        self.enable_streaming: bool = (
            settings.get_bool("stream_responses", False) and self.supports_streaming
        )
        self.aborted: bool = False
        self.events: TranslationEvents | None = None

        # Request pacing is shared by every thread that uses this client, so
        # parallel workers stay within rate_limit requests per minute together
        self._rate_limit_lock = threading.RLock()
        self._next_request_time: float | None = None
        self._abort_event = threading.Event()

        if not self.instructions:
            raise TranslationError("No instructions provided for the translator")

    @property
    def supports_conversation(self) -> bool:
        return self.settings.get_bool("supports_conversation", False)

    @property
    def supports_system_prompt(self) -> bool:
        return self.settings.get_bool("supports_system_prompt", False)

    @property
    def supports_system_messages(self) -> bool:
        return self.settings.get_bool("supports_system_messages", False)

    @property
    def supports_system_messages_for_retry(self) -> bool:
        return self.settings.get_bool(
            "supports_system_messages_for_retry", self.supports_system_messages
        )

    @property
    def system_role(self) -> str:
        return self.settings.get_str("system_role") or "system"

    @property
    def prompt_template(self) -> str:
        return self.settings.get_str("prompt_template") or default_prompt_template

    @property
    def rate_limit(self) -> float | None:
        return self.settings.get_float("rate_limit")

    @property
    def temperature(self) -> float:
        return self.settings.get_float("temperature") or 0.0

    @property
    def max_retries(self) -> int:
        return self.settings.get_int("max_retries") or 3

    @property
    def backoff_time(self) -> float:
        return self.settings.get_float("backoff_time") or 5.0

    @property
    def supports_streaming(self) -> bool:
        return self.settings.get_bool("supports_streaming", False)

    def BuildTranslationPrompt(
        self,
        user_prompt: str,
        instructions: str,
        lines: list[SubtitleLine],
        context: dict,
    ) -> TranslationPrompt:
        """
        Generate a translation prompt for the context
        """
        prompt = TranslationPrompt(user_prompt, self.supports_conversation)
        prompt.supports_system_prompt = self.supports_system_prompt
        prompt.supports_system_messages = (
            self.supports_conversation and self.supports_system_messages
        )
        prompt.supports_system_messages_for_retry = (
            self.supports_system_messages_for_retry
        )
        prompt.system_role = self.system_role
        prompt.prompt_template = self.prompt_template
        prompt.include_line_timings = self.settings.get_bool(
            "include_line_timings", False
        )
        prompt.max_single_line_length = (
            self.settings.get_int("max_single_line_length") or 42
        )
        prompt.target_cps = self.settings.get_float("target_cps") or 15.0
        prompt.GenerateMessages(instructions, lines, context)
        return prompt

    def RequestTranslation(
        self,
        prompt: TranslationPrompt,
        temperature: float | None = None,
        streaming_callback: StreamingCallback = None,
    ) -> Translation | None:
        """
        Generate the messages to request a translation
        """
        if self.aborted:
            return None

        self._wait_for_rate_limit()

        if self.aborted:
            return None

        # Create a translation request to encapsulate the operation
        request = TranslationRequest(prompt, streaming_callback)

        # Perform the translation
        translation = self._request_translation(request, temperature)

        if self.aborted or translation is None:
            return None

        if translation.text:
            logging.debug(f"Response:\n{translation.text}")

        return translation

    def GetParser(self, task_type: str = DEFAULT_TASK_TYPE) -> TranslationParser:
        """
        Return a parser that can process the provider's response
        """
        return TranslationParser(task_type, Options(self.settings))

    def AbortTranslation(self) -> None:
        self.aborted = True
        self._abort_event.set()
        self._abort()

    def SetEvents(self, events: TranslationEvents) -> None:
        """
        Attach translation events to use for  log messages.
        """
        self.events = events

    def _emit_error(self, message: str) -> None:
        if self.events:
            self.events.error.send(self, message=message)
        else:
            logging.error(message)

    def _emit_warning(self, message: str) -> None:
        if self.events:
            self.events.warning.send(self, message=message)
        else:
            logging.warning(message)

    def _emit_info(self, message: str) -> None:
        if self.events:
            self.events.info.send(self, message=message)
        else:
            logging.info(message)

    def _request_translation(
        self, request: TranslationRequest, temperature: float | None = None
    ) -> Translation | None:
        """
        Make a request to the API to provide a translation
        """
        _ = request, temperature  # Mark as accessed to avoid lint warnings
        raise NotImplementedError

    def _abort(self) -> None:
        # Try to terminate ongoing requests
        self.aborted = True

    def _wait_for_rate_limit(self) -> None:
        """
        Space request starts at least 60 / rate_limit seconds apart across all threads.

        Each caller reserves the next free start time under the lock, then sleeps
        outside it, so an abort wakes every waiting thread at once.
        """
        rate_limit = self.rate_limit
        if not rate_limit or rate_limit <= 0.0:
            return

        interval = 60.0 / rate_limit
        with self._rate_limit_lock:
            now = time.monotonic()
            start_time = max(now, self._next_request_time or now)
            self._next_request_time = start_time + interval

        sleep_time = start_time - now
        if sleep_time > 0.0:
            logging.debug(
                f"Sleeping for {sleep_time:.2f} seconds to respect rate limit"
            )
            self._abort_event.wait(sleep_time)
