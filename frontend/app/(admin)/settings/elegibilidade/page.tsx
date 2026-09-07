"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListChecks, Pencil, Save, X } from "lucide-react";
import { FormEvent, useState } from "react";

import {
  ApiError,
  getEligibilityParameters,
  updateEligibilityParameter,
} from "@/lib/api";
import type { EligibilityParameter } from "@/lib/types";

const inputClassName =
  "h-10 w-full rounded-md border border-input bg-background px-3 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring";

const practicalEffects: Record<string, string> = {
  ticket_minimo:
    "Operações abaixo deste valor enquadrado são descartadas na ingestão.",
  ticket_maximo:
    "Operações acima deste valor enquadrado são descartadas na ingestão.",
  pct_max_contrato:
    "Percentual máximo do saldo vincendo do contrato que pode ser ofertado.",
  prazo_padrao_meses:
    "Prazo usado quando a vigência restante do contrato não está disponível.",
  dias_minimos_expiracao:
    "Cotações mais próximas da expiração do que este limite são descartadas.",
  prazo_minimo_dias:
    "Prazo contratual mínimo aceito para uma nova operação.",
  cnpj_idade_minima_meses:
    "Tempo mínimo de existência do CNPJ exigido para elegibilidade.",
  watchdog_heartbeat_timeout_minutos:
    "Tempo sem heartbeat antes de uma análise em andamento ser marcada como falha.",
};

const groupLabels: Record<string, string> = {
  elegibilidade: "Política de elegibilidade",
  operacional: "Proteções operacionais",
};

const groupDescriptions: Record<string, string> = {
  elegibilidade: "Limites usados para admitir, enquadrar e definir o prazo das operações.",
  operacional: "Limites de segurança para identificar execuções interrompidas.",
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatNumber(value: number, maximumFractionDigits = 2) {
  return new Intl.NumberFormat("pt-BR", { maximumFractionDigits }).format(value);
}

function formatValue(parameter: EligibilityParameter) {
  if (parameter.unit === "BRL") {
    return new Intl.NumberFormat("pt-BR", {
      currency: "BRL",
      maximumFractionDigits: 2,
      style: "currency",
    }).format(parameter.value);
  }
  if (parameter.unit === "decimal") {
    return `${formatNumber(parameter.value * 100)}%`;
  }
  return `${formatNumber(parameter.value)} ${parameter.unit}`;
}

function formatInput(parameter: EligibilityParameter) {
  const value = parameter.unit === "decimal" ? parameter.value * 100 : parameter.value;
  return formatNumber(value, 6);
}

function parseInput(parameter: EligibilityParameter, value: string) {
  const normalized = value
    .replace(/R\$/gi, "")
    .replace(/%/g, "")
    .replace(/\s/g, "")
    .replace(/\./g, "")
    .replace(",", ".");
  const parsed = Number(normalized);
  return parameter.unit === "decimal" ? parsed / 100 : parsed;
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    return error.message;
  }
  return "Não foi possível salvar o parâmetro.";
}

