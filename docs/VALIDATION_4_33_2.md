# Ellenőrzés — 4.33.2

## A módosítás

Az AI-kérésben a kiválasztott teljes DPS ServiceImpl metódus, a benne lévő
Oracle-forrás és az érintett entityk teljes Java-forrása szerepel. A prompt
leírja az entity-alapú repositoryk, a meglévő service-metódus és a szükséges
kiegészítő DTO elkészítését. A repository és a bekötési változó nevét a migrátor adja.

A teljes modul DTO-/XML-/képernyőleltára, hívásgráfja és a megismételt séma-/
triggerforrás helyben marad. A felhasználó által külön megadott forrás továbbra
is elküldhető. A teljes belső csomagra vonatkozó 160 KB-os korlát megszűnt.

## Célzott Python-tesztek

```text
PYTHONPATH=<requirements-test target>:tests:. python3 -m unittest \
  test_backend_assistant test_jpa_assistant \
  test_backend_assistant_web test_schema_workbench -v

Ran 52 tests in 75.436s
OK
```

Kihagyott teszt nincs. A célzott regresszióban több mint 500 000 byte belső
XML- és DTO-adat mellett is létrejött az Ollama-kérés, 20 000 byte alatti
bemenettel. A kiválasztott metódus a legújabb kézi kommentmódosítással együtt,
karakterre változatlanul került át. Az entity tesztje a teljes forrás azonosságát
és a fölösleges leltárak hiányát ellenőrzi.

A ténylegesen nagy metódusra külön teszt ellenőrzi a teljes forrás megtartását,
a hálózat előtti tokenbecslést és a konkrét num_ctx-javaslatot. A rendszer
nem ígér a modell ablakánál nagyobb forrásra csonkolásmentes feldolgozást.

A korábbi javaslat-/átvétel-/cache-/módosulásellenőrzések, a sémakezelés,
a háttérhiba-visszajelzés és a legacy JDBC CLI regressziói is sikeresek.
A JPA entity/repository/DTO/service minta Java 11-es fordítása és futtatása
API-helyettesítők mellett sikeres; az adat, NULL és hiányzó sor ágakat ellenőrzi.

## Angular és böngésző

Sikeres `npm run build:ui` production build. A korábban is meglévő NG8102
figyelmeztetések megmaradtak; a fordítás sikeres.

Sikeres `tests/unified_assistant_browser.cjs` valódi Chromium, Python HTTP API,
migrációs worker és Ollama-tesztkiszolgáló mellett:

- CSV fájl és beillesztés, a felhasználó pontos nyolcoszlopos sora, CHAR/BYTE és PK;
- a tényleges AI-kérés csak a kiválasztott metódust és entityt tartalmazza;
- HTTP 422 és háttérhiba látható a gomb mellett, oldalfrissítés után is;
- javaslat, service/entity/repository/DTO beillesztése, friss ZIP;
- Controller megőrzése és külön Angular-origin használata.

## Határ

Nem futott valódi Qwen-inferencia, Oracle/Hibernate vagy teljes céges build.
A böngésző az összeállított production felületet használta. A belső Ollama-
géppel való kapcsolat és az adott üzleti trigger helyessége itt nem vizsgálható.
