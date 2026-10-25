# 4.24 – A második felmérés javításai, okosabb lekérdezőgombok, egyezés-ellenőrzés

## Miért

A 4.23-mal készült felmérés (5 form, 82 végpont) szerint a végpontok fele tiltott volt. A leggyakoribb okok:

| Ok a felmérésben | Végpont | Javítás (4.24) |
|---|---:|---|
| Gomb beágyazott alert-blokkal, utána helyi eljárás `qms$forms_errors.push`-sal (T9) | 5 | üzenet-eljárások mindenhol |
| Indítás: `<keretrendszer-csomag>.nav_opening_wnd := FALSE;` | 4 | a keretrendszer-csomag állapota elhagyható |
| Helyi csomag, amelynek egy másik tagja `GET_BLOCK_PROPERTY`-t hív | 4 | csak a hívott tagok kerülnek a blokkba |
| `WHEN-VALIDATE-RECORD`: `csomag.alert_eljárás(csomag.KONSTANS)` | 3 | a csomag alert-eljárása üzenet |
| Futásidejű `DEFAULT_WHERE` / `ORDER_BY` a beszúrást, módosítást és törlést is tiltotta | 4 | csak a lekérdezést érinti |
| `GET_BLOCK_PROPERTY`, `NAME_IN('BLOKK.MEZŐ')` csomagban, adattriggerben | 4 | a form statikus értéke, illetve kötött változó |
| LONG típusú mező | 1 | szövegként kezelve |

A felmérésben szereplő minták (névcserés formában) a `test_survey_424` tesztben vannak. A felmérés replikáján
minden végpont engedélyezett.

## 1. Indítási végpont: a keretrendszer-csomag állapota

`qms$….nav_opening_wnd := FALSE;` a Headstart navigációjának állapota, nem üzleti logika. Ha a csomag a katalógus
`call_prefixes` listájában van, és a kapott érték tiszta (konstans, literál), az értékadás ugyanúgy kimarad, mint a
keretrendszer-hívás. Az indítási végpont (PRE-FORM + WHEN-NEW-FORM-INSTANCE) így elkészül. A rekordcsoport-építés
(`CREATE_GROUP`, `ADD_GROUP_ROW` …), a `SET_APPLICATION_PROPERTY` és a `FORMS_DDL('ALTER SESSION …')` már eddig
is működött.

## 2. Helyi csomagok: csak a hívott tagok

Eddig egy helyi csomag egészben került a névtelen PL/SQL-blokkba. Ha bármelyik tagja az adatbázisban nem futtatható
hívást tartalmazott (`GET_BLOCK_PROPERTY(.., CURRENT_RECORD)`, `GO_BLOCK`, `FIND_ALERT` …), a csomagot hívó összes
gomb és trigger tiltott lett.

4.24-től ilyenkor a blokkba csak az kerül, amit a kód elér:

- a hívott tagok és az általuk hívott tagok;
- az általuk használt csomagváltozók és konstansok;
- a csomag inicializáló része.

A generált metódus megjegyzése ezt jelzi (`Helyi csomag, csak a hívott tagjai`). Ha egy elért tag nem futtatható,
az ok megnevezi a tagot (`A(z) PKG helyi csomag nem futtatható: PKG.TAG tag: …`).

## 3. Üzenet-eljárások mindenhol

A migrált felület maga jeleníti meg az üzeneteket, ezért a Forms üzenetmegjelenítő eszközeit nem kell utánozni.
A kód futtatása előtt minden, ami csak szöveget mutat, `MESSAGE(szöveg)` lesz. Ez a gombokra, az indításra, az
adattriggerekre és a lekérdezőgombokra egyaránt érvényes:

| Forms-kód | Migrálva |
|---|---|
| `WUZENET('Nincs kiválasztva!');` | `MESSAGE('Nincs kiválasztva!');` |
| `QMS$FORMS_ERRORS.PUSH(QMS$FORMS_ERRORS.MSGGETTEXT(37, 'Szöveg'), 'E', 'X', 37);` | `MESSAGE('Szöveg');` |
| `QMS$FORMS_ERRORS.RAISE_FAILURE;` | `RAISE FORM_TRIGGER_FAILURE;` |
| `DECLARE al ALERT; n NUMBER; BEGIN al := FIND_ALERT('A'); SET_ALERT_PROPERTY(al, ALERT_MESSAGE_TEXT, 'Hiba'); n := SHOW_ALERT(al); END;` | `MESSAGE('Hiba');` |
| helyi eljárás vagy csomagtag, amely csak a paraméterét mutatja alertben | `PROCEDURE x(p VARCHAR2) IS BEGIN MESSAGE(p); END;` |

