"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  ExternalLink,
  FileUp,
  LoaderCircle,
  Play,
  Trash2,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { ApprovalActions } from "@/components/approval-actions";
import { useSession } from "@/hooks/use-session";
import {
  ApiError,
  createOverride,
  getComponents,
  getOperation,
  getOperationOverrides,
  getOperationUploads,
  removeCertificateUpload,
  resumeAfterUploads,
  uploadCertificate,
  validateTaxaOverride,
} from "@/lib/api";
import { formatTaxaAm } from "@/lib/format";
import type {
  Component,
  ComponentSnapshot,
  OperationDetails,
  Rating,
  UploadDocumentType,
  UploadTask,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const ratingColors: Record<Rating, string> = {
  A: "bg-[#EAF3DE] text-[#27500A]",
  B: "bg-[#E6F1FB] text-[#0C447C]",
  C: "bg-[#FAEEDA] text-[#633806]",
  D: "bg-[#FAECE7] text-[#712B13]",
  E: "bg-[#FCEBEB] text-[#791F1F]",
};

const certificateDetails: Record<
  UploadDocumentType,
  { description: string; href: string; label: string }
> = {
  cndt_tst: {
    description: "Tribunal Superior do Trabalho",
    href: "https://cndt-certidao.tst.jus.br/inicio.faces",
    label: "CNDT - Certidão Negativa de Débitos Trabalhistas",
  },
  cnd_federal: {
    description: "Receita Federal + PGFN",
    href: "https://servicos.receitafederal.gov.br/servico/certidoes/#/home/cnpj",
    label: "CND Federal - Certidão Negativa de Débitos",
  },
  fgts: {
    description: "Caixa Econômica Federal",
    href: "https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf",
    label: "CRF FGTS - Certificado de Regularidade",
  },
};

const overrideSchema = z.object({
  override_type: z.literal("taxa"),
  previous_value: z.string().min(1, "Informe o valor anterior."),
  taxa_proposta: z.string().min(1, "Informe a taxa proposta."),
  justificativa: z
    .string()
    .min(10, "A justificativa deve conter pelo menos 10 caracteres."),
});

type OverrideFormValues = z.infer<typeof overrideSchema>;

interface PipelineComponent {
  config?: Component;
  name: string;
  snapshot?: ComponentSnapshot;
}

type ParsedResult = Record<string, unknown>;

const fieldClassName =
  "h-9 w-full rounded-md border border-input bg-background px-2.5 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring";

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
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
  })
    .format(new Date(date))
    .replace(",", "");
}

function formatElapsed(createdAt: string, now: number) {
  const elapsedSeconds = Math.max(
    0,
    Math.floor((now - new Date(createdAt).getTime()) / 1000),
  );
  const minutes = String(Math.floor(elapsedSeconds / 60)).padStart(2, "0");
  const seconds = String(elapsedSeconds % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function formatDuration(durationMs: number | null | undefined) {
  if (durationMs === null || durationMs === undefined) {
    return "—";
  }

  return `${(durationMs / 1000).toLocaleString("pt-BR", {
    maximumFractionDigits: 1,
  })}s`;
}

function formatValue(value: unknown, type?: string): string {
  if (value === null || value === undefined || value === "") return "—";
  if (type === "taxa" && (typeof value === "number" || typeof value === "string")) {
    const num = typeof value === "string" ? parseFloat(value) : value;
    if (!isNaN(num)) return formatTaxaAm(num);
  }
  return String(value);
}

function taxaPercentInput(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "";
  }
  return (value * 100).toFixed(2);
}

function parseTaxaPercent(value: string) {
  const normalized = value.replace(",", ".");
  const numeric = Number(normalized);
  return Number.isFinite(numeric) && numeric > 0 ? numeric / 100 : null;
}

function formatTaxaPercent(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "";
  }
  return (value * 100).toLocaleString("pt-BR", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
}

function asParsedResult(value: unknown): ParsedResult {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as ParsedResult)
    : {};
}

function certificateStatus(component: PipelineComponent) {
  const result = asParsedResult(component.snapshot?.parsed_result);
  if (result.status !== "obtida") {
    return null;
  }

  switch (result.resultado) {
    case "negativa":
      return {
        icon: <Check aria-hidden="true" className="h-3 w-3" />,
        label: "certidão negativa",
        style: "text-emerald-700",
      };
    case "positiva_com_efeitos_negativa":
      return {
        icon: <AlertTriangle aria-hidden="true" className="h-3 w-3" />,
        label: "positiva com efeitos",
        style: "text-amber-700",
      };
    case "positiva":
      return {
        icon: <XCircle aria-hidden="true" className="h-3 w-3" />,
        label: "certidão positiva",
        style: "text-red-700",
      };
    default:
      return {
        icon: <FileUp aria-hidden="true" className="h-3 w-3" />,
        label: "recebida",
        style: "text-muted-foreground",
      };
  }
}

