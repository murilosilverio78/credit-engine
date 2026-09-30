"use client";

import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { FileText, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { generateOperationReport, getFunnelOperations, getManualAnalyses } from "@/lib/api";
import { formatTaxaAm } from "@/lib/format";
import { type FunnelItem, type FunilEstagio, type ManualAnalysis, type Rating } from "@/lib/types";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;
const funnelStages: FunilEstagio[] = ["LISTA_ESPERA", "ENQUADRADA", "DOCUMENTADA", "QUALIFICADA", "ENCERRADA"];
const stageLabels: Record<FunilEstagio, string> = {
  LISTA_ESPERA: "Não enquadradas", ENQUADRADA: "Sem documentos", DOCUMENTADA: "Em verificação", QUALIFICADA: "Qualificadas", ENCERRADA: "Encerradas",
};
const ratingOptions: Rating[] = ["A", "B", "C", "D", "E"];
const ratingColors: Record<Rating, string> = {
  A: "bg-[#EAF3DE] text-[#27500A]", B: "bg-[#E6F1FB] text-[#0C447C]", C: "bg-[#FAEEDA] text-[#633806]", D: "bg-[#FAECE7] text-[#712B13]", E: "bg-[#FCEBEB] text-[#791F1F]",
};

function normalizeCnpj(cnpj: string) { return cnpj.replace(/\D/g, ""); }
function formatCnpj(cnpj: string) {
  const digits = normalizeCnpj(cnpj);
  return digits.length === 14 ? digits.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5") : cnpj;
}
function formatBrl(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 }).format(value);
}
function formatScore(score: number | null | undefined) { return score === null || score === undefined ? "—" : score.toLocaleString("pt-BR"); }
function formatDate(value: string | null | undefined) {
  return value ? new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" }).format(new Date(value)) : "—";
}
function expiration(value: string | null) {
  if (!value) return { label: "—", urgent: false };
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.ceil((new Date(`${value}T00:00:00`).getTime() - today.getTime()) / 86_400_000);
  return { label: days < 0 ? "Expirada" : days === 0 ? "Hoje" : `${days} dia${days === 1 ? "" : "s"}`, urgent: days <= 3 };
}
function RatingBadge({ rating }: { rating: Rating | null | undefined }) {
  return rating ? <span className={cn("inline-flex rounded px-2 py-0.5 text-[10px] font-medium", ratingColors[rating])}>{rating}</span> : <span className="text-muted-foreground">—</span>;
}
function FunnelReasons({ reasons }: { reasons: FunnelItem["motivos"] }) {
  if (!reasons.length) return <span className="text-muted-foreground">—</span>;
  const hidden = reasons.slice(2);
  return <div className="flex flex-wrap gap-1">{reasons.slice(0, 2).map((reason) => <span className={cn("inline-flex rounded px-1.5 py-0.5 text-[10px] leading-4", reason.tipo === "indisponibilidade" ? "bg-amber-100 text-amber-900" : "bg-red-100 text-red-800")} key={reason.codigo} title={reason.detalhe}>{reason.rotulo}</span>)}{hidden.length > 0 && <span className="inline-flex rounded bg-muted px-1.5 py-0.5 text-[10px] leading-4" title={hidden.map((reason) => `${reason.rotulo}: ${reason.detalhe}`).join("\n")}>+{hidden.length}</span>}</div>;
}
function MetricCard({ label, value, subtitle }: { label: string; value: number | string; subtitle: string }) {
  return <div className="rounded-md bg-muted px-3 py-2.5"><p className="mb-1 text-[10px] uppercase tracking-[0.05em] text-muted-foreground">{label}</p><p className="font-mono text-xl font-medium text-foreground">{value}</p><p className="mt-0.5 text-[10px] text-muted-foreground">{subtitle}</p></div>;
}

