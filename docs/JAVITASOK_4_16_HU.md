# 4.16 – DPS ServiceBase és generálás egyenesen a projektbe

## 1. DPS: `XYServiceBase`

A céges formátumban (`--awu-azon` / AWU_AZON) a DPS-ben is van modulszintű ServiceBase, a WBS-hez hasonlóan.
A ServiceImpl ebből örököl:

```java
public abstract class XYServiceBase extends ModuleServiceBase<DpsLogHelper> implements XYService {

    @Override
    public String getModuleName() {
        return XYConstants.NAME;
    }
}
```

```java
@XSlf4j
@Service
public class XYServiceImpl extends XYServiceBase {
```

- **Az ősosztály** a `java_service_base_dps` beállításból jön (alapból `ModuleServiceBase<DpsLogHelper>`). Az
  importjai a ServiceBase-be kerülnek; a ServiceImpl-ből kimaradnak, ha ott már nem használtak.
- **A ServiceBase generált fájl**, minden generáláskor frissül. A ServiceImpl továbbra is CREATE_ONCE.
- **Meglévő modulnál** (`--regenerate`) a megőrzött ServiceImpl a régi `extends ModuleServiceBase<…> implements
  XYService` sorral is lefordul. Az új alakra váltáshoz az `analysis/backend-regeneration/` javaslatából emeld át a
  deklarációt.

## 2. Generálás egyenesen a fő projektmappába

Ha a CL, DPS, WBS és frontend projekt egy közös mappában van, a migrátor a generált fájlokat egyből a
helyükre teszi, kézi másolgatás nélkül.

**A projekt feltérképezése:**
- **CL, DPS, WBS:** a Java-forrásgyökerek (`…/src/main/java`) közül a migrátor felismeri, melyik melyik. Először a
  mappa neve alapján (`…-cl`, `…-dps`, `…-wbs`), utána a benne lévő `cl` / `dps` / `wbs` csomagok és a céges
  osztályok alapján (`DpsLogHelper`, `WbsServiceBase`, `DataProviderServiceRestClientBase` …).
- **Frontend:** az Angular-projekt (`angular.json`) képernyőmappája. Ha egy korábbi telepítés már tett oda
  `frm-forms-screen.ts`-t, az a mappa; különben `<sourceRoot>/app`.
- **Kihagyott mappák:** a `node_modules`, `target`, `build`, `dist` és a rejtett mappák kimaradnak.
- **Kézi megadás:** ha a felismerés nem egyértelmű, a migrátor megmondja, és a részeket kézzel is meg lehet adni
  (lásd lent).

**Hova kerülnek a fájlok:**
- **Java:** a csomagja szerint, a saját projektjébe. Például
  `rendszer-dps/src/main/java/hu/ceg/…/rendeles/dps/RendelesServiceImpl.java`.
- **Csomagsor nélküli fájlok:** a webes felületen generált fájlokból (`java_empty_package`) hiányzik a csomagsor.
  Ilyenkor a telepítés beírja azt a csomagot, amelyre a generált importok hivatkoznak, így a fájl a helyén
  lefordul.
- **Frontend:** a képernyőmappába kerül a `frm-forms-screen.ts` és a `<modul>/…` mappa.
- **Ami kimarad:** a riportok és jegyzetek (`.md`) a generált kimenetben maradnak.

**Szabályok:**

| Helyzet | Mi történik |
|---|---|
| A fájl még nincs a projektben | megírjuk (**új**) |
| Generált fájl (DTO, interfész, Constants, ServiceBase, CommonMigrateTools, `frm-forms-screen.ts`), amelyet a legutóbbi telepítés óta nem módosítottak | felülírjuk (**frissítve**) |
| Generált fájl, amelyet a projektben kézzel módosítottak, vagy nem a migrátor írta | nem írjuk felül (**ütközés**); a friss változat a kimenetben van. Felülírás: `--force` |
| CREATE_ONCE fájl (ServiceImpl, ControllerImpl, képernyőkomponens), amely már létezik | soha nem írjuk felül (**megőrizve**), `--force`-szal sem |

- **Nyilvántartás:** a projekt gyökerében a `.frm-deploy.json` tartja számon, melyik fájlt mikor és milyen
  tartalommal írta a migrátor. Ebből tudja, hogy egy fájlt azóta kézzel módosítottak-e.
- **Biztonság:** a projektmappán kívülre semmi sem kerül, és symlinket nem követ.

**Parancssorból:**

```bash
# generálás és telepítés egyben
python -m frm_forms migrate FORM_fmb.xml --out kimenet/rendeles --screen --awu-azon 1234 --project C:/projektek/rendszer

# már meglévő kimenet(ek) telepítése: előbb az előnézet, aztán a telepítés
python -m frm_forms deploy kimenet/rendeles --project C:/projektek/rendszer --dry-run
python -m frm_forms deploy kimenet/rendeles --project C:/projektek/rendszer
python -m frm_forms deploy kimenet/batch --project C:/projektek/rendszer   # egy batch összes modulja

# ha a felismerés nem elég
python -m frm_forms deploy kimenet/rendeles --project C:/projektek/rendszer --layout DPS=rendszer-dps/src/main/java --layout frontend=rendszer-ui/src/app/kepernyok
```

- A `batch` is elfogadja a `--project` kapcsolót: a futás végén minden kész modult telepít.
- A részek a configban is megadhatók: `"project_layout": {"CL": "…", "DPS": "…", "WBS": "…", "frontend": "…"}`.
- A riport: `PROJECT_DEPLOY_HU.md` és modulonként `analysis/project-deploy.json`.
- Kilépési kód: 3, ha ütközés volt, vagy egy rész nem található (kihagyott fájlok).

**A webes felületen:**
- A feladat áttekintőjén és a tömeges futtatás nézetében új panel van: **Telepítés a projektbe**.
- Add meg a fő projektmappa teljes útvonalát, nézd meg az **Előnézetet**, majd **Telepítés**. A mappát a böngésző
  megjegyzi.
- A „Részek kézi megadása” alatt a CL / DPS / WBS / frontend mappa kézzel is beírható.
- **Hol fut:** a szerver csak a helyi gépről fogad kérést, és a telepítés ezen a gépen ír.
- **Korlátozás:** a `FRM_PROJECT_ROOTS` környezeti változóval (vesszővel elválasztott mappák) leszűkíthető, mely
  mappák alá szabad telepíteni.
- **Újrafordítás:** a lefordított webes felületbe (`web-dist`) ez a panel csak az `angular-frontend` forrás
  újrafordítása után kerül be.

## Ellenőrzés

- **Teljes tesztkészlet:** 559 teszt. A 101 hibás teszt ugyanaz, mint a `main` ágon (a repóból hiányzó
  mintabemeneteket keresik), új hibás teszt nincs.
- **`test_company_backend`:** a DPS ServiceBase felépítése, importjai, és hogy a ServiceImpl ebből örököl.
- **`test_project_deploy`:**
  - a felismerés (mappanév, céges osztályok, korábbi telepítés, nem egyértelmű eset, kézi megadás);
  - a telepítés (helyek, csomagsor beírása, CREATE_ONCE, ütközés, `--force`, frissítés, előnézet, hiányzó
    rész, symlink);
  - a CLI (`deploy`, `migrate --project`);
  - a webes végpont (előnézet, telepítés, elutasított kérések).
