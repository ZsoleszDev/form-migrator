# 4.25.1 – A CommonMigrateTools csomagját nem kell megadni

## Mi változott

A migrátor felületéről kikerült a **„CommonMigrateTools Java package”** mező. A `CommonMigrateTools.java` mostantól
ugyanúgy működik, mint a `frm-forms-screen.ts`: a migrátor magától megtalálja a projektben, és az importokat
hozzá igazítja.

- **Telepítéskor:** az Előnézet és a Telepítés megkeresi a projektben lévő `CommonMigrateTools.java`-t a
  kiválasztott CL-, DPS- és WBS-mappák `src/main/java`-jában. A generált Java-fájlokban a
  `…CommonMigrateTools.X` importokat a megtalált fájl `package` sorára írja át. Ez eddig is így működött; a mező
  emiatt volt felesleges.
- **Generáláskor:** ha a célmappák már a generálás előtt ki vannak választva, a generátor is megkeresi, és eleve a
  talált csomagot írja az importokba. Így a ZIP-ben lévő fájlok is jók.
- **Ha még nincs a projektben:** a generált importok és a letöltött `CommonMigrateTools.java` csomagja az
  alapértelmezés (`<java_package>.cl`). A telepítési riport megmutatja, hova kell tenni a fájlt. Ha később máshova,
  más csomaggal teszed, a következő telepítés oda igazítja az importokat.

## Felülírás

A CLI- vagy szerverconfig `common_migrate_tools_package` beállítása megmaradt. Ritkán kell: akkor hasznos, ha a
modult telepítés nélkül, a ZIP-ből másolod be, és a célmappák a generáláskor nem voltak kiválasztva.

A böngészőben korábban elmentett beállítások között maradt érték már nem jut el a szerverhez.

## Teendő

Nincs. Elég, ha a `CommonMigrateTools.java` egyszer bekerül a CL-projektbe. A feladatnál továbbra is külön
letölthető.

## Ellenőrzés

- **A telepítés átírja az importot:** a `test_project_deploy` alapértelmezett csomaggal generál, a projektben pedig
  saját csomagú `CommonMigrateTools.java` van. A telepített DPS ServiceImpl a projekt példányát importálja.
- **A generátor is megkeresi:** ha a célmappák a generálás előtt ismertek, a generátor rögtön a projekt csomagját
  írja.
