# Lekérdezésgombok, DO_KEY és a megmaradó 501-es metódusok

## CHAR jelölők az eredeti Oracle-feltételben

Ugyanaz a `DEFAULT_WHERE` szűrőépítő korábban `NUMBER` jelölőkkel lefordult,
`CHAR` jelölőkkel kézi végpontot kapott. A felismerő a natív PL/SQL `IF`
feltételére is a JDBC SQL-fordító szigorú típusvizsgálatát használta.

Most az eredeti, tiszta PL/SQL-feltétel Oracle-ben fut. Például a
`:CONTROLS.FLAG_A = 1` szöveges mezővel is megmarad. A mező bindje továbbra is
`VARCHAR2`, és nincs új Java-oldali számkonverzió vagy feltételkiértékelés.
A `NULL`, a konverziós hiba és az NLS-kezelés az eredeti Oracle-kód feladata.
Az `NVL` és a támogatott tiszta STANDARD-függvények is ezen az útvonalon maradnak.

Ismeretlen mező, Forms-getter, saját helyi függvény és mellékhatásos
eljárás hívása nem válik automatikusan tiszta feltétellé. A végleges WHERE
előre lefordított SQL-változatokat és típusos bindeket használ; annak SQL-
típusellenőrzése változatlan. A szűrőn túl végzett üzleti művelet megmarad
kézi feladatnak, amíg nincs a teljes működést lefedő adapter.

## DO_KEY és a key-trigger forrása

A `DO_KEY` először a hozzá tartozó key-triggert indítja; a built-in csak
key-trigger hiányában fut. A migrátor közös eseménytérképet használ például:

| Hívás | Forms-esemény |
|---|---|
| `DO_KEY('LIST_VALUES')` | `KEY-LISTVAL` |
| `DO_KEY('EXECUTE_QUERY')` | `KEY-EXEQRY` |
| `DO_KEY('COMMIT_FORM')` | `KEY-COMMIT` |

A korábbi lexikai térkép `KEY-LIST-VALUES` nevet állított elő, így nem találta
meg a valódi `KEY-LISTVAL` forrást. Ez javítva van. Az eseményjelöltek és a
belőlük hívott helyi programegységek forrásai az `analysis/backend-evidence.md`
fájlban szerepelnek. Ezek elemzési jelöltek: a hatókört és az eseményhierarchiát
nem állítjuk automatikusan végrehajtási sorrendnek.

A dinamikus lekérdezés-adapter az eljárás végi `DO_KEY('EXECUTE_QUERY')`
hívást is támogatja, ha nincs alkalmazható saját `KEY-EXEQRY`. A saját key-
trigger megtartása akkor is szükséges, ha csak `NULL` áll benne: ez a Forms-ban
felülírja az alapműveletet.

A puszta LOV-nyitó továbbra is összevonható a célmező saját nyitójával.
Saját `KEY-LISTVAL` üzleti logikája esetén a gomb megmarad; ilyen kód nem
tűnik el zajként. A katalógusban azonosított, csak natív naptárt helyettesítő
item-trigger továbbra is natív dátumvezérlőt és backend nélküli nyitást kap.
A `MouseNavigate=false` gombnál a fókusz megmaradhat másik mezőn: csak az
explicit `GO_ITEM`/`GO_BLOCK` igazolja a következő `DO_KEY` célját.

