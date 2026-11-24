"use client";

// Lo que se ve si una pantalla falla mientras se arma (error.tsx de cada ruta).

import { CircleAlert, RotateCw } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export function ErrorPantalla({ reset }: { reset: () => void }) {
  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <Alert variant="destructive" className="bg-card">
        <CircleAlert aria-hidden />
        <AlertTitle>Algo salió mal</AlertTitle>
        <AlertDescription>
          <p>No pudimos mostrar esta página. Puede ser un problema de conexión.</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={reset}>
            <RotateCw aria-hidden />
            Intentar de nuevo
          </Button>
        </AlertDescription>
      </Alert>
    </main>
  );
}
