# Következő változat (a 4.35 után) — javítások: pontosabb AI-tippek, típusos adatbázis-rutinok, valódi mentés, Forms-sorrend

- **Magyar tizedesvessző a UI-modellben is.** A magyar Forms Builder a számpropertyket tizedesvesszővel
  exportálja (`HighestAllowedValue="9999999999,99"`). A backend eddig is elfogadta, a UI-modell `INVALID_NUMBER`-rel
  leállt, így a CLI alapmódja a repó saját replika-űrlapját is elutasította. Most mindkettő ugyanazt az olvasót
  használja (`common.nls_decimal`); ezres elválasztót továbbra sem találgat.
- **Java asszisztens: tippek csak az eredeti Forms-kódból.** Az `original_code()` a migrátor saját megjegyzéseit is
  beolvasta, ezért minden rekordhook promptja `MESSAGE`-tippet kapott. A tippek és a mezősorok most a feladat
  Forms-forrásaiból jönnek, a PL/SQL-megjegyzések nélkül.
- **Pontos Oracle-formátumminták.** A „`TO_CHAR(date, fmt)`: ugyanaz a minta” tipp hibás volt (Oracle `MI`/`DD`/`YYYY`
  = Java `mm`/`dd`/`yyyy`). Az `oracle_formats` a kód minden literál formátummintáját a pontos `java.time`-mintára
  fordítja (`TO_CHAR`, `TO_DATE` az Oracle 1–2 jegyes engedékenységével), és megmondja, ha nincs megfelelő (NLS-nevek,
  AM/PM, RR, hétszám) vagy ha számformátum. Új tipp: `a || b` → `SqlValues.concat` (Oracle-ben a NULL kimarad,
  Javában `"a" + null = "anull"`).
- **Adatbázis-rutinok típusos metódusként.** A Forms-kód tárolt eljárás- és függvényhívásai (pl. PRE-INSERT
  `kod_pkg.check_insert(:B.KOD, :B.DATUM, :B.NEV)`) a schema.json `procedures` aláírásából generált, típusos
  metódust kapnak (pl. `String kodPkgCheckInsert(String pKod, LocalDateTime pDatum)`; pozíciós vagy nevesített
  paraméterek, a kihagyottaknál az adatbázis alapértéke, OUT/IN OUT érték visszatérési értékként vagy kis
  holder osztályban). A prompt ezeket kínálja egyetlen megengedett PL/SQL-hívásként, és megnevezi az aláírás
  nélküli rutinokat (MISSING, sosem újraírás). A beillesztés a hívott metódusokat is hozzáadja (`DbCalls` a
  ServiceImpl `NamedParameterJdbcTemplate`-jén); ha a modell maga írja meg, javítást kér.
- **Zárolás várakozás nélkül.** A rekord zárolása `FOR UPDATE NOWAIT`: ha más munkamenet tartja (ORA-00054,
  ORA-30006), HTTP 409 „A rekordot most más felhasználó zárolja; próbáld újra később.”, mint Formsban — a kérés és a
  kapcsolat nem vár korlátlanul. `CommonMigrateTools.VERSION` = 6: a projekt vegye át az új példányt.
- **Nincs csendes levágás 200 sornál.** A lista-, kereső- és lekérdezőgomb-végpontok eggyel több sort kérnek,
  és a `PageResult.hasMore` jelzi, ha a lap nem fért el (a POST-QUERY a plusz sort nem látja). A képernyő szól
  (`migrationMoreRows`); az űrlapblokk egyetlen rekordot kér (`limit: 1`), és szól, ha több is illeszkedett.
- **Valódi mentés.** A `save()` eddig üres változáslistákat küldött. Most a lekérdezés az űrlap rekordját
  teljesen beírja (`reset`) és megjegyzi (`migrationLoaded`); a mentés blokkonként módosításként küldi, ha a
  képernyő eltér tőle (`updated: [{ original, value }]`), lekérdezés nélkül új rekordként, ha a felhasználó beírt
  valamit. A jelölőnégyzet a képernyőn `true/false`, a rekordban a Forms-érték; a dátum a képernyőn `Date`, a
  rekordban helyi idő másodpercre; az üres szöveg `null`. Mentés után a képernyő a mentett, újraolvasott rekordot
  mutatja (a generált kulccsal), az üzenetek és utasítások az `applyActionResult`-ba mennek. A kérés a mentési
  lánc által olvasott képernyőértékeket is viszi (új detail rekord master-kulcsa, PRE-COMMIT kötései). Törlést a
  képernyő nem küld: a `DELETE_RECORD` megfelelője fejlesztői feladat.
