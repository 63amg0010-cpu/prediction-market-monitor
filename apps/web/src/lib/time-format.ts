export type Freshness = "fresh" | "due" | "stale" | "no_success"

const KST_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
})

export function freshnessAt(value: string | null, reference: string): Freshness {
  if (value === null) return "no_success"
  const ageSeconds = (Date.parse(reference) - Date.parse(value)) / 1_000
  if (!Number.isFinite(ageSeconds)) return "no_success"
  if (ageSeconds <= 2 * 60 * 60 + 15 * 60) return "fresh"
  if (ageSeconds <= 3 * 60 * 60) return "due"
  return "stale"
}

export function freshnessLabel(freshness: Freshness): string {
  switch (freshness) {
    case "fresh":
      return "최신"
    case "due":
      return "갱신 예정"
    case "stale":
      return "지연"
    case "no_success":
      return "성공 이력 없음"
  }
}

function relativeLabel(value: string, reference: string): string {
  const seconds = Math.max(0, Math.round((Date.parse(reference) - Date.parse(value)) / 1_000))
  if (seconds < 60) return "방금 전"
  if (seconds < 3_600) return `${Math.round(seconds / 60)}분 전`
  if (seconds < 86_400) return `${Math.round(seconds / 3_600)}시간 전`
  return `${Math.round(seconds / 86_400)}일 전`
}

export function formatTimestamp(value: string | null, reference: string): string {
  if (value === null) return "성공 이력 없음"
  return `${KST_FORMATTER.format(new Date(value))} KST · ${relativeLabel(value, reference)}`
}
