"""Production settings must not put secrets in logs or stored error messages.

At DEBUG the engine logs its configuration after secret templating, so the
``abi`` service defaults to a quieter level. DuckDB repeats the DuckLake catalog
DSN in a failed ATTACH's error, which Dagster stores, so the Postgres password
reaches libpq through ``PGPASSWORD`` instead of sitting in the DSN.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _services() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


def _environment(service: str) -> dict[str, str]:
    entries = _services()[service]["environment"]
    if isinstance(entries, dict):
        return {key: str(value) for key, value in entries.items()}
    return dict(entry.split("=", 1) for entry in entries)


def _default(value: str) -> str:
    """The value compose uses when the variable is unset: ``${NAME:-x}`` gives ``x``."""
    match = re.fullmatch(r"\$\{\w+:?-(.*)\}", value)
    return match.group(1) if match else value


def test_abi_does_not_log_at_debug_by_default() -> None:
    level = _environment("abi")["LOG_LEVEL"]
    assert _default(level).upper() != "DEBUG"
    assert level.startswith("${LOG_LEVEL"), "LOG_LEVEL must stay overridable"


def test_ducklake_catalog_has_no_password() -> None:
    config = yaml.safe_load((ROOT / "config.local.yaml").read_text())
    catalog = config["services"]["dataset"]["dataset_adapter"]["config"]["catalog"]
    assert "POSTGRES_PASSWORD" not in catalog
    assert not re.search(r"://[^/@]+:[^/@]+@", catalog), "the DSN must not carry a password"


def test_services_loading_the_engine_get_pgpassword() -> None:
    for service in ("abi", "dagster"):
        assert _environment(service).get("PGPASSWORD") == "${POSTGRES_PASSWORD}", service
