# 예측시장 커뮤니티 반응 모니터 디자인 시스템

상태: **구현 전 계약**  
적용 대상: `apps/web`의 개인용 관리자 대시보드, 게시글, 일일 보고서, 운영 상태 화면  
기준일: 2026-07-22  
권위: 화면 코드보다 이 문서가 먼저다. 코드에 필요한 토큰, 프리미티브, 상태, 접근성 규칙이 없으면 이 문서를 먼저 갱신한다.

## 0. Research Log

- **제품/콘텐츠 근거:** 제품 사양과 Phase 4 범위를 읽었다. 화면의 우선 질문은 "오늘의 수치를 믿을 수 있는가?"이며, 그 다음이 언급량, 증감률, 감성, 참여도, 게시글, 일일 보고서다. 단일 관리자용이며 한국어가 기본이고 영문 원문과 미국 소스가 함께 나타난다.
- **Embedded references:** Layer B 후보는 `sentry.md`, `kraken.md`, `posthog.md`였다. Layer A는 운영형 기본인 `taste-skill.md`, Layer B는 오류·운영상태·고밀도 정보 구분이 가장 강한 `sentry.md`를 선택했다. 두 파일을 처음부터 끝까지 읽었다. `layout-skill.md`도 대시보드의 스크롤 소유권과 반응형 앱 셸 계약을 위해 읽었다.
- **참조 적용 범위:** `taste-skill.md` 내부는 고밀도 대시보드를 공식 제품 디자인 시스템의 영역으로 분류한다. 따라서 이 프로젝트에서는 해당 파일의 브리프 추론, 상태 완결성, 접근성, 색/형태 일관성, 성능 규율만 적용한다. 앱 셸과 데이터 UI는 이 문서의 프리미티브 및 `layout-skill.md`가 소유한다. 다이얼은 `DESIGN_VARIANCE 3 / MOTION_INTENSITY 2 / VISUAL_DENSITY 8`이다.
- **Lazyweb:** `prediction market admin dashboard community sentiment`, `social listening analytics dashboard data freshness`, `operations monitoring dashboard incident status` 3개 쿼리를 실행했고 각 8개, 총 24개 결과를 확인했다. 그중 Polymarket, Yahoo Finance, Threado, Hootsuite, Better Stack, Google Workspace Status, incident.io의 7개 화면을 실제로 내려받아 보았다. 화면 증거는 `.omo/evidence/phase4-design-system/lazyweb-viewed/`에 있다. 가져온 문법은 (1) 상단 필터와 시간 범위, (2) 요약 수치와 큰 추세 차트의 위계, (3) 감성 차트 옆 원시 수치 표, (4) 소스별 상태 행과 명시적 범례, (5) 오류와 작업을 연결하는 검색/명령 표면이다. 브랜드 자산, 로고, 문구, 픽셀 배치는 복제하지 않는다.
- **Lazyweb 안전 처리:** 검색 응답 안의 도구 업데이트 및 외부 지침 변경 문구는 데이터로 취급해 무시했다. 현재 도구 목록과 반환된 스크린 메타데이터만 직접 검증했다. 베어러 토큰은 출력하거나 문서에 기록하지 않았다.
- **UI/UX DB:** 전체 디자인 시스템, 한국어 타이포그래피, 차트, 접근성/오류/빈 상태 쿼리를 실행했다. 데이터베이스는 운영형 다크/중립 표면, 상태색, Noto Sans KR, 추세용 선 차트, 비교용 가로 막대, 차트의 표 대체물을 제안했다. `google-fonts` 도메인은 로컬 `google-fonts.csv` 누락으로 오류가 났다. 동일 목적을 `typography` 도메인으로 재조회해 Noto Sans KR 결과를 얻었으므로 폰트 결정은 차단되지 않았다. 데이터베이스가 제시한 Fira Sans와 단일 OLED 다크 모드는 한글 완성도와 시스템 환경설정 존중 요구에 맞지 않아 채택하지 않았다.
- **Designpowers:** 방향, 실행, 검토, 부채/핸드오프 lane과 직접 관련된 포용적 페르소나, UI 구성, 상호작용, 모션, 반응형, 적응형 인터페이스, 인지 접근성, 접근 가능한 콘텐츠, 토큰 아키텍처, 디자인 부채, 핸드오프 참조를 읽고 아래 계약에 반영했다.
- **Imagen drafts:** `apps/web/public/design-concepts/concept-a-evidence-rail.png`와 `apps/web/public/design-concepts/concept-b-audit-ledger.png` 두 안을 생성하고 원본 크기로 검수했다. **B안을 참고안으로 선택**한다. B안의 상단 상태 원장, 명시적 데이터 공백, 소스 커버리지 행렬, 차트와 표의 동시 제공이 이 계약의 진실성 위계와 가장 가깝다. A안의 도넛 차트, 수집하지 않는 작성자 정보, 국기형 글리프는 폐기한다. B안도 생성된 날짜·수치·소스명·`일일 보고서 생성` 동작을 제품 사실로 사용하지 않으며, 구현 시 API 스키마와 이 문서의 카피/상태/동작 계약으로 교체한다.
- **Skipped lanes:** 없음. `google-fonts` 도메인 오류는 위의 대체 조회로 복구된 부분 오류이며 research lane 전체를 건너뛴 것이 아니다.

### 0.1 실제 화면에서 가져온 문법

| 화면 | 가져올 것 | 가져오지 않을 것 |
|---|---|---|
| Polymarket | 조밀한 카드 안의 짧은 질문, 범주 필터, 핵심 값 우선순위 | 거래용 Yes/No 버튼, 브랜드 색, 거래 유도 문구 |
| Yahoo Finance | 큰 추세 패널과 주변 비교 카드의 정보 위계 | 과도한 3중 사이드바, 긴 빈 공간, 금융 포털 크롬 |
| Threado | 처리 중 배너, 차트와 수치 표의 결합, 보고서형 내비게이션 | 무의미한 업그레이드 배너, 브랜드 아이콘 |
| Hootsuite | 필터 칩, 핵심 지표, 시간 추세, 감성 비율, 활동 밀도 | 한 화면에 과도하게 분리된 내비게이션 레이어 |
| Better Stack | 낮은 채도의 다크 셸, 상태 행, 작업 검색, 오류에서 행동으로의 연결 | 온보딩 체크리스트, 제품 고유 문구 |
| Google Status | 상태 범례, 소스별 시간 이력, 마지막 갱신 시각 | 색과 작은 아이콘만으로 의미 전달하기 |
| incident.io | 명령 검색, 심각도 라벨, 현재 작업을 유지하는 오버레이 | 사고 대응 제품의 고유 정보 구조 |

### 0.2 콘텐츠 블록과 사용자 결정 경로

