"use client";

import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { useSession } from "@/hooks/use-session";

const DIRETOR_ONLY_PREFIXES = [
  "/settings/alcadas",
  "/settings/pricing",
  "/settings/users",
];

export function AdminAuthGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { loading, session } = useSession();

  useEffect(() => {
    if (loading) {
      return;
    }

    if (!session) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      return;
    }

    if (
      DIRETOR_ONLY_PREFIXES.some((prefix) => pathname.startsWith(prefix)) &&
      session.user.role !== "diretor"
    ) {
      router.replace("/forbidden");
    }
  }, [loading, pathname, router, session]);

  if (loading || !session) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-muted/40 text-sm text-muted-foreground">
        Carregando sessao...
      </div>
    );
  }

  if (
    DIRETOR_ONLY_PREFIXES.some((prefix) => pathname.startsWith(prefix)) &&
    session.user.role !== "diretor"
  ) {
    return null;
  }

  return children;
}
