import Link from "next/link";

export const dynamic = "force-dynamic";

type Salud = { status: string; checks: Record<string, string> };

async function consultarSalud(): Promise<Salud | null> {
  // Dentro de Docker el panel habla con la API por la red interna.
  const base = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
  try {
    const resp = await fetch(`${base}/health`, { cache: "no-store" });
    return (await resp.json()) as Salud;
  } catch {
    return null;
  }
}

export default async function Inicio() {
  const salud = await consultarSalud();
  const todoBien = salud?.status === "ok";

  return (
    <main className="mx-auto max-w-3xl p-4 sm:p-8">
      <h1 className="text-3xl font-bold">Bitácora de Café</h1>
      <p className="mt-2 text-stone-600">Aquí va a ver la historia de cada lote.</p>
      <Link
        href="/lotes"
        className="mt-6 inline-block rounded-lg bg-amber-800 px-4 py-2 font-medium text-white hover:bg-amber-900"
      >
        Ver los lotes
      </Link>

      <section className="mt-8 rounded-lg border border-stone-200 bg-white p-4">
        <h2 className="font-semibold">Estado del sistema</h2>
        {salud === null ? (
          <p className="mt-2 text-red-700">No pudimos conectarnos con el servidor.</p>
        ) : (
          <>
            <p className={`mt-2 ${todoBien ? "text-green-700" : "text-red-700"}`}>
              {todoBien ? "Todo está funcionando." : "Hay un problema en el sistema."}
            </p>
            <ul className="mt-2 text-sm text-stone-600">
              <li>Base de datos: {salud.checks.db === "ok" ? "bien" : "con fallas"}</li>
              <li>Cola de tareas: {salud.checks.redis === "ok" ? "bien" : "con fallas"}</li>
            </ul>
          </>
        )}
      </section>
    </main>
  );
}
