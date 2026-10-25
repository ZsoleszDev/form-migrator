# Automatizálás: portfólió, adatszótár, DB-hívások, LOV-végpontok

Ez a leírás azt a munkafolyamatot mutatja be, amellyel a teljes alkalmazás formjain
mérhetővé válik a hátralévő munka, és a legnagyobb tételek automatikusan fogynak.

## 1. Tömeges futtatás és összesítő

```bash
python -m frm_forms batch formok/ --olb olb/qmsolb65_olb.xml --out build/portfolio
```

- A mappában minden `.fmb` és FormModule XML formot generál (a `*_olb.xml` / `*_mmb.xml`
  könyvtárakat kihagyja), formonként egy almappába. Alapmód: `--mode screen`.
- Egy hibás form nem állítja meg a futást. A kilépési kód 2, ha volt sikertelen form.
- `build/portfolio/PORTFOLIO_HU.md`:
  - **Mit érdemes először javítani:** a végpontokat tiltó okok, a feloldott végpontok
    száma szerint rangsorolva. Egy sor egy üzenetsablon: a nevek, a bindek és a
    literálok ki vannak emelve belőle.
  - Az átültetendő triggerek okai, eseményekkel és hatókörrel.
  - A külső és ismeretlen hívások (DB-csomagok, csatolt könyvtárak), formszámmal.
- `portfolio.json` a teljes listákat tartalmazza, a `portfolio-forms.csv` pedig
  pontosvesszővel és BOM-mal készül, így Excelben közvetlenül megnyitható.
- `--report-only`: nem generál, csak a meglévő almappákból számolja újra a riportot.

## 2. Adatszótár: kulcsok, oszlopok, eljárás-aláírások

```bash
python -m frm_forms dictionary-sql build/portfolio --out dictionary.sql
# a DBA futtatja (csak olvas: ALL_TAB_COLUMNS, ALL_CONSTRAINTS, ALL_ARGUMENTS):
#   sqlplus -s felhasznalo/jelszo@adatbazis @dictionary.sql   -> dictionary.txt
python -m frm_forms dictionary-import dictionary.txt --out schema-kozos.json
```

A közös séma csak `tables` és `procedures` szakaszt tartalmaz, így minden formhoz
használható. A blokkszintű `blocks` beállítások (`writable`, `table`) formspecifikusak:
az ismeretlen blokknevet a generátor elgépelésként elutasítja. Ezeket formonként add
meg, és ha a meglévő fájlt bővíted, használd a `--merge` kapcsolót:
`dictionary-import dictionary.txt --merge schema.json --out schema.json`.

- A szkript bármely Oracle-verzión fut (nem használ JSON-függvényt), és csak a
  kimenetekben ténylegesen használt táblákat és rutinokat kérdezi le.
- Az import a `schema.json` két új szakaszát tölti ki: `tables` és `procedures`.
  `--merge` esetén a meglévő, kézzel ellenőrzött bejegyzések elsőbbséget kapnak.
- `tables`:
  - Ha a formban nincs `PrimaryKey` jelölés, a tábla elsődleges kulcsát veszi át,
    feltéve, hogy minden kulcsoszlop le van képezve.
  - A táblában nem létező oszlopra mutató mezőt `SQL_MAPPING` okkal tiltja.
  - A nullitást és a hosszakat szándékosan nem veszi át: egy NOT NULL oszlopot
    DB-trigger is kitölthet.
- `procedures`: a túlterhelt rutinok kimaradnak. Ha ugyanaz a név több sémában is
  létezik, csak `OWNER.`-rel minősítve hívható.

## 3. DB-eljárások és -függvények hívása

Ha egy adatműveleti trigger (WHEN-VALIDATE-*, PRE-INSERT/UPDATE/DELETE, POST-QUERY)
olyan rutint hív, amelynek aláírása szerepel a `schema.json` `procedures`
szakaszában, a hívás JDBC-hívássá fordul, a szolgáltatás tranzakciójában:

- **Eljárás:** `csomag.eljaras(:B.KOD, :B.NEV)` → `{call CSOMAG.ELJARAS(?, ?)}`.
  - OUT és IN OUT paraméter csak ugyanannak a blokknak a mezője lehet.
  - A végén elhagyható (DEFAULTED) paramétereket nem kell megadni.
