# 4.15 – javítások egy valódi felmérés alapján

Egy 6 formos valódi felmérés (4.14) szerint a végpontok 42%-a működött. A legtöbb a **gomb- és indítási
végpontoknál** maradt tiltva: 37-ből 33. A riportból az is kiderült, hogy néhány ok rejtve maradt. A 4.15 a
leggyakoribb, egyedüli okként tiltó okokat szünteti meg, és a felmérést pontosítja, hogy a következő futás a
valódi okokat mutassa.

| Felmérési ok | Egyedüli okként tiltott végpont | 4.15 |
|---|---:|---|
| Képernyőlépés a kód közepén (`EXECUTE_QUERY`, `CLEAR_BLOCK` … után még kód fut) | 7 (a gomb saját kódjában) | **képernyőpont** (1. pont) |
| Helyi csomag inicializáló résszel (`BEGIN` a csomagtörzs végén) | 6 | **támogatott** (2. pont) |
| Beágyazott alprogram egy csomagtagban | 2 | **támogatott** (3. pont) |
| Headstart hibaverem (`qms$forms_errors.push …`) argumentummal | 6 | nyitott kérdés (lásd lent) |
| Indítási végpont nem generálható (5 formnál) | – (a riport nem mutatta) | **a felmérés most megmutatja** (4. pont) |

Az egyedüli okként tiltott végpontok várhatóan megnyílnak, kivéve, ha a javítás után egy eddig takart
második ok kerül elő. Ezt a következő felmérés mutatja meg.

## 1. Képernyőpont: képernyőlépés a gomb kódjának közepén

Eddig az `EXECUTE_QUERY`, `CLEAR_BLOCK` és társaik csak a kód utolsó lépéseként működtek. Ha utána még kód
jött (például a lekérdezett rekord mezőjét olvasta), a gomb kézi feladat maradt. A 4.14 ezt a `COMMIT_FORM`-ra
már megoldotta (mentési pont). Most ugyanez a mechanizmus a képernyőlépésekre is működik:

1. A kérés a lépésnél megáll, és `FRM_RESUME` utasítással tér vissza.
2. A képernyő végrehajtja az addigi utasításokat és a lépést. A lekérdezésnél megvárja, amíg a sorok
   megérkeznek.
3. A képernyő a gombot `FRM.RESUME = pont` paraméterrel hívja újra. A pont előtti utasítások kimaradnak, a
   kód a lépés után, **a képernyő új értékeivel** folytatódik, ahogy a Formsban.

```
GO_BLOCK('B');                 IF frm_resume NOT IN (1) THEN frm_cmd('GO_BLOCK', 'B'); END IF;
EXECUTE_QUERY;         ->      frm_screen_point(1, 'EXECUTE_QUERY');
:CTRL.X := :B.NEV;             nv_… := nv_…;
```

- **Lépések:** `EXECUTE_QUERY`, `CLEAR_BLOCK`, `CLEAR_RECORD`, `CREATE_RECORD`, `CLEAR_FORM`. Egy gombban lehet
  mentési pont és képernyőpont is; a számozásuk közös.
- **Eltérés a Formstól:** a pont előtti adatbázis-módosítások a ponton véglegesednek (a Formsban a következő
  mentéskor). A ServiceImpl metódusának kommentje jelzi.
- **Kézi feladat marad**, megnevezett okkal:
  - ha a pont ciklusban, CASE utasításban vagy kivételkezelőben áll;
  - ha a kód GOTO-t tartalmaz;
  - ha egy helyi változót a pont előtt állít és utána olvas;
  - ha egy helyi csomagnak változói vannak (a folytatás új kérésben indul);
  - `DELETE_RECORD`, mert nem biztos, hova kerül utána a kurzor;
  - `CALL_FORM` / `NEW_FORM`, mert elnavigál;
  - ha a lépés egy helyi eljárás belsejében áll, és az eljárásban is kód követi.
