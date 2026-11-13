# Java asszisztens — 4.35

## A cél

A DPS ServiceImpl minden metódusát a Java asszisztens (helyi AI) írja meg, úgy, hogy ugyanúgy működjön, mint a
Forms: ugyanaz az SQL, ugyanaz a mentés, csak Java-ban, a Forms-maradványok nélkül. 4.35-től
(`backend_trigger_mode: "ai"`, ez az alapértelmezés) a generátor a metódus keretét adja. Egy trigger keretében a
kikommentezett eredeti Forms-kód áll, egy adatvégpont vagy LOV keretében az `AI-feladat` megjegyzés. Mellettük a
migrátor determinisztikus tippjei vannak: az SQL natív SQL-ként, a paraméterek forrása, a sor → mező
megfeleltetés, a hívandó triggerek sorrendje és a visszatérési érték. A Java-alkalmazás csak adatbázismunkát
kap: a képernyő munkája (indítás, mezőértékek, navigáció) a képernyőé.

Az AI mindent a service-ben ír meg, a service `entityManager` mezőjén keresztül futtatott natív SQL-lel.
Repositoryt nem ír, és a repositoryra nem is kell gondolnia: a táblák JPA entityjét (Domain) és **üres**
repositoryját a migrátor készíti, ide a fejlesztő áthelyezheti a lekérdezéseket, ha úgy látja jónak.

## Mit generál a migrátor a triggerekhez?

| Forms-trigger | Generált metódus | Ki hívja |
| --- | --- | --- |
| `WHEN-BUTTON-PRESSED`, ha adatbázismunkát végez | `public ActionResult on<Blokk><Gomb>(UserDto, ActionRequest)` | a gomb (`applyActionResult` dolgozza fel a választ) |
| lekérdező gomb (`SET_BLOCK_PROPERTY … DEFAULT_WHERE` + `EXECUTE_QUERY`) | `public PageResult<XRow> on…(UserDto, QueryActionRequest)` | a gomb; a képernyő a visszaadott sorokat mutatja |
| adatbázismunka nélküli gomb (mezőérték, ellenőrzés, lekérdezés, mentés, navigáció) | nincs Java-metódus | a képernyő maga (FRONTEND_SCREEN_HU.md) |
| `PRE-FORM`, `WHEN-NEW-FORM-INSTANCE` | nincs Java-metódus | a képernyő kezdőállapota |
| `POST-QUERY`, `PRE/POST/ON-INSERT|UPDATE|DELETE`, `WHEN-VALIDATE-ITEM/RECORD`, `POST-CHANGE`, `ON-CHECK-DELETE-MASTER` | `private void trigger<Esemény><Mező><Blokk>(XRow row, RuleContext context)` | a CRUD-folyamat a Forms-ponton (validálás, beszúrás előtt, lekérdezés után …) |
| `PRE-COMMIT`, `POST-FORMS-COMMIT` | privát hook a `commitForm`-ban | a mentési lánc |

Az indítókódból (`PRE-FORM`, `WHEN-NEW-FORM-INSTANCE`) nem lesz Java: a webes képernyő megnyitása nem esemény a
Java-alkalmazásban. Az állandó értékadás és a konstans mezőtulajdonság a képernyő kezdőállapota, a
`GO_BLOCK` + `EXECUTE_QUERY` a képernyő indításkori lekérdezése, a Forms-keretrendszer (`qms$…`), a kurzor-, ablak-
és munkamenet-beállítás kimarad. A memóriabeli értéklista, a csomagváltozó és az indításkori adatbázismunka (SQL,
adatbázis-rutin) a képernyő MIGRATION_NOTES.md-jébe kerül („Nem migrált adatbázismunka”, a kóddal): ha az
alkalmazásnak kell (például egy adatbázisból vett kezdőérték), a fejlesztő köti be a képernyő saját lekérdezéseként.
A céges, csak Forms-oldali könyvtárrutinok a keretrendszer-katalógus `forms_runtime_calls` listáján vannak
(`ank_ctrl.*`, `ank_menu.*`); egy újabb ilyen könyvtár ugyanide vehető fel, és onnantól sehol sem kerül a backendbe.

Gombból csak akkor lesz Java-végpont, ha a kódja (a hívott helyi eljárásokkal és a `DO_KEY` által indított
key-triggerrel együtt) adatbázist ér: SQL, adatbázis-rutin vagy -csomagelem, a blokk SQL-jének módosítása, szerverre
nyúló Forms-hívás (`frm_forms/database_work.py`; kétség esetén a szerveré marad). A többi a képernyőé.

A blokkok adatvégpontjai (lista, keresés, új rekord, módosítás, törlés) és a LOV-ok is AI-feladatok:

| Forms | Generált metódus | A megjegyzésben |
| --- | --- | --- |
| a blokk lekérdezése (`EXECUTE_QUERY`) | `public PageResult<XRow> list<Blokk>(UserDto, int offset, int limit)`, feltételnél `search<Blokk>(…, SearchRequest)` | a blokk SQL-je (`DEFAULT_WHERE`, `ORDER BY`, lapozás), a feltételek forrása, oszlop → mező, a POST-QUERY hookok |
| új rekord | `create<Blokk>(UserDto, XRow row)` | kis-/nagybetűsítés (CaseRestriction), mezőellenőrzés (kötelező, hossz, szám), szekvencia, a hookok Forms-sorrendben, az `INSERT` (vagy az ON-INSERT hook). Az alapértéket a képernyő adja, a mentés nem írja felül |
| módosítás | `update<Blokk>(UserDto, XRow original, XRow value)` | a kulcs nem változhat, zárolás (`SELECT … FOR UPDATE NOWAIT`, eltérésnél 409), ellenőrzés, hookok, az `UPDATE` |
| törlés | `delete<Blokk>(UserDto, XRow original)` | zárolás, ON-CHECK-DELETE-MASTER / PRE-DELETE, a `DELETE` |
| LOV | `lov<Lov>(UserDto, LovRequest)` | a rekordcsoport lekérdezése a beírt szöveg szűrésével, a paraméterek forrása, az oszlopnevek |

