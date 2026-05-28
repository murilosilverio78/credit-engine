"use client";

import {
  Circle,
  Document,
  Font,
  G,
  Line,
  Page,
  Polygon,
  Rect,
  StyleSheet,
  Svg,
  Text,
  View,
  pdf,
} from "@react-pdf/renderer";

import type { ComponentSnapshot, OperationDetails, Rating } from "@/lib/types";

type JsonRecord = Record<string, unknown>;

Font.register({
  family: "Geist",
  fonts: [
    {
      fontWeight: 400,
      src: "https://fonts.gstatic.com/s/geist/v1/UcCO3FwrK3iLTcvneQg7Ca725JhhKnNqk4j1ebLhAm8SrXTc2dphjZ-Ik-7sw3Lz.woff",
    },
    {
      fontWeight: 500,
      src: "https://fonts.gstatic.com/s/geist/v1/UcCO3FwrK3iLTcvneQg7Ca725JhhKnNqk4j1ebLhAm8SrXTc2dphjZ-Ik-7AwnLz.woff",
    },
  ],
});

interface Dimension {
  justificativa?: string;
  peso?: number;
  score?: number;
}

const dimensionOrder = [
  "regularidade_fiscal",
  "saude_cadastral",
  "relacionamento_governamental",
  "porte_operacionalidade",
  "reputacao_mercado",
];

const dimensionLabels: Record<string, string> = {
  regularidade_fiscal: "Regularidade fiscal",
  saude_cadastral: "Saúde cadastral",
  relacionamento_governamental: "Relacionamento gov.",
  porte_operacionalidade: "Porte / oper.",
  reputacao_mercado: "Reputação mercado",
};

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

