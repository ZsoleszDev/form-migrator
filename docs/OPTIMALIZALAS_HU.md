# Csak Forms-futtatókörnyezetben működő hívások – 4.9.0

A 4.10 további javításai: [naptárképernyő, natív PL/SQL és tényleges eseménybekötés](MIGRACIOS_PONTOSSAG_4_10_HU.md).

A 4.8-as szűrés csak a Headstart/Designer előtagú (`qms$`, `cg…$`) hívásokat ismerte fel. Minden más ismeretlen hívást az adatbázisra bízott, így olyan Forms-oldali kód is `DbCalls.call(...)` blokkba került, amely az adatbázisban nem létezik: például `calendar.event('WHEN-BUTTON-PRESSED');`, `web.show_document(...)` vagy `delete_record;`. Ezek futáskor PLS-00201 hibával (HTTP 501) álltak le. `POST-QUERY`-ben a teljes listázást, `WHEN-NEW-FORM-INSTANCE`-ben pedig az összes végpontot megállították.

A bemenet továbbra is kizárólag az `.fmb`. A döntés a form saját kódján, a Forms nyelv beépített hívásain és egy felülvizsgálható katalóguson alapul; csatolt `.pll` nem kell hozzá.

| Trigger tartalma | Generált backend |
|---|---|
| Csak katalogizált Forms-oldali rutin (`forms_runtime_calls`, alapból `CALENDAR.*`) | Nincs adatbázis-hívás, végpont és teendő. A trigger `framework` státuszú, a naptárblokk nem jelenik meg a képernyőn. |
| Ugyanez SQL-lel vagy saját logikával | A hívás helyén `NULL;` áll, a többi változatlanul fut. Jegyzet: `CALENDAR.EVENT -> NULL (Forms-futtatókörnyezet: CALENDAR.*)`. |
| Forms beépített eljárás vagy csomag (`DELETE_RECORD`, `SET_ITEM_INSTANCE_PROPERTY`, `WEB.SHOW_DOCUMENT`, `TEXT_IO.*` …) | Soha nem kerül adatbázis-blokkba. A trigger indoklással kézi vagy frontendfeladat lesz. |
| Gomb, amely csak ilyen hívásokból áll | Nincs backend végpont (`forms_runtime` kategória), a frontend kezeli. Fájl-, OS-, riport- vagy más triggert indító hívásnál (`HOST`, `RUN_PRODUCT`, `TEXT_IO`, `DO_KEY` …) a kézi végpont megmarad. |
| Indítási trigger csak ablak-, nézet-, navigációs és lekérdező hívással (pl. `set_window_property(forms_mdi_window, window_state, maximize)`) | Nem tiltja a végpontokat (hatókör: `frontend`). Mező-, blokk- vagy rekordtulajdonság, feltétel vagy értékadás esetén a tiltás megmarad. |
| Üres forrású, vagy csomagként hívott, de eljárásként exportált helyi programegység | Indoklással elutasítva; nem kerül hibás deklaráció a névtelen blokkba. |

A szűrés itt sem név szerinti tippelés. A `CALENDAR.*` csak a `CALENDAR` csomag rutinjaira illeszkedik, a `CALENDAR_UTIL.X`-re és a sémanévvel minősített `APP.CALENDAR.EVENT`-re nem. Ha egy argumentum nem hagyható el (`calendar.event(pkg.fn(...))`), vagy a hívás kifejezésben szerepel, a trigger kézi feladat marad. A beépített hívásokat csak hívási pozícióban azonosítja, ezért egy `HELP` nevű oszlop nem okoz elutasítást.

## Katalógus

A `framework-catalog.json` új kulcsa:

```json
"forms_runtime_calls": {
  "CALENDAR.*": "A naptár-segédablak Forms-oldali eseménykezelője: …"
}
```

A minta `RUTIN` vagy `CSOMAG.RUTIN` lehet, `*` és `?` helyettesítővel; a csomagnévben legyen konkrét karakter. Saját katalógusban a kulcs hiánya az alapértéket jelenti, a `{}` kikapcsolja. Csak olyan rutint vegyél fel, amely az adatbázisban nem létezik, és amelyet a webes felület kivált. A `dictionary-sql` ezeket a rutinokat nem kéri le.

Ha futáskor mégis PLS-00201 érkezik, a HTTP 501 üzenet megnevezi a rutint. A Forms-oldali rutin csak akkor hagyható ki a katalógussal, ha a teljes működését bizonyítottan kiváltja a natív vezérlő; saját üzleti kódhoz adapter szükséges. A hiányzó adatbázis-objektumot ne szűrd ki.

