# Egyfájlos Angular képernyőváz

Elsőként: `frontend/teszt/MIGRATION_NOTES.md`.

A képernyő a szerkezetet követi, a pozíciókat relatív oszlopokra alakítja. Az események és adatok bekötése fejlesztői feladat. A generált backend műveletei ellenőrzésig tiltottak.

3 régió; 0 technikai/rejtett mező a megjelenítésen kívül.

A következő rész a szigorú generátor auditját is tartalmazza. Az ismeretlen property-k továbbra is ellenőrizendők; a képernyőváz ettől független, prezentációs célú kimenet az effektív XML-ből és az UI-elemzésből.

---

# Migrációs riport — FRM_ANK_TESZT

Ez tényleges generálási leltár, nem funkcionális azonosságot becslő százalék. A forrás XML teljes példánya az analysis/source.xml fájlban marad. Konkrét backendbekötések és megmaradt feladatok: [BACKEND_TASKS.md](BACKEND_TASKS.md).

| Mutató | Darab |
|---|---:|
| Blokkok | 5 |
| Mezők/gombok | 17 |
| Triggerek | 15 |
| Szabálymotor által felismert triggerek | 1 |
| Átültetendő triggerek | 3 |
| Keretrendszeri (nem teendő) triggerek | 11 |
| Blokkoló leletek | 5 |

## Blokkok

| Oracle blokk | REST-részútvonal | Lekérdezés | Beszúrás | Módosítás | Törlés |
|---|---|---|---|---|---|
| V_ELEK_ADLAP | vElekAdlap | — | — | — | — |
| AIT | ait | tiltva | nem készül | tiltva | nem készül |
| CGNV$W01_1 | cgnvW011 | — | — | — | — |
| CALENDAR | calendar | — | — | — | — |
| QMS$TRANS_ERRORS | qmsTransErrors | — | — | — | — |

A „kész, MODULE_REVIEWED-re vár” műveleteknek nincs saját tiltása; a DPS ServiceImpl egyetlen MODULE_REVIEWED kapcsolója élesíti őket, ha a modulszintű ellenőrzés megtörtént:

- Migrációs váz: az összes adatbázis-művelet tiltott az ellenőrzött implementációig.

Backend nélküli blokkok (nincs adatforrás):

- CALENDAR: Keretrendszer-blokk (frm_forms/data/framework-catalog.json): Headstart naptár-segédablak: a dátummezők natív naptárvezérlője váltja ki. DatabaseDataBlock=true, de nincs QueryDataSourceName/DMLDataTargetName: nincs mit lekérdezni vagy menteni, ezért nem készül backend-végpont. Valódi táblánál add meg a schema.json blocks.CALENDAR.table értékét.
- QMS$TRANS_ERRORS: Keretrendszer-blokk (frm_forms/data/framework-catalog.json): Headstart tranzakciós hibakonzol: a fogadó alkalmazás hibakezelése váltja ki. DatabaseDataBlock=true, de nincs QueryDataSourceName/DMLDataTargetName: nincs mit lekérdezni vagy menteni, ezért nem készül backend-végpont. Valódi táblánál add meg a schema.json blocks.QMS$TRANS_ERRORS.table értékét.

## Triggerek

