# TEMPEST — статус форка

## 2026-11-10 · этап 0

**ACCEPTED пользователем.**

- Исходный PiTun v1.7.8 — `b75223d621395e7b641a77bcff61cd8647deb207`. Fork parent, upstream master и tag сверены. Сохраняем FastAPI, React, SQLite, генерацию Xray и upstream-тесты. [Baseline и команды](UPSTREAM_BASELINE.md).
- Сохраняем шаблоны управляемого proxy routing, diagnostics, snapshot/watchdog. Для будущего MVP исключаем из доверенной зоны API системный DNS, WAN/DHCP/Wi-Fi, Docker lifecycle, SSH deploy и управление внешними панелями. [Карта привилегий](HOST_PRIVILEGE_AUDIT.md).
- Локальные WG/AWG/VLESS ingress в PiTun отсутствуют. Критические разрывы — привилегии API, неявный DIRECT, схема TUN и качество UDP health signal. [Матрица](TEMPEST_GAP_MATRIX.md).
- ADR-001 остаётся **PROPOSED**: выбор host agent vs netns зависит от Linux VM тестов. [ADR-001](adr/ADR-001-privilege-separation.md).
- Baseline: backend 294 passed / 1 skipped / 1 deselected в выбранном наборе; frontend 142 passed и build успешен. На момент этапа 0 ESLint ещё не был настроен.

## 2026-11-10 · этап 1

**READY_FOR_REVIEW.** Этап 1 не принят; этап 2 остаётся `BLOCKED`. Задача 1.1 завершена. Dev-safe профиль подготовлен; GitHub Actions runtime smoke для hosted Ubuntu добавлен, но ещё ожидает первого запуска после push. Задача 1.6 и Gate 1 не закрыты до успешного CI run и review отчёта.

- Добавлен отдельный `docker-compose.dev.safe.yml`: непривилегированные UID, read-only root filesystems, `cap_drop: ALL`, loopback-only port mappings, отдельный DB volume; без host network/PID, Docker socket и системных host mounts. `docker compose config` и статический аудит параметров прошли.
- `DEV_SAFE_MODE=1` останавливает штатный lifecycle после локальной SQLite и defaults. Ревью проверило все зарегистрированные HTTP routes/WebSocket deny boundary, lifecycle startup hooks, dataplane guards и закрытые backup restore/network/system operations; для этого добавлены регрессионные тесты. Эти новые backend tests не запускались в текущей среде из-за отсутствующих Python-зависимостей.
- Задача 1.1 закрыта: пользовательский UI, API metadata, frontend package и dev docs используют TEMPEST. `LICENSE`, upstream URL/history, legacy `pitun_token`, `pitun-routing` и API schema fields оставлены для совместимости/атрибуции. Массового переименования внутренних модулей нет.
- Документированы команды запуска/остановки/reset и профиль отдельной Linux VM. VM не создана: это изменило бы сетевую конфигурацию TORNADO; WSL inventory завершился `E_ACCESSDENIED`.
- Frontend в текущем прогоне: 142 passed; build/typecheck и lint проходят. Compose static config/security audit, shell syntax и Python `py_compile` проходят. Ранее backend suite имел 301 passed / 1 skipped / 1 deselected + 7 safe-mode tests, но новые backend regression tests ещё не выполнялись: доступный Python 3.12 не содержит FastAPI/SQLModel/pytest, а локальный pip cache не содержит нужных пакетов. Остаются предупреждения Vite о native config loader/крупном chunk и upstream Pydantic/Starlette deprecations.
- Loopback Python API и Vite UI запускались, login, read endpoints, DB-only Naive node creation, denied `/system/start` (423) и UI proxy прошли. Процессы остановлены, порты 18000/13000 больше не слушают.
- Диагностика Docker была только read-only: клиент 29.1.2 есть, engine pipe `//./pipe/docker_engine` отсутствует, служба `com.docker.service` остановлена с типом запуска Manual, процессов Docker Desktop нет. `WslService` работает, но WSL inventory возвращает `E_ACCESSDENIED`; чтение Docker config также запрещено. Поэтому подтверждена непосредственная причина — daemon недоступен/не запущен; почему Docker Desktop его не поднял, установить не удалось. Ничего не запускалось и системные настройки не менялись.
- Подготовлен `scripts/dev-safe-compose-smoke.sh`: build/up, healthchecks, login, DB-only CRUD, deny network/lifecycle/restore, runtime inspect, restart, clean teardown и до/после snapshots. Docker Compose runtime, контейнерные свойства и VM firewall/routes/DNS до/после не подтверждены. Задача 1.6 остаётся незавершённой. [Инструкция запуска](DEV_SAFE_MODE.md), [Linux VM](dev/LINUX_VM.md), [журнал решений](dev/DECISIONS.md).
- Изменён workflow: единственная рабочая ветка — `master`, PR/feature branches не используются без отдельного запроса; CI запускается на каждом push в `master`. Добавлен Ubuntu runtime smoke job, который всегда загружает 14-дневный artifact с редактированными логами, allowlisted container metadata и IP-masked network diff. Текущий workflow ещё не запускался; Gate 1 остаётся открытым до успешного smoke и review отчёта.
- Проверки workflow-изменения: Bash `-n`, workflow YAML/master-trigger assertions, sanitizer checks (пароли, bearer/JWT, URL credentials и IP), mocked failure-path test (EXIT cleanup вызывает `compose down`, отчёт сохраняется), `docker compose config --quiet`, `git diff --check` — pass. Frontend: 142 теста, lint и build — pass. Backend pytest локально недоступен (`Python 3.12: No module named pytest`); GitHub Actions backend job установит зависимости и выполнит suite после push. Runtime job пока не запущен.

ADR-001 и выбор netns остаются открытыми; реальные nftables/WG правила не запускались.