- **Az indítókódból csak az adatbázismunka megy a szerverre.** A PRE-FORM és a WHEN-NEW-FORM-INSTANCE eddig
  egészében az AI-ra várt (`onFormInit`), keretrendszer-hívással, kurzorbeállítással, statikus értéklistával együtt.
  Most utasításonként válogat (`frm_forms/startup.py`): az állandó értékadás és a konstans mezőtulajdonság a
  képernyő kezdőállapota (`startValue`, `disabled`, `readonly`), az állandó `:GLOBAL` az az érték, amelyet a
  képernyő a kéréseiben küld, a `GO_BLOCK` + `EXECUTE_QUERY` a konstruktor lekérdezése; a `qms$…`, a
  `SET_APPLICATION_PROPERTY` és az `ALTER SESSION` kimarad; a memóriabeli értéklista és a csomagváltozó a
  képernyő MIGRATION_NOTES.md-jébe kerül. Az AI csak az SQL-t, az adatbázis-rutinokat és a biztosan el nem
  dönthető részt kapja; ha nincs ilyen, nincs `onFormInit` sem.
- **A céges Forms-felületkönyvtárak kimaradnak.** Az `ank_ctrl` (képernyőcím, súgósor) és az `ank_menu` (menü)
  rutinjai csak az Oracle Forms futtatókörnyezetben működnek: a keretrendszer-katalógus `forms_runtime_calls`
  listáján vannak, így sem a backendbe, sem az AI-feladatokba nem kerülnek, és a csatolt könyvtárból sem
  töltődnek be. Az adatbázist érintő céges rutinok (pl. `naplo_init`, `ank_jog.irhat`) maradnak.
- **Forms-sorrend a validációban.** A mező saját ellenőrzései (kötelező, hossz, tartomány, listaérték) a
  WHEN-VALIDATE-ITEM/RECORD és a PRE-INSERT/UPDATE előtt futnak, ahogy Formsban; a triggerek által írt értékeket
  utána újra ellenőrzi.
- **Tesztek és CI.** A `tests/java_support.py` importáláskor `plsql`-re állította a `FRM_BACKEND_TRIGGER_MODE`-ot,
  így az eredmény a futtatási sorrendtől függött; most minden modul kimondja a saját módját és visszaállítja a
  környezetet. Az elavult elvárások frissítve, új futtatásos tesztek (Java: lapozás, zárolás, validációs sorrend;
  Node: a mentés változáslistái), GitHub Actions munkafolyamat (`.github/workflows/tests.yml`: Python 3.13, JDK 21,
  Node 22, TypeScript). A tesztek egy része az `examples/` mappát használja, amely nincs a repóban: amíg nem kerül
  be, ezek hibával állnak le.

# 4.35 — Minden szerveroldali trigger az AI-é, automatikus és élő AI-sor

- **Új alapértelmezés: `backend_trigger_mode: "ai"`.** A determinisztikus generátor nem fordítja le a
  triggerek kódját (sem Java-ra, sem PL/SQL-átadásra). Elkészíti a végleges keretet — végpont, DTO,
  controller, `log1x`, `@Transactional`, a CRUD-folyamat hívási pontja —, a metódus közepére pedig a
  kikommentezett eredeti Forms-kódot teszi (`//region Eredeti Forms-kód: TULAJDONOS / ESEMÉNY`), alá egy
  `// TODO(AI)` sort és egy HTTP 501-es helyőrzőt. A régi viselkedés kérhető: `"backend_trigger_mode": "plsql"`
  vagy `"java"` a configban, illetve `FRM_BACKEND_TRIGGER_MODE` környezeti változóval.
