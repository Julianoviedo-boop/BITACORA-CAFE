import { Skeleton } from "@/components/ui/skeleton";

export default function CargandoAlertas() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-8 sm:px-6" aria-busy="true" aria-label="Cargando las alertas">
      <Skeleton className="h-9 w-56" />
      <Skeleton className="mt-2 h-5 w-full max-w-lg" />
      <div className="mt-6 space-y-3">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="flex gap-3 rounded-xl border bg-card p-4">
            <Skeleton className="size-9 shrink-0 rounded-full" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-5 w-full" />
              <Skeleton className="h-4 w-48" />
            </div>
          </div>
        ))}
      </div>
    </main>
  );
}
