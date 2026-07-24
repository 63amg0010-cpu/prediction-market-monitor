import { Check, CircleNotch, Plus, Trash } from "@phosphor-icons/react/ssr"
import type { Metadata } from "next"

import { AppShell } from "../../components/app-shell"
import { ChartFrame } from "../../components/chart-frame"
import { EvidenceRail } from "../../components/evidence-rail"
import { FilterBar } from "../../components/filter-bar"
import { MentionAnalysis } from "../../components/mention-analysis"
import { Panel } from "../../components/panel"
import { ShowcaseDataTable, ShowcaseVolume } from "../../components/showcase-data-table"
import { ShowcaseInteractions } from "../../components/showcase-interactions"
import { ShowcaseMetrics } from "../../components/showcase-metric"
import { StatusBadge } from "../../components/status-badge"

const OUTCOMES = ["success", "pending", "blocked", "partial", "error", "unknown"] as const

export const metadata: Metadata = {
  title: "Design validation harness",
  robots: { index: false, follow: false },
}

export default function DesignSystemPage() {
  return (
    <main className="showcase-page" id="main-content" tabIndex={-1}>
      <header>
        <p className="eyebrow">UNLISTED VALIDATION HARNESS</p>
        <h1>프리미티브 쇼케이스</h1>
        <p>제품 데이터가 아닌 상태·반응형·접근성 검증 전용 표면입니다.</p>
      </header>

      <Panel labelledBy="showcase-shell-title">
        <h2 id="showcase-shell-title">실제 AppShell 반응형 변형</h2>
        <p className="chart-caveat">동일한 AppShell 프리미티브를 뷰포트 계약별로 렌더링합니다.</p>
        <div className="showcase-shell-variants">
          {(["desktop", "tablet", "mobile"] as const).map((variant) => (
            <div className="showcase-shell-preview" data-preview-viewport={variant} key={variant}>
              <strong className="showcase-preview-label">{variant}</strong>
              <AppShell activeView="overview" preview={variant}>
                <div className="showcase-preview-content">
                  <p className="eyebrow">APP SHELL PREVIEW</p>
                  <strong>콘텐츠 영역</strong>
                  <span>내비게이션과 건너뛰기 링크를 확인합니다.</span>
                </div>
              </AppShell>
            </div>
          ))}
        </div>
      </Panel>

      <Panel labelledBy="showcase-confidence-title">
        <h2 id="showcase-confidence-title">데이터 신뢰도 매트릭스</h2>
        <p className="chart-caveat">연결되지 않은 검증 상태를 성공으로 꾸미지 않습니다.</p>
        <div className="showcase-evidence-matrix">
          <EvidenceRail
            activeView="overview"
            data={null}
            id="showcase-evidence-normal"
            previewState="normal"
            unavailableReason={null}
          />
          <EvidenceRail
            activeView="overview"
            data={null}
            id="showcase-evidence-null"
            previewState="null"
            unavailableReason="검증 전용 미연결 상태"
          />
          <EvidenceRail
            activeView="overview"
            data={null}
            id="showcase-evidence-unknown"
            previewState="unknown"
            unavailableReason="검증 상태 미판별"
          />
          <EvidenceRail
            activeView="overview"
            data={null}
            id="showcase-evidence-loading"
            previewState="loading"
            unavailableReason="검증 상태 로딩"
          />
          <EvidenceRail
            activeView="overview"
            data={null}
            id="showcase-evidence-error"
            previewState="error"
            unavailableReason="검증 상태 오류"
          />
          <EvidenceRail
            activeView="overview"
            data={null}
            id="showcase-evidence-blocked"
            previewState="blocked"
            unavailableReason="검증 상태 차단"
          />
        </div>
      </Panel>

      <Panel labelledBy="showcase-status-title">
        <h2 id="showcase-status-title">상태·피드백·버튼 매트릭스</h2>
        <div className="showcase-row">
          {OUTCOMES.map((outcome) => (
            <StatusBadge key={outcome} outcome={outcome} />
          ))}
        </div>
        <div className="showcase-feedback-grid">
          <output className="feedback-block feedback-pending">
            대기: 결과를 불러오는 중입니다.
          </output>
          <output className="feedback-block feedback-error">
            오류: 입력 근거를 확인해 주세요.
          </output>
          <div className="empty-state">빈 결과: 조건에 맞는 항목이 없습니다.</div>
        </div>
        <div className="showcase-row">
          <button className="button button-primary" type="button">
            기본 동작
          </button>
          <button className="button button-secondary" type="button">
            보조 동작
          </button>
          <button className="button button-ghost" type="button">
            고스트 동작
          </button>
          <button className="button button-danger" type="button">
            <Trash aria-hidden size={20} /> 삭제
          </button>
          <button aria-label="추가" className="icon-button" type="button">
            <Plus aria-hidden size={20} />
          </button>
          <button aria-busy="true" className="button button-secondary" type="button">
            <CircleNotch aria-hidden size={20} /> 처리 중
          </button>
          <button className="button button-success" type="button">
            <Check aria-hidden size={20} /> 완료됨
          </button>
          <button className="button button-primary" disabled type="button">
            비활성
          </button>
        </div>
        <ShowcaseInteractions />
      </Panel>

      <Panel labelledBy="showcase-filter-title">
        <h2 id="showcase-filter-title">반응형 필터 상태</h2>
        <FilterBar
          actionPath="/__design-system"
          filters={{ country: "kr", sourceId: "", keyword: "검증", period: "24h" }}
          resultCount={12}
          sources={[]}
        />
      </Panel>

      <ShowcaseMetrics />

      <Panel labelledBy="showcase-chart-frame-title">
        <h2 id="showcase-chart-frame-title">재사용 차트 프레임 상태</h2>
        <p className="chart-caveat">
          line, bar, stacked 렌더러가 정상·대기·빈 결과·오류·부분 상태를 공유합니다.
        </p>
        <div className="showcase-chart-grid showcase-chart-frame-grid">
          <ChartFrame
            description="정상 시계열"
            id="showcase-chart-line"
            kind="line"
            labels={["월", "화", "수"]}
            state="ready"
            title="Line · 정상"
            values={[4, 8, 6]}
          />
          <ChartFrame
            description="데이터 대기"
            id="showcase-chart-bar-loading"
            kind="bar"
            state="loading"
            title="Bar · 대기"
          />
          <ChartFrame
            description="일부 구간 확인"
            id="showcase-chart-stacked-partial"
            kind="stacked"
            labels={["긍정", "중립", "부정"]}
            state="partial"
            title="Stacked · 부분"
            values={[6, 3, 1]}
          />
          <ChartFrame
            id="showcase-chart-line-empty"
            kind="line"
            state="empty"
            title="Line · 빈 결과"
          />
          <ChartFrame
            id="showcase-chart-bar-error"
            kind="bar"
            retryHref="#showcase-chart-bar-error"
            state="error"
            title="Bar · 오류"
          />
          <ChartFrame
            id="showcase-chart-bar-blocked"
            kind="bar"
            retryHref="#showcase-chart-bar-blocked"
            state="blocked"
            title="Bar · 차단"
          />
        </div>
      </Panel>

      <section
        aria-label="실제 차트 프리미티브"
        className="showcase-chart-composition"
        id="showcase-chart-title"
      >
        <MentionAnalysis dashboard={null} />
      </section>

      <ShowcaseDataTable />
      <ShowcaseVolume />
    </main>
  )
}
