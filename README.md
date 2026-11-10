# Forms migrátor 4.35 — keret + eredeti Forms-kód és tippek, minden metódust a helyi AI ír

A szokásos FMB/XML → Angular + Java generálás után a szerveroldali Oracle-triggereket
a helyi AI (Ollama, pl. qwen-coder) írja át Java-ra, a már elkészült DPS ServiceImpl metódusaiban.

**Mit generál a migrátor?** A teljes frontendet és a backend keretét: végpontok, DTO-k,
controller, `log1x`, tranzakció. A DPS ServiceImpl **minden metódusát az AI írja**, a migrátor
determinisztikus tippjei alapján:
- a gombok és a rekordtriggerek (POST-QUERY, PRE-INSERT, WHEN-VALIDATE-ITEM … privát hookként)
  metódusába kikommentezve az eredeti Forms-kód kerül, mellé az SQL-jük natív SQL-ként, nevesített
  paraméterekkel;
- a blokkok adatvégpontjaiba (lista, keresés, új rekord, módosítás, törlés) és a LOV-okba az
  `AI-feladat` megjegyzés kerül: a Forms által futtatott SQL (lekérdezés, `FOR UPDATE NOWAIT`
  zárolás, INSERT/UPDATE/DELETE), az oszlop → mező megfeleltetés, a triggerek Forms-sorrendje és a
  visszatérési érték.

Az AI mindent a service-ben ír meg, ugyanazzal az SQL-lel és mentéssel, mint a Forms: az
`entityManager` natív lekérdezéseivel, repository és JDBC nélkül. A táblák JPA entityjét és
**üres** repositoryját a migrátor készíti, ide a fejlesztő áthelyezheti a lekérdezéseket.
(A régi, determinisztikus fordítás: `"backend_trigger_mode": "plsql"` vagy `"java"`.)

**Mikor indul az AI?** Magától: a migrálás végén a backend sorba állítja a modul összes
metódusát. Táblaszerkezetre nem kell várni, mert az AI az eredeti SQL-t futtatja. Egyszerre egy kérés fut,
hogy a laptop ne terhelődjön túl. A szintaktikailag ellenőrzött javaslat automatikusan bekerül a
fájlokba (az eredeti és a diff megmarad); mindkét automatizmus kikapcsolható.

**Mit látok közben?** Élő kártyát: mióta fut, épp a promptot olvassa, gondolkodik vagy ír,
mennyi idő telt el az első tokenig, hány token jött és milyen sebességgel, a válasz utolsó sorait.
A futás leállítható. Minden prompt és nyers válasz az AI-naplóban marad
(`http://localhost:8000/ai-log`, újrafordítás nélkül is). Részletek: [docs/JAVA_ASSISTANT_HU.md](docs/JAVA_ASSISTANT_HU.md).

Az Ollama-kapcsolat mentése ellenőrzi a szervert és a modell nevét a migrátor PC-jéről.
Alapcím kell, például `http://192.168.1.50:11434`; az `/api/chat` útvonalat a program adja hozzá.

## Használat

1. Indítsd a migrátort: `python -m frm_forms.web --port 8000`.
   Nyisd meg a `http://localhost:8000` címet, a közös AI-beállításoknál add meg
   az Ollama laptopjának címét porttal és a modell pontos nevét, majd mentsd a kapcsolatot.
2. Generálj modult. A végén az AI-munkasor magától elindul a modul összes metódusával;
   a fejléc AI-jelzője mutatja, mi fut.
3. Az **Adatbázistáblák** panelen add meg a táblák oszlopait (CSV-ből), jelöld a valódi PK-t, és
   mentsd: ebből készül a tábla JPA entityje és üres repositoryja. Az AI-feladatokat ez nem tartja vissza.
4. A kész triggerek automatikusan bekerülnek a fájlokba; ellenőrizd a diffet, majd töltsd le a
   **Friss modul ZIP** csomagot. (Kézi mód: kapcsold ki az automatikus beillesztést, és használd a
   **Trigger javásítása** → **Beillesztés a fájlokba** gombokat.)

A felület két fő része: **Adatbázistáblák** és **Trigger javásítása**.
Nem kell promptot írni, adatelérési módot választani vagy előbb frontendbemeneteket
bekötni. A képernyőértékek bekötése külön, összecsukott haladó eszköz.

## Mit készít az AI-val együtt?

- A DPS ServiceImpl minden AI-ra váró metódusa üres: fölötte egy segítő sor, benne a megjegyzés
  (az eredeti Forms-kód és a tippek). A migrátor nem kezdi el a törzset (nincs helyőrző, nincs félig
  lefordított kód), és nincs `MODULE_REVIEWED` kapcsoló: a fejlesztő dönti el, mikor kész a modul.
- A tippek csak a lényeget tartalmazzák: az SQL-t (natív SQL, `:mező` paraméterekkel és azok
  forrásával), a sor → DTO-mező megfeleltetést, a hívandó trigger-hookokat Forms-sorrendben, a
  mezőellenőrzéseket (kötelező, hossz) és a visszatérési értéket.
