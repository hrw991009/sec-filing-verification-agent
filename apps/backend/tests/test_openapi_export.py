from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from industry_platform import openapi as openapi_module


def test_openapi_export_and_cli_write_an_atomic_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "nested" / "openapi.json"

    openapi_module.export_openapi(output)

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["openapi"].startswith("3.")
    assert "/health/live" in document["paths"]
    assert not output.with_suffix(".json.tmp").exists()

    cli_output = tmp_path / "cli-openapi.json"
    monkeypatch.setattr(sys, "argv", ["openapi", "--output", str(cli_output)])
    openapi_module.main()
    assert json.loads(cli_output.read_text(encoding="utf-8")) == document
