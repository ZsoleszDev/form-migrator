# Ellenőrzés — 4.29 táblaszerkezet-bekérő

## Kapcsolódó regressziók

```text
PYTHONPATH=<webes tesztfüggőségek>:tests python -m unittest test_backend_assistant test_backend_assistant_web test_manual_action_source test_compact_backend test_ollama_export test_schema_workbench test_schema_workbench_web test_automation.AutomationTests.test_dictionary_sql_and_import_round_trip test_automation.AutomationTests.test_dictionary_key_and_columns_are_used_only_when_the_form_lacks_them -v
Ran 66 tests in 21.834s
OK
```

24 új sémakezelési/API-teszt és 42 kapcsolódó korábbi teszt. A tesztkörnyezetben a
`requirements-test.txt` függőségei elérhetők. Python 3.12.14, OpenJDK 17.0.20.
A kapcsolódó régi tesztek Java 11 célverziójú fordítást és az asszisztens
kódátvételi folyamatát is ellenőrzik. Valódi LLM-et egyik teszt sem igényel.

Új ellenőrzések:

- SELECT, JOIN, vesszős FROM, INSERT, UPDATE, DELETE és MERGE táblái;
- CTE-hatókör, al-lekérdezésben újrahasznált alias, származtatott tábla aliasa;
- sémanév körüli whitespace, komment, szövegliterál és FOR UPDATE;
- EXTRACT/TRIM, táblafüggvény, dinamikus SQL, idézőjeles név és DB-link;
- bizonytalan record/cursor %TYPE/%ROWTYPE nem lesz kitalált adatbázistábla;
- explicit logikai nullable, PK/nullable ellentmondás, hibás és duplikált oszlop;
- BYTE/CHAR külön tárolása; negatív NUMBER scale; TIMESTAMP precision 0..9;
- LOB/időzónás típus nincs automatikusan String/LocalDateTime típusra találgatva;
- hiányzó tábla vagy bizonyíthatóan hiányzó SQL-oszlop megakasztja az AI-feladatot;
- mentett séma bekerül a kontextusba és a triggerből ismert tábla JDBC-helperébe;
- sémaváltozás érvényteleníti a régi javaslatot és a régi szerkesztési revíziót;
- CLI újragenerálás megőrzi a kézi adatokat; új import ütközése látható;
- szimulált fájlírási hiba után a korábbi metaadat visszaáll;
- valódi FastAPI feltöltés → migrációs worker → üres szerkezet → mentés/export;
- hibás adat 422, elavult revízió 409, hiányzó helyi kliensfejléc 403;
- webes Újrafuttatás ugyanazzal a mentett sémával generál új modult.

## A tényleges HTML/JavaScript DOM-próbája

Opcionális fejlesztői függőség: jsdom. A migrátor futtatásához nem szükséges.

```text
NODE_PATH=<jsdom node_modules mappa> node tests/schema_editor_dom.cjs
Schema editor DOM: empty state, fields, validation, draft guards, save, delete and reload OK
```

A próba a csomag tényleges HTML- és JavaScript-fájlját futtatja. Ellenőrzi az üres
kezdeti táblát, hozzáadást/törlést, típusfüggő mezőket, külön nullable-választást,
a mentés JSON-adatát, a hibás mentés utáni piszkozatot, a mentetlen modul-/táblaváltás
védelmét és a sikertelen újratöltés után megmaradó piszkozatot. A forrásbeli HTML
szövegként jelenik meg. A másik feladat hiányos táblája nem tiltja a kész feladatot.

Statikus ellenőrzések: `python -m compileall -q frm_forms` és
`node --check frm_forms/data/backend-assistant.js`.

## Határok

Teljes böngészős vizuális teszt nem futott: nincs telepített Chromium. A DOM-próba
interakciót ellenőriz, nem a böngészőben renderelt elrendezést.

Valós Oracle, tényleges helyi LLM és a teljes céges Angular/Spring környezet nem
állt rendelkezésre. A kézzel megadott tábla helyességét nem ellenőrzi élő DB.
A statikus SQL-felismerő nem teljes Oracle parser vagy szinonimafeloldó.

A teljes történeti tesztcsomag helyett ebben a körben a módosított és kapcsolódó
útvonalak futottak. A korábban is hiányzó examples/web-ui bemenetek miatt a teljes
régi tesztcsomag nem tekinthető zöldnek; előző összehasonlítása:
[VALIDATION_4_28.md](VALIDATION_4_28.md). Ezeket nem pótoltuk kitalált üzleti adatokkal.
