"use client";

import { type ReactNode, useEffect, useState } from "react";

type IdleWarningModalProps = {
  open: boolean;
  secondsRemaining: number;
  onStayLoggedIn: () => void;
};

function Dialog({ children, open }: { children: ReactNode; open: boolean }) {
  if (!open) {
    return null;
  }
  return (
    <div
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 px-4"
      role="dialog"
    >
      {children}
    </div>
  );
}

function DialogContent({ children }: { children: ReactNode }) {
  return (
    <div className="w-full max-w-md rounded-lg border border-border bg-background p-5 shadow-lg">
      {children}
    </div>
  );
}

function DialogHeader({ children }: { children: ReactNode }) {
  return <div className="mb-4 space-y-2">{children}</div>;
}

function DialogTitle({ children }: { children: ReactNode }) {
  return <h2 className="text-base font-semibold text-foreground">{children}</h2>;
}

function DialogDescription({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

function Button({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      className="inline-flex h-9 items-center justify-center rounded-md bg-foreground px-4 text-sm font-medium text-background hover:bg-foreground/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  );
}

export function IdleWarningModal({
  open,
  secondsRemaining,
  onStayLoggedIn,
}: IdleWarningModalProps) {
  const [remaining, setRemaining] = useState(secondsRemaining);

  useEffect(() => {
    if (!open) {
      return;
    }

    setRemaining(secondsRemaining);
    const interval = window.setInterval(() => {
      setRemaining((value) => Math.max(value - 1, 0));
    }, 1000);

    return () => window.clearInterval(interval);
  }, [open, secondsRemaining]);

  return (
    <Dialog open={open}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Sessão prestes a expirar</DialogTitle>
          <DialogDescription>
            Sua sessão expira em {remaining} segundos por inatividade.
          </DialogDescription>
        </DialogHeader>
        <div className="flex justify-end">
          <Button onClick={onStayLoggedIn}>Continuar conectado</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
