# Замена старого TEMPEST на VORTEX

## Штатное обновление control plane

Первое обновление новым механизмом и все последующие выполняются одинаково,
из Fish или Bash:

```sh
cd ~/Docker/tempest
git pull --ff-only
./scripts/deploy/update-vortex.sh --check
./scripts/deploy/update-vortex.sh
```

`--check` проверяет состав и доступность обновления без изменения production;
эту команду можно опустить. `--status` показывает установленный и целевой SHA,
тип обновления и состояние контейнеров. `--rollback` возвращает предыдущий
совместимый API/UI bundle и образы, сохраняя текущую SQLite; перед откатом
создаётся новая защищённая копия. Для восстановления данных БД из backup
нужна отдельная проверка совместимости и ручная операция, иначе можно потерять
изменения пользователей после обновления.

Updater проверяет чистый `master`, точное совпадение с `origin/master` и
успешные push-запуски Hosted CI/CodeQL для HEAD через публичный GitHub API.
Токен в команду не вводится. Он сравнивает HEAD с версией действующего
deployment bundle, проверяет контейнеры и службы, место на `/opt`, создаёт
root-only online backup SQLite и копии конфигурации, сохраняет image IDs и
строит новые образы до замены контейнеров. Compose ждёт готовности до 120
секунд; после пересоздания API проверяется доступ через nginx, в том числе
после смены IP контейнера. После проверки HTTP, Agent IPC и
неизменности active/LKG он атомарно обновляет путь panel unit. Ошибка после
замены контейнеров вызывает rollback; прерванная транзакция восстанавливается
при следующем обычном запуске, включая обновление без пересборки.
Одновременно работает только один updater. Ручной rollback сохраняет текущее
подтверждённое поколение Agent, даже если оно изменилось после установки.

Изменения Agent, runtime, systemd, Compose и неизвестных файлов updater
отклоняет до изменения production: для них требуется отдельная согласованная
процедура. Обычное обновление не вызывает Apply, не перезапускает Agent,
network unit или dataplane и не трогает другие Docker-проекты. Хранение
созданных updater backup/bundle ограничено `TEMPEST_UPDATE_KEEP` (2–5,
по умолчанию 2); текущий, предыдущий и используемые контейнерами bundle
сохраняются. Минимальный запас места задаёт `TEMPEST_UPDATE_RESERVE_GIB`
(по умолчанию 3 ГиБ сверх оценки сборки). Старые ручные backup и чужие
Docker-объекты updater не удаляет.

На VORTEX первое выполнение нового updater ещё не проверено. Read-only SSH
после подготовки этой инструкции показал Compose labels обоих контейнеров на
bundle `daf4b070` (позднее, чем задокументированный `a4aee115`); путь panel
unit остался непроверенным. Перед первым запуском сверить вывод `--check`;
при расхождении bundle и Docker labels updater остановится до мутации.

## Состояние control plane после обновления 2026-11-28

Следующий раздел — исторический снимок установки `a4aee115`. Позднейший
read-only осмотр контейнеров показал Compose bundle `daf4b070`; состояние
panel unit и данные SQLite в этом осмотре не проверялись.

Control plane собран из проверенного master `a4aee1156a590ac23f3c63c72ba117b91d01296d`: API и Web UI `1.7.8`. Image IDs: API `sha256:6e881ba89b49b28665e72836dd7a6b1e9c9738e3adedfd3ddf33d4c081c504eb`, panel `sha256:6012e3fb0ca834544765970e8bead42eef2602106ce78d1ac0fe816bd6399544`. Работающие контейнеры используют Compose project `tempest-working-core` и постоянный root-owned bundle `/opt/tempest-deploy-a4aee1156a590ac23f3c63c72ba117b91d01296d`. Panel unit заменён tracked версией; `systemctl daemon-reload` выполнен. API `healthy`, panel `running`; SQLite volume `tempest-working-core_core_db`, bind mount private provisioning и Agent socket сохранены.

Перед изменением root-only архивированы прежний deployment directory, panel unit/environment и идентификаторы старых Docker-образов. SQLite online backup: `/home/gary/tempest-controlplane-rollback-20261128T105248Z/working-core.db`, `PRAGMA integrity_check=ok`, SHA-256 `30e4be336bfcb92f765bdcd024b2c7b8ef986a2ce087bb5a4a43d7adb368b4f8`. Копия также помещена в закрытый backup под `/var/backups/tempest/controlplane-*/`. Старые образы сохранены тегами `tempest-working-core-api:rollback-pre-a4aee115` и `tempest-working-core-panel:rollback-pre-a4aee115`; тома и backups не удалялись.