- **Ami nem üzenet:** az a párbeszéd, amelynek a válaszát a kód felhasználja (`IF SHOW_ALERT(..) = ALERT_BUTTON1`),
  marad a korábbi emulációval (a képernyő megkérdezi a felhasználót).
- **Saját rutinok:** a keretrendszer-katalógus (`framework_catalog`) három új kulcsa sorolja fel őket:

```json
{
  "message_calls": {"QMS$FORMS_ERRORS.PUSH": 1, "WUZENET": 1, "CEG_UZENET.HIBA": 2},
  "message_functions": {"QMS$FORMS_ERRORS.MSGGETTEXT": 2},
  "failure_calls": ["QMS$FORMS_ERRORS.RAISE_FAILURE"]
}
```

- **A kulcsok jelentése:**
  - `message_calls`: a szám a szöveges argumentum sorszáma;
  - `message_functions`: a függvény a megadott argumentumát adja vissza (az `MSGGETTEXT` alapértelmezett szövegét);
  - `failure_calls`: ezek a rutinok `RAISE FORM_TRIGGER_FAILURE`-ként futnak.
- **Alapértékek:** a gyári katalógus a fenti Headstart-rutinokat és a `WUZENET`-et tartalmazza. Egy saját katalógus,
  amelyből a kulcs hiányzik, ezeket az alapértékeket kapja.
- **Óvatosan:** naplózó vagy más munkát is végző eljárást ne vegyél fel a katalógusba, mert csak az üzenet maradna
  belőle.

## 4. Form-szintű adat-triggerek blokkonként

A Formsban egy form-szintű `POST-QUERY`, `WHEN-VALIDATE-RECORD`, `PRE-INSERT`, `PRE-UPDATE`, `PRE-DELETE`,
`POST-INSERT`/`UPDATE`/`DELETE` vagy `ON-CHECK-DELETE-MASTER` minden olyan blokkra lefut, amelynek nincs saját
triggere ugyanarra az eseményre. A blokk saját triggerének Execution Hierarchy beállítása ezt módosítja:

- **Before:** a blokk saját kódja fut előbb, utána a form-szintű.
- **After:** a form-szintű kód fut előbb.

4.24-től a migrátor is így jár el:

- **Blokkonkénti másolat:** minden érintett adatbázis-blokk kap egy másolatot (`FORM:POST-QUERY@BLOKK`). A másolat
  úgy fordul és kötődik be, mint a blokk saját triggere.
- **Ismert blokknév:** a másolatban a `:SYSTEM.TRIGGER_BLOCK` a blokk neve.
- **Az eredeti trigger:** csak felsorolja a másolatait (`target: "per-block"`).
- **Korábbi következmény:** a 4.23-ban egy le nem fordult form-szintű `POST-QUERY` a lekérdezőgomb TODO-ja lett.
  4.24-től (ha az adatbázisban futtatható) a sorokon lefut.
- **Kimarad:** a cserélő `ON-INSERT`/`UPDATE`/`DELETE` és a mezőszintű események form-szinten maradnak.

## 5. Lekérdezőgombok (Java): több változat, mezőértékek kötött paraméterként

A 4.23-as Java-lekérdezőgomb csak az állandó SQL-darabokból álló, paraméter nélküli szűrőépítő eljárást fogadta el.
4.24-ben az elfogadott változatok bővültek. A gomb mostantól két metódus:

- **`<metódus>Query(képernyőértékek…)`:**
  - visszaadja a WHERE-t, a rendezést, a kötött értékeket és az üzeneteket (`QueryText`);
  - ugyanúgy építi fel őket, ahogy a Forms-kód;
  - statikus, adatbázis nélkül hívható, a generált teszt is ezt hívja.