A `commitForm` (Forms COMMIT_FORM) determinisztikus marad: saját SQL-je nincs, csak a blokkok végpontjait hívja
Forms-sorrendben. A rekordcsoport nélküli (statikus) LOV HTTP 501-et ad, az okkal, ez nem AI-feladat.

Minden ilyen metódus: egy rövid segítő sor fölötte, a keret, benne a megjegyzés, alatta üres törzs. A migrátor
semmit nem kezd el benne (sem helyőrzőt, sem félig lefordított kódot), mert az csak összezavarná a modellt:

```java
// CTRL.PB_LEZAR / WHEN-BUTTON-PRESSED: a törzset a Java asszisztens írja meg a lenti Forms-kódból.
@Transactional(rollbackFor = Exception.class)
@Override
public ActionResult onCtrlPbLezar(UserDto user, ActionRequest request) throws Exception {
    return log1x(log, RendelesConstants.ON_CTRL_PB_LEZAR_NAME, user, null, () -> {
        //region Eredeti Forms-kód: CTRL.PB_LEZAR / WHEN-BUTTON-PRESSED
        // A képernyő ezeket küldi: RENDELES.ID, RENDELES.STATUSZ.
        // SQL: UPDATE rendeles SET statusz = 'L' WHERE id = :id — :id = RENDELES.ID
        // --- CTRL.PB_LEZAR / WHEN-BUTTON-PRESSED
        // DECLARE
        // ...
        //endregion

    });
}

// ORDERS blokk (APP_ORDERS): új rekord.
@Transactional(rollbackFor = Exception.class)
@Override
public RowResult<OrdersRow> createOrders(UserDto user, OrdersRow row) throws Exception {
    return log1x(log, OrdersConstants.CREATE_ORDERS_NAME, user, null, () -> {
        //region AI-feladat: ORDERS / új rekord
        // Lépések Forms-sorrendben (var context = new RuleContext()):
        // 1. Mezőellenőrzés (HTTP 422, FormsChecks): kötelező: CODE; max. hossz: CODE 20, NAME 80.
        // 2. Triggerek: triggerWhenValidateItemCodeOrders(row, context) (WHEN-VALIDATE-ITEM);
        //    triggerPreInsertOrders(row, context) (PRE-INSERT).
        // 3. SQL: INSERT INTO APP_ORDERS (ID, CODE, NAME) VALUES (:id, :code, :name) (:mező = row.mező).
        // Vissza: new RowResult<>(row, context.messages()).
        //endregion
    });
}
```

A trigger régiójában a trigger teljes kódja és a hívott helyi eljárások/csomagok kódja is benne van, előtte
pedig utasításonként az SQL: az `INTO` nélkül, a Forms-kötések nevesített paraméterként, a forrásukkal
(`row.mező`, `BLOKK.MEZŐ`, `GLOBAL.X`) és az eredmény helyével. A tipp ugyanaz az utasítás, mint a
kommentben, csak nevesített paraméterekkel; legfeljebb nyolc sor, a többit csak megszámolja. A keret (aláírás,
DTO, log1x, tranzakció, hívási pont) végleges; az AI csak a törzset írja meg, a régió marad. Amíg egy végpont
törzse üres, a ServiceImpl nem fordul le (a lambda nem ad vissza értéket): a modult akkor kell használatba venni,
amikor minden törzs elkészült. A rekordtriggerek hookjai (`private void`) üresen is fordulnak. Modulszintű kapcsoló
(`MODULE_REVIEWED`) nincs: hogy mikor kész a modul, azt a fejlesztő dönti el.

## A Domain és az üres Repository

A blokk táblájához a generálás elkészíti a JPA entityt (`<Tábla>Domain`) és az üres repositoryt
(`interface <Tábla>Repository extends JpaRepository<<Tábla>Domain, ID> {}`). Ha a táblának van igazolt
szerkezete (schema.json `tables`, adatszótár vagy az **Adatbázistáblák** panel), a Domain a tábla minden oszlopát
leképezi az adatbázis típusával; ha nincs, a blokk oszlopait. A panelen mentett táblaszerkezetből (PK-val) azonnal
elkészül a tábla Domain-je és üres repositoryja, a trigger tábláinál is. A Domain a szerkezettel együtt frissül, a már
meglévő repositoryhoz viszont a migrátor nem nyúl, mert a fejlesztő már áttehetett bele lekérdezéseket. PK nélkül nincs
entity, mert az `@Id` kell hozzá.

A service nem használja őket. Ha a fejlesztő egy lekérdezést a repositoryba tesz, az eredeti SQL változatlanul
maradhat: `@Query(value = "<az SQL>", nativeQuery = true) List<Object[]> findX(@Param("x") String x);`.

## Automatikus AI-sor

- A migrálás végén a backend sorba állítja a modul összes AI-feladatát: az adatvégpontokat, a LOV-okat, a gombokat
  és a rekordtriggereket (feltétel: mentett AI-kapcsolat és bekapcsolt **Automatikus futás**). Táblaszerkezetre
  nem várnak, mert az AI az eredeti SQL-t futtatja. A táblaszerkezet az entityhez kell.
- Egyszerre egy modellkérés fut. Kézi indítás a sor elejére kerül.
- **Automatikus beillesztés** (alapból be): a szintaktikailag ellenőrzött (`javac`-parser), `MISSING:`
  nélküli javaslat bekerül a fájlokba; az eredeti fájl és a diff a `backend-assistant/proposals/<id>/`
  mappában marad, a modul ZIP frissül. Ha közben a modul megváltozott, egyszer újrafuttatja.
