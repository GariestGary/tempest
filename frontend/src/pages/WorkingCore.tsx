import { useState, type FormEvent } from 'react'
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
}
type Gateway = {
  revision: number
  providers: { id: string; name: string }[]
  saved_egresses: { id: string; name: string; provider_id: string | null }[]
  selected_egress_id: string | null
  vless: { address: string; port: number; server_name: string } | null
  clients: { id: string; name: string; addresses: string[] }[]
  rules: Rule[]
  resolvers: string[]
  agent: Agent | null
  agent_error: string | null
}
type DraftDiff = { revision: number; base_generation: number; sections: string[] }

const actions: Record<Action, string> = { direct: 'DIRECT', vpn: 'VLESS', block: 'BLOCK' }
const sections: Record<string, string> = {
  wireguard: 'WireGuard', vless: 'VLESS', dns: 'DNS', 'destination-policy': 'Правила маршрутизации',
}
const panel = 'rounded-xl border border-gray-800 bg-gray-900 p-4 sm:p-6 space-y-4'
const field = 'w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-gray-100'
const primary = 'rounded-md bg-brand-600 px-4 py-2 font-medium text-white hover:bg-brand-500 disabled:opacity-50'
const secondary = 'rounded-md border border-gray-700 px-3 py-2 hover:bg-gray-800 disabled:opacity-50'

function value(form: FormData, key: string) {
  return String(form.get(key) ?? '').trim()
}

function errorMessage(error: unknown) {
  if (isAxiosError(error) && typeof error.response?.data?.detail === 'string') {
    return error.response.data.detail
  }
  return 'Не удалось выполнить запрос. Обновите статус перед повтором.'
}