- **A végpont:**
  - beolvassa a kérésből a képernyő értékeit;
  - meghívja a `…Query` metódust;
  - futtatja a JDBC-lekérdezést.

```java
    static QueryText onT1PbLekerdezesQuery(String col1, String col5, BigDecimal col2) {
        var q = new QueryText();
        String lekSql = "";
        if (col1 != null) {
            lekSql = "COL6 = '00'";
            lekSql += " and COL7 = :col1";                        // ' and COL7 = ''' || :T1.COL1 || ''''
            lekSql += " and COL8 like '%' || :col5 || '%'";       // ' like ''%' || NAME_IN('T1.COL5') || '%'''
            if (BigDecimal.ONE.equals(col2)) {
                lekSql += " and COL9 = 'X'";                      // ' = ' || CHR(39) || 'X' || CHR(39)
            }
            q.where = lekSql;
            q.run = true;
            q.params.addValue("col1", col1);
            q.params.addValue("col5", col5);
        } else {
            q.messages.add("Adatlap kiválasztása nem történt meg!");
        }
        return q;
    }
```

- **Mezőérték az SQL-szövegben → kötött paraméter:**
  - Az SQL-szövegbe fűzött képernyőérték kötött paraméter lesz. Ide tartozik a `:BLOKK.MEZŐ` és a
    `NAME_IN('BLOKK.MEZŐ')`, valamint egy helyi változó is, akár idézőjelben, akár anélkül. Az idézőjeles darab
    kettéválik (`'%' || :nev || '%'`), az üres fele elmarad (`= :kod`).
  - Szöveges érték idézőjel nélkül nem kerülhet az SQL-be (az SQL-részlet lenne). Ilyenkor a gombot a PL/SQL-adapter
    viszi.
- **Szűrőépítő paraméterrel:**
  - `LEKERDEZES('BLK', :XY_LAP.KOD)`: a paraméter a hívás argumentuma.
  - Ha az argumentum blokknév, a `SET_BLOCK_PROPERTY` is megkapja.
- **Szűrőépítő a triggerben:**
  - A szűrőépítő lehet magában a triggerben is, `DECLARE`-rel és több helyi változóval.
  - A változó típusa lehet `VARCHAR2`, `NUMBER`, `DATE` vagy `tábla.oszlop%TYPE`. A `%TYPE` típusát az első értéke
    adja.
- **Kifejezések:**
  - `DECODE` és `CASE` az SQL-szövegben (Java feltételes kifejezés lesz);
  - `IN`, `NOT IN` és `BETWEEN` a feltételekben;
  - `CHR(39)` és `CHR(10)` az összefűzésben.
- **Rendezés:** dinamikus `ORDER_BY` változóból (`q.orderBy`, üresen a blokk alaprendezése).
- **Mentés és visszaállítás `CLEAR_FORM` körül:** a Forms-szokás (`v := :X; CLEAR_FORM; :X := v;`) felismerve. A
  törölt mezők `NULL`-ként olvasódnak, a visszaírt érték az eredeti.
- **Biztonságosabb WHERE:**
  - A `DEFAULT_WHERE` zárójelbe kerül, így a master-detail `AND` feltétel nem keveredik egy `OR`-ral.
  - Az elején álló `WHERE` kulcsszó elmarad.
  - Üres szöveg esetén minden sor visszajön.
- **Nem tiltja saját magát:** a gomb saját `SET_BLOCK_PROPERTY`-je az, amit a Java-lekérdezés megvalósít, ezért már
  nem jelenik meg TODO-ként.

### Ami továbbra is a tartalék útra kerül (PL/SQL-adapter, PL/SQL az adatbázisban, kézi)

Az ok olvasható formában megjelenik a `form.ir.json` (`query_java_reason`), a `backend-plan.json` és a
`BACKEND_TASKS.md` → Lekérdezőgombok helyen, valamint a metódus megjegyzésében. Ilyen esetek:

- **SELECT … INTO a képernyő mezőibe:** a felmérés F3-as gombja előbb a fejadatot tölti a mezőkbe, utána kérdez le.
  A lekérdezés eredménye (`PageResult`) a képernyő más mezőit nem tölti, ezért ez a gomb a tartalék útra kerül. Az
  F3 a futásidejű `DEFAULT_WHERE` miatt ott is kézi átültetés marad (lásd lent).
