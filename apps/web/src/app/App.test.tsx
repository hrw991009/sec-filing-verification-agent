import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../auth/auth-context";
import { App } from "./App";

const currentUser = {
  user: {
    email: "learner@example.com",
    id: "11111111-1111-4111-8111-111111111111",
  },
  workspaces: [
    {
      id: "22222222-2222-4222-8222-222222222222",
      name: "行业研究 Workspace",
      role: "owner" as const,
    },
  ],
};

function contextValue(
  state: AuthContextValue["state"],
  overrides: Partial<AuthContextValue> = {},
): AuthContextValue {
  return {
    changePassword: vi.fn(() => Promise.resolve()),
    login: vi.fn(() => Promise.resolve()),
    logout: vi.fn(() => Promise.resolve()),
    register: vi.fn(() => Promise.resolve()),
    retryBootstrap: vi.fn(),
    state,
    ...overrides,
  };
}

describe("application authentication shell", () => {
  it("registers through the authentication context without retaining the password", async () => {
    const user = userEvent.setup();
    const register = vi.fn(() => Promise.resolve());
    const value = contextValue({ status: "anonymous" }, { register });

    render(
      <AuthContext value={value}>
        <App />
      </AuthContext>,
    );

    await user.click(screen.getByRole("button", { name: "创建账户" }));
    await user.type(screen.getByLabelText("邮箱"), "learner@example.com");
    await user.type(screen.getByLabelText("密码"), "Strong!Pass123");
    await user.click(screen.getByRole("button", { name: "创建账户并进入" }));

    expect(register).toHaveBeenCalledWith("learner@example.com", "Strong!Pass123");
    expect(screen.getByLabelText("密码")).toHaveValue("");
  });

  it("renders server-verified workspace state and signs out", async () => {
    const user = userEvent.setup();
    const logout = vi.fn(() => Promise.resolve());
    const value = contextValue({ currentUser, status: "authenticated" }, { logout });

    render(
      <AuthContext value={value}>
        <App />
      </AuthContext>,
    );

    expect(screen.getByRole("heading", { name: "你的 Workspace" })).toBeVisible();
    expect(screen.getByText("行业研究 Workspace")).toBeVisible();
    expect(screen.getByText("所有者")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "退出" }));
    expect(logout).toHaveBeenCalledOnce();
  });
});
