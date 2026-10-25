# Céges backend-formátum – 4.12.0

Az `AWU_AZON` megadásával a CL, DPS és WBS egymáshoz illesztett, céges formátumban
készül. A 4.11-ben még hiányzó backendillesztés elkészült. A generátor az eredeti
műveleteket, payloadokat, HTTP-metódusokat, lapozást és üzeneteket tartja meg;
az üzleti SQL/PLSQL továbbra is közvetlenül a DPS ServiceImpl-ben fut.

## Generált hierarchia

| Réteg / fájl | Forma |
|---|---|
| DPS `XYController<S extends ModuleService>` | `extends ModuleController<S>`; `UserDto user`, `ResponseEntity<RestResponseDto<…>>`, `throws Exception` |
| DPS `XYControllerBase<S extends ModuleService>` | Package-private abstract; `extends ModuleControllerBase<DpsLogHelper, S> implements XYController<S>`; `getModulePath()` → `XYConstants.PATH` |
| DPS `XYControllerImpl` | `extends XYControllerBase<XYServiceImpl>`; `@XSlf4j`, `@RestController`; verziózott DPS-útvonal; UserDto header; log1x + RestHelper.successResponse |
| DPS `XYService` | `extends ModuleService`; nyers payload visszatérés (a controller burkolja) |
| DPS `XYServiceImpl` | A meglévő SQL/PLSQL, tranzakciók és validációk; önálló DTO-importok és getter/setter hozzáférés |
| WBS `XYController<S extends WbsService>` | `extends WbsController<S>`; Authentication + HttpServletRequest, burkolt válasz |
| WBS `XYControllerBase<S extends WbsService>` | Package-private abstract; `extends WbsControllerBase<S> implements XYController<S>`; `getModulePath()` → `XYConstants.NAME` |
| WBS `XYControllerImpl` | `@XSlf4j`, `@RestController`, `@DependsOn("wbsInfoService")`, globális `@PreAuthorize`; WbsUserParcel + log0x |
| WBS `XYService<R extends RestClient>` | `extends WbsService<R>`; UserDto és burkolt válasz |
| WBS `XYServiceBase<R extends RestClient>` | Package-private abstract; `extends WbsServiceBase<R> implements XYService<R>`; `getModuleName()` → `XYConstants.NAME` |
| WBS `XYServiceImpl` | `extends XYServiceBase<XYRestClientImpl>`; log0x + getResponseDto(restClient.…, null) |

A művelet neve végig azonos: controller interface → implementation → service → CL.
A példákban eltérő `getTableData`/`getPeldakFilter` és `GET_TABLE_DATA_PATH` neveket
nem másoltuk át külön szerződésként. A tényleges név a Forms-műveletből származik.
WBS-ben a `request` a HttpServletRequest neve; a body neve `payload` vagy
`updateRequest`, így nincs azonos nevű paraméter egy metódusban.

## Hívás és válasz

1. WBS ControllerImpl: `WbsUserParcel.getUser(authentication, request)`.
2. WBS ServiceImpl: `log0x`, a meglévő `Access.check`, majd `restClient.művelet(user, …)`.
3. CL: a céges `DataProviderServiceRestClientBase` kezeli a HTTP-hívást és a UserDto továbbítását.
4. DPS ControllerImpl: `@RequestHeader(ConstantsBase.USER_REQUEST_HEADER_PARAM)`, majd `log1x` és `RestHelper.successResponse(service.…(...))`.
5. DPS ServiceImpl: változatlan adatbázis-logika, nyers eredmény.
6. WBS ServiceImpl: a céges `getResponseDto` kezeli a már burkolt választ; nincs második `successResponse`.

A request header UserDto-konverzióját és a generikus base osztályok `service` /
`restClient` injektálását a meglévő céges keretrendszer biztosítja a minták szerint.
A WBS-ben nincs külön Spring RestClient.Builder és nincs kézi klienspéldányosítás.
A DPS kapcsolat URL-jét ebben a profilban a céges kliensalaposztály kezeli.

A listázás meglévő `PageResultDto<T>` típusa megőrzi a `rows` és `messages` mezőket;
nem alakítjuk veszteséggel `List<T>`-vé. Ugyanez igaz a RowResult, keresés, LOV és akció DTO-kra.
A PUT továbbra is egy UpdateRequestDto body-t fogad, és annak original/value értékeit
adja a service-nek. A létrehozás megtartja a HTTP 201 státuszt.
A WBS controller az eredeti RuntimeExceptiont továbbdobja, így a már ismert
HTTP-hibastátuszokat nem rejti el egy új általános kivétel.

## SQL/PLSQL és jogosultság

A DTO-illesztés kizárólag a generált Java kódrészekben történik. A karakterláncok,
text blockok és kommentek maszkoltak: az SQL-literálok, PL/SQL-blokkok, bindnevek
és hibaüzenetek tartalma nem módosul. Például `row.id = …` → `row.setId(…)`,
`row.id` → `row.getId()`, `request.blocks()` → `request.getBlocks()`.
A belső runtime recordok accessorai (pl. `Param.value()`) változatlanok.
Ismeretlen összetett mezőértékadásnál a generátor hibát jelez, nem ír ki hibás settert.

