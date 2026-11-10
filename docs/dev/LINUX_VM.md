# Изолированный Linux integration стенд

## Статус

На этапе 1 подготовлен профиль стенда, но VM не создана и не запущена: создание виртуального сетевого адаптера меняет конфигурацию TORNADO, а WSL инвентаризация в текущем Windows-сеансе завершилась `E_ACCESSDENIED`. Сетевую VM следует подготовить отдельно до сетевых интеграционных тестов; реальные nftables/WireGuard правила на этапе 1 не применять.

## Рекомендуемая конфигурация

- Гостевая ОС: Ubuntu Server 26.04 LTS, x86_64.
- 2 vCPU, 4 GB RAM, диск от 24 GB; snapshot `clean-os` до установки тестируемого кода.
- Hyper-V private switch либо VirtualBox host-only network. Не подключать bridge к домашнему LAN/VORTEX и не объявлять VM default gateway для TORNADO.
- Не пробрасывать Docker socket, host PID, каталоги `/etc`, credentials, SSH keys и production backup. Не использовать shared folders с правом записи в host workspace.
- Для начального safe-mode smoke можно клонировать репозиторий внутри гостя и открыть только loopback UI/API. Внешний NAT-интернет включать только при конкретной необходимости скачивания зависимостей, затем отключать.
- Перед экспериментами сохранять guest `ip -brief address`, `ip route`, `nft list ruleset`, `/etc/resolv.conf`; после сравнивать снимки. Это команды будущей ручной проверки внутри гостя, запускать их не в этой фазе.
- Реальные TUN/WG/nftables сценарии допускаются лишь после отдельного согласования соответствующей фазы и проверки поддержки ядра. Сетевые права не выдаются Control Plane.

## Phase 1 Compose smoke

После подготовки отдельной VM и установки Docker Engine/Compose v2 запустите
из корня чистого clone:

```bash
sudo bash ./scripts/dev-safe-compose-smoke.sh
```

Скрипт выполняет безопасный build/up, healthchecks, login, DB-only node CRUD,
отрицательные проверки сетевых/lifecycle/restore API, runtime `docker inspect`,
restart и teardown. Для каждого запуска он генерирует уникальные имена проекта
и БД, поэтому teardown удаляет только ресурсы этого smoke. UI/API публикуются
на loopback `13000`/`18000` по умолчанию. Отчёт сравнивает VM state до запуска,
после запуска и после teardown. Docker-managed bridge/NAT/firewall delta —
ожидаемый эффект Compose; любую сетевую мутацию непосредственно TEMPEST считать
дефектом.

VM и Docker Engine пока не подготовлены и не запускались на TORNADO; smoke
остается невыполненным, задача 1.6 не закрыта.

## TORNADO и WSL2

- Для обычной разработки на Windows используйте Docker Desktop и [dev-safe Compose](../DEV_SAFE_MODE.md); WSL2 как отдельный шаг не нужен.
- WSL2 можно использовать для Python/Node unit-тестов, но не считать его подтверждённым стендом для kernel WG, TUN или `CAP_NET_ADMIN`. Сначала проверяются доступность ядра, `/dev/net/tun` и network namespace в отдельной VM.
- Не включать mirrored networking, port forwarding или bridged NIC ради этого этапа. Перечень WSL дистрибутивов на TORNADO не удалось получить из-за Windows `E_ACCESSDENIED`; никакие настройки WSL не менялись.

## Использование по этапам

Этап 1 использует только no-op dataplane и не требует VM privileges. VM предназначена для последующих тестов агента/namespace, fault injection и проверки rollback после реализации и отдельного одобрения. Не выполнять `ip route`, `nft`, `sysctl`, `wg-quick`, `systemctl` или аналогичные команды изменения состояния до такого этапа.
