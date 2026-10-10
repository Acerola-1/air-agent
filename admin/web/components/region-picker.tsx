"use client";

import { useCallback, useEffect, useState } from "react";
import { MapPin, Plus, Search, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { regionsApi } from "@/lib/api";
import type { Region } from "@/lib/types";
import { cn } from "@/lib/utils";

export const MAX_FOLLOWED_REGIONS = 5;

const LEVEL_LABEL: Record<number, string> = { 1: "省", 2: "市", 3: "区县" };

/** 批量解析区划编码 → 名称（结果缓存，最多 5 个编码，逐条取详情足够）。 */
function useRegionNames(codes: string[]) {
  const [names, setNames] = useState<Record<string, string>>({});

  useEffect(() => {
    const missing = codes.filter((code) => !(code in names));
    if (!missing.length) return;
    let cancelled = false;

    void (async () => {
      const resolved: Record<string, string> = {};
      await Promise.all(
        missing.map(async (code) => {
          try {
            const res = await fetch(`/api/regions/${code}`, { credentials: "include" });
            if (res.ok) {
              const region = (await res.json()) as Region;
              resolved[code] = region.name;
            }
          } catch {
            // 解析失败就显示编码本身，不影响主流程
          }
        })
      );
      if (!cancelled && Object.keys(resolved).length) {
        setNames((prev) => ({ ...prev, ...resolved }));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [codes, names]);

  return names;
}

type Props = {
  value: string[];
  onChange: (codes: string[]) => void;
  disabled?: boolean;
};

/** 关注城市选择器：省 → 市 → 区县 级联，最小可选到区县；也可直接搜索。 */
export function RegionPicker({ value, onChange, disabled }: Props) {
  const names = useRegionNames(value);

  const [open, setOpen] = useState(false);
  const [provinces, setProvinces] = useState<Region[]>([]);
  const [cities, setCities] = useState<Region[]>([]);
  const [districts, setDistricts] = useState<Region[]>([]);
  const [selected, setSelected] = useState<{ province: string; city: string; district: string }>(
    { province: "", city: "", district: "" }
  );
  const [keyword, setKeyword] = useState("");
  const [results, setResults] = useState<Region[]>([]);

  const loadProvinces = useCallback(async () => {
    setProvinces(await regionsApi.children());
  }, []);

  useEffect(() => {
    if (open && !provinces.length) void loadProvinces();
  }, [open, provinces.length, loadProvinces]);

  useEffect(() => {
    if (!selected.province) {
      setCities([]);
      return;
    }
    void regionsApi.children(selected.province).then(setCities);
  }, [selected.province]);

  useEffect(() => {
    if (!selected.city) {
      setDistricts([]);
      return;
    }
    void regionsApi.children(selected.city).then(setDistricts);
  }, [selected.city]);

  useEffect(() => {
    if (!keyword.trim()) {
      setResults([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      void regionsApi
        .search(keyword.trim())
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
  }, [keyword]);

  const atLimit = value.length >= MAX_FOLLOWED_REGIONS;

  function add(code: string) {
    if (!code || value.includes(code) || atLimit) return;
    onChange([...value, code]);
  }

  function remove(code: string) {
    onChange(value.filter((item) => item !== code));
  }

  /** 添加当前级联里选中的最深层节点。 */
  function addFromCascade() {
    add(selected.district || selected.city || selected.province);
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {value.length === 0 && (
          <span className="text-muted-foreground text-sm">尚未选择关注城市</span>
        )}
        {value.map((code) => (
          <Badge key={code} variant="secondary" className="gap-1 py-1 pr-1 pl-2 text-xs">
            <MapPin className="size-3" />
            {names[code] ?? code}
            {!disabled && (
              <button
                type="button"
                onClick={() => remove(code)}
                className="hover:bg-muted-foreground/20 ml-0.5 rounded-sm p-0.5"
                title="移除"
              >
                <X className="size-3" />
              </button>
            )}
          </Badge>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || atLimit}
          onClick={() => setOpen(true)}
        >
          <Plus className="size-4" />
          添加城市
        </Button>
        <span className={cn("text-xs", atLimit ? "text-destructive" : "text-muted-foreground")}>
          {value.length} / {MAX_FOLLOWED_REGIONS}
        </span>
      </div>

      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) {
            setKeyword("");
            setResults([]);
          }
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>选择关注城市</DialogTitle>
            <DialogDescription>
              按 省 → 市 → 区县 逐级选择（最小到区县），也可以直接搜索名称。最多{" "}
              {MAX_FOLLOWED_REGIONS} 个。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-1.5">
              <div className="text-muted-foreground flex items-center gap-1.5 text-xs">
                <Search className="size-3" />
                直接搜索
              </div>
              <Input
                placeholder="输入城市或区县名称，例如：杭州 / 西湖区"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
              />
              {results.length > 0 && (
                <div className="max-h-52 overflow-y-auto rounded-md border">
                  {results.map((region) => (
                    <button
                      key={region.code}
                      type="button"
                      disabled={value.includes(region.code) || atLimit}
                      onClick={() => add(region.code)}
                      className="hover:bg-accent flex w-full items-center justify-between px-3 py-1.5 text-left text-sm disabled:opacity-40"
                    >
                      <span>{region.name}</span>
                      <span className="text-muted-foreground text-xs">
                        {LEVEL_LABEL[region.level]} · {region.code}
                        {value.includes(region.code) ? " · 已选" : ""}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-2">
              <div className="text-muted-foreground text-xs">逐级选择</div>
              <div className="grid grid-cols-3 gap-2">
                <Select
                  value={selected.province}
                  onChange={(e) =>
                    setSelected({ province: e.target.value, city: "", district: "" })
                  }
                >
                  <option value="">选择省份</option>
                  {provinces.map((region) => (
                    <option key={region.code} value={region.code}>
                      {region.name}
                    </option>
                  ))}
                </Select>

                <Select
                  value={selected.city}
                  disabled={!selected.province || !cities.length}
                  onChange={(e) => setSelected((prev) => ({ ...prev, city: e.target.value, district: "" }))}
                >
                  <option value="">
                    {selected.province && !cities.length ? "无下辖市" : "选择城市"}
                  </option>
                  {cities.map((region) => (
                    <option key={region.code} value={region.code}>
                      {region.name}
                    </option>
                  ))}
                </Select>

                <Select
                  value={selected.district}
                  disabled={!selected.city || !districts.length}
                  onChange={(e) => setSelected((prev) => ({ ...prev, district: e.target.value }))}
                >
                  <option value="">
                    {selected.city && !districts.length ? "无下辖区县" : "选择区县"}
                  </option>
                  {districts.map((region) => (
                    <option key={region.code} value={region.code}>
                      {region.name}
                    </option>
                  ))}
                </Select>
              </div>

              <p className="text-muted-foreground text-xs">
                提示：不选到区县也可以，会添加当前选中的最深层级（省或市）。
              </p>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              关闭
            </Button>
            <Button
              onClick={addFromCascade}
              disabled={!selected.province || atLimit}
            >
              添加
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