- **Képernyő:** a `frm-forms-screen.ts` **2-es változata** kell. A generált komponens `executeQuery(block,
  done)` horga a sorok megjelenítése után jelez, a futtató ezután folytatja a gombot.

## 2. Helyi csomag inicializáló résszel

A csomagtörzs végén álló `BEGIN … END` rész (például paraméter beolvasása egy csomagváltozóba) eddig
kézi feladat volt. Most a blokk elején, beágyazott blokkként fut, a saját kivételkezelőjével együtt. Ha egy
csomag inicializálása egy másik csomagra épül, a hívott csomagé fut előbb. A csomagváltozók továbbra is
kérésenként indulnak újra, ezért az inicializálás is kérésenként fut (a Formsban munkamenetenként egyszer).

Ha az inicializáló rész adatot vagy képernyőt módosít (`INSERT`, `UPDATE`, `DELETE`, `MERGE`, `COMMIT`,
`GO_BLOCK`, `SET_ITEM_PROPERTY` …), kézi feladat marad. Ilyenkor ugyanis kérésenként futna, nem
munkamenetenként egyszer.

## 3. Beágyazott alprogram egy csomagtagban

Egy csomag eljárása saját belső eljárásokat és függvényeket deklarálhat. Ezek eddig elutasították az egész
csomagot, most változatlanul beágyazódnak.

## 4. Felmérés: ami eddig rejtve maradt

- **Indítási végpont:** ha a PRE-FORM / WHEN-NEW-FORM-INSTANCE kódjából nem készülhet indítási végpont, eddig
  végpont sem keletkezett, így a riportban sem látszott. Most tiltott „gomb / indítás” végpont, `STARTUP` kóddal
  és az okkal. A 6 formos felmérésben 5 formnál ez volt a helyzet.
- **„Nem támogatott token” okok:** a riport eddig csak a token pozícióját mutatta, az adatbázisos átfuttatás
  okát (a valódi akadályt) levágta. Most ez is látszik.
- **Billentyű-triggerek kettébontva:**
  - a webes képernyőn is értelmes billentyűk: KEY-NEXT-ITEM, KEY-Fn, KEY-LISTVAL, KEY-CLRBLK …;
  - a Forms-felület billentyűi: KEY-HELP, KEY-ENTQRY, KEY-EXIT, KEY-CLRFRM, KEY-OTHERS … Ezeknél többnyire
    nincs teendő, a súgót és a kilépést a host alkalmazás adja.

  A felmérésben a 83 „egyéb” billentyű-trigger többsége ilyen volt (KEY-HELP 39, KEY-ENTQRY 11, KEY-EXIT 8 …).
- **Új eltéréssorok:**
  - a nem futó mezőesemények (vezérlőblokk WHEN-VALIDATE-ITEM / POST-CHANGE, WHEN-*-CHANGED);
  - a nem futó képernyőesemények (WHEN-NEW-BLOCK/RECORD/ITEM-INSTANCE, WHEN-WINDOW-*, WHEN-CUSTOM-ITEM-EVENT …).

## 5. Átnevezés: „niva” helyett „frm”

A „niva” elnevezés mindenhonnan kikerült (kód, generált kód, környezeti változók, webes felület,
dokumentáció). Ahol eddig `niva`, `Niva` vagy `NIVA` állt, ott most `frm`, `Frm`, illetve `FRM` áll:

