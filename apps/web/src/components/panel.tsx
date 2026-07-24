import type { ReactNode } from "react"

type PanelProps = {
  readonly children: ReactNode
  readonly className?: string
  readonly labelledBy?: string
  readonly variant?: "base" | "raised" | "critical"
}

export function Panel({ children, className = "", labelledBy, variant = "base" }: PanelProps) {
  return (
    <section aria-labelledby={labelledBy} className={`panel panel-${variant} ${className}`.trim()}>
      {children}
    </section>
  )
}