## Ellenőrzés (4.9)

```powershell
python -m unittest discover -s tests -p "test_forms_runtime.py" -v
```

A teljes csomag 341 tesztet futtat. A hibák listája azonos a 4.8-as kiinduló állapottal (34 hiba, 46 kivétel, 29 kihagyás); új hiba nincs. A golden-esetek közül csak a `dbcall` változott, egyetlen sorban: a DbCalls PLS-00201 üzenete már nem csatolt könyvtárat feltételez.

---

# Triggerzaj-szűrés és közvetlen DPS SQL – 4.8.0

A korábbi akciógenerátor minden `WHEN-BUTTON-PRESSED` triggerhez készített backend végpontot, akkor is, ha az elemzés már keretrendszeri vagy frontendműveletként azonosította. Ez okozta például a naptár OK/Cancel kezelőihez tartozó felesleges akciókat.

## Mi marad ki, és mi marad meg?

| Trigger tartalma | Generált backend |
|---|---|
| Csak katalógusban szereplő keretrendszerhívás, például `qms$calendar.cancel;` | Nincs akcióvégpont. |
| Kifejezetten üres `NULL;` vagy `BEGIN NULL; END;` | Nincs akcióvégpont vagy üres triggerhook. |
| Teljesen felismert frontendlépések, például `go_block('B'); execute_query;` vagy ablakkezelés | Nincs párhuzamos gombvégpont. A frontend/host adapter kezeli a lépéseket; a lekérdezés a meglévő list/search végpontra kerül. |
| SQL, saját eljáráshívás, saját feltétel, validáció vagy hibajelzés | Megmarad. A támogatott adatbáziskód futtatható PL/SQL blokkot vagy Java-szabályt kap. |
| Naptárhívás és mellette SQL/saját logika | A teljes trigger megmarad; csak a bizonyított keretrendszerrész helyettesíthető. |
| Ismeretlen rutin, nem támogatott Forms-builtin, hiányzó/hibás forrás | Nem minősül zajnak. A korábbi megőrzési/HTTP 501 viselkedés és a konkrét indok megmarad. |

A szűrés **nem az OK/CANCEL/CALENDAR név alapján** történik. Egy `CALENDAR.CANCEL` nevű gomb, amely adatot módosít, ugyanúgy megmarad. A dátummező `WHEN-VALIDATE-ITEM` SQL-je továbbra is a blokk validációs eseményláncába kerül; ebből nem következik külön, azonnali frontend HTTP-hívás. A nem támogatott események forrása és bekötési feladata megmarad.

A `framework_catalog` továbbra is a szervezet által felülvizsgált keretrendszerrutin-lista. Saját üzleti rutint ne sorolj ebbe. Egy keretrendszerhívás argumentumában lévő saját függvény (`qms$event(pkg.mutate(...))`) nem veszhet el: ez kézi átültetést igénylő trigger marad. A sztringekben lévő pontosvesszők és kommentjelölők nem befolyásolják a szűrést.

A kihagyásokat az `analysis/action-plan.json`, az `analysis/backend-plan.json` és a `BACKEND_TASKS.md` tartalmazza, indoklással. Az eredeti triggerforrás és SHA256 megmarad az elemzésben. A saját/ismeretlen triggerrel rendelkező, adatforrás nélküli naptárblokk sem tűnik el automatikusan a képernyőből.

## A DPS ServiceImpl felépítése

- A CL, DPS és WBS rétegben egyaránt 4 Java-fájl készül. Nincs külön `…Data.java`, domain vagy repository.
- A publikus DPS-metódusban fut a művelet a meglévő céges `log1x` kereten belül. Az SQL, a triggerkód és a privát mapping-/ellenőrző segédek ugyanebben az osztályban találhatók.
- Csak a végpontokból elérhető blokkszintű segédek és helyi Forms-programegységek kerülnek a Java-kódba. Az üres eseményhookok kimaradnak; a közös JDBC/típuskezelő segédek az őket használó modulokba kerülnek.
- A támogatott PL/SQL szövege olvasható Java 17 text block. A `DbCalls.call(jdbc, ...)` segéd belül Spring `JdbcTemplate.execute` + `CallableStatement` hívást használ.