- **Mi lesz AI-feladat:** gombok (`WHEN-BUTTON-PRESSED` → `ActionResult` végpont; a saját `DEFAULT_WHERE` +
  `EXECUTE_QUERY` lekérdező gomb → `PageResult<…Row>` a `QueryActionRequest`-tel), induló triggerek
  (`PRE-FORM`, `WHEN-NEW-FORM-INSTANCE` → `onFormInit`), a mentési lánc (`PRE-COMMIT`, `POST-FORMS-COMMIT` →
  privát hook a `commitForm`-ban), és a rekord saját triggerei (`POST-QUERY`, `PRE/POST/ON-INSERT|UPDATE|DELETE`,
  `WHEN-VALIDATE-ITEM/RECORD`, `POST-CHANGE`, `ON-CHECK-DELETE-MASTER` …) → `private void
  trigger<Esemény><Mező><Blokk>(XRow row, RuleContext context)` hook, amelyet a CRUD-folyamat a Forms-pontokon hív.
  Amíg az AI nem írta meg, a hook 501-et dob, így semmi sem fut csendben rosszul.
- **A Java asszisztens a hookokat is kínálja.** A prompt a rekord mezőit (Forms-elem → `row.mező`, típus,
  oszlop, PK), az esemény szabályát (pl. a POST-QUERY nem írhat adatbázismezőt) és csak a kódban ténylegesen
  előforduló Forms-beépítettek egysoros Java-mintáját kapja (`MESSAGE`, `RAISE FORM_TRIGGER_FAILURE`,
  `SHOW_ALERT` két hívással, `:GLOBAL`, `DEFAULT_VALUE`, `COMMIT_FORM`, `SET_ITEM_PROPERTY` …). A válasz
  „csak kód, magyarázat nélkül”. A beillesztés a regiont megtartja, a törzset cseréli.
- **Táblaszerkezet csak ott kell, ahol a kód tényleg táblát használ:** a trigger SQL-jében szereplő táblák
  (a képernyőérték nem kér entityt). A `NEXTVAL`-os `PRE-INSERT` a saját blokkja tábláját kéri.
- **Automatikus AI-sor.** Ha a migrálás kész és van mentett AI-kapcsolat, a backend sorba állítja az összes
  átírandó triggert, amelynek a táblái ismertek; a többi a táblaszerkezet mentése után indul magától.
  Egyszerre mindig egy modellkérés fut (a laptop CPU-ja nem terhelődik túl). Egy automatikusan sikertelen
  feladatot ugyanarra a bemenetre nem ismétel; az **Újrapróbálás (sikertelenek is)** gomb újraindítja.
- **Automatikus beillesztés.** A szintaktikailag ellenőrzött, `MISSING:` nélküli javaslat azonnal bekerül a
  fájlokba; az eredeti fájl mentése és a diff megmarad (`backend-assistant/proposals/<id>/`), a modul ZIP
  frissül. Kikapcsolható (Automatikus beillesztés kapcsoló, vagy `auto_apply: false`).
- **Élő állapot gondolkodás közben.** Az Ollama-hívás streamelt: eltelt idő, fázis (kapcsolódás → prompt
  feldolgozása → gondolkodik → írja a választ), az első tokenig eltelt idő, tokenek száma, tokens/s, a
  kimeneti keret kihasználtsága és a válasz utolsó sorai. A futás bármikor leállítható (a kapcsolat bontásával
  az Ollama is abbahagyja); az időkorlát a néma prompt-feldolgozás alatt is érvényes. A megszakadt válasz
  részlete az AI-naplóban marad.
- **Laptopbarát alapértékek:** `num_ctx` 16384, `num_predict` 4096, időkorlát 900 mp (legfeljebb 3600),
  `keep_alive` 5 perc. A közös beállítások a backend adatmappájában (`assistant-settings.json`) vannak.
- **Képernyő:** a gomb a backend válaszát feldolgozza (`applyActionResult`): a visszaírt mezők a
  képernyőre, az üzenetek toastba, `GO_BLOCK`+`EXECUTE_QUERY` a blokk lekérdezése, `SET_ITEM_PROPERTY …
  ENABLED` a mező tiltása/engedése, `COMMIT_FORM` a mentés, `SHOW_ALERT` kérdés és második hívás a válasszal.
- **Felület:** új AI-munkasor (fejlécjelző minden nézetben, élő kártya, sor, kész/hibás lista,
  triggerállapot-rács), reszponzív táblák és párbeszédablakok (mobilon teljes képernyő), összecsukható
  AI-beállítások. Az újrafordítás nélküli `/ai-log` oldal ugyanezt mutatja.
