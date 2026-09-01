"""Run a live SEC identity smoke separately from deterministic evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx2
from anyio import Path as AsyncPath

from industry_platform.core.config import Settings
from industry_platform.modules.disclosures.adapters.sec_edgar import (
    CachedSecResponse,
    OfficialSecJsonClient,
)
from industry_platform.modules.disclosures.domain import (
    SEC_MAX_SUBMISSIONS_RESPONSE_BYTES,
    normalize_cik,
    sec_submissions_current_url,
    sha256_hex,
)

LIVE_SEC_SMOKE_SCHEMA_VERSION = 1
LIVE_SEC_SMOKE_ADAPTER_VERSION = "official-sec-json-v1"
DEFAULT_CIK = "0000320193"


@dataclass(slots=True)
class _OneShotBudget:
    calls: int = 0

    async def acquire(self) -> None:
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("Live SEC identity smoke exceeded its one-request budget")


@dataclass(slots=True)
class _EphemeralCache:
    value: CachedSecResponse | None = None

    async def get(self) -> CachedSecResponse | None:
        return self.value

    async def put(self, value: CachedSecResponse) -> None:
        self.value = value


async def run_live_sec_identity_smoke(
    *,
    settings: Settings,
    output: Path,
    cik: str = DEFAULT_CIK,
) -> dict[str, object]:
    if not settings.sec_source_configured:
        raise RuntimeError("SEC identity is not configured")
    normalized_cik = normalize_cik(cik)
    async with httpx2.AsyncClient(trust_env=False) as http_client:
        client = OfficialSecJsonClient(
            http_client,
            _OneShotBudget(),
            user_agent=settings.sec_user_agent,
            timeout_seconds=settings.sec_request_timeout_seconds,
            maximum_attempts=1,
        )
        response = await client.fetch(
            sec_submissions_current_url(normalized_cik),
            _EphemeralCache(),
            cache_ttl_seconds=60,
            maximum_bytes=SEC_MAX_SUBMISSIONS_RESPONSE_BYTES,
        )
    record = build_live_sec_identity_record(
        response,
        cik=normalized_cik,
        requests_per_second=settings.sec_requests_per_second,
    )
    async_output = AsyncPath(output)
    await async_output.parent.mkdir(parents=True, exist_ok=True)
    await async_output.write_text(
        json.dumps(record, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return record


def build_live_sec_identity_record(
    response: CachedSecResponse,
    *,
    cik: str,
    requests_per_second: int,
) -> dict[str, object]:
    try:
        document = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Live SEC smoke response is not JSON") from None
    if not isinstance(document, dict):
        raise ValueError("Live SEC smoke response is not an object")
    raw_cik = document.get("cik")
    entity_name = document.get("name")
    if not isinstance(raw_cik, (str, int)) or isinstance(raw_cik, bool):
        raise ValueError("Live SEC smoke identity is missing")
    observed_cik = normalize_cik(raw_cik)
    if observed_cik != cik or not isinstance(entity_name, str) or not entity_name.strip():
        raise ValueError("Live SEC smoke identity does not match the request")
    source_available_at = response.source_available_at or response.retrieved_at
    return {
        "schema_version": LIVE_SEC_SMOKE_SCHEMA_VERSION,
        "execution_kind": "live_sec_identity_smoke",
        "live_sec_executed": True,
        "identity_configured": True,
        "source_url": sec_submissions_current_url(cik),
        "cik": cik,
        "entity_name": " ".join(entity_name.split()),
        "retrieved_at": response.retrieved_at.astimezone(UTC).isoformat(),
        "source_available_at": source_available_at.astimezone(UTC).isoformat(),
        "content_sha256": sha256_hex(response.body),
        "byte_size": len(response.body),
        "adapter_version": LIVE_SEC_SMOKE_ADAPTER_VERSION,
        "requests_per_second": requests_per_second,
        "request_budget_scope": "one_shot_process_local_smoke",
        "observed_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".data/evals/sec-live-identity-v1.json"),
    )
    parser.add_argument("--cik", default=DEFAULT_CIK)
    args = parser.parse_args()
    record = asyncio.run(
        run_live_sec_identity_smoke(
            settings=Settings(),
            output=args.output,
            cik=args.cik,
        )
    )
    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "cik": record["cik"],
                "entity_name": record["entity_name"],
                "retrieved_at": record["retrieved_at"],
            },
            ensure_ascii=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
