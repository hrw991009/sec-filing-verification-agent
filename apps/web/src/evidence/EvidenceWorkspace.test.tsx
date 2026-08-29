import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Evidence, ResearchClaim } from "./evidence-api";
import type { ResearchRun } from "../research/research-api";

const evidenceId = "11111111-1111-4111-8111-111111111111";
const researchRunId = "22222222-2222-4222-8222-222222222222";
const originRunId = "77777777-7777-4777-8777-777777777777";
const originStepId = "88888888-8888-4888-8888-888888888888";

const evidence: Evidence = {
  authorization_snapshot: {
    action: "evidence.normalize",
    actor_user_id: "33333333-3333-4333-8333-333333333333",
    captured_at: "2026-08-21T08:00:00Z",
    role: "owner",
    workspace_id: "44444444-4444-4444-8444-444444444444",
  },
  canonical_url: "https://example.test/source",
  content_sha256: "a".repeat(64),
  created_at: "2026-08-21T08:00:00Z",
  excerpt: "A bounded attributable source excerpt.",
  id: evidenceId,
  invalidated_at: null,
  invalidation_reason: null,
  kind: "news",
  license_or_terms: "Public metadata with attribution.",
  locator: {
    content_sha256: "a".repeat(64),
    locator_type: "industry_source_v1",
    provider: "world_bank_news",
    schema_version: 1,
    source_item_id: "55555555-5555-4555-8555-555555555555",
    source_kind: "news",
    source_version: "api-v2-2026-08",
  },
  normalizer_version: "evidence-normalizer-v1",
  origin_case_id: null,
  origin_observation_id: "66666666-6666-4666-8666-666666666666",
  origin_run_id: originRunId,
  origin_source_ordinal: 1,
  origin_step_id: originStepId,
  origin_tool_call_id: "99999999-9999-4999-8999-999999999999",
  retrieved_at: "2026-08-21T08:00:00Z",
  revision: 1,
  source_published_at: "2026-08-21T07:00:00Z",
  source_resource_version: `api-v2-2026-08:${"a".repeat(32)}`,
  status: "active",
  title: "Public transport transition",
  updated_at: "2026-08-21T08:00:00Z",
  workspace_id: "44444444-4444-4444-8444-444444444444",
};

const researchRun: ResearchRun = {
  agent_run_id: originRunId,
  agent_status: "running",
  brief: {
    approval_reason: null,
    budget: {
      deadline: "2026-08-21T08:10:00Z",
      max_cost_micro_usd: 300_000,
      max_steps: 20,
      max_total_tokens: 12_000,
    },
    completion_criteria: ["Produce an attributable L3 draft"],
    confirmed_at: evidence.created_at,
    confirmed_by_user_id: evidence.authorization_snapshot.actor_user_id,
    confirmed_scope: ["Public smart transport news"],
    exclusions: ["Investment advice"],
    financial_scope: null,
    id: "12121212-1212-4121-8121-121212121212",
    original_question: "Find a public transport policy update.",
    revision: 1,
  },
  cost_micro_usd: 40,
  created_at: evidence.created_at,
  current_node: "synthesize_claims",
  draft: null,
  event_count: 18,
  graph_version: "research-l3-graph-v1",
  id: researchRunId,
  input_tokens_used: 20,
  output_tokens_used: 10,
  owner_user_id: evidence.authorization_snapshot.actor_user_id,
  plan: null,
  revision: 2,
  state_schema_version: 1,
  status: "active",
  step_count: 3,
  stop_reason: null,
  updated_at: evidence.updated_at,
  workspace_id: evidence.workspace_id,
};

const claim: ResearchClaim = {
  confidence: 0.8,
  conflict: false,
  coverage: 1,
  created_at: evidence.created_at,
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  relations: [
    {
      evidence,
      ordinal: 1,
      origin_run_id: originRunId,
      origin_step_id: originStepId,
      relation: "supports",
      relation_version: 1,
      status: "active",
    },
  ],
  research_run_id: researchRunId,
  revision: 1,
  statement: "Public transport is transitioning.",
  updated_at: evidence.updated_at,
  verification_status: "supported",
  workspace_id: evidence.workspace_id,
};

const mocks = vi.hoisted(() => ({
  getClaimGraph: vi.fn(),
  getEvidence: vi.fn(),
  invalidateEvidence: vi.fn(),
  listEvidence: vi.fn(),
  listResearchClaims: vi.fn(),
}));

const researchMocks = vi.hoisted(() => ({ listResearchRuns: vi.fn() }));

vi.mock("./evidence-api", () => mocks);
vi.mock("../research/research-api", () => researchMocks);

import { EvidenceWorkspace } from "./EvidenceWorkspace";

describe("EvidenceWorkspace", () => {
  it("shows reverse lineage and invalidates through a revision precondition", async () => {
    const user = userEvent.setup();
    mocks.listEvidence.mockResolvedValue([evidence]);
    mocks.getEvidence.mockResolvedValue(evidence);
    researchMocks.listResearchRuns.mockResolvedValue([researchRun]);
    mocks.listResearchClaims.mockResolvedValue([claim]);
    mocks.getClaimGraph.mockResolvedValue({
      edges: [
        {
          id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          relation: "supports",
          source_node_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          status: "active",
          target_node_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        },
      ],
      nodes: [],
      research_run_id: researchRunId,
    });
    mocks.invalidateEvidence.mockResolvedValue({
      ...evidence,
      excerpt: null,
      invalidated_at: "2026-08-21T09:00:00Z",
      invalidation_reason: "Source withdrawn",
      revision: 2,
      status: "tombstoned",
    });

    render(
      <EvidenceWorkspace
        canManage
        focusedEvidenceId={evidenceId}
        onOpenResearch={vi.fn()}
        refreshToken={0}
        workspaceId={evidence.workspace_id}
      />,
    );

    expect(await screen.findByText("A bounded attributable source excerpt.")).toBeVisible();
    expect(screen.getByText("Observation")).toBeVisible();
    expect(await screen.findByText("Public transport is transitioning.")).toBeVisible();
    expect(screen.getByText(/coverage 100%/u)).toBeVisible();

    await user.type(screen.getByLabelText("Evidence 失效原因"), "Source withdrawn");
    await user.click(screen.getByRole("button", { name: "撤销 Evidence" }));

    await waitFor(() => {
      expect(mocks.invalidateEvidence).toHaveBeenCalledWith(evidence.workspace_id, evidenceId, 1, {
        reason: "Source withdrawn",
        status: "tombstoned",
      });
    });
  });
});
