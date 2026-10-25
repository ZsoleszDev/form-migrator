# 4.18 – Célmappák a generálás elején, a fájlok pontosan oda

A 4.17 visszajelzései alapján:
- **Java:** a kiválasztott mappa útját a telepítés a `src/main/java`-ig visszavágta, és a generált csomag szerint új
  mappákat hozott létre (`…/java/hu/company/features/…/dps`).
- **Frontend:** a fájlok a kiválasztott mappába kerültek, de egy `<modul>` almappába.
- **Segédfájlok:** a `CommonMigrateTools.java` és a `frm-forms-screen.ts` minden telepítéskor bekerült a projektbe.

A 4.18-ban a mappákat már a generálás előtt meg lehet adni, és a fájlok pontosan oda kerülnek.

## 1. Célmappák a generálás előtt

Az űrlap **2. Adatok** részében új blokk van: **Célmappák a projektben**. Ebben a CL, DPS, WBS és Frontend mappa
egyenként kitallózható. A blokk egy form generálásakor látszik; tömeges futtatásnál és felmérésnél nincs.

- **A Java-mappák útvonalából lesz a modul csomagja:** a `src/main/java` utáni rész. Ha az útvonalban nincs
  `src/main/java`, a `hu` mappától kezdődő rész.

  | Kiválasztott mappa | Csomag |
  |---|---|
  | `…\rendszer-cl\src\main\java\hu\ceg\rendszer\cl\modules\rendeles` | `hu.ceg.rendszer.cl.modules.rendeles` |
  | `…\rendszer-dps\src\main\java\hu\ceg\rendszer\dps\rendeles` | `hu.ceg.rendszer.dps.rendeles` |

- **Ezzel a csomaggal generálódik minden:** a package sorok, és a DPS, illetve a WBS importjai a CL-osztályokra.
  A mező alatt látszik a kiszámolt csomag. Ha az útvonalból nem állapítható meg, piros figyelmeztetés jelenik
  meg, és a szerver a feladatot el sem indítja.
- **A „CL package” mező megszűnt**, mert a CL-mappa útvonala megadja. A DPS és a WBS is a saját mappájából kapja
  a csomagját; eddig ez kötött volt: `<java_package>.<modul>.dps` / `.wbs`.
- **CommonMigrateTools csomagja:** ha a „CommonMigrateTools Java package” mező üres, a migrátor megkeresi a
  projektben már meglévő `CommonMigrateTools.java`-t, és az importok annak a csomagjára mutatnak. A keresés a
  kiválasztott Java-mappák `src/main/java`-jában történik.
- **A böngésző modulnévenként megjegyzi a mappákat:** ugyanazt a modult újra generálva visszajönnek. Másik
  modulnévnél nem maradnak bent az előző modul mappái.
- **A feladat is megjegyzi a mappáit:** a telepítési panel ezekkel nyílik meg.

## 2. Telepítés pontosan a kiválasztott mappába

- **Java:** a fájlok közvetlenül a kiválasztott mappába kerülnek, a mappa csomagjával. Új mappa nem készül.
- **Frontend:** a fájlok közvetlenül a kiválasztott mappába kerülnek, `<modul>` almappa nélkül.
- **Más mappa a telepítésnél, mint a generáláskor:** a telepítés a mappák csomagjára igazítja a package sorokat és
  a rétegek közötti importokat (DPS és WBS → CL), így a fájlok ott is lefordulnak.
- **Egy modulmappába egyszerre egy modul telepíthető.** A tömeges futtatás nézetéből ezért kikerült a telepítési
  panel; a modulokat egyenként telepítsd a feladatuknál.
- **A kiválasztott mappának léteznie kell.** A 4.17 a frontend képernyőmappáját még létrehozta, a 4.18 semmilyen
  mappát nem hoz létre.
- **Csomag szerinti elhelyezés:** ha a Java-projekt mappáját vagy a `src/main/java`-t, illetve az Angular-projekt
  mappáját választod (vagy parancssorból `--project`-et adsz meg), a régi szabály él. A Java-fájlok a csomagjuk
  szerint kerülnek a `src/main/java` alá, a frontend a képernyőmappába, `<modul>/` alá.

## 3. Segédfájlok: külön letöltés

A `CommonMigrateTools.java` és a `frm-forms-screen.ts` közös segédfájl, nem kell minden modullal újra
beilleszteni. Ezért a telepítés soha nem teszi őket a projektbe.

- **Letöltés:** a feladat telepítési paneljén a **Segédfájlok** sorban egy-egy gomb. API-ból:
  `GET /api/jobs/{id}/helpers/{név}`. A ZIP továbbra is tartalmazza őket.
- **Az előnézet megmutatja, megvannak-e a projektben:** megvan / régebbi változat / újabb változat / nincs. Ha
  nincs, azt is megmutatja, hová kell tenni.
  - `CommonMigrateTools.java`: a kiválasztott Java-mappák `src/main/java`-jában keresi; a változat a `VERSION`.
  - `frm-forms-screen.ts`: az Angular-projektben keresi; a változat a `FRM_FORMS_SCREEN_VERSION`.
- **Az importok a megtalált példányra mutatnak:**
  - A komponens `frm-forms-screen` importja a relatív útvonalra mutat, például
    `'../../shared/frm-forms-screen'`.
  - A Java a megtalált `CommonMigrateTools` csomagjára hivatkozik.
- **Ha nincs a projektben,** az import változatlan marad. A frontendnél ilyenkor a modulmappa szülőmappájában
  keresi a fájlt; a riport ezt a helyet adja meg.

## Parancssorból

- **`migrate`:** a `--layout CL=… --layout DPS=…` és a config `project_layout` beállítása már a generáláskor
  megadja a csomagokat, a generálás végén pedig a migrátor telepít is.
- **Új config-beállítások:** `dps_package` és `wbs_package` (a `cl_package` párja). A kiválasztott modulmappák
  felülírják őket.
- **`deploy`:** ugyanazokkal a szabályokkal telepít, mint a webes felület.

## Átállás

- **Webes felület:** az `angular-frontend` forrást újra kell fordítani (`web-dist`).
- **A 4.17-es telepítés maradványai** kézzel törölhetők:
  - a felesleges mappák: `…/src/main/java/hu/company/features/<modul>/…` és `<képernyők>/<modul>/`;
  - ha van saját példányod, a projektbe másolt `CommonMigrateTools.java` és `frm-forms-screen.ts` is.
- **A 4.17-es panel mappái nem jönnek át:** a böngésző mostantól modulnévenként tárolja őket.

## Ellenőrzés

- **Teljes tesztkészlet:** 575 teszt. A 101 hibás teszt ugyanaz, mint a `main` ágon (a repóból hiányzó
  mintabemeneteket keresik), új hibás teszt nincs.
- **`test_project_deploy`:**
  - a mappákból számolt csomagok;
  - a generálás a kiválasztott mappák csomagjával, és a projektben talált `CommonMigrateTools` csomagjával;
  - a telepítés pontosan a modulmappákba: nincs új mappa, a frontend almappa nélkül kerül a helyére, a package
    sorok és az importok a mappákhoz igazodnak;
  - a segédfájlok: nem kerülnek a projektbe, az előnézet jelzi az állapotukat, és a webes végponton
    letölthetők;
  - egy modulmappába csak egy modul telepíthető;
  - a webes feladat a célmappákkal, és a hibás célmappák elutasítása már a feladat létrehozásakor.
- **A felület ellenőrzése:** a TypeScript-ellenőrzés és az Angular-fordító sablonelemzése hibátlan.
