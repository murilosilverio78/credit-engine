"use client";

import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useState } from "react";

import { IdleWarningModal } from "@/components/idle-warning-modal";
import { useIdleTimeout } from "@/hooks/use-idle-timeout";
import { useSession } from "@/hooks/use-session";
import { clearAuthToken } from "@/lib/auth-token";

const DIRETOR_ONLY_PREFIXES = [
  "/settings/alcadas",
  "/settings/pricing",
  "/settings/users",
];

function AuthenticatedIdleGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [showWarning, setShowWarning] = useState(false);
  const [secondsRemaining, setSecondsRemaining] = useState(120);

  const handleWarning = useCallback(() => {
    setSecondsRemaining(120);
    setShowWarning(true);
  }, []);

  const handleExpire = useCallback(() => {
    clearAuthToken();
    router.replace("/login?reason=idle");
  }, [router]);

  const { resetTimer } = useIdleTimeout({
    onWarning: handleWarning,
    onExpire: handleExpire,
  });

  const handleStayLoggedIn = useCallback(() => {
    resetTimer();
    setShowWarning(false);
  }, [resetTimer]);

  return (
    <>
      <IdleWarningModal
        onStayLoggedIn={handleStayLoggedIn}
        open={showWarning}
        secondsRemaining={secondsRemaining}
      />
      {children}
    </>
  );
}

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

  return <AuthenticatedIdleGuard>{children}</AuthenticatedIdleGuard>;
}
