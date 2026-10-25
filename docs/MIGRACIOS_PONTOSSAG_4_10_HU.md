# Naptárképernyő és működéshű migráció – 4.10.0

A 4.9-es forrásra épülő frissítés. A meglévő CL → WBS → DPS felépítés marad; az SQL/PLSQL és a hozzá szükséges segédek a DPS ServiceImpl-ben vannak. Nincs új domain/repository réteg és nincs új függőség.

## Javított naptárképernyő

A 4.9 már kihagyta a felismert CALENDAR segédblokkot, de a canvas grafikai szövegei – például a hét napjai – ismét megjelenítendő felületet hoztak létre. Így megmaradt egy üres naptárablak vagy extra tartalmi oldal.

A javítás minden canvas eredeti mezőtulajdonosait megvizsgálja. Ha az összes mező kizárólag bizonyítottan kihagyható keretrendszer-blokkhoz tartozik, a canvas grafikai elemei sem hoznak létre felületet. A Window.PrimaryCanvas hivatkozás sem hozza vissza ezt az oldalt. A napfeliratok, az eredeti forrás és a kihagyás oka az elemzésben megmaradnak.

Megmarad a felület, ha:

- a canvas más, akár rejtett üzleti blokkmezőt is tartalmaz;
- a naptárblokk adatforrást, saját SQL-t, ismeretlen vagy hiányzó triggerkódot tartalmaz;
- a blokkmezőre ellenőrzött képernyő-felülbírálás vonatkozik;
- a canvasnak csak szövege van, és nincs bizonyítható kapcsolata kihagyott blokkal.

A név önmagában nem elég: egy CALENDAR nevű üzleti/tájékoztató canvas nem tűnik el. A valódi dátummező WHEN-VALIDATE-ITEM SQL-je megmarad. Audit: `analysis/screen-plan.json` → `framework_blocks`, `framework_canvases`, `graphics`.

A korábbi `CALENDAR.*` katalógusszabály továbbra is annak a feltételezése, hogy a szervezeti naptárcsomagot a natív vezérlő helyettesíti. Ha ezen a néven saját üzleti rutin is van, szűkítsd a mintát vagy kapcsold ki a `forms_runtime_calls: {}` értékkel. A natív naptár önmagában nem pótolja a dátumváltáshoz kapcsolt üzleti triggert.

## Eredeti PL/SQL a DPS ServiceImpl-ben

Új alapbeállítás:

```json
{
  "backend_trigger_mode": "plsql"
}
```

Az alapérték config megadása nélkül, a CLI-ben és a webes generáláskor is érvényes. A támogatott adat-triggereket most akkor is Oracle futtatja, ha a Java-fordító ismeri a bennük szereplő kifejezéseket. Ez megőrzi például az Oracle függvényeit, NULL-kezelését és PL/SQL kivételágait. A korábbi Java-first működés `"java"` értékkel kérhető. A két korábbi golden példa ezt a visszafelé kompatibilis módot ellenőrzi; az új tesztek az alapértelmezett natív módot is vizsgálják.

A ServiceImpl-beli `DbCalls.call(jdbc, sql, ...)` belül Spring `JdbcTemplate.execute` és `CallableStatement` segítségével futtat egy névtelen Oracle blokkot. A mezőértékek JDBC-paraméterek; a SQL szövegébe nem kerülnek felhasználói értékek.

A forrás szükséges illesztései:

- `:BLOKK.MEZŐ` → kötött, típusos helyi változó; a visszaírható értékek OUT-paraméteren visszakerülnek a Java rekordba;
- `MESSAGE` → összegyűjtött válaszüzenet; `FORM_TRIGGER_FAILURE` → hibás műveletet jelző kivétel;
- bizonyított keretrendszer-/naptárhívás → kihagyás, naplózott indoklással;
- a hívott, beágyazható helyi programegységek → a névtelen blokk deklarációi;
- az akcióban támogatott `COMMIT` → a sikeres HTTP-végpont tranzakciójának lezárása.

`SELECT … INTO`, DML, helyi PL/SQL és adatbáziscsomag-hívás ezen az útvonalon futhat. A csak Forms-ban létező `GO_ITEM`, `SET_ITEM_PROPERTY`, `EXECUTE_QUERY`, stb. nem adatbázis-rutin. Ismeretlen vagy vegyes, nem átültethető működésnél az egész érintett trigger kézi feladat marad; az adatbázisrészt nem indítjuk el önmagában.

További javítások:

- A `CASE … WHEN … THEN` már nem minősül tévesen kivételkezelőnek, ezért nem keletkezik illegális puszta `RAISE`.
- A kivételkezelőben később álló keretrendszeri hibajelzés is továbbdobja a hibát. A naptár-értesítés kihagyása viszont nem szakít meg egy helyreállító kivételágat.
- A kifejezésben hívott csomagfüggvény OUT/IN OUT argumentumai is részt vesznek a védett mezők ellenőrzésében; ismeretlen aláírásnál futásidejű ellenőrzés akadályozza meg az észrevétlen visszaírást.

