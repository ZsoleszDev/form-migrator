# 4.14 – kevesebb kód, kevesebb kézi munka, ellenőrzés az adatbázisban

A 4.13 után kézi munka maradt ott, ahol a gomb csatolt könyvtár (`.pll`) rutinját hívta, ahol a
`DO_KEY` a form saját KEY-triggerét futtatta, vagy ahol a `COMMIT_FORM` a kód közepén állt. Ezek a
végpontok kommentben vagy 501-gyel készültek. A generált modul ráadásul minden formban megismételte
ugyanazt a PL/SQL-prológust és ugyanazt a több száz soros képernyőkódot. A 4.14 ezeket szünteti meg,
és ad egy parancsot, amely a generált SQL-t és PL/SQL-t a céladatbázisban fordítja le, mielőtt bárki
megnyomná a gombot.

A felmérés mintáit utánzó formon (`tests/fixtures/felmeres_replika_fmb.xml`, `survey`):

| | 4.13 | 4.14 |
|---|---:|---:|
| Engedélyezett végpont (LOV nélkül) | 19 / 20 (95%) | **20 / 20 (100%)** |
| DPS `ServiceImpl` | 1674 sor | 1544 sor, eggyel több működő végponttal |
| Angular komponens | 1402 sor | 946 sor, plusz a projektenként **egyszer** szükséges 564 soros `frm-forms-screen.ts` |

Az utolsó tiltott végpont a `DO_KEY('COMMIT_FORM')` gomb volt: a 4.14 lefuttatja a form KEY-COMMIT
triggerét, és a `COMMIT_FORM` után folytatja a gomb kódját.

## 1. Csatolt PL/SQL-könyvtárak (`--pld`)

A form csatolt könyvtárainak rutinjai eddig csak annyit kaptak: „futáskor az adatbázis oldja fel”. A
könyvtár azonban kliensoldali kód, az adatbázisban nincs meg, ezért ezek a hívások futáskor
`PLS-00201` hibát adtak volna. Mostantól a könyvtár beolvasható, és a rutinjai a form saját
programegységeihez hasonlóan a névtelen blokkba ágyazódnak.

```bash
python -m frm_forms migrate FORM_fmb.xml --out kimenet --screen --pld ANKLIB.pld --pld KOZOS.pld
python -m frm_forms batch formok/ --out batch --pld konyvtarak/ANKLIB.pld
python -m frm_forms survey formok/ --out felmeres --pld konyvtarak/ANKLIB.pld
```

- **Szöveges `.pld`:** közvetlenül olvasható. Alapból UTF-8, ha az nem sikerül, cp1250 (a Windowsos Forms
  kódlapja). Más kódlap: `"pld_encoding": "cp1252"` a `config.json`-ban.
- **Bináris `.pll`:** a Forms Compiler alakítja át (`frmcmp_batch module=… module_type=LIBRARY script=YES`).
  Ha az `frmcmp` nincs a PATH-on, a `library_export_command` argumentumlista adja meg, `{input}` (és
  `{output_dir}`) helyőrzővel, shell nélkül.
- **Webes felület:** a form mellé `.pld` / `.pll` fájlok is húzhatók.
- **Csak az kerül be, amit a form elér:** a form kódjából hívott rutinok és az általuk hívottak. A form
  saját, azonos nevű egysége elsőbbséget kap, utána a csatolási sorrendben első könyvtár. A könyvtárban
  lévő `.attach` (beágyazott csatolás) is követhető. A keretrendszer-katalógus rutinjai (például
  `qms$…`) nem ágyazódnak be.
- **Biztonságos:** ha egy könyvtár nem bontható (ismeretlen szerkezet), egyetlen rutinja sem kerül be, a
  hívások pedig a régi módon, megjelölve maradnak. Félig beolvasott könyvtár nincs.
- **Nyomon követhető:** az `analysis/libraries.json` sorolja fel, melyik rutin melyik könyvtárból jött.
  A `PlsqlUnits` komment `csatolt könyvtár: ANKLIB` jelölést kap. Az `ATTACHED_LIBRARY` tétel már csak a
  be nem töltött könyvtárakat nevezi meg.

## 2. `DO_KEY` a form saját KEY-triggerével

