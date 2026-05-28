"use client";

import { useQuery } from "@tanstack/react-query";

import { getAlcadas } from "@/lib/api";
import type { AlcadaConfig, Rating, UserRole } from "@/lib/types";
import { useSession } from "@/hooks/use-session";

interface UseAlcadaReturn {
  config: AlcadaConfig | null;
  podeAprovar: (valor: number | null, rating: Rating | null) => boolean;
  podeOverride: (valor: number | null, novoRating?: Rating) => boolean;
  precisaEscalar: (valor: number | null, rating: Rating | null) => boolean;
  roleEscalaDest: () => "gerente" | "diretor" | null;
}

const ratingRank: Record<Rating, number> = {
  A: 1,
  B: 2,
  C: 3,
  D: 4,
  E: 5,
};

function ratingAllowed(current: Rating | null | undefined, max: Rating | null | undefined) {
  if (!current || !max) {
    return true;
  }
  return ratingRank[current] <= ratingRank[max];
}

function roleOrder(role: UserRole | undefined) {
  if (role === "diretor") {
    return 3;
  }
  if (role === "gerente") {
    return 2;
  }
  return 1;
}

export function useAlcada(): UseAlcadaReturn {
  const { session } = useSession();
  const query = useQuery({
    queryFn: getAlcadas,
    queryKey: ["alcadas"],
    staleTime: 60_000,
  });
  const configs = query.data ?? [];
  const config =
    configs.find((item) => item.role === session?.user.role) ?? null;

  function podeAprovar(valor: number | null, rating: Rating | null) {
    if (!config) {
      return false;
    }
    const amount = valor ?? 0;
    return amount <= Number(config.max_valor) && ratingAllowed(rating, config.max_rating);
  }

  function podeOverride(valor: number | null, novoRating?: Rating) {
    if (!config || !config.pode_override) {
      return false;
    }
    const amount = valor ?? 0;
    const maxAmount = config.override_max_valor ?? config.max_valor;
    return amount <= Number(maxAmount) && ratingAllowed(novoRating, config.override_max_rating);
  }

  function precisaEscalar(valor: number | null, rating: Rating | null) {
    return !podeAprovar(valor, rating);
  }

  function roleEscalaDest(): "gerente" | "diretor" | null {
    const current = roleOrder(session?.user.role);
    if (current < 2) {
      return "gerente";
    }
    if (current < 3) {
      return "diretor";
    }
    return null;
  }

  return { config, podeAprovar, podeOverride, precisaEscalar, roleEscalaDest };
}
