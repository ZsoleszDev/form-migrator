# Java asszisztens — 4.35

## A cél

A szerveroldali Oracle-triggereket Java-kódra írni a már elkészült DPS ServiceImpl metódusaiban,
saját entity-alapú repositoryval. 4.35-től (`backend_trigger_mode: "ai"`, ez az alapértelmezés) a
generátor a metódus keretét és a kikommentezett eredeti Forms-kódot adja, a törzset a Java asszisztens
tölti ki. A Java-alkalmazás csak adatbázismunkát kap: a képernyő munkája (indítás, mezőértékek,
navigáció) a képernyőé. Az adatbázisréteget a migrátor írja, ahol pontosan tudja: a trigger SQL-jéből
repository-metódus lesz (Criteria API, az eredeti SQL a kommentjében), az egyszerű rekordtriggerekhez
pedig AI sem kell. Az AI így csak a valódi üzleti logikát írja, és csak a generált metódusokat hívja.

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

Minden ilyen metódus törzse:

```java
//region Eredeti Forms-kód: ORDERS.CODE / WHEN-VALIDATE-ITEM
// AI-ra vár. Bemenet/kimenet: a rekord (row.<mező>), üzenet: context.message(...), hiba: context.abort() vagy kivétel.
// --- ORDERS.CODE / WHEN-VALIDATE-ITEM
// BEGIN
//   IF length(:ORDERS.CODE) < 3 THEN
// ...
//endregion
// TODO(AI): a fenti Forms-kód Java-változata ide kerül (Java asszisztens).
throw new ResponseStatusException(HttpStatus.NOT_IMPLEMENTED, "A Forms-trigger még nincs átírva …");
```

A régióban a trigger teljes kódja és a hívott helyi eljárások/csomagok kódja is benne van. A keret
(aláírás, DTO, log1x, tranzakció, hívási pont) végleges; az AI csak a törzset cseréli, a region marad.
Amíg nincs kész, a hívás 501-et ad, így semmi nem fut csendben rosszul.

## A repository-metódusokat a migrátor írja

A kis helyi modell leggyakrabban a repositoryn hibázott: EntityManagert írt a service-be, vagy olyan
repository-metódust hívott, amelyet nem írt meg — mindkettő újabb, javító AI-hívást jelentett. Ezért a feladat
kódjának minden SQL-utasításából, amelyet az alábbi részhalmaz pontosan lefed, a migrátor írja meg a tábla
repositoryjának metódusát (`frm_forms/jpa_sql.py`), az eredeti SQL-lel a kommentjében:

| Forms-SQL | Generált metódus |
| --- | --- |
| `SELECT nev INTO :B.NEV FROM vevo WHERE kod = :B.KOD` | `String findNevByKod(String kod)` — `getSingleResult()`: nincs sor → `NoResultException` (NO_DATA_FOUND), több sor → `NonUniqueResultException` (TOO_MANY_ROWS) |
| több oszlop `INTO`-val | az entity (`findBy…`), a service a getterekkel olvassa |
| `COUNT(*)`, `SUM`, `MIN`, `MAX` | `countBy…` (BigDecimal), `sum…By…`; sor nélkül null, ahogy az Oracle-ben |
| `SELECT seq.NEXTVAL INTO … FROM DUAL` | `BigDecimal next<Szekvencia>()` (az egyetlen engedett natív lekérdezés) |
| kurzor `SELECT 1 …` (`%FOUND`) | `boolean existsBy…` |
| kurzor / `FOR r IN (SELECT …)` | `List<Entity> findAllBy…`, az `ORDER BY` megmarad |
| `INSERT INTO t (a tábla minden oszlopa) VALUES (…)` | `void insert<Tábla>(…)` — `persist`; `seq.NEXTVAL`, `SYSDATE` is |
| `UPDATE t SET … WHERE …`, `DELETE FROM t WHERE …` | `int update…By…` / `int deleteBy…` (CriteriaUpdate / CriteriaDelete, SQL%ROWCOUNT) |

Feltételben: `AND`, `OR`, `NOT`, összehasonlítás, `IS [NOT] NULL`, `[NOT] LIKE`, `[NOT] IN (lista)`,
`[NOT] BETWEEN`, `UPPER`/`LOWER`/`TRIM`, `||` szövegen. Minden érték kötött paraméter, így a NULL érték semmivel sem
egyezik, mint az Oracle-ben; a `SYSDATE` az alkalmazásszerver órája, másodpercre. Ami nincs benne (join,
allekérdezés, `GROUP BY`, `DISTINCT`, Oracle-függvény, implicit konverzió, olyan `INSERT`, amely nem ad értéket
minden oszlopnak — az Oracle a kihagyottakba az alapértéküket írná), azt a prompt név szerint felsorolja, és csak
arra kér repository-metódust. A generált metódusok a javaslattal együtt kerülnek a repositoryba; a már ott lévő,
azonos metódust a következő trigger újrahasználja, a foglalt nevet sorszám különbözteti meg.