A korábbi `XYRestClient.Access` ellenőrzés megmarad DPS-ben és WBS-ben. Ennek
host bean-jét továbbra is biztosítani kell; nincs megengedő alapimplementáció.
A megadott globális PreAuthorize annotáció ezt nem váltja ki.
A tranzakciós annotációk, MODULE_REVIEWED kapu, bindek, DTO-validáció és
rekordváltozás-ellenőrzés változatlanok. Domain/repository réteg nem készült.

## Beállítások és beillesztés

```bash
python -m frm_forms migrate form_fmb.xml --screen --module pelda --awu-azon 1234 --out output/pelda
```

Az `AWU_AZON` nélküli CLI/API-generálás a korábbi formátumot tartja meg.
A frontend migrátor az előző kiadás óta formonként bekéri a számot.

A `java_company_imports` a tényleges privát package-neveket várja. A CL osztályokon túl:
`ModuleService`, `ModuleController`, `ModuleControllerBase`, `ModuleServiceBase`,
`DpsLogHelper`, `DPSConstants`, `RestHelper`, `WbsService`, `WbsController`,
`WbsControllerBase`, `WbsServiceBase`, `WbsUserParcel` szükséges.
A UserDto neve a meglévő `java_user_type` beállításból jön.
A DPS ServiceImpl ősosztálya továbbra is a `java_service_base_dps` értéke.
A company profil controller- és WBS service-ősosztályai a most megadott mintát követik;
a régi `java_controller_base_*` és `java_service_base_wbs` beállítások a legacy profilban érvényesek.

A GET/POST klienshívás a megadott exGetEntity/exPostEntity mintát követi.
A PUT/DELETE segédnevei továbbra sem ismertek; a `java_cl_http_helpers` `PUT` és `DELETE`
kulcsaihoz a tényleges céges neveket kell megadni, ha a szignatúra
`(path, getHttpEntity(body, user), ptr)`. Hiányukban az érintett CL-metódus
`UnsupportedOperationException`-t dob, és a riportban `transport_ready: false`.
A HTTP-metódust nem változtattuk meg és nem találtunk ki private API-t.

4.11-es CL-only vagy korábbi backendből **új célmappába generálj**, majd emeld át
a kézi ServiceImpl/ControllerImpl módosításokat. A formátumváltást a `--regenerate`
nem engedi rá a régi implementációra. 4.12-n belül az eddigi CREATE_ONCE szabály
érvényes: a kézzel módosított fájl megmarad, a friss javaslat az
`analysis/backend-regeneration/` mappába kerül.

## Frontend kapcsolódási pont

A backend útvonalak Java-kifejezései:

- DPS: `DPSConstants.SERVICE_API_VERSIONED_PATH + XYConstants.PATH`.
- WBS: `WbsControllerBase.SERVICE_API_VERSIONED_PATH + XYConstants.PATH`.

E konstansok tényleges szöveges értéke és a RestResponseDto JSON-payloadjának mezőneve
nincs a projektben. A generátor ezeket nem következteti ki az AWU-számból.
Az `analysis/backend-plan.json` már az új CL-műveletútvonalakat tartalmazza;
a hiányzó teljes WBS-alapútvonalat Java-kifejezésként adja át.
A céges `--screen` kimenet alapból `events` módban működik, a host hívja a backendet.
A közvetlen HTTP módhoz a teljes URL-t és a céges válasz kibontását is illeszteni kell.
A régebbi strict/scaffold frontend-generálást ez a backendformátum-frissítés nem alakítja át;
annak API-hívásai külön illesztést igényelnek.

Az `analysis/cl-contract.json` jelzői:

| Mező | Jelentés |
|---|---|
| `backend_contract_ready: true` | A CL/DPS/WBS generált szerződései illeszkednek |
| `host_configuration_ready` | Minden céges import és az alkalmazott HTTP-segéd meg van adva; nem helyettesít host buildet |
| `integration_ready: false` | A teljes frontend/host integráció még nincs igazolva |
| `pending_layers` | A frontend válaszburkoló és a tényleges WBS-modulútvonal |

## Ellenőrzés

A célzott tesztek összepárosítják minden művelet controller/interface/service/CL
szignatúráját, ellenőrzik a végpontokat, a header és body paramétereket, a generikus
ősosztályokat, a naplózást és a válaszkezelést. CRUD, többblokkos keresés, LOV,
PL/SQL-akció, csak akciót tartalmazó és adatvégpont nélküli form is szerepel.
A DPS régi/új SQL-literáljai, text blockjai és kommentjei összehasonlítva változatlanok.
A Java-DTO átalakítás és a CREATE_ONCE/formátumváltás külön regressziós tesztet kapott.

Valódi céges Java-fordítást vagy Oracle-integrációs tesztet nem futtattunk: a környezetben
nincs JDK és privát keretrendszer/Oracle-kapcsolat. A vizsgálat generátorteszt és
szerződésellenőrzés. Részletes futási eredmény: `VALIDACIO_4_12.json`.
