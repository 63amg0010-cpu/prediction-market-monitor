import { DashboardLoader } from "../../components/dashboard-loader"
import { type PageSearchParams, parsePageFilters } from "../../lib/filter-contract"

type PageProps = { readonly searchParams: Promise<PageSearchParams> }

async function StatusPage({ searchParams }: PageProps) {
  return <DashboardLoader activeView="status" filters={parsePageFilters(await searchParams)} />
}

export { StatusPage as default }
