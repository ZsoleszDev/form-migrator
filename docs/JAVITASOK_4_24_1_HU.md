# 4.24.1 – Nagy formok: nem száll el a migrálás, a hiba oka a felületen látszik

## A hiba

Egy nagyobb form migrálása leállt, a felület pedig ezt írta: „A generálás hibával leállt. A részletek a naplóban
találhatók.” Napló fül viszont nincs: a feladat ablakában a Napló és a Forráskódok fül ki van kommentezve.

A kiváltó ok egy tipikus nagy-form minta volt: egy hosszú, sok tagból álló `||` összefűzés. Egy ilyen kifejezés
nagyon mély szintaxisfa, és a `form.ir.json` kiírása `RecursionError`-ral leállította az egész futást. A worker ezt a
hibatípust nem írta ki olvashatóan, ezért a felület csak a naplóra hivatkozott.

## Javítások

- **Mély kód:** a migrálás (parancssorból és a webes workerből is) egy nagy veremméretű szálon fut, megemelt
  rekurziós korláttal. Egy 1500 tagú összefűzés így rendben lefut. Python 3.12-től a JSON-kódoló kb. 10 000
  beágyazási szintig bírja.
- **Egy trigger nem állíthatja le a formot:** ha egy trigger vagy programegység feldolgozásában a migrátor váratlan
  hibába fut (KeyError, IndexError …), az az adott trigger oka lesz:
  `Belső hiba a migrátorban (KeyError: 'X' (hely: frm_forms/…py:123, függvény)); kézi átültetés. …`. A többi trigger
  és végpont elkészül, a hiba részletei (traceback) a naplóba kerülnek.
- **Olvasható hibaüzenet:**
  - A worker minden váratlan hibánál kiírja a teljes tracebacket, utána egy záró sort:
    `HIBA: Váratlan belső hiba (<típus>): <üzenet> (hely: frm_forms/<fájl>:<sor>, <függvény>).` Ezt a feladatlista
    hibaként mutatja.
  - Ha a folyamat üzenet nélkül állt le, a kilépési kódból derül ki az ok: elfogyott memória (SIGKILL), Windows-on
    verem-túlcsordulás (`0xC00000FD`) vagy memóriahozzáférési hiba.
- **Hibanapló a felületen:** a Napló fül rejtve marad. Hibás vagy megszakadt feladatnál az Áttekintés fülön, a
  hibaüzenet alatt megjelenik a napló vége („Hibanapló”), Másolás és Mentés gombbal.
- **Gyorsabb nagy formoknál:** a futásidejű blokkbeállítások keresése, a KEY-triggerek osztályozása, a statikus
  blokktulajdonságok és a ServiceImpl segédmetódusainak felbontása futásonként egyszer készül, nem gombonként.
  Egy 25 blokkos, 30 lekérdezőgombos mintán a futásidő 49 mp-ről 32 mp-re csökkent.

## Teendő

- **Webes felület:** a módosított `angular-frontend/migrator-app.ts`-t a saját Angular-projektetekben kell újra
  lefordítani (`npm run build`), majd a `web-dist` mappát frissíteni és a Python-szervert újraindítani.
- **Ha ezután is hibát kaptok:** a hibaüzenet már a hiba típusát és helyét is mutatja, a Hibanapló vége pedig a
  részleteket. Ezt (névcserés formában is jó) érdemes elküldeni.

## Ellenőrzés

- **Új teszt (`test_robustness`):**
  - az 1500 tagú összefűzéses gomb migrálása lefut, és a gyorsítótár nem kerül a `form.ir.json`-ba;
  - egy adapter szándékos hibája csak az adott trigger oka lesz, a PL/SQL-adapter átveszi a gombot;
  - a worker a hiba típusát és helyét írja;
  - a feladatlista üzenetei traceback, jelzés és Windows-kilépési kód esetén.
- **Teljes tesztkészlet:** új hibás teszt nincs.