- Új API: `GET/POST /api/assistant/settings`, `GET /api/assistant/queue`, `POST /api/assistant/cancel`,
  `POST /api/jobs/{id}/assistant/auto`, `GET /api/jobs/{id}/assistant/queue`, `POST /api/jobs/{id}/assistant/cancel`.
- A riport új sora: „AI-ra váró triggerek (Java asszisztens)”; a `backend-plan.json` végpontjain `runs: "ai"`.
- A csomagban lévő kész Angular-felület (web-dist) ebben a változatban sem lett újrafordítva (itt nincs
  npm-hozzáférés): `npm ci` + `npm run build:ui`. Addig a `/ai-log` oldal mindent mutat és vezérel.
  Ellenőrzés: docs/VALIDATION_4_35.md, az XML teljességéről: docs/XML_TELJESSEG_HU.md.

# 4.34.1 — a Végpont lista a kikommentezett Forms-kódot hordozó metódusokat mutatja

- A Java asszisztens pontosan azokat a DPS ServiceImpl metódusokat kínálja, amelyekben még ott van a
  `//region Eredeti Forms-kód` blokk: kézi gombok, Java/PL-SQL lekérdező gombok (pl. `onCgnvW011PbLekerdezes`),
  gated CRUD. A kész, ilyen blokk nélküli metódusok (pl. `listAit`) nem jelennek meg.
- Lekérdező gombnál a célblokk táblája is kap repositoryt; nem `ActionRequest/ActionResult` metódusnál a prompt
  a meglévő visszatérési és kéréstípus megtartását kéri.

# 4.34 — AI-napló, minimális prompt és robusztus válaszfeldolgozás

- A jelentett hiba oka: a modell `needs_context` állapotot adott és közben repository-metódust is
  írt; a 4.33.3 ezt az egész választ eldobta („Hiányzó adat esetén nincs részleges repository/DTO.”),
  sehol nem mentette, és az újrapróbálás nem tartalmazta a modell előző válaszát, így ugyanaz ismétlődött.
- Az AI-nak csak a kiválasztott DPS ServiceImpl metódus megy (benne a kikommentezett Oracle-triggerrel)
  egy ```java blokkban, rövid szabálylista, és a metódusban ténylegesen hivatkozott táblák repositoryjának
  2–3 soros leírása (mezők típussal és oszloppal, PK, a meglévő repository-metódusok szignatúrája).
  Megszűnt a JSON-csomagolás, az entityk teljes forrása és a JSON-séma (format). A mintamodulokon a kérés
  6,4 KB → 3,8 KB, illetve 8,1 KB → 5,2 KB. Széles táblánál csak a PK és a hivatkozott oszlopok mennek.
- A modell Markdown kódblokkokban válaszol: `// SERVICE` (az átírt metódus és privát segédmetódusai),
  `// REPOSITORY <Név>` (csak új metódusok), opcionálisan `MISSING:` és `NOTES:`. Nincs JSON-escape-elt kód,
  nincs kötelező teszteset-lista; a kikommentezett eredeti kódot nem kell visszaküldenie, a migrátor megtartja.
- Új válaszfeldolgozó (`assistant_reply.py`): teljes osztályokat, csak metódustörzset, címke helyett
  címsort, kódblokk nélküli választ, `<think>` részt, lezáratlan utolsó blokkot és a régi JSON-választ is
  elfogad. A saját `em` EntityManager-mezőt `entityManager`-re igazítja, konstruktort és fölösleges mezőt
  elhagy, a meglévő metódussal ütköző nevet a hívásokkal együtt átnevezi, a JDK/JPA-típusokat importálja.
- Hiányzó forrás (`MISSING:`) esetén a vázlat megtekinthető és letölthető, a hiányzó elemek látszanak,
  de beilleszteni nem lehet. A modulkaput (`MODULE_REVIEWED`) és az eredeti Forms-kommentet a migrátor kezeli.
- Pontos javítási kérés: a második hívás tartalmazza a modell előző válaszát és a konkrét hibát angolul
  (nem definiált repository-metódus, Spring Data interfész, ismeretlen repository, `getBlocks()`,
  EntityManager a service-ben, PL/SQL-hívás maradt, Java-szintaktikai hiba sorral). Kimeneti keretnél
  megszakadt válasz után nincs újabb, ugyanúgy megszakadó hívás; a hiba a num_predict növelését javasolja.