- **Függvény kifejezésben:** `:B.NEV := csomag.fv(:B.KOD)` → `{? = call CSOMAG.FV(?)}`.
- **Támogatott típusok:** szöveg, szám és dátum. Minden más típus indoklással
  tiltott marad.
- **Üzleti hibák:** a `RAISE_APPLICATION_ERROR` (ORA-20000..20999) üzenete HTTP 422-vel
  jut el a felhasználóhoz, ahogy egy elbukó Forms-trigger üzenete is.
- **Tiltott esetek:**
  - az aláírás nélküli rutin: „DB-rutin aláírás nélkül”;
  - a Forms beépített hívás adatműveleti triggerben;
  - a helyi csomag.

  Mindegyik saját, egyértelmű okkal marad tiltott.

A Designer záró kivételkezelője (`EXCEPTION WHEN OTHERS THEN cgte$other_exceptions;`,
illetve a csak `RAISE FORM_TRIGGER_FAILURE`) kimarad: nélküle a hiba továbbmegy, és a
kérés elbukik, ahogy a trigger is. A hibát elnyelő vagy javító kezelő
(`WHEN OTHERS THEN NULL`) indoklással tiltott marad; a parser soha nem dobja el csendben.

## PL/SQL az adatbázisban (átfuttatás)

Ha egy trigger Javára nem fordítható, de csak adatbázis-munkát végez, az eredeti PL/SQL
változatlanul fut az Oracle-ben, névtelen blokkban. Ide tartozik a SELECT INTO, a DML,
a kurzorok, a kivételkezelők és a helyi eljárások.

- **Adatműveleti triggerek:** POST-QUERY, WHEN-VALIDATE-*, PRE-/POST-INSERT/UPDATE/DELETE.
  - A rekord mezői kötött változók lesznek.
  - A módosított értékek visszaíródnak, de csak ott, ahol a Forms is engedi. A POST-QUERY a
    lekérdezett adatbázismezőket nem írja; kulcsot csak a PRE-INSERT ír, például szekvenciából.
  - Ha a kód olyan mezőt ír, amelyet az adott eseményben nem lehet visszaírni, a trigger
    indoklással tiltott marad.
- **Gombok:**
  - A generált akció-végpont a kérés összes blokkértékét megkapja, egy tranzakcióban
    futtatja a kódot, a módosított mezőket és az üzeneteket pedig visszaadja; a képernyő
    ezeket visszaírja.
  - A trigger végi `COMMIT` helyett a végpont tranzakciója véglegesít.
- **Átalakítások:**
  - `MESSAGE` → üzenet a válaszban;
  - `RAISE FORM_TRIGGER_FAILURE` → HTTP 422 az üzenettel;
  - keretrendszeri hívás → `NULL`, kivételkezelőben `RAISE`;
  - helyi eljárás → beágyazott deklaráció.
- **Ami nem fut át:**
  - Forms beépített hívás (`GO_BLOCK`, `SET_ITEM_PROPERTY` stb.);
  - `:SYSTEM`;
  - más blokk mezője adatműveleti triggerben;
  - helyi csomag;
  - `ROLLBACK`.
- **Ismeretlen eljárások: az adatbázis dönt.** A Forms egy nem helyi, nem beépített nevet a
  csatolt könyvtárakban, majd az adatbázisban keres. Csak az `.fmb`-ből dolgozva ezért minden
  ilyen hívás fut, és futáskor az Oracle oldja fel a nevet.
  - Ha a rutin nincs az adatbázisban (például csatolt `.pll`-ben van), csak az az egy művelet
    ad HTTP 501-et a rutin nevével (`PLS-00201`); minden más működik.
  - Ha egy ilyen eljárás olyan mezőt kap, amelyet az adott eseményben nem szabad visszaírni,
    a blokk futáskor ellenőrzi, nem módosította-e. Ha igen, HTTP 501 „Migrációs korlát”
    jelzést ad, és nem dobja el csendben az értéket.
- **Ismert aláírás esetén:** ha az adatszótár (2. pont) ismeri az eljárást, a paraméterszám és
  az OUT-paraméterek már generáláskor ellenőrzöttek. Az adatszótár nem kötelező.
