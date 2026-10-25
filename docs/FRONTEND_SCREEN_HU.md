# Egyfájlos Angular képernyőváz — 4.4.2

**4.4.2 javítás:** az `ObjectGroupChild Type` és a korábbi típusmező-nevek egységes kezelése, ellenőrzött aliasokkal. [Részletek és frissítés](docs/VALTOZASOK_4_4_2_HU.md).

**4.4.1 javítás:** az azonos nevű `Package Spec` / `Package Body` pár szabályos bemenet; mindkét forrás megmarad. [Részletek és frissítés](docs/VALTOZASOK_4_4_1_HU.md).

A `screen` mód az Oracle Forms képernyő szerkezetét követi: szűrők, találati táblák,
gombsorok, fülek és keretek. A koordináták az olvasási sorrendet, a sorokat és az
arányos oszlopszélességet adják meg. Nincs abszolút pozicionálás vagy pixelmásolat.

## Gyors indítás

A Python-függőségek nem változtak. A csomag tartalmazza az újraépített webes
kezelőfelületet; annak használatához nem kell npm-et futtatni.

```powershell
.\.venv\Scripts\python.exe -m frm_forms.web --port 8000
```

Nyisd meg a `http://localhost:8000` oldalt. A generálási módnál válaszd a
**Képernyőváz — egy TS, FormBlock + Optimus** lehetőséget. Új beállításoknál ez az
alapértelmezett. A korábban elmentett saját beállításaid megmaradnak, ezért azoknál
kézzel válts át. A modul neve lehet például `fadlek`.

A projekt gyökeréből, parancssorral:

```powershell
.\.venv\Scripts\python.exe -m frm_forms migrate .\frm_ank_fadlek_fmb.xml --screen --frontend-only --module fadlek --config .\examples\config-screen.json --out .\build\fadlek-screen --zip
```

A `--frontend-only` elhagyásával a korábbi Java rétegek és gombvégpontvázak is
elkészülnek. A képernyőváz mód nem engedélyez adatbázis-műveleteket: azokhoz továbbra
is ellenőrzött üzleti implementáció szükséges.

Ha megvan a hivatkozott object library XML-exportja, add hozzá például:

```powershell
--olb .\qmsolb65_olb.xml
```

Az exportálás továbbra is `frmf2xml USE_PROPERTY_IDS=NO DUMP=ALL OVERWRITE=YES`.
A hiányzó öröklés és triggertörzs bekerül a migrációs jegyzetbe. Az új mód
prezentációs javaslatokat adhat. Az eredeti XML változatlan; a szigorú besorolás
az `analysis/ui-model-strict.json` fájlban marad meg. Az `analysis/ui-model.json`
a generált képernyő típuskövetkeztetéseit is tartalmazza, azok indoklásával együtt.

## Kimenet

| Fájl | Tartalom |
|---|---|
| `frontend/fadlek/fadlek.component.ts` | Egy szerkeszthető komponens: inline template, a képernyő adatai (struktúrák, táblaoszlopok) és mindaz, amit a képernyő használ, egyszerű metódusként (végpontok, lekérdezés, LOV, gombok, mentés) |
| `frontend/wf-table.ts` | A táblázat (`<wf-table>`, a p-table köré): egyszer kell a projektbe tenni, külön letölthető; csak táblázatos képernyőnél készül |
| `frontend/fadlek/MIGRATION_NOTES.md` | Blokkok, adatforrások, gombhívások, LOV SQL/return mapping, öröklési hiányok |
| `analysis/screen-plan.json` | Géppel olvasható elrendezési döntések és kihagyott technikai mezők |
| `analysis/screen-overrides.template.json` | Mezőazonosítók és forráslenyomatok a tartós kézi döntésekhez |
| `analysis/screen-overrides.applied.json` | Aktív felülbírálások esetén a ténylegesen használt szabályfájl |
| `analysis/ui-model.json` | A generált vezérlőtípusok, típuseredet, validációk és az eredeti property-k |
| `analysis/ui-model-strict.json` | A screen típuskövetkeztetése előtti szigorú besorolás |
| `analysis/discovery/form-explorer.html` | Offline kereshető modultérkép és az eredeti kódok |
| `analysis/source.xml`, `effective-source.xml` | Eredeti és feloldott forrás |

Kipróbálható, generált példák: `screen-output/fadlek`, `screen-output/fadlek-overrides`,
`screen-output/screen-layout`, `screen-output/screen-dialogs`,
`screen-output/screen-dialogs-accordion`, `screen-output/screen-pages`, `screen-output/ui-features`
és `screen-output/ui-features-accordion`. A korábbi `sample-output` és
`review-output` a régebbi generálási módok referenciái.

## FormBlock és Optimus importok

Az Angular- és a publikus Optimus-importok automatikusak. Kizárólag
`@openng/optimus-ui/*` komponensek készülnek; nincs PrimeNG-függőség.
A fogadó projektben az Optimus témája és a Tailwind már legyen konfigurálva.

A privát FormBlock importútvonala nincs a feltöltött projektben. Ezért alapból a
generált fájl elején egyetlen TODO jelzi a `FormBlocksComponent` és a
`FormBlock.Structure` importját. Töltsd ki egyszer a saját csomagútvonalaiddal,
vagy konfiguráld az automatikus importot az alábbi kulcsokkal:

| Kulcs | Érték |
|---|---|
| `emit_imports` | `true` |
| `optimus_import_path` | A valódi FormBlocksComponent exportútvonal |
| `optimus_form_block_symbol` | Üresen `FormBlocksComponent`; más exportált névnél azt add meg |
| `form_block_type_import_path` | A valódi FormBlock namespace exportútvonal |

A képernyőváz nem igényel environment-importot: a szerver- és modulútvonalat a `ServiceBase.url()` adja.
A vezérlők a megadott `FormBlock.Structure` szerződést követik, például
`labelText`, `btnSeverity`, `colBefore`, `maxLenght`, `suggestions`.

Az önálló `button` típus felirata a `labelText` (ahogy a `ButtonGroup` is ezt
használja); a `btnLabel` az `inputGroup` hozzáfűzött gombjához tartozik. Ha a
saját FormBlock mégis `btnLabel`-ből olvassa a gombfeliratot:
`screen_button_label_property: "btnLabel"`. A gomb súgószövege (`Hint`)
`tooltipData` lesz, nem `inputInfo`.

