// Local-only visual review server. It never contacts a real TEMPEST API.
import http from 'node:http'
import { spawn } from 'node:child_process'

const mockPort = Number(process.env.TEMPEST_MOCK_API_PORT || 18322)
const uiPort = Number(process.env.TEMPEST_MOCK_UI_PORT || 5178)
const scenario = process.env.TEMPEST_MOCK_SCENARIO || 'normal'
const longName = 'Очень длинное название провайдера для проверки переноса и доступности элементов управления'
const state = {
  revision: 14,
  providers: [{ id: 'p1', name: scenario === 'long' ? longName : 'Northstar VPN' }, { id: 'p2', name: 'Личный сервер' }],
  saved_egresses: [{ id: 'e1', name: 'Frankfurt · Reality', provider_id: 'p2', security: 'reality' }],
  saved_awg_egresses: [{ id: 'e2', name: scenario === 'long' ? longName : 'Amsterdam · AWG', provider_id: 'p1' }, { id: 'e3', name: 'Helsinki · AWG', provider_id: 'p1' }],
  selected_egress_id: 'e2', vless: null,
  clients: [{ id: 'c1', name: 'Рабочий ноутбук', addresses: ['10.245.248.3/32'] }, { id: 'c2', name: 'Телефон', addresses: ['10.245.248.4/32'] }],
  rules: [{ destination: '192.0.2.0/24', port: 443, egress: 'vpn' }], default_egress: 'vpn', resolvers: ['1.1.1.1'],
  agent: { active_generation: 12, last_known_good_generation: 12, pending_generation_id: null, last_error: null, active_egress_id: 'e2', egress_state: 'active_verified' }, agent_error: null,
}
if (scenario === 'empty') Object.assign(state, { providers: [], saved_egresses: [], saved_awg_egresses: [], clients: [], rules: [], selected_egress_id: null })
const server = http.createServer((req, res) => {
  const reply = (status, body) => { res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(body)) }
  if (req.method === 'GET' && req.url === '/api/core') return scenario === 'error' ? reply(503, { detail: 'Тестовая ошибка API' }) : reply(200, state)
  if (req.method === 'POST' && req.url === '/api/auth/login') {
    const token = ['e30', Buffer.from(JSON.stringify({ exp: 4102444800 })).toString('base64url'), 'mock'].join('.')
    return reply(200, { access_token: token, token_type: 'bearer' })
  }
  if (req.method === 'POST' && req.url === '/api/core/preview') return reply(200, { revision: 14, base_generation: 12, sections: ['vless', 'destination-policy'] })
  if (req.method === 'POST' && req.url === '/api/core/awg-egresses/preview') return reply(200, { ipv6_configured: false })
  return reply(501, { detail: 'Тестовый режим: изменения не сохраняются и не применяются.' })
})
server.listen(mockPort, '127.0.0.1', () => {
  const vite = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(uiPort), '--strictPort'], {
    cwd: new URL('..', import.meta.url), stdio: 'inherit', env: { ...process.env, VITE_WORKING_CORE: '1', VITE_MOCK_PREVIEW: '1', VITE_DEV_PROXY_TARGET: `http://127.0.0.1:${mockPort}` },
  })
  vite.on('exit', code => { server.close(); process.exit(code || 0) })
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { vite.kill(signal); server.close() })
  console.log(`MOCK ONLY (${scenario}): http://127.0.0.1:${uiPort}/ — любой логин/пароль; изменения не сохраняются.`)
})
