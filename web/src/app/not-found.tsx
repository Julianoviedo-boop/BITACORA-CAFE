import { SearchX } from "lucide-react";
import Link from "next/link";

import { Vacio } from "@/components/bitacora/estados";
import { Button } from "@/components/ui/button";

export default function NoEncontrado() {
  return (
    <main className="mx-auto max-w-xl px-4 py-16">
      <Vacio titulo="No encontramos lo que busca" icono={SearchX}>
        Puede que el enlace esté mal escrito o que el lote ya no exista.
      </Vacio>
      <div className="mt-6 text-center">
        <Button asChild variant="outline">
          <Link href="/lotes">Ver todos los lotes</Link>
        </Button>
      </div>
    </main>
  );
}