- AI-napló (`assistant_log.py`): minden futás menti a küldött system/user promptot, minden nyers választ
  (az elutasítottat is), a gondolkodási szöveget, az Ollama tokenszámait és hogy mit vett át a migrátor.
  A fájlok a modulmappa mellett vannak (`<modul>-ai-log/`), a modul ZIP-jébe nem kerülnek.
- Megjelenítés: Angular **AI-kommunikáció** rész (prompt-előnézet, korábbi hívások, nyers válasz, átvett
  blokkok) és újrafordítás nélkül is működő `http://localhost:8000/ai-log` oldal; a hibaüzenet a linket is
  tartalmazza. Új API: `POST .../assistant/prompt`, `GET .../assistant/exchanges[/{id}]`.
  CLI: `backend-assist MODUL --task ID --show-prompt`.
- JPQL-szöveges lekérdezés csak figyelmeztetés; szekvencia (`SELECT X.NEXTVAL FROM DUAL`) engedélyezett
  natív lekérdezés; minden más natív SQL/JDBC elutasítva. A `TODO` megjegyzés figyelmeztetés, a
  `NOT_IMPLEMENTED`/`UnsupportedOperationException` helyőrző hiba.
- A csomagban lévő kész Angular-felület (web-dist) ebben a változatban nem lett újrafordítva; az új panelhez
  `npm ci` + `npm run build:ui` kell, addig az `/ai-log` oldal mutat mindent. Ellenőrzés: docs/VALIDATION_4_34.md.

# 4.33.3 — automatikus AI-segítség, lapozott szerkesztők és Ollama-diagnosztika

- Eltűnt az „AI segítség generáláskor” módválasztó. A webfelület mindig assist
  módot és legalább egy elemzési kérésnyi keretet küld, régi mentett off/0 mellett is.
  A közös Ollama-cím kötelező. A trigger tényleges átírása a Java asszisztensben történik.
- Az összecsukott haladó bemenetpanel nem hozza létre a mezőit. A séma- és
  bemeneti táblázat tízesével lapozható; minden adat a vázlatban marad.
  Oszlopmódosítás és törlés stabil azonosítót használ, lapozás után is.
- Az AI-kapcsolat mentése a Python backend felől ellenőrzi az Ollama modelllistáját.
  A visszajelzés megkülönbözteti a hiányzó modellt és a hibás szerver-/útvonalválaszt.
- Teljes /api/chat, /api/generate vagy /api végpont beillesztésénél alapcímre
  normalizálás; a proxy előtagja és a port megmarad, nincs dupla /api/chat.
- HTTP-hibánál látható a tényleges kért végpont. Port nélküli 404 esetén 11434-es
  tipp is megjelenik. A modellkiszolgáló saját JSON-hibaüzenete megmarad.
- Fejlesztői Angular-böngészőteszt 600 további bemeneti sorral és 200 oszlopos CSV-vel,
  kapcsolatpróba és a teljes javaslat-/beillesztési folyamat. Részletek: docs/VALIDATION_4_33_3.md.

# 4.33.2 — a kiválasztott metódus és entityk az AI-nak

- Az AI automatikus bemenete a kiválasztott DPS ServiceImpl teljes metódusa,
  benne a kikommentezett Oracle-forrással, és az érintett entityk teljes Java-kódja.
- Nem kerül mellé a modul teljes DTO-/XML-/képernyőleltára, hívásgráfja vagy
  megismételt séma- és triggerforrás. A felhasználó külön megadott forrása megmarad.
- A prompt előírja a meglévő végpont megtartását, a metódustörzs átírását és az
  entity-alapú repository-műveletek elkészítését. Hiányzó üzleti függőséget az AI kérhet.
- Megszűnt a teljes belső feladatcsomag 160 KB-os korlátja. A tényleges AI-bemenet
  és a kimeneti keret becsült tokenigényét ellenőrzi; a forrást nem vágja le.
- Frissített kész Angular-felület és útmutatók. Ellenőrzés: docs/VALIDATION_4_33_2.md.

