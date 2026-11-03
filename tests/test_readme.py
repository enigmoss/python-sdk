"""Every Python block of the README runs against a fake transport and passes mypy --strict.

A block may be preceded by ``<!-- readme-test: name=X -->`` (other blocks reuse it with
``uses=X``) and ``scenario=fastapi|django|flask`` (the test then drives the endpoint with a
signed webhook, twice, and checks that the order is fulfilled once).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import enigmoss
from enigmoss import HttpRequest, HttpResponse, webhooks
from enigmoss._signing import sign_webhook
from tests.support import BASE_URL, PAYMENT_KEY, PAYOUT_KEY, PROJECT_ID, ok, response

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
NOW = 1767225600

BLOCK = re.compile(r"(?:<!-- readme-test: (?P<meta>[^>]*?) -->\n)?```python\n(?P<code>.*?)```", re.S)


@dataclass
class Block:
    index: int
    code: str
    name: str | None
    uses: str | None
    scenario: str | None

    @property
    def id(self) -> str:
        return self.name or self.scenario or f"block-{self.index}"


def blocks() -> list[Block]:
    result = []
    for index, match in enumerate(BLOCK.finditer(README), start=1):
        meta = dict(item.split("=", 1) for item in (match.group("meta") or "").split())
        result.append(
            Block(index, match.group("code"), meta.get("name"), meta.get("uses"), meta.get("scenario"))
        )
    return result


BLOCKS = blocks()
NAMED = {b.name: b for b in BLOCKS if b.name}
FRAMEWORK = {"fastapi": "fastapi", "django": "django", "flask": "flask"}


class Router:
    """Answers the README calls with fixtures."""

    ROUTES = (
        ("POST", r"/payments", "payment", False),
        ("GET", r"/payments", "payment", True),
        ("POST", r"/payments/[^/]+/cancel", "payment", False),
        ("POST", r"/payouts", "payout", False),
        ("GET", r"/payouts", "payout", True),
        ("GET", r"/project", "project", False),
    )

    def __init__(self) -> None:
        self.sent: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.sent.append(request)
        path = request.url[len(BASE_URL) :].split("?")[0].removeprefix("/api/v4")
        for method, pattern, fixture, is_list in self.ROUTES:
            if request.method == method and re.fullmatch(pattern, path):
                data = response(fixture)
                return ok([data] if is_list else data, {"cursor": {"has_more": False}})
        raise AssertionError(f"README calls an unrouted endpoint: {request.method} {path}")


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Router:
    router = Router()
    monkeypatch.setattr("enigmoss._client.StdlibTransport", lambda: router)
    monkeypatch.setattr(webhooks, "_now", lambda: float(NOW))
    monkeypatch.setenv("ENIGMOSS_BASE_URL", BASE_URL)
    monkeypatch.setenv("ENIGMOSS_PROJECT_ID", PROJECT_ID)
    monkeypatch.setenv("ENIGMOSS_PAYMENT_KEY", PAYMENT_KEY)
    monkeypatch.setenv("ENIGMOSS_PAYOUT_KEY", PAYOUT_KEY)
    monkeypatch.chdir(tmp_path)
    return router


def needs(block: Block) -> str | None:
    framework = FRAMEWORK.get(block.scenario or "")
    if framework and importlib.util.find_spec(framework) is None:
        return framework
    return None


def source(block: Block) -> str:
    return (NAMED[block.uses].code + "\n\n" if block.uses else "") + block.code


def signed_webhook() -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "event": "payment.paid",
            "event_id": "019b7710-0000-7000-8000-000000000001",
            "created_at": "2026-01-01T00:05:00Z",
            "data": response("paid_payment"),
        }
    ).encode()
    return body, {
        "Content-Type": "application/json",
        "X-Project-Uuid": PROJECT_ID,
        "X-Timestamp": str(NOW),
        "X-Signature": sign_webhook(PAYMENT_KEY, str(NOW), PROJECT_ID, body),
    }


def deliver(block: Block, namespace: dict[str, Any], body: bytes, headers: dict[str, str]) -> int:
    if block.scenario == "fastapi":
        from fastapi.testclient import TestClient

        return int(
            TestClient(namespace["app"]).post("/webhooks/enigmoss", content=body, headers=headers).status_code
        )
    if block.scenario == "flask":
        return int(
            namespace["app"].test_client().post("/webhooks/enigmoss", data=body, headers=headers).status_code
        )
    from django.test import RequestFactory

    request = RequestFactory().post(
        "/webhooks/enigmoss", data=body, content_type="application/json", headers=headers
    )
    return int(namespace["enigmoss_webhook"](request).status_code)


def setup_django() -> None:
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            SECRET_KEY="readme-test",
            ALLOWED_HOSTS=["*"],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            INSTALLED_APPS=[],
        )
        django.setup()


def test_the_readme_has_the_documented_blocks() -> None:
    scenarios = {b.scenario for b in BLOCKS}
    assert {"fastapi", "django", "flask"} <= scenarios
    assert 'f"test-{uuid4().hex[:8]}"' in README and 'f"order-{order_id}-{attempt}"' in README
    assert 'order-1"' not in README  # a fixed order_id would replay or conflict on the second run


@pytest.mark.parametrize("block", BLOCKS, ids=lambda b: b.id)
def test_block_runs(block: Block, sandbox: Router) -> None:
    missing = needs(block)
    if missing:
        pytest.skip(f"{missing} is not installed")
    if block.scenario == "django":
        setup_django()
    namespace: dict[str, Any] = {"__name__": f"readme_{block.index}"}
    exec(compile(source(block), f"README.md:block-{block.index}", "exec"), namespace)

    if block.scenario:
        fulfilled: list[str] = []
        namespace["fulfil_order"] = fulfilled.append
        body, headers = signed_webhook()
        assert deliver(block, namespace, body, headers) == 200
        assert deliver(block, namespace, body, headers) == 200  # a repeated delivery
        assert fulfilled == ["order-42"]
        assert deliver(block, namespace, body.replace(b"10.50", b"99.99"), headers) == 400


def test_blocks_pass_mypy_strict(tmp_path: Path) -> None:
    if importlib.util.find_spec("mypy") is None:
        pytest.skip("mypy is not installed (the lint group runs this check)")
    files = []
    for block in BLOCKS:
        if needs(block):
            continue
        path = tmp_path / f"readme_block_{block.index}.py"
        path.write_text(source(block), encoding="utf-8")
        files.append(str(path))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--config-file",
            str(ROOT / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            *files,
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_llms_txt_names_only_real_methods() -> None:
    text = (ROOT / "llms.txt").read_text(encoding="utf-8")
    client = enigmoss.Enigmoss(base_url=BASE_URL, project_id=PROJECT_ID, payment_key=PAYMENT_KEY)
    for resource, methods in re.findall(r"^- (\w+) \[\w+\]: (.*?)(?=^- |\n\n)", text, re.S | re.M):
        target = getattr(client, resource)
        for method in re.findall(r"(?:^|; )(\w+)(?:\(|;|$)", methods.replace("\n  ", " ")):
            assert callable(getattr(target, method, None)), f"llms.txt names {resource}.{method}"
