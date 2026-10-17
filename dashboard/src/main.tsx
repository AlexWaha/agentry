import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createAppRouter } from "@/app/router";
import { createQueryClient } from "@/lib/query-client";
import { bootToken } from "@/lib/token";
import { startWsClient } from "@/lib/ws-client";
import "@/styles/index.css";

// Order matters: the token is read from the URL fragment and the fragment removed before anything renders (FR-79).
bootToken();

const queryClient = createQueryClient();
startWsClient(queryClient);
const router = createAppRouter();

const container = document.getElementById("root");
if (!container) throw new Error("Missing #root element");

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
