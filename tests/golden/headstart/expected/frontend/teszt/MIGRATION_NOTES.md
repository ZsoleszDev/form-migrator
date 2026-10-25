# Feldolgozatlan adatlapok

Szerkeszthető Angular képernyőváz; az üzleti működés bekötése fejlesztői feladat.

A triggerfordítás és a tényleges eseménybekötés külön leltára: `RUNTIME_COVERAGE.md` és `analysis/runtime-coverage.json` a modul gyökerétől.

## Migrációs teendők

0 gomb-akció kézi bekötése · 1 felismert gomb · 1 LOV-végpont · 4 saját/vegyes trigger · 0 kikövetkeztetett típus ellenőrzése.

## Beillesztés

- A komponens egyetlen `.component.ts`; a FormBlock és a Tailwind a fogadó alkalmazásból érkezik.
- A publikus komponensek importja kizárólag `@openng/optimus-ui/*`. A privát FormBlock importját a céges profil adja meg, vagy egészítsd ki a jelölt TODO-t.
- Táblázat: `<wf-table>` (`wf-table.ts`, egyszer kell a projektbe tenni; a java-imports.json `WfTable` bejegyzése adja az importját). Sorai: `<blokk>Rows`, kijelölt sora: `<blokk>Selection`, oszlopai: `<blokk>Columns`. A p-table minden bemenete és sablonja (`#header`, `#body`, `#caption` ...) átadható neki, például saját szűrőkhöz.
- A komponens a céges `ServiceBase`-t örökli, és csak a keretet adja (4.27): a FormBlock-régiók szerkezetét, a táblázatokat, a végpontmetódusokat és gombonként (`on<Gomb>Click()`) a gomb HTTP-kérését, kezdetlegesen, a képernyő értékeivel. Forms-emulációt nem tartalmaz: a mezőállapotok, a LOV-javaslatok és visszaírt értékek, az alertek, a :GLOBAL értékek, a checkbox Forms-értékei és a válaszok feldolgozása fejlesztői feladat (TODO a metódusban, a nem fordított gomboknál az eredeti Forms-kóddal).
- A mezők műveleti engedélyeit és formátummaszkjait a query/insert/update móddal együtt ellenőrizd; a képernyő nem Forms-futtató.
- `--regenerate` megőrzi a komponens kézi módosításait. Új elrendezéshez generálj új célmappába, és hasonlítsd össze.

## Képernyőrészek

| Blokk | Canvas / fül | Megjelenítés | Mezők | Látható rekordok |
|---|---|---|---:|---:|
| V_ELEK_ADLAP | CG$PAGE_1 | form | 7 | 1 |
| AIT | CG$PAGE_1 | table | 5 | 15 |
| CGNV$W01_1 | CG$PAGE_1 | form | 1 | 1 |

## Ablakok és párbeszédablakok

Egy Forms Window egy megjelenítési egység: az összes hozzárendelt Content/Tab/Stacked canvas ugyanabban az ablakban marad. A Dialog stílus és a Modal érték külön tulajdonság; a nem modális másodlagos ablak is p-dialog.

| Window | Szerep | Modal / forrás | Canvasok | Kezdő Content canvas | Döntés alapja |
|---|---|---|---|---|---|
| WINDOW | Főoldal | false / default | CG$PAGE_1 | CG$PAGE_1 | Az egyetlen használt, nem modális Document ablak. |

A `default` generátor-fallback, nem bizonyított Oracle-alapérték. A felugró ablakok kezdetben zártak; a startup triggereket a váz nem futtatja. Több lehetséges Document főablaknál a FirstNavigationBlock, illetve a screen_primary_window beállítás dönt; nincs modulnévhez kötött kivétel.

### Ablak- és canvasműveletek a forrásban

Statikus hivatkozások, automatikus gombbekötés nélkül. A feltételek, eseménysorrend, GO_BLOCK/GO_ITEM fókusz- és rekordhatásai külön ellenőrzést igényelnek.

