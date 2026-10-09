import { redirect } from "next/navigation";

import { PrivacyScreen } from "@/components/settings/privacy-screen";
import { Unauthenticated, apiGet } from "@/lib/api-server";
import type { Me } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function PrivacyPage() {
  let me: Me;
  try {
    me = await apiGet<Me>("/auth/me");
  } catch (err) {
    if (err instanceof Unauthenticated) redirect("/login");
    throw err;
  }

  // O e-mail vai como prop porque a confirmação de encerramento compara
  // com ele no cliente (para desabilitar o botão) e no servidor (que é
  // quem valida de verdade).
  return <PrivacyScreen email={me.email} timezone={me.timezone} />;
}
