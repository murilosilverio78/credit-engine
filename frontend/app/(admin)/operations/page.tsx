"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type KeyboardEvent, useMemo, useState } from "react";

import { getAdminOperations, getPendingOverrides } from "@/lib/api";
import { formatTaxaAm } from "@/lib/format";
import {
  type Operation,
  type OperationStatus,
  type Rating,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;

const statusOptions: OperationStatus[] = [
  "pending",
  "processing",
  "completed",
  "failed",
  "manual_review",
  "approved",
  "rejected",
  "escalated",
];
const ratingOptions: Rating[] = ["A", "B", "C", "D", "E"];

const ratingColors: Record<Rating, string> = {
  A: "bg-[#EAF3DE] text-[#27500A]",
  B: "bg-[#E6F1FB] text-[#0C447C]",
  C: "bg-[#FAEEDA] text-[#633806]",
  D: "bg-[#FAECE7] text-[#712B13]",
  E: "bg-[#FCEBEB] text-[#791F1F]",
};

const statusColors: Record<OperationStatus, string> = {
  pending: "bg-muted text-muted-foreground",
  processing: "animate-pulse bg-blue-100 text-blue-800",
  completed: "bg-blue-100 text-blue-800",
  failed: "bg-red-100 text-red-800",
  manual_review: "bg-orange-100 text-orange-800",
  approved: "bg-green-100 text-green-800",
  rejected: "bg-red-100 text-red-800",
  escalated: "bg-amber-100 text-amber-800",
};

function normalizeCnpj(cnpj: string) {
  return cnpj.replace(/\D/g, "");
}

function formatCnpj(cnpj: string) {
  const digits = normalizeCnpj(cnpj);

  if (digits.length !== 14) {
    return cnpj;
  }

  return digits.replace(
    /^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/,
    "$1.$2.$3/$4-$5",
  );
}

function formatScore(score: number | null) {
  return score === null ? "—" : score.toLocaleString("pt-BR");
}

function formatDate(date: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
  })
    .format(new Date(date))
    .replace(",", "");
}

function RatingBadge({ rating }: { rating: Rating | null }) {
  if (!rating) {
    return <span className="text-muted-foreground">—</span>;
  }

  return (
    <span
      className={cn(
        "inline-flex rounded px-2 py-0.5 text-[10px] font-medium",
        ratingColors[rating],
      )}
    >
      {rating}
    </span>
  );
}

function StatusBadge({ status }: { status: OperationStatus }) {
  return (
    <span
      className={cn(
        "inline-flex rounded px-2 py-0.5 text-[10px] font-medium",
        statusColors[status],
      )}
      data-testid="op-status-badge"
    >
      {status}
    </span>
  );
}

function MetricCard({
  label,
  value,
  subtitle,
}: {
  label: string;
  value: number | string;
  subtitle: string;
}) {
  return (
    <div className="rounded-md bg-muted px-3 py-2.5">
      <p className="mb-1 text-[10px] uppercase tracking-[0.05em] text-muted-foreground">
        {label}
      </p>
      <p className="font-mono text-xl font-medium text-foreground">{value}</p>
      <p className="mt-0.5 text-[10px] text-muted-foreground">{subtitle}</p>
    </div>
  );
}

