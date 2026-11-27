import { useState, FormEvent } from 'react'
import { ShieldCheck, LockKeyhole } from 'lucide-react'
import './working-core.css'
import { authApi } from '@/api/client'
import { useT } from '@/hooks/useT'

export function Login() {
  const translate = useT()
  const t = (english: string, russian: string) => import.meta.env.VITE_WORKING_CORE === '1' ? russian : translate(english, russian)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const data = await authApi.login({ username, password })
      localStorage.setItem('pitun_token', data.access_token)
      window.location.href = '/'
    } catch {
      setError(t('Invalid username or password', 'Неверное имя пользователя или пароль'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="wc-login"><div className="wc-login-brand"><span className="wc-logo"><ShieldCheck size={21}/></span><strong>TEMPEST</strong><span> / VPN GATEWAY</span></div>
      <form onSubmit={handleSubmit} className="wc-login-card space-y-5">
        <div className="wc-login-intro"><span className="wc-login-icon"><LockKeyhole size={21}/></span>
          <h1>{t('Welcome back', 'Вход в TEMPEST')}</h1>
          <p>{t('Sign in to manage your gateway', 'Войдите в панель управления шлюзом')}</p>
        </div>

        {error && (
          <div className="rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 px-4 py-2.5 text-sm text-red-700 dark:text-red-300">
            {error}
          </div>
        )}

        <div className="space-y-3">
          <div>
            <label className="block text-sm text-gray-300 mb-1.5">{t('Username', 'Имя пользователя')}</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
              autoFocus
              required
            />
          </div>
          <div>
            <label className="block text-sm text-gray-300 mb-1.5">{t('Password', 'Пароль')}</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
              required
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
        >
          {loading ? t('Signing in\u2026', '\u0412\u0445\u043e\u0434\u2026') : t('Sign In', '\u0412\u043e\u0439\u0442\u0438')}
        </button>

        {import.meta.env.VITE_WORKING_CORE !== '1' && <p className="text-center text-xs text-gray-600">
          {t('Default: admin / password', '\u041f\u043e \u0443\u043c\u043e\u043b\u0447\u0430\u043d\u0438\u044e: admin / password')}
        </p>}
      </form>
    </div>
  )
}
