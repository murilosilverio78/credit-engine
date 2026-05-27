"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Download,
  FileCheck,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type ReactNode, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ApiError, getOperation } from "@/lib/api";
import type { ComponentSnapshot, OperationDetails, Rating } from "@/lib/types";
import { cn } from "@/lib/utils";

type JsonRecord = Record<string, unknown>;

interface Dimension {
  justificativa?: string;
  peso?: number;
  score?: number;
}

const ratingColors: Record<Rating, string> = {
  A: "bg-[#EAF3DE] text-[#27500A]",
  B: "bg-[#E6F1FB] text-[#0C447C]",
  C: "bg-[#FAEEDA] text-[#633806]",
  D: "bg-[#FAECE7] text-[#712B13]",
  E: "bg-[#FCEBEB] text-[#791F1F]",
};

const dimensionsLabels: Record<string, string> = {
  regularidade_fiscal: "Regularidade fiscal",
  saude_cadastral: "Saúde cadastral",
  relacionamento_governamental: "Relacionamento gov.",
  porte_operacionalidade: "Porte / operacionalidade",
  reputacao_mercado: "Reputação de mercado",
};

const dimensionOrder = [
  "regularidade_fiscal",
  "saude_cadastral",
  "relacionamento_governamental",
  "porte_operacionalidade",
  "reputacao_mercado",
];

const legalEntityLabels: Record<string, string> = {
  sancionado_ceis: "Sancionado no CEIS",
  sancionado_cnep: "Sancionado no CNEP",
  sancionado_cepim: "Sancionado no CEPIM",
  sancionado_ceaf: "Sancionado no CEAF",
  possui_contratacao: "Possui contratação",
  favorecido_despesas: "Favorecido em despesas",
  favorecido_transferencias: "Favorecido em transferências",
  convenios: "Possui convênios",
  participa_licitacao: "Participa de licitação",
  emitiu_nfe: "Emitiu NF-e",
  beneficiado_renuncia_fiscal: "Beneficiado por renúncia fiscal",
  possui_sancao: "Possui sanção",
};

const componentOrder = [
  "contratos",
  "brasil_api",
  "pessoa_juridica",
  "recursos_recebidos",
  "ceis",
  "cnep",
  "cepim",
  "acordos_leniencia",
  "cnd_federal",
  "cndt_tst",
  "fgts",
  "web_research",
];

const documentComponents = new Set(["cnd_federal", "cndt_tst", "fgts"]);
const sanctionComponents = new Set([
  "ceis",
  "cnep",
  "cepim",
  "acordos_leniencia",
]);

function asRecord(value: unknown): JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function decodeText(value: string) {
  let result = value;

  for (let pass = 0; pass < 2 && /Ã|Â|â|ð|�/.test(result); pass += 1) {
    try {
      const corrected = decodeURIComponent(escape(result));
      if (corrected === result) {
        break;
      }
      result = corrected;
    } catch {
      break;
    }
  }

  return result;
}

function decodedValue<T>(value: T): T {
  if (typeof value === "string") {
    return decodeText(value) as T;
  }

  if (Array.isArray(value)) {
    return value.map((item) => decodedValue(item)) as T;
  }

  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, decodedValue(item)]),
    ) as T;
  }

  return value;
}

function stringValue(value: unknown, fallback = "—") {
  return value === null || value === undefined || value === ""
    ? fallback
    : String(value);
}

function numberValue(value: unknown) {
  return typeof value === "number" ? value : Number(value) || 0;
}

function formatCnpj(cnpj: unknown) {
  const value = stringValue(cnpj, "");
  const digits = value.replace(/\D/g, "");
  return digits.length === 14
    ? digits.replace(
        /^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/,
        "$1.$2.$3/$4-$5",
      )
    : value || "—";
}

function formatCurrency(value: unknown) {
  return new Intl.NumberFormat("pt-BR", {
    currency: "BRL",
    maximumFractionDigits: 2,
    style: "currency",
  }).format(numberValue(value));
}

function formatDate(value: unknown) {
  if (!value) {
    return "—";
  }

  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) {
    return stringValue(value);
  }

  return new Intl.DateTimeFormat("pt-BR").format(date);
}

