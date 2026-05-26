import type { ReactNode } from "react";

import { AdminSidebar } from "@/components/admin-sidebar";

export default function AdminLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <div className="min-h-dvh bg-background">
      <AdminSidebar />
      <main className="ml-[200px] min-h-dvh">{children}</main>
    </div>
  );
}