function currentTaxaValue(operation: OperationDetails) {
  return operation.taxa_sugerida?.toString() ?? "";
}

function pipelineComponents(
  operation: OperationDetails,
  components: Component[] | undefined,
) {
  const snapshots = operation.components ?? [];
  const snapshotsByName = new Map(
    snapshots.map((snapshot) => [snapshot.component, snapshot]),
  );
  const names = new Set([
    ...snapshots.map((snapshot) => snapshot.component),
    ...(components ?? []).map((component) => component.component),
  ]);

  return Array.from(names).map((name) => ({
    config: components?.find((component) => component.component === name),
    name,
    snapshot: snapshotsByName.get(name),
  }));
}

function isManual(component: PipelineComponent) {
  return (
    component.snapshot?.status === "waiting_upload" ||
    (component.config?.enabled && component.config.timeout_seconds === 0)
  );
}

function isRoadmap(component: PipelineComponent) {
  return component.config?.enabled === false;
}

function isFailed(component: PipelineComponent) {
  return ["failed", "error"].includes(component.snapshot?.status ?? "");
}

function isDone(component: PipelineComponent) {
  return ["completed", "ok", "success"].includes(
    component.snapshot?.status ?? "",
  );
}

function isRunning(component: PipelineComponent) {
  return ["processing", "running", "started"].includes(
    component.snapshot?.status ?? "",
  );
}

function ErrorText({ message }: { message?: string }) {
  return message ? (
    <p className="mt-1 text-[11px] text-red-700" role="alert">
      {message}
    </p>
  ) : null;
}

function Topbar({
  operation,
}: {
  operation: OperationDetails;
}) {
  const processing =
    operation.status === "pending" || operation.status === "processing";
  const manualReview = operation.status === "manual_review";

  return (
    <header className="flex items-center gap-2 border-b-[0.5px] border-border bg-background px-5 py-3">
      <Link
        className="flex h-7 items-center gap-1 rounded-md border-[0.5px] border-border px-2 text-xs text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        href="/operations"
      >
        <ArrowLeft aria-hidden="true" className="h-3 w-3" />
        voltar
      </Link>
      <span className="font-mono text-sm font-medium text-foreground">
        {operation.id.slice(0, 12)}…
      </span>
      <span
        className={cn(
          "rounded px-2 py-0.5 text-[10px] font-medium",
          processing
            ? "animate-pulse bg-blue-100 text-blue-800"
            : manualReview
              ? "bg-amber-100 text-amber-800"
            : operation.status === "failed"
              ? "bg-red-100 text-red-800"
              : "bg-emerald-100 text-emerald-800",
        )}
        data-testid="detail-status"
      >
        {manualReview ? "aguardando certidões" : operation.status}
      </span>
    </header>
  );
}