const styles = StyleSheet.create({
  page: {
    backgroundColor: "#FFFFFF",
    color: "#2C2C2A",
    fontFamily: "Geist",
    fontSize: 9,
    fontWeight: 400,
    lineHeight: 1.5,
    paddingBottom: 57,
    paddingLeft: 43,
    paddingRight: 43,
    paddingTop: 57,
  },
  footer: {
    bottom: 20,
    left: 0,
    position: "absolute",
    right: 0,
    textAlign: "center",
  },
  footerText: {
    color: "#888780",
    fontSize: 8,
    textAlign: "center",
  },
  headerTop: {
    color: "#6B7280",
    flexDirection: "row",
    fontSize: 10,
    justifyContent: "space-between",
    marginBottom: 12,
  },
  companyHeader: {
    flexDirection: "row",
    gap: 16,
    justifyContent: "space-between",
    marginBottom: 12,
  },
  companyName: {
    fontFamily: "Geist",
    fontSize: 16,
    fontWeight: 500,
    marginBottom: 5,
  },
  companyMeta: {
    color: "#6B7280",
    fontSize: 9,
    lineHeight: 1.35,
  },
  ratingBadge: {
    alignItems: "center",
    backgroundColor: "#E6F1FB",
    borderRadius: 6,
    height: 40,
    justifyContent: "center",
    width: 40,
  },
  ratingBadgeText: {
    color: "#0C447C",
    fontFamily: "Geist",
    fontSize: 24,
    fontWeight: 500,
    lineHeight: 1,
    textAlign: "center",
  },
  metricsRow: {
    flexDirection: "row",
    gap: 8,
    marginBottom: 18,
  },
  metricCard: {
    backgroundColor: "#F5F5F5",
    borderRadius: 4,
    flexGrow: 1,
    padding: 8,
  },
  metricLabel: {
    color: "#6B7280",
    fontSize: 8,
    marginBottom: 4,
  },
  metricValue: {
    color: "#2C2C2A",
    fontFamily: "Geist",
    fontSize: 16,
    fontWeight: 500,
  },
  sectionTitle: {
    color: "#5F5E5A",
    fontFamily: "Geist",
    fontSize: 8,
    fontWeight: 500,
    letterSpacing: 0.5,
    marginBottom: 8,
    marginTop: 12,
    textTransform: "uppercase",
  },
  scorecard: {
    flexDirection: "row",
    gap: 16,
    marginBottom: 8,
  },
  radarColumn: {
    width: "40%",
  },
  dimensionsColumn: {
    flexGrow: 1,
    width: "60%",
  },
  dimensionItem: {
    marginBottom: 7,
  },
  dimensionHeader: {
    flexDirection: "row",
    fontSize: 9,
    justifyContent: "space-between",
    marginBottom: 3,
  },
  progressTrack: {
    backgroundColor: "#E5E7EB",
    borderRadius: 8,
    height: 5,
  },
  progressFill: {
    borderRadius: 8,
    height: 5,
  },
  opinionBox: {
    borderLeftColor: "#639922",
    borderLeftWidth: 3,
    marginBottom: 12,
    paddingLeft: 10,
    paddingVertical: 3,
  },
  conclusion: {
    fontFamily: "Geist",
    fontSize: 8,
    fontWeight: 500,
    marginBottom: 5,
    textTransform: "uppercase",
  },
  opinionText: {
    color: "#2C2C2A",
    fontSize: 9,
    lineHeight: 1.5,
  },
  twoColumns: {
    flexDirection: "row",
    gap: 12,
  },
  column: {
    flexGrow: 1,
    width: "50%",
  },
  listItem: {
    flexDirection: "row",
    fontSize: 9,
    lineHeight: 1.35,
    marginBottom: 4,
  },
  listBullet: {
    borderRadius: 2.5,
    height: 5,
    marginRight: 6,
    marginTop: 4,
    width: 5,
  },
  annexTitleBox: {
    borderTopColor: "#0C447C",
    borderTopWidth: 1.5,
    marginBottom: 14,
    paddingTop: 8,
  },
  annexTitle: {
    color: "#0C447C",
    fontFamily: "Geist",
    fontSize: 12,
    fontWeight: 500,
  },
  annexSection: {
    marginBottom: 12,
  },
  annexSectionTitle: {
    color: "#5F5E5A",
    fontFamily: "Geist",
    fontSize: 10,
    fontWeight: 500,
    marginBottom: 5,
  },
  grid2: {
    flexDirection: "row",
    flexWrap: "wrap",
  },
  dataRow: {
    flexDirection: "row",
    marginBottom: 3,
    paddingRight: 8,
    width: "50%",
  },
  rowLabel: {
    color: "#888780",
    fontSize: 8,
    width: "38%",
  },
  rowValue: {
    color: "#2C2C2A",
    fontFamily: "Geist",
    fontSize: 9,
    fontWeight: 500,
    width: "62%",
  },
  table: {
    borderColor: "#D1D5DB",
    borderTopWidth: 1,
    marginTop: 5,
  },
  tableRow: {
    borderBottomColor: "#D1D5DB",
    borderBottomWidth: 1,
    flexDirection: "row",
    minHeight: 18,
  },
  tableHead: {
    backgroundColor: "#F3F4F6",
    fontFamily: "Geist",
    fontWeight: 500,
  },
  tableCell: {
    fontSize: 8,
    padding: 4,
  },
  muted: {
    color: "#6B7280",
  },
});

function asRecord(value: unknown): JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function numberValue(value: unknown) {
  return typeof value === "number" ? value : Number(value) || 0;
}

function decodeText(value: string) {
  let result = value;
  for (let pass = 0; pass < 2 && /Ãƒ|Ã‚|Ã¢|Ã°|�/.test(result); pass += 1) {
    try {
      const corrected = decodeURIComponent(escape(result));
      if (corrected === result) break;
      result = corrected;
    } catch {
      break;
    }
  }
  return result;
}

function decodedValue<T>(value: T): T {
  if (typeof value === "string") return decodeText(value) as T;
  if (Array.isArray(value)) return value.map((item) => decodedValue(item)) as T;
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, decodedValue(item)]),
    ) as T;
  }
  return value;
}

