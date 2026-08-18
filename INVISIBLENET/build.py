#!/usr/bin/env python3
"""Собирает happRouting-профиль Invisible Net на основе upstream roscomvpn.

Берёт HAPP/DEFAULT.DEEPLINK из hydraponique/roscomvpn-routing, меняет ровно одно
поле — Name — и перекладывает результат в INVISIBLENET/happ-routing.deeplink,
откуда его забирает контейнер routing-updater и кладёт в панель Remnawave.

Зачем менять имя: Happ обновляет профиль по совпадению Name. С upstream-именем
"RoscomVPN" клиенту прилетает ВТОРОЙ профиль, а прежний остаётся висеть в списке
(проверено на устройстве 2026-08-01).

LastUpdated всегда растёт: Happ игнорирует профиль, у которого значение не больше
предыдущего, а upstream может отдать метку старее нашей.

Перед записью проверяется, что каждая geo-категория профиля реально присутствует
в geosite.dat/geoip.dat по тем URL, на которые сам профиль и ссылается. Если
upstream уедет на категорию, которой в файлах нет, — Xray у клиента не стартует
вообще, поэтому здесь скрипт падает и битое до панели не доезжает.

Публикуем НЕ каждый раз, когда шевельнулся upstream. В теге Geoipurl зашита дата,
и roscomvpn-geoip выкладывает новый тег ЕЖЕДНЕВНО, тогда как сами правила и
geosite заморожены с апреля. Каждая публикация заставляет Happ переимпортировать
профиль, а в зазоре «правила применены — geo ещё качаются» ядро Xray не стартует
и клиент видит «не найдена категория torrent»: четырёх секций (torrent, whitelist,
twitch-ads, geoip:direct) нет в базовой Loyalsoldier, с которой Happ живёт до
докачки roscomvpn. То есть окно отказа повторялось каждый день на ровном месте.
Поэтому: если относительно прошлой публикации изменился ТОЛЬКО тег geoip —
пропускаем, пока закреплённой базе не исполнится GEOIP_MAX_AGE_DAYS. Устаревший
на месяц geoip безвреден: это списки IP РФ, они дрейфуют медленно и ядро не роняют.
"""

import base64
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

UPSTREAM = ("https://raw.githubusercontent.com/hydraponique/roscomvpn-routing"
            "/main/HAPP/DEFAULT.DEEPLINK")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "happ-routing.deeplink")
PREFIX = "happ://routing/onadd/"
OUR_NAME = "Invisible Net RU-Direct"

# Поля, расхождение по которым не считается изменением содержимого.
VOLATILE = {"Name", "LastUpdated"}

# Сколько дней держим закреплённый geoip, пока upstream ежедневно выкладывает
# новый тег. Обновимся, когда базе станет столько дней — либо раньше, если
# в профиле изменится что-то кроме этого тега.
GEOIP_MAX_AGE_DAYS = 30


def fetch(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "invisiblenet-sync"})
    data = urllib.request.urlopen(req, timeout=120).read()
    return data if binary else data.decode()


def decode(deeplink):
    if not deeplink.startswith(PREFIX):
        raise SystemExit(f"не deeplink: {deeplink[:60]!r}")
    b = deeplink[len(PREFIX):].strip()
    b += "=" * (-len(b) % 4)
    return json.loads(base64.b64decode(b))


def encode(profile):
    body = json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode()
    return PREFIX + base64.b64encode(body).decode()


def varint(buf, i):
    val = shift = 0
    while True:
        byte = buf[i]
        i += 1
        val |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return val, i
        shift += 7


def sections(blob):
    """Имена секций из geosite.dat/geoip.dat (protobuf: repeated entry = 1,
    внутри country_code = 1)."""
    found = set()
    i = 0
    while i < len(blob):
        key, i = varint(blob, i)
        field, wire = key >> 3, key & 7
        if wire == 2:
            length, i = varint(blob, i)
            payload = blob[i:i + length]
            i += length
            if field == 1 and payload:
                inner, j = varint(payload, 0)
                if inner >> 3 == 1 and inner & 7 == 2:
                    slen, j = varint(payload, j)
                    found.add(payload[j:j + slen].decode("utf-8", "replace").lower())
        elif wire == 0:
            _, i = varint(blob, i)
        elif wire == 5:
            i += 4
        elif wire == 1:
            i += 8
        else:
            break
    return found


