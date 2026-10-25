# 4.13 – működő alkalmazás a felmérés alapján

A 4.12.2-es felmérés (`FELMERES_HU.md`) szerint a generált végpontok 99%-a tiltott maradt. Egy-egy
végpontot több ok is tiltott, ezért egyetlen ok javítása sem nyitott meg végpontot („egyedüli ok” = 0).
A 4.13 a felmérés okait sorban megszünteti, és hozzáteszi azt, ami a működő alkalmazáshoz hiányzott:
a Forms-hívások futtatását a gombokban és az indítási kódban, a mentési láncot és a fejlesztői munkapadot.

A felmérés mintáit utánzó szintetikus formon (`tests/fixtures/felmeres_replika_fmb.xml`):

| | 4.12.2 | 4.13 |
|---|---:|---:|
| Engedélyezett végpont (LOV nélkül) | 0 / 18 (0%) | 19 / 20 (95%) |
| Gomb- és indítási végpont | 0 / 6 | 6 / 7 (5 gomb + az indítási végpont) |
| Mentés a képernyőről | a fejlesztő köti be | **Mentés** gomb, egy tranzakcióban |

Az egyetlen tiltott végpont egy `DO_KEY('COMMIT_FORM')` gomb, mert a form saját KEY-COMMIT logikáját át kell ültetni.

A saját formjaidon így mérheted újra:

```bash
python -m frm_forms survey formok/ --out felmeres-4.13
```

## 1. Ami már nem tilt végpontot

| Felmérési ok | Most |
|---|---|
| PRE-FORM / WHEN-NEW-FORM-INSTANCE („all”: a modul minden végpontja) | Képernyő-előkészítés: nem tilt. A kódjuk az **indítási végpontban** fut (2. pont). A `SET_BLOCK_PROPERTY`-t továbbra is külön ellenőrizzük. Ha az indítási kód leállíthatja a formot (`EXIT_FORM`, `FORM_TRIGGER_FAILURE`), `STARTUP_ACCESS` ellenőrzési tétel készül. A feltétel nélküli `SET_ITEM_PROPERTY(…, UPDATE_ALLOWED/INSERT_ALLOWED, PROPERTY_FALSE)` a backendben is csak olvashatóvá teszi a mezőt. |
| KEY-EXEQRY / KEY-CREREC / KEY-COMMIT … „felülírja az alapműveletet” | A billentyű-trigger a Forms-billentyűn fut, a webes képernyő saját vezérlője hívja a végpontot. Ha a trigger nem végzi el az alapműveletet (pl. a KEY-DELREC csak üzenetet ír), `KEY_DISABLES_OPERATION` keletkezik, és a képernyő nem kínálja fel a műveletet. Saját vagy könyvtári rutin hívásakor `KEY_TRIGGER_WRAPPER` ellenőrzési tétel készül. |
| „Várt token” / „Nem támogatott token '%'” az ON-INSERT, ON-UPDATE, ON-CHECK-DELETE-MASTER … triggerekben | Az **ON-INSERT, ON-UPDATE, ON-DELETE** az eredeti PL/SQL-lel fut a generált DML helyett. Az **ON-CHECK-DELETE-MASTER** a törlés előtt fut, a **POST-CHANGE** a validációs láncban, valamint lekérdezett soronként is, ha nem ír lekérdezett mezőt. Az ON-INSERT, a PRE-INSERT-hez hasonlóan, kulcsot írhat (szekvenciából). |
| QUERY_FILTER, ORDER_BY, RELATION_QUERY | Ha a szigorú SQL-fordító nem érti a feltételt, a **Forms WHERE / ORDER BY eredeti szövege fut az Oracle-ben**, ahogy a Forms is a lekérdezéséhez fűzi: allekérdezés, nem leképezett oszlop, SYSDATE, `WHERE (…)` és `ORDER BY …` előtag is. A `:BLOKK.MEZŐ`, `:GLOBAL.X` és `:PARAMETER.X` a keresés típusos paramétere lesz, a szöveg a formból jön, a kérésből soha. A blokk `Alias` tulajdonsága a FROM-ba kerül. |
| NO_PRIMARY_KEY | A Formshoz hasonlóan a **ROWID** azonosítja a rekordot: `ROWIDTOCHAR(ROWID)` a DTO-ban, `ROWID = CHARTOROWID(:rowid)` a módosításnál és a törlésnél, `RETURNING ROWID` a beszúrásnál. Nem találgatunk ID-oszlopot. Valódi kulcshoz továbbra is a `schema.json` `primary_key` beállítása szolgál. |
| MASTER_DETAIL (írás) | A reláció saját triggerei (ON-CHECK-DELETE-MASTER, kaszkádoló PRE-DELETE) a törléssel futnak. Az új részletrekord kulcsát a mentési lánc adja (3. pont). Élő módban (`backend_live`) ellenőrzési tétel. |
| DYNAMIC_LOV, CopyValueFromItem, HighestAllowedValue `N,N` | A LOV-végpont generált, élő módban ellenőrzési tétel. A Headstart keretrendszeri LOV-ja (`QMS$…`) nem feladat. A CopyValueFromItem a mentési láncban érvényesül. A tizedesvesszős határérték (`9999999999,99`) BigDecimal-ellenőrzés lesz. A CaseRestriction nagy- vagy kisbetűsít mentés előtt. |
| Helyi csomag hívása (`csomag.eljaras`) | A form saját csomagja (Package Spec + Body) a névtelen blokkba ágyazódik: a változói és alprogramjai helyi deklarációk, a `CSOMAG.TAG` hívásokból `TAG`. A csomagváltozók kérésenként újraindulnak. Inicializáló résszel (`BEGIN` a törzs végén) rendelkező csomag kézi feladat marad. |

