import { useState, type SubmitEvent } from "react";

import { ApiProblem } from "../api/api";
import { useAuth, type CurrentUser } from "../auth/auth-context";
import { ChatWorkbench } from "../chat/ChatWorkbench";

interface WorkspaceHomeProps {
  readonly currentUser: CurrentUser;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return `${error.message}${error.traceId === null ? "" : `（追踪号 ${error.traceId}）`}`;
  }
  return "操作失败，请稍后重试。";
}

export function WorkspaceHome({ currentUser }: WorkspaceHomeProps) {
  const { changePassword, logout } = useAuth();
  const [showSettings, setShowSettings] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submitPasswordChange(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await changePassword(currentPassword, newPassword);
    } catch (caught: unknown) {
      setError(errorMessage(caught));
    } finally {
      setCurrentPassword("");
      setNewPassword("");
      setSubmitting(false);
    }
  }

  async function signOut(): Promise<void> {
    setError(null);
    setSubmitting(true);
    try {
      await logout();
    } catch (caught: unknown) {
      setError(errorMessage(caught));
      setShowSettings(true);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <ChatWorkbench
        currentUser={currentUser}
        onLogout={signOut}
        onOpenSettings={() => {
          setError(null);
          setShowSettings(true);
        }}
      />
      {showSettings ? (
        <div className="dialog-backdrop" role="presentation">
          <section
            aria-labelledby="account-settings-title"
            aria-modal="true"
            className="dialog-card account-settings-dialog"
            role="dialog"
          >
            <div className="settings-dialog__heading">
              <div>
                <p className="eyebrow">账户与安全</p>
                <h2 id="account-settings-title">个人设置</h2>
              </div>
              <button
                aria-label="关闭账户设置"
                className="quiet-button"
                disabled={submitting}
                onClick={() => {
                  setShowSettings(false);
                }}
                type="button"
              >
                关闭
              </button>
            </div>
            <p className="settings-dialog__identity">当前账户：{currentUser.user.email}</p>
            {error === null ? null : (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <form
              className="password-form"
              onSubmit={(event) => {
                void submitPasswordChange(event);
              }}
            >
              <label>
                当前密码
                <input
                  autoComplete="current-password"
                  maxLength={128}
                  onChange={(event) => {
                    setCurrentPassword(event.currentTarget.value);
                  }}
                  required
                  type="password"
                  value={currentPassword}
                />
              </label>
              <label>
                新密码
                <input
                  autoComplete="new-password"
                  maxLength={128}
                  minLength={12}
                  onChange={(event) => {
                    setNewPassword(event.currentTarget.value);
                  }}
                  required
                  type="password"
                  value={newPassword}
                />
              </label>
              <button className="primary-button" disabled={submitting} type="submit">
                {submitting ? "正在更新…" : "更新并撤销全部会话"}
              </button>
            </form>
          </section>
        </div>
      ) : null}
    </>
  );
}
