# Dinamikus DEFAULT_WHERE lekérdezésgombok

> **4.23 óta** az alapértelmezés az egyszerű, Java-alapú lekérdezőgomb (`query_action_mode: "java"`, lásd
> [JAVITASOK_4_23_HU.md](JAVITASOK_4_23_HU.md)). Az itt leírt PL/SQL-adapter a tartalék: akkor fut, ha a gombot
> nem lehet egyszerű Java-kódra fordítani (4.24-től akkor is, ha a generáláskori egyezés-ellenőrzés eltérést talál,
> lásd [JAVITASOK_4_24_HU.md](JAVITASOK_4_24_HU.md)), vagy ha a konfigurációban `query_action_mode: "plsql"` áll.

A `LEKERDEZESI_FELTETELEK` mintájú helyi eljárás korábban HTTP 501-es gombvégpontot
eredményezett. A `SET_BLOCK_PROPERTY`, `GO_BLOCK` és `EXECUTE_QUERY` Oracle Forms
futtatókörnyezetet igényel; ezeket egy adatbázisos PL/SQL-blokk közvetlenül nem futtatja.

A migrátor a teljes, támogatott mintát most lekérdezési akcióként fordítja le:

1. A gomb eredeti `IF` feltétele és a szűrőépítő eljárás feltételes ágai a DPS
   ServiceImpl által indított névtelen PL/SQL-blokkban futnak, Oracle-ben.
2. Az eljárás a `DEFAULT_WHERE` beállítása helyett visszaadja az összeállított
   feltételt. A navigáció és az `EXECUTE_QUERY` hívás lekérdezésjelzőre fordul.
3. A DPS csak a form forrásában lévő állandó SQL-részletekből előre lefordított
   változatot futtatja JDBC-vel. A `:BLOKK.MEZO` értékek típusos bindek. A kliens
   SQL-t, táblanevet, oszlopnevet vagy lekérdezésjelzőt nem adhat meg.
4. A lekérdezett rekordokon a támogatott `POST-QUERY` triggerek is lefutnak. A
   válasz `PageResult`/céges `PageResultDto`: `rows` és `messages`.
5. Az Angular a gomb megnyomásakor elküldi az aktuális mező- és jelölőértékeket,
   majd megjeleníti a célblokk sorait. A későbbi `executeQuery(célblokk)` is ezt a
   gombot használja, az aktuális szűrőkkel. A meglévő lapméret-korlát 1..200 marad.

Ha a gomb feltétele nem teljesül, nincs SELECT: `rows: null` és a figyelmeztető
üzenet érkezik vissza. Ez megőrzi a korábban megjelenített sorokat. A tényleges
lekérdezés üres találata `rows: []`, amely kiüríti a listát és „Nincs találat” üzenetet ad.

## Felismert eljárásminta

Az eljárás nem kap paramétert; egyetlen helyi `VARCHAR2(n)` szűrőváltozója van.
A változó állandó szöveget kap, illetve állandó részletekkel bővülhet `||` segítségével,
akár `IF`/`ELSIF`/`ELSE` ágakban. A feltételek ismert, típusos Forms-mezőkre és
tiszta PL/SQL-kifejezésekre hivatkozhatnak. Ezek az eredeti Oracle-kódban futnak:
a `CHAR` jelölő és a numerikus `1`/`0` összehasonlítása megmarad, a bind típusa
`VARCHAR2`. A JDBC-vel futó végleges WHERE külön, szigorú SQL-ellenőrzést kap.

Támogatott az idézőjel-helyettesítés is:

```sql
SELECT REPLACE(lek_sql, ';', CHR(39)) INTO lek_sql FROM DUAL;
SET_BLOCK_PROPERTY('BLK', DEFAULT_WHERE, lek_sql);
GO_BLOCK('BLK');
EXECUTE_QUERY();
```

Az utolsó három utasításnak feltétel nélkül, az eljárás végén kell állnia,
ugyanarra az ismert és lekérdezhető adatblokkra. A blokk/oszlopok SQL-leképezésének
igazolhatónak kell lennie. A master/detail reláció már támogatott egyenlőségfeltételei
a dinamikus szűrő mellett megmaradnak. Az eljárás neve és a szűrőmezők neve nem rögzített.

Az utolsó utasítás `DO_KEY('EXECUTE_QUERY')` is lehet, ha a célblokkban és a
formon nincs alkalmazható `KEY-EXEQRY` trigger, és nincs saját `DO_KEY`
programegység. Saját key-trigger esetén annak teljes logikájához adapter kell.

