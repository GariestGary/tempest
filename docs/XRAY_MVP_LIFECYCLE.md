# Xray lifecycle для WireGuard MVP

Владелец утвердил Xray 26.10.31 после A/B на `b35c5de`. Этот срез открывает применение профиля через TEMPEST. Профиль включает один userspace WG ingress, один VLESS TCP/TLS upstream, явные TCP/UDP CIDR+port правила и default BLOCK. DNS на согласованных адресах идёт через VLESS, остальные порты 53/853 блокируются. TLS использует проверку имени и при необходимости SHA-256 pin. [Решение владельца и результаты A/B](ENGINE_VS_KERNEL_DECISION.md)

## Границы и запуск

- `XrayPlan` принимает типизированные поля. Raw JSON Xray, произвольные outbound, пути, systemd команды, маркировка, TUN и сторонний routing backend отсутствуют в контракте. Agent компилирует конфиг, переиспользуя чистую генерацию VLESS PiTun и правила лабораторного профиля. Freedom разрешает только явный DIRECT CIDR и порт. Blackhole стоит первым outbound и завершает routing.
- Для opt-in Xray Agent используется отдельный сокет с тем же AF_UNIX framing, `SO_PEERCRED`, allowlist UID/GID, опциональным cgroup и механизмом replay. Старый NetworkPlan, table Agent и их клиенты сохраняются. Старые NetworkPlan API сохраняют inspection. Working Core обращается к Xray Agent через отдельный авторизованный API. Операторская CLI остаётся доступна. [Панель и тестовый стенд](WORKING_CORE.md)
- Operator provisioning создаёт выделенные пространства движка и WG probe, сервисные UID/GID, защищённый каталог runtime, закреплённый бинарник и root-owned traffic verifier. Перед стартом оператор сверяет идентичность target и PID 1. Unit проверяет mountpoint и использует `NetworkNamespacePath`. Agent сверяет имя и живую идентичность namespace, сверка `/proc/1/ns/net` внутри Agent дополнительно выполняется при наличии прав чтения этого объекта.
- Пример использует systemd для lifecycle. Agent может только запускать или останавливать `tempest-xray.service` и запускать фиксированный oneshot `tempest-xray-probe.service`. Полномочия ограничены polkit по UID, action, unit и verb. Xray читает конфиг по фиксированному пути и связан с Agent через `BindsTo`. Включается Agent. Он восстанавливает Xray после provisioning namespace. Для Xray отсутствует самостоятельный автозапуск. [Шаблоны systemd](../deploy/systemd)
- Кладите `tempest-xray.tmpfiles.conf` как `tempest-xray.conf` в каталог конфигурации systemd-tmpfiles при отдельно согласованном развёртывании. Для файла `/etc/tempest/xray-agent.env` требуются `TEMPEST_AGENT_ALLOWED_UIDS`, `TEMPEST_AGENT_ALLOWED_GIDS`, `TEMPEST_XRAY_LISTEN` и `TEMPEST_XRAY_PORT`. Порт, статические UID/GID, путь бинарника и runtime files задаются при provisioning. Xray gateway читает конфиг через группу `tempest-xray`. Control Plane не входит в эту группу. StateDirectory Agent закрыт режимом 0700.
- One-shot должен стартовать заново при каждом вызове, выполнять реальные пробы с отдельного WG-клиента и завершаться успехом только при подтверждении допускаемых и заблокированных путей. Готовность сверяется до и после пробы по systemd InvocationID и активным байтам конфигурации. При переподключении WG допускается до 22 секунд на повторные пробы того же процесса. Пример для согласованной VM в [лабораторном verifier](../scripts/lab/xray-mvp-probe.py). Он ожидает от тестового sink echo случайного маркера со своим наблюдаемым адресом источника. Поставляемый профиль требует своего доверенного verifier и рабочих client маршрутов. Наличие процесса, config check и слушающий UDP сами по себе не дают готовность.

## Операторский контракт

При согласованном запуске подготовьте закрытый файл плана с `schema_version=1`, `generation_id` (UUID), `generation`, `base_generation`, `target_namespace_id`, `listen_address`, `listen_port`, `tunnel_addresses`, `private_key`, `peers`, `vless`, `resolvers`, `rules` и `default_egress="block"`. Схема с точными вложенными полями и ограничениями находится в [профиле](../backend/app/network_agent/xray_profile.py). Приватный ключ, UUID VLESS и содержимое профилей сохраняются только в закрытых файлах.

Из каталога backend выполните команды под разрешённым UID/GID. В аргументе поколения используется UUID, который вы записали в план.

```bash
.venv/bin/python -m app.network_agent.xray_client validate /path/to/candidate.json
.venv/bin/python -m app.network_agent.xray_client stage /path/to/candidate.json
.venv/bin/python -m app.network_agent.xray_client diff /path/to/candidate.json
.venv/bin/python -m app.network_agent.xray_client apply "$GENERATION_ID"
.venv/bin/python -m app.network_agent.xray_client verify "$GENERATION_ID"
.venv/bin/python -m app.network_agent.xray_client confirm "$GENERATION_ID"
.venv/bin/python -m app.network_agent.xray_client read_status
```

