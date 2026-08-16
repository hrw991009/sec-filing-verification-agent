import { useCallback } from "react";

import { listMessages, type ConversationMessage } from "./chat-api";
import { MESSAGE_PAGE_SIZE } from "./chat-workbench-model";

/** Load every persisted message page for the currently selected Workspace. */
export function useAllConversationMessages(
  workspaceId: string,
): (conversationId: string) => Promise<ConversationMessage[]> {
  return useCallback(
    async (conversationId: string): Promise<ConversationMessage[]> => {
      const collected: ConversationMessage[] = [];
      let cursor: string | null = null;
      do {
        const page = await listMessages(
          workspaceId,
          conversationId,
          cursor === null ? { limit: MESSAGE_PAGE_SIZE } : { cursor, limit: MESSAGE_PAGE_SIZE },
        );
        collected.push(...page.messages);
        cursor = page.next_cursor;
      } while (cursor !== null);
      return collected;
    },
    [workspaceId],
  );
}
