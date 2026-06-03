"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";

import { clearAuthToken } from "@/lib/auth-token";

export function Providers({ children }: { children: ReactNode }) {
  const [authWarning, setAuthWarning] = useState("");
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            refetchOnWindowFocus: false,
            staleTime: 30_000,
          },
        },
      }),
  );

  useEffect(() => {
    function handleUnauthorized(event: Event) {
      const detail = (event as CustomEvent<string>).detail;
      clearAuthToken();
      setAuthWarning(
        detail || "Sessao expirada ou sem permissao. Faca login novamente.",
      );
      if (!window.location.pathname.startsWith("/login")) {
        const next = encodeURIComponent(window.location.pathname);
        window.location.href = `/login?next=${next}`;
      }
    }

    window.addEventListener("api:unauthorized", handleUnauthorized);
    return () => {
      window.removeEventListener("api:unauthorized", handleUnauthorized);
    };
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      {authWarning ? (
        <div className="fixed right-4 top-4 z-50 max-w-sm rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 shadow-sm">
          <div className="flex items-start gap-3">
            <p className="leading-5">{authWarning}</p>
            <button
              aria-label="Fechar aviso"
              className="ml-auto text-red-700 hover:text-red-900"
              onClick={() => setAuthWarning("")}
              type="button"
            >
              x
            </button>
          </div>
        </div>
      ) : null}
      {children}
    </QueryClientProvider>
  );
}
