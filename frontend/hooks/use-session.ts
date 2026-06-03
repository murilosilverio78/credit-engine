"use client";

import { useQuery } from "@tanstack/react-query";

import { clearAuthToken, getAuthToken } from "@/lib/auth-token";
import { ApiError, getCurrentUser } from "@/lib/api";
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
  if (!getAuthToken()) {
    return null;
  }
  let data: Awaited<ReturnType<typeof getCurrentUser>>;
  try {
    data = await getCurrentUser();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      clearAuthToken();
      return null;
    }
    throw error;
  }
  const user = data.user;

  const alcada: Alcada =
    user.alcada ??
    (user.role === "diretor"
      ? "committee"
      : user.role === "gerente"
        ? "manager"
        : "analyst");

  return {
    user: {
      alcada,
      email: user.email,
      id: user.id,
      name: user.name,
      role: user.role,
    },
  };
}

export function useSession(): { session: Session | null; loading: boolean } {
  const token = getAuthToken();
  const query = useQuery({
    queryFn: fetchSession,
    queryKey: ["auth", "session", token],
    retry: false,
    staleTime: 60_000,
  });

  return {
    loading: query.isLoading,
    session: query.data ?? null,
  };
}
