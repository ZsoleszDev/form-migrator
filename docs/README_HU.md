# FRM Forms Migrator 4.32.0 — használat

**4.32 – IntelliJ CSV és automatikus AI-feladat:** a Java asszisztens táblaszerkesztője
CSV-fájlból vagy beillesztett szövegből kitölthető, DUAL/SYS.DUAL sémát nem kér.
A trigger átültetéséhez a promptot a backend automatikusan megadja. A bemenetek
panelje az **A triggerhez szükséges értékek forrása** nevet kapta, példákkal.
[CSV-import](CSV_TABLASZERKEZET_HU.md) · [Teljes munkamenet](MIGRALASI_MUNKAMENET_HU.md).

**4.28 – helyi Java fejlesztőasszisztens:** a kész modulok végpontjaihoz kódjavaslat,
teljes híváslánc, opcionális JDBC adatmodell/repository, diff és ellenőrzött átvétel.
A szokásos generálás után: [Java asszisztens útmutató](JAVA_ASSISTANT_HU.md),
webes oldal: `http://localhost:8000/assistant`.

**4.27 – frontend: csak a keret:**

- **A komponens váz:** FormBlock-régiók, `<wf-table>`-ök, végpontmetódusok, és gombonként a gomb HTTP-kérése,
  kezdetlegesen, a képernyő értékeivel (a backend-akció a kód által olvasott mezőket kapja; a lekérdező gomb a
  sorokat a táblázatba teszi).
- **Nincs Forms-emuláció:** kikerült a `text()`, `data()`, `blocks()`, `parameters()`, `showResult()`, a
  mezőállapot-fordítás (`setItemState` ...), a LOV-keresés és -visszaírás, a mentési lánc (`newRecord`,
  `deleteRecord`, eredeti rekordok), a válaszinterfészek és a toastok. Ezek helyén TODO, a nem fordított gomboknál
  az eredeti Forms-kóddal.

Részletek: [JAVITASOK_4_27_HU.md](JAVITASOK_4_27_HU.md).

**4.26 – nincs saját futtató, a képernyő a céges keretre épül:**

- **A `frm-forms-screen.ts` megszűnt:** a komponens a céges `ServiceBase`-t örökli, és csak azt tartalmazza, amit a
  képernyő használ, egyszerű metódusként (végpontok, `query<Blokk>()`, `search<Lov>()`, `on<Gomb>Click()`, `save()` ...).
  A ritka Forms-emulációk (alertek, :GLOBAL, képernyő- és mentési pontok, a backend Forms-utasításai) TODO-k.
- **`wf-table.ts`:** önálló táblázat-komponens (`<wf-table>`) a p-table köré. A p-table minden bemenete, kimenete és
  sablonja (`#header`, `#body`, `#caption` ...) átadható neki; az importja a java-imports.json `WfTable` bejegyzése.
- **Nincs wrapper div** az `ank-form-block`-ok körül.

Részletek: [JAVITASOK_4_26_HU.md](JAVITASOK_4_26_HU.md).

**4.25.2 – hibás PrimaryCanvas:** ha egy ablak `PrimaryCanvas`-a nem az ablak saját Content canvasa (például a
Designer Stacked `CG$POPUP_…` canvasa), a migrálás nem áll le: `SCREEN_PRIMARY_CANVAS_IGNORED` figyelmeztetés, és az
ablak az első Content canvasán indul. Részletek: [JAVITASOK_4_25_2_HU.md](JAVITASOK_4_25_2_HU.md).

**4.25.1 – a CommonMigrateTools csomagját nem kell megadni:** a felületről kikerült a „CommonMigrateTools Java
package” mező. A telepítés (és ha a célmappák ismertek, már a generálás) megkeresi a projektben lévő
`CommonMigrateTools.java`-t, és az importokat annak a csomagjára igazítja, ugyanúgy, mint a `frm-forms-screen.ts`-nél.
Részletek: [JAVITASOK_4_25_1_HU.md](JAVITASOK_4_25_1_HU.md).

**4.25 – változóértékek a java-variables.json-ból:** a generált Java saját változóinak (fejlesztői bemenetek,
lekérdezőgombok képernyőértékei) név szerint adható érték:
`{"variableName": "ibuKod", "variableValue": "commonService.Details(param)", "autowired": "commonService", "import": "hu.company.pelda.CommonService"}`.
A ServiceImpl ekkor megkapja az importot és az osztály tetején a `@Autowired private CommonService commonService;` mezőt,
a metódusban pedig `String ibuKod = commonService.Details(param);` áll a TODO-s `null` helyett. A fájl alapból a
migrátor gyökerében van (`java-variables.json`), más útvonalat a `java_variable_map` beállítás ad. Részletek:
[JAVITASOK_4_25_HU.md](JAVITASOK_4_25_HU.md).

**4.24.2 – `modName` az ősosztályból:** a `frm-forms-screen.ts` már nem generál saját `modName` gettert, mert a céges
`ServiceBase`-ből öröklődik. A futtató verziója 5; a projektben lévő példányt le kell cserélni. Részletek:
[JAVITASOK_4_24_2_HU.md](JAVITASOK_4_24_2_HU.md).

**4.24.1 – nagy formok:**

- **Nem száll el a migrálás:** a nagyon mély kód (hosszú `||` összefűzés) nem állítja le a futást; egy trigger
  váratlan belső hibája csak az adott trigger oka lesz.
