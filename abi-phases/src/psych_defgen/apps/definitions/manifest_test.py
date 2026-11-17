"""The Construct Definitions app as Nexus lists, serves and packages it."""

import json
import pathlib
import re
import tomllib

import yaml

APP = pathlib.Path(__file__).parent
MANIFEST = APP / "manifest.json"
INDEX = APP / "index.html"
SRC = APP.parents[2]
PROJECT = SRC.parent
PYPROJECT = PROJECT / "pyproject.toml"
CATALOG_ID = "psych_defgen:definitions"

# What Nexus serves next to an app's index.html under /app-html/ (naas-abi
# apps adapter, _APP_ASSET_SUFFIXES). Anything else is not reachable.
SERVED = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".mjs",
    ".png",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
}
IMPORT = re.compile(
    r"""(?:import|export)\s[^'"]*?from\s+['"]([^'"]+)['"]|import\(\s*['"]([^'"]+)['"]"""
)


def _modules():
    """Every ES module reachable from the page, following relative imports."""
    entry = re.search(r'<script type="module" src="([^"]+)"', INDEX.read_text()).group(
        1
    )
    assert entry == "./js/main.js"
    pending, seen = [(APP / entry).resolve()], set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        for match in IMPORT.finditer(path.read_text()):
            target = match.group(1) or match.group(2)
            assert target.startswith("./") or target.startswith("../"), (
                path.name,
                target,
            )
            pending.append((path.parent / target).resolve())
    return seen


def test_the_manifest_has_what_the_listing_needs():
    manifest = json.loads(MANIFEST.read_text())

    assert manifest["name"] == "Construct Definitions"
    assert manifest["description"].endswith(".")
    assert manifest["url"] == "html:index.html"
    assert manifest["icon_emoji"] == "📖"
    assert manifest["version"] == "1.0.0"
    assert manifest["author"] == manifest["maintainer"] == "HealthyPhases"
    assert manifest["license"] == "MIT"
    assert manifest["tier"] == "community"
    assert manifest["pricing"] == {"type": "free", "price": 0}
    assert manifest["category"] == "domain"
    assert {"definitions", "psychology", "pubmed"} <= set(manifest["keywords"])
    # No right-pane agent: the app stands on its own.
    assert "agent" not in manifest


def test_the_manifest_points_at_a_page_that_exists():
    manifest = json.loads(MANIFEST.read_text())

    assert (APP / manifest["url"].removeprefix("html:")).is_file()


def test_the_page_declares_a_charset_a_title_and_a_viewport():
    text = INDEX.read_text()

    assert 'charset="utf-8"' in text
    assert "<title>Construct Definitions</title>" in text
    assert (
        '<meta name="viewport" content="width=device-width, initial-scale=1">' in text
    )


def test_every_module_the_page_loads_exists_is_relative_and_is_served_by_nexus():
    modules = _modules()

    assert {path.name for path in modules} >= {
        "main.js",
        "app.js",
        "api.js",
        "router.js",
        "ui.js",
    }
    for path in modules:
        assert path.is_file(), path
        assert path.suffix in SERVED, path
        assert APP in path.parents, path


def test_the_stylesheet_is_local_and_has_light_and_dark_tokens():
    hrefs = re.findall(r'<link rel="stylesheet" href="([^"]+)"', INDEX.read_text())

    assert hrefs == ["./css/app.css"]
    css = (APP / hrefs[0]).read_text()
    assert "@media (prefers-color-scheme: dark)" in css
    assert ":root[data-theme='dark']" in css
    assert ":root:not([data-theme='light'])" in css


def test_nothing_loads_from_another_origin():
    # No CDN: the app must work wherever Nexus serves it, offline included.
    for path in [INDEX, *APP.glob("css/*.css"), *APP.glob("js/**/*.js")]:
        text = path.read_text()
        # Links out (to PubMed, PMC, the APA Dictionary) are fine; loaded resources are not.
        assert not re.search(
            r"""<(?:script|link|img|iframe|source)\b[^>]*\b(?:src|href)=["'](?:https?:)?//""",
            text,
        ), path
        assert not re.search(r"""(?:from|import)\s*\(?\s*['"](?:https?:)?//""", text), (
            path
        )
        assert not re.search(r"""url\(\s*['"]?(?:https?:)?//""", text), path
        assert "@import" not in text, path


def test_the_api_client_is_the_only_fetch_caller_and_resolves_nexus_like_the_workspace():
    for path in APP.glob("js/**/*.js"):
        if path.name != "api.js":
            assert "fetch(" not in path.read_text(), path
    text = (APP / "js" / "api.js").read_text()

    assert "__NEXUS_RUNTIME_CONFIG__" in text
    assert "Expected JSON from" in text
    assert "credentials: 'include'" in text
    assert "nexus-auth" in text
    assert "'/psych_defgen/api'" in text


def test_the_package_ships_every_file_the_app_serves():
    globs = tomllib.loads(PYPROJECT.read_text())["tool"]["setuptools"]["package-data"][
        "psych_defgen"
    ]
    package = SRC / "psych_defgen"
    shipped = {path for pattern in globs for path in package.glob(pattern)}
    served = [p for p in APP.rglob("*") if p.is_file() and p.suffix in SERVED]

    assert INDEX in served and MANIFEST in served
    for path in served:
        assert path in shipped, path.relative_to(package).as_posix()


def test_the_main_workspace_turns_the_app_on():
    for config in ("config.yaml", "config.local.yaml"):
        loaded = yaml.safe_load((PROJECT / config).read_text())
        nexus = next(m for m in loaded["modules"] if m["module"] == "naas_abi")[
            "config"
        ]["nexus_config"]
        [organization] = nexus["organizations"]
        [workspace] = organization["workspaces"]

        assert CATALOG_ID in workspace["apps"], config
        assert "phases_v2:workspace" in workspace["apps"], config