function ParameterCard({ parameter }: { parameter: EligibilityParameter }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(formatInput(parameter));
  const [justificativa, setJustificativa] = useState("");
  const [error, setError] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      updateEligibilityParameter(
        parameter.key,
        parseInput(parameter, value),
        justificativa,
      ),
    onSuccess: async () => {
      setEditing(false);
      setJustificativa("");
      setError("");
      await queryClient.invalidateQueries({
        queryKey: ["eligibility", "parameters"],
      });
    },
  });

  function toggleEditing() {
    if (!editing) {
      setValue(formatInput(parameter));
      setJustificativa("");
      setError("");
    }
    setEditing((current) => !current);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = parseInput(parameter, value);
    if (!Number.isFinite(parsed)) {
      setError("Informe um valor numérico válido.");
      return;
    }
    if (justificativa.trim().length < 10) {
      setError("Informe uma justificativa com pelo menos 10 caracteres.");
      return;
    }
    setError("");
    mutation.mutate();
  }

  return (
    <article
      className="rounded-lg border-[0.5px] border-border bg-background p-4"
      data-key={parameter.key}
      data-testid="eligibility-param"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[13px] font-medium text-foreground">{parameter.label}</h3>
          <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
            {parameter.key}
          </p>
        </div>
        <button
          aria-label={editing ? `Fechar edição de ${parameter.label}` : `Editar ${parameter.label}`}
          className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border-[0.5px] border-border px-2.5 text-xs text-foreground hover:bg-muted"
          onClick={toggleEditing}
          type="button"
        >
          {editing ? <X aria-hidden="true" className="h-3.5 w-3.5" /> : <Pencil aria-hidden="true" className="h-3.5 w-3.5" />}
          {editing ? "Fechar" : "Editar"}
        </button>
      </div>

      <p className="mt-3 min-h-10 text-xs leading-5 text-muted-foreground">
        {practicalEffects[parameter.key] ?? parameter.label}
      </p>

      <div className="mt-3 border-t-[0.5px] border-border pt-3">
        <p className="text-[11px] text-muted-foreground">Valor atual</p>
        <p className="mt-0.5 font-mono text-base font-medium text-foreground">
          {formatValue(parameter)}
        </p>
        <p className="mt-1 text-[10px] text-muted-foreground">
          Atualizado em {formatDate(parameter.updated_at)}
        </p>
      </div>

      {editing ? (
        <form className="mt-4 border-t-[0.5px] border-border pt-4" onSubmit={submit}>
          <label className="block text-[11px] font-medium text-muted-foreground">
            <span className="mb-1 block">
              Novo valor {parameter.unit === "decimal" ? "(%)" : `(${parameter.unit})`}
            </span>
            <input
              className={inputClassName}
              inputMode="decimal"
              onChange={(event) => setValue(event.target.value)}
              value={value}
            />
          </label>
          <label className="mt-2 block text-[11px] font-medium text-muted-foreground">
            <span className="mb-1 block">Justificativa</span>
            <textarea
              className="h-16 w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
              data-testid="eligibility-justificativa"
              onChange={(event) => setJustificativa(event.target.value)}
              placeholder="Motivo da alteração..."
              value={justificativa}
            />
          </label>
          {error ? <p className="mt-1 text-[11px] text-red-700">{error}</p> : null}
          {mutation.isError ? (
            <p className="mt-1 text-[11px] text-red-700">
              {errorMessage(mutation.error)}
            </p>
          ) : null}
          <button
            className="mt-3 inline-flex h-10 items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground px-4 text-[13px] font-medium text-foreground hover:bg-muted disabled:opacity-50"
            data-testid="eligibility-save"
            disabled={mutation.isPending}
            type="submit"
          >
            <Save aria-hidden="true" className="h-4 w-4" />
            Salvar parâmetro
          </button>
        </form>
      ) : null}
    </article>
  );
}

export default function EligibilitySettingsPage() {
  const parametersQuery = useQuery({
    queryFn: getEligibilityParameters,
    queryKey: ["eligibility", "parameters"],
  });
  const parameters = parametersQuery.data ?? [];
  const groups = ["elegibilidade", "operacional"].filter((group) =>
    parameters.some((parameter) => parameter.grupo === group),
  );

  return (
    <div className="flex min-h-dvh flex-col bg-muted/40">
      <header className="border-b-[0.5px] border-border bg-background px-5 py-3.5">
        <div className="flex items-center gap-2">
          <ListChecks aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
          <h1 className="text-[15px] font-medium text-foreground">Elegibilidade</h1>
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Parâmetros que controlam admissão, enquadramento e continuidade da esteira.
        </p>
      </header>

      <main className="flex-1 px-5 py-4">
        <div className="mb-5 rounded-lg border-[0.5px] border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900">
          Alterações afetam novas análises imediatamente e ficam registradas na trilha de auditoria.
        </div>

        {parametersQuery.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Carregando parâmetros...</p>
        ) : parametersQuery.isError ? (
          <p className="py-10 text-center text-sm text-red-700">Não foi possível carregar parâmetros.</p>
        ) : parameters.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Nenhum parâmetro configurado.</p>
        ) : (
          <div className="space-y-6">
            {groups.map((group) => (
              <section key={group}>
                <div className="mb-3 border-b-[0.5px] border-border pb-2">
                  <h2 className="text-xs font-medium text-foreground">
                    {groupLabels[group] ?? group}
                  </h2>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {groupDescriptions[group]}
                  </p>
                </div>
                <div className="grid gap-3 lg:grid-cols-2">
                  {parameters
                    .filter((parameter) => parameter.grupo === group)
                    .map((parameter) => (
                      <ParameterCard key={parameter.key} parameter={parameter} />
                    ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
