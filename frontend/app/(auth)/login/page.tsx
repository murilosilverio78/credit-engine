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
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [showResend, setShowResend] = useState(false);
  const [resendSent, setResendSent] = useState(false);
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
    setShowResend(false);
    setResendSent(false);

    try {
      const response = await fetch("/api/auth/login", {
        body: JSON.stringify({ email, password }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      const data = await response.json();

      if (!response.ok) {
        if (data?.detail?.code === "EMAIL_NOT_VERIFIED") {
          setError(
            "Email ainda não confirmado. Verifique sua caixa de entrada ou solicite um novo link abaixo.",
          );
          setShowResend(true);
        } else {
          setError(
            typeof data?.detail === "string"
              ? data.detail
              : "Email ou senha inválidos.",
          );
        }
        return;
      }

      router.replace(searchParams.get("next") || "/operations");
    } catch {
      setError("Erro ao conectar. Tente novamente.");
    } finally {
      setSubmitting(false);
    }
  }

  async function resendVerification() {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL;
    if (!apiUrl) {
      return;
    }

    try {
      await fetch(`${apiUrl}/api/v1/auth/resend-verification`, {
        body: JSON.stringify({ email }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      setResendSent(true);
    } catch {}
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
            Informe seu email e senha para acessar o Credit Engine.
          </p>
          <label className="block text-[11px] font-medium text-muted-foreground">
            <span className="mb-1 block">Email</span>
            <input
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
              data-testid="login-email"
              onChange={(event) => setEmail(event.target.value)}
              placeholder="voce@empresa.com"
              type="email"
              value={email}
            />
          </label>
          <label className="mt-3 block text-[11px] font-medium text-muted-foreground">
            <span className="mb-1 block">Senha</span>
            <input
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
              data-testid="login-password"
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Sua senha"
              type="password"
              value={password}
            />
          </label>
          {error ? (
            <p className="mt-3 text-xs text-red-700" data-testid="login-error" role="alert">
              {error}
            </p>
          ) : null}
          <button
            className="mt-4 flex h-10 w-full items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground bg-background text-[13px] font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="login-submit"
            disabled={submitting || !email.trim() || !password}
            type="submit"
          >
            {submitting ? (
              <LoaderCircle aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
            ) : null}
            {submitting ? "Entrando..." : "Iniciar sessão"}
          </button>
          {showResend && !resendSent ? (
            <button
              className="mt-3 w-full text-center text-xs font-medium text-foreground underline-offset-4 hover:underline"
              data-testid="login-resend"
              onClick={resendVerification}
              type="button"
            >
              Reenviar email de confirmação
            </button>
          ) : null}
          {resendSent ? (
            <p className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
              Novo link enviado. Verifique sua caixa de entrada.
            </p>
          ) : null}
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