A `qms$event_item` katalógus szerinti keretrendszeri hívása kimarad. A gomb
`MESSAGE('...')` figyelmeztetése bekerül a válaszba. A bemutatott, egyetlen állandó
szöveget átvevő `WUZENET('...')` is üzenetként fordul, ha nincs ilyen nevű helyi
programegység. A saját helyi `WUZENET` (vagy más nevű üzenet-eljárás) is üzenet, ha csak
megjeleníti a kapott szöveget: `MESSAGE`, illetve alert-párbeszéd (`FIND_ALERT` akár konstans
nevével, `SET_ALERT_PROPERTY` az üzenetszöveggel vagy a címmel, `SHOW_ALERT`, `IF ID_NULL(...)`
ág). Ilyenkor az eljárás eredeti kódja megjegyzésként a generált Java-metódusba kerül (4.22).
Ha mást is csinál (naplóz, a megnyomott gomb szerint dönt, ír valamit), az üzleti hatásait külön
meg kell vizsgálni; a migrátor nem dobja el a saját eljárás kódját.

## A bemutatott három jelölő viselkedése

A négy `IF` eredeti működését tartjuk meg; nem javítjuk át feltételezett üzleti szabályra:

| COL2 | COL3 | COL4 | További COL9-szűrés |
|---|---|---|---|
| 1 | 1 | 1 | E04 vagy P01 |
| 1 | 1 | 0 | E04 vagy E07 |
| 0 | 1 | 1 | E07 vagy P01 |
| 1 | 0 | 1 | E04 vagy P01 |
| egyéb kombináció | | | nincs további COL9-szűrés |

Ebből négy eltérő SQL-változat keletkezik. Az egymást kizáró numerikus feltételek
lehetetlen kombinációi nem növelik a generált Java-kódot. A nulla/NULL kezelést az
eredeti PL/SQL végzi, a Java nem értékeli újra a jelölők üzleti feltételeit.

Az anonimizált példában az `IN` listából kihagyott kódok helyére a teljes forrás
tényleges kódjai kerülnek; a migrátor nem talál ki hiányzó listaértékeket.

## Megmaradó korlátok és kimutatás

Ismeretlen változó, nem állandó SQL-összefűzés, szűrőn túli mezőírás, DML,
további eljáráshívás, ciklus, nem támogatott SQL, ismeretlen adatforrás vagy más
futásidőben állított blokk-property esetén indokolt, kézi átültetési akadály marad.
A statikus lista nem válik szűrés nélküli kerülőúttá; csak a felismert gomb oldja fel
saját `DEFAULT_WHERE` akadályát. Az általános `MODULE_REVIEWED` kapcsoló megmarad.

Legfeljebb 64 szűrőépítési állapotot és 32767 karakteres feltételt elemzünk. A
korlát túllépése konkrét akadályt eredményez. A forrásban deklarált VARCHAR2-méretet
és az Oracle futás közbeni ellenőrzését nem változtatjuk meg.

- `analysis/backend-plan.json`: a gomb `runs: "plsql-query"`, `query_block`,
  `query_unit`, `implemented` és `blockers` mezői. Kézi akciónál az
  `adapter_diagnostics` külön mutatja a frontend-, query- és PL/SQL-felismerés okát.
- `analysis/backend-evidence.md`: eredeti forrás, előre lefordított SQL és bindek.
- `analysis/form.ir.json`: a trigger `query_action` terve, az adaptált névtelen
  PL/SQL-blokk és a szűrőváltozatok.
- `analysis/screen-plan.json`: `backend_calls.query_actions` célblokkjai.
- `RUNTIME_COVERAGE.md`: a gomb és a célblokk POST-QUERY eseménybekötése.

## Átvétel és ellenőrzés

Generáld újra a modult, és vedd át a CL, DPS, WBS és Angular fájlok változásait.
A lekérdezésgomb szerződése `ActionRequest`/`ActionResult` helyett
`QueryActionRequest`/`PageResult<célblokk>`; a céges formátumban külön DTO-fájlok
készülnek. Csak a ServiceImpl cseréje nem elegendő.

A `CREATE_ONCE` fájlokat a `--regenerate` megőrzi. A friss DPS/WBS-javaslatot az
`analysis/backend-regeneration/` könyvtárból kell összefésülni a meglévő fájlokkal;
a frontend változásait új célmappába generálva hasonlítsd össze. A korábbi NORMAL-mód javítása megmarad,
a CommonMigrateTools API-ja és 2-es verziója változatlan.

A `tests/test_query_actions.py` önálló Forms XML-lel ellenőrzi a bemutatott mintát,
a nyolc jelölőkombinációt, a hat adatlapkódot, a kötött paramétereket, a visszautasított
mintákat és a céges rétegek szerződését. A generált Java Java 11 szinten fordul, és
teszt-JDBC-vel végigfut a PL/SQL-kötés → SELECT → POST-QUERY → válasz útvonal. A
generált TypeScript eseménykezelő Node alatt futási ellenőrzést kap. Ezek helyi
tesztek; a tényleges PL/SQL- és adatbáziseredményeket a saját Oracle-környezetben
kell üzleti regresszióval ellenőrizni.
