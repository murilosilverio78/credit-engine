"use client";

import { LoaderCircle } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

function ConfirmAccess() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const next = searchParams.get("next") || "/operations";
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function confirm() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/auth/confirm", {
        body: JSON.stringify({ next, token }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const data = (await response.json()) as { next?: string };
      router.push(data.next || "/operations");
    } catch {
      setError("Não foi possível confirmar o acesso. Solicite um novo link.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-dvh flex-col bg-muted/40">
      <header className="border-b-[0.5px] border-border bg-background px-5 py-3.5">
        <p className="text-[13px] font-medium text-foreground">Credit Engine</p>
        <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
          AntecipaGov
        </p>
      </header>
      <main className="flex flex-1 items-center justify-center px-5 py-10">
        <section className="w-full max-w-sm rounded-lg border-[0.5px] border-border bg-background p-5">
          <p className="mb-1 text-[15px] font-medium text-foreground">
            Confirmar acesso
          </p>
          <p className="mb-5 text-xs leading-5 text-muted-foreground">
            Clique no botão abaixo para confirmar seu acesso ao Credit Engine.
          </p>
          {error ? (
            <p className="mb-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
              {error}
            </p>
          ) : null}
          <button
            className="flex h-10 w-full items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground bg-background text-[13px] font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            disabled={loading || !token}
            onClick={confirm}
            type="button"
          >
            {loading ? (
              <LoaderCircle aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
            ) : null}
            {loading ? "Confirmando..." : "Confirmar acesso"}
          </button>
        </section>
      </main>
    </div>
  );
}

export default function ConfirmPage() {
  return (
    <Suspense fallback={null}>
      <ConfirmAccess />
    </Suspense>
  );
}
