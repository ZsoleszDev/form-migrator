# Forms migrátor 4.35 — keret + eredeti Forms-kód, a triggereket a helyi AI írja

A szokásos FMB/XML → Angular + Java generálás után a szerveroldali Oracle-triggereket
a helyi AI (Ollama, pl. qwen-coder) írja át Java-ra, a már elkészült DPS ServiceImpl metódusaiban.

**Mit generál a migrátor?** A teljes frontendet és a backend keretét: végpontok, DTO-k,
controller, `log1x`, tranzakció, CRUD. A triggerek kódját **nem fordítja le**: minden
triggerhez elkészíti a metódust (gombhoz végpontot, a rekord triggereihez — POST-QUERY,
PRE-INSERT, WHEN-VALIDATE-ITEM … — privát hookot a CRUD-folyamat megfelelő pontján), és
a közepére kikommentezve beteszi az eredeti Forms-kódot. Ebből dolgozik az AI.
(A régi, determinisztikus fordítás: `"backend_trigger_mode": "plsql"` vagy `"java"`.)

**Mikor indul az AI?** Magától: a migrálás végén a backend sorba állítja azokat a triggereket,
amelyek táblái ismertek; a többit a táblaszerkezet mentése után. Egyszerre egy kérés fut, hogy a
laptop ne terhelődjön túl. A szintaktikailag ellenőrzött javaslat automatikusan bekerül a
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
2. Generálj modult. A végén az AI-munkasor magától elindul azokkal a triggerekkel,
   amelyekhez nem kell táblaszerkezet; a fejléc AI-jelzője mutatja, mi fut.
3. A **Java asszisztens** fülön a „táblára vár” triggerekhez importáld a táblák oszlopait
   CSV-ből, jelöld a valódi PK-t, és mentsd: a hozzájuk tartozó triggerek automatikusan sorba állnak.
4. A kész triggerek automatikusan bekerülnek a fájlokba; ellenőrizd a diffet, majd töltsd le a
   **Friss modul ZIP** csomagot. (Kézi mód: kapcsold ki az automatikus beillesztést, és használd a
   **Trigger javásítása** → **Beillesztés a fájlokba** gombokat.)

A felület két fő része: **Adatbázistáblák** és **Trigger javásítása**.
Nem kell promptot írni, adatelérési módot választani vagy előbb frontendbemeneteket
bekötni. A képernyőértékek bekötése külön, összecsukott haladó eszköz.

## Mit készít az AI-val együtt?

- A migrátor az igazolt táblaszerkezetből JPA entityt készít.
- Táblánként saját Spring repository készül. Az AI a konkrét adatbázis-műveleteket
  ebben valósítja meg, az entitykkel, EntityManager/Criteria API használatával.
- A DPS ServiceImpl kiválasztott metódusa a repositorykat hívja; a migrátor
  a Spring-bekötéseket is beilleszti.
- Szükség esetén kiegészítő DTO készül. A már létező Controller, végpont,
  service-aláírás és request/result DTO-szerződés megmarad.
- Ugyanazon tábla repositoryja a következő triggernél is használható. A már
  meglévő metódusokat megőrzi, új metódusokat hozzáadhat.

Az AI a kiválasztott Java-metódust kapja, benne az eredeti Oracle-forrással, és a
metódusban hivatkozott táblák entityjeinek rövid leírását (nem a teljes Java-forrásukat).
A kikommentezett eredeti kódot nem kell visszaküldenie: a migrátor a meglévő metódusból
megtartja. Ha fontos külső rutin vagy céges API hiányzik, a `MISSING:` listában
konkrétan kéri; a **Kiegészítő forrás** részben lehet megadni. A Java-formázás és az
importok rendezése helyben fut, AI-hívás nélkül.

## Beillesztés a céges projektbe

A JPA entityk és repositoryk a modul DPS-csomagjában, a kiegészítő DTO-k a CL-ben
vannak. A célprojektben JPA/Hibernate és e csomagok felismerése szükséges.
A Java 11-es alapbeállítás `javax.persistence`; Jakarta projekt esetén a generálási
configban `"jpa_namespace": "jakarta"` adható meg.

A meglévő naplózási és tranzakciós keret megmarad. A generált kódot a céges
projektben fordítani és az eredeti működéssel tesztelni kell.

A kész webfelület benne van a csomagban; normál használathoz nem kell npm.
**4.35:** a csomagban lévő kész felület (`web-dist`) még a 4.33.3-as Angular-forrásból
készült. Az új AI-munkasorhoz és a reszponzív nézetekhez fordítsd újra: `npm ci`, majd `npm run build:ui`.
Addig a `http://localhost:8000/ai-log` oldal mutatja és vezérli az AI-sort (indítás, leállítás,
automatizmusok), ezt a Python backend szolgálja ki. Az automatikus futás és beillesztés a backendben
történik, tehát a régi felülettel is működik.
Saját Angular-projektben az `angular-frontend/migrator-app.ts` és
`angular-frontend/migrator-assistant.ts` és `angular-frontend/migrator-ai-queue.ts` fájlok használhatók együtt.

- [Egyszerű munkamenet](docs/MIGRALASI_MUNKAMENET_HU.md)
- [Java asszisztens részletek](docs/JAVA_ASSISTANT_HU.md)
- [CSV-import és SQL-exportminta](docs/CSV_TABLASZERKEZET_HU.md)
- [4.35 ellenőrzései](docs/VALIDATION_4_35.md)
- [Mit tartalmaz az XML-export, és mi kell még?](docs/XML_TELJESSEG_HU.md)
- [4.34 ellenőrzései](docs/VALIDATION_4_34.md)
- [4.33.3 ellenőrzései](docs/VALIDATION_4_33_3.md)
- [4.33 korábbi ellenőrzései](docs/VALIDATION_4_33.md)
- [Korábbi változások](MIGRATION_NOTES.md)
