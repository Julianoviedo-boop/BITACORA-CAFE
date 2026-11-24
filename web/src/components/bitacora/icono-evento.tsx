// Ícono de cada tipo de evento. Las claves son las de config/eventos.yaml; un tipo
// nuevo que no esté aquí sale con el ícono genérico, sin romper nada.

import {
  Bug,
  Cherry,
  CircleDot,
  Droplet,
  HandCoins,
  RefreshCw,
  Scissors,
  SprayCan,
  Sprout,
  Sun,
  Thermometer,
  Waves,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

const ICONOS: Record<string, LucideIcon> = {
  abonada: Sprout,
  fumigacion: SprayCan,
  desyerba: Scissors,
  recoleccion: Cherry,
  beneficio: Waves,
  secado: Sun,
  venta: HandCoins,
  monitoreo_sanitario: Bug,
  volteo: RefreshCw,
  medicion_fermentacion: Thermometer,
  medicion_humedad: Droplet,
};

export function IconoEvento({ tipo, className }: { tipo: string; className?: string }) {
  const Icono = ICONOS[tipo] ?? CircleDot;
  return <Icono aria-hidden className={cn("size-4", className)} />;
}