- **Programegységek a ServiceImpl-ben:** a végpontokból ténylegesen használt saját eljárások és függvények a DPS ServiceImpl
  `PlsqlUnits` osztályában szerepelnek, egyenként megnevezett konstansként, futtatható
  PL/SQL-lel. Kommentben áll mellettük, melyik trigger vagy gomb hívja őket, és melyik mező
  melyik változó lett. A nem használt és nem futtatható egységek az okkal és az eredeti
  forrással együtt az elemzésben maradnak meg.

## Backend-konvenciók: `log1x` és céges alaposztályok

A generált backend a céges mintát követi. Az alábbi szemléltető váz kommentjei helyén a generátor teljes művelettörzset ad:

```java
@XSlf4j
@Service
public class XYServiceImpl extends ModuleServiceBase<DpsLogHelper> implements XYService {
  public PageResult<AitRow> searchAit(UserDto user, SearchRequest<AitCriteria> request) throws Exception {
    return log1x(log, XYConstants.SEARCH_AIT_NAME, user, null, () -> {
      // Itt fut a paraméterellenőrzés, a jdbc.query(...) és a triggerlogika.
      // A teljes SQL és a privát segédek ugyanebben a ServiceImpl-ben vannak.
      /* a generátor ide illeszti a teljes művelettörzset */
    });
  }
}
```

- **Publikus belépési pontok `log1x`-ben:** a DPS és a WBS `ServiceImpl`, valamint a `ControllerImpl`
  végpontjai. Az első paraméter a `UserDto user`, a naplózott név a CL-ben lévő
  `…_NAME` konstans (például `SEARCH_AIT_NAME = "searchAit"`), a metódusok `throws Exception`
  kivételt jeleznek.
- **A `ControllerImpl`** a bejelentkezett felhasználót a `java_user_expression` kifejezéssel kéri
  le (alapból `getUser()`), és így adja tovább a szolgáltatásnak.
- **Olvasható `ServiceImpl`:** a metódusok fölött csak rövid megjegyzés áll (Forms-blokk,
  művelet, állapot, triggerek). A gombok metódusában közvetlenül fut az eredeti PL/SQL. A
  blokkok SQL-je, a Forms-triggerek kódja, a használt Forms-eljárások (`PlsqlUnits`) és a
  JDBC-segédek ugyanebben a DPS ServiceImpl-ben vannak. Külön Data/domain/repository fájl nincs.
  Az üres hookok és a hívás nélküli segédek nem generálódnak. Az eredeti Forms SQL és
  PL/SQL az `analysis/backend-evidence.md`-ben olvasható, nem kommentként a kódban.
- **Céges beállítások (egyszer, a szerver konfigurációjában):**

| Kulcs | Alapérték |
|---|---|
| `java_service_base_dps` | `ModuleServiceBase<DpsLogHelper>` |
| `java_service_base_wbs` | `ModuleServiceBase<WbsLogHelper>` |
| `java_controller_base_dps` | `ModuleControllerBase<DpsLogHelper>` |
| `java_controller_base_wbs` | `ModuleControllerBase<WbsLogHelper>` |
| `java_user_type` | `UserDto` |
| `java_user_expression` | `getUser()` |
| `java_company_imports` | a fenti osztályok teljes nevei, például `["hu.ceg.common.ModuleServiceBase", …]` |

Amíg a `java_company_imports` üres, a generált fájlok elején egy TODO-megjegyzés sorolja
fel az importálandó osztályokat.

## Azonnal éles backend (`backend_live`)

Bekapcsolva a generált végpontok azonnal működnek. A `MODULE_REVIEWED` kapcsoló kezdőértéke
`true`, az írás engedélyezett (hacsak a `schema.json` `writable: false`-t nem mond), és a
WHERE-rel szűrt blokk írása ellenőrizendő jelzés, nem tiltás. Egyetlen kapcsoló
(`MODULE_REVIEWED = false`) továbbra is mindent letilt. A webes felületen alapból be van
kapcsolva (`FRM_BACKEND_LIVE`), parancssorból a konfigurációban: `"backend_live": true`.

## 4. LOV-végpontok

Minden LOV-hoz, amelynek RecordGroupQuery-je van, egy `POST .../lov/<név>` végpont készül.

- **Forms-mód:** a migrált Angular felület mindig `NORMAL` módban működik.
  A `:SYSTEM.MODE` és a statikus `NAME_IN('SYSTEM.MODE')` olvasások helyén
  `'NORMAL'` SQL-literál fut; ehhez nem kell új kérésparaméter vagy beállítás.
  Ez a LOV-okra, blokkszűrőkre, PL/SQL-re, Java-triggerekre és frontend
  mezőállapot-feltételekre is vonatkozik. Az eredeti forrás az elemzési fájlokban megmarad.
