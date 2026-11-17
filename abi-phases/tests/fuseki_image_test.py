"""Fuseki must stay on the locally built 5.6 image, never stain/jena-fuseki.

stain/jena-fuseki ships Fuseki 5.1.0, which leaks one TDB2 read lock per query
(apache/jena#2755, fixed in 5.2.0). After 65535 requests every query fails with
HTTP 500 and the API cannot start. The upstream `abi deploy local` template
still uses that image, so regenerating docker-compose.yml from it would bring
the outage back. See .deploy/docker/README.md.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FUSEKI_BUILD_DIR = ROOT / ".deploy" / "docker" / "fuseki"
FIRST_FIXED_VERSION = (5, 2, 0)


def _fuseki_service() -> dict:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    return compose["services"]["fuseki"]


def _build_context(service: dict) -> Path | None:
    build = service.get("build")
    context = build.get("context") if isinstance(build, dict) else build
    return (ROOT / context).resolve() if context else None


def _dockerfile_fuseki_version() -> tuple[int, ...]:
    dockerfile = (FUSEKI_BUILD_DIR / "Dockerfile").read_text()
    match = re.search(r"^ARG FUSEKI_VERSION=(\d+)\.(\d+)\.(\d+)\s*$", dockerfile, re.M)
    assert match, "the Fuseki Dockerfile must pin ARG FUSEKI_VERSION=<x.y.z>"
    return tuple(int(part) for part in match.groups())


def test_fuseki_does_not_use_the_stain_image() -> None:
    image = _fuseki_service().get("image", "")
    assert "stain/jena-fuseki" not in image, (
        f"fuseki uses {image}, which is Fuseki 5.1.0 and fails every request after "
        "65535 queries. Build .deploy/docker/fuseki instead."
    )


def test_fuseki_is_built_from_the_local_dockerfile() -> None:
    service = _fuseki_service()
    assert _build_context(service) == FUSEKI_BUILD_DIR, (
        "fuseki must build ./.deploy/docker/fuseki (Fuseki 5.6 on the stain base)"
    )
    # Next to `build:`, an `image:` name lets Compose pull that image instead of
    # building, which is how the 5.1.0 image would come back.
    assert "image" not in service, "fuseki must have no image: next to its build:"


def test_dockerfile_installs_a_fuseki_without_the_lock_leak() -> None:
    version = _dockerfile_fuseki_version()
    assert version >= FIRST_FIXED_VERSION, (
        f"Fuseki {'.'.join(map(str, version))} leaks a TDB2 read lock per query; "
        "use 5.2.0 or later"
    )


def test_dockerfile_checks_the_fuseki_download() -> None:
    dockerfile = (FUSEKI_BUILD_DIR / "Dockerfile").read_text()
    assert re.search(r"^ARG FUSEKI_SHA512=[0-9a-f]{128}\s*$", dockerfile, re.M)
    assert "sha512sum -c" in dockerfile
