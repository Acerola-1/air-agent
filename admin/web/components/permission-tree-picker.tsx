"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { PERMISSION_BUTTON, type PermissionNode } from "@/lib/types";
import { cn } from "@/lib/utils";

function collectCodes(node: PermissionNode): string[] {
  return [node.code, ...node.children.flatMap(collectCodes)];
}

type NodeProps = {
  node: PermissionNode;
  depth: number;
  selected: Set<string>;
  disabled: boolean;
  onToggle: (codes: string[], checked: boolean) => void;
};

function TreeItem({ node, depth, selected, disabled, onToggle }: NodeProps) {
  // 默认全部展开：配置角色权限时，按钮级权限才是重点，
  // 折叠起来会让"已选 N 项"与眼前看到的勾选数对不上。
  const [expanded, setExpanded] = useState(true);
  const hasChildren = node.children.length > 0;
  const isButton = node.type === PERMISSION_BUTTON;

  return (
    <div>
      <div
        className="hover:bg-accent/40 flex items-center gap-1.5 rounded-sm py-0.5 pr-2"
        style={{ paddingLeft: `${depth * 16 + 4}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-muted-foreground hover:text-foreground p-0.5"
            title={expanded ? "收起" : "展开"}
          >
            {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          </button>
        ) : (
          <span className="w-[18px]" />
        )}

        <Checkbox
          disabled={disabled}
          checked={selected.has(node.code)}
          onChange={(e) => onToggle(collectCodes(node), e.target.checked)}
        />

        <span className={cn("text-sm", !isButton && "font-medium")}>{node.name}</span>
        <span className="text-muted-foreground font-mono text-[11px]">{node.code}</span>
      </div>

      {expanded &&
        node.children.map((child) => (
          <TreeItem
            key={child.id}
            node={child}
            depth={depth + 1}
            selected={selected}
            disabled={disabled}
            onToggle={onToggle}
          />
        ))}
    </div>
  );
}

type Props = {
  nodes: PermissionNode[];
  value: string[];
  onChange: (codes: string[]) => void;
  disabled?: boolean;
};

/** 权限树勾选。勾父级会连带勾选全部子级，取消同理——后台里这比逐个点省事。 */
export function PermissionTreePicker({ nodes, value, onChange, disabled = false }: Props) {
  const selected = new Set(value);

  function toggle(codes: string[], checked: boolean) {
    const next = new Set(selected);
    for (const code of codes) {
      if (checked) next.add(code);
      else next.delete(code);
    }
    onChange([...next]);
  }

  function selectAll(checked: boolean) {
    onChange(checked ? nodes.flatMap(collectCodes) : []);
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Button type="button" variant="outline" size="xs" disabled={disabled} onClick={() => selectAll(true)}>
          全选
        </Button>
        <Button type="button" variant="outline" size="xs" disabled={disabled} onClick={() => selectAll(false)}>
          全不选
        </Button>
        <span className="text-muted-foreground ml-auto text-xs">已选 {value.length} 项</span>
      </div>

      <div className="max-h-80 overflow-y-auto rounded-md border p-1">
        {nodes.map((node) => (
          <TreeItem
            key={node.id}
            node={node}
            depth={0}
            selected={selected}
            disabled={disabled}
            onToggle={toggle}
          />
        ))}
      </div>
    </div>
  );
}