def geoip_age_days(url):
    """Возраст закреплённой geoip-базы в днях по тегу вида @202608180355.

    Возвращает None, если тег непривычной формы — тогда решение о пропуске
    публикации не принимаем и ведём себя как раньше (публикуем)."""
    match = re.search(r"geoip@(\d{12})\b", url or "")
    if not match:
        return None
    try:
        stamp = datetime.strptime(match.group(1), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - stamp).days


def validate(profile):
    if profile.get("Name") != OUR_NAME:
        raise SystemExit(f"Name не наш: {profile.get('Name')!r}")

    print(f"  geosite: {profile['Geositeurl']}")
    print(f"  geoip  : {profile['Geoipurl']}")
    pools = {
        "geosite": sections(fetch(profile["Geositeurl"], binary=True)),
        "geoip": sections(fetch(profile["Geoipurl"], binary=True)),
    }
    print(f"  секций: geosite={len(pools['geosite'])} geoip={len(pools['geoip'])}")

    missing = []
    total = 0
    for key in ("DirectSites", "DirectIp", "ProxySites", "ProxyIp",
                "BlockSites", "BlockIp"):
        for ref in profile.get(key, []):
            total += 1
            kind, _, name = ref.partition(":")
            # "yandex@ads" — атрибут-фильтр; ядру нужна сама секция "yandex".
            base = name.split("@")[0].lower()
            if kind not in pools:
                missing.append(f"{ref} (неизвестный префикс)")
            elif base not in pools[kind]:
                missing.append(ref)
    if missing:
        raise SystemExit("ОТСУТСТВУЮТ В GEO-ФАЙЛАХ: " + ", ".join(missing))
    print(f"  все {total} geo-ссылок на месте")

    link = encode(profile)
    if len(link) > 8000:
        raise SystemExit(f"deeplink {len(link)} символов — уедет HTTP-заголовком, слишком много")
    return link


def main():
    print("upstream:", UPSTREAM)
    upstream = decode(fetch(UPSTREAM))
    print(f"  получен профиль {upstream.get('Name')!r}, "
          f"LastUpdated={upstream.get('LastUpdated')}")

    previous = decode(open(OUT).read()) if os.path.exists(OUT) else None

    if previous is not None:
        a = {k: v for k, v in upstream.items() if k not in VOLATILE}
        b = {k: v for k, v in previous.items() if k not in VOLATILE}
        if a == b:
            print("содержимое upstream не изменилось — коммитить нечего")
            return 0

        # Единственное расхождение — ежедневный тег geoip? Такая публикация не
        # несёт клиенту ничего, кроме переимпорта профиля и окна, в котором ядро
        # не стартует. Держим закреплённую базу, пока она не состарится.
        if ({k: v for k, v in a.items() if k != "Geoipurl"}
                == {k: v for k, v in b.items() if k != "Geoipurl"}):
            age = geoip_age_days(previous.get("Geoipurl"))
            if age is not None and age < GEOIP_MAX_AGE_DAYS:
                print(f"изменился только тег geoip; закреплённой базе {age} дн. "
                      f"(лимит {GEOIP_MAX_AGE_DAYS}) — публиковать нечего")
                return 0
            print(f"изменился только тег geoip, базе {age} дн. — пора обновить")

        changed = sorted(set(a) ^ set(b)) or sorted(k for k in a if a[k] != b.get(k))
        print("изменилось:", ", ".join(changed))

    profile = dict(upstream)
    profile["Name"] = OUR_NAME
    # Строго больше и нашего прошлого значения, и upstream-метки.
    floor = max(int(upstream.get("LastUpdated") or 0),
                int(previous.get("LastUpdated") or 0) if previous else 0)
    profile["LastUpdated"] = str(max(int(time.time()), floor + 1))

    print("валидация:")
    link = validate(profile)

    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(link + "\n")
    print(f"записан {OUT} ({len(link)} символов), LastUpdated={profile['LastUpdated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
