import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { http } from '@/api/client'
import { WorkingCore } from '@/pages/WorkingCore'

vi.mock('@/api/client', () => ({
  http: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}))

describe('Working Core panel', () => {
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
    fireEvent.click(await screen.findByText('Импорт подписки VLESS'))
    fireEvent.change(screen.getByLabelText('Провайдер для импорта'), { target: { value: 'provider-1' } })
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
    expect(await screen.findByText('Выходов добавлено в каталог: 1.')).toBeInTheDocument()
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
    const mode = await screen.findByLabelText('Способ добавления выхода')
    const form = within(mode.closest('form')!)
    fireEvent.change(mode, { target: { value: 'manual' } })
    fireEvent.change(form.getByLabelText('Защита VLESS'), { target: { value: 'reality' } })
    const values = {
      Название: 'Test exit',
      Провайдер: 'provider-1',
      'IP выхода': '192.0.2.20',
      'Имя сервера (SNI)': 'egress.example.test',
      'UUID клиента': 'manual-test-id',
      'Публичный ключ Reality': 'B'.repeat(43),
      'Short ID (если задан)': 'aabb',
      Fingerprint: 'firefox',
      Flow: 'xtls-rprx-vision',
    }
    for (const [label, value] of Object.entries(values)) {
      fireEvent.change(form.getByLabelText(label, { exact: true }), { target: { value } })
    }
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
    fireEvent.change(await screen.findByLabelText('Провайдер выхода «Amsterdam»'), { target: { value: 'provider-1' } })
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
    fireEvent.click(await screen.findByRole('button', { name: 'Выбрать' }))
    await waitFor(() => expect(http.post).toHaveBeenCalledWith('/core/egresses/nl-id/select', { revision: 9 }))
    expect(http.post).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Выход выбран в черновике. Проверьте и примените изменения.')).toBeInTheDocument()
  })

  it('requires an operator to review and apply the same saved draft', async () => {
    vi.mocked(http.get).mockResolvedValue({
      data: {
        revision: 4,
        vless: { address: '192.0.2.20', port: 8443, server_name: 'egress.poc.test' },
        clients: [],
        rules: [{ destination: '198.51.100.10/32', port: 18080, egress: 'direct' }],
        resolvers: ['198.51.100.53'],
        agent: { active_generation: 7, last_known_good_generation: 7, pending_generation_id: null },
      },
    })
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
    expect(await screen.findByText('Конфигурация применена. Контрольные пробы WireGuard прошли.')).toBeInTheDocument()
    expect(http.post).toHaveBeenLastCalledWith('/core/apply', { revision: 4, base_generation: 7 })
  })
})