- Egy automatikusan sikertelen feladat ugyanarra a bemenetre nem fut újra; az **Újrapróbálás
  (sikertelenek is)** gomb kényszeríti. A **Sor leállítása** a futót megszakítja, a sort üríti.
- A két kapcsoló és a kapcsolat a közös AI-beállításokban van (`<adatmappa>/assistant-settings.json`).

## Élő állapot

Az Ollama-hívás streamelt. A fejléc AI-jelzője, a Java asszisztens munkasora és az `/ai-log` oldal mutatja:

- eltelt idő (másodpercenként frissül) és fázis: kapcsolódás → prompt feldolgozása → gondolkodik → írja a választ;
- az első tokenig eltelt idő (CPU-n ez a prompt feldolgozása), a gondolkodási és választokenek száma, tokens/s,
  a kimeneti keret (num_predict) kihasználtsága;
- a válasz és a gondolkodás utolsó sorai.

A leállítás bontja a kapcsolatot, így az Ollama is abbahagyja; az időkorlát a néma prompt-feldolgozás alatt is
érvényes. A megszakadt válasz eddigi része az AI-naplóba kerül.

### A panel követi az AI-t

A Java asszisztens panelje alapból **követi a futó AI-feladatot** (a modulválasztó alatti jelölőnégyzet):

- ha az AI egy másik modulon dolgozik, a panel arra a modulra vált; ha a követést kikapcsolod, a panel marad, az
  élő kártya pedig megmutatja, melyik modulon fut a kérés, és az **Átváltás erre a modulra** gombbal odaléphetsz;
- a modulon belül a futó trigger lesz a kiválasztott végpont, a nyitott AI-kommunikáció a futó kérést mutatja;
- amikor a futás véget ér, megjelenik az eredménye (a javaslat és a diff), a következő futás élő állapota pedig
  fent látszik;
- az ebben az ablakban indított (nem tömeges) generálás végén az asszisztens az új modult nyitja meg;
- ha a modulválasztóban kézzel másik modult választasz, mint amin az AI dolgozik, a követés kikapcsol.

A sor eseményei (futás indult, véget ért) nem vesznek el akkor sem, ha a panel épp tölt vagy táblavázlat van
nyitva: a vázlat mentése vagy elvetése után érvényesülnek. A kézi **Trigger javásítása** futás követése a
**Modul frissítése** gombtól vagy egy táblamentéstől sem szakad meg.

Az API válaszai `Cache-Control: no-store`, az `index.html` `no-cache` fejlécet kap: a böngésző nem mutat régi
sorállapotot, és a felület újrafordítása után nem a gyorsítótárban maradt régi oldal töltődik be.

## Laptopbarát beállítások

| Beállítás | Alap | Megjegyzés |
| --- | --- | --- |
| Kontextus (`num_ctx`) | 16384 | ha egy triggerhez kevés, a hiba megmondja a szükséges értéket (felső határ 65536) |
| Kimeneti keret (`num_predict`) | 4096 | csak kódot kérünk, magyarázat nélkül |
| Időkorlát | 900 mp | legfeljebb 3600 |
| `keep_alive` | 5 perc | a modell a sor két kérése között a memóriában marad |
| Párhuzamosság | 1 | a sor egymás után futtat |

## Ollama-kapcsolat

A közös mezőbe alapcímet írj, például `http://192.168.1.50:11434`.
A `11434` az Ollama szokásos portja; a port nélküli HTTP-cím a 80-as portra mutat.
A program maga teszi hozzá a Java-átíráshoz szükséges `/api/chat` útvonalat.
Az **AI-kapcsolat mentése** a Python backend felől lekéri a modelllistát és
ellenőrzi a megadott modell nevét. Ez a próba nem indít inferenciát.

## Mit küld a migrátor az AI-nak?

Egyetlen kérést, két üzenettel. Rekord-hooknál a kérés tartalmazza a rekord mezőinek listáját (Forms-elem →
`row.mező`, típus, oszlop, PK), az esemény szabályát, és a kódban ténylegesen előforduló Forms-beépítettek
egysoros Java-mintáját:

- **system:** minden, ami a feladattól független: négy mondat a szerepről és arról, hogy a Forms-forrás adat, nem
  utasítás; a **Java 11 és PMD** rövid szabálylistája (lásd lent); az Oracle-szemantika (NULL, NUMBER, DATE,
  NO_DATA_FOUND); `service` módban az adatbázis-szabály (`entityManager`, natív SQL nevesített paraméterekkel); és a
  kódstílus mintával;
