import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { http } from '@/api/client'
import { WorkingCore } from '@/pages/WorkingCore'

function choose(label: string, option: string) {
  fireEvent.click(screen.getByRole('combobox', { name: label }))
  fireEvent.click(screen.getByRole('option', { name: option }))
}

vi.mock('@/api/client', () => ({
  http: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

describe('Working Core panel', () => {
  it('filters the combined catalog and previews changes from another section', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: {
      revision: 4, vless: null, clients: [], rules: [], resolvers: [],
      providers: [{ id: 'p1', name: 'Provider' }], selected_egress_id: 'a1',
      saved_awg_egresses: [{ id: 'a1', name: 'AWG exit', provider_id: 'p1' }],
      saved_egresses: [{ id: 'v1', name: 'VLESS exit', provider_id: null }],
      agent: { active_generation: 7, last_known_good_generation: 7, pending_generation_id: null },
      sync_state: 'changes', sync_sections: ['destination-policy'], sync_diff_available: true,
    } })
    vi.mocked(http.post).mockReset().mockResolvedValue({ data: { revision: 4, base_generation: 7, sections: ['vless'] } })
    render(<QueryClientProvider client={new QueryClient()}><WorkingCore /></QueryClientProvider>)
    await screen.findByRole('button', { name: 'Добавить выход' })
    fireEvent.click(screen.getByRole('button', { name: 'Выходы' }))
    const catalog = within(screen.getByRole('region', { name: 'Каталог выходов' }))
    expect(catalog.getByText('VLESS exit')).toBeInTheDocument()
    choose('Фильтр по протоколу', 'VLESS')
    expect(catalog.queryByText('AWG exit')).toBeNull()
    expect(catalog.getByText('VLESS exit')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Предпросмотр' }))
    expect(await screen.findByText('Есть неприменённые изменения')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Применить конфигурацию' })).toBeEnabled())
    expect(http.post).toHaveBeenCalledWith('/core/preview')
  })
  it('does not delete an exit when its confirmation is cancelled', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: {
      revision: 4, vless: null, clients: [], rules: [], resolvers: [], agent: null,
      providers: [], saved_egresses: [{ id: 'e1', name: 'Test', provider_id: null }], selected_egress_id: null,
    } })
    vi.mocked(http.delete).mockReset()
    render(<QueryClientProvider client={new QueryClient()}><WorkingCore /></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Действия выхода Test' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Удалить' }))
    const dialog = await screen.findByRole('dialog', { name: 'Удалить выход?' })
    expect(http.delete).not.toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Отмена' }))
    expect(screen.queryByRole('dialog', { name: 'Удалить выход?' })).toBeNull()
    expect(http.delete).not.toHaveBeenCalled()
  })
  it('previews subscription results and only adds exits after explicit confirmation', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: {
      revision: 7, vless: null, clients: [], rules: [], resolvers: [], agent: null,
      providers: [{ id: 'provider-1', name: 'VPS' }], saved_egresses: [], selected_egress_id: null,
    } })
    vi.mocked(http.post).mockReset()
      .mockResolvedValueOnce({ data: {
        revision: 7, preview_id: 'preview-1', results: [
          { line: 1, status: 'ready', name: 'VLESS #1' },
          { line: 2, status: 'duplicate' },
        ],
      } })
      .mockResolvedValueOnce({ data: { added: 1 } })
    render(<QueryClientProvider client={new QueryClient()}><WorkingCore /></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Добавить выход' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Импорт подписки' }))
    choose('Провайдер для импорта', 'VPS')
    const source = screen.getByLabelText('Содержимое подписки')
    fireEvent.change(source, { target: { value: 'fixture-subscription-content' } })
    fireEvent.click(screen.getByRole('button', { name: 'Предпросмотр подписки' }))

    const confirm = await screen.findByRole('button', { name: 'Добавить выходы в каталог' })
    expect(http.post).toHaveBeenCalledExactlyOnceWith('/core/providers/provider-1/subscription/preview', {
      revision: 7, content: 'fixture-subscription-content', dial_ips: {},
    })
    expect(source).toHaveValue('')
    expect(screen.getByText(/Строка 2 · Дубликат/)).toBeInTheDocument()
    await waitFor(() => expect(confirm).toBeEnabled())
    fireEvent.click(confirm)
    expect(await within(screen.getByRole('dialog', { name: 'Импорт подписки VLESS' })).findByText('Выходов добавлено в каталог: 1.')).toBeInTheDocument()
    expect(http.post).toHaveBeenLastCalledWith('/core/providers/provider-1/subscription/commit', {
      revision: 7, preview_id: 'preview-1',
    })
    expect(http.post).toHaveBeenCalledTimes(2)
  })

  it('adds a manually entered Reality exit to the provider without selecting it', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: {
      revision: 4, vless: null, clients: [], rules: [], resolvers: [], agent: null,
      providers: [{ id: 'provider-1', name: 'VPS' }], saved_egresses: [], selected_egress_id: null,
    } })
    vi.mocked(http.post).mockReset().mockResolvedValue({ data: { id: 'new-exit' } })
    render(<QueryClientProvider client={new QueryClient()}><WorkingCore /></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Добавить выход' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Добавить VLESS' }))
    const mode = await screen.findByRole('combobox', { name: 'Способ добавления выхода' })
    const form = within(mode.closest('form')!)
    choose('Способ добавления выхода', 'Ручной ввод')
    choose('Защита VLESS', 'TCP/Reality')
    const values = {
      Название: 'Test exit',
      'IP выхода': '192.0.2.20',
      'Имя сервера (SNI)': 'egress.example.test',
      'UUID клиента': 'manual-test-id',
      'Публичный ключ Reality': 'B'.repeat(43),
      'Short ID (если задан)': 'aabb',
    }
    for (const [label, value] of Object.entries(values)) {
      fireEvent.change(form.getByLabelText(label, { exact: true }), { target: { value } })
    }
    choose('Провайдер', 'VPS')
    choose('Fingerprint', 'firefox')
    choose('Flow', 'xtls-rprx-vision')
    fireEvent.click(form.getByRole('button', { name: 'Сохранить выход' }))
    await waitFor(() => expect(http.post).toHaveBeenCalledExactlyOnceWith('/core/egresses', {
      revision: 4, name: 'Test exit', provider_id: 'provider-1',
      config: {
        address: '192.0.2.20', port: 443, server_name: 'egress.example.test', client_id: 'manual-test-id',
        reality: { public_key: 'B'.repeat(43), short_id: 'aabb', fingerprint: 'firefox', flow: 'xtls-rprx-vision' },
      },
    }))
  })

  it('assigns a saved exit to a provider without applying traffic', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: {
      revision: 4, vless: null, clients: [], rules: [], resolvers: [], agent: null,
      providers: [{ id: 'provider-1', name: 'VPS' }],
      saved_egresses: [{ id: 'exit-1', name: 'Amsterdam', provider_id: null }], selected_egress_id: null,
    } })
    vi.mocked(http.put).mockReset().mockResolvedValue({ data: null })
    vi.mocked(http.post).mockReset()
    render(<QueryClientProvider client={new QueryClient()}><WorkingCore /></QueryClientProvider>)
    await screen.findByRole('combobox', { name: 'Провайдер выхода «Amsterdam»' })
    choose('Провайдер выхода «Amsterdam»', 'VPS')
    await waitFor(() => expect(http.put).toHaveBeenCalledWith('/core/egresses/exit-1/provider', { revision: 4, provider_id: 'provider-1' }))
    expect(http.post).not.toHaveBeenCalled()
  })

  it('selects a saved exit in the draft without applying it', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: {
      revision: 9, vless: null, clients: [], rules: [], resolvers: [], agent: null,
      saved_egresses: [{ id: 'nl-id', name: 'VPS NL' }], selected_egress_id: null,
    } })
    vi.mocked(http.post).mockReset().mockResolvedValue({ data: null })
    render(<QueryClientProvider client={new QueryClient()}><WorkingCore /></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Действия выхода VPS NL' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Выбрать' }))
    await waitFor(() => expect(http.post).toHaveBeenCalledWith('/core/egresses/nl-id/select', { revision: 9 }))
    expect(http.post).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Выход выбран в черновике. Проверьте и примените изменения.')).toBeInTheDocument()
  })

  it('requires an operator to review and apply the same saved draft', async () => {
    vi.mocked(http.get).mockResolvedValueOnce({
      data: {
        revision: 4,
        vless: { address: '192.0.2.20', port: 8443, server_name: 'egress.poc.test' },
        clients: [],
        rules: [{ destination: '198.51.100.10/32', port: 18080, egress: 'direct' }],
        resolvers: ['198.51.100.53'],
        agent: { active_generation: 7, last_known_good_generation: 7, pending_generation_id: null },
        sync_state: 'changes', sync_sections: ['destination-policy'], sync_diff_available: true,
      },
    }).mockResolvedValue({ data: {
      revision: 4, vless: { address: '192.0.2.20', port: 8443, server_name: 'egress.poc.test' },
      clients: [], rules: [{ destination: '198.51.100.10/32', port: 18080, egress: 'direct' }], resolvers: [],
      agent: { active_generation: 7, last_known_good_generation: 7, pending_generation_id: null },
      sync_state: 'synced', sync_sections: [], sync_diff_available: true,
    } })
    vi.mocked(http.post).mockReset()
      .mockResolvedValueOnce({ data: { revision: 4, base_generation: 7, sections: ['destination-policy'] } })
      .mockResolvedValueOnce({ data: { active_generation: 8 } })
    const queryClient = new QueryClient()
    render(<QueryClientProvider client={queryClient}><WorkingCore /></QueryClientProvider>)

    const preview = await screen.findByRole('button', { name: 'Посмотреть изменения' })
    expect(screen.queryByRole('button', { name: 'Применить конфигурацию' })).toBeNull()
    fireEvent.click(preview)
    const apply = await screen.findByRole('button', { name: 'Применить конфигурацию' })
    expect(http.post).toHaveBeenCalledExactlyOnceWith('/core/preview')
    expect(screen.getByText('Правила маршрутизации')).toBeInTheDocument()
    await waitFor(() => expect(apply).toBeEnabled())
    fireEvent.click(apply)
    expect(await screen.findByText('Конфигурация применена и подтверждена. Контрольные пробы прошли; новое поколение сохранено как LKG.')).toBeInTheDocument()
    expect(http.post).toHaveBeenLastCalledWith('/core/apply', { revision: 4, base_generation: 7 })
    expect(await screen.findByText('Все изменения применены')).toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: 'Неприменённые изменения' })).toBeNull()
  })

  it('loads synchronized state after a page reload without showing the draft bar', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: {
      revision: 12, vless: { address: '192.0.2.20', port: 8443, server_name: 'egress.poc.test' },
      clients: [], rules: [], resolvers: [], selected_egress_id: 'e1',
      saved_egresses: [{ id: 'e1', name: 'Applied exit', provider_id: null }],
      agent: { active_generation: 4, last_known_good_generation: 4, active_egress_id: 'e1', pending_generation_id: null },
      sync_state: 'synced', sync_sections: [], sync_diff_available: true,
    } })
    render(<QueryClientProvider client={new QueryClient()}><WorkingCore /></QueryClientProvider>)
    expect(await screen.findByText('Все изменения применены')).toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: 'Неприменённые изменения' })).toBeNull()
    const catalog = screen.getByRole('region', { name: 'Каталог выходов' })
    expect(within(catalog).getByText('Активен')).toBeInTheDocument()
    expect(within(catalog).queryByText('Выбран для применения')).toBeNull()
  })

  it('never claims synchronization when Agent is unavailable', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: {
      revision: 2, vless: null, clients: [], rules: [], resolvers: [], selected_egress_id: null,
      agent: null, agent_error: 'Agent недоступен', sync_state: 'agent_unavailable',
      sync_sections: [], sync_diff_available: false,
    } })
    render(<QueryClientProvider client={new QueryClient()}><WorkingCore /></QueryClientProvider>)
    expect((await screen.findAllByText('Agent недоступен')).length).toBeGreaterThan(0)
    expect(screen.queryByText('Все изменения применены')).toBeNull()
    expect(screen.queryByRole('complementary', { name: 'Неприменённые изменения' })).toBeNull()
  })

  it('drops the synchronized claim when a later Agent status refresh fails', async () => {
    vi.mocked(http.get).mockResolvedValueOnce({ data: {
      revision: 2, vless: { address: '192.0.2.20', port: 8443, server_name: 'egress.poc.test' },
      clients: [], rules: [], resolvers: [], selected_egress_id: null,
      agent: { active_generation: 1, last_known_good_generation: 1, pending_generation_id: null },
      sync_state: 'synced', sync_sections: [], sync_diff_available: true,
    } }).mockRejectedValueOnce(new Error('status refresh failed'))
    render(<QueryClientProvider client={new QueryClient()}><WorkingCore /></QueryClientProvider>)
    expect(await screen.findByText('Все изменения применены')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Обновить статус' }))
    expect(await screen.findByText('Состояние конфигурации неизвестно')).toBeInTheDocument()
    expect((await screen.findAllByText('Agent недоступен')).length).toBeGreaterThan(0)
    expect(screen.queryByText('Все изменения применены')).toBeNull()
  })

  it('keeps an apply failure visible after refreshing Agent state', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: {
      revision: 2, vless: { address: '192.0.2.20', port: 8443, server_name: 'egress.poc.test' },
      clients: [], rules: [], resolvers: [], selected_egress_id: null,
      agent: { active_generation: 1, last_known_good_generation: 1, pending_generation_id: null },
      sync_state: 'changes', sync_sections: ['destination-policy'], sync_diff_available: true,
    } })
    vi.mocked(http.post).mockReset()
      .mockResolvedValueOnce({ data: { revision: 2, base_generation: 1, sections: ['destination-policy'] } })
      .mockRejectedValueOnce(Object.assign(new Error('Apply failed'), {
        isAxiosError: true, response: { data: { detail: 'Apply failed and was rolled back.' } },
      }))
    render(<QueryClientProvider client={new QueryClient()}><WorkingCore /></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Посмотреть изменения' }))
    const apply = await screen.findByRole('button', { name: 'Применить конфигурацию' })
    await waitFor(() => expect(apply).toBeEnabled())
    fireEvent.click(apply)
    expect(await screen.findByText('Ошибка применения')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('Apply failed and was rolled back.')
  })
})
