"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, Check, LoaderCircle, RotateCcw, X } from "lucide-react";
import { useState } from "react";

import {
  approveOperation,
  escalateOperation,
  reprocessOperationScore,
  rejectOperation,
} from "@/lib/api";
import type { OperationDetails } from "@/lib/types";
import { useAlcada } from "@/hooks/use-alcada";
import { useSession } from "@/hooks/use-session";

const buttonClassName =
  "flex h-10 items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground bg-background px-3 text-[13px] font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50";

function operationValue(operation: OperationDetails) {
  return operation.valor_solicitado ?? operation.limite_aprovado ?? null;
}

export function ApprovalActions({ operation }: { operation: OperationDetails }) {
  const queryClient = useQueryClient();
  const alcada = useAlcada();
  const { session } = useSession();
  const [mode, setMode] = useState<"reject" | "escalate" | null>(null);
  const [confirmReprocessing, setConfirmReprocessing] = useState(false);
  const [justificativa, setJustificativa] = useState("");
  const [message, setMessage] = useState("");
  const value = operationValue(operation);
  const canApprove = alcada.podeAprovar(value, operation.rating);
  const mustEscalate = alcada.precisaEscalar(value, operation.rating);
  const canDecide = operation.status === "completed";
  const scoreSnapshot = operation.components?.find(
    (component) => component.component === "score_engine",
  );
  const scoreReprocessing = scoreSnapshot?.status === "running";
  const canReprocess =
    session?.user.role === "diretor" &&
    (operation.status === "completed" || operation.status === "failed");

  const approveMutation = useMutation({
    mutationFn: () => approveOperation(operation.id),
    onSuccess: async () => {
      setMessage("Operação aprovada.");
      await queryClient.invalidateQueries({ queryKey: ["operation", operation.id] });
    },
  });
  const rejectMutation = useMutation({
    mutationFn: () => rejectOperation(operation.id, { justificativa }),
    onSuccess: async () => {
      setMessage("Operação rejeitada.");
      setMode(null);
      setJustificativa("");
      await queryClient.invalidateQueries({ queryKey: ["operation", operation.id] });
    },
  });
  const escalateMutation = useMutation({
    mutationFn: () => escalateOperation(operation.id, { justificativa }),
    onSuccess: async () => {
      setMessage("Escalada pendente.");
      setMode(null);
      setJustificativa("");
      await queryClient.invalidateQueries({ queryKey: ["operation", operation.id] });
    },
  });
  const reprocessMutation = useMutation({
    mutationFn: () => reprocessOperationScore(operation.id),
    onSuccess: async () => {
      setConfirmReprocessing(false);
      await queryClient.invalidateQueries({ queryKey: ["operation", operation.id] });
    },
  });
  const pending =
    approveMutation.isPending ||
    rejectMutation.isPending ||
    escalateMutation.isPending ||
    reprocessMutation.isPending ||
    scoreReprocessing;

  function submitInline() {
    if (mode === "reject" && justificativa.trim().length < 10) {
      setMessage("Informe uma justificativa com pelo menos 10 caracteres.");
      return;
    }
    setMessage("");
    if (mode === "reject") {
      rejectMutation.mutate();
    }
    if (mode === "escalate") {
      escalateMutation.mutate();
    }
  }

  return (
    <div className="mb-3.5 rounded-lg border-[0.5px] border-border bg-background px-4 py-3.5">
      <div className="flex flex-wrap items-center gap-2">
        {canDecide && canApprove ? (
          <button
            className={buttonClassName}
            data-testid="action-approve"
            disabled={pending}
            onClick={() => approveMutation.mutate()}
            type="button"
          >
            <Check aria-hidden="true" className="h-3.5 w-3.5" />
            Aprovar
          </button>
        ) : null}
        {canDecide ? (
          <button
            className={buttonClassName}
            data-testid="action-reject"
            disabled={pending}
            onClick={() => setMode("reject")}
            type="button"
          >
            <X aria-hidden="true" className="h-3.5 w-3.5" />
            Rejeitar
          </button>
        ) : null}
        {canDecide && mustEscalate ? (
          <button
            className={buttonClassName}
            data-testid="action-escalate"
            disabled={pending}
            onClick={() => setMode("escalate")}
            type="button"
          >
            <ArrowUp aria-hidden="true" className="h-3.5 w-3.5" />
            Escalar
          </button>
        ) : null}
        {canReprocess ? (
          <button
            className="ml-auto flex h-10 items-center justify-center gap-1.5 rounded-md border border-dashed border-border bg-muted/40 px-3 text-[13px] font-medium text-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="action-reprocess-score"
            disabled={pending}
            onClick={() => setConfirmReprocessing(true)}
            type="button"
          >
            {scoreReprocessing || reprocessMutation.isPending ? (
              <LoaderCircle aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw aria-hidden="true" className="h-3.5 w-3.5" />
            )}
            {scoreReprocessing ? "Reprocessando score" : "Reprocessar score"}
          </button>
        ) : null}
        {message === "Escalada pendente." ? (
          <span
            className="rounded bg-amber-100 px-2 py-1 text-[11px] font-medium text-amber-800"
            data-testid="action-message"
          >
            Escalada pendente
          </span>
        ) : null}
      </div>
      {confirmReprocessing ? (
        <div className="mt-3 border-l-2 border-l-muted-foreground/40 pl-3">
          <p className="text-xs text-foreground">
            Reprocessar o score atual {operation.score ?? "—"} ({operation.rating ?? "—"})
            e recalcular a taxa sugerida?
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Os demais componentes e consultas externas serão reutilizados.
          </p>
          <div className="mt-2 flex gap-2">
            <button
              className="h-8 rounded-md border border-border px-3 text-xs hover:bg-muted"
              onClick={() => setConfirmReprocessing(false)}
              type="button"
            >
              Cancelar
            </button>
            <button
              className="h-8 rounded-md border border-foreground px-3 text-xs font-medium hover:bg-muted disabled:opacity-50"
              disabled={reprocessMutation.isPending}
              onClick={() => reprocessMutation.mutate()}
              type="button"
            >
              Confirmar reprocessamento
            </button>
          </div>
        </div>
      ) : null}
      {operation.score_reprocessamento?.payload.status === "completed" &&
      operation.score_reprocessamento.previous_value &&
      operation.score_reprocessamento.new_value ? (
        <p className="mt-3 text-xs text-muted-foreground" data-testid="score-reprocess-comparison">
          Anterior: {operation.score_reprocessamento.previous_value.score ?? "—"} (
          {operation.score_reprocessamento.previous_value.rating ?? "—"}) → Atual:{" "}
          {operation.score_reprocessamento.new_value.score ?? "—"} (
          {operation.score_reprocessamento.new_value.rating ?? "—"})
        </p>
      ) : null}
      {operation.score_reprocessamento?.payload.status === "failed" ? (
        <p className="mt-3 text-xs text-red-700" role="alert">
          O último reprocessamento falhou: {operation.score_reprocessamento.payload.error}
        </p>
      ) : null}
      {reprocessMutation.isError ? (
        <p className="mt-3 text-xs text-red-700" role="alert">
          Não foi possível iniciar o reprocessamento do score.
        </p>
      ) : null}
      {mode ? (
        <div className="mt-3">
          <label className="block text-[11px] font-medium text-muted-foreground">
            <span className="mb-1 block">
              {mode === "reject" ? "Justificativa obrigatória" : "Justificativa opcional"}
            </span>
            <textarea
              className="h-16 w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
              data-testid="action-justificativa"
              onChange={(event) => setJustificativa(event.target.value)}
              placeholder="Descreva o motivo..."
              value={justificativa}
            />
          </label>
          <div className="mt-2 flex gap-2">
            <button
              className="h-8 rounded-md border-[0.5px] border-border px-3 text-xs text-foreground hover:bg-muted"
              onClick={() => setMode(null)}
              type="button"
            >
              Cancelar
            </button>
            <button
              className="h-8 rounded-md border-[0.5px] border-foreground px-3 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
              disabled={pending}
              onClick={submitInline}
              type="button"
            >
              Confirmar
            </button>
          </div>
        </div>
      ) : null}
      {message && message !== "Escalada pendente." ? (
        <p className="mt-3 text-xs text-muted-foreground" data-testid="action-message" role="status">
          {message}
        </p>
      ) : null}
      {(approveMutation.isError || rejectMutation.isError || escalateMutation.isError) ? (
        <p className="mt-3 text-xs text-red-700" data-testid="action-message" role="alert">
          Não foi possível concluir a ação.
        </p>
      ) : null}
    </div>
  );
}
