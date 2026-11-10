# TEMPEST — статус форка

## 2026-11-10 · этап 0

**ACCEPTED пользователем.**

- Исходный PiTun v1.7.8 — `b75223d621395e7b641a77bcff61cd8647deb207`. Fork parent, upstream master и tag сверены. Сохраняем FastAPI, React, SQLite, генерацию Xray и upstream-тесты. [Baseline и команды](UPSTREAM_BASELINE.md).
- Сохраняем шаблоны управляемого proxy routing, diagnostics, snapshot/watchdog. Для будущего MVP исключаем из доверенной зоны API системный DNS, WAN/DHCP/Wi-Fi, Docker lifecycle, SSH deploy и управление внешними панелями. [Карта привилегий](HOST_PRIVILEGE_AUDIT.md).
- Локальные WG/AWG/VLESS ingress в PiTun отсутствуют. Критические разрывы — привилегии API, неявный DIRECT, схема TUN и качество UDP health signal. [Матрица](TEMPEST_GAP_MATRIX.md).
- ADR-001 остаётся **PROPOSED**: выбор host agent vs netns зависит от Linux VM тестов. [ADR-001](adr/ADR-001-privilege-separation.md).
- Baseline: backend 294 passed / 1 skipped / 1 deselected в выбранном наборе; frontend 142 passed и build успешен. На момент этапа 0 ESLint ещё не был настроен.

## 2026-11-10 · этап 1

**ACCEPTED владельцем** по успешному [GitHub Actions run #5](https://github.com/GariestGary/tempest/actions/runs/42783278332), commit `38fec1220c250b3b398e9160842e301d92d9d356`. Run #5 завершил все jobs успешно, включая backend/frontend/static Compose и hosted Linux runtime smoke Gate 1. Этап 2 имеет статус `IN_PROGRESS`; задача 2.1 принята владельцем, выполняется только задача 2.2.

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

## 2026-11-10 · этап 2, задача 2.2 — IN_PROGRESS

Добавлены самостоятельный `python -m app.network_agent` AF_UNIX listener и systemd unit. Транспорт использует frame limit/timeouts, `SO_PEERCRED` UID/GID allowlist, optional cgroup match, последовательное обслуживание клиентов и bounded process-local replay guard. Действия логируются с kernel peer IDs без тел запросов. Состояние остаётся in-memory mock; все сетевые операции — no-op. `Confirm` повторно проверяет целостность pending snapshot.

Перед hosted Linux проверки локальный Python недоступен на TORNADO из-за отказа Microsoft Store runtime. Итоговый READY_FOR_REVIEW, CI/CodeQL результаты и SHA будут внесены после проверок. Effective capabilities запущенного systemd service, cgroup/user-namespace mapping и namespace behavior пока не проверялись; задач 2.3–2.8 работа не касается, ADR-001 остаётся `PROPOSED`.