# 4.33.1 — reaktív mezőletiltás és látható AI-hibák

- A modulválasztó reaktív FormControl mezőjét disable/enable vezérli emitEvent=false
  mellett; megszűnt a sablonbeli disabled + formControl figyelmeztetés. A választó
  futás és mentetlen vázlat közben ténylegesen le van tiltva.
- A hiba közvetlenül a Trigger javásítása gomb mellett, aria-live/alert jelzéssel
  megjelenik, és a konzolba is bekerül a tényleges üzenet.
- HTTP 422 esetén a FastAPI/Pydantic mezőszintű hibák is olvashatók.
  Háttérfeladat-hiba és oldalfrissítés után visszatöltött hiba is megjelenik.
- Üres kivételszövegnél a backend a kivétel típusát adja vissza és naplózza.
- Az Ollama elutasított HTTP-kérésének JSON error üzenete megmarad,
  például a hiányzó modell neve; nincs automatikus ismétlés.
- A 4.33 egyszerű, entity-alapú folyamata megmaradt. Az eredeti felhasználói
  AI-leállás oka a beküldött Angular-figyelmeztetésből nem volt azonosítható.
- Ellenőrzés: docs/VALIDATION_4_33_1.md.

# 4.33 — egyszerű triggerjavásítás, entitykkel és repositorykkal

- Két fő panel: a kiválasztott trigger adatbázistáblái és a Trigger javásítása.
- A képernyőértékek bekötése opcionális, összecsukott haladó részbe került;
  a Java-átírásnak nincs külön frontend-bekötési előfeltétele.
- Az alapfolyamat JPA entityt és saját Spring repositoryt készít táblánként.
  A valódi oszlopok és PK alapján determinisztikus leképezés, összetett kulcsnál IdClass készül.
- Az AI a repository metódusait és a meglévő service-metódus üzleti törzsét adja,
  szükség esetén külön DTO-mezőkkel. Adatbázis-elérés az entitykre épülő repositoryban,
  EntityManager/Criteria API-val; nincs új JDBC/SQL/PLSQL-változat az alapfolyamatban.
- A Controller, az URL, a service-interface és a meglévő request/result szerződés megmarad.
  A repository Spring-bekötését a migrátor illeszti be a ServiceImpl-be.
- A már meglevő repository-metódusok megőrzése és újrahasználata, kiegészítő DTO-k kezelése.
  A javaslat minden érintett fájllal együtt kerül átvételre, módosulásellenőrzéssel.
- Egyszerű Beillesztés a fájlokba gomb; a teljes service fájl az alap előnézet.
  A részletes teszt-/formázási jelentések és a futtatási beállítások összecsukhatók.
- A teljes promptot a backend adja. Az alap kontextus 32768, kimenet 8192 token.
  Csak a kiválasztott trigger táblái és persistence API-jai kerülnek az AI kontextusába.
- Java 11-hez javax.persistence az alap; jpa_namespace=jakarta is beállítható.
- A 4.32.1 CSV-import javítása megmaradt. Friss Angular production build és egyszerűsített útmutatók.
- Ellenőrzés: docs/VALIDATION_4_33.md.

# 4.32.1 — fejléc nélküli, nyolcoszlopos CSV javítása

- A felület SQL-mintájának 8 oszlopa (a végén CHAR_LENGTH, CHAR_USED) fejléc nélkül
  is importálható. A korábbi 6 oszlopos és a fejléces, átrendezett export is működik.
- A felhasználó pontos sora: `OSZLOP_NEVE,VARCHAR2,40,,,N,40,B` → VARCHAR2, 40 BYTE,
  nullable=false. A CHAR hossz külön megmarad; NUMBER esetén a 22 nem lesz precision.
- A hibás CHAR_LENGTH/CHAR_USED és az eltérő mezőszámú sorok teljes egészében
  elutasításra kerülnek; a mentett szerkezet nem változik.
- A felület és az útmutató is jelzi az opcionális fejlécet. A kész Angular-build frissült.
- Ellenőrzés: docs/VALIDATION_4_32_1.md.

# 4.32 — IntelliJ CSV, DUAL és automatikus AI-feladat