## 2. Forms-futtatókörnyezet a gombokban és az indítási kódban

A gomb és az indítási kód PL/SQL-je továbbra is változatlanul fut az Oracle-ben. Ami Forms-hívás, az nem
utasítja el a triggert: a blokk elején generált helyi eljárások **felületi utasításként** rögzítik,
a képernyő pedig a válasz után sorban végrehajtja (`frm_forms/forms_emulation.py`, `screen_emulation.py`).

| Forms | A webes megfelelő |
|---|---|
| `GO_BLOCK`, `GO_ITEM`, `SET_ITEM_PROPERTY`, `SHOW_/HIDE_WINDOW`, `SHOW_/HIDE_VIEW`, `WEB.SHOW_DOCUMENT` … | utasítás a képernyőnek (`ActionResult.commands`) |
| `EXECUTE_QUERY`, `COMMIT_FORM`, `CLEAR_BLOCK`, `CREATE_RECORD`, `DELETE_RECORD`, `CALL_FORM` … | utasítás, **csak a kód utolsó lépéseként**: ha utána még mezőt olvas vagy SQL-t futtat, kézi feladat marad (a sorrend eltérne) |
| `CREATE_PARAMETER_LIST` / `ADD_PARAMETER` + `CALL_FORM` | navigáció a cél form útvonalára (`form_routes`), a paraméterek query paraméterként |
| `NAME_IN('B.I')`, `COPY(x, 'B.I')`, `DEFAULT_VALUE(x, 'GLOBAL.G')` | a kötött változók (írásnál vissza a képernyőre) |
| `:GLOBAL.X := …` | visszakerül a képernyőre; a böngészőfülön a formok között közös, minden kéréssel megy |
| `:SYSTEM.CURSOR_BLOCK`, `FORM_STATUS`, `MESSAGE_LEVEL` … | a képernyő a kéréssel küldi; a `TRIGGER_BLOCK`, `TRIGGER_ITEM` és `CURRENT_FORM` értéke rögzített |
| `FIND_ALERT` / `SET_ALERT_PROPERTY` / `SHOW_ALERT` | párbeszédablak. A kérés munkáját mentési pont görgeti vissza, a választott gombbal a kód **elölről** fut újra |
| `VALIDATE`, `SYNCHRONIZE`, `SET_APPLICATION_PROPERTY`, `BELL` | nincs teendő (a képernyő a kérés előtt validál) |
| `FORMS_DDL('ALTER SESSION …')` | kimarad: a munkamenet-beállítás az adatforrás dolga (például Hikari `connection-init-sql`) |
| `CREATE_GROUP`, `ADD_GROUP_COLUMN`, `ADD_GROUP_ROW`, `SET_GROUP_*_CELL` | a képernyő felépíti a rekordcsoportot (`recordGroups`) |
| `DO_KEY('X')` | csak akkor, ha a formban nincs saját KEY-trigger az X-re. Ha van, annak logikáját át kell ültetni |

**Indítási végpont:** a form PRE-FORM és WHEN-NEW-FORM-INSTANCE triggere egy `onFormInit` végpont lesz
(`/init`), a képernyő a konstruktorában hívja. Így fut le például a paramétertábla lekérdezése, a `:GLOBAL`
értékek beállítása, a mezőállapotok, a kezdő lekérdezés és a futásidőben épített rekordcsoport.

