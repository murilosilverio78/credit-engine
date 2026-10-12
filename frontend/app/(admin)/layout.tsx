import type { ReactNode } from "react";

import { AdminAuthGate } from "@/components/admin-auth-gate";
import { AdminSidebar } from "@/components/admin-sidebar";

export default function AdminLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <AdminAuthGate>
      <div className="min-h-dvh bg-background">
        <AdminSidebar />
        <main className="ml-[200px] min-h-dvh">{children}</main>
      </div>
    </AdminAuthGate>
  );
}
