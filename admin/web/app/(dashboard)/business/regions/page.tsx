"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronRight, MapPin } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { regionsApi } from "@/lib/api";
import type { Region } from "@/lib/types";

const LEVEL_LABEL: Record<number, string> = { 1: "省", 2: "市", 3: "区县" };

export default function RegionsPage() {
  const [stack, setStack] = useState<Region[]>([]);
  const [items, setItems] = useState<Region[]>([]);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<Region[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const current = stack[stack.length - 1];

  const loadChildren = useCallback(async (parentCode?: string) => {
    setLoading(true);
    setError(null);
    try {
      setItems(await regionsApi.children(parentCode));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载行政区划失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadChildren(current?.code);
  }, [current?.code, loadChildren]);

  useEffect(() => {
    const keyword = search.trim();
    if (!keyword) {
      setResults(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      void regionsApi
        .search(keyword)
        .then((rows) => {
          if (!cancelled) setResults(rows);
        })
        .catch(() => {
          if (!cancelled) setResults([]);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [search]);

  /** 搜索结果的层级是跳着的，用它重建面包屑需要逐级取父节点。 */
  async function jumpTo(region: Region) {
    const chain: Region[] = [region];
    let parentCode = region.parent_code;
    while (parentCode) {
      const parent = await fetch(`/api/regions/${parentCode}`, { credentials: "include" }).then(
        (res) => (res.ok ? (res.json() as Promise<Region>) : null)
      );
      if (!parent) break;
      chain.unshift(parent);
      parentCode = parent.parent_code;
    }
    // 末级是当前节点本身，父链只用于面包屑导航
    setStack(chain.slice(0, -1));
    setItems(await regionsApi.children(chain[chain.length - 2]?.code));
    setSearch("");
    setResults(null);
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">行政区划</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          全国省 / 市 / 区县三级，共 3347 条。数据由 `scripts/import_regions.py` 从开源数据集导入。
        </p>
      </div>

      <div className="relative max-w-md">
        <Input
          placeholder="搜索区划名称，例如：东莞市 / 西湖区"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {error && <p className="text-destructive text-sm">{error}</p>}

      {results !== null ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">搜索结果（{results.length} 条）</CardTitle>
            <CardDescription>点击可跳转到该区划所在层级</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {results.length === 0 && (
              <p className="text-muted-foreground text-sm">没有匹配的区划</p>
            )}
            {results.map((region) => (
              <Button
                key={region.code}
                variant="outline"
                size="sm"
                onClick={() => void jumpTo(region)}
              >
                <MapPin className="size-3.5" />
                {region.name}
                <span className="text-muted-foreground ml-1 text-xs">
                  {LEVEL_LABEL[region.level]} {region.code}
                </span>
              </Button>
            ))}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader className="gap-3">
            <div className="flex flex-wrap items-center gap-1 text-sm">
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground"
                onClick={() => setStack([])}
              >
                全国
              </button>
              {stack.map((node, index) => (
                <span key={node.code} className="flex items-center gap-1">
                  <ChevronRight className="text-muted-foreground size-3.5" />
                  <button
                    type="button"
                    className={
                      index === stack.length - 1
                        ? "font-medium"
                        : "text-muted-foreground hover:text-foreground"
                    }
                    onClick={() => setStack((prev) => prev.slice(0, index + 1))}
                  >
                    {node.name}
                  </button>
                </span>
              ))}
            </div>
            <CardDescription>
              {loading
                ? "加载中…"
                : current
                  ? `${current.name} 下辖 ${items.length} 个节点`
                  : `全国共 ${items.length} 个省级行政区`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {items.map((region) => (
                <Button
                  key={region.code}
                  variant="outline"
                  size="sm"
                  disabled={!region.has_children}
                  title={region.has_children ? "点击下钻" : "没有下级区划"}
                  onClick={() => setStack((prev) => [...prev, region])}
                >
                  {region.name}
                  <span className="text-muted-foreground ml-1 text-xs">{region.code}</span>
                </Button>
              ))}
              {!loading && items.length === 0 && (
                <p className="text-muted-foreground text-sm">该节点没有下级区划</p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">数据说明</CardTitle>
        </CardHeader>
        <CardContent className="text-muted-foreground space-y-2 text-sm">
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">省 31</Badge>
            <Badge variant="secondary">市 342</Badge>
            <Badge variant="secondary">区县 2974</Badge>
          </div>
          <p>
            东莞、中山、儋州、嘉峪关四个「直筒子市」在行政上不设区县，直接管辖乡镇，
            因此它们到市级即为最深层级（乡镇级节点已按「最小只到区县」的要求排除）。
          </p>
          <p>港澳台数据源结构与大陆不一致，暂未导入。</p>
        </CardContent>
      </Card>
    </div>
  );
}
