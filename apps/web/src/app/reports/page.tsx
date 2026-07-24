import { DashboardLoader } from "../../components/dashboard-loader"
import { type PageSearchParams, parsePageFilters } from "../../lib/filter-contract"

type PageProps = { readonly searchParams: Promise<PageSearchParams> }

async function ReportsPage({ searchParams }: PageProps) {
  return <DashboardLoader activeView="reports" filters={parsePageFilters(await searchParams)} />
}

export { ReportsPage as default }