Eddig a `DO_KEY('X')` csak akkor működött, ha a formban nem volt saját KEY-X trigger. Most a
trigger kódja **beágyazott blokként** fut a `DO_KEY` helyén, a saját blokk-környezetében. A
`:BLOKK.MEZŐ` hivatkozások, a Forms-hívások és a helyi rutinok ugyanúgy fordulnak, mint a gombban.

- **Hatókör:** a Forms szabálya szerint először a gombmező saját triggere, aztán a gomb blokkjáé, végül a
  form szintű trigger.
- **Navigáció után:** ha a kód a `DO_KEY` előtt `GO_ITEM` / `GO_BLOCK` hívással (vagy olyan helyi
  rutinnal, amely navigálhat) elmozdította a kurzort, akkor generáláskor nem tudható, melyik mező vagy
  blokk triggere futna. Ilyenkor csak a form szintű trigger jöhet szóba. Ha blokk vagy mező szintű is
  van, kézi feladat lesz, megnevezett okkal.
- **Kézi feladat marad:**
  - az `Execution Hierarchy = BEFORE/AFTER` (ilyenkor a szülőszintű trigger is fut);
  - a végtelen `DO_KEY` lánc;
  - a gombból értelmetlen `DO_KEY('LIST_VALUES')` és `DO_KEY('ENTER_QUERY')`.

## 3. `COMMIT_FORM` a gomb kódjának közepén: mentési pont

Eddig a `COMMIT_FORM` csak a kód utolsó lépéseként működött. Ha utána még volt kód (például naplózás
vagy egy új lekérdezés), a gomb kézi feladat lett. Most ilyenkor **mentési pont** keletkezik:

1. A gomb kérése a `COMMIT_FORM`-ig fut, és a mezők akkori értékeit egy `FRM_COMMIT` utasítással adja vissza.
2. A képernyő ment: a `commitForm` végpont **ugyanabban a tranzakcióban** újrafuttatja a gomb kódját a
   pontig, ellenőrzi, hogy ugyanoda jutott-e (ha az adatok közben változtak, HTTP 409 a válasz), és
   csak ezután menti a blokkokat. A gomb pont előtti adatbázis-módosításai így a mentéssel együtt
   véglegesednek, vagy vele együtt vesznek el, ahogy a Formsban.
3. A képernyő a gombot még egyszer meghívja `FRM.RESUME = pont` paraméterrel. Ebben a futásban minden
   pont előtti utasítás kimarad, a ponthoz vezető ágak feltétele nem értékelődik újra, a kód pontosan a
   `COMMIT_FORM` után folytatódik, a már mentett értékekkel.

```
IF ank_jog.irhat THEN                  IF (frm_resume IN (1) OR (frm_resume NOT IN (1) AND (ank_jog.irhat))) THEN
    COMMIT_FORM;               ->          frm_commit_form(1);
    ank_naplo.mentes(:B.ID);               ank_naplo.mentes(nv_…);
END IF;                                END IF;
```

**Kézi feladat marad**, megnevezett okkal:

- ha a pont ciklusban, CASE utasításban vagy kivételkezelőben áll;
- ha a kód GOTO-t tartalmaz;
- ha egy helyi változót a pont előtt állít és utána olvas, mert a második kérés elölről indítja a blokkot;
- ha egy helyi csomagnak változói vannak.

A gomb ServiceImpl-metódusa ilyenkor két részre válik: a publikus végpontra és egy privát
`run…` metódusra. Ezt a privát metódust a `commitForm` is hívja (`CommitRequest.action`,
`actionBlocks`, `actionParameters`).

## 4. Karcsúbb generált kód, azonos működéssel

- **`CommonMigrateTools.FormsPlsql`:** a Forms-emuláció állandó PL/SQL-segédeljárásai (`frm_msg`,
  `frm_cmd`, `frm_find` …) eddig minden gomb blokkjában szó szerint megismétlődtek. Most egyszer
  szerepelnek, a CL-ben. A ServiceImpl csak összefűzi, amit a blokk használ:
  `FormsPlsql.MSG + FormsPlsql.CMD + "…"`. Az adatbázis ugyanazt a teljes blokkot kapja.
