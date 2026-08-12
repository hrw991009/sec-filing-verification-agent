import { useState, type SubmitEvent } from "react";

import { ApiProblem } from "../api/api";
import { useAuth } from "../auth/auth-context";

type AuthMode = "login" | "register";

function errorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return `${error.message}${error.traceId === null ? "" : `（追踪号：${error.traceId}）`}`;
  }
  return "暂时无法完成请求，请稍后重试。";
}

export function AuthPage() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "register") {
        await register(email, password);
      } else {
        await login(email, password);
      }
    } catch (caught: unknown) {
      setError(errorMessage(caught));
    } finally {
      setPassword("");
      setSubmitting(false);
    }
  }

  function selectMode(nextMode: AuthMode): void {
    setMode(nextMode);
    setError(null);
    setPassword("");
  }

  return (
    <main className="auth-layout">
      <section className="brand-panel" aria-labelledby="product-title">
        <div className="brand-mark" aria-hidden="true">
          IIP
        </div>
        <p className="eyebrow">Industry Intelligence Platform</p>
        <h1 id="product-title">把行业信号，变成可追溯的研究结论。</h1>
        <p className="brand-panel__summary">
          在统一 Workspace 中组织证据、研究任务与 Agent
          协作。身份、会话和租户边界从第一天就由服务端验证。
        </p>
        <ul className="capability-list" aria-label="平台基础能力">
          <li>证据优先的行业研究</li>
          <li>可审计的 Agent 执行</li>
          <li>严格隔离的团队 Workspace</li>
        </ul>
      </section>

      <section className="auth-card" aria-labelledby="auth-heading">
        <div className="auth-tabs" role="group" aria-label="账户操作">
          <button
            aria-pressed={mode === "login"}
            className={mode === "login" ? "auth-tab auth-tab--active" : "auth-tab"}
            onClick={() => {
              selectMode("login");
            }}
            type="button"
          >
            登录
          </button>
          <button
            aria-pressed={mode === "register"}
            className={mode === "register" ? "auth-tab auth-tab--active" : "auth-tab"}
            onClick={() => {
              selectMode("register");
            }}
            type="button"
          >
            创建账户
          </button>
        </div>

        <div className="auth-card__heading">
          <p className="eyebrow">安全入口</p>
          <h2 id="auth-heading">{mode === "register" ? "创建你的研究空间" : "欢迎回来"}</h2>
          <p>
            {mode === "register"
              ? "注册会原子创建账户、默认 Workspace 和 Owner 成员关系。"
              : "登录后 Access Token 仅保存在当前页面内存中。"}
          </p>
        </div>

        <form className="auth-form" onSubmit={(event) => void submit(event)}>
          <label>
            邮箱
            <input
              autoComplete="email"
              name="email"
              onChange={(event) => {
                setEmail(event.currentTarget.value);
              }}
              required
              type="email"
              value={email}
            />
          </label>
          <label>
            密码
            <input
              autoComplete={mode === "register" ? "new-password" : "current-password"}
              maxLength={128}
              minLength={mode === "register" ? 12 : 1}
              name="password"
              onChange={(event) => {
                setPassword(event.currentTarget.value);
              }}
              required
              type="password"
              value={password}
            />
          </label>
          {mode === "register" ? (
            <p className="field-hint">至少 12 位，并同时包含大小写字母、数字和符号。</p>
          ) : null}
          {error === null ? null : (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "正在处理…" : mode === "register" ? "创建账户并进入" : "登录 Workspace"}
          </button>
        </form>
      </section>
    </main>
  );
}
