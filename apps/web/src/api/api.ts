import { createIndustryPlatformApiClient, type components } from "@industry-platform/api-contract";

import {
  clearAccessSession,
  getAccessSession,
  getAccessSessionRevision,
  setAccessSession,
} from "../auth/session";

type CurrentUser = components["schemas"]["CurrentUserResponse"];
type LoginResponse = components["schemas"]["LoginResponse"];
type RegisterResponse = components["schemas"]["RegistrationResponse"];
type RefreshResponse = components["schemas"]["RefreshResponse"];

interface ApiResult<T> {
  readonly data?: T;
  readonly error?: unknown;
  readonly response: Response;
}

interface ProblemLike {
  readonly code?: unknown;
  readonly detail?: unknown;
  readonly trace_id?: unknown;
}

const CSRF_COOKIE_NAME = "__Host-iip_csrf";
const CSRF_HEADER_NAME = "X-CSRF-Token";
const client = createIndustryPlatformApiClient({
  baseUrl: window.location.origin,
});

let refreshInFlight: Promise<RefreshResponse> | null = null;

export class ApiProblem extends Error {
  readonly code: string;
  readonly status: number;
  readonly traceId: string | null;

  constructor(status: number, problem: ProblemLike | null) {
    const detail =
      typeof problem?.detail === "string" ? problem.detail : "The request could not be completed.";
    super(detail);
    this.name = "ApiProblem";
    this.code = typeof problem?.code === "string" ? problem.code : "REQUEST_FAILED";
    this.status = status;
    this.traceId = typeof problem?.trace_id === "string" ? problem.trace_id : null;
  }
}

function problemFrom(value: unknown): ProblemLike | null {
  return typeof value === "object" && value !== null ? value : null;
}

function unwrapData<T>(result: ApiResult<T>): T {
  if (result.response.ok && result.data !== undefined) {
    return result.data;
  }

  throw new ApiProblem(result.response.status, problemFrom(result.error));
}

function assertNoContent(result: { readonly error?: unknown; readonly response: Response }): void {
  if (!result.response.ok) {
    throw new ApiProblem(result.response.status, problemFrom(result.error));
  }
}

function readCookie(name: string): string {
  if (typeof document === "undefined") {
    return "";
  }

  const prefix = `${name}=`;
  const entry = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));

  if (entry === undefined) {
    return "";
  }
  try {
    return decodeURIComponent(entry.slice(prefix.length));
  } catch {
    return "";
  }
}

function csrfHeaders(): Record<string, string> {
  return { [CSRF_HEADER_NAME]: readCookie(CSRF_COOKIE_NAME) };
}

function requestRefresh() {
  return client.POST("/api/v1/auth/refresh", {
    headers: csrfHeaders(),
  });
}

async function executeRefresh(): Promise<RefreshResponse> {
  const expectedRevision = getAccessSessionRevision();
  let result: Awaited<ReturnType<typeof requestRefresh>>;
  try {
    result = await requestRefresh();
  } catch {
    result = await requestRefresh();
  }

  try {
    const refreshed = unwrapData<RefreshResponse>(result);
    setAccessSession(
      {
        expiresAt: refreshed.expires_at,
        token: refreshed.access_token,
      },
      expectedRevision,
    );
    return refreshed;
  } catch (error: unknown) {
    if (error instanceof ApiProblem && error.status === 401) {
      clearAccessSession(expectedRevision);
    }
    throw error;
  }
}

export function refreshAccessSession(): Promise<RefreshResponse> {
  refreshInFlight ??= executeRefresh().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

async function withAccessToken<T>(request: (accessToken: string) => Promise<T>): Promise<T> {
  let attemptedSession = getAccessSession();
  if (attemptedSession === null) {
    await refreshAccessSession();
    attemptedSession = getAccessSession();
  }
  if (attemptedSession === null) {
    throw new ApiProblem(401, { code: "INVALID_AUTHENTICATED_SESSION" });
  }

  try {
    return await request(attemptedSession.token);
  } catch (error: unknown) {
    if (!(error instanceof ApiProblem) || error.status !== 401) {
      throw error;
    }

    const latestSession = getAccessSession();
    if (latestSession?.token === attemptedSession.token) {
      await refreshAccessSession();
    }
    const replacementSession = getAccessSession();
    if (replacementSession === null) {
      throw error;
    }
    return request(replacementSession.token);
  }
}

export async function registerAccount(email: string, password: string): Promise<RegisterResponse> {
  return unwrapData<RegisterResponse>(
    await client.POST("/api/v1/auth/register", {
      body: { email, password },
    }),
  );
}

export async function loginAccount(email: string, password: string): Promise<LoginResponse> {
  const loggedIn = unwrapData<LoginResponse>(
    await client.POST("/api/v1/auth/login", {
      body: { email, password },
    }),
  );
  setAccessSession({
    expiresAt: loggedIn.expires_at,
    token: loggedIn.access_token,
  });
  return loggedIn;
}

export async function getCurrentUser(): Promise<CurrentUser> {
  return withAccessToken(async (accessToken) =>
    unwrapData<CurrentUser>(
      await client.GET("/api/v1/auth/me", {
        headers: { Authorization: `Bearer ${accessToken}` },
      }),
    ),
  );
}

export async function logoutAccount(): Promise<void> {
  const result = await client.POST("/api/v1/auth/logout", {
    headers: csrfHeaders(),
  });

  if (result.response.ok) {
    clearAccessSession();
    return;
  }
  assertNoContent(result);
}

export async function changeAccountPassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await withAccessToken(async (accessToken) => {
    const result = await client.POST("/api/v1/auth/change-password", {
      body: {
        current_password: currentPassword,
        new_password: newPassword,
      },
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    assertNoContent(result);
  });
  clearAccessSession();
}
