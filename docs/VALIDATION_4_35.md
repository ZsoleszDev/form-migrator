# Ellenőrzés — 4.35

## Mi változott

Részletesen: [MIGRATION_NOTES.md](../MIGRATION_NOTES.md) 4.35. Röviden: a szerveroldali triggereket a
determinisztikus generátor nem fordítja le (`backend_trigger_mode: "ai"`), csak a metódus keretét és a
kikommentezett eredeti Forms-kódot generálja; a törzset a Java asszisztens írja. A backend a migrálás és a
táblaszerkezet mentése után magától futtatja az AI-sort (egyszerre egy kérés), a jó javaslatot beilleszti,
a futás közben élő állapotot ad (idő, fázis, tokenek, sebesség, a válasz vége), és leállítható.

## Automatikus tesztek

Futtatás: `PYTHONPATH=tests:. python3 -m unittest …` (Python 3.13, OpenJDK 21 `--release 11`, TypeScript `tsc`).

| Modul | Eredmény |
| --- | --- |
| `test_ai_triggers` (új, 20 eset) | OK |
| — keretek: minden szerveroldali trigger metódusa a teljes eredeti kóddal, `TODO(AI)` és 501; nincs lefordított SQL/`DbCalls` | OK |
| — szerződések: `ActionResult` / `PageResult<OrdersRow>` + `QueryActionRequest` / `private void trigger…(OrdersRow, RuleContext)`; a CRUD a Forms-pontokon hívja a hookokat | OK |
| — terv, riport, képernyő: `runs: "ai"`, „AI-ra váró triggerek … \| 5”, `applyActionResult`, lekérdező gomb a sorokkal; a képernyő `tsc --strict` | OK |
| — asszisztensfeladatok: csak a kód által használt táblák kellenek (validálás: 0, POST-QUERY: APP_PARTNERS, NEXTVAL-os PRE-INSERT: APP_ORDERS) | OK |
| — hook: prompt → Markdown-válasz → beillesztés (a region marad) → a modul Java 11-re fordul; a szekvenciás PRE-INSERT a saját táblája repositoryját kapja, JPA-val fordul | OK |
| — streamelt hívás: élő állapot (fázisok, tokenek, első token), leállítás néma prompt-feldolgozás közben, időkorlát, megszakadt válasz megőrzése, szerverhiba-sor | OK |
| — AI-sor: migrálás után automatikus indítás és beillesztés, kikapcsolt automatizmusok, sikertelen futás nem ismétlődik (csak kényszerítve), egyszerre egy futás + leállítás | OK |
| — régi mód (`plsql`) változatlanul fordít | OK |
| `test_assistant_web_manager` (a javaslatot olvasó esetek automatikus beillesztés nélkül; a `queued` állapot kivárása) | OK |
| `test_screen_component`, `test_commit_points` (a gomb válaszát `applyActionResult` dolgozza fel; `COMMIT_FORM` → `save()`) | OK |
| `test_backend_assistant` (az alapértelmezett kimeneti keret 4096) | OK |

A determinisztikus fordítás régi tesztjei (`test_forms_keys`, `test_plsql_and_states`, `test_company_backend`, …)
`FRM_BACKEND_TRIGGER_MODE=plsql` mellett futnak (`tests/java_support.py`), mert a régi motort ellenőrzik.

### Teljes tesztkészlet

`python3 -m unittest discover -s tests`, ugyanebben a környezetben:

| | Futott | Hiba (F) | Hiba (E) | Kihagyva |
| --- | --- | --- | --- | --- |
| 4.34.1 (változatlan forrás) | 768 | 38 | 55 | 56 |
| 4.35 | 788 | 38 | 55 | 56 |

