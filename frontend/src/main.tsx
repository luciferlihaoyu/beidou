import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import './index.css'
import App from './App.tsx'

// 天宫 SSO 跳转消费点：后端 /sso/launch 校验通过后 302 至 /?sso_token=<北斗本地token>，
// 此处将 token 落地到 localStorage（beidou_token），并立即从地址栏清除 query，防 token 残留。
const ssoToken = new URLSearchParams(window.location.search).get('sso_token')
if (ssoToken) {
  localStorage.setItem('beidou_token', ssoToken)
  window.history.replaceState({}, document.title, window.location.pathname)
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
