"""A failed API startup must end the abi container, so Docker restarts it.

With uvicorn's reloader on, a startup error (Fuseki down or answering 500)
kills only the server worker. The reloader parent keeps running, the container
stays "Up (unhealthy)" and `restart: unless-stopped` never fires. The compose
stack runs the API with ENV=local, so config.local.yaml must turn the reloader
off. See .deploy/docker/README.md, "When the API fails to start".
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def test_compose_runs_the_api_with_config_local() -> None:
    abi = _compose()["services"]["abi"]
    assert "ENV=local" in abi["environment"]


def test_compose_restarts_the_api_when_it_exits() -> None:
    assert _compose()["services"]["abi"]["restart"] == "unless-stopped"


def test_config_local_turns_the_reloader_off() -> None:
    config = yaml.safe_load((ROOT / "config.local.yaml").read_text())
    # Upstream's ApiConfiguration.reload defaults to True, so leaving the key
    # out turns the reloader on.
    assert config["api"].get("reload") is False
