# 4.20 – Frontend: rövidebb, Angularosabb képernyők

A generált komponensben már csak a képernyő saját része van: az adatai, az egysoros végpontmetódusok és a sablon.
Minden általános logika a közös futtatóba került (`frm-forms-screen.ts`, **4-es változat**). Ez egyszer van meg a
projektben, és minden képernyő ezt örökli.

## Számok

| Minta | 4.19 | 4.20 |
|---|---:|---:|
| Felmérési replika (`felmeres_replika_fmb.xml`, `--awu-azon`) | 969 sor | 123 sor |
| Headstart golden (`tests/golden/headstart`) | 468 sor | 73 sor |
| Mezőállapotos minta (SET_ITEM_PROPERTY) | 330 sor | 57 sor |
| PL/SQL-es gombok, mentési lánc | 444 sor | 70 sor |
| CALL_FORM, kézi navigáció | 457 sor | 101 sor |

A futtató 589 sorról 1030 sorra nőtt, de projektenként csak egyszer van meg, képernyőnként nem ismétlődik.

## 1. Ami kikerült a komponensből

Ezek Forms-működést utánzó segédszerkezetek voltak. Angularban nincs rájuk szükség, ezért a modul kódjában már
nem szerepelnek:

- kurzorblokk-követés (`cursorBlock`) és régió→blokk térkép (`regionBlocks`);
- feliratlista (`fieldLabels`) és állapotcél-térkép (`stateTargets`);
- a teljes Oracle-név térkép: csak az eltérések maradnak (`oracleNames`), a többit a futtató a mezőnévből vezeti le
  (`ubiInptipKod` ↔ `UBI_INPTIP_KOD`);
- feliratkozás-nyilvántartás (`bindings`) és `ngOnDestroy`: a futtató `takeUntilDestroyed`-del iratkozik le;
- `TOAST_LIFE` konstans, `numberValidator`, `validationRules`, `lovRules` / `lovTargets`;
- blokkonkénti `…Structure`, `…Rows`, `…Selection` mezők és sortípus-interfészek;
- az `onFormGroupGenerated`, `onAction`, `onLovSearch`, `executeQuery`, `runAction` … metódusok másolatai.

A futtató a `GO_BLOCK` / `EXECUTE_QUERY` parancssorokhoz (amelyeket a backend küldhet) továbbra is számon tartja az
aktuális blokkot. Ez kizárólag a futtatóban van, a modul kódjában nem.

## 2. Egyszerűbb FormBlock-JSON

Az összes régió egy `structures` objektumba került, mezőnként egy sorral, egyszeres idézőjellel. A gomb- és
LOV-beállítást egy-egy segéd adja:

```ts
protected override readonly structures: Record<string, FormBlock.Structure[]> = {
  ctrl: [
    { type: 'text', ownId: 'CTRL.MODUS', formControlName: 'modus', labelText: 'Mód', col: '2', maxLenght: 1 },
    { type: 'button', ownId: 'CTRL.PB_KERES', labelText: 'Keresés', col: '2', ...this.button('CTRL.PB_KERES') },
  ],
  rendeles: [
    { type: 'autocomplete', ownId: 'RENDELES.STATUSZ', formControlName: 'statusz', labelText: 'Státusz', col: '3', ...this.lov('RENDELES.STATUSZ', 'LOV_STATUSZ') },
  ],
};
```

- **`...this.button(ownId)`:** a `btnSeverity: 'primary'` színt és az `onClick` bekötést adja.
- **`...this.lov(ownId, lov)`:** a lenyitót, az `optionLabel` / `optionValue` beállítást, a `suggestions` listát és
  a `completeMethod`-ot adja.
- **Validátorok a struktúrából:** a `validator: true` beállításból `Validators.required`, a
  `minLenght` / `maxLenght` beállításból hosszellenőrzés, a `regexRule` beállításból `Validators.pattern` lesz. Ezt a
  futtató végzi. A komponens `validators` adatában csak az marad, ami a struktúrából nem olvasható ki:
  - `FrmValidators.number({...})`;
  - `FrmValidators.date`;
  - a kötelező checkbox `FrmValidators.checked` szabálya;
  - az eltérő fix hossz.
- **A sablon:**
  ```html
  <ank-form-block [formStructure]="structures.ctrl" (formGroupGenerated)="onFormGroupGenerated('ctrl', $event)" />
  ```

## 3. Táblák: `frmTable` és `<frm-table>`

```ts
protected override readonly tables = {
  TETEL: frmTable(5, [['id', 'Tétel', '27.27%'], ['termek', 'Termék', '36.36%']]),
};
```

- **A sablonban:** `<frm-table [table]="tables.TETEL" />`.
- **Sorok és kijelölés:** a sorok a `tables.TETEL.rows`, a kijelölt sor a `tables.TETEL.selection` mezőben van.
- **Közös komponensek:** a táblázat, az eszköztár (`<frm-toolbar>`) és a Forms-alert (`<frm-alert>`) önálló
  komponens a futtatóban. Mindhárom `input()` / `output()` signalokat használ.