function formatDateTime(value: Date) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(value);
}

function formatPercent(value: unknown) {
  return `${(numberValue(value) * 100).toLocaleString("pt-BR", {
    maximumFractionDigits: 0,
  })}%`;
}

function formatDuration(component: ComponentSnapshot) {
  if (documentComponents.has(component.component)) {
    return "upload";
  }
  if (component.duration_ms === 0) {
    return "cache";
  }
  return component.duration_ms === null
    ? "—"
    : `${component.duration_ms.toLocaleString("pt-BR")}ms`;
}

function scoreColor(score: number) {
  return score >= 75
    ? {
        bar: "bg-emerald-500",
        text: "text-emerald-700",
      }
    : score >= 50
      ? {
          bar: "bg-amber-500",
          text: "text-amber-700",
        }
      : {
          bar: "bg-red-500",
          text: "text-red-700",
        };
}

function conclusion(rating: Rating | null) {
  if (rating === "A" || rating === "B") {
    return {
      border: "border-l-emerald-500",
      label: "Conclusão — perfil favorável",
      text: "text-emerald-700",
    };
  }
  if (rating === "C") {
    return {
      border: "border-l-amber-500",
      label: "Conclusão — perfil neutro",
      text: "text-amber-700",
    };
  }
  return {
    border: "border-l-red-500",
    label: "Conclusão — perfil restritivo",
    text: "text-red-700",
  };
}

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="mb-2 mt-4 text-[11px] font-medium uppercase tracking-[0.06em] text-muted-foreground">
      {children}
    </h2>
  );
}

