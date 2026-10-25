# CL-formátum – 4.11.0

Ez a 4.11-es lépés történeti leírása. A DPS/WBS illesztése a 4.12-ben elkészült;
az aktuális útmutató: [BACKEND_FORMATUM_4_12_HU.md](BACKEND_FORMATUM_4_12_HU.md).

Ebben a körben a CL forrásfájlok formátuma változott. A triggerbesorolás,
a PL/SQL-futtatás, a DPS/WBS implementáció és a generált Angular képernyő változatlan.
Új domain vagy repository réteg nem készül.

## Bekapcsolás

Az `angular-frontend/migrator.ts` komponens **2. Adatok** részében az input neve
és az API-konfiguráció kulcsa egyaránt `AWU_AZON`. Csak az `AWU_` utáni számot
kell megadni, pl. `1234`. A vezető nullák megmaradnak; 1–30 ASCII számjegy adható meg.
Azonosító nélkül a frissített felületről nem indítható generálás.

Tömeges feltöltéskor minden form saját mezőt kap; ismétlődő számot a felület nem fogad el.
Az AWU-szám formhoz tartozik, ezért nem mentjük általános böngésző-beállításként.

CLI:

```bash
python -m frm_forms migrate form_fmb.xml --screen --module pelda --awu-azon 1234 --out output/pelda
```

Vagy a már használt config JSON-ba: `"AWU_AZON": "1234"`.
A kapcsoló felülírja a config értékét. Üres/elhagyott azonosítóval a CLI és az API
a korábbi, egymáshoz illeszkedő CL/DPS/WBS-generálást tartja meg.
A CLI `batch` nem oszt szét egy közös AWU-számot több formnak: ilyenkor a webes
formonkénti mezőket vagy külön `migrate --awu-azon` parancsokat kell használni.
CL-formátum váltásakor új célmappába kell generálni; a `--regenerate` ezt ellenőrzi.

## Generált fájlok

| Fájl | Új forma |
|---|---|
| `PeldaConstants.java` | `@NoArgsConstructor(access = AccessLevel.PRIVATE)`, `NAME`, `PATH`, `AUTH`, páros műveleti `*_NAME`/`*_PATH` |
| `Pelda…Dto.java` | Önálló osztályok; `@Data`, `@Getter`, `@NoArgsConstructor`, `@EqualsAndHashCode(onlyExplicitlyIncluded = true)`; privát mezők és teljes konstruktor |
| `PeldaRestClient.java` | Céges `RestClient` interfész; első paraméter a `UserDto user`, visszatérés `ResponseEntity<RestResponseDto<…>>` |
| `PeldaRestClientImpl.java` | `@Component`, `DataProviderServiceRestClientBase`, publikus `ptrResponse…()`, `getModulePath()`, `exGetEntity`/`exPostEntity` |

Példa `AWU_AZON=1234` esetén:

```java
@NoArgsConstructor(access = AccessLevel.PRIVATE)
public class PeldaConstants {
    public static final String NAME = WebMenuLeaf.Path.AWU_1234;
    public static final String PATH = WebMenuLeaf.FullPath.AWU_1234;
    public static final String AUTH = ConstantsBase.AUTH_HAS_ACCESS_PREFIX + PATH + ConstantsBase.AUTH_HAS_ACCESS_SUFFIX;

    public static final String LIST_B_NAME = "listb";
    public static final String LIST_B_PATH = ConstantsBase.PD + LIST_B_NAME;
}
```

A metódusnév a meglévő műveletből származik (pl. `listB`, `searchB`); a műveleti
NAME kisbetűs. A kliens `getModulePath() + PeldaConstants.LIST_B_PATH` útvonalat
használ, további `ConstantsBase.PD` nélkül. Így egy helyen kerül bele az elválasztó.

A DTO-kban megmarad minden adatmező, rejtett kulcs, validáció és szám/dátum
JSON-formázás. A meglévő oldaleredmény `rows` és `messages` mezőit, az `offset`/`limit`
lapozást, a keresési kritériumokat, a LOV és az akciók payloadját sem hagyjuk el.
Ezért a korábbi `PageResult` új, önálló `PeldaPageResultDto<T>` osztály lesz,
és a kliens azt burkolja `RestResponseDto`-ba. Ahol eddig lista volt, ott lista marad.
A konstruktornév mindig a tényleges DTO-osztály neve; névütközés esetén egyedi nevet kap.