function FunnelCell({ item, column, onGenerate, generating }: { item: FunnelItem; column: string; onGenerate: (id: string) => void; generating: boolean }) {
  if (column === "cnpj") return <><span className="font-mono text-[11px]">{formatCnpj(item.cnpj)}</span><span className="mt-0.5 block font-mono text-[10px] text-muted-foreground">{item.cotacao_id}</span></>;
  if (column === "razao") return <span className="whitespace-normal text-[11px]" title={item.razao_social ?? item.cnpj}>{item.razao_social || formatCnpj(item.cnpj)}</span>;
  if (column === "tipo") return item.tipo || "—";
  if (column === "valor") return formatBrl(item.valor_solicitado);
  if (column === "margem") return formatBrl(item.margem_disponivel);
  if (column === "enquadrado") return formatBrl(item.valor_enquadrado);
  if (column === "docs") return item.n_documentos ?? "—";
  if (column === "expira") { const value = expiration(item.data_expiracao); return <span className={cn(value.urgent && "font-medium text-red-700")}>{value.label}</span>; }
  if (column === "motivos") return <FunnelReasons reasons={item.motivos} />;
  if (column === "ultimo-estagio") return item.estagio_max ? stageLabels[item.estagio_max] : "—";
  if (column === "encerrada") return formatDate(item.estagio_atualizado_em);
  if (column === "relatorio") return item.relatorio?.gerado ? "Gerado" : "Pendente";
  if (column === "rating") return <RatingBadge rating={item.relatorio?.rating} />;
  if (column === "score") return formatScore(item.relatorio?.score);
  if (column === "taxa") return formatTaxaAm(item.relatorio?.taxa_sugerida);
  if (column === "acao") {
    if (!item.operation_id) return <span className="text-muted-foreground">—</span>;
    if (item.relatorio?.gerado) return <Link className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-background px-2 text-[11px] hover:bg-muted" href={`/operations/${item.operation_id}/report`} onClick={(event) => event.stopPropagation()}><FileText aria-hidden="true" className="h-3.5 w-3.5" />Ver relatório</Link>;
    return <button className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-background px-2 text-[11px] hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50" disabled={generating} onClick={(event) => { event.stopPropagation(); onGenerate(item.operation_id!); }} type="button"><FileText aria-hidden="true" className="h-3.5 w-3.5" />{generating ? "Gerando" : "Gerar relatório"}</button>;
  }
  return "—";
}

const columnsByStage: Record<FunilEstagio, Array<[string, string]>> = {
  LISTA_ESPERA: [["cnpj", "CNPJ"], ["razao", "Razão social"], ["tipo", "Tipo"], ["valor", "Valor pedido"], ["margem", "Margem"], ["expira", "Expira em"], ["motivos", "Motivos"]],
  ENQUADRADA: [["cnpj", "CNPJ"], ["razao", "Razão social"], ["valor", "Valor pedido"], ["margem", "Margem"], ["enquadrado", "Valor enquadrado"], ["expira", "Expira em"]],
  DOCUMENTADA: [["cnpj", "CNPJ"], ["razao", "Razão social"], ["valor", "Valor pedido"], ["margem", "Margem"], ["enquadrado", "Valor enquadrado"], ["docs", "Nº docs"], ["expira", "Expira em"], ["motivos", "Motivos"]],
  QUALIFICADA: [["cnpj", "CNPJ"], ["razao", "Razão social"], ["valor", "Valor pedido"], ["margem", "Margem"], ["enquadrado", "Valor enquadrado"], ["expira", "Expira em"], ["relatorio", "Relatório"], ["rating", "Rating"], ["score", "Score"], ["taxa", "Taxa"], ["acao", "Ação"]],
  ENCERRADA: [["cnpj", "CNPJ"], ["razao", "Razão social"], ["valor", "Valor pedido"], ["ultimo-estagio", "Último estágio"], ["encerrada", "Encerrada em"]],
};

