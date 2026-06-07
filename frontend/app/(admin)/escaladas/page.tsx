"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpCircle, Check, ExternalLink, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useSession } from "@/hooks/use-session";
import { getPendingEscaladas, resolveEscalation } from "@/lib/api";
import { formatTaxaAm } from "@/lib/format";
import type { EscaladaPendente, Rating, UserRole } from "@/lib/types";
import { cn } from "@/lib/utils";

const ratingColors: Record<Rating, string> = {
  A: "bg-[#EAF3DE] text-[#27500A]",
  B: "bg-[#E6F1FB] text-[#0C447C]",
  C: "bg-[#FAEEDA] text-[#633806]",
  D: "bg-[#FAECE7] text-[#712B13]",
  E: "bg-[#FCEBEB] text-[#791F1F]",
};

function formatCnpj(cnpj: string) {
  const digits = cnpj.replace(/\D/g, "");
  return digits.length === 14
    ? digits.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5")
    : cnpj;
}

function formatCurrency(value: number | null) {
  if (value === null || value === undefined) {
    return "—";
  }
  return new Intl.NumberFormat("pt-BR", {
    currency: "BRL",
    style: "currency",
  }).format(value);
}

function relativeTime(date: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(date).getTime()) / 60000));
  if (minutes < 60) {
    return `${minutes} min atrás`;
  }
  return `${Math.floor(minutes / 60)} h atrás`;
}

function cardBorder(role: UserRole | null) {
  if (role === "diretor") {
    return "rounded-l-none border-l-2 border-l-amber-500";
  }
  if (role === "gerente") {
    return "rounded-l-none border-l-2 border-l-blue-500";
  }
  return "";
}

function canResolveEscalada(role: string | undefined) {
  return role === "gerente" || role === "diretor" || role === "comite";
}

