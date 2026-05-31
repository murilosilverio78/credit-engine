"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";
import { FormEvent, useState } from "react";

import { getAlcadaAuditTrail, getAlcadas, updateAlcada } from "@/lib/api";
import type { AlcadaConfig, Rating, UserRole } from "@/lib/types";

const inputClassName =
  "h-10 w-full rounded-md border border-input bg-background px-3 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring";

const ratingOptions: Rating[] = ["A", "B", "C", "D", "E"];
const roleLabels: Record<UserRole, string> = {
  analista: "Analista",
  diretor: "Diretor",
  gerente: "Gerente",
};

function formatCurrency(value: number | null) {
  if (value === null || value === undefined) {
    return "—";
  }
  return new Intl.NumberFormat("pt-BR", {
    currency: "BRL",
    maximumFractionDigits: 0,
    style: "currency",
  }).format(value);
}

function formatDate(date: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(date));
}

function AlcadaCard({ config }: { config: AlcadaConfig }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    justificativa: "",
    max_rating: config.max_rating,
    max_valor: String(config.max_valor ?? 0),
    override_max_rating: config.override_max_rating ?? "B",
    override_max_valor: String(config.override_max_valor ?? ""),
    pode_aprovar_escalada: config.pode_aprovar_escalada,
    pode_override: config.pode_override,
  });
  const [error, setError] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      updateAlcada(config.role, {
        justificativa: form.justificativa,
        max_rating: form.max_rating,
        max_valor: Number(form.max_valor),
        override_max_rating: form.pode_override ? form.override_max_rating : null,
        override_max_valor:
          form.pode_override && form.override_max_valor
            ? Number(form.override_max_valor)
            : null,
        pode_aprovar_escalada: form.pode_aprovar_escalada,
        pode_override: form.pode_override,
      }),
    onSuccess: async () => {
      setEditing(false);
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["alcadas"] });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (form.justificativa.trim().length < 10) {
      setError("Informe uma justificativa com pelo menos 10 caracteres.");
      return;
    }
    setError("");
    mutation.mutate();
  }

  return (
    <article
      className="rounded-lg border-[0.5px] border-border bg-background p-4"
      data-role={config.role}
      data-testid="alcada-row"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-[13px] font-medium text-foreground">
            {roleLabels[config.role] ?? config.role}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Regras de aprovação e override
          </p>
        </div>
        <button
          className="h-8 rounded-md border-[0.5px] border-border px-3 text-xs text-foreground hover:bg-muted"
          onClick={() => setEditing((value) => !value)}
          type="button"
        >
          {editing ? "Fechar" : "Editar"}
        </button>
      </div>
      <div className="grid gap-2 text-xs">
        <div className="flex justify-between gap-3 rounded-md bg-muted px-3 py-2">
          <span className="text-muted-foreground">Aprova até</span>
          <span className="font-mono text-foreground">{formatCurrency(config.max_valor)}</span>
        </div>
        <div className="flex justify-between gap-3 rounded-md bg-muted px-3 py-2">
          <span className="text-muted-foreground">Rating máx.</span>
          <span className="font-mono text-foreground">{config.max_rating}</span>
        </div>
        <div className="flex justify-between gap-3 rounded-md bg-muted px-3 py-2">
          <span className="text-muted-foreground">Override</span>
          <span className="text-foreground">{config.pode_override ? "Sim" : "Não"}</span>
        </div>
        {config.pode_override ? (
          <div className="flex justify-between gap-3 rounded-md bg-muted px-3 py-2">
            <span className="text-muted-foreground">Override até</span>
            <span className="font-mono text-foreground">
              {formatCurrency(config.override_max_valor)} · {config.override_max_rating ?? "—"}
            </span>
          </div>
        ) : null}
      </div>
      {editing ? (
        <form className="mt-4 border-t-[0.5px] border-border pt-4" onSubmit={submit}>
          <div className="grid gap-2 md:grid-cols-2">
            <label className="block text-[11px] font-medium text-muted-foreground">
              <span className="mb-1 block">Valor máx.</span>
              <input
                className={inputClassName}
                onChange={(event) => setForm((value) => ({ ...value, max_valor: event.target.value }))}
                type="number"
                value={form.max_valor}
              />
            </label>
            <label className="block text-[11px] font-medium text-muted-foreground">
              <span className="mb-1 block">Rating máx.</span>
              <select
                className={inputClassName}
                onChange={(event) => setForm((value) => ({ ...value, max_rating: event.target.value as Rating }))}
                value={form.max_rating}
              >
                {ratingOptions.map((rating) => (
                  <option key={rating} value={rating}>{rating}</option>
                ))}
              </select>
            </label>
            <label className="block text-[11px] font-medium text-muted-foreground">
              <span className="mb-1 block">Pode override</span>
              <select
                className={inputClassName}
                onChange={(event) => setForm((value) => ({ ...value, pode_override: event.target.value === "true" }))}
                value={String(form.pode_override)}
              >
                <option value="true">Sim</option>
                <option value="false">Não</option>
              </select>
            </label>
            <label className="block text-[11px] font-medium text-muted-foreground">
              <span className="mb-1 block">Aprova escalada</span>
              <select
                className={inputClassName}
                onChange={(event) => setForm((value) => ({ ...value, pode_aprovar_escalada: event.target.value === "true" }))}
                value={String(form.pode_aprovar_escalada)}
              >
                <option value="true">Sim</option>
                <option value="false">Não</option>
              </select>
            </label>
            {form.pode_override ? (
              <>
                <label className="block text-[11px] font-medium text-muted-foreground">
                  <span className="mb-1 block">Override — valor máx.</span>
                  <input
                    className={inputClassName}
                    onChange={(event) => setForm((value) => ({ ...value, override_max_valor: event.target.value }))}
                    type="number"
                    value={form.override_max_valor}
                  />
                </label>
                <label className="block text-[11px] font-medium text-muted-foreground">
                  <span className="mb-1 block">Override — rating máx.</span>
                  <select
                    className={inputClassName}
                    onChange={(event) => setForm((value) => ({ ...value, override_max_rating: event.target.value as Rating }))}
                    value={form.override_max_rating}
                  >
                    {ratingOptions.map((rating) => (
                      <option key={rating} value={rating}>{rating}</option>
                    ))}
                  </select>
                </label>
              </>
            ) : null}
          </div>
          <label className="mt-2 block text-[11px] font-medium text-muted-foreground">
            <span className="mb-1 block">Justificativa</span>
            <textarea
              className="h-16 w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
              data-testid="alcada-justificativa"
              onChange={(event) => setForm((value) => ({ ...value, justificativa: event.target.value }))}
              placeholder="Motivo da alteração..."
              value={form.justificativa}
            />
          </label>
          {error ? <p className="mt-1 text-[11px] text-red-700">{error}</p> : null}
          {mutation.isError ? (
            <p className="mt-1 text-[11px] text-red-700">Não foi possível salvar.</p>
          ) : null}
          <button
            className="mt-3 flex h-10 items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground bg-background px-4 text-[13px] font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50"
            data-testid="alcada-save"
            disabled={mutation.isPending}
            type="submit"
          >
            Salvar regras
          </button>
        </form>
      ) : null}
    </article>
  );
}

