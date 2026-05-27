"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Switch } from "@/components/ui/switch";
import { getComponents, toggleComponent } from "@/lib/api";
import type { Component } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ComponentGroup {
  components: string[];
  title: string;
}

const groups: ComponentGroup[] = [
  {
    title: "Dados cadastrais & sanções",
    components: [
      "brasil_api",
      "pessoa_juridica",
      "ceis",
      "cnep",
      "cepim",
      "contratos",
      "recursos_recebidos",
      "acordos_leniencia",
    ],
  },
  {
    title: "Certidões",
    components: ["cndt_tst", "cnd_federal", "fgts"],
  },
  {
    title: "Enriquecimento & score",
    components: [
      "web_research",
      "score_engine",
      "serasa_pj",
      "boa_vista",
      "serpro",
    ],
  },
];

const ROADMAP_COMPONENTS = ["serasa_pj", "boa_vista", "serpro"];

type ComponentType = "auto" | "manual" | "disabled" | "roadmap";

function componentType(component: Component): ComponentType {
  if (ROADMAP_COMPONENTS.includes(component.component)) {
    return "roadmap";
  }

  if (!component.enabled) {
    return "disabled";
  }

  return component.timeout_seconds === 0 ? "manual" : "auto";
}

function typeStyle(type: ComponentType) {
  switch (type) {
    case "auto":
      return "bg-emerald-100 text-emerald-800";
    case "manual":
      return "bg-amber-100 text-amber-800";
    case "disabled":
      return "bg-muted text-muted-foreground";
    case "roadmap":
      return "bg-muted text-muted-foreground";
  }
}

function formatTimeout(component: Component) {
  return component.timeout_seconds === 0 ? "—" : `${component.timeout_seconds}s`;
}

function formatRetry(component: Component) {
  return component.timeout_seconds === 0 ? "—" : String(component.max_retries);
}

