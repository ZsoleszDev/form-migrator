# 4.24.2 – A frm-forms-screen.ts nem definiálja újra a modName-et

## Mi változott

A `frm-forms-screen.ts` (a közös képernyő-futtató) eddig saját `modName` gettert generált:

```ts
get modName(): string {
    return WFF.trim(this.router.url, '/');
}
```

A `modName`-et a céges `ServiceBase` már biztosítja, a `FrmFormsScreen` pedig ebből öröklődik. Ezért a getter
kikerült a futtatóból.

- **A naplózás nem változik:** a `send` továbbra is a `WFF.debug(this.modName + '.<metódus>', res)` hívással naplóz,
  a `modName` az ősosztályból jön, ugyanúgy, mint a `router`.
- **A futtató verziója 5:** `FRM_FORMS_SCREEN_VERSION = '5'`. A telepítés a projektben talált régebbi (4-es)
  `frm-forms-screen.ts`-t „régebbi változat”-ként jelzi.
- **A generált komponensek nem változtak.**

## Teendő

Töltsd le az új `frm-forms-screen.ts`-t, és cseréld le vele a projektben lévő példányt. A futtató megosztott
segédfájl, a telepítés nem írja felül.

## Ellenőrzés

- **Tesztcsonkok:** a `ServiceBase` csonkjai (`tests/ts_stubs`) a valódi osztályhoz hasonlóan adják a `modName`-et. A
  szigorú `tsc` és az `ngc` ellenőrzés így az örökölt `modName`-mel fordítja a futtatót. A csonkból kivéve a `tsc`
  hibát jelez (TS2339), vagyis az ellenőrzés valóban az öröklésre támaszkodik.
- **`test_screen_runtime`:** azt ellenőrzi, hogy a futtatóban nincs `get modName`.
- **A headstart golden minta** frissítve: csak a verzió és a getter változott.
