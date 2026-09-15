# SEC 事实核验 Skill

`sec.filing-verification@v1` 是应用内置的可执行 Skill。Research 页面选择
「SEC Filing」，勾选「使用 SEC 事实核验 Skill」后，填写已导入知识库、申报范围与问题即可启动。

Skill 使用现有 Research L5 LangGraph：范围确认 → 计划 → 有界工具循环 →
Evidence 规范化 → Claims → 草稿 → Verifier → 最多一次修复 → 最终报告。
Scope guard、Investigator、Evidence curator、Writer、Verifier 是图内角色职责，
共享 Run、预算和 Checkpoint；当前没有独立 Agent 之间的消息或并行调度。

## 接口

认证后的 `GET /api/v1/skills` 返回名称、版本、图版本和角色职责。
启动复用 `POST /api/v1/workspaces/{workspace_id}/research-runs`，提供
`Authorization` 和 `Idempotency-Key`，在原有请求中增加两个字段：

```json
{
  "skill_name": "sec.filing-verification",
  "skill_version": "v1",
  "mode": "local",
  "original_question": "核验本申报中的营业收入及其变化，并列出证据。",
  "confirmed_scope": ["Apple 2023 Form 10-K"],
  "exclusions": ["投资建议"],
  "completion_criteria": ["提供可追溯 Evidence、计算依据及核验结果"],
  "knowledge_base_ids": ["替换为当前 Workspace 已导入且就绪的知识库 UUID"],
  "financial_scope": {
    "schema_version": 1,
    "cik": "0000320193",
    "accession": "0000320193-23-000106",
    "form": "10-K",
    "report_period": "2023-09-30",
    "as_of": "2023-11-03T12:00:00Z",
    "unit": "USD",
    "scale": 6
  }
}
```

服务器沿用现有成员权限、资源授权和 readiness 校验。返回 `202` 仅表示受理，
响应里的 `agent_run_id`、`research_run_id`、`job_id` 用于原有事件、报告和恢复接口。
图执行状态与业务核验结论保持分开：Verifier 返回现有的 `verified`、`partial`、
`conflict`、`insufficient_evidence`，不能把 Run 完成解释为事实全部核验通过。

## 注册与执行

`modules/skills/registry.py` 保存可信定义；`policy.py` 复用 SEC profile 并约束为六个只读工具：
`knowledge_search`、`sec.search_filing`、`sec.read_filing_section`、
`sec.get_xbrl_facts`、`sec.diff_filings`、`finance.calculate`。
Skill 不开放监控订阅，也不扩大运行环境原有的工具权限和调用上限。

版本通过既有 `AgentRun.harness_version = skill:sec.filing-verification:v1` 持久化，
进入现有幂等请求指纹与 Trace。Worker 首次加载及恢复时均按该版本绑定策略；
未知版本、工具缺失或缺少 Verifier/持久恢复依赖时拒绝执行。无 Skill 字段的历史请求
继续使用原 Research profile。图与 Checkpoint 格式沿用既有版本，未新建执行历史表。

首版由用户在页面或 API 显式启动。没有把长工作流放进普通 ToolExecutor，
也未向模型开放自动启动子 Run 的工具；这种入口需要进一步定义父子预算、取消与结果回收语义。
Skill 的原子检索/计算动作仍全部经过现有 ToolRegistry/ToolExecutor。

回滚时先停止新 Skill 提交并处理在途 Skill Run；不要让不识别此 Skill 的旧 Worker
领取这些任务。保留已有 Run、Evidence 和 Checkpoint，再按版本恢复或明确终止。
删除定义后，当前 Worker 会拒绝未知 Skill 版本，不自动降级成普通 Research。
