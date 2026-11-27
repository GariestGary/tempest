import { useEffect, useState, type FormEvent } from 'react'
import { Activity, Boxes, Cable, ChevronLeft, CircleUserRound, LayoutDashboard, LogOut, Menu, Route, Settings2, ShieldCheck, UsersRound } from 'lucide-react'
import './working-core.css'
import { useQuery } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { authApi, http } from '@/api/client'

type Action = 'direct' | 'vpn' | 'block'
type Rule = { destination: string; port: number; egress: Action }
type Agent = {
  active_generation: number
  last_known_good_generation: number
  pending_generation_id: string | null
  last_error: string | null
  active_egress_id?: string | null
  egress_state?: 'active_unverified' | 'active_verified' | 'unavailable' | null
  egress_checked_epoch_ms?: number | null
}
type VlessSecurity = 'tls' | 'reality'
type Provider = { id: string; name: string }
type Gateway = {
  revision: number
  providers: Provider[]
  saved_egresses: { id: string; name: string; provider_id: string | null; security?: VlessSecurity }[]
  saved_awg_egresses: { id: string; name: string; provider_id: string | null }[]
  selected_egress_id: string | null
  vless: { address: string; port: number; server_name: string; security?: VlessSecurity } | null
  clients: { id: string; name: string; addresses: string[] }[]
  rules: Rule[]
  default_egress: 'block' | 'vpn'
  resolvers: string[]
  agent: Agent | null
  agent_error: string | null
}
type DraftDiff = { revision: number; base_generation: number; sections: string[] }

const actions: Record<Action, string> = { direct: 'DIRECT', vpn: 'VPN', block: 'BLOCK' }
const sections: Record<string, string> = {
  wireguard: 'WireGuard', vless: 'VPN-выход', dns: 'DNS', 'destination-policy': 'Правила маршрутизации',
}
const panel = 'rounded-xl border border-gray-800 bg-gray-900 p-4 sm:p-6 space-y-4'
const field = 'w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-gray-100'
const primary = 'rounded-md bg-brand-600 px-4 py-2 font-medium text-white hover:bg-brand-500 disabled:opacity-50'
const secondary = 'rounded-md border border-gray-700 px-3 py-2 hover:bg-gray-800 disabled:opacity-50'

function value(form: FormData, key: string) {
  return String(form.get(key) ?? '').trim()
}

function securityLabel(security?: VlessSecurity) {
  return security === 'reality' ? 'Reality' : 'TLS'
}

function manualVless(body: FormData) {
  const upstream = {
    address: value(body, 'address'),
    port: Number(value(body, 'port')),
    server_name: value(body, 'server_name'),
    client_id: value(body, 'client_id'),
  }
  if (value(body, 'security') !== 'reality') {
    return { ...upstream, certificate_sha256: value(body, 'pin') || null }
  }
  return {
    ...upstream,
    reality: {
      public_key: value(body, 'public_key'),
      short_id: value(body, 'short_id'),
      fingerprint: value(body, 'fingerprint'),
      flow: value(body, 'flow'),
    },
  }
}

function ManualVlessFields() {
  const [security, setSecurity] = useState<VlessSecurity>('tls')
  return <div className="grid gap-3 sm:grid-cols-2">
    <label>Защита VLESS<select name="security" className={field} value={security}
      onChange={event => setSecurity(event.target.value === 'reality' ? 'reality' : 'tls')}>
      <option value="tls">TCP/TLS</option>
      <option value="reality">TCP/Reality</option>
    </select></label>
    <label>IP выхода<input name="address" className={field} required /></label>
    <label>Порт<input name="port" className={field} type="number" defaultValue={443} min={1} max={65535} required /></label>
    <label>Имя сервера (SNI)<input name="server_name" className={field} maxLength={253} required /></label>
    <label>UUID клиента<input name="client_id" className={field} type="password" autoComplete="off" required /></label>
    {security === 'reality' ? <>
      <label>Публичный ключ Reality<input name="public_key" className={field} type="password" autoComplete="off"
        pattern="[A-Za-z0-9_\-]{43}" minLength={43} maxLength={43} required /></label>
      <label>Short ID (если задан)<input name="short_id" className={field} type="password" autoComplete="off"
        pattern="(?:[A-Fa-f0-9]{2}){0,8}" maxLength={16} /></label>
      <label>Fingerprint<select name="fingerprint" className={field} defaultValue="chrome">
        {['chrome', 'firefox', 'safari', 'ios', 'edge'].map(name => <option key={name}>{name}</option>)}
      </select></label>
      <label>Flow<select name="flow" className={field} defaultValue="">
        <option value="">Без flow</option>
        <option value="xtls-rprx-vision">xtls-rprx-vision</option>
      </select></label>
    </> : <label>SHA-256 сертификата (необязательно)<input name="pin" className={field} maxLength={64} /></label>}
  </div>
}