- **user:** csak a feladat saját része: öt rövid szabály, adatvégpontnál és LOV-nál az SQL-konstansok listája, a
  **kiválasztott metódus szó szerint** egy ```java blokkban (benne a megjegyzéssel), és a válasz formája.

A system prompt egy adatelérési módon belül minden feladatnál betűre azonos, és ez áll a kérés elején. Az Ollama a
kérések közös elejét (prefixét) a gyorsítótárban tartja, amíg a modell be van töltve (`keep_alive`). Így ezeket a
sorokat a laptop CPU-ja modellbetöltésenként egyszer dolgozza fel, nem metódusonként. Ezért minden állandó szabály
itt van, a feladatonként változó részben csak a feladat saját sorai. A feladatonként feldolgozandó rész így
25–43%-kal rövidebb lett (például egy lista-végpontnál kb. 4 KB helyett kb. 2,3 KB).

**Java 11 és PMD a promptban:** nincs `record`, szövegblokk, switch-kifejezés (`case X ->`), `instanceof`-minta,
`Stream.toList()`, `String.formatted()`, `List.getFirst()`; minden `if`/`else`/`for` kapcsos zárójellel; soronként egy
változó; nincs nem használt lokális változó vagy privát metódus; `"SZÖVEG".equals(érték)`, Stringnél soha `==`;
BigDecimal-nál `compareTo(...) == 0` (az Oracle `1 = 1.0` igaz, a `BigDecimal.equals` nem); `isEmpty()` a
`size() == 0` helyett; `toUpperCase(Locale.ROOT)`; konkrét kivétel elkapása, üres catch nélkül, az ok megtartásával;
nincs `System.out` és `printStackTrace`. Amit ebből a migrátor egyértelműen javítani tud, azt a válaszban helyben
javítja (lásd a táblázatot).

### Céges (AWU) DTO-k: a modul saját osztálynevei és getter/setterek

Az `AWU_AZON`-nal generált modul DTO-i külön Lombok-osztályok (`<Modul>ActionResultDto`, `<Modul>PageResultDto<T>`,
`<Modul><Blokk>Dto`), privát mezőkkel. A szabályok szövege viszont az általános szerződést nevezte meg
(`Return new ActionResult(...)`, `request.blocks()`, `row.mező`), és a kis modell ezt szó szerint átvette:
`return new ActionResult(...)` egy `RtActionResultDto`-t visszaadó metódusban.

Most a prompt a modul nyelvén szól (`frm_forms/dto_dialect.py`). A típusneveket a CL DTO-k forrásából és a
metódus saját szignatúrájából veszi; ez utóbbi a döntő, így egy kézzel átnevezett `MyModulActionResultDto` is
helyesen jelenik meg:

- `4. Return new RtActionResultDto(blocks, messages, commands, globals)`, és a gombváz is így zárul;
- `request.getBlocks()`, `request.getParameters()`, `request.getOffset()`, `request.getLimit()`;
- hooknál a rekord mezői: `:RENDELES.VEVO -> row.getVevo() / row.setVevo(..)`, és a szabály a Lombok-beant írja le;
- a metódus `//region` megjegyzésében a migrátor tippjei is (`:vevo = row.getVevo()`, `eredmény -> row.setVevoNev(..)`,
  `request.getCriteria().getRendelesId()`). Az eredeti Forms-kód a `// ---` sor után változatlan marad, és a fájl
  megjegyzése sem változik: a promptban jelenik meg így. A már legenerált (CREATE_ONCE) ServiceImpl-ek is ezt a
  promptot kapják, újragenerálás nélkül.

A céges modul rekordtrigger-hookjai (`triggerPreInsertTetel(RtTetelDto row, …)`) eddig nem kerültek az AI-sorba,
mert a felismerő `…Row` típust várt. Most ezek is feladatok.

Az alapformátum (`AWU_AZON` nélkül) promptja változatlan: `ActionResult`, `request.blocks()`, `row.mező`.

Az adatbázis-szabály (a system promptban): az adatbázis-elérés a service `entityManager` mezőjén át, natív
lekérdezésként, nevesített paraméterekkel (`createNativeQuery(sql).setParameter(...)`, majd `getResultList()`,
`getSingleResult()` vagy `executeUpdate()`). Az oszlopértéket a `SqlValues.text/number/dateTime` olvassa, a null
értéket a migrátor köti `SqlValues.bind`-dal. Repository és JDBC nincs. A feladat szabályai (a user részben): a
log1x-keret megtartása, honnan jön az SQL (a megjegyzésből, illetve adatvégpontnál a konstansokból), a
képernyőértékek (`PlsqlValues.text/number/datetime`, null-biztosan) és az `ActionResult` használata.

Adatvégpontnál és LOV-nál a szabály az `AI-feladat` lépései szerinti törzs, és csak az, ami a művelethez kell:

- új rekord, módosítás: a `FormsChecks` mezőellenőrzés, a hookok hívása, a HTTP-válaszok (404, 409, 422), és az,
  hogy a zárolás és az írás `FormsErrors.translate`-tel adja a Forms-szerű hibát;
- törlés: a hookok, a zárolás és a HTTP-válaszok;
- lista, keresés: a hookok (POST-QUERY) és a 422; LOV: csak a 422. Mezőellenőrzés és zárolás itt nincs a szabályok
  között: nem kell, és a kis modellt csak megzavarná.

Az SQL-konstansok SQL-jét a prompt nem ismétli: a metódus `//region` megjegyzésében a konstans neve áll
(`// LOV_LOV_STATUSZ_SQL`), a paraméterei a konstanslistában. Rövidebb, és nincs mit újragépelni.

A kódstílus-rész (a system promptban, egy mintával) a céges PMD/Checkstyle ellenőrzésekhez: a több soros kifejezés
új sora az operátorral kezdődik (`+`, `&&`, `||` nem áll sor végén), és a lokális változó közvetlenül az első
használata előtt, legfeljebb 5 sorral fölötte jön létre.

A kódban hívott adatbázis-rutinoknál (igazolt szignatúrával) a kérés megmondja a hívás módját: függvényt
`SELECT PKG.F(:a) FROM DUAL`-lal, IN paraméteres eljárást `BEGIN PKG.P(:a); END;`-dal, OUT paraméteresnél
`StoredProcedureQuery`-vel. A rutin az Oracle-ben fut tovább, az AI nem írja újra.

A táblák szerkezete, az entity Java-forrása, a modul DTO-/XML-/képernyőleltára, a hívásgráf és a többi végpont nem
megy el. A **Kiegészítő forrás** mező tartalma csak akkor kerül a kérésbe, ha kitöltöd.

