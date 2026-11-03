"use client";

import { useEffect, useState } from "react";
import { CheckIcon, SunIcon } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItemIndicator,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  THEME_EVENT,
  THEME_OPTIONS,
  applyTheme,
  publishTheme,
  readThemePreference,
  type ThemePreference,
} from "@/lib/theme";
import { cn } from "@/lib/utils";

function isThemePreference(value: string): value is ThemePreference {
  return value === "light" || value === "dark" || value === "system";
}

export function ThemeToggle({ className }: { className?: string }) {
  const [preference, setPreference] = useState<ThemePreference>("system");

  useEffect(() => {
    const sync = () => {
      const next = readThemePreference();
      setPreference(next);
      applyTheme(next);
    };
    sync();
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onMedia = () => {
      if (readThemePreference() === "system") applyTheme("system");
    };
    window.addEventListener(THEME_EVENT, sync);
    window.addEventListener("storage", sync);
    media.addEventListener("change", onMedia);
    return () => {
      window.removeEventListener(THEME_EVENT, sync);
      window.removeEventListener("storage", sync);
      media.removeEventListener("change", onMedia);
    };
  }, []);

  const current = THEME_OPTIONS.find((option) => option.id === preference)?.label ?? "跟随系统";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="外观"
          title={`外观：${current}`}
          className={cn(
            "inline-flex size-8 items-center justify-center rounded-md text-muted-foreground outline-none",
            "hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
            className,
          )}
        >
          <SunIcon className="size-4" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent aria-label="外观">
        <DropdownMenuRadioGroup
          value={preference}
          onValueChange={(value) => {
            if (!isThemePreference(value)) return;
            setPreference(value);
            publishTheme(value);
          }}
        >
          {THEME_OPTIONS.map((option) => (
            <DropdownMenuRadioItem key={option.id} value={option.id}>
              {option.label}
              <DropdownMenuItemIndicator>
                <CheckIcon />
              </DropdownMenuItemIndicator>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