| Azonosító | Eredmény | Cél / ok | SHA256 |
|---|---|---|---|
| FRM_ANK_TESZT:PRE-FORM | framework | framework | 2d6bf882d3c600a9173d18a28a5cf3ea7e64ffbe0de2a0ec48df34e4e7fa2a83 |
| FRM_ANK_TESZT:WHEN-NEW-FORM-INSTANCE | framework | framework | b1cdb4554673aa293cf74836e0253cc849ba5f02c4a41ce236a4c07e0289837c |
| FRM_ANK_TESZT:ON-ERROR | framework | framework | 689b841ceb1f679850d7683c6be859d53876165369ae02e49e44e13382a64a5e |
| FRM_ANK_TESZT:ON-MESSAGE | framework | framework | f632c7e2530b7c4734d488f0b296e0b2155668a760b349fc562a7724c8f096b9 |
| FRM_ANK_TESZT:KEY-EXIT | framework | framework | 63f393d7c77ac4e847fe6a2d9df8fbb096800580bd0eec3ffdd6404a7887368f |
| FRM_ANK_TESZT:KEY-HELP | framework | framework | abe56b8ef4acf07d7e8b2daac3188ed2adb99d3cbed7a4b6aef324b4bb748dc1 |
| FRM_ANK_TESZT:KEY-CLRFRM | framework | framework | 1200cb1eeadb7cddc9cc23e059da14486365f64fe07eca51fa5fe7b3b5d2622c |
| FRM_ANK_TESZT:WHEN-WINDOW-CLOSED | framework | framework | 683e5d7e1b2901e5c3ca7e78218ea662fc500f3ce9b5a5f800c971b330e4fc0b |
| FRM_ANK_TESZT:POST-FORMS-COMMIT | framework | framework | 370f41e43380338ac1626c754efd4e93baf4ad3f6b5123be24b10062afda9228 |
| FRM_ANK_TESZT:KEY-COMMIT | review | billentyű-trigger: a webes képernyő saját vezérlője hívja a generált végpontot; a végpontot nem tiltja, a többletlogikát a képernyőn kell átnézni. | 92227ec95d84b2c09dcd933a37443f858f1556d60c0cbf47ad72262ce09a4bae |
| AIT:WHEN-NEW-RECORD-INSTANCE | framework | framework | 89290923e56ac947f438b6b20db96f7921756cac59a3c236b4230731ce509361 |
| AIT:KEY-DELREC | review | billentyű-trigger: a webes képernyő saját vezérlője hívja a generált végpontot; a végpontot nem tiltja, a többletlogikát a képernyőn kell átnézni. | 6626f267a0734f2c5d714ed69f0f0dcdbcd2a72d9dadaddfdfd046d1d4d06156 |
| AIT:POST-QUERY | review | POST-QUERY nem írhat adatbázismezőt ebben a snapshot-alapú adapterben. Átfuttatás az adatbázisban sem lehetséges: A trigger ebben az eseményben nem visszaírható mezőt ír: AIT.AIT_MEGJ (lekérdezett adatbázismező, kulcs vagy nem módosítható oszlop). | b4eec645a6e44ab773d123f0be953e7f2ff2c260f47563d40d0c31740826d0ab |
| CGNV$W01_1.PB_RESZLETEK:WHEN-BUTTON-PRESSED | converted | action | e9a4f9392210c5d001119c0ae580c610880698a09fd0e8cdaacdfac0204f1041 |
| CALENDAR:WHEN-NEW-BLOCK-INSTANCE | framework | framework | eaceeda785822af10cae870b4685b1f74e59648fec4a4c9132ee40b9f07f829e |

## Ellenőrzési tételek

| Kód | Objektum | Hatókör | Részlet |
|---|---|---|---|
| DYNAMIC_LOV | V_ELEK_ADLAP | write | V_ELEK_ADLAP.UBI_INPTIP_KOD: LOV=INPTIP; rekordcsoport/return mapping adapter szükséges. |
| QUERY_FILTER | AIT | write | A WHERE olvasási SQL-re lefordítva; írás előtt a rekordszűrést és szervercontextet külön ellenőrizni kell. |
| ORDER_BY | AIT | review | Az eredeti ORDER BY ismert oszlopokkal átültetve: AIT_KULCS DESC |
| PRESENTATION_OR_CONTEXT | @FORM:FRM_ANK_TESZT | review | canvas: CG$PAGE_1; az eredeti XML/FIR megőrzi, pixelpontos elrendezés/context nem generálódik. |
| PRESENTATION_OR_CONTEXT | @FORM:FRM_ANK_TESZT | review | window: WINDOW; az eredeti XML/FIR megőrzi, pixelpontos elrendezés/context nem generálódik. |
| QUERY_BIND_REQUIRED | AIT | read | A paraméter nélküli lista tiltott. A típusos searchAit metódusnak add át: V_ELEK_ADLAP.UBI_INPTIP_KOD |
| FRAMEWORK_DATA_TRIGGER | @FORM:FRM_ANK_TESZT | review | FRM_ANK_TESZT:POST-FORMS-COMMIT: csak keretrendszer-hívás (qms$event_form('POST-FORMS-COMMIT')); a végpontot nem tiltja. Ellenőrizd, hogy az adatbázisoldali logika (Table API, DB-trigger) lefedi-e. |
| UNSUPPORTED_TRIGGER | @FORM:FRM_ANK_TESZT | frontend | FRM_ANK_TESZT:KEY-COMMIT: billentyű-trigger: a webes képernyő saját vezérlője hívja a generált végpontot; a végpontot nem tiltja, a többletlogikát a képernyőn kell átnézni. |
| UNSUPPORTED_TRIGGER | AIT | frontend | AIT:KEY-DELREC: billentyű-trigger: a webes képernyő saját vezérlője hívja a generált végpontot; a végpontot nem tiltja, a többletlogikát a képernyőn kell átnézni. |
| UNSUPPORTED_TRIGGER | AIT | all | AIT:POST-QUERY: POST-QUERY nem írhat adatbázismezőt ebben a snapshot-alapú adapterben. Átfuttatás az adatbázisban sem lehetséges: A trigger ebben az eseményben nem visszaírható mezőt ír: AIT.AIT_MEGJ (lekérdezett adatbázismező, kulcs vagy nem módosítható oszlop). |
| KEY_DISABLES_OPERATION | AIT | review | AIT:KEY-DELREC: a Forms-felületen a(z) törlés nem érhető el (a trigger nem hívja: DELETE_RECORD); a generált képernyő sem kínálja fel. |
| SCAFFOLD_REVIEW_REQUIRED | @FORM:FRM_ANK_TESZT | all | Migrációs váz: az összes adatbázis-művelet tiltott az ellenőrzött implementációig. |

