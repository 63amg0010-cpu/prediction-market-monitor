import { DashboardLoader } from "../../components/dashboard-loader"
import { type PageSearchParams, parsePageFilters } from "../../lib/filter-contract"

type PageProps = { readonly searchParams: Promise<PageSearchParams> }

async function PostsPage({ searchParams }: PageProps) {
  return <DashboardLoader activeView="posts" filters={parsePageFilters(await searchParams)} />
}

export { PostsPage as default }
