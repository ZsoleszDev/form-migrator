# Ellenőrzés — 4.34

## A jelentett hiba oka

`Nem érkezett érvényes Java-javaslat: Hiányzó adat esetén nincs részleges repository/DTO.; Hiányzó adat esetén nincs részleges repository/DTO.`

- A modell `status: needs_context` választ adott, és közben repository-metódust is írt.
  A 4.33.3 validátora ezt elutasította, és a teljes választ eldobta — a hiányzó elemek listáját is.
- A választ sehol nem mentette, ezért a felületen nem látszott sem a prompt, sem a modell válasza.
- Az újrapróbálás csak egy új felhasználói üzenetet küldött („Az előző válasz elutasítva…”) a modell előző
  válasza nélkül; temperature 0 mellett a modell ugyanazt adta, ezért szerepel kétszer ugyanaz a hiba.
- A kötött JSON-séma miatt a Java-kód JSON-stringben, escape-elve készült, kötelező tesztesetlistával.

## Mit küld most a modellnek

Ugyanarra a két mintavégpontra mérve (system + user, byte):

| Végpont | 4.33.3 | 4.34 | JSON-séma (format) |
| --- | --- | --- | --- |
| kézi trigger, tábla nélkül (`onCgnvW011PbLekerdezes`) | 6388 | 3793 | 1255 → nincs |
| JPA-táblás trigger (`onCtrlRun`, APP.ACCOUNT) | 8136 | 5208 | 1255 → nincs |

A különbség a szabályszövegből és az entity teljes Java-forrásának elhagyásából jön; a metódus szó szerint
megy. Valódi triggernél a metódus a kérés nagyobb része; széles táblánál az entity leírása 15 oszlop fölött
csak a PK-t és a hivatkozott oszlopokat tartalmazza. A kimeneti oldalon nincs JSON-escape, kötelező teszteset
és az eredeti komment visszamásolása, ami CPU-n a lassabb, kimeneti tokeneket csökkenti.

## Automatikus tesztek

Futtatás: `PYTHONPATH=tests:. python3 -m unittest …` (Python 3.13, OpenJDK 21 `--release 11`).

| Modul | Eredmény |
| --- | --- |
| `test_assistant_reply` (új, 18 eset: válaszformák, javítási üzenetek, importok, átnevezés, escape) | OK |
| `test_backend_assistant` (22 régi + 5 új: Markdown-válasz, AI-napló, elutasított válaszok és javítás, `needs_context`-vázlat, előnézet = elküldött kérés, HTTP-hiba naplózása) | OK |
| `test_jpa_assistant` (7 régi + 2 új: a jelentett eset, teljes osztályokból álló Markdown-válasz Java 11 fordítással és futtatással) | OK |
| `test_assistant_web_manager` (új, 3 eset: futásrekord, napló, előnézet, hibaüzenet linkkel — FastAPI nélkül) | OK |
| `test_backend_assistant_web` (FastAPI-val; 2 új eset az új útvonalakra) | itt kihagyva: nincs FastAPI |
| `test_ollama_connection` | itt nem fut: FastAPI-t importál (a 4.33.3-on is így) |

A JPA-tesztek a beillesztett service-t, repositoryt, entityt és DTO-t Java 11-re fordítják és futtatják
(adat, NULL és hiányzó sor ág), a Markdown-válasz saját `em` mezős, konstruktoros repository-osztályával is.

### Teljes tesztkészlet

`python3 -m unittest discover -s tests` ugyanebben a környezetben, a változatlan 4.33.3-as forráson és a 4.34-en:

| | Futott | Hiba (F) | Hiba (E) | Kihagyva |
| --- | --- | --- | --- | --- |
| 4.33.3 | 737 | 38 | 47 | 54 |
| 4.34 | 766 | 38 | 47 | 56 |

A két futás hibás tesztjeinek listája **azonos** (85 teszt). Mind már a 4.33.3-ban is így viselkedett: a csomagból
hiányzó `examples/` mintafájlok (pl. `backend-query_fmb.xml`, `package-pair_fmb.xml`), az ezekre épülő
generálás, valamint a FastAPI hiánya. A +29 teszt az új eset, a +2 kihagyott az új FastAPI-útvonalteszt.

## Felület

- `frm_forms/data/ai-log.html` (`/ai-log`): valódi Chromiumban, a valódi `AssistantManager` adataival
  (egy sikeres és egy kétszer elutasított, megszakadt futás) világos, sötét és 390 px széles nézetben:
  nincs konzolhiba, nincs vízszintes görgetés, a modell szövege csak szövegként jelenik meg (`pre` elemben
  nincs HTML-elem), működik a futáslista, a részletek, a végpontszűrő és a prompt-előnézet.
- `angular-frontend/migrator-assistant.ts`: TypeScript-ellenőrzés a projekt szigorú beállításaival
  (Angular/rxjs/optimus csonkokkal; ugyanez az eredeti fájlon is hibátlan), és a sablon szerkezeti
  ellenőrzése (blokkok, címkék, hivatkozott tagok és interfészmezők).
  **Nem futott Angular-fordítás**: ebben a környezetben nem érhető el az npm-regiszter. A kész `web-dist`
  ezért még a 4.33.3-as forrásból van; `npm ci` + `npm run build:ui` után jelenik meg az új panel.
- `tests/unified_assistant_browser.cjs` az új promptformára és panelre frissítve; itt nem futott (FastAPI és
  Angular-build kell hozzá).

## Határ

Nem futott valódi Qwen-inferencia, Oracle/Hibernate vagy teljes céges build. A modellválaszokat a tesztek
rögzített szövegekkel helyettesítik (a kis modellek tipikus eltéréseivel). Az első valódi futás után az
AI-napló pontosan megmutatja, mit kapott és mit adott a modell.
