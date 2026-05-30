"use client";

import { LoaderCircle } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

type ConfirmState = "loading" | "success" | "error" | "invalid";

function ConfirmEmailContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [state, setState] = useState<ConfirmState>("loading");

  useEffect(() => {
    const token = searchParams.get("token");
    const apiUrl = process.env.NEXT_PUBLIC_API_URL;

    if (!token || !apiUrl) {
      setState("invalid");
      return;
    }

    async function confirmEmail() {
      try {
        const response = await fetch(`${apiUrl}/api/v1/auth/confirm-email`, {
          body: JSON.stringify({ token }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        });
        setState(response.ok ? "success" : "error");
      } catch {
        setState("error");
      }
    }

    void confirmEmail();
  }, [searchParams]);

  const title =
    state === "success"
      ? "Email confirmado!"
      : state === "loading"
        ? "Confirmando email..."
        : "Link inválido ou expirado";
  const description =
    state === "success"
      ? "Você já pode fazer login."
      : state === "loading"
        ? "Aguarde enquanto validamos seu acesso."
        : "Solicite um novo link na página de login.";

  return (
    <div className="flex min-h-dvh flex-col bg-muted/40">
      <header className="border-b-[0.5px] border-border bg-background px-5 py-3.5">
        <p className="text-[13px] font-medium text-foreground">Credit Engine</p>
        <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
          AntecipaGov
        </p>
      </header>
      <main className="flex flex-1 items-center justify-center px-5 py-10">
        <div className="w-full max-w-sm rounded-lg border-[0.5px] border-border bg-background p-5">
          <p className="mb-1 text-[15px] font-medium text-foreground">{title}</p>
          <p className="text-xs leading-5 text-muted-foreground">{description}</p>
          {state === "loading" ? (
            <div className="mt-4 flex h-10 items-center gap-2 text-xs text-muted-foreground">
              <LoaderCircle aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
              Validando token
            </div>
          ) : (
            <button
              className="mt-4 flex h-10 w-full items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground bg-background text-[13px] font-medium text-foreground transition-colors hover:bg-muted"
              onClick={() => router.push("/login")}
              type="button"
            >
              Ir para o login
            </button>
          )}
        </div>
      </main>
    </div>
  );
}

export default function ConfirmEmailPage() {
  return (
    <Suspense fallback={null}>
      <ConfirmEmailContent />
    </Suspense>
  );
}
