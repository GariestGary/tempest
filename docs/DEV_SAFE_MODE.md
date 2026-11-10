# Локальная dev-среда TEMPEST

Эта конфигурация предназначена для локальной разработки UI/API. Она не поднимает VPN и не является production Compose.

## Запуск

Нужны Docker Desktop с Compose v2. Отдельный Compose-файл `docker-compose.dev.safe.yml` содержит профиль локальной разработки `dev-safe`:

```powershell
docker compose -f docker-compose.dev.safe.yml config --quiet
docker compose -f docker-compose.dev.safe.yml up --build
```

Полный runtime smoke запускается на изолированном GitHub-hosted `ubuntu-latest`
при каждом push в `master`. Для ручного запуска в Linux VM используйте
[`scripts/dev-safe-compose-smoke.sh`](../scripts/dev-safe-compose-smoke.sh):

```bash
sudo bash ./scripts/dev-safe-compose-smoke.sh
```

Скрипт создаёт уникальные Compose project и SQLite volume, собирает и поднимает
API/UI, ждёт healthchecks, проверяет UI title и login, выполняет DB-only node
CRUD, проверяет HTTP 423 для сетевых/lifecycle/restore операций, сверяет
`docker inspect` каждого контейнера, перезапускает сервисы и удаляет только
созданные им контейнеры, сеть, образы и volume. EXIT/INT/TERM/HUP обработчики
пытаются выполнить cleanup и сохраняют отчёт до удаления временной рабочей
директории. По умолчанию используются
loopback-порты API `18000` и UI `13000`; их можно задать через
`TEMPEST_DEV_API_PORT` и `TEMPEST_DEV_UI_PORT`. Учетные данные smoke по умолчанию
`admin` / `password`; для переопределения задайте
`TEMPEST_SMOKE_USERNAME` / `TEMPEST_SMOKE_PASSWORD`.

Перед build скрипт сохраняет адреса интерфейсов, IPv4/IPv6 routes и rules,
nftables ruleset и SHA-256 `/etc/resolv.conf`; после `up` и teardown пишет
diff в `network-diff.redacted.txt`. Docker-managed bridge, routes и NAT/firewall
правила, созданные Compose, ожидаемы между исходным снимком и работающим
Compose. После teardown эти изменения должны быть убраны; любую сетевую
мутацию непосредственно TEMPEST считать дефектом. Сравнивайте оба diff и не
считайте само наличие Docker bridge изменением приложения.

В `diagnostics/` скрипт сохраняет ограниченный Compose status и последние 300
строк логов сервисов; отчёт содержит только разрешённые поля `docker inspect`.
Пароли, bearer/JWT токены и IP-адреса маскируются. Ответ login и сырые
environment/inspect данные остаются во временной директории и удаляются после
cleanup. В GitHub Actions отчёт прикрепляется как artifact на 14 дней даже при
ошибке smoke. Для ручного запуска по умолчанию путь отчёта печатается в конце;
его можно задать через `TEMPEST_SMOKE_REPORT_DIR`.

На TORNADO Docker Engine недоступен, поэтому локальный runtime запуск здесь не
выполняется. Hosted run #2 нашёл запись стандартного Vite config loader в
read-only `/app/node_modules/.vite-temp`; runner loader исправил старт UI. Run #3
затем обнаружил, что `/tmp` приходит через `HostConfig.Tmpfs`, а не обычный
список `Mounts`; проверка скорректирована и ждёт повторного hosted прогона.
До успешного полного run и review приложенного отчёта Gate 1 и задача 1.6
остаются открытыми.

После запуска доступны:

- UI: <http://localhost:3000>
- API через UI proxy: <http://localhost:3000/api>
- API напрямую: <http://localhost:8000>
- Health: <http://localhost:8000/health>

Порты API и UI можно переопределить переменными `TEMPEST_DEV_API_PORT` и `TEMPEST_DEV_UI_PORT`. Они публикуются только на `127.0.0.1`. При смене порта UI в CORS допустимые origins также следует актуализировать в Compose.

Начальная запись администратора соответствует upstream seed (`admin` / `password`); сразу измените пароль через интерфейс. `TEMPEST_DEV_SECRET_KEY` можно задать для локального JWT signing key; значение по умолчанию предназначено только для loopback development.

## Остановка и данные

Если Compose запущен в foreground — `Ctrl+C`. Если он запущен в фоне:

```powershell
docker compose -f docker-compose.dev.safe.yml down --remove-orphans
```

Dev SQLite хранится в отдельном Docker volume `tempest-dev-safe-db`, а исходники монтируются только для чтения. Сбросить dev-данные:

```powershell
docker compose -f docker-compose.dev.safe.yml down --volumes --remove-orphans
```

Эта команда удаляет только volume, объявленный этим Compose-проектом. Она не чистит остальные Docker volumes/images.

## Граница безопасного профиля

- API и UI работают под UID `10001` и `1000`, с `cap_drop: ALL`, `no-new-privileges`, read-only root filesystem и временным `/tmp`.
- Нет `privileged`, `network_mode: host`, `pid: host`, Docker socket, host `/etc`/`/proc` mounts, устройств или добавленных capabilities. DB — отдельный именованный volume.
- Compose healthchecks проверяют API `/health` и доступность Vite UI; UI ждёт healthy API.
- `DEV_SAFE_MODE=1` прерывает штатный backend startup сразу после подготовки SQLite и локальных defaults: не стартуют Xray, scheduler, router reconcile/watchdog, системные toggles, Docker sidecars и dataplane.
- API по умолчанию закрыт; allowlist оставляет безопасный read-only просмотр данных/settings, login, ограниченные DB-only mutations и выбранный CRUD. Остальные HTTP-запросы получают HTTP 423, включая system/network endpoints, diagnostics, restore/import side-effects, deploy/update и фоновые задания. Все WebSocket-запросы закрываются.
- `/api/system/settings` читает сохранённые значения, не опрашивая интерфейс, `/proc/sys` или resolver. `/api/system/status` показывает Xray остановленным и nftables неактивными.
- UI показывает постоянную плашку **SAFE DEVELOPMENT MODE — VPN traffic is disabled**. No-op dataplane не принимает VPN-пакеты и не подтверждает применение сетевого плана.

Compose создаёт свою bridge-сеть и loopback port mappings. Контейнеры не получают полномочий управлять сетью хоста, но Docker daemon на Linux может создавать собственные bridge/NAT/firewall state при публикации портов. Runtime `docker inspect` должен подтвердить `Privileged=false`, отсутствие host PID/network, пустой `CapAdd`, `CapDrop=ALL`, отсутствие Docker socket и системных RW mounts, read-only rootfs и только loopback port bindings. Сам smoke снимает VM network state до/после; сравнивать следует отдельно Docker-managed bridge/NAT/firewall изменения и возможные TEMPEST изменения. Само приложение не должно менять DNS, default route, nftables, интерфейсы или TUN.

Не запускайте для этого этапа `docker-compose.yml`, `docker-compose.dev.yml`, `install.sh`, updater/deploy scripts или privileged контейнеры.
