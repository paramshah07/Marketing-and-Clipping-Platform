import * as React from "react"
import { cn } from "cn"
import { Switch as SwitchPrimitive } from "radix-ui"

function Switch({
  className,
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "peer group/switch relative inline-flex h-4 w-7 shrink-0 items-center rounded-full p-0.5 outline-none focus-visible:ring-2 focus-visible:ring-accent data-checked:bg-fg data-unchecked:bg-line-strong data-disabled:cursor-not-allowed data-disabled:opacity-50",
        className
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className="pointer-events-none block size-3 rounded-full transition-transform data-checked:translate-x-3 data-checked:bg-bg data-unchecked:bg-muted"
      />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
