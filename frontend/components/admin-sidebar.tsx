"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowUpCircle,
  FileSearch,
  Plus,
  SlidersHorizontal,
  Users,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useSession } from "@/hooks/use-session";
import { clearAuthToken } from "@/lib/auth-token";
import { getHealth, getPendingEscaladas, getPendingOverrides } from "@/lib/api";
import { cn } from "@/lib/utils";

interface NavigationItem {
  href: string;
  icon: LucideIcon;
  label: string;
  showPendingCount?: boolean;
  directorOnly?: boolean;
  pendingType?: "escaladas" | "overrides";
}

const navigation: NavigationItem[] = [
  { href: "/operations", icon: FileSearch, label: "Operações" },
  { href: "/operations/new", icon: Plus, label: "Nova análise" },
  {
    href: "/overrides",
    icon: AlertTriangle,
    label: "Overrides",
    showPendingCount: true,
    pendingType: "overrides",
  },
  {
    href: "/escaladas",
    icon: ArrowUpCircle,
    label: "Escaladas",
    showPendingCount: true,
    pendingType: "escaladas",
  },
  {
    href: "/components",
    icon: SlidersHorizontal,
    label: "Componentes",
  },
  {
    href: "/settings/alcadas",
    icon: SlidersHorizontal,
    label: "Alçadas",
    directorOnly: true,
  },
  {
    href: "/settings/pricing",
    icon: SlidersHorizontal,
    label: "Precificação",
    directorOnly: true,
  },
  {
    href: "/settings/users",
    icon: Users,
    label: "Usuários",
    directorOnly: true,
  },
];

function isActivePath(pathname: string, href: string) {
  if (href === "/operations") {
    return (
      pathname === "/operations" ||
      (pathname.startsWith("/operations/") && pathname !== "/operations/new")
    );
  }

  return pathname === href;
}

function navTestId(href: string) {
  const route = href.split("/").filter(Boolean).at(-1) || "home";
  return `nav-${route}`;
}

export function AdminSidebar() {
  const pathname = usePathname();
  const { session } = useSession();
  const { data: pendingOverrides = [] } = useQuery({
    queryKey: ["overrides", "pending"],
    queryFn: getPendingOverrides,
    enabled: Boolean(session),
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  });
  const { data: pendingEscaladas = [] } = useQuery({
    queryKey: ["escaladas", "pendentes"],
    queryFn: getPendingEscaladas,
    enabled: Boolean(session),
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  });
  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 60_000,
    refetchIntervalInBackground: true,
    retry: false,
  });

  const healthStatus = healthQuery.isSuccess
    ? { color: "bg-emerald-500", label: "API online" }
    : healthQuery.isError
      ? { color: "bg-red-500", label: "API offline" }
      : { color: "bg-muted-foreground/50", label: "Verificando API" };
  const alcadaLabel =
    session?.user.alcada === "committee"
      ? "comitê"
      : session?.user.alcada === "manager"
        ? "gerente"
        : "analista";
  const alcadaDot =
    session?.user.alcada === "committee"
      ? "bg-amber-500"
      : session?.user.alcada === "manager"
        ? "bg-blue-500"
        : "bg-muted-foreground/50";

  async function logout() {
    clearAuthToken();
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  }

  return (
    <aside className="fixed inset-y-0 left-0 flex w-[200px] flex-col border-r-[0.5px] border-border bg-background">
      <div className="border-b-[0.5px] border-border px-4 py-4">
        <p className="truncate text-[13px] font-medium text-foreground">
          Credit Engine
        </p>
        <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
          AntecipaGov
        </p>
      </div>

      <nav aria-label="Navegação principal" className="flex-1 py-2">
        <ul>
          {navigation.filter((item) => !item.directorOnly || session?.user.role === "diretor").map((item) => {
            const active = isActivePath(pathname, item.href);
            const Icon = item.icon;
            const pendingCount =
              item.pendingType === "escaladas"
                ? pendingEscaladas.length
                : pendingOverrides.length;

            return (
              <li key={item.href}>
                <Link
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex h-9 items-center gap-2 px-3.5 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                    active
                      ? "bg-muted font-medium text-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                  data-testid={navTestId(item.href)}
                  href={item.href}
                >
                  <Icon aria-hidden="true" className="h-4 w-4 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{item.label}</span>
                  {item.showPendingCount ? (
                    <span
                      aria-label={`${pendingCount} pendências`}
                      className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium tabular-nums leading-none text-amber-800"
                    >
                      {pendingCount}
                    </span>
                  ) : null}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {session ? (
        <div className="border-t-[0.5px] border-border px-3.5 py-3">
          <p className="truncate text-[11px] font-medium text-foreground">
            {session.user.name || session.user.email}
          </p>
          <p className="mt-0.5 flex items-center gap-1 text-[10px] text-muted-foreground">
            <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", alcadaDot)} />
            {alcadaLabel}
            <button
              className="ml-auto text-[10px] hover:text-foreground"
              onClick={logout}
              type="button"
            >
              Sair
            </button>
          </p>
        </div>
      ) : null}

      <div className="border-t-[0.5px] border-border px-3.5 py-3">
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <span
            aria-hidden="true"
            className={cn("h-1.5 w-1.5 rounded-full", healthStatus.color)}
          />
          <span>{healthStatus.label}</span>
        </div>
      </div>
    </aside>
  );
}
