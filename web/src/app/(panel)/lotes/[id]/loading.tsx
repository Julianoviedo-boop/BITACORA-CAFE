import { Skeleton } from "@/components/ui/skeleton";

export default function CargandoLote() {
  return (
    <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8" aria-busy="true" aria-label="Cargando el lote">
      <Skeleton className="h-4 w-36" />
      <Skeleton className="mt-6 h-4 w-20" />
      <Skeleton className="mt-2 h-10 w-64 max-w-full" />
      <Skeleton className="mt-3 h-5 w-80 max-w-full" />
      <div className="mt-3 flex gap-2">
        <Skeleton className="h-5 w-28 rounded-full" />
        <Skeleton className="h-5 w-36 rounded-full" />
      </div>
      <Skeleton className="mt-10 h-6 w-36" />
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Skeleton className="h-60 rounded-xl" />
        <Skeleton className="hidden h-60 rounded-xl md:block" />
      </div>
      <Skeleton className="mt-10 h-6 w-44" />
      <div className="mt-4 space-y-6">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="flex gap-4">
            <Skeleton className="size-10 shrink-0 rounded-full" />
            <Skeleton className="h-28 flex-1 rounded-xl" />
          </div>
        ))}
      </div>
    </main>
  );
}
