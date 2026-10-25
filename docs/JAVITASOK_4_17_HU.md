# 4.17 – A mappák kitallózása, részenként

A 4.16-ban a fő projektmappa útvonalát kézzel kellett beírni (`C:\Users\…`), és a CL, DPS, WBS és frontend
projektnek ebben a közös mappában kellett lennie. Most minden rész mappája kitallózható, és bárhol lehet.

## 1. Tallózás a webes felületen

A **Telepítés a projektbe** panelen öt sor van: **Fő projektmappa**, **CL**, **DPS**, **WBS** és **Frontend**.
Mindegyik mellett ott a **Tallózás…** gomb.

- **A gép saját mappaválasztó ablaka:** a gombra a megszokott mappaválasztó ablak nyílik meg (Windowson az
  Intézőé). A kiválasztott mappa bekerül a mezőbe.
  - A böngésző biztonsági okból nem adhatja át egy mappa teljes útvonalát, ezért az ablakot a migrátor
    szervere nyitja meg. A szerver ugyanazon a gépen fut, mint a böngésző (csak helyi kérést fogad), így az
    ablak a te gépeden jelenik meg.
  - Amíg az ablak nyitva van, a felületen egy kis ablak jelzi. A **Mégse** gomb bezárja a mappaválasztót. Ha
    nem látod a mappaválasztót, a tálcán találod.
- **Beépített mappaböngésző:** ha a mappaválasztó ablak nem nyílik meg, a felületbe épített böngésző jelenik meg.
  - Mikor: például ha a szerver asztali munkamenet nélkül fut, vagy hiányzik a Python `tkinter` csomagja. (A
    python.org Windows-telepítője alapból tartalmazza: „tcl/tk and IDLE”.)
  - Mit mutat: a meghajtókat, a saját mappát, a **↑ Fel** gombot és az almappákat. A Java-projekteket
    (`src/main/java`) és az Angular-projekteket (`angular.json`) címke jelöli.
  - Használat: lépj be a mappába, majd **Ezt a mappát választom**.
  - A várakozó ablakból is átválthatsz rá: **Inkább itt, a böngészőben**.
- **Kézzel is beírható:** a mezőkbe továbbra is beírható egy útvonal.
- **A böngésző megjegyzi** a kiválasztott mappákat, így legközelebb nem kell újra kitallózni őket.

**Mit lehet kiválasztani:**

| Rész | Kiválasztható mappa | Hova kerülnek a fájlok |
|---|---|---|
| CL, DPS, WBS | a Java-projekt mappája (`rendszer-dps`), a `src/main/java`, vagy egy csomagmappa benne | a `src/main/java` alá, a csomagjuk szerint (ahogy a Java megköveteli) |
| Frontend | az Angular-projekt mappája (ahol az `angular.json` van) | a korábbi telepítés képernyőmappájába, ha van ilyen; különben a `<sourceRoot>/app` alá |
| Frontend | egy képernyőmappa (például `rendszer-ui/src/app/kepernyok`) | ebbe a mappába; ha még nem létezik, az első telepítés létrehozza |
| Fő projektmappa | a közös mappa (nem kötelező) | a migrátor ebben keresi meg azokat a részeket, amelyeket külön nem választottál ki |

- **Nem kell közös mappa:** a részek bárhol lehetnek. Ha mind a négyet kitallózod, a fő projektmappa üresen
  maradhat.
- **Vegyesen is megy:** megadhatod a fő projektmappát és mellette egy-két részt. A külön kiválasztott rész
  mindig elsőbbséget kap.
- **A Java-rész mappájának léteznie kell.** Egy új Java-projektet a migrátor nem hoz létre.
- **Előnézet:**
  - A ki nem választott, de megtalált részeknél a mező „felismerve: …” szöveggel mutatja, hol találta meg őket
    a migrátor.
  - A célmappák listája minden részhez a pontos helyet mutatja. Ha egy rész nincs kiválasztva, és nem is
    található, a fájljai kimaradnak.
  - A fájltáblában minden fájlnál a rész és a hely látszik (`DPS: src/main/java/…`); a teljes útvonal a
    buborékban.
- **Telepítés:** csak az ugyanezekre a mappákra készült előnézet után indítható.

## 2. Nyilvántartás részenként

