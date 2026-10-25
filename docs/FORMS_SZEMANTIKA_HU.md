# Forms-szemantika puska a kézi átültetéshez

Ami Forms-ból webre átírva a legkönnyebben elromlik, és amit a generált kód hogyan kezel. Ugyanez a lista a
generált `analysis/MUNKAPAD.html` alján is megtalálható.

## Rekordállapotok

| Forms | Jelentés | A generált képernyőn |
|---|---|---|
| NEW | üres új rekord | üres űrlap, nincs eredeti rekord |
| INSERT | új, kitöltött rekord | módosított (dirty) űrlap eredeti rekord nélkül: a mentés **beszúrás** |
| QUERY | lekérdezett, változatlan | `originals[blokk]` = a backend DTO-ja (rejtett mezők, ROWID is) |
| CHANGED | lekérdezett, módosított | dirty űrlap eredeti rekorddal: a mentés **módosítás** (ütközésfigyeléssel) |

A törölt rekordot a Forms a következő mentésig tartja számon, a képernyőn ez a `pendingDeletes` lista.

## FORM_TRIGGER_FAILURE

Formsban a trigger és a futó művelet leáll, a már elvégzett DML viszont **nem** görgetődik vissza magától. A webes
végpont ORA-20999 → HTTP 422 válasszal áll le, a végpont tranzakciója pedig visszagörget. Mentéskor így semmi
sem marad félúton, és a Formsnál szigorúbban viselkedik.

## Triggerek sorrendje mentéskor (COMMIT_FORM)

1. Validálás: WHEN-VALIDATE-ITEM (a generált kódban mentéskor, nem a mező elhagyásakor), WHEN-VALIDATE-RECORD
2. PRE-COMMIT
3. Blokkonként, a blokkok sorrendjében:
   - törölt rekordok: (ON-CHECK-DELETE-MASTER), PRE-DELETE, ON-DELETE vagy DELETE, POST-DELETE
   - új és módosított rekordok, rekordsorrendben: PRE-INSERT/PRE-UPDATE, ON-INSERT/ON-UPDATE vagy a DML, POST-INSERT/POST-UPDATE
4. POST-FORMS-COMMIT
5. COMMIT (ON-COMMIT), majd POST-DATABASE-COMMIT

A `commitForm` végpont ugyanezt a sorrendet követi, egy tranzakcióban.

## WHEN-VALIDATE-ITEM vagy PRE-INSERT?

- A **WHEN-VALIDATE-ITEM** ellenőriz és származtatott mezőt tölt ki. Kulcsot nem írhat.
- A **PRE-INSERT** csak beszúrás előtt fut: itt kap kulcsot a rekord (szekvencia), és töltődnek az
  auditoszlopok. Kulcsot csak a PRE-INSERT és az ON-INSERT írhat.
- A **POST-QUERY** lekérdezett soronként fut. Lekérdezett adatbázismezőt nem írhat (különben a rekord CHANGED
  lenne), csak leírásmezőt (megnevezést).
- A **POST-CHANGE** régi stílusú validálás. Lekérdezéskor is lefut, ha a mező nem üres.

## :SYSTEM változók

| Változó | Formsban | Webes megfelelő |
|---|---|---|
| CURSOR_BLOCK / CURSOR_ITEM | ahol a kurzor áll; gombnyomáskor a gomb blokkja, ha Mouse Navigate = Yes | a képernyő az utoljára szerkesztett blokkot küldi |
| TRIGGER_BLOCK / TRIGGER_ITEM | a futó trigger gazdája | a generáláskor rögzített |
| MODE | NORMAL / ENTER-QUERY / QUERY | mindig NORMAL |
| FORM_STATUS / BLOCK_STATUS / RECORD_STATUS | NEW / QUERY / CHANGED | közelítés: módosított űrlap = CHANGED, különben QUERY |
| MESSAGE_LEVEL | az üzenetszűrés szintje | 0 (a kód írhatja, a kérésen belül érvényes) |

## :GLOBAL és :PARAMETER

- A **:GLOBAL** munkamenet-szintű, szöveges, a formok között közös. A képernyő a böngészőfülön (sessionStorage)
  tárolja, minden kéréssel elküldi, és amit a PL/SQL ír, azt a válaszból visszamenti.
- A **:PARAMETER** a hívó form paraméterlistája. Webes megfelelője a cél útvonal query paraméterei: a
  CALL_FORM így adja át őket.

## Master-detail

- A detail rekord kulcsa a masterből jön (Copy Value from Item, a reláció join-feltétele). A mentési lánc a most
  mentett masterből, különben a master képernyőértékéből tölti ki.
- **Non-Isolated** (alapértelmezés): a master nem törölhető, ha van detailje (a Forms által generált
  ON-CHECK-DELETE-MASTER).
- **Cascading**: a detailek is törlődnek (a master PRE-DELETE-je).
- **Isolated**: nincs ellenőrzés.

## Forms-hívás a kód közepén

Formsban a `COMMIT_FORM`, az `EXECUTE_QUERY` és a `CALL_FORM` azonnal lefut, a következő utasítás már a
hatását látja. A webes emuláció a Forms-hívásokat a PL/SQL **végén** hajtja végre a képernyőn. Ezért ezek után
nem állhat olyan utasítás, amely mezőt olvas vagy SQL-t futtat; ilyenkor a generátor kézi feladatnak jelöli.
Kézi átíráskor a hívás utáni részt külön lépésbe kell tenni (például a lekérdezés után egy második
akció-végpontba).

## SHOW_ALERT

A webes kérés nem tud egy kérdésnél megállni. A válasz nélküli alert visszagörgeti a kérés addigi munkáját
(mentési pont), a képernyő megkérdezi a felhasználót, és a válasszal a kód **elölről** fut le újra. Az alert
előtti adatbázis-munka tehát kétszer fut, de csak egyszer véglegesedik. Ha az alert előtt nem
tranzakciós mellékhatás van (fájl, e-mail, autonóm tranzakció), azt kézzel kell átnézni.
