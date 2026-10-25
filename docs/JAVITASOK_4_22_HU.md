# 4.22 – Alert-párbeszédes üzenetek, fejlesztői bemenetek

## A kiinduló eset

Egy lekérdezőgomb (`LEKERDEZESI_FELTETELEK` + `WUZENET`) a migrátor kimenetében teljesen kikommentezve maradt
(„Eredeti Forms-kód kiindulásnak”).

- **Ami eddig is működött:** a gomb `DEFAULT_WHERE`-építő része, vagyis a többsoros szűrőszöveg, a hét jelölő-IF, a
  `select replace(…)` és a `--lek_sql:=…` megjegyzések.
- **Ami megakasztotta:** csak a helyi `WUZENET` eljárás, mert nem az eddig ismert alakú volt:
  ```sql
  m_alertdialog  CONSTANT VARCHAR2(15) := 'QMS$INFORMATION';
  …
  m_alertid := FIND_ALERT ( m_alertdialog );
  SET_ALERT_PROPERTY( m_alertid, ALERT_MESSAGE_TEXT, vv_uzenet);
  m_alertbutton := SHOW_ALERT( m_alertid );
  ```
  A konstans deklarációt a felismerő nem fogadta el, ezért az egész gomb kézi feladat lett.

Most a gomb lekérdezési akcióként fut (`runs: "plsql-query"`). A `WUZENET('Adatlap kiválasztása nem történt meg!')`
hívásból képernyőüzenet (toast) lesz.

## 1. Alert-párbeszédes üzenet-eljárások

Egy helyi eljárás akkor számít üzenetnek, ha csak megjeleníti a kapott szöveget. A következő elemekből állhat:

- **Deklarációk:**
  - `NUMBER`, `INTEGER`, `PLS_INTEGER`, `BOOLEAN`, `ALERT` vagy `VARCHAR2(n)` / `CHAR(n)` változó;
  - konstans szó szerinti értékkel (pl. az alert neve).
- **Utasítások:**
  - `MESSAGE(p)`;
  - `v := FIND_ALERT(…)`, `SET_ALERT_PROPERTY(…, ALERT_MESSAGE_TEXT, p)`, `SET_ALERT_PROPERTY(…, TITLE, …)`;
  - `CHANGE_ALERT_MESSAGE`, `SET_ALERT_BUTTON_PROPERTY`, `v := SHOW_ALERT(…)`;
  - `SYNCHRONIZE`, `BELL`, `NULL`.
- **Ágak:** a Headstart-féle `IF ID_NULL(alert) THEN MESSAGE(p); ELSE … END IF;` is elfogadott. A végén
  `RAISE FORM_TRIGGER_FAILURE` állhat.

Ami ennél többet csinál, az továbbra is kézi feladat:

- naplótáblába ír;
- a megnyomott alert-gomb szerint dönt (`IF button = ALERT_BUTTON1 …`);
- a saját változóin kívül mást is módosít.

**Az eredeti kód megmarad:** az eljárás kódja megjegyzésként a generált Java-metódusba kerül, hátha később kell.
Ha nem kell, törölhető:

```java
            // WUZENET (helyi alert/üzenet-eljárás): a webes képernyőn üzenetként jelenik meg. Az eredeti kódja, ha később kellene:
            // PROCEDURE wuzenet(vv_uzenet varchar2) IS
            //   m_alertdialog  CONSTANT VARCHAR2(15) := 'QMS$INFORMATION';
            // …
```

**SQL-megjegyzések:** a szűrőépítő `--…` megjegyzései változatlanul a futó PL/SQL-ben maradnak.

## 2. Fejlesztői bemenetek: amit a kód sehonnan nem kaphat meg

Eddig egy trigger kézi feladat lett, ha olyan értéket olvasott, aminek a migrált felületen nincs forrása. Most a
trigger lefut, és minden ilyen értékből a generált Java-metódusban egy saját változó lesz. Ezt a fejlesztő tölti fel:

