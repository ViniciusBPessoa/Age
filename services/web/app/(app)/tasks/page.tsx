import { redirect } from "next/navigation";

import { TasksScreen } from "@/components/tasks/tasks-screen";
import { Unauthenticated, apiGet } from "@/lib/api-server";
import type { Me } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function TasksPage() {
  let me: Me;
  try {
    me = await apiGet<Me>("/auth/me");
  } catch (err) {
    if (err instanceof Unauthenticated) redirect("/login");
    throw err;
  }

  return <TasksScreen timezone={me.timezone} />;
}
