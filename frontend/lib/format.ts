export function formatTaxaAm(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) {
    return "—";
  }

  const display = rate < 1 ? rate * 100 : rate;
  return `${display.toLocaleString("pt-BR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}% a.m.`;
}
