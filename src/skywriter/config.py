"""Static application metadata for the foundation shell."""

from dataclasses import dataclass

from skywriter import __version__


@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    """Immutable desktop metadata shared by the entry point and UI."""

    name: str = "SKYWriter"
    organization: str = "305 Skylab"
    version: str = __version__


DEFAULT_CONFIG = ApplicationConfig()
