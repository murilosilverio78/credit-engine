"use client";

import { useQuery } from "@tanstack/react-query";

import type { Alcada, UserRole } from "@/lib/types";

interface Session {
  user: {
    id: string;
    email: string;
    name: string;
    role: UserRole;
    alcada: Alcada;
  };
}

async function fetchSession() {
  const response = await fetch("/api/auth/me", { credentials: "include" });
  if (!response.ok) {
    return null;
  }
  const data = (await response.json()) as { session: Session | null };
  return data.session;
}

export function useSession(): { session: Session | null; loading: boolean } {
  const query = useQuery({
    queryFn: fetchSession,
    queryKey: ["auth", "session"],
    retry: false,
    staleTime: 60_000,
  });

  return {
    loading: query.isLoading,
    session: query.data ?? null,
  };
}