```java
            // TODO: :XX.YY (ismeretlen mező, nincs a formban): add át ennek a változónak a megfelelő értéket.
            String xxYy = null;
            Object[] out = DbCalls.call(jdbc, "DECLARE …",
                    DbCalls.in(xxYy, Types.VARCHAR),
                    …
```

| Hivatkozás | Mikor lesz fejlesztői bemenet |
|---|---|
| `:BLOKK.MEZŐ`, ami nincs a formban | mindig |
| másik blokk mezője | rekordszintű adattriggerben (`POST-QUERY`, `PRE-INSERT` …), ahol csak a saját rekord érhető el |
| `:GLOBAL.X`, `:PARAMETER.X` | adattriggerben (a gombok és a mentés a képernyőtől kapják) |
| `:SYSTEM.X` | ha a képernyő nem küldi, illetve adatbázis-oldalon nincs megfelelője |
| blokk nélküli `:MEZŐ` | ha nem egyértelmű |

- **Hol működik:** gombakció, lekérdezőgomb, mentési pont, `PRE-COMMIT` / `POST-FORMS-COMMIT`, indítási kód,
  adattriggerek. A helyi eljárásokban lévő ilyen hivatkozásokra is.
- **Típus:** a változó típusa a mezőé (`String`, `java.math.BigDecimal`, `java.time.LocalDateTime`), ismeretlen
  mezőnél `String`.
- **Írás:** ha a kód egy ilyen értéket *írna* (pl. `:GLOBAL.X := …` adattriggerben), az a migrált felületen elveszne.
  Ez továbbra is kézi feladat, megnevezett okkal.
- **Amíg nem töltöd fel:** a kód `null` értékkel fut.
- **Kimutatás:**
  - a metódus feletti megjegyzés: „Fejlesztői bemenet (…): :XX.YY -> xxYy”;
  - `BACKEND_TASKS.md` → **Fejlesztői bemenetek** táblázat (trigger, Forms-hivatkozás, Java-változó, ok);
  - `analysis/backend-plan.json` → `developer_inputs` a gombvégpontoknál;
  - `RUNTIME_COVERAGE.md` → a trigger hiányai között.
- **Korlát:** a `DEFAULT_WHERE` szűrőszövegben (a SQL-en belül) továbbra is csak a formban lévő mezők
  szerepelhetnek, mert azokból típusos JDBC-bind készül.

## Ellenőrzés

- **Új tesztek:**
  - `test_query_actions`: a valódi `WUZENET`-alakkal a lekérdezőgomb fut, az eljárás kódja megjegyzésként a
    metódusban van, a szűrő `--` megjegyzése a PL/SQL-ben marad;
  - `test_query_actions`: kilenc eljárásmintán ellenőrzi, mi számít üzenetnek és mi nem.
- **Új tesztfájl (`test_developer_inputs`):**
  - gomb ismeretlen mezővel és `:SYSTEM.LAST_QUERY`-vel;
  - `POST-QUERY` `:GLOBAL`-lal és másik blokk mezőjével;
  - mentési pontos gomb és `PRE-COMMIT` a felmérési replikán;
  - a generált Java minden esetben lefordul (`javac`).
- **Módosított tesztek:** két régi teszt elutasítást várt ugyanilyen hivatkozásokra; ezek most a fejlesztői
  bemenetet ellenőrzik.
- **Teljes tesztkészlet:** 586 teszt. A 101 hibás teszt ugyanaz, mint a `main` ágon (a repóból hiányzó
  mintabemeneteket keresik), új hibás teszt nincs.

## Átállás

- **A már generált ServiceImpl** CREATE_ONCE fájl. Az új változatot a `--regenerate` az
  `analysis/backend-regeneration/` mappába teszi, onnan kell átvenni.
- **A `frm-forms-screen.ts` és a `CommonMigrateTools.java`** nem változott.