- **Látható a hiba oka:** a hibaüzenet a hiba típusát és helyét mutatja, és hibás feladatnál az Áttekintés fülön ott
  a napló vége (Hibanapló), Másolás és Mentés gombbal.

Részletek: [JAVITASOK_4_24_1_HU.md](JAVITASOK_4_24_1_HU.md).

**4.24 – a második felmérés javításai, okosabb lekérdezőgombok, egyezés-ellenőrzés:**

- **Kevesebb tiltott végpont:**
  - az indítási kód keretrendszer-csomag változója (`….nav_opening_wnd := FALSE`) elhagyható;
  - egy helyi csomagból csak a hívott tagok kerülnek a blokkba;
  - a futásidejű `DEFAULT_WHERE` / `ORDER_BY` csak a lekérdezést érinti;
  - a `GET_BLOCK_PROPERTY` a form statikus beállításaiból, a `NAME_IN('BLOKK.MEZŐ')` kötött változóként fut;
  - a LONG típusú mező szöveg.
- **Üzenet-eljárások mindenhol:** `WUZENET`, `QMS$FORMS_ERRORS.PUSH`/`MSGGETTEXT`/`RAISE_FAILURE`, a beágyazott
  alert-blokk és a csak üzenetet mutató helyi eljárás vagy csomagtag `MESSAGE` lesz. A saját rutinokat a
  keretrendszer-katalógus új kulcsai sorolják fel: `message_calls`, `message_functions`, `failure_calls`.
- **Form-szintű adat-triggerek:** blokkonként futnak az Execution Hierarchy szerint.
- **Lekérdezőgombok:**
  - a WHERE-be fűzött mezőérték kötött paraméter lesz;
  - a szűrőépítő lehet paraméteres vagy magában a triggerben (`DECLARE`, `%TYPE`);
  - ismeri a `DECODE`/`CASE`/`IN`, a `CHR(39)`, a `NAME_IN` és a dinamikus `ORDER_BY` kifejezéseket.
- **Egyezés-ellenőrzés:**
  - generáláskor a Java WHERE-t összeveti az eredeti PL/SQL kiértékelésével;
  - eltérés esetén a tartalék út fut, és az ok megmondja, miben tér el a két WHERE;
  - `query_java_tests: true` mellett JUnit-teszt is készül.
- **Olvasható okok:** a hibák a forrás sorát is megmutatják.

Részletek: [JAVITASOK_4_24_HU.md](JAVITASOK_4_24_HU.md).

**4.23 – egyszerű Java lekérdezőgombok, eredeti kód régióban:**

- **Csak az SQL-kérés:** a `DEFAULT_WHERE`-építő lekérdezőgombból (pl. `LEKERDEZESI_FELTETELEK` + `WUZENET`)
  olvasható Java-metódus lesz. A képernyő mezőiből olvas, a WHERE-t Java `if`-ekkel rakja össze, és egyetlen
  JDBC-lekérdezést futtat. A `SET_BLOCK_PROPERTY`, `GO_BLOCK`, `EXECUTE_QUERY` és a `WUZENET` nincs emulálva (az
  egyszövegű hívás üzenet).
- **Nem blokkol más trigger:** ami a Formsban még futna, de nem fordult le (pl. form-szintű `POST-QUERY`), az a
  metódusban `// TODO`, nem 501-es hiba.
- **Eredeti kód régióban:** az eredeti Forms-kód `//region` … `//endregion` között, összecsukhatóan marad a
  metódusban. Így van a kézi gombvázakban és a frontend kézi navigációjában (`//#region`) is.
- **Régi mód:** a korábbi PL/SQL-adapter a `query_action_mode: "plsql"` beállítással kérhető, és tartalékként is fut.

Részletek: [JAVITASOK_4_23_HU.md](JAVITASOK_4_23_HU.md).

**4.22 – alert-párbeszédes üzenetek, fejlesztői bemenetek:**

- **Alert-párbeszédes üzenet-eljárások:** a helyi `WUZENET`-féle eljárás (`FIND_ALERT` konstans névvel,
  `SET_ALERT_PROPERTY`, `SHOW_ALERT`, `IF ID_NULL`) üzenetként fut, így az ilyen gombok (pl. a
  `LEKERDEZESI_FELTETELEK` lekérdezőgomb) nem maradnak kikommentezve. Az eljárás eredeti kódja megjegyzésként a
  generált metódusban marad.
- **Fejlesztői bemenetek:** amit a kód sehonnan nem kaphat meg (a formban nem lévő mező, adattriggerben másik blokk
  mezője vagy `:GLOBAL`, át nem adott `:SYSTEM` változó), az a Java-metódusban egy `null` kezdőértékű, `// TODO`
  jelölésű változó lesz. A trigger így lefut, a fejlesztő átadja a változónak a megfelelő értéket. A listájuk:
  `BACKEND_TASKS.md` → Fejlesztői bemenetek.

Részletek: [JAVITASOK_4_22_HU.md](JAVITASOK_4_22_HU.md).

**4.21 – nincs .frm-deploy.json, Angular-fordítási javítás, konstruktor:**

- **Nincs `.frm-deploy.json`:** a telepítés csak a generált fájlokat írja. A generált fájl mindig frissül, a
  CREATE_ONCE fájl (ServiceImpl, ControllerImpl, komponens) soha. A korábbi telepítések `.frm-deploy.json` fájljait
  törli.