Az Oracle JDBC támogatja a névtelen PL/SQL blokkokat: [Oracle JDBC dokumentáció](https://docs.oracle.com/en/database/oracle/oracle-database/26/jjdbc/JDBC-getting-started.html). A kivétel-újradobás nyelvi szabályai: [Oracle PL/SQL hibakezelés](https://docs.oracle.com/en/database/oracle/oracle-database/21/lnpls/plsql-error-handling.html).

## Futtatható kód és azonos működés

Minden generálás elkészíti a `RUNTIME_COVERAGE.md` és `analysis/runtime-coverage.json` fájlokat. Ezek minden eredeti triggerre megadják a forrást, SHA256 lenyomatot, futtatómotort, kapcsolódó végpontot, tiltásokat, tényleges időzítést és hiányzó bekötést. Nincs megtévesztő „100% migrálva” arány.

| Esemény | Jelenlegi generált működés | A Forms-azonossághoz szükséges ellenőrzés |
|---|---|---|
| Adatbázisos WHEN-BUTTON-PRESSED | A megjelenített gomb HTTP módban a DPS akcióját hívja | Kontextus, jogosultság, kijelölt rekord, válasz visszaírása |
| WHEN-VALIDATE-ITEM / RECORD | Create/update mentési láncban fut | Mező-/rekordelhagyás, módosított mezők és hívássorrend |
| PRE/POST INSERT, UPDATE, DELETE | A generált DML előtt/után, egy végpont tranzakciójában | Az összes érintett blokk közös mentési szemantikája |
| POST-QUERY | Lekérdezéskor soronként és mentés utáni újraolvasáskor | Írási mellékhatások, eredeti lekérdezési életciklus |
| GO_BLOCK + EXECUTE_QUERY gomb | Felismert blokk esetén list/search HTTP-hívás | Fókusz-/navigációs triggerek és függő lekérdezések sorrendje |
| Egyéb startup, KEY-/ON-/navigációs esemény | Kód és feladat megőrizve, részleges frontendkezelés lehetséges | Az eredeti eseménylánc hostoldali bekötése |

A következő fejlesztési sorrend adná a legtöbb működésbeli javulást:

1. **Közös Forms esemény- és rekordállapot-kezelő a fogadó alkalmazásban.** Módosított rekordok, WVI/WVR időzítés, sikertelen validáció utáni navigáció megállítása. Egy minden mezőváltozásra indított HTTP-kérés eltérne a Forms viselkedésétől, DML-es validációnál különösen.
2. **Több blokk közös mentése és tranzakciója.** A mostani végpont-tranzakció nem egyenértékű a Forms több szerkesztési lépésen át élő rekordpufferével. COMMIT_FORM, POST, visszavonás, master-detail és kulcsképzés együtt kezelendő.
3. **Form/session-kontextus és kimeneti értékek.** GLOBAL/PARAMETER/SYSTEM, NLS, csomagállapot, nem megjelenített mezők. A frontend jelenleg üres paramétertérképet küld; az akciók táblázatos kimenete és a megváltozott kijelölés kezelése is ellenőrzendő a hostban. A JDBC pool nem garantál állandó adatbázis-sessiont.
4. **Összehasonlító elfogadási próbák valós formokon.** Azonos bemenettel: lekérdezés, dátummódosítás, hibás dátum, naptár Cancel/OK, mentés, visszavonás, master-detail. A megjelenített érték, az üzenet és az adatbázis állapota együtt hasonlítandó össze.

Ehhez érdemes egy konkrét FMB/exportált XML, az Oracle-séma és a céges host adapter együtt ellenőrzött mintájával folytatni. Ebben a projektcsomagban nincs élő Oracle vagy teljes céges célalkalmazás; a javítás nem igazol teljes funkcionális azonosságot.

## Átállás és ellenőrzés

1. A migrátor forrásának frissítése után indítsd újra a Python szervert. A feltöltő Angular felület új build nélkül használható.
2. Ugyanazt a formot először **új kimeneti mappába** generáld. A `--regenerate` a régi, kézzel szerkesztett komponenst és ServiceImpl-et megőrzi, ezért önmagában nem távolítja el a korábbi naptárképernyőt, és nem cseréli le a Java triggert PL/SQL-re.
3. A meglévő modulban a kézi kód megőrzésével emeld át a változásokat. Backend merge-javaslat: `analysis/backend-regeneration/`; frontendhez az új mappában generált komponens az összehasonlítási alap.
4. Nézd át a `RUNTIME_COVERAGE.md`, `BACKEND_TASKS.md` és `analysis/backend-plan.json` eredményeit. A `backend_live` és a meglévő műveleti tiltások szabályai nem változtak.

Önálló regressziók:

```sh
python -m unittest discover -s tests -p test_migration_fidelity.py -v
python -m unittest discover -s tests -p test_forms_runtime.py -v
python -m unittest discover -s tests -p test_trigger_noise.py -v
```

Az új tesztcsomag a naptárcanvasok szűrését, a megőrzendő üzleti eseteket, a natív/Java módot, a kivételkezelést, az OUT-paraméterek védelmét és az eseménykimutatást ellenőrzi. Java 17 fordítással és JDBC próbaduplával a WVI → WVR sorrendet, az OUT érték továbbadását és az üzeneteket is vizsgálja. Ez nem helyettesít Oracle-en futtatott PL/SQL tesztet vagy a céges Spring/Angular integrációt. A teljes tesztfutás kiinduló és végső eredményét a `VALIDACIO_4_10.json` rögzíti.
