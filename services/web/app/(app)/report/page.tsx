import { redirect } from "next/navigation";

import { ReportScreen } from "@/components/report-screen";
import { Unauthenticated, apiGet } from "@/lib/api-server";
import type { Me } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ReportPage() {
  let me: Me;
  try {
    me = await apiGet<Me>("/auth/me");
  } catch (err) {
    if (err instanceof Unauthenticated) redirect("/login");
    throw err;
  }

  return <ReportScreen timezone={me.timezone} />;
}