function ProcessingView({
  operation,
  pipeline,
  refreshIn,
}: {
  operation: OperationDetails;
  pipeline: PipelineComponent[];
  refreshIn: number;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, []);

  return (
    <section className="flex-1 px-5 py-4">
      <div className="mb-4 flex items-center justify-between rounded-lg border-[0.5px] border-border bg-background px-4 py-3.5">
        <div>
          <p className="mb-0.5 font-mono text-[10px] text-muted-foreground">
            {formatCnpj(operation.cnpj)}
          </p>
          <p className="text-sm font-medium text-foreground">
            {operation.razao_social || formatCnpj(operation.cnpj)}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Score e rating disponíveis ao concluir
          </p>
        </div>
        <div className="text-right">
          <p className="mb-1 animate-pulse text-[11px] text-blue-700">
            em análise
          </p>
          <p className="font-mono text-xl font-medium text-foreground">
            {formatElapsed(operation.created_at, now)}
          </p>
          <p className="mt-0.5 text-[10px] text-muted-foreground">
            atualiza em {refreshIn}s
          </p>
        </div>
      </div>

      <h2 className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-muted-foreground">
        Pipeline — progresso em tempo real
      </h2>
      <div className="flex flex-col gap-1">
        {pipeline.map((component) => {
          const done = isDone(component);
          const running = isRunning(component);

          return (
            <div
              className={cn(
                "flex items-center gap-2.5 rounded-md border-[0.5px] border-border bg-background px-3 py-2",
                done && "rounded-l-none border-l-2 border-l-emerald-500",
                running && "rounded-l-none border-l-2 border-l-blue-500",
                !done && !running && "opacity-50",
              )}
              data-component={component.name}
              data-testid="snapshot-row"
              key={component.name}
            >
              <p className="w-[150px] font-mono text-xs font-medium text-foreground">
                {component.name}
              </p>
              <p className="flex-1 text-[11px] text-muted-foreground">
                {component.config?.description || "Componente da análise"}
              </p>
              <p
                className={cn(
                  "w-[55px] text-right font-mono text-[11px] text-muted-foreground",
                  running && "animate-pulse",
                )}
              >
                {running
                  ? `${formatDuration(component.snapshot?.duration_ms)}…`
                  : formatDuration(component.snapshot?.duration_ms)}
              </p>
              <p
                className={cn(
                  "flex w-[80px] items-center justify-end gap-1 text-[11px]",
                  done && "text-emerald-700",
                  running && "animate-pulse text-blue-700",
                  !done && !running && "text-muted-foreground",
                )}
              >
                {done ? <Check className="h-3 w-3" /> : null}
                {running ? <LoaderCircle className="h-3 w-3 animate-spin" /> : null}
                {done ? "ok" : running ? "rodando" : "aguarda"}
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ManualReviewView({
  operation,
  operationId,
  onRefresh,
}: {
  operation: OperationDetails;
  operationId: string;
  onRefresh: () => Promise<unknown>;
}) {
  const queryClient = useQueryClient();
  const [filenames, setFilenames] = useState<Record<string, string>>({});
  const [resumeRequested, setResumeRequested] = useState(false);
  const uploadsQuery = useQuery({
    queryFn: () => getOperationUploads(operationId),
    queryKey: ["operations", operationId, "uploads"],
    refetchInterval: 5_000,
  });
  const uploadMutation = useMutation({
    mutationFn: ({
      file,
      task,
    }: {
      file: File;
      task: UploadTask;
    }) => uploadCertificate(task.token, task.document_type, file),
    onSuccess: async (_, { file, task }) => {
      setFilenames((current) => ({ ...current, [task.id]: file.name }));
      await queryClient.invalidateQueries({
        queryKey: ["operations", operationId, "uploads"],
      });
    },
  });
  const removeMutation = useMutation({
    mutationFn: (task: UploadTask) => removeCertificateUpload(task.token),
    onSuccess: async (_, task) => {
      setFilenames((current) => {
        const next = { ...current };
        delete next[task.id];
        return next;
      });
      await queryClient.invalidateQueries({
        queryKey: ["operations", operationId, "uploads"],
      });
    },
  });
  const resumeMutation = useMutation({
    mutationFn: () => resumeAfterUploads(operationId),
    onSuccess: async () => {
      setResumeRequested(true);
      await onRefresh();
    },
  });
  const uploads = uploadsQuery.data ?? [];
  const completeCount = uploads.filter((upload) => upload.status === "completed").length;
  const allCompleted =
    uploads.length > 0 && uploads.every((upload) => upload.status === "completed");
  const progress = uploads.length ? (completeCount / uploads.length) * 100 : 0;

  return (
    <section className="flex-1 px-5 py-4">
      <div className="mb-3.5 flex items-center justify-between rounded-lg border-[0.5px] border-border bg-background px-4 py-3.5">
        <div>
          <p className="mb-0.5 font-mono text-[10px] text-muted-foreground">
            {formatCnpj(operation.cnpj)}
          </p>
          <p className="text-sm font-medium text-foreground">
            {operation.razao_social || formatCnpj(operation.cnpj)}
          </p>
          <p className="mt-1 flex items-center gap-1 text-[11px] text-amber-700">
            <AlertTriangle aria-hidden="true" className="h-3 w-3" />
            Pipeline pausado - faça upload das certidões para continuar
          </p>
        </div>
        <div className="text-right">
          <p className="mb-1 text-[10px] text-muted-foreground">progresso</p>
          <p className="font-mono text-lg font-medium text-foreground">
            {completeCount}
            <span className="text-xs font-normal text-muted-foreground">
              /{uploads.length || 3}
            </span>
          </p>
        </div>
      </div>

      <div className="mb-3.5 h-[3px] overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-emerald-500 transition-[width]"
          style={{ width: `${progress}%` }}
        />
      </div>

      <h2 className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-muted-foreground">
        Certidões obrigatórias
      </h2>
      <div className="mb-3.5 rounded-lg border-[0.5px] border-border bg-background px-4 py-3.5">
        {uploadsQuery.isLoading ? (
          <p className="text-xs text-muted-foreground">Carregando certidões...</p>
        ) : uploads.length ? (
          <div className="flex flex-col gap-2.5">
            {uploads.map((task) => {
              const completed = task.status === "completed";
              const failed = task.status === "failed";
              const details = certificateDetails[task.document_type];
              const uploading =
                uploadMutation.isPending &&
                uploadMutation.variables?.task.id === task.id;
              const removing =
                removeMutation.isPending &&
                removeMutation.variables?.id === task.id;

              return (
                <div
                  className={cn(
                    "flex items-center gap-3 rounded-md border-[0.5px] border-border bg-muted/40 px-3 py-2.5",
                    completed &&
                      "rounded-l-none border-l-2 border-l-emerald-500 bg-background",
                    failed &&
                      "rounded-l-none border-l-2 border-l-red-500 bg-background",
                    !completed &&
                      !failed &&
                      "rounded-l-none border-l-2 border-l-amber-500",
                  )}
                  key={task.id}
                >
                  <div
                    className={cn(
                      "flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground",
                      completed && "bg-emerald-100 text-emerald-700",
                      failed && "bg-red-100 text-red-700",
                    )}
                  >
                    {completed ? (
                      <Check className="h-4 w-4" />
                    ) : failed ? (
                      <XCircle className="h-4 w-4" />
                    ) : (
                      <FileUp className="h-4 w-4" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium text-foreground">
                      {details.label}
                    </p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">
                      {details.description}
                    </p>
                    <a
                      className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-blue-700 no-underline hover:text-blue-800 focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      href={details.href}
                      rel="noopener noreferrer"
                      target="_blank"
                    >
                      emitir certidão
                      <ExternalLink aria-hidden="true" className="h-3 w-3" />
                    </a>
                    <p
                      className={cn(
                        "mt-0.5 text-[10px]",
                        completed
                          ? "font-mono text-emerald-700"
                          : failed
                            ? "font-mono text-red-700"
                            : "text-amber-700",
                      )}
                    >
                      {completed || failed
                        ? filenames[task.id] || "PDF enviado"
                        : "aguardando upload"}
                    </p>
                    {failed ? (
                      <p className="mt-0.5 text-[11px] text-red-700">
                        {task.error_message ||
                          "CNPJ da certidão não corresponde ao CNPJ da operação"}
                      </p>
                    ) : null}
                  </div>
                  {completed || failed ? (
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          "flex h-7 items-center gap-1 rounded-md border px-3 text-[11px]",
                          completed
                            ? "border-emerald-200 text-emerald-700"
                            : "border-red-200 text-red-700",
                        )}
                      >
                        {completed ? (
                          <Check className="h-3 w-3" />
                        ) : (
                          <XCircle className="h-3 w-3" />
                        )}
                        {completed ? "enviado" : "CNPJ inválido"}
                      </span>
                      <button
                        className="flex h-7 items-center gap-1 rounded-md border border-red-200 px-3 text-[11px] text-red-700 hover:bg-red-50 disabled:opacity-50"
                        disabled={removing || resumeRequested}
                        onClick={() => removeMutation.mutate(task)}
                        type="button"
                      >
                        <Trash2 className="h-3 w-3" />
                        {removing ? "removendo..." : "remover"}
                      </button>
                    </div>
                  ) : (
                    <label
                      className={cn(
                        "flex h-7 cursor-pointer items-center gap-1 rounded-md border border-border bg-background px-3 text-[11px] text-foreground hover:bg-muted",
                        uploading && "pointer-events-none opacity-50",
                      )}
                    >
                      <FileUp className="h-3 w-3" />
                      {uploading ? "enviando..." : "selecionar PDF"}
                      <input
                        accept=".pdf,application/pdf"
                        className="sr-only"
                        disabled={uploading}
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          if (file) {
                            uploadMutation.mutate({ file, task });
                          }
                          event.target.value = "";
                        }}
                        type="file"
                      />
                    </label>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            Nenhuma certidão manual solicitada para esta operação.
          </p>
        )}
        {uploadMutation.isError ? (
          <p className="mt-3 text-xs text-red-700" role="alert">
            {uploadMutation.error instanceof ApiError && uploadMutation.error.message
              ? uploadMutation.error.message
              : "Nao foi possivel enviar o PDF. Verifique o arquivo e tente novamente."}
          </p>
        ) : null}
        {removeMutation.isError ? (
          <p className="mt-3 text-xs text-red-700" role="alert">
            Não foi possível remover o PDF. Tente novamente.
          </p>
        ) : null}
      </div>

      <h2 className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-muted-foreground">
        Retomar pipeline
      </h2>
      <div className="rounded-lg border-[0.5px] border-border bg-background px-4 py-3.5">
        <p className="mb-3 text-xs leading-5 text-muted-foreground">
          Após conferir todas as certidões, clique em Retomar análise para continuar com{" "}
          <span className="font-medium text-foreground">
            web_research -&gt; score_engine
          </span>
          .
        </p>
        <button
          className={cn(
            "flex h-8 items-center gap-1.5 rounded-md border px-4 text-xs font-medium opacity-40",
            allCompleted &&
              !resumeRequested &&
              "border-emerald-200 text-emerald-700 opacity-100 hover:bg-emerald-50",
          )}
          disabled={!allCompleted || resumeRequested || resumeMutation.isPending}
          onClick={() => resumeMutation.mutate()}
          type="button"
        >
          <Play className="h-3 w-3" />
          {resumeMutation.isPending
            ? "Retomando..."
            : resumeRequested
              ? "Análise retomada"
              : "Retomar análise"}
        </button>
        {resumeMutation.isError ? (
          <p className="mt-3 text-xs text-red-700" role="alert">
            Não foi possível retomar a análise. Verifique as certidões enviadas.
          </p>
        ) : null}
      </div>
    </section>
  );
}

function CompletedView({
  operation,
  pipeline,
  operationId,
}: {
  operation: OperationDetails;
  pipeline: PipelineComponent[];
  operationId: string;
}) {
  const queryClient = useQueryClient();
  const { session } = useSession();
  const [confirmation, setConfirmation] = useState("");
  const {
    formState: { errors },
    handleSubmit,
    register,
    reset,
    setValue,
  } = useForm<OverrideFormValues>({
    defaultValues: {
      justificativa: "",
      override_type: "taxa",
      previous_value: currentTaxaValue(operation),
      taxa_proposta: "",
    },
    resolver: zodResolver(overrideSchema),
  });
  const currentUserRole = session?.user.role ?? "analista";
  const overridesQuery = useQuery({
    queryFn: () => getOperationOverrides(operationId),
    queryKey: ["operations", operationId, "overrides"],
  });
  const taxaValidationMutation = useMutation({
    mutationFn: (taxaProposta: number) =>
      validateTaxaOverride(operationId, taxaProposta, currentUserRole),
  });
  const overrideMutation = useMutation({
    mutationFn: (values: OverrideFormValues) =>
      createOverride(operationId, {
        justificativa: values.justificativa,
        new_value: parseTaxaPercent(values.taxa_proposta) ?? values.taxa_proposta,
        override_type: "taxa",
        previous_value: currentTaxaValue(operation),
      }),
    onSuccess: async () => {
      setConfirmation("Override solicitado com sucesso.");
      reset({
        justificativa: "",
        override_type: "taxa",
        previous_value: currentTaxaValue(operation),
        taxa_proposta: "",
      });
      taxaValidationMutation.reset();
      await queryClient.invalidateQueries({
        queryKey: ["operations", operationId, "overrides"],
      });
    },
  });

  useEffect(() => {
    setValue("previous_value", currentTaxaValue(operation));
  }, [operation, setValue]);

  const decisionBadge =
    operation.status === "approved"
      ? { label: "Aprovada", style: "bg-green-100 text-green-800" }
      : operation.status === "rejected"
        ? { label: "Rejeitada", style: "bg-red-100 text-red-700" }
        : operation.status === "escalated"
          ? { label: "Escalada — aguardando decisão", style: "bg-amber-100 text-amber-800" }
          : null;

  return (
    <section className="flex-1 px-5 py-4">
      <div className="mb-3.5 rounded-lg border-[0.5px] border-border bg-background px-4 py-3.5">
        <div className="mb-3 flex items-start justify-between">
          <div>
            <p className="mb-0.5 font-mono text-[11px] text-muted-foreground">
              {formatCnpj(operation.cnpj)}
            </p>
            <p className="text-sm font-medium text-foreground">
              {operation.razao_social || formatCnpj(operation.cnpj)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {operation.status === "completed" ? (
              <Link
                className="flex h-8 items-center gap-1 rounded-md border border-border px-3 text-xs text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                href={`/operations/${operationId}/report`}
              >
                Ver relatório completo
                <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
              </Link>
            ) : null}
            {operation.rating ? (
              <span
                className={cn(
                  "rounded px-3.5 py-1 text-[13px] font-medium",
                  ratingColors[operation.rating],
                )}
                data-testid="detail-rating"
              >
                {operation.rating}
              </span>
            ) : null}
          </div>
        </div>
        <div className="grid grid-cols-4 gap-2">
          <DetailMetric
            label="Score"
            testId="detail-score"
            value={
              <>
                {operation.score ?? "—"}
                <span className="ml-0.5 text-xs font-normal text-muted-foreground">
                  /100
                </span>
              </>
            }
          />
          <DetailMetric
            label="Taxa sugerida"
            testId="detail-taxa"
            value={
              <>
                {formatTaxaAm(operation.taxa_sugerida)}
              </>
            }
          />
          <DetailMetric
            label="Limite aprovado"
            testId="detail-limite"
            value={
              operation.limite_aprovado === null ||
              operation.limite_aprovado === undefined
                ? "—"
                : operation.limite_aprovado.toLocaleString("pt-BR", {
                    currency: "BRL",
                    style: "currency",
                  })
            }
          />
          <DetailMetric label="Fonte" small value={operation.source} />
          <DetailMetric
            label="Criada em"
            small
            value={formatDate(operation.created_at)}
          />
        </div>
      </div>

      {/* Dados da Proposta — verificação de coerência da taxa */}
      <div className="mb-4 rounded-lg border bg-card p-4">
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Dados da Proposta
        </h3>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div>
            <p className="text-xs text-muted-foreground">Valor Solicitado</p>
            <p className="text-sm font-medium">
              {operation.valor_solicitado != null
                ? operation.valor_solicitado.toLocaleString("pt-BR", {
                    currency: "BRL",
                    style: "currency",
                  })
                : "—"}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Prazo</p>
            <p className="text-sm font-medium">
              {operation.prazo_dias != null ? `${operation.prazo_dias} dias` : "—"}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Saldo do Contrato</p>
            <p className="text-sm font-medium">
              {operation.contrato_saldo != null
                ? operation.contrato_saldo.toLocaleString("pt-BR", {
                    currency: "BRL",
                    style: "currency",
                  })
                : "—"}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Taxa Sugerida</p>
            <p className="text-sm font-medium">
              {operation.taxa_sugerida != null
                ? formatTaxaAm(operation.taxa_sugerida)
                : operation.pricing_skipped_reason
                  ? `N/A — ${operation.pricing_skipped_reason}`
                  : "—"}
            </p>
          </div>
        </div>
      </div>

      {operation.status === "completed" ? (
        <ApprovalActions operation={operation} />
      ) : decisionBadge ? (
        <div className="mb-3.5 rounded-lg border-[0.5px] border-border bg-background px-4 py-3.5">
          <span
            className={cn(
              "inline-flex rounded px-2.5 py-1 text-[11px] font-medium",
              decisionBadge.style,
            )}
          >
            {decisionBadge.label}
          </span>
        </div>
      ) : null}

      <h2 className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-muted-foreground">
        Pipeline de componentes
      </h2>
      <div className="mb-3.5 grid grid-cols-4 gap-2">
        {pipeline.map((component) => {
          const failed = isFailed(component);
          const manual = isManual(component);
          const roadmap = isRoadmap(component);
          const done = isDone(component) && !manual && !roadmap;
          const certificate = certificateStatus(component);

          return (
            <div
              className={cn(
                "rounded-md border-[0.5px] border-border bg-background p-2.5",
                done && "rounded-l-none border-l-2 border-l-emerald-500",
                certificate?.style === "text-emerald-700" &&
                  "rounded-l-none border-l-2 border-l-emerald-500",
                certificate?.style === "text-amber-700" &&
                  "rounded-l-none border-l-2 border-l-amber-500",
                certificate?.style === "text-red-700" &&
                  "rounded-l-none border-l-2 border-l-red-500",
                failed && "rounded-l-none border-l-2 border-l-red-500",
                manual && !certificate && "rounded-l-none border-l-2 border-l-amber-500",
                roadmap && "opacity-50",
              )}
              data-component={component.name}
              data-testid="snapshot-row"
              key={component.name}
            >
              <p className="mb-1 text-[11px] font-medium text-foreground">
                {component.name}
              </p>
              <p
                className={cn(
                  "flex items-center gap-1 text-[10px] text-muted-foreground",
                  done && "text-emerald-700",
                  manual && !certificate && "text-amber-700",
                  failed && "text-red-700",
                  certificate?.style,
                )}
              >
                {failed ? (
                  <XCircle aria-hidden="true" className="h-3 w-3" />
                ) : (
                  certificate?.icon ?? (done ? <Check className="h-3 w-3" /> : null)
                )}
                {failed
                  ? component.snapshot?.error_message || "falha"
                  : certificate
                    ? certificate.label
                  : manual
                    ? "certidão não enviada"
                    : roadmap
                      ? "roadmap"
                      : `ok — ${formatDuration(component.snapshot?.duration_ms)}`}
              </p>
            </div>
          );
        })}
      </div>

      <h2 className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-muted-foreground">
        Solicitar override
      </h2>
      <form
        className="mb-3.5 rounded-lg border-[0.5px] border-border bg-background px-4 py-3.5"
        onSubmit={handleSubmit((values) => {
          setConfirmation("");
          overrideMutation.mutate(values);
        })}
      >
        <input type="hidden" {...register("override_type")} />
        <input type="hidden" {...register("previous_value")} />
        <div className="mb-2.5 grid grid-cols-2 gap-2.5">
          <OverrideField
            label="Taxa sugerida atual"
            message={errors.previous_value?.message}
          >
            <input
              className={cn(fieldClassName, "font-mono")}
              readOnly
              value={`${taxaPercentInput(operation.taxa_sugerida)}% a.m.`}
            />
          </OverrideField>
          <OverrideField label="Taxa proposta (% a.m.)" message={errors.taxa_proposta?.message}>
            <input
              className={cn(fieldClassName, "font-mono")}
              inputMode="decimal"
              placeholder="Ex: 2,15"
              step="0.01"
              type="number"
              {...register("taxa_proposta")}
              onBlur={(event) => {
                const taxaProposta = parseTaxaPercent(event.target.value);
                if (taxaProposta) {
                  taxaValidationMutation.mutate(taxaProposta);
                } else {
                  taxaValidationMutation.reset();
                }
              }}
            />
            {taxaValidationMutation.isPending ? (
              <p className="mt-1 text-[11px] text-muted-foreground">
                Verificando...
              </p>
            ) : taxaValidationMutation.data?.approved ? (
              <p className="mt-1 text-[11px] text-emerald-700">
                ✓ Aprovável na sua alçada
              </p>
            ) : taxaValidationMutation.data ? (
              <p className="mt-1 text-[11px] text-amber-700">
                Taxa mínima para sua alçada:{" "}
                {formatTaxaPercent(taxaValidationMutation.data.taxa_minima_sua_alcada)}
                % a.m.
              </p>
            ) : taxaValidationMutation.isError ? (
              <p className="mt-1 text-[11px] text-red-700">
                Não foi possível validar a taxa.
              </p>
            ) : null}
          </OverrideField>
        </div>
        <OverrideField
          label="Justificativa"
          message={errors.justificativa?.message}
        >
          <textarea
            className="h-14 w-full resize-none rounded-md border border-input bg-background px-2.5 py-2 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring"
            placeholder="Descreva o motivo do override (mínimo 10 caracteres)..."
            {...register("justificativa")}
          />
        </OverrideField>
        {confirmation ? (
          <p className="mt-3 text-xs text-emerald-700" role="status">
            {confirmation}
          </p>
        ) : null}
        {overrideMutation.isError ? (
          <p className="mt-3 text-xs text-red-700" role="alert">
            Não foi possível solicitar o override.
          </p>
        ) : null}
        <button
          className="mt-3 flex h-8 items-center gap-1.5 rounded-md border-[0.5px] border-border px-3.5 text-xs text-foreground hover:bg-muted disabled:opacity-50"
          disabled={overrideMutation.isPending}
          type="submit"
        >
          <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5" />
          Solicitar override
        </button>
      </form>

      <h2 className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-muted-foreground">
        Histórico de overrides
      </h2>
      <div className="overflow-hidden rounded-lg border-[0.5px] border-border bg-background">
        <table className="w-full border-collapse text-xs">
          <thead className="bg-muted">
            <tr className="text-left text-[11px] font-medium text-muted-foreground">
              <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">
                Tipo
              </th>
              <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">
                Alteração
              </th>
              <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">
                Justificativa
              </th>
              <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">
                Alçada
              </th>
              <th className="border-b-[0.5px] border-border px-3 py-2 font-medium">
                Data
              </th>
            </tr>
          </thead>
          <tbody className="[&>tr:last-child>td]:border-b-0">
            {overridesQuery.data?.length ? (
              overridesQuery.data.map((override) => (
                <tr key={override.id}>
                  <td className="border-b-[0.5px] border-border px-3 py-2">
                    {override.override_type}
                  </td>
                  <td className="border-b-[0.5px] border-border px-3 py-2 font-mono">
                    {formatValue(override.previous_value, override.override_type)} →{" "}
                    {formatValue(override.new_value, override.override_type)}
                  </td>
                  <td className="border-b-[0.5px] border-border px-3 py-2">
                    {override.justificativa}
                  </td>
                  <td className="border-b-[0.5px] border-border px-3 py-2">
                    {override.alcada_required}
                  </td>
                  <td className="border-b-[0.5px] border-border px-3 py-2 font-mono text-muted-foreground">
                    {formatDate(override.created_at || override.requested_at)}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td
                  className="px-3 py-6 text-center text-muted-foreground"
                  colSpan={5}
                >
                  Nenhum override solicitado.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function DetailMetric({
  label,
  small,
  testId,
  value,
}: {
  label: string;
  small?: boolean;
  testId?: string;
  value: ReactNode;
}) {
  return (
    <div className="rounded-md bg-muted px-2.5 py-2" data-testid={testId}>
      <p className="mb-1 text-[10px] text-muted-foreground">{label}</p>
      <p
        className={cn(
          "font-mono text-lg font-medium text-foreground",
          small && "text-xs font-normal text-muted-foreground",
        )}
      >
        {value}
      </p>
    </div>
  );
}

function OverrideField({
  children,
  label,
  message,
}: {
  children: ReactNode;
  label: string;
  message?: string;
}) {
  return (
    <label className="block text-[11px] font-medium text-muted-foreground">
      <span className="mb-1 block">{label}</span>
      {children}
      <ErrorText message={message} />
    </label>
  );
}

export default function OperationDetailPage() {
  const params = useParams<{ id: string }>();
  const operationId = params.id;
  const [refreshIn, setRefreshIn] = useState(5);
  const operationQuery = useQuery({
    queryFn: () => getOperation(operationId),
    queryKey: ["operation", operationId],
    refetchInterval: (query) => {
      const status = query.state.data?.status;

      if (
        status === "pending" ||
        status === "processing" ||
        status === "manual_review" ||
        !status
      ) {
        return 5_000;
      }

      return false;
    },
    refetchIntervalInBackground: true,
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 2,
  });
  const componentsQuery = useQuery({
    queryFn: getComponents,
    queryKey: ["components"],
    staleTime: 30_000,
  });
  const operation = operationQuery.data;
  const pipeline = useMemo(
    () =>
      operation
        ? pipelineComponents(operation, componentsQuery.data)
        : [],
    [componentsQuery.data, operation],
  );

  useEffect(() => {
    if (
      operation?.status !== "pending" &&
      operation?.status !== "processing"
    ) {
      return;
    }

    setRefreshIn(5);
    const interval = window.setInterval(() => {
      setRefreshIn((seconds) => (seconds <= 1 ? 5 : seconds - 1));
    }, 1000);

    return () => window.clearInterval(interval);
  }, [operation?.status, operationQuery.dataUpdatedAt]);

  if (
    operationQuery.isError &&
    operationQuery.error instanceof ApiError &&
    operationQuery.error.status === 404
  ) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-muted/40">
        <div className="rounded-lg border-[0.5px] border-border bg-background p-8 text-center">
          <p className="mb-2 text-sm font-medium text-foreground">
            Operação não encontrada
          </p>
          <Link className="text-xs text-primary underline" href="/operations">
            Voltar para operações
          </Link>
        </div>
      </div>
    );
  }

  if (!operation) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-muted/40 text-sm text-muted-foreground">
        Carregando operação...
      </div>
    );
  }

  const processing =
    operation.status === "pending" || operation.status === "processing";
  const manualReview = operation.status === "manual_review";
  const canOverride =
    operation.status === "completed" ||
    operation.status === "failed" ||
    operation.status === "approved" ||
    operation.status === "rejected" ||
    operation.status === "escalated";

  return (
    <div className="flex min-h-dvh flex-col bg-muted/40">
      <Topbar operation={operation} />
      {manualReview ? (
        <ManualReviewView
          operation={operation}
          operationId={operationId}
          onRefresh={operationQuery.refetch}
        />
      ) : processing ? (
        <ProcessingView
          operation={operation}
          pipeline={pipeline}
          refreshIn={refreshIn}
        />
      ) : canOverride ? (
        <CompletedView
          operation={operation}
          operationId={operationId}
          pipeline={pipeline}
        />
      ) : (
        <ProcessingView
          operation={operation}
          pipeline={pipeline}
          refreshIn={refreshIn}
        />
      )}
    </div>
  );
}