| 순서 | 블록 | 역할 | 답해야 하는 질문 |
|---:|---|---|---|
| 1 | 페이지 헤더 | 현재 위치와 시간 범위 식별 | 지금 어떤 범위의 데이터를 보고 있는가? |
| 2 | 데이터 신뢰도 레일 | 증명 | 이 화면의 수치가 최신이며 완전한가? |
| 3 | 필터 바 | 비교 조건 설정 | 국가, 커뮤니티, 키워드, 기간을 어떻게 좁힐까? |
| 4 | 핵심 지표 | 설명 | 언급량, 변화, 감성, 참여도가 어떻게 움직였나? |
| 5 | 추세 및 비교 차트 | 비교 | 언제, 어느 소스에서 변화가 발생했나? |
| 6 | 최근 게시글 | 근거 탐색 | 어떤 원문이 수치 변화를 만들었나? |
| 7 | 일일 보고서 | 요약과 유지 | 오늘 확인해야 할 핵심 변화는 무엇인가? |
| 8 | 운영 상태와 복구 | 행동 | 막히거나 실패한 항목을 어떻게 안전하게 복구할까? |

## 1. Atmosphere & Identity

### 1.1 Design Read

한국어 비개발자 1명이 노트북과 휴대폰에서 사용하는 고밀도 운영 대시보드로 읽는다. 분위기는 **조용한 야간 관제석**이며, 장식보다 신뢰, 최신성, 복구 가능성을 먼저 보여준다.

### 1.2 기억에 남을 한 가지

시그니처는 상단의 **데이터 신뢰도 레일**이다. 수집, 소스 반영, AI 분석, 일일 보고서가 서로 다른 근거와 시각을 가진다는 사실을 한 줄에서 보여준다. 성공처럼 보이는 초록색 장식이 아니라, 상태 아이콘, 명시적 상태어, 절대 시각, 상대 시각, 범위, 근거 링크가 함께 있어야 한다.

### 1.3 재질과 색 이야기

Sentry에서 가져온 비기본 결정은 순수 검정 대신 따뜻한 자주빛 검정, 회색 대신 자주빛 중성 램프, 눌리는 느낌의 얕은 inset 컨트롤, 오버레이에만 쓰는 자주빛 주변광이다. 일반 카드에 유리 효과나 글로우를 반복하지 않는다. 배경, 내비게이션, 기본 패널, 상승 패널의 명도 차와 한 줄 테두리로 깊이를 만든다.

### 1.4 디자인 원칙

1. **Truth before trend:** 최신성, 반영 범위, 분석 커버리지가 수치보다 먼저다.
2. **Unknown is not zero:** 집계되지 않음, 대기, 차단, 누락을 0 또는 중립으로 바꾸지 않는다.
3. **Action follows cause:** 오류와 차단은 원인, 영향 범위, 다음 행동을 함께 제공한다.
4. **Dense, not cramped:** 정보는 조밀하지만 44px 동작 영역, 명확한 그룹, 단일 스크롤 소유권을 지킨다.
5. **Korean first, bilingual safe:** 한국어 줄바꿈과 숫자 표를 우선하고 영문 원문, 긴 URL, 혼합 문자열에도 깨지지 않는다.
6. **No decorative telemetry:** 상태 점, 펄스, 움직이는 그래프는 실제 의미가 있을 때만 사용한다.

### 1.5 포용적 페르소나와 통과 조건

| 페르소나 | 상황과 제약 | 핵심 과업 | 이 디자인의 통과 조건 |
|---|---|---|---|
| 민서, 주 사용자 | 개발 지식이 없고 업무 전후 노트북으로 5분 점검 | 오늘 데이터가 믿을 만한지 판단하고 원문 1개 열기 | 30초 안에 최신성, 누락 소스, 분석 대기를 설명할 수 있고 로그를 읽지 않아도 된다 |
| 현우, 이동 중 주 사용자 | 375px 휴대폰, 한 손, 주의가 분산된 상황 | 실패 여부와 오늘 보고서만 빠르게 확인 | 첫 화면에서 상태 요약과 보고서로 이동할 수 있고 가로 스크롤이나 작은 아이콘 조작이 없다 |
| 수진, 저시력/색각 제약 | 200% 줌, 고대비 설정, 적록 구분이 어려움 | 성공, 차단, 오류, 부분 완료를 구분 | 색을 제거해도 아이콘, 텍스트, 구조로 모든 상태를 구분하고 확대 시 내용 손실이 없다 |
| 준호, 운동/키보드 제약 | 미세 조작이 어렵고 키보드를 주로 사용 | 필터 적용, 게시글 열기, 재시도 실행 | 논리적 탭 순서, 44px 대상, 보이는 포커스, 키보드 동등 기능, 포커스 복귀가 모두 동작한다 |

### 1.6 음성 및 문체

- **직접적:** 원인과 다음 행동을 먼저 쓴다. 기술 용어만 나열하지 않는다.
- **차분함:** 오류에서도 사용자를 탓하거나 과장하지 않는다.
- **증거 중심:** 성공을 주장할 때 완료 시각, 범위, 근거를 보여준다.
- **일관됨:** `완료`, `대기 중`, `차단됨`, `일부 완료`, `오류`, `집계되지 않음`을 동의어로 바꾸지 않는다.
- 전문 도구의 읽기 수준을 유지하되 한 문장에 한 가지 정보만 담는다.
- 이모지와 이모지 아이콘을 사용하지 않는다.

## 2. Color & Token Architecture

토큰은 global -> semantic -> component 3단계다. 컴포넌트 CSS에는 global 원시색을 직접 쓰지 않는다. 테마는 semantic 매핑만 바꾼다.

### 2.1 Global color tokens

| Token | Value | Source / role |
|---|---|---|
| `--color-ink-1000` | `#120F1A` | Sentry warm-black을 더 낮춘 최심부 |
| `--color-ink-950` | `#171321` | 내비게이션, 깊은 표면 |
| `--color-ink-900` | `#1F192C` | 기본 다크 표면 |
| `--color-ink-850` | `#272037` | 상승 다크 표면 |
| `--color-ink-800` | `#302742` | 선택/호버 다크 표면 |
| `--color-ink-700` | `#463A5A` | 강한 다크 경계, 라이트 보조 텍스트 |
| `--color-ink-600` | `#665978` | 보조 중성 |
| `--color-ink-500` | `#877B96` | 비활성 중성 |
| `--color-ink-400` | `#A99FB5` | 다크 3차 텍스트 |
| `--color-ink-300` | `#CDC6D3` | 다크 2차 텍스트 |
| `--color-ink-200` | `#E2DDE6` | 라이트 경계 |
| `--color-ink-100` | `#F1EDF3` | 라이트 내비게이션 |
| `--color-ink-50` | `#F8F6FA` | 라이트 캔버스, 다크 기본 텍스트 |
| `--color-white` | `#FFFFFF` | 라이트 기본 표면 |
| `--color-violet-800` | `#40358D` | 라이트 인터랙션 호버 |
| `--color-violet-700` | `#5145A8` | 라이트 인터랙션 |
| `--color-violet-500` | `#8D82E6` | 다크 데이터 시리즈 |
| `--color-violet-300` | `#C4BEF6` | 다크 인터랙션과 포커스 |
| `--color-violet-100` | `#EEECFD` | 라이트 선택 표면 |
| `--color-green-700` | `#166A42` | 라이트 성공 텍스트 |
| `--color-green-300` | `#76D39F` | 다크 성공 텍스트 |
| `--color-green-100` | `#DDF7E8` | 라이트 성공 표면 |
| `--color-blue-700` | `#275FA8` | 라이트 대기/정보 텍스트 |
| `--color-blue-300` | `#7EB2F2` | 다크 대기/정보 텍스트 |
| `--color-blue-100` | `#E5F0FF` | 라이트 대기/정보 표면 |
| `--color-amber-800` | `#764500` | 라이트 차단 텍스트 |
| `--color-amber-400` | `#E3A638` | 다크 차단 텍스트 |
| `--color-amber-100` | `#FFF0C7` | 라이트 차단 표면 |
| `--color-red-700` | `#A92C45` | 라이트 오류 텍스트 |
| `--color-red-300` | `#FF8798` | 다크 오류 텍스트 |
| `--color-red-100` | `#FFE4E9` | 라이트 오류 표면 |

