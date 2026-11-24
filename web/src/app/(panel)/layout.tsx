import { Navegacion } from "@/components/bitacora/navegacion";

export default function PanelLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <Navegacion />
      {children}
    </>
  );
}
