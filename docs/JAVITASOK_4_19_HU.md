# 4.19 – Frontend: FormBlocksComponent, kérésnaplózás, modName

## 1. `FormBlocksComponent`

A generált képernyő a `FormBlocksComponent`-et importálja, és ez kerül a `@Component` `imports` listájába. Eddig
az `AnkFormBlockComponent` volt.

- Ha a cég mégis más nevet használ, az `optimus_form_block_symbol` beállítás továbbra is felülírja.
- A HTML-selector (`html_selectors.form_block`, alapból `ank-form-block`) nem változott.

## 2. `frm-forms-screen.ts`: `modName`, router a `ServiceBase`-ből

A futtató **3-as változata** (`FRM_FORMS_SCREEN_VERSION = '3'`):

```ts
/** A modul neve az útvonalából: a kérések naplójában (WFF.debug) ez áll a függvénynév előtt. */
get modName(): string {
  return WFF.trim(this.router.url, '/');
}
```

- **Router:** a `protected readonly router = inject(Router);` sor és a `Router` importja kikerült, mert a
  `router` a `ServiceBase`-ből öröklődik.
- **Importok:** a futtató TODO-sorában a `ServiceBase` mellé a `WFF` került. Ha a `java-imports.json`-ban van
  `"WFF"` bejegyzés, a migrátor maga írja be az importot. A generált képernyők a `WFF`-et eddig is importálták.
- **Gombok nélküli képernyő:** a generált komponens ilyenkor nem a futtatót örökli, hanem közvetlenül a
  `ServiceBase`-t. A `modName` getter ilyenkor magában a komponensben van, ugyanígy.
- **Saját router a komponensben sem:** a generált komponensek sem injektálnak routert. A navigáció is a
  `ServiceBase` routerét használja.

## 3. Minden sikeres kérés naplózása

Minden végpontmetódusban a sikeres válasz legelőször a `WFF.debug` hívásba kerül, a `modName` és a
metódus nevével:

```ts
listRendeles(offset = 0, limit = 200) {
  return this.http.get(this.url('listrendeles'), { params: { offset, limit } })
    .pipe(
      tap((res) => WFF.debug(this.modName + '.listRendeles', res)),
      catchError((error) => {
        WFF.err('Hiba', error);
        throw error;
      })
    );
}
```

- **Mi kerül a naplóba:** a `tap` minden más feldolgozás előtt fut, tehát a napló a nyers választ mutatja. Így
  naplózódik minden lekérdezés, lista, LOV, gomb, mentés és indítási hívás.
- **A futtató hívásai is:** a futtató gomb-, mentési és indítási kérései is a komponens végpontmetódusain
  mennek át, ezért ugyanígy naplózódnak.

## Mellékesen javítva

Ha egy gombok nélküli képernyőn csak listázó végpont volt (keresés nem), a nem használt
`criteria` / `wireText` / `value` / `localIso` miatt a szigorú TypeScript-fordítás (`noUnusedLocals`) hibát
jelzett. Ezek most csak akkor generálódnak, ha használja őket valami.

## Átállás

1. **Cseréld a projektben a `frm-forms-screen.ts`-t a 3-as változatra.** A feladatnál a Segédfájlok közül
   tölthető le. A telepítés előnézete jelzi, ha a projektben még a 2-es van („régebbi változat”).
2. **A `WFF` importja a futtatóban:** a `frm-forms-screen.ts` elején a TODO-sor helyére írd be, ugyanonnan,
   ahonnan a képernyők is importálják. Ha a `java-imports.json`-ban benne van, a migrátor maga beírja.
3. **A már meglévő képernyők nem frissülnek maguktól:** a komponens CREATE_ONCE fájl. Ha naplózást szeretnél
   bennük, az új változat a `--regenerate` kimenetében van.

## Ellenőrzés

- **Teljes tesztkészlet:** 576 teszt. A 101 hibás teszt ugyanaz, mint a `main` ágon (a repóból hiányzó
  mintabemeneteket keresik), új hibás teszt nincs.
- **`test_screen_runtime`:**
  - a naplózás: minden HTTP-hívásnál, a metódus nevével;
  - a `modName` a futtatóban, illetve futtató nélkül a komponensben;
  - sehol nincs saját router-injektálás;
  - a `FormBlocksComponent` az importokban.
  - Új, gombok nélküli listaképernyő is szigorú `tsc` ellenőrzést kap.
- **A headstart golden kimenet** az új alakra frissült.