### 2.2 Semantic theme mapping

| Semantic token | Light | Dark | Usage |
|---|---|---|---|
| `--surface-canvas` | `--color-ink-50` | `--color-ink-1000` | 앱 배경 |
| `--surface-nav` | `--color-ink-100` | `--color-ink-950` | 사이드/하단 내비게이션 |
| `--surface-base` | `--color-white` | `--color-ink-900` | 기본 패널 |
| `--surface-raised` | `--color-white` | `--color-ink-850` | 메뉴, 강조 패널 |
| `--surface-interactive` | `--color-violet-100` | `--color-ink-800` | 선택된 행/필터 |
| `--surface-scrim` | `rgb(18 15 26 / 56%)` | `rgb(18 15 26 / 72%)` | 모달 배경 |
| `--text-primary` | `--color-ink-950` | `--color-ink-50` | 제목, 본문 |
| `--text-secondary` | `--color-ink-700` | `--color-ink-300` | 보조 설명 |
| `--text-tertiary` | `--color-ink-600` | `--color-ink-400` | 부가 메타데이터 |
| `--text-disabled` | `--color-ink-500` | `--color-ink-600` | 비활성 컨트롤 |
| `--border-subtle` | `--color-ink-100` | `--color-ink-800` | 그룹 분리 |
| `--border-default` | `--color-ink-200` | `--color-ink-700` | 카드/입력 경계 |
| `--border-strong` | `--color-ink-400` | `--color-ink-600` | 호버/선택 경계 |
| `--interactive-primary` | `--color-violet-700` | `--color-violet-300` | 링크, 선택, 기본 액션 |
| `--interactive-primary-hover` | `--color-violet-800` | `--color-violet-100` | 호버 |
| `--interactive-on-primary` | `--color-white` | `--color-ink-1000` | 기본 액션 글자 |
| `--focus-ring` | `--color-violet-700` | `--color-violet-300` | 2px 외곽 포커스 |
| `--status-success-fg` | `--color-green-700` | `--color-green-300` | 완료 |
| `--status-success-bg` | `--color-green-100` | `#183226` | 완료 표면 |
| `--status-pending-fg` | `--color-blue-700` | `--color-blue-300` | 대기 |
| `--status-pending-bg` | `--color-blue-100` | `#192D45` | 대기 표면 |
| `--status-blocked-fg` | `--color-amber-800` | `--color-amber-400` | 차단 |
| `--status-blocked-bg` | `--color-amber-100` | `#3A2B12` | 차단 표면 |
| `--status-partial-fg` | `--color-violet-700` | `--color-violet-300` | 일부 완료 |
| `--status-partial-bg` | `--color-violet-100` | `#2D2744` | 일부 완료 표면 |
| `--status-error-fg` | `--color-red-700` | `--color-red-300` | 오류 |
| `--status-error-bg` | `--color-red-100` | `#3D2028` | 오류 표면 |

### 2.3 Component token aliases

```text
--button-primary-bg: var(--interactive-primary)
--button-primary-fg: var(--interactive-on-primary)
--button-primary-border: var(--interactive-primary)
--control-bg: var(--surface-base)
--control-border: var(--border-default)
--panel-bg: var(--surface-base)
--panel-border: var(--border-subtle)
--row-hover-bg: var(--surface-interactive)
--chart-grid: var(--border-subtle)
--chart-current: var(--interactive-primary)
--chart-previous: var(--text-tertiary)
--overlay-bg: var(--surface-raised)
```

### 2.4 Color rules

- 기본 테마는 시스템 `prefers-color-scheme`을 따른다. 사용자가 테마를 선택하면 명시적 선택을 세션 간 보존한다.
- 인터랙션 보라색과 상태색을 섞지 않는다. 보라색 버튼이 성공을 뜻하지 않는다.
- 상태색은 항상 Phosphor 아이콘, 텍스트 상태어, 필요한 경우 수치와 함께 쓴다. 색만으로 상태를 표현하지 않는다.
- 차트 선/막대는 배경 대비 3:1, 모든 데이터 라벨은 4.5:1 이상이어야 한다.
- 본문/배경은 WCAG 2.2 AA 4.5:1 이상, 큰 글자는 3:1 이상, 포커스와 컨트롤 경계는 인접색 대비 3:1 이상이어야 한다.
- 새 원시색은 금지한다. 진짜 새 의미가 생기면 global과 semantic 토큰을 함께 추가하고 대비 증거를 남긴다.
- 순수 검정은 쓰지 않는다. 순수 흰색은 라이트 기본 표면에서만 허용한다.

## 3. Korean Typography, Icons & Data Formatting

### 3.1 Font stack

- **Primary UI:** `"Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif`
- **Numeric/mono:** `"IBM Plex Mono", ui-monospace, "SFMono-Regular", Consolas, monospace`
- 런타임 외부 폰트 요청은 금지한다. Next.js 구현에서는 `next/font`로 필요한 weight와 subset을 자체 호스팅하고 `display: swap`을 사용한다.
- 숫자/mono는 시각화 값, 비율, 큐 수, 코드, 타임스탬프에만 쓴다. 한국어 문장과 상태어에는 mono를 쓰지 않는다.
- 폰트 패밀리는 위 2개를 넘지 않는다.

### 3.2 Type scale

| Token | Size / line-height | Weight | Tracking | Usage |
|---|---|---:|---:|---|
| `--type-page-title` | `1.75rem / 2.25rem` | 700 | `-0.015em` | 페이지 H1 |
| `--type-section-title` | `1.375rem / 1.875rem` | 700 | `-0.01em` | 주요 H2 |
| `--type-card-title` | `1.125rem / 1.625rem` | 600 | `-0.005em` | 패널 H3 |
| `--type-body` | `1rem / 1.5rem` | 400 | `0` | 기본 본문 |
| `--type-body-strong` | `1rem / 1.5rem` | 600 | `0` | 강조 본문 |
| `--type-label` | `0.875rem / 1.25rem` | 600 | `0` | 버튼, 필터, 표 헤더 |
| `--type-caption` | `0.75rem / 1.125rem` | 500 | `0.01em` | 보조 시각, 단위, 부가 메타만 |
| `--type-metric-lg` | `2rem / 2.375rem` | 650 | `-0.02em` | 핵심 지표 값 |
| `--type-metric-md` | `1.375rem / 1.75rem` | 650 | `-0.01em` | 상태/비교 값 |
| `--type-data` | `0.875rem / 1.25rem` | 500 | `0` | 표 숫자, 축 라벨 |

