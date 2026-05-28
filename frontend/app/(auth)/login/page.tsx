"use client";

import { LoaderCircle } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useState } from "react";

import { useSession } from "@/hooks/use-session";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { loading, session } = useSession();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!loading && session) {
      router.replace(searchParams.get("next") || "/operations");
    }
  }, [loading, router, searchParams, session]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch("/api/auth/magic-link", {
        body: JSON.stringify({ email }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      setSent(true);
    } catch {
      setError("Não foi possível enviar o link. Verifique o email informado.");
    } finally {
      setSubmitting(false);
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
        <form
          className="w-full max-w-sm rounded-lg border-[0.5px] border-border bg-background p-5"
          onSubmit={submit}
        >
          <p className="mb-1 text-[15px] font-medium text-foreground">
            Iniciar sessão
          </p>
          <p className="mb-5 text-xs leading-5 text-muted-foreground">
            Informe seu email corporativo para receber um link mágico de acesso.
          </p>
          <label className="block text-[11px] font-medium text-muted-foreground">
            <span className="mb-1 block">Email</span>
            <input
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
              onChange={(event) => setEmail(event.target.value)}
              placeholder="voce@empresa.com"
              type="email"
              value={email}
            />
          </label>
          {sent ? (
            <p className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
              Link enviado. Verifique sua caixa de entrada.
            </p>
          ) : null}
          {error ? (
            <p className="mt-3 text-xs text-red-700" role="alert">
              {error}
            </p>
          ) : null}
          <button
            className="mt-4 flex h-10 w-full items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground bg-background text-[13px] font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            disabled={submitting || !email.trim()}
            type="submit"
          >
            {submitting ? (
              <LoaderCircle aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
            ) : null}
            {submitting ? "Enviando..." : "Iniciar sessão"}
          </button>
        </form>
      </main>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
