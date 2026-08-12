import { useState, type SubmitEvent } from "react";

import { ApiProblem } from "../api/api";
import { useAuth, type CurrentUser } from "../auth/auth-context";

interface WorkspaceHomeProps {
  readonly currentUser: CurrentUser;
}

const roleNames = {
  admin: "管理员",
  member: "成员",
  owner: "所有者",
  viewer: "观察者",
} as const;

function errorMessage(error: unknown): string {
  return error instanceof ApiProblem ? error.message : "操作失败，请稍后重试。";
}

export function WorkspaceHome({ currentUser }: WorkspaceHomeProps) {
  const { changePassword, logout } = useAuth();
  const [showPasswordForm, setShowPasswordForm] = useState(false);
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
      setSubmitting(false);
    }
  }

  return (
    <main className="workspace-shell">
      <header className="topbar">
        <a className="product-lockup" href="/" aria-label="行业智能平台首页">
          <span className="brand-mark brand-mark--compact" aria-hidden="true">
            IIP
          </span>
          <span>
            <strong>行业智能平台</strong>
            <small>Industry Intelligence Platform</small>
          </span>
        </a>
        <div className="account-actions">
          <span>{currentUser.user.email}</span>
          <button
            className="quiet-button"
            disabled={submitting}
            onClick={() => void signOut()}
            type="button"
          >
            退出
          </button>
        </div>
      </header>

      <div className="workspace-layout">
        <nav className="side-nav" aria-label="主导航">
          <p className="side-nav__label">Workspace</p>
          <a className="side-nav__item side-nav__item--active" href="#overview">
            总览
          </a>
          <span className="side-nav__item side-nav__item--disabled">研究任务</span>
          <span className="side-nav__item side-nav__item--disabled">Agent 工作台</span>
          <p className="side-nav__label">账户</p>
          <button
            className="side-nav__item side-nav__button"
            onClick={() => {
              setShowPasswordForm((visible) => !visible);
              setError(null);
            }}
            type="button"
          >
            修改密码
          </button>
        </nav>

        <section className="workspace-content" id="overview" aria-labelledby="workspace-heading">
          <div className="page-heading">
            <div>
              <p className="eyebrow">已验证的实时成员关系</p>
              <h1 id="workspace-heading">你的 Workspace</h1>
              <p>角色与会话状态由每次请求重新查询 PostgreSQL，不信任前端缓存。</p>
            </div>
            <div className="security-chip">服务端已验证</div>
          </div>

          {error === null ? null : (
            <p className="form-error form-error--wide" role="alert">
              {error}
            </p>
          )}

          <div className="workspace-grid">
            {currentUser.workspaces.map((workspace) => (
              <article className="workspace-card" key={workspace.id}>
                <div className="workspace-card__icon" aria-hidden="true">
                  {workspace.name.slice(0, 1).toUpperCase()}
                </div>
                <div>
                  <h2>{workspace.name}</h2>
                  <p>{workspace.id}</p>
                </div>
                <span className="role-badge">{roleNames[workspace.role]}</span>
              </article>
            ))}
          </div>

          {showPasswordForm ? (
            <section className="settings-panel" aria-labelledby="password-heading">
              <div>
                <p className="eyebrow">安全设置</p>
                <h2 id="password-heading">修改密码</h2>
                <p>成功后会原子撤销所有设备上的会话，你需要使用新密码重新登录。</p>
              </div>
              <form
                className="password-form"
                onSubmit={(event) => void submitPasswordChange(event)}
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
          ) : null}
        </section>
      </div>
    </main>
  );
}