- **A lekérdezés** a fenti módhelyettesítéssel fut. Köré még két dolog kerül:
  - szűrés a begépelt szövegre (az első látható oszlopra: `LIKE 'szöveg%'`);
  - sorkorlát (`limit`, alapértelmezés 50, legfeljebb 500).
- **Paraméterek:** a `:BLOKK.MEZŐ` bindek típusos paraméterek lesznek; kérés:
  `{"term": "Bud", "parameters": {"F.ORSZAG": "HU"}, "limit": 50}`.
- **Tiltott LOV:** a `:GLOBAL`, a `:SYSTEM.MODE` kivételével a `:SYSTEM`, a `:PARAMETER` bindet, a nem SELECT
  lekérdezést vagy a hiányzó rekordcsoportot tartalmazó LOV indoklással tiltott.
- **Naptár:** a katalógusban szereplő naptárhívással kiváltott dátum-LOV nem kap végpontot.
- **Kapcsolók:**
  - Mint minden generált végpont, a LOV is a `MODULE_REVIEWED` kapcsoló mögött van.
  - A korábbi viselkedés (LOV-kód nélküli backend) a `"backend_lov_endpoints": false`
    beállítással kapható vissza.
- **Hol látszik:** a végpontok listája a `BACKEND_TASKS.md` **LOV-végpontok**
  szakaszában és a `backend-plan.json` `lovs` tömbjében található.

## 5. Golden-tesztek a valódi formokra

```text
tests/golden/<eset>/input.xml        a form
tests/golden/<eset>/golden.json      opcionális: {"args": [...], "files": [...]}
tests/golden/<eset>/schema.json      opcionális: --schema (rules.json, config.json hasonlóan)
tests/golden/<eset>/expected/        az elfogadott kimenet
```

```bash
FRM_UPDATE_GOLDEN=1 python -m unittest tests.test_golden   # elvárt kimenet (újra)generálása
python -m unittest tests.test_golden                          # eltérésnél olvasható diff
```

Érdemes a legfontosabb valódi formokat felvenni. Így a generátor minden módosítása
formonként, sorra pontosan látszik, a szándékos változást pedig a verziókezelőben
lehet átnézni.

## Javasolt sorrend

1. `batch` a teljes formkészletre, majd a `PORTFOLIO_HU.md` átnézése.
2. `dictionary-sql` → a DBA lefuttatja → `dictionary-import` → `schema-kozos.json`.
3. `batch --schema schema-kozos.json` újra: a DB-hívások és a kulcsok miatti tiltások eltűnnek.
4. A riport tetején maradó okokat érdemes a generátorban kezelni.

## CommonMigrateTools: közös segédfájl a CL-ben

A generált backend általános segédei egyetlen fájlban vannak: `backend/CL/CommonMigrateTools.java`.
A csomagja a projektben lévő példányé (lásd lent), alapból `<java_package>.cl`. Beágyazott
osztályként tartalmazza a következőket:

| Osztály | Feladat |
|---|---|
| `SqlValues` | Oracle NULL-kezelés, háromértékű logika, NUMBER-aritmetika |
| `RuleContext` | a Forms `MESSAGE` és a `FORM_TRIGGER_FAILURE` megfelelője |
| `DbCalls` | PL/SQL-hívások és a triggerek névtelen blokkjai |
| `PlsqlValues` | gombkérés-értékek és típusos PL/SQL-kötések közötti átalakítás |
| `LovQuery` | LOV-lekérdezés szűréssel és sorkorláttal |
| `FormsChecks` | Required, MaximumLength és NUMBER(p, s) ellenőrzése, külön validációs könyvtár nélkül |
| `FormsErrors` | hibafordító: ORA-20xxx → HTTP 422; hiányzó rutin (PLS-00201) és migrációs korlát → HTTP 501 |

- **Használat:** a modulok DPS ServiceImpl-je csak a használt osztályokat importálja
  (`import …CommonMigrateTools.SqlValues;`), nem másolja be őket. A fájlt egyszer kell a
  CL-projektbe tenni. Csak akkor kell cserélni, ha a `CommonMigrateTools.VERSION` megváltozik.
  A 4.12.2 formázási javítás átvételéhez is cseréld le ezt a fájlt; a működés
  és a `VERSION = "2"` API-verzió változatlan.
