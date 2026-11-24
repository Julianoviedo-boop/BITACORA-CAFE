// Estados vacío y de error, iguales en todas las pantallas.

import { CircleAlert, Leaf, type LucideIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { MENSAJES_ERROR, type Motivo } from "@/lib/api";
import { cn } from "@/lib/utils";

export function Vacio({
  titulo,
  children,
  icono: Icono = Leaf,
  className,
}: {
  titulo: string;
  children?: React.ReactNode;
  icono?: LucideIcon;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center rounded-xl border border-dashed bg-card/60 px-6 py-10 text-center",
        className,
      )}
    >
      <span className="flex size-12 items-center justify-center rounded-full bg-verde-suave text-primary">
        <Icono aria-hidden className="size-6" />
      </span>
      <p className="mt-4 font-serif text-lg font-semibold">{titulo}</p>
      {children && <p className="mt-1 max-w-md text-sm text-muted-foreground">{children}</p>}
    </div>
  );
}

export function ErrorCarga({ motivo, className }: { motivo: Motivo; className?: string }) {
  return (
    <Alert variant="destructive" className={cn("bg-card", className)}>
      <CircleAlert aria-hidden />
      <AlertTitle>No se pudo cargar</AlertTitle>
      <AlertDescription>{MENSAJES_ERROR[motivo]}</AlertDescription>
    </Alert>
  );
}