Hatókör: all = a kapcsolódó olvasás és írás tiltva; read = csak az olvasás; write = minden írás; create/update/delete = csak az adott írás; button = az érintett gomb tiltva; frontend = képernyő-feladat, a backendet nem tiltja; review = megőrzött/ellenőrizendő elem.

## AI-használat

```json
{
  "mode": "off",
  "attempted_calls": 0,
  "cache_hits": 0,
  "successful_calls": 0,
  "failures": 0,
  "skipped_size": 0,
  "skipped_budget": 0,
  "cache_misses": 0,
  "prompt_tokens": 0,
  "output_tokens": 0,
  "unique_candidates": 0,
  "reused_advice": 0
}
```

Az AI-javaslatok nem oldanak fel tiltást, és nem kerülnek a forráskódba. Az analysis/ai-advice.json tartalmazza őket.

## Esemény- és adatbázis-szemantika

A következő szemantika a Java backendhez tartozik. A 3.0-s frontend csak a FormBlock képernyőt építi fel; az eredeti gombok eseményt adnak a hostnak, a triggerek nem futnak automatikusan a böngészőben.

- Egy REST-mentés egy blokk egyetlen rekordját kezeli egy Spring-tranzakcióban. Több blokk COMMIT_FORM viselkedése nem támogatott.
- WHEN-VALIDATE-ITEM: minden támogatott item-trigger lefut mentéskor, XML-sorrendben; utána WHEN-VALIDATE-RECORD, majd PRE-INSERT/PRE-UPDATE. Nincs Forms-fókuszváltási állapotgép.
- PRE-DELETE a zárolt aktuális rekordon, POST-QUERY a betöltött rekordon fut. POST-QUERY adatbázisoszlopot író változata blokkolva van a snapshot sértetlensége miatt.
- A mentési snapshot az összes leképezett DB-oszlopot összehasonlítja, SELECT FOR UPDATE után. DB oldali version oszlop nélkül az ABA-változás (visszaállított érték) nem érzékelhető.
- BigDecimal, Oracle üres sztring = NULL, háromértékű logika és rövidzár támogatott. NLS, CHAR padding, locale szerinti rendezés, numerikus túlcsordulás és teljes Oracle NUMBER kerekítés nem emulált. Osztás: 38 számjegyű MathContext.
- DATE a napszakot is megőrzi, LocalDateTime formában. TIMESTAMP időzónával, LOB, LONG, RAW és speciális típusok adaptert igényelnek.
- A meglévő DB triggerek, constraint-ek, defaultok és package-ek továbbra is a DB-ben maradnak; az eszköz nem csatlakozik az adatbázishoz és nem ellenőrzi őket.
- UI Enabled/Visible nem jogosultság. A host alkalmazás jogosultságát és sor-/tenant-szűrését be kell kötni.
- A komponens standard mezőelrendezést készít; az eredeti canvas, LOV, tab, alert és összes Forms-runtime tulajdonság nem emulált.

## Beépítés

A konkrét package, import és útvonal az INTEGRATION.md fájlban található. A generátor tesztelése nem helyettesíti a host Java/Angular buildet és az üzleti regressziót.

## Frontend modell

A sémavalidált mezőterv: analysis/ui-model.json. A generált frontendben nincs automatikus CRUD vagy Forms runtime.


## Modultérkép és gombvégpontok

Kereshető, offline objektum- és hívástérkép: analysis/discovery/form-explorer.html. Eredeti forrás: analysis/discovery/sources/.
A gombesemények fix végpontterve: analysis/action-plan.json. A lexikai hívásjelöltek szignatúrája, iránya és futási sorrendje ellenőrizendő.
Java generáláskor modulonként 4 CL-, 5 DPS- és 5 WBS-fájl készül. Fájlok, DTO-kiválasztás és végpontok: analysis/backend-plan.json.
A DPS *ServiceImpl.java CRUD-metódusainál az adatforrás és műveleti utasítások, protected *Reviewed gombmetódusainál a forrás/híváslánc és HTTP 501 váz található. Ez a fájl CREATE_ONCE, itt folytasd az implementációt.

