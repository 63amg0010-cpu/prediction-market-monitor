"use client"

import ky from "ky"
import { useEffect, useState } from "react"

import { type DashboardState, DashboardStateSchema } from "../lib/dashboard-contract"
import { type DashboardFilters, filtersToPageSearchParams } from "../lib/filter-contract"
import type { DashboardViewName } from "./app-shell"
import { DashboardView } from "./dashboard-view"

type DashboardLoaderProps = {
  readonly activeView: DashboardViewName
  readonly filters: DashboardFilters
}

const INITIAL_STATE: DashboardState = { kind: "loading" }

export function DashboardLoader({ activeView, filters }: DashboardLoaderProps) {
  const [state, setState] = useState<DashboardState>(INITIAL_STATE)
  const [requestVersion, setRequestVersion] = useState(0)
  const query = filtersToPageSearchParams(filters).toString()

  useEffect(() => {
    const abortController = new AbortController()
    async function load(): Promise<void> {
      setState(INITIAL_STATE)
      try {
        const response = await ky.get(`/api/dashboard?${query}`, {
          retry: 0,
          throwHttpErrors: false,
          timeout: 30_000,
          signal: abortController.signal,
          headers: {
            "cache-control": "no-store",
            "x-client-request-version": requestVersion.toString(),
          },
        })
        if (response.status === 401 || response.status === 403) {
          window.location.assign("/login")
          return
        }
        let raw: unknown
        try {
          raw = await response.json()
        } catch (error) {
          if (!(error instanceof Error)) {
            throw error
          }
          raw = null
        }
        const parsed = DashboardStateSchema.safeParse(raw)
        if (!parsed.success) {
          setState({
            kind: "unavailable",
            reason: "BFF 응답 형식을 확인할 수 없습니다.",
            correlationId: null,
            retryable: false,
          })
          return
        }
        if (!abortController.signal.aborted) {
          setState(parsed.data)
        }
      } catch (caught) {
        if (caught instanceof Error && caught.name === "AbortError") {
          return
        }
        if (caught instanceof Error) {
          setState({
            kind: "unavailable",
            reason: "BFF에 연결할 수 없습니다.",
            correlationId: null,
            retryable: true,
          })
          return
        }
        throw caught
      }
    }
    void load()
    return () => abortController.abort()
  }, [query, requestVersion])

  function retry(): void {
    setRequestVersion((current) => current + 1)
    document.getElementById("main-content")?.focus()
  }

  return <DashboardView activeView={activeView} filters={filters} onRetry={retry} state={state} />
}
