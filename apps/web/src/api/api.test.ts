import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json" },
    status: 200,
  });
}

describe("typed API session recovery", () => {
  it("shares one refresh request across concurrent authenticated calls", async () => {
    let refreshCalls = 0;
    const fetchMock = vi.fn<typeof fetch>((input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;

      if (url.endsWith("/api/v1/auth/refresh")) {
        refreshCalls += 1;
        return Promise.resolve(
          jsonResponse({
            access_token: "short-lived-access-value",
            expires_at: "2026-08-11T12:10:00Z",
            token_type: "Bearer",
          }),
        );
      }

      if (url.endsWith("/api/v1/auth/me")) {
        return Promise.resolve(
          jsonResponse({
            user: {
              email: "learner@example.com",
              id: "11111111-1111-4111-8111-111111111111",
            },
            workspaces: [],
          }),
        );
      }

      return Promise.resolve(new Response(null, { status: 404 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const { getCurrentUser } = await import("./api");

    const [first, second] = await Promise.all([getCurrentUser(), getCurrentUser()]);

    expect(first.user.email).toBe("learner@example.com");
    expect(second.user.email).toBe("learner@example.com");
    expect(refreshCalls).toBe(1);
  });

  it("retries one transport failure inside the recovery grace window", async () => {
    let refreshCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(() => {
        refreshCalls += 1;
        if (refreshCalls === 1) {
          return Promise.reject(new TypeError("simulated response loss"));
        }
        return Promise.resolve(
          jsonResponse({
            access_token: "recovered-access-value",
            expires_at: "2026-08-11T12:10:00Z",
            token_type: "Bearer",
          }),
        );
      }),
    );
    const { refreshAccessSession } = await import("./api");

    const recovered = await refreshAccessSession();

    expect(recovered.access_token).toBe("recovered-access-value");
    expect(refreshCalls).toBe(2);
  });

  it("does not let an older in-flight refresh overwrite an explicit logout", async () => {
    let resolveRefresh: ((response: Response) => void) | undefined;
    let refreshCalls = 0;
    const fetchMock = vi.fn<typeof fetch>((input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.endsWith("/api/v1/auth/refresh")) {
        refreshCalls += 1;
        return new Promise<Response>((resolve) => {
          resolveRefresh = resolve;
        });
      }
      if (url.endsWith("/api/v1/auth/logout")) {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      return Promise.resolve(new Response(null, { status: 404 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const api = await import("./api");
    const session = await import("../auth/session");
    session.setAccessSession({
      expiresAt: "2026-08-11T12:05:00Z",
      token: "older-access-value",
    });

    const pendingRefresh = api.refreshAccessSession();
    await vi.waitFor(() => {
      expect(refreshCalls).toBe(1);
    });
    await api.logoutAccount();
    expect(session.getAccessSession()).toBeNull();

    if (resolveRefresh === undefined) {
      throw new Error("The refresh request was not captured.");
    }
    resolveRefresh(
      jsonResponse({
        access_token: "stale-refreshed-access-value",
        expires_at: "2026-08-11T12:10:00Z",
        token_type: "Bearer",
      }),
    );
    await pendingRefresh;

    expect(session.getAccessSession()).toBeNull();
  });

  it("does not let an older refresh rejection clear a newer login", async () => {
    let resolveRefresh: ((response: Response) => void) | undefined;
    let refreshCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((input) => {
        const url =
          typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (url.endsWith("/api/v1/auth/refresh")) {
          refreshCalls += 1;
          return new Promise<Response>((resolve) => {
            resolveRefresh = resolve;
          });
        }
        if (url.endsWith("/api/v1/auth/login")) {
          return Promise.resolve(
            jsonResponse({
              access_token: "newer-login-access-value",
              expires_at: "2026-08-11T12:10:00Z",
              token_type: "Bearer",
              user: {
                email: "learner@example.com",
                id: "11111111-1111-4111-8111-111111111111",
              },
            }),
          );
        }
        return Promise.resolve(new Response(null, { status: 404 }));
      }),
    );
    const api = await import("./api");
    const session = await import("../auth/session");

    const pendingRefresh = api.refreshAccessSession();
    await vi.waitFor(() => {
      expect(refreshCalls).toBe(1);
    });
    await api.loginAccount("learner@example.com", "Newer!Pass123");

    if (resolveRefresh === undefined) {
      throw new Error("The refresh request was not captured.");
    }
    resolveRefresh(
      new Response(
        JSON.stringify({
          code: "INVALID_REFRESH_SESSION",
          detail: "A valid browser session could not be refreshed.",
          status: 401,
          title: "Invalid refresh session",
          trace_id: "0123456789abcdef0123456789abcdef",
          type: "urn:iip:problem:invalid-refresh-session",
        }),
        {
          headers: { "content-type": "application/problem+json" },
          status: 401,
        },
      ),
    );
    await expect(pendingRefresh).rejects.toBeInstanceOf(api.ApiProblem);

    expect(session.getAccessSession()?.token).toBe("newer-login-access-value");
  });
});