function errorMessage(error: unknown) {
  if (isAxiosError(error) && typeof error.response?.data?.detail === 'string') {
    return error.response.data.detail
  }
  return 'Не удалось выполнить запрос. Обновите статус перед повтором.'
}

const importStatuses = {
  ready: 'Будет добавлен',
  duplicate: 'Дубликат',
  unsupported_protocol: 'Другой протокол или формат',
  incompatible_vless: 'Несовместимый VLESS TCP/TLS или отсутствует IP домена',
  incompatible_reality: 'Проверьте TCP, IP, SNI, ключ и параметры Reality',
  deployment_conflict: 'Конфликт с параметрами Working Core',
  catalog_full: 'Нет места в каталоге',
}

type SubscriptionPreview = {
  preview_id: string | null
  revision: number
  results: { line: number; status: keyof typeof importStatuses; name?: string; security?: VlessSecurity }[]
}

function SubscriptionImport({ providers, revision, busy, run }: {
  providers: Provider[]
  revision: number
  busy: boolean
  run: (operation: () => Promise<string>) => Promise<void>
}) {
  const [mode, setMode] = useState<'content' | 'url'>('content')
  const [preview, setPreview] = useState<(SubscriptionPreview & { providerId: string }) | null>(null)
  const ready = preview?.results.filter(item => item.status === 'ready').length ?? 0

  function showPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const body = new FormData(form)
    const providerId = value(body, 'subscriptionProvider')
    setPreview(null)
    void run(async () => {
      const dialIps = Object.fromEntries(value(body, 'dialIps').split(/\r?\n/).filter(line => line.trim()).map(line => {
        const [host, address, ...extra] = line.trim().split(/\s+/)
        if (!address || extra.length) throw new Error('invalid dial IP mapping')
        return [host, address]
      }))
      const result = (await http.post<SubscriptionPreview>(
        `/core/providers/${providerId}/subscription/preview`,
        { revision, [mode]: value(body, 'subscriptionSource'), dial_ips: dialIps },
      )).data
      const source = form.elements.namedItem('subscriptionSource') as HTMLInputElement | HTMLTextAreaElement
      source.value = ''
      setPreview({ ...result, providerId })
      return ''
    })
  }

  return <details className="rounded-lg border border-gray-800 p-3">
    <summary className="cursor-pointer font-medium">Импорт подписки VLESS</summary>
    <form className="mt-3" onSubmit={showPreview} onChange={() => setPreview(null)}>
      <fieldset className="grid gap-3 sm:grid-cols-2" disabled={busy}>
        <label>Провайдер для импорта<select name="subscriptionProvider" className={field} required defaultValue="">
          <option value="" disabled>Выберите провайдера</option>
          {providers.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
        </select></label>
        <label>Источник<select className={field} value={mode} onChange={event => setMode(event.target.value === 'url' ? 'url' : 'content')}>
          <option value="content">Текст или Base64</option>
          <option value="url">HTTPS-ссылка</option>
        </select></label>
        {mode === 'content'
          ? <label className="sm:col-span-2">Содержимое подписки<textarea name="subscriptionSource" className={field} required rows={4}
              maxLength={24576} autoComplete="off" spellCheck={false} placeholder="Ссылки VLESS по одной на строку или список в Base64" /></label>
          : <label className="sm:col-span-2">HTTPS-ссылка подписки<input name="subscriptionSource" className={field} required type="password"
              maxLength={4096} autoComplete="off" /></label>}
        <label className="sm:col-span-2">IP для доменов выходов (если нужны)
          <textarea name="dialIps" className={field} rows={2} maxLength={8192} spellCheck={false} placeholder="vpn.example.org 192.0.2.20" />
          <span className="text-sm text-gray-400">Один домен и один IP через пробел на строку. Указывайте домен из адреса VLESS, если вместо IP в ссылке указан домен.</span>
        </label>
        <button className={secondary}>Предпросмотр подписки</button>
      </fieldset>
    </form>
    {preview && <div className="mt-3 space-y-2">
      <p role="status">К добавлению: {ready}. Пропущено: {preview.results.length - ready}.</p>
      <ul className="max-h-56 overflow-y-auto text-sm text-gray-300">
        {preview.results.map(item => <li key={item.line}>
          Строка {item.line} · {importStatuses[item.status]}{item.name && ` · ${item.name} · ${securityLabel(item.security)}`}
        </li>)}
      </ul>
      {revision !== preview.revision && <p className="text-sm text-amber-400">Черновик изменился. Сделайте предпросмотр ещё раз.</p>}
      {preview.preview_id && <button className={primary} disabled={busy || revision !== preview.revision} onClick={() => void run(async () => {
        const response = await http.post<{ added: number }>(
          `/core/providers/${preview.providerId}/subscription/commit`,
          { revision: preview.revision, preview_id: preview.preview_id },
        )
        setPreview(null)
        return `Выходов добавлено в каталог: ${response.data.added}.`
      })}>Добавить выходы в каталог</button>}
    </div>}
  </details>
}

