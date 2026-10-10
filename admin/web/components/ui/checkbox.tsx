import * as React from "react"

import { cn } from "@/lib/utils"

/** 用原生 checkbox 而非 radix：少一个组件依赖，且表单语义更直接。 */
function Checkbox({ className, type = "checkbox", ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="checkbox"
      className={cn(
        "border-input accent-primary focus-visible:ring-ring/50 size-4 shrink-0 cursor-pointer rounded-[4px] border outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
}

export { Checkbox }
