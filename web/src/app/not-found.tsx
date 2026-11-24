import Link from "next/link";

export default function NoEncontrado() {
  return (
    <main className="mx-auto max-w-3xl p-4 sm:p-8">
      <h1 className="text-2xl font-bold">No encontramos lo que busca</h1>
      <p className="mt-2 text-stone-600">Puede que el enlace esté mal escrito o que el lote ya no exista.</p>
      <Link href="/lotes" className="mt-4 inline-block text-amber-800 hover:underline">
        Ver todos los lotes
      </Link>
    </main>
  );
}