- **Előny:** egy javítás itt minden modulra érvényes, újragenerálás nélkül.
- **Függőségek:** csak JDK és Spring (web, jdbc). Java 11 és Spring Boot 2.3 (Spring Framework 5.2)
  kompatibilis. A CL-projektnek ezért el kell érnie a spring-jdbc-t is.

**A csomagját nem kell megadni (4.25.1):** a CommonMigrateTools ugyanúgy működik, mint a frontend közös fájlja (4.26-tól a `wf-table.ts`).
A telepítés (Előnézet / Telepítés) megkeresi a projektben lévő `CommonMigrateTools.java`-t a kiválasztott
Java-mappák `src/main/java`-jában, és a generált importokat annak a `package` sorára írja át. Ha a célmappák már a
generálás előtt ki vannak választva, a generátor is megkeresi, és eleve azt a csomagot írja. Ha a projektben még
nincs, a riport megmutatja, hova kell tenni a letöltött fájlt. A letöltött fájl és a generált importok csomagja ilyenkor
az alapértelmezés (`<java_package>.cl`). A felületen ezért megszűnt a „CommonMigrateTools Java package” mező.

**Felülírás (ritkán kell):** a CLI- vagy szerverconfig `common_migrate_tools_package` beállítása továbbra is
megadja a csomagot, például `"hu.ceg.common.cl"`. Ez akkor hasznos, ha a modult telepítés nélkül, a ZIP-ből
másolod be. Csak a package-et add meg, osztálynév, `import`, pontosvessző vagy fájlrendszerbeli útvonal nélkül.

## Típusnevek és importok

A generált Java rövid neveket használ (`LocalDateTime`, `BigDecimal`, `Map`), teljes csomagnév nélkül.
Import csak a JDK-, Spring- és Jackson-osztályokhoz készül. A `jakarta`/`javax` osztályok (például
`HttpServletRequest`) és a céges osztályok importját a fejlesztő adja hozzá a projekt szerint; a
`java_company_imports` megadása nem kötelező. A `jakarta.validation` nincs használatban.

## Java 11 és Spring Boot 2.3

A generált backend Java 11-en és Spring Boot 2.3-on (Spring Framework 5.2) fordul és fut:

- a DTO-k és a csomagoló típusok (`RowResult`, `PageResult` stb.) egyszerű osztályok, nem
  `record`-ok; az elérőik neve (`criteria()`, `messages()`) változatlan;
- hagyományos `switch`, `instanceof` + típuskényszerítés, összefűzött karakterlánc-literálok
  (nincs text block), `Collectors.toList()`;
- a CL REST-kliense a `RestTemplate`-et, a WBS a Spring Boot `RestTemplateBuilder` beanjét használja;
- nincs `jakarta.*` függőség.

A fordítási tesztek `--release 11` kapcsolóval ellenőrzik a kimenetet.

## Importtérkép: java-imports.json

A céges osztályok (`RestResponseDto`, `ModuleService`, `UserDto` stb.) importhelyét egy
JSON-fájl adja meg: egyszerű osztálynév → teljes Java-név.

```json
{
  "RestResponseDto": "hu.ff.xy.cl.modules.RestResponseDto",
  "ModuleService": "hu.ff.xy.dps.modules.ModuleService",
  "HttpServletRequest": "javax.servlet.http.HttpServletRequest"
}
```

- **Hol van:** alapból a migrátor gyökerében, `java-imports.json` néven. Kiindulásnak a
  `java-imports.example.json` másolható. Más útvonalat a `java_import_map` beállítás (CLI/szerver
  config, a config fájlhoz képest) vagy a `FRM_JAVA_IMPORT_MAP` környezeti változó ad meg;
  a `-` érték kikapcsolja. Az `_`-sal kezdődő kulcsok megjegyzések.
- **Mikor olvassa:** minden generáláskor újra, így a fájl módosítása a következő modultól érvényes.
- **Mit csinál:** minden generált backend-fájlba (CL, DPS, WBS), amelyben a név a kódban
  előfordul, a térkép szerinti import kerül. A térkép elsőbbséget élvez a generátor saját
  importjával és a `java_company_imports` listával szemben. Nem kerül import oda, ahol a név
  nem szerepel, ahol a fájl maga deklarálja, vagy ahol ugyanabban a csomagban van.
