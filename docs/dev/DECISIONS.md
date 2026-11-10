# Журнал решений среды разработки

## DEV-001 — safe-mode профиль разработки

**Статус:** принято для локальной разработки на этапе 1; не заменяет ADR-001 и не утверждает production архитектуру.

- Opt-in `DEV_SAFE_MODE=1` — единственная причина включения dev-safe ветки; production по умолчанию сохраняет существующее поведение.
- Safe startup готовит только локальную БД/default rows. Любые API, не вошедшие в явный allowlist безопасных операций, отклоняются.
- Dataplane — явный `mock-noop`: он никогда не заявляет, что VPN работает. NetworkAgent/IPC не реализуются в этой фазе.
- Compose работает без host namespaces, capabilities, Docker socket и системных mounts; контейнеры непривилегированные и read-only, порты доступны только через loopback.
- Проверка реального Compose runtime и неизменности host firewall ожидает доступного изолированного Docker/Linux VM стенда.

Подробности и команды: [локальная dev-среда](../DEV_SAFE_MODE.md), [Linux VM](LINUX_VM.md). ADR-001 остаётся `PROPOSED` до namespace/WG проверки и владельческой приёмки.