### 3.3 Korean and bilingual rules

- 한글 제목에는 대문자 변환이나 넓은 tracking을 적용하지 않는다.
- 한국어 문장에는 `word-break: keep-all`; URL, 영문 ID, 토큰에는 `overflow-wrap: anywhere`를 적용한다.
- 본문 최소 크기는 16px이다. 12px caption은 핵심 상태, 오류 원인, 동작 라벨로 사용할 수 없다.
- 축약보다 줄바꿈을 우선한다. 표 안에서 축약할 때 키보드/터치로 전체 값을 확인할 수 있는 버튼 또는 설명을 제공한다.
- 숫자에는 `font-variant-numeric: tabular-nums lining-nums`를 사용한다.
- 수치: `Intl.NumberFormat("ko-KR")`. 백분율은 소수 자릿수 정책을 지표별로 고정한다.
- 시각: `2026. 7. 22. 00:14 KST` 형식의 절대값을 제공하고 `8분 전`을 보조로 쓴다. 상대 시각만 쓰지 않는다.
- 외부 영문 원문 제목은 원문을 보존하되 화면 언어와 다른 경우 `lang="en"`을 지정한다.

### 3.4 Icons

- 한 프로젝트에서 `@phosphor-icons/react` 한 가족만 사용한다. 구현 전 `package.json`을 확인하고 없으면 명시적으로 설치한다.
- 기본은 regular/1.5px 계열, 크기는 `16 / 20 / 24px` 토큰만 사용한다.
- 아이콘 단독 버튼은 보이는 tooltip과 `aria-label`을 모두 갖는다.
- 이모지, 이모지 아이콘, 손으로 그린 SVG path, 서로 다른 아이콘 가족 혼용은 금지한다.
- 상태 매핑: 완료 `CheckCircle`, 대기 `Clock`, 차단 `LockKey`, 일부 완료 `CircleHalf`, 오류 `WarningOctagon`, 정보 `Info`, 외부 링크 `ArrowSquareOut`.

## 4. Spacing, Layout & Responsive Contract

### 4.1 Spacing, sizing and shape

모든 의도적 간격은 4px 기반이다. intrinsic sizing, `%`, `auto`, `minmax()`, `clamp()`, container/viewport unit은 브라우저 메커니즘이며 토큰으로 억지 변환하지 않는다.

| Token | Value | Usage |
|---|---:|---|
| `--space-1` | `4px` | 아이콘 내부 간격 |
| `--space-2` | `8px` | 인라인 그룹 |
| `--space-3` | `12px` | 조밀한 행/컨트롤 내부 |
| `--space-4` | `16px` | 기본 카드/모바일 여백 |
| `--space-5` | `20px` | 중간 패널 내부 |
| `--space-6` | `24px` | 기본 패널/태블릿 여백 |
| `--space-8` | `32px` | 그룹 사이 |
| `--space-10` | `40px` | 페이지 섹션 사이 |
| `--space-12` | `48px` | 큰 섹션 사이 |
| `--space-16` | `64px` | 셸 고정 헤더/하단 내비게이션 기준 |

| Shape token | Value | Rule |
|---|---:|---|
| `--radius-control` | `6px` | 버튼, 입력, 필터 |
| `--radius-panel` | `8px` | 카드, 표, 차트 패널 |
| `--radius-overlay` | `12px` | 모달, 명령 팔레트 |
| `--radius-pill` | `999px` | 상태 badge와 짧은 선택 칩만 |
| `--control-target` | `44px` | 모든 동작 대상의 최소 실제 크기 |
| `--row-min` | `48px` | 조밀한 데이터 행 최소 높이 |

### 4.2 App shell and scroll ownership

- 루트 셸은 `100dvb`로 제한된 `fixed-sidenav-shell`이다. `100vh`를 쓰지 않는다.
- **1280/768:** 사이드 내비게이션과 상단 헤더는 고정 영역이다. `main-scroll-region` 하나만 세로 스크롤을 소유한다.
- **375:** 상단 헤더와 하단 내비게이션은 고정 영역이다. 본문 하나만 스크롤하며 하단 safe-area와 64px 내비게이션 높이를 padding으로 예약한다.
- 스크롤 자식은 `min-block-size: 0; overflow: auto`를 반드시 가진다.
- 차트, 표, 필터가 별도 세로 스크롤을 만들지 않는다. 긴 표는 페이지네이션/가상화로 해결한다.
- 가로 reel은 보조 범례나 날짜만 허용한다. 핵심 콘텐츠는 375px에서 단일 열로 재배치되어야 한다.
- 고정 셸의 DOM/읽기 순서는 skip link -> header -> nav -> main이다. CSS로 시각 순서만 바꾸지 않는다.

### 4.3 Grid

- 최대 본문 너비는 `1440px`; main 영역 안에서 가운데 정렬한다.
- 1280 상태: 12열, 20px gutter, 32px main padding.
- 768 상태: 6열, 16px gutter, 24px main padding.
- 375 상태: 1열, 16px 양쪽 gutter.
- 반복 카드 grid는 `repeat(auto-fit, minmax(min(16rem, 100%), 1fr))` 계약을 사용해 좁은 컨테이너 overflow를 막는다.
- 컴포넌트는 `@container`로 자기 폭에 반응한다. 셸 전환만 `@media`로 처리한다.

### 4.4 Exact viewport behavior

#### 375px

- 56px top app bar + 한 개의 `main-scroll-region` + 64px bottom navigation.
- 하단 내비게이션은 `개요 / 게시글 / 보고서 / 상태` 4개이며 아이콘과 텍스트를 함께 표시한다.
- 페이지 제목 아래에 데이터 신뢰도 요약 1개가 먼저 온다. 펼치면 수집/소스/분석/보고서 4개 행이 된다.
- 필터는 `필터` 버튼으로 여는 전체 높이 sheet이며 적용 전 결과 수를 보여준다. 적용된 조건은 최대 2개만 노출하고 나머지는 `외 3개`처럼 요약한다.
- 핵심 지표는 2열을 기본으로 하되 200% 확대나 긴 번역에서 1열로 바뀐다.
- 추세 차트는 한 번에 최대 2개 시리즈, 축 tick 4개 이하, 직접 라벨을 사용한다. 표 대체물 버튼은 항상 보인다.
- 데이터 표는 카드형 행 목록으로 바뀐다. 제목, 소스, 시각, 감성, 참여도, 외부 링크 순서다.
- primary content에 가로 스크롤이 없어야 한다.

