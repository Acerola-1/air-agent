"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { permissionsApi } from "@/lib/api";
import { PERMISSION_BUTTON, PERMISSION_DIRECTORY, PERMISSION_MENU, type PermissionNode } from "@/lib/types";
import { cn } from "@/lib/utils";

const TYPE_LABEL: Record<number, string> = {
  [PERMISSION_DIRECTORY]: "目录",
  [PERMISSION_MENU]: "菜单",
  [PERMISSION_BUTTON]: "按钮",
};

function typeVariant(type: number) {
  if (type === PERMISSION_DIRECTORY) return "default" as const;
  if (type === PERMISSION_MENU) return "secondary" as const;
  return "muted" as const;
}

function TreeNode({ node, depth }: { node: PermissionNode; depth: number }) {
  const [expanded, setExpanded] = useState(depth < 1);

  return (
    <div>
      <div
        className="hover:bg-accent/40 flex items-center gap-2 rounded-sm py-1 pr-2"
        style={{ paddingLeft: `${depth * 18 + 4}px` }}
      >
        {node.children.length ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-muted-foreground hover:text-foreground p-0.5"
          >
            {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          </button>
        ) : (
          <span className="w-[18px]" />
        )}
        <Badge variant={typeVariant(node.type)} className="shrink-0">
          {TYPE_LABEL[node.type]}
        </Badge>
        <span className={cn("text-sm", node.type !== PERMISSION_BUTTON && "font-medium")}>
          {node.name}
        </span>
        <span className="text-muted-foreground font-mono text-[11px]">{node.code}</span>
        {node.path && (
          <span className="text-muted-foreground font-mono text-[11px]">{node.path}</span>
        )}
      </div>
      {expanded &&
        node.children.map((child) => (
          <TreeNode key={child.id} node={child} depth={depth + 1} />
        ))}
    </div>
  );
}

export default function PermissionsPage() {
  const [tree, setTree] = useState<PermissionNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setTree(await permissionsApi.tree());
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载权限失败");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const count = (nodes: PermissionNode[]): number =>
    nodes.reduce((sum, node) => sum + 1 + count(node.children), 0);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">权限管理</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          权限由代码定义、通过种子脚本写入，界面只读。共 {count(tree)} 项。
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">权限树</CardTitle>
          <CardDescription>
            目录用于分组，菜单对应左侧导航，按钮对应页面内的操作。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading && <p className="text-muted-foreground text-sm">加载中…</p>}
          {error && <p className="text-destructive text-sm">{error}</p>}
          {!loading && !error && (
            <div className="rounded-md border p-1">
              {tree.map((node) => (
                <TreeNode key={node.id} node={node} depth={0} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
