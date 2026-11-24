"use client";

import { ErrorPantalla } from "@/components/bitacora/error-pantalla";

export default function ErrorAlertas({ reset }: { error: Error; reset: () => void }) {
  return <ErrorPantalla reset={reset} />;
}
