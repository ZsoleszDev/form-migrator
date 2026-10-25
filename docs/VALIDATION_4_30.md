# Ellenőrzés — 4.30

## Kapcsolódó tesztcsomag

```text
FRM_TSC=<typescript/bin/tsc> PYTHONPATH=<webes tesztfüggőségek>:tests python -m unittest \
  test_input_bindings test_input_bindings_web test_assistant_format \
  test_backend_assistant test_backend_assistant_web test_java_style test_java_imports \
  test_schema_workbench test_schema_workbench_web test_manual_action_source \
  test_compact_backend test_ollama_export test_screen_component test_query_actions \
  test_automation.AutomationTests.test_dictionary_sql_and_import_round_trip \
  test_automation.AutomationTests.test_dictionary_key_and_columns_are_used_only_when_the_form_lacks_them -v

Ran 129 tests in 56.496s
OK (skipped=1)
```

128 sikeres teszt; a külön Angular compilerhez kötött `strictTemplates` teszt
kimaradt, mert ebben a környezetben az Angular compiler nem volt telepítve.
A TypeScript szigorú ellenőrzése a céges típusok tesztmásolataival sikeresen futott.

Ellenőrzött viselkedés:

- helyi Java-formázás, importok, kapcsos zárójelek, szövegliterál és formázási idempotencia;
- meglévő explicit projekt-/javax-import és csomagnév megőrzése;
- a formázó nem módosítja a generátor globális állapotát;
- egy modellválasz után formázott előnézet, majd azonos bájtok átvétele;
- szimulált formázási hiba után nincs további modellkérés;
- formázási konfiguráció és bemeneti bekötés módosulása érvényteleníti a régi javaslatot;
- generált képernyő tényleges Node-futtatása: kérésblokkok, globális érték,
  modulparaméter, hiányzó régió, nagy decimális szöveg, checkbox-kód és helyi dátum;
- mentett bekötések CLI-konfigurációs körútja és webes Újrafuttatása;
- valós FastAPI feltöltés/worker, helyi kérésfejléc, friss ZIP és AI-kontextus;
- hibás forrás és jogosulatlan SYSTEM-/konstruktor-/hívó nélküli automatikus bekötés elutasítása;
- elavult revízió, kézi komponensmódosítás, aktív AI-futás és többfájlos írási hiba kezelése;
- újrageneráláskor a képernyő megőrzése és az eltérő bemeneti szerződés jelzése;
- a korábbi sémafelismerés, táblaszerkesztés, Java-átvétel és dictionary-folyamat regressziói;
- Java 11 célverziójú fordítási és futtatási próbák tesztfüggőségekkel.

## A tényleges szerkesztőfelület DOM-próbái

```text
NODE_PATH=<jsdom node_modules> node tests/input_bindings_dom.cjs
Binding editor DOM: sources, manual/init limits, drafts, save/reload and conflicts OK

NODE_PATH=<jsdom node_modules> node tests/schema_editor_dom.cjs
Schema editor DOM: empty state, fields, validation, draft guards, save, delete and reload OK
```

A csomagban szállított HTML/JavaScript futott jsdom alatt. A tesztek ellenőrizték
a választómezőket, mentetlen adatok megőrzését, modulváltást, hibás mentést és
újratöltést, sikeres mentést, a kézi/szerveroldali és indítási korlátokat.
Teljes grafikus böngészős vizuális ellenőrzés nem futott.

## Határok

Ez célzott regressziós csomag, nem a teljes történeti tesztkészlet eredménye.
A korábban kapott projektből hiányzó példafájlokat ez a kiadás nem pótolja.
Valódi Qwen/Ollama-hívás, Oracle-adatbázis és a teljes céges alkalmazás
PMD/Checkstyle/build/üzleti tesztje nem futott. Az asszisztens tesztjeiben
ellenőrzött modellválaszokat használtunk; az AI kódolási minőségét nem mértük.