- **TS4111 javítva:** a sablon `structures['…']` alakban hivatkozik a FormBlock-struktúrára (az Angular CLI
  `noPropertyAccessFromIndexSignature` beállítása miatt).
- **Konstruktor:** minden komponensben van `constructor() { super(); }`, a változók és a függvények között.

Részletek: [JAVITASOK_4_21_HU.md](JAVITASOK_4_21_HU.md).

**4.20 – frontend: rövidebb, Angularosabb képernyők:**

- **Csak a képernyő saját része:** a generált komponensben az adatai (`structures`, `tables`, `queries`, `lovs` …), az
  egysoros végpontmetódusok és a sablon vannak. A felmérési replika komponense 969 helyett 123 sor.
- **Forms-utánzás nélkül:** kurzorblokk, régió- és feliratlisták, feliratkozás-kezelés és validátor-másolatok nincsenek
  a modulban; a futtató a struktúrából vezeti le őket.
- **Egyszerűbb FormBlock-JSON:** mezőnként egy sor, a gombot `...this.button(ownId)`, a LOV-ot `...this.lov(ownId, lov)`
  köti be; táblázat: `frmTable(...)` és `<frm-table>`.
- **Felesleges kommentek nélkül:** csak a továbbfejlesztést segítő megjegyzések maradnak.
- **`frm-forms-screen.ts` 4-es változat:** a projektben cserélni kell (Segédfájlok).

Részletek: [JAVITASOK_4_20_HU.md](JAVITASOK_4_20_HU.md).

**4.19 – frontend: FormBlocksComponent, kérésnaplózás, modName:**

- **FormBlocksComponent:** a generált képernyő a `FormBlocksComponent`-et importálja (eddig
  `AnkFormBlockComponent`).
- **Kérésnaplózás:** minden sikeres HTTP-válasz legelőször a `WFF.debug(this.modName + '.<metódus>', res)`
  hívásba kerül.
- **`frm-forms-screen.ts` 3-as változat:** új `modName` getter (`WFF.trim(this.router.url, '/')`); a saját
  router-injektálás kikerült, a `router` a `ServiceBase`-ből jön.

Részletek: [JAVITASOK_4_19_HU.md](JAVITASOK_4_19_HU.md).

**4.18 – célmappák a generálás elején, pontosan oda:**

- **Célmappák az űrlapon:** a modul CL, DPS, WBS és frontend mappája már a generálás előtt kitallózható. A
  Java-mappák útvonalából lesz a csomag (a CL package mező megszűnt), ezzel készülnek a package sorok és az
  importok.
- **Pontosan oda:** a fájlok a kiválasztott mappába kerülnek, új csomag- vagy modulmappa nem készül.
- **Segédfájlok külön:** a `CommonMigrateTools.java` és a `frm-forms-screen.ts` nem kerül a projektbe; a feladatnál
  külön letölthető. A telepítés megkeresi a projektben lévő példányt, és arra igazítja az importokat.

Részletek: [JAVITASOK_4_18_HU.md](JAVITASOK_4_18_HU.md).

**4.17 – a mappák kitallózása, részenként:**

- **Tallózás:** a „Telepítés a projektbe” panelen a CL, DPS, WBS és frontend mappa (és a nem kötelező fő
  projektmappa) a „Tallózás…” gombbal választható ki, útvonalat nem kell beírni. A gomb a gép saját
  mappaválasztó ablakát nyitja meg; ha az nem nyílik meg, a felületbe épített mappaböngészőt.
- **Bárhol lehetnek:** a részeknek nem kell egy közös mappában lenniük. Parancssorból: `--layout RÉSZ=<mappa>`,
  `--project` nélkül is.

Részletek: [JAVITASOK_4_17_HU.md](JAVITASOK_4_17_HU.md).

**4.16 – DPS ServiceBase és generálás egyenesen a projektbe:**

- **DPS ServiceBase:** céges formátumban a DPS ServiceImpl a modul saját `XYServiceBase` osztályából örököl
  (`getModuleName()` → `XYConstants.NAME`).
- **Telepítés a projektbe:** `--project <fő mappa>` (vagy `deploy` parancs, vagy a webes felület „Telepítés a
  projektbe” panelje). A migrátor felismeri a CL, DPS, WBS és frontend projektet, és mindent a helyére tesz. A
  CREATE_ONCE fájlokat nem írja felül, és jelzi a kézzel módosított generált fájlokat.

Részletek: [JAVITASOK_4_16_HU.md](JAVITASOK_4_16_HU.md).

**4.15 – javítások egy valódi felmérés alapján:**

- **Képernyőpont:** a gomb kódjának közepén álló `EXECUTE_QUERY`, `CLEAR_BLOCK`, `CREATE_RECORD` … után a kód a
  képernyő új értékeivel folytatódik.
- **Helyi csomagok:** az inicializáló résszel rendelkező csomagok és a csomagtagba ágyazott alprogramok is
  beágyazódnak.
- **Felmérés:** a nem generálható indítási végpont oka és a takart okok is látszanak; a billentyű-triggerek
  kettébontva jelennek meg.
- **Átnevezés:** a „niva” elnevezés helyett mindenhol „frm” áll (`python -m frm_forms`, `FRM_…` környezeti
  változók, `frm-forms-screen.ts`, `frm_…` PL/SQL-segédek).
- A `frm-forms-screen.ts` (2-es változat) és a `CommonMigrateTools.java` (VERSION 5) fájlt cserélni kell.

Részletek és átállás: [JAVITASOK_4_15_HU.md](JAVITASOK_4_15_HU.md).

