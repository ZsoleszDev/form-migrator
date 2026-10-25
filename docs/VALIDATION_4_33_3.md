# Ellenőrzés — 4.33.3

## Kezelt hibák és viselkedés

A kapott konzolnapló Angular `Maximum call stack size exceeded` hibákat és
HTTP 404-es Ollama-választ tartalmazott. A korábbi szerkesztő az összecsukott
haladó panel összes vezérlőjét is létrehozta, és a CSV minden oszlopát egyszerre
rajzolta ki. A nagy leltárral indított régi fejlesztői felület próbája elakadt.

A friss változat az összecsukott panel vezérlőit nem hozza létre. A két szerkesztő
10 soros oldalakra bontja a megjelenítést, az adatok a teljes vázlatban megmaradnak.
Lapozás utáni módosítás/törlés az oszlop stabil azonosítójával történik.

Az AI-módválasztó megszűnt. A beépített Angular-felület mindig assist módot és
legalább 1 elemzési kérésnyi keretet küld; a régi mentett off/0 nem írja felül.
A rövid generáláskori tanácsadás és a táblaszerkezetek utáni tényleges Java-átírás
külön munkalépés maradt, közös Ollama-kapcsolattal.

Mentéskor a Python backend GET /api/tags kéréssel ellenőrzi a szervert és a
modell nevét. Ez nem inferenciapróba. A teljes API-útvonal alapcímre normalizálódik;
a port és a proxy előtagja megmarad. HTTP-hibánál a pontos kért végpont látható.
A felhasználó gépén kapott 404 konkrét oka az IP/port és a szerver naplója nélkül
nem dönthető el; nem állítjuk, hogy kizárólag a hiányzó port okozta.

## Célzott Python-tesztek

```text
PYTHONPATH=<requirements-test target>:tests:. python3 -m unittest \
  test_ollama_connection test_backend_assistant \
  test_backend_assistant_web test_ollama_export -v

Ran 43 tests in 38.198s
OK
```

Kihagyott teszt nincs a fenti futásban. Öt új HTTP-kapcsolatteszt ellenőrzi az
alapcímet, a teljes /api/chat címet, a proxy előtagját, a modell hiányát, a
hiányos szerverválaszt és a HTTP-hiba tényleges URL-jét. A próba nem indít chatet.
A Java-asszisztens beillesztése, a teljes metódus/entity bemenet, a cache,
a kimeneti hibák és az Ollama HTTP-hiba továbbra is megfelelően működnek.

A régi általános test_web_api modul futtatását is megpróbáltuk, de nem tekinthető
sikeresnek: a kapott forráscsomagból hiányzik az általa használt examples könyvtár.
A fenti célzott tesztek saját, önálló fixture-öket használnak.

## Angular és valódi böngésző

Mindkét build sikeres:

```text
npm run build --workspace web-ui -- --configuration=development
npm run build:ui
```

A korábban is meglévő főkomponensbeli NG8102 buildfigyelmeztetések megmaradtak.
A tests/unified_assistant_browser.cjs mindkét builddel sikeres valódi Chromium,
Python API/worker és HTTP Ollama-tesztkiszolgáló mellett:

- a közös kapcsolat mentése a backend felől ellenőrzi a modelllistát;
- /api/chat végű cím beillesztése alapcímre normalizálódik;
- a régi mentett kikapcsolt mód és 0 kérés mellett is assist/pozitív keret kerül a feladatba;
- az AI-módválasztó nincs jelen;
- 600 további bemeneti sor zárt panel mellett nem hoz létre vezérlőket;
- megnyitáskor 10 sor jelenik meg, bezáráskor a vezérlők megszűnnek;
- 200 oszlopos CSV lapozása, utolsó oldali módosítás megőrzése és vázlat elvetése;
- a felhasználó pontos 8 oszlopos CSV-sora, PK, CHAR/BYTE és sémamentés;
- HTTP 422, háttérhiba és oldalfrissítés utáni visszatöltés;
- AI-javaslat, service/entity/repository/DTO beillesztése és friss ZIP;
- Controller megőrzése és külön Angular-origin;
- nincs RangeError, Angular NG futásidejű hiba vagy disabled + reactive form figyelmeztetés.

## Határ és frissítés

Nem történt valódi Qwen-inferencia, Oracle/Hibernate futtatás vagy teljes céges
build. A felhasználó belső Ollama-gépét ez a környezet nem éri el.

A kész csomag production felületet tartalmaz. A Python backendet a friss csomagból
kell újraindítani, mert új kapcsolatellenőrző API is készült. Saját Angular-projektben
mind a migrator-app.ts, mind a migrator-assistant.ts cserélendő.
