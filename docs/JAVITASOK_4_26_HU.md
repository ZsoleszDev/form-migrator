# 4.26 – Nincs saját futtató: a képernyő a céges keretre épül

> Ne alakítson ki a migrátor saját logikát, mert az még egy plusz nehezítés a fejlesztőnek, hogy át kell látnia; az alap
> keret a célpont.

## 1. A frm-forms-screen.ts megszűnt

A generált képernyők eddig a közös `frm-forms-screen.ts`-t (`FrmFormsScreen`) örökölték. Abban élt az általánosított
Forms-futtató: gombok, lekérdezés, LOV, mezőállapotok, validátorok, indítás, alertek, :GLOBAL/:SYSTEM, Forms-utasítások,
mentési lánc. Ha egy képernyőn más kellett, a közös fájlba kellett belenyúlni. Ez a fájl megszűnt.

- **A komponens a céges `ServiceBase`-t örökli**, és mindaz, amit a képernyő használ, a saját fájljában van,
  egyszerű, olvasható metódusként. Csak az kerül bele, ami ennek a képernyőnek kell:
  - végpontonként egy metódus (`this.http` + `WFF.debug` / `WFF.err`);
  - `onFormGroupGenerated` (a FormBlock-régiók FormGroupjai a `forms`-ban);
  - `query<Blokk>()` a lekérdezéshez, `show<Blokk>()` / `fill<Blokk>()` az eredményhez;
  - `search<Lov>()` a LOV-kereséshez, `choose<Lov>()` a ReturnItem mezőkhöz;
  - `on<Gomb>Click()` gombonként: a felismert lépései, a backend-akciója, a navigációja, vagy TODO az eredeti
    Forms-kóddal;
  - `save()`, `newRecord()`, `deleteRecord()` a Forms-mentéshez;
  - a mezőállapotok (SET_ITEM_PROPERTY) lefordított kezelői;
  - néhány kis segéd (`value`, `text`, `blocks`, `parameters`, `showResult` ...), ha valamelyik metódus használja.
- **Az ablakok és canvasok** láthatósága sima mező (`windowVisible`, `canvasVisible`, `activeContentCanvas`); a
  dialógus `[(visible)]="windowVisible['EDIT']"`, a gomb lépése `this.windowVisible['EDIT'] = true;`.
- **Validátorok:** a képernyő nem tesz saját validátort a kontrollokra. A szabályok a FormBlock tulajdonságai
  (`validator`, `minLenght`/`maxLenght`, `min`/`max`, `regexRule`); a decimális tartományt és pontosságot a backend
  ellenőrzi (a jegyzet „Adapter szükséges” sorként jelzi).
- **Ami nem fut magától, az TODO** a gomb metódusában (a döntés szerint):
  - a backend által visszaadott Forms-utasítások (`commands`: GO_BLOCK, EXECUTE_QUERY ...). A TODO felsorolja,
    melyeket adhatja vissza a gomb kódja, és melyik lekérdező metódust kell hívni; a válaszban érkezőket a
    képernyő naplózza;
  - az alert-párbeszéd (`SHOW_ALERT`, `FRM.ALERTS`);
  - a képernyő- és mentési pont (`FRM_RESUME`, `FRM_COMMIT`);
  - a :GLOBAL / :SYSTEM érték, ha a backend olvassa: a `parameters()` metódusban kell átadni.

  A backend ugyanúgy készül, mint eddig. A `backend-plan.json` `api.actions` bejegyzése mostantól a gomb lehetséges
  Forms-utasításait (`commands`) és a képernyőpontot (`screen_points`) is jelzi.

## 2. wf-table.ts: a táblázat egy helyen, a p-table teljes API-jával

A több rekordos blokk eddig a futtató `<frm-table>`-je volt, rögzített sablonnal. Helyette önálló fájl készül:
`frontend/wf-table.ts`, `wf-table` selectorral.

