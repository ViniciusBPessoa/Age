import { redirect } from "next/navigation";

import { CalendarScreen } from "@/components/calendar-screen";
import { Unauthenticated, apiGet } from "@/lib/api-server";
import type { Me } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function CalendarPage() {
  let me: Me;
  try {
    me = await apiGet<Me>("/auth/me");
  } catch (err) {
    if (err instanceof Unauthenticated) redirect("/login");
    throw err;
  }

  // A busca de eventos fica no cliente, não aqui: a janela depende da
  // semana que a pessoa está olhando, e navegar entre semanas não deve
  // custar um round-trip de página inteira.
  return <CalendarScreen timezone={me.timezone} />;
}