- **Üres utasítások nélkül:** kimaradnak az emuláció után üresen maradt elemek:
  - a `NULL;` utasítások;
  - a `BEGIN NULL; END;` blokkok;
  - az `IF NOT TRUE THEN … END IF` (a `FORM_SUCCESS` itt mindig igaz);
  - a már nem használt `FORM_TRIGGER_FAILURE` deklaráció.

  Ami számíthat, az megmarad: a címke, a deklaráció, a kivételkezelő és az ELSE ág. Amit a generátor
  nem tud biztosan szerkezetre bontani, azt változatlanul hagyja.
- **Egysoros blokkőr:** a művelettiltás egy `Set.of("read", …).contains(operation)` ellenőrzés lett a
  korábbi `switch` helyett.
- **Csak a szükséges kérésmezők:** a gombmetódus csak akkor deklarálja a `values` / `parameters`
  változót, ha a blokk valóban köt mezőt, illetve `:GLOBAL` / `:SYSTEM` értéket.

A `CommonMigrateTools` **VERSION 4**-re nőtt (`FormsPlsql`, `PlsqlValues.prelude`).

## 5. Közös képernyő-futtató: `FrmFormsScreen`

A Forms-emulációs képernyők (gombok, mentési lánc, alertek, `:GLOBAL` / `:SYSTEM`) eddig mindegyike
tartalmazta a teljes futtatókódot (`runAction`, `formsCommit`, `runCommands` …). Most ez egyszer, a
`frontend/frm-forms-screen.ts` fájlban van. A komponens `extends FrmFormsScreen`, és csak a saját
adatait tartja meg (`protected override readonly …`: blokkok, végpontok, alertek, útvonalak), valamint a
saját elrendezéséhez tartozó horgokat (`executeQuery`, `showRows`, `setItemState`, `formsWindow` …).

- **Telepítés:** a `frm-forms-screen.ts` a modulmappák mellé kerül, mert a komponens
  `'../frm-forms-screen'`-ből importál. Minden formhoz ugyanaz a fájl tartozik, a
  `FRM_FORMS_SCREEN_VERSION` mutatja a változatát. Újabb verzió esetén cserélni kell.
- **TypeScript:** az `override` módosítóhoz TypeScript 4.3 vagy újabb kell. A kód `strict` és
  `noImplicitOverride` beállítással is fordul.
- **Változatlan marad:** az emuláció nélküli egyszerű képernyő, amely nem kapja meg a futtatót.

## 6. `verify-db`: a generált SQL és PL/SQL lefordítása a céladatbázisban

Egy hibás oszlopnév, egy nem létező rutin (`PLS-00201`) vagy egy hiányzó jogosultság eddig csak akkor
derült ki, amikor valaki megnyomta a gombot. A `migrate` mostantól elkészíti az
`analysis/db-statements.json` fájlt. Ebben van minden szöveg, amelyet a modul az Oracle-nek küld:

- a gombok, triggerek és a `commitForm` PL/SQL-je;
- a LOV-lekérdezések;
- a blokkok lekérdező és DML-utasításai, de csak azok, amelyeket a ServiceImpl ténylegesen futtat
  (az ON-INSERT például kiváltja a generált INSERT-et).

A `verify-db` ezeket a `DBMS_SQL.PARSE` eljárással lefordítja, **végrehajtás nélkül**:

```bash
pip install oracledb                        # thin mód: nem kell Oracle kliens
export FRM_DB_PASSWORD='…'                 # a jelszó csak környezeti változóból jön
python -m frm_forms verify-db kimenet --dsn dbhost:1521/ORCL --user APP
python -m frm_forms verify-db batch/ --dsn dbhost:1521/ORCL --user APP --report DB_VERIFY.md
```

- **Kimenet:**
  - modulonként `analysis/db-verify.json`;
  - összesítő `DB_VERIFY_HU.md` (alapból az első megadott mappában), benne a hibaüzenet és a generált
    szöveg azon sora, amelyre az `ORA-06550` mutat.