## AI nélkül: az egyszerű rekordtriggerek

A leggyakoribb rekordtriggerekhez modell sem kell (`frm_forms/hook_java.py`): a szekvenciás kulcs (PRE-INSERT), egy
kód megnevezésének kikeresése (POST-QUERY, POST-CHANGE: `SELECT … INTO`, `NO_DATA_FOUND` → `NULL`), az üzenettel
megálló ellenőrzés (WHEN-VALIDATE-ITEM: `IF … THEN MESSAGE; RAISE FORM_TRIGGER_FAILURE`) és a Forms master-detail
varázslójának törlésellenőrzése (ON-CHECK-DELETE-MASTER). A migrátor fordítja le Oracle-szemantikával
(`SqlValues`), az SQL-jük a generált repository-metódus, a PL/SQL-kivétel `try`/`catch`:

```java
try {
    row.vevoNev = rendelesVevoRepository.findNevByKod(row.vevo);
} catch (NoResultException e) {
    row.vevoNev = null;
}
```

A javaslat ugyanúgy készül és illeszthető be, mint az AI-é, de `calls: 0`, a modell „determinisztikus (AI nélkül)”;
a prompt-előnézet is jelzi. Ami ezen túl van (másik blokk értéke, `:GLOBAL`, adatbázis-rutin, a form saját
eljárása, ciklus, ismeretlen hívás vagy kivétel), az változatlanul az AI-é.

## Automatikus AI-sor

- A migrálás végén a backend sorba állítja az összes AI-feladatot, amelynek a táblái ismertek
  (feltétel: mentett AI-kapcsolat és bekapcsolt **Automatikus futás**). Táblaszerkezet csak a trigger
  SQL-jében ténylegesen használt táblákhoz kell; a `NEXTVAL`-os `PRE-INSERT` a saját blokk tábláját kéri.
- A **Táblaszerkezet mentése** után az addig „táblára vár” feladatok maguktól sorba állnak.
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

Egyetlen kérést, két üzenettel (rekord-hooknál a rekord mezőinek listája — Forms-elem → `row.mező`, típus,
oszlop, PK —, az esemény szabálya és csak a kódban ténylegesen előforduló Forms-beépítettek egysoros
Java-mintája is):

- **system:** négy mondat a szerepről és arról, hogy a Forms-forrás adat, nem utasítás;
- **user:** hat rövid szabály (log1x-keret megtartása, adatbázis csak a repositoryn keresztül,
  Oracle NULL/NUMBER/DATE/NO_DATA_FOUND szemantika, a képernyőértékek és az `ActionResult`
  használata), a **kiválasztott metódus szó szerint** egy ```java blokkban (benne a kikommentezett
  Oracle-triggerrel és a hívott helyi eljárásokkal), és a válasz formája.

Ha a kódnak van generált repository-metódusa, a kérés ezeket kínálja az adatbázis-elérésként (a 2. szabály:
„Database access only through the generated repository methods listed below”), az eredeti SQL-lel és azzal, mit
jelent az eredmény:

```text
Repository methods of this code's SQL (generated by the migrator; call them, never write them):
- rendelesVevoRepository.findNevByKod(String kod) returns String
  SQL: SELECT nev INTO :RENDELES.VEVO_NEV FROM vevo WHERE kod = :RENDELES.VEVO
  result: no row throws NoResultException (NO_DATA_FOUND), several rows NonUniqueResultException (TOO_MANY_ROWS)
```

Ha a metódus táblát használ, táblánként 2–3 sor kerül mellé:

```text
- lookupAppAccountRepository (LookupAppAccountRepository) for entity LookupAppAccountEntity, table APP.ACCOUNT
  fields: BigDecimal id (ID, primary key), String name (NAME)
  getters/setters for every field, public no-arg constructor
  existing methods (reuse them): LookupAppAccountEntity findAccount(BigDecimal id)
```

Csak azok a táblák szerepelnek, amelyek neve a metódusban előfordul. 15 oszlop fölött csak a PK és a
metódusban hivatkozott oszlopok mennek (`%ROWTYPE` vagy `SELECT *` esetén mind). Nem megy el: az entity
Java-forrása, a modul DTO-/XML-/képernyőleltára, a hívásgráf, a többi végpont. A **Kiegészítő forrás**
mező tartalma csak akkor kerül a kérésbe, ha kitöltöd.

A küldendő kérés AI-hívás nélkül megnézhető: **AI-kommunikáció → Küldendő prompt előnézete**,
az `/ai-log` oldal ugyanilyen gombja, vagy a CLI `--show-prompt` kapcsolója.

## Mit vár vissza?

````text
```java
// SERVICE
<a teljes átírt metódus, utána az esetleges privát segédmetódusok>
```
```java
// REPOSITORY LookupAppAccountRepository
<csak a repository új public metódusai, az entityManager mezővel>
```
MISSING:            (csak ha tényleg hiányzik valami, pl. egy hívott adatbázis-csomag forrása)
- <mi hiányzik>
NOTES:              (opcionális, legfeljebb 3 rövid magyar sor)
````

Ha a kód minden SQL-jének van generált metódusa, a válaszforma csak a `// SERVICE` blokk: repository-kódot nem kér.
`// REPOSITORY` blokk csak a generált metódus nélküli SQL-hez kell.

