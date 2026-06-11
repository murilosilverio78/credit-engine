"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, Users, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useSession } from "@/hooks/use-session";
import { reviewOverride, getPendingOverrides } from "@/lib/api";
import { formatTaxaAm } from "@/lib/format";
import type { Override, UserRole } from "@/lib/types";
import { cn } from "@/lib/utils";

function formatCnpj(cnpj: string) {
  const digits = cnpj.replace(/\D/g, "");
  return digits.length === 14
    ? digits.replace(
        /^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/,
        "$1.$2.$3/$4-$5",
      )
    : cnpj;
}

function formatDate(date: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  })
    .format(new Date(date))
    .replace(",", " às");
}

function formatValue(type: string, value: unknown): string {
  if (type === "taxa" && (typeof value === "number" || typeof value === "string")) {
    const num = typeof value === "string" ? parseFloat(value) : value;
    if (!isNaN(num)) return formatTaxaAm(num);
  }
  return String(value ?? "—");
}

function alcadaLabel(alcada: UserRole) {
  return alcada;
}

function alcadaBadge(alcada: UserRole) {
  switch (alcada) {
    case "diretor":
      return "bg-amber-100 text-amber-800";
    case "gerente":
      return "bg-blue-100 text-blue-800";
    case "analista":
      return "bg-muted text-muted-foreground";
  }
}

function cardBorder(alcada: UserRole) {
  switch (alcada) {
    case "diretor":
      return "rounded-l-none border-l-2 border-l-amber-500";
    case "gerente":
      return "rounded-l-none border-l-2 border-l-blue-500";
    case "analista":
      return "";
  }
}

function canReviewOverride(role: string | undefined) {
  return role === "gerente" || role === "diretor" || role === "comite";
}

function reviewErrorMessage(error: unknown) {
  return error instanceof Error && error.message
    ? error.message
    : "Não foi possível processar a revisão.";
}

interface PendingCardProps {
  canReview: boolean;
  override: Override;
  onReviewed: (override: Override, decision: "approved" | "rejected") => void;
}