- **Saját kivételkezelés:** `EXCEPTION WHEN …`, `RAISE <saját kivétel>`.
- **Képernyőmező írása:** az a kód, amely a fenti visszaállításon túl mezőt ír.

## 6. Egyezés-ellenőrzés generáláskor, JUnit-teszt

A Java-kód egy köztes leírásból készül, és a migrátor ezt Pythonban ki is értékeli. Ugyanazokon a bemeneti
eseteken az eredeti PL/SQL-t is kiértékeli Oracle-szemantikával: `''` = `NULL`, a `NULL` összefűzése üres,
háromértékű logika, `DECODE`, `NVL`, `REPLACE(…, CHR(39))`. Az eredményt összeveti.

- **Bemeneti esetek:**
  - minden mező üresen;
  - minden mező kitöltve;
  - minden IF-ág, a feltételéhez illő értékekkel;
  - a feltételekben szereplő literálok;
  - szükség esetén egyszerre egy érték változik.
- **Összevetés:**
  - a Java-oldalon a kötött értékeket SQL-literálként behelyettesíti;
  - mindkét oldalt normalizálja: szóközök, `'a' || 'b'`, `NVL`/`TO_CHAR` literálokon.
- **Eltérés esetén:** a gomb nem lesz Java-lekérdezés, és az ok megmutatja az esetet és a két WHERE-t, például:
  `A Java WHERE eltér az eredeti PL/SQL-étől (eset: T1.COL2='1', …; WHERE - PL/SQL: "…", Java: "…")`.
- **Egyezés esetén:** a metódus megjegyzése jelzi
  (`Egyezés-ellenőrzés: 20 bemeneti esetben a Java WHERE megegyezik az eredeti PL/SQL-ével.`), a `backend-plan.json`
  pedig az `equivalence` mezőben.
- **Nem ellenőrizhető esetek:** amit Pythonban nem lehet eldönteni (adatbázis-függvény egy feltételben), az
  „nem ellenőrizhető” esetnek számít, nem eltérésnek. Ilyen az is, ha az eredeti kód `NULL` számot fűzne idézőjel
  nélkül az SQL-be: az a Formsban is hibás SQL lenne.
- **JUnit-teszt:** `"query_java_tests": true` mellett elkészül a `backend/DPS-test/<Modul>QueryTextTest.java`
  (JUnit 5).
  - Ugyanezekkel az esetekkel hívja a `…Query` metódusokat, és ellenőrzi a WHERE-t, a rendezést, a kötött értékeket
    és az üzeneteket.
  - Helye: a DPS modul `src/test/java` mappája, a ServiceImpl csomagjában. A telepítő nem másolja.
  - Ha a fejlesztő később átírja a metódust, a teszt jelzi, ha a viselkedés eltér az eredetitől.

## 7. Olvasható okok

- **A PL/SQL-értelmező hibája** megmutatja a forrássort. Például a korábbi
  `Várt token: ;; kapott: VN_HOSSZ, pozíció: 10.` helyett:
  `Nem értelmezhető PL/SQL: itt „;” kellene, de „VN_HOSSZ” áll (2. sor: vn_hossz NUMBER;).`
- **A lezáratlan megjegyzés, szöveg és q-literál hibája** is megmutatja a sort (felmérés: `Lezáratlan /* megjegyzés`).
- **A Java-lekérdezés okai** (`query_java_reason`) a 6. pont szerinti helyeken jelennek meg.

## 8. Kisebb javítások

- **`NAME_IN('BLOKK.MEZŐ')`:** az adattriggerekben és helyi eljárásaikban is a mező értéke (kötött változó), nem
  hiba. Számított névvel továbbra is ok.
- **`GET_BLOCK_PROPERTY` a form statikus beállításaiból:** `QUERY_ALLOWED`, `INSERT_ALLOWED`, `UPDATE_ALLOWED`,
  `DELETE_ALLOWED`, `DEFAULT_WHERE`, `ORDER_BY`, `QUERY_DATA_SOURCE_NAME`, `DML_DATA_TARGET_NAME`,
  `RECORDS_DISPLAYED`.
  - Szó szerinti blokknévvel az érték kerül a helyére.
  - Számított blokknévvel (például `NAME_IN('SYSTEM.CURSOR_BLOCK')`) a blokkokon végigmenő `CASE`.
  - Ha a kód valahol futásidőben állítja a tulajdonságot (`SET_BLOCK_PROPERTY`), nincs csere.