#### 768px

- 72px 아이콘+짧은 라벨 rail, 56px top bar, main 한 개가 스크롤한다.
- 데이터 신뢰도 레일은 2 x 2, 지표도 2 x 2다.
- main은 6열이다. 큰 추세는 6열, 감성/소스 상태는 각각 3열이지만 컨테이너가 좁으면 순서대로 쌓인다.
- 필터는 top bar 아래 wrapping cluster이며 줄이 2개를 넘으면 `필터 더 보기` disclosure로 접는다.
- pointer와 touch 입력을 모두 가정하며 모든 대상은 44px를 유지한다.

#### 1280px

- 232px 고정 sidenav, 64px top header, 나머지 main 한 개가 스크롤한다.
- 데이터 신뢰도 레일과 핵심 지표는 각각 4 x 3열이다.
- 첫 분석 행은 언급량 추세 8열 + 감성 구성 4열이다.
- 다음 행은 최근 게시글 8열 + 일일 보고서/운영 요약 4열이다.
- 필터는 한 줄 cluster를 기본으로 하며 검색, 국가, 커뮤니티, 키워드, 기간, 초기화 순서다.
- 사이드바가 접히더라도 main 폭 계산은 CSS grid가 소유하며 임의 `calc()` 폭을 반복하지 않는다.

### 4.5 Content stress and layering

- 빈 목록, 40자 한국어 라벨, 120자 영문 제목, 공백 없는 URL/ID, `null`, 1,000개 행을 각각 시험한다.
- 1280px에서 200% 확대 시 640px 상당 레이아웃으로 reflow되어야 하며 양방향 스크롤이 없어야 한다.
- z-index는 `base 0 / sticky 10 / nav 20 / popover 40 / modal 60 / toast 80`만 사용한다.
- `overflow-wrap: anywhere`와 `min-inline-size: 0` 없이 긴 URL을 넣지 않는다.

## 5. Components, States & Primitive Showcase

### 5.1 Truthful outcome semantics

아래 5개는 서로 대체할 수 없는 terminal/operational 의미다. `unknown`은 결과 상태가 아니라 증거 부재다.

| State | Korean label | Meaning | Required visible content | Never do |
|---|---|---|---|---|
| `success` | 완료 | 서버가 요구하는 terminal evidence와 영속화가 확인됨 | 범위, 완료 시각, 근거/상세 링크 | HTTP 2xx나 작업 시작만으로 완료 표시 |
| `pending` | 대기 중 | 큐에 있거나 실행 중이며 terminal outcome이 없음 | 현재 단계, 대기 수/시작 시각, 다음 자동 행동 | 중립 또는 성공으로 합산, 무의미한 재시도 |
| `blocked` | 차단됨 | 권한, 정책, capability, 예산, 안전 전제조건이 충족되지 않아 시작/계속할 수 없음 | 차단 이유, 영향 범위, 필요한 소유자 행동, 마지막 검증 시각 | 시도 실패인 `error`로 표시, 조건 미충족 상태에서 재시도 활성화 |
| `partial` | 일부 완료 | 일부 소스/슬라이스만 terminal success이고 최소 1개가 성공하지 못함 | `2/4 소스 반영`, 누락 목록, 합계 영향, 가능한 복구 행동 | 누락 값을 0으로 채움, 전체 성공 색 사용 |
| `error` | 오류 | 실제 시도가 실행됐고 예상 밖 실패로 terminalized됨 | 안전한 오류 요약, 발생 시각, 실패 범위, retry eligibility | 비밀/stack trace 노출, 원인 없는 `오류가 발생했습니다` |
| `unknown` | 집계되지 않음 | 값 또는 근거가 존재하지 않음 | 무엇이 없고 왜 없는지 | `0`, `0%`, `중립`, `완료`로 치환 |

추가 규칙:

- **Freshness는 outcome과 독립이다.** 마지막 성공이 3시간 이내면 `최신`, 2시간 15분 초과 3시간 이하면 `갱신 예정`, 3시간 초과면 `오래됨`, 성공 이력이 없으면 `성공 이력 없음`이다. 현재 오류와 마지막 성공 시각을 동시에 보여줄 수 있다.
- `partial` 합계에는 coverage 분모/분자를 같이 표시한다. 빠진 소스가 있으면 변화율 옆에 `부분 데이터` 라벨을 붙인다.
- 비교 기간 분모가 0이거나 알 수 없으면 변화율은 `비교 불가`다. 무한대나 0%로 표시하지 않는다.
- 미분석 게시글은 감성 분모에서 제외하고 `분석 커버리지 73%`를 함께 보여준다.
- 지속 상태 변경은 `aria-live="polite"`; 사용자가 방금 실행한 동작의 실패는 `role="alert"`를 쓴다.

### 5.2 `AppShell`

- **Purpose:** 고정 내비게이션과 한 개의 main scroll owner를 제공한다.
- **Structure:** skip link -> app header -> sidenav/bottom nav -> `main#main-content`.
- **Variants:** `wide-sidenav`, `compact-rail`, `mobile-bottom-nav`.
- **States:** 기본, 현재 route, nav 접힘, 느린 로드, 세션 만료 오류.
- **Accessibility:** 현재 링크 `aria-current="page"`; route 전환 후 H1 또는 main으로 포커스 이동; 하단 내비게이션은 아이콘+라벨.
- **Layout:** 4.2와 4.4를 그대로 따른다.

### 5.3 `PageHeader`

- **Purpose:** 페이지 제목, 절대 기준 시각, 보조 설명, 한 개의 primary action을 제공한다.
- **Variants:** overview, list, report, operations.
- **States:** 기본, 갱신 중, 오래됨, action loading/disabled/error.
- **Rule:** 제목은 H1 하나다. `최근 갱신`은 상대+절대 시각을 함께 쓴다.

### 5.4 `Button` and `IconButton`

- **Variants:** primary, secondary, ghost, destructive. 한 화면에는 primary intent 하나만 둔다.
- **Structure:** semantic `<button>`, optional Phosphor icon, label, optional progress indicator.
- **States:** default, hover, focus-visible, active, disabled-with-reason, loading, success, error.
- **Spacing:** target 44px, inline padding `--space-3/4`, icon gap `--space-2`.
- **Motion:** press는 `transform: scale(.98)` 100ms; 로딩 중 레이아웃 폭을 바꾸지 않는다.
- **Accessibility:** loading 시 `aria-busy`; disabled reason은 설명과 연결한다. IconButton은 tooltip과 `aria-label` 필수.

### 5.5 `StatusBadge` and `StatusCallout`

- **Purpose:** 5개 outcome과 unknown을 같은 문법으로 표시한다.
- **Structure:** semantic icon + 상태어 + optional count. Callout은 제목 + 원인 + 영향 + 다음 행동 + 시각.
- **States:** success, pending, blocked, partial, error, unknown. hover는 상세 링크가 있을 때만 존재한다.
- **Accessibility:** 색 없이 구분 가능; 장식 아이콘은 `aria-hidden`, 전체 문구가 의미를 말한다.
- **Rule:** badge 텍스트를 줄여 `OK`, `WARN`처럼 바꾸지 않는다.