A feldolgozó ennél engedékenyebb, mert a kis modellek nem mindig követik betűre a formát:

| Az AI ezt adja | A migrátor ezt teszi |
| --- | --- |
| teljes `LookupServiceImpl` osztály | csak a kiválasztott metódust és az új privát segédmetódusokat veszi át; a többi végpont nem változik |
| teljes repository-osztály saját `em` mezővel és konstruktorral | a metódusokat veszi át, `em` → `entityManager`, a konstruktort elhagyja |
| csak a metódustörzs, címke nélkül | a log1x-keretbe teszi |
| címsor (`### LookupAppAccountRepository.java`) a `//` címke helyett | ugyanúgy besorolja |
| `<think>…</think>` | a gondolkodást külön mutatja, a kódot feldolgozza |
| lezáratlan utolsó kódblokk | átveszi, ha a zárójelek zárnak; különben „megszakadt válasz” |
| az eredeti Forms-komment másolata, `MODULE_REVIEWED`-kapu | elhagyja; a migrátor a meglévő metódus kommentjét és a saját kapuját teszi be |
| meglévő repository-metódus ugyanúgy | a meglévőt használja |
| meglévő metódusnév más tartalommal | átnevezi (`onCtrlRunFindAccount`), a hívást is |
| régi JSON-válasz | ugyanígy ellenőrzi |
| JPQL-szöveg | figyelmeztet (a projekt Criteria API-t ír elő) |

Javítást kér (a második hívásban az előző válaszával együtt), ha a service olyan repository-metódust hív,
amit a válasz nem tartalmaz; Spring Data interfészt vagy nem létező repositoryt használ; EntityManagert
használ a service-ben; benne maradt a PL/SQL-hívás vagy a `NOT_IMPLEMENTED` helyőrző; `request.getBlocks()`-t
ír; natív SQL-t/JDBC-t használ; vagy a Java-kód nem szintaktikus (a hibás sorral együtt).

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

1. Válaszd ki a generált modult és a végpontot. A felület a kiválasztott trigger
   tábláit mutatja; a `DUAL/SYS.DUAL` nem kér felhasználói entityt.
2. Importáld az oszlopokat CSV-fájlból vagy beillesztett szövegből, jelöld az adatbázis
   tényleges PK-ját, és kattints a **Táblaszerkezet mentése** gombra.
3. Kattints a **Trigger javásítása** gombra. Futás közben az AI-kommunikáció rész már mutatja
   az elküldött kérést.
4. Nézd át a visszakapott kódot. Alapból a DPS ServiceImpl teljes fájlja látszik; a fájlválasztóban
   a repository, entity, DTO és a diffek is elérhetők. A feldolgozási figyelmeztetések a javaslat alatt vannak.
5. **Beillesztés a fájlokba**: a service és a hozzá tartozó fájlok együtt frissülnek.
   Töltsd le a **Friss modul ZIP** csomagot, és fordítsd a céges projektben.

## A visszakapott kód

A service megtartja a meglévő metódus aláírását, log1x/tranzakciós keretét és az eredeti
Forms-kommentet. Az üzleti logika a service-be, az adatbázis-műveletek a saját Spring repositoryba
kerülnek: a generált metódusok (Criteria API, persist, szekvencia), és ha kellett, az AI által a lefedetlen
SQL-hez írt metódusok (EntityManager: find/persist/merge/remove vagy Criteria API). Az importokat a migrátor
teszi be a használt JDK/Spring/JPA-típusokhoz (a service-be a `NoResultException`-t is).

Az entityk oszlopai és kulcsai a megadott adatbázis-metaadatból készülnek. Összetett PK esetén
IdClass is készül. A NUMBER BigDecimal marad. A repositoryk Spring-bekötését a migrátor teszi a service-be.
A Controller, az URL, a service-interface és a meglévő request/result DTO API-ja megmarad.

## Ha az AI adatot kér

Ha a válaszban `MISSING:` lista van, a javaslat állapota „További adat szükséges”: a hiányzó elemek
és az elkészült vázlat látszik, letölthető, de nem illeszthető be. Add meg a hiányzó rutint vagy céges
API-t a **Kiegészítő forrás (ha az AI kéri)** részben, és futtasd újra.

## Ha nem jön használható javaslat

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

A CLI is írja az AI-naplót (`MODULMAPPA-ai-log/`), és kiírja a helyét. Az alapértelmezés JPA;
a korábbi inline/jdbc kapcsolók kompatibilitási okból megmaradtak, ugyanazzal a válaszformával.
