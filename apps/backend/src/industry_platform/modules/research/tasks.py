"""Built-in financial research task definitions, separate from instruction skills."""

from dataclasses import dataclass

from industry_platform.modules.research.domain import RESEARCH_GRAPH_VERSION, ResearchNode


@dataclass(frozen=True, slots=True)
class ResearchRole:
    name: str
    nodes: tuple[ResearchNode, ...]
    responsibility: str


@dataclass(frozen=True, slots=True)
class ResearchTaskDefinition:
    name: str
    version: str
    description: str
    graph_version: str
    roles: tuple[ResearchRole, ...]

    @property
    def harness_version(self) -> str:
        # Persisted on AgentRun and included in the existing idempotency fingerprint.
        return f"research-task:{self.name}:{self.version}"


FILING_VERIFICATION = ResearchTaskDefinition(
    name="sec.filing-verification",
    version="v1",
    description="核验指定 SEC 申报中的事实与派生数字, 返回可追溯证据、核验结果和报告。",
    graph_version=RESEARCH_GRAPH_VERSION,
    roles=(
        ResearchRole(
            "scope_guard",
            (ResearchNode.CLARIFY_SCOPE, ResearchNode.WRITE_RESEARCH_BRIEF, ResearchNode.PLAN),
            "确认公司、申报、期间和截止时点; 歧义通过现有审批流程处理。",
        ),
        ResearchRole(
            "investigator",
            (ResearchNode.RESEARCH_LOOP,),
            "使用受控检索、原文、XBRL、比较和计算工具收集证据。",
        ),
        ResearchRole(
            "evidence_curator",
            (ResearchNode.NORMALIZE_EVIDENCE, ResearchNode.SYNTHESIZE_CLAIMS),
            "重新授权并规范化来源, 将主张关联到正式 Evidence。",
        ),
        ResearchRole(
            "writer",
            (ResearchNode.OUTLINE, ResearchNode.DRAFT, ResearchNode.FINALIZE),
            "生成带引用报告, 并保留证据不足、冲突和未决问题。",
        ),
        ResearchRole(
            "verifier",
            (ResearchNode.VERIFY, ResearchNode.REVISE),
            "核对 Evidence、期间、单位、计算和引用; 最多一次定向修复。",
        ),
    ),
)


DUPONT_ANALYSIS = ResearchTaskDefinition(
    name="sec.dupont-analysis",
    version="v1",
    description="补齐并核对连续两年年报及三期资产、归母权益余额, 输出可追溯的三因素杜邦分析。",
    graph_version=RESEARCH_GRAPH_VERSION,
    roles=FILING_VERIFICATION.roles,
)


class ResearchTaskRegistry:
    """Exact-version lookup; unknown versions never fall back to ordinary Research."""

    def definitions(self) -> tuple[ResearchTaskDefinition, ...]:
        return (FILING_VERIFICATION, DUPONT_ANALYSIS)

    def resolve(self, name: str, version: str) -> ResearchTaskDefinition:
        for definition in self.definitions():
            if (definition.name, definition.version) == (name, version):
                return definition
        raise ValueError("Research task name or version is unsupported")

    def from_harness(self, harness_version: str) -> ResearchTaskDefinition | None:
        if not harness_version.startswith(("research-task:", "skill:")):
            return None
        for definition in self.definitions():
            # Decode old persisted runs here only; never rewrite their identity/checkpoints.
            if harness_version in (
                definition.harness_version,
                f"skill:{definition.name}:{definition.version}",
            ):
                return definition
        raise ValueError("Persisted research task version is unsupported")


RESEARCH_TASKS = ResearchTaskRegistry()
