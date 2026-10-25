# 4.25 – Változóértékek a java-variables.json-ból

## Mi változott

A DPS ServiceImpl végpontjaiban a generált Java saját változókat is létrehoz:

- **fejlesztői bemenetek:** amit a kód sehonnan nem kap meg, például `String ibuKod = null;` TODO-val;
- **a Java lekérdezőgombok képernyőértékei:** például `String col1 = PlsqlValues.text(values, "T1", "COL1");`.

Az új `java-variables.json` név szerint megadja egy ilyen változó értékét, és azt is, milyen import és milyen
`@Autowired` mező kell hozzá:

```json
{
  "variables": [
    {
      "variableName": "ibuKod",
      "variableValue": "commonService.Details(param)",
      "autowired": "commonService",
      "import": "hu.company.pelda.CommonService"
    }
  ]
}
```

Amikor a generátor az `ibuKod` változót hozza létre:

1. **Import:** a ServiceImpl-be bekerül az `import hu.company.pelda.CommonService;`.
2. **Mező:** az osztály tetejére bekerül
   ```java
   @Autowired
   private CommonService commonService;
   ```
   A típus az `autowired` értéke nagy kezdőbetűvel, a mező neve maga az `autowired`.
3. **Változó:** a metódusban, ahol eddig `String ibuKod = null;` és TODO állt, most
   ```java
   // :IBU_KOD (blokk nélküli mezőhivatkozás, nem egyértelmű): az érték a java-variables.json-ból.
   String ibuKod = commonService.Details(param);
   ```

Részletek:

- **Típus:** a változó típusa a generált marad (`String`, `BigDecimal`, `LocalDateTime`).
- **Elhagyható részek:** az `import` és az `autowired` elhagyható. Az `import` lehet lista is.
- **Közös szolgáltatás:** ha több változó ugyanazt a szolgáltatást használja, az import és a mező egyszer szerepel.
- **Csak a használt bejegyzések:** importot és mezőt csak az a ServiceImpl kap, amelyik a változót tényleg létrehozza.
- **Hol hat:** a gombok és a mentési lánc PL/SQL-futtatója, az adat-triggerek, a PL/SQL lekérdező-adapter és a Java
  lekérdezőgomb. A Java lekérdezőgombban a képernyőértéket is felülírja, ha a neve szerepel a fájlban.
- **Céges (AWU) formátum:** az utófeldolgozás megtartja a mezőket és az importokat.

## Hol van a fájl

- **Alapértelmezés:** `java-variables.json` a migrátor gyökerében. A repóban lévő példány üres `variables`
  listát tartalmaz; a fenti bejegyzés a `_példa` kulcsban mintaként szerepel. A `_` kezdetű kulcsok megjegyzések.
- **Más útvonal:** a `java_variable_map` beállítás (a config fájlhoz képest), vagy a `FRM_JAVA_VARIABLE_MAP`
  környezeti változó. A `"-"` érték kikapcsolja.
- **Beolvasás:** minden migráláskor újra, a webes felület is ezt használja. A beolvasás az elemzés előtt
  történik, mert az adat-triggerek Java-kódja már az elemzéskor elkészül.
- **Változónevek:** a pontos neveket a `BACKEND_TASKS.md` Fejlesztői bemenetek táblázata és a metódus feletti
  megjegyzés mutatja (`:XX.YY -> xxYy`). A szabályok: `docs/AUTOMATIZALAS_HU.md` → Változóértékek.

## Riportok

- **A metódus feletti megjegyzés** külön sorolja fel a még TODO-s és a JSON-ból kitöltött bemeneteket.
- **`BACKEND_TASKS.md`:**
  - a Fejlesztői bemenetek táblázatban csak a még kitöltendők maradnak;
  - a JSON-ból kitöltöttek a „Fejlesztői bemenetek a java-variables.json-ból” táblázatba kerülnek, az értékkel.
- **`analysis/backend-plan.json`:** a végpont `developer_inputs` bejegyzése `value` mezőt kap.
- **`analysis/runtime-coverage.json`:** a kitöltött bemenet már nem hiány.
- **`analysis/java-variables.json`:** minden bejegyzés, és hogy melyik fájlba került (`used_in`).

## Hibák

A hibás fájl leállítja a migrálást, és a hibaüzenet megmondja, mi a baj. Hibának számít:

- a hibás JSON;
- a nem Java-név a `variableName`-ben vagy az `autowired`-ben, ideértve a kulcsszót és a generált osztály saját
  mezőit (`jdbc`, `log`);
- az üres vagy többsoros `variableValue`;
- a nem teljes osztálynév az `import`-ban;
- az ismeretlen kulcs;
- a kétszer szereplő változó.

## Ellenőrzés

- **`tests/test_java_variables.py`:**
  - a fájl formái és hibái;
  - a fejlesztői bemenet és az adat-trigger változója;
  - egy import és egy `@Autowired` mező két bejegyzéshez;
  - a nem használt bejegyzés nem hagy nyomot;
  - a riportok;
  - a Java lekérdezőgomb;
  - a céges formátum;
  - a `javac` fordítás egy `hu.company.pelda.CommonService` csonkkal.
- **A többi teszt nem olvassa a fejlesztő saját fájlját:** `FRM_JAVA_VARIABLE_MAP=-`, ahogy az importtérképnél is.
