# TEMPEST — статус форка

## 2026-11-10 · этап 0

**READY_FOR_REVIEW.** Этап 0 подготовлен к приёмке владельцем. Этап 1 заблокирован до явного подтверждения.

- Исходный PiTun v1.7.8 — `b75223d621395e7b641a77bcff61cd8647deb207`. Fork parent, upstream master и tag сверены. Сохраняем FastAPI, React, SQLite, генерацию Xray и upstream-тесты. [Baseline и команды](UPSTREAM_BASELINE.md).
- Сохраняем шаблоны управляемого proxy routing, diagnostics, snapshot/watchdog. Для будущего MVP исключаем из доверенной зоны API системный DNS, WAN/DHCP/Wi-Fi, Docker lifecycle, SSH deploy и управление внешними панелями. [Карта привилегий](HOST_PRIVILEGE_AUDIT.md).
- Локальные WG/AWG/VLESS ingress в PiTun отсутствуют. Критические разрывы — привилегии API, неявный DIRECT, схема TUN и качество UDP health signal. [Матрица](TEMPEST_GAP_MATRIX.md).
- ADR-001 предлагает сначала проверять namespaced dataplane, оставить host agent для сценария с доказанной потребностью в host netns. Выбор и минимальный набор capabilities ещё требуют Linux VM. [ADR-001](adr/ADR-001-privilege-separation.md).
- Backend: 294 passed, 1 skipped, 1 deselected в выбранном наборе. Frontend: 142 passed и сборка успешна. ESLint не запускает проверки без конфигурации. Сетевой E2E и проверка production-пакетов остаются отдельными задачами.

Аудит изменяет только документацию. SHA коммита аудита можно получить командой `git log -1 --format=%H -- docs/UPSTREAM_BASELINE.md`. Возврат после коммита — обычный `git revert <SHA коммита аудита>`; сетевой rollback для этого этапа не требуется.
