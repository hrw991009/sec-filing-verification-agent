import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "./AuthProvider";
import { useAuth, type CurrentUser } from "./auth-context";
import { clearAccessSession, setAccessSession } from "./session";

const apiMocks = vi.hoisted(() => ({
  getCurrentUser: vi.fn(),
  refreshAccessSession: vi.fn(),
}));

vi.mock("../api/api", () => ({
  ApiProblem: class ApiProblem extends Error {
    readonly status = 500;
    readonly traceId = null;
  },
  changeAccountPassword: vi.fn(),
  getCurrentUser: apiMocks.getCurrentUser,
  loginAccount: vi.fn(),
  logoutAccount: vi.fn(),
  refreshAccessSession: apiMocks.refreshAccessSession,
  registerAccount: vi.fn(),
}));

function AuthStateProbe() {
  const { state } = useAuth();
  return <p>{state.status}</p>;
}

describe("AuthProvider", () => {
  beforeEach(() => {
    clearAccessSession();
    apiMocks.refreshAccessSession.mockImplementation(() => {
      const refreshed = {
        access_token: "test-access-value",
        expires_at: "2026-08-11T12:10:00Z",
        token_type: "Bearer",
      };
      setAccessSession({
        expiresAt: refreshed.expires_at,
        token: refreshed.access_token,
      });
      return Promise.resolve(refreshed);
    });
    apiMocks.getCurrentUser.mockResolvedValue({
      user: {
        email: "learner@example.com",
        id: "11111111-1111-4111-8111-111111111111",
      },
      workspaces: [],
    });
  });

  it("returns the UI to anonymous state when the API store clears its session", async () => {
    render(
      <AuthProvider>
        <AuthStateProbe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("authenticated")).toBeVisible();
    });

    act(() => {
      clearAccessSession();
    });

    expect(screen.getByText("anonymous")).toBeVisible();
  });

  it("does not let a late current-user response restore a cleared session", async () => {
    let resolveCurrentUser: ((value: CurrentUser) => void) | undefined;
    apiMocks.getCurrentUser
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveCurrentUser = resolve;
          }),
      )
      .mockResolvedValue({
        user: {
          email: "learner@example.com",
          id: "11111111-1111-4111-8111-111111111111",
        },
        workspaces: [],
      });

    render(
      <AuthProvider>
        <AuthStateProbe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(apiMocks.getCurrentUser).toHaveBeenCalledOnce();
    });
    act(() => {
      clearAccessSession();
    });
    const settleCurrentUser = resolveCurrentUser;
    if (settleCurrentUser === undefined) {
      throw new Error("The current-user request was not captured.");
    }
    await act(async () => {
      settleCurrentUser({
        user: {
          email: "learner@example.com",
          id: "11111111-1111-4111-8111-111111111111",
        },
        workspaces: [],
      });
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(screen.getByText("anonymous")).toBeVisible();
    });
    expect(screen.queryByText("authenticated")).not.toBeInTheDocument();
  });
});