**4.14 – kevesebb kód, kevesebb kézi munka:** a felmérés mintáit utánzó formon minden végpont működik
(20 / 20). A generált képernyőkomponens harmadával, a DPS ServiceImpl a több működő végpont ellenére is
rövidebb lett.

Újdonságok:

- **Csatolt könyvtárak:** `--pld KONYVTAR.pld` (vagy feltöltés a weben, `.pll` esetén frmcmp). A form által
  elért könyvtári rutinok beágyazódnak, nem az adatbázisban keresi őket a kód.
- **`DO_KEY` a saját KEY-triggerrel:** a trigger kódja a hívás helyén fut, Forms-hatókörrel.
- **`COMMIT_FORM` a gomb közepén:** a képernyő a ponton ment, a gomb pedig a mentés után folytatódik.
- **Karcsúbb kód:**
  - a PL/SQL-segédek egyszer szerepelnek, a `CommonMigrateTools.FormsPlsql`-ben;
  - az üres utasítások kimaradnak;
  - a blokkőr egy sor lett.
- **Közös képernyő-futtató:** `frontend/frm-forms-screen.ts` projektenként egyszer, a komponens
  `extends FrmFormsScreen`.
- **`verify-db`:** a generált SQL és PL/SQL lefordítása a céladatbázisban (`DBMS_SQL.PARSE`), végrehajtás nélkül.
- **Felmérés – eltérések a Forms-működéstől:** a riport azt is megszámolja, mi működik, de nem pontosan úgy,
  mint a Formsban (többsoros írható blokkok, mentéskori validáció, eszköztáron nem futó KEY-triggerek …).
- A CL-projektben a `CommonMigrateTools.java` fájlt cserélni kell (VERSION 4).

Részletek és átállás: [JAVITASOK_4_14_HU.md](JAVITASOK_4_14_HU.md).

**4.13 – működő alkalmazás a felmérés alapján:** a felmérés okait sorban megszüntettük. Az indító- és
billentyű-triggerek nem tiltanak végpontot. Az ON-INSERT/ON-UPDATE/ON-DELETE az eredeti PL/SQL-lel fut. A
WHERE/ORDER BY az eredeti szöveggel fut az Oracle-ben. A kulcs nélküli blokkot ROWID azonosítja. A helyi
csomagok beágyazódnak.

Újdonságok:

- **Forms-emuláció:** a gombokban és az indítási kódban a Forms-hívások (GO_BLOCK, SET_ITEM_PROPERTY,
  EXECUTE_QUERY, SHOW_ALERT, CALL_FORM, :GLOBAL, :SYSTEM) felületi utasításként jutnak a képernyőre.
- **Indítási végpont:** a PRE-FORM és a WHEN-NEW-FORM-INSTANCE kódja egy végpontban fut, amikor a képernyő megnyílik.
- **Mentési lánc:** a `commitForm` végpont egy tranzakcióban, Forms-sorrendben ment. A képernyő eszköztárat kap:
  Új rekord, Törlés, Mentés.
- **Riport** a képernyő által nem hívott végpontokról.
- **Munkapad:** `analysis/MUNKAPAD.html`, a maradék kézi munka kártyákon, Jira-exporttal.
- A felmérés mintáit utánzó formon a végpontok 95%-a működik (korábban 0%).
- A CL-projektben a `CommonMigrateTools.java` fájlt cserélni kell (VERSION 3; a 4.14-től VERSION 4).

Részletek és átállás: [JAVITASOK_4_13_HU.md](JAVITASOK_4_13_HU.md). Forms-szemantika puska a kézi
átültetéshez: [FORMS_SZEMANTIKA_HU.md](FORMS_SZEMANTIKA_HU.md).

**Trigger-adapter javítások:** a dinamikus lekérdezésgomb feltételei `CHAR`
jelölőmezők numerikus összehasonlításaival is az eredeti PL/SQL-ben futnak.
A `DO_KEY` valódi `KEY-*` eseményneveket használ; saját key-trigger logikája
megmarad. A teljes felismerési ok és a megőrzött régi ServiceImpl jelzése
segít megkülönböztetni a felismerési hibát az át nem vett új kódtól.
Részletek: [JAVITASOK_TRIGGER_ADAPTEREK_HU.md](JAVITASOK_TRIGGER_ADAPTEREK_HU.md).

**Dinamikus lekérdezésgombok:** a helyi eljárásban állandó SQL-részletekből épülő
`DEFAULT_WHERE`, majd `GO_BLOCK` + `EXECUTE_QUERY` minta automatikusan átültethető.
Az eredeti feltételes PL/SQL Oracle-ben fut, a DPS ServiceImpl JDBC-vel kérdezi le a
célblokkot, az Angular pedig megjeleníti a rekordokat. Részletek, korlátok és
újragenerálás: [JAVITASOK_DEFAULT_WHERE_HU.md](JAVITASOK_DEFAULT_WHERE_HU.md).

**Fix Forms-mód:** a `:SYSTEM.MODE` és a statikus `NAME_IN('SYSTEM.MODE')` mindig
`NORMAL` értékre fordul a LOV-lekérdezésekben, blokkszűrőkben, triggerekben és
frontend mezőállapot-feltételekben. Az Angular felületen nincs Forms Enter Query mód;
a kliensnek nem kell módot küldenie. Más rendszer- és kontextusértékek ellenőrzése megmarad.
Az új kód átvételéhez generáld újra a modult; meglévő `ServiceImpl` esetén az
`analysis/backend-regeneration/` javaslatát össze kell fésülni a megőrzött fájllal.

