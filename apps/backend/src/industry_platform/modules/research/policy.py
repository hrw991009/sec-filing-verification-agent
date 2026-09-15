"""Validate explicit Brief requirements against the selected Research Tool surface."""

from collections.abc import Sequence


def validate_required_tools(
    names: Sequence[str], *, local: bool, task_selected: bool, task_name: str | None = None
) -> None:
    if task_name == "sec.dupont-analysis" and not set(names).issubset(
        {"sec.get_xbrl_facts", "finance.calculate"}
    ):
        raise ValueError("DuPont Research task supports only retrieval and calculation")
    if not names:
        return
    # Disclosure schemas also expose Research approval payloads. Resolve the
    # executable catalog after schema modules have loaded.
    from industry_platform.modules.disclosures.profile import (
        SEC_L4_TOOL_REFERENCES,
        SEC_L5_TOOL_REFERENCES,
    )

    references = SEC_L4_TOOL_REFERENCES if task_selected else SEC_L5_TOOL_REFERENCES
    if (
        not local
        or len(names) > 8
        or len(set(names)) != len(names)
        or not set(names).issubset(reference.name for reference in references)
    ):
        raise ValueError("Required Tools exceed the selected Research policy")
