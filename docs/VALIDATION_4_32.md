# Ellenőrzés — 4.32

## Célzott Python-regressziók

A csomag requirements-test.txt fájljának pontos verzióival futtatva
(FastAPI 0.141.1, Starlette 1.6.0, Pydantic 2.13.5, Uvicorn 0.53.0, HTTPX 0.28.1):

```text
FRM_TSC=<typescript/bin/tsc> PYTHONPATH=<requirements-test target>:tests:. python -m unittest \
  test_schema_csv test_schema_workbench test_backend_assistant \
  test_schema_workbench_web test_backend_assistant_web \
  test_input_bindings test_input_bindings_web test_assistant_format \
  test_web_api.WebTests.test_cors_preflight_exact_localhost_origin_and_headers \
  test_web_api.WebTests.test_foreign_origin_simple_form_and_dns_rebinding_rejected -v

Ran 76 tests in 42.317s
OK
```

Nem maradt kihagyott teszt. A CSV-formátum túlhosszú mezőjének utolsó javítása után
az érintett 18 CSV/séma webteszt ismét sikeres (2.802 s).

Új esetek: a felhasználó hatoszlopos exportja; fejléc/BOM/CRLF/idézőjelek;
vessző/pontosvessző/tab; pontos CHAR_LENGTH/CHAR_USED; NUMBER tárolási hossz és
precision külön kezelése; timestamp precision; negatív NUMBER scale; ismeretlen
típus, hibás nullable, ismétlődő oszlop, kevert séma, elavult revízió és hibás CSV
elutasítása; import mint vázlat, majd valódi séma-mentés/export; DUAL/SYS.DUAL
kihagyása a referenciákból, régi metaadatokból és JDBC-segédekből; kötelező
beépített AI-feladat üres felhasználói prompt mellett és opcionális CLI-kiegészítés.
A meglévő Java-szintaxis-, javaslatátvételi, bemenetbekötési, újrafuttatási,
TypeScript strict és determinisztikus formázási ellenőrzések is sikeresek.

## Angular production build

```text
npm ci
npm run build:ui
```

Sikeres Angular 22.1.6 / TypeScript 6.0.3 strictTemplates production build.
A kész web-dist ugyanazt a két Angular-forrást tartalmazó projektet futtatja.
A fő komponens 11 korábbi NG8102 figyelmeztetése megmaradt; fordítási hiba nincs.
A node_modules és a build cache nem része az átadott csomagnak.

## Valódi böngészős munkamenet

Chromium 153; valódi HTTP API, XML-feltöltés és migrációs worker; helyi, rögzített
válaszú HTTP Ollama-tesztkiszolgáló. A csomag production buildjével:

```text
NODE_PATH=<playwright node_modules> FRM_BROWSER=<chromium executable> \
  FRM_TEST_PYTHONPATH=<requirements-test target> node tests/unified_assistant_browser.cjs

Unified Angular browser: upload → generation → CSV file/paste/reject/discard → schema save → explained bindings → automatic prompt → needs_context → reload/resume → formatted proposal → reviewed apply → ZIP; separate frontend origin OK
```

Ellenőrzött működés:

- CSV-fájl kiválasztásakor automatikus kitöltés, pontos precision/scale és üres PK;
- import előtt/után a szerveroldali mentés csak a mentés gombbal történik;
- vázlat elvetése, hibás beillesztett CSV után változatlan táblázat;
- fejléc nélküli CSV beillesztése, PK kézi megjelölése, séma mentése;
- a széles táblázatok a panelen belül görgethetők, nem szélesítik ki az oldalt;
- GLOBAL érték képernyőmezőhöz kötése és mentése, vázlat megőrzése tabváltáskor;
- AI-feladatíró mező nélkül ténylegesen elküldött teljes beépített prompt;
- a mentett CSV-séma és értékforrás bekerül a modell HTTP-kérésébe;
- túl kicsi kontextusablak elutasítása és hiba-visszatöltés oldalfrissítés után;
- aktív AI-futás visszatöltése, needs_context, projektminta pótlása, Java-diff;
- átnézés utáni átvétel és friss ZIP-letöltés;
- külön 4200-as Angular originről is a beállított Python API használata;
- a böngésző nem hívja közvetlenül az Ollamát; böngészős futásidejű hiba nem volt.

A panelről belső vizuális ellenőrzés is készült.

## Határok

Célzott regressziók futottak; a teljes történeti tesztcsomagot ebben a körben nem
futtattuk újra. Az AI-válaszok tesztválaszok, ezért ezek nem igazolják a Qwen
üzleti átültetési pontosságát. Nem futott valódi Oracle vagy a teljes céges
alkalmazás. Az átvett kódot a céges projektben fordítani és az eredeti Forms
folyamattal összehasonlítva tesztelni kell.