**4.12.2 – Checkstyle-kompatibilis Java:** a generált backend-Java (és a `CommonMigrateTools`)
megfelel a céges Checkstyle- és PMD-ellenőrzésnek: kapcsos zárójelek, soronként egy utasítás,
üres sor a tagok között, csoportosított és rendezett importok `*` nélkül, UTF-8 literálok
`\u` escape helyett. Az importsorrend a `java_import_order` beállítással igazítható.
Részletek: [JAVITASOK_4_12_2_HU.md](JAVITASOK_4_12_2_HU.md).

**4.12.1 – Java metódusnevek és közös segédimport:** a gombtriggerek olvasható,
`on…` kezdetű metódusneveket kapnak, általános hash-utótag nélkül. A privát
blokksegédek neve is megfelel a megadott PMD-mintának. A migrátor
„Célkörnyezet és Java” paneljén megadható a `CommonMigrateTools` Java package-e.
Részletek és újragenerálás: [JAVITASOK_4_12_1_HU.md](JAVITASOK_4_12_1_HU.md).

**4.12 – céges CL/DPS/WBS-formátum:** az `AWU_AZON` megadásával a teljes backend
a megadott céges osztályhierarchiával készül. Új DPS/WBS ControllerBase és WBS ServiceBase,
egyező végpontok, UserDto továbbítás, céges válaszburkoló és log1x/log0x naplózás.
Az SQL/PLSQL továbbra is a DPS ServiceImpl-ben van; csak a DTO-hozzáférések változnak getter/setter hívásokra.
A privát importokat és a PUT/DELETE segédneveket a hostból kell megadni.
A frontend válaszburkolója és teljes WBS-útvonala még hostillesztést igényel;
a céges `--screen` kimenet alapból eseményes (`events`) módot használ.
`AWU_AZON` nélkül a CLI/API a korábbi formátumot használja.
Részletek: [BACKEND_FORMATUM_4_12_HU.md](BACKEND_FORMATUM_4_12_HU.md).

**Csak `.fmb`:** a fejlesztő csak a formot tölti fel. Az ismeretlen eljáráshívásokat futáskor az adatbázis oldja fel. A form programegységei megnevezve kerülnek a DPS ServiceImpl-be (`PlsqlUnits`).

**Futtatható backend és mezőállapotok (korábbi CL, AWU_AZON nélkül):**
- A támogatott adat-triggerek alapértelmezetten az eredeti PL/SQL-t futtatják a DPS ServiceImpl-ből,
  kötött mezőkkel és visszaírással. A korábbi Java-fordítás: `backend_trigger_mode: "java"`.
- Lekérdezőgombok (`DEFAULT_WHERE`-építő): `query_action_mode: "java"` (alapértelmezés, egyszerű Java-lekérdezés)
  vagy `"plsql"` (az eredeti eljárás Oracle-ben fut, előre lefordított változatokkal). `query_java_tests: true`:
  JUnit-teszt a Java-lekérdezések WHERE-jére (`backend/DPS-test/`, az egyezés-ellenőrzés eseteivel).
- A `RUNTIME_COVERAGE.md` külön mutatja a kód futtathatóságát és a tényleges eseménybekötést;
  például a szerveroldali WHEN-VALIDATE-ITEM jelenleg mentéskor fut, nem mezőelhagyáskor.
- Az `backend_live` beállítással (a weben alapból bekapcsolva) a generált backend azonnal éles.
- A `SET_ITEM_PROPERTY` alapú tiltás, rejtés és kötelezőség a feltételeivel együtt a frontendbe fordul.

