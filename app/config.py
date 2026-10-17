"""Settings read from the environment, with defaults that work on a laptop."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "fitcheck.db"
GENAI_ENDPOINT_TEMPLATE = "https://inference.generativeai.{region}.oci.oraclecloud.com"

CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 240


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or unusable."""


@dataclass(frozen=True)
class Settings:
    oci_profile: str
    oci_config_path: str
    region: str
    compartment_id: str | None
    reasoning_model: str
    vision_model: str
    database_url: str

    @property
    def genai_endpoint(self) -> str:
        return GENAI_ENDPOINT_TEMPLATE.format(region=self.region)

    @property
    def timeout(self) -> tuple[int, int]:
        return (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)


def _database_url() -> str:
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        return configured
    return f"sqlite:///{DEFAULT_SQLITE_PATH}"


def load_settings() -> Settings:
    """Build settings from the environment. Never raises for missing OCI values."""
    return Settings(
        oci_profile=os.getenv("OCI_CONFIG_PROFILE", "DEFAULT"),
        oci_config_path=os.getenv("OCI_CONFIG_PATH", "~/.oci/config"),
        region=os.getenv("OCI_REGION", "us-chicago-1"),
        compartment_id=os.getenv("OCI_COMPARTMENT_ID", "").strip() or None,
        reasoning_model=os.getenv("REASONING_MODEL", "meta.llama-3.3-70b-instruct"),
        vision_model=os.getenv("VISION_MODEL", "meta.llama-3.2-90b-vision-instruct"),
        database_url=_database_url(),
    )


SETTINGS = load_settings()
