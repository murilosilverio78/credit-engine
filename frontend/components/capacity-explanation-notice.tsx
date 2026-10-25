import { AlertTriangle, Info } from "lucide-react";

import type { CapacidadeExplicacao } from "@/lib/types";
import { cn } from "@/lib/utils";

export function CapacityExplanationNotice({
  explanation,
  className,
}: {
  explanation?: CapacidadeExplicacao | null;
  className?: string;
}) {
  if (!explanation) {
    return null;
  }

  const isAlert = explanation.nivel === "ALERTA";
  const Icon = isAlert ? AlertTriangle : Info;

  return (
    <div
      className={cn(
        "mt-3 flex gap-2 rounded-md border px-3 py-2 text-sm",
        isAlert
          ? "border-amber-200 bg-amber-50 text-amber-900"
          : "border-sky-200 bg-sky-50 text-sky-900",
        className,
      )}
    >
      <Icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <p className="font-medium">{explanation.titulo}</p>
        <p className="mt-0.5 leading-5">{explanation.detalhe}</p>
      </div>
    </div>
  );
}