Сравнение online backup с live SQLite после обновления показало одинаковые SHA-256 строк пользователей и Working Core draft. В обеих БД: 1 пользователь, 1 provider, 1 saved AWG egress и 1 client; SHA-256 draft `b3e648febdea0f02f9f541aa008296d629c6297a36b32c8a77c146184c5fbcc4`. Значения конфигурации и password hashes не выводились.

Уточнение к прежнему аудиту: старый `/opt/tempest-deploy-92fec95` был `root:root` mode `0700`. Проверка от `gary` не могла прочитать его содержимое, а подавленный stderr привёл к неподтверждённому выводу, что Compose-манифесты отсутствуют. Старый каталог сохранён в root backup. Фактическое наличие/отсутствие манифестов в архивной копии и причина возможного удаления не установлены; называть это потерей файлов без root-level просмотра архива нельзя. Новый постоянный bundle развёрнут отдельно, старый путь не перезаписывался.

Post-update GET `/api/core` вернул `sync_state=synced`, `sync_diff_available=true`, ноль diff sections; Agent `idle`, active/LKG generation `6`, UUID `ff523ea8-e2b0-441d-8e56-7686bbd7fe8c`, ошибок нет. Домен `https://net.jetstream.su/` отвечает `302` через сохранённый Caddy/Authelia маршрут, loopback UI и UI health — `200`; `proxy` network и alias `tempest-working-core-web` сохранены. Panel unit прошёл `systemd-analyze verify`; network и Xray Agent units остались active, Agent running. Read-only Agent status показывает egress `active_verified`.

VORTEX не перезагружался; dataplane/Agent/network service не перезапускались; Apply и реальные клиентские traffic tests не выполнялись. Поэтому подтверждены состояние control plane и Agent, но не end-to-end VPN-трафик клиента. При импорте API также появляется существующее предупреждение `SECRET_KEY=changeme`; Working Core загружает реальный ключ из защищённого файла в lifespan, но предупреждение остаётся шумным и требует отдельного улучшения. UI build сообщает о крупном JavaScript bundle; npm audit нашёл зависимости с известными уязвимостями — зависимости в рамках этого обновления не менялись.

Прежний вручную созданный panel unit ожидал только появление socket path через Fish; repository unit ожидает active Xray Agent и connectable Unix socket. Production Agent и network unit не заменялись, чтобы не затрагивать dataplane. Caddy, Authelia и сторонние сервисы не перезапускались.

При установке обнаружены: DNAT с портом требует `-p udp`, conntrack — `--ctproto udp`; AppArmor разрешает `wg` читать probe key через точечную строку [apparmor-wg.local](../deploy/working-core/apparmor-wg.local), без отключения профиля. Исходники Agent должны быть `root:tempest-control`, каталоги 0750, файлы 0640. Закрытый umask при распаковке также может сделать публичный Docker build context нечитаемым, включая `/entrypoint.sh`; нормализовать права только проверенных публичных исходников, не секретов. Пакетирование и повторная чистая установка пока не проверены.

Для подготовки только публичных Docker contexts используйте `bash scripts/deploy/prepare-build-context.sh <commit> <новый-абсолютный-каталог>`: экспортируются только отслеживаемые backend/frontend файлы указанного commit, а не секреты и незакоммиченное рабочее дерево; закрытый родитель остаётся 0700. Проверяйте состав commit до экспорта. После запуска Agent вызовите `python3 scripts/deploy/wait-agent-socket.py`: проверяются active service и подключение к Unix-сокету с ограниченным ожиданием; авторизация IPC и здоровье dataplane требуют отдельной проверки. Предупреждение `SECRET_KEY=changeme` выводится до startup Working Core; реальный ключ загружается из защищённого файла при startup. Само предупреждение не подтверждает использование этого значения для авторизации.

Сетевой rollback не подтверждён: удаление probe interface выбирает объект только по имени, а удаление namespaces и обработка частичного создания требуют отдельной проверки принадлежности и fault-тестов. Не выполнять эти операции на VORTEX на основании одних скриптов.

**Политика владельца от 2026-11-28:** VORTEX является production-хостом TEMPEST. Read-only аудит, диагностика TEMPEST, логи и состояние служб разрешены. Установка, обновление и обслуживание TEMPEST выполняются по явному разрешению владельца; обязательны защищённая резервная копия и подготовленный rollback. Уже разрешённые необходимые шаги выполняются автономно. Reboot, сторонние сервисы, host firewall/routing и работающий dataplane остаются вне такого разрешения и требуют отдельного согласования. Исторический audit ниже не подтверждает их текущее состояние.

