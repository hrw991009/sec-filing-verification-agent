"""Generate the Agent security contract from the frozen SEC temporal manifest."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from industry_platform.modules.evaluation.agent_security import (
    build_agent_security_dataset,
    build_agent_security_observations,
    write_agent_security_json,
)
from industry_platform.modules.evaluation.sec_temporal import load_sec_temporal_manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate agent-security-v1")
    parser.add_argument("--temporal", required=True, type=Path)
    parser.add_argument("--dataset-output", required=True, type=Path)
    parser.add_argument("--observations-output", required=True, type=Path)
    args = parser.parse_args(argv)
    temporal_path = cast(Path, args.temporal)
    dataset = build_agent_security_dataset(
        load_sec_temporal_manifest(temporal_path),
        temporal_path=temporal_path,
    )
    observations = build_agent_security_observations(dataset)
    write_agent_security_json(cast(Path, args.dataset_output), dataset)
    write_agent_security_json(cast(Path, args.observations_output), observations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
