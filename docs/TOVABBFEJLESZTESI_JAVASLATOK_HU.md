# Pontosabb Forms → Angular + Java migráció

4.30 frissítés: a Java-asszisztens helyi, determinisztikus formázása és a
frontend–backend bemenetek szerkesztése elkészült. Lásd:
[INPUT_BINDINGS_HU.md](INPUT_BINDINGS_HU.md), [VALIDATION_4_30.md](VALIDATION_4_30.md).
Az alábbi korábbi pontossági javaslatok ettől még nem jelentenek megvalósított
funkciókat vagy igazolt Forms-egyenértékűséget.


A 4.29 forrásának célzott áttekintése alapján. A sorrend a hibás üzleti működés
elkerülését és a hiányzó információ korai bekérését helyezi előre.

## Ebben a kiadásban megvalósult

**Táblaszerkezet-bekérő a Java asszisztensben.** A Forms-blokkokon kívül a
trigger és a helyi programegység statikus SQL-hivatkozása is kiválthatja a bekérést.
Az ismeretlen táblához üres oszlopszerkesztő jelenik meg; oszlopot hozzáadni és
törölni lehet. Oszlopnév, Oracle-típus, nullable, hossz/BYTE/CHAR, precision/scale,
PK, alapérték és megjegyzés adható meg. A felhasználó által megadott szerkezet
menthető, exportálható és az AI kontextusában felhasználható.

Az érintett AI-feladat a hiányzó szerkezetet megvárja. Kommentből/literálból
nem készül táblabejegyzés. CTE-nevek és az al-lekérdezések aliasai külön kezeltek.
Dinamikus, idézőjeles vagy távoli adatforrásnál külön jelzés kér kézi tisztázást.
Az ismeretlen record/cursor típushivatkozás nem lesz kitalált tábla.

Fő kódhelyek: `frm_forms/schema_workbench.py`,
`frm_forms/web/assistant.py` és `frm_forms/data/backend-assistant.*`.
Használat: [JAVA_ASSISTANT_HU.md](JAVA_ASSISTANT_HU.md).

## Javasolt következő lépések

| Sorrend | Fejlesztés | Miért javítja a pontosságot? | Ellenőrizhető eredmény |
|---|---|---|---|
| 1 | Részletesebb dictionary-export/import | A jelenlegi export oszlophossza `DATA_LENGTH`; az import ezt szövegnél `max_length` értékként kezeli, pedig a byte- és karakterhossz külön adat. Negatív scale is elveszhet a jelenlegi `isdigit()` ágon. | BYTE/CHAR és többbájtos szöveg, NUMBER(8,-2), NUMBER(*,2), TIMESTAMP(9) metaadata veszteség nélkül körbeér. |
| 2 | Szinonima és séma feloldására választó | `UGYFEL` és `APP.UGYFEL` azonosságát most nem bizonyítja a migrátor. A pontos egyezés követelménye többértelműségnél indokolt, de sok kézi munkát hagy. | A felhasználó ellenőrzött owner/objektum közül választ; az eredeti név és feloldási bizonyíték is megmarad. Két lehetséges owner esetén nincs automatikus választás. |
| 3 | Hiányzó eljárásokhoz célzott adatbekérő | Az `ALL_ARGUMENTS` import és a szabad projektminta mező már megvan. Az eljárás üzleti logikája, túlterhelése és mellékhatása azonban nem derül ki pusztán a nevéből. | Ismeretlen hívásnál külön űrlap kéri az IN/OUT/IN OUT paramétereket, visszatérési típust, teljes package body/PLD forrást vagy céges Java API-t. Hiány esetén pontos kérdés készül. |
| 4 | Oracle–Java összehasonlító tesztek | A Java-szintaxis és a kód lefordíthatósága még nem bizonyítja az eredeti működést. A jelenlegi próbaválaszos tesztek az asszisztens integrációját mérik. | Ugyanarra a rögzített bemenetre összevetett kimeneti mezők, hiba, módosított DB-sorok és visszagörgetés, elkülönített tesztadatbázisban. |
| 5 | Írási metaadatok és mellékhatások | A táblaszerkesztő jelenleg oszlopokat és PK-t ismer; a dictionary-szkript is csak oszlopot, PK-t és rutinparamétert exportál. FK, UNIQUE, CHECK, DB-trigger és kulcsgenerálás nélkül egy INSERT/UPDATE Java-átírása hiányos lehet. | A generálási terv jelzi a DB-trigger, szekvencia/identity, alapérték, FK és CHECK szerepét. Hiányzó írási szerződésnél nem válik automatikusan kész állapotúvá az író végpont. |
| 6 | Forms-események és felületi állapot tesztelése | A `runtime_coverage.py` már jelzi például a mező-/rekordelhagyási validáció és a mentéskori validáció eltérését. Ezt a kódátírás önmagában nem rendezi. | Rögzített eseménysor: mezőváltás, rekordváltás, gomb, master/detail, mentés, hiba. Az Angular hívási sorrendje és megjelenített eredménye is ellenőrzött. |
| 7 | Fordítási hibákkal támogatott javítókör | A JDK parser jelenleg szintaxist vizsgál. A céges DTO/API eltérései a teljes projektben derülnek ki. A `verify-db` már létezik; nem kell újra kitalálni. | A felhasználó feltöltött javac/Maven/Gradle/TypeScript hibáiból és verify-db jelentéséből célzott, újra átnézhető javítás készül. A futtatást megbízható projektparancsok végzik. |
| 8 | Végpontspecifikus, visszakövethető kontextus | A triggerforrás már végpontonként szűrt, de a teljes schema bekerül a feladatba. Nagy projektnél ez sok felesleges helyet foglalhat a helyi modell kontextusában. | Csak a használt táblák/rutinok és függőségeik kerülnek a modellhez, teljes forrásokkal és hash-ekkel. Nincs csendes csonkolás vagy hiányzó függőség. |

