import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ChatApi from "./chat-api";
import type { MemoryDetail, MemorySnapshot } from "./chat-api";
import { MemoryWorkspace } from "./MemoryWorkspace";

const mocks = vi.hoisted(() => ({
  deleteMemory: vi.fn(),
  disableMemory: vi.fn(),
  enableMemory: vi.fn(),
  getMemory: vi.fn(),
  listMemories: vi.fn(),
  recordMemoryFeedback: vi.fn(),
  updateMemory: vi.fn(),
}));

vi.mock("./chat-api", async (loadOriginal) => ({
  ...(await loadOriginal<typeof ChatApi>()),
  ...mocks,
}));

const workspaceId = "11111111-1111-4111-8111-111111111111";
const userId = "22222222-2222-4222-8222-222222222222";
const memoryId = "33333333-3333-4333-8333-333333333333";
const revisionId = "44444444-4444-4444-8444-444444444444";
const conversationId = "55555555-5555-4555-8555-555555555555";

function memory(ownerUserId = userId): MemorySnapshot {
  return {
    confidence: 0.95,
    created_at: "2026-08-20T08:00:00Z",
    current_revision_id: revisionId,
    current_version: 2,
    expires_at: null,
    id: memoryId,
    kind: "preference",
    owner_user_id: ownerUserId,
    revision: 4,
    scope: ownerUserId === userId ? "user" : "workspace",
    source_conversation_id: conversationId,
    status: "confirmed",
    updated_at: "2026-08-20T08:01:00Z",
  };
}

function detail(ownerUserId = userId): MemoryDetail {
  const snapshot = memory(ownerUserId);
  const revision = {
    content: "钢铁报告默认使用中文回答。",
    created_at: "2026-08-20T08:01:00Z",
    editor_user_id: ownerUserId,
    expires_at: null,
    id: revisionId,
    kind: snapshot.kind,
    policy_decision: "allowed" as const,
    scope: snapshot.scope,
    source_message_ids: [revisionId],
    validity: "valid" as const,
    version: 2,
    write_action: "update" as const,
    write_reason: "user_governance_update",
  };
  return { current_revision: revision, memory: snapshot, revisions: [revision] };
}

describe("MemoryWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listMemories.mockResolvedValue([memory()]);
    mocks.getMemory.mockResolvedValue(detail());
    mocks.updateMemory.mockResolvedValue(detail());
    mocks.disableMemory.mockResolvedValue({
      ...detail(),
      memory: { ...memory(), revision: 5, status: "disabled" },
    });
    mocks.enableMemory.mockResolvedValue(detail());
    mocks.recordMemoryFeedback.mockResolvedValue({
      actor_user_id: userId,
      created_at: "2026-08-20T08:02:00Z",
      id: revisionId,
      memory_id: memoryId,
      memory_revision_id: revisionId,
      reason: null,
      updated_at: "2026-08-20T08:02:00Z",
      value: "helpful",
    });
    mocks.deleteMemory.mockResolvedValue(undefined);
  });

  it("reloads server state for search, edit, disable, feedback, and delete", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <MemoryWorkspace
        canManage
        focusedMemoryId={memoryId}
        userId={userId}
        workspaceId={workspaceId}
      />,
    );

    expect(await screen.findByDisplayValue("钢铁报告默认使用中文回答。")).toBeVisible();
    await user.type(screen.getByRole("textbox", { name: "搜索 Memory" }), "钢铁");
    await user.selectOptions(screen.getByRole("combobox", { name: "Memory 状态" }), "confirmed");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() => {
      expect(mocks.listMemories).toHaveBeenLastCalledWith(
        workspaceId,
        expect.objectContaining({ query: "钢铁", status: "confirmed" }),
      );
    });

    const editor = screen.getByRole("textbox", { name: "当前正文" });
    await user.clear(editor);
    await user.type(editor, "钢铁报告默认使用英文回答。");
    await user.click(screen.getByRole("button", { name: "保存新 revision" }));
    await waitFor(() => {
      expect(mocks.updateMemory).toHaveBeenCalledWith(
        workspaceId,
        memoryId,
        4,
        expect.objectContaining({ content: "钢铁报告默认使用英文回答。" }),
      );
    });

    await user.click(screen.getByRole("button", { name: "停用" }));
    await waitFor(() => {
      expect(mocks.disableMemory).toHaveBeenCalledWith(workspaceId, memoryId, 4);
    });
    await user.click(screen.getByRole("button", { name: "有帮助" }));
    await waitFor(() => {
      expect(mocks.recordMemoryFeedback).toHaveBeenCalled();
    });
    await user.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => {
      expect(mocks.deleteMemory).toHaveBeenCalled();
    });
    expect(mocks.listMemories.mock.calls.length).toBeGreaterThan(1);
  });

  it("keeps a shared Memory readable but disables owner-only governance", async () => {
    mocks.listMemories.mockResolvedValue([memory(revisionId)]);
    mocks.getMemory.mockResolvedValue(detail(revisionId));
    render(
      <MemoryWorkspace
        canManage
        focusedMemoryId={memoryId}
        userId={userId}
        workspaceId={workspaceId}
      />,
    );

    expect(await screen.findByText(/仅创建者可以修改或删除/u)).toBeVisible();
    expect(screen.getByRole("button", { name: "保存新 revision" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "删除" })).toBeDisabled();
  });
});