- **Kilépési kód:** hiba esetén 3, így CI-ben is használható.
- **Biztonság:**
  - Csak SELECT / WITH, DML (INSERT, UPDATE, DELETE, MERGE) és névtelen blokk kerül az adatbázisba. A
    DDL-t a `DBMS_SQL.PARSE` azonnal végrehajtaná, ezért azt már a küldés előtt elutasítjuk.
  - A munkamenet visszagörgetéssel zárul, semmi sem véglegesedik.
  - A pozicionális `?` kötéseket a parancs `:b1, :b2 …` névre cseréli.
- **Tipikus találatok:**
  - `PLS-00201`: a rutin nincs az adatbázisban, mert csatolt könyvtárban van (`--pld`), vagy hiányzik a
    jogosultság;
  - `ORA-00904`: hibás oszlopnév;
  - `ORA-00942`: a tábla nem létezik, vagy nincs rá jog.

## 7. Felmérés: eltérések a Forms-működéstől

A felmérés (`survey`) eddig azt mutatta meg, mi tiltja a végpontokat. Most egy új szakasz azt is
megszámolja, **mi működik, de nem pontosan úgy, mint a Formsban**. Így a portfólió alapján dönthető el, melyik
közelítés megszüntetése a legfontosabb. Ugyanúgy névtelenített és megosztható, mint a riport többi része:

- többsoros, írható adatbázis-blokkok (részletblokk-jelöléssel);
- mentéskor futó szerveroldali mező- és rekordvalidáció;
- az eszköztáron nem futó, saját logikájú KEY-COMMIT / KEY-EXEQRY / KEY-CREREC / KEY-DELREC és az egyéb
  billentyű-triggerek;
- a kód közepén álló képernyőlépések (EXECUTE_QUERY, CLEAR_BLOCK …), lépésenként;
- ON-ERROR / ON-MESSAGE;
- a többsoros blokkon soronként futó POST-QUERY, külön jelölve az egyszerű kikeresést (`SELECT … INTO`),
  amely a lekérdezésbe olvasztható.

A `felmeres.json` `approximations` mezőt kap (`survey_version`: 2), a webes felület külön panelen
mutatja. Részletek: [FELMERES_HU.md](FELMERES_HU.md).

## Ellenőrzés

- Teljes tesztkészlet: 529 teszt, az új tesztek mind zöldek. A már a 4.13-as kiinduló állapotban is hibás
  101 teszt nem változott: ezek a repóból hiányzó mintabemeneteket (`examples/` és a minta-`.fmb`
  exportok) keresik. Új hibás teszt nincs.
- Új tesztek:
  - `test_libraries`;
  - `test_commit_points` (Node-os képernyő-szimulációval: gomb → mentés → folytatás);
  - `test_slim_code`;
  - `test_screen_runtime`;
  - `test_db_verify` (álkapcsolattal; valódi adatbázis nem kell).
  - `test_survey` bővítése (minden eltéréstípus egy replika-változaton, névtelenítés-ellenőrzéssel).
- A generált képernyők és a futtató szigorú TypeScript-ellenőrzése csonkokkal (`tests/ts_stubs`):

  ```bash
  FRM_TSC=/út/a/tsc-hez PYTHONPATH=.:tests python -m unittest test_screen_runtime
  ```

  `tsc` nélkül a teszt kimarad.

## Átállás (újragenerálás)

1. Cseréld a `CommonMigrateTools.java` fájlt (VERSION 4).
2. Másold a `frontend/frm-forms-screen.ts` fájlt a host alkalmazásba, a modulmappák mellé (egyszer,
   minden formhoz közös).
3. Futtasd a `--regenerate` parancsot. A CREATE_ONCE `ServiceImpl`, `ControllerImpl` és a
   képernyőkomponens megmarad. Az új változatuk az `analysis/backend-regeneration/` mappában van, ezt
   kell összefésülni. A komponensből sok kód kikerül (az `extends FrmFormsScreen` veszi át), ezért
   ennél a fájlnál egyszerűbb az új változatot átvenni, és a saját módosításokat visszavezetni.
4. Ha a form csatolt könyvtárat használ: add meg a `--pld` kapcsolót (vagy webes felületen töltsd fel a
   könyvtárat). A korábban „az adatbázis oldja fel” jelzésű hívások így beágyazódnak.
5. Ajánlott: `verify-db` a fejlesztői vagy teszt adatbázison, még mielőtt a modul a tesztelőkhöz kerül.
