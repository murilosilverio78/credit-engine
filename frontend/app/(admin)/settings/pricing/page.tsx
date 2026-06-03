"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgePercent } from "lucide-react";
import { FormEvent, useState } from "react";

import {
  ApiError,
  getPricingAudit,
  getPricingMatrix,
  getPricingParameters,
  updatePricingMatrix,
  updatePricingParameter,
} from "@/lib/api";
import type { AuditTrailItem, PricingMatrixRow, PricingParameter } from "@/lib/types";

const inputClassName =
  "h-10 w-full rounded-md border border-input bg-background px-3 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring";

const groupLabels: Record<string, string> = {
  custos_operacionais: "Custos Operacionais",
  estrutura_capital: "Estrutura de Capital",
  risco_credito: "Risco de Crédito",
};

const groupHelp: Record<string, string> = {
  custos_operacionais: "Fees e custos flat sobre principal, receita ou operação.",
  estrutura_capital: "CDI, spreads e participação dos tranches de capital.",
  risco_credito: "Parâmetros base de perda esperada.",
};

function formatDate(date: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(date));
}

function formatDecimal(value: number) {
  return new Intl.NumberFormat("pt-BR", {
    maximumFractionDigits: 4,
    minimumFractionDigits: 2,
  }).format(value * 100);
}

function parseDecimal(value: string) {
  const normalized = value.replace(/\./g, "").replace(",", ".");
  return Number(normalized) / 100;
}

function formatUnknown(value: unknown) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function fieldFromPayload(payload: unknown) {
  if (!payload || typeof payload !== "object") {
    return "-";
  }
  const data = payload as { key?: string; rating?: string };
  return data.key ?? data.rating ?? "-";
}

function mutationErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    return error.message;
  }
  return "Nao foi possivel salvar.";
}

function ParameterCard({ parameter }: { parameter: PricingParameter }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(formatDecimal(parameter.value));
  const [justificativa, setJustificativa] = useState("");
  const [error, setError] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      updatePricingParameter(parameter.key, parseDecimal(value), justificativa),
    onSuccess: async () => {
      setEditing(false);
      setJustificativa("");
      setError("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["pricing", "parameters"] }),
        queryClient.invalidateQueries({ queryKey: ["pricing", "audit"] }),
      ]);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (justificativa.trim().length < 10) {
      setError("Informe uma justificativa com pelo menos 10 caracteres.");
      return;
    }
    if (!Number.isFinite(parseDecimal(value))) {
      setError("Informe um valor numérico válido.");
      return;
    }
    setError("");
    mutation.mutate();
  }

  return (
    <article
      className="rounded-lg border-[0.5px] border-border bg-background p-4"
      data-key={parameter.key}
      data-testid="pricing-param"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-foreground">{parameter.label}</p>
          <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
            {parameter.key} · {parameter.unit}
          </p>
        </div>
        <button
          className="h-8 rounded-md border-[0.5px] border-border px-3 text-xs text-foreground hover:bg-muted"
          onClick={() => setEditing((current) => !current)}
          type="button"
        >
          {editing ? "Fechar" : "Editar"}
        </button>
      </div>
      <div className="mt-3 rounded-md bg-muted px-3 py-2">
        <span className="text-[11px] text-muted-foreground">Valor atual</span>
        <p className="font-mono text-[15px] font-medium text-foreground">
          {formatDecimal(parameter.value)}%
        </p>
      </div>
      {editing ? (
        <form className="mt-4 border-t-[0.5px] border-border pt-4" onSubmit={submit}>
          <label className="block text-[11px] font-medium text-muted-foreground">
            <span className="mb-1 block">Valor ({parameter.unit})</span>
            <input
              className={inputClassName}
              onChange={(event) => setValue(event.target.value)}
              placeholder="1,14"
              value={value}
            />
          </label>
          <label className="mt-2 block text-[11px] font-medium text-muted-foreground">
            <span className="mb-1 block">Justificativa</span>
            <textarea
              className="h-16 w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
              data-testid="pricing-justificativa"
              onChange={(event) => setJustificativa(event.target.value)}
              placeholder="Motivo da alteração..."
              value={justificativa}
            />
          </label>
          {error ? <p className="mt-1 text-[11px] text-red-700">{error}</p> : null}
          {mutation.isError ? (
            <p className="mt-1 text-[11px] text-red-700">
              {mutationErrorMessage(mutation.error)}
            </p>
          ) : null}
          <button
            className="mt-3 flex h-10 items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground bg-background px-4 text-[13px] font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50"
            data-testid="pricing-save"
            disabled={mutation.isPending}
            type="submit"
          >
            Salvar parâmetro
          </button>
        </form>
      ) : null}
    </article>
  );
}

