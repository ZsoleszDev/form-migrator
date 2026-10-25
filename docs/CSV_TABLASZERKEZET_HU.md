# Táblaszerkezet beolvasása IntelliJ CSV-exportból — 4.32.1

## Használat

1. Generálj modult, majd a Java asszisztens **1. Adatbázistáblák** részében válaszd ki a táblát.
2. A **CSV-fájl** mezőben válaszd ki az IntelliJ-exportot. A táblázat automatikusan kitöltődik.
   Alternatíva: nyisd le a **CSV-szöveg beillesztése és exportminta** részt,
   illeszd be a tartalmat és kattints a **CSV beolvasása** gombra.
3. Ellenőrizd a kulcsot és a hosszakat. A beolvasás vázlatot készít; a meglévő fájlokat nem írja át.
4. Kattints a **Táblaszerkezet mentése** gombra. A séma bekerül az AI kontextusába,
   a friss ZIP-be és a schema.json-exportba, valamint a webes újrafuttatáskor is megmarad.

Fejléc nélküli, hatoszlopos export:

```csv
OSZLOP_NEVE1,NUMBER,22,20,0,N
OSZLOP_NEVE2,NUMBER,22,20,0,N
OSZLOP_NEVE3,VARCHAR2,30,,,Y
```

A sorrend: `COLUMN_NAME,DATA_TYPE,DATA_LENGTH,DATA_PRECISION,DATA_SCALE,NULLABLE`.
Fejléc nélküli, nyolcoszlopos export is támogatott; ugyanebben a sorrendben
az utolsó két mező a `CHAR_LENGTH,CHAR_USED`:

```csv
OSZLOP_NEVE,VARCHAR2,40,,,N,40,B
UGYFEL_NEV,VARCHAR2,120,,,Y,30,C
UGYFEL_ID,NUMBER,22,20,0,N,0,
```

Az első sor 40 BYTE, a második 30 CHAR hosszúságú mezőt jelent. A numerikus
mező utolsó, üres értékét jelző vesszőt is tartsd meg. Egy fájlban az adatsorok
mezőszáma legyen azonos (6 vagy 8).

Ugyanezekkel a nevekkel fejléc is megadható; fejléccel a sorrend szabadon változtatható.
Vessző, pontosvessző és tabulátor elválasztó is támogatott, valamint UTF-8 BOM,
Windows CRLF és a szokásos CSV-idézőjelezés. A fájl legfeljebb 1 MB / 1000 oszlop lehet.

`Y` = lehet NULL, `N` = nem lehet NULL. A `NUMBER,22,20,0` esetén **precision=20,
scale=0** töltődik be; a 22 byte a tárolási hossz, nem a számjegyek száma.

## Amit a hatoszlopos CSV nem közöl

- **Elsődleges kulcs:** a NOT NULL nem bizonyítja, hogy egy mező PK. A jelölőnégyzetet
  az adatbázis tényleges kulcsa alapján állítsd be. Kulcs nélküli táblánál üresen maradhat;
  ilyen táblához a kulcs szerinti repository nem készül el.
- **Karakter- vagy bytehossz:** a DATA_LENGTH bytehossz. A hagyományos CHAR/VARCHAR2
  mezők BYTE-korláttal kerülnek a szerkesztőbe, figyelmeztetéssel; ebből nem állapítható
  meg az eredeti CHAR/BYTE deklaráció. CHAR deklarációnál add meg a valódi
  karakterhosszt, vagy használd az alábbi bővített exportot. NCHAR/NVARCHAR2 esetén
  karakterhossz nélkül a beolvasás megáll, mert a bytehosszból azt nem lehet biztosan kiszámítani.
- **Alapérték, megjegyzés:** új oszlopnál üres. Azonos nevű, már mentett oszlopnál
  a PK-jelölés, alapérték és megjegyzés megmarad. Az import oszloplistája tölti ki a vázlatot;
  az abból hiányzó oszlopok elmaradnak. Az elvetés visszaállítja a mentett definíciót.

Mentetlen táblavázlat vagy bemeneti bekötés mellett előbb ments vagy vesd el a vázlatot.
Hibás CSV esetén nincs részleges kitöltés. A mentés továbbra is ellenőrzi például
a típusokat, a hosszakat és a nullable/PK ellentmondását.

## Pontos karakterhossz: ajánlott, nyolcoszlopos export

IntelliJ SQL-konzolban futtasd, majd exportáld CSV-be. A fejléc opcionális:

```sql
SELECT column_name, data_type, data_length, data_precision,
       data_scale, nullable, char_length, char_used
FROM all_tab_columns
WHERE owner = 'SEMA_NEVE'
  AND table_name = 'TABLA_NEVE'
ORDER BY column_id;
```

A séma- és táblanevet cseréld ki. A `CHAR_USED=C` a CHAR, `B` a BYTE deklarációt jelenti.
Tulajdonos nélkül azonos nevű táblák több sémából is bekerülhetnek; csak egyet exportálj.
Az opcionális `OWNER` és `TABLE_NAME` fejlécet is kezeli az import, és ellenőrzi,
hogy az export a kiválasztott táblához tartozik-e. A fejléc nélküli 6 és 8 oszlopos CSV
táblanevet nem tartalmaz, ezért a felületen kiválasztott célhoz kerülnek az oszlopok.

## DUAL és az AI feladata

A `DUAL` / `SYS.DUAL` Oracle beépített egysoros segédtábla, nem egy hiányzó üzleti tábla.
Sem a táblaszerkezet-bekérő, sem a JDBC-helper nem kér hozzá definíciót.
Más tulajdonos `APP.DUAL` nevű tábláját továbbra is valódi üzleti objektumként kezeli.

Az AI a kiválasztott trigger átültetési feladatát automatikusan megkapja a backendtől.
Végpontválasztás után elég a **Java-javaslat készítése** gombot használni; a trigger,
helyi rutinok, DTO-k, séma és értékforrások bekerülnek a kérésbe. Csak a ténylegesen
hiányzó forráskódot vagy céges API-t kell szükség esetén a Projektminta mezőbe tenni.
