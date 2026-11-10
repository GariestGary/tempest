# TEMPEST — статус форка

## 2026-11-10 · этап 0

**ACCEPTED пользователем.**

- Исходный PiTun v1.7.8 — `b75223d621395e7b641a77bcff61cd8647deb207`. Fork parent, upstream master и tag сверены. Сохраняем FastAPI, React, SQLite, генерацию Xray и upstream-тесты. [Baseline и команды](UPSTREAM_BASELINE.md).
- Сохраняем шаблоны управляемого proxy routing, diagnostics, snapshot/watchdog. Для будущего MVP исключаем из доверенной зоны API системный DNS, WAN/DHCP/Wi-Fi, Docker lifecycle, SSH deploy и управление внешними панелями. [Карта привилегий](HOST_PRIVILEGE_AUDIT.md).
- Локальные WG/AWG/VLESS ingress в PiTun отсутствуют. Критические разрывы — привилегии API, неявный DIRECT, схема TUN и качество UDP health signal. [Матрица](TEMPEST_GAP_MATRIX.md).
- ADR-001 остаётся **PROPOSED**: выбор host agent vs netns зависит от Linux VM тестов. [ADR-001](adr/ADR-001-privilege-separation.md).
- Baseline: backend 294 passed / 1 skipped / 1 deselected в выбранном наборе; frontend 142 passed и build успешен. На момент этапа 0 ESLint ещё не был настроен.

## 2026-11-10 · этап 1

**READY_FOR_REVIEW.** Этап 1 не принят; этап 2 остаётся `BLOCKED`. Задачи 1.1 и 1.6 выполнены и проверены; Gate 1 готов к review. Hosted run #4 прошёл backend/frontend/static Compose и полный runtime smoke. Приёмка этапа ожидает подтверждения пользователя.

- Добавлен отдельный `docker-compose.dev.safe.yml`: непривилегированные UID, read-only root filesystems, `cap_drop: ALL`, loopback-only port mappings, отдельный DB volume; без host network/PID, Docker socket и системных host mounts. `docker compose config` и статический аудит параметров прошли.
- `DEV_SAFE_MODE=1` останавливает штатный lifecycle после локальной SQLite и defaults. Ревью проверило все зарегистрированные HTTP routes/WebSocket deny boundary, lifecycle startup hooks/background tasks, dataplane guards и закрытые backup restore/network/system operations. Исправленные regression tests прошли: backend run #4 дал 1736 passed, 2 skipped.
- Задача 1.1 закрыта: пользовательский UI, API metadata, frontend package и dev docs используют TEMPEST. `LICENSE`, upstream URL/history, legacy `pitun_token`, `pitun-routing` и API schema fields оставлены для совместимости/атрибуции. Массового переименования внутренних модулей нет.
- Документированы команды запуска/остановки/reset и профиль отдельной Linux VM. VM не создана: это изменило бы сетевую конфигурацию TORNADO; WSL inventory завершился `E_ACCESSDENIED`.
- Frontend: 142 passed; build/typecheck и lint проходят. Compose static config/security audit, shell syntax, Python `py_compile` и `git diff --check` проходят. Hosted backend run #4: 1736 passed, 2 skipped. Предупреждения: Vite native config loader/крупный chunk, upstream Pydantic/Starlette deprecations.
- Loopback Python API и Vite UI запускались, login, read endpoints, DB-only Naive node creation, denied `/system/start` (423) и UI proxy прошли. Процессы остановлены, порты 18000/13000 больше не слушают.
- Диагностика Docker была только read-only: клиент 29.1.2 есть, engine pipe `//./pipe/docker_engine` отсутствует, служба `com.docker.service` остановлена с типом запуска Manual, процессов Docker Desktop нет. `WslService` работает, но WSL inventory возвращает `E_ACCESSDENIED`; чтение Docker config также запрещено. Поэтому подтверждена непосредственная причина — daemon недоступен/не запущен; почему Docker Desktop его не поднял, установить не удалось. Ничего не запускалось и системные настройки не менялись.
- Runtime smoke run [#4](https://github.com/GariestGary/tempest/actions/runs/42781710707) прошёл: build/up, API/UI health, UI title, login, DB-only create/read/update/delete, HTTP 423 deny checks для lifecycle/network/restore/diagnostics, restart с сохранением записи и clean teardown. Runtime inspect подтвердил для обоих сервисов `Privileged=false`, non-host PID/network, `CapAdd=null`, `CapDrop=ALL`, read-only rootfs, no-new-privileges, отсутствие devices/socket/system mounts, только DB volume writable, tmpfs `/tmp` с `noexec,nosuid`, read-only source binds и loopback port bindings. Sanitized artifact [#4](https://github.com/GariestGary/tempest/actions/runs/42781710707/artifacts/13746286950) содержит allowlisted inspect summary, logs, teardown, network diff и `result=0`.
- Сетевой diff run #4: `before → after-up` добавились Docker-managed Compose bridge/veth/routes и Docker NAT/filter/raw rules для loopback-портов 18000/13000. `after-up → after-teardown` Compose bridge/veth/routes и правила публикации портов удалены; default/external routes, `ip rule` и resolver hash не изменились. После teardown остались только Docker Engine общие следы runner: `docker0` получил IPv6 link-local, пустая `ip raw` table и счётчики nftables изменились. Это Docker-managed состояние; изменений сетевой конфигурации непосредственно TEMPEST не обнаружено.
- Git workflow: только `master`, direct commits, без feature branches/PR без отдельного запроса; run #4 был событием `push` в `master`. В нём прошли backend, frontend, static Compose audit и runtime smoke; artifact сохраняется на 14 дней. Предыдущие неудачные runs #2/#3 приложили отчёты, и cleanup выполнялся при обеих ошибках.
- Проверки: Bash `-n`, workflow YAML/master-trigger assertions, sanitizer checks (пароли, bearer/JWT, URL credentials и IP), mocked cleanup-on-error, `docker compose config --quiet`, `git diff --check`; frontend 142 теста, lint/build; backend 1736 passed/2 skipped; hosted Compose runtime smoke — pass.

ADR-001 и выбор netns остаются открытыми; реальные nftables/WG правила не запускались.