function MatrixEditor({ row }: { row: PricingMatrixRow }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    bond_cobertura: formatDecimal(row.bond_cobertura),
    bond_premio_aa: row.bond_premio_aa === null ? "" : formatDecimal(row.bond_premio_aa),
    justificativa: "",
    lgd_mult: formatDecimal(row.lgd_mult),
    pd_mult: formatDecimal(row.pd_mult),
    perfil: row.perfil ?? "",
  });
  const [error, setError] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      updatePricingMatrix(row.rating, {
        bond_cobertura: parseDecimal(form.bond_cobertura),
        bond_premio_aa: form.bond_premio_aa ? parseDecimal(form.bond_premio_aa) : null,
        justificativa: form.justificativa,
        lgd_mult: parseDecimal(form.lgd_mult),
        pd_mult: parseDecimal(form.pd_mult),
        perfil: form.perfil,
      }),
    onSuccess: async () => {
      setEditing(false);
      setError("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["pricing", "matrix"] }),
        queryClient.invalidateQueries({ queryKey: ["pricing", "audit"] }),
      ]);
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
    <>
      <tr
        className={row.recusa ? "bg-muted/50 text-muted-foreground" : ""}
        data-rating={row.rating}
        data-testid="pricing-matrix-row"
      >
        <td className="border-b-[0.5px] border-border px-3 py-2 font-mono font-medium">
          {row.rating}
          {row.recusa ? (
            <span className="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-[10px] font-medium text-red-800">
              RECUSA
            </span>
          ) : null}
        </td>
        <td className="border-b-[0.5px] border-border px-3 py-2 font-mono">{formatDecimal(row.pd_mult)}%</td>
        <td className="border-b-[0.5px] border-border px-3 py-2 font-mono">{formatDecimal(row.lgd_mult)}%</td>
        <td className="border-b-[0.5px] border-border px-3 py-2 font-mono">{formatDecimal(row.bond_cobertura)}%</td>
        <td className="border-b-[0.5px] border-border px-3 py-2 font-mono">
          {row.bond_premio_aa === null ? "-" : `${formatDecimal(row.bond_premio_aa)}%`}
        </td>
        <td className="border-b-[0.5px] border-border px-3 py-2">{row.perfil ?? "-"}</td>
        <td className="border-b-[0.5px] border-border px-3 py-2 text-right">
          {!row.recusa ? (
            <button
              className="h-8 rounded-md border-[0.5px] border-border px-3 text-xs text-foreground hover:bg-muted"
              onClick={() => setEditing((current) => !current)}
              type="button"
            >
              {editing ? "Fechar" : "Editar"}
            </button>
          ) : null}
        </td>
      </tr>
      {editing ? (
        <tr>
          <td className="border-b-[0.5px] border-border bg-muted/40 px-3 py-3" colSpan={7}>
            <form className="grid gap-2 md:grid-cols-3" onSubmit={submit}>
              {(["pd_mult", "lgd_mult", "bond_cobertura", "bond_premio_aa"] as const).map((field) => (
                <label className="block text-[11px] font-medium text-muted-foreground" key={field}>
                  <span className="mb-1 block">{field}</span>
                  <input
                    className={inputClassName}
                    onChange={(event) => setForm((value) => ({ ...value, [field]: event.target.value }))}
                    value={form[field]}
                  />
                </label>
              ))}
              <label className="block text-[11px] font-medium text-muted-foreground md:col-span-3">
                <span className="mb-1 block">Perfil</span>
                <input
                  className={inputClassName}
                  onChange={(event) => setForm((value) => ({ ...value, perfil: event.target.value }))}
                  value={form.perfil}
                />
              </label>
              <label className="block text-[11px] font-medium text-muted-foreground md:col-span-3">
                <span className="mb-1 block">Justificativa</span>
                <textarea
                  className="h-16 w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
                  data-testid="pricing-justificativa"
                  onChange={(event) => setForm((value) => ({ ...value, justificativa: event.target.value }))}
                  placeholder="Motivo da alteração..."
                  value={form.justificativa}
                />
              </label>
              {error ? <p className="text-[11px] text-red-700 md:col-span-3">{error}</p> : null}
              {mutation.isError ? (
                <p className="text-[11px] text-red-700 md:col-span-3">
                  {mutationErrorMessage(mutation.error)}
                </p>
              ) : null}
              <button
                className="flex h-10 items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground bg-background px-4 text-[13px] font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50"
                data-testid="pricing-save"
                disabled={mutation.isPending}
                type="submit"
              >
                Salvar rating
              </button>
            </form>
          </td>
        </tr>
      ) : null}
    </>
  );
}