### 5.6 `EvidenceRail`

- **Purpose:** 수집, 소스, 분석, 보고서 신뢰도를 지표보다 먼저 증명한다.
- **Structure:** label, StatusBadge, 핵심 값/coverage, absolute timestamp, detail link.
- **Variants:** 4-cell wide, 2x2 mid, collapsed summary + disclosure mobile.
- **States:** 모든 outcome, mixed freshness, loading, no evidence.
- **Motion:** disclosure/업데이트는 160ms opacity+translate; 자동 pulse 금지.
- **Accessibility:** 모바일 disclosure는 `aria-expanded`; 상태 변경은 polite live region; DOM 순서는 수집 -> 소스 -> 분석 -> 보고서.

### 5.7 `FilterBar`, `FilterField`, `FilterChip`, `FilterSheet`

- **Purpose:** 국가, 커뮤니티, 키워드, 기간을 조합한다.
- **Structure:** visible label + control + optional helper/error. placeholder-as-label 금지.
- **States:** default, hover, focus, selected, disabled, loading options, empty options, validation error.
- **Keyboard:** tab으로 field 이동, arrow/enter/escape는 native combobox/listbox 계약을 따른다. 필터 적용 후 결과 heading에 포커스를 강제로 옮기지 않고 결과 수를 polite로 알린다.
- **Mobile:** sheet에서 취소/적용이 항상 보이고, 닫으면 trigger로 포커스가 돌아온다.
- **Content:** `초기화`는 destructive 색을 쓰지 않으며 적용된 필터만 제거한다.

### 5.8 `MetricTile`

- **Purpose:** 언급량, 증감률, 감성, 참여도 한 값을 짧게 설명한다.
- **Structure:** H3/label, metric, unit, comparison, coverage/status, optional sparkline.
- **States:** valid success, partial, pending, unknown/null, loading skeleton, error.
- **Rule:** 감성은 대표값 하나만 강조하되 분석 커버리지를 함께 표시한다. engagement unknown은 0이 아니다.
- **Accessibility:** metric과 변화 방향을 문장으로 제공한다. sparkline은 장식이면 `aria-hidden`; 의미가 있으면 text summary 제공.

### 5.9 `Panel`

- **Purpose:** 연관 콘텐츠의 시각적/semantic 그룹이다. 모든 것을 카드로 감싸는 도구가 아니다.
- **Variants:** base, raised, inset, critical.
- **States:** default, hover only if clickable, focus-within, loading, empty, partial, error.
- **Depth:** tonal shift + 1px border. 일반 panel에 glow/blur 금지.
- **Layout:** stack/cluster/grid 중 하나를 이름으로 명시한다.

### 5.10 `ChartFrame`

- **Purpose:** 제목, 요약, 범례, plot, 상태, 표 대체물을 한 계약으로 묶는다.
- **Structure:** H3 + text insight + nearby legend + plot + `데이터 표 보기` disclosure.
- **States:** loading skeleton, success, pending, blocked, partial, empty, error with retry.
- **Accessibility:** SVG/Canvas 자체만 읽게 하지 않는다. visible summary와 semantic table을 제공하며 interactive point/legend는 키보드 접근 가능하다.
- **Responsive:** container width에 따라 tick/series를 줄이되 핵심 데이터나 상태를 숨기지 않는다.

### 5.11 `DataList`, `DataTable`, `PostRow`

- **Purpose:** 최근 게시글, 소스 상태, 차트 원시 값을 탐색한다.
- **Desktop/tablet:** `<table>`에는 caption, scoped headers, `aria-sort`; 50행 이상은 가상화 또는 페이지네이션.
- **Mobile:** DOM 의미를 유지하는 label-value list/card로 바꾼다. 가로 표 스크롤을 기본 해결책으로 쓰지 않는다.
- **PostRow:** 제목, source/country, published time, sentiment+analysis state, engagement known/unknown, external source link.
- **States:** loading rows, empty with filter reset, partial coverage, row error, external link unavailable.
- **Content stress:** 긴 제목은 2줄 후 전체 확인 수단, URL은 어디서나 wrap, null은 명시적 문구.

### 5.12 `DailyReportCard`

- **Purpose:** 보고서 날짜, revision, completeness, 주요 변화, 상승 키워드, source coverage를 요약한다.
- **States:** success, generating/pending, blocked, partial, error, no report.
- **Rule:** 보고서 `success`는 저장된 manifest/hash 재현 근거가 있을 때만 사용한다. late correction은 revision과 수정 시각을 보여준다.
- **Accessibility:** heading 기반 요약, 목록은 의미 순서, 차트가 없어도 내용을 이해할 수 있다.

### 5.13 `FeedbackBlock`, `Toast`, `Dialog`, `CommandPalette`

- **FeedbackBlock:** 페이지/패널에 남아야 하는 blocked, partial, error에 사용한다. 원인과 recovery action을 포함한다.
- **Toast:** 사용자가 방금 실행한 일시적 결과에만 쓴다. focus를 훔치지 않고 `aria-live="polite"`; 오류/차단 근거를 toast에만 남기지 않는다.
- **Dialog:** 파괴적이거나 확인이 필요한 단일 결정만. 닫기, Escape, scrim click 정책, trigger focus 복귀를 정의한다.
- **CommandPalette:** search-first 탐색/작업 표면. `Ctrl/Cmd+K`는 보조 진입이며 항상 보이는 검색/메뉴 대안이 있다. overlay에만 `--radius-overlay`와 자주빛 ambient shadow를 허용한다.

### 5.14 Primitive Showcase Gate

제품 화면 조합 전에 dev-only `/__design-system` 또는 동등한 state harness를 먼저 만든다. production nav에 노출하지 않고 production build에는 포함하지 않는다.

필수 showcase matrix:

| Primitive | Required scenarios |
|---|---|
| AppShell/Nav | 375 bottom nav, 768 compact rail, 1280 sidenav, current route, 200% zoom |
| Button/IconButton | variants x default/hover/focus/active/disabled/loading/success/error, 긴 한글 label |
| StatusBadge/Callout | success/pending/blocked/partial/error/unknown, light/dark, 색 제거 상태 |
| EvidenceRail | 4-cell, 2x2, collapsed mobile, mixed outcome/freshness, no evidence |
| Filters | default/selected/empty/error/loading, keyboard sequence, mobile sheet focus return |
| MetricTile | valid/partial/pending/null/loading/error, 긴 단위와 큰 수 |
| Panel/Feedback | base/raised/critical, empty/blocked/error/recovery |
| ChartFrame | line/bar/stacked composition, loading/empty/partial/error, table alternative |
| DataTable/PostRow | sort/focus/external link, 0/1/50+ rows, 긴 제목/URL/null |
| Dialog/CommandPalette | open/close/Escape, initial/return focus, no trap escape failure |

