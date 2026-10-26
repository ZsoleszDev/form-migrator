# Mit tartalmaz a Forms2XML-export, és mi kell még a működő modulhoz? (4.35)

Röviden: a `frmf2xml USE_PROPERTY_IDS=NO DUMP=ALL OVERWRITE=YES` export **mindent tartalmaz, ami az
.fmb fájlban van**, és ebből a képernyő és a backend kerete teljesen felépíthető. Ami hiányzik, az nem
az .fmb része: a csatolt könyvtárak kódja, az örökölt objektumok forrása, a menü, és főleg az
**adatbázis** (táblák, kulcsok, szekvenciák, tárolt eljárások). A triggerek Java-ra írásához az AI-nak
ezek közül az adatbázis-szerkezet kell; a migrátor feladatonként csak azt kéri, amit a kód ténylegesen használ.

## Benne van az XML-ben (DUMP=ALL mellett)

| Terület | Tartalom | Mire használja a migrátor |
| --- | --- | --- |
| Blokkok | adatforrás (tábla/nézet/eljárás), WHERE, ORDER BY, engedélyek (insert/update/delete/query), rekordszám | CRUD-végpontok, DTO-k, lekérdezések |
| Elemek | típus, hossz, pontosság, formátum, kötelező, kezdőérték, min/max, oszlopnév, PK, LOV, ellenőrző/rádió értékek, `CopyValueFromItem`, koordináták | űrlapmezők, validáció, master-detail kulcs |
| Triggerek, programegységek | a teljes PL/SQL-forrás (form-, blokk- és elemszinten; helyi eljárások, függvények, csomagok) | a metódusok régiójába kikommentezve, az AI ebből dolgozik |
| Kapcsolatok (Relation) | master–detail join, törlési viselkedés, automatikus lekérdezés | detail-lekérdezés, `ON-CHECK-DELETE-MASTER` |
| LOV, rekordcsoport | lekérdezés, oszlopok, visszaadott mezők | LOV-végpontok, választólisták |
| Canvasok, ablakok, tab-lapok | méret, típus, láthatóság, elemek helye | Angular elrendezés, párbeszédablakok |
| Riasztások (Alert) | stílus, szöveg, gombfeliratok | `SHOW_ALERT` kérdés a képernyőn |
| Paraméterek, vizuális attribútumok, property class | név, típus, alapérték | bemenetek, megjelenés |
| Csatolt könyvtár, alosztályozás | csak a **nevek** (`AttachedLibrary`, `ParentModule`, `ParentName`) | jelzi, ha a forrás hiányzik |

A minta-exporton (`tests/fixtures/felmeres_replika_fmb.xml`) a `python -m frm_forms inventory` futtatás szerint
minden attribútumot feldolgoz a migrátor, kivéve a `Module/@version` értéket (exportverzió, nincs hatása).
A 4.35 az `Item/@CopyValueFromItem`, `SynchronizeWithItem`, `CalculationMode` attribútumokat a leltárban is
felismertnek mutatja (a generálás eddig is használta őket).

Fontos: `DUMP=ALL` nélkül a Forms2XML csak az alapértéktől eltérő tulajdonságokat írja ki, és az örökölt
értékek sem mindig látszanak. Mindig a fenti paranccsal exportálj.

## Nincs benne — és honnan pótolható

| Hiányzik | Miért kell | Hogyan add meg |
| --- | --- | --- |
| **Táblák szerkezete** (oszlopok, típusok, PK, nullable) | JPA-entity és repository a trigger SQL-jéhez | Java asszisztens → Adatbázistáblák: CSV-import; vagy `dictionary-sql` + `dictionary-import`, `--schema` |
| **Szekvenciák, nézetek, DB-triggerek** | `NEXTVAL`, nézetre épülő blokk, az adatbázis saját logikája | ugyanígy a schema.json-ba; a DB-trigger a Java mellett is fut — ellenőrizd, hogy nem dupláz-e |
| **Tárolt eljárások / csomagok** (adatbázisban) | a trigger hívja őket | a hívás maradhat DB-hívás; az AI `MISSING:` listában kéri a szignatúrát → Kiegészítő forrás; vagy `dictionary-sql` |
| **Csatolt PL/SQL-könyvtár (.pll) kódja** | a trigger a könyvtár eljárását hívja | Forms Builderben .pld-be konvertálva: `--pld KONYVTAR.pld` |
| **Objektumkönyvtár (.olb)** | az örökölt (subclass) elemek és triggerek | `frmf2xml … KONYVTAR.olb` → `--olb KONYVTAR_olb.xml` |
| **Menü (.mmb)** | menüparancsok, jogosultsági szerepek | `--mmb MENU_mmb.xml` (megőrzi; menüt nem generál) |
| Futásidejű környezet | `:GLOBAL` értékek más formokból/menüből, hívó form paraméterei, felhasználó, szerepkörök, NLS-dátumformátum | a kérés `parameters` mezőjébe a bemeneti bekötés teszi (analysis/input-bindings.json); a felhasználót, jogosultságot a céges projekt adja |
| Ikonok, képek, riportok (.rdf), Java bean-ek | csak a nevük van meg | kézzel a céges projekt eszközeivel |

## Mi kell ahhoz, hogy a migrálás + AI után működő modul legyen?

1. Teljes export: `.fmb` → `DUMP=ALL` XML, a csatolt `.pll` → `.pld`, a használt `.olb` → `_olb.xml`.
2. A triggerekben használt táblák szerkezete (az Adatbázistáblák panel a hiányzókat triggerenként mutatja).
3. Az AI-sor lefuttatása; a `MISSING:`-t kérő feladatokhoz a hiányzó forrás megadása.
4. A céges projektben fordítás és a fő folyamatok kipróbálása (lekérdezés, új rekord, módosítás, törlés, gombok).

A frontendben néhány lépés ma is kézi befejezést kér (ezek nem az XML hiányai):
a törlés a mentésben (a `save()` az új és módosított rekordot küldi), a lista-blokkok kezdeti betöltése, a LOV-javaslatok, a `KEY-COMMIT`
logika a mentéshez kötve, és a `:GLOBAL` értékek tárolása a képernyők között. Részletek és javaslatok a
4.35 ellenőrző dokumentumában: [VALIDATION_4_35.md](VALIDATION_4_35.md).
