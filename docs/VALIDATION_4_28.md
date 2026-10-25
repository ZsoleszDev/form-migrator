# Ellenőrzés — 4.28 Java asszisztens

## Új működés

```text
PYTHONPATH=<webes tesztfüggőségek>:tests python -m unittest test_backend_assistant test_backend_assistant_web test_manual_action_source test_compact_backend test_ollama_export -v
Ran 40 tests in 19.696s
OK
```

A webes tesztfüggőségek a projekt `requirements-test.txt` fájljának rögzített
verziói szerint telepítve. A tesztek nem kérnek valódi LLM-et vagy Oracle-kapcsolatot.
Ez 18 új asszisztens-tesztet és 22 meglévő, kapcsolódó tesztet jelent. Környezet:
Python 3.12.14, OpenJDK 17.0.20; a Java-fordítás célverziója 11.

Ellenőrzött folyamatok:

- valódi Forms XML generálása, teljes helyi programegység és DTO az asszisztenskontextusban;
- Ollama-próbaválasz → teljes Java-fájljavaslat és diff, a backend érintése nélkül;
- fejlesztői átvétel, forrás-/DTO-változás és javaslatmódosítás elutasítása;
- eredeti fájl mentése, más végpontok megtartása és friss modul/backend ZIP;
- újragenerálás után az átvett kód, a javaslat és az eredeti fájlmásolat megőrzése;
- hiányzó kontextus esetén nincs részleges kód vagy alkalmazható csomag;
- pontos cache és kizárólag offline cache-használat;
- hibás struktúra korlátozott javítása, timeout után nincs újabb kérés;
- méret-/kontextuskorlát ellenőrzése csonkolás helyett;
- tiltott fájlútvonalak és ütköző privát segédmetódusok elutasítása;
- igazolt dictionary-oszlopok és összetett kulcs szerinti JDBC helper;
- opcionális entity/repository publikálása és átvétele a végponttal együtt;
- hibás Java-szintaxis elutasítása a JDK parserével;
- átvett service és JDBC helperek Java 11-re fordítása a meglévő Spring/céges próbaduplákkal;
- Java runtime próba: a modulszintű tiltás az átvett metódusban is megmarad;
- tényleges FastAPI feltöltés és migrációs worker, asszisztens háttérfutás,
  javaslatletöltés, átvétel és friss backend-ZIP HTTP-n;
- párhuzamos AI-futás és a futás közbeni modultörlés elutasítása.

Két meglévő webes teszt (CORS preflight és idegen origin/DNS-rebinding védelem)
szintén sikeresen lefutott. Két további kiválasztott régi webteszt a csomagból
hiányzó `examples/customer_fmb.xml` fájl miatt nem tudott lefutni.

Statikus ellenőrzések: `python -m compileall -q frm_forms`,
`node --check frm_forms/data/backend-assistant.js`. A weboldal HTTP-kiszolgálását
és a CSP-t az API-teszt ellenőrzi. Teljes böngészős megjelenítési teszt nem futott:
a környezetben nincs telepített Chromium.

## Korábbi teljes tesztcsomag összehasonlítása

Módosítás előtt:

```text
python -m unittest discover -s tests
Ran 644 tests in 94.507s
FAILED (failures=34, errors=46, skipped=43)
```

Módosítás után, az első 14 új tesztet is tartalmazó összehasonlító futásban:

```text
python -m unittest discover -s tests -v
Ran 658 tests in 117.295s
FAILED (failures=34, errors=46, skipped=45)
```

A 80 hibás eset neve előtte és utána pontosan azonos: új regressziós eset nem
jelent meg. A teljes futásban a webes függőségek nem voltak bekapcsolva; a 17 új
teszt korábbi külön futásában, valamint a végső 40 tesztes futásban a rögzített
webes függőségek is rendelkezésre álltak.

A feltöltött forráscsomag nem tartalmazza az `examples` könyvtárat, többek között
a customer, backend-query, screen-layout és más régi mintákat. Több régi teszt
ezekre épül. Nem készült hozzájuk kitalált helyettesítő üzleti adat vagy XML.

## A bizonyíték határa

A tesztelt LLM-válaszok rögzített próbaválaszok: az integrációt és a kódátvételt
ellenőrzik, nem a választott modell tényleges migrációs pontosságát. A JDK parser
szintaxist, a fordítás a próbaduplákkal elérhető Java API-kat ellenőrzi.

Valós Oracle, DB-triggerek, NLS-beállítások, a teljes céges Spring/Angular projekt,
PMD és valamennyi eredeti Forms eseménylánc végponttól végpontig tartó ellenőrzése
nem állt rendelkezésre. Ezeket a valódi projektben kell ellenőrizni a javaslat
átvétele után. A felület nem állítja az AI-javaslatról, hogy ezek már megtörténtek.