**Felismert lépéssoros gombok** (`go_block` + `create_record`, `commit_form` …): a képernyő hajtja végre
őket, backendhívás nélkül.

A `CommonMigrateTools` **VERSION 3**-ra nőtt (`PlsqlValues.commands`, `PlsqlValues.global`). A CL-projektben
ezt az egy fájlt kell cserélni. Az `ActionResult` új mezői a `commands` és a `globals`; a régi kétparaméteres
konstruktora megmaradt.

## 3. Mentési lánc: `commitForm`

Egy végpont (`POST …/commit`) kapja a képernyő összes változását, és **egy tranzakcióban, Forms-sorrendben**
menti őket. A sorrend: PRE-COMMIT, majd blokksorrendben blokkonként előbb a törölt, aztán az új és a
módosított rekordok, végül POST-FORMS-COMMIT. Minden rekordot a meglévő egyrekordos művelet ment, a saját
triggereivel (validálás, PRE-/ON-/POST-, ON-CHECK-DELETE-MASTER). Hiba esetén semmi sem mentődik.

- Az új részletrekord kulcsa a most mentett masterből jön (szekvenciával kiosztott kulcs is), különben a master
  képernyőértékéből (reláció, Copy Value from Item).
- A PRE-COMMIT és a POST-FORMS-COMMIT az eredeti PL/SQL-lel fut a képernyő értékeivel, ezért nem tiltják az
  írást. Az alertet tartalmazó változatuk kézi feladat marad.
- A képernyő **Új rekord / Törlés / Mentés** eszköztárat kap. A lekérdezett rekordot az eredeti DTO-jával
  (rejtett mezők, ROWID) együtt tartja meg, a mentés ebből dönti el, hogy módosítás vagy beszúrás kell.
  A Törlés csak jelöl, a Mentés véglegesíti. Ha egy billentyű-trigger kikapcsolja a műveletet
  (KEY_DISABLES_OPERATION), a gomb nem jelenik meg.
- Közelítés: blokkonként egy aktuális rekord. A táblázatos blokkok soraihoz saját szerkesztő kell; az egyrekordos
  végpontok ehhez megvannak.

## 4. Riport: végpontok, amelyeket a képernyő nem hív

A `MIGRATION_NOTES.md` „Végpontok, amelyeket a képernyő nem hív” táblázata minden ilyen végpontot az okával
együtt sorol fel. Ilyen például az egyrekordos create/update/delete, amelyet a mentési lánc használ, vagy a
képernyőn kívüli feltételű keresés. A `:GLOBAL` / `:PARAMETER` keresési feltételt a képernyő mostantól a
saját kontextusából tölti ki.

## 5. Munkapad és Forms-szemantika

Minden kimenetben van `analysis/MUNKAPAD.html` (és `workbench.json`): egy kártya jut minden megmaradt kézi
feladatra és ellenőrzési döntésre. A kártyán szerepel:

- az eredeti kód;
- a generátor indoklása;
- a javasolt hely (képernyő, gombvégpont, adatvégpont, csak ellenőrzés);
- egy induló váz;
- egy állapotjelző (teendő, folyamatban, kész; a böngészőben tárolva).

A **Jira CSV** gomb a szűrt kártyákat exportálja. A lap alján egy rövid Forms-szemantika puska van, bővebben:
[FORMS_SZEMANTIKA_HU.md](FORMS_SZEMANTIKA_HU.md).

## Egyéb javítások

- A lekérdezésgomb hosszú WHERE-variánsa (`"…" + "…".equals(whereText)`) a sortördelés miatt nem fordult. Most
  `whereText.equals("…")` alakú.
- Céges (AWU) mód: a ROWID mező és az új DTO-k (`…CommitRequestDto`, `…BlockChangesDto`, `…CommitResultDto`)
  getter/setterrel készülnek.
- Tesztek Windows alatt: a javac argumentumfájl perjeles útvonalakat kap.

## Átállás (újragenerálás)

1. A `CommonMigrateTools.java` cseréje (VERSION 3).
2. `--regenerate`: a CL (DTO-k, Constants, RestClient) és a DPS/WBS interfészek frissülnek. A CREATE_ONCE
   `ServiceImpl`, `ControllerImpl` és a képernyőkomponens megmarad; az új változatuk az
   `analysis/backend-regeneration/` mappában van, ezt kell összefésülni. Új modulnál nincs teendő.
3. Ha a régi viselkedést szeretnéd: a startup/key triggerek tiltó hatása nem kapcsolható vissza. A
   `schema.json` `writable: false` beállítása továbbra is tiltja az írást, a `primary_key` pedig felülírja a
   ROWID-et.