- **Új hely:** a `.frm-deploy.json` (melyik fájlt mikor és milyen tartalommal írta a migrátor) most minden
  rész saját projektmappájába kerül: `rendszer-dps/.frm-deploy.json`, az Angular-projekt mappája …, mert a
  részek külön helyen lehetnek.
- **A 4.16-os fájl:** a 4.16 a fő projektmappába írta a nyilvántartást. A migrátor ezt továbbra is elolvassa,
  ha megadod a fő projektmappát: a 4.16-tal telepített fájlokat felismeri, és frissíti őket, nem jelzi
  ütközésnek. Az első 4.17-es telepítés után a régi fájl törölhető.

## 3. Parancssorból

```bash
# a részek egyenként, fő mappa nélkül
python -m frm_forms deploy kimenet/rendeles --layout CL=C:/projektek/rendszer-cl --layout DPS=C:/projektek/rendszer-dps --layout WBS=C:/projektek/rendszer-wbs --layout frontend=C:/projektek/rendszer-ui

# generálás és telepítés egyben, a részek egyenként
python -m frm_forms migrate FORM_fmb.xml --out kimenet/rendeles --screen --awu-azon 1234 --layout DPS=D:/dps/rendszer-dps --layout frontend=C:/ui/rendszer-ui ...
```

- **`--project` nem kötelező:** elég egy `--layout` is. A `migrate` és a `batch` egy `--layout`-tal is telepít.
- **A `--layout` értéke:** teljes útvonal, vagy a `--project` mappához képest.
- **A configban:** `"project_layout": {"CL": "C:/projektek/rendszer-cl", …}`. Ez csak telepítéskor számít, tehát
  `--project` vagy `--layout` mellett.

## 4. Biztonság

- **Ki kérheti:** a szerver csak a helyi gépről fogad kérést. A mappaválasztás, a böngészés és a telepítés csak
  a felület fejlécével (`X-Frm-Client`) kérhető, más weboldal nem indíthatja.
- **Hova írhat:** semmi sem kerül a kiválasztott rész mappáján kívülre, és symlinket nem követ. (A 4.16-ban a
  fő projektmappa volt a határ.)
- **`FRM_PROJECT_ROOTS`:** ha be van állítva, a mappaböngésző és a mappaválasztó ablak is csak ezek alatt enged
  választani. A telepítés minden rész mappáját ellenőrzi.
- **`FRM_FOLDER_DIALOG=false`:** kikapcsolja a mappaválasztó ablakot, ilyenkor mindig a beépített böngésző
  nyílik meg.

## Átállás

- **Webes felület:** a panel csak az `angular-frontend` forrás újrafordítása után kerül be a lefordított
  felületbe (`web-dist`).
- **A 4.16-ban megadott fő projektmappa** átkerül az új panelre, a böngésző emlékszik rá.
- **Parancssor és config:** a `--project` és a `project_layout` úgy működik, mint eddig. Egy dolog változott:
  a `--layout` mappájának (kivéve a frontend képernyőmappáját) léteznie kell.

## Ellenőrzés

- **Teljes tesztkészlet:** 570 teszt. A 101 hibás teszt ugyanaz, mint a `main` ágon (a repóból hiányzó
  mintabemeneteket keresik), új hibás teszt nincs.
- **`test_project_deploy`:**
  - a részek egyenként, fő mappa nélkül és bárhol; vegyesen a fő mappával;
  - a kiválasztott mappa értelmezése: Java-projektmappa, `src/main/java`, csomagmappa, Angular-projekt, új
    képernyőmappa;
  - a hibás mappák elutasítása;
  - a részenkénti nyilvántartás, és hogy a 4.16-os nyilvántartás is számít;
  - a CLI: `deploy` csak `--layout`-tal, és ha semmi sincs megadva;
  - a webes végpont: fő mappa nélkül, elutasított kérések, `FRM_PROJECT_ROOTS` minden részre.
- **A tallózás tesztjei:**
  - a mappaböngésző: listázás, a projektek jelölése, a rejtett mappák kihagyása, az engedélyezett mappák;
  - a mappaválasztó ablak (egy álablak-folyamattal): választás, bezárás, ha nincs ablak (501), kikapcsolva,
    egyszerre csak egy, megszakítás a felületről.
- **A felület ellenőrzése:** a TypeScript-ellenőrzés és az Angular-fordító sablonelemzése hibátlan.