| Tulajdonos / esemény | Hívás | Window / canvas | Feloldás | Forrás / sor |
|---|---|---|---|---|
| CGNV$W01_1.PB_RESZLETEK / WHEN-BUTTON-PRESSED | go_block('AIT') | WINDOW / CG$PAGE_1 | resolved | analysis/discovery/sources/o32-e9a4f9392210.sql:2 |

## Elrendezés és felülbírálások

Sorillesztési tolerancia: 0.25 × a kisebb mezőmagasság; a valódi sorok elkülönítéséhez függőleges és vízszintes átfedésvizsgálat is történik. Belső térközök megőrzése: false. Kerethez a teljes mezőnek bele kell férnie; a beágyazott keretek megmaradnak.

Szabálysablon: `analysis/screen-overrides.template.json`. Töltsd ki a szükséges mező `reason` és `set` részét, majd add át `--screen-overrides` kapcsolóval vagy a webes feltöltőn. A forrásváltozás miatt elavult aktív szabály hibával megállítja a generálást.

### Összetartozó LOV-mezők

| Kódmező | LOV | Köztes gombok | Visszaírt mezők ugyanabban a sorban |
|---|---|---|---|
| V_ELEK_ADLAP.UBI_INPTIP_KOD | INPTIP |  | V_ELEK_ADLAP.UBI_INPTIP_KOD_NEV |

Az összetartozás az explicit LOV ReturnItem és az olvasási sor alapján igazolt. Az a köztes gomb, amelynek triggere bizonyítottan csak a kódmező listáját nyitja, beolvad a mező saját lenyitó gombjába (lásd lent).

## Validációk és formátumok

A szabályokat a FormBlock tulajdonságai adják (validator, minLenght/maxLenght, min/max, regexRule); saját validátort a képernyő nem tesz a kontrollokra (4.26), a többit a backend ellenőrzi. Required checkboxnál a false is kitöltött érték; ezért ott nem használjuk a `validator: true` kapcsolót, amelynek checkbox-szemantikája a privát komponensben nem ismert.

| Mező | Forrás property | Érték | Lefedettség | Megvalósítás / teendő |
|---|---|---|---|---|
| V_ELEK_ADLAP.UBI_INPTIP_KOD | MaximumLength | 10 | Megvalósítva | FormBlock minLenght/maxLenght. |
| V_ELEK_ADLAP.UBI_INPTIP_KOD_NEV | MaximumLength | 80 | Megvalósítva | FormBlock minLenght/maxLenght. |
| AIT.AIT_TIPUS | MaximumLength | 10 | Adapter szükséges | Csak olvasható táblázat: a szabályt és formázást a szerkesztő/backend adapterben kell átvenni. |
| AIT.AIT_STATUS | MaximumLength | 1 | Adapter szükséges | Csak olvasható táblázat: a szabályt és formázást a szerkesztő/backend adapterben kell átvenni. |
| AIT.AIT_MEGJ | MaximumLength | 200 | Adapter szükséges | Csak olvasható táblázat: a szabályt és formázást a szerkesztő/backend adapterben kell átvenni. |

A rejtett mezők szabályai is megmaradnak az `analysis/screen-plan.json` `validation_audit` listájában.

## Adatforrások

- `V_ELEK_ADLAP`: `vezérlőblokk`.
- `AIT`: `ANK_ADLAP_ITEMS`.
- `CGNV$W01_1`: `vezérlőblokk`.

**AIT — WhereClause** (a backend adapterben őrizd meg):

```sql
AIT_STATUS = 'F'
AND AIT_TIPUS = :V_ELEK_ADLAP.UBI_INPTIP_KOD
```


**AIT — OrderByClause** (a backend adapterben őrizd meg):

```sql
AIT_KULCS
DESC
```


## Blokk művelet-engedélyek

