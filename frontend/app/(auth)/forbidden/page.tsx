import Link from "next/link";

export default function ForbiddenPage() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-muted/40 px-5">
      <div className="w-full max-w-sm rounded-lg border-[0.5px] border-border bg-background p-5 text-center">
        <p className="mb-1 text-[15px] font-medium text-foreground">
          Acesso restrito
        </p>
        <p className="mb-4 text-xs leading-5 text-muted-foreground">
          Esta área exige perfil diretor.
        </p>
        <Link
          className="inline-flex h-9 items-center justify-center rounded-md border-[0.5px] border-border px-3 text-xs text-foreground hover:bg-muted"
          href="/operations"
        >
          Voltar para operações
        </Link>
      </div>
    </div>
  );
}