function EscaladaCard({ canResolve, item }: { canResolve: boolean; item: EscaladaPendente }) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [justificativa, setJustificativa] = useState("");
  const [error, setError] = useState("");
  const mutation = useMutation({
    mutationFn: (decision: "approved" | "rejected") =>
      resolveEscalation(item.operation_id, {
        action:
          decision === "approved"
            ? "escalation_approved"
            : "escalation_rejected",
        approval_id: item.id,
        justificativa: decision === "approved" ? "Escalada aprovada pela alçada responsável." : justificativa,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["escaladas", "pendentes"] });
    },
  });

  function reject() {
    if (justificativa.trim().length < 10) {
      setError("Informe uma justificativa com pelo menos 10 caracteres.");
      return;
    }
    setError("");
    mutation.mutate("rejected");
  }

  return (
    <article
      className={cn(
        "mb-2.5 rounded-lg border-[0.5px] border-border bg-background px-4 py-3.5",
        cardBorder(item.requested_role),
      )}
      data-testid="escalada-row"
    >
      <div className="mb-3 flex items-start justify-between gap-4">
        <div>
          <p className="mb-0.5 font-mono text-[10px] text-muted-foreground">
            {formatCnpj(item.cnpj)}
          </p>
          <p className="text-[13px] font-medium text-foreground">
            {item.razao_social || formatCnpj(item.cnpj)}
          </p>
        </div>
        {item.rating_momento ? (
          <span className={cn("rounded px-2 py-0.5 text-[11px] font-medium", ratingColors[item.rating_momento])}>
            {item.rating_momento}
          </span>
        ) : null}
      </div>
      <div className="mb-3 grid grid-cols-4 gap-2">
        <div className="rounded-md bg-muted px-2.5 py-2">
          <p className="text-[10px] text-muted-foreground">Valor</p>
          <p className="font-mono text-[13px] text-foreground">{formatCurrency(item.valor_operacao)}</p>
        </div>
        <div className="rounded-md bg-muted px-2.5 py-2">
          <p className="text-[10px] text-muted-foreground">Score</p>
          <p className="font-mono text-[13px] text-foreground">{item.score_momento ?? "—"}</p>
        </div>
        <div className="rounded-md bg-muted px-2.5 py-2">
          <p className="text-[10px] text-muted-foreground">Taxa</p>
          <p className="font-mono text-[13px] text-foreground">{formatTaxaAm(item.taxa_sugerida)}</p>
        </div>
        <div className="rounded-md bg-muted px-2.5 py-2">
          <p className="text-[10px] text-muted-foreground">Solicitante</p>
          <p className="truncate text-[13px] text-foreground">
            {item.requested_name || item.requested_by || "—"}
          </p>
        </div>
      </div>
      <p className="mb-3 text-xs text-muted-foreground">
        {item.requested_role ?? "—"} · {relativeTime(item.created_at)}
      </p>
      {item.justificativa ? (
        <p className="mb-3 text-xs leading-5 text-muted-foreground">
          <span className="font-medium text-foreground">Justificativa: </span>
          {item.justificativa}
        </p>
      ) : null}
      <div className="flex items-center justify-between gap-3">
        <Link
          className="flex items-center gap-1 text-[11px] text-blue-700 hover:underline"
          href={`/operations/${item.operation_id}`}
        >
          <ExternalLink aria-hidden="true" className="h-3 w-3" />
          ver operação
        </Link>
        {canResolve ? (
          <div className="flex gap-2">
            <button
              className="flex h-8 items-center gap-1 rounded-md border-[0.5px] border-red-200 bg-red-50 px-3 text-[11px] font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
              disabled={mutation.isPending}
              onClick={() => setRejecting(true)}
              type="button"
            >
              <X aria-hidden="true" className="h-3 w-3" />
              Rejeitar
            </button>
            <button
              className="flex h-8 items-center gap-1 rounded-md border-[0.5px] border-emerald-200 bg-emerald-50 px-3 text-[11px] font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate("approved")}
              type="button"
            >
              <Check aria-hidden="true" className="h-3 w-3" />
              Aprovar escalada
            </button>
          </div>
        ) : null}
      </div>
      {canResolve && rejecting ? (
        <div className="mt-3 rounded-md border-[0.5px] border-border bg-muted/40 p-3">
          <textarea
            className="h-16 w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
            onChange={(event) => setJustificativa(event.target.value)}
            placeholder="Motivo da rejeição..."
            value={justificativa}
          />
          {error ? <p className="mt-1 text-[11px] text-red-700">{error}</p> : null}
          <div className="mt-2 flex justify-end gap-2">
            <button className="h-8 rounded-md border-[0.5px] border-border px-3 text-xs" onClick={() => setRejecting(false)} type="button">
              Cancelar
            </button>
            <button className="h-8 rounded-md border-[0.5px] border-red-200 bg-red-50 px-3 text-xs font-medium text-red-700" onClick={reject} type="button">
              Confirmar rejeição
            </button>
          </div>
        </div>
      ) : null}
    </article>
  );
}

export default function EscaladasPage() {
  const { session } = useSession();
  const query = useQuery({
    queryFn: getPendingEscaladas,
    queryKey: ["escaladas", "pendentes"],
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  });
  const items = query.data ?? [];
  const canResolve = canResolveEscalada(session?.user.role);

  return (
    <div className="flex min-h-dvh flex-col bg-muted/40">
      <header className="border-b-[0.5px] border-border bg-background px-5 py-3.5">
        <div className="flex items-center gap-2">
          <ArrowUpCircle aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
          <h1 className="text-[15px] font-medium text-foreground">Escaladas pendentes</h1>
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {items.length} {items.length === 1 ? "escala aguardando decisão" : "escaladas aguardando decisão"}
        </p>
      </header>
      <section className="flex-1 px-5 py-4">
        {query.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Carregando escaladas...</p>
        ) : query.isError ? (
          <p className="py-10 text-center text-sm text-red-700">Não foi possível carregar escaladas.</p>
        ) : items.length === 0 ? (
          <div className="rounded-lg border-[0.5px] border-border bg-background px-4 py-12 text-center text-sm text-muted-foreground">
            Nenhuma escalada pendente.
          </div>
        ) : (
          items.map((item) => <EscaladaCard canResolve={canResolve} item={item} key={item.id} />)
        )}
      </section>
    </div>
  );
}
