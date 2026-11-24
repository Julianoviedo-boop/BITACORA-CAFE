// Clave de acceso al panel (HTTP Basic). Ver docs/decisiones.md, D-020.
//
// El panel muestra datos de los caficultores: no puede quedar abierto en internet.
// Usuario y clave salen de PANEL_USUARIO y PANEL_CLAVE. En producción, si faltan,
// el panel no se muestra; en desarrollo se deja pasar para trabajar sin clave.

import { NextResponse, type NextRequest } from "next/server";

function iguales(a: string, b: string): boolean {
  // Comparación en tiempo constante (no revela cuántas letras acertó).
  let diferencia = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    diferencia |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diferencia === 0;
}

function credenciales(request: NextRequest): [string, string] | null {
  const auth = request.headers.get("authorization") ?? "";
  if (!auth.startsWith("Basic ")) return null;
  try {
    const texto = new TextDecoder().decode(
      Uint8Array.from(atob(auth.slice(6)), (c) => c.charCodeAt(0)),
    );
    const i = texto.indexOf(":");
    return i < 0 ? null : [texto.slice(0, i), texto.slice(i + 1)];
  } catch {
    return null;
  }
}

export function proxy(request: NextRequest) {
  const usuario = process.env.PANEL_USUARIO ?? "";
  const clave = process.env.PANEL_CLAVE ?? "";

  if (!usuario || !clave) {
    if (process.env.NODE_ENV !== "production") return NextResponse.next();
    return new NextResponse("El panel no tiene clave configurada. Avísele a quien administra el sistema.", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }

  const recibidas = credenciales(request);
  if (recibidas && iguales(recibidas[0], usuario) && iguales(recibidas[1], clave)) {
    return NextResponse.next();
  }
  return new NextResponse("Necesita usuario y clave para entrar al panel.", {
    status: 401,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "WWW-Authenticate": 'Basic realm="Bitacora de Cafe", charset="UTF-8"',
    },
  });
}

export const config = {
  // Todo menos los archivos estáticos que arma Next.js y la página pública del
  // lote (/l/<token>), que abre un comprador sin clave (D-022).
  matcher: ["/((?!_next/static|_next/image|favicon.ico|l/).*)"],
};