A Lombok annotációk a megadott céges mintát követik. A `@NoArgsConstructor`
a kézzel írt teljes konstruktor mellett is létrehozza az üres konstruktort;
[Lombok dokumentáció](https://projectlombok.org/features/constructor).

## Céges importok és HTTP-segédek

A privát package-eket a meglévő `java_company_imports` listában kell megadni.
Szükséges osztályok: `WebMenuLeaf`, `ConstantsBase`, `RestClient`, `RestResponseDto`,
`DataProviderServiceRestClientBase`, valamint a `java_user_type` (alapból `UserDto`).
A generátor nem talál ki package-neveket; a hiányokat a forrásban és
az `analysis/cl-contract.json` fájlban jelzi. A CL nem importálja a Spring `RestClient` osztályát.

A megadott GET/POST szerződés szerint:

```java
return exGetEntity(path, user, ptrResponseListB());
return exPostEntity(path, getHttpEntity(request, user), ptrResponseSearchB());
```

A meglévő PUT/DELETE műveletek HTTP-metódusa megmarad. Ezekhez még nincs céges
alaposztályminta; segédmetódus hiányában a generált metódus kifejezett
`UnsupportedOperationException` kivételt dob, és a riportban `transport_ready: false`.
Ha a céges metódus szignatúrája szintén `(path, getHttpEntity(body, user), ptr)`,
a config `java_cl_http_helpers` objektumában a `PUT` és `DELETE` kulcshoz megadható
a tényleges metódusnév. Nem nevezünk ki automatikusan nem ismert `exPutEntity` vagy
`exDeleteEntity` metódust, és nem változtatjuk a hívást POST-ra.

## A következő kör kapcsolódási pontjai

Az új CL önálló szerződésváltozás. A teljes generált kimenet együtt **még nem
fordítható és nem telepíthető**, mert a többi réteg formátumát most nem alakítottuk át.
Az `analysis/cl-contract.json` ezt `integration_ready: false` értékkel jelöli.

- DPS/WBS: önálló DTO-importok és getter/setter használat a korábbi publikus mezők/record-accessorok helyett.
- Controller: `Constants.PATH`, új műveletútvonalak és a `RestResponseDto` válaszburkoló.
- WBS: `UserDto` továbbítása és az új Spring komponens injektálása.
- Jogosultság: a korábbi `RestClient.Access` ellenőrzésének megőrzése az új céges szerződésben.
- Generált Angular API: az új útvonalak és a válaszburkoló illesztése.

Minden generált csomag tartalmaz `CL_INTEGRATION.md` átadási leírást.
Az `analysis/cl-contract.json` összerendeli a régi és új útvonalakat, DTO-neveket
és a még hiányzó HTTP-segédeket. A régi backend-terv `api` része a változatlan
DPS/WBS/Angular állapotát írja le; az új CL szerződése külön a `cl_contract` mezőben van.

## A migrátor felületének beillesztése

A frissített `angular-frontend/migrator.ts` forrást a céges Angular projektben kell
beilleszteni és újrafordítani. A ZIP-ben nincs ehhez Angular `package.json`,
hostprojekt vagy a privát UI-könyvtár, ezért a régi `web-dist` statikus csomag
nem lett újrafordítva, abban még nincs AWU-beviteli mező.
A backend API és a CLI már fogadja az új beállítást.

## Ellenőrzés

A `tests/test_company_cl.py` a kért formátumot, a megőrzött payloadokat,
a DTO-névütközést, az AWU-validációt, a webes opció átadását, a CLI felülírást,
a HTTP-segédek hiányának jelzését és az újragenerálás határait ellenőrzi.
Külön teszt hasonlítja össze a régi és új CL-lel generált DPS/WBS/Angular fájlokat:
a tartalmuk bájtra azonos.

Ebben a környezetben nincs JDK/javac, Angular fordítókörnyezet vagy céges Java
alaposztálykészlet; valódi céges Java/Angular fordítást nem tudtunk futtatni.
A teljes tesztkészlet eredményét és a korábbi hibáktól való eltérést
a `VALIDACIO_4_11.json` tartalmazza.