A forrásbeli engedélyek következnek; nem engedélyeznek automatikus CRUD-műveleteket. A `default` jelzés a UI-modell fallbackje, nem igazolt Oracle-property; az adapterben ellenőrizendő.

| Blokk | InsertAllowed | UpdateAllowed | DeleteAllowed | QueryAllowed | NavigationStyle |
|---|---|---|---|---|---|
| V_ELEK_ADLAP | true (default) | true (default) | true (default) | true (default) | Nincs megadva |
| AIT | false (explicit) | true (explicit) | false (explicit) | true (explicit) | Nincs megadva |
| CGNV$W01_1 | true (default) | true (default) | true (default) | true (default) | Nincs megadva |

A blokk NavigationStyle tulajdonságához nincs FormBlock.Structure megfelelő; a rekordok közötti navigációt a host adapterben kell megvalósítani.

## Gombok és eredeti hívások

A gomb metódusa (`on<Gomb>Click()`) a felismert lépések kérését küldi (executeQuery: a blokk keresése, commit: `save()`, showWindow/showView: láthatóság), vagy a gomb backend-akcióját hívja; a többi gombnál TODO és az eredeti Forms-kód. A keretrendszer-diszpécserhívások (katalógus) nem teendők.

| ownId | Felirat | Felismert lépések | Saját hívások (kézi) |
|---|---|---|---|
| CGNV$W01_1.PB_RESZLETEK | Részletek | goBlock(AIT), executeQuery | — |

## LOV bekötés

A LOV-os mező autocomplete (`dropdown`, üres `suggestions`), a LOV-végpontnak saját metódusa van (`lov<Név>`). A javaslatok betöltése (`completeMethod`) és a ReturnItem mezők kitöltése fejlesztői feladat. Az alábbi SQL és paraméterek dokumentáció; nem böngészőből végrehajtandó kód.

### INPTIP — V_ELEK_ADLAP.UBI_INPTIP_KOD

Rekordcsoport: `RG_INPTIP`. Paraméterek: 

| Oszlop | Felirat | ReturnItem |
|---|---|---|
| KOD |  | V_ELEK_ADLAP.UBI_INPTIP_KOD |
| NEV |  | V_ELEK_ADLAP.UBI_INPTIP_KOD_NEV |

```sql
SELECT KOD, NEV
FROM ANK_INPTIP
ORDER BY KOD
```


## Térköz-mezők

A keretrendszer-katalógus `spacer_items` mintáira (pl. `L_URES_*`) illeszkedő mezők üres `label` elemként, üres labelText-tel kerültek a rácsba, `formControlName`, érték és validáció nélkül; csak a szélességüknek megfelelő helyet tartják. Ha a forrásban viselkedésük is van (trigger, LOV, adatbázis-oszlop), SPACER_BEHAVIOUR jelzés mutatja; ellenőrzött widget-felülbírálással mezőként visszaállítható. Típus: `screen_spacer_type`.

| Mező | Minta | col | Felirat |
|---|---|---:|---|
| V_ELEK_ADLAP.L_URES_1 | L_URES_* | 12 | — |
| V_ELEK_ADLAP.L_URES_2 | L_URES_* | 2 | — |
| AIT.L_URES_3 | L_URES_* | 1 | — |

## Keretrendszer-blokkok

A katalógusban (`frm_forms/data/framework-catalog.json`) szereplő, adatforrás nélküli blokkok nem kerülnek a képernyőre; a szerepüket a natív vezérlők és a host veszik át. Saját katalógus: `framework_catalog`.

| Blokk | Mezők | Miért maradt ki |
|---|---:|---|
| CALENDAR | 2 | Headstart naptár-segédablak: a dátummezők natív naptárvezérlője váltja ki. |
| QMS$TRANS_ERRORS | 2 | Headstart tranzakciós hibakonzol: a fogadó alkalmazás hibakezelése váltja ki. |

