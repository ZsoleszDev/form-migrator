# 4.21 – Nincs .frm-deploy.json, Angular-fordítási javítás, konstruktor

## 1. A telepítés nem ír `.frm-deploy.json`-t

A projektbe telepítés (`--project` / `--layout`, `deploy` parancs, webes „Telepítés a projektbe”) csak a generált
fájlokat írja, sehová nem tesz nyilvántartást.

- **Felülírási szabály:**

  | Fájl | Ha már létezik a projektben |
  |---|---|
  | Generált fájl (Constants, DTO-k, RestClient, Controller/Service interfész és Base) | frissül |
  | CREATE_ONCE fájl (ServiceImpl, ControllerImpl, képernyőkomponens) | megmarad, a friss változat a generált kimenetben van |

- **Kézi módosítás:** a generált fájlokba írt kézi változtatás a következő telepítéskor elvész, ezért a saját kód
  a CREATE_ONCE fájlokba (ServiceImpl, ControllerImpl, komponens) való. Eddig a `.frm-deploy.json` alapján ezt
  „ütközésként” jelezte a telepítés; nyilvántartás nélkül ez már nem lehetséges.
- **A régi nyilvántartás törlődik:** a 4.16–4.20 által írt `.frm-deploy.json` fájlokat a következő telepítés törli
  (a fő projektmappából és a részek projektmappájából). Csak azt törli, amelyiket a migrátor írta
  (`"generator": "frm-forms-migrator"`). Az előnézet csak felsorolja őket („Törlendő”), és nem töröl.
- **`--force` / `--project-force`:** a kapcsolók megmaradtak, hogy a meglévő szkriptek ne törjenek el, de nincs
  hatásuk.

## 2. Frontend: `structures['…']` a sablonban (TS4111)

A sablon eddig így hivatkozott a FormBlock-struktúrára:

```html
<ank-form-block [formStructure]="structures.cgnvW011" … />
```

A `structures` típusa `Record<string, FormBlock.Structure[]>`, és az Angular CLI alap tsconfigjában bekapcsolt
`noPropertyAccessFromIndexSignature` erre TS4111 hibát ad. Most szögletes zárójellel hivatkozik rá:

```html
<ank-form-block [formStructure]="structures['cgnvW011']" (formGroupGenerated)="onFormGroupGenerated('cgnvW011', $event)" />
```

A többi sablonhivatkozás nem érintett:

- a `tables.TETEL` és a `labels.text1` típusa a generált objektumból jön, nem `Record`, ezért a pontos alak helyes;
- az ablak- és canvas-állapot eddig is szögletes zárójellel szerepelt.

## 3. Frontend: a konstruktor mindig ott van

Minden generált komponensben van konstruktor, a változók és a függvények között:

```ts
  protected override readonly actionSteps = { … };

  constructor() {
    super();
  }

  aitSearch(body: unknown) { return this.send('aitSearch', this.http.post(this.url('ait/query/search'), body)); }
```

Ha a képernyőnek van indulási kódja, az is a konstruktorba kerül, a `super()` után:

- `this.runAction('@INIT')`;
- induláskori mezőállapot, pl. `this.setItemState(…)`.

## Ellenőrzés

- **Valódi Angular-fordítás (új):** a `test_screen_runtime` a generált képernyőt és a `frm-forms-screen.ts`-t az
  Angular fordítóval (`ngc`, `@angular/compiler-cli` 20) is lefordítja. A beállítások:
  - `strictTemplates`;
  - az Angular CLI alap tsconfigja: `strict`, `noPropertyAccessFromIndexSignature`, `noImplicitOverride`,
    `noImplicitReturns`, `noFallthroughCasesInSwitch`.

  A céges osztályok és az Optimus-modulok helyettesítői a `tests/ts_stubs/angular` alatt vannak. Futtatás:
  `FRM_NGC=<telepítés>/node_modules/.bin/ngc`, enélkül a teszt kimarad. A régi `structures.x` alakon ugyanazt a
  TS4111 hibát adja, amit a projektben láttál.
- **Kézi próba:** 17-féle generált képernyő fordult le hibátlanul így:
  - mezőállapotok, PL/SQL-es gombok, gomblépések, LOV, checkbox, validációk;
  - CALL_FORM-navigáció, rejtett ablak, stacked canvas;
  - több ablak fülekkel és harmonikával, két dokumentumablak;
  - headstart, lekérdező gomb, felmérési replika (backenddel és csak frontenddel).
- **A `tsc`-teszt** is az Angular CLI tsconfig-kapcsolóival fut.
- **Teljes tesztkészlet:** 578 teszt. A 101 hibás teszt ugyanaz, mint a `main` ágon (a repóból hiányzó
  mintabemeneteket keresik), új hibás teszt nincs.

## Átállás

- **A futtató (`frm-forms-screen.ts`)** nem változott, továbbra is a 4-es változat kell.
- **A már generált képernyők** nem frissülnek maguktól (CREATE_ONCE). Bennük kézzel cseréld
  `structures['…']`-re a `structures.…` hivatkozásokat, vagy vedd át az új kimenetből.
- **A régi `.frm-deploy.json` fájlokat** a következő telepítés magától törli; kézzel is törölheted őket.
