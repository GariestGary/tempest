# Изолированный Linux integration стенд

## Статус

Локальная VM на TORNADO не создана: это меняло бы конфигурацию хоста, а WSL инвентаризация завершилась `E_ACCESSDENIED`. Для Gate 1 safe Compose runtime smoke назначен изолированный GitHub-hosted `ubuntu-latest`; он не использует TORNADO или VORTEX. Реальные nftables/WireGuard integration rules остаются за рамками этапа 1.

## Рекомендуемая конфигурация

- Гостевая ОС: Ubuntu Server 26.04 LTS, x86_64.
- 2 vCPU, 4 GB RAM, диск от 24 GB; snapshot `clean-os` до установки тестируемого кода.
- Hyper-V private switch либо VirtualBox host-only network. Не подключать bridge к домашнему LAN/VORTEX и не объявлять VM default gateway для TORNADO.
- Не пробрасывать Docker socket, host PID, каталоги `/etc`, credentials, SSH keys и production backup. Не использовать shared folders с правом записи в host workspace.
- Для начального safe-mode smoke можно клонировать репозиторий внутри гостя и открыть только loopback UI/API. Внешний NAT-интернет включать только при конкретной необходимости скачивания зависимостей, затем отключать.
- Перед экспериментами сохранять guest `ip -brief address`, `ip route`, `nft list ruleset`, `/etc/resolv.conf`; после сравнивать снимки. Это команды будущей ручной проверки внутри гостя, запускать их не в этой фазе.
- Реальные TUN/WG/nftables сценарии допускаются лишь после отдельного согласования соответствующей фазы и проверки поддержки ядра. Сетевые права не выдаются Control Plane.

## Phase 1 Compose smoke

GitHub Actions выполняет этот smoke на hosted Ubuntu runner при каждом push в
`master`. Для ручного запуска в отдельной Linux VM с Docker Engine/Compose v2
используйте из корня clone:

```bash
sudo bash ./scripts/dev-safe-compose-smoke.sh
```

Скрипт выполняет безопасный build/up, healthchecks, login, DB-only node CRUD,
отрицательные проверки сетевых/lifecycle/restore API, runtime `docker inspect`,
restart и teardown. Для каждого запуска он генерирует уникальные имена проекта
и БД, поэтому cleanup затрагивает только ресурсы этого smoke. UI/API публикуются
только на loopback `13000`/`18000` по умолчанию. Отчёт сравнивает сетевое
состояние до запуска, с работающим Compose и после teardown. Docker-managed
bridge/NAT/firewall изменения ожидаемы во время запуска; изменения от TEMPEST
считаются дефектом. Остаточные изменения после teardown требуют разбора.

Отчёт сохраняется в `TEMPEST_SMOKE_REPORT_DIR` либо в каталоге
`$RUNNER_TEMP/tempest-safe-compose-smoke-<run-id>` (при ручном запуске — под
`$TMPDIR` или `/tmp`). Он содержит отредактированные Compose логи, выбранные
поля `docker inspect` и IP-маскированный network diff, но не сохраняет login
response, токены, сырые environment или исходные inspect JSON. GitHub Actions
прикрепляет отчёт как artifact на 14 дней, включая неуспешные прогоны.
EXIT/INT/TERM/HUP traps пытаются выполнить `docker compose down` перед выходом.

На TORNADO runtime smoke не выполнялся из-за недоступного Docker Engine. Новый
GitHub Actions job ещё должен пройти после push; до успешного прогона и review
отчёта задача 1.6 и Gate 1 остаются открытыми.

## TORNADO и WSL2

- Для обычной разработки на Windows используйте Docker Desktop и [dev-safe Compose](../DEV_SAFE_MODE.md); WSL2 как отдельный шаг не нужен.
- WSL2 можно использовать для Python/Node unit-тестов, но не считать его подтверждённым стендом для kernel WG, TUN или `CAP_NET_ADMIN`. Сначала проверяются доступность ядра, `/dev/net/tun` и network namespace в отдельной VM.
- Не включать mirrored networking, port forwarding или bridged NIC ради этого этапа. Перечень WSL дистрибутивов на TORNADO не удалось получить из-за Windows `E_ACCESSDENIED`; никакие настройки WSL не менялись.

## Использование по этапам

Этап 1 использует только no-op dataplane и не требует VM privileges. VM предназначена для последующих тестов агента/namespace, fault injection и проверки rollback после реализации и отдельного одобрения. Не выполнять `ip route`, `nft`, `sysctl`, `wg-quick`, `systemctl` или аналогичные команды изменения состояния до такого этапа.