## Triggerek besorolása

14 trigger a megjelenített blokkokban: 10 csak keretrendszer-hívás (nem teendő), 0 vegyes, 4 saját kód, 0 üres. A vegyes és saját kódú triggerek a tényleges migrációs munka.

### Billentyűkezelés

Saját logikát tartalmazó KEY-* triggerek. A FormBlock `hotkeyShow` / `hotkeyBlock` beállításaival köthetők; a tisztán keretrendszeri navigációs billentyűk nincsenek a listán.

| Tulajdonos | Billentyű | Saját hívások |
|---|---|---|
| (form) | KEY-COMMIT | commit_form |
| AIT | KEY-DELREC | message |

## Backend-hívások

Minden végpontnak saját metódusa van a komponensben: `this.http.<ige>(this.url('<végpont>'))`. A sikeres választ legelőször `WFF.debug(this.modName + '.<metódus>', res)` naplózza, hibánál `WFF.err('Hiba', error)` jelez. A `this.url(...)` argumentuma a végpont neve úgy, ahogy a CL használja: rákeresve a CL-ben, a DPS-ben, a WBS-ben és a komponensben is megtalálható.

| Metódus | Hívás | CL-konstans | Használja |
|---|---|---|---|
| `aitUpdate` | `PUT this.url('ait/update')` | `TesztConstants.AIT_UPDATE_PATH` | a képernyő nem hívja (lásd lent) |
| `aitSearch` | `POST this.url('ait/query/search')` | `TesztConstants.AIT_SEARCH_PATH` | lekérdezés (gomb) |
| `lovInptip` | `POST this.url('lov/inptip')` | `TesztConstants.LOV_INPTIP_PATH` | a képernyő nem hívja (lásd lent) |
| `commitForm` | `POST this.url('commit')` | `TesztConstants.COMMIT_FORM_PATH` | a képernyő nem hívja (lásd lent) |

### Végpontok, amelyeket a képernyő nem hív

Nem hiba: a backend kész, de a generált képernyő nem hívja őket. Ha egyik sem kell, a végpont törölhető.

| Metódus | Művelet | Blokk / gomb | Miért nem hívja |
|---|---|---|---|
| `aitUpdate` | update | `AIT` | a blokk nem űrlapként jelenik meg: a mentést a fejlesztő köti be |
| `lovInptip` | lov | `INPTIP` | a LOV-mező javaslatainak betöltése fejlesztői feladat (completeMethod) |
| `commitForm` | commit | `@FORM` | a képernyőn nincs menthető űrlapblokk |

Mentés (create/update/delete): a metódusok elkészülnek, de a Forms COMMIT-szemantikája (több rekord, sorrend, hibakezelés) miatt a mentést a fejlesztő köti be; a komponens nem ment automatikusan.

## Részletes források

- `../../analysis/screen-plan.json`: elrendezési döntések, rejtett mezők, LOV-k és figyelmeztetések.
- `../../analysis/screen-preview.html`: a generált képernyők előnézete (fő képernyő, párbeszédablakok, fülek), böngészőben megnyitható.
- `../../analysis/field-lengths.template.json`: kitölthető mezőhossz-minta (formControlName -> min/max).
- `../../analysis/layout-preview.html`: az eredeti canvas és a generált rács egymás mellett, böngészőben összevethető.
- `../../analysis/discovery/form-explorer.html`: kereshető offline modultérkép, eredeti trigger/program unit forrásokkal.
- `../../analysis/ui-model.json`: a generált képernyő vezérlőtípusai, típuseredet és property-audit.
- `../../analysis/ui-model-strict.json`: a screen következtetése előtti, szigorú besorolás. `../../analysis/inheritance.json`: öröklési audit.
- `../../analysis/issues-summary.json`: csoportosított problémák. A strict mód hibái továbbra is megmaradnak; a képernyőváz ezeket nem minősíti megoldottnak.
