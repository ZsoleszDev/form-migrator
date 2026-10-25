# FRM Migration Studio — helyi indítás, lépésről lépésre

A 4.2-es csomag a migrátort, egy Python HTTP API-t és egy magyar Angular kezelőfelületet tartalmaz. A felületen FMB/XML fájlt tölthetsz fel, elindíthatod a generálást, megtekintheted a riportot és a forrásokat, majd letöltheted a teljes modult vagy külön az Angular/Java részt. Az új, alapértelmezett Képernyőváz mód használata: [FRONTEND_SCREEN_HU.md](FRONTEND_SCREEN_HU.md).

**A generált kimenet továbbra is a főalkalmazásba illeszthető feature-komponens és Java package.** A most hozzáadott Angular alkalmazás a migrátor helyi kezelőfelülete, nem kerül bele a generált modulokba.

## 1. Csomagold ki

Nyiss terminált a `frm-forms-migrator` mappában. Itt legyen a `frm_forms`, `web-ui`, `web-dist`, `examples` mappa és a `requirements-web.txt` fájl.

Szükséges:

- Python **3.10 vagy újabb**; a csomag Python 3.12-vel lett ellenőrizve.
- A kész felület használatához böngésző. Node.js nem szükséges, ha a mellékelt buildet használod.
- Angular-fejlesztéshez Node.js **22.22.3+ a 22-es ágból**, **24.15+ a 24-es ágból**, vagy **26.x**, valamint npm. A projekt Angular 22.1.6-ot és TypeScript 6.0-t használ. A pontos verziókat a `package-lock.json` rögzíti. [Angular kompatibilitási táblázat](https://angular.dev/reference/versions).
- FMB feldolgozásához helyi, megfelelő verziójú **Oracle Forms2XML** környezet. Az XML mintához nem szükséges Oracle.

## 2. Telepítsd a Python függőségeket

**Windows / PowerShell:**

```powershell
cd C:\munka\frm-forms-migrator
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt
```

A `py -3.12` helyére a telepített, legalább 3.10-es Pythonod parancsa kerülhet, például `python`. Az alábbi parancsok nem igénylik a virtuális környezet aktiválását vagy az ExecutionPolicy átállítását.

**Linux / macOS:**

```bash
cd /sajat/mappa/frm-forms-migrator
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-web.txt
```

Az első függőségtelepítés csomagletöltést igényel. A működő alkalmazás ezután nem használ CDN-t, külső betűtípust, analitikát vagy felhős AI-t. Teljesen offline telepítéshez azonos célplatformon/Python-verzióval készíts wheelhouse-t: `python -m pip download -r requirements-web.txt -d wheelhouse`; a célgépen `python -m pip install --no-index --find-links wheelhouse -r requirements-web.txt`. A kész Angular build miatt ott npm sem szükséges.

## 3. Indítsd el a backendet

**Windows:**

```powershell
.\.venv\Scripts\python.exe -m frm_forms.web --port 8000
```

**Linux / macOS:**

```bash
.venv/bin/python -m frm_forms.web --port 8000
```

Hagyd nyitva ezt a terminált. Leállítás: **Ctrl+C**.

Nyisd meg: **http://localhost:8000**. Ez már a mellékelt, lefordított Angular kezelőfelület. Ezzel az egyetlen Python folyamattal használható a rendszer. Az első képernyőn add meg a **Migrátor teljes API URL** mezőt, például `http://localhost:8000/api`, majd mentsd a kapcsolatot. Nincs automatikusan kiválasztott API-cím.

Állapotellenőrzés: **http://localhost:8000/api/health**. A `status: "ok"` a backend működését jelzi. Az `exporter.status: "missing"` azt jelenti, hogy FMB-hez még be kell állítanod az Oracle eszközt; az XML feldolgozás ettől működik.

A programot a kicsomagolt projektből indítsd. A mellékelt build és a példák nem kerülnek bele a CLI opcionális `pip install .` telepítésébe. A teljes helyi Studio-hoz a ZIP mappáját használd.

## 4. Ha külön Angular fejlesztői szervert szeretnél a 4200-as porton

A Python backend maradjon futva. **Második terminálban**, a projekt gyökeréből:

```bash
npm ci
npm run start:ui
```

Nyisd meg: **http://localhost:4200**. A Beállítások oldalon te adod meg a teljes migrátor API-címet, például **http://localhost:8000/api**. A megadásáig nincs API-kérés. Globális Angular CLI telepítése nem kell; az npm script a csomag saját CLI-jét használja.

A backend CORS-beállítása már engedi ezeket az origineket:

```text
http://localhost:4201
http://127.0.0.1:4201
http://localhost:8000
http://127.0.0.1:8000
```

A backend `--port` módosítása az utolsó két címet is módosítja. A 4200-as címek mindig engedélyezettek. A frontend **Beállítások → Migrátor teljes API URL** mezőjében adhatod meg a címet; ezt a böngésző megőrzi. Az URL tartalmazza az `/api` részt is. A korábbi 1.x-es címbeállítást a 3.0 nem veszi át automatikusan.

A forrás módosítása után a kész felület újrafordítása:

```bash
npm run build:ui
``` 

Az eredmény a `web-dist/browser` mappába kerül. Ha a backend indulásakor még nem létezett ez a build, a fordítás után indítsd újra a Python szervert. A ZIP-ben eleve benne van a build. A fejlesztői szerver leállításához a második terminálban is Ctrl+C-t használj.

## 5. Első próba, AI és Oracle nélkül

1. Nyisd meg az **Új migráció** oldalt.
2. Kattints a **Példa betöltése** gombra. Ez a helyi `customer_fmb.xml` és `schema.json` szintetikus mintát választja ki.
3. Az AI-kapcsolót hagyd kikapcsolva; a példa ezt automatikusan kikapcsolja.
4. A Java/API nevet hagyd üresen: a Title alapján `ugyfelek` készül. A frontend az `environment.baseUrl` címet használja; WBS URL-t nem kell kitölteni, a DPS-cím a Java bekötéséhez opcionális. A frontend a FormBlock képernyőt építi fel, HTTP-műveletet nem indít.
5. Kattints a **Generálás indítása** gombra.
6. A **Feladatok** oldalon követheted a tényleges feldolgozási szakaszt.
7. A kész feladatnál nyisd meg az összesítést, a forrásfájlokat és a naplót.
8. Töltsd le a **teljes ZIP-et**, vagy külön az **Angular** és **Java** csomagot.

A példában 1 blokk, 8 mező/gomb és 5 triggert ismer fel a szabálymotor, **0 AI-hívással**. A generált kód nem indul el automatikusan és nem kapcsolódik adatbázishoz. Beépítéséhez a generált `INTEGRATION.md` és a [részletes migrációs útmutató](README_HU.md) tartalmazza a lépéseket.

A formblokkokat, p-table táblázatot, tabokat, 1/0 checkboxot és LOV-deklarációt az `examples/ui-features_fmb.xml` mintával próbálhatod ki. Az örökléses checkboxhoz az `examples/inheritance/checkbox_fmb.xml` mellé töltsd fel a két ugyanott található `_olb.xml` fájlt. Kész kimenetek: `sample-output/ui-features/` és `sample-output/inherited-checkbox/`.

## Webes felület: egy mód, tömeges futtatás, fő képernyő, előnézet

- **Egy generálási mód, csak az `.fmb`:** képernyőváz, Java backend és elemzés egy lépésben.
  Nincs módválasztó, kapcsoló és kiegészítő-feltöltés. Megadható a forrásfájl (vagy több), egy
  opcionális modulnév, és a céges adatok (Java-csomag, címek, FormBlock-szerződés, importok),
  amelyeket eddig is meg kellett adni.
- **Tömeges futtatás:** több `.fmb`/`.xml` jelölhető ki vagy húzható be egyszerre.
  - Formonként külön feladat indul, közös kiegészítőkkel (OLB, séma, szabályok).
  - Ha a feladatsor megtelt, a felület kivárja a szabad helyet; a korlát a `FRM_MAX_PENDING` változóval állítható, alapértéke 50.
  - A Feladatok fülön a tömeges futtatás összesítője mutatja, mely okok tiltják a legtöbb végpontot.
  - Egyetlen ZIP-ben letölthető minden modul és a `PORTFOLIO_HU.md`.
- **Több képernyős form:** nem kell előre beírni a főablak nevét.
  - Ha a form több egyenrangú ablakból áll, a feladat „Döntésre vár” állapotba kerül, és a felület lenyíló listában megkérdezi, melyik legyen a fő képernyő.
  - A választás után ugyanaz a feladat folytatódik.
- **Azonnal éles backend:** alapból bekapcsolva. A generált végpontok azonnal működnek, a
  triggerek PL/SQL-je az adatbázisban fut. `FRM_BACKEND_LIVE=false` esetén az új feladatok
  ellenőrzésig HTTP 501-et adnak.
- **Előnézet:** a feladat Előnézet fülén a generált képernyők láthatók; több képernyőnél mindegyik megnyitható.
- **API:**
  - `POST /api/jobs/{id}/answer`;
  - `GET /api/jobs/{id}/preview`;
  - `GET /api/batches/{batch}` és `…/download`;
  - a `POST /api/jobs` új, opcionális `batch` mezője.

A lefordított felület (`web-dist`) az `angular-frontend/migrator-app.ts` és
`angular-frontend/migrator-assistant.ts` komponensekből épül. Módosítás után a
projekt gyökerében futtasd: `npm run build:ui`. Az asszisztens a fő felület saját füle.
Részletes munkamenet: [MIGRALASI_MUNKAMENET_HU.md](MIGRALASI_MUNKAMENET_HU.md).

## 6. Saját FMB feltöltése

1. Telepítsd vagy használd a Forms-verziótokhoz tartozó Oracle Forms2XML környezetet azon a gépen, ahol a Python backend fut.
2. A `frmf2xml` / `frmf2xml.bat` legyen elérhető a backend termináljának PATH-jában, vagy állíts be `export_command` argumentumlistát egy szerveroldali JSON configban.
3. A szükséges Oracle környezeti változókat, CLASSPATH-ot, natív könyvtárakat és `FORMS_PATH`-ot a backend indítása előtt állítsd be. A feltöltött FMB a helyi feladatmappába kerül; a kapcsolódó PLL/OLB és más függőségeknek az Oracle számára elérhető helyen kell lenniük.
4. Indítsd a backendet a configgal: `python -m frm_forms.web --port 8000 --config config.json` — a saját `.venv` Python parancsoddal.
5. A felületen húzd be vagy válaszd ki a `.fmb` fájlt.
6. Ha rendelkezésre áll, töltsd fel a DB-mapping `schema.json` és a felülvizsgált szabályok `rules.json` fájlját is.
7. A cím alapú fájlnevet a generátor képezi. Állítsd be szükség esetén a céges FormBlock selectorait/típusait, majd indítsd a generálást. A teljes profil leírása: [COMPANY_PROFILE_HU.md](COMPANY_PROFILE_HU.md).

Config-példa Windowshoz; az útvonalat a saját telepítésedre módosítsd:

```json
{
  "java_package": "hu.ceg.foalkalmazas.modules",
  "api_prefix": "/api/forms",
  "angular_selector_prefix": "app",
  "export_command": [
    "C:\\Oracle\\Middleware\\Oracle_Home\\bin\\frmf2xml.bat",
    "USE_PROPERTY_IDS=NO",
    "DUMP=ALL",
    "OVERWRITE=YES",
    "{input}"
  ]
}
```

Az `examples/config-oracle-java.json` közvetlen Java-exporter hívást is bemutat. Az exporter beállítása megbízható helyi configfájlban történik; a feltöltési API nem fogad el futtatható parancsot. Részletek és hivatalos Oracle-forrás: [README_HU.md, 4. pont](README_HU.md#4-készíts-xml-t-az-fmb-ből).

A Forms2XML az abszolút útvonallal kapott FMB mellé írja az `<név>_fmb.xml`-t, bizonyos wrapperek pedig a munkakönyvtárba. A migrátor mindkét helyen keresi, és csak az aktuális futás által írt fájlt fogadja el.

**Oracle telepítés nélkül egy bináris FMB-t ez a Python program sem tud kibontani.** Ha csak a Forms szerveren van exporter, exportáljatok ott XML-t, és az XML-t töltsd fel. A hiányzó Oracle exporter a felületen és a hibás feladat naplójában is megjelenik.

## 7. AI-kapcsolat

A webfelületen az AI-segítség automatikusan aktív. A közös beállításoknál add meg
az Ollama alapcímét és a pontos modellnevet, majd kattints az **AI-kapcsolat mentése**
gombra. A cím porttal együtt kell, például `http://192.168.1.50:11434`.
A `/api/chat` útvonalat a migrátor teszi hozzá; egy beillesztett teljes Ollama-
végpontot alapcímre alakít. Mentéskor a PC-n futó backend ellenőrzi a szervert
és a modelllistát, inferencia nélkül.

A generáláskor rövid elemzési javaslatok készülhetnek. A megmaradt összetett
triggerek tényleges Java-átírását a táblaszerkezetek megadása után a **Java asszisztens**
fülön indítod. A teljes kiválasztott service-metódus és az entityk mennek az AI-nak.

A régi `off` / `assist` / `cached` módok a CLI és a közvetlen API kompatibilitása
miatt megmaradtak. A beépített Angular-felület mindig `assist` módot küld, a
korábban elmentett kikapcsolt módot nem veszi át. Környezeti alapértékek:

**Windows / PowerShell:**

```powershell
$env:FRM_OLLAMA_URL = "http://xx:11434"
$env:FRM_OLLAMA_MODEL = "frm-model"
.\.venv\Scripts\python.exe -m frm_forms.web --port 8000
```

**Linux / macOS:**

```bash
export FRM_OLLAMA_URL="http://xx:11434"
export FRM_OLLAMA_MODEL="frm-model"
.venv/bin/python -m frm_forms.web --port 8000
```

Az `xx` a saját belső hostneved legyen. Ha az Ollama ugyanazon a gépen fut, használj `http://localhost:11434` címet. Ezután a teljes rendszer az adott gépen működik. Az alapértékek az általad megadott `http://xx:11434` és `frm-model`; a program `.env` fájlt nem olvas automatikusan. A webes feladatnál az általad megadott Ollama URL és modell felülírja a szerver környezeti alapértékét. A teljes Ollama-kapcsolat továbbra is a Python backendből történik. Az Angular közvetlenül nem hívja az Ollamát, így ahhoz nem kell böngészős CORS-t állítanod.

Gyenge gépre a felület alapbeállításai: maximum 3 hívás/feladat, 2048-as kontextus, 256 kimeneti token, 120 másodperc/kérés, maximum 1200 karakteres trigger és 3200 bájtos teljes prompt. Kezdhetsz **1 hívással**. Egyszerre egy migráció és azon belül egy AI-kérés fut. Az AI beállításainál a kontextus, válaszhossz, timeout, forráshossz, promptméret, think mód és cache-verzió is módosítható. Nincs automatikus újrapróbálási ciklus; a hibás kérés is fogyasztja a keretet.

Az AI itt **review-javaslatot** készít, nem ellenőrizetlen végrehajtható kódot. Az ismeretlen üzleti logikát továbbra is át kell ültetni; a riportban és a generált kódban a hozzá tartozó tiltás megmarad. A frontend/backend alapjait a szabályalapú generátor akkor is elkészíti, ha az Ollama nem érhető el. A belső Ollama végpontot ebben a fejlesztési környezetben nem hívtuk meg.

## 8. Feladatok, riportok és helyi fájlok kezelése

- A generálások sorba állnak; egyszerre **1** fut, legfeljebb **10 aktív/várakozó** feladat lehet.
- A felületen megszakítható a futó vagy várakozó feladat. Befejezett/megszakított/hibás feladat újrafuttatható; ez új azonosítót és új kimenetet hoz létre.
- A törlés a feladat összes helyi bemenetét, forrását és ZIP-jét végleg eltávolítja; a felület megerősítést kér. Futó feladat előbb megszakítandó.
- A forráslista kereshető; a megtekintő a fájl első 1 MiB-ját, a napló az utolsó 32 KiB-ot mutatja. A ZIP a teljes fájlokat tartalmazza.
- Az **Ellenőrzendő** állapot kész csomagot jelent, amelyhez még kézi átültetés vagy mapping-ellenőrzés szükséges. A **Szigorú mód** bekapcsolásakor a generátor ezt 3-as kilépési kóddal is jelzi; a csomag ettől még letölthető.
- Az **Elkészült** állapot a generátor által ellenőrzött részhalmazra vonatkozik; nem bizonyítja a teljes Forms-üzleti működés egyenértékűségét.
- A **Beállítások** oldalon törölhető a közös AI-cache, ha nincs futó/várakozó feladat.

Alapértelmezett adatmappa: `local-data/`. A feladatok a `local-data/jobs/<azonosító>/`, a cache a `local-data/cache/` alatt találhatók. Az alkalmazás megőrzi a kész feladatokat újraindítás után. A megszakadt futásokat `interrupted` állapotúként tölti vissza; nem indítja őket újra automatikusan.

Egyedi adatmappa: `python -m frm_forms.web --data-dir D:/frm-data`. Az `--data-dir`, `--config` és `--port` együtt is használható. Ugyanazt az adatmappát egyszerre egy backend használhatja. A normál Ctrl+C leállítás a worker leállítását is elindítja. Kényszerített operációs rendszeres kilövésnél szükség lehet a hátramaradt Oracle/worker folyamat külön leállítására. A kliens megszakítása az Ollama szerveroldali inferenciájának azonnali leállását nem garantálja.

| Környezeti változó | Alapérték / szerep |
|---|---|
| `FRM_API_PORT` | `8000`; a `--port` felülírja |
| `FRM_WORK_DIR` | A projekt `local-data` mappája; a `--data-dir` felülírja |
| `FRM_SERVER_CONFIG` | Opcionális helyi JSON config; a `--config` felülírja |
| `FRM_JOB_TIMEOUT` | `1800` másodperc, teljes feladat időkorlátja |
| `FRM_OLLAMA_URL` | `http://xx:11434` |
| `FRM_OLLAMA_MODEL` | `frm-model` |
| `FRM_PROJECT_ROOTS` | Üres; vesszővel elválasztott mappák: a telepítés, a mappaböngésző és a mappaválasztó ablak csak ezek alá enged |
| `FRM_FOLDER_DIALOG` | `true`; a „Tallózás…” a gép saját mappaválasztó ablakát nyitja meg. `false`: mindig a beépített mappaböngésző |

Feltöltési limit: 32 MiB FMB/XML, külön-külön 1 MiB schema/rules JSON. A kapcsolódó limitet a felület is kijelzi. A JSON-formátumok és a generátor részletes támogatási határai: [README_HU.md](README_HU.md), [SEMANTICS_HU.md](docs/SEMANTICS_HU.md).

## 9. API és CORS

A Python szerver kizárólag `127.0.0.1` címen figyel. A CORS pontos originlistát használ, nem `*`-ot. A módosító kérésekhez `X-Frm-Client: local-ui` fejléc kell; az Angular service ezt automatikusan hozzáadja. A `Content-Disposition` letöltési fejléc olvasható a böngészőből. A CORS nem felhasználóazonosítás: ez egy helyi, egyfelhasználós eszköz. [FastAPI CORS dokumentáció](https://fastapi.tiangolo.com/tutorial/cors/).

| Metódus és útvonal | Feladat |
|---|---|
| `GET /api/health`, `GET /api/defaults` | Állapot, beállítások, limitek |
| `POST /api/jobs` | Multipart feltöltés: `file`, `options` JSON-szöveg, opcionálisan `schema_file`, `rules_file` |
| `GET /api/jobs`, `GET /api/jobs/{id}` | Feladatlista / egy feladat |
| `POST /api/jobs/{id}/cancel`, `POST /api/jobs/{id}/retry` | Megszakítás / újrafuttatás |
| `DELETE /api/jobs/{id}` | Befejezett feladat és fájljainak törlése |
| `GET /api/jobs/{id}/files` | Generált fájlok listája |
| `GET /api/jobs/{id}/file?path=…` | Generált szöveges fájl előnézete |
| `GET /api/jobs/{id}/logs` | Feldolgozási napló |
| `GET /api/jobs/{id}/download?kind=all` | Teljes ZIP; további érték: `frontend`, `backend` |
| `DELETE /api/cache` | Közös AI-cache törlése, üres sor mellett |
| `POST /api/jobs/{id}/deploy`, `POST /api/batches/{id}/deploy` | Telepítés a projektbe (`project`, `layout`, `dry_run`) |
| `GET /api/jobs/{id}/helpers/{név}` | A közös segédfájl letöltése: `CommonMigrateTools.java` vagy `wf-table.ts` |
| `POST /api/fs/folders` | Egy mappa almappái a beépített mappaböngészőnek (`path`; üresen: meghajtók és a saját mappa) |
| `POST /api/fs/pick`, `POST /api/fs/pick/cancel` | A gép saját mappaválasztó ablaka (megvárja a választást) / bezárása |
| `GET /api/openapi.json` | Géppel olvasható API-séma |

A beépített Swagger/ReDoc felület nincs bekapcsolva, hogy a böngésző ne töltsön le külső JavaScriptet. Az OpenAPI JSON helyben elérhető. A feladat létrehozása 202, hibás opció 422, túl nagy fájl 413, megtelt sor 429, még nem letölthető/törölhető feladat 409 választ ad.

## 10. Hibaelhárítás és ellenőrzés

| Jelenség | Teendő |
|---|---|
| Backend nem érhető el | Ellenőrizd a Python terminált és a `/api/health` címet. A frontend Beállítások oldalán a teljes migrátor API URL-t add meg, az `/api` résszel együtt. |
| CORS-hiba | `http://localhost:4200` vagy `http://127.0.0.1:4200` címen nyisd meg a felületet. Más port vagy HTTPS külön origin. A backendet a dokumentált paranccsal indítsd. |
| Port foglalt | Állítsd le a korábbi szervert, vagy adj másik `--port` értéket és mentsd el a frontendben az új backend URL-t. |
| Hiányzik az Oracle exporter | Telepítsd/konfiguráld a megfelelő Forms környezetet, vagy tölts fel előre exportált XML-t. |
| Hibás XML / nem várt Forms-dialektus | Nyisd meg a naplót. Angol nevű Forms2XML export kell; az eredeti forrást a feladat bemeneti mappája megőrzi. |
| JSON elutasítva | Schema: `{"blocks": {...}}`; rules: `{"replacements": {...}}`. Teljes szerver-configot ne a schema/rules feltöltőbe adj. |
| AI időtúllépés / csonka válasz | Ellenőrizd az Ollama címet és modellt; csökkentsd a hívások számát, vagy óvatosan emeld a timeoutot/válaszhosszt. A szabályalapú eredmény megmarad. |
| Lock-hiba | Ugyanazt az adatmappát másik FRM backend használja; ne indíts több Uvicorn workert. |
| API JSON jelenik meg a kész UI helyett | Ellenőrizd, hogy a `web-dist/browser/index.html` megvan-e; fordítsd le a frontendet, majd indítsd újra a backendet. |

Fejlesztői ellenőrzés, a projekt gyökerében a virtuális környezet Pythonjával:

```bash
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
python scripts/smoke_local.py
```

A `smoke_local.py` saját ideiglenes backendfolyamatot indít egy szabad helyi porton; ellenőrzi az Angular asseteket, CORS-t, feltöltést és mindhárom ZIP-et, majd leállítja a folyamatot. A végrehajtott és a céges környezet hiányában nem végrehajtott ellenőrzéseket a [VALIDATION_HU.md](VALIDATION_HU.md) külön sorolja fel.


## 4.0 frontend-generálás

Az OLB XML-ek a forrás alatt többes fájlválasztóval tölthetők fel, az MMB külön opcionális mező. A fájlnevek maradjanak `<név>_olb.xml` és `<név>_mmb.xml`. Egy kérés legfeljebb 32 kísérőfájlt fogad; a teljes HTTP-kérés méretkorlátja is érvényes. Hiányzó OLB vagy ismeretlen frontend-attribútum esetén a feladat hibával leáll, nincs letölthető részleges forrás.

A generált frontend több fájlos. A FormBlock/táblázat selectorok és az `emit_imports` importútvonalai a haladó beállításokban kezelhetők. A táblázat importját a saját Optimus-csomagból add meg; a generátor nem találja ki. Az `inventory`, `--analysis-only` és `--regenerate` parancssori művelet; az újragenerálás megőrzi a kézzel szerkesztett komponensfájlokat.

További lépések: README_HU.md és COMPANY_PROFILE_HU.md. A backend továbbra is a localhost:4200 origint engedi a fejlesztői Angular felület számára.

## Migrációs váz mód

Nagy DUMP=ALL exporthoz a Generálási mód mezőben válaszd a Migrációs váz lehetőséget. [Modultérkép, végpontvázak és korlátok](docs/MODULTERKEP_ES_VAZ_HU.md). Az alapértelmezett szigorú mód megőrzi a korábbi ellenőrzéseket.
