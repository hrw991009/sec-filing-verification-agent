import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Evidence, ResearchClaim, ResearchRun } from "./evidence-api";

const evidenceId = "11111111-1111-4111-8111-111111111111";
const researchRunId = "22222222-2222-4222-8222-222222222222";

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
  origin_observation_id: "66666666-6666-4666-8666-666666666666",
  origin_run_id: "77777777-7777-4777-8777-777777777777",
  origin_source_ordinal: 1,
  origin_step_id: "88888888-8888-4888-8888-888888888888",
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
  agent_run_id: evidence.origin_run_id,
  created_at: evidence.created_at,
  id: researchRunId,
  owner_user_id: evidence.authorization_snapshot.actor_user_id,
  revision: 2,
  status: "active",
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
      origin_run_id: evidence.origin_run_id,
      origin_step_id: evidence.origin_step_id,
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
  listResearchRuns: vi.fn(),
}));

vi.mock("./evidence-api", () => mocks);

import { EvidenceWorkspace } from "./EvidenceWorkspace";

describe("EvidenceWorkspace", () => {
  it("shows reverse lineage and invalidates through a revision precondition", async () => {
    const user = userEvent.setup();
    mocks.listEvidence.mockResolvedValue([evidence]);
    mocks.getEvidence.mockResolvedValue(evidence);
    mocks.listResearchRuns.mockResolvedValue([researchRun]);
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