export default function OperationsPage() {
  const router = useRouter();
  const [offset, setOffset] = useState(0);
  const [cnpjSearch, setCnpjSearch] = useState("");
  const [status, setStatus] = useState<OperationStatus | "">("");
  const [rating, setRating] = useState<Rating | "">("");

  const operationsQuery = useQuery({
    queryKey: ["operations", { limit: PAGE_SIZE, offset, status, rating }],
    queryFn: () => getAdminOperations(PAGE_SIZE, offset),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
    refetchIntervalInBackground: true,
  });
  const pendingOverridesQuery = useQuery({
    queryKey: ["overrides", "pending"],
    queryFn: getPendingOverrides,
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  });

  const operations = operationsQuery.data?.items ?? [];
  const visibleOperations = useMemo(() => {
    const search = normalizeCnpj(cnpjSearch);

    return operations.filter((operation) => {
      const matchesCnpj =
        search.length === 0 || normalizeCnpj(operation.cnpj).includes(search);
      const matchesStatus = status === "" || operation.status === status;
      const matchesRating = rating === "" || operation.rating === rating;

      return matchesCnpj && matchesStatus && matchesRating;
    });
  }, [cnpjSearch, operations, rating, status]);

  const total = operationsQuery.data?.total ?? 0;
  const completed = operations.filter(
    (operation) => operation.status === "completed",
  ).length;
  const processing = operations.filter(
    (operation) => operation.status === "processing",
  ).length;
  const pendingOverrides = pendingOverridesQuery.data?.length ?? 0;
  const hasFilters = cnpjSearch !== "" || status !== "" || rating !== "";
  const firstResult = total === 0 ? 0 : offset + 1;
  const lastResult = Math.min(offset + operations.length, total);
  const hasPreviousPage = offset > 0;
  const hasNextPage = offset + PAGE_SIZE < total;

  function resetPagination() {
    setOffset(0);
  }

  function openOperation(
    operation: Operation,
    event?: KeyboardEvent<HTMLTableRowElement>,
  ) {
    if (!event || event.key === "Enter" || event.key === " ") {
      event?.preventDefault();
      router.push(`/operations/${operation.id}`);
    }
  }

  return (
    <div className="flex min-h-dvh flex-col bg-muted/40">
      <header className="flex items-center justify-between border-b-[0.5px] border-border bg-background px-5 py-3.5">
        <div>
          <h1 className="text-[15px] font-medium text-foreground">Operações</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Histórico de análises de crédito
          </p>
        </div>
        <Link
          className="flex h-8 items-center gap-1.5 rounded-md border-[0.5px] border-border bg-background px-3.5 text-xs text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          href="/operations/new"
        >
          <Plus aria-hidden="true" className="h-3.5 w-3.5" />
          Nova análise
        </Link>
      </header>

      <section className="flex-1 p-4 px-5" aria-label="Histórico de operações">
        <div className="mb-4 grid grid-cols-4 gap-2.5">
          <MetricCard label="Total" subtitle="operações" value={total} />
          <MetricCard
            label="Concluídas"
            subtitle="com score"
            value={completed}
          />
          <MetricCard
            label="Em análise"
            subtitle="processando"
            value={processing}
          />
          <MetricCard
            label="Overrides"
            subtitle="pendente"
            value={pendingOverrides}
          />
        </div>

        <div className="mb-3 flex gap-2">
          <label className="sr-only" htmlFor="cnpj-search">
            Buscar por CNPJ
          </label>
          <input
            className="h-8 w-[180px] rounded-md border border-input bg-background px-2.5 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
            data-testid="filter-cnpj"
            id="cnpj-search"
            onChange={(event) => {
              setCnpjSearch(event.target.value);
              resetPagination();
            }}
            placeholder="Buscar por CNPJ..."
            type="search"
            value={cnpjSearch}
          />
          <label className="sr-only" htmlFor="status-filter">
            Filtrar por status
          </label>
          <select
            className="h-8 rounded-md border border-input bg-background px-2.5 text-xs text-foreground outline-none focus:border-ring focus:ring-1 focus:ring-ring"
            data-testid="filter-status"
            id="status-filter"
            onChange={(event) => {
              setStatus(event.target.value as OperationStatus | "");
              resetPagination();
            }}
            value={status}
          >
            <option value="">Todos os status</option>
            {statusOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
          <label className="sr-only" htmlFor="rating-filter">
            Filtrar por rating
          </label>
          <select
            className="h-8 rounded-md border border-input bg-background px-2.5 text-xs text-foreground outline-none focus:border-ring focus:ring-1 focus:ring-ring"
            data-testid="filter-rating"
            id="rating-filter"
            onChange={(event) => {
              setRating(event.target.value as Rating | "");
              resetPagination();
            }}
            value={rating}
          >
            <option value="">Todos os ratings</option>
            {ratingOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="overflow-hidden rounded-md border-[0.5px] border-border bg-background">
          <table className="w-full table-fixed border-collapse text-xs">
            <thead className="bg-muted">
              <tr>
                <th className="w-[145px] border-b-[0.5px] border-border px-2.5 py-2 text-left text-[11px] font-medium text-muted-foreground">
                  CNPJ
                </th>
                <th className="border-b-[0.5px] border-border px-2.5 py-2 text-left text-[11px] font-medium text-muted-foreground">
                  Razão social
                </th>
                <th className="w-[70px] border-b-[0.5px] border-border px-2.5 py-2 text-left text-[11px] font-medium text-muted-foreground">
                  Rating
                </th>
                <th className="w-[70px] border-b-[0.5px] border-border px-2.5 py-2 text-left text-[11px] font-medium text-muted-foreground">
                  Score
                </th>
                <th className="w-[115px] border-b-[0.5px] border-border px-2.5 py-2 text-left text-[11px] font-medium text-muted-foreground">
                  Taxa sugerida
                </th>
                <th className="w-[120px] border-b-[0.5px] border-border px-2.5 py-2 text-left text-[11px] font-medium text-muted-foreground">
                  Status
                </th>
                <th className="w-[105px] border-b-[0.5px] border-border px-2.5 py-2 text-left text-[11px] font-medium text-muted-foreground">
                  Data
                </th>
              </tr>
            </thead>
            <tbody className="[&>tr:last-child>td]:border-b-0">
              {operationsQuery.isLoading ? (
                <tr>
                  <td
                    className="h-28 text-center text-sm text-muted-foreground"
                    colSpan={7}
                  >
                    Carregando operações...
                  </td>
                </tr>
              ) : operationsQuery.isError ? (
                <tr>
                  <td
                    className="h-28 text-center text-sm text-red-700"
                    colSpan={7}
                  >
                    Não foi possível carregar as operações.
                  </td>
                </tr>
              ) : visibleOperations.length === 0 ? (
                <tr>
                  <td
                    className="h-28 text-center text-sm text-muted-foreground"
                    colSpan={7}
                  >
                    Nenhuma operação encontrada. Inicie uma análise.
                  </td>
                </tr>
              ) : (
                visibleOperations.map((operation) => (
                  <tr
                    aria-label={`Abrir operação ${formatCnpj(operation.cnpj)}`}
                    className="cursor-pointer focus-within:bg-muted/80 hover:bg-muted/80 focus:bg-muted/80 focus:outline-none"
                    data-op-id={operation.id}
                    data-testid="op-row"
                    key={operation.id}
                    onClick={() => openOperation(operation)}
                    onKeyDown={(event) => openOperation(operation, event)}
                    role="link"
                    tabIndex={0}
                  >
                    <td className="border-b-[0.5px] border-border px-2.5 py-2 font-mono text-[11px]">
                      {formatCnpj(operation.cnpj)}
                    </td>
                    <td className="truncate border-b-[0.5px] border-border px-2.5 py-2 text-[11px]">
                      {operation.razao_social || formatCnpj(operation.cnpj)}
                    </td>
                    <td className="border-b-[0.5px] border-border px-2.5 py-2">
                      <RatingBadge rating={operation.rating} />
                    </td>
                    <td className="border-b-[0.5px] border-border px-2.5 py-2 font-mono text-[11px]">
                      {formatScore(operation.score)}
                    </td>
                    <td className="border-b-[0.5px] border-border px-2.5 py-2 font-mono text-[11px]">
                      {formatTaxaAm(operation.taxa_sugerida)}
                    </td>
                    <td className="border-b-[0.5px] border-border px-2.5 py-2">
                      <StatusBadge status={operation.status} />
                    </td>
                    <td className="border-b-[0.5px] border-border px-2.5 py-2 font-mono text-[11px] text-muted-foreground">
                      {formatDate(operation.created_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
          <p>
            {hasFilters
              ? `${visibleOperations.length} resultado(s) nesta página de ${total} operações`
              : `Mostrando ${firstResult}–${lastResult} de ${total} operações`}
          </p>
          <div className="flex gap-1.5">
            <button
              className="rounded-md border-[0.5px] border-border bg-background px-2.5 py-1.5 text-[11px] text-foreground transition-colors enabled:hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!hasPreviousPage || operationsQuery.isFetching}
              onClick={() => setOffset((current) => current - PAGE_SIZE)}
              type="button"
            >
              ← anterior
            </button>
            <button
              className="rounded-md border-[0.5px] border-border bg-background px-2.5 py-1.5 text-[11px] text-foreground transition-colors enabled:hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!hasNextPage || operationsQuery.isFetching}
              onClick={() => setOffset((current) => current + PAGE_SIZE)}
              type="button"
            >
              próxima →
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
