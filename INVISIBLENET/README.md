# INVISIBLENET — happRouting для Invisible Net VPN

Форк нужен ровно для одного: держать копию upstream-профиля roscomvpn под **нашим
именем профиля**. Всё остальное берётся у hydraponique без изменений.

## Как это работает

```
hydraponique/roscomvpn-routing  ──HTTP──►  build.py  ──►  happ-routing.deeplink
      (HAPP/DEFAULT.DEEPLINK)              (Action)              │
                                                                 │ контейнер
                                                                 │ routing-updater
                                                                 ▼
                                                    PATCH /api/subscription-settings
                                                          (happRouting)
                                                                 │
                                                                 ▼
                                                      клиенты Happ (раз в 12 ч)
```

`build.py` запускается GitHub Action'ом раз в сутки в 07:00 UTC (upstream
обновляется около 06:00) и вручную через **Actions → Invisible Net — sync
happRouting → Run workflow**.

## Что именно патчится

| Поле | Upstream | У нас | Почему |
|---|---|---|---|
| `Name` | `RoscomVPN` | `Invisible Net RU-Direct` | Happ обновляет профиль по совпадению имени. С чужим именем клиенту прилетает **второй** профиль, а старый остаётся висеть в списке |
| `LastUpdated` | их метка | всегда больше предыдущей | Happ игнорирует профиль, если значение не выросло; upstream иногда отдаёт метку старее нашей |

Больше **ничего** не меняется: geo-URL с пинами по тегам, все 22 категории,
DNS, `DnsHosts`, `UseChunkFiles`, `DomainStrategy`, `RouteOrder` — как в upstream.

## Проверки перед записью

`build.py` падает и не коммитит, если:

- upstream отдал не deeplink или битый base64;
- `Name` не наш;
- **какая-либо geo-категория профиля отсутствует** в `geosite.dat`/`geoip.dat` по
  тем URL, на которые ссылается сам профиль;
- deeplink разросся больше 8000 символов (он уезжает HTTP-заголовком).

Последняя проверка — главная. 2026-08-01 у клиентов перестало запускаться ядро
Xray с ошибкой «отсутствует секция TWITCH-ADS в подключенном файле geosite.dat»:
профиль ссылался на категории, которых нет в стандартных geo-сборках. Для Xray
отсутствующая секция — фатальная ошибка старта, не «правило не сработало».

## Если что-то пошло не так

Откат делается на стороне панели, а не здесь: остановить контейнер
(`docker compose down` в `/opt/routing-updater` на мастере), затем вернуть прежний
`happRouting` через API. До клиентов доедет в течение 12 часов
(`Profile-Update-Interval`).

Upstream-workflow `update-configs.yml` из этого форка **удалён намеренно** — он
содержал job деплоя по SSH на серверы владельца upstream.
