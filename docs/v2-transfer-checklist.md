# Перенесення погоджених рішень V2 у V1

Донор: `dragonidf/ha-heating-2`, PR #43, commit
`dc292e280ac492dcf4cf97d049688f2b40199cb5`.
Основа: чинний V1 `dragonidf/ha-heating-ai` 0.3.23.
Перелік відновлено з редакції 2 погодженого ТЗ від 12.11.2026.
Переносимо перевірені підходи; solver, синтетичні джерела та повний UI V2 не копіюються.

| Пункт | Джерело V2 | Поточний стан |
|---|---|---|
| Якість джерел | `packages/ha_native/kws.py`, `voltage.py`, `source_activity.py` | KWS-облік і правило нулів V1 збережені; потрібно завершити явні value/report timestamps і provenance без вигаданого raw ACK |
| Короткий safety reducer | `packages/control/kernel.py` | Напруга та one-writer handoff реалізовані; потрібно завершити незалежний memory-only safety pass і manual epoch перед останнім dispatch |
| Критичні команди | `releases/ha_v2/.../critical_ledger.py` | Донорський журнал є в preview; production prepare/sent/HA echo/unknown та crash reconciliation ще не підключені |
| Фонові обчислення | kernel/single-flight worker | Immutable snapshot, single-flight і stale rejection V1 збережені; потрібна остаточна перевірка timeout/черги та незалежності STOP від I/O |
| Захищений backend/CAS | `native_api.py`, `native_store.py` | V1 має revision/readback; потрібен versioned admin-only production boundary для критичних змін |
| Fan arbitration | `packages/fan_policy` | Повна fireplace/night/backup policy V1 збережена; потрібно закрити external manual hold/STOP перед dispatch |
| UI та кеш | live timeline та content hashes | Версійований content-hash loader готовий; погоджений перший графік −6h/+24h, ручний AUTO та окрема історія ще потребують завершення |
| Міграція/parity | `packages/migration/rehearsal.py` | Імпорт зі checksum, незмінний оригінал, повний archive exporter та offline saved-reference parity реалізовані; фактичний cutover ще не виконаний |

До закриття пунктів production-реліз не вважати завершеним. Дані V1,
helper IDs, енергію, теплову математику, one-shot, камін і зовнішні apps зберігати.
AUTO, свідомо обраний користувачем, не є аварійним fallback. HA echo не є hardware ACK.