export function WorkingCore() {
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ['working-core'],
    queryFn: () => http.get<Gateway>('/core').then(response => response.data),
    retry: false,
  })
  const [page, setPage] = useState('overview')
  const [collapsed, setCollapsed] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [importing, setImporting] = useState(true)
  const [savedManual, setSavedManual] = useState(false)
  const [diff, setDiff] = useState<DraftDiff | null>(null)
  const [changePassword, setChangePassword] = useState(false)
  const [awgPreview, setAwgPreview] = useState<{ ipv6_configured: boolean } | null>(null)

  useEffect(() => {
    const closeMenu = (event: KeyboardEvent) => { if (event.key === 'Escape') setMobileNav(false) }
    window.addEventListener('keydown', closeMenu)
    return () => window.removeEventListener('keydown', closeMenu)
  }, [])

  async function run(operation: () => Promise<string>) {
    setBusy(true)
    setError('')
    setNotice('')
    setDiff(null)
    try {
      setNotice(await operation())
    } catch (error) {
      setError(errorMessage(error))
    } finally {
      await refetch()
      setBusy(false)
    }
  }

  function saveEgress(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const body = new FormData(form)
    void run(async () => {
      if (importing) {
        await http.post('/core/egress/import', { uri: value(body, 'uri'), dial_address: value(body, 'address') || null })
      } else {
        await http.put('/core/egress', manualVless(body))
      }
      form.reset()
      return 'Выход сохранён в черновике.'
    })
  }

  function createClient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const name = value(new FormData(form), 'name')
    void run(async () => {
      const response = await http.post<Blob>('/core/clients', { name }, { responseType: 'blob' }).catch(async error => {
        if (isAxiosError(error) && error.response?.data instanceof Blob) {
          error.response.data = JSON.parse(await error.response.data.text())
        }
        throw error
      })
      const filename = String(response.headers['content-disposition']).match(/tempest-[a-f0-9]{6}\.conf/)
      const url = URL.createObjectURL(response.data)
      const link = document.createElement('a')
      link.href = url
      link.download = filename?.[0] ?? 'tempest.conf'
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      form.reset()
      return 'Клиент добавлен в черновик. Сохраните скачанный .conf в защищённом месте.'
    })
  }

  function addSavedEgress(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!data) return
    const form = event.currentTarget
    const body = new FormData(form)
    void run(async () => {
      await http.post('/core/egresses', {
        revision: data.revision, name: value(body, 'name'),
        provider_id: value(body, 'provider') || null,
        ...(savedManual
          ? { config: manualVless(body) }
          : { uri: { uri: value(body, 'uri'), dial_address: value(body, 'address') || null } }),
      })
      form.reset()
      return 'Выход сохранён. Выберите его и примените изменения.'
    })
  }

  function saveRules(rules: Rule[], defaultEgress?: 'block' | 'vpn') {
    if (!data) return
    void run(async () => {
      await http.put('/core/rules', { revision: data.revision, rules, ...(defaultEgress ? { default_egress: defaultEgress } : {}) })
      return 'Правила сохранены в черновике.'
    })
  }

  function addRule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!data) return
    const input = new FormData(event.currentTarget)
    saveRules([...data.rules, {
      destination: value(input, 'destination'),
      port: Number(value(input, 'port')),
      egress: value(input, 'egress') as Action,
    }])
  }

  const agent = data?.agent
  const pending = agent?.pending_generation_id
  const hasVpn = Boolean(data?.vless || data?.saved_awg_egresses?.some(item => item.id === data.selected_egress_id))
  return (
    <div className={`wc-shell ${collapsed ? 'is-collapsed' : ''}`} data-page={page}>
      <aside className={`wc-sidebar ${mobileNav ? 'is-open' : ''}`}>
        <div className="wc-brand"><span className="wc-logo"><ShieldCheck size={20} /></span><span className="wc-brand-text"><strong>TEMPEST</strong><small>VPN GATEWAY</small></span></div>
        <div className="wc-nav-label">УПРАВЛЕНИЕ</div>
        <nav aria-label="Основная навигация" className="wc-nav">
          {([
            ['overview', 'Обзор', LayoutDashboard], ['providers', 'Провайдеры', Boxes],
            ['egress', 'Выходы', Cable], ['routing', 'Маршрутизация', Route],
            ['clients', 'Клиенты', UsersRound], ['settings', 'Настройки', Settings2],
          ] as const).map(([id, label, Icon]) => <button key={id as string} type="button" title={label as string}
            aria-current={page === id ? 'page' : undefined} onClick={() => { setPage(id as string); setSearch(''); setMobileNav(false); document.getElementById('main-content')?.scrollTo(0, 0) }}>
            <Icon size={19} /><span>{label as string}</span>
          </button>)}
        </nav>
        <div className="wc-sidebar-bottom"><span className="wc-sidebar-caption">SELF-HOSTED GATEWAY</span><button type="button" className="wc-collapse" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? 'Развернуть меню' : 'Свернуть меню'}><ChevronLeft size={18} /></button></div>
      </aside>
      {mobileNav && <button className="wc-backdrop" aria-label="Закрыть меню" onClick={() => setMobileNav(false)} />}
      <div className="wc-workspace">
        <header className="wc-topbar">
          <button className="wc-menu" onClick={() => setMobileNav(!mobileNav)} aria-label="Открыть меню"><Menu size={20} /></button>
          <div className="wc-breadcrumb"><span>TEMPEST</span><span className="wc-slash">/</span><strong>{({ overview: 'Обзор', providers: 'Провайдеры', egress: 'Выходы', routing: 'Маршрутизация', clients: 'Клиенты', settings: 'Настройки' } as Record<string, string>)[page]}</strong></div>
          <div className="wc-top-actions"><span className={`wc-agent ${agent ? 'online' : ''}`}><i />{agent ? 'Agent подключён' : 'Agent недоступен'}</span><button type="button" className="wc-account" onClick={() => setPage('settings')} aria-label="Настройки учётной записи"><CircleUserRound size={19}/></button><button type="button" className="wc-account" aria-label="Выйти" onClick={() => { localStorage.removeItem('pitun_token'); window.location.assign('/login') }}><LogOut size={18}/></button></div>
        </header>
        <main className="wc-main" id="main-content">
          <div className="wc-content">
            <div className="wc-page-heading"><div><div className="wc-eyebrow"><Activity size={14}/> WORKING CORE</div><h1>{({ overview: 'Обзор системы', providers: 'Провайдеры', egress: 'Исходящие подключения', routing: 'Маршрутизация', clients: 'Клиенты WireGuard', settings: 'Настройки' } as Record<string, string>)[page]}</h1><p>{({ overview: 'Состояние шлюза и управление конфигурацией', providers: 'Организуйте подключения по провайдерам', egress: 'Управляйте каталогом и выбором VPN-выхода', routing: 'Определите, куда направлять трафик', clients: 'Управляйте доступом ваших устройств', settings: 'Учётная запись и доступ к панели' } as Record<string, string>)[page]}</p></div></div>
            <div className="wc-settings-only"><div className="wc-settings-card"><h2>Учётная запись</h2><p>Обновите пароль для входа в панель управления.</p><button className={secondary} onClick={() => setChangePassword(!changePassword)}>Сменить пароль</button></div></div>
        {changePassword && page === 'settings' && <form className={panel} onSubmit={event => {
          event.preventDefault()
          const form = event.currentTarget
          const values = new FormData(form)
          void run(async () => {
            await authApi.changePassword({
              current_password: String(values.get('old')),
              new_password: String(values.get('new')),
            })
            form.reset()
            setChangePassword(false)
            return 'Пароль изменён.'
          })
        }}>
          <fieldset className="grid gap-3 sm:grid-cols-2" disabled={busy}>
            <label>Текущий пароль<input className={field} name="old" type="password" autoComplete="current-password" required /></label>
            <label>Новый пароль<input className={field} name="new" type="password" autoComplete="new-password" minLength={16} maxLength={72} required /></label>
          </fieldset>
          <button className={primary} disabled={busy}>Сохранить пароль</button>
        </form>}
        {notice && <p className="rounded-lg border border-green-800 p-3" role="status">{notice}</p>}
        {error && <p className="rounded-lg border border-red-800 p-3" role="alert">{error}</p>}
        {isPending && <p role="status">Загрузка конфигурации…</p>}
        {isError && <p role="alert">Панель не смогла загрузить конфигурацию. <button className={secondary} onClick={() => void refetch()}>Повторить</button></p>}

        {data && <>
          <section className={`${panel} wc-overview`} aria-labelledby="apply-heading">
            <div className="wc-overview-intro"><span className="wc-overview-icon"><Activity size={20}/></span><div><strong>Рабочая конфигурация</strong><p>Состояние и безопасное применение изменений</p></div><span className="wc-overview-revision">ЧЕРНОВИК · {data.revision}</span></div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="apply-heading" className="text-lg font-semibold">Конфигурация</h2>
              <button className={secondary} disabled={busy} onClick={() => void refetch()}>Обновить статус</button>
            </div>
            <dl className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
              <div><dt className="text-gray-400">Активное поколение</dt><dd>{agent ? agent.active_generation || 'Нет' : 'Неизвестно'}</dd></div>
              <div><dt className="text-gray-400">Последнее подтверждённое (LKG)</dt><dd>{agent ? agent.last_known_good_generation || 'Нет' : 'Неизвестно'}</dd></div>
              <div><dt className="text-gray-400">DNS через VPN</dt><dd>{data.resolvers.join(', ')}</dd></div>
              <div><dt className="text-gray-400">Выход в черновике</dt><dd>{[...(data.saved_awg_egresses ?? []), ...(data.saved_egresses ?? [])].find(item => item.id === data.selected_egress_id)?.name ?? (data.vless ? 'VLESS' : 'Не выбран')}</dd></div>
            </dl>
            {(data.agent_error || agent?.last_error) && <p role="status" className="text-amber-400">{data.agent_error || agent?.last_error}</p>}
            {pending && <button className={secondary} disabled={busy} onClick={() => void run(async () => {
              await http.post(`/core/pending/${pending}/rollback`)
              return 'Ожидающее поколение отменено. Проверьте активное поколение.'
            })}>Отменить ожидающее поколение</button>}
            {!!agent?.last_known_good_generation && !agent.active_generation && !pending &&
              <button className={secondary} disabled={busy} onClick={() => void run(async () => {
                const status = (await http.post<Agent>('/core/recover')).data
                return status.active_generation ? 'Подтверждённое поколение восстановлено.' : 'Ingress закрыт. Проверьте статус Agent.'
              })}>Восстановить LKG</button>}
            <div className="flex flex-wrap gap-2">
              <button className={primary} disabled={busy || !agent || !hasVpn || Boolean(pending)} onClick={() => void run(async () => {
                setDiff((await http.post<DraftDiff>('/core/preview')).data)
                return ''
              })}>Посмотреть изменения</button>
            </div>
            {diff && <div className="space-y-3">
              <p>{diff.sections.length ? diff.sections.map(section => sections[section] ?? section).join(', ') : 'Нет изменений, влияющих на трафик.'}</p>
              <p className="text-sm text-gray-400">При применении WireGuard переподключится. Подтверждение проверяет трафик контрольного peer.</p>
              <button className={primary} disabled={busy || diff.revision !== data.revision || !diff.sections.length} onClick={() => void run(async () => {
                await http.post('/core/apply', { revision: diff.revision, base_generation: diff.base_generation })
                return 'Конфигурация применена. Контрольные пробы WireGuard прошли.'
              })}>Применить конфигурацию</button>
            </div>}
            {busy && <p role="status" className="text-sm text-gray-400">Выполняется запрос…</p>}
          </section>

          <section className={`${panel} wc-catalog`} aria-labelledby="saved-egresses-heading">
            <h2 id="saved-egresses-heading" className="text-lg font-semibold">{page === 'providers' ? 'Ваши провайдеры' : 'Каталог выходов'}</h2>
            <p className="text-sm text-gray-400">{page === 'providers' ? 'Объединяйте связанные VPN-выходы по провайдерам.' : 'Выбранный выход используется правилами VPN после применения. Применение прерывает соединения.'}</p>
            <div className="wc-providers-only"><details className="wc-action"><summary>Добавить провайдера</summary><form onSubmit={event => {
              event.preventDefault()
              const form = event.currentTarget
              const name = value(new FormData(form), 'providerName')
              void run(async () => {
                await http.post('/core/providers', { revision: data.revision, name })
                form.reset()
                return 'Провайдер добавлен.'
              })
            }} className="flex flex-wrap items-end gap-2">
              <label>Название провайдера<input name="providerName" className={field} maxLength={64} required /></label>
              <button className={secondary} disabled={busy}>Добавить провайдера</button>
            </form>
            </details>
            {!!data.providers?.length && <ul className="space-y-2">{data.providers.map(provider => <li key={provider.id} className="flex flex-wrap items-center gap-2">
              <span>{provider.name}</span>
              <button className={secondary} disabled={busy} onClick={() => {
                const name = window.prompt('Новое название провайдера', provider.name)?.trim()
                if (!name) return
                void run(async () => {
                  await http.put(`/core/providers/${provider.id}`, { revision: data.revision, name })
                  return 'Провайдер переименован.'
                })
              }}>Переименовать</button>
              <button className={secondary} disabled={busy || [...data.saved_egresses, ...(data.saved_awg_egresses ?? [])].some(item => item.provider_id === provider.id)} onClick={() => {
                if (!window.confirm(`Удалить провайдера «${provider.name}»?`)) return
                void run(async () => {
                  await http.delete(`/core/providers/${provider.id}`, { data: { revision: data.revision } })
                  return 'Провайдер удалён.'
                })
              }}>Удалить провайдера</button>
            </li>)}</ul>}
            {!data.providers?.length && <p className="wc-empty">Провайдеров пока нет. Добавьте первого, чтобы упорядочить выходы.</p>}</div>
            <div className="wc-egress-only">
            <label className="wc-search">Поиск выходов<input type="search" placeholder="Название выхода" value={search} onChange={event => setSearch(event.target.value)} /></label>
            {!!data.providers?.length && <SubscriptionImport providers={data.providers} revision={data.revision} busy={busy} run={run} />}
            <details className="wc-action"><summary>Импортировать AWG</summary><form onChange={() => setAwgPreview(null)} onSubmit={event => {
              event.preventDefault()
              const form = event.currentTarget
              const body = new FormData(form)
              const file = body.get('awgFile')
              if (!(file instanceof File) || file.size > 24576) return
              void run(async () => {
                const config = await file.text()
                await http.post('/core/awg-egresses', {
                  revision: data.revision, name: value(body, 'awgName'),
                  provider_id: value(body, 'awgProvider') || null, config,
                })
                form.reset()
                setAwgPreview(null)
                return 'AWG-конфигурация сохранена. Выберите выход и просмотрите изменения перед применением.'
              })
            }}>
              <fieldset className="grid gap-3 sm:grid-cols-2" disabled={busy}>
                <legend>Импорт AmneziaWG</legend>
                <label>Название AWG<input name="awgName" className={field} maxLength={64} required /></label>
                <label>Провайдер AWG<select name="awgProvider" className={field} defaultValue=""><option value="">Без провайдера</option>{data.providers?.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select></label>
                <label>Файл AWG .conf<input name="awgFile" className={field} type="file" accept=".conf" required /></label>
                <button type="button" className={secondary} onClick={event => {
                  const form = event.currentTarget.form
                  const file = form && new FormData(form).get('awgFile')
                  setAwgPreview(null)
                  if (!(file instanceof File) || !file.size || file.size > 24576) {
                    setError('Выберите файл AWG размером до 24 КиБ.')
                    return
                  }
                  void run(async () => {
                    const response = await http.post<{ ipv6_configured: boolean }>('/core/awg-egresses/preview', {
                      config: await file.text(),
                    })
                    setAwgPreview(response.data)
                    return 'Структура файла проверена. Совместимость и соединение ещё не проверены.'
                  })
                }}>Проверить файл AWG</button>
                <button className={secondary}>Сохранить AWG</button>
              </fieldset>
              {awgPreview && <p className="text-sm text-gray-400">IPv6 в конфигурации: {awgPreview.ipv6_configured ? 'задан' : 'не задан'}. Проверка не подтверждает работу VPN.</p>}
            </form>
            </details>
            {!!data.saved_awg_egresses?.length && <ul>{data.saved_awg_egresses.filter(item => item.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(item => <li key={item.id} className="flex flex-wrap items-center gap-3 py-2">
              <span>{item.name} · AWG · {data.selected_egress_id === item.id ? 'выбран в черновике' : 'сохранён'}
                {agent?.active_egress_id === item.id && (agent.egress_state === 'unavailable' ? ' · недоступен' : agent.egress_state === 'active_verified' ? ' · последняя проверка прошла' : ' · активен, доступность не проверена')}
                {agent?.active_egress_id === item.id && agent.egress_checked_epoch_ms ? ` · проверка: ${new Date(agent.egress_checked_epoch_ms).toLocaleString('ru-RU')}` : ''}</span>
              {agent?.active_egress_id === item.id && <button className={secondary} disabled={busy || Boolean(pending)} onClick={() => void run(async () => {
                const status = (await http.post<Agent>('/core/awg-egresses/probe')).data
                return status.egress_state === 'unavailable' ? 'Последняя проверка AWG не прошла.' : 'Контрольная проверка AWG прошла.'
              })}>Проверить AWG</button>}
              <button className={secondary} disabled={busy || data.selected_egress_id === item.id} onClick={() => void run(async () => {
                await http.post(`/core/awg-egresses/${item.id}/select`, { revision: data.revision })
                return 'AWG выбран в черновике; требуется просмотр и применение.'
              })}>Выбрать AWG</button>
              <button className={secondary} disabled={busy || data.selected_egress_id === item.id} onClick={() => {
                if (!window.confirm(`Удалить сохранённый выход «${item.name}»?`)) return
                void run(async () => {
                  await http.delete(`/core/awg-egresses/${item.id}`, { data: { revision: data.revision } })
                  return 'AWG-конфигурация удалена.'
                })
              }}>Удалить AWG</button>
            </li>)}</ul>}
            {data.saved_awg_egresses?.some(item => item.id === data.selected_egress_id) &&
              <p className="text-sm text-gray-400">Проверьте доступность DNS вместе с подключением после применения конфигурации.</p>}
            <details className="wc-action"><summary>Добавить VLESS в каталог</summary><form onSubmit={addSavedEgress}>
              <fieldset className="grid gap-3 sm:grid-cols-2" disabled={busy}>
                <label>Название<input name="name" className={field} maxLength={64} required /></label>
                <label>Провайдер<select name="provider" className={field} defaultValue=""><option value="">Без провайдера</option>{data.providers?.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select></label>
                <label>Способ добавления выхода<select className={field} value={savedManual ? 'manual' : 'uri'}
                  onChange={event => setSavedManual(event.target.value === 'manual')}>
                  <option value="uri">Импорт ссылки</option><option value="manual">Ручной ввод</option>
                </select></label>
                {savedManual ? <div className="sm:col-span-2"><ManualVlessFields /></div> : <>
                  <label>IP выхода (если в ссылке домен)<input name="address" className={field} /></label>
                  <label className="sm:col-span-2">Ссылка VLESS TCP/TLS или Reality<input type="password" name="uri"
                    className={field} maxLength={4096} autoComplete="off" required /></label>
                </>}
                <button className={primary}>Сохранить выход</button>
              </fieldset>
            </form>
            </details>
            {!!data.saved_egresses?.length && <ul className="divide-y divide-gray-800">
              {data.saved_egresses.filter(item => item.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(item => <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <span>{item.name} · {securityLabel(item.security)}{item.id === data.selected_egress_id ? ' · выбран в черновике' : ''}</span>
                <label>Провайдер выхода «{item.name}»<select className={field} value={item.provider_id ?? ''} disabled={busy} onChange={event => {
                  const provider_id = event.target.value || null
                  void run(async () => {
                    await http.put(`/core/egresses/${item.id}/provider`, { revision: data.revision, provider_id })
                    return 'Провайдер выхода изменён.'
                  })
                }}><option value="">Без провайдера</option>{data.providers?.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select></label>
                <div className="flex gap-2">
                  <button className={secondary} disabled={busy || item.id === data.selected_egress_id} onClick={() => void run(async () => {
                    await http.post(`/core/egresses/${item.id}/select`, { revision: data.revision })
                    return 'Выход выбран в черновике. Проверьте и примените изменения.'
                  })}>Выбрать</button>
                  <button className={secondary} disabled={busy || item.id === data.selected_egress_id} onClick={() => {
                    if (!window.confirm(`Удалить сохранённый выход «${item.name}»?`)) return
                    void run(async () => {
                      await http.delete(`/core/egresses/${item.id}`, { data: { revision: data.revision } })
                      return 'Выход удалён.'
                    })
                  }}>Удалить</button>
                </div>
              </li>)}
            </ul>}
            {!data.saved_egresses?.length && !data.saved_awg_egresses?.length && <p className="wc-empty">Выходов пока нет. Импортируйте конфигурацию AWG или добавьте VLESS.</p>}</div>
          </section>

          <div className="wc-secondary-sections">
            <section className={`${panel} wc-legacy-egress`} aria-labelledby="egress-heading">
              <h2 id="egress-heading" className="text-lg font-semibold">Выход VLESS</h2>
              <p className="text-sm text-gray-400">{data.vless ? `${securityLabel(data.vless.security)} · ${data.vless.server_name} · ${data.vless.address} · ${data.vless.port}` : 'Выход ещё не настроен.'}</p>
              <details className="wc-action"><summary>Изменить прямой VLESS</summary>
              <div className="flex flex-wrap gap-2" aria-label="Способ добавления VLESS">
                <button className={secondary} aria-pressed={importing} onClick={() => setImporting(true)} disabled={busy}>Импорт ссылки</button>
                <button className={secondary} aria-pressed={!importing} onClick={() => setImporting(false)} disabled={busy}>Ручной ввод</button>
              </div>
              <form key={String(importing)} onSubmit={saveEgress}>
                <fieldset className="space-y-3" disabled={busy}>
                  {importing ? <>
                    <label className="block">Ссылка VLESS<input type="password" name="uri" className={field} required autoComplete="off" maxLength={4096} /></label>
                    <label className="block">IP выхода (если в ссылке домен)<input name="address" className={field} /></label>
                  </> : <ManualVlessFields />}
                  <button className={primary}>Сохранить выход</button>
                </fieldset>
              </form>
              </details>
            </section>

            <section className={`${panel} wc-clients`} aria-labelledby="clients-heading">
              <h2 id="clients-heading" className="text-lg font-semibold">Клиенты WireGuard</h2>
              <details className="wc-action"><summary>Добавить клиента</summary><form onSubmit={createClient}>
                <fieldset className="space-y-3" disabled={busy}>
                  <label className="block">Имя клиента<input name="name" className={field} maxLength={64} required /></label>
                  <button className={primary}>Создать и скачать .conf</button>
                </fieldset>
              </form>
              </details>
              <p className="text-sm text-gray-400">Скачивание доступно при создании. Доступ нового клиента включается при применении черновика.</p>
              <label className="wc-search">Поиск клиентов<input type="search" placeholder="Имя клиента" value={search} onChange={event => setSearch(event.target.value)} /></label>
              {!data.clients.length && <p className="wc-empty">Клиентов пока нет. Создайте конфигурацию для первого устройства.</p>}
              {!!data.clients.length && <ul className="divide-y divide-gray-800">
                {data.clients.filter(client => client.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(client => <li key={client.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <div className="break-words">{client.name}</div>
                    <div className="break-words text-sm text-gray-400">{client.addresses.join(', ')}</div>
                  </div>
                  <button className={secondary} disabled={busy || !agent || Boolean(pending)} onClick={() => {
                    if (!window.confirm(`Отозвать доступ клиента «${client.name}»? Этот конфиг больше не будет работать.`)) return
                    void run(async () => {
                      const status = (await http.delete<Agent>(`/core/clients/${client.id}`)).data
                      return status.last_error ? 'Клиент отозван. Доступ остальных клиентов проверьте в статусе.' : 'Доступ клиента отозван.'
                    })
                  }}>Отозвать</button>
                </li>)}
              </ul>}
            </section>
          </div>

          <section className={`${panel} wc-routing`} aria-labelledby="rules-heading">
            <h2 id="rules-heading" className="text-lg font-semibold">Правила TCP и UDP</h2>
            <p className="text-sm text-gray-400">Порты DNS 53 и 853 зарезервированы. DIRECT разрешается только отдельным правилом.</p>
            <label>Остальной трафик<select className={field} value={data.default_egress ?? 'block'} disabled={busy || !hasVpn} onChange={event => {
              saveRules(data.rules, event.target.value === 'vpn' ? 'vpn' : 'block')
            }}><option value="block">BLOCK</option><option value="vpn">Выбранный VPN</option></select></label>
            <details className="wc-action"><summary>Добавить правило</summary><form onSubmit={addRule}>
              <fieldset className="grid items-end gap-3 sm:grid-cols-4" disabled={busy || !hasVpn}>
                <label>Адрес или подсеть CIDR<input name="destination" className={field} placeholder="198.51.100.10/32" required /></label>
                <label>Порт<input name="port" className={field} type="number" min={1} max={65535} required /></label>
                <label>Действие<select name="egress" className={field} defaultValue="vpn">
                  {Object.entries(actions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select></label>
                <button className={primary}>Добавить правило</button>
              </fieldset>
            </form>
            </details>
            {!data.rules.length && <p className="wc-empty">Правил пока нет. Используется политика по умолчанию.</p>}
            {!!data.rules.length && <ul className="divide-y divide-gray-800">
              {data.rules.map((rule, index) => <li key={`${rule.destination}-${rule.port}`} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <span>{rule.destination} · {rule.port} · {actions[rule.egress]}</span>
                <button className={secondary} disabled={busy} onClick={() => saveRules(data.rules.filter((_, i) => i !== index))}>Удалить</button>
              </li>)}
            </ul>}
          </section>
        </>}
          </div>
        </main>
      </div>
    </div>
  )
}
