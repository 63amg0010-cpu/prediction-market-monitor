import { createServer } from "node:http"

const args = new Map(process.argv.slice(2).map((value) => value.split("=", 2)))
const port = Number(args.get("--port") ?? "4178")
const mode = args.get("--mode") ?? "clean"

function shell(body) {
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>stub</title></head><body>${body}</body></html>`
}

function posts(url) {
  const search = url.searchParams.get("search") ?? ""
  return shell(`
    <h1>최근 게시글</h1>
    <button type="button" aria-expanded="false">필터 열기</button>
    <ul aria-label="적용 중인 필터"><li>${search ? `검색: ${search}` : "기본 조건"}</li></ul>
    <form action="/posts" method="get" aria-label="대시보드 필터">
      <label>분류 키워드<input name="keyword" placeholder="예: 예측시장, 폴리마켓, 확률"></label>
      <label>글 검색<input name="search" placeholder="예: 금리 인하, election odds"></label>
      <label>기간<select name="period"><option selected value="30d">30일</option></select></label>
      <button type="submit">적용</button>
    </form>
    <p><strong>최신 원문</strong><span>2026. 07. 28. 18:00 KST</span></p>
    <ul aria-label="소스별 최근 수집"><li>Manifold Markets · 최신</li></ul>
    <ul><li><div class="post-main"><strong>예측 시장 최신 댓글</strong></div><a href="https://example.com/original" target="_blank">원문 열기</a></li></ul>
    <nav aria-label="게시글 페이지 이동"><span aria-disabled="true">이전 페이지</span><strong>1/1 페이지</strong><span aria-disabled="true">다음 페이지</span></nav>
    <script>
      ${mode === "storage" ? 'localStorage.setItem("auth", "persisted")' : ""}
      ${mode === "console" ? 'console.error("stub console failure")' : ""}
      ${mode === "write" ? 'fetch("/api/write-probe", {method:"POST"})' : ""}
    </script>
  `)
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", `http://127.0.0.1:${port}`)
  response.setHeader("content-type", "text/html; charset=utf-8")
  if (url.pathname === "/login" && request.method === "GET") {
    response.end(
      shell(
        '<form action="/api/auth/login" method="post"><label>관리자 비밀번호<input type="password"></label><button>대시보드 열기</button></form>',
      ),
    )
    return
  }
  if (url.pathname === "/api/auth/login" && request.method === "POST") {
    request.resume()
    response.statusCode = 302
    response.setHeader("location", "/")
    response.setHeader("set-cookie", "admin_session=opaque; HttpOnly; SameSite=Strict")
    response.end()
    return
  }
  if (url.pathname === "/" && request.method === "GET") {
    response.end(
      shell(
        `<h1>커뮤니티 반응 개요</h1><a href="/posts">게시글</a><script>${mode === "storage" ? 'localStorage.setItem("auth", "persisted")' : ""}</script>`,
      ),
    )
    return
  }
  if (url.pathname === "/posts" && request.method === "GET") {
    response.end(posts(url))
    return
  }
  response.statusCode = request.method === "POST" ? 204 : 404
  response.end()
})

server.listen(port, "127.0.0.1", () => {
  process.stdout.write(`READY ${port} ${mode}\n`)
})