function textValue(value: unknown, fallback = "-"): string {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  if (Array.isArray(value)) {
    return value.map((item) => textValue(item, "")).filter(Boolean).join("; ") || fallback;
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function formatCnpj(cnpj: unknown) {
  const value = textValue(cnpj, "");
  const digits = value.replace(/\D/g, "");
  return digits.length === 14
    ? digits.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5")
    : value || "-";
}

function formatCurrency(value: unknown) {
  return new Intl.NumberFormat("pt-BR", {
    currency: "BRL",
    maximumFractionDigits: 2,
    style: "currency",
  }).format(numberValue(value));
}

function formatDate(value: unknown) {
  if (!value) return "-";
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return textValue(value);
  return new Intl.DateTimeFormat("pt-BR").format(date);
}

function formatDateTime(value: Date) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(value);
}

function formatPercent(value: unknown, decimals = 0) {
  return `${(numberValue(value) * 100).toLocaleString("pt-BR", {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  })}%`;
}

function suggestedRate(operation: OperationDetails, engine: JsonRecord) {
  const rawRate = operation.taxa_sugerida ?? numberValue(engine.taxa_sugerida_am);
  const rate = rawRate < 1 ? rawRate * 100 : rawRate;
  return `${new Intl.NumberFormat("pt-BR", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 1,
  }).format(rate)}% a.m.`;
}

function truncate(value: unknown, maxLength: number) {
  const text = textValue(value);
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function formatTaxRegime(value: unknown) {
  const records = asArray(value);
  if (records.length) {
    return records
      .map((item) => {
        const record = asRecord(item);
        return `${textValue(record.ano)}: ${textValue(record.forma ?? record.regime ?? record.descricao)}`;
      })
      .join(" | ");
  }
  return textValue(value);
}

function formatCertificateResult(value: unknown) {
  switch (value) {
    case "positiva_com_efeitos_negativa":
      return "Positiva com efeitos de negativa";
    case "negativa":
      return "Negativa";
    case "positiva":
      return "Positiva";
    default:
      return textValue(value);
  }
}

function formatContractStatus(contract: JsonRecord) {
  if (typeof contract.ativo === "boolean") {
    return contract.ativo ? "ativo" : "encerrado";
  }
  return textValue(contract.status);
}

function snapshotsMap(operation: OperationDetails) {
  return new Map(
    (operation.components ?? []).map((snapshot) => [
      snapshot.component,
      decodedValue(snapshot),
    ]),
  );
}

function conclusion(rating: Rating | null) {
  if (rating === "A" || rating === "B") {
    return { color: "#27500A", label: "CONCLUSÃO — PERFIL FAVORÁVEL" };
  }
  if (rating === "C") {
    return { color: "#633806", label: "CONCLUSÃO — PERFIL NEUTRO" };
  }
  return { color: "#791F1F", label: "CONCLUSÃO — PERFIL RESTRITIVO" };
}

function Footer({ generatedAt }: { generatedAt: Date }) {
  return (
    <View fixed style={styles.footer}>
      <Text style={styles.footerText}>
        Gerado em {formatDateTime(generatedAt)} · Credit Engine AntecipaGov · Confidencial
      </Text>
    </View>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metricCard}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={styles.metricValue}>{value}</Text>
    </View>
  );
}

function DataRow({ label, value }: { label: string; value: unknown }) {
  return (
    <View style={styles.dataRow}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue}>{textValue(value)}</Text>
    </View>
  );
}

