import { Skeleton } from "@/components/ui/skeleton";

export default function CargandoLotes() {
  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6" aria-busy="true" aria-label="Cargando los lotes">
      <Skeleton className="h-9 w-32" />
      <Skeleton className="mt-2 h-5 w-72 max-w-full" />
      <ul className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => (
          <li key={i} className="rounded-xl border bg-card p-4">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="mt-2 h-6 w-40" />
            <Skeleton className="mt-2 h-4 w-32" />
            <Skeleton className="mt-4 h-5 w-28 rounded-full" />
            <Skeleton className="mt-6 h-10 w-full" />
          </li>
        ))}
      </ul>
    </main>
  );
}
