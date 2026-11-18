import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { http } from '@/api/client'
import { WorkingCore } from '@/pages/WorkingCore'

vi.mock('@/api/client', () => ({
  http: { get: vi.fn(), post: vi.fn() },
}))

describe('Working Core panel', () => {
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
    vi.mocked(http.post)
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