function RadarChartPdf({ dimensions }: { dimensions: [string, Dimension][] }) {
  const labels = [
    ["Regularidade", "fiscal"],
    ["Saúde", "cadastral"],
    ["Relacionamento", "gov."],
    ["Porte /", "oper."],
    ["Reputação", "mercado"],
  ];
  const center = 75;
  const radius = 48;
  const axisPoints = dimensionOrder.map((_, index) => {
    const angle = -Math.PI / 2 + (index * 2 * Math.PI) / dimensionOrder.length;
    return { x: center + Math.cos(angle) * radius, y: center + Math.sin(angle) * radius };
  });
  const dataPoints = dimensionOrder.map((key, index) => {
    const dimension = dimensions.find(([name]) => name === key)?.[1] ?? {};
    const score = Math.min(Math.max(numberValue(dimension.score), 0), 100);
    const angle = -Math.PI / 2 + (index * 2 * Math.PI) / dimensionOrder.length;
    const scaled = (radius * score) / 100;
    return { x: center + Math.cos(angle) * scaled, y: center + Math.sin(angle) * scaled };
  });
  const toPoints = (points: Array<{ x: number; y: number }>) =>
    points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");

  return (
    <Svg height={150} viewBox="0 0 150 150" width={150}>
      {[0.25, 0.5, 0.75, 1].map((scale) => (
        <Polygon
          fill="none"
          key={scale}
          points={toPoints(axisPoints.map((point) => ({
            x: center + (point.x - center) * scale,
            y: center + (point.y - center) * scale,
          })))}
          stroke="#D8DEE5"
          strokeWidth={scale === 1 ? 1 : 0.5}
        />
      ))}
      {axisPoints.map((point, index) => (
        <Line
          key={index}
          stroke="#D8DEE5"
          strokeWidth={0.5}
          x1={center}
          x2={point.x}
          y1={center}
          y2={point.y}
        />
      ))}
      <Polygon
        fill="#EAF3DE"
        points={toPoints(dataPoints)}
        stroke="#639922"
        strokeWidth={1.5}
      />
      {dataPoints.map((point, index) => (
        <Circle cx={point.x} cy={point.y} fill="#639922" key={index} r={2.2} />
      ))}
      {axisPoints.map((point, index) => {
        const offsetX = point.x < center - 6 ? -7 : point.x > center + 6 ? 7 : 0;
        const offsetY = point.y < center - 6 ? -7 : point.y > center + 6 ? 10 : 0;
        return (
          <Text
            key={index}
            style={{ fill: "#6B7280", fontSize: 6 }}
            textAnchor={offsetX < 0 ? "end" : offsetX > 0 ? "start" : "middle"}
            x={point.x + offsetX}
            y={point.y + offsetY}
          >
            {labels[index].join(" ")}
          </Text>
        );
      })}
    </Svg>
  );
}

function ScorecardPdf({ dimensions }: { dimensions: [string, Dimension][] }) {
  const listedDimensions = dimensionOrder.map((key) => [
    key,
    dimensions.find(([name]) => name === key)?.[1] ?? {},
  ] as [string, Dimension]);

  return (
    <View>
      <Text style={styles.sectionTitle}>SCORECARD — 5 DIMENSÕES</Text>
      <View style={styles.scorecard}>
        <View style={styles.radarColumn}>
          <RadarChartPdf dimensions={listedDimensions} />
        </View>
        <View style={styles.dimensionsColumn}>
          {listedDimensions.map(([key, dimension]) => {
            const score = Math.min(Math.max(numberValue(dimension.score), 0), 100);
            const favorable = score >= 75;
            return (
              <View key={key} style={styles.dimensionItem}>
                <View style={styles.dimensionHeader}>
                  <Text>{dimensionLabels[key]}</Text>
                  <Text style={{ color: favorable ? "#27500A" : "#633806", fontFamily: "Geist", fontWeight: 500 }}>
                    {score} · Peso {formatPercent(dimension.peso)}
                  </Text>
                </View>
                <View style={styles.progressTrack}>
                  <View
                    style={[
                      styles.progressFill,
                      { backgroundColor: favorable ? "#639922" : "#BA7517", width: `${score}%` },
                    ]}
                  />
                </View>
              </View>
            );
          })}
        </View>
      </View>
    </View>
  );
}

function OpinionPdf({ engine, rating }: { engine: JsonRecord; rating: Rating | null }) {
  const status = conclusion(rating);
  return (
    <View>
      <Text style={styles.sectionTitle}>PARECER DO AGENTE</Text>
      <View style={styles.opinionBox}>
        <Text style={[styles.conclusion, { color: status.color }]}>{status.label}</Text>
        <Text style={styles.opinionText}>{textValue(engine.parecer, "Parecer não disponível.")}</Text>
      </View>
    </View>
  );
}

