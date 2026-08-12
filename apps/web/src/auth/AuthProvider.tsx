import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  ApiProblem,
  changeAccountPassword,
  getCurrentUser,
  loginAccount,
  logoutAccount,
  refreshAccessSession,
  registerAccount,
} from "../api/api";
import {
  AuthContext,
  type AuthContextValue,
  type AuthState,
  type CurrentUser,
} from "./auth-context";
import { getAccessSession, getAccessSessionRevision, subscribeToAccessSession } from "./session";

interface AuthProviderProps {
  readonly children: ReactNode;
}

function publicError(error: unknown): string {
  if (error instanceof ApiProblem) {
    return `${error.message}${error.traceId === null ? "" : `（追踪号：${error.traceId}）`}`;
  }
  return "服务暂时不可用，请稍后重试。";
}

async function loadStableCurrentUser(): Promise<CurrentUser | null> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const requestRevision = getAccessSessionRevision();
    const currentUser = await getCurrentUser();
    if (getAccessSession() === null) {
      return null;
    }
    if (getAccessSessionRevision() === requestRevision) {
      return currentUser;
    }
  }
  return null;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const [bootstrapVersion, setBootstrapVersion] = useState(0);

  useEffect(
    () =>
      subscribeToAccessSession(() => {
        if (getAccessSession() === null) {
          setState({ status: "anonymous" });
        }
      }),
    [],
  );

  useEffect(() => {
    let active = true;

    async function bootstrap(): Promise<void> {
      try {
        await refreshAccessSession();
        const currentUser = await loadStableCurrentUser();
        if (active && currentUser !== null) {
          setState({ currentUser, status: "authenticated" });
        }
      } catch (error: unknown) {
        if (!active) {
          return;
        }
        if (error instanceof ApiProblem && error.status === 401) {
          setState({ status: "anonymous" });
          return;
        }
        setState({ error: publicError(error), status: "unavailable" });
      }
    }

    void bootstrap();
    return () => {
      active = false;
    };
  }, [bootstrapVersion]);

  const loadAuthenticatedUser = useCallback(async (): Promise<void> => {
    const currentUser = await loadStableCurrentUser();
    if (currentUser !== null) {
      setState({ currentUser, status: "authenticated" });
    }
  }, []);

  const login = useCallback(
    async (email: string, password: string): Promise<void> => {
      await loginAccount(email, password);
      await loadAuthenticatedUser();
    },
    [loadAuthenticatedUser],
  );

  const register = useCallback(
    async (email: string, password: string): Promise<void> => {
      await registerAccount(email, password);
      await login(email, password);
    },
    [login],
  );

  const logout = useCallback(async (): Promise<void> => {
    await logoutAccount();
    setState({ status: "anonymous" });
  }, []);

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string): Promise<void> => {
      await changeAccountPassword(currentPassword, newPassword);
      setState({ status: "anonymous" });
    },
    [],
  );

  const retryBootstrap = useCallback(() => {
    setState({ status: "loading" });
    setBootstrapVersion((version) => version + 1);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      changePassword,
      login,
      logout,
      register,
      retryBootstrap,
      state,
    }),
    [changePassword, login, logout, register, retryBootstrap, state],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
