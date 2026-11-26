# AWG egress — передача на TORNADO, 27.11.2026

Ветка `wip/awg-egress`; базовый SHA `93feeab084e2a66e1371e7f503baede6d1f32c6d`. SHA WIP-коммита: `git rev-parse HEAD` после checkout этой ветки. `master` не менялся. WIP не является функциональным checkpoint; подробные результаты — [STATUS.md](STATUS.md), границы — [WORKING_CORE_AWG.md](WORKING_CORE_AWG.md). Перед разработкой прочитать PRODUCT_GOALS и PROJECT_SPEC.

## Реализовано и проверено

- Импорт и защищённое хранение AWG, выбор выхода в панели, Xray SOCKS → awgproxy, явные VPN/BLOCK/DIRECT, IPv6 BLOCK для IPv4-only, lifecycle, Agent IPC, LKG/rollback и ручная проверка доступности egress.
- В disposable VM реальный WG-клиент в отдельном namespace прошёл через AWG: внешний IPv4 совпал с AWG SOCKS, HTTPS и UDP NTP прошли; переход VLESS → AWG не изменил ключ peer. Проверены BLOCK, разрешённый DIRECT, отказ AWG/relay без DIRECT fallback, rollback и reboot. Таймауты с последующими успешными повторами остаются ограничением. Внешний клиент вне VM и публикация UDP ingress не проверены.
- Последнее известное состояние `tempest-lab-ubuntu`: Agent/Xray/AWG и лабораторный relay активны; active/LKG generation 29, pending отсутствует, три отозванных ключа сохранены. Панель и API запущены. Состояние и секреты остаются на TYPHOON в защищённом хранилище VM, не в Git. Холодная копия `~/.local/share/libvirt/images/tempest-lab/recovery-awg-20261126/` предшествует отзывам: не восстанавливать без сверки актуального журнала отзывов. Копия находится на том же физическом диске.

## Блокер DNS

Простые запросы клиента TCP/UDP проходят, но Xray переписывает upstream на UDP и большой TCP DNSKEY ответ приходит с TC. Полноценный DNS не подтверждён. Прямой TCP DNS через AWG SOCKS к испытанным resolvers отклонялся; отдельная TLS-проба к DoT через SOCKS однажды прошла. Попытка встроить DNS-over-TLS через Xray DNS outbound и dialerProxy не дала ответов по TCP/UDP; кандидат отклонён, эксперимент удалён, возвращён UDP LKG и предупреждение панели.

Следующий шаг: исследовать DNS/TLS dialer Xray и возможность полноценного TCP DNS через существующую цепочку; при необходимости оценить проверенный DNS-прокси с SOCKS/TLS. Сохранить fail-closed, LKG и не восстанавливать отозванные ключи. Не считать наличие простого ответа доказательством полноценного DNS.

## Код и проверки

Основные файлы: `backend/app/core/awg_bridge.py`, `core/working_core.py`, `network_agent/xray_profile.py` (генерация), `xray_agent.py` (транзакции/проверка), `xray_runtime.py` (lifecycle), `models.py` и `xray_client.py` (IPC), `backend/app/working_core.py` (API), `frontend/src/pages/WorkingCore.tsx` (панель); unit/политика в `deploy/systemd/`, закреплённая сборка и лабораторные сценарии — `scripts/build-awgproxy.sh`, `scripts/awgproxy-no-route-monitor.patch`, `scripts/lab/working-core-awg-*`. Не переносить lab relay и адреса как production конфигурацию.

Минимум после изменения DNS: целевые pytest `test_xray_agent.py`, `test_awg_bridge.py`, `test_working_core_providers.py` и применимые frontend checks; в разрешённой VM — реальные DNS TCP/UDP, включая большой DNSSEC-ответ без усечения, и проверка отказа upstream без утечки/DIRECT fallback и корректного LKG/rollback. Повторять другие сетевые испытания только если затронуты соответствующие компоненты. Последний локальный целевой срез: 31 passed, 1 skipped (нет заданного test Xray), 1 исключён из-за sandbox AF_UNIX; полный прогон не завершён. CI/CodeQL для WIP не проверены.

## До первого развёртывания на VORTEX

Завершить DNS и обязательные end-to-end критерии G01/G02/G06/G09/G10, включая внешний WG-клиент и UDP ingress, безопасный rollback и отзывы, проверить packaging и лицензии, выполнить функциональный checkpoint и CI/CodeQL. Затем отдельно согласовать read-only инвентаризацию, конкретные селекторы, изолированный план установки, резервирование/независимую консоль и rollback согласно [FIRST_VORTEX_DEPLOYMENT.md](FIRST_VORTEX_DEPLOYMENT.md). Любое изменение VORTEX требует отдельного разрешения; существующие сервисы и глобальную сеть не затрагивать.