function PointsPdf({ engine }: { engine: JsonRecord }) {
  const positive = asArray(engine.pontos_positivos);
  const attention = asArray(engine.pontos_atencao);
  return (
    <View>
      <Text style={styles.sectionTitle}>PONTOS POSITIVOS E DE ATENÇÃO</Text>
      <View style={styles.twoColumns}>
        <View style={styles.column}>
          <Text style={[styles.annexSectionTitle, { color: "#27500A" }]}>Pontos positivos</Text>
          {positive.map((point, index) => (
            <View key={index} style={styles.listItem}>
              <View style={[styles.listBullet, { backgroundColor: "#639922" }]} />
              <Text>{textValue(point)}</Text>
            </View>
          ))}
        </View>
        <View style={styles.column}>
          <Text style={[styles.annexSectionTitle, { color: "#633806" }]}>Pontos de atenção</Text>
          {attention.map((point, index) => (
            <View key={index} style={styles.listItem}>
              <View style={[styles.listBullet, { backgroundColor: "#BA7517" }]} />
              <Text>{textValue(point)}</Text>
            </View>
          ))}
        </View>
      </View>
    </View>
  );
}

function Table({
  cellFontSize = 8,
  columns,
  rowBackgrounds,
  rows,
  widths,
}: {
  cellFontSize?: number;
  columns: string[];
  rowBackgrounds?: string[];
  rows: string[][];
  widths: string[];
}) {
  return (
    <View style={styles.table}>
      <View style={[styles.tableRow, styles.tableHead]}>
        {columns.map((column, index) => (
          <Text key={column} style={[styles.tableCell, { width: widths[index] }]}>
            {column}
          </Text>
        ))}
      </View>
      {rows.map((row, rowIndex) => (
        <View
          key={rowIndex}
          style={[
            styles.tableRow,
            rowBackgrounds?.[rowIndex] ? { backgroundColor: rowBackgrounds[rowIndex] } : {},
          ]}
        >
          {row.map((cell, index) => (
            <Text key={`${rowIndex}-${index}`} style={[styles.tableCell, { fontSize: cellFontSize, width: widths[index] }]}>
              {cell}
            </Text>
          ))}
        </View>
      ))}
    </View>
  );
}

function BrasilApiAnnex({ result }: { result: JsonRecord }) {
  const qsa = asArray(result.qsa)
    .slice(0, 6)
    .map((item) => {
      const partner = asRecord(item);
      return `${textValue(partner.nome_socio ?? partner.nome)} (${textValue(partner.qualificacao_socio ?? partner.qualificacao)})`;
    })
    .join("; ");
  const secondary = asArray(result.atividades_secundarias)
    .slice(0, 3)
    .map((item) => textValue(asRecord(item).text ?? asRecord(item).descricao ?? item))
    .join("; ");

  return (
    <View style={styles.annexSection}>
      <Text style={styles.annexSectionTitle}>brasil_api</Text>
      <View style={styles.grid2}>
        <DataRow label="Razão social" value={result.razao_social} />
        <DataRow label="CNPJ" value={formatCnpj(result.cnpj)} />
        <DataRow label="Porte" value={result.porte} />
        <DataRow label="Capital social" value={formatCurrency(result.capital_social)} />
        <DataRow label="Data abertura" value={formatDate(result.data_abertura)} />
        <DataRow label="Situação" value={result.situacao_cadastral ?? result.descricao_situacao_cadastral} />
        <DataRow label="Regime tributário" value={formatTaxRegime(result.regime_tributario)} />
        <DataRow label="Sócios" value={qsa} />
        <DataRow label="Atividade principal" value={textValue(asRecord(result.atividade_principal).text ?? result.atividade_principal)} />
        <DataRow label="Atividades secundárias" value={secondary} />
      </View>
    </View>
  );
}