A küldendő kérés AI-hívás nélkül megnézhető: **AI-kommunikáció → Küldendő prompt előnézete**,
az `/ai-log` oldal ugyanilyen gombja, vagy a CLI `--show-prompt` kapcsolója.

## Mit vár vissza?

````text
```java
// SERVICE
<a teljes átírt metódus, utána az esetleges privát segédmetódusok>
```
MISSING:            (csak ha tényleg hiányzik valami, pl. egy hívott adatbázis-csomag forrása)
- <mi hiányzik>
NOTES:              (opcionális, legfeljebb 3 rövid magyar sor)
````

Repository-kódot nem kér. A gombok promptja a törzs vázát is megadja (képernyőérték olvasása, `messages`,
`commands`, `blocks`, `return new ActionResult(...)`): a kis modell egy vázat sokkal biztosabban követ, mint a
szöveges szabályt.

A feldolgozó ennél engedékenyebb, mert a kis modellek nem mindig követik betűre a formát:

| Az AI ezt adja | A migrátor ezt teszi |
| --- | --- |
| teljes `LookupServiceImpl` osztály | csak a kiválasztott metódust és az új privát segédmetódusokat veszi át; a többi végpont nem változik |
| csak a metódustörzs, címke nélkül | a log1x-keretbe teszi |
| `<think>…</think>` | a gondolkodást külön mutatja, a kódot feldolgozza |
| lezáratlan utolsó kódblokk | átveszi, ha a zárójelek zárnak; különben „megszakadt válasz” |
| az eredeti Forms-komment vagy az `AI-feladat` megjegyzés másolata | elhagyja; a migrátor a meglévő metódus megjegyzését teszi be |
| meglévő metódusnév más tartalommal | átnevezi (`onCtrlRunFindAccount`), a hívást is |
| régi JSON-válasz | ugyanígy ellenőrzi |
| `request.getBlocks()`, `request.getParameters()` | helyben javítja: `request.blocks()`, `request.parameters()` (a DTO record-stílusú) |
| `row.getVevo()`, `row.setId(x);` egy hookban | helyben javítja: `row.vevo`, `row.id = x;` (a Row DTO-nak nyilvános mezői vannak) |
| céges modulban `new ActionResult(...)`, `new PageResult<>(...)`, `OrdersRow` | helyben javítja a modul osztályára: `new RtActionResultDto(...)`, `new RtPageResultDto<>(...)`, `OrdersOrdersDto` |
| céges modulban `request.blocks()`, `row.vevo`, `row.vevo = x;`, `request.criteria().kod` | helyben javítja: `request.getBlocks()`, `row.getVevo()`, `row.setVevo(x);`, `request.getCriteria().getKod()` (a DTO Lombok-bean; a változó típusát a paraméterekből és a deklarációkból ismeri). Ami így sem javítható (pl. `row.osszeg += 1`), arra pontos javítási kérés megy |
| `stream.toList()`, `list.getFirst()` (Java 16/21) | helyben javítja: `.collect(Collectors.toList())`, `.get(0)` |
| `x.equals("A")`, `x == "A"`, `x != "A"` | helyben javítja: `"A".equals(x)`, `!"A".equals(x)` (PMD; NULL esetén nem NPE, hanem nincs egyezés, mint az Oracle-ben) |
| `list.size() == 0`, `> 0`, `!= 0` | helyben javítja: `list.isEmpty()`, `!list.isEmpty()` (csak ha a kifejezés határa egyértelmű) |
| `s.toUpperCase()`, `new BigDecimal(0)`, `new BigDecimal(0.1)` | helyben javítja: `toUpperCase(Locale.ROOT)`, `BigDecimal.ZERO`, `new BigDecimal("0.1")` |
| `record`, szövegblokk, `case X ->`, `instanceof String s`, `.formatted(...)` | Java 11-ben nincs: pontos javítási kérés megy, akkor is, ha a gépen nincs JDK a szintaxis-ellenőrzéshez |
| üres `catch`, `printStackTrace()`, `System.out`, `catch (Exception e)` | PMD-figyelmeztetés a javaslatnál; nem kér újabb AI-hívást |
| `PersistenceException`, `Query`, `StoredProcedureQuery`, `ParameterMode` import nélkül | beteszi az importot a `jpa_namespace` szerint (javax vagy jakarta) |
| `.setParameter("név", érték)` egy natív lekérdezésen | helyben javítja: `SqlValues.bind(érték)`. A típus nélküli null-t a Hibernate 5 bináris értékként küldi, amit az Oracle NUMBER és DATE oszlopnál elutasít (ORA-00932); a `bind` üres szöveget küld, amit az Oracle NULL-nak olvas. A biztosan nem null értéket (szám, `offset`, `limit`) és a tárolt eljárás típusos paramétereit nem bántja |
| címke nélküli második blokk csak privát metódusokkal | a service segédmetódusai |
| `String record = …` | rendben (csak a `record Név(…)` deklaráció tilos) |

Ezekért nem megy újabb kérés: egy javító kör egy teljes modellhívás (CPU-n percek), és a kis modell gyakran
ugyanazt a hibát ismétli. Javítást csak az kér (a második hívásban az előző válaszával együtt), aminek nincs
egyértelmű javítása: a service JDBC-t vagy ismeretlen repositoryt hív (`jdbc`, `DbCalls`, `xRepository`); benne
maradt a PL/SQL-hívás vagy a `NOT_IMPLEMENTED` helyőrző; vagy a Java-kód nem szintaktikus (a hibás sorral együtt).

### Mi állítja meg a javaslatot, és mi nem

Az arany középút: újabb AI-hívást (és két hívás után hibás vázlatot) csak az okoz, ami **nem fordulna le**, vagy
**biztosan hibát adna futáskor**, vagy biztonsági kérdés. Minden mást a migrátor helyben javít, vagy
figyelmeztetésként mutat, és a javaslat átvehető.

