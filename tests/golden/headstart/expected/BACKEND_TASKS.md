# Backend bekötési terv

A forrás és a generált kód alapján készült. A hiányzó runtime-viselkedés nem válik automatikusan kész megoldássá.

## Modulszintű ellenőrzés

Minden generált CRUD-műveletre vonatkozik; a kész műveleteket a `TesztServiceImpl.MODULE_REVIEWED` kapcsoló élesíti:

- Migrációs váz: az összes adatbázis-művelet tiltott az ellenőrzött implementációig.

## Lekérdezések

| Blokk | Metódus | HTTP útvonal | Futás |
|---|---|---|---|
| AIT | searchAit | POST /api/forms/teszt/ait/query/search | tiltott / ellenőrizendő |

### searchAit

| Forms-forrás | criteria mező | típus | JDBC bind |
|---|---|---|---|
| V_ELEK_ADLAP.UBI_INPTIP_KOD | vElekAdlapUbiInptipKod | text | q0 |

Kérésváz (a null értékeket a tényleges keresőmezőkből/kijelölt master rekordból töltsd):

```json
{
  "criteria": {
    "vElekAdlapUbiInptipKod": null
  },
  "offset": 0,
  "limit": 50
}
```

## LOV-végpontok

A RecordGroupQuery változatlanul fut; kérés: `{"term": "beg", "parameters": {"BLOKK.MEZŐ": "érték"}, "limit": 50}`. A válasz sorai oszlopnév szerint érkeznek; a visszaírandó mezők a LOV oszlop-leképezései.

| LOV | HTTP útvonal | Paraméterek | Futás |
|---|---|---|---|
| INPTIP | POST /api/forms/teszt/lov/inptip | — | kész, MODULE_REVIEWED-re vár |

## Backend végpont nélkül kezelt triggerek

A teljes forrás megmarad az elemzésben. A szűrés a trigger tartalmán alapul, nem a gomb nevén.

| Objektum | Besorolás | Indok |
|---|---|---|
| CGNV$W01_1.PB_RESZLETEK | frontend | Felismert Forms-felületművelet; a frontend/host adapter feladata. |

## Konkrét teendők

| Objektum | Ok | Folytatás |
|---|---|---|
| V_ELEK_ADLAP | V_ELEK_ADLAP.UBI_INPTIP_KOD: LOV=INPTIP; rekordcsoport/return mapping adapter szükséges. | A LOV lekérdezése generált végponton fut (lásd: LOV-végpontok); a frontend host-adapterében kösd be, és íráskor a választott értéket szerveroldalon is ellenőrizd (Validate from List). |
| AIT | A WHERE olvasási SQL-re lefordítva; írás előtt a rekordszűrést és szervercontextet külön ellenőrizni kell. | Ellenőrizd a WHERE teljes SQL-jét, bindjeit és a sor-/tenant-jogosultságokat a ServiceImpl-ben. |
| AIT | A paraméter nélküli lista tiltott. A típusos searchAit metódusnak add át: V_ELEK_ADLAP.UBI_INPTIP_KOD | A paraméter nélküli listázás helyett kösd be a típusos keresési végpontot a felsorolt Forms-mezőkből. |
| AIT | AIT:POST-QUERY: POST-QUERY nem írhat adatbázismezőt ebben a snapshot-alapú adapterben. Átfuttatás az adatbázisban sem lehetséges: A trigger ebben az eseményben nem visszaírható mezőt ír: AIT.AIT_MEGJ (lekérdezett adatbázismező, kulcs vagy nem módosítható oszlop). | A megadott trigger és teljes híváslánca alapján implementáld/teszteld a ServiceImpl műveletet. |
| @FORM:FRM_ANK_TESZT | Migrációs váz: az összes adatbázis-művelet tiltott az ellenőrzött implementációig. | A képernyőváz mód tiltását a ServiceImpl és a gazdaalkalmazás integrációjának ellenőrzése után a MODULE_REVIEWED kapcsoló oldja fel (egy helyen, nem metódusonként). |
| AIT | A schema.json writable beállítása nem engedélyez írást. | Igazold a kulcsokat, DML-célt, DB-triggereket és tranzakciót, majd állítsd be a schema.json writable értékét. Ez önmagában más tiltást nem old fel. |

A teljes, géppel is feldolgozható terv: [backend-handoff.json](analysis/backend-handoff.json).
Kiinduló schema (minden írás tiltva): [backend-schema.example.json](analysis/backend-schema.example.json). Ellenőrizd adatbázis-metaadatokkal; hiányzó kulcsot nem talál ki.
Triggerek és hívásláncok: [form-map.json](analysis/discovery/form-map.json). Az AI külön javaslat, nem módosít tiltást vagy kódot.
