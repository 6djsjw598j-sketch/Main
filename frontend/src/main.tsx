import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { retryDelay, shouldRetry } from "./api/hooks";
import { Dashboard } from "./App";
import "./index.css";
import { PreferencesProvider } from "./state/preferences";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: shouldRetry,
      retryDelay,
      refetchOnWindowFocus: true,
      staleTime: 5_000,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <PreferencesProvider>
        <Dashboard />
      </PreferencesProvider>
    </QueryClientProvider>
  </StrictMode>,
);
