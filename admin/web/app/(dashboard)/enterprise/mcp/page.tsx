"use client";

/** 企业 MCP 授权状态：脱敏只读。未核验协议/未配置时明确显示"企业 MCP 已禁用"。 */

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, bindingsApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { McpBinding } from "@/lib/types";

export default function EnterpriseMcpPage() {
  const { ctx, can } = useSession();
  const tenantId = ctx?.current_tenant?.id;
  const [items, setItems] = useState<McpBinding[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await bindingsApi.list());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载授权状态失败");
    }
  }, []);

  useEffect(() => {
    if (tenantId && can("mcp:binding:read")) void load();
  }, [tenantId, can, load]);

  if (!tenantId) return <p className="text-muted-foreground text-sm">尚未选择企业。</p>;
  if (!can("mcp:binding:read")) return <p className="text-muted-foreground text-sm">你没有 MCP 授权查看权限。</p>;

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">MCP 授权状态 · {ctx?.current_tenant?.name}</h1>
      {error && <p className="text-destructive text-sm">{error}</p>}
      <p className="text-muted-foreground text-sm">
        凭据与外部主体由平台配置；此处仅展示脱敏状态。协议未核验或绑定缺失时，本企业的 MCP 业务调用保持禁用（不会回退匿名身份）。
      </p>
      <Card>
        <CardContent className="p-0">
          {items.length === 0 ? (
            <div className="text-muted-foreground p-6 text-sm">尚未配置授权绑定 —— 企业 MCP 已禁用。</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>服务</TableHead>
                  <TableHead>外部主体</TableHead>
                  <TableHead>协议</TableHead>
                  <TableHead>修订</TableHead>
                  <TableHead>凭据</TableHead>
                  <TableHead>状态</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((b) => (
                  <TableRow key={b.id}>
                    <TableCell className="font-mono text-xs">{b.server_key}</TableCell>
                    <TableCell>{b.external_subject}{b.membership_id ? "（成员覆盖）" : ""}</TableCell>
                    <TableCell>{b.protocol_version === "unverified" ? <Badge variant="destructive">未核验</Badge> : b.protocol_version}</TableCell>
                    <TableCell className="tabular-nums">{b.auth_revision}</TableCell>
                    <TableCell>{b.credential_configured ? "已配置" : "缺失"}</TableCell>
                    <TableCell>{b.status === "active" ? "启用" : "停用"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