## Keretrendszer-katalógus

A Designer/Headstart formok generált keretrendszer-objektumait egy szerkeszthető
katalógus sorolja fel: `frm_forms/data/framework-catalog.json`. Saját példány a
`framework_catalog` beállítással adható meg (a config fájlhoz képest relatív útvonal).
Minden alábbi döntés erre a fájlra hivatkozik a `MIGRATION_NOTES.md`-ben; név alapján
semmi nem dől el, ami nincs a katalógusban.

| Kulcs | Mire hat |
|---|---|
| `blocks` | Az itt felsorolt, **adatforrás nélküli** blokkok nem kerülnek a képernyőre (pl. `CALENDAR`, `QMS$TRANS_ERRORS`). Adatforrással rendelkező azonos nevű blokk megjelenik, `FRAMEWORK_BLOCK_KEPT` jelzéssel. |
| `date_picker_calls` | Ha egy egyrekordos mező `KEY-LISTVAL` triggere csak ezt hívja (Headstart: `qms$calendar.key_listval`), a mező natív naptár lesz, a naptár-LOV nélkül. Char típusú forrásnál `DATE_PICKER_TEXT_VALUE` jelzi, hogy a szöveggé alakítás a host feladata. |
| `call_prefixes` | Keretrendszer-diszpécserhívások (`qms$`, `cgnv$`, `cgte$`…). A triggerbesorolás és a gomblépések ezeket nem számítják teendőnek. |
| `empty_hint_templates` | Felirat nélküli mező, amelynek súgója kitöltetlen sablon (pl. `Adja meg a(z)  értékét`): technikai mező, nem jelenik meg; a rejtett mezők között szerepel. Ellenőrzött felülbírálással megjeleníthető. |
| `spacer_items` | Elrendezési térközt jelölő mezők mintái (`ITEM` vagy `BLOKK.ITEM`, `*` és `?` helyettesítővel; alapértelmezés: `L_URES`, `L_URES_*`). Lásd: Térköz-mezők. |

## Térköz-mezők

A `spacer_items` mintára illeszkedő mező (alapból `L_URES` és `L_URES_*`) nem input lesz,
hanem a szélességének megfelelő üres hely: `{ type: "label", ownId, labelText: "", col }`
(`colBefore`/`colAfter`, ha van). Nincs `formControlName`, kezdőérték és validáció, és
táblázatban nem lesz belőle oszlop. A típus a `screen_spacer_type` beállítással `text`
vagy `divider` is lehet.

Az elnevezési konvenció dönt. Ha a mezőnek a forrásban viselkedése is van (például
az OLB-ből örökölt keretrendszeri trigger, LOV vagy adatbázis-oszlop), akkor is üres
hely marad, és ezt `SPACER_BEHAVIOUR` jelzés mutatja. Ellenőrzött `widget`
felülbírálással mezőként visszaállítható. Ha egy saját katalógusból hiányzik a
`spacer_items` kulcs, az alapminták érvényesek; `[]` kikapcsolja a felismerést.

## Kitöltött sorok

A mezők alapból kitöltik a sort (`screen_preserve_gaps: false`). Üres hely csak a
térköz-mezők helyén marad; a gombsorok jobbra igazítása megmarad. Bekapcsolva a forrás
vízszintes térközei `colBefore`-ként jelennek meg.

## Mezőhosszak (segítő JSON)

A szöveges beviteli mezők minimális és maximális hossza egy JSON-fájlban adható meg:

```json
{ "version": 1, "fields": { "ubiInptipKod": { "min": 2, "max": 10 }, "V_ELEK_ADLAP.nev": { "max": 80 } } }
```

- **Kulcs:** a `formControlName`. Ha több blokkban is előfordul, `BLOKK.formControlName`
  alakban adható meg; ez erősebb a sima névnél.
- **Hatás:** a megadott érték felülírja a Forms MaximumLength/FixedLength értékét, és
  FormBlock `minLenght`/`maxLenght` lesz belőle; az ellenőrzés a FormBlocké, a komponensbe nem kerül
  külön validátor.
- **Használat:** `migrate`/`batch --field-lengths fajl.json`, a konfigurációban
  `screen_field_lengths`, vagy a webes felület „Mezőhosszak” feltöltése. Egy fájl a
  batch összes formjára érvényes.
- **Jelzések:** a formban nem talált név `FIELD_LENGTH_UNKNOWN`, a nem szöveges mezőre
  vonatkozó szabály `FIELD_LENGTH_IGNORED` jelzést kap; ezek a `MIGRATION_NOTES.md`-ben látszanak.
- **Minta:** minden futás ír egy kitölthető mintát a form szöveges mezőivel és a jelenlegi
  hosszakkal: `analysis/field-lengths.template.json`.

## Mezőállapotok (SET_ITEM_PROPERTY)

4.27 óta a képernyő nem fordítja le a triggerek `SET_ITEM_PROPERTY` hívásait (ENABLED, VISIBLE/DISPLAYED,
REQUIRED, UPDATE/INSERT_ALLOWED): a generált komponens csak a keret, Forms-emulációt nem tartalmaz.

- **Kezdőállapot:** a mező Forms-tulajdonságai (Enabled, UpdateAllowed, Required ...) továbbra is a FormBlock
  struktúrájába kerülnek (`disabled`, `readonly`, `validator`).
- **Futás közbeni állapot:** fejlesztői feladat, a FormBlock-struktúra (`structures[...]`) és a FormGroup
  (`forms[...]`) segítségével. A gombnyomásra állapotot állító gomb metódusa TODO-t és az eredeti Forms-kódot kapja.
- **Leltár:** a `RUNTIME_COVERAGE.md` és az `analysis/runtime-coverage.json` a triggereket kézi teendőként listázza.

## Toast és gombok

- **ToastService:** minden generált komponens `toast` néven megkapja:
  `protected readonly toast = inject(ToastService)`. Importja a `toast_service_import_path`
  (és `emit_imports: true`) beállításból készül; ennek hiányában TODO-megjegyzés jelzi, hogy
  a saját csomagodból kell importálni. A generált váz maga nem hívja (4.27): a fejlesztő üzeneteihez van ott.