## 4. Bekötés adatként

A komponens nem írja meg, *hogyan* fut egy lekérdezés vagy egy LOV, csak azt, *mit* hív:

```ts
protected override readonly queries: Record<string, FrmQuery> = {
  TETEL: { call: request => this.searchTetel(request), criteria: { rendelesId: 'RENDELES.id' } },
};
protected override readonly lovs: Record<string, FrmLov> = {
  LOV_STATUSZ: { call: request => this.lovLovStatusz(request), columns: { KOD: 'RENDELES.STATUSZ' } },
};
protected override readonly actionEndpoints: Record<string, FrmActionCall> = {
  'CTRL.PB_KERES': request => this.onCtrlPbKeres(request),
};

searchTetel(body: unknown) { return this.send('searchTetel', this.http.post(this.url('searchtetel'), body)); }
```

- **Ugyanígy adat:**
  - a CALL_FORM-navigáció (`navigations`);
  - a felismert gomblépések (`actionSteps`);
  - a mentési lánc (`commitBlocks`, `commitEndpoint`);
  - az alertek (`alertDefinitions`).
- **`send`:** a 4.19-es naplózást (`WFF.debug(this.modName + '.<metódus>', res)`) és hibajelzést
  (`WFF.err('Hiba', error)`) egy helyen, a futtatóban végzi. A végpontmetódus egy sor.

## 5. Mezőállapotok tömörebben

- **Egy hívás mezőnként:** ugyanannak a mezőnek az egymás utáni tulajdonságai egy hívásba kerülnek:
  `this.setItemState('B.MEGJ', { enabled: true, required: true })`.
- **A feltétel maga az érték:** az `IF feltétel THEN … PROPERTY_TRUE ELSE … PROPERTY_FALSE` mintából egy sor lesz:
  `this.setItemState('B.EXTRA', { visible: this.cmp(…) })`.
- **Nincs külön metódus:** az egyutasításos kezelő közvetlenül a térképbe kerül
  (`'B.PB_ENGED': () => this.setItemState('B.PB_MENT', { enabled: true })`), illetve a konstruktorba.

## 6. Kommentek

- **Kikerült:** a függvények és a meződefiníciók feletti magyarázó kommentek, a doc-kommentek (`/** … */`) a
  komponensből és a futtatóból is.
- **Maradt, mert a továbbfejlesztést segíti:**
  - a TODO-importsorok;
  - a kézi navigációnál az eredeti Forms-kód és a mintasor;
  - a képernyőn nem szereplő mezők állapotáról szóló jelzés;
  - a futtatóban a szakaszelválasztók.

## Átállás

1. **Cseréld a projektben a `frm-forms-screen.ts`-t a 4-es változatra** (`FRM_FORMS_SCREEN_VERSION = '4'`). A
   feladatnál a Segédfájlok közül tölthető le. A telepítés előnézete jelzi, ha a projektben még régebbi változat
   van. A 4.20-as képernyők a 3-as futtatóval nem fordulnak le.
2. **A már meglévő (4.19-es vagy régebbi) képernyők** a 3-as futtatóhoz készültek, és nem frissülnek maguktól (a
   komponens CREATE_ONCE fájl). Két lehetőség van:
   - generáld őket újra új mappába, és emeld át a kézi módosításokat;
   - vagy tartsd meg nekik a 3-as futtatót más néven (pl. `frm-forms-screen-v3.ts`), és az ő importjukat
     írd át erre.
3. **A webes felület nem változott,** a `web-dist`-et nem kell újraépíteni.

## Ellenőrzés

- **Teljes tesztkészlet:** 576 teszt. A 101 hibás teszt ugyanaz, mint a `main` ágon (a repóból hiányzó
  mintabemeneteket keresik), új hibás teszt nincs.
- **Szigorú `tsc`** (`strict`, `noUnusedLocals`, `noImplicitOverride`):
  - a `test_screen_runtime` három kimenetre futtatja: a replikára, a checkboxos és mezőállapotos egyszerű
    képernyőre és a listaképernyőre;
  - kézzel további 14 generált képernyőre is lefutott (mezőállapotok, PL/SQL, gomblépések, LOV, validációk,
    navigáció, ablakok, headstart, csak frontend): mind hibátlan.
- **Angular-sablonok:** a generált sablonok és a futtató három komponensének sablonja hibátlanul feldolgozható
  (`@angular/compiler` 20).
- **Node-szimulációk az új futtatón:** a lekérdezésre váró és utána folytatódó gomb, a lekérdező gomb és a mentési
  pont a kód közepén.
- **A headstart golden kimenet** az új alakra frissült; a `frm-forms-screen.ts` is része lett.