## Frontend UI-modell 1.0.0

A frontend kizárólag az analysis/ui-model.json fájlból készül. Az ItemType forrása és az OLB property-proveniencia külön adat.

Itemek: 17. ItemType attribútumból: 17/17; heurisztikából: 0/17.

| Widget | Darab |
|---|---:|
| autocomplete | 1 |
| button | 1 |
| checkbox | 1 |
| datetime | 2 |
| display | 3 |
| number | 1 |
| text | 8 |

### Nem támogatott itemek

| Item | Canvas | ItemType / ok |
|---|---|---|
| — | — | Nincs |

### Öröklés

Feloldott kapcsolatok: 0.

Feloldatlan öröklés: nincs.

### Endpoint-deklarációk

| Azonosító | Művelet | Útvonal | Állapot |
|---|---|---|---|
| ait_read | POST | /api/forms/teszt/ait/query/search | meglévő backend engedélyei szerint |
| ait_update | PUT | /api/forms/teszt/ait/update | meglévő backend engedélyei szerint |
| vElekAdlap_ubiInptipKod_lov | GET | TODO: host szerződés | csak deklaráció; nincs LOV backend |

### Frontend issue-k

| Súly | Kód | Hely | Leírás |
|---|---|---|---|
| error | UNMAPPED_ATTRIBUTE | /module/formmodule:FRM_ANK_TESZT[0] | formmodule.firstnavigationblock: Nincs jóváhagyott leképezés; inventory és property_aliases segítségével ellenőrizd. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/block:AIT[12]/trigger:KEY-DELREC[6] | TODO: frontend besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/block:AIT[12]/trigger:POST-QUERY[7] | TODO: backend besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/block:AIT[12]/trigger:WHEN-NEW-RECORD-INSTANCE[5] | TODO: manual besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/block:CALENDAR[14]/trigger:WHEN-NEW-BLOCK-INSTANCE[2] | TODO: manual besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/block:CGNV$W01_1[13]/item:PB_RESZLETEK[0]/trigger:WHEN-BUTTON-PRESSED[0] | TODO: frontend besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/trigger:KEY-CLRFRM[7] | TODO: manual besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/trigger:KEY-COMMIT[10] | TODO: backend besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/trigger:KEY-EXIT[5] | TODO: manual besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/trigger:KEY-HELP[6] | TODO: manual besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/trigger:ON-ERROR[3] | TODO: manual besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/trigger:ON-MESSAGE[4] | TODO: frontend besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/trigger:POST-FORMS-COMMIT[9] | TODO: backend besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/trigger:PRE-FORM[1] | TODO: manual besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/trigger:WHEN-NEW-FORM-INSTANCE[2] | TODO: manual besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | TRIGGER_ACTION_TODO | /module/formmodule:FRM_ANK_TESZT[0]/trigger:WHEN-WINDOW-CLOSED[8] | TODO: manual besorolás statikus függőségi jelzés; a trigger nem kerül automatikusan végrehajtásra. |
| review | EXACT_DECIMAL_TEXT | AIT.AIT_KULCS | A NUMBER szöveges decimálisként marad pontos; JS Number konverzió nincs. |
| review | FLOW_LAYOUT | CALENDAR// | Nincs koordináta: explicit XML-sorrend, teljes szélesség; pixelértéket nem feltételezünk. |
| review | FLOW_LAYOUT | QMS$TRANS_ERRORS// | Nincs koordináta: explicit XML-sorrend, teljes szélesség; pixelértéket nem feltételezünk. |
| review | LOV_ENDPOINT_TODO | V_ELEK_ADLAP.UBI_INPTIP_KOD | TODO: a LOV endpoint URL-jét és a query/return mapping adapterét a host adja meg. Az SQL csak analysis fájl. |
| review | SCREEN_SPACER_ITEM | V_ELEK_ADLAP.L_URES_1 | Elrendezési térköz (keretrendszer-katalógus: L_URES_*): üres FormBlock-elem, adatkötés és validáció nélkül. |
| review | SCREEN_SPACER_ITEM | V_ELEK_ADLAP.L_URES_2 | Elrendezési térköz (keretrendszer-katalógus: L_URES_*): üres FormBlock-elem, adatkötés és validáció nélkül. |
| review | SCREEN_SPACER_ITEM | AIT.L_URES_3 | Elrendezési térköz (keretrendszer-katalógus: L_URES_*): üres FormBlock-elem, adatkötés és validáció nélkül. |

Az ismeretlen Optimus adapterek és az üzleti triggerek TODO-integrációs pontok. Nincs kitalált komponensnév vagy automatikusan végrehajtott PL/SQL.
