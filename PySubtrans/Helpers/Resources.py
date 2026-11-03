import os

import appdirs  # type: ignore

old_config_dir: str = appdirs.user_config_dir(
    "GPTSubtrans", "MachineWrapped", roaming=True
)
config_dir: str = appdirs.user_config_dir("LLMSubtrans", "MachineWrapped", roaming=True)


def GetResourcePath(relative_path: str, *parts: str) -> str:
    """
    Locate a resource file or folder relative to the current working directory.
    """
    return os.path.join(os.path.abspath("."), relative_path or "", *parts)