export default function PricingSettingsPage() {
  const paramsQuery = useQuery({
    queryFn: getPricingParameters,
    queryKey: ["pricing", "parameters"],
  });
  const matrixQuery = useQuery({
    queryFn: getPricingMatrix,
    queryKey: ["pricing", "matrix"],
  });
  const auditQuery = useQuery({
    queryFn: getPricingAudit,
    queryKey: ["pricing", "audit"],
  });
  const params = paramsQuery.data ?? [];
  const matrix = matrixQuery.data ?? [];
  const audit = auditQuery.data ?? [];

  function groupParams(group: string) {
    return params.filter((parameter) => parameter.grupo === group);
  }

  return (
    <div className="flex min-h-dvh flex-col bg-muted/40">
      <header className="border-b-[0.5px] border-border bg-background px-5 py-3.5">
        <div className="flex items-center gap-2">
          <BadgePercent aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
          <h1 className="text-[15px] font-medium text-foreground">Precificação</h1>
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Parâmetros que alimentam o motor determinístico de taxa sugerida.
        </p>
      </header>
      <section className="flex-1 px-5 py-4">
        <div className="mb-4 rounded-lg border-[0.5px] border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900">
          Alterações afetam todas as novas precificações em até 60 segundos.
        </div>
        {paramsQuery.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Carregando parâmetros...</p>
        ) : paramsQuery.isError ? (
          <p className="py-10 text-center text-sm text-red-700">Não foi possível carregar parâmetros.</p>
        ) : (
          <div className="space-y-4">
            {["estrutura_capital", "custos_operacionais", "risco_credito"].map((group) => (
              <section className="rounded-lg border-[0.5px] border-border bg-background p-4" key={group}>
                <p className="text-[11px] font-medium uppercase tracking-[0.05em] text-muted-foreground">
                  {groupLabels[group]}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">{groupHelp[group]}</p>
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                  {groupParams(group).map((parameter) => (
                    <ParameterCard key={parameter.key} parameter={parameter} />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}

        <section className="mt-4 rounded-lg border-[0.5px] border-border bg-background p-4">
          <p className="text-[11px] font-medium uppercase tracking-[0.05em] text-muted-foreground">
            Matriz de Rating
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Multiplicadores e cobertura usados no cálculo de risco, LGD e performance bond.
          </p>
          {matrixQuery.isLoading ? (
            <p className="py-6 text-center text-sm text-muted-foreground">Carregando matriz...</p>
          ) : matrixQuery.isError ? (
            <p className="py-6 text-center text-sm text-red-700">Não foi possível carregar matriz.</p>
          ) : (
            <div className="mt-3 overflow-hidden rounded-md border-[0.5px] border-border">
              <table className="w-full border-collapse text-xs">
                <thead className="bg-muted text-left text-[11px] text-muted-foreground">
                  <tr>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Rating</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">% PD</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">% LGD</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Cobertura Bond</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Prêmio Bond</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Perfil</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {matrix.map((row) => (
                    <MatrixEditor key={row.rating} row={row} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="mt-4 rounded-lg border-[0.5px] border-border bg-background p-4">
          <p className="text-[11px] font-medium uppercase tracking-[0.05em] text-muted-foreground">
            Histórico
          </p>
          {audit.length ? (
            <div className="mt-3 overflow-hidden rounded-md border-[0.5px] border-border">
              <table className="w-full border-collapse text-xs">
                <thead className="bg-muted text-left text-[11px] text-muted-foreground">
                  <tr>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Data</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Autor</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Campo</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Anterior → Novo</th>
                    <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">Justificativa</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((item: AuditTrailItem, index) => (
                    <tr key={item.id ?? `${item.created_at}-${index}`}>
                      <td className="border-b-[0.5px] border-border px-3 py-2 font-mono text-muted-foreground">
                        {formatDate(item.created_at)}
                      </td>
                      <td className="border-b-[0.5px] border-border px-3 py-2">
                        {item.actor_type || item.actor_id || "-"}
                      </td>
                      <td className="border-b-[0.5px] border-border px-3 py-2 font-mono">
                        {fieldFromPayload(item.payload)}
                      </td>
                      <td className="border-b-[0.5px] border-border px-3 py-2">
                        <span className="text-muted-foreground">{formatUnknown(item.previous_value)}</span>
                        <span className="mx-1 text-muted-foreground">→</span>
                        <span>{formatUnknown(item.new_value)}</span>
                      </td>
                      <td className="border-b-[0.5px] border-border px-3 py-2">
                        {item.override_reason || "-"}
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
        </section>
      </section>
    </div>
  );
}
