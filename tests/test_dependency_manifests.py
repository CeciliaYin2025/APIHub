"""requirements.txt must cover every runtime dependency pyproject.toml declares.

The Dockerfile installs requirements.txt, not pyproject.toml, so a package
declared only in pyproject is silently missing from the image. That is how
`mcp` shipped absent and the MCP server crash-looped with ModuleNotFoundError
the first time it was ever deployed — nothing imports it in the API process,
so no existing test noticed.
"""

import re
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _requirement_names(lines) -> set[str]:
    names = set()
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # Strip extras and any version specifier: "celery[redis]>=5.3.0" -> "celery"
        name = re.split(r"[<>=!~\[;]", line, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def _pyproject_runtime_names() -> set[str]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return _requirement_names(data["project"]["dependencies"])


def _requirements_names() -> set[str]:
    return _requirement_names((_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines())


def test_requirements_covers_every_pyproject_runtime_dependency():
    missing = _pyproject_runtime_names() - _requirements_names()
    assert not missing, (
        f"declared in pyproject.toml but absent from requirements.txt: {sorted(missing)}. "
        "The Docker image installs requirements.txt, so these would be missing at runtime."
    )


def test_mcp_is_installable_in_the_image():
    # Explicit rather than relying on the set comparison above: the MCP server is
    # a separate process from the API, so nothing else fails fast when it is gone.
    assert "mcp" in _requirements_names()