function ContractsAnnex({ result }: { result: JsonRecord }) {
  const contracts = asArray(result.contratos_detalhe ?? result.contratos).slice(0, 20);
  return (
    <View style={styles.annexSection}>
      <Text style={styles.annexSectionTitle}>contratos</Text>
      <View style={styles.grid2}>
        <DataRow label="Total" value={result.total_contratos} />
        <DataRow label="Ativos" value={result.contratos_ativos} />
        <DataRow label="Encerrados" value={result.contratos_encerrados} />
        <DataRow label="Valor ativo" value={formatCurrency(result.valor_total_ativo)} />
        <DataRow label="Histórico" value={formatCurrency(result.valor_total_historico)} />
      </View>
      <Table
        columns={["Número", "Órgão", "Valor", "Status", "Vigência"]}
        rows={contracts.map((item) => {
          const contract = asRecord(item);
          return [
            textValue(contract.numero ?? contract.numero_contrato),
            truncate(contract.orgao ?? contract.orgao_contratante, 20),
            formatCurrency(contract.valor ?? contract.valor_inicial ?? contract.valor_global),
            formatContractStatus(contract),
            `${formatDate(contract.data_inicio ?? contract.data_assinatura)} - ${formatDate(contract.data_fim ?? contract.data_vigencia_fim)}`,
          ];
        })}
        cellFontSize={7}
        rowBackgrounds={contracts.map((_, index) => (index % 2 === 0 ? "#FFFFFF" : "#F9F9F9"))}
        widths={["16%", "30%", "18%", "14%", "22%"]}
      />
    </View>
  );
}

function ResourcesBarChart({ result }: { result: JsonRecord }) {
  const monthlyMap = new Map<string, number>();
  asArray(result.recursos_detalhe).forEach((item) => {
    const resource = asRecord(item);
    const month = textValue(resource.mes, "");
    if (month) monthlyMap.set(month, (monthlyMap.get(month) ?? 0) + numberValue(resource.valor));
  });
  const monthlyValues = Array.from(monthlyMap.entries())
    .sort(([first], [second]) => first.localeCompare(second))
    .slice(-13)
    .map(([month, value]) => ({ label: `${month.slice(4, 6)}/${month.slice(0, 4)}`, value }));
  const maxValue = Math.max(...monthlyValues.map((item) => item.value), 1);
  const chartWidth = 460;
  const chartHeight = 100;
  const labelHeight = 18;
  const axisLeft = 0;
  const baseY = chartHeight - labelHeight;
  const maxBarHeight = 64;
  const barWidth = monthlyValues.length ? chartWidth / monthlyValues.length - 2 : 0;

  if (!monthlyValues.length) {
    return <Text style={[styles.muted, { fontSize: 8 }]}>Sem pagamentos mensais para exibir</Text>;
  }

  return (
    <Svg height={chartHeight} viewBox={`0 0 ${chartWidth} ${chartHeight}`} width={chartWidth}>
      <Line stroke="#CBD5E1" strokeWidth={0.8} x1={axisLeft} x2={chartWidth} y1={baseY} y2={baseY} />
      <Text style={{ fill: "#888780", fontSize: 6 }} x={0} y={8}>
        {formatCurrency(maxValue)}
      </Text>
      {monthlyValues.map((item, index) => {
        const height = (item.value / maxValue) * maxBarHeight;
        const x = index * (barWidth + 2) + 1;
        const y = baseY - height;
        return (
          <G key={item.label}>
            <Rect fill="#378ADD" height={height} style={{ fill: "#378ADD" }} width={barWidth} x={x} y={y} />
            <Text
              style={{ fill: "#888780", fontSize: 6 }}
              textAnchor="middle"
              x={x + barWidth / 2}
              y={baseY + 10}
            >
              {item.label}
            </Text>
          </G>
        );
      })}
    </Svg>
  );
}

function ResourcesAnnex({ result }: { result: JsonRecord }) {
  const annualValues = Object.entries(asRecord(result.valor_por_ano))
    .map(([year, value]) => `${year}: ${formatCurrency(value)}`)
    .join("; ");
  const valuesByAgency = new Map<string, number>();
  asArray(result.recursos_detalhe).forEach((item) => {
    const resource = asRecord(item);
    const agency = textValue(resource.orgao, "");
    if (agency) valuesByAgency.set(agency, (valuesByAgency.get(agency) ?? 0) + numberValue(resource.valor));
  });
  const agencies = Array.from(valuesByAgency.entries())
    .sort(([, first], [, second]) => second - first)
    .slice(0, 5);

  return (
    <View style={styles.annexSection}>
      <Text style={styles.annexSectionTitle}>recursos_recebidos</Text>
      <View style={styles.grid2}>
        <DataRow label="Valor total" value={formatCurrency(result.valor_total_recebido)} />
        <DataRow label="Período" value={`${textValue(result.periodo_inicio)} - ${textValue(result.periodo_fim)}`} />
        <DataRow label="Valor por ano" value={annualValues} />
      </View>
      <ResourcesBarChart result={result} />
      <Text style={[styles.annexSectionTitle, { fontSize: 8, marginTop: 4 }]}>Top órgãos pagadores</Text>
      {agencies.map(([agency, value]) => (
        <Text key={agency} style={{ fontSize: 8, marginBottom: 2 }}>
          {agency}: {formatCurrency(value)}
        </Text>
      ))}
    </View>
  );
}

