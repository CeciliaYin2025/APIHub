"""The MCP service must be told the prefix it is mounted under.

nginx strips `/apihub-test/mcp` before proxying, so the app sees a bare `/mcp`.
Starlette's Mount then answers a request for `/mcp` (no trailing slash) with a
307 to `/mcp/` — built from the app's own view of the path. Without
`--root-path` that Location drops the gateway prefix, and without
`--proxy-headers` it also downgrades https to http, so pasting the endpoint
into a browser lands on `http://<host>/mcp/` and 404s.

These are deployment facts, not app logic, so they are asserted against the
compose files that carry them.
"""

import io
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent

# (compose file, service name, expected --root-path value)
STACKS = [
    ("deploy/apihub/docker-compose.mcp.yml", "apihub-mcp", "/apihub/mcp"),
    ("deploy/apihub-test/docker-compose.mcp.yml", "apihub-test-mcp", "/apihub-test/mcp"),
]


def _command(compose_path: str, service: str) -> list[str]:
    data = yaml.safe_load(io.open(_ROOT / compose_path, encoding="utf-8"))
    return data["services"][service]["command"]


@pytest.mark.parametrize("compose_path,service,prefix", STACKS)
def test_mcp_command_sets_root_path_to_the_gateway_prefix(compose_path, service, prefix):
    cmd = _command(compose_path, service)
    assert "--root-path" in cmd, f"{service} must pass --root-path or redirects lose the gateway prefix"
    assert cmd[cmd.index("--root-path") + 1] == prefix


@pytest.mark.parametrize("compose_path,service,prefix", STACKS)
def test_mcp_command_trusts_forwarded_proto(compose_path, service, prefix):
    # The gateway sets X-Forwarded-Proto; uvicorn ignores it unless told to.
    cmd = _command(compose_path, service)
    assert "--proxy-headers" in cmd, f"{service} must pass --proxy-headers or redirects downgrade https to http"
    assert "--forwarded-allow-ips" in cmd


@pytest.mark.parametrize("compose_path,service,prefix", STACKS)
def test_mcp_root_path_matches_the_public_url_path(compose_path, service, prefix):
    # MCP_PUBLIC_URL in the .env.mcp example ends with the same prefix; keeping
    # them consistent is what makes the OAuth resource metadata and the redirect
    # agree about where this server lives.
    example = _ROOT / Path(compose_path).parent / ".env.mcp.example"
    text = io.open(example, encoding="utf-8").read()
    public_line = next(ln for ln in text.splitlines() if ln.startswith("MCP_PUBLIC_URL="))
    assert public_line.rstrip().endswith(prefix), f"{example.name} MCP_PUBLIC_URL must end with {prefix}"