- A táblaszerkezet-szerkesztő CSV-fájlból és beillesztett szövegből is kitölthető.
  A hatoszlopos, fejléc nélküli IntelliJ-export mellett fejléces, bővített CHAR/BYTE
  metaadat is támogatott. Típusellenőrzés után vázlat készül; mentés külön gombbal.
- NUMBER storage length nem lesz mezőhossz/precision. A PK-t nem következteti nullable-ből.
  Nem egyértelmű karakterhossznál jelzés vagy célzott adatbekérés marad.
- DUAL és SYS.DUAL nem szerepel hiányzó sémafüggőségként és nem kap repositoryt.
- A backend minden AI-kérésbe teljes átültetési feladatot tesz. A kötelező
  feladatíró mező megszűnt; a hiányzó forrásokhoz továbbra is van Projektminta.
- A bemenetpanel neve: „A triggerhez szükséges értékek forrása”. Konkrét példákkal,
  állapotösszesítővel és közérthető választásokkal magyarázza az adatátadást.
- A kész webfelület ugyanebből a frissített Angular-forrásból készült.
- Használat: docs/CSV_TABLASZERKEZET_HU.md. Ellenőrzés: docs/VALIDATION_4_32.md.

# 4.31 — az asszisztens beépítése az Angular migrátorba

- Egy Angular-alkalmazás, három fül: Generálás, Feladatok, Java asszisztens.
- Közös Ollama alapcím és modell a felület tetején, böngészőben menthető beállítással.
  A generálási AI-mód és a végpontonként indított Java-javaslat ugyanazt a kapcsolatot használja.
- A kész modul részleteiből és a feladatlistából közvetlen átmenet az asszisztenshez.
- Az asszisztensben teljes táblaszerkesztő, bemeneti bekötések, futásjelzés,
  hiányzó kontextus, korábbi javaslatok, diff, átnézett átvétel és friss ZIP-letöltések.
- Tabváltáskor megmaradó vázlatok; modul-/API-váltás és mentetlen vázlatok védelme.
- Aktív AI-futás visszatöltése oldalfrissítés után a Python backend állapotából.
- A korábbi külön HTML/JavaScript-felület megszűnt; a `/assistant` könyvjelző
  az Angular-felület megfelelő fülére irányítja át a modult is megőrizve.
- A kész webes build most az `angular-frontend` komponensekből készül.
  Visszaállított, zárolt függőségekkel újrafordítható npm/Angular workspace.
- A Python backend, a generátor és az AI-kódátvétel ellenőrzési szabályai megmaradtak.

Útmutató: [docs/MIGRALASI_MUNKAMENET_HU.md](docs/MIGRALASI_MUNKAMENET_HU.md).

# 4.30 — determinisztikus Java-formázás és frontend–backend bemenetek

- Az AI-javaslat teljes Java-fájlja és opcionális JDBC segédei a helyi import-
  és formázófolyamaton mennek át. Nincs formázási javítóprompt; bizonytalan
  esetben az eredeti javaslat marad és kézi formázási jelzés készül.
- A meglévő csomagnév és explicit projektimport megmarad. A formázó saját
  beállításállapottal fut; a generálás globális konfigurációját nem módosítja.
- Az előnézet, a diff, a letöltés és az átvétel ugyanazt a formázott eredményt használja.
- Új **Frontend–backend bemenetek** panel a Java asszisztens oldalán. Képernyőmező,
  modulparaméter vagy kézi/szerveroldali megjegyzés rendelhető a felismert bemenethez.
- Az automatikus bekötések valóban módosítják a generált kérés értékeit. A DTO
  szöveges értékeihez pontos decimális szöveg, Forms checkbox-kód és helyi dátum használható.
- Külön állapot a hiányzó érték, a még átadandó modulparaméter, a hívó kód nélküli
  végpont, a bizonytalan bemenetirány és a konstruktorban futó indítás.
- A SYSTEM és szerveroldali lokális változók kézi bekötési feladatok. A panelbe
  írt megjegyzés önmagában nem valósítja meg a szerveroldali kódot.
- A mentett bekötések bekerülnek az AI kontextusába, exportálhatók és webes
  újrafuttatáskor megmaradnak. Mentéshez nem kell Ollama.
- Elavult revízió, kézzel módosított komponens és újrageneráláskor változó
  bemeneti szerződés esetén nincs automatikus felülírás. Az eredeti kódról másolat marad.