function Metric({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  return (
    <div className="rounded-md bg-muted px-3 py-2.5">
      <p className="mb-1 text-[10px] text-muted-foreground">{label}</p>
      <p className="font-mono text-xl font-medium text-foreground">{children}</p>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b-[0.5px] border-border py-1.5 text-xs last:border-b-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="max-w-[70%] text-right font-mono text-[11px] text-foreground">
        {value}
      </span>
    </div>
  );
}

function EmptyData({ label = "Nenhum registro" }: { label?: string }) {
  return (
    <p className="rounded-md bg-muted/60 px-3 py-5 text-center text-xs text-muted-foreground">
      {label}
    </p>
  );
}

function BrasilApiDetails({ result }: { result: JsonRecord }) {
  const partners = asArray(result.qsa).map((item) => asRecord(item));
  const taxRegimes = asArray(result.regime_tributario).map((item) =>
    asRecord(item),
  );
  const activities = [
    result.atividade_principal,
    ...asArray(result.atividades_secundarias),
  ].filter(Boolean);

  return (
    <>
      <DetailRow label="Razão social" value={stringValue(result.razao_social)} />
      <DetailRow label="CNPJ" value={formatCnpj(result.cnpj)} />
      <DetailRow label="Porte" value={stringValue(result.porte)} />
      <DetailRow label="Capital social" value={formatCurrency(result.capital_social)} />
      <DetailRow label="Data de abertura" value={formatDate(result.data_abertura)} />
      <DetailRow
        label="Sócio(s)"
        value={
          partners.length
            ? partners
                .map(
                  (partner) =>
                    `${stringValue(partner.nome)} (${stringValue(partner.qualificacao)})`,
                )
                .join("; ")
            : "—"
        }
      />
      <DetailRow
        label="Regime tributário"
        value={
          taxRegimes.length
            ? taxRegimes
                .map(
                  (regime) =>
                    `${stringValue(regime.forma)} (${stringValue(regime.ano)})`,
                )
                .join("; ")
            : "—"
        }
      />
      <div className="pt-2">
        <p className="mb-1.5 text-xs text-muted-foreground">Atividades</p>
        <ul className="space-y-1 text-[11px] text-foreground">
          {activities.map((activity, index) => (
            <li className="flex gap-2" key={`${String(activity)}-${index}`}>
              <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-500" />
              {stringValue(activity)}
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

function ContractsDetails({ result }: { result: JsonRecord }) {
  const contracts = asArray(result.contratos_detalhe).map((item) =>
    asRecord(item),
  );
  const agencies = asArray(result.orgaos_contratantes).map((item) =>
    stringValue(item),
  );

  return (
    <>
      <div className="grid gap-x-8 sm:grid-cols-2">
        <DetailRow label="Total de contratos" value={stringValue(result.total_contratos)} />
        <DetailRow label="Contratos ativos" value={stringValue(result.contratos_ativos)} />
        <DetailRow label="Contratos encerrados" value={stringValue(result.contratos_encerrados)} />
        <DetailRow label="Valor total ativo" value={formatCurrency(result.valor_total_ativo)} />
        <DetailRow
          label="Valor histórico total"
          value={formatCurrency(result.valor_total_historico)}
        />
        <DetailRow label="Órgãos contratantes" value={agencies.join(", ") || "—"} />
      </div>
      {contracts.length ? (
        <div className="mt-4 overflow-x-auto rounded-md border border-border">
          <table className="min-w-[760px] table-fixed border-collapse text-[11px]">
            <thead className="bg-muted text-left text-muted-foreground">
              <tr>
                <th className="w-[90px] px-2.5 py-2 font-medium">Número</th>
                <th className="w-[160px] px-2.5 py-2 font-medium">Órgão</th>
                <th className="px-2.5 py-2 font-medium">Objeto</th>
                <th className="w-[105px] px-2.5 py-2 font-medium">Valor</th>
                <th className="w-[75px] px-2.5 py-2 font-medium">Status</th>
                <th className="w-[130px] px-2.5 py-2 font-medium">Datas</th>
              </tr>
            </thead>
            <tbody>
              {contracts.map((contract, index) => (
                <tr className="border-t border-border" key={`${contract.numero}-${index}`}>
                  <td className="px-2.5 py-2 font-mono">{stringValue(contract.numero)}</td>
                  <td className="px-2.5 py-2">{stringValue(contract.orgao)}</td>
                  <td className="px-2.5 py-2">
                    <span className="line-clamp-2" title={stringValue(contract.objeto)}>
                      {stringValue(contract.objeto)}
                    </span>
                  </td>
                  <td className="px-2.5 py-2 font-mono">
                    {formatCurrency(contract.valor_final ?? contract.valor_inicial)}
                  </td>
                  <td className="px-2.5 py-2">
                    <span
                      className={cn(
                        "rounded px-1.5 py-0.5",
                        contract.ativo
                          ? "bg-emerald-100 text-emerald-700"
                          : "bg-muted text-muted-foreground",
                      )}
                    >
                      {contract.ativo ? "ativo" : "encerrado"}
                    </span>
                  </td>
                  <td className="px-2.5 py-2 font-mono text-muted-foreground">
                    {formatDate(contract.data_inicio)} – {formatDate(contract.data_fim)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyData />
      )}
    </>
  );
}

function ResourcesDetails({ result }: { result: JsonRecord }) {
  const annualValues = Object.entries(asRecord(result.valor_por_ano));
  const agencies = asArray(result.orgaos_pagadores);
  const monthlyMap = new Map<string, number>();
  asArray(result.recursos_detalhe).forEach((item) => {
    const resource = asRecord(item);
    const month = stringValue(resource.mes, "");
    monthlyMap.set(month, (monthlyMap.get(month) ?? 0) + numberValue(resource.valor));
  });
  const monthlyValues = Array.from(monthlyMap.entries())
    .sort(([first], [second]) => first.localeCompare(second))
    .map(([month, value]) => ({
      label: `${month.slice(4, 6)}/${month.slice(0, 4)}`,
      value,
    }));

  return (
    <>
      <div className="grid gap-x-8 sm:grid-cols-2">
        <DetailRow label="Valor total recebido" value={formatCurrency(result.valor_total_recebido)} />
        <DetailRow
          label="Período"
          value={`${stringValue(result.periodo_inicio)} – ${stringValue(result.periodo_fim)}`}
        />
        <DetailRow label="Registros" value={stringValue(result.total_registros)} />
        <DetailRow
          label="Valores por ano"
          value={
            annualValues.length
              ? annualValues
                  .map(([year, value]) => `${year}: ${formatCurrency(value)}`)
                  .join(" · ")
              : "—"
          }
        />
      </div>
      <p className="mb-1.5 mt-3 text-xs text-muted-foreground">
        Principais órgãos pagadores
      </p>
      <div className="flex flex-wrap gap-1.5">
        {agencies.map((agency) => (
          <span className="rounded bg-muted px-2 py-1 text-[11px]" key={String(agency)}>
            {stringValue(agency)}
          </span>
        ))}
      </div>
      {monthlyValues.length ? (
        <div className="mt-4 h-52" aria-label="Recebimentos agregados por mês">
          <ResponsiveContainer height="100%" width="100%">
            <BarChart data={monthlyValues} margin={{ bottom: 0, left: 14, right: 8, top: 8 }}>
              <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="label" fontSize={10} tickLine={false} />
              <YAxis
                fontSize={10}
                tickFormatter={(value: number) => `${Math.round(value / 1000)}k`}
                tickLine={false}
              />
              <Tooltip formatter={(value: number) => formatCurrency(value)} />
              <Bar dataKey="value" fill="#2563eb" name="Recebido" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <EmptyData label="Sem pagamentos mensais para exibir" />
      )}
    </>
  );
}

function SanctionDetails({ result, component }: { result: JsonRecord; component: string }) {
  const records = asArray(result.registros ?? result.acordos);

  if (!records.length) {
    return <EmptyData label="Nenhum registro" />;
  }

  const columns = Object.keys(asRecord(records[0])).slice(0, 5);
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="min-w-full border-collapse text-[11px]">
        <thead className="bg-muted text-left text-muted-foreground">
          <tr>
            {columns.map((column) => (
              <th className="px-2.5 py-2 font-medium" key={column}>
                {dimensionsLabels[column] ?? column.replaceAll("_", " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {records.map((record, index) => {
            const values = asRecord(record);
            return (
              <tr className="border-t border-border" key={`${component}-${index}`}>
                {columns.map((column) => (
                  <td className="px-2.5 py-2" key={column}>
                    {stringValue(values[column])}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function LegalEntityDetails({ result }: { result: JsonRecord }) {
  const flags = Object.entries(result).filter(([, value]) => typeof value === "boolean");

  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {flags.map(([key, value]) => (
        <div
          className="flex items-center justify-between rounded-md bg-muted/60 px-3 py-2 text-xs"
          key={key}
        >
          <span>{legalEntityLabels[key] ?? key.replaceAll("_", " ")}</span>
          <span
            className={cn(
              "rounded px-2 py-0.5 text-[10px] font-medium",
              value ? "bg-blue-100 text-blue-800" : "bg-muted text-muted-foreground",
            )}
          >
            {value ? "sim" : "não"}
          </span>
        </div>
      ))}
    </div>
  );
}

function WebResearchDetails({ result }: { result: JsonRecord }) {
  return (
    <>
      <DetailRow label="Nível de risco" value={stringValue(result.nivel_risco)} />
      <p className="my-3 text-xs leading-5 text-foreground">{stringValue(result.resumo)}</p>
      <p className="mb-1.5 text-xs text-muted-foreground">Alertas</p>
      {asArray(result.alertas).length ? (
        <ul className="mb-3 space-y-1 text-xs">
          {asArray(result.alertas).map((alert, index) => (
            <li className="flex gap-2 text-amber-700" key={`${String(alert)}-${index}`}>
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {stringValue(alert)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mb-3 text-xs text-emerald-700">Nenhum alerta identificado.</p>
      )}
      <p className="mb-1.5 text-xs text-muted-foreground">Fontes consultadas</p>
      <div className="flex flex-wrap gap-1.5">
        {asArray(result.fontes_consultadas).map((source) => (
          <span className="rounded bg-muted px-2 py-1 text-[11px]" key={String(source)}>
            {stringValue(source)}
          </span>
        ))}
      </div>
    </>
  );
}

function DocumentDetails({ result }: { result: JsonRecord }) {
  return (
    <>
      <p className="mb-3 inline-flex items-center gap-1.5 rounded bg-emerald-100 px-2.5 py-1 text-xs font-medium text-emerald-700">
        <FileCheck className="h-3.5 w-3.5" />
        certidão recebida
      </p>
      <DetailRow label="Nome do arquivo" value={stringValue(result.filename)} />
      <DetailRow label="Storage key" value={stringValue(result.storage_key)} />
    </>
  );
}

function ComponentDetails({ snapshot }: { snapshot: ComponentSnapshot }) {
  const result = asRecord(snapshot.parsed_result);

  if (snapshot.component === "brasil_api") {
    return <BrasilApiDetails result={result} />;
  }
  if (snapshot.component === "contratos") {
    return <ContractsDetails result={result} />;
  }
  if (snapshot.component === "recursos_recebidos") {
    return <ResourcesDetails result={result} />;
  }
  if (sanctionComponents.has(snapshot.component)) {
    return <SanctionDetails component={snapshot.component} result={result} />;
  }
  if (snapshot.component === "pessoa_juridica") {
    return <LegalEntityDetails result={result} />;
  }
  if (snapshot.component === "web_research") {
    return <WebResearchDetails result={result} />;
  }
  if (documentComponents.has(snapshot.component)) {
    return <DocumentDetails result={result} />;
  }

  return <EmptyData label="Sem detalhes disponíveis" />;
}

function PdfFooter({ generatedAt }: { generatedAt: Date }) {
  return (
    <footer className="hidden border-t border-border pt-3 text-[10px] text-muted-foreground print:mt-6 print:block">
      Gerado em {formatDateTime(generatedAt)} · Credit Engine AntecipaGov · Confidencial
    </footer>
  );
}

function Report({ operation }: { operation: OperationDetails }) {
  const generatedAt = useMemo(() => new Date(), []);
  const snapshots = useMemo(
    () =>
      new Map(
        (operation.components ?? []).map((snapshot) => [
          snapshot.component,
          decodedValue(snapshot),
        ]),
      ),
    [operation.components],
  );
  const company = asRecord(snapshots.get("brasil_api")?.parsed_result);
  const contracts = asRecord(snapshots.get("contratos")?.parsed_result);
  const engine = asRecord(snapshots.get("score_engine")?.parsed_result);
  const rawDimensions = asRecord(engine.dimensoes);
  const dimensions = [
    ...dimensionOrder
      .filter((name) => rawDimensions[name])
      .map((name) => [name, rawDimensions[name] as Dimension] as [string, Dimension]),
    ...(Object.entries(rawDimensions).filter(
      ([name]) => !dimensionOrder.includes(name),
    ) as [string, Dimension][]),
  ];
  const remainingComponents = Array.from(snapshots.values()).filter(
    (snapshot) =>
      snapshot.component !== "score_engine" &&
      !componentOrder.includes(snapshot.component),
  );
  const components = [
    ...componentOrder
      .map((name) => snapshots.get(name))
      .filter((snapshot): snapshot is ComponentSnapshot => Boolean(snapshot)),
    ...remainingComponents,
  ];
  const [selectedName, setSelectedName] = useState(
    snapshots.has("contratos") ? "contratos" : components[0]?.component ?? "",
  );
  const selected = snapshots.get(selectedName);
  const status = conclusion(operation.rating);
  const rate = operation.taxa_sugerida ?? numberValue(engine.taxa_sugerida_am);

  return (
    <div className="min-h-dvh bg-muted/40 print:bg-white">
      <header className="flex items-center gap-2 border-b-[0.5px] border-border bg-background px-5 py-3 print:hidden">
        <Link
          className="flex h-8 items-center gap-1 rounded-md border border-border px-2.5 text-xs text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          href={`/operations/${operation.id}`}
        >
          <ArrowLeft aria-hidden="true" className="h-3.5 w-3.5" />
          voltar
        </Link>
        <span className="text-sm font-medium">Relatório de crédito</span>
      </header>

      <main className="mx-auto max-w-6xl px-5 py-4 print:max-w-none print:p-0">
        <section className="rounded-lg border-[0.5px] border-border bg-background px-6 py-5 print:border-0 print:px-0 print:pb-6">
          <div className="mb-4 flex items-start justify-between gap-6">
            <div>
              <p className="mb-1 font-mono text-[10px] text-muted-foreground print:hidden">
                Relatório de crédito — {operation.id.slice(0, 12)}
              </p>
              <p className="mb-2 hidden text-sm font-semibold text-blue-800 print:block">
                Credit Engine / AntecipaGov
              </p>
              <h1 className="text-lg font-medium text-foreground">
                {stringValue(company.razao_social, operation.razao_social ?? "—")}
              </h1>
              <p className="mt-1 font-mono text-xs text-muted-foreground">
                {formatCnpj(company.cnpj ?? operation.cnpj)} · {stringValue(company.porte)} ·{" "}
                {stringValue(company.municipio)}
                {company.uf ? `/${stringValue(company.uf)}` : ""} · abertura{" "}
                {formatDate(company.data_abertura)}
              </p>
              <p className="mt-2 hidden text-xs text-muted-foreground print:block">
                Gerado em {formatDateTime(generatedAt)}
              </p>
            </div>
            <div className="flex flex-col items-end gap-2">
              {operation.rating ? (
                <span
                  className={cn(
                    "flex h-[52px] w-[52px] items-center justify-center rounded-md text-[26px] font-medium",
                    ratingColors[operation.rating],
                  )}
                  aria-label={`Rating ${operation.rating}`}
                >
                  {operation.rating}
                </span>
              ) : null}
              <button
                className="flex h-9 items-center gap-1.5 rounded-md border border-border bg-background px-3 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring print:hidden"
                onClick={() => window.print()}
                type="button"
              >
                <Download className="h-3.5 w-3.5" />
                Baixar PDF
              </button>
            </div>
          </div>
          <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="Score">
              {operation.score ?? numberValue(engine.score)}
              <span className="ml-0.5 text-[11px] font-normal text-muted-foreground">
                /100
              </span>
            </Metric>
            <Metric label="Taxa sugerida">
              {rate.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}
              <span className="ml-0.5 text-[11px] font-normal text-muted-foreground">
                % a.m.
              </span>
            </Metric>
            <Metric label="Limite sugerido">
              {formatPercent(engine.limite_sugerido_pct_contrato)}
              <span className="ml-0.5 text-[11px] font-normal text-muted-foreground">
                contrato
              </span>
            </Metric>
            <Metric label="Contratos ativos">
              {stringValue(contracts.contratos_ativos)}
              <span className="ml-1 text-[11px] font-normal text-muted-foreground">
                / {formatCurrency(contracts.valor_total_ativo)}
              </span>
            </Metric>
          </div>
        </section>

        <SectionTitle>Parecer do agente</SectionTitle>
        <section
          className={cn(
            "rounded-r-lg border-[0.5px] border-l-[3px] border-border bg-background px-4 py-3.5",
            status.border,
          )}
        >
          <p className={cn("mb-1.5 text-[10px] font-medium uppercase tracking-[0.06em]", status.text)}>
            {status.label}
          </p>
          <p className="max-w-[95ch] text-[13px] leading-6 text-foreground">
            {stringValue(engine.parecer, "Parecer não disponível.")}
          </p>
        </section>

        <SectionTitle>Scorecard — 5 dimensões</SectionTitle>
        <section className="grid gap-2.5 md:grid-cols-2">
          {dimensions.map(([key, dimension], index) => {
            const score = numberValue(dimension.score);
            const colors = scoreColor(score);
            return (
              <article
                className={cn(
                  "rounded-lg border-[0.5px] border-border bg-background px-3.5 py-3",
                  index === dimensions.length - 1 && dimensions.length % 2 === 1 && "md:col-span-2",
                )}
                key={key}
              >
                <div className="mb-1.5 flex items-center justify-between gap-3">
                  <h3 className="text-xs font-medium">
                    {dimensionsLabels[key] ?? key.replaceAll("_", " ")}
                  </h3>
                  <span className={cn("font-mono text-lg font-medium", colors.text)}>
                    {score}
                  </span>
                </div>
                <p className="mb-1.5 text-[10px] text-muted-foreground">
                  Peso {formatPercent(dimension.peso)}
                </p>
                <div className="mb-2 h-1 overflow-hidden rounded-full bg-muted">
                  <div className={cn("h-full rounded-full", colors.bar)} style={{ width: `${score}%` }} />
                </div>
                <p className="text-[11px] leading-[1.55] text-muted-foreground">
                  {stringValue(dimension.justificativa)}
                </p>
              </article>
            );
          })}
        </section>

        <section className="grid gap-2.5 md:grid-cols-2">
          <div>
            <SectionTitle>Pontos positivos</SectionTitle>
            <div className="h-full rounded-lg border-[0.5px] border-border bg-background px-3.5 py-3">
              {asArray(engine.pontos_positivos).map((point, index) => (
                <p
                  className="flex gap-2 border-b-[0.5px] border-border py-1.5 text-[11px] leading-5 text-muted-foreground last:border-b-0"
                  key={`${String(point)}-${index}`}
                >
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
                  {stringValue(point)}
                </p>
              ))}
            </div>
          </div>
          <div>
            <SectionTitle>Pontos de atenção</SectionTitle>
            <div className="h-full rounded-lg border-[0.5px] border-border bg-background px-3.5 py-3">
              {asArray(engine.pontos_atencao).map((point, index) => (
                <p
                  className="flex gap-2 border-b-[0.5px] border-border py-1.5 text-[11px] leading-5 text-muted-foreground last:border-b-0"
                  key={`${String(point)}-${index}`}
                >
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
                  {stringValue(point)}
                </p>
              ))}
            </div>
          </div>
        </section>

        <SectionTitle>Componentes consultados — clique para ver detalhes</SectionTitle>
        <section className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {components.map((component) => {
            const manual = documentComponents.has(component.component);
            return (
              <button
                aria-pressed={selectedName === component.component}
                className={cn(
                  "min-h-[70px] rounded-md border-[0.5px] border-border bg-background p-2.5 text-left transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring print:min-h-0",
                  selectedName === component.component && "border-blue-400 bg-blue-50 print:border-border print:bg-white",
                )}
                key={component.component}
                onClick={() => setSelectedName(component.component)}
                type="button"
              >
                <p className="mb-1 text-[11px] font-medium">{component.component}</p>
                <p
                  className={cn(
                    "flex items-center gap-1 text-[10px]",
                    manual ? "text-amber-700" : "text-emerald-700",
                  )}
                >
                  {manual ? <FileCheck className="h-3 w-3" /> : <Check className="h-3 w-3" />}
                  {manual ? "manual" : "ok"}
                </p>
                <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                  {formatDuration(component)}
                </p>
              </button>
            );
          })}
        </section>

        {selected ? (
          <section className="mt-3.5 rounded-lg border border-blue-200 bg-background px-4 py-3.5 print:hidden">
            <h2 className="mb-3 text-xs font-medium text-blue-700">
              {selected.component} — detalhes
            </h2>
            <ComponentDetails snapshot={selected} />
          </section>
        ) : null}
        <PdfFooter generatedAt={generatedAt} />
      </main>
    </div>
  );
}

export default function OperationReportPage() {
  const params = useParams<{ id: string }>();
  const operationId = params.id;
  const operationQuery = useQuery({
    queryFn: () => getOperation(operationId),
    queryKey: ["operation", operationId],
    staleTime: Number.POSITIVE_INFINITY,
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 2,
  });

  if (
    operationQuery.isError &&
    operationQuery.error instanceof ApiError &&
    operationQuery.error.status === 404
  ) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-muted/40">
        <div className="rounded-lg border border-border bg-background p-8 text-center">
          <p className="mb-2 text-sm font-medium">Operação não encontrada</p>
          <Link className="text-xs text-primary underline" href="/operations">
            Voltar para operações
          </Link>
        </div>
      </div>
    );
  }

  if (!operationQuery.data) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-muted/40 text-sm text-muted-foreground">
        Carregando relatório...
      </div>
    );
  }

  return <Report operation={decodedValue(operationQuery.data)} />;
}
