# 4.27 – Frontend: csak a keret

> Még kevesebb logika kellene frontend oldalra, tényleg csak a váz. A lényeg, hogy a frontend generálásakor
> felépítse FormBlockkal a képernyőket, és a HTTP-kérések a gombokhoz ki legyenek építve, kezdetlegesen.

## 1. Mi marad a generált komponensben

A komponens a céges `ServiceBase`-t örökli, és ennyit tartalmaz:

- a `ToastService` és a `toastLife` mezőt (a céges komponenskonvenció; a váz maga nem hívja);
- a FormBlock-régiók `structures` szerkezetét és a `forms` FormGroupjait (`onFormGroupGenerated`);
- a táblázatokat (`<wf-table>`): oszlopok, sorok, kijelölt sor, sor végi gombok (`onRowAction`);
- az ablakok és canvasok láthatóságát (`windowVisible`, `canvasVisible`, `activeContentCanvas`);
- végpontonként egy metódust (`this.http.<ige><any>(this.url('…'))` + `WFF.debug` / `WFF.err`);
- gombonként egy `on<Gomb>Click()` metódust a gomb kérésével;
- a `save()`-et, ha a képernyőnek van mentési végpontja és menthető űrlapblokkja, a sablon tetején egy
  **Mentés** gombbal;
- a konstruktorban az indítási végpont hívását, ha van.

## 2. Ami kikerült (Forms-emuláció)

- **Segédmetódusok:** `text()` (Oracle-szövegre alakítás), `data()` (válaszboríték), `value()`, `blocks()`
  (Oracle-nevek), `parameters()` (URL-paraméterek), `showResult()` / `showBlocks()` (visszaírt mezők),
  `suggest()`, és az `items` tábla.
- **Mezőállapotok:** a `SET_ITEM_PROPERTY` hívások fordítása (`setItemState`, `stateValue`, `setItemValue`,
  `isNull`, `cmp`, a mezőfeliratkozások, a `Validators`). A `screen_states.py` megszűnt.
- **LOV:** `search<Lov>()`, `choose<Lov>()` és a `completeMethod`. A mező autocomplete marad (`dropdown`, üres
  `suggestions`), a LOV-végpont metódusa (`lov<Név>`) kész.
- **Mentési lánc:** az `originals` / `deleted` nyilvántartás, a `newRecord()`, a `deleteRecord()`, a módosított
  rekordok összeállítása és a mentés utáni visszatöltés.
- **Lekérdezési segédek:** `query<Blokk>()`, `show<Blokk>()`, `fill<Blokk>()`; a lekérdezés kérése a gombban van.
- **TODO-listák:** az alertek, képernyő- és mentési pontok, a backend Forms-utasításai (`commands`).
- **Toastok:** „Nincs találat”, „Kész”, „Nincs bekötve” stb.
- **Válaszinterfészek:** `Page`, `ActionResult`, `CommitResult`; a végpont típusa `any`.

## 3. A gomb kérése

- **Backend-akció:** a kérés azokat a mezőket viszi Oracle-néven, amelyeket a gomb kódja olvas, a régió értékeiből
  (`getRawValue()`), átalakítás nélkül; a táblázat mezője a kijelölt sorból jön:

  ```ts
  // CTRL.PB_UJRASZAMOL: a gomb kódja a backendben fut.
  protected onPbUjraszamolClick(): void {
    const rendeles = this.forms['rendeles']?.getRawValue() ?? {};
    // TODO: a válasz feldolgozása (res.blocks: a visszaírt mezők, res.messages: az üzenetek).
    this.actionOnctrlpbujraszamol({ blocks: { RENDELES: { ID: rendeles.id, OSSZEG: rendeles.osszeg } }, parameters: {} }).subscribe();
  }
  ```

  - a :GLOBAL / :PARAMETER / :SYSTEM értékek `null`-ként, TODO-val a `parameters`-ben;
  - a képernyőn nem szereplő mező `null`, TODO-val;
  - a backend saját protokollértékei (`FRM.ALERTS`, `FRM.RESUME`, `FRM.COMMIT`) nem kerülnek a kérésbe.
- **Lekérdező gomb** (Java-lekérdezés vagy felismert `GO_BLOCK` + `EXECUTE_QUERY`): a sorok a táblázatba kerülnek:

  ```ts
  this.aitSearch({ criteria: { vElekAdlapUbiInptipKod: vElekAdlap.ubiInptipKod }, offset: 0, limit: 200 }).subscribe(res => {
    this.aitRows = res.rows ?? [];
  });
  ```

  Űrlapblokk lekérdezésénél az első sor a régióba kerül (`patchValue`). Céges módban a TODO jelzi, hogy a
  sorokat a `RestResponseDto` adatmezőjéből kell venni.
- **Navigáció** (CALL_FORM / OPEN_FORM / NEW_FORM): `this.router.navigate([...], { queryParams })` a képernyő
  értékeivel; összetett formhívásnál TODO az eredeti kóddal.
- **Ablakok:** `show_window` / `hide_window` / `show_view` / `hide_view` → láthatósági mező.
- **Minden más gomb:** TODO és az eredeti Forms-kód (`//#region Eredeti Forms-kód`).

## 4. Backend

A `backend-plan.json` `api.actions` bejegyzése mostantól a gomb kódjának bemeneteit is tartalmazza:
`reads` (BLOKK.MEZŐ) és `parameters` (GLOBAL / PARAMETER / SYSTEM). Ebből készül a gomb kérése. A generált Java nem
változott.

## Teendő

- **A korábban generált képernyők** változatlanok maradnak (CREATE_ONCE). Az új vázat új célmappába generálva kapod.
- **A gombok TODO-i:** a válasz feldolgozása, az értékek Oracle-formára alakítása (checkbox, dátum), a
  mezőállapotok, a LOV-javaslatok és a mentés összeállítása fejlesztői feladat.

## Ellenőrzés

- **`tests/test_screen_component.py`:**
  - nincs Forms-emuláció a komponensben (`text`, `data`, `blocks`, `setItemState` ...);
  - a gomb kérése a képernyő értékeivel, a :GLOBAL érték TODO-val, `FRM.*` nélkül;
  - a gombok kérése Node-ban lefuttatva;
  - tsc (strict) és ngc (strictTemplates) a generált képernyőkre.
- **`test_query_actions`:** a lekérdező gomb kérése és a sorok a táblázatban, Node-ban is.
- **A többi frontend-teszt** (mezőállapotok, navigáció, mentési és képernyőpont, indítás) a vázhoz igazítva.
