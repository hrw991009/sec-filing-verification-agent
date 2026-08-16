import { ApiProblem } from "../api/api";
import { getAgentTrace, type AgentTrace } from "./chat-api";

const DEFAULT_ATTEMPT_DELAYS_MS = [0, 500, 1_000, 2_000, 4_000] as const;
const MAX_ATTEMPT_DELAY_MS = 60_000;

export type ConfirmedAgentRunStatus = "cancelled" | "completed" | "failed";

export interface ConfirmedAgentRunTerminal {
  readonly status: ConfirmedAgentRunStatus;
  readonly trace: AgentTrace;
}

export interface PollAgentRunTerminalOptions {
  readonly attemptDelaysMs?: readonly number[];
  readonly signal?: AbortSignal;
}

function expectedTerminalEvent(status: ConfirmedAgentRunStatus): string {
  if (status === "completed") return "agent.run.completed";
  if (status === "cancelled") return "agent.run.cancelled";
  return "agent.run.failed";
}

function isConsistentStopReason(
  status: ConfirmedAgentRunStatus,
  stopReason: AgentTrace["run"]["stop_reason"],
): boolean {
  if (status === "completed") return stopReason === "final";
  if (status === "cancelled") return stopReason === "cancelled";
  return stopReason !== null && stopReason !== "final" && stopReason !== "cancelled";
}

/**
 * Accept a terminal state only when the Trace summary and its final committed
 * business Event agree. A cancellation request or a transport close is never
 * sufficient evidence by itself.
 */
export function confirmedAgentRunTerminal(trace: AgentTrace): ConfirmedAgentRunTerminal | null {
  const status = trace.run.status;
  if (status !== "completed" && status !== "failed" && status !== "cancelled") {
    return null;
  }
  if (trace.run.terminal_at === null || !isConsistentStopReason(status, trace.run.stop_reason)) {
    return null;
  }
  const terminalEvent = trace.events.at(-1);
  if (
    terminalEvent?.event_type !== expectedTerminalEvent(status) ||
    terminalEvent.details.stop_reason !== trace.run.stop_reason
  ) {
    return null;
  }
  return { status, trace };
}

function retryableStatusReadError(error: unknown): boolean {
  if (error instanceof ApiProblem) {
    return error.status === 408 || error.status === 429 || error.status >= 500;
  }
  return error instanceof TypeError;
}

function validateAttemptDelays(delays: readonly number[]): void {
  if (
    delays.length === 0 ||
    delays.some(
      (delay) => !Number.isSafeInteger(delay) || delay < 0 || delay > MAX_ATTEMPT_DELAY_MS,
    )
  ) {
    throw new RangeError("The Agent Run status polling schedule is invalid.");
  }
}

function waitForAttempt(delayMs: number, signal: AbortSignal | undefined): Promise<boolean> {
  if (signal?.aborted === true) return Promise.resolve(false);
  if (delayMs === 0) return Promise.resolve(true);

  return new Promise((resolve) => {
    const timer = window.setTimeout(() => {
      finish(true);
    }, delayMs);
    function finish(ready: boolean): void {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      resolve(ready);
    }
    function abort(): void {
      finish(false);
    }
    signal?.addEventListener("abort", abort, { once: true });
  });
}

/** Poll an existing read-only Trace endpoint for a bounded terminal confirmation. */
export async function pollAgentRunTerminal(
  workspaceId: string,
  runId: string,
  options: PollAgentRunTerminalOptions = {},
): Promise<ConfirmedAgentRunTerminal | null> {
  const delays = options.attemptDelaysMs ?? DEFAULT_ATTEMPT_DELAYS_MS;
  validateAttemptDelays(delays);

  for (const [index, delay] of delays.entries()) {
    if (!(await waitForAttempt(delay, options.signal))) return null;
    try {
      const trace = await getAgentTrace(workspaceId, runId, options.signal);
      const terminal = confirmedAgentRunTerminal(trace);
      if (terminal !== null) return terminal;
    } catch (error: unknown) {
      if (options.signal?.aborted === true) return null;
      if (!retryableStatusReadError(error) || index === delays.length - 1) {
        throw error;
      }
    }
  }
  return null;
}