- **Hívás:** mind a négy paraméterrel: `this.toast.success(cím, részletek, true, élettartam)`
  (ugyanígy `warning` és `danger`). A harmadik paraméter (`save`) az előzményekbe mentést kéri.
- **Élettartamok:** `toast_life_ms` (alap: success 3000, warning 8000, danger 6000 ms), a
  komponensben `protected readonly toastLife = { success: 3000, warning: 8000, danger: 6000 };`.
- **Backend-hiba:** a végpontmetódus `catchError`-ja `WFF.err('Hiba', error)` hívással jelez.
- **Gombok:** a gomb a struktúrában `btnSeverity: 'primary', onClick: () => this.on<Gomb>Click()`; a
  metódus a komponensben van, a gomb Forms-kódja szerint (lásd lent).

## Tartomány-elválasztók

Ha egy mező promptja csak írásjel (`-`, `–`, `/`, `:`…), és ugyanabban a sorban
mező áll előtte, a prompt a két mező közé kerül `type: 'label'` elemként, a mező
saját felirata üres lesz — így a két mező egy vonalban marad, ahogy Forms-ban.
Hely hiányában (`SEPARATOR_NO_ROOM`) vagy ha nincs előtte mező, marad feliratnak.

## Gomblépések és teendők

A `WHEN-BUTTON-PRESSED` triggerekből a gyakori Forms beépített lépések típusos
listává alakulnak (`goBlock`, `goItem`, `executeQuery`, `enterQuery`, `commit`,
`clearBlock`, `clearForm`, `exitForm`, rekordnavigáció, `showWindow`/`hideWindow`,
`showCanvas`/`hideCanvas`, literális `message`). Csak akkor, ha a trigger minden
utasítása felismert; feltétel, változó vagy saját eljárás esetén `steps: null`, a
jegyzet pedig kilistázza a saját hívásokat.

A gomb `on<Gomb>Click()` metódusa (4.27) csak a HTTP-kérést és az ablakok láthatóságát építi ki a lépésekből:

- `go_block('RESULT'); execute_query;` → a RESULT blokk keresésének kérése, a feltételek a képernyőről, a sorok a
  táblázatba (`this.resultRows = res.rows ?? [];`);
- `commit_form` → `this.save();`;
- `show_window('EDIT')` → `this.windowVisible['EDIT'] = true;` (ugyanígy `show_view` / `hide_view`);
- a fókuszlépések (`goItem`, rekordnavigáció, `enterQuery`, `listValues`) kimaradnak.

Ha a gombnak más lépése is van (`clear_block`, `message`, `exit_form` ...), vagy egy lépéshez nincs mit hívni
(például nincs a blokkhoz generált lekérdezés), a gomb TODO-t kap az eredeti Forms-kóddal.

A `MIGRATION_NOTES.md` első szekciója a tényleges teendőket összesíti; a
**Triggerek besorolása** a triggereket keretrendszeri / vegyes / saját / üres
csoportra bontja, a **Billentyűkezelés** a saját hívást tartalmazó `KEY-*`
triggereket, az **Oracle-specifikus SQL** pedig a LOV- és blokklekérdezések
adatbázis-függő szerkezeteit (csak adatbázis-csere esetén teendő).

## Képernyő-előnézet

Minden futás ír egy `analysis/screen-preview.html` fájlt: a generált képernyők
úgy, ahogy megjelennek, vagyis a fő képernyő, a párbeszédablakok, a fülek, a rétegzett régiók,
a táblázatok és a térközök. A képernyők és fülek között szkript nélkül, CSS-sel
lehet váltani, így a ZIP-ből megnyitva és a webes felület Előnézet fülén (sandboxolt
iframe-ben) is működik. Csak szerkezetet mutat: adat és működés nélkül.

## Elrendezés-előnézet

Minden futás ír egy `analysis/layout-preview.html` fájlt: bal oldalon az eredeti
Forms-canvas a koordinátákból (keretek, szövegek, promptok), jobb oldalon a
generált FormBlock-rács. Offline, szkript nélküli, böngészőben megnyitható — a
Forms-futtatókörnyezet nélküli előtte/utána összevetéshez.

## Listanyitó gombok

Headstart/Designer formokban a LOV-os mező mellett gyakran külön, keskeny gomb
áll, amelynek WHEN-BUTTON-PRESSED triggere csak annyi, hogy `go_item` a mezőre,
majd `do_key('LIST_VALUES')` vagy `LIST_VALUES`. Ha a cél mező saját lenyitó
vezérlővel jelenik meg (autocomplete, dropdown, multiselect, naptár), a gomb
nem készül el külön: beolvad a mező saját lenyitó gombjába, és a mező megkapja
a felszabaduló oszlopokat.

A felismerés a trigger tartalmán alapul, nem a mező vagy a szülőobjektum nevén.
Csak `NULL` és egy `GLOBAL` változóba másolt literál (Headstart egérpozíció-
könyvelés) tehető félre mellette; bármilyen feltétel, értékadás, más hívás vagy
kivételkezelő esetén a gomb megmarad. A beolvasztott gombokat a
`MIGRATION_NOTES.md` **Beolvasztott listanyitó gombok** táblázata és az
`ui-model.json` `SCREEN_FOLDED_LIST_BUTTON` review issue-ja sorolja fel; a
megtartott jelölteket `LIST_BUTTON_KEPT` jelzi az okkal. Ellenőrzött
felülbírálás (`widget: "button"`) esetén a gomb mindig megmarad.
Kikapcsolás: `screen_fold_list_buttons: false`.

## Elrendezési szabályok