export function WorkingCore() {
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ['working-core'],
    queryFn: () => http.get<Gateway>('/core').then(response => response.data),
    retry: false,
  })
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [importing, setImporting] = useState(true)
  const [diff, setDiff] = useState<DraftDiff | null>(null)
  const [changePassword, setChangePassword] = useState(false)

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
        await http.put('/core/egress', {
          address: value(body, 'address'),
          port: Number(value(body, 'port')),
          server_name: value(body, 'server_name'),
          client_id: value(body, 'client_id'),
          certificate_sha256: value(body, 'pin') || null,
        })
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
        uri: { uri: value(body, 'uri'), dial_address: value(body, 'address') || null },
      })
      form.reset()
      return 'Выход сохранён. Выберите его и примените изменения.'
    })
  }

  function saveRules(rules: Rule[]) {
    if (!data) return
    void run(async () => {
      await http.put('/core/rules', { revision: data.revision, rules })
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
  return (
    <main className="min-h-screen bg-gray-950 px-4 py-6 text-gray-100 sm:px-8">
      <div className="mx-auto max-w-5xl space-y-5">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold">TEMPEST <span className="text-base font-normal text-gray-400">Working Core</span></h1>
          <div className="flex flex-wrap gap-2 text-sm">
            <button type="button" className={secondary} onClick={() => setChangePassword(!changePassword)}>Сменить пароль</button>
            <button type="button" className={secondary} onClick={() => {
              localStorage.removeItem('pitun_token')
              window.location.assign('/login')
            }}>Выйти</button>
          </div>
        </header>

        {changePassword && <form className={panel} onSubmit={event => {
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
          <section className={panel} aria-labelledby="apply-heading">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="apply-heading" className="text-lg font-semibold">Конфигурация</h2>
              <button className={secondary} disabled={busy} onClick={() => void refetch()}>Обновить статус</button>
            </div>
            <dl className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
              <div><dt className="text-gray-400">Активное поколение</dt><dd>{agent ? agent.active_generation || 'Нет' : 'Неизвестно'}</dd></div>
              <div><dt className="text-gray-400">Последнее подтверждённое (LKG)</dt><dd>{agent ? agent.last_known_good_generation || 'Нет' : 'Неизвестно'}</dd></div>
              <div><dt className="text-gray-400">DNS через VLESS</dt><dd>{data.resolvers.join(', ')}</dd></div>
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
              <button className={primary} disabled={busy || !agent || !data.vless || Boolean(pending)} onClick={() => void run(async () => {
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

          <section className={panel} aria-labelledby="saved-egresses-heading">
            <h2 id="saved-egresses-heading" className="text-lg font-semibold">Провайдеры и выходы VLESS</h2>
            <p className="text-sm text-gray-400">Выбранный выход используется всеми правилами VLESS после применения конфигурации.</p>
            <form onSubmit={event => {
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
              <button className={secondary} disabled={busy || data.saved_egresses.some(item => item.provider_id === provider.id)} onClick={() => {
                if (!window.confirm(`Удалить провайдера «${provider.name}»?`)) return
                void run(async () => {
                  await http.delete(`/core/providers/${provider.id}`, { data: { revision: data.revision } })
                  return 'Провайдер удалён.'
                })
              }}>Удалить провайдера</button>
            </li>)}</ul>}
            <form onSubmit={addSavedEgress}>
              <fieldset className="grid gap-3 sm:grid-cols-2" disabled={busy}>
                <label>Название<input name="name" className={field} maxLength={64} required /></label>
                <label>Провайдер<select name="provider" className={field} defaultValue=""><option value="">Без провайдера</option>{data.providers?.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select></label>
                <label>IP выхода (если в ссылке домен)<input name="address" className={field} /></label>
                <label className="sm:col-span-2">Ссылка VLESS TCP/TLS<input type="password" name="uri" className={field} maxLength={4096} autoComplete="off" required /></label>
                <button className={primary}>Сохранить выход</button>
              </fieldset>
            </form>
            {!!data.saved_egresses?.length && <ul className="divide-y divide-gray-800">
              {data.saved_egresses.map(item => <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <span>{item.name}{item.id === data.selected_egress_id ? ' · выбран в черновике' : ''}</span>
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
          </section>

          <div className="grid gap-5 lg:grid-cols-2">
            <section className={panel} aria-labelledby="egress-heading">
              <h2 id="egress-heading" className="text-lg font-semibold">Выход VLESS</h2>
              <p className="text-sm text-gray-400">{data.vless ? `${data.vless.server_name} · ${data.vless.address} · ${data.vless.port}` : 'Выход ещё не настроен.'}</p>
              <div className="flex flex-wrap gap-2" aria-label="Способ добавления VLESS">
                <button className={secondary} aria-pressed={importing} onClick={() => setImporting(true)} disabled={busy}>Импорт ссылки</button>
                <button className={secondary} aria-pressed={!importing} onClick={() => setImporting(false)} disabled={busy}>Ручной ввод</button>
              </div>
              <form key={String(importing)} onSubmit={saveEgress}>
                <fieldset className="space-y-3" disabled={busy}>
                  {importing ? <>
                    <label className="block">Ссылка VLESS<input type="password" name="uri" className={field} required autoComplete="off" maxLength={4096} /></label>
                    <label className="block">IP выхода (если в ссылке домен)<input name="address" className={field} /></label>
                  </> : <>
                    <label className="block">IP выхода<input name="address" className={field} required /></label>
                    <label className="block">Порт<input name="port" className={field} type="number" defaultValue={443} min={1} max={65535} required /></label>
                    <label className="block">Имя сервера TLS<input name="server_name" className={field} required /></label>
                    <label className="block">UUID клиента<input name="client_id" className={field} type="password" autoComplete="off" required /></label>
                    <label className="block">SHA-256 сертификата (необязательно)<input name="pin" className={field} maxLength={64} /></label>
                  </>}
                  <button className={primary}>Сохранить выход</button>
                </fieldset>
              </form>
            </section>

            <section className={panel} aria-labelledby="clients-heading">
              <h2 id="clients-heading" className="text-lg font-semibold">Клиенты WireGuard</h2>
              <form onSubmit={createClient}>
                <fieldset className="space-y-3" disabled={busy}>
                  <label className="block">Имя клиента<input name="name" className={field} maxLength={64} required /></label>
                  <button className={primary}>Создать и скачать .conf</button>
                </fieldset>
              </form>
              <p className="text-sm text-gray-400">Скачивание доступно при создании. Доступ нового клиента включается при применении черновика.</p>
              {!!data.clients.length && <ul className="divide-y divide-gray-800">
                {data.clients.map(client => <li key={client.id} className="flex items-center justify-between gap-3 py-3">
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

          <section className={panel} aria-labelledby="rules-heading">
            <h2 id="rules-heading" className="text-lg font-semibold">Правила TCP и UDP</h2>
            <p className="text-sm text-gray-400">По умолчанию BLOCK. Порты DNS 53 и 853 зарезервированы.</p>
            <form onSubmit={addRule}>
              <fieldset className="grid items-end gap-3 sm:grid-cols-4" disabled={busy || !data.vless}>
                <label>Адрес или подсеть CIDR<input name="destination" className={field} placeholder="198.51.100.10/32" required /></label>
                <label>Порт<input name="port" className={field} type="number" min={1} max={65535} required /></label>
                <label>Действие<select name="egress" className={field} defaultValue="vpn">
                  {Object.entries(actions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select></label>
                <button className={primary}>Добавить правило</button>
              </fieldset>
            </form>
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
  )
}