export default function AlcadasSettingsPage() {
  const query = useQuery({
    queryFn: getAlcadas,
    queryKey: ["alcadas"],
  });
  const auditQuery = useQuery({
    queryFn: getAlcadaAuditTrail,
    queryKey: ["alcadas", "audit"],
  });
  const configs = query.data ?? [];
  const auditItems = auditQuery.data ?? [];

  return (
    <div className="flex min-h-dvh flex-col bg-muted/40">
      <header className="border-b-[0.5px] border-border bg-background px-5 py-3.5">
        <div className="flex items-center gap-2">
          <SlidersHorizontal aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
          <h1 className="text-[15px] font-medium text-foreground">Configuração de alçadas</h1>
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Limites por perfil para aprovação, override e escalada.
        </p>
      </header>
      <section className="flex-1 px-5 py-4">
        {query.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Carregando alçadas...</p>
        ) : query.isError ? (
          <p className="py-10 text-center text-sm text-red-700">Não foi possível carregar alçadas.</p>
        ) : (
          <div className="grid gap-3 lg:grid-cols-3">
            {configs.map((config) => (
              <AlcadaCard config={config} key={config.role} />
            ))}
          </div>
        )}
        <div className="mt-4 rounded-lg border-[0.5px] border-border bg-background p-4">
          <p className="text-[11px] font-medium uppercase tracking-[0.05em] text-muted-foreground">
            Histórico
          </p>
          {auditItems.length ? (
            <div className="mt-3 overflow-hidden rounded-md border-[0.5px] border-border">
              <table className="w-full border-collapse text-xs">
                <thead className="bg-muted text-left text-[11px] text-muted-foreground">
                  <tr>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Data</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Ator</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Justificativa</th>
                  </tr>
                </thead>
                <tbody>
                  {auditItems.map((item, index) => (
                    <tr key={item.id ?? `${item.created_at}-${index}`}>
                      <td className="border-b-[0.5px] border-border px-3 py-2 font-mono text-muted-foreground">
                        {formatDate(item.created_at)}
                      </td>
                      <td className="border-b-[0.5px] border-border px-3 py-2">
                        {item.actor_type || item.actor_id || "—"}
                      </td>
                      <td className="border-b-[0.5px] border-border px-3 py-2">
                        {item.override_reason || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="mt-2 text-xs text-muted-foreground">
              Nenhuma alteração registrada.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}