- **Mi hiányzik még:** generálás után az `analysis/java-imports.json` riport
  `without_import` része sorolja fel azokat a neveket, amelyek import nélkül maradtak (fájlonként).
  Ezeket a térképbe felvéve a következő generálás már importálja őket.
- **Angular-importok ugyanebből a fájlból:** minden érték, ami nem pontozott Java-osztálynév
  (kisbetűs csomagrészek, a végén nagybetűs osztálynév, például `hu.ff.xy.RestResponseDto`),
  Angular-import: például `"WFF": "wf-package"`, `"ToastService": "@ff/ui/toast"` vagy
  `"ServiceBase": "src/app/core/base/service-base"`. A generált komponensbe ezekről kerül
  `import { ServiceBase } from 'src/app/core/base/service-base';`, a hozzá tartozó TODO-sor eltűnik.
  Ha egy név mindkét oldalon kell: `{"java": "hu.ff.xy.Valami", "ts": "src/app/valami"}`. A frontend
  riportja: `analysis/ts-imports.json`.
- **Ellenőrzés:** hibás JSON, rossz formájú bejegyzés, vagy olyan érték, amely nem az adott
  névvel végződik (például `"UserDto": "hu.x.Masik"`), érthető hibaüzenettel leállítja a generálást.

## Változóértékek: java-variables.json

A DPS ServiceImpl metódusai saját változókat is létrehoznak: a fejlesztői bemeneteket (amit a kód sehonnan nem kap
meg, ezért `String ibuKod = null;` és TODO lesz belőle) és a Java lekérdezőgombok képernyőértékeit. A
`java-variables.json` név szerint megadja egy ilyen változó értékét:

```json
{
  "variables": [
    {
      "variableName": "ibuKod",
      "variableValue": "commonService.Details(param)",
      "autowired": "commonService",
      "import": "hu.company.pelda.CommonService"
    }
  ]
}
```

Ahol a generátor az `ibuKod` változót létrehozza, ott ez lesz:

```java
import hu.company.pelda.CommonService;
...
public class XyServiceImpl extends ... {
    @Autowired
    private CommonService commonService;
    ...
            // :IBU_KOD (...): az érték a java-variables.json-ból.
            String ibuKod = commonService.Details(param);
```

- **`variableName`:** a generált változó neve. A fejlesztői bemenetnél a forrás teljes neve camelCase-ben
  (`:XX.YY` → `xxYy`, `:GLOBAL.IBU_KOD` → `globalIbuKod`, blokk nélküli `:IBU_KOD` → `ibuKod`), a lekérdezőgomb
  képernyőértékénél a mező neve (`:T1.COL2` → `col2`; ha több blokkban is van ilyen nevű mező, `t1Col2`). A pontos neveket a `BACKEND_TASKS.md` Fejlesztői
  bemenetek táblázata és a metódus feletti megjegyzés mutatja.
- **`variableValue`:** egysoros Java-kifejezés, a `=` jobb oldala (a záró `;` elhagyható). A változó típusa a
  generált marad (szöveg: `String`, szám: `BigDecimal`, dátum: `LocalDateTime`).
- **`autowired`** (elhagyható): az osztály tetejére kerülő `@Autowired` mező neve. A típusa a név nagy
  kezdőbetűvel: `commonService` → `private CommonService commonService;`. Ha több bejegyzés ugyanazt a
  szolgáltatást használja, a mező egyszer szerepel.
- **`import`** (elhagyható): teljes osztálynév vagy ezek listája; a ServiceImpl-be `import …;` sor lesz belőle.
- **Csak ami kell:** importot és mezőt csak az a ServiceImpl kap, amelyik a változót tényleg létrehozza.
- **Formátum:** lista, egyetlen bejegyzés, vagy `{"variables": [...]}`. Az `_`-sal kezdődő kulcsok megjegyzések
  (a gyökérben lévő minta a `_példa` kulcsban mutatja a fenti bejegyzést).
- **Hol van:** alapból a migrátor gyökerében, `java-variables.json` néven (üres `variables` listával). Más
  útvonalat a `java_variable_map` beállítás (a config fájlhoz képest) vagy a `FRM_JAVA_VARIABLE_MAP` környezeti
  változó ad meg; a `-` érték kikapcsolja. Minden migráláskor újra beolvassa, a webes felület is.
