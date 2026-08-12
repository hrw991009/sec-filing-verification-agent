import { AuthGuard } from "../auth/AuthGuard";
import { AuthPage } from "./AuthPage";
import { WorkspaceHome } from "./WorkspaceHome";

export function App() {
  return (
    <AuthGuard
      anonymous={<AuthPage />}
      authenticated={(currentUser) => <WorkspaceHome currentUser={currentUser} />}
    />
  );
}
