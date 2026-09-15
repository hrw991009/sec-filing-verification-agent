"""Server-owned Skill definitions and executable, least-privilege profile binding."""

from dataclasses import dataclass

from industry_platform.modules.research.domain import RESEARCH_GRAPH_VERSION, ResearchNode


@dataclass(frozen=True, slots=True)
class SkillRole:
    name: str
    nodes: tuple[ResearchNode, ...]
    responsibility: str


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    name: str
    version: str
    description: str
    graph_version: str
    roles: tuple[SkillRole, ...]

    @property
    def harness_version(self) -> str:
        # Persisted on AgentRun and included in the existing idempotency fingerprint.
        return f"skill:{self.name}:{self.version}"


FILING_VERIFICATION = SkillDefinition(
    name="sec.filing-verification",
    version="v1",
    description="核验指定 SEC 申报中的事实与派生数字, 返回可追溯证据、核验结果和报告。",
    graph_version=RESEARCH_GRAPH_VERSION,
    roles=(
        SkillRole(
            "scope_guard",
            (ResearchNode.CLARIFY_SCOPE, ResearchNode.WRITE_RESEARCH_BRIEF, ResearchNode.PLAN),
            "确认公司、申报、期间和截止时点; 歧义通过现有审批流程处理。",
        ),
        SkillRole(
            "investigator",
            (ResearchNode.RESEARCH_LOOP,),
            "使用受控检索、原文、XBRL、比较和计算工具收集证据。",
        ),
        SkillRole(
            "evidence_curator",
            (ResearchNode.NORMALIZE_EVIDENCE, ResearchNode.SYNTHESIZE_CLAIMS),
            "重新授权并规范化来源, 将主张关联到正式 Evidence。",
        ),
        SkillRole(
            "writer",
            (ResearchNode.OUTLINE, ResearchNode.DRAFT, ResearchNode.FINALIZE),
            "生成带引用报告, 并保留证据不足、冲突和未决问题。",
        ),
        SkillRole(
            "verifier",
            (ResearchNode.VERIFY, ResearchNode.REVISE),
            "核对 Evidence、期间、单位、计算和引用; 最多一次定向修复。",
        ),
    ),
)


class SkillRegistry:
    """Exact-version lookup; unknown versions never fall back to ordinary Research."""

    def definitions(self) -> tuple[SkillDefinition, ...]:
        return (FILING_VERIFICATION,)

    def resolve(self, name: str, version: str) -> SkillDefinition:
        for definition in self.definitions():
            if (definition.name, definition.version) == (name, version):
                return definition
        raise ValueError("Skill name or version is unsupported")

    def from_harness(self, harness_version: str) -> SkillDefinition | None:
        if not harness_version.startswith("skill:"):
            return None
        for definition in self.definitions():
            if definition.harness_version == harness_version:
                return definition
        raise ValueError("Persisted Skill version is unsupported")


SKILL_REGISTRY = SkillRegistry()
