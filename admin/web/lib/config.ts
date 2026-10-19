/** 后端部署配置（同源 `/api/config`，运行时读取，避免构建期写死地址）。 */
export type BackendConfig = {
  identityBackend: "local" | "remote";
  iamAdminUrl: string | null;
};

let cached: Promise<BackendConfig> | null = null;

export function getBackendConfig(): Promise<BackendConfig> {
  if (!cached) {
    cached = fetch("/api/config", { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) throw new Error(String(res.status));
        return (await res.json()) as BackendConfig;
      })
      .catch(() => ({ identityBackend: "local" as const, iamAdminUrl: null }));
  }
  return cached;
}