Gate pass 조건:

1. 375, 768, 1280px에서 각 primitive와 필수 state가 실제 브라우저로 캡처된다.
2. light/dark, reduced-motion, prefers-contrast 또는 forced-colors, 200% zoom을 검사한다.
3. keyboard-only로 모든 동작을 완료하고 focus가 항상 보인다.
4. 상태는 색 없이 구분되며 차트에는 table 대체물이 있다.
5. raw hex/font-size/spacing이 구현 파일에 남지 않고 이 문서 토큰으로 추적된다.
6. 빈 값, 긴 label, 공백 없는 문자열에서 primary horizontal scroll이 없다.
7. `/visual-qa`의 dual-oracle이 fresh evidence로 통과하기 전에는 product screen 구현을 완료로 표시하지 않는다.

예상 증거 위치:

```text
.omo/evidence/phase4-ui/showcase/375.png
.omo/evidence/phase4-ui/showcase/768.png
.omo/evidence/phase4-ui/showcase/1280.png
.omo/evidence/phase4-ui/showcase/states.json
.omo/evidence/phase4-ui/showcase/keyboard.txt
.omo/evidence/phase4-ui/showcase/contrast.json
.omo/evidence/phase4-ui/showcase/visual-qa.md
```

## 6. Charts, Motion & Interaction

### 6.1 Approved chart grammar

| Question | Chart | Contract |
|---|---|---|
| 언급량이 언제 변했나? | time-series line | 현재 기간 solid, 비교 기간 dashed, 6개 이하 series, 결측 구간은 선을 끊는다 |
| 어느 커뮤니티/키워드가 큰가? | sorted horizontal bar | 내림차순, 막대 끝 value label, 15개 초과는 table/search |
| 감성 비율은 어떤가? | 100% horizontal stacked bar + counts | positive/neutral/negative text와 pattern/shape를 함께 쓰고 분석 coverage 표시 |
| 소스가 최신인가? | status timeline/list | 상태 icon+text+timestamp. 장식용 gauge나 donut로 바꾸지 않는다 |
| 이상 급증은 어디인가? | line with explicit markers | marker shape + text annotation + adjacent anomaly summary list |

금지:

- 3D chart, 장식 gradient fill, >5 category pie/donut, word cloud 단독 사용, 근거 없는 gauge.
- red/green만으로 series 구분, hover-only exact value, 분리된 범례, 회전된 모바일 축 label.
- missing/pending 값을 0으로 연결하거나 partial 데이터의 coverage를 숨기는 것.

모든 chart는 다음을 가진다:

- 제목, 한 문장 핵심 insight, 기준 기간/단위, 가까운 범례, exact tooltip/tap label.
- 키보드 접근 가능한 series toggle 또는 동일한 visible control.
- `데이터 표 보기`, screen-reader용 요약, locale-aware 숫자/날짜.
- loading, empty, pending, blocked, partial, error 상태.
- 1,000 point 이상 집계/downsample, 10,000 point 이상 interval aggregation. 시각적 집계 사실을 표기한다.

### 6.2 Motion tokens

| Token | Duration | Easing | Use |
|---|---:|---|---|
| `--motion-none` | `0ms` | none | reduced-motion, 즉시 상태 변경 |
| `--motion-press` | `100ms` | `ease-out` | button press feedback |
| `--motion-fast` | `140ms` | `ease-out` | hover/focus layer |
| `--motion-standard` | `180ms` | `cubic-bezier(.2,.8,.2,1)` | popover, disclosure, filter state |
| `--motion-overlay-in` | `220ms` | `cubic-bezier(.16,1,.3,1)` | dialog/palette enter |
| `--motion-overlay-out` | `140ms` | `ease-in` | dialog/palette exit |

### 6.3 Motion rules

- 의미: 무엇이 바뀌었는지, 다음에 볼 곳이 어디인지, 요소 관계가 무엇인지 중 하나를 설명해야 한다.
- `transform`, `opacity`, `filter`만 animate한다. width, height, top, left, margin, padding을 animate하지 않는다.
- 자동 갱신으로 행 순서가 바뀌면 사용자가 보고 있던 위치를 유지하고 `새 데이터 7건` control로 명시적 반영을 요청한다.
- chart update는 180ms crossfade이며 line-drawing show를 하지 않는다. 결측/partial band는 즉시 읽혀야 한다.
- 무한 pulse, marquee, parallax, scroll hijack, decorative shimmer는 금지한다. skeleton shimmer가 필요하면 reduced-motion에서 정지 gradient로 바꾼다.
- `prefers-reduced-motion: reduce`에서는 모든 비필수 motion을 0ms로 하고 overlay는 즉시 표시한다.
- 상태 발표는 motion이 아니라 text와 live region이 담당한다.

## 7. Depth & Surface

전략은 **tonal-shift + sparse borders + overlay-only shadow**다.

| Level | Treatment | Use |
|---|---|---|
| Canvas | `--surface-canvas` | 앱 배경 |
| Nav | `--surface-nav` + 경계 1px | 고정 내비게이션 |
| Base | `--surface-base` + `--border-subtle` | 일반 panel |
| Raised | `--surface-raised` + `--border-default` | menu, active evidence item |
| Overlay | raised + 12px radius + tinted shadow | dialog, command palette only |

Overlay shadow:

```text
0 18px 48px rgb(18 15 26 / 42%),
0 0 0 1px rgb(196 190 246 / 12%),
inset 0 1px 0 rgb(248 246 250 / 8%)
```

Button press surface:

```text
inset 0 1px 0 rgb(255 255 255 / 12%),
inset 0 -1px 0 rgb(18 15 26 / 30%)
```

규칙:

- 일반 카드와 차트에 box shadow를 쓰지 않는다.
- backdrop blur는 modal scrim 뒤 command palette에만 최대 12px로 허용하며 불투명 fallback을 제공한다.
- surface마다 radius를 임의로 바꾸지 않는다. panel 8px, control 6px, overlay 12px다.
- active evidence rail의 ambient edge는 한 화면에서 한 곳만 허용한다.

## 8. Accessibility Constraints, Debt & Handoff

### 8.1 Binding accessibility constraints