До подтверждения доступен `rollback "$GENERATION_ID"`. `stage` и `diff` возвращают набор затронутых секций и номера поколений без секретов. `validate` и `stage` используют закрытый временный файл для синтаксической проверки. `apply` повторяет проверку до остановки рабочего процесса. После остановки фиксирует durable intent и список отзывов, затем применяет рабочую копию через атомарный rename и fsync. Verify для того же поколения обязателен перед Confirm. Каждая транзакция имеет ограниченный дедлайн. Номер новой заявки должен превышать durable watermark принятых попыток.

При запуске Agent останавливает оставшийся процесс, сбрасывает pending из журнала и восстанавливает только отдельно записанный подтверждённый LKG. Журнал содержит разные поля для pending, LKG и отзыва в одной атомарной записи. Это исключает расхождение LKG и списка отозванных ключей после сбоя записи. Несовместимый LKG остаётся в истории, ingress закрыт. План без peers применим как явное закрытие ingress. Для возвращения к работе нужен новый план с разрешённым ключом или безопасная текущая LKG. Старый ключ запрещён при последующих apply и после restart. Восстановление всей старой копии диска требует отдельно защищённой сверки отзывов.

Метод прогонки проверяется через CLI и реальные systemd unit в лаборатории. Открытые проверки Working Core включают reboot, штатный boot provisioning, аварийные разрывы записи и границы поставляемых сервисов. Реальные сетевые испытания выполняются в отдельно согласованной VM. Статусы прежних задач приведены в [истории](PROJECT_SPEC.md#приложение-a-исторический-план-фаз-011).

## Проверка 2026-11-18

В согласованной disposable VM `tempest-lab-ubuntu` (Ubuntu 26.04.1, systemd 259) заново создана топология `tpab-*` из A/B. Использованы прошедший проверку SHA-256 Xray 26.10.31, тот же тестовый VLESS upstream и независимый WireGuard клиент. Клиент CLI обращался к AF_UNIX под отдельными разрешёнными UID/GID. Временные unit использовали те же механизмы systemd с лабораторными путями и identity. Поставляемые значения путей, UID/GID и boot provisioning остаются предметом проверки. Целостность QCOW2 и отклик независимой консоли подтверждены перед испытанием.

| Сценарий | Результат |
|---|---|
| Validate, stage и diff | PASS. Xray listener отсутствовал до apply. Кандидат с DIRECT по умолчанию отклонён, работающий конфиг сохранился. |
| Apply → Verify → Confirm | PASS. Трафик с WG-клиента прошёл 14/14 проб, включая проверку источников DIRECT/VLESS, BLOCK и DNS IPv4/IPv6. Confirm до Verify отклонён. Подтверждение повторно проверило трафик. |
| Права gateway | PASS. UID Xray отличался от UID Agent и клиента IPC. Все пять capability masks равны нулю, `NoNewPrivs=1`, TUN не виден, процесс в `tpab-g`. |
| Рестарт Agent с pending | PASS. Pending отброшен, подтверждённый LKG восстановлен, Verify снова подтвердил реальные потоки. |
| Коллизия на заранее выделенном UDP-порту | PASS. Apply отклонён, при занятом порте TEMPEST оставил ingress закрытым. После удаления fault listener и рестарта Agent безопасный LKG восстановил трафик. |
| Отзыв последнего peer и rollback | PASS. Ingress закрыт. Старый LKG не активировался при rollback и рестарте. Повторный stage того же ключа отклонён, проба с отозванного клиента не прошла. |
| Cleanup | PASS. Удалены только временные unit, polkit-правило, данные и четыре lab namespaces. Снимки PID 1 и исходного `tempest-ns-lab` совпали с baseline, исключение — IPv6 RA `expires` `65424 → 65358`. |

Первый старт схемы выявил, что `PrivateTmp` скрывает временный каталог `/var/tmp` от unit. Повтор использовал выделенный временный каталог вне `/var/tmp`. В первом живом apply после рестарта клиенту потребовалось повторно установить WG-сеанс. Ограниченное повторение реального verifier сохранило проверку readiness при смене процесса. Итоговый прогон завершился с кодом 0. SHA-256 обезличенной сводки `33994579ed0d16b6878f75873da30f110dff7755846eef0a2bd5e748a3097ac9`.

Targeted pytest на Linux с закреплённым Xray прошёл 94 теста. Набор покрывает новый профиль и lifecycle, сквозной AF_UNIX с тестовым runtime, table-only транзакцию, действующие границы Agent/Compose и release pin. Фактическое восстановление после reboot и пробная загрузка QCOW2 — `NOT_RUN`.
