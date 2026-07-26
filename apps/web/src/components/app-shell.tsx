import { Article, ChartLineUp, FileText, Pulse } from "@phosphor-icons/react/ssr"
import Link from "next/link"
import type { ReactNode } from "react"

import { LogoutButton } from "./logout-button"

export type DashboardViewName = "overview" | "posts" | "reports" | "status"

type AppShellProps = {
  readonly activeView: DashboardViewName
  readonly children: ReactNode
  readonly preview?: "desktop" | "tablet" | "mobile"
}

const NAV_ITEMS = [
  { key: "overview", href: "/", label: "개요", icon: ChartLineUp },
  { key: "posts", href: "/posts", label: "게시글", icon: Article },
  { key: "reports", href: "/reports", label: "보고서", icon: FileText },
  { key: "status", href: "/status", label: "상태", icon: Pulse },
] as const

export function AppShell({ activeView, children, preview }: AppShellProps) {
  const contentId = preview === undefined ? "main-content" : `showcase-${preview}-content`
  const navLabel = preview === undefined ? "주요 메뉴" : `${preview} 검증 주요 메뉴`
  return (
    <div
      className={`app-shell${preview === undefined ? "" : " app-shell-preview"}`}
      data-preview-viewport={preview}
    >
      <a className="skip-link" href={`#${contentId}`}>
        본문으로 건너뛰기
      </a>
      <header className="app-header">
        <Link className="brand" href="/">
          <span aria-hidden>PM</span>
          <strong>예측시장 커뮤니티분석</strong>
        </Link>
        <div className="header-meta">
          <span>개인 관리자</span>
          <LogoutButton />
        </div>
      </header>
      <nav aria-label={navLabel} className="app-nav">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon
          return (
            <Link
              aria-current={activeView === item.key ? "page" : undefined}
              href={item.href}
              key={item.key}
            >
              <Icon aria-hidden size={20} />
              <span>{item.label}</span>
            </Link>
          )
        })}
      </nav>
      <main className="main-scroll-region" id={contentId} tabIndex={-1}>
        {children}
      </main>
    </div>
  )
}
