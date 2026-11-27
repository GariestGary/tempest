# Production verifier: подготовленный кандидат

[`working-core-probe.py`](../scripts/deploy/working-core-probe.py) — кандидат для существующего `tempest-xray-probe.service`; он **не установлен и не проверен реальным трафиком**, поэтому не использовать его для подтверждения generation до завершения provisioning и независимой проверки. Он не меняет Agent, `/core/apply`, таймауты существующего unit или механизм Verify/Confirm.

Проверки через отдельный WireGuard probe peer: DNS A по TCP/UDP, DNSKEY по TCP без TC и размером >512 байт, HTTPS с проверкой сертификата и сверкой наблюдаемого внешнего IPv4, DIRECT и VPN TCP/UDP, BLOCK TCP/UDP. Все destinations, интерфейсы, адреса и ожидаемые источники задаются оператором в root-owned `/etc/tempest/working-core-probe.json`; значения лаборатории в программу не встроены. Никакие секреты не печатаются. Проверка маршрута выполняется через `ip route get` до каждого сетевого запроса; ошибки, неверный config, соединение refused или недоступный control считаются FAIL.

Чтобы недоступная BLOCK-цель не выглядела как успешная блокировка, требуется **независимый контрольный маршрут к той же цели и порту**, отличный от WG-интерфейса. Программа проводит положительный TCP/UDP echo контроль по нему до и после BLOCK-проб; сами BLOCK-пробы используют WG source и проверенный WG route. Это не перенос лабораторного relay. Подготовлен минимальный кандидат [echo fixture](../scripts/deploy/working-core-echo.py): только явно выбранный IPv4/порт, allowlist точных источников и случайные маркеры; wildcard и произвольный payload отвергаются. Он ещё не развёрнут и не проверен сетевым испытанием. Probe namespace, policy routing двух источников и доверенный TCP/UDP echo target ещё необходимо создать и проверить. Контрольный путь относится только к пробному peer и не должен открывать DIRECT для пользовательских клиентов.

Форма файла, намеренно без рабочих адресов, ключей или заданных поставщиков:

```json
{
  "wg_interface": "<wg-interface>", "wg_source": "<probe-wg-ipv4>",
  "control_interface": "<control-interface>", "control_source": "<control-ipv4>",
  "control_expected_source": "<fixture-sees-control-ipv4>",
  "dns_server": "<client-dns-ipv4>", "dns_name": "<dns-test-name>",
  "https_address": "<https-target-ipv4>", "https_name": "<https-certificate-name>",
  "direct": {"address": "<direct-fixture-ipv4>", "port": 18080, "expected_source": "<fixture-sees-direct-ipv4>"},
  "vpn": {"address": "<vpn-fixture-ipv4>", "port": 18080, "expected_source": "<fixture-sees-vpn-ipv4>"},
  "block": {"address": "<block-fixture-ipv4>", "port": 18080}
}
```

Эти placeholders — не готовый профиль. HTTPS endpoint должен поддерживать `/cdn-cgi/trace` с полем `ip=` и достоверным TLS-сертификатом; IP выхода должен совпадать с источником, наблюдаемым fixture на VPN-пути. Фиксация адреса AWG в verifier может требовать обновления при смене исходящего IP; произвольный «любой глобальный IP» не считается доказательством. Диапазоны документации RFC 5737 отвергаются. Конфигурацию можно создавать только после выбора и проверки контролируемых ресурсов.

Локальные tests проверяют схему и отказ при отсутствии независимого контроля, ошибке маршрута, connection refused и усечённом DNSKEY. Это не подтверждает реальные маршруты, доступность fixture, systemd sandbox или совместимость с `TimeoutStartSec=5s`. Время выполнения, поведение при отказах и защита от подмены fixture требуют проверки до установки и подтверждения поколений. Включать этот verifier без этих доказательств запрещено; остаётся действовать fail-closed.