Részletek: [AUTOMATIZALAS_HU.md](AUTOMATIZALAS_HU.md#plsql-az-adatbázisban-átfuttatás), [FRONTEND_SCREEN_HU.md](FRONTEND_SCREEN_HU.md#mezőállapotok-set_item_property).

**Képernyő-konvenciók:**
- Az `L_URES_*` mezők mindig üres `label` elemek, üres felirattal.
- A mezők kitöltik a sort; rés csak a térköz-mezők helyén marad.
- A mezőhosszak JSON-fájlból adhatók meg (`--field-lengths`, minta: `analysis/field-lengths.template.json`).
- A gombok `primary` színűek.
- Minden generált komponensbe bekerül a `ToastService` (`toast`), amely a hibákat, figyelmeztetéseket és sikeres műveleteket jelzi (`cím, részletek, true, élettartam`).
- A backend a céges mintát követi: a publikus `ServiceImpl`- és `ControllerImpl`-metódusok `log1x`-ben futnak, `UserDto user` paraméterrel. Az SQL/PLSQL és a segédek közvetlenül a DPS ServiceImpl-ben vannak, külön Data/domain/repository réteg nélkül.
- **4.10 – naptárképernyő és működéshűség:** a kizárólag kihagyott segédblokkhoz tartozó canvas napfeliratai sem készítenek külön oldalt/dialogot. Közös üzleti canvas és saját trigger megmarad. Natív PL/SQL alapértelmezés, javított kivételkezelés, eseménybekötési kimutatás: [MIGRACIOS_PONTOSSAG_4_10_HU.md](MIGRACIOS_PONTOSSAG_4_10_HU.md).
- **4.9 – csak Forms-ban működő hívások:** a Forms beépített eljárásai és csomagjai, valamint a katalógus `forms_runtime_calls` rutinjai (alapból `CALENDAR.*`) soha nem kerülnek `DbCalls` blokkba. A csak ilyen hívásokból álló trigger nem kap végpontot, vegyes triggerben a hívás helyén `NULL;` áll. Részletek: [OPTIMALIZALAS_HU.md](OPTIMALIZALAS_HU.md).
- **4.8 – triggerzaj-szűrés:** csak a bizonyítottan keretrendszeri, NULL vagy frontendműveletek maradnak ki a backend akciókból; a döntés a teljes kódon alapul. A dátumvalidáció és a vegyes üzleti logika megmarad. Átállás és ellenőrzés: [OPTIMALIZALAS_HU.md](OPTIMALIZALAS_HU.md).

Részletek: [FRONTEND_SCREEN_HU.md](FRONTEND_SCREEN_HU.md#mezőhosszak-segítő-json).

**Webes felület és generált frontend:**
- Az `.fmb` feltöltés hibája javítva: a Forms2XML a bemenet mellé írt, a migrátor csak az exportmappában keresett.
- Egy generálási mód van.
- Tömeges futtatás összesítővel.
- A több képernyős formnál a fő képernyőt lenyíló listából lehet kiválasztani, hibaüzenet helyett.
- A generált képernyők előnézete a felületen és a ZIP-ben is elérhető.
- A generált komponens backend-hívásai pontosan a CL `Constants` útvonalait követik.

Részletek: [LOCAL_START_HU.md](LOCAL_START_HU.md#webes-felület-egy-mód-tömeges-futtatás-fő-képernyő-előnézet), [FRONTEND_SCREEN_HU.md](FRONTEND_SCREEN_HU.md#backend-hívások-cl-útvonalak).

**Felmérés:** sok form egy futással, és megosztható riport arról, mi tiltja a generált végpontokat (egyedüli okok, anonimizált kódpéldák). Webes kapcsoló vagy `python -m frm_forms survey`. Részletek: [FELMERES_HU.md](FELMERES_HU.md).

**Automatizálás:** tömeges futtatás rangsorolt összesítővel (`batch`), Oracle adatszótár-export és -import (`dictionary-sql`, `dictionary-import`), DB-eljárás- és -függvényhívások fordítása ellenőrzött aláírással, generált LOV-végpontok és golden-tesztek a valódi formokra. Munkafolyamat: [AUTOMATIZALAS_HU.md](AUTOMATIZALAS_HU.md).

**Headstart/Designer tisztítás:** a Forms2XML `&#10;` sortöréseit a PL/SQL- és SQL-feldolgozás dekódolja, így a többsoros triggerek, WHERE és ORDER BY feltételek is lefordulnak. A képernyő-, billentyű- és keretrendszeri (`qms$…`) triggerek nem tiltják a backend-végpontokat; az indítási kód saját logikával, az adatesemények és a futásidejű `SET_BLOCK_PROPERTY` (WHERE, ORDER BY, `…_ALLOWED`) továbbra is tilt — műveletenként, nem mindent. Csak a Forms-blokk által engedett műveletek végpontjai készülnek el; az adatforrás nélküli blokkok (pl. `CALENDAR`, `QMS$TRANS_ERRORS`) nem kapnak backendet; a kihagyások oka az `analysis/backend-plan.json`-ban. A modulszintű tiltás (képernyőváz, öröklés) a ServiceImpl elején egyszer szerepel, és egyetlen `MODULE_REVIEWED` kapcsoló élesíti a kész műveleteket. A frontend a `L_URES_*` térköz-mezőket üres elemként rajzolja: [Térköz-mezők](FRONTEND_SCREEN_HU.md#térköz-mezők).

**4.4.2 javítás:** az `ObjectGroupChild Type` és a korábbi típusmező-nevek egységes kezelése, ellenőrzött aliasokkal. [Részletek és frissítés](docs/VALTOZASOK_4_4_2_HU.md).

**4.4.1 javítás:** az azonos nevű `Package Spec` / `Package Body` pár szabályos bemenet; mindkét forrás megmarad. [Részletek és frissítés](docs/VALTOZASOK_4_4_1_HU.md).

**Az új egyfájlos, szerkezethű frontendhez:** [FRONTEND_SCREEN_HU.md](FRONTEND_SCREEN_HU.md). A webes felület alapértelmezett módja a Képernyőváz; CLI-ben `--screen`. Az alábbi többfájlos UI-leírás a meglévő szigorú mód dokumentációja.

**Új a 4.1-ben:** CALENDAR mapping javítás, kereshető offline modultérkép, `--scaffold` migrációs váz és kommentelt Java gombvégpontok. [Részletes útmutató és a FRM_ANK_FADLEK eredménye](docs/MODULTERKEP_ES_VAZ_HU.md).

A migrátor egy Forms modulból a fő alkalmazásba beilleszthető forrásokat állít elő. Nem hoz létre új Angular vagy Spring alkalmazást. A generált Angular frontend kizárólag a verziózott `analysis/ui-model.json` alapján készül; a 4.1-es Java-generálás emellett ellenőrizendő gombvégpontokat és forráskommenteket is készít.

## 1. Csomagold ki és indítsd el

Python 3.10 vagy újabb szükséges. A parancssori generátorhoz nincs külső Python-függőség. A böngészős felülethez:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt
.\.venv\Scripts\python.exe -m frm_forms.web --port 8000
```

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-web.txt
.venv/bin/python -m frm_forms.web --port 8000
```

Nyisd meg a `http://localhost:8000` címet. A migrátor API URL mezőjébe írd: `http://localhost:8000/api`. A teljes, fordított Angular kezelőfelület benne van a ZIP-ben; ehhez nem kell npm vagy Node. A folyamat helyben fut, alapértelmezés szerint nincs AI-hívás.

## 2. Próbáld ki Oracle nélkül

A felületen a **Példa betöltése** egy szintetikus XML-t tölt be. Parancssorból:

```bash
python -m frm_forms migrate examples/customer_fmb.xml --schema examples/schema.json --out build/customer --ai off --zip
```

Kimenet: `build/customer/` és `build/customer.zip`. A kész minták a `sample-output/` mappában is megtalálhatók. A minták nem a céges Oracle modulok exportjai.

## 3. Exportáld a valódi bemeneteket

Az Oracle Forms telepítés parancskörnyezetében:

```bash
frmf2xml USE_PROPERTY_IDS=NO DUMP=ALL OVERWRITE=YES CUSTOMER.fmb qmsolb65.olb shared.olb navigation.mmb
```

- `USE_PROPERTY_IDS=NO`: property-nevek kerüljenek az XML-be.
- `DUMP=ALL`: a teljes property-készletet exportáld.
- `OVERWRITE=YES`: az exportáló felülírhatja a saját korábbi XML-jeit.
- Őrizd meg a fájlneveket: `CUSTOMER_fmb.xml`, `qmsolb65_olb.xml`, `navigation_mmb.xml`.
- Az OLB-ben örökölt property-khez/triggerekhez az OLB XML is kell; a DUMP=ALL önmagában nem helyettesíti a könyvtári hivatkozások feloldását.
- A `ParentFilename` nem engedélyez fájlrendszeri automatikus keresést: minden könyvtárat te adsz meg.

[Oracle Forms2XML parancssori leírás](https://docs.oracle.com/en/database/oracle/application-express/19.2/aemig/Converting_FormModules_ObjectLibraries_MenuModules_to_XML.html). A valódi exporterhez a saját Oracle verziód telepítése és működő környezete szükséges.

Közvetlen FMB-bemenet is használható, ha a Python folyamat számára elérhető a `frmf2xml`. Egyedi exportáló argumentumlistája a config `export_command` kulcsába tehető; példa az `examples/config-oracle-java.json`. OLB/MMB kapcsolóval csak XML adható meg. A PLL/PLD feldolgozás nem része ennek a kiadásnak.

## 4. Nézd meg az attribútumleltárt

```bash
python -m frm_forms inventory CUSTOMER_fmb.xml qmsolb65_olb.xml navigation_mmb.xml --out build/inventory
```

Az `inventory.json` az XML-ben ténylegesen szereplő elem- és attribútumneveket, gyakoriságot és rendezett példaértékeket tartalmazza. Az `unused-attributes.json` az ismeretlen/nem használt neveket külön listázza. A `Property` gyerekelemek property-nevei külön leltárban szerepelnek. Az inventory nem futtat generálást, ezért ismeretlen attribútumokat tartalmazó XML vizsgálatára is használható; hibás XML-t elutasít.

A `recognized` statikus feldolgozási katalógust jelent, nem azt, hogy a property minden itemtípusnál aktív. A részletes szabályok: `docs/ATTRIBUTE_MAPPING_HU.md`. Verziófüggő eltérő névhez csak ellenőrzött, explicit alias használható:

```json
{
  "property_aliases": {
    "Item": {"VersionSpecificLength": "MaximumLength"}
  }
}
```

Nem ismert property-t a program nem fordít le hasonló hangzás alapján. Ismert viselkedést nem lehet az `ignored_properties` kapcsolóval kikapcsolni. Ismeretlen, igazoltan kizárólag metaadat jellegű attribútum figyelmen kívül hagyása külön, indoklást tartalmazó configbejegyzéssel lehetséges; ebből mindig review issue lesz.

## 5. Generálj

```bash
python -m frm_forms migrate CUSTOMER_fmb.xml --olb qmsolb65_olb.xml --olb shared_olb.xml --mmb navigation_mmb.xml --config examples/config-company.json --out build/customer --ai off --zip
```

A `--olb` ismételhető; a `--mmb` egyszer adható meg. A menü XML megőrzésre és öröklésfeloldásra szolgál; teljes Angular menü nem készül belőle. A backend nélküli frontend-munkához add hozzá a `--frontend-only` kapcsolót. A frontend neve a `FormModule.Title` normalizált alakja; üres Title esetén a Name. A `--module` csak a Java/API technikai nevet szabályozza.

A fail-closed ellenőrzés a forrásgenerálás és az AI előtt fut. Hiányzó OLB, ciklus, többértelmű típus, nem támogatott item, ismeretlen attribútum vagy nem értelmezhető elrendezés esetén hibával leáll, a célmappa/ZIP nem jön létre. AI-off módban azonos exportált XML, konfiguráció és kimeneti csomagnév azonos forrásfájlokat és ZIP-bájtokat eredményez; nincs beépített futásidőbélyeg.

## 6. Vizsgáld meg az elutasítást

```bash
python -m frm_forms migrate CUSTOMER_fmb.xml --olb qmsolb65_olb.xml --out build/analysis --analysis-only --ai off
```

Ez kifejezetten elemzési csomag: nincs benne Java vagy Angular forrás. Feloldható bemenetnél a hibás/nem támogatott itemek is szerepelnek az UI-modellben és a riportban. Bemeneti/öröklési hiba esetén csak `analysis/input-issues.json` és a riport készül; feloldatlan adatokból nem keletkezik megtévesztő részmodell. A hiányzó OLB-k mellett a biztosan ismert érintett formitemek száma szerepel; hiányzó szülőblokk további örökölt itemeket is rejthet.

Kilépési kódok: `0` siker; `1` elutasítás/futtatási hiba; `3` elemzési hibák vagy a régi backend `--strict` review-jelzése; `130` megszakítás. A `--strict` a backend korábbi működését őrzi; a frontend hibái ettől függetlenül megállítják a normál generálást.

## 7. Mi van a kimenetben?

| Útvonal | Tartalom | Újragenerálás |
|---|---|---|
| `frontend/<modul>/component.ts` | Angular 22 integrációs komponens | Csak egyszer |
| `component.html`, `component.scss` | Sablon, stílushely | Csak egyszer |
| `model.ts` | Típusok, blokkonkénti Value/Wire interfészek, értékkonverzió/validáció | Mindig |
| `form-structure.ts` | FormBlock factory és FormGroup-kötés | Mindig |
| `actions.ts`, `endpoints.ts` | Trigger- és endpoint-deklarációk | Mindig |
| `surfaces.ts` | Canvas/tab megjelenítési csoportok | Mindig |
| `blocks/<blokk>.component.ts` | Blokkkomponens, `p-table` vagy FormBlock | Csak egyszer |
| `blocks/<blokk>/form-structure.ts` | Blokkspecifikus mezőszerkezet | Mindig |
| `i18n/<modul>.hu.json` | Látható szövegek | Mindig |
| `analysis/ui-model.json` | Sémavalidált UI-modell 1.0.0 | Mindig |
| `analysis/record-groups/*.sql` | A LOV-ban használt RecordGroupQuery | Mindig |
| `backend/CL`, `DPS`, `WBS` | A változatlan Java-generátor forrásai | Mindig |
| `migration-report.md`, `generated-files.json` | Riport, hash-ek és felülírási szabályok | Mindig |

A nyers XML, a feloldott XML, a property-proveniencia és az OLB-katalógus az `analysis/` alatt megmarad.

## 8. Illeszd be a host alkalmazásba

Részletes útmutató: a generált `INTEGRATION.md`, valamint `COMPANY_PROFILE_HU.md`.

- Angular 22, a saját FormBlock implementáció, az Optimus `p-table`, a host témája/Tailwind és az i18n-szolgáltatás szükséges.
- Alapból `emit_imports=false`: a fejlesztő teszi be az importokat. Automatikus importhoz a configban a tényleges céges útvonalakat/exportált neveket kell megadni.
- A `translate` input a host fordítófüggvénye. Az `action` a forrásgombok ellenőrzött üzleti kezelője; a `lookup` a deklarált LOV endpoint adaptere. A generátor nem hajtja végre automatikusan a PL/SQL-t.
- A LOV útvonala null, amíg nincs jóváhagyott backend-szerződés. Nincs generált LOV repository/controller. Az SQL csak elemzési anyag.
- Az API URL alapja `environment.baseUrl`; a program nem talál ki céges hostnevet.
- A JAVA oldalon CL → közös DTO/Constants/RestClient; DPS → DB/üzleti logika; WBS → továbbítás CL RestClienttel. A jogosultság, adatforrás és a meglévő backend review-tiltások feloldása továbbra is hostfeladat.

## 9. Újragenerálás

```bash
python -m frm_forms migrate CUSTOMER_fmb.xml --olb qmsolb65_olb.xml --out build/customer --regenerate --zip
```

Csak 4.x UI-modell manifesttel rendelkező cél frissíthető. A CREATE_ONCE fájlok bájtjai változatlanok maradnak, a kézi kiegészítő fájlokat is megőrzi. Minden generálás először átmeneti mappában készül. Hiba esetén a korábbi eredmény megmarad. Ha mezőt, blokkot vagy selector-kontraktust változtatsz, a megőrzött integrációs komponenseket kézzel hozzá kell igazítani; a generátor ezeket nem írja felül.

## 10. AI és offline működés

Az AI mód `off | cached | assist`, alapból `off`. A frontend típus- és layoutdöntéseit az AI nem írhatja felül, ezért a frontendforrások AI-módtól függetlenül determinisztikusak. AI használatakor a diagnosztikai csomag javaslatai és cache/hívási statisztikái eltérhetnek; a teljes ZIP bájtazonosságát AI-off módban ellenőrizzük. Az `assist` kizárólag a meglévő, rövid triggerjavaslatokhoz használja az Ollamát; kis kontextus, kéréslimit és cache maradt.

```powershell
$env:FRM_OLLAMA_URL = "http://BELSO-OLLAMA:11434"
$env:FRM_OLLAMA_MODEL = "frm-model"
```

A webes felületen is megadható az Ollama URL/modell. A `http://xx:11434` csak alapértelmezett helykitöltő. Az AI nélküli generálás nem kér hálózati szolgáltatást. Offline telepítéshez az internetes, azonos Python/OS környezetben töltsd le a wheel-eket (`pip download -r requirements-web.txt -d python-packages`), majd a célgépen telepíts `--no-index --find-links=python-packages` kapcsolókkal. A lefordított webes felület helyi fájlokat használ.
