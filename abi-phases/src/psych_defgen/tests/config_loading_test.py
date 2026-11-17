"""Boot with the NCBI secrets absent from .env, as on production.

The engine renders config.yaml with Jinja, and ``{{ secret.NAME }}`` raises
when NAME is absent from the secret store and stdin is not a terminal: one
optional key would stop the API and Dagster's code location. These tests
load the real configuration files through the engine's own loader, template
rendering included, with ``NCBI_EMAIL`` and ``NCBI_API_KEY`` absent (not
empty), then build the module on the dotenv secret service those files
configure.
"""

import io
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from naas_abi_core.engine.engine_configuration.EngineConfiguration import (
    EngineConfiguration,
    GlobalConfig,
)
from naas_abi_core.services.secret.adaptors.secondary.dotenv_secret_secondaryadaptor import (
    DotenvSecretSecondaryAdaptor,
)
from naas_abi_core.services.secret.Secret import Secret

from psych_defgen import ABIModule, PsychDefgenConfiguration
from psych_defgen.factory import SECRET_SETTINGS, definition_service

ROOT = Path(__file__).resolve().parents[3]  # abi-phases/
CONFIG_FILES = ("config.yaml", "config.local.yaml")
NCBI = ("NCBI_EMAIL", "NCBI_API_KEY")
API = "/psych_defgen/api"
TOKEN = "psych-defgen-test-token"


def secret_names(text: str) -> set[str]:
    return set(re.findall(r"secret\.([A-Za-z0-9_]+)", text))


def module_entry(text: str) -> str:
    """The psych_defgen module entry of a configuration file, as text."""
    match = re.search(r"\n  - module: psych_defgen\n(?:(?!\n  - ).)*", text, re.S)
    assert match, "psych_defgen module entry not found"
    return match.group(0)


def boot(config_file: str, env: dict[str, str], tmp_path, monkeypatch):
    """Load ``config_file`` as the engine does, with only ``env`` in .env and
    no secret in the environment, and build the module from it."""
    text = (ROOT / config_file).read_text()
    for name in secret_names(text) | {name for name, _ in SECRET_SETTINGS}:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "deployment"
    home.mkdir()
    (home / ".env").write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    monkeypatch.chdir(home)  # the dotenv adapter reads ./.env
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # no TTY to prompt on

    configuration = EngineConfiguration.from_yaml_content(text, base_dir=str(ROOT))

    (entry,) = [m for m in configuration.modules if m.module == "psych_defgen"]
    assert entry.enabled
    module_configuration = PsychDefgenConfiguration(
        global_config=GlobalConfig.model_construct(), **(entry.config or {})
    )
    secret = Secret([DotenvSecretSecondaryAdaptor(path=str(home / ".env"))])
    return module_configuration, secret


def other_secrets(config_file: str) -> dict[str, str]:
    """A value for every secret the file names except the NCBI ones. "1"
    parses as every type the configuration asks for (an SMTP port too)."""
    names = secret_names((ROOT / config_file).read_text()) - set(NCBI)
    return dict.fromkeys(sorted(names), "1")


def module(configuration, secret, dataset=None) -> ABIModule:
    services = SimpleNamespace(
        dataset_available=lambda: dataset is not None,
        dataset=dataset,
        secret=secret,
    )
    return ABIModule(SimpleNamespace(services=services), configuration)


@pytest.mark.parametrize("config_file", CONFIG_FILES)
def test_the_module_entry_templates_no_secret(config_file):
    # A {{ secret.NAME }} here would stop the engine whenever NAME is absent.
    assert secret_names(module_entry((ROOT / config_file).read_text())) == set()


@pytest.mark.parametrize("config_file", CONFIG_FILES)
def test_the_engine_boots_without_the_ncbi_secrets(
    config_file, tmp_path, monkeypatch, dataset, caplog_messages
):
    monkeypatch.setenv("ABI_API_KEY", TOKEN)
    env = other_secrets(config_file)
    assert not set(env) & set(NCBI)

    configuration, secret = boot(config_file, env, tmp_path, monkeypatch)
    loaded = module(configuration, secret, dataset)
    loaded.on_initialized()
    app = FastAPI()
    loaded.api(app)
    client = TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})

    settings = client.get(f"{API}/settings").json()
    assert settings["missing"] == ["NCBI_EMAIL"]
    refused = client.post(f"{API}/runs", json={"term": "loneliness"})
    assert refused.status_code == 409
    assert "NCBI_EMAIL" in refused.json()["detail"]
    assert any("NCBI_EMAIL" in message for message in caplog_messages)


@pytest.mark.parametrize("config_file", CONFIG_FILES)
def test_without_an_ncbi_key_ncbi_is_used_anonymously(
    config_file, tmp_path, monkeypatch
):
    env = {**other_secrets(config_file), "NCBI_EMAIL": "researcher@example.org"}

    configuration, secret = boot(config_file, env, tmp_path, monkeypatch)
    loaded = module(configuration, secret)

    assert loaded.configuration.ncbi_api_key is None
    service = definition_service(loaded.configuration)
    assert service.literature.interval == 0.37  # 3 requests/s, no key


def test_the_ncbi_key_is_read_by_name_when_present(tmp_path, monkeypatch):
    env = {
        **other_secrets("config.local.yaml"),
        "NCBI_EMAIL": "researcher@example.org",
        "NCBI_API_KEY": "ncbi-key-value",
    }

    configuration, secret = boot("config.local.yaml", env, tmp_path, monkeypatch)
    loaded = module(configuration, secret)

    keyed = loaded.configuration
    assert keyed.ncbi_api_key.get_secret_value() == "ncbi-key-value"
    assert keyed.ncbi_email.get_secret_value() == "researcher@example.org"
    assert "ncbi-key-value" not in repr(keyed)
    assert definition_service(keyed).literature.interval < 0.37


def test_the_module_may_read_the_secret_service_through_the_engine(
    tmp_path, monkeypatch
):
    from naas_abi_core.engine.EngineProxy import EngineProxy

    env = {**other_secrets("config.local.yaml"), "NCBI_EMAIL": "a@example.org"}
    configuration, secret = boot("config.local.yaml", env, tmp_path, monkeypatch)
    engine = SimpleNamespace(services=SimpleNamespace(secret=secret))
    proxy = EngineProxy(engine, "psych_defgen", ABIModule.dependencies)

    loaded = ABIModule(proxy, configuration)

    assert loaded.configuration.ncbi_email.get_secret_value() == "a@example.org"