A két futás hibás tesztjeinek listája **azonos** (93 teszt). Mind már a 4.34.1-ben is így viselkedett: a csomagból hiányzó `examples/`
mintafájlok (`HIBA: Létező .fmb vagy Forms2XML .xml bemeneti fájl kell.`) és az ezekre épülő generálás,
valamint a FastAPI hiánya ebben a környezetben. A +20 futott teszt a `test_ai_triggers` esetei.

## Végponttól végpontig, a valódi webes backenddel

A valódi `frm_forms.web` backend (FastAPI helyett egy Starlette-alapú helyi pótlással), egy streamelő
Ollama-utánzattal (6 mp „prompt-feldolgozás”, utána tokenenként 0,12 mp), egy rendelés-formmal
(adatbázisblokk POST-QUERY, PRE-INSERT, WHEN-VALIDATE-ITEM triggerrel; számoló és lekérdező gomb):

1. Feltöltés → migrálás kész → az AI-sor magától elindult a táblát nem igénylő `WHEN-VALIDATE-ITEM` hookkal;
   élő fázisok: prompt → írja a választ → kész (≈7 mp), automatikusan beillesztve. 4 feladat „táblára vár”.
2. `APP_ORDERS` CSV-import és mentés → 3 feladat azonnal sorba állt; `APP_PARTNERS` → a negyedik.
3. A sor egymás után futtatta őket. Az egyik javaslatot a közben mentett második tábla miatt nem lehetett
   beilleszteni („a séma … megváltozott”): a sor egyszer automatikusan újrafuttatta — ugyanaz a prompt volt,
   ezért gyorsítótárból jött — és beillesztette. Vége: 5/5 kész, 0 hibás, a modul ZIP frissült.
4. A küldött promptok 3,3–4,6 KB-osak (a hookoké a legkisebb).
5. Leállítás prompt-feldolgozás közben: a futás „Leállítva” lett, az Ollama-utánzat a kapcsolat bontását jelezte.

Az `/ai-log` oldal (munkasor, élő kártya, triggerállapot-rács, napló) valódi Chromiumban, 1366 és 390 px
szélesen: nincs vízszintes görgetés, nincs konzolhiba.

## Felület

- Az Angular-forrás (`angular-frontend/migrator-app.ts`, `migrator-assistant.ts`, új `migrator-ai-queue.ts`)
  `tsc`-vel típusellenőrzött (Angular/Optimus csonkokkal) és a sablonok kötései ellenőrizve. Ebben a
  környezetben az npm-regiszter nem érhető el, ezért a `web-dist` **nincs újrafordítva**:
  `npm ci` + `npm run build:ui` szükséges. Addig az `/ai-log` oldal mutat és vezérel mindent; az automatikus
  futás és beillesztés a backendben történik, a régi felülettel is.
- A generált képernyők `tsc --strict` ellenőrzése (Angular CLI tsconfig) a régi és az új mintákon OK.

## Ismert korlátok — mi kell még a 100%-hoz

- **Frontend-adatfolyam:** a mentésben a törlés (a `save()` az új és a módosított rekordot már küldi), a lista-blokkok kezdeti betöltése, a LOV-javaslatok
  és a `:GLOBAL` értékek tárolása ma kézi befejezést kér. Ezek determinisztikusan generálhatók (a blokk- és
  DTO-modell megvan), ez a következő legnagyobb lépés a működő modul felé.
- **KEY-COMMIT / KEY-* triggerek:** a mentés gombhoz kötése még kézi; AI-feladattá tehető ugyanígy (keret + kód).
- **Fordítási visszacsatolás:** a beillesztés után a modul `javac`-kal fordítható a céges csonkokkal; a hibaüzenet
  visszaküldhető az AI-nak egy javító körre.
- **Adatbázis-szótár:** a `dictionary-sql` + `dictionary-import` a táblák mellé a szekvenciák és tárolt eljárások
  szignatúráját is adja; minél teljesebb, annál kevesebb `MISSING:`.
- Az XML teljességéről: [XML_TELJESSEG_HU.md](XML_TELJESSEG_HU.md).
