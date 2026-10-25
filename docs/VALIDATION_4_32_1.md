# Ellenőrzés — 4.32.1

## Javított hiba

A felület ajánlott nyolcoszlopos SQL-exportja eddig csak fejléccel működött.
A fejléc nélküli `OSZLOP_NEVE,VARCHAR2,40,,,N,40,B` sor tévesen
„6 mező szükséges, 8 érkezett” hibát okozott. A fejléc nélküli import most
a dokumentált hat- vagy nyolcoszlopos sorrendet fogadja el.

## Python-regressziók

A requirements-test.txt szerinti korábban telepített függőségekkel:

```text
PYTHONPATH=<requirements-test target>:tests:. python3 -m unittest \
  test_schema_csv test_schema_workbench test_schema_workbench_web -v

Ran 45 tests in 8.973s
OK
```

Ellenőrzött: a felhasználó pontos sora; hat- és nyolcoszlopos, valamint fejléces,
átrendezett export; BOM/CRLF/idézőjelek; vessző/pontosvessző/tab; BYTE és CHAR
külön kezelése; NCHAR/NVARCHAR2; NUMBER precision/scale és TIMESTAMP precision;
hibás vagy kevert mezőszám és hibás karakter-metaadat elutasítása; előnézet után
változatlan mentett állapot, majd külön mentés és schema.json-export. A meglévő
sémakezelési, párhuzamos szerkesztési és DUAL-regressziók is sikeresek.
Nem maradt kihagyott teszt.

## Angular és böngésző

- `npm run build:ui`: sikeres Angular 22.1.6 / TypeScript 6.0.3 production build.
  A fő komponens korábbi NG8102 figyelmeztetései megmaradtak; fordítási hiba nincs.
- `tests/unified_assistant_browser.cjs`: sikeres, valódi Chromium + HTTP API +
  migrációs worker, rögzített válaszú Ollama-tesztkiszolgálóval.
- A pontos fejléc nélküli sor fájlként és beillesztett szövegként is kitöltötte
  a szerkesztőt (40 BYTE, nullable=false); a hibás CSV változatlanul hagyta.
- A 400 byte / 100 CHAR oszlop 100 CHAR-ként került mentésre, exportba és az AI
  kérésébe. A PK kézi megjelölése és a külön mentés megmaradt.
- A teljes további munkamenet is sikeres: bemeneti bekötés, AI-feladat,
  oldalfrissítés utáni folytatás, javaslat átnézése és átvétele, ZIP-letöltés,
  valamint a külön frontend origin használata. Böngészős futásidejű hiba nem volt.

## Határok

A teljes történeti tesztcsomag nem futott újra. A próbák nem használtak valódi
Oracle-adatbázist vagy valódi Qwen-válaszokat; az üzleti átültetési pontosságot
ezek a CSV-regressziók nem mérik. Új függőség nem szükséges.