**Исторический статус до обновления:** Working Core был развёрнут на VORTEX; boot recovery и повторяемая сборка deployment bundle не подтверждены. Предыдущий вывод об отсутствии Compose manifests возник при проверке root-only каталога от `gary` и не был доказан. Архив исходного bundle сохранён до установки новой versioned копии; причина возможного отсутствия файлов не установлена. Старые данные не удалялись.

## Снимок и приоритет повторного использования

Read-only [инвентаризация](VORTEX_LEGACY_INVENTORY.md) 27.11 установила Ubuntu 26.04.1 x86_64, Docker 29.7.2 / Compose 5.5.0 и около 6,7 GiB свободно. В тот момент старый `vortex-panel` занимал `127.0.0.1:9080`, а общий Caddy site `net.jetstream.su` проксировал к нему через Authelia. По новому снимку 28.11 restart policy старого контейнера уже `no`, панель TEMPEST включена на том же loopback-порту, а Caddy направляет её на `tempest-working-core-web:8080` с `forward_auth`. Старый контейнер, его данные и общие службы по-прежнему сохранены. Другие сведения о sing-box, Xray и guard в следующем абзаце — историческая инвентаризация до cutover; их актуальные состояния отдельно не перепроверялись в этой задаче.

Текущий опубликованный UI — `127.0.0.1:9080`, его hostname Caddy — `tempest-working-core-web:8080`. Изменения UDP 51820 в этой задаче не проверялись; не выводить состояние старого endpoint из проверки панели. Внешняя публикация ingress не проверялась.

Сеть панели/API `10.244.247.0/24` и пул WG-клиентов `10.245.248.0/24` оценивались в snapshot 27.11; текущие network objects этим аудитом не перепроверялись. Отдельная сеть панели/API изолирует control plane от общей `proxy`. [`vortex-readiness.py`](../scripts/deploy/vortex-readiness.py) выполняет read-only preflight портов, маршрутов, Docker-сетей, volumes, путей, units и пользователей, не связывая сокеты и не меняя VORTEX. Конфликт UI-порта 9080 был устранён при ручном cutover; состояние UDP 51820 здесь не проверялось. SSH tunnel из старого плана больше не является текущей схемой доступа.

Из read-only sudo-отчёта: IPv4 FORWARD policy `drop`, есть общие Docker NAT/filter/raw и `inet zapret2`. Найденные handles Docker rules с портом 9080 — только кандидаты, не доказательство принадлежности: те же номера используются внутри других контейнеров. Правила не удалять вручную по портам или старым handles. UFW активен; в отчёте не определён полный поток через общие цепочки. Ни текущие DNS/маршруты хоста, ни общие Caddy, Docker, AdGuard Home, sing-box или zapret2 не изменять без согласованного и проверенного назначения.

## Разработанные материалы и оставшиеся блокеры

