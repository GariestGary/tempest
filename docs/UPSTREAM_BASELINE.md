# PiTun v1.7.8 — исходный baseline TEMPEST

Аудит от 2026-11-10. Объект аудита — неизменённое дерево PiTun. План этапа 0 состоял из сверки Git, трассировки путей от API и запуска приложения до системных операций, сопоставления с требованиями TEMPEST и безопасных локальных проверок.

## Происхождение

| Что сверено | Результат |
|---|---|
| Репозиторий и родитель | `origin` — [GariestGary/tempest](https://github.com/GariestGary/tempest), `upstream` — [DaveBugg/PiTun](https://github.com/DaveBugg/PiTun). Метаданные публичной страницы форка указывают на `DaveBugg/PiTun` как parent/network root. |
| Исходный commit | `b75223d621395e7b641a77bcff61cd8647deb207`. Перед аудитом на нём стояли `docs/phase-00-baseline` и локальная `master`. `git ls-remote` подтвердил тот же SHA для `upstream/master`, `origin/master` и peeled-тега `v1.7.8^{}`. SHA объекта аннотированного тега — `c3e4680c10ceea2ed42c6fad8b655a276c0cce33`. |
| Версия | `1.7.8` в [backend/app/config.py](../backend/app/config.py#L8), [frontend/package.json](../frontend/package.json) и [CHANGELOG.md](../CHANGELOG.md). |
| Право на форк | [LICENSE](../LICENSE) — BSD-3-Clause, copyright 2026 PiTun contributors. История, LICENSE, upstream changelog, acknowledgements из [README](../README.md#acknowledgements) и уведомление шрифта сохранены. |

Сверка без записи remote-tracking refs и без изменений на GitHub.

```powershell
git remote -v
git rev-parse HEAD
git ls-remote upstream refs/heads/master refs/tags/v1.7.8 'refs/tags/v1.7.8^{}'
git ls-remote origin refs/heads/master
```

## Структура и зависимости

| Область | Зафиксированное состояние |
|---|---|
| `backend/` | Python 3.11 в CI/образе. FastAPI 0.111.1, Uvicorn 0.48.0, SQLModel 0.0.19, Alembic 1.18.4, aiosqlite 0.22.1, Pydantic 2.13.4, HTTPX 0.28.1, python-jose, bcrypt, psutil, Docker SDK 7.1.0, AsyncSSH 2.24.0. 33 файла миграций, 88 файлов тестов. Прямые версии в [requirements.txt](../backend/requirements.txt), pytest 9.0.3 и pytest-asyncio 1.4.0 в [requirements-dev.txt](../backend/requirements-dev.txt). У psutil нижняя граница, транзитивные зависимости разрешаются pip. |
| `frontend/` | React 19, TypeScript 5, Vite 8, Tailwind 4, React Query 5, Zustand 5, Axios, Recharts, QR. Vitest 4, Testing Library, jsdom. 22 файла тестов. CI/образ используют Node 20. Точные версии npm фиксирует [package-lock.json](../frontend/package-lock.json). |
| Dataplane | [backend/Dockerfile](../backend/Dockerfile#L5) загружает Xray-core `26.10.31` с SHA-256 для amd64/arm64/arm. Linux-утилиты включают nftables, iproute2, curl, ping и сканирование LAN. GeoIP/GeoSite загружаются отдельно и хранятся на хосте в `/usr/local/share/xray`. |
| Сервисы | [docker-compose.yml](../docker-compose.yml) — FastAPI + Xray в backend, nginx для SPA и отдельный edge nginx на 80/443. [docker/](../docker/) — образы dnsmasq (~2.90), hostapd (~2.10), NaiveProxy (v154.0.8037.49-5). Sidecars поднимаются через Docker API. Отдельный dev Compose запускает тот же Python lifecycle с `XRAY_BINARY=/bin/false`. |
| Эксплуатация | [install.sh](../install.sh), [scripts/](../scripts/), [nginx.conf](../nginx.conf), [docs/](.) и [.github/workflows/](../.github/workflows/). CI проверяет pytest, Vitest и сборку TypeScript/Vite. CodeQL отдельно. Release workflow собирает артефакты по тегу. |

BSD-3-Clause позволяет сохранить код и историю PiTun в TEMPEST при сохранении текста лицензии и уведомлений в исходниках и материалах бинарных поставок. Уведомления не дают права заявлять об одобрении проекта авторами PiTun. В runtime-части npm lock 81 запись: 66 MIT, 13 ISC, одна BSD-3-Clause, одна MIT AND ISC. У шрифта [собственное уведомление](../frontend/src/assets/fonts/TwemojiCountryFlags.LICENSE.md). Код Xray-core лицензирован по [MPL-2.0](https://github.com/XTLS/Xray-core/blob/v26.10.31/LICENSE), dnsmasq — по [GPL v2 или v3](https://thekelleys.org.uk/dnsmasq/doc.html#license), hostapd — по [BSD](https://w1.fi/hostapd/), у NaiveProxy отдельная [BSD-лицензия и уведомления Chromium](https://github.com/klzgrad/naiveproxy/blob/master/LICENSE). Эти программы остаются отдельными компонентами со своими условиями распространения. Для образа релиза ещё нужна проверка фактически вложенных файлов, notices, системных и транзитивных пакетов, бинарных артефактов и геобаз. Сторонний код в этапе 0 не добавлялся.

## Карта кода

| Подсистема | Точка входа, путь данных, проверки |
|---|---|
| API и auth | [main.py](../backend/app/main.py#L386) подключает FastAPI routers и Bearer-auth. [api/auth.py](../backend/app/api/auth.py), [core/auth.py](../backend/app/core/auth.py) — bcrypt, JWT, лимит попыток входа в памяти процесса. [test_auth.py](../backend/tests/test_auth.py). |
| Модели и миграции | [models.py](../backend/app/models.py), [schemas.py](../backend/app/schemas.py), [database.py](../backend/app/database.py), [alembic/versions](../backend/alembic/versions), [entrypoint.sh](../backend/entrypoint.sh). SQLModel/SQLite — исходное состояние, entrypoint применяет Alembic; тестовые фикстуры создают таблицы через metadata и не прогоняют историю миграций. |
| UI | [App.tsx](../frontend/src/App.tsx) → [страницы](../frontend/src/pages) Login, Dashboard, Nodes, Subscriptions, Routing, Devices, DNS, Balancers, NodeCircles, Router, Diagnostics, Settings, Servers, Xui и др. [api/client.ts](../frontend/src/api/client.ts), [hooks](../frontend/src/hooks), [store](../frontend/src/store), [RU/EN](../frontend/src/hooks/useT.ts). |
| Узлы и подписки | [api/nodes.py](../backend/app/api/nodes.py), [api/subscriptions.py](../backend/app/api/subscriptions.py), [uri_parser.py](../backend/app/core/uri_parser.py), [sub_scheduler.py](../backend/app/core/sub_scheduler.py). Импорт формирует записи `Node`; CRUD и обновление подписки могут синхронизировать dataplane. [test_nodes.py](../backend/tests/test_nodes.py), [test_subscriptions.py](../backend/tests/test_subscriptions.py). |
| Конфигурация Xray | [system._regenerate_and_write](../backend/app/api/system.py#L1421) собирает DB state → [config_gen.py](../backend/app/core/config_gen.py) формирует inbounds/outbounds/rules/DNS и валидирует → [dataplane.py](../backend/app/core/dataplane.py#L61), [xray.py](../backend/app/core/xray.py), [xray_api.py](../backend/app/core/xray_api.py) ведут конфиг, процесс и отдельные runtime-вызовы. [test_config_gen.py](../backend/tests/test_config_gen.py), [test_dataplane_sync.py](../backend/tests/test_dataplane_sync.py). |
| Маршрутизация | [api/routing.py](../backend/app/api/routing.py), [api/routing_sets.py](../backend/app/api/routing_sets.py), [core/route_explain.py](../backend/app/core/route_explain.py#L240) — упорядоченные правила, sets по устройствам, импорт/экспорт, статическое объяснение и опциональная проба через временный Xray. MAC/source выбирает TPROXY inbound, `inboundTag` привязывает к нему правила, затем проверяются глобальные правила. |
| TPROXY и TUN | [system._apply_nftables](../backend/app/api/system.py#L393) → [nftables.py](../backend/app/core/nftables.py) и [tun.py](../backend/app/core/tun.py); nftables и policy routing в production относятся к host namespace. [test_nftables_routing_sets.py](../backend/tests/test_nftables_routing_sets.py) перехватывает команды и проверяет текст. |
| Устройства и router | [api/devices.py](../backend/app/api/devices.py), [device_scanner.py](../backend/app/core/device_scanner.py) — ARP/neighbour/scan, MAC/IP, include/exclude, назначения RoutingSet. [api/network.py](../backend/app/api/network.py) и [api/system.py](../backend/app/api/system.py#L1286) → [router_mode.py](../backend/app/core/router_mode.py#L327), [router_watchdog.py](../backend/app/core/router_watchdog.py), [wan.py](../backend/app/core/wan.py), [wifi.py](../backend/app/core/wifi.py), [dhcp.py](../backend/app/core/dhcp.py). |
| DNS | [api/dns.py](../backend/app/api/dns.py), [config_gen.py](../backend/app/core/config_gen.py), [dns_logger.py](../backend/app/core/dns_logger.py), [DNS.tsx](../frontend/src/pages/DNS.tsx) — Xray DNS, per-domain upstream, FakeDNS, sniffing, опциональный журнал. Host fallback DNS — отдельный вызов [network_apply.py](../backend/app/core/network_apply.py#L859). |
| Health/failover | [healthcheck.py](../backend/app/core/healthcheck.py#L97) — быстрый цикл для активного узла и отдельный полный обход. После серии отказов — подходящий NodeCircle, затем явный список fallback. [circle_scheduler.py](../backend/app/core/circle_scheduler.py), [api/balancers.py](../backend/app/api/balancers.py), [speedtest.py](../backend/app/core/speedtest.py), [autocheck_scheduler.py](../backend/app/core/autocheck_scheduler.py) — кандидаты, ротация и пробы через Xray. [test_healthcheck_udp.py](../backend/tests/test_healthcheck_udp.py), `test_circle_*.py`. |
| Docker | [naive_manager.py](../backend/app/core/naive_manager.py), [naive_supervisor.py](../backend/app/core/naive_supervisor.py), [dhcp.py](../backend/app/core/dhcp.py#L193), [wifi.py](../backend/app/core/wifi.py#L439), [resource_limits.py](../backend/app/core/resource_limits.py#L200) — управление sidecars, события Docker, изменения лимитов. |
| Удалённые серверы | [api/servers.py](../backend/app/api/servers.py#L230) → [deploy.py](../backend/app/core/deploy.py), [ssh.py](../backend/app/core/ssh.py), [jobs.py](../backend/app/core/jobs.py) → скрипты на целевом VPS. [api/server_clients.py](../backend/app/api/server_clients.py#L104) управляет WG peers через SSH; [api/xui.py](../backend/app/api/xui.py), [xui_api.py](../backend/app/core/xui_api.py) работают с удалённой панелью. |
| Backups и восстановление | [api/backup.py](../backend/app/api/backup.py#L59) — JSON v1 с выборочной маскировкой, preview и merge/restore; restore вызывает Xray/nftables. [network_apply.py](../backend/app/core/network_apply.py#L209) — отдельные snapshots сетевых файлов и gateway. [router_watchdog.py](../backend/app/core/router_watchdog.py#L195) — отмена неподтверждённого router mode, в том числе на boot. [pitun-update.sh](../scripts/pitun-update.sh#L351) — pre-update копии SQLite и управление релизом. |

Подробности в [карте привилегий](HOST_PRIVILEGE_AUDIT.md), [матрице требований](TEMPEST_GAP_MATRIX.md) и [ADR-001](adr/ADR-001-privilege-separation.md).

## Проверки и воспроизведение

Изучены [README](../README.md), [CONTRIBUTING](../CONTRIBUTING.md), [SECURITY](../SECURITY.md), [CI](../.github/workflows/ci.yml) и [pytest fixtures](../backend/tests/conftest.py). Для этапа 0 запуск ограничен проверками без управления сетью. Штатный pytest fixture использует временную SQLite и отключает значительную часть фоновых задач, но сам по себе не служит общей границей привилегий для всех тестов или произвольной dev БД.

| Проверка на TORNADO | Результат |
|---|---|
| Python 3.11, 15 выбранных файлов pytest, с заглушками host lifecycle и Docker SDK | **294 passed, 1 skipped, 1 deselected**. Пропуск — тест генерации сертификата без доступного `openssl`. Исключён реальный Xray config test. Предупреждение о deprecated API AnyIO в Starlette TestClient. |
| Node 24.14.0 / npm 11.9.0, `npm.cmd run test:ci` | **142 passed, 22 файла**. |
| `npm.cmd run build` | **Успех**. TypeScript и Vite. Предупреждения о будущем загрузчике конфигов Vite и крупном entry chunk (~1 МБ до gzip). |
| `npm.cmd run lint` | **Код 1**. В репозитории отсутствует конфигурация ESLint, поэтому CI намеренно пропускает этот шаг. |

В первом запуске pytest использовались только upstream fixtures. При проверке lifecycle обнаружилось, что `naive_manager.sync_all` пытается найти sidecars даже при пустой БД. Docker socket по default Unix-пути на TORNADO отсутствовал, `DOCKER_HOST` и локальные `.env` не были заданы. Результат воспроизведён с явным отключением этого пути и запретом Docker SDK. Ниже команда с дополнительными заглушками.

Из `backend/` на Windows, Python 3.11. Использовать чистое локальное окружение без боевых конфигов и credentials. При установке зависимостей нужен доступ к registry. В отчётном запуске каталог venv назывался `.venv-phase0`.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
@'
import os
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

os.environ.update(DATABASE_URL="sqlite://", SECRET_KEY="test-secret-key-for-pytest",
                  XRAY_BINARY="/bin/false")
import pytest

with ExitStack() as stack:
    for target in (
        "app.api.system.apply_system_toggles_on_boot",
        "app.core.router_mode.reconcile_on_boot",
        "app.core.router_watchdog.recover_on_boot",
        "app.core.naive_manager.naive_manager.sync_all",
        "app.main._reapply_limits",
    ):
        stack.enter_context(patch(target, new_callable=AsyncMock))
    stack.enter_context(patch("docker.DockerClient", side_effect=AssertionError("Docker disabled")))
    stack.enter_context(patch("docker.from_env", side_effect=AssertionError("Docker disabled")))
    raise SystemExit(pytest.main([
        "-p", "pytest_asyncio.plugin", "-q",
        "tests/test_schemas.py", "tests/test_database.py", "tests/test_config_gen.py",
        "tests/test_cert_pin.py", "tests/test_hy2_outbound.py", "tests/test_hy2_modes.py",
        "tests/test_healthcheck_udp.py", "tests/test_nftables_routing_sets.py",
        "tests/test_dns_doh_wireformat.py", "tests/test_host_fallback_dns.py",
        "tests/test_route_explain.py", "tests/test_chain_drift.py",
        "tests/test_circle_interval_matrix.py", "tests/test_circle_speed_gate.py",
        "tests/test_backup.py", "-k", "not real_xray",
    ]))
'@ | .\.venv\Scripts\python.exe -
```

Из `frontend/`.

```powershell
npm.cmd ci --ignore-scripts --legacy-peer-deps --no-audit --no-fund
npm.cmd run test:ci
npm.cmd run build
npm.cmd run lint
```

Полный upstream CI backend использует `python -m pytest tests/ -q` на Ubuntu. Shell suites вызывают через bash функции из installer/updater и в части тестов поднимают временные процессы или подменяют команды. Для полного повторения использовать обычного пользователя в чистой Linux VM без Docker socket, credentials и доступа к рабочим узлам, предварительно сверив shell fixtures. Сам installer, штатный lifecycle приложения, dev/production Compose и live E2E скрипты в baseline не запускались. На TORNADO и VORTEX сетевых мутаций не выполнялось, diff сетевого состояния не собирался.

Отдельно предстоит проверить миграции всей цепочки, `xray run -test` закреплённой сборкой, TUN/TPROXY/WG/AWG, реальный egress IP, IPv4/IPv6/DNS при отказах, rollback и чистое восстановление. Эти сценарии требуют выделенной Linux VM. Форк подтверждён как база для продолжения после приёмки этапа 0. Это решение опирается на историю, лицензию, пригодные к переиспользованию подсистемы и прошедшие локальные проверки.