| A modell kódja | Mi történik |
| --- | --- |
| a service saját mezője (a fejlesztő `@Autowired` beanje), vagy a java-variables.json `autowired` mezője (`formsCommonsRepository.getIgKod(user)`) | rendben: ezek létező mezők. Korábban „ismeretlen repository” hibát adott, akkor is, ha a migrátor saját cseréje írta a kódba |
| `private static final String SQL = "…";` a metódus mellett | a konstans értéke a kódba kerül (osztálymezőt a migrátor nem vesz fel); korábban szó nélkül kimaradt, és a kód nem fordult volna le |
| más mező a metódus mellett, amit a kód használ | egy pontos javítási kérés: legyen helyi változó |
| `setParameter` olyan névre, amely nincs a megírt SQL-ben | helyben törli (futáskor hibát adna), figyelmeztetéssel: nézd meg, nem maradt-e ki feltétel |
| a hookok más sorrendben | figyelmeztetés (egy ciklus vagy segédmetódus is megváltoztathatja a szöveg szerinti sorrendet); hiányzó hookhívás továbbra is javítási kérés |
| `// REPOSITORY` blokk `service` módban | kimarad, figyelmeztetéssel (ha a kód hívja, az a hívásnál derül ki) |
| a modell megírta egy generált adatbázis-rutin metódusát | a generált marad, megjegyzéssel |
| `"…".formatted(x)` | helyben `String.format("…", x)` |

### Ami lefordul, de futáskor hibázna

A fordító nem látja a natív lekérdezések hibáit, ezért ezeket a migrátor ellenőrzi a válaszon
(`frm_forms/answer_checks.py`), és pontos, angol nyelvű javítási kérést küld a modellnek:

- **Paraméterek:** az SQL minden `:név` paramétere kap értéket, és a `setParameter` nem állít be olyan nevet, amely
  nincs az SQL-ben. A futáskor összerakott SQL-nél a hiányzó kötést előbb helyben pótolja (lásd lent); csak az
  marad hiba, aminek az értékét sehonnan nem tudja venni. A továbbadott lekérdezést nem ítéli meg.
- **Adatvégpont és LOV:** a kód a megjegyzés SQL-jét futtatja, és a megjegyzésben megnevezett triggereket a
  megadott sorrendben hívja. A lista lapozása kétféleképp is írható (`OFFSET … FETCH` az SQL-ben, vagy
  `setFirstResult`/`setMaxResults`), ezért ott csak a blokk lekérdezése kötelező. Az elvárt SQL és a hívások a
  `backend-plan.json` végpontjának `contract` mezőjében vannak.

### Kívülről jövő értékek: java-variables.json és a futáskor összerakott SQL

- **java-variables.json az AI kódjában is.** Ha a modell olyan nevű változót hoz létre, vagy olyan nevű SQL-paramétert
  állít be, amely a térképben szerepel (`variableName`), a migrátor utólag kicseréli az értékét a térkép kifejezésére
  (`String igkod = formsCommonsRepository.getIgKod(user);`). A service-be bekerül a bejegyzés importja és `@Autowired`
  mezője. A modell nem kap róla tájékoztatást, ezért a prompt sem hosszabb.
- **A futáskor összerakott SQL paramétere:** lásd a következő részt.

### A natív SQL paramétereinek kötése

A kis modell az SQL-t jól írja, a kötések könyvelését viszont elrontja, főleg ha a Forms `DEFAULT_WHERE`-jéhez
hasonlóan darabokból rakja össze a lekérdezést. Ez nem a metódus logikáján múlik, ezért a migrátor helyben rendbe
teszi (`frm_forms/sql_binding.py`), újabb AI-hívás nélkül.

**Mit lát.** Minden `createNativeQuery`-nél követi az SQL darabjait: a literált, az SQL-konstanst, és a lekérdezés
előtt épített `String`-et (`+=`), `StringBuilder`-t (`append`) és `List<String>`-et (`add`, majd `String.join`),
egymásból építve is (`sql.append(where)`). Minden darabnál tudja, milyen feltétel alatt kerül az SQL-be (`if`,
`else`, ciklus, kapcsos zárójellel vagy anélkül). Látja a kötéseket is: `setParameter` a láncon vagy a lekérdezés
változóján, és a lekérdezésre ráadott `Map` elemei (`params.forEach(q::setParameter)`, lambda, `entrySet`-ciklus).

**Mit javít:**

| A modell kódja | Javítás |
| --- | --- |
| a mindig jelen lévő darab `:név`-e nincs kötve | `.setParameter("név", érték)` a lekérdezésen |
| a mindig jelen lévő darab `:név`-e csak egy `if` alatt kötött | a kötés feltétel nélküli lesz, a kiürült `if` törlődik |
| az `if` alatti darab `:név`-e mindig kötött, vagy sehol | `sqlParams.put("név", SqlValues.bind(érték));` a darab mellé, ugyanabba az `if`-be (a kapcsos zárójel nélküli `if` kapcsos zárójelet kap); `Map<String, Object> sqlParams = new LinkedHashMap<>();` a builder után, `sqlParams.forEach(q::setParameter);` a lekérdezés után. Ha a lekérdezésnek nincs változója, `Query sqlQuery = …` lesz belőle |
| a modell saját `Map`-jéből hiányzik egy érték | a darab mellé kerül az ő `Map`-jébe; az értékei `SqlValues.bind(...)`-ot kapnak |
| szövegliterál SQL `:név`-e nincs kötve | `.setParameter("név", érték)`, ha van hozzá érték |
| csak képernyőértéket vagy állandót olvasó, használatlan változó | törli (PMD UnusedLocalVariable); az SQL-paraméterrel azonos nevűt nem |

