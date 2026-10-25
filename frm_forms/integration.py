from .common import name


def guide(module, package, config, frontend_name=None):
    frontend_name = frontend_name or module
    cls = name(module, 'pascal')
    selector = config['angular_selector_prefix'] + '-' + name(frontend_name, 'kebab')
    wbs = config['wbs_base_url'] or '<a WBS alap URL-je>'
    dps = config['dps_base_url'] or '<a DPS alap URL-je>'
    api = config['api_prefix'].rstrip('/') + '/' + module
    from .java_imports import layer_package
    cl_pkg, dps_pkg, wbs_pkg = (layer_package(config, package, layer) for layer in ('CL', 'DPS', 'WBS'))
    return f'''# {module} beépítése — Angular 22 / CL / DPS / WBS

A frontend pontosan egy fájl: `frontend/{frontend_name}/{frontend_name}.component.ts`. A backendben csak a `CL`, `DPS`, `WBS` mappa van, bennük Java-forrásokkal. Teljes célalkalmazás és buildprojekt nem generálódik.

## 1. CL / CommonLib

Másold a `backend/CL/*.java` fájlokat a CommonLib projekt megfelelő package-mappájába: `{cl_pkg}`.

- Modulonként **4 fájl**: `{cls}Dtos`, `{cls}Constants`, `{cls}RestClient`, `{cls}RestClientImpl`. Több blokk és gombesemény sem növeli a fájlszámot.
- A Dtos egyetlen fájlban tartalmazza a végpontokhoz szükséges rekordtípusokat és közös PageResult/RowResult/UpdateRequest burkolókat. A rejtett kulcsok, a szabályokhoz szükséges mezők, a Required/MaximumLength, az igazolt NUMBER precision/scale és a DATE másodpercei megmaradnak. A sima vezérlőblokkokhoz nem készül használatlan Row DTO. A kiválasztás indoka: `analysis/backend-plan.json`.
- A gombok ellenőrzendő ActionRequest/ActionResult szerződése ugyanebben a fájlban van; az Oracle blokk-/mezőneveket tartalmazó map értékei string/null típusúak. A paraméterirányok és eljárásszignatúrák ellenőrzése után pontosítsd ezt a szerződést.
- A Constants tartalmazza a modul BASE_PATH és az összes CRUD-/gombvégpont PATH konstansát. A DPS és WBS Controller interfészek és a CL HTTP-adapter is ezeket használják. A meglévő teljes HTTP-útvonalak változatlanok.
- A RestClientImpl a Spring `RestTemplate` HTTP-kliensét használja: Spring Boot **2.3+** (Spring Framework 5.2+) és Java **11+** elegendő; `jakarta.*` függőség nincs.
- Jackson 2 és Jakarta Validation típusok szükségesek a DTO-khoz. A Java Time JSON-támogatást a host biztosítsa. A `RestClientImpl` példányát a WBS ServiceImpl hozza létre a host builderéből. A generikus válaszokat ParameterizedTypeReference őrzi meg.

## 2. DPS / adatbázis és üzleti logika

Másold a `backend/DPS/*.java` fájlokat a DPS projekt `{dps_pkg}` package-ébe. A DPS függjön a CL-től, és biztosítson web/JDBC/validation függőségeket, Oracle drivert, DataSource-t és tranzakciókezelőt.

Modulonként **4 fájl**: `{cls}Controller`, `{cls}ControllerImpl`, `{cls}Service`, `{cls}ServiceImpl`. A publikus ServiceImpl- és ControllerImpl-belépési pontok `log1x(log, {cls}Constants.<METÓDUS>_NAME, user, null, () -> ...)` hívásban futnak (céges alaposztály, `UserDto`); a tranzakciók a DPS ServiceImpl publikus metódusain vannak. Az SQL, a Forms-triggerek, a ténylegesen hívott helyi eljárások (`PlsqlUnits`) és a JDBC-segédek **közvetlenül a DPS ServiceImpl-ben** találhatók. Nincs külön Data, domain vagy repository réteg. A PL/SQL többsoros Java szövegblokk, az értékek típusos IN/OUT bindeken érkeznek. Az eredeti forrás: `analysis/backend-evidence.md`.

Jogosultságot a generált kód nem ellenőriz, és ehhez nem kér beant: a hozzáférést a projekt saját jogosultságkezelése szabályozza. A Forms blokkszabályai (Insert/Update/Delete Allowed) a generált kódban érvényesülnek.

A DPS ServiceImpl CRUD-metódusai és privát segédei végzik az SQL-lekérdezést, a mappinget és a triggerhívásokat. A futtatható gombtrigger eredeti PL/SQL-je `DbCalls.call(jdbc, ...)` útján, ugyanabban a Spring-tranzakcióban fut. A helyi Forms-eljárások a névtelen Oracle blokkba ágyazódnak. A teljesen felismert keretrendszer-, NULL- és frontendtriggerek nem kapnak külön akcióvégpontot vagy `*Reviewed` metódust. A kihagyások forrással és indokkal az `analysis/action-plan.json` és `analysis/backend-plan.json` fájlokban vannak; a bizonytalan vegyes logika megmarad, szükség esetén HTTP 501-gyel.

A ServiceImpl és ControllerImpl **CREATE_ONCE**: `--regenerate` az SQL/PLSQL-ben végzett kézi munkát is megőrzi. Az új változatok és diffek az `analysis/backend-regeneration/` alá kerülnek; ezeket az új interfészekkel/DTO-kkal együtt kézzel össze kell fésülni fordítás előtt. A korábbi `…Data.java` felépítésről is új mappába generálj (`module-service-v2`), majd emeld át a saját módosításokat.

## 3. WBS / közvetítő réteg

Másold a `backend/WBS/*.java` fájlokat a WBS projekt `{wbs_pkg}` package-ébe. A WBS is a CL-től függ; **nem függ a DPS Java-kódjától**, és nem kap repositoryt.

Modulonként ugyanaz a **4 fájl** készül, mint a DPS-ben. A WBS ServiceImpl a CL RestClienten keresztül továbbít, és a kapott RestTemplateBuilderből létrehozza a modul HTTP-kliensét. Nincs külön ServiceBase vagy Configuration fájl.

A generáláskor megadott DPS alap URL: `{dps}`. Futáskor felülírhatod a WBS meglévő konfigurációjában:

```properties
frm.{module}.dps-base-url={dps}
```

Ha sem generáláskor, sem futáskor nincs valódi DPS URL, a WBS-kliens konfigurációja hibával leáll. A generator nem talál ki célcímet. A gateway/context prefix az alap URL része lehet; a generált modulútvonalat ne írd bele még egyszer.

A WBS host biztosítsa a `RestTemplateBuilder` beant, a belső szolgáltatáshíváshoz szükséges hitelesítést, request-context továbbítást és hálózati timeoutokat. A meglévő céges builder/interceptor használható. Nincs automatikus, korlátlan bejövő fejlécmásolás.

A DPS és WBS ugyanazokat a CL útvonalakat külön hostban használja. A megfelelő host csak a saját DPS vagy WBS package-et szkennelje. Mindkét ControllerImpl egyetlen Spring webes contextbe szkennelése útvonalütközést okozna.

## 4. Angular 22 / közvetlen FormBlock-leírás

A frontend fájlneve a `FormModule.Title` normalizált camelCase alakja: `frontend/{frontend_name}/{frontend_name}.component.ts`. Üres Title esetén a Name az alap. A HTML, inline stílushely, helyi formérték-interfészek és `FormBlock.Structure[]` tömbök ugyanebben a fájlban vannak.

1. Másold a TS-fájlt az Angular 22 hostba.
2. Importáld a fájl elején felsorolt Angular core/forms szimbólumokat, az `environment` objektumot és a saját `{config['form_block_structure_type']}` típust.
3. Importáld a valódi céges FormBlock komponenst/modult, és add hozzá a standalone komponens `imports` metaadatához. A generált fájlban kérés szerint nincsenek importok.
4. Az API alapcíme közvetlenül `environment.baseUrl`. A generált `endpoints` csak útvonalleírás: a komponens nem indít automatikus HTTP-hívást.
5. A formot a céges FormBlock hozza létre. Input: `[formStructure]`; output: `(formGroupGenerated)`. A callback átveszi a FormGroup-ot. Ismételt kibocsátáskor a már beírt értékeket és a dirty jelzést megtartja.
6. A forrásban szereplő gombok az `actionRequested` eseményt bocsátják ki `{{block, action, value}}` adattal. Ehhez kösd a saját lekérdezést/mentést. Nincs hozzáadott CRUD-toolbar, lapozó, DOM-fókuszkezelő vagy általános Forms runtime.

A sablon egy formhoz egy `<{config['html_selectors']['form_block']}>` sort tartalmaz. A mezők: `text`, `calendar`, `checkBox`, `dropdown`, `radioButton`, `password`, `inputTextarea`, megfelelő precision esetén `inputNumber`. A `maxLenght` írásmód a megadott céges interface-t követi. A szükséges megjelenítést maga az Optimus FormBlock biztosítja, a generátor nem ad hozzá section/nav/fieldset konténereket vagy saját CSS-osztályokat.

A formértékek és a Java API DTO-k eltérhetnek: calendar → Date, bináris checkbox → boolean, pontos nagy NUMBER → string. A mentési/betöltési metódusban a saját API-szerződésetek szerint alakítsd őket. Automatikus mentés vagy wire-konverzió ebben a frontend-profilban nincs. NUMBER hiányzó vagy 15-nél nagyobb precision mellett szövegmező marad; a `schema.json` ismert precision/scale értékei alapján használható inputNumber.

### Naptár és több rekordos blokkok

A nem adatbázisos, CALENDAR jellegű és legalább hét CELL-sorszámú mezőt tartalmazó segédblokk nem lesz külön mezőlista. A már meglévő DATE mezők calendar típusúak. Ha nincs azonosítható dátummező, egyetlen `selectedDate` picker készül, a célmező bekötése review-tétel. Az eredeti XML és teljes IR megmarad, a döntések az `analysis/frontend-plan.json` fájlban láthatók. Egyedi segédblokkok a `calendar_blocks` profilban adhatók meg.

Több megjelenített rekord (`RecordsDisplayCount` / `NumberOfRecordsDisplayed` > 1), vagy `table_blocks` beállítás esetén egyetlen céges táblázatkomponens készül. A sorok nem jelennek meg még egyszer szerkesztő formként. A rows tömböt a saját adatlekérdezésed tölti fel.

**A tényleges Optimus táblázat API-ja nem szerepelt a kapott specifikációban.** Az alap `{config['html_selectors']['table']}` selector, `{config['table_bindings']['rows']}` és `{config['table_bindings']['columns']}` input integrációs minta. Ezeket a `html_selectors.table` és `table_bindings` beállításokban igazítsd a valódi komponenshez. Az oszlopdefiníció kulcsai szintén konfigurálhatók. Teljesen eltérő szerződés esetén a `templates/optimus-table.component.html.tpl` cserélhető a már támogatott `template_dir` mechanizmussal. Nincs natív HTML táblázat.

Tab/accordion speciális céges bekötést ez a lépés nem generál. Az ilyen forráselemeket a parser megőrzi a további UI-tervezéshez.

## 5. Útvonalak és ellenőrzés

Alap: `{api}/<block-key>`. A műveletek:

| HTTP | Végződés | Kérés / válasz |
|---|---|---|
| GET | `/{config['endpoint_names']['list']}` | offset/limit; PageResult |
| POST | `/{config['endpoint_names']['create']}` | Row; RowResult, HTTP 201 |
| PUT | `/{config['endpoint_names']['update']}` | original/value; RowResult |
| DELETE | `/{config['endpoint_names']['delete']}` | eredeti Row a bodyban; üzenetlista |

A DELETE body támogatását a gatewayen is ellenőrizd. A REST adapter megőrzi a DPS HTTP-státuszát, például a stale snapshot 409-et; a nyers DPS hibaszöveget nem szivárogtatja ki.

Futtasd a CL, DPS, WBS saját buildjét és az Angular 22 buildet az importok/adapterek bekötése után. A SQL/sequence/mapping, az Oracle DB triggerek és az üzleti egyenértékűség ellenőrzése továbbra is a valódi környezetben történik. A generátor működésének ellenőrzése nem helyettesíti ezt. Újrageneráláskor új célmappát használj és diffelj.

'''
