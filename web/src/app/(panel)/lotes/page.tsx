import Link from "next/link";

import { listarLotes, MENSAJES_ERROR, type Lote } from "@/lib/api";

export const dynamic = "force-dynamic";

function TarjetaLote({ lote }: { lote: Lote }) {
  const detalles = [lote.finca && `Finca ${lote.finca}`, lote.variedad, lote.proceso].filter(Boolean);
  return (
    <li>
      <Link
        href={`/lotes/${lote.id}`}
        className="block rounded-lg border border-stone-200 bg-white p-4 hover:border-amber-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-700"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-lg font-semibold">
            {lote.nombre} <span className="font-normal text-stone-500">· {lote.codigo}</span>
          </h2>
          {!lote.activo && (
            <span className="rounded bg-stone-200 px-2 py-0.5 text-xs text-stone-700">Inactivo</span>
          )}
        </div>
        {detalles.length > 0 && <p className="mt-1 text-sm text-stone-600">{detalles.join(" · ")}</p>}
        <p className="mt-2 text-sm text-stone-700">
          {lote.eventos === 0
            ? "Todavía no tiene actividades registradas."
            : `${lote.eventos} ${lote.eventos === 1 ? "actividad" : "actividades"} · la última fue el ${lote.ultimo_evento_texto}`}
        </p>
      </Link>
    </li>
  );
}

export default async function Lotes() {
  const resultado = await listarLotes();

  return (
    <main className="mx-auto max-w-3xl p-4 sm:p-8">
      <h1 className="text-3xl font-bold">Lotes</h1>
      <p className="mt-2 text-stone-600">Toque un lote para ver todo lo que se ha hecho en él.</p>

      {!resultado.ok ? (
        <p className="mt-8 rounded-lg border border-red-200 bg-red-50 p-4 text-red-800">
          {MENSAJES_ERROR[resultado.motivo]}
        </p>
      ) : resultado.datos.length === 0 ? (
        <p className="mt-8 rounded-lg border border-stone-200 bg-white p-4 text-stone-700">
          Todavía no hay lotes. Se crean solos cuando un caficultor nombra un lote nuevo por
          WhatsApp y confirma que lo quiere crear.
        </p>
      ) : (
        <ul className="mt-6 space-y-3">
          {resultado.datos.map((lote) => (
            <TarjetaLote key={lote.id} lote={lote} />
          ))}
        </ul>
      )}
    </main>
  );
}
