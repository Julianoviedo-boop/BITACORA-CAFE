import { Skeleton } from "@/components/ui/skeleton";

export default function CargandoLotePublico() {
  return (
    <div className="min-h-screen" aria-busy="true" aria-label="Cargando la historia del lote">
      <div className="bg-primary">
        <div className="mx-auto max-w-3xl space-y-3 px-4 pt-8 pb-8 sm:px-6">
          <Skeleton className="h-4 w-48 bg-primary-foreground/20" />
          <Skeleton className="h-10 w-64 max-w-full bg-primary-foreground/20" />
          <Skeleton className="h-4 w-24 bg-primary-foreground/20" />
          <Skeleton className="h-14 w-72 max-w-full bg-primary-foreground/20" />
        </div>
      </div>
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-8 sm:px-6">
        <Skeleton className="h-16 w-full rounded-xl" />
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="flex gap-4">
            <Skeleton className="size-10 shrink-0 rounded-full" />
            <Skeleton className="h-24 flex-1 rounded-xl" />
          </div>
        ))}
      </div>
    </div>
  );
}
