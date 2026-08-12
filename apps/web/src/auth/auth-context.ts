import { createContext, use } from "react";

import type { components } from "@industry-platform/api-contract";

export type CurrentUser = components["schemas"]["CurrentUserResponse"];

export type AuthState =
  | { readonly status: "loading" }
  | { readonly status: "anonymous" }
  | { readonly error: string; readonly status: "unavailable" }
  | { readonly currentUser: CurrentUser; readonly status: "authenticated" };

export interface AuthContextValue {
  readonly changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  readonly login: (email: string, password: string) => Promise<void>;
  readonly logout: () => Promise<void>;
  readonly register: (email: string, password: string) => Promise<void>;
  readonly retryBootstrap: () => void;
  readonly state: AuthState;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const context = use(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used inside AuthProvider.");
  }
  return context;
}
