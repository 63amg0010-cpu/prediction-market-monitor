import type { Metadata } from "next"
import { IBM_Plex_Mono, Noto_Sans_KR } from "next/font/google"
import type { ReactNode } from "react"

import "./styles/globals.css"

const uiFont = Noto_Sans_KR({
  weight: "variable",
  display: "swap",
  variable: "--font-ui",
  preload: false,
  fallback: ["Apple SD Gothic Neo", "Malgun Gothic", "system-ui"],
})

const dataFont = IBM_Plex_Mono({
  weight: ["500", "600"],
  subsets: ["latin"],
  display: "swap",
  variable: "--font-data",
})

export const metadata: Metadata = {
  title: { default: "예측시장 커뮤니티분석", template: "%s · 예측시장 커뮤니티분석" },
  description: "예측시장 커뮤니티 수집·분석·보고서 근거를 확인하는 개인 관리자 화면",
  robots: { index: false, follow: false },
}

function RootLayout({ children }: { readonly children: ReactNode }) {
  return (
    <html className={`${uiFont.variable} ${dataFont.variable}`} lang="ko">
      <body>{children}</body>
    </html>
  )
}

export { RootLayout as default }
