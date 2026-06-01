"use client";

import { UserPlus } from "lucide-react";
import { FormEvent, useState } from "react";

const inputClassName =
  "h-10 w-full rounded-md border border-input bg-background px-3 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring";

type UserRole = "analista" | "gerente" | "diretor";

export default function UsersSettingsPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<UserRole>("analista");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    setSuccess("");

    try {
      const response = await fetch("/api/auth/register", {
        body: JSON.stringify({ email, name, password, role }),
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        setError(
          typeof data?.detail === "string"
            ? data.detail
            : "Não foi possível criar o usuário.",
        );
        return;
      }

      setSuccess(`Usuário criado. Um email de confirmação foi enviado para ${email}.`);
      setName("");
      setEmail("");
      setRole("analista");
      setPassword("");
    } catch {
      setError("Erro ao conectar. Tente novamente.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-dvh flex-col bg-muted/40">
      <header className="border-b-[0.5px] border-border bg-background px-5 py-3.5">
        <div className="flex items-center gap-2">
          <UserPlus aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
          <h1 className="text-[15px] font-medium text-foreground">Usuários</h1>
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Crie acessos com senha temporária e confirmação de email.
        </p>
      </header>

      <section className="flex-1 px-5 py-4">
        <form
          className="max-w-2xl rounded-lg border-[0.5px] border-border bg-background p-4"
          onSubmit={submit}
        >
          <p className="mb-3 text-[10px] font-medium uppercase tracking-[0.05em] text-muted-foreground">
            Novo usuário
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block text-[11px] font-medium text-muted-foreground">
              <span className="mb-1 block">Nome</span>
              <input
                className={inputClassName}
                data-testid="user-name"
                onChange={(event) => setName(event.target.value)}
                placeholder="Nome completo"
                value={name}
              />
            </label>
            <label className="block text-[11px] font-medium text-muted-foreground">
              <span className="mb-1 block">Email</span>
              <input
                className={inputClassName}
                data-testid="user-email"
                onChange={(event) => setEmail(event.target.value)}
                placeholder="voce@empresa.com"
                type="email"
                value={email}
              />
            </label>
            <label className="block text-[11px] font-medium text-muted-foreground">
              <span className="mb-1 block">Role</span>
              <select
                className={inputClassName}
                data-testid="user-role"
                onChange={(event) => setRole(event.target.value as UserRole)}
                value={role}
              >
                <option value="analista">Analista</option>
                <option value="gerente">Gerente</option>
                <option value="diretor">Diretor</option>
              </select>
            </label>
            <label className="block text-[11px] font-medium text-muted-foreground">
              <span className="mb-1 block">Senha temporária</span>
              <input
                className={inputClassName}
                data-testid="user-password"
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Senha temporária"
                type="password"
                value={password}
              />
            </label>
          </div>

          {error ? (
            <p className="mt-3 text-xs text-red-700" data-testid="user-message-error" role="alert">
              {error}
            </p>
          ) : null}
          {success ? (
            <p className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-xs text-emerald-700" data-testid="user-message-success">
              {success}
            </p>
          ) : null}

          <button
            className="mt-4 flex h-10 items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground bg-background px-4 text-[13px] font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="user-submit"
            disabled={submitting || !name.trim() || !email.trim() || !password}
            type="submit"
          >
            {submitting ? "Criando..." : "Criar usuário"}
          </button>
        </form>
      </section>
    </div>
  );
}