- **Minden p-table tulajdonság átadható**, a PrimeNG/Optimus Table bemenetei és kimenetei a p-table alapértékeivel:
  `[lazy]`, `[globalFilterFields]`, `[sortField]`, `[rowsPerPageOptions]`, `(onLazyLoad)`, `(onSort)` stb. A
  wf-table saját alapértékei: lapozó, 15 sor, egysoros kijelölés, kicsi méret, rácsvonalak, görgethető.
- **A sablonok is átadhatók:** `#caption`, `#header`, `#body`, `#emptymessage`, `#footer`, `#summary`,
  `#rowexpansion`, `#groupheader`, `#groupfooter`, `#colgroup`, `#loadingbody`, `#paginatorleft`, `#paginatorright`.
  Így a fejlesztő saját szűrőket tehet bele, vagy átalakíthatja a sort. Amit nem ad meg, ott az alapértelmezett
  fejléc, sor és üres-szöveg jelenik meg a `columns` alapján.
- **A sor végi gombok** (a blokk gombjai) az `[actions]` bemenettel jelennek meg, a kattintás az `(action)`
  kimeneten jön.
- **A képernyő** csak az adatait adja: `<wf-table [value]="tetelRows" [columns]="tetelColumns" [rows]="5"
  [(selection)]="tetelSelection" />`, az oszlopok a backend DTO-mezőivel.
- **Import:** a java-imports.json `"WfTable": { "ts": "../wf-table" }` bejegyzése adja. Ha nincs ilyen bejegyzés, a
  generált `'../wf-table'` import marad, és a telepítés a projektben megtalált `wf-table.ts`-re igazítja.
- **Egy helyen módosítható:** ha változik a p-table importja vagy selectora, vagy az Optimus valamelyik bemenetet
  nem ismeri, csak a `wf-table.ts`-t kell módosítani.

A `wf-table.ts` a `CommonMigrateTools.java`-hoz hasonló megosztott segédfájl. Egyszer kell a projektbe tenni, a
feladatnál külön letölthető (Segédfájlok), és a telepítés nem írja felül. Változata a `WF_TABLE_VERSION` (1); a
telepítés jelzi, ha a projektben régebbi van.

## 3. Nincs wrapper div az ank-form-blockok körül

Az `ank-form-block` elemek nem kerülnek `<div class="flex flex-col gap-3">` (keretben), `gap-4` (fülön, dialógusban)
vagy `<section class="flex flex-col gap-2">` közé: a FormBlock maga rendezi el a mezőit.

## Teendő

- **A projektben lévő `frm-forms-screen.ts`:** a 4.26-tal generált képernyők már nem használják. A korábban
  generált képernyők továbbra is igénylik, amíg újra nem generálod őket.
- **A `wf-table.ts`:** töltsd le, és tedd a projektbe. A java-imports.json-ba vedd fel a helyét:
  `"WfTable": { "ts": "<útvonal>/wf-table" }`.

## Ellenőrzés

- **`tests/test_screen_component.py`:**
  - nincs közös futtató, a táblázat wf-table;
  - a wf-table API-ja és sablonjai;
  - minden végpont naplóz;
  - a konstruktor helye;
  - csak a használt részek kerülnek a komponensbe;
  - nincs wrapper div;
  - az ablakok és lépéseik;
  - a `save()` Node-ban lefuttatva (módosítás, törlés);
  - tsc (strict) és ngc (strictTemplates) a generált képernyőkre, és egy saját sablonokat (`#caption`, `#header`,
    `#body`, `#emptymessage`) és p-table bemeneteket átadó `wf-table`-használatra.
- **A Java lekérdezőgomb kérése és a sorok megjelenítése** Node-ban: `test_query_actions`.
- **A korábbi frontend-tesztek** az új felépítéshez igazítva: ablakok, mezőállapotok, navigáció, telepítés, web.
  A futtató megszűnt Node-os folyamattesztjei (képernyő- és mentési pont a frontendben) helyett a gomb TODO-ját
  ellenőrzik.