function LegalEntityAnnex({ result }: { result: JsonRecord }) {
  return (
    <View style={styles.annexSection}>
      <Text style={styles.annexSectionTitle}>pessoa_juridica</Text>
      <View style={styles.grid2}>
        {Object.entries(result)
          .filter(([, value]) => typeof value === "boolean")
          .map(([key, value]) => (
            <DataRow key={key} label={legalEntityLabels[key] ?? key.replaceAll("_", " ")} value={value ? "Sim" : "Não"} />
          ))}
      </View>
    </View>
  );
}

function SanctionAnnex({ component, result }: { component: string; result: JsonRecord }) {
  const records = asArray(result.registros ?? result.acordos);
  const columns = records.length ? Object.keys(asRecord(records[0])).slice(0, 4) : [];
  return (
    <View style={styles.annexSection}>
      <Text style={styles.annexSectionTitle}>{component}</Text>
      {records.length ? (
        <Table
          columns={columns}
          rows={records.slice(0, 12).map((record) => {
            const row = asRecord(record);
            return columns.map((column) => textValue(row[column]));
          })}
          widths={columns.map(() => `${100 / columns.length}%`)}
        />
      ) : (
        <Text style={[styles.muted, { fontSize: 8 }]}>Nenhum registro</Text>
      )}
    </View>
  );
}

function CertificateAnnex({ component, result }: { component: string; result: JsonRecord }) {
  const positive = result.resultado === "positiva";
  const regular = result.resultado === "negativa" || result.resultado === "positiva_com_efeitos_negativa";
  return (
    <View style={styles.annexSection}>
      <Text style={styles.annexSectionTitle}>{component}</Text>
      <View style={styles.grid2}>
        <DataRow label="Resultado" value={formatCertificateResult(result.resultado)} />
        <DataRow label="CNPJ certidão" value={formatCnpj(result.cnpj_certidao ?? result.cnpj)} />
        <DataRow label="Emissão" value={result.data_emissao} />
        <DataRow label="Validade" value={result.data_validade} />
        <DataRow label="Órgão emissor" value={result.orgao_emissor} />
        <DataRow label="Situação" value={positive ? "Irregular" : regular ? "Regular" : "Não identificada"} />
      </View>
    </View>
  );
}

function WebResearchAnnex({ result }: { result: JsonRecord }) {
  const alerts = asArray(result.alertas ?? result.alerts).map((item) => textValue(item)).join("; ");
  return (
    <View style={styles.annexSection}>
      <Text style={styles.annexSectionTitle}>web_research</Text>
      <View style={styles.grid2}>
        <DataRow label="Nível de risco" value={result.nivel_risco ?? result.risco ?? result.score_reputacao} />
        <DataRow label="Alertas" value={alerts} />
      </View>
      <Text style={{ fontSize: 8, lineHeight: 1.35 }}>{textValue(result.resumo)}</Text>
    </View>
  );
}

