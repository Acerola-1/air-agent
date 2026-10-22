import { redirect } from "next/navigation";

/** 原“概览”只展示已迁移到 IAM 的入口和无效卡片；现在直接进入运营总览。 */
export default function Page() {
  redirect("/operations");
}