**Az érték forrása,** sorrendben: a modell saját kötésének kifejezése (ha az ott, ahová kerül, elérhető), az
azonos nevű változó vagy metódusparaméter (más írásmóddal is: `:p_kod` → `pKod`), végül a java-variables.json
bejegyzése. Ha egyik sincs, a modell egyetlen pontos javítási kérést kap: hozzon létre `név` nevű változót a darab
előtt.

**Amihez nem nyúl** (figyelmeztetést kaphat):

- a futáskor képzett paraméternév (`" OR ID = :p" + i`);
- az újrakezdett builder (`setLength(0)`, új értékadás);
- a `?:`-tal választott darab: kivétel a „ha üres, ne kerüljön bele” minta (`where.isEmpty() ? "" : …`);
- a helyes kötés, az if/else ágakban külön értékkel kötött is.

A prompt egy sora mutatja a feltételes darab mintáját: SQL-darab `if` alatt → az értéke ugyanabban az `if`-ben egy
`Map`-be, majd `params.forEach(query::setParameter)`.

### Az adatvégpontok és a LOV-ok SQL-je: konstansok, Oracle szerinti összehasonlítás

Ezeknek a metódusoknak az SQL-je a migrátoré (a blokk lekérdezése, a `FOR UPDATE NOWAIT` zárolás, az
`INSERT`/`UPDATE`/`DELETE`, a LOV rekordcsoport-lekérdezése a szűréssel és a limittel). Korábban a modellnek a
megjegyzésből kellett betűre újragépelnie. Egy hosszú lekérdezés ritkán jött vissza pontosan, és az ellenőrzés két
irányban is tévedett:

- elutasította a **helyes** másolatot, ha a modell elhagyta a rekordcsoport `--` megjegyzését, `AS`-t írt az alias
  elé, vagy soronként darabolta a szöveget (két hívás után hibás vázlat lett belőle);
- elfogadta a **hibásat**: egysoros Java-szövegben a `-- megjegyzés` az utána következő teljes SQL-t kikommentezi, de
  mivel az összehasonlítás eldobta a szóközöket és a sortöréseket, ez nem látszott.

Most (`frm_forms/frame_sql.py`):

- **Konstansok.** Az utasítások a service konstansai: `LOV_INPTIP_SQL`, `LIST_X_SQL`, `UPDATE_X_LOCK_SQL`,
  `UPDATE_X_SQL`, `CREATE_X_SQL`, `DELETE_X_LOCK_SQL`, `DELETE_X_SQL`. A prompt felsorolja őket a paramétereikkel, a
  modell név szerint futtatja őket (`entityManager.createNativeQuery(LOV_INPTIP_SQL)`). Így nincs mit elgépelni, és
  CPU-n a hosszú SQL kimeneti tokenjei is megspórolódnak. A metódus által használt konstanst a migrátor deklarálja az
  osztály elején, soronként, a megjegyzések megtartásával (`"...\n" + "..."`), külön kommentár nélkül. A megtartott
  `//region AI-feladat` tippekben az ilyen SQL helyén ezután a konstans neve áll (`// 2. Zárolás: UPDATE_X_LOCK_SQL
  (:mező = original.mező) …`), így az SQL nem szerepel kétszer a fájlban. A már legenerált modulok is így
  működnek, újragenerálás nélkül.
- **Összehasonlítás Oracle szerint.** Tokenenként történik, a megjegyzéseket úgy hagyja el, ahogy az Oracle, és nem
  számít a kis- és nagybetű, a sortörés vagy az alias előtti `AS`. Ami Oracle szerint ugyanaz az utasítás, az
  átmegy, és a modell kódjában úgy marad, ahogy írta.
- **Helyi javítás.** Ha a modell újragépelt SQL-je eltér, de egyértelműen ugyanaz a szándék (ugyanazok a paraméterek,
  majdnem ugyanazok a tokenek, például egy sorba írt `--` megjegyzés vagy két összeragadt szó), a lekérdezés a konstanst
  futtatja helyette. Ehhez nem kell újabb AI-hívás; a javaslatnál figyelmeztetés jelzi. Ha az eltérés érdemi (más
  feltétel, más paraméter), a javítási kérés arra kéri a modellt, hogy a konstanst futtassa.
- **Minden natív lekérdezésnél:** ha egy egysoros SQL-szövegben a `--` megjegyzés után még SQL következik, az hiba,
  és pontos javítási kérés megy vissza. A `String sql = "..."` változóban tartott SQL is ellenőrzött, kivéve, ha a
  változót később módosítják.

A megjegyzések SQL-je az `analysis/db-statements.json`-ba is bekerül, így a `verify-db` még az AI futása előtt
lefordítja az Oracle-ben (futtatás nélkül): a hibás oszlopnév vagy hiányzó jogosultság ott derül ki.

## Az AI-napló

Minden futás menti: a küldött system és user promptot, minden próbálkozás nyers válaszát (az
elutasítottat is), az esetleges gondolkodási szöveget, a javítási kérést, az Ollama token- és
időadatait, és hogy melyik kódblokkot hova sorolta a migrátor, mit hagyott el és miért.

- **Webfelület:** Java asszisztens → 2. Trigger javásítása → **AI-kommunikáció** (újrafordított felület kell).
- **Újrafordítás nélkül:** `http://localhost:8000/ai-log` (a modul és a végpont kiválasztható; a hibaüzenet
  közvetlen linket ad az adott futáshoz).
- **Fájl:** a modulmappa mellett, `<modul>-ai-log/<azonosító>.json`; a legutóbbi 60 futás marad meg.
  A modul ZIP-jébe nem kerül.