function MainReportPage({
  generatedAt,
  operation,
  snapshots,
}: {
  generatedAt: Date;
  operation: OperationDetails;
  snapshots: Map<string, ComponentSnapshot>;
}) {
  const company = asRecord(snapshots.get("brasil_api")?.parsed_result);
  const contracts = asRecord(snapshots.get("contratos")?.parsed_result);
  const engine = asRecord(snapshots.get("score_engine")?.parsed_result);
  const dimensions = Object.entries(asRecord(engine.dimensoes)) as [string, Dimension][];

  return (
    <Page size="A4" style={styles.page}>
      <View style={styles.headerTop}>
        <Text>Credit Engine / AntecipaGov</Text>
        <Text>Gerado em {formatDateTime(generatedAt)}</Text>
      </View>
      <View style={styles.companyHeader}>
        <View style={{ flexGrow: 1 }}>
          <Text style={styles.companyName}>{textValue(company.razao_social, operation.razao_social ?? "-")}</Text>
          <Text style={styles.companyMeta}>
            {formatCnpj(company.cnpj ?? operation.cnpj)} · {textValue(company.porte)} · {textValue(company.municipio)}
            {company.uf ? `/${textValue(company.uf)}` : ""} · abertura {formatDate(company.data_abertura)}
          </Text>
        </View>
        {operation.rating ? (
          <View style={styles.ratingBadge}>
            <Text style={styles.ratingBadgeText}>{operation.rating}</Text>
          </View>
        ) : null}
      </View>
      <View style={styles.metricsRow}>
        <MetricCard label="Score" value={`${operation.score ?? numberValue(engine.score)}/100`} />
        <MetricCard label="Taxa sugerida" value={suggestedRate(operation, engine)} />
        <MetricCard label="Limite sugerido" value={`${formatPercent(engine.limite_sugerido_pct_contrato)} contrato`} />
        <MetricCard
          label="Contratos ativos"
          value={`${textValue(contracts.contratos_ativos)} · ${formatCurrency(contracts.valor_total_ativo)}`}
        />
      </View>
      <ScorecardPdf dimensions={dimensions} />
      <OpinionPdf engine={engine} rating={operation.rating} />
      <PointsPdf engine={engine} />
      <Footer generatedAt={generatedAt} />
    </Page>
  );
}

function AnnexPages({
  generatedAt,
  snapshots,
}: {
  generatedAt: Date;
  snapshots: Map<string, ComponentSnapshot>;
}) {
  return (
    <Page size="A4" style={styles.page}>
      <View style={styles.annexTitleBox}>
        <Text style={styles.annexTitle}>ANEXO — DADOS CONSULTADOS</Text>
      </View>
      <BrasilApiAnnex result={asRecord(snapshots.get("brasil_api")?.parsed_result)} />
      <ContractsAnnex result={asRecord(snapshots.get("contratos")?.parsed_result)} />
      <ResourcesAnnex result={asRecord(snapshots.get("recursos_recebidos")?.parsed_result)} />
      <LegalEntityAnnex result={asRecord(snapshots.get("pessoa_juridica")?.parsed_result)} />
      {["ceis", "cnep", "cepim", "acordos_leniencia"].map((component) => (
        <SanctionAnnex
          component={component}
          key={component}
          result={asRecord(snapshots.get(component)?.parsed_result)}
        />
      ))}
      {["cnd_federal", "cndt_tst", "fgts"].map((component) => (
        <CertificateAnnex
          component={component}
          key={component}
          result={asRecord(snapshots.get(component)?.parsed_result)}
        />
      ))}
      <WebResearchAnnex result={asRecord(snapshots.get("web_research")?.parsed_result)} />
      <Footer generatedAt={generatedAt} />
    </Page>
  );
}

function ReportDocument({ generatedAt, operation }: { generatedAt: Date; operation: OperationDetails }) {
  const snapshots = snapshotsMap(operation);
  return (
    <Document>
      <MainReportPage generatedAt={generatedAt} operation={operation} snapshots={snapshots} />
      <AnnexPages generatedAt={generatedAt} snapshots={snapshots} />
    </Document>
  );
}

export async function generateReportPdf(operation: OperationDetails) {
  const generatedAt = new Date();
  const blob = await pdf(<ReportDocument generatedAt={generatedAt} operation={operation} />).toBlob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const cnpj = operation.cnpj.replace(/\D/g, "");
  const date = generatedAt.toISOString().slice(0, 10).replace(/-/g, "");
  link.href = url;
  link.download = `CreditEngine_${cnpj}_${date}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