- Egy canvason a régiók a forrás Y/X sorrendjét követik, az adatblokkok XML-sorrendjétől függetlenül.
- Azonos sorban arányos, összesen 12 oszlopos FormBlock mezők készülnek. A 18/24 oszlopos profil is használható, ha a saját FormBlock támogatja.
- Kis Y-eltérés még közös sor lehet: a különbség legfeljebb a kisebb mezőmagasság `screen_row_tolerance` része (alapból 0,25), a függőleges átfedés legalább 60%, a mezők vízszintesen nem fedik egymást. A sor minden tagjának illeszkednie kell; a közeli mezők lánca nem olvaszt össze külön sorokat. Azonos Y továbbra is közös sor; hiányzó vagy 1-es helyőrző magasságnál nincs közelítő illesztés. `0` tolerancia visszaadja az azonos Y szerinti viselkedést.
- `screen_preserve_gaps: true` esetén a legalább egy rácsoszlopnyi belső vízszintes térköz `colBefore` lesz. Hiányos koordinátából nincs térköztalálgatás; a kézi sorrend/sor döntés elsőbbséget élvez. Nem minden Forms-távolságot tart meg: ez továbbra is arányos, átrendezhető képernyőváz.
- A csak gombokat tartalmazó sor jobbra igazodik.
- A több rekordos régióból `p-table` készül, adatbázishoz nem kötött blokknál is. Az item `ItemsDisplay` beállítása külön kezelhető.
- A táblázat szerkeszthető mezőcsoportok helyett egyszerű értékeket jelenít meg. A szerkesztőfunkciót a fejlesztő köti be, ha kell.
- Az XML-ben létező tab page-ekhez `p-tabs` készül; `screen_tab_layout: "accordion"` esetén `p-accordion`.
- A címes grafikai keret `p-fieldset`, a statikus szöveg egyszerű HTML. A főcímet nem ismétli meg ugyanazzal a statikus felirattal.
- A mező teljes téglalapjának bele kell férnie a keretbe. A legkisebb befoglaló keret nyer; az egymásba ágyazott keretek a kimenetben is egymásba kerülnek. A statikus szöveg ugyanígy a megfelelő kerethez kapcsolódik.
- Az ablakokat Window → Canvas → régió/blokk → item kapcsolatok alapján kezeli. Modal=true, WindowStyle=Dialog és másodlagos Document ablak esetén ablakonként egy `p-dialog` készül, az eredeti modalitással. A nyitási logikát az eseménykezelőben kell bekötni.
- `Visible=false` és létező canvasok mellett canvas nélküli item nem kerül a képernyőre. A mező az elemzésben megmarad, és a kimaradás oka látszik.

Az örökölt segédblokkokat nem a nevük alapján törli. Például egy ténylegesen látható
`CALENDAR` blokk megmarad; a fadlek canvas nélküli naptársegédje nem jelenik meg.

A jegyzet azonosítja az egy sorban lévő LOV-kódmező, köztes gomb és visszaírt
megnevezés kapcsolatát is. A bizonyíték az explicit `ReturnItem` és a közös sor;
ez nem írja át a gomb működését, és nem találgat kapcsolatot csak a mezőnevekből.

## Modulfüggetlen ablakkezelés

A generátor nem használ FADLEK-, blokk- vagy ablaknévhez kötött kivételeket.
A canvas tulajdonosa a `Canvas.WindowName`; ennek hiányában az egyértelmű
`Window.PrimaryCanvas` kapcsolat, illetve egyetlen deklarált ablak esetén
jelölt következtetés. Többértelmű vagy hibás hivatkozásnál beszédes hibát ad.
Az OLB-ből feloldott Window-property-ket is figyelembe veszi.

| Forms-szerkezet | Generált felület |
|---|---|
| Egyetlen használt, nem modális Document ablak | A komponens főoldala |
| Több nem modális Document ablak | A FirstNavigationBlock szerinti ablak a főoldal; a többi nem modális `p-dialog` |
| `WindowStyle="Dialog"`, `Modal="false"` | Nem modális `p-dialog` |
| `Modal="true"` | Modális `p-dialog`, a WindowStyle-tól függetlenül |
| Több canvas ugyanabban a dialogablakban | Egy közös `p-dialog`, benne minden hozzárendelt régió |
| Tab canvas a dialogban | `p-tabs` vagy `p-accordion` a dialog tartalmában |
| Stacked canvas | Kapcsolható régió a tulajdonos ablakban; a forrás Visible állapotával |
| Több Content canvas ugyanabban az ablakban | Egy aktív tartalmi canvas; váltás a `showCanvas` metódussal |
| Deklarált, megjelenített tartalom nélküli technikai ablak | Auditban megmarad; nem hoz létre üres párbeszédablakot |

Ha a `FirstNavigationBlock` alapján sem egyértelmű a főablak, add meg a profilban
a `screen_primary_window` értékét, vagy töltsd ki a webes **Főablak neve** mezőt.
Az érték a pontos `Window.Name`. Modulváltáskor ellenőrizd a korábban mentett
beállítást. A program nem választ a nevek jelentése vagy az XML ablaksorrendje alapján.

Az azonos Window alá tartozó, egyszerre váltott Content canvasok kezdő oldala a
`PrimaryCanvas`. Ennek hiányában az első látható Content canvas szerepel
ellenőrizendő következtetésként. A stacked régiók abszolút átfedése és z-sorrendje
nem alakul át automatikusan webes viselkedéssé; a váltási szabályokat kösd be.

### Ablakok és canvasok a generált komponensben

Az állapot csak az azt igénylő modulokba kerül: például dialog, rejtett ablak/canvas,
stacked régió vagy több Content canvas esetén. Egyszerű képernyőn nem keletkezik
ablakkezelő kód. Az állapot a komponens sima mezője (4.26):

```typescript
protected readonly windowVisible: Record<string, boolean> = { MAIN: true, EDIT: false };
protected readonly canvasVisible: Record<string, boolean> = { PAGE: true, EXTRA: false };

protected onPbNyitClick(): void {   // show_window('EDIT'); show_view('EXTRA');
  this.windowVisible['EDIT'] = true;
  this.canvasVisible['EXTRA'] = true;
}
```

A sablon ezeket köti: `<p-dialog [(visible)]="windowVisible['EDIT']" …>`, `@if (canvasVisible['EXTRA']) { … }`,
több Content canvasnál `@if (activeContentCanvas['W'] === 'C') { … }`. Több ablak egyszerre is nyitva
lehet; egyik zárása nem zárja be a másikat.

Ha a bezárást validálni kell, a dialógus `(visibleChange)` eseményére köss saját metódust. A
`CloseAllowed=false` elrejti az X gombot; hiányában a generátor true fallbacket használ. Az Esc és
háttérkattintás nem zárja az ablakot. Ablakbezáráskor nincs automatikus mentés vagy adat-visszavonás.

