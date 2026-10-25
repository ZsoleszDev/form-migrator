# 4.23 – Egyszerű Java lekérdezőgombok, eredeti kód régióban

## Miért

A `LEKERDEZESI_FELTETELEK`-féle lekérdezőgombot eddig egy PL/SQL-adapter migrálta, ami a Forms-működést a lehető
legpontosabban utánozta:

- az eredeti eljárás egy névtelen PL/SQL-blokkban futott Oracle-ben;
- a kiszámolt WHERE-t a Java előre lefordított változatokkal vetette össze;
- bármilyen más, le nem fordult trigger a formon (pl. egy form-szintű `POST-QUERY`) 501-es hibával letiltotta a
  gombot.

A migrált alkalmazásnak nem kell mindenben a Formsot másolnia: elég magát az SQL-kérést értelmezni és végrehajtani.
4.23-tól ez az alapértelmezés.

## Mit generál

A gombból egyetlen, olvasható Java-metódus lesz (rövidítve, a valódi form nevesítésével):

```java
    // CGNV$W01_1.PB_LEKERDEZES: lekérdezés a(z) AIT blokkra. A WHERE feltételt a LEKERDEZESI_FELTETELEK alapján a Java állítja össze …
    // TODO: a Formsban ez is fut, a migrált lekérdezés nem: FRM_ANK_FADLEK:POST-QUERY: …
    public PageResult<AitRow> onCgnvW011PbLekerdezes(UserDto user, QueryActionRequest request) throws Exception {
        //region Eredeti Forms-kód: CGNV$W01_1.PB_LEKERDEZES, LEKERDEZESI_FELTETELEK, WUZENET
        // … a trigger, a szűrőépítő, a WUZENET és a le nem fordult form-szintű POST-QUERY eredeti kódja …
        //endregion
        return log1x(log, …, () -> {
            …
            String xyUbKod = PlsqlValues.text(values, "XY_LAP", "XY_UB_KOD");
            BigDecimal ibuF12 = PlsqlValues.number(values, "XY_LAP", "IBU_F12");
            …
            var messages = new ArrayList<String>();
            String where = null;
            if (xyUbKod != null) {
                String lekSql = "XY_AT_KOD='00' and ((:xyUbKod = 'XY_42_LAP' and XY_INT_KOD in ('XY_34','XY_340','XY_341')) or\n"
                        + …
                        + "              USER_KOD = :ibuUserKod";
                if (BigDecimal.ONE.equals(ibuF12) && BigDecimal.ZERO.equals(ibuE700) && BigDecimal.ZERO.equals(ibuP20)) {
                    lekSql += " and ERKEZES_KOD='B56'";
                }
                …
                where = lekSql;
            } else {
                messages.add("Adatlap kiválasztása nem történt meg!");
            }
            if (where == null) {
                return new PageResult<>(null, messages);
            }
            var params = new MapSqlParameterSource()
                    .addValue("xyUbKod", xyUbKod)
                    .addValue("ibuUserKod", ibuUserKod)
                    .addValue("offset", request.offset())
                    .addValue("limit", request.limit());
            var rows = jdbc.query("SELECT … FROM … WHERE " + where + " ORDER BY … OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY", params, …);
            …
            return new PageResult<>(rows, messages);
        });
    }
```

- **A képernyő értékei:** a `:XY_LAP.MEZŐ` hivatkozások a form beviteli mezői. A metódus a kérésből olvassa őket,
  szöveges mezőt `String`-ként, számot `BigDecimal`-ként.
- **Feltételek:** a trigger és a szűrőépítő `IF`-jei Java `if`-ek lesznek:
  - `IS [NOT] NULL` → `== null` / `!= null`;
  - checkbox `=1` → `BigDecimal.ONE.equals(…)`;
  - szöveg `= 'X'` → `"X".equals(…)`;
  - `AND` / `OR` / `NOT`, `NVL(mező, állandó)`.
- **Az SQL-darabok:**
  - a `select replace(lek_sql, ';', chr(39))` cserét a migrátor már generáláskor elvégzi, a Java-kódban kész
    idézőjelek vannak;
  - a darabokban lévő `:XY_LAP.MEZŐ` hivatkozásokból kötött JDBC-paraméter lesz (`:xyUbKod`);
  - a WHERE csak a forrásban lévő állandó darabokból áll össze.
