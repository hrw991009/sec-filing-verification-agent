import type { ReactNode } from "react";

import { useAuth, type CurrentUser } from "./auth-context";

interface AuthGuardProps {
  readonly authenticated: (currentUser: CurrentUser) => ReactNode;
  readonly anonymous: ReactNode;
}

export function AuthGuard({ authenticated, anonymous }: AuthGuardProps) {
  const { retryBootstrap, state } = useAuth();

  if (state.status === "loading") {
    return (
      <main className="status-page" aria-live="polite">
        <div className="status-page__spinner" aria-hidden="true" />
        <p>正在安全恢复会话…</p>
      </main>
    );
  }

  if (state.status === "unavailable") {
    return (
      <main className="status-page" role="alert">
        <p>{state.error}</p>
        <button type="button" onClick={retryBootstrap}>
          重新连接
        </button>
      </main>
    );
  }

  return state.status === "authenticated" ? authenticated(state.currentUser) : anonymous;
}