- 목표는 WCAG 2.2 AA다. 주요 본문은 가능하면 AAA 7:1을 목표로 한다.
- skip link, landmarks, 논리적 heading, route마다 유일한 H1을 제공한다.
- semantic HTML이 충분하면 ARIA를 추가하지 않는다. custom control은 검증된 WAI-ARIA pattern만 사용한다.
- 모든 기능은 keyboard로 가능하며 Tab 순서는 DOM/시각 순서와 일치한다. Escape로 overlay를 닫고 trigger로 focus가 돌아간다.
- focus indicator는 2px + 2px offset, 인접색 대비 3:1 이상이며 clipping되지 않는다.
- interactive target은 44x44px 이상, 인접 target 간 8px 이상이다.
- 200% zoom과 text spacing override에서도 정보/동작 손실, 겹침, primary horizontal scroll이 없어야 한다.
- forced-colors/high contrast에서 border와 status가 사라지지 않으며 system color fallback을 둔다.
- `prefers-reduced-motion`, `prefers-color-scheme`, `prefers-contrast`, 가능한 경우 `prefers-reduced-transparency`를 존중한다.
- chart는 text summary와 table을 제공한다. 상태는 color-only가 아니다.
- dynamic update는 사용자의 읽기/scroll을 방해하지 않으며 live region 빈도를 제한한다.
- 오류 문구는 `[무엇이 일어났는가] + [다음에 무엇을 할 수 있는가]` 형식이다.
- 외부 링크는 목적을 말하는 visible label을 쓰며 새 창 여부를 알린다.
- 제품 이미지 concept은 실제 UI에 넣지 않는다. 장식 이미지가 생기면 빈 alt, 의미 이미지라면 전달 정보 중심 alt를 쓴다.

### 8.2 Cognitive and content constraints

- 항상 `현재 위치 / 갈 수 있는 곳 / 이전 조건`을 알 수 있게 page title, active nav, 보존된 filter summary를 제공한다.
- 화면당 primary action은 하나다. 재시도는 원인이 해소되고 idempotent할 때만 강조한다.
- 안전한 기본값을 제공하고 고급 필터는 progressive disclosure로 숨긴다.
- 필터/scroll 위치는 뒤로 가기 후 보존한다.
- 오류가 나도 입력과 filter를 보존한다. 파괴적 행동은 확인하고 가능한 경우 undo를 제공한다.
- 자동 갱신은 갑자기 내용을 밀어내지 않는다. 복귀를 돕는 `새 데이터` 안내를 사용한다.

### 8.3 Design debt register

현재 **사용자에게 승인받은 accepted design/accessibility debt는 없다.** Critical/Major 접근성 문제는 debt로 미루지 않고 구현을 차단한다.

| ID | Source | Severity | What / affected users | Suggested fix | Status | Acceptance |
|---|---|---|---|---|---|---|
| 없음 | - | - | 구현 전 계약 단계에서 승인된 부채 없음 | - | - | - |

새 debt 형식:

```text
ID, date, source, severity, exact issue, affected persona/users,
location, suggested fix, owner, status, rationale, user acknowledgement
```

- 허용 status는 Open, Resolved, Accepted, Escalated다.
- accessibility debt의 Accepted는 사용자가 영향받는 사람과 remediation을 확인하고 명시적으로 승인한 경우에만 가능하다.
- 3회 이상 열린 항목, 같은 persona에 누적된 항목, 사용자 불만으로 확인된 항목은 Escalated로 올린다.

### 8.4 Pending validation, not accepted debt

- Noto Sans KR 실제 font file, weight, Hangul line break는 구현 브라우저에서 검증해야 한다.
- 실제 API의 null/partial/error payload를 연결한 시각 검증은 UI 구현 전이라 아직 실행할 수 없다.
- generated concept의 텍스트 정확성은 구현 근거가 아니다. 구현 카피는 이 문서와 API schema가 소유한다.
- 위 항목은 성공으로 표시하지 않는다. 구현/QA phase의 pending work이며 accepted debt가 아니다.

### 8.5 Implementation handoff

구현 순서:

1. semantic tokens와 light/dark/high-contrast mapping을 만든다.
2. Noto Sans KR + IBM Plex Mono를 runtime 외부 요청 없이 로드한다.
3. AppShell, controls, status, evidence rail, filter, panel, chart frame, data list primitives를 만든다.
4. `/__design-system` showcase에서 Section 5.14 gate를 먼저 통과한다.
5. 개요 -> 게시글 -> 일일 보고서 -> 운영 상태 순으로 product screen을 조합한다.
6. 실제 API 상태 success/pending/blocked/partial/error/unknown을 fixture가 아닌 test contract로 연결한다.
7. production build를 real Chrome으로 375/768/1280에서 검사하고 `/visual-qa`를 실행한다.
8. keyboard, screen reader, 200% zoom, reduced-motion, forced-colors, light/dark를 검사한다.
9. Lighthouse는 production build, real Chrome, mobile/desktop 각각 3-5회 median으로 전 범주 100을 요구한다. UX를 줄여 점수를 만들지 않는다.
10. significant implementation은 objective visual evidence와 persona walkthrough를 첨부해 `/review-work`로 닫는다.

구현자 규칙:

- import 전 `package.json`을 확인한다. Phosphor, chart library, dev tooling이 없으면 임의로 있다고 가정하지 않는다.
- raw hex, 임의 font-size, 임의 spacing, 임의 duration을 product code에 넣지 않는다.
- React client component는 filter, overlay, chart interaction 같은 실제 상호작용 leaf에만 둔다.
- product route보다 showcase가 먼저다. showcase screenshot만으로 product fidelity를 주장하지 않는다.
- mock/generated data로 완료 처리하지 않는다. 데이터가 없으면 설계된 empty/pending/blocked 상태를 렌더한다.
- 상태를 단순 boolean `ok`로 줄이지 않는다. 5개 outcome과 unknown, freshness를 별도 축으로 보존한다.

### 8.6 Verification matrix for the next owner

| Scenario | Invocation | Binary observable | Required artifact |
|---|---|---|---|
| Primitive fidelity | `/visual-qa` on `/__design-system` at 375/768/1280 | 모든 primitive/state, overflow 0, dual-oracle pass | `.omo/evidence/phase4-ui/showcase/` |
| Outcome truth | E2E/API fixtures for 5 outcomes + unknown | 각 상태어, icon, 원인, 시각, action이 정확하고 success 오표시 0 | `.omo/evidence/phase4-ui/status-semantics/` |
| Keyboard | Playwright keyboard-only walkthrough | trap 0, invisible focus 0, 모든 primary task 완료 | `.omo/evidence/phase4-ui/keyboard/` |
| Responsive | real Chrome 375/768/1280 + 200% zoom | primary horizontal scroll 0, clipped controls 0 | `.omo/evidence/phase4-ui/responsive/` |
| Charts | chart/table parity checks | chart value와 table value 일치, unknown-to-zero 변환 0 | `.omo/evidence/phase4-ui/charts/` |
| Accessibility | axe + manual screen reader/high contrast | Critical/Major 0, 상태 발표/heading/landmark 통과 | `.omo/evidence/phase4-ui/accessibility/` |
| Performance | production real-Chrome Lighthouse mobile/desktop 3-5 runs | median 100/100/100/100 양쪽 | `.omo/evidence/phase4-ui/lighthouse/` |

### 8.7 DoneClaim rule

이 문서는 구현 방향에 대해 **decision-complete**다. 구현자는 시각 방향, token, 상태 의미, responsive layout, chart 문법, primitive, accessibility를 새로 발명하지 않는다. 실제 화면의 완료는 Section 5.14와 8.6의 fresh artifact가 모두 존재하고, 열린 Critical/Major 접근성 또는 persona blocker가 0일 때만 주장할 수 있다.
