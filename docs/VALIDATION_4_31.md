# Ellenőrzés — 4.31, egységes Angular felület

## Angular production build

```text
npm ci
npm run build:ui
```

Angular 22.1.6, TypeScript 6.0.3, Optimus UI 2.0.2 és Tailwind 4.1.14;
a pontos függőségeket a projekt gyökerében lévő package-lock.json rögzíti.
A production build és az Angular `strictTemplates` ellenőrzése sikeres.
A korábbi fő komponensben örökölt, nullish-coalescing típusjelzések figyelmeztetésként
megmaradtak; fordítási hiba nem maradt. A build először törli a generált web-dist
könyvtárat, így régebbi, már nem hivatkozott fő JavaScript-csomag nem kerül az átadásba.

## Teljes böngészős munkamenet

A csomagban lévő production build Chromium 153 alatt futott. Valódi Python API,
feltöltés és migrációs worker, valamint helyi HTTP Ollama-tesztkiszolgáló működött.
A böngésző az elkészült Angular-kódot futtatta, az API és a worker valódi
HTTP-kéréseket kapott.

```text
NODE_PATH=<playwright node_modules> FRM_BROWSER=<chromium> \
  FRM_TEST_PYTHONPATH=<requirements-test target> node tests/unified_assistant_browser.cjs

Unified Angular browser: upload → generation → schema → bindings → needs_context → reload/resume → formatted proposal → reviewed apply → ZIP; separate frontend origin OK
```

Ellenőrzött működés:

- kapcsolat beállítása a közös Angular-felületen;
- egyetlen Ollama-cím és modell mentése, majd ugyanennek használata a Java-kérésben;
- valódi XML-feltöltés, generálás és a modul részleteiből közvetlen váltás az asszisztens fülre;
- üres tábla szerkesztése: NUMBER/VARCHAR2, hossz/CHAR, nullable és PK, majd szerveroldali mentés;
- hiányzó GLOBAL érték képernyőmezőre kötése; tabváltás után megmaradó mentetlen vázlat;
- vázlat mellett tiltott AI-indítás, majd a tényleges Angular-kérés értékének mentése;
- túl kicsi kontextusablak esetén AI-hívás előtti elutasítás és a hiba visszatöltése oldalfrissítés után;
- a kontextuskeret módosítása és böngészőben megjegyzése;
- aktív AI-futás visszatöltése oldalfrissítéskor, majd needs_context megjelenítése;
- projektminta pótlása, formázott teljes Java-javaslat és diff megjelenítése;
- a review-négyzetig tiltott átvétel, majd valódi szerveroldali alkalmazás és friss modul ZIP;
- a böngésző közvetlenül nem hívta az Ollamát, a kérések a Python backendből érkeztek;
- a régi /assistant könyvjelző átirányítása az egységes Angular-felületre;
- külön, 4200-as frontend originről is a beállított Python API-t használó asszisztens;
- CORS-on keresztül végrehajtott táblaszerkesztési POST a külön frontend originről;
- nem volt böngészős futásidejű hiba. A felületről belső vizuális ellenőrzés is készült.

## Kapcsolódó Python-regressziók

```text
FRM_TSC=<typescript/bin/tsc> PYTHONPATH=<webes tesztfüggőségek>:tests python -m unittest \
  test_backend_assistant test_backend_assistant_web test_schema_workbench \
  test_schema_workbench_web test_input_bindings test_input_bindings_web \
  test_assistant_format \
  test_web_api.WebTests.test_cors_preflight_exact_localhost_origin_and_headers \
  test_web_api.WebTests.test_foreign_origin_simple_form_and_dns_rebinding_rejected -v

Ran 59 tests in 43.278s
OK
```

A korábbi szerkezetbekérő, bemeneti bekötés, lenyomatok, formázás és Java-átvétel
ellenőrzései sikeresek. Az API-tesztek az új Angular belépési pontot, a régi könyvjelző
átirányítását, az aktív futás visszatöltését és a helyi kérésvédelmet is vizsgálták.

## Határok

A Qwen tényleges kódolási minőségét és a belső hálózati laptop elérhetőségét
ez a környezet nem mérte. A böngészős teszt AI-válaszai rögzített tesztválaszok.
Nem futott valódi Oracle-adatbázis vagy a teljes céges alkalmazás üzleti,
PMD/Checkstyle és integrációs tesztje. Az átvett kódot a céges projektben is
fordítani és az eredeti Forms-folyamattal összehasonlítva tesztelni kell.