## Elsőként javítandó konkrét kódhelyek

**Dictionary:** `frm_forms/dictionary.py`, `dictionary_sql()` és
`dictionary_import()`. Visszafelé kompatibilis, verziózott új sorformátumot
érdemes bevezetni `DATA_LENGTH`, `CHAR_LENGTH`, `CHAR_USED` és külön precision/scale
adatokkal. A korábbi export olvasható maradjon, de bizonytalan hosszhoz ne tartozzon
karakterkorlátként igazolt állapot. Ne változzon a NULL precision automatikusan
egy választott egész típussá. A default, identity és constraint import külön
adat legyen; a default kifejezést ne másolja át a rendszer ellenőrzés nélkül Java-ba.

**Névfeloldás:** `schema_workbench.py` és `dictionary.py`. A rövid név, owner,
szinonima és DB-link külön adat. A feloldásról a felhasználó vagy tényleges
dictionary-bizonyíték döntsön. A különböző táblák azonos oszlopnevei nem elegendők.

**Eljárás-bekérő:** `backend_assistant.py` feladatkontextusa és a webes asszisztens.
A már meglévő `procedures` szakaszra épüljön. Minden pótlás módosítsa a kontextus
lenyomatát, így a régi cache/javaslat ne éljen tovább új szerződés mellett.

**Viselkedési tesztek:** végpontonként külön tesztadat és elvárt eseménysor.
Kezdő minták: NULL/üres szöveg; tizedes szám, negatív scale; időt is tartalmazó
Oracle DATE; SELECT INTO nulla/egy/több sor; kivétel utáni rollback; tenant és
felhasználói kontextus; COMMIT_FORM több blokk módosításával. Az AI által felsorolt
teszteset csak terv, amíg ténylegesen le nem fut.

## Fontos elhatárolások a megvalósításhoz

A szerkezet megadása csökkenti a találgatást, de nem adja meg a teljes üzleti
szabályrendszert. Egy FK, DB-trigger, csomageljárás vagy Forms-eseménylánc hatását
külön kell ismerni. A kézi metaadat megadottsága, az importból származó adat,
a célprojektben sikeres fordítás és az összehasonlító teszt külön állapot legyen.

A jelenlegi opcionális „entity” JDBC POJO, repositoryja kulcs szerinti olvasást
ad. JPA-ra váltás előtt szükség van a célprojekt ORM-verziójára, annotációs
konvencióira, kulcs- és kapcsolatkezelésére. Ezt ne válassza ki a migrátor implicit módon.

## Elsődleges Oracle-források

- [ALL_TAB_COLUMNS — típus, byte-/karakterhossz, nullable, precision/scale](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/ALL_TAB_COLUMNS.html).
- [Oracle adattípusok — NUMBER, DATE, TIMESTAMP, szövegtípusok](https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/Data-Types.html).
- [ALL_CONSTRAINTS — PK, FK, UNIQUE, CHECK és constraint-állapotok](https://docs.oracle.com/en/database/oracle/oracle-database/26/refrn/ALL_CONSTRAINTS.html).

A javasolt fejlesztések megvalósításakor a tényleges céladatbázis verzióját is
rögzíteni kell; az itt felsorolt következő lépések még nincsenek megvalósítva.
