# TEMPEST — статус форка

## 2026-11-10 · этап 0

**ACCEPTED пользователем.**

- Исходный PiTun v1.7.8 — `b75223d621395e7b641a77bcff61cd8647deb207`. Fork parent, upstream master и tag сверены. Сохраняем FastAPI, React, SQLite, генерацию Xray и upstream-тесты. [Baseline и команды](UPSTREAM_BASELINE.md).
- Сохраняем шаблоны управляемого proxy routing, diagnostics, snapshot/watchdog. Для будущего MVP исключаем из доверенной зоны API системный DNS, WAN/DHCP/Wi-Fi, Docker lifecycle, SSH deploy и управление внешними панелями. [Карта привилегий](HOST_PRIVILEGE_AUDIT.md).
- Локальные WG/AWG/VLESS ingress в PiTun отсутствуют. Критические разрывы — привилегии API, неявный DIRECT, схема TUN и качество UDP health signal. [Матрица](TEMPEST_GAP_MATRIX.md).
- ADR-001 остаётся **PROPOSED**: выбор host agent vs netns зависит от Linux VM тестов. [ADR-001](adr/ADR-001-privilege-separation.md).
- Baseline: backend 294 passed / 1 skipped / 1 deselected в выбранном наборе; frontend 142 passed и build успешен. На момент этапа 0 ESLint ещё не был настроен.

## 2026-11-10 · этап 1