A popupok kezdetben zártak. Az XML startup- és gombtriggerei nem futnak le;
a jegyzetben megtalálod a `SHOW_WINDOW`, `HIDE_WINDOW`, `SHOW_VIEW`, `HIDE_VIEW`,
`GO_BLOCK`, `GO_ITEM` és `SET_WINDOW_PROPERTY` hivatkozásokat, forrássorral együtt.
Az explicit célok feloldását, a dinamikus és a meg nem jelenített célokat külön
jelöli. Feltételes PL/SQL-ből nem készül feltétel nélküli ablaknyitás.

Kipróbálható minták: `examples/screen-dialogs_fmb.xml` (főablak, három külön
popup, fülek és rejtett stacked régió) és `examples/screen-pages_fmb.xml`
(váltott Content canvasok). A generált megfelelőik a `screen-output` alatt vannak.

A leképezéshez használt háttér: [Oracle konténerobjektumok](https://docs.oracle.com/cd/E26401_01/doc.122/e22961/T302934T308158.htm),
[Optimus UI Dialog](https://optimus.openng.org/dialog). A stacked canvas ablakon
belüli elhelyezését, valamint a dialog modalitásának külön kezelését ezekkel és
a telepített Optimus 2.0.2 komponenssel is ellenőriztük.

## Tartós, ellenőrzött felülbírálások

1. Generálj egy képernyővázat, majd másold ki az `analysis/screen-overrides.template.json` fájlt a projektedbe.
2. Csak a módosítandó mezők `reason` és `set` részét töltsd ki. A `source_fingerprint` értékét hagyd meg. Az üres `set: {}` sorok inaktívak, akár törölhetők is.
3. Add meg a fájlt a webes felület **Képernyő-felülbírálások** mezőjében, vagy a CLI `--screen-overrides` kapcsolójával. A webes feladat újrapróbálása is megtartja ugyanazokat a döntéseket.
4. Új célmappába generálj, hogy a módosított komponens is elkészüljön. A `--regenerate` továbbra is megőrzi a már létező komponensedet.

A `set` objektum például:

```json
{"widget": "checkbox", "label": "Elektronikusan beérkezett", "col": 4}
```

Ez csak a mezőszabály `set` része; a teljes fájlhoz használd a generált sablont.
Az `examples/fadlek-screen-overrides.json` teljes, a mellékelt FADLEK-forráshoz
kötött példa. Ellenőrzöttként rögzíti a hat kikövetkeztetett vezérlőtípust és
olvasható feliratot ad a LOV melletti gombnak:

```powershell
python -m frm_forms migrate review-output/fadlek/analysis/source.xml --screen --frontend-only --module fadlek --screen-overrides examples/fadlek-screen-overrides.json --out build/fadlek-reviewed
```

| `set` kulcs | Jelentés |
|---|---|
| `widget` | A támogatott szemantikai widgetnév, például `checkbox`, `button`, `text`, `autocomplete` |
| `label` | A megjelenített felirat; a forrás XML változatlan |
| `col`, `col_before` | Mezőszélesség és előtte hagyott rácsoszlopok; csak űrlapon |
| `row` | Nullától induló sorazonosító az adott régión belül; csak űrlapon |
| `order` | Sorrend a sorban; táblázatnál az egész régió oszlopsorrendje; a nem módosított mezők XML-sorrendindexét használja |
| `group` | A gyökér `groups` objektumában definiált csoport azonosítója; csak űrlapon, külön fieldsethez |
| `readonly`, `enabled` | Az adott vezérlő által kifejezhető állapot; nem ad backend műveleti engedélyt |

A csoport definíciója például `"groups": {"search": {"label": "Kódkeresés"}}`.
Egy csoport egy blokkon, canvas/fülön és eredeti kereten belül használható. Ezzel
elkerülhető, hogy a felülbírálás eltüntesse az eredeti képernyőrészek határát.
Ha kézzel rendezel egy sort, célszerű minden tagján explicit `order` értéket adni.
Az el nem osztott rácsoszlopok a sor végére kerülnek; túlcsorduláskor hibát kapsz.
A táblázat csak olvasható adatoszlopain `enabled`/`readonly` nem állítható;
táblázatgombon az `enabled` használható. Adapteres helyőrzőn csak felirat/csoport
vagy támogatott widgetre váltás engedett.

A widgetváltás ellenőrzi a szükséges forrásadatokat: checkboxhoz eltérő
CheckedValue/UncheckedValue, listához opciók, autocomplete-hoz LOVName kell.
A meglévő LOV-kapcsolatot nem lehet egy másik widgettel csendben eldobni.
A Required és a többi validáció ezzel a fájllal nem kapcsolható ki.

A lenyomat az **öröklés feloldása utáni itemet**, gyerekelemeit/triggertörzsét és
a blokk megjelenítést/műveleteket befolyásoló alapadatait védi. Nem az egész fájl
hash-e: egy másik mező változása nem érvénytelenít minden döntést. Az XML
attribútumsorrendje, szerkesztői DirtyInfo és behúzás nem számít. Aktív szabály
eltérő lenyomatánál `SCREEN_OVERRIDE_STALE` hiba keletkezik. Ilyenkor generálj
szabályfájl nélkül új sablont, ellenőrizd újra a döntést, majd vedd át az új hash-t.

A felülbírálások bekerülnek a jegyzetbe és a `screen-plan.json` auditlistájába.
Típusváltásnál az `ui-model.json` `type_source: "override"` jelölést és indoklást
kap. A nyers XML, a property-eredet és a `ui-model-strict.json` megmarad.

## Típusjavaslatok és szövegek

`screen_infer_widgets: true` esetén egyetlen egyértelmű másodlagos jel alapján a
Text Item képernyővázbeli típusa javasolható:

- `WHEN-BUTTON-PRESSED` trigger: gomb;
- eltérő, nem üres CheckedValue és UncheckedValue: checkbox.

Az eltérő vagy ellentmondó jelekhez nincs találgatott vezérlő. Minden ilyen döntés
szerepel a `MIGRATION_NOTES.md` fájlban. Kikapcsolás: `screen_infer_widgets: false`.
Az `ui-model.json` `widget`, `type_source` és `inference_reason` mezője egyezik a
képernyőtervvel. Az `item_type` és `item_type_property_source` az eredeti XML
értékét és annak eredetét mutatja. A jegyzetben külön **Kikövetkeztetett típusok**
táblázat található. A szigorú generálási mód típusellenőrzése változatlan.

`screen_repair_display_text: true` esetén a feliratok hibás UTF-8/cp1250/cp1252/latin1
dekódolását csak visszafordítható és a kódolási hibajeleket csökkentő esetben javítja.
Nem helyesírás-ellenőrző. A javítások tételesen szerepelnek a jegyzetben; az XML,
az SQL és a kezdeti adatértékek nem változnak. Bizonytalan forrásnál kapcsold ki.

## Validációk és meg nem valósított tulajdonságok

A szabályok a FormBlock tulajdonságai; a komponens nem tesz saját validátort a kontrollokra (4.26):

| Forms-tulajdonság | FormBlock |
|---|---|
| Required | `validator: true` (checkboxnál nem: a false is érvényes érték) |
| MaximumLength / FixedLength | `maxLenght` / `minLenght` |
| CaseRestriction | `regexRule` |
| Lowest/HighestAllowedValue egész számnál | `min` / `max` |

Decimális mezőnél a minimum/maximum, precision és scale ellenőrzését a backend végzi; ha
a felületen is kell, a képernyőn kell felvenni (a jegyzet „Adapter szükséges” sorként jelzi).
A nagy decimális értékek stringként maradnak meg; a safe-integer vezérlő kizárólag
biztonságos egész számot fogad el.
CaseRestriction esetén ellenőrzés készül, automatikus betűátalakítás nem.
A támogatott dátummaszk `dateFormat`-ként jelenik meg; ismeretlen FormatMaskhoz
adapter szükséges. A csak olvasható táblákhoz nem készül szerkesztővalidáció.

A migrációs jegyzet külön táblázatban jelzi a validációk megvalósítását és
hiányait, a mezőnkénti CaseRestriction/AutoSkip/navigációs property-ket, valamint
a blokk InsertAllowed, UpdateAllowed, DeleteAllowed, QueryAllowed és
NavigationStyle értékeit. Az engedélyek forrása is látható; a `default` a
generátor fallbackje, nem bizonyított Oracle-alapérték.

A boilerplate-leltár a keretek és statikus szövegek célját is tartalmazza:
fieldset-legenda, önálló szöveg, már megjelenített főcím, vagy fel nem használt
elem indoklással. Az SQL-részletekben a kódolt sortörések és tabok olvasható
formában jelennek meg; a nyers SQL és XML a forrásauditban változatlan.

## Backend-hívások (céges minta)

A generált komponens útvonalon elérhető oldal, nem gyerekkomponens: nincs `@Input` és `@Output`.
A céges `ServiceBase`-t örökli; közös futtató nincs. Minden végpontnak saját metódusa van:

```ts
export class XyComponent extends ServiceBase {
  private readonly http = inject(HttpClient);

  aitSearch(body: unknown) {
    return this.http.post<any>(this.url('ait/query/search'), body).pipe(
      tap(res => WFF.debug(this.modName + '.aitSearch', res)),
      catchError(error => {
        WFF.err('Hiba', error);
        return EMPTY;
      }),
    );
  }
}
```

- **Végpontmetódusok:** minden generált végpontnak (lekérdezés, lista, LOV, gomb, mentés) saját
  metódusa van. A `this.url('…')` argumentuma a végpont neve úgy, ahogy a CL használja (céges
  formátumban pontosan a `…_NAME` érték). Erre rákeresve a hívás a CL-ben, a DPS-ben, a WBS-ben és a
  komponensben is megtalálható. A szerver- és modulútvonalat a `ServiceBase.url()` teszi elé. A válasz típusa
  `any` (4.27): saját interfész és válaszboríték-kezelő (`data()`) nincs.
- **Ki hívja:** a gombok metódusai (`onPbKeresClick()`), a `save()` és az indítási végpontot a konstruktor. A
  többi (például a LOV vagy egy gomb nélküli lekérdezés) kész metódus, a fejlesztő hívja.
- **Naplózás:** minden sikeres válasz legelőször a `WFF.debug(this.modName + '.<metódus>', res)` hívásba
  kerül (`tap`), ahol a `<metódus>` a végpontmetódus neve. A `modName` (a modul útvonala) és a `router`
  a céges `ServiceBase`-ből öröklődik.
- **Hibák:** a `catchError` a `WFF.err('Hiba', error)` hívással jelez, és a hívás ott véget ér.
- **A gomb kérése (4.27):** a backend-akció kérése azokat a mezőket viszi Oracle-néven, amelyeket a gomb kódja
  olvas, a régió értékeiből (`getRawValue()`), a táblázatból a kijelölt sorból, átalakítás nélkül:

  ```ts
  const rendeles = this.forms['rendeles']?.getRawValue() ?? {};
  // TODO: a válasz feldolgozása (res.blocks: a visszaírt mezők, res.messages: az üzenetek).
  this.actionOnctrlpbujraszamol({ blocks: { RENDELES: { ID: rendeles.id, OSSZEG: rendeles.osszeg } }, parameters: {} }).subscribe();
  ```

  A :GLOBAL / :PARAMETER / :SYSTEM értékek `null`-ként, TODO-val szerepelnek a `parameters`-ben; a képernyőn nem
  szereplő mező `null` és TODO. A backend saját protokollértékei (`FRM.ALERTS`, `FRM.RESUME`, `FRM.COMMIT`) nem
  kerülnek a kérésbe. A checkbox booleanként, a dátum `Date`-ként megy: az Oracle-értékre alakítás, a visszaírt
  mezők, az üzenetek, az alertek, a képernyő- és mentési pontok, a Forms-utasítások (`commands`) fejlesztői feladat.
- **Válaszboríték:** céges módban a válasz `RestResponseDto`-ban érkezik: a lekérdező gomb TODO-ja jelzi, hogy a
  sorokat a boríték adatmezőjéből kell venni.
- **Be nem kötött gombok:** a kézzel átültetendő gomb metódusa TODO-t és az eredeti Forms-kódot (regionban)
  tartalmaz.
- **Importok:** a `ServiceBase`, a `WFF`, a `ToastService`, a `WfTable` és a FormBlock-osztályok a
  `java-imports.json` `/`-es bejegyzéseiből kapnak importot (lásd AUTOMATIZALAS_HU.md), például
  `"WfTable": { "ts": "../wf-table" }`. Ami nincs a térképben, arra TODO-sor és az
  `analysis/ts-imports.json` riport figyelmeztet.

## Táblázat: wf-table

A több rekordos blokk `<wf-table>` lesz (`frontend/wf-table.ts`, a p-table köré). A képernyő csak az
adatait adja:

```html
<wf-table [value]="tetelRows" [columns]="tetelColumns" [rows]="5" [(selection)]="tetelSelection" />
```

- **A p-table teljes API-ja:** a wf-table a PrimeNG/Optimus p-table minden bemenetét és kimenetét
  továbbadja (`[lazy]`, `[globalFilterFields]`, `[sortField]`, `[rowsPerPageOptions]`, `(onLazyLoad)`,
  `(onSort)` ...), a p-table alapértékeivel. Saját alapértékei: lapozó, 15 sor, egysoros kijelölés, kicsi
  méret, rácsvonalak, görgethető.
- **Sablonok:** a p-table sablonjai is átadhatók: `#caption` (például saját szűrőkhöz), `#header`, `#body`,
  `#emptymessage`, `#footer`, `#summary`, `#rowexpansion`, `#groupheader`, `#groupfooter`, `#colgroup`,
  `#loadingbody`, `#paginatorleft`, `#paginatorright`. Amit a képernyő nem ad meg, ott az alapértelmezett
  fejléc, sor és üres-szöveg jelenik meg a `columns` alapján:

  ```html
  <wf-table [value]="rows" [columns]="columns" [(selection)]="selected">
    <ng-template #caption><input pInputText (input)="szur($event)" /></ng-template>
    <ng-template #body let-row let-columns="columns">
      <tr [pSelectableRow]="row">@for (col of columns; track col.field) { <td>{{ row[col.field] }}</td> }</tr>
    </ng-template>
  </wf-table>
  ```
- **Sor végi gombok:** a blokk gombjai `[actions]` bemenetként jelennek meg a sor végén; a kattintás
  kiválasztja a sort, az `(action)` kimenet a gomb `ownId`-ját adja (`onRowAction`).
- **Egy helyen módosítható:** ha változik a p-table importja vagy selectora, vagy az Optimus valamelyik
  bemenetet nem ismeri, csak a `wf-table.ts`-t kell módosítani. A `WF_TABLE_VERSION` mutatja a változatot; a
  telepítés jelzi, ha a projektben régebbi van.

## Események és LOV

A gomb a struktúrában `onClick: () => this.on<Gomb>Click()`. A metódus a gomb Forms-kódja szerint (4.27):

1. navigál (CALL_FORM, OPEN_FORM, NEW_FORM) → `this.router.navigate([...], { queryParams })`, a paraméterek a
   képernyő értékeivel; összetett formhívásnál TODO az eredeti kóddal;
2. felismert lépések → a lekérdezés kérése, `save()`, ablak-láthatóság (lásd Gomblépések);
3. backend-akció (az eredeti PL/SQL vagy a Java-lekérdezés) → a végpont hívása a kód által olvasott mezőkkel;
   lekérdező gombnál a sorok a táblázatba kerülnek;
4. különben TODO az eredeti Forms-kóddal.

A LOV a struktúrában lenyitó autocomplete (`dropdown`, `optionLabel`/`optionValue`, üres `suggestions`). A LOV
végpontjának saját metódusa van (`lov<Név>`); a javaslatok betöltése (`completeMethod`) és a ReturnItem
mezők kitöltése fejlesztői feladat (4.27).

Az SQL, a paraméterek, a ReturnItem mapping és a master-detail kapcsolatok a
migrációs jegyzetben találhatók. Kliensből nem futtatunk SQL-t vagy PL/SQL-t.

## Újragenerálás és ellenőrzés

A `.component.ts` CREATE_ONCE fájl: `--regenerate` bájtonként megőrzi a kézi
módosításokat. A frissített képernyőtervet új mappába generáld, és onnan emeld át
a változásokat. Strict/scaffold/screen módváltáshoz új célmappa kell.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
cd web-ui
npm ci
npm run build
cd ..
.\.venv\Scripts\python.exe scripts\check_screen_angular.py
```

A frontend fejlesztői függőségei Angular 22.1.6 és Optimus UI 2.0.2; a Node
verziókövetelményt a `web-ui/package.json` tartalmazza. A compiler-próba valódi
Optimus komponenseket és a megadott privát FormBlock API tesztmását használja.
A saját FormBlock implementációját a fogadó alkalmazás buildjével kell ellenőrizni.

## Fordítás a saját Angular-projekteddel

Másold le az `examples/config-screen-host.example.json` fájlt, és az
`@sajat-projekt/form-block` helyőrzőt cseréld ki a tényleges csomagodra vagy
tsconfig-aliasodra. Ha relatív importot használsz, a `--component-dir` mappához
képest add meg. A projekt Angular/Optimus/FormBlock függőségei már legyenek telepítve.

```powershell
python scripts/check_screen_host.py --host-project C:/projektek/sajat-angular --tsconfig tsconfig.app.json --component-dir src/app --profile config-sajat-formblock.json --source frm_ank_fadlek_fmb.xml
```

A `--olb` ismételhető, a `--screen-overrides` itt is használható. A script
ideiglenesen generál egy képernyőt, és a megadott projekt telepített Angular
fordítójával, `strict` és `strictTemplates` mellett ellenőrzi. A host tsconfig
importútvonalai és az explicit `rootDir` megmaradnak; ha nincs `rootDir`, a host
gyökere lesz az alap, hogy az ideiglenes tsconfig helye ne változtassa meg a
forrásgyökeret. A próba a generált komponenst és annak importjait fordítja,
nem helyettesíti az alkalmazás teljes buildjét.

Nincs beépített FormBlock-helyettesítő ebben a scriptben. Az ideiglenes komponens
és config siker/hiba esetén is törlődik; nincs függőségtelepítés. A fordítási
hibák a használt mezőproperty-k vagy Angular-kötések inkompatibilitását jelzik.
A megjelenítést, validációs üzeneteket és eseményeket utána a saját alkalmazásban
is próbáld ki. A privát komponens forrása nincs ebben a csomagban, így annak
tényleges implementációjával itt nem futott ellenőrzés.

## Mely ablakok készüljenek el? (screen_windows)

Ha a form egynél több olyan ablakból áll, amelyen megjelenik valami, a webes generálás egyszer
megáll, és megkérdezi, mely ablakokból készüljön képernyő. Ablakonként látszik a cím, a technikai
név, a mezők száma, a blokkok, és egy egyszerűsített előnézet (ugyanaz a szkript nélküli nézet,
mint az „Előnézet” fülön). Alapból minden ablak ki van jelölve; a kihagyott ablakokból (például a
Forms segédablakaiból) semmi nem generálódik.

- **Döntés után:** a kihagyott ablakok a generálás legelején eltűnnek a formból, mintha nem is
  léteznének. Eltűnnek a vásznaik, a csak rajtuk megjelenő mezők (triggereikkel együtt), a csak rajtuk
  megjelenő blokkok (minden triggerükkel és relációjukkal), és az így már nem használt LOV-ok és
  rekordcsoportok. Ezekhez se felület, se végpont, se akció nem készül. Ami eltűnt, az
  `analysis/window-scope.json`-ban látszik. A form szintű triggerek, a programegységek és a
  megjelenített mező nélküli segédblokkok megmaradnak. Ha több főablak-jelölt marad, a „Melyik legyen a fő képernyő?” kérdés
  ezután, már csak a kiválasztott ablakok közül jön.
- **Kötegben:** minden többablakos form egyszer kérdez; a következő döntésre váró form
  automatikusan megnyílik.
- **Beállítások (CLI/szerver):** `"screen_window_selection": "ask"` kérdez, `"all"` (a CLI
  alapértéke) mindent generál; `"screen_windows": ["MAIN_WIN", "SEARCH_WIN"]` előre megadja a
  döntést. Nem létező ablaknév hibát ad.

## A komponens kerete

A generált sablon nem kap saját keretet (`<div class="flex flex-col gap-4 p-4">`) és címet
(`<h1>{{ title }}</h1>`): a keretet és a címet a befogadó oldal adja. A sablon közvetlenül a
szakaszokkal kezdődik.

## A komponens felépítése (4.27)

A generált komponens a céges `ServiceBase`-t örökli, és csak a keretet adja: a FormBlock-régiók szerkezetét, a
táblázatokat, a végpontmetódusokat és gombonként a gomb HTTP-kérését. Forms-emulációt (mezőállapotok, LOV-visszaírás,
alertek, mentési lánc, Forms-értékek átalakítása) nem tartalmaz. Rövidítve (a felmérési replikából):

```ts
export class RendelesComponent extends ServiceBase {
  protected readonly toast = inject(ToastService);
  protected readonly toastLife = { success: 3000, warning: 8000, danger: 6000 };
  private readonly http = inject(HttpClient);

  protected readonly forms: Record<string, FormGroup> = {};
  protected readonly structures: Record<string, FormBlock.Structure[]> = {
    ctrl: [
      { type: 'button', ownId: 'CTRL.PB_KERES', labelText: 'Keresés', col: '2', btnSeverity: 'primary', onClick: () => this.onPbKeresClick() },
    ],
  };

  protected readonly tetelColumns = [{ field: 'id', header: 'Tétel', width: '27.27%' }, { field: 'cikk', header: 'Cikk', width: '45.45%' }];
  protected tetelRows: Record<string, unknown>[] = [];
  protected tetelSelection: Record<string, unknown> | null = null;

  constructor() {
    super();
    // Indításkor (Forms WHEN-NEW-FORM-INSTANCE): @INIT.
    this.actionOnforminit({ blocks: {}, parameters: { 'GLOBAL.CG$APP': null } }).subscribe();
  }

  // ... végpontmetódusok ...

  protected onFormGroupGenerated(region: string, group: FormGroup): void {
    this.forms[region] = group;
  }

  // CTRL.PB_KERES: a gomb kódja a backendben fut.
  protected onPbKeresClick(): void {
    const ctrl = this.forms['ctrl']?.getRawValue() ?? {};
    // TODO: a válasz feldolgozása (res.blocks: a visszaírt mezők, res.messages: az üzenetek).
    this.actionOnctrlpbkeres({ blocks: { CTRL: { MODUS: ctrl.modus } }, parameters: {} }).subscribe();
  }

  // Mentés (Forms COMMIT_FORM): a változások egy kérésben; a backend egy tranzakcióban, Forms-sorrendben ment.
  protected save(): void {
    // TODO: a blokkok változásai a DTO mezőivel: inserted: [új rekord], updated: [{ original, value }], deleted: [rekord].
    this.commitForm({ changesRendeles: { inserted: [], updated: [], deleted: [] } }).subscribe();
  }
}
```

- **Konstruktor:** mindig van `constructor() { super(); }`, a mezők és a függvények között; ha a képernyőnek
  indítási végpontja van, a hívása is ide kerül.
- **Sablon:** a struktúrákra szögletes zárójellel hivatkozik:
  `<ank-form-block [formStructure]="structures['ctrl']" (formGroupGenerated)="onFormGroupGenerated('ctrl', $event)" />`.
  A `structures` típusa `Record<string, …>`, és az Angular CLI alap tsconfigja (`noPropertyAccessFromIndexSignature`)
  a `structures.ctrl` alakot TS4111 hibával elutasítja. Az `ank-form-block` körül nincs `div`: a FormBlock maga
  rendezi el a mezőit (keretben, fülön és dialógusban is).
- **Metódusok:** a végpontok; `onFormGroupGenerated` (a régiók FormGroupjai a `forms`-ban); `on<Gomb>Click()`
  gombonként; `save()`, ha a képernyőnek van mentési végpontja és menthető űrlapblokkja; `onRowAction()`, ha egy
  táblázatnak sor végi gombjai vannak. Segédmetódus (`text`, `data`, `blocks`, `parameters`, `showResult` ...) nincs.
- **Mentés:** a sablon tetején egy **Mentés** gomb (`save()`). A `save()` a mentési végpont kérését küldi, a
  blokkok változásai üres listák: összeállításuk (a lekérdezett rekordhoz képest módosítás, új rekord, törlés)
  fejlesztői feladat (TODO).
- **Kommentek:** csak a továbbfejlesztést segítő megjegyzések maradnak (TODO-importok, a gombok TODO-i, a nem
  fordított gomboknál az eredeti Forms-kód).
