# Ellenőrzés — 4.33

## Célzott regressziók

```text
PYTHONPATH=<requirements-test target>:tests:. python3 -m unittest \
  test_backend_assistant test_schema_workbench test_backend_assistant_web test_jpa_assistant -v

Ran 48 tests in 71.676s
OK
```

A requirements-test.txt szerinti korábban telepített függőségekkel futott;
kihagyott teszt nem volt.

Ellenőrzött:

- A trigger eredeti forrása, aktuális DPS-metódusa, DTO-k és a megadott séma
  automatikusan bekerülnek a JPA-átültetési kérésbe.
- JPA entity, valódi PK, összetett kulcs, javax/jakarta névtér, LOB-leképezés.
  Elsődleges kulcs hiányában nincs kitalált entity vagy AI-kérés.
- Saját Spring repository metódusok, EntityManager és determinisztikus Spring-bekötés.
- Kiegészítő DTO; a Controller, a service-interface és a meglévő végponti DTO
  nem változik. A fájlok átvétele és a változatlan forrás ellenőrzése együtt történik.
- Két trigger ugyanazt a repositoryt és DTO-t használja. A meglevő metódus megmarad,
  és nincs második, ütköző repository-mező.
- Hibás vagy tiltott osztály/SQL/JDBC-javaslat elutasítása; módosult repository
  miatt az elavult javaslat nem írja felül a fejlesztő munkáját.
- A korábbi cache-, naplózási, tranzakciós, Java-szintaxis-, séma- és API-regressziók.

## Java 11 fordítás és futtatás

Az átvett service, repository, entity és kiegészítő DTO a teljes generált backenddel
együtt `javac --release 11` fordításon átment. A Spring/céges/JPA API-khoz a teszt
API-helyettesítőket használ, nem valódi Hibernate providert.

A lefordított Java-próba a service nyilvános végpontját hívja. Az injektált saját
repository ténylegesen az EntityManager tesztpéldányát hívja, az eredmény visszajut
a meglévő ActionResult DTO-ba. Ellenőrzött a sikeres lekérdezés, a NULL név megtartása
és a hiányzó rekord hibája. Eredmény: `SERVICE_REPOSITORY_RUNTIME_OK`.

## Angular és teljes böngészős folyamat

`npm run build:ui`: sikeres Angular 22.1.6 / TypeScript 6.0.3 production build,
strictTemplates ellenőrzéssel. A fő komponens korábbi NG8102 figyelmeztetései
megmaradtak; fordítási hiba nincs.

`tests/unified_assistant_browser.cjs`: sikeres valódi Chromium, HTTP API,
migrációs worker és rögzített válaszú Ollama-kiszolgáló mellett:

```text
upload → generation → CSV file/paste/reject/discard → schema save
→ automatic JPA prompt → needs_context → reload/resume → formatted proposal
→ insert service/entity/repository/DTO → ZIP; separate frontend origin OK
```

A 4.32.1 pontos, fejléc nélküli nyolcoszlopos CSV-sora fájlból és beillesztve is
működött. A CHAR/BYTE különbség megmaradt. Az alapfolyamatban nem kellett megnyitni
a frontend-bekötéseket, és nincs adatelérési módválasztó. A repository metódusa és
a service-hívás bekerült a fájlokba, a kiegészítő DTO elkészült, az eredeti Controller
változatlan maradt. A friss ZIP letölthető volt. Böngészős futásidejű hiba nem volt.

## Határok

A teljes történeti tesztkészlet nem futott újra. A próbák nem használtak valódi Qwen
modellt, Hibernate providert, céges futtatókörnyezetet vagy Oracle-adatbázist.
Nem igazolják tetszőleges trigger üzleti azonosságát. A céges projektben JPA/Hibernate,
entity/repository felismerés, megfelelő adatforrás és tranzakciókezelő szükséges;
a teljes fordítást és az eredeti Forms működésével történő összevetést ott kell elvégezni.
