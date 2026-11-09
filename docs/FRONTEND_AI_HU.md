# Frontend AI-átírás (opcionális)

A migrátor által generált képernyő (`frontend/<képernyő>/<képernyő>.component.ts`) működő, de egyszerű kód:
standalone komponens, `inject()`, sima mezők állapotnak. Ha kéred, a helyi modell modern Angular-kódra írja át,
**ugyanazzal a működéssel**:

- standalone komponens marad, az `imports` a sablon minden komponensével;
- az állapot (sorok, kiválasztás, betöltött rekord, jelzők) `signal()`, a származtatott érték `computed()`, a sablon
  `név()`-vel olvassa, a kód `.set()` / `.update()`-tel írja; `@Input()` → `input()`, `@Output()` → `output()`;
- olvasható nevek, pontosabb típusok, kis privát segédmetódus az ismétlődő kód helyett;
- rövid magyar kommentek: mit csinál a metódus a felhasználónak, melyik Forms-gombból vagy -triggerből jön.

Ami nem változhat (ezt a prompt kimondja, a migrátor ellenőrzi): minden HTTP-hívás (URL, ige, törzs), minden
metódusnév (a sablon, a FormBlock-struktúrák és más kód hívja), a lépések sorrendje, az üzenetek, a selector és az
osztály neve.

## Bekapcsolás

Az átírás **nem kötelező, és sosem fut magától**. Bekapcsolni a Java asszisztens fülön a **Frontend AI-átírás
(opcionális)** panelen, vagy újrafordítás nélkül a `http://localhost:8000/ai-log` oldal **Frontend AI-átírás**
részén lehet (**Engedélyezve** kapcsoló; a közös AI-beállításokban `frontend_ai`). Ugyanazt az Ollama-kapcsolatot
és ugyanazt az AI-sort használja, mint a Java asszisztens: egyszerre egy kérés fut.

## Belefér-e a kontextusba?

A panel képernyőnként megmutatja, mekkora a fájl, mennyit kap belőle az AI, és mekkora kérés és válasz kell hozzá.

- **Teljes fájl átírása AI-val** — csak akkor aktív (és kiemelt), ha a teljes kérés és a teljes válasz is belefér
  a beállított kontextusba (`num_ctx`) és kimeneti keretbe (`num_predict`). Egy félbevágott válasz semmit sem ér,
  ezért ilyenkor a gomb nem villan fel; mellette ott van, mekkora beállítás kellene hozzá.
- **Részenként (N kérés)** — a „másik megoldás”, ha a teljes fájl nem fér bele:
  1. először a **fej** megy (importok, dekorátor a sablonnal, mezők, konstruktor): itt lesz az állapotból signal;
  2. utána a **metódusok csoportokban**, amennyi egy kérésbe belefér. Minden kérés megkapja az új fej vázlatát és a
     signalok listáját (`tetelRows (signal)`, `migrationParameters (input)`), így minden rész ugyanúgy olvassa és
     írja az állapotot;
  3. a migrátor eredeti sorrendben összerakja a részeket, és az egészet ugyanúgy ellenőrzi, mint a teljes fájlt.
- Ha egy metódus egymagában sem fér bele, a panel megnevezi; ilyenkor a kontextust kell növelni.

A nagy állandó értékek (a FormBlock-struktúrák, nagy oszloplisták) **nem mennek el az AI-hoz**: a helyükön
`__FRM_KEEP_1__` helyőrző áll, amit a modell változatlanul hagy, a migrátor pedig visszateszi az eredeti értéket.
Ezek konfigurációk, nem átírandó kód, és egy nagy képernyőnek ezek a legnagyobb részei.

## Ellenőrzés, javaslat, beillesztés

Minden válasz után a migrátor ellenőrzi:

- zárójelek, osztálynév, selector, `standalone: true`;
- minden eredeti metódus megvan-e;
- minden helyőrző pontosan egyszer szerepel-e;
- signalnak nem ad-e értéket `=`-vel (`.set()` kell);
- a képernyőérték-jelölések (`frm-input`) megmaradtak-e: ha nem, figyelmeztet;
- az `@angular/core` importja pontosan a használt neveket tartalmazza-e: ezt helyben javítja;
- ha a gépen van TypeScript (a migrátor `node_modules`-a vagy a `tsc` csomagja, `FRM_TYPESCRIPT`), a szintaxist is.

Hibánál egy javítókérés megy (a beállított próbálkozásszámig). Ha az utolsó válasz is hibás, vagy egy rész nem
sikerült (annak a résznek az eredeti metódusai maradnak), az eredmény **hibás vázlat**: a hibákkal együtt
megtekinthető, de nem illeszthető be.

A javaslat a diffet és a teljes új fájlt mutatja. **Beillesztés a képernyőbe** csak átnézés után, és csak akkor,
ha a fájl azóta nem változott; az eredeti fájl mentése az `analysis/frontend-assistant/originals/` mappába kerül,
a modul ZIP frissül. Minden kérés és válasz az AI-naplóban látható, részenként (`[2/3. rész: ...]`).

**Fontos:** az átírás után a képernyőértékek bekötése (Haladó: képernyőértékek bekötése) a komponenst kézzel
módosítottnak látja, és már nem ír bele. Ezért előbb kösd be a bemeneteket, utána írasd át a képernyőt.

## API

- `GET /api/jobs/{job}/frontend-assistant?num_ctx=&num_predict=` — a képernyők mérete és a két mód illeszkedése;
- `POST /api/jobs/{job}/frontend-assistant` — `{task_id, mode: "whole" | "parts"}` (a nem illő módot 409-cel elutasítja);
- `POST /api/jobs/{job}/frontend-assistant/prompt` — az első kérés előnézete, AI-hívás nélkül;
- `GET /api/jobs/{job}/frontend-assistant/proposals/{id}` és `POST .../apply` (`{"reviewed": true}`).

A módosító kérésekhez `X-Frm-Client: local-ui` fejléc kell, mint a migrátor többi végpontjához.