- **Riportok:** a JSON-ból kitöltött változók nem TODO-k. A `BACKEND_TASKS.md` külön táblázatban sorolja fel őket
  („Fejlesztői bemenetek a java-variables.json-ból”, az értékkel). Az `analysis/backend-plan.json` a bemenet
  mellé írja az értéket (`value`). Az `analysis/java-variables.json` bejegyzésenként megmutatja, melyik
  fájlba került (`used_in`; üres lista: egyik metódus sem hozta létre).
- **Ellenőrzés:** hibás JSON, nem Java-név (`variableName`, `autowired`), üres vagy többsoros érték, hibás
  import, ismeretlen kulcs vagy kétszer szereplő változó érthető hibaüzenettel leállítja a migrálást.

## CL package: honnan importál a DPS és a WBS

A felület „Célkörnyezet és Java” paneljén a **CL package (modul)** mezőben adható meg, hová
másolod a modul generált CL-fájljait, például `hu.company.cl.pages.modules.xymodul`. A DPS és
a WBS innen importálja a modul DTO-it, a `…Constants` és a `…RestClient` osztályt, céges (AWU)
módban a céges DTO-kat is. Ez a mező váltotta a korábbi „Java alap package” mezőt.

- **Tömeges futtatás:** a `{module}` a modul nevét jelenti, például
  `hu.company.cl.pages.modules.{module}`.
- **Üres mező:** a szerver alapértelmezése, `<java_package>.<modul>.cl`.
- **Üres `package` sor:** a webes generálásnál a modul Java-fájljainak nincs `package` sora,
  mert a fájlok a projekt saját (modulonként eltérő) mappáiba kerülnek. Az IntelliJ a bemásolás
  helyén felajánlja a helyes csomagot („Set package name to …”). A közös
  `CommonMigrateTools.java` megtartja a beállított csomagját.
- **CLI/szerver beállítás:** `"cl_package": "hu.company.cl.pages.modules.xymodul"`, illetve
  `"java_empty_package": true`. Parancssorban alapból a `package` sorok megmaradnak, és ilyenkor
  a CL-fájlok a `cl_package` csomagot kapják.

## Checkstyle-elrendezés

Minden generált backend-Java Checkstyle-kompatibilis elrendezést kap (részletek:
[JAVITASOK_4_12_2_HU.md](JAVITASOK_4_12_2_HU.md)).

- **`java_import_order`:** a Checkstyle CustomImportOrder `customImportOrderRules` értéke,
  alapból `STATIC###STANDARD_JAVA_PACKAGE###THIRD_PARTY_PACKAGE` (a csoportok között egy üres sor).
- **`java_checkstyle_format`:** `false` esetén a korábbi elrendezés marad (hibakereséshez).

## Headstart naptárablak

A Headstart `CALENDAR` segédblokkja (napcellák: `CELL1` … `CELL42`, hónapléptetés, OK/Mégse)
a Formsban a dátumválasztót valósította meg. Ha a katalógusban szereplő `CALENDAR` blokk
legalább 28 számozott napcellából áll, a migrátor a saját triggereivel együtt elhagyja:

- **Felület:** nincs `CALENDAR` ablak, vászon és napcella; az ablak a képernyőtervben üres,
  `unused` szerepű, a komponensbe nem kerül.
- **Backend:** nincs végpont a gombjaihoz.

A dátummezőt az Angular dátumvezérlő naptára kezeli. Egy `CALENDAR` nevű, de napcellák nélküli,
üzleti logikát tartalmazó blokk megmarad.

## Generált Java: @Service és sorhossz

- A DPS és a WBS szolgáltatásai név nélküli `@Service` annotációt kapnak.
- A JDBC SQL-szövegek (lekérdezések, DML, PL/SQL-blokkok, LOV-ok) egyik sora sem éri el a
  120 karaktert (PMD). A túl hosszú literált a generátor szóközöknél összefűzött darabokra bontja
  (`"…" + "…"`); a karakterlánc értéke bájtra azonos marad, és escape-szekvenciát nem vág ketté.
  A folytatósorok a Checkstyle-elrendezés `CONTINUATION` behúzását követik. A többi hosszú
  kódsort az IntelliJ formázója tördeli.

