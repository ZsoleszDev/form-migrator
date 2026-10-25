# 4.25.2 – Az ablak hibás PrimaryCanvas-a nem állítja le a migrálást

## A hiba

Egy nagy (Designer által generált) form migrálása ezzel állt le:

```
HIBA: SCREEN_PRIMARY_CANVAS_TYPE: a saját ablak Content canvasa szükséges: CG$POPUP_17
```

Az egyik ablak `PrimaryCanvas` tulajdonsága olyan canvasra mutatott, amely nem az ablak saját Content canvasa. A
Designer `CG$POPUP_…` canvasai jellemzően Stacked típusúak. A migrátor ezt eddig végzetes hibának vette.

## Mi változott

- **Figyelmeztetés, nem leállás:** ha egy ablak `PrimaryCanvas`-a nem az ablak saját Content canvasa, a migrátor
  `SCREEN_PRIMARY_CANVAS_IGNORED` figyelmeztetést ad, és figyelmen kívül hagyja. Ilyen eset:
  - Stacked vagy Tab típusú canvas;
  - üres, nem Content canvas;
  - egy másik ablak canvasa.

  Az ablak ugyanúgy megjelenik a saját canvasaival, és az első Content canvasán indul.
- **A Canvas.WindowName dönt:** ha egy canvas `WindowName`-je az A ablak, de a B ablak `PrimaryCanvas`-a is rá
  mutat, a canvas az A ablakban marad. A B ablak hivatkozása elavultnak számít (`SCREEN_PRIMARY_CANVAS_IGNORED`).
  Eddig ez `SCREEN_WINDOW_CANVAS_CONFLICT` hibával leállt.
- **Ami továbbra is hiba:**
  - a nem létező canvasra mutató `PrimaryCanvas` (`SCREEN_UNKNOWN_PRIMARY_CANVAS`);
  - az, ha két ablak `PrimaryCanvas`-a ugyanarra a `WindowName` nélküli canvasra mutat.

  Ezeknél nem dönthető el, melyik ablakhoz tartozik a canvas.

## Az IndexError a naplóban

A naplóban a `HIBA` sor előtt egy `IndexError: list index out of range` is állt. Ez nem ugyanaz a hiba: egy trigger
fordítása közben történt, és a 4.24.1-es védőháló elkapta. Az a trigger kézi átültetésre maradt, a migrálás
ment tovább. Most, hogy a migrálás végigfut, a riportban az adott triggernél ez az ok áll:

```
Belső hiba a migrátorban (IndexError: list index out of range (hely: frm_forms/….py:N, függvény)); kézi átültetés.
```

A `hely` rész a migrátor kódjára mutat, a form adatait nem tartalmazza. Ezzel a sorral (vagy a naplóban a
`Traceback` utáni `File "…frm_forms…"` sorokkal) a hiba javítható.

## Ellenőrzés

- **`test_screen_windows`:** három esetet ellenőriz:
  - Stacked `CG$POPUP_17` PrimaryCanvas;
  - üres Stacked PrimaryCanvas;
  - egy másik ablak canvasára mutató PrimaryCanvas.

  Mindháromnál figyelmeztetés van, a főablak és a párbeszédablak a helyén marad.
- **ngc:** a csak Stacked canvast tartalmazó ablak generált komponense az Angular-fordítóval (strictTemplates)
  lefordul.