Használat: [docs/INPUT_BINDINGS_HU.md](docs/INPUT_BINDINGS_HU.md).
Ellenőrzések: [docs/VALIDATION_4_30.md](docs/VALIDATION_4_30.md).

# 4.29 — triggerből felismert táblák és táblaszerkezet-bekérő

- A csak triggerek/helyi programegységek statikus SQL-jében szereplő táblák is
  bekerülnek a sémabekérőbe és a dictionary-exportba.
- Az asszisztensoldalon új **Adatbázistáblák** panel. Ismeretlen táblánál üres
  szerkesztő, hozzáadható/törölhető oszlopsorok, explicit Oracle-típus és nullable.
- Hossz, BYTE/CHAR, precision/scale, összetett PK, alapérték és megjegyzés.
  A PK/nullable ellentmondást a szerver elutasítja.
- Forrás és sorszám a hivatkozásoknál; CTE-aliasok, al-lekérdezések és normál
  táblák megkülönböztetése. Komment/szövegliterál nem lesz táblabizonyíték.
  Dinamikus, távoli és idézőjeles objektumok külön diagnózist kapnak.
- A hiányos táblát használó Java-feladat AI-hívás előtt megáll. A mentett
  szerkezet és az igazolható oszlophivatkozások a modell kontextusába kerülnek.
- Triggerből ismert tábla is kaphat opcionális JDBC POJO/repository javaslatot.
- schema.json export; webes újrafuttatás és CLI újragenerálás után megmaradó
  kézi metaadat. Új import és korábbi kézi definíció ütközése külön javítandó állapot.
- Verziólenyomat védi a párhuzamos szerkesztést. Sémaváltozás után a korábbi
  AI-javaslat elavult; új javaslat kell.
- Új Python-függőség és Node-build nélkül használható kész felület.

Használat: [docs/JAVA_ASSISTANT_HU.md](docs/JAVA_ASSISTANT_HU.md).
Pontossági fejlesztési sorrend: [docs/TOVABBFEJLESZTESI_JAVASLATOK_HU.md](docs/TOVABBFEJLESZTESI_JAVASLATOK_HU.md).
Ellenőrzések: [docs/VALIDATION_4_29.md](docs/VALIDATION_4_29.md).

# 4.28 — Java fejlesztőasszisztens

- Új, külön helyi weboldal: `/assistant`, a meglévő kész webes felületről megnyitható.
- Végpontonkénti kontextus a triggerből, hívásláncból, DTO-kból, Java-szerződésből és sémából.
- Ollama Java-kódjavaslat, kötött JSON-válasz, cache, méret- és kéréskeret.
- Hiányzó kontextus külön állapotban; félkész siker helyett pontos kérdések.
- Teljes `.java.txt` javaslatok és diffek, letölthető review ZIP.
- Explicit, lenyomatokkal védett átvétel az akció-végpontokhoz; eredeti fájlok mentése.
- Blokkolt CRUD-végpontokhoz kézzel összefésülendő javaslatok, változatlan írási kapukkal.
- Opcionális JDBC POJO és read-only repository igazolt dictionary-metaadatból, összetett kulccsal.
- Telepített JDK-val Java 11 szintaktikai ellenőrzés; strukturális hiba javítására korlátozott újrakérés.
- `backend-assist` és `backend-apply` CLI; a szokásos generálási AI mód változatlan.
- Az új képességhez nincs új Python-függőség. A kész webes oldal nem igényel Node-buildet.

Használat: [docs/JAVA_ASSISTANT_HU.md](docs/JAVA_ASSISTANT_HU.md).

Az új tesztek a generálás → Ollama-próbaválasz → diff → átvétel → friss ZIP folyamatot,
a módosult szerződés elutasítását, a Java 11 fordítást és a megmaradó modulkaput ellenőrzik.
Részletes futási eredmények: [docs/VALIDATION_4_28.md](docs/VALIDATION_4_28.md).

Az eredeti ZIP-ből az `examples` és a `web-ui` projekt hiányzik, miközben több régi
teszt és régi dokumentum hivatkozik rájuk. Ezeket nem pótoltam kitalált üzleti mintákkal.
A korábbi teljes tesztcsomag emiatt nem zöld; az új és módosított részeket külön is ellenőriztem.