- **Futásidejű `DEFAULT_WHERE` / `ONETIME_WHERE` / `ORDER_BY`:** csak a blokk lekérdezését (lista, keresés) tiltja;
  a beszúrás, módosítás és törlés kulcs szerint dolgozik.
  - Ha egy Java-lekérdezőgomb maga alkalmazza, csak átnézendő tétel (review).
  - Ilyenkor a blokk saját keresése az eredeti WHERE-rel fut.
- **Forms-adattípusok:** a `LONG` és az `ALPHA` szöveg (a `LONG`-hoz ellenőrzési megjegyzéssel), a `MONEY`,
  `RNUMBER`, `RINT`, `RMONEY` szám, az `EDATE` és a `JDATE` dátum.
- **`--` megjegyzés CR sortöréssel:** csak CR-rel tördelt kódban a `--` megjegyzés eddig a szöveg végéig tartott; most
  a sor végén ér véget.
- **Java-fordító (`backend_trigger_mode: "java"`) és `DECLARE`:**
  - kezeli a `DECLARE`-t és a helyi változókat (`%TYPE` a blokk oszlopából), az `IN`-t és a `BETWEEN`-t;
  - a változók egy névtelen tartóobjektum mezői (`var local = new Object() { … };`), hogy a `SqlValues.and/or`
    lambdái is olvashassák őket.

## Ami a felmérésből marad

- **F3-as gomb:** fejadat SELECT … INTO a képernyőmezőkbe, majd lekérdezés. A PL/SQL-adapter viszi, de a
  `SET_BLOCK_PROPERTY(DEFAULT_WHERE)` miatt kézi.
- **Képernyőlépés egy csomagtag vagy eljárás közepén:** `EXECUTE_QUERY` / `CLEAR_BLOCK` / `COMMIT_FORM` után további
  kód.
- **Egyéb:** `DO_KEY` saját `KEY-COMMIT`-tal, `WHEN-CREATE-RECORD`, `DEFAULT_VALUE` UI-hívás.
- **Frontend-feladatok:** `KEY-*`, `ON-ERROR`, `WHEN-NEW-*-INSTANCE`. A végpontokat nem tiltják.

## Ellenőrzés

- **Új teszt (`test_survey_424`):**
  - a felmérés replikája a fenti mintákkal: minden végpont engedélyezett, a Java lefordul;
  - üzenet-eljárások és saját katalógus;
  - olvasható értelmezőhibák, a bővített nyelvtan;
  - `GET_BLOCK_PROPERTY` és `NAME_IN`, LONG típusú mező;
  - form-szintű triggerek (Override, After);
  - Java-fordító `DECLARE`-rel;
  - lekérdezőgomb-változatok (bindek, paraméter, `DECODE`/`IN`, dinamikus rendezés, beágyazott `DECLARE`,
    `CLEAR_FORM` visszaállítás, SELECT INTO tartalék);
  - az egyezés-ellenőrzés megfogja a szándékosan elrontott fordítást;
  - a generált JUnit-teszt lefordul és lefut (JUnit-csonkokkal).
- **`test_query_java`:** az új szerkezetre frissítve; a form-szintű `POST-QUERY` már nem TODO, hanem lefut.
- **`test_headstart_cleanup`:** a futásidejű `DEFAULT_WHERE` + `EXECUTE_QUERY` gomb Java-lekérdezés lett, a blokk
  olvasását nem tiltja; a lekérdezés nélküli futásidejű WHERE továbbra is tiltja.

## Átállás

- **ServiceImpl:** CREATE_ONCE fájl. A friss metódusok (a `…Query` metódus, a `QueryText` és a `whereText` segéd)
  a `--regenerate` után az `analysis/backend-regeneration/` mappában vannak.
- **Frontend:** a frontend és a CL-szerződés (`QueryActionRequest` → `PageResult`) nem változott.
- **Saját keretrendszer-katalógus:** érvényes marad; az új kulcsok nélkül a gyári alapértékek élnek.
