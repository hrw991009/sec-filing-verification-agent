import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import "./app/app.css";
import { AuthProvider } from "./auth/AuthProvider";

const rootElement = document.getElementById("root");

if (rootElement === null) {
  throw new Error("The root element #root was not found.");
}

createRoot(rootElement).render(
  <StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </StrictMode>,
);