- **Kimarad, mert a webes lekérdezésnek nem kell:**
  - a `SET_BLOCK_PROPERTY` (helyette `where = …`), a `GO_BLOCK`, az `EXECUTE_QUERY` (helyette a `jdbc.query`);
  - a navigációs beépített hívások (`GO_ITEM`, `SYNCHRONIZE`, `SET_ITEM_PROPERTY` …).
  - A metódus feletti megjegyzés felsorolja őket.
- **`WUZENET` és társai:** egy egyetlen szövegkonstanst kapó hívás üzenet lesz (`messages.add(…)`), a Forms-oldali
  alert-kódot nem kell értelmezni. Ha az eljárás mást is csinál (pl. naplóz), a metódus `// TODO` sorban jelzi, és a
  kódja ott van a régióban.
- **A célblokk saját szabályai:** ha a blokk `POST-QUERY` triggere lefordult, az a sorokon lefut.
- **Fejlesztői bemenet:** ha a WHERE olyan mezőre hivatkozik, ami nincs a formban, abból `null` kezdőértékű, TODO-s
  változó lesz (4.22).

## Nem tilt le más trigger

Ami a Formsban a lekérdezéskor még futna, de nem fordult le, az már nem 501-es hiba. Ilyen a te esetedben a
`DECLARE`-es form-szintű `POST-QUERY`.

- **Hol látszik:** a metódus feletti `// TODO` sor és a `backend-plan.json` `todo` mezője.
- **Az eredeti kódja:** a metódus régiójában.
- **Futás:** a gomb fut (`runs: "java-query"`, `implemented: true`).

## Eredeti kód régióban

Az eredeti Forms-kód összecsukható régióban marad (IntelliJ, VS Code). Ez a következő helyeken érvényes:

- a Java lekérdezőgombban;
- a kézi átültetésre váró gombok vázában (`Eredeti Forms-kód kiindulásnak …`);
- a PL/SQL-adapter üzenet-eljárás megjegyzésében;
- a frontend kézi navigációs metódusában (`//#region` … `//#endregion`).

```java
        //region Eredeti Forms-kód: …
        // …
        //endregion
```

## Régi mód és tartalék

- **Tartalék:** ha a gomb nem fordítható egyszerű Java-kódra, a korábbi PL/SQL-adapter fut. Ilyen eset például egy
  feltétel, amit nem lehet egyszerű Java-kifejezéssé alakítani. Az okot a `form.ir.json` `query_java_reason` mezője
  mutatja.
- **A régi mód kérése:** a konfigurációban `"query_action_mode": "plsql"`.

## Ellenőrzés

- **Új teszt (`test_query_java`), a valódi form névcserés mintájával** (alert-`WUZENET`, `--` megjegyzések, `DECLARE`-es
  form-szintű `POST-QUERY`):
  - a gomb Java-lekérdezésként fut, a form-szintű trigger TODO;
  - a kód Java `if`-ekből áll, PL/SQL-emuláció nélkül;
  - az eredeti kód régióban van;
  - a generált Java lefordul és fut (`javac` + JVM): a WHERE a jelölők szerint épül fel, a kötött értékek
    helyesek, kiválasztás nélkül csak az üzenet jön vissza, lekérdezés nélkül;
  - fejlesztői bemenet a WHERE-ben, tartalék a PL/SQL-adapterre, régió a kézi gombvázban;
  - az `EXECUTE_QUERY` a szűrőépítő után, magában a triggerben is állhat.
- **Régi mód:** a meglévő `test_query_actions` tesztek `query_action_mode: "plsql"` mellett ellenőrzik tovább a régi
  adaptert.
- **Teljes tesztkészlet:** 594 teszt. A 101 hibás teszt ugyanaz, mint a `main` ágon (a repóból hiányzó
  mintabemeneteket keresik), új hibás teszt nincs.

## Átállás

- **A ServiceImpl** CREATE_ONCE fájl. A friss metódust a `--regenerate` az `analysis/backend-regeneration/` mappába
  teszi, onnan kell átvenni.
- **A frontend és a CL-szerződés** (`QueryActionRequest` → `PageResult`) nem változott.
- **A `CommonMigrateTools.java` és a `frm-forms-screen.ts`** nem változott.