function ComponentTable({
  components,
  onToggle,
  pendingComponent,
}: {
  components: Component[];
  onToggle: (component: Component, enabled: boolean) => void;
  pendingComponent: string | null;
}) {
  return (
    <div className="mb-3.5 overflow-hidden rounded-md border-[0.5px] border-border bg-background">
      <table className="w-full table-fixed border-collapse">
        <thead className="bg-muted">
          <tr>
            <th className="w-[175px] border-b-[0.5px] border-border px-2.5 py-2 text-left text-[10px] font-medium text-muted-foreground">
              Componente
            </th>
            <th className="border-b-[0.5px] border-border px-2.5 py-2 text-left text-[10px] font-medium text-muted-foreground">
              Descrição
            </th>
            <th className="w-[80px] border-b-[0.5px] border-border px-2.5 py-2 text-left text-[10px] font-medium text-muted-foreground">
              Tipo
            </th>
            <th className="w-[72px] border-b-[0.5px] border-border px-2.5 py-2 text-left text-[10px] font-medium text-muted-foreground">
              Timeout
            </th>
            <th className="w-[60px] border-b-[0.5px] border-border px-2.5 py-2 text-left text-[10px] font-medium text-muted-foreground">
              Retry
            </th>
            <th className="w-[62px] border-b-[0.5px] border-border px-2.5 py-2 text-left text-[10px] font-medium text-muted-foreground">
              Ativo
            </th>
          </tr>
        </thead>
        <tbody className="[&>tr:last-child>td]:border-b-0">
          {components.length === 0 ? (
            <tr>
              <td
                className="px-2.5 py-5 text-center text-[11px] text-muted-foreground"
                colSpan={6}
              >
                Nenhum componente configurado neste grupo.
              </td>
            </tr>
          ) : components.map((component) => {
            const type = componentType(component);
            const roadmap = type === "roadmap";

            return (
              <tr className={cn(roadmap && "pointer-events-none opacity-40")} key={component.component}>
                <td className="border-b-[0.5px] border-border px-2.5 py-2 font-mono text-[11px] text-foreground">
                  {component.component}
                </td>
                <td className="border-b-[0.5px] border-border px-2.5 py-2 text-[11px] text-muted-foreground">
                  {component.description}
                </td>
                <td className="border-b-[0.5px] border-border px-2.5 py-2">
                  <span
                    className={cn(
                      "inline-flex rounded px-2 py-0.5 text-[10px] font-medium",
                      typeStyle(type),
                    )}
                  >
                    {type === "disabled" ? "desabilitado" : type}
                  </span>
                </td>
                <td className="border-b-[0.5px] border-border px-2.5 py-2 font-mono text-[11px] text-foreground">
                  {formatTimeout(component)}
                </td>
                <td className="border-b-[0.5px] border-border px-2.5 py-2 font-mono text-[11px] text-foreground">
                  {formatRetry(component)}
                </td>
                <td className="border-b-[0.5px] border-border px-2.5 py-2">
                  <Switch
                    aria-label={`${component.enabled ? "Desabilitar" : "Habilitar"} ${component.component}`}
                    checked={component.enabled}
                    disabled={roadmap || pendingComponent === component.component}
                    onCheckedChange={(checked) => onToggle(component, checked)}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function ComponentsPage() {
  const queryClient = useQueryClient();
  const [toast, setToast] = useState("");
  const componentsQuery = useQuery({
    queryFn: getComponents,
    queryKey: ["components"],
  });
  const toggleMutation = useMutation({
    mutationFn: ({
      component,
      enabled,
    }: {
      component: string;
      enabled: boolean;
    }) => toggleComponent(component, enabled),
    onMutate: async ({ component, enabled }) => {
      await queryClient.cancelQueries({ queryKey: ["components"] });
      const previous = queryClient.getQueryData<Component[]>(["components"]);

      queryClient.setQueryData<Component[]>(["components"], (items = []) =>
        items.map((item) =>
          item.component === component ? { ...item, enabled } : item,
        ),
      );

      return { previous };
    },
    onError: (_, __, context) => {
      queryClient.setQueryData(["components"], context?.previous);
      setToast("Não foi possível atualizar o componente.");
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["components"] });
    },
  });

  useEffect(() => {
    if (!toast) {
      return;
    }

    const timeout = window.setTimeout(() => setToast(""), 4000);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const items = componentsQuery.data ?? [];

  return (
    <div className="relative flex min-h-dvh flex-col bg-muted/40">
      <header className="border-b-[0.5px] border-border bg-background px-5 py-3.5">
        <h1 className="text-[15px] font-medium text-foreground">
          Componentes da esteira
        </h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Habilite, desabilite e configure cada módulo
        </p>
      </header>
      <section className="flex-1 px-5 py-4">
        {componentsQuery.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            Carregando componentes...
          </p>
        ) : componentsQuery.isError ? (
          <p className="py-10 text-center text-sm text-red-700">
            Não foi possível carregar os componentes.
          </p>
        ) : (
          groups.map((group) => {
            const groupComponents = group.components
              .map((componentName) =>
                items.find((item) => item.component === componentName),
              )
              .filter((component): component is Component => Boolean(component));

            return (
              <div key={group.title}>
                <h2 className="mb-1.5 border-b-[0.5px] border-border py-2 text-[10px] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                  {group.title}
                </h2>
                <ComponentTable
                  components={groupComponents}
                  onToggle={(component, enabled) =>
                    toggleMutation.mutate({
                      component: component.component,
                      enabled,
                    })
                  }
                  pendingComponent={
                    toggleMutation.isPending
                      ? toggleMutation.variables?.component ?? null
                      : null
                  }
                />
              </div>
            );
          })
        )}
      </section>
      {toast ? (
        <div
          aria-live="polite"
          className="fixed bottom-5 right-5 rounded-md border-[0.5px] border-red-200 bg-background px-4 py-3 text-xs text-red-700"
          role="alert"
        >
          {toast}
        </div>
      ) : null}
    </div>
  );
}
