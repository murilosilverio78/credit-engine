"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, Check, X } from "lucide-react";
import { useState } from "react";

import {
  approveOperation,
  escalateOperation,
  rejectOperation,
} from "@/lib/api";
import type { OperationDetails } from "@/lib/types";
import { useAlcada } from "@/hooks/use-alcada";

const buttonClassName =
  "flex h-10 items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground bg-background px-3 text-[13px] font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50";

function operationValue(operation: OperationDetails) {
  return operation.valor_solicitado ?? operation.limite_aprovado ?? null;
}

export function ApprovalActions({ operation }: { operation: OperationDetails }) {
  const queryClient = useQueryClient();
  const alcada = useAlcada();
  const [mode, setMode] = useState<"reject" | "escalate" | null>(null);
  const [justificativa, setJustificativa] = useState("");
  const [message, setMessage] = useState("");
  const value = operationValue(operation);
  const canApprove = alcada.podeAprovar(value, operation.rating);
  const mustEscalate = alcada.precisaEscalar(value, operation.rating);

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
  const pending =
    approveMutation.isPending ||
    rejectMutation.isPending ||
    escalateMutation.isPending;

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
        {canApprove ? (
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
        {mustEscalate ? (
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
        {message === "Escalada pendente." ? (
          <span
            className="rounded bg-amber-100 px-2 py-1 text-[11px] font-medium text-amber-800"
            data-testid="action-message"
          >
            Escalada pendente
          </span>
        ) : null}
      </div>
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
