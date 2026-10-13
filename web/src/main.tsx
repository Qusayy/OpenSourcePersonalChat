import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router/dom";
import { loadBootstrap } from "@/lib/bootstrap";
import { queryClient, seedQueries } from "@/state/queries";
import { router } from "./routes";
import "./styles/app.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root");

// The first values are in the page already when the server sent it; only the
// dev server's page has to ask. Either way nothing is drawn before they are
// known, so no figure is ever shown empty and then filled.
loadBootstrap().then((boot) => {
  seedQueries(boot);
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
});
