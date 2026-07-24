import { DashboardLoader } from "../components/dashboard-loader"
import { type PageSearchParams, parsePageFilters } from "../lib/filter-contract"

type PageProps = { readonly searchParams: Promise<PageSearchParams> }

async function OverviewPage({ searchParams }: PageProps) {
  return <DashboardLoader activeView="overview" filters={parsePageFilters(await searchParams)} />
}

export { OverviewPage as default }
