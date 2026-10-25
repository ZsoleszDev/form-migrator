# 4.12.2 – Checkstyle-kompatibilis Java-kimenet

A céges build a generált Java-kódot Checkstyle-lal ellenőrzi. A jelzett hibák (NeedBraces,
LeftCurly, RightCurlyAlone, CustomImportOrder, EmptyLineSeparator,
AvoidEscapedUnicodeCharacters) Checkstyle-szabályok, nem PMD-szabályok. A konfiguráció a
Google-féle `google_checks.xml` 4 szóközös változatának felel meg: a `java.`/`javax.`
importok külön csoportot alkotnak, és üres sor választja el őket a többitől.

## Mi változott

Minden generált backend-Java a `java_tidy` után egy új lépésen megy át
(`frm_forms/java_style.py`). Ez a CL-, DPS- és WBS-fájlokra, a régi és a céges (AWU)
formátumra, valamint a `template_dir` sablonjaira egyaránt vonatkozik. Csak az elrendezés
változik, a kód jelentése nem.

| Szabály | A generált kódban |
|---|---|
| NeedBraces | Minden `if`/`else`/`for`/`while`/`do` törzs kapcsos zárójelet kap. |
| LeftCurly, RightCurly | A `{` a sor végén áll. A `}` külön sorba kerül, vagy `} else`, `} catch`, `} finally`, `} while` formában folytatódik. Az üres konstruktor, metódus és osztály két sort kap. |
| OneStatementPerLine | Soronként egy utasítás áll, a `case`/`default` címke külön sorba kerül. |
| AnnotationLocation | A típusok, metódusok és konstruktorok annotációi külön sorba kerülnek (`@Override`). |
| EmptyLineSeparator | Minden osztálytag előtt üres sor áll. Az egymást követő mezők között ez nem kötelező. |
| CustomImportOrder | A csoportok sorrendje: statikus, `java.`/`javax.`, minden más. A csoportok között egy üres sor áll, csoporton belül a Checkstyle-sorrend érvényes. |
| AvoidStarImport, UnusedImports | A generált `*` importok (`…Dtos.*`, `org.springframework.web.bind.annotation.*`) helyére a ténylegesen használt osztályok importja kerül. A nem használt, az ismétlődő és a `java.lang` importok kimaradnak. |
| AvoidEscapedUnicodeCharacters | A szöveg- és karakterliterálokban `\u00e1` helyett `á` áll (a fájlok UTF-8 kódolásúak). A vezérlőkarakterek escape-elve maradnak (`\t`, `\n`, `\000`). |
| WhitespaceAround | Szóköz kerül az `=` köré (annotációban is: `name = "offset"`), valamint a `==`, `!=`, `&&`, `||` és `->` köré, továbbá a vessző után. |
| Indentation | A blokkbehúzás 4 szóköz, a `case` +4. A tördelt sorok megtartják a formájukat, de legalább 8 oszloppal beljebb kezdődnek, mint az utasítás. |

A `CommonMigrateTools.java` sablonját kézzel is átírtam. A fenti szabályokon túl minden
publikus típus és metódus Javadocot kapott, és egyetlen sor sem hosszabb 100 karakternél.
A viselkedés nem változott. A `VERSION` továbbra is `"2"`, így a korábban generált modulok is
működnek az új fájllal. A projektben egyszer le kell cserélni.

## PMD-javítások

A céges PMD-ellenőrzés (6.21) a fenti elrendezés után még négy szabályt jelzett. Mindegyiket
a generátor oldalán javítottam:

| PMD-szabály | Javítás |
|---|---|
| MethodNamingConventions (`[a-z][a-zA-Z0-9]*`) | A DPS ServiceImpl blokkonkénti segédmetódusai aláhúzás nélküli nevet kapnak: `map_ait` helyett `mapAit`, `validateRules_vElek` helyett `validateRulesVElek`. A művelet áll elöl, így a második karakter kisbetű marad, ami a Checkstyle MethodName szabálynak is megfelel. |
| MissingBreakInSwitch | A `guard()` switch `default` ága is `break`-kel zárul. |
| UnusedFormalParameter | A sorleképező csak `ResultSet`-et kap: `(rs, rowNum) -> mapAit(rs)`. A privát segédmetódusok csak a ténylegesen olvasott paramétereiket tartják meg (például egy `context`-et nem használó trigger nem kapja meg), és a hívásaikból is kimaradnak ezek az argumentumok. A mezőellenőrzés nélküli `validate()` teljesen kimarad, mert egy üres hibalista ellenőrzése semmit sem csinál. |
| PreserveStackTrace | A `CommonMigrateTools` szám-, dátum- és LOV-értékhibáinál, valamint a CL RestClientImpl DPS-hibáinál az eredeti kivétel lesz az új kivétel oka (`cause`). A HTTP-válasz nem változik. |

A 4.12.1-ben bevezetett `validateRules_b` alakú nevek ezzel `validateRulesB` alakúra változnak.
Ezek privát segédmetódusok, ezért a végpontokat és a CL/WBS szerződést nem érintik. A
CREATE_ONCE ServiceImpl-ekben a változás a `--regenerate` által adott `.patch` fájlból vehető át.

## Beállítások (CLI config / szerver `engine_config`)

- `java_import_order` – alapértéke `STATIC###STANDARD_JAVA_PACKAGE###THIRD_PARTY_PACKAGE`. Ez
  a Checkstyle CustomImportOrder `customImportOrderRules` értéke. Ha a céges konfiguráció más
  sorrendet vár, itt kell megadni, például `THIRD_PARTY_PACKAGE###STANDARD_JAVA_PACKAGE` vagy
  `STATIC###SAME_PACKAGE(3)###STANDARD_JAVA_PACKAGE###THIRD_PARTY_PACKAGE`. Hibás érték
  esetén a generálás érthető hibaüzenettel leáll.
- `java_checkstyle_format` – alapértéke `true`. `false` esetén a generátor a korábbi
  elrendezést adja (hibakereséshez).

## Biztonsági háló

- A formázó tokenekre bontja a fájlt, és a kimenetet összeveti a bemenettel. A kódtokeneknek
  (a kapcsos zárójelek kivételével, a literálokat értékük szerint véve) és a kommenteknek
  egyezniük kell. Ha nem egyeznek, vagy a formázó nem ismer egy szerkezetet (pl. címkézett
  utasítás), a fájl változatlanul íródik ki. Ilyenkor az `analysis/java-style.json` sorolja fel
  az érintett fájlokat; normál esetben ez a fájl nem jön létre. `FRM_JAVA_STYLE_STRICT=1`
  mellett ilyenkor hiba keletkezik.
- Ellenőrzés a tesztcsomag által generált 660 Java-fájlon:
  - a javac-szintaxisfák előtte és utána azonosak (a blokkok egységesítése után);
  - a Checkstyle-szabályok független ellenőrzése a fenti szabályokra 0 hibát ad;
  - a formázás stabil, vagyis másodszor futtatva már nem változtat;
  - a fordítási próbák ugyanazokat az eredményeket adják, mint korábban.
- Új tesztek: `tests/test_java_style.py`. A tesztek szerint minden generált fájl már a
  végleges elrendezésben van, és a szabályonkénti példák is ezt igazolják.

## Amit a formázás nem old meg

- **Elnevezési szabályok:** például a MemberName a DTO-mezőknél (`lUres3`), vagy az
  AbbreviationAsWordInName az `onVElek…` metódusneveknél. Ezek az API-szerződés részei,
  ezért nem változtak.
- **Javadoc-szabályok** (MissingJavadocMethod stb.) a generált modulosztályokban.
- **LineLength:** a generált SQL-szövegek és a hosszú szignatúrák sorai 100 karakternél
  hosszabbak lehetnek. A korábbi jelzések alapján a céges limit nem 100 karakter.

## Újragenerálás

A modult egyszerűen újra kell generálni. A `--regenerate` a CREATE_ONCE fájlokat (DPS/WBS
`ServiceImpl`, `ControllerImpl`) nem írja felül. A friss, Checkstyle-kompatibilis változatuk
az `analysis/backend-regeneration/…java.txt` fájlban, az eltérés a mellette lévő `.patch`
fájlban található.
