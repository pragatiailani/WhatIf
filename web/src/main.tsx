import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

// Apply the saved theme before first paint to avoid a flash.
try {
  document.documentElement.dataset.theme = localStorage.getItem("whatif.theme") || "dark";
} catch {
  document.documentElement.dataset.theme = "dark";
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