Az értékek paraméterkötéssel jutnak az Oracle-be; nem kerülnek szövegesen az SQL-be. A `:BLOKK.MEZŐ` hivatkozások típusos helyi változókká válnak, az eredmények OUT paramétereken kerülnek vissza. A NUMBER, DATE és szöveges értékek, valamint a `MESSAGE` eredményei kezeltek. A helyi Forms-eljárások a névtelen blokk deklarációi közé ágyazódnak, nem kell őket új repositorykba szétbontani vagy adatbázis-eljárásként telepíteni.

Az Oracle `SELECT ... INTO ...`, DML és adatbáziscsomag-hívások megőrzik a PL/SQL működésüket. Egy sima `SELECT ...` eredménylista Java-oldali lekéréséhez a generált CRUD/LOV `jdbc.query`/`queryForList` útvonala szolgál. A Forms-specifikus felülethívások (`GO_ITEM`, `SHOW_WINDOW`, stb.) önmagukban nem futnak Oracle PL/SQL-ként; a generátor nem indít el félig átültetett vegyes triggert.

A végrehajtás Spring-tranzakcióban történik. A közvetlen akcióban lévő támogatott `COMMIT` helyett a sikeres végpont zárja a tranzakciót; a generált írások és triggerhívások `rollbackFor = Exception.class` beállítást kapnak. Az eltérő tranzakciókezelést igénylő forrás kézi feladat marad. A helyi eljárásból hívott ismert rutin név szerinti OUT paraméterei is beleszámítanak az írásellenőrzésbe: így a védett kulcs/adatbázismező nem írható át észrevétlenül.

A meglévő `Access.check`, `MODULE_REVIEWED`, műveleti tiltások, kulcs- és adatmódosítási ellenőrzések megmaradtak. A `backend_live` beállítás viselkedése nem változott. Az ismeretlen adatbázis-/PLL-rutin feloldhatósága továbbra is a célkörnyezettől függ.

## Használat és átállás

1. Frissítsd a migrátor forrását a ZIP tartalmával, majd indítsd újra a Python szervert. Új Python-függőség nem kell; az Angular feltöltő felület változatlanul használható.
2. Generáld újra ugyanazt az FMB/XML modult **új kimeneti mappába**. A korábbi `module-compact-v1` / `…Data.java` felépítésre nem engedjük rá az új `--regenerate` műveletet, mert az megőrzött ServiceImpl-lel hibás vegyes kimenetet eredményezhetne.
3. A célalkalmazásban az új modul fájljaival váltsd le a korábbi generált fájlokat; az elavult `…Data.java` ne maradjon mellettük. A saját módosításokat emeld át.
4. A további, 4.8-as kimenetre futó `--regenerate` megőrzi a ServiceImpl-et, benne a kézzel módosított SQL-t is. Az új teljes javaslat és diff az `analysis/backend-regeneration/` alatt található.

## Ellenőrzés

Az önálló regressziós tesztekhez nem szükséges az eredeti példafájl-könyvtár:

```powershell
python -m unittest discover -s tests -p "test_trigger_noise.py" -v
python -m unittest discover -s tests -p "test_compact_backend.py" -v
python -m unittest discover -s tests -p "test_headstart_cleanup.py" -v
python -m unittest discover -s tests -p "test_plsql_and_states.py" -v
python -m unittest discover -s tests -p "test_golden.py" -v
```

A Java-tesztek Java 17 fordítóval futnak. A JDBC-próba ellenőrzi a ténylegesen elküldött teljes SQL szövegét, a NUMBER/DATE/NULL IN bindeket, az OUT regisztrációkat, a visszaadott mezőket és üzeneteket, valamint azt, hogy a hibás számérték végrehajtás előtt elutasításra kerül. A tesztben Spring/JDBC/céges API tesztmásolatok szerepelnek; ez nem élő Oracle-integrációs próba.

A kapott projektből az `examples/` könyvtár és több régi teszt által igényelt állomány hiányzik. A teljes tesztcsomag emiatt és korábbi elavult elvárások miatt már a módosítás előtt sem volt zöld. A konkrét kiinduló és módosítás utáni eredményeket a `VALIDACIO_4_8.json` rögzíti. Éles Oracle-adatbázis és a tényleges céges hostkörnyezet ebben az ellenőrzésben nem állt rendelkezésre.

Hivatalos háttér: [Oracle JDBC: PL/SQL és névtelen blokkok](https://docs.oracle.com/en/database/oracle/oracle-database/26/jjdbc/JDBC-getting-started.html), [Spring CallableStatementCallback](https://docs.spring.io/spring-framework/docs/current/javadoc-api/org/springframework/jdbc/core/CallableStatementCallback.html).
