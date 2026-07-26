import { LockKey } from "@phosphor-icons/react/ssr"

import { LoginForm } from "../../components/login-form"

function LoginPage() {
  return (
    <main className="login-page">
      <section aria-labelledby="login-title" className="login-card">
        <div className="login-mark" aria-hidden>
          <LockKey size={28} />
        </div>
        <p className="eyebrow">SINGLE ADMIN</p>
        <h1 id="login-title">반응 관제실 로그인</h1>
        <p>수집과 분석 근거를 확인할 수 있는 개인 관리자 화면입니다.</p>
        <LoginForm />
        <small>비밀번호와 세션 토큰은 브라우저 저장소나 화면 응답에 남지 않습니다.</small>
      </section>
    </main>
  )
}

export { LoginPage as default }