- [`fetch-upstream.sh`](../scripts/deploy/fetch-upstream.sh) скачивает официальные Xray и sing-box в новый операторский каталог и сверяет зафиксированные хэши. `bash scripts/build-awgproxy.sh <чистый-checkout> <путь>` воспроизводит закреплённый awgproxy с Go 1.27.3 и проверенным module cache; локальная сборка повторно совпала с SHA-256. [`verify-runtime.py`](../scripts/deploy/verify-runtime.py) проверяет три Linux amd64 бинарника по ELF и SHA-256; [`stage-runtime.sh`](../scripts/deploy/stage-runtime.sh) готовит локальный stage Xray/awgproxy без копирования внешнего sing-box. [`install-runtime.sh`](../scripts/deploy/install-runtime.sh) — только **бинарный** установочный шаг; его запуск на VORTEX возможен только если входит в явно разрешённую операцию и подготовлены backup/rollback. Он не устанавливает TEMPEST целиком и не запускает сервисы.
- Подготовлены [`requirements-agent-linux.lock`](../backend/requirements-agent-linux.lock), manifest [60 wheels](../deploy/working-core/agent-wheels-linux-amd64.sha256) и локальный wheelhouse: версии совпадают с проверенной локальной Python 3.11 средой, download выполнен с проверкой hash; dry-run lock прошёл. Официальный архив Python 3.11.18 `20261124` закреплён SHA-256 `d873f435e58d396a18858d4f52a335f1abe8cea385cb9d411ec511f958249d54`. [`prepare-wheelhouse.sh`](../scripts/deploy/prepare-wheelhouse.sh) скачивает колёса без установки, а [`install-agent-python.sh`](../scripts/deploy/install-agent-python.sh) и [`install-agent-code.sh`](../scripts/deploy/install-agent-code.sh) предоставляют отдельные проверяемые шаги. На дату исторической проверки VORTEX Python 3.11 отсутствовал; чистая установка этих шагов ещё не проверена. Установка Agent-кода в рамках control-plane обновления не нужна без отдельной причины и разрешения.
- [Compose overlay](../deploy/working-core/vortex.compose.yml) готовит только сеть панели/API. Он не создаёт изолированный namespace gateway, безопасную исходящую связность, UDP ingress или verifier. Docker может создавать scoped bridge/NAT/firewall объекты; точный состав согласовать до запуска.
- Ранее владелец сообщил, что AWG egress и WG-клиенты через Wi-Fi/мобильную сеть работают; этот audit их не перепроверял. Кандидат [production verifier](PRODUCTION_VERIFIER.md) всё ещё зависит от лабораторных адресов и не подтверждает traffic behavior на VORTEX. Reboot, возврат после boot и восстановление из backup не проверялись. Отдельный Compose deployment bundle при read-only audit оказался неполным; его наличие обязательно для повторного `docker compose start`.
- [Уведомления](THIRD_PARTY_NOTICES.md) содержат лицензии PiTun, Xray и awgproxy; sing-box 1.14.2 оператор получает отдельно из официального выпуска и проверяет SHA-256. Он не входит в stage или API image. Аудит транзитивных зависимостей для возможного распространения ещё не завершён.

## Исторический план до ручного cutover

Пункты ниже описывают план, подготовленный до deployment. Не считать их текущими действиями или разрешением на production changes: текущее подтверждённое состояние и оставшиеся ограничения приведены выше.

1. Закрыто архивировать старую панель/volume, Compose overrides, связанные конфигурации sing-box и Caddy, сведения об их владельцах/правах, unit и текущие enable/restart состояния; проверить manifest. Существующий Caddy использует файлы внутри `tempest2-production`, поэтому архивировать копии, не перемещать работающий каталог. Сырые конфиги/inspect могут содержать секреты и не должны попадать в Git или обычные логи.
2. После подтверждения, что guard не нужен другим потребителям, отключить только старый `tempest-port-guard.timer`; сохранить обратную операцию. Остановить и отключить автозапуск только `vortex-panel`, не удаляя контейнер/volume. Для освобождения UDP 51820 применить только проверенный локальный diff старого endpoint и его `route.rules` reference, сохраняя прочие правила и общие inbounds; [`preview-retire-old-wg.py`](../scripts/deploy/preview-retire-old-wg.py) создаёт только защищённый черновик по точному SHA и отказывает при дополнительных ссылках. Проверить все файлы конфигурации sing-box в отдельном закрытом staging-каталоге, согласовать конкретный diff, корректно reload и проверить остальные функции. Не менять общий процесс/config до этого согласования.
3. После готовности и проверки network provisioning и production verifier сгенерировать **новые** ключи gateway/probe, пароль и app-secret; не использовать лабораторные ключи, пользователей, адреса или UDP relay. Установить фиксированные компоненты и собственные units, запустить Agent и панель, проверить UID/GID, пустые capabilities, namespace и доступ через SSH. Для Caddy при необходимости подготовить отдельный минимальный diff старого `reverse_proxy`, сохранив `forward_auth`, остальные сайты и общую сеть `proxy`; не менять Caddy без согласования назначения.
4. Через UI импортировать AWG, выпустить профиль одного устройства, выполнить preview/apply/verify/confirm и проверить обычным WG-клиентом HTTPS, TCP/UDP, DNS и исходящий адрес. Проверку внешнего клиента и UDP ingress совместить с первым практическим подключением. Убедиться, что оставшиеся сервисы VORTEX работают как ранее.
5. Rollback: отключить только новый экземпляр и его точно учтённые объекты; возвратить старую панель и её автозапуск, а при необходимости — только проверенный старый конфиг sing-box и guard. До восстановления старых ключей сверить актуальные отзывы: архив не должен возвращать отозванный доступ. Новые данные и архив старой системы сохранить; безвозвратное удаление старых данных выполнять только после успешной проверки и в пределах согласованного плана. Файлы, используемые общим Caddy или другими сервисами, нельзя считать удаляемыми до разрешения их зависимостей. Установка, остановка, обновление и rollback нового gateway останутся неполными, пока не подготовлены и не проверены network/verifier provisioning.