Hivatalos háttér: [Oracle Forms Builder Reference, DO_KEY, 95–96. oldal](https://docs.oracle.com/cd/A97337_01/ias102_otn/buslog.102/a73074.pdf).

## Teljes diagnosztika és a régi ServiceImpl

A kézi akció `adapter_diagnostics` mezője az `analysis/backend-plan.json`
fájlban külön mutatja a frontend-, query- és natív PL/SQL-felismerés okát.
A Java-komment sortöréssel a teljes okot tartalmazza; a szűrőépítő konkrét
felismerési hibája a generikus UI-hiba elé kerül. A teljes ok az eredeti forrás
mellett az `analysis/backend-evidence.md` fájlban is szerepel.

A `--regenerate` megőrzi a már meglévő ServiceImpl/ControllerImpl és Angular
komponensfájlokat. Ha a backendhez friss javaslat készül, a terv
`regeneration_status.manual_merge_required: true` értéket és a megőrzött
fájlok listáját tartalmazza. Az `implemented` és `runs` a friss generált
javaslatot írja le; a megőrzött régi fájl még tartalmazhat HTTP 501-et.

Az új felismerést először új kimeneti mappába generálva ellenőrizd. Vedd át
együtt az érintett CL/DPS/WBS és Angular fájlokat, vagy fésüld össze a backend
`analysis/backend-regeneration/changes.json` szerinti javaslatát és a friss
Angular komponenst a saját módosításokkal. A query-akció kérés- és válasz-
szerződése eltér a korábbi általános akcióétól.

## A még hiányzó konkrét források

A `ROGZITOFORM_HIVASA` név önmagában nem határozza meg a célformot, az átadott
paramétereket, a visszatérési működést vagy a mentési mellékhatásokat.
Ezekhez a tényleges Forms XML és a hívott programegység teljes kódja szükséges.
A három rövidített Java-komment alapján nem állítható, hogy a konkrét
modul minden gombja már működik. A friss generálás `backend-plan.json`,
`backend-evidence.md` és szükség esetén a teljes Forms XML adja a további
általános adapterek alapját.

Az új regressziók szöveges jelölőket, átnevezett modul/blokk/mező/eljárás
mintát, form-/blokk-/item-szintű key-triggert, naptár- és LOV-nyitót, saját
függvények megtartását és a régi 501-es implementáció megőrzését ellenőrzik.
A generált Java fordítás és JDBC-próbadupla ellenőrzi a `NUMBER` és `VARCHAR`
bindekkel a builder → SELECT → POST-QUERY folyamatot. Valódi Oracle és a
teljes céges Angular/Spring környezet nem áll rendelkezésre a helyi teszthez.
A NORMAL-mód és a CommonMigrateTools API-ja változatlan.

## Naptárgomb több szintű KEY-LISTVAL-lal

A Headstart naptárgombja (`go_item` a dátummezőre, `do_key('List_values')`,
`copy('0', 'GLOBAL.save_mouse_record')`) gyakran olyan formban áll, ahol a `KEY-LISTVAL`
a mezőn, a blokkon és a formon is szerepel, mindhárom helyen a katalógusban szereplő
`qms$calendar.key_listval` hívással. A Forms a legspecifikusabb triggert futtatja; mivel
itt bármelyik szint csak a naptárat nyitja, a gomb összevonható a dátummező saját naptárával.
Ilyenkor a gomb nem jelenik meg a felületen, és backend-végpont sem készül hozzá. Ha bármelyik
jelölt mást is csinál, a gomb megmarad, és a kódja kommentként a metódusba kerül.

## Formhívás (CALL_FORM / OPEN_FORM / NEW_FORM) → Angular-navigáció

A gomb triggerében vagy az általa hívott, paraméter nélküli helyi eljárásban felismert
minta: paraméterlista (`GET_PARAMETER_LIST` / `CREATE_PARAMETER_LIST` / `DESTROY_PARAMETER_LIST`),
`ADD_PARAMETER(..., TEXT_PARAMETER, érték)` és egyetlen `CALL_FORM`, `OPEN_FORM` vagy `NEW_FORM`.
Az érték lehet képernyőmező (`:BLOKK.MEZŐ`, `NAME_IN`, `TO_CHAR`), szövegállandó, szám, illetve
ezekből értéket kapó helyi változó. A Headstart `qms$...` hívásai keretrendszeri zajnak számítanak.

- **Backend:** a gombhoz nem készül végpont.
- **Angular:** `private readonly router = inject(Router);`, és az `onAction`-ben
  `this.navigate(ownId)`: `this.router.navigate([útvonal], { queryParams })`.
- **Útvonal:** alapból `/<form neve kisbetűvel>` (például `/rogzito`); a `form_routes` beállítás írja
  felül: `"form_routes": {"ROGZITO": "/pages/modules/rogzito"}`.
- **Áttekintés:** a navigációk az `analysis/screen-plan.json` `navigations` részében és a
  MIGRATION_NOTES „Navigáció” táblázatában szerepelnek.

Ha az eljárás mást is csinál (lekérdezés, feltétel, üzenet, `DATA_PARAMETER`), a gomb kézi
feladat marad: a metódusba kommentként bekerül a trigger, a hívott helyi eljárások és a
`DO_KEY` által indított key-triggerek eredeti kódja.

## Helyi üzenetkiíró eljárás (például WUZENET) a lekérdezőgombban

A lekérdező gomb `ELSE` ágában álló `wuzenet('…')` akkor is üzenetnek számít, ha a `WUZENET`
a formban definiált helyi eljárás, feltéve, hogy csak a kapott szöveget jeleníti meg.
Elfogadott tartalom: egy `VARCHAR2` paraméter, `MESSAGE(p)` vagy alert a szöveggel
(`SET_ALERT_PROPERTY(…, ALERT_MESSAGE_TEXT, p)` / `CHANGE_ALERT_MESSAGE`, `SHOW_ALERT`,
`FIND_ALERT`), `SYNCHRONIZE`, `BELL`, és a végén esetleg `RAISE FORM_TRIGGER_FAILURE`.
Ha az eljárás mást is csinál (például naplóz egy táblába), a gomb kézi feladat marad.

## Összetett formhívás → a komponens saját metódusa

Ha a gomb (vagy az általa hívott helyi eljárás) formot hív (`CALL_FORM` / `OPEN_FORM` /
`NEW_FORM`), de a hívás nem fordítható le automatikusan, a navigáció a frontend kézi feladata.
Ilyen eset például, ha a célformot egy kód vagy lekérdezés választja ki. Ilyenkor:

- **Backend:** nem készül hozzá 501-es végpont, mert a navigáció felületi művelet.
- **Komponens:** a gomb saját `navigate<Gomb>()` metódust kap a kijelölt táblázatsorokkal
  (`this.<tábla>Selection`), a `Router`-rel, egy `router.navigate` mintasorral és kommentként
  az eredeti Forms-kóddal (a trigger és az általa hívott helyi eljárások). Amíg nincs befejezve,
  `Nincs bekötve` toastot mutat.
- **Áttekintés:** a lista az `analysis/screen-plan.json` `manual_navigations` részében és a
  MIGRATION_NOTES „Navigáció” szakaszában szerepel.

Ha az eljárás adatot is módosít (`INSERT` / `UPDATE` / `DELETE` / `MERGE` / `COMMIT`), a gomb
backend-oldali kézi feladat marad, a kódjával kommentként.