**ACCEPTED владельцем** по успешному [GitHub Actions run #5](https://github.com/GariestGary/tempest/actions/runs/42783278332), commit `38fec1220c250b3b398e9160842e301d92d9d356`. Run #5 завершил все jobs успешно, включая backend/frontend/static Compose и hosted Linux runtime smoke Gate 1. Актуальный статус этапа 2 приведён ниже.

- Добавлен отдельный `docker-compose.dev.safe.yml`: непривилегированные UID, read-only root filesystems, `cap_drop: ALL`, loopback-only port mappings, отдельный DB volume; без host network/PID, Docker socket и системных host mounts. `docker compose config` и статический аудит параметров прошли.
- `DEV_SAFE_MODE=1` останавливает штатный lifecycle после локальной SQLite и defaults. Ревью проверило все зарегистрированные HTTP routes/WebSocket deny boundary, lifecycle startup hooks/background tasks, dataplane guards и закрытые backup restore/network/system operations. Исправленные regression tests прошли: backend run #4 дал 1736 passed, 2 skipped.
- Задача 1.1 закрыта: пользовательский UI, API metadata, frontend package и dev docs используют TEMPEST. `LICENSE`, upstream URL/history, legacy `pitun_token`, `pitun-routing` и API schema fields оставлены для совместимости/атрибуции. Массового переименования внутренних модулей нет.
- Документированы команды запуска/остановки/reset и профиль отдельной Linux VM. VM не создана: это изменило бы сетевую конфигурацию TORNADO; WSL inventory завершился `E_ACCESSDENIED`.
- Frontend: 142 passed; build/typecheck и lint проходят. Compose static config/security audit, shell syntax, Python `py_compile` и `git diff --check` проходят. Hosted backend run #4: 1736 passed, 2 skipped. Предупреждения: Vite native config loader/крупный chunk, upstream Pydantic/Starlette deprecations.
- Loopback Python API и Vite UI запускались, login, read endpoints, DB-only Naive node creation, denied `/system/start` (423) и UI proxy прошли. Процессы остановлены, порты 18000/13000 больше не слушают.
- Диагностика Docker была только read-only: клиент 29.1.2 есть, engine pipe `//./pipe/docker_engine` отсутствует, служба `com.docker.service` остановлена с типом запуска Manual, процессов Docker Desktop нет. `WslService` работает, но WSL inventory возвращает `E_ACCESSDENIED`; чтение Docker config также запрещено. Поэтому подтверждена непосредственная причина — daemon недоступен/не запущен; почему Docker Desktop его не поднял, установить не удалось. Ничего не запускалось и системные настройки не менялись.
- Runtime smoke run [#5](https://github.com/GariestGary/tempest/actions/runs/42783278332) принят владельцем: build/up, API/UI health, login, DB-only CRUD, deny checks, restart с сохранением записи и clean teardown; security inspection и network before/up/teardown diff прошли. Предыдущий подробный allowlisted отчет [run #4 artifact](https://github.com/GariestGary/tempest/actions/runs/42781710707/artifacts/13746286950) содержит runtime inspect, логи и сетевой diff. Run #5 и его commit `38fec1220c250b3b398e9160842e301d92d9d356` являются основанием приёмки Gate 1.
- Сетевой diff run #4: `before → after-up` добавились Docker-managed Compose bridge/veth/routes и Docker NAT/filter/raw rules для loopback-портов 18000/13000. `after-up → after-teardown` Compose bridge/veth/routes и правила публикации портов удалены; default/external routes, `ip rule` и resolver hash не изменились. После teardown остались только Docker Engine общие следы runner: `docker0` получил IPv6 link-local, пустая `ip raw` table и счётчики nftables изменились. Это Docker-managed состояние; изменений сетевой конфигурации непосредственно TEMPEST не обнаружено.
- Git workflow: только `master`, direct commits, без feature branches/PR без отдельного запроса; GitHub Actions запускается на каждый `push` в `master`. Неудачные runs #2/#3 приложили отчёты, и cleanup выполнялся при обеих ошибках.
- Проверки: Bash `-n`, workflow YAML/master-trigger assertions, sanitizer checks (пароли, bearer/JWT, URL credentials и IP), mocked cleanup-on-error, `docker compose config --quiet`, `git diff --check`; frontend 142 теста, lint/build; backend 1736 passed/2 skipped; hosted Compose runtime smoke — pass.

## 2026-11-10 · этап 2, задача 2.1 — ACCEPTED

**Этап 2 `IN_PROGRESS`; задача 2.1 `ACCEPTED` владельцем** после ревью `be061d953c52e25da8338d545d117fdbb1d9da24` и успешного CI run #10. Добавлены versioned typed `NetworkPlan`, строгий JSON IPC контракт и privilege-free mock Agent. Предыдущая hosted проверка: [Actions run #9](https://github.com/GariestGary/tempest/actions/runs/42814005624) (backend 1764 passed / 2 skipped, frontend 142 passed + lint/build, safe Compose smoke); [CodeQL run #9](https://github.com/GariestGary/tempest/actions/runs/42814005724). Задача 2.2 — единственная текущая авторизация.

Задача 2.1 принята; Gate 2 остаётся непринятым. AF_UNIX server/peer authentication, durable journal, Linux namespace A/B experiment и nftables/WireGuard apply не входили в 2.1. ADR-001 остаётся `PROPOSED`. Python на TORNADO не запускается из-за недоступного Windows Store runtime; backend проверен hosted runner. Сетевые настройки TORNADO/VORTEX не менялись.

- Базовый кандидат — dataplane/Agent в заранее подготовленном отдельном Linux network namespace; `CAP_NET_ADMIN` проверяется только внутри этого namespace. Host-netns systemd Agent — резервный вариант, если эксперимент докажет конкретную host-only операцию. Control Plane (FastAPI/React/SQLite) остаётся непривилегированным и без сетевых системных вызовов.
- Технический контракт и A/B рецепт Linux-only эксперимента: [NetworkPlan / IPC и эксперимент](NETWORK_PLAN_IPC.md). Mock исполняет только in-memory state machine; сетевые syscalls отсутствуют.
- Run #5 и commit приёмки этапа 1: [Actions](https://github.com/GariestGary/tempest/actions/runs/42783278332), `38fec1220c250b3b398e9160842e301d92d9d356`.

ADR-001 и выбор netns остаются открытыми; реальные nftables/WG правила не запускались.

## 2026-11-10 · этап 2, задача 2.2 — ACCEPTED

Владелец принял задачу 2.2 после проверки commit `91fb2c63a5204b1a16fb960a718108aae5e4d3e9` и успешного GitHub CI. Этап 2 остаётся `IN_PROGRESS`, Gate 2 не принят. Текущая авторизация — только задача 2.3.

Добавлены самостоятельный `python -m app.network_agent` AF_UNIX listener и systemd unit. Транспорт использует 4-byte length-prefixed framing (64 KiB maximum), read/write timeout, `SO_PEERCRED` UID/GID allowlist, optional cgroup match, сериализованную обработку клиентов и bounded 4096-ID process-local replay guard с fail-closed при заполнении. Действия логируются с kernel peer IDs без тел запросов. Состояние остаётся in-memory mock; все сетевые операции — no-op. `Confirm` заново проверяет целостность pending snapshot и не продвигает повреждённый candidate в last-known-good.

Финальный [CI run #13](https://github.com/GariestGary/tempest/actions/runs/42820848020) на commit `6e603460266180a7f5571ab33578911b02bbb846` успешен: backend 1781 passed / 2 skipped / 32 warnings; целевой Linux IPC/sandbox test 45 passed; frontend 142 passed, lint/build; safe Compose static/runtime checks. [CodeQL run #13](https://github.com/GariestGary/tempest/actions/runs/42820848049): Python и JavaScript/TypeScript успешны. Run #11 завершился одним неверным ожиданием теста на EOF против Linux `ECONNRESET`; тест скорректирован, итоговый run #13 зелёный.

Linux CI на ephemeral Ubuntu 24.04 runner проверил AF_UNIX connect/deny через реальные `SO_PEERCRED`, UID/GID allowlist, kernel identity в log, JSON identity spoof rejection, framing/oversize, timeout, lost response/retry, replay, конкурирующих клиентов, standalone lifecycle и systemd sandbox declarations. CI не запускал systemd unit, не менял capabilities/network namespaces и не применял сетевые операции. Локальный Python недоступен на TORNADO из-за отказа Microsoft Store runtime.

Не проверены на выделенной Linux VM: effective capability masks работающего systemd unit, реальное взаимодействие `NetworkNamespacePath`, user-namespace/cgroup mapping production контейнера, runtime systemd sandbox enforcement и A/B namespace/host-netns experiments. Их нельзя выводить из статических unit assertions. Unit не устанавливался на TORNADO/VORTEX; сетевое состояние пользовательских машин не менялось. Durable replay journal/process-restart recovery, nftables/WireGuard/TUN и routing не входят в этот срез; ADR-001 остаётся `PROPOSED`, Gate 2 и весь этап 2 не приняты.

## 2026-11-11 · этап 2, задача 2.3 — IN_PROGRESS

Разрешена только задача 2.3. [Карта миграции](TASK_2_3_CALL_MAP.md) фиксирует сетевые вызовы API, менеджеров, startup/shutdown и scheduler, делит их на необходимые, legacy и опасные. Первый срез вводит явно включаемый `CONTROL_PLANE_MODE=agent`: типизированные AF_UNIX вызовы `NetworkPlan` validate/diff/status; остальной API, WebSocket, operational startup/shutdown и dataplane apply закрыты явным отказом. Legacy PiTun профиль сохранён как текущая совместимость; dev-safe Compose не менялся. Реальный apply не реализован, задача 2.3 **не завершена**.

Риски Agent остаются открытыми: ограниченный process-local replay cache, отсутствие durable recovery, непроверенный runtime systemd unit и UID/cgroup mapping. На TYPHOON не устанавливался Agent и не менялись сеть, DNS, маршруты, firewall или VPN. Локальный Python 3.14 не имеет зависимостей проекта; установка через `uv` не удалась из-за DNS к PyPI. Статическая компиляция и `git diff --check` прошли.

После первоначального CI run новый тест ошибочно разбирал пустой `HEAD` response как JSON (остальные 1787 backend tests прошли); исправлено в `65f53cc785ce028d89c1e254170df411db05de69`, а режим Agent теперь включается до startup тестового клиента. Итоговый [CI run](https://github.com/GariestGary/tempest/actions/runs/43089098227): targeted Agent/NetworkPlan suite 52 passed; полный backend 1788 passed / 2 skipped / 32 warnings; frontend 142 passed, lint/build; safe Compose config/runtime smoke — pass. [CodeQL run](https://github.com/GariestGary/tempest/actions/runs/43089098133) — success. Кодовый срез: `49c0a4c31e9dece6be45a3a0a5d48cfc94b50469` + исправление теста `65f53cc785ce028d89c1e254170df411db05de69`. Это review первой части 2.3, не завершение задачи или Gate 2.

## 2026-11-12 · этап 2, задача 2.3 — DB-derived NetworkPlan inspection

Вынесен чистый builder `backend/app/network_agent/plan_builder.py`: enabled legacy Node IDs/protocol metadata превращаются в typed v1 egress candidates без имён, адресов и credentials; поколение основано на Agent status. Builder добавляет только обязательный `protected_ingress` block, не создаёт route/interface/allow policy и не разрешает DIRECT. Добавлены `/api/network-plan/current`, `/current/validate`, `/current/diff`; typed client проверяет форму результата и выдаёт отдельную ошибку на Agent rejection. Прямой apply не добавлен, Agent-mode middleware/startup refusals сохранены.

Первый [Actions run #19](https://github.com/GariestGary/tempest/actions/runs/43180280515) обнаружил ошибку fixture ID в regression test; fixture исправлен в `1ebf6531132aacf8be8f0ebc23181eb94ddfb1c8`. Итоговый [run #20](https://github.com/GariestGary/tempest/actions/runs/43180560494) успешен: targeted 70 passed; backend 1806 passed / 2 skipped / 32 warnings; frontend checks, safe Compose config/runtime и CodeQL successful. Коммиты: `2e27792c2463d1a64bb9add17d4f712661d9df28`, `1ebf6531132aacf8be8f0ebc23181eb94ddfb1c8`. TORNADO Python/pytest недоступны; локально прошёл `git diff --check`. Реальные сетевые mutations, Agent apply/recovery, VM namespace/capability/ownership испытания не выполнялись; 2.3 остаётся `IN_PROGRESS`, Gate 2 не принят.
