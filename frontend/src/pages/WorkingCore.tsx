import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Boxes, Cable, ChevronLeft, CircleUserRound, LayoutDashboard, LogOut, Menu, Route, Settings2, ShieldCheck, UsersRound, Plus, X } from 'lucide-react'
import './working-core.css'
import { Select, ActionMenu, DecisionDialog } from './WorkingCoreControls'
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

function ActionDialog({ title, children, error, notice, busy, clearError, triggerId }: { title: string; children: ReactNode; error?: string; notice?: string; busy?: boolean; clearError?: () => void; triggerId?: string }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)
  const [open, setOpen] = useState(false)
  return <>
    <button ref={trigger} id={triggerId} hidden={Boolean(triggerId)} type="button" className="wc-action-trigger" onClick={() => {
      returnFocus.current = document.activeElement as HTMLElement | null
      clearError?.()
      setOpen(true)
      const element = dialog.current
      if (element?.showModal) element.showModal()
      else element?.setAttribute('open', '')
    }}><Plus size={16}/>{title}</button>
    <dialog ref={dialog} className="wc-dialog" aria-label={title} onClose={() => { setOpen(false); (returnFocus.current ?? trigger.current)?.focus() }}>
      <div className="wc-dialog-head"><div><span>НАСТРОЙКА</span><h2>{title}</h2></div><button type="button" className="wc-dialog-close" aria-label="Закрыть" onClick={() => { if (dialog.current?.close) dialog.current.close(); else dialog.current?.removeAttribute('open') }}><X size={18}/></button></div>
      <div className="wc-dialog-content">{open && error && <p role="alert" className="wc-dialog-error">{error}</p>}{open && notice && <p role="status" className="wc-dialog-success">{notice}</p>}{open && busy && <p role="status">Выполняется запрос…</p>}{children}</div>
    </dialog>
  </>
}

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
    <label>Защита VLESS<Select aria-label="Защита VLESS" name="security" className={field} value={security}
      onChange={event => setSecurity(event.target.value === 'reality' ? 'reality' : 'tls')}>
      <option value="tls">TCP/TLS</option>
      <option value="reality">TCP/Reality</option>
    </Select></label>
    <label>IP выхода<input name="address" className={field} required /></label>
    <label>Порт<input name="port" className={field} type="number" defaultValue={443} min={1} max={65535} required /></label>
    <label>Имя сервера (SNI)<input name="server_name" className={field} maxLength={253} required /></label>
    <label>UUID клиента<input name="client_id" className={field} type="password" autoComplete="off" required /></label>
    {security === 'reality' ? <>
      <label>Публичный ключ Reality<input name="public_key" className={field} type="password" autoComplete="off"
        pattern="[A-Za-z0-9_\-]{43}" minLength={43} maxLength={43} required /></label>
      <label>Short ID (если задан)<input name="short_id" className={field} type="password" autoComplete="off"
        pattern="(?:[A-Fa-f0-9]{2}){0,8}" maxLength={16} /></label>
      <label>Fingerprint<Select aria-label="Fingerprint" name="fingerprint" className={field} defaultValue="chrome">
        {['chrome', 'firefox', 'safari', 'ios', 'edge'].map(name => <option key={name}>{name}</option>)}
      </Select></label>
      <label>Flow<Select aria-label="Flow" name="flow" className={field} defaultValue="">
        <option value="">Без flow</option>
        <option value="xtls-rprx-vision">xtls-rprx-vision</option>
      </Select></label>
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

function SubscriptionImport({ providers, revision, busy, error, notice, clearError, run }: {
  providers: Provider[]
  revision: number
  busy: boolean
  error: string
  notice: string
  clearError: () => void
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

  return <ActionDialog triggerId="add-subscription" title="Импорт подписки VLESS" busy={busy} error={error} notice={notice} clearError={clearError}>
    <form className="mt-3" onSubmit={showPreview} onChange={() => setPreview(null)}>
      <fieldset className="grid gap-3 sm:grid-cols-2" disabled={busy}>
        <label>Провайдер для импорта<Select aria-label="Провайдер для импорта" name="subscriptionProvider" className={field} required defaultValue="">
          <option value="" disabled>Выберите провайдера</option>
          {providers.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
        </Select></label>
        <label>Источник<Select aria-label="Источник" className={field} value={mode} onChange={event => setMode(event.target.value === 'url' ? 'url' : 'content')}>
          <option value="content">Текст или Base64</option>
          <option value="url">HTTPS-ссылка</option>
        </Select></label>
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
  </ActionDialog>
}

export function WorkingCore() {
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ['working-core'],
    queryFn: () => http.get<Gateway>('/core').then(response => response.data),
    retry: false,
  })
  const [page, setPage] = useState('overview')
  const [collapsed, setCollapsed] = useState<boolean | null>(null)
  const [autoCompact, setAutoCompact] = useState(() => typeof window.matchMedia === 'function' && window.matchMedia('(min-width: 761px) and (max-width: 1200px)').matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(min-width: 761px) and (max-width: 1200px)')
    const update = () => setAutoCompact(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  const compact = collapsed ?? autoCompact
  const [mobileNav, setMobileNav] = useState(false)
  const [search, setSearch] = useState('')
  const [protocolFilter, setProtocolFilter] = useState('all')
  const [providerFilter, setProviderFilter] = useState('all')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [importing, setImporting] = useState(true)
  const [savedManual, setSavedManual] = useState(false)
  const [diff, setDiff] = useState<DraftDiff | null>(null)
  const [changePassword, setChangePassword] = useState(false)
  const [awgPreview, setAwgPreview] = useState<{ ipv6_configured: boolean } | null>(null)
  const [awgFileName, setAwgFileName] = useState('')
  const [decision, setDecision] = useState<{ title: string; body: string; initial?: string; confirm: (value: string) => void } | null>(null)
  const [goToPreview, setGoToPreview] = useState(false)

  useEffect(() => {
    if (goToPreview && page === 'overview' && diff) {
      document.getElementById('wc-preview')?.scrollIntoView?.({ block: 'nearest' })
      setGoToPreview(false)
    }
  }, [goToPreview, page, diff])

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
      setGoToPreview(false)
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
  const egressRows = [
    ...(data?.saved_awg_egresses ?? []).map(item => ({ ...item, kind: 'awg' as const })),
    ...(data?.saved_egresses ?? []).map(item => ({ ...item, kind: 'vless' as const })),
    ...(data?.vless && !data.selected_egress_id ? [{ id: 'direct-vless', name: 'Прямой VLESS', provider_id: null, kind: 'direct' as const }] : []),
  ]
  const filteredEgresses = egressRows.filter(item => item.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()) &&
    (protocolFilter === 'all' || (protocolFilter === 'awg' ? item.kind === 'awg' : item.kind !== 'awg')) &&
    (providerFilter === 'all' || (providerFilter === 'none' ? !item.provider_id : item.provider_id === providerFilter)))
  const previewCurrent = Boolean(diff && data && diff.revision === data.revision && agent && diff.base_generation === agent.active_generation)
  function showPreview() {
    setPage('overview')
    setGoToPreview(true)
    if (!previewCurrent) void run(async () => {
      setDiff((await http.post<DraftDiff>('/core/preview')).data)
      return ''
    })
  }
  return (
    <div className={`wc-shell ${compact ? 'is-collapsed' : ''}`} data-page={page}>
      <aside className={`wc-sidebar ${mobileNav ? 'is-open' : ''}`}>
        <div className="wc-brand"><span className="wc-logo"><ShieldCheck size={20} /></span><span className="wc-brand-text"><strong>TEMPEST</strong><small>VPN GATEWAY</small></span></div>
        <div className="wc-nav-label">УПРАВЛЕНИЕ</div>
        <nav aria-label="Основная навигация" className="wc-nav">
          {([
            ['overview', 'Обзор', LayoutDashboard], ['providers', 'Провайдеры', Boxes],
            ['egress', 'Выходы', Cable], ['routing', 'Маршрутизация', Route],
            ['clients', 'Клиенты', UsersRound], ['settings', 'Настройки', Settings2],
          ] as const).map(([id, label, Icon]) => <button key={id as string} type="button" title={label as string}
            aria-current={page === id ? 'page' : undefined} onClick={() => { setPage(id as string); setSearch(''); setMobileNav(false); document.getElementById('main-content')?.scrollTo?.(0, 0) }}>
            <Icon size={19} /><span>{label as string}</span>
          </button>)}
        </nav>
        <div className="wc-sidebar-bottom"><span className="wc-sidebar-caption">SELF-HOSTED GATEWAY</span><button type="button" className="wc-collapse" onClick={() => setCollapsed(!compact)} aria-label={compact ? 'Развернуть меню' : 'Свернуть меню'}><ChevronLeft size={18} /></button></div>
      </aside>
      {mobileNav && <button className="wc-backdrop" aria-label="Закрыть меню" onClick={() => setMobileNav(false)} />}
      <div className="wc-workspace">
        <header className="wc-topbar">
          <button className="wc-menu" onClick={() => setMobileNav(!mobileNav)} aria-label="Открыть меню"><Menu size={20} /></button>
          <div className="wc-breadcrumb"><span>TEMPEST</span><span className="wc-slash">/</span><strong>{({ overview: 'Обзор', providers: 'Провайдеры', egress: 'Выходы', routing: 'Маршрутизация', clients: 'Клиенты', settings: 'Настройки' } as Record<string, string>)[page]}</strong></div>
          <div className="wc-top-actions">{import.meta.env.VITE_MOCK_PREVIEW === '1' && <span className="wc-mock-label">МОК · изменения не сохраняются</span>}<span className={`wc-agent ${agent ? 'online' : ''}`}><i />{!data ? 'Статус Agent неизвестен' : agent ? 'Agent подключён' : 'Agent недоступен'}</span><button type="button" className="wc-account" onClick={() => setPage('settings')} aria-label="Настройки учётной записи"><CircleUserRound size={19}/></button><button type="button" className="wc-account" aria-label="Выйти" onClick={() => { localStorage.removeItem('pitun_token'); window.location.assign('/login') }}><LogOut size={18}/></button></div>
        </header>
        <main className="wc-main" id="main-content">
          <div className="wc-content">
            <div className="wc-page-heading"><div><h1>{({ overview: 'Обзор системы', providers: 'Провайдеры', egress: 'Исходящие подключения', routing: 'Маршрутизация', clients: 'Клиенты WireGuard', settings: 'Настройки' } as Record<string, string>)[page]}</h1><p>{({ overview: 'Состояние шлюза и управление конфигурацией', providers: 'Организуйте подключения по провайдерам', egress: 'Управляйте каталогом и выбором VPN-выхода', routing: 'Определите, куда направлять трафик', clients: 'Управляйте доступом ваших устройств', settings: 'Учётная запись и доступ к панели' } as Record<string, string>)[page]}</p></div></div>
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
            <p className="wc-config-hint">Черновик сохраняется отдельно от активного поколения. Просмотрите изменения перед применением.</p>
            <div className="flex flex-wrap gap-2">
              <button className={primary} disabled={busy || !agent || !hasVpn || Boolean(pending)} onClick={() => void run(async () => {
                setDiff((await http.post<DraftDiff>('/core/preview')).data)
                return ''
              })}>Посмотреть изменения</button>
            </div>
            {diff && <div id="wc-preview" className="wc-preview space-y-3"><div className="wc-preview-heading">Предпросмотр изменений <span>Ревизия {diff.revision}</span></div>
              {!previewCurrent && <p className="wc-stale-preview">Черновик или активное поколение изменились. Повторите предпросмотр.</p>}
              <p>{diff.sections.length ? diff.sections.map(section => sections[section] ?? section).join(', ') : 'Нет изменений, влияющих на трафик.'}</p>
              <p className="text-sm text-gray-400">При применении WireGuard переподключится. Подтверждение проверяет трафик контрольного peer.</p>
              <button className={primary} disabled={busy || !previewCurrent || !diff.sections.length} onClick={() => void run(async () => {
                await http.post('/core/apply', { revision: diff.revision, base_generation: diff.base_generation })
                return 'Конфигурация применена и подтверждена. Контрольные пробы прошли; новое поколение сохранено как LKG.'
              })}>Применить конфигурацию</button>
            </div>}
            {busy && <p role="status" className="text-sm text-gray-400">Выполняется запрос…</p>}
          </section>

          <section className={`${panel} wc-catalog`} aria-labelledby="saved-egresses-heading">
            <h2 id="saved-egresses-heading" className="wc-sr-only">{page === 'providers' ? 'Ваши провайдеры' : 'Каталог выходов'}</h2>
            <div className="wc-providers-only"><div className="wc-toolbar"><label className="wc-search">Поиск<input type="search" placeholder="Название провайдера" value={search} onChange={event => setSearch(event.target.value)} /></label><ActionDialog busy={busy} error={error} notice={notice} clearError={() => { setError(''); setNotice('') }} title="Добавить провайдера"><form onSubmit={event => {
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
            </ActionDialog></div>
            {!!data.providers?.length && <div className="wc-table-wrap"><table className="wc-table"><thead><tr><th>Провайдер</th><th>Выходы</th><th>Действия</th></tr></thead><tbody>{data.providers.filter(provider => provider.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(provider => <tr key={provider.id}>
              <td><strong>{provider.name}</strong></td><td>{[...(data.saved_egresses ?? []), ...(data.saved_awg_egresses ?? [])].filter(item => item.provider_id === provider.id).length}</td><td><ActionMenu label={`Действия провайдера ${provider.name}`} actions={[
              { label: 'Переименовать', disabled: busy, onSelect: () => setDecision({ title: 'Переименовать провайдера', body: 'Укажите новое название.', initial: provider.name, confirm: name => { void run(async () => { await http.put(`/core/providers/${provider.id}`, { revision: data.revision, name }); return 'Провайдер переименован.' }) } }) },
              { label: 'Удалить провайдера', danger: true, disabled: busy || [...(data.saved_egresses ?? []), ...(data.saved_awg_egresses ?? [])].some(item => item.provider_id === provider.id), onSelect: () => setDecision({ title: 'Удалить провайдера?', body: `Провайдер «${provider.name}» будет удалён.`, confirm: () => { void run(async () => { await http.delete(`/core/providers/${provider.id}`, { data: { revision: data.revision } }); return 'Провайдер удалён.' }) } }) },
            ]}/></td></tr>)}</tbody></table></div>}
            {!!data.providers?.length && search && data.providers.every(provider => !provider.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())) && <p className="wc-empty">По вашему запросу провайдеры не найдены.</p>}
            {!data.providers?.length && <p className="wc-empty">Провайдеров пока нет. Добавьте первого, чтобы упорядочить выходы.</p>}</div>
            <div className="wc-egress-only">
              <div className="wc-toolbar">
                <label className="wc-search">Поиск<input type="search" placeholder="Название выхода" value={search} onChange={event => setSearch(event.target.value)} /></label>
                <label>Протокол<Select aria-label="Фильтр по протоколу" value={protocolFilter} onChange={event => setProtocolFilter(event.target.value)}><option value="all">Все протоколы</option><option value="awg">AWG</option><option value="vless">VLESS</option></Select></label>
                <label>Провайдер<Select aria-label="Фильтр по провайдеру" value={providerFilter} onChange={event => setProviderFilter(event.target.value)}><option value="all">Все провайдеры</option><option value="none">Без провайдера</option>{data.providers?.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</Select></label>
                <ActionMenu label="Добавить выход" text="Добавить выход" actions={[
                  { label: 'Импорт AWG', onSelect: () => document.getElementById('add-awg')?.click() },
                  { label: 'Добавить VLESS', onSelect: () => document.getElementById('add-vless')?.click() },
                  { label: 'Импорт подписки', disabled: !data.providers?.length, onSelect: () => document.getElementById('add-subscription')?.click() },
                  { label: 'Прямой VLESS', onSelect: () => document.getElementById('edit-direct-vless')?.click() },
                ]}/>
              </div>
              {!!data.providers?.length && <SubscriptionImport providers={data.providers} revision={data.revision} busy={busy} error={error} notice={notice} clearError={() => { setError(''); setNotice('') }} run={run} />}
            <ActionDialog triggerId="add-awg" busy={busy} error={error} notice={notice} clearError={() => { setError(''); setNotice('') }} title="Импортировать AWG"><form onChange={() => setAwgPreview(null)} onSubmit={event => {
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
                setAwgFileName('')
                setAwgPreview(null)
                return 'AWG-конфигурация сохранена. Выберите выход и просмотрите изменения перед применением.'
              })
            }}>
              <fieldset className="grid gap-3 sm:grid-cols-2" disabled={busy}>
                <legend>Импорт AmneziaWG</legend>
                <label>Название AWG<input name="awgName" className={field} maxLength={64} required /></label>
                <label>Провайдер AWG<Select aria-label="Провайдер AWG" name="awgProvider" className={field} defaultValue=""><option value="">Без провайдера</option>{data.providers?.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</Select></label>
                <label className="wc-file">Файл AWG .conf<input name="awgFile" className={field} type="file" accept=".conf" required onChange={event => setAwgFileName(event.target.files?.[0]?.name ?? '')} /><span>{awgFileName || 'Выберите файл .conf'}</span></label>
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
                    return import.meta.env.VITE_MOCK_PREVIEW === '1' ? 'Демо: файл получен mock API, содержимое не проверено.' : 'Структура файла проверена. Совместимость и соединение ещё не проверены.'
                  })
                }}>Проверить файл AWG</button>
                <button className={secondary}>Сохранить AWG</button>
              </fieldset>
              {awgPreview && <p className="text-sm text-gray-400">IPv6 в конфигурации: {awgPreview.ipv6_configured ? 'задан' : 'не задан'}. Проверка не подтверждает работу VPN.</p>}
            </form>
            </ActionDialog>
            <ActionDialog triggerId="add-vless" busy={busy} error={error} notice={notice} clearError={() => { setError(''); setNotice('') }} title="Добавить VLESS в каталог"><form onSubmit={addSavedEgress}>
              <fieldset className="grid gap-3 sm:grid-cols-2" disabled={busy}>
                <label>Название<input name="name" className={field} maxLength={64} required /></label>
                <label>Провайдер<Select aria-label="Провайдер" name="provider" className={field} defaultValue=""><option value="">Без провайдера</option>{data.providers?.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</Select></label>
                <label>Способ добавления выхода<Select aria-label="Способ добавления выхода" className={field} value={savedManual ? 'manual' : 'uri'}
                  onChange={event => setSavedManual(event.target.value === 'manual')}>
                  <option value="uri">Импорт ссылки</option><option value="manual">Ручной ввод</option>
                </Select></label>
                {savedManual ? <div className="sm:col-span-2"><ManualVlessFields /></div> : <>
                  <label>IP выхода (если в ссылке домен)<input name="address" className={field} /></label>
                  <label className="sm:col-span-2">Ссылка VLESS TCP/TLS или Reality<input type="password" name="uri"
                    className={field} maxLength={4096} autoComplete="off" required /></label>
                </>}
                <button className={primary}>Сохранить выход</button>
              </fieldset>
            </form>
            </ActionDialog>
              <ActionDialog triggerId="edit-direct-vless" busy={busy} error={error} notice={notice} clearError={() => { setError(''); setNotice('') }} title="Изменить прямой VLESS">
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
              </ActionDialog>
              {egressRows.length ? <div className="wc-table-wrap"><table className="wc-table wc-egress-table"><thead><tr><th>Выход</th><th>Протокол</th><th>Провайдер</th><th>Состояние</th><th>Действия</th></tr></thead><tbody>
                {filteredEgresses.map(item => <tr key={`${item.kind}-${item.id}`}>
                  <td><strong>{item.name}</strong>{item.kind === 'direct' && <small>{data.vless?.server_name} · {data.vless?.address}:{data.vless?.port}</small>}</td>
                  <td>{item.kind === 'awg' ? 'AWG' : `VLESS${item.kind === 'vless' ? ` · ${securityLabel(item.security)}` : ''}`}</td>
                  <td>{item.kind === 'vless' ? <Select aria-label={`Провайдер выхода «${item.name}»`} value={item.provider_id ?? ''} disabled={busy} onChange={event => {
                    const provider_id = event.target.value || null
                    void run(async () => { await http.put(`/core/egresses/${item.id}/provider`, { revision: data.revision, provider_id }); return 'Провайдер выхода изменён.' })
                  }}><option value="">Без провайдера</option>{data.providers?.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</Select> : (data.providers?.find(provider => provider.id === item.provider_id)?.name ?? 'Без провайдера')}</td>
                  <td><div className="wc-status-stack">{(item.kind === 'direct' || item.id === data.selected_egress_id) && <span className="wc-badge wc-badge-draft">В черновике</span>}
                    {item.kind === 'awg' && agent?.active_egress_id === item.id && <span className={`wc-badge ${agent.egress_state === 'active_verified' ? 'wc-badge-success' : agent.egress_state === 'unavailable' ? 'wc-badge-error' : 'wc-badge-warning'}`}>{agent.egress_state === 'active_verified' ? 'Проверка прошла' : agent.egress_state === 'unavailable' ? 'Недоступен' : 'Не проверен'}</span>}
                    {item.kind === 'awg' && agent?.active_egress_id === item.id && agent.egress_checked_epoch_ms && <small>Проверка: {new Date(agent.egress_checked_epoch_ms).toLocaleString('ru-RU')}</small>}
                  </div></td>
                  <td>{item.kind === 'awg' ? <ActionMenu label={`Действия выхода ${item.name}`} actions={[
                    ...(agent?.active_egress_id === item.id ? [{ label: 'Проверить AWG', disabled: busy || Boolean(pending), onSelect: () => { void run(async () => { const status = (await http.post<Agent>('/core/awg-egresses/probe')).data; return status.egress_state === 'unavailable' ? 'Последняя проверка AWG не прошла.' : 'Контрольная проверка AWG прошла.' }) } }] : []),
                    { label: 'Выбрать AWG', disabled: busy || data.selected_egress_id === item.id, onSelect: () => { void run(async () => { await http.post(`/core/awg-egresses/${item.id}/select`, { revision: data.revision }); return 'AWG выбран в черновике; требуется просмотр и применение.' }) } },
                    { label: 'Удалить AWG', danger: true, disabled: busy || data.selected_egress_id === item.id, onSelect: () => setDecision({ title: 'Удалить выход?', body: `Выход «${item.name}» будет удалён из каталога.`, confirm: () => { void run(async () => { await http.delete(`/core/awg-egresses/${item.id}`, { data: { revision: data.revision } }); return 'AWG-конфигурация удалена.' }) } }) },
                  ]}/> : item.kind === 'vless' ? <ActionMenu label={`Действия выхода ${item.name}`} actions={[
                    { label: 'Выбрать', disabled: busy || item.id === data.selected_egress_id, onSelect: () => { void run(async () => { await http.post(`/core/egresses/${item.id}/select`, { revision: data.revision }); return 'Выход выбран в черновике. Проверьте и примените изменения.' }) } },
                    { label: 'Удалить', danger: true, disabled: busy || item.id === data.selected_egress_id, onSelect: () => setDecision({ title: 'Удалить выход?', body: `Выход «${item.name}» будет удалён из каталога.`, confirm: () => { void run(async () => { await http.delete(`/core/egresses/${item.id}`, { data: { revision: data.revision } }); return 'Выход удалён.' }) } }) },
                  ]}/> : <ActionMenu label="Действия прямого VLESS" actions={[{ label: 'Изменить', onSelect: () => document.getElementById('edit-direct-vless')?.click() }]}/>}</td>
                </tr>)}
              </tbody></table>{!filteredEgresses.length && <p className="wc-empty">По вашему запросу выходы не найдены.</p>}</div> : <p className="wc-empty">Выходов пока нет. Добавьте AWG или VLESS.</p>}
            </div>
          </section>
          <div className="wc-secondary-sections">
            <section className={`${panel} wc-clients`} aria-labelledby="clients-heading">
              <h2 id="clients-heading" className="wc-sr-only">Клиенты WireGuard</h2>
              <p className="text-sm text-gray-400">Скачивание доступно при создании. Доступ нового клиента включается при применении черновика.</p>
              <div className="wc-toolbar"><label className="wc-search">Поиск клиентов<input type="search" placeholder="Имя клиента" value={search} onChange={event => setSearch(event.target.value)} /></label><ActionDialog busy={busy} error={error} notice={notice} clearError={() => { setError(''); setNotice('') }} title="Добавить клиента"><form onSubmit={createClient}>
                <fieldset className="space-y-3" disabled={busy}>
                  <label className="block">Имя клиента<input name="name" className={field} maxLength={64} required /></label>
                  <button className={primary}>Создать и скачать .conf</button>
                </fieldset>
              </form>
              </ActionDialog></div>
              {!data.clients.length && <p className="wc-empty">Клиентов пока нет. Создайте конфигурацию для первого устройства.</p>}
              {!!data.clients.length && search && data.clients.every(client => !client.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())) && <p className="wc-empty">По вашему запросу клиенты не найдены.</p>}
              {!!data.clients.length && <div className="wc-table-wrap"><table className="wc-table"><thead><tr><th>Устройство</th><th>Адреса</th><th>Действия</th></tr></thead><tbody>
                {data.clients.filter(client => client.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(client => <tr key={client.id}>
                  <td><strong>{client.name}</strong></td><td>{client.addresses.join(', ')}</td><td>
                  <ActionMenu label={`Действия клиента ${client.name}`} actions={[{ label: 'Отозвать доступ', danger: true, disabled: busy || !agent || Boolean(pending), onSelect: () => setDecision({ title: 'Отозвать доступ?', body: `Конфигурация клиента «${client.name}» больше не будет работать.`, confirm: () => { void run(async () => { const status = (await http.delete<Agent>(`/core/clients/${client.id}`)).data; return status.last_error ? 'Клиент отозван. Доступ остальных клиентов проверьте в статусе.' : 'Доступ клиента отозван.' }) } }) }]}/>
                </td></tr>)}
              </tbody></table></div>}
            </section>
          </div>

          <section className={`${panel} wc-routing`} aria-labelledby="rules-heading">
            <h2 id="rules-heading" className="text-lg font-semibold">Правила TCP и UDP</h2>
            <p className="text-sm text-gray-400">Порты DNS 53 и 853 зарезервированы. DIRECT разрешается только отдельным правилом.</p>
            <div className="wc-toolbar"><label>Остальной трафик<Select aria-label="Остальной трафик" className={field} value={data.default_egress ?? 'block'} disabled={busy || !hasVpn} onChange={event => {
              saveRules(data.rules, event.target.value === 'vpn' ? 'vpn' : 'block')
            }}><option value="block">BLOCK</option><option value="vpn">Выбранный VPN</option></Select></label>
            <ActionDialog busy={busy} error={error} notice={notice} clearError={() => { setError(''); setNotice('') }} title="Добавить правило"><form onSubmit={addRule}>
              <fieldset className="grid items-end gap-3 sm:grid-cols-4" disabled={busy || !hasVpn}>
                <label>Адрес или подсеть CIDR<input name="destination" className={field} placeholder="198.51.100.10/32" required /></label>
                <label>Порт<input name="port" className={field} type="number" min={1} max={65535} required /></label>
                <label>Действие<Select aria-label="Действие" name="egress" className={field} defaultValue="vpn">
                  {Object.entries(actions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </Select></label>
                <button className={primary}>Добавить правило</button>
              </fieldset>
            </form>
            </ActionDialog></div>
            {!data.rules.length && <p className="wc-empty">Правил пока нет. Используется политика по умолчанию.</p>}
            {!!data.rules.length && <div className="wc-table-wrap"><table className="wc-table"><thead><tr><th>Приоритет</th><th>Назначение</th><th>Порт</th><th>Действие</th><th></th></tr></thead><tbody>
              {data.rules.map((rule, index) => <tr key={`${rule.destination}-${rule.port}`}>
                <td>{index + 1}</td><td>{rule.destination}</td><td>{rule.port}</td><td><span className="wc-badge">{actions[rule.egress]}</span></td><td>
                <ActionMenu label={`Действия правила ${index + 1}`} actions={[{ label: 'Удалить', danger: true, disabled: busy, onSelect: () => setDecision({ title: 'Удалить правило?', body: `Правило для ${rule.destination}:${rule.port} будет удалено из черновика.`, confirm: () => saveRules(data.rules.filter((_, i) => i !== index)) }) }]}/>
              </td></tr>)}
            </tbody></table></div>}
          </section>
        </>}
          </div>
        </main>
        {data && <aside className={`wc-draft-bar ${previewCurrent && diff?.sections.length ? 'has-changes' : ''}`} aria-label="Состояние черновика">
          <div><strong>Черновик · {data.revision}</strong><span>{pending ? 'Ожидается завершение применения' : previewCurrent ? (diff?.sections.length ? 'Есть изменения для применения' : 'Изменений трафика нет') : 'Изменения не проверены'}</span></div>
          <button type="button" className={secondary} disabled={busy || !agent || !hasVpn || Boolean(pending)} onClick={showPreview}>{previewCurrent ? 'К предпросмотру' : 'Проверить изменения'}</button>
        </aside>}
      </div>
      {decision && <DecisionDialog key={`${decision.title}-${decision.initial ?? decision.body}`} title={decision.title} body={decision.body} initial={decision.initial} onCancel={() => setDecision(null)} onConfirm={value => { const action = decision.confirm; setDecision(null); action(value) }}/>}
    </div>
  )
}