export default function OperationsPage() {
  const router = useRouter();
  const [active, setActive] = useState<FunilEstagio | "MANUAIS">("LISTA_ESPERA");
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [rating, setRating] = useState<Rating | "">("");
  const [reportStatus, setReportStatus] = useState<"" | "gerado" | "pendente">("");
  const [reasonType, setReasonType] = useState<"" | "criterio" | "indisponibilidade">("");
  const [includeTests, setIncludeTests] = useState(false);
  const [generatingOperationId, setGeneratingOperationId] = useState<string | null>(null);
  const stage = active === "MANUAIS" ? "LISTA_ESPERA" : active;
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedSearch(search), 300);
    return () => window.clearTimeout(timeout);
  }, [search]);
  const funnelQuery = useQuery({ queryKey: ["funnel-operations", { stage, offset, search: debouncedSearch, rating, reportStatus, reasonType }], queryFn: () => getFunnelOperations(stage, PAGE_SIZE, offset, { busca: debouncedSearch, rating, relatorio: reportStatus, tipoMotivo: reasonType }), enabled: active !== "MANUAIS", placeholderData: keepPreviousData, refetchInterval: 15_000 });
  const summaryQuery = useQuery({ queryKey: ["funnel-summary"], queryFn: () => getFunnelOperations("LISTA_ESPERA", 1, 0), refetchInterval: 15_000 });
  const manualQuery = useQuery({ queryKey: ["manual-analyses", { includeTests, offset, search: debouncedSearch }], queryFn: () => getManualAnalyses(includeTests, PAGE_SIZE, offset, debouncedSearch), enabled: active === "MANUAIS", placeholderData: keepPreviousData });
  const reportMutation = useMutation({ mutationFn: generateOperationReport, onMutate: (id) => setGeneratingOperationId(id), onSuccess: () => funnelQuery.refetch(), onSettled: () => setGeneratingOperationId(null) });
  const funnelItems = funnelQuery.data?.items ?? [];
  const manualItems = manualQuery.data?.items ?? [];
  const visibleFunnelItems = funnelItems;
  const visibleManualItems = manualItems;
  const isManual = active === "MANUAIS";
  const data = isManual ? manualQuery.data : funnelQuery.data;
  const total = data?.total ?? 0;
  const visible = isManual ? visibleManualItems : visibleFunnelItems;
  const hasFilters = Boolean(search || (!isManual && (rating || reportStatus || reasonType)));
  const summaryUnavailable = summaryQuery.isError || !summaryQuery.data || summaryQuery.data.total_fila === null || summaryQuery.data.estagios === null || summaryQuery.data.relatorios_gerados === null;
  const summaryValue = (value: number | null | undefined) => summaryUnavailable ? "Indisponível" : value ?? 0;
  function selectTab(next: FunilEstagio | "MANUAIS") { setActive(next); setOffset(0); setSearch(""); setDebouncedSearch(""); setRating(""); setReportStatus(""); setReasonType(""); }
  return <div className="flex min-h-dvh flex-col bg-muted/40">
    <header className="flex items-center justify-between border-b border-border bg-background px-5 py-3.5"><div><h1 className="text-[15px] font-medium text-foreground">Operações</h1><p className="mt-0.5 text-xs text-muted-foreground">Fila de cotações e análises manuais</p></div><Link className="flex h-8 items-center gap-1.5 rounded-md border border-border bg-background px-3.5 text-xs text-foreground transition-colors hover:bg-muted" href="/operations/new"><Plus aria-hidden="true" className="h-3.5 w-3.5" />Nova análise</Link></header>
    <section className="flex-1 p-4 px-5" aria-label="Funil de operações">
      <div className="mb-4 grid grid-cols-2 gap-2.5 md:grid-cols-4 xl:grid-cols-7"><MetricCard label="Total da fila" subtitle="cotações" value={summaryValue(summaryQuery.data?.total_fila)} />{funnelStages.map((item) => <MetricCard key={item} label={stageLabels[item]} subtitle="no estágio" value={summaryValue(summaryQuery.data?.estagios?.[item])} />)}<MetricCard label="Com score" subtitle="cotações" value={summaryValue(summaryQuery.data?.relatorios_gerados)} /></div>
      <div className="mb-3 flex flex-wrap items-center gap-1.5 border-b border-border">{funnelStages.map((item) => <button className={cn("flex h-9 items-center gap-1.5 border-b-2 px-2.5 text-xs transition-colors", active === item ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")} key={item} onClick={() => selectTab(item)} type="button">{stageLabels[item]}<span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">{summaryValue(summaryQuery.data?.estagios?.[item])}</span></button>)}<span aria-hidden="true" className="mx-1 h-5 border-l border-border" /><button className={cn("flex h-9 items-center border-b-2 px-2.5 text-xs transition-colors", isManual ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")} onClick={() => selectTab("MANUAIS")} type="button">Análises manuais</button></div>
      <div className="mb-3 flex flex-wrap gap-2"><label className="sr-only" htmlFor="operation-search">Buscar por CNPJ ou razão social</label><input className="h-8 w-[245px] rounded-md border border-input bg-background px-2.5 text-xs outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring" id="operation-search" onChange={(event) => { setSearch(event.target.value); setOffset(0); }} placeholder="Buscar CNPJ ou razão social..." type="search" value={search} />{active === "QUALIFICADA" && <><select aria-label="Filtrar por rating" className="h-8 rounded-md border border-input bg-background px-2.5 text-xs" onChange={(event) => { setRating(event.target.value as Rating | ""); setOffset(0); }} value={rating}><option value="">Todos os ratings</option>{ratingOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select><select aria-label="Filtrar por situação do relatório" className="h-8 rounded-md border border-input bg-background px-2.5 text-xs" onChange={(event) => { setReportStatus(event.target.value as "" | "gerado" | "pendente"); setOffset(0); }} value={reportStatus}><option value="">Todo relatório</option><option value="gerado">Gerado</option><option value="pendente">Pendente</option></select></>}{(active === "LISTA_ESPERA" || active === "DOCUMENTADA") && <select aria-label="Filtrar por categoria de motivo" className="h-8 rounded-md border border-input bg-background px-2.5 text-xs" onChange={(event) => { setReasonType(event.target.value as "" | "criterio" | "indisponibilidade"); setOffset(0); }} value={reasonType}><option value="">Todos os motivos</option><option value="criterio">Critério</option><option value="indisponibilidade">Indisponibilidade</option></select>}{isManual && <label className="flex h-8 items-center gap-2 rounded-md border border-input bg-background px-2.5 text-xs"><input checked={includeTests} onChange={(event) => { setIncludeTests(event.target.checked); setOffset(0); }} type="checkbox" />Incluir testes</label>}</div>
      <div className="overflow-x-auto rounded-md border border-border bg-background">{isManual ? <ManualTable error={manualQuery.isError} items={visibleManualItems} loading={manualQuery.isLoading} onOpen={(id) => router.push(`/operations/${id}`)} /> : <FunnelTable error={funnelQuery.isError} generatingOperationId={generatingOperationId} items={visibleFunnelItems} loading={funnelQuery.isLoading} onGenerate={(id) => reportMutation.mutate(id)} onOpen={(id) => router.push(`/operations/${id}`)} stage={stage} />}</div>
      <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground"><p>{hasFilters ? `${visible.length} resultado(s) nesta página de ${total}` : `Mostrando ${total === 0 ? 0 : offset + 1}–${Math.min(offset + (isManual ? manualItems.length : funnelItems.length), total)} de ${total}`}</p><div className="flex gap-1.5"><button className="rounded-md border border-border bg-background px-2.5 py-1.5 text-[11px] disabled:cursor-not-allowed disabled:opacity-50" disabled={offset === 0} onClick={() => setOffset((current) => current - PAGE_SIZE)} type="button">← anterior</button><button className="rounded-md border border-border bg-background px-2.5 py-1.5 text-[11px] disabled:cursor-not-allowed disabled:opacity-50" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset((current) => current + PAGE_SIZE)} type="button">próxima →</button></div></div>
    </section>
  </div>;
}

function FunnelTable({ stage, items, loading, error, onGenerate, generatingOperationId, onOpen }: { stage: FunilEstagio; items: FunnelItem[]; loading: boolean; error: boolean; onGenerate: (id: string) => void; generatingOperationId: string | null; onOpen: (id: string) => void }) {
  const columns = columnsByStage[stage];
  return <table className="w-full border-collapse text-xs"><thead className="bg-muted"><tr>{columns.map(([key, label]) => <th className="whitespace-nowrap border-b border-border px-2.5 py-2 text-left text-[11px] font-medium text-muted-foreground" key={key}>{label}</th>)}</tr></thead><tbody className="[&>tr:last-child>td]:border-b-0">{loading || error || items.length === 0 ? <tr><td className={cn("h-28 text-center text-sm", error ? "text-red-700" : "text-muted-foreground")} colSpan={columns.length}>{loading ? "Carregando cotações..." : error ? "Não foi possível carregar as cotações." : "Nenhuma cotação encontrada."}</td></tr> : items.map((item) => <tr className={cn(item.operation_id && "cursor-pointer hover:bg-muted/80")} key={item.id} onClick={() => item.operation_id && onOpen(item.operation_id)}>{columns.map(([key]) => <td className="border-b border-border px-2.5 py-2 align-top text-[11px]" key={key}><FunnelCell column={key} generating={generatingOperationId === item.operation_id} item={item} onGenerate={onGenerate} /></td>)}</tr>)}</tbody></table>;
}
function ManualTable({ items, loading, error, onOpen }: { items: ManualAnalysis[]; loading: boolean; error: boolean; onOpen: (id: string) => void }) {
  const headers = ["CNPJ", "Razão social", "Rating", "Score", "Taxa", "Status", "Valor", "Data"];
  return <table className="w-full border-collapse text-xs"><thead className="bg-muted"><tr>{headers.map((header) => <th className="whitespace-nowrap border-b border-border px-2.5 py-2 text-left text-[11px] font-medium text-muted-foreground" key={header}>{header}</th>)}</tr></thead><tbody>{loading || error || items.length === 0 ? <tr><td className={cn("h-28 text-center text-sm", error ? "text-red-700" : "text-muted-foreground")} colSpan={headers.length}>{loading ? "Carregando análises manuais..." : error ? "Não foi possível carregar as análises manuais." : "Nenhuma análise manual encontrada."}</td></tr> : items.map((item) => <tr className="cursor-pointer hover:bg-muted/80" key={item.id} onClick={() => onOpen(item.id)}><td className="border-b border-border px-2.5 py-2 font-mono text-[11px]">{formatCnpj(item.cnpj)}</td><td className="whitespace-normal border-b border-border px-2.5 py-2 text-[11px]" title={item.razao_social ?? item.cnpj}>{item.razao_social || formatCnpj(item.cnpj)}</td><td className="border-b border-border px-2.5 py-2"><RatingBadge rating={item.rating} /></td><td className="border-b border-border px-2.5 py-2 font-mono">{formatScore(item.score)}</td><td className="border-b border-border px-2.5 py-2">{formatTaxaAm(item.taxa_sugerida)}</td><td className="border-b border-border px-2.5 py-2">{item.status}</td><td className="border-b border-border px-2.5 py-2">{formatBrl(item.valor_solicitado)}</td><td className="border-b border-border px-2.5 py-2">{formatDate(item.created_at)}</td></tr>)}</tbody></table>;
}