| Régi | Új |
|---|---|
| `python -m niva_forms …`, `python -m niva_forms.web` | `python -m frm_forms …`, `python -m frm_forms.web` |
| `niva-forms-screen.ts`, `NivaFormsScreen`, `NIVA_FORMS_SCREEN_VERSION` | `frm-forms-screen.ts`, `FrmFormsScreen`, `FRM_FORMS_SCREEN_VERSION` |
| PL/SQL-segédek és utasítások: `niva_cmd`, `niva_msg` …, `NIVA.RESUME`, `NIVA_COMMIT` … | `frm_cmd`, `frm_msg` …, `FRM.RESUME`, `FRM_COMMIT` … |
| Környezeti változók: `NIVA_DB_PASSWORD`, `NIVA_JAVA_IMPORT_MAP`, `NIVA_CORS_ORIGINS`, `NIVA_BACKEND_LIVE`, `NIVA_OLLAMA_MODEL` … | `FRM_DB_PASSWORD`, `FRM_JAVA_IMPORT_MAP`, `FRM_CORS_ORIGINS`, `FRM_BACKEND_LIVE`, `FRM_OLLAMA_MODEL` … |
| A webes kérések fejléce: `X-Niva-Client` | `X-Frm-Client` |
| `.niva-ai-cache`; a böngészőben tárolt `niva-…` beállítások; a `niva.forms.globals` (:GLOBAL értékek) | `.frm-ai-cache`, `frm-…`, `frm.forms.globals` |
| Az Ollama-modell alapértelmezett neve: `niva-model` | `frm-model` |

A generált kötött változók `nv_<hash>` neve nem a „niva” elnevezés része, ezért nem változott.

A `CommonMigrateTools.java` **VERSION 5**-re nőtt, mert a `FormsPlsql` segédeljárásainak neve változott. Az új
generált kód a régi `CommonMigrateTools`-szal nem fut, és fordítva sem.

## Nyitott kérdés: Headstart hibaverem

A `qms$forms_errors.push(qms$forms_errors.msggettext(37, '…'), …)` és a `qms$forms_errors.raise_failure`
hívásokat a generátor keretrendszer-hívásnak tekinti. Mivel argumentuma van, nem hagyhatja el, ezért az egész
trigger kézi feladat (6 egyedüli okként tiltott végpont). Átültetésük a Headstart könyvtár (`qmslib`)
pontos működésétől függ: mit ad vissza a `msggettext`, és mit csinál a `push`. Ehhez a könyvtár forrása
(`.pld`) vagy a működés leírása kell. Ezek nélkül a generátor nem találgat.

## Átállás

1. A régi `niva-forms-screen.ts` helyére tedd az új `frm-forms-screen.ts` fájlt
   (`FRM_FORMS_SCREEN_VERSION = '2'`).
2. Cseréld a `CommonMigrateTools.java` fájlt (VERSION 5).
3. Generáld újra a modulokat (`--regenerate`). Minden generált fájl változik (`frm_…` nevek,
   `extends FrmFormsScreen`, az `executeQuery` új `done` paramétere). Az új változat az
   `analysis/backend-regeneration/` mappában van, ezt kell összefésülni.
4. Szkriptekben, CI-ban és a környezeti változókban cseréld a neveket (`python -m frm_forms`, `FRM_…`). A
   migrátor böngészőben tárolt beállításait (API-cím, opciók) egyszer újra meg kell adni. Ha saját Ollama-modellt
   `niva-model` néven hoztál létre, add meg a nevét a felületen, vagy nevezd át `frm-model`-re.
5. Futtasd újra a felmérést. Most az indítási végpontok okai és az eddig takart okok is látszanak.

## Ellenőrzés

- Teljes tesztkészlet: 545 teszt. A 101 hibás teszt ugyanaz, mint a `main` ágon (a repóból hiányzó
  mintabemeneteket keresik), új hibás teszt nincs.
- Új tesztek:
  - `test_screen_points`: a backend-átalakítás, egy replika-változat működő végpontjai és lefordított Javája,
    valamint egy Node-os képernyőszimuláció, amely megvárja a lekérdezést, és csak utána folytat;
  - `test_local_packages`: inicializáló rész, beágyazott alprogram, elutasítások;
  - `test_survey`: indítási végpont, token-okok, az új eltéréssorok.
- A generált képernyők és a futtató szigorú `tsc` ellenőrzése (`FRM_TSC`) a 2-es futtatóval is hibátlan.
