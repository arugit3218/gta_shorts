import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import App from "./App";
import { DialogProvider, ToastProvider } from "./components/ui";
import "./styles.css";

// A data router (instead of <BrowserRouter>) so pages can block navigation when they have unsaved
// edits (useBlocker). App keeps its own <Routes> as descendant routes.
const router = createBrowserRouter([
  {
    path: "*",
    element: (
      <ToastProvider>
        <DialogProvider>
          <App />
        </DialogProvider>
      </ToastProvider>
    ),
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
