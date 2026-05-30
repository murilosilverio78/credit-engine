"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  ApiError,
  createOperation,
  getCompanyByCnpj,
  getComponents,
} from "@/lib/api";
import type { Component, PropostaInput } from "@/lib/types";
import { cn } from "@/lib/utils";

function digitsOnly(value: string) {
  return value.replace(/\D/g, "");
}

function isValidCnpj(value: string) {
  const digits = digitsOnly(value);

  if (digits.length !== 14 || /^(\d)\1+$/.test(digits)) {
    return false;
  }

  const calculateDigit = (base: string, weights: number[]) => {
    const total = base
      .split("")
      .reduce((sum, digit, index) => sum + Number(digit) * weights[index], 0);
    const remainder = total % 11;
    return remainder < 2 ? 0 : 11 - remainder;
  };

  const firstDigit = calculateDigit(
    digits.slice(0, 12),
    [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
  );
  const secondDigit = calculateDigit(
    `${digits.slice(0, 12)}${firstDigit}`,
    [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
  );

  return digits.endsWith(`${firstDigit}${secondDigit}`);
}

function formatCnpj(value: string) {
  return digitsOnly(value)
    .slice(0, 14)
    .replace(/^(\d{2})(\d)/, "$1.$2")
    .replace(/^(\d{2})\.(\d{3})(\d)/, "$1.$2.$3")
    .replace(/\.(\d{3})(\d)/, ".$1/$2")
    .replace(/(\d{4})(\d)/, "$1-$2");
}

function formatCurrency(value: string) {
  const digits = digitsOnly(value);

  if (!digits) {
    return "";
  }

  return (Number(digits) / 100).toLocaleString("pt-BR", {
    currency: "BRL",
    style: "currency",
  });
}

function parseCurrency(value: string) {
  const digits = digitsOnly(value);
  return digits ? Number(digits) / 100 : undefined;
}

const formSchema = z.object({
  cnpj: z
    .string()
    .min(1, "Informe o CNPJ.")
    .refine(isValidCnpj, "Informe um CNPJ válido."),
  valor_solicitado: z.string(),
  contrato_saldo: z.string(),
  prazo_dias: z
    .string()
    .refine(
      (value) => !value || (/^\d+$/.test(value) && Number(value) > 0),
      "Informe um prazo inteiro maior que zero.",
    ),
  contrato_id: z.string(),
});

type AnalysisFormValues = z.infer<typeof formSchema>;

const inputClassName =
  "h-10 w-full rounded-md border border-input bg-background px-3 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring";

const componentLabels: Record<string, string> = {
  acordos_leniencia: "Acordos de leniência",
  brasil_api: "BrasilAPI — cadastral",
  ceis: "CEIS",
  cnep: "CNEP",
  cepim: "CEPIM",
  cnd_federal: "CND Federal",
  cndt_tst: "CNDT — TST",
  contratos: "Portal Transparência",
  fgts: "FGTS",
  pessoa_juridica: "Pessoa jurídica",
  recursos_recebidos: "Recursos recebidos",
  score_engine: "Score engine",
  serasa_pj: "Serasa PJ",
  web_research: "Web research",
};

function componentLabel(component: Component) {
  return (
    componentLabels[component.component] ??
    component.description ??
    component.component.replaceAll("_", " ")
  );
}

function componentStatus(component: Component) {
  if (!component.enabled) {
    return { color: "bg-slate-300", label: "Roadmap" };
  }

  if (component.timeout_seconds === 0) {
    return { color: "bg-amber-500", label: "Upload manual" };
  }

  return { color: "bg-emerald-500", label: "Automático" };
}

function FieldError({ message }: { message?: string }) {
  if (!message) {
    return null;
  }

  return (
    <p className="mt-1 text-[11px] text-red-700" role="alert">
      {message}
    </p>
  );
}

export default function NewOperationPage() {
  const router = useRouter();
  const {
    clearErrors,
    formState: { errors },
    handleSubmit,
    register,
    setError,
    setValue,
    watch,
  } = useForm<AnalysisFormValues>({
    defaultValues: {
      cnpj: "",
      contrato_id: "",
      contrato_saldo: "",
      prazo_dias: "",
      valor_solicitado: "",
    },
    resolver: zodResolver(formSchema),
  });

  const cnpj = watch("cnpj");
  const cnpjDigits = digitsOnly(cnpj);
  const validCnpj = isValidCnpj(cnpj);
  const companyQuery = useQuery({
    enabled: validCnpj,
    queryFn: () => getCompanyByCnpj(cnpjDigits),
    queryKey: ["company", cnpjDigits],
    retry: false,
    staleTime: 60 * 60 * 1000,
  });
  const componentsQuery = useQuery({
    queryFn: getComponents,
    queryKey: ["components"],
    staleTime: 30_000,
  });
  const createOperationMutation = useMutation({
    mutationFn: createOperation,
    onError: (error) => {
      if (error instanceof ApiError && error.status === 422) {
        let validationErrors: Array<{ loc?: string[]; msg?: string }> = [];

        try {
          validationErrors = (
            JSON.parse(error.message) as {
              detail?: Array<{ loc?: string[]; msg?: string }>;
            }
          ).detail ?? [];
        } catch {
          validationErrors = [];
        }

        if (validationErrors.length === 0) {
          setError("cnpj", { message: "Verifique os campos informados." });
        }

        validationErrors.forEach((validationError) => {
          const field = validationError.loc?.at(-1);
          if (
            field === "cnpj" ||
            field === "valor_solicitado" ||
            field === "contrato_saldo" ||
            field === "prazo_dias" ||
            field === "contrato_id"
          ) {
            setError(field, {
              message: validationError.msg ?? "Valor inválido.",
            });
          }
        });
      }
    },
  });

  useEffect(() => {
    if (!createOperationMutation.data) {
      return;
    }

    const timeout = window.setTimeout(() => {
      router.push(`/operations/${createOperationMutation.data.operation_id}`);
    }, 1500);

    return () => window.clearTimeout(timeout);
  }, [createOperationMutation.data, router]);

  function submit(values: AnalysisFormValues) {
    clearErrors();

    const payload: PropostaInput = {
      cnpj: digitsOnly(values.cnpj),
      source: "admin_ui",
    };
    const value = parseCurrency(values.valor_solicitado);

    if (value !== undefined) {
      payload.valor_solicitado = value;
    }
    const saldo = parseCurrency(values.contrato_saldo);
    if (saldo !== undefined) {
      payload.contrato_saldo = saldo;
    }
    if (values.prazo_dias) {
      payload.prazo_dias = Number(values.prazo_dias);
    }
    if (values.contrato_id.trim()) {
      payload.contrato_id = values.contrato_id.trim();
    }

    createOperationMutation.mutate(payload);
  }

  return (
    <div className="flex min-h-dvh flex-col bg-muted/40">
      <header className="border-b-[0.5px] border-border bg-background px-5 py-3.5">
        <h1 className="text-[15px] font-medium text-foreground">Nova análise</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Dispare uma análise de crédito por CNPJ
        </p>
      </header>

      <div className="flex flex-1 gap-5 p-5">
        <div className="min-w-0 flex-1">
          <form
            className="rounded-lg border-[0.5px] border-border bg-background p-4"
            noValidate
            onSubmit={handleSubmit(submit)}
          >
            <div className="mb-3.5">
              <label
                className="mb-1.5 block text-xs font-medium text-muted-foreground"
                htmlFor="cnpj"
              >
                CNPJ <span className="text-red-700">*</span>
              </label>
              <input
                {...register("cnpj")}
                aria-invalid={Boolean(errors.cnpj)}
                autoComplete="off"
                className={cn(inputClassName, "font-mono text-sm")}
                id="cnpj"
                inputMode="numeric"
                onChange={(event) => {
                  setValue("cnpj", formatCnpj(event.target.value), {
                    shouldValidate: Boolean(errors.cnpj),
                  });
                }}
                placeholder="00.000.000/0000-00"
              />
              <FieldError message={errors.cnpj?.message} />
              {validCnpj && companyQuery.isLoading ? (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Consultando CNPJ...
                </p>
              ) : null}
              {validCnpj && companyQuery.data ? (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {companyQuery.data.razao_social} — CNPJ validado
                </p>
              ) : null}
              {validCnpj && companyQuery.isError ? (
                <p className="mt-1 text-[11px] text-red-700">
                  CNPJ válido, mas não foi possível consultar a empresa.
                </p>
              ) : null}
            </div>

            <div className="my-4 border-t-[0.5px] border-border" />
            <p className="mb-3 text-[10px] uppercase tracking-[0.06em] text-muted-foreground">
              Campos opcionais — operação específica
            </p>

            <div className="grid grid-cols-2 gap-2.5">
              <div className="mb-3.5">
                <label
                  className="mb-1.5 block text-xs font-medium text-muted-foreground"
                  htmlFor="valor_solicitado"
                >
                  Valor solicitado
                </label>
                <input
                  {...register("valor_solicitado")}
                  aria-invalid={Boolean(errors.valor_solicitado)}
                  className={inputClassName}
                  id="valor_solicitado"
                  inputMode="numeric"
                  onChange={(event) => {
                    setValue(
                      "valor_solicitado",
                      formatCurrency(event.target.value),
                    );
                  }}
                  placeholder="R$ 0,00"
                />
                <FieldError message={errors.valor_solicitado?.message} />
              </div>
              <div className="mb-3.5">
                <label
                  className="mb-1.5 block text-xs font-medium text-muted-foreground"
                  htmlFor="contrato_saldo"
                >
                  Saldo do contrato (R$)
                </label>
                <input
                  {...register("contrato_saldo")}
                  aria-invalid={Boolean(errors.contrato_saldo)}
                  className={inputClassName}
                  id="contrato_saldo"
                  inputMode="numeric"
                  onChange={(event) => {
                    setValue(
                      "contrato_saldo",
                      formatCurrency(event.target.value),
                    );
                  }}
                  placeholder="R$ 0,00"
                />
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Opcional — usado para calcular o limite máximo (70% do saldo)
                </p>
                <FieldError message={errors.contrato_saldo?.message} />
              </div>
              <div className="mb-3.5">
                <label
                  className="mb-1.5 block text-xs font-medium text-muted-foreground"
                  htmlFor="prazo_dias"
                >
                  Prazo (dias)
                </label>
                <input
                  {...register("prazo_dias")}
                  aria-invalid={Boolean(errors.prazo_dias)}
                  className={cn(inputClassName, "font-mono")}
                  id="prazo_dias"
                  inputMode="numeric"
                  onChange={(event) => {
                    setValue(
                      "prazo_dias",
                      digitsOnly(event.target.value).slice(0, 6),
                      { shouldValidate: Boolean(errors.prazo_dias) },
                    );
                  }}
                  placeholder="30"
                />
                <FieldError message={errors.prazo_dias?.message} />
              </div>
            </div>

            <div className="mb-3.5">
              <label
                className="mb-1.5 block text-xs font-medium text-muted-foreground"
                htmlFor="contrato_id"
              >
                Contrato ID
              </label>
              <input
                {...register("contrato_id")}
                aria-invalid={Boolean(errors.contrato_id)}
                className={cn(inputClassName, "font-mono")}
                id="contrato_id"
                placeholder="Ex: 00123/2024"
              />
              <p className="mt-1 text-[11px] text-muted-foreground">
                ID do contrato no Portal da Transparência
              </p>
              <FieldError message={errors.contrato_id?.message} />
            </div>

            <div className="my-4 border-t-[0.5px] border-border" />

            {createOperationMutation.isError &&
            createOperationMutation.error instanceof ApiError &&
            createOperationMutation.error.status >= 500 ? (
              <p className="mb-3 text-xs text-red-700" role="alert">
                Erro interno. Tente novamente.
              </p>
            ) : null}
            {createOperationMutation.isSuccess ? (
              <p
                className="mb-3 text-xs text-emerald-700"
                role="status"
              >
                Análise iniciada. Redirecionando...
              </p>
            ) : null}

            <button
              className="flex h-10 w-full items-center justify-center gap-1.5 rounded-md border-[0.5px] border-foreground bg-background text-[13px] font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              disabled={createOperationMutation.isPending}
              type="submit"
            >
              <Play aria-hidden="true" className="h-3.5 w-3.5" />
              {createOperationMutation.isPending
                ? "Iniciando análise..."
                : "Iniciar análise"}
            </button>
          </form>
        </div>

        <aside className="w-[220px] shrink-0">
          <div className="rounded-lg border-[0.5px] border-border bg-background p-4">
            <h2 className="mb-2 text-[11px] font-medium uppercase tracking-[0.05em] text-muted-foreground">
              Componentes ativos
            </h2>
            <div>
              {componentsQuery.isLoading ? (
                <p className="py-2 text-[11px] text-muted-foreground">
                  Carregando componentes...
                </p>
              ) : componentsQuery.isError ? (
                <p className="py-2 text-[11px] text-red-700">
                  Não foi possível carregar componentes.
                </p>
              ) : (
                componentsQuery.data?.map((component) => {
                  const status = componentStatus(component);

                  return (
                    <div
                      className="flex items-center gap-1.5 border-b-[0.5px] border-border py-1 text-[11px] text-muted-foreground last:border-b-0"
                      key={component.component}
                    >
                      <span
                        aria-label={status.label}
                        className={cn("h-1.5 w-1.5 rounded-full", status.color)}
                        title={status.label}
                      />
                      <span>{componentLabel(component)}</span>
                    </div>
                  );
                })
              )}
            </div>

            <div className="my-2.5 border-t-[0.5px] border-border" />
            <h2 className="mb-2 text-[11px] font-medium uppercase tracking-[0.05em] text-muted-foreground">
              Legenda
            </h2>
            {[
              { color: "bg-emerald-500", label: "Automático" },
              { color: "bg-amber-500", label: "Upload manual" },
              { color: "bg-slate-300", label: "Roadmap" },
            ].map((item) => (
              <div
                className="mb-1 flex items-center gap-1.5 text-[11px] text-muted-foreground last:mb-0"
                key={item.label}
              >
                <span className={cn("h-1.5 w-1.5 rounded-full", item.color)} />
                {item.label}
              </div>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}