- Az AI a service `entityManager` mezőjén keresztül futtatja az SQL-t
  (`createNativeQuery(...).setParameter(...)`). Az oszlopértékeket a `SqlValues.text/number/dateTime`,
  az Oracle-hibákat a `FormsErrors.translate` alakítja át. Az adatbázis-rutinok
  (`PKG.PROC`, `PKG.FUNC`) az Oracle-ben futnak tovább, a hívásuk módját az igazolt
  szignatúrából a prompt adja meg.
- A migrátor a táblák JPA entityjét (Domain) a blokkból, illetve az igazolt táblaszerkezetből
  készíti. Mellé **üres** `JpaRepository` kerül. A service nem használja őket, a fejlesztő ide
  helyezheti át a lekérdezéseket, például `@Query(value = "...", nativeQuery = true)` metódusként.
- A `commitForm` (Forms COMMIT_FORM) determinisztikus marad: csak a blokkok végpontjait hívja
  Forms-sorrendben, saját SQL-je nincs.
- Opcionálisan a generált képernyő is elküldhető az AI-nak (standalone komponens, signalok, olvasható,
  kommentezett kód, ugyanazzal a működéssel). A teljes fájl csak akkor, ha belefér a kontextusba; ha nem,
  részenként (a fej a signalokkal, utána a metódusok). Sosem automatikus, beillesztés csak átnézés után.
- A migrátor azt is ellenőrzi a válaszon, amit a fordító nem lát: minden SQL-paraméter kap-e értéket, benne van-e
  változatlanul a megjegyzés SQL-je, és meghívja-e a megnevezett triggereket a megadott sorrendben. Hiba esetén
  pontos javítási kérés megy vissza a modellnek. A natív lekérdezések paramétereit null-biztosra javítja
  (`SqlValues.bind`), mert a típus nélküli null az Oracle-nél NUMBER és DATE oszlopon hibát adna.
- Az AI-válasz szokásos apró hibáit (`request.getBlocks()`, `row.getX()`) a migrátor helyben javítja,
  nem kér érte újabb AI-hívást. Ha az utolsó válaszban is marad hiba, az hibás vázlatként megtekinthető marad.
- Java csak adatbázismunkából lesz: az indítókód (PRE-FORM, WHEN-NEW-FORM-INSTANCE) és az
  adatbázismunka nélküli gombok a képernyő feladatai, nem kapnak Java-végpontot.
- A már létező Controller, végpont, service-aláírás és request/result DTO-szerződés megmarad.

Az AI a kiválasztott Java-metódust kapja a megjegyzéseivel együtt, plusz rövid szabályokat.
A megjegyzést nem kell visszaküldenie, a migrátor a meglévő metódusból megtartja. Ha fontos
külső rutin vagy céges API hiányzik, a `MISSING:` listában konkrétan kéri; a **Kiegészítő forrás**
részben lehet megadni. A Java-formázás és az importok rendezése helyben fut, AI-hívás nélkül.
(A korábbi, repositoryba író mód parancssorból még elérhető: `backend-assist --data-access jpa`.)

## Beillesztés a céges projektbe

A JPA entityk és az üres repositoryk a modul DPS-csomagjában vannak. A service az
`EntityManager`-t használja (`@PersistenceContext`), így a célprojektben JPA/Hibernate szükséges.
A Java 11-es alapbeállítás `javax.persistence`; Jakarta projekt esetén a generálási
configban `"jpa_namespace": "jakarta"` adható meg.

A meglévő naplózási és tranzakciós keret megmarad. A generált kódot a céges
projektben fordítani és az eredeti működéssel tesztelni kell.

A kész webfelület benne van a csomagban; normál használathoz nem kell npm.
**4.35:** a csomagban lévő kész felület (`web-dist`) még a 4.33.3-as Angular-forrásból
készült. Az új AI-munkasorhoz, a reszponzív nézetekhez és a frontend AI-átírás paneljéhez fordítsd újra: `npm ci`,
majd `npm run build:ui` (Node.js 22.22.3+ vagy 24.15+ kell az Angular CLI-hez).
Addig a `http://localhost:8000/ai-log` oldal mutatja és vezérli az AI-sort (indítás, leállítás,
automatizmusok), ezt a Python backend szolgálja ki. Az automatikus futás és beillesztés a backendben
történik, tehát a régi felülettel is működik.
Saját Angular-projektben az `angular-frontend/migrator-app.ts` és
`angular-frontend/migrator-assistant.ts` és `angular-frontend/migrator-ai-queue.ts` fájlok használhatók együtt.

- [Egyszerű munkamenet](docs/MIGRALASI_MUNKAMENET_HU.md)
- [Java asszisztens részletek](docs/JAVA_ASSISTANT_HU.md)
- [Frontend AI-átírás (opcionális): standalone, signalok, kommentezett kód](docs/FRONTEND_AI_HU.md)
- [CSV-import és SQL-exportminta](docs/CSV_TABLASZERKEZET_HU.md)
- [4.35 ellenőrzései](docs/VALIDATION_4_35.md)
- [Mit tartalmaz az XML-export, és mi kell még?](docs/XML_TELJESSEG_HU.md)
- [4.34 ellenőrzései](docs/VALIDATION_4_34.md)
- [4.33.3 ellenőrzései](docs/VALIDATION_4_33_3.md)
- [4.33 korábbi ellenőrzései](docs/VALIDATION_4_33.md)
- [Korábbi változások](MIGRATION_NOTES.md)
