"""Validate and inspect Scenario datasets without creating a test-only Runtime."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Never, TextIO

from industry_platform.modules.agent_harness.scenarios import load_scenario_dataset

_INVALID_DATASET_EXIT_CODE = 2
_INVALID_ARGUMENTS_EXIT_CODE = 2


class HarnessArgumentError(ValueError):
    """Raised instead of letting argparse write process-global error output."""


class _HarnessArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise HarnessArgumentError("Harness command arguments are invalid")


def _parser() -> argparse.ArgumentParser:
    parser = _HarnessArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("validate", "Validate a dataset and print non-sensitive metadata."),
        ("list", "List case identities without printing prompts or expected outputs."),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("--dataset", required=True, type=Path)
    return parser


def _write_json(stream: TextIO, document: object) -> None:
    stream.write(json.dumps(document, ensure_ascii=False, sort_keys=True))
    stream.write("\n")


def run_cli(
    arguments: Sequence[str],
    *,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Execute one metadata-only command and return a stable process status."""

    try:
        parsed = _parser().parse_args(list(arguments))
    except HarnessArgumentError as error:
        _write_json(
            stderr,
            {
                "error": {
                    "code": "INVALID_HARNESS_ARGUMENTS",
                    "message": str(error),
                }
            },
        )
        return _INVALID_ARGUMENTS_EXIT_CODE
    try:
        dataset = load_scenario_dataset(parsed.dataset)
    except (OSError, ValueError) as error:
        _write_json(
            stderr,
            {
                "error": {
                    "code": "INVALID_SCENARIO_DATASET",
                    "message": str(error),
                }
            },
        )
        return _INVALID_DATASET_EXIT_CODE

    if parsed.command == "validate":
        _write_json(
            stdout,
            {
                "case_count": len(dataset.cases),
                "dataset_id": dataset.dataset_id,
                "dataset_version": dataset.dataset_version,
                "schema_version": dataset.schema_version,
                "status": "valid",
            },
        )
        return 0

    _write_json(
        stdout,
        {
            "cases": [
                {
                    "case_id": case.case_id,
                    "case_version": case.case_version,
                    "scenario_id": case.scenario.scenario_id,
                    "scenario_version": case.scenario.scenario_version,
                }
                for case in dataset.cases
            ],
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
        },
    )
    return 0


def main() -> None:
    """Run the installed Harness dataset CLI."""

    raise SystemExit(run_cli(sys.argv[1:], stdout=sys.stdout, stderr=sys.stderr))


if __name__ == "__main__":
    main()