function PendingOverrideCard({ canReview, override, onReviewed }: PendingCardProps) {
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reviewComment, setReviewComment] = useState("");
  const [commentError, setCommentError] = useState("");
  const mutation = useMutation({
    mutationFn: ({
      decision,
      comment,
    }: {
      decision: "approved" | "rejected";
      comment?: string | null;
    }) =>
      reviewOverride(override.operation_id, override.id, {
        decision,
        review_comment: comment,
      }),
    onSuccess: (_, variables) => onReviewed(override, variables.decision),
  });
  const mutationError = mutation.isError
    ? reviewErrorMessage(mutation.error)
    : "";

  function approve() {
    mutation.mutate({ comment: null, decision: "approved" });
  }

  function openReject() {
    setRejectOpen(true);
  }

  function reject() {
    if (!reviewComment.trim()) {
      setCommentError("Informe o motivo da rejeição.");
      return;
    }

    setCommentError("");
    mutation.mutate({
      comment: reviewComment.trim(),
      decision: "rejected",
    });
  }

  return (
    <article
      className={cn(
        "relative mb-2.5 rounded-lg border-[0.5px] border-border bg-background px-4 py-3.5",
        cardBorder(override.alcada_required),
      )}
      data-testid="override-row"
    >
      <div className="mb-2.5 flex items-start justify-between gap-4">
        <div>
          <p className="mb-0.5 font-mono text-[10px] text-muted-foreground">
            {formatCnpj(override.cnpj)} — operação{" "}
            {override.operation_id.slice(0, 8)}
          </p>
          <p className="text-[13px] font-medium text-foreground">
            {override.razao_social || formatCnpj(override.cnpj)}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="rounded bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
            {override.override_type}
          </span>
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-medium",
              alcadaBadge(override.alcada_required),
            )}
          >
            {override.alcada_required === "diretor" ? (
              <Users aria-hidden="true" className="h-2.5 w-2.5" />
            ) : null}
            {alcadaLabel(override.alcada_required)}
          </span>
        </div>
      </div>

      <div className="mb-2.5 flex items-center gap-2 rounded-md bg-muted px-3 py-2 font-mono text-[13px]">
        <span className="text-muted-foreground line-through">
          {formatValue(override.override_type, override.previous_value)}
        </span>
        <span className="text-[11px] text-muted-foreground">→</span>
        <span className="font-medium text-foreground">
          {formatValue(override.override_type, override.new_value)}
        </span>
      </div>

      <p className="mb-3 text-xs leading-5 text-muted-foreground">
        <span className="font-medium text-foreground">Justificativa: </span>
        {override.justificativa}
      </p>

      <footer className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <p className="text-[11px] text-muted-foreground">
            Solicitado em{" "}
            {formatDate(override.requested_at || override.created_at)}
          </p>
          <Link
            className="flex items-center gap-1 text-[11px] text-blue-700 hover:underline"
            href={`/operations/${override.operation_id}`}
          >
            <ExternalLink aria-hidden="true" className="h-3 w-3" />
            ver operação
          </Link>
        </div>
        {canReview ? (
          <div>
            <div className="flex items-center gap-2">
              <button
                className="flex h-[30px] items-center gap-1 rounded-md border-[0.5px] border-red-200 bg-red-50 px-3 text-[11px] font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
                disabled={mutation.isPending}
                onClick={openReject}
                type="button"
              >
                <X aria-hidden="true" className="h-3 w-3" />
                Rejeitar
              </button>
              <button
                className="flex h-[30px] items-center gap-1 rounded-md border-[0.5px] border-emerald-200 bg-emerald-50 px-3 text-[11px] font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
                disabled={mutation.isPending}
                onClick={approve}
                type="button"
              >
                <Check aria-hidden="true" className="h-3 w-3" />
                Aprovar
              </button>
            </div>
          </div>
        ) : null}
      </footer>

      {canReview && mutationError && !rejectOpen ? (
        <p className="mt-3 text-xs text-red-700" role="alert">
          {mutationError}
        </p>
      ) : null}

      {canReview && rejectOpen ? (
        <div
          aria-label="Confirmar rejeição"
          className="absolute inset-0 flex items-center justify-center rounded-lg bg-background/95 p-4"
          role="dialog"
        >
          <div className="w-full max-w-md rounded-lg border-[0.5px] border-border bg-background p-4">
            <h3 className="mb-1 text-sm font-medium text-foreground">
              Rejeitar override
            </h3>
            <p className="mb-3 text-xs text-muted-foreground">
              Informe o comentário da revisão para registrar a rejeição.
            </p>
            <textarea
              className="h-20 w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
              onChange={(event) => {
                setReviewComment(event.target.value);
                if (event.target.value.trim()) {
                  setCommentError("");
                }
              }}
              placeholder="Motivo da rejeição..."
              value={reviewComment}
            />
            {commentError ? (
              <p className="mt-1 text-[11px] text-red-700" role="alert">
                {commentError}
              </p>
            ) : null}
            <div className="mt-3 flex justify-end gap-2">
              <button
                className="h-8 rounded-md border-[0.5px] border-border px-3 text-xs text-foreground hover:bg-muted"
                onClick={() => {
                  setRejectOpen(false);
                  setCommentError("");
                }}
                type="button"
              >
                Cancelar
              </button>
              <button
                className="h-8 rounded-md border-[0.5px] border-red-200 bg-red-50 px-3 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
                disabled={mutation.isPending}
                onClick={reject}
                type="button"
              >
                Confirmar rejeição
              </button>
            </div>
            {mutationError ? (
              <p className="mt-3 text-xs text-red-700" role="alert">
                {mutationError}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </article>
  );
}

export default function OverridesPage() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const { session } = useSession();
  const [toast, setToast] = useState("");
  const overridesQuery = useQuery({
    queryFn: getPendingOverrides,
    queryKey: ["overrides", "pending"],
    refetchInterval: 20_000,
    refetchIntervalInBackground: true,
  });
  const pending = overridesQuery.data ?? [];
  const canReview = canReviewOverride(session?.user.role);

  useEffect(() => {
    if (!toast) {
      return;
    }

    const timeout = window.setTimeout(() => setToast(""), 4000);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  function handleReviewed(
    reviewedOverride: Override,
    decision: "approved" | "rejected",
  ) {
    queryClient.setQueryData<Override[]>(
      ["overrides", "pending"],
      (items = []) =>
        items.filter((item) => item.id !== reviewedOverride.id),
    );
    void queryClient.invalidateQueries({ queryKey: ["overrides", "pending"] });
    router.refresh();
    setToast(
      decision === "approved"
        ? "Override aprovado com sucesso."
        : "Override rejeitado com sucesso.",
    );
  }

  return (
    <div className="relative flex min-h-dvh flex-col bg-muted/40">
      <header className="border-b-[0.5px] border-border bg-background px-5 py-3.5">
        <h1 className="text-[15px] font-medium text-foreground">
          Overrides pendentes
        </h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {pending.length}{" "}
          {pending.length === 1
            ? "override aguardando revisão"
            : "overrides aguardando revisão"}
        </p>
      </header>
      <section className="flex-1 px-5 py-4">
        {overridesQuery.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            Carregando overrides...
          </p>
        ) : overridesQuery.isError ? (
          <p className="py-10 text-center text-sm text-red-700">
            Não foi possível carregar os overrides pendentes.
          </p>
        ) : pending.length === 0 ? (
          <div className="rounded-lg border-[0.5px] border-border bg-background px-4 py-12 text-center text-sm text-muted-foreground">
            Nenhum override pendente.
          </div>
        ) : (
          pending.map((override) => (
            <PendingOverrideCard
              canReview={canReview}
              key={override.id}
              onReviewed={handleReviewed}
              override={override}
            />
          ))
        )}
      </section>
      {toast ? (
        <div
          aria-live="polite"
          className="fixed bottom-5 right-5 rounded-md border-[0.5px] border-emerald-200 bg-background px-4 py-3 text-xs text-emerald-700"
          role="status"
        >
          {toast}
        </div>
      ) : null}
    </div>
  );
}