- **API:** `GET /api/jobs/{job}/assistant/exchanges`, `GET /api/jobs/{job}/assistant/exchanges/{id}`,
  `POST /api/jobs/{job}/assistant/prompt` (`X-Frm-Client: local-ui` fejléccel).

## A munkamenet

1. Válaszd ki a generált modult és a végpontot (az adatvégpontok és a LOV-ok is a listában vannak).
2. Az **Adatbázistáblák** panelen a táblák oszlopai CSV-fájlból vagy beillesztett szövegből importálhatók; jelöld az
   adatbázis tényleges PK-ját, és kattints a **Táblaszerkezet mentése** gombra. Ebből készül a tábla entityje és üres
   repositoryja; az AI-futást nem tartja vissza. A `DUAL/SYS.DUAL` nem kér entityt.
3. Kattints a **Trigger javásítása** gombra. Futás közben az AI-kommunikáció rész már mutatja
   az elküldött kérést.
4. Nézd át a visszakapott kódot. Alapból a DPS ServiceImpl teljes fájlja látszik, a diffel együtt.
   A feldolgozási figyelmeztetések a javaslat alatt vannak.
5. **Beillesztés a fájlokba**: a service frissül. Töltsd le a **Friss modul ZIP** csomagot, és fordítsd a céges
   projektben.

## A visszakapott kód

A service megtartja a meglévő metódus aláírását, log1x/tranzakciós keretét és a megjegyzést (az eredeti
Forms-kódot, illetve az `AI-feladat` lépéseit). A logika és az SQL a service-ben van: az `entityManager` natív
lekérdezései, az eredeti Oracle SQL-lel. Az importokat a migrátor teszi be a használt JDK/Spring/JPA-típusokhoz és
a `CommonMigrateTools` segédeihez (`SqlValues`, `FormsChecks`, `FormsErrors`, `LovQuery`, `PlsqlValues`).

**A projekt `CommonMigrateTools.java`-ja legyen naprakész.** Ez az egy közös fájl nem települ a modulokkal: egyszer
kell a projektbe tenni, és új változatnál cserélni (visszafelé kompatibilis). Az AI-módú kód a 9. változatot várja: a
`LovQuery.limit` a 7.-ben, a `SqlValues.text/number/dateTime` és a `FormsErrors.translate` a 8.-ban, a
`SqlValues.bind` a 9.-ben jelent meg. Ha a projektben régebbi van, a kód nem fordul le („cannot find symbol
LovQuery”). Ha a projekt mappái be vannak állítva, a Java asszisztens minden javaslatnál figyelmeztet, ha a
projektben régebbi `CommonMigrateTools.java` van; a telepítési jelentés is jelzi. A friss változat a modul
`backend/CL/CommonMigrateTools.java` fájlja.

A Domain és az üres repository a DPS-csomagban van, a JPA-annotációk csomagja a `jpa_namespace` beállítás (javax
vagy jakarta). Összetett PK esetén a Domain beágyazott `Key` osztálya az id (`@IdClass`). A NUMBER BigDecimal marad.
A Controller, az URL, a service-interface és a meglévő request/result DTO API-ja megmarad.

## Ha az AI adatot kér

Ha a válaszban `MISSING:` lista van, a javaslat állapota „További adat szükséges”: a hiányzó elemek
és az elkészült vázlat látszik, letölthető, de nem illeszthető be. Add meg a hiányzó rutint vagy céges
API-t a **Kiegészítő forrás (ha az AI kéri)** részben, és futtasd újra.

## Ha nem jön használható javaslat

Ha az utolsó próbálkozás válaszában is maradt hiba, a futás nem vész el: az utolsó válasz **hibás vázlatként**
(`status: draft`) megmarad, a hibákkal a figyelmeztetések között. A kódja megtekinthető, letölthető és kézzel
javítható, de így nem illeszthető be, és nem kerül a cache-be (a következő futás újra kérdezi a modellt). Ha a
vázlat nem illeszthető, akkor is látszik a service-fájl a modell metódusával.

Hibaüzenet (vázlat nélkül) csak akkor jön, ha a válaszban nincs használható kód (üres, megszakadt, csak magyarázat).
A gomb melletti hibaüzenet megmondja, hány AI-hívás történt és mi volt a baj, és az AI-napló linkjét
is tartalmazza. Gyakori okok:

- **„a kimeneti keretnél megszakadt”** — növeld a Kimeneti keretet (num_predict) a Memória és futtatás résznél.
- **„A teljes kontextus és a kimeneti keret becsült igénye …”** — a num_ctx kevés; a hiba megadja a szükséges értéket.
  A becslés óvatos (kb. 3 byte/token); a tényleges bemeneti tokenszámot az AI-napló mutatja.
- **„Nem találtunk használható SERVICE kódot”** — nézd meg a nyers választ; ha a modell magyarázott kód helyett,
  a második hívás már az előző válaszával és pontos kéréssel megy.

## CLI

```text
python -m frm_forms backend-assist MODULMAPPA --list
python -m frm_forms backend-assist MODULMAPPA --task FELADAT_ID --show-prompt
python -m frm_forms backend-assist MODULMAPPA --task FELADAT_ID --url http://LAPTOP_IP:11434 --model MODELL
python -m frm_forms backend-apply MODULMAPPA --proposal JAVASLAT_ID --reviewed
```

A CLI is írja az AI-naplót (`MODULMAPPA-ai-log/`), és kiírja a helyét. Az alapértelmezés a `service`
(EntityManager a service-ben). A korábbi, repositoryba író mód (`--data-access jpa`, a generált repository-metódusokkal
és az egyszerű triggerek AI nélküli fordításával) és az inline/jdbc kapcsolók kompatibilitási okból megmaradtak; a
webes felület mindig a `service` módot használja.
