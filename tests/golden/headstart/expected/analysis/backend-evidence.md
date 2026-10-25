# Backend-bizonyíték

A DPS ServiceImpl metódusai mögötti eredeti Forms SQL és PL/SQL, és a generált SQL. A kódban csak rövid összefoglaló áll; a részletek itt vannak.

## updateAit

```text
Forms blokk: AIT | művelet: update
Adatforrás: Table / ANK_ADLAP_ITEMS
Ellenőrzött mapping: ANK_ADLAP_ITEMS
Kulcs: AIT_KULCS -> AIT_KULCS
Mezők/oszlopok: aitKulcs -> AIT_KULCS, aitTipus -> AIT_TIPUS, aitStatus -> AIT_STATUS, aitMegj -> AIT_MEGJ
Eredeti WHERE: AIT_STATUS = 'F'
AND AIT_TIPUS = :V_ELEK_ADLAP.UBI_INPTIP_KOD
Eredeti ORDER BY: AIT_KULCS
DESC
query_allowed: true
insert_allowed: false
update_allowed: true
delete_allowed: false
Generált művelet: HTTP 501, ellenőrzésig tiltott
Tiltás oka: A WHERE olvasási SQL-re lefordítva; írás előtt a rekordszűrést és szervercontextet külön ellenőrizni kell.
Tiltás oka: AIT:POST-QUERY: POST-QUERY nem írhat adatbázismezőt ebben a snapshot-alapú adapterben. Átfuttatás az adatbázisban sem lehetséges: A trigger ebben az eseményben nem visszaírható mezőt ír: AIT.AIT_MEGJ (lekérdezett adatbázismező, kulcs vagy nem módosítható oszlop).
Tiltás oka: Írás nincs engedélyezve a schema.json-ban (writable: true).
Rekord betöltése és zárolása: SELECT AIT_KULCS, AIT_TIPUS, AIT_STATUS, AIT_MEGJ FROM ANK_ADLAP_ITEMS WHERE AIT_KULCS = :aitKulcs FOR UPDATE
UPDATE oszlopok: AIT_TIPUS, AIT_STATUS, AIT_MEGJ

TRIGGER AIT / POST-QUERY [review -> manual]
SHA256: b4eec645a6e44ab773d123f0be953e7f2ff2c260f47563d40d0c31740826d0ab
BEGIN
  :AIT.AIT_MEGJ := UPPER(:AIT.AIT_MEGJ);
END;
Forrás: analysis/discovery/sources/o29-b4eec645a6e4.sql
CALL CANDIDATE [sql_builtin] UPPER(:AIT.AIT_MEGJ)

TRIGGER FRM_ANK_TESZT / POST-FORMS-COMMIT [keretrendszer, nem üzleti logika]: qms$event_form('POST-FORMS-COMMIT')

TRIGGER FRM_ANK_TESZT / KEY-COMMIT [review -> manual]
SHA256: 92227ec95d84b2c09dcd933a37443f858f1556d60c0cbf47ad72262ce09a4bae
BEGIN
  IF :SYSTEM.FORM_STATUS = 'CHANGED' THEN
    commit_form;
  END IF;
END;

Folytatás: a DPS ServiceImpl SQL/mapping és szabálymetódusaiban.
TODO: ellenőrizd a WHERE/ORDER BY, bindek, jogosultsági/tenant-szűrés és DB-triggerek egyenértékűségét.
A fenti eredeti SQL/PLSQL bizonyíték; nem kerül automatikusan végrehajtásra.
Teljes forrás és blokkoló okok: analysis/form.ir.json, analysis/discovery/form-map.json.
```

## searchAit

```text
Forms blokk: AIT | művelet: read
Adatforrás: Table / ANK_ADLAP_ITEMS
Ellenőrzött mapping: ANK_ADLAP_ITEMS
Kulcs: AIT_KULCS -> AIT_KULCS
Mezők/oszlopok: aitKulcs -> AIT_KULCS, aitTipus -> AIT_TIPUS, aitStatus -> AIT_STATUS, aitMegj -> AIT_MEGJ
Eredeti WHERE: AIT_STATUS = 'F'
AND AIT_TIPUS = :V_ELEK_ADLAP.UBI_INPTIP_KOD
Eredeti ORDER BY: AIT_KULCS
DESC
query_allowed: true
insert_allowed: false
update_allowed: true
delete_allowed: false
Generált művelet: HTTP 501, ellenőrzésig tiltott
Tiltás oka: AIT:POST-QUERY: POST-QUERY nem írhat adatbázismezőt ebben a snapshot-alapú adapterben. Átfuttatás az adatbázisban sem lehetséges: A trigger ebben az eseményben nem visszaírható mezőt ír: AIT.AIT_MEGJ (lekérdezett adatbázismező, kulcs vagy nem módosítható oszlop).
Generált SQL: SELECT AIT_KULCS, AIT_TIPUS, AIT_STATUS, AIT_MEGJ FROM ANK_ADLAP_ITEMS WHERE ((AIT_STATUS = 'F') AND (AIT_TIPUS = :q0)) ORDER BY AIT_KULCS DESC OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
Keresési bind: :V_ELEK_ADLAP.UBI_INPTIP_KOD -> criteria.vElekAdlapUbiInptipKod -> :q0

TRIGGER AIT / POST-QUERY [review -> manual]
SHA256: b4eec645a6e44ab773d123f0be953e7f2ff2c260f47563d40d0c31740826d0ab
BEGIN
  :AIT.AIT_MEGJ := UPPER(:AIT.AIT_MEGJ);
END;
Forrás: analysis/discovery/sources/o29-b4eec645a6e4.sql
CALL CANDIDATE [sql_builtin] UPPER(:AIT.AIT_MEGJ)

Folytatás: a DPS ServiceImpl SQL/mapping és szabálymetódusaiban.
TODO: ellenőrizd a WHERE/ORDER BY, bindek, jogosultsági/tenant-szűrés és DB-triggerek egyenértékűségét.
A fenti eredeti SQL/PLSQL bizonyíték; nem kerül automatikusan végrehajtásra.
Teljes forrás és blokkoló okok: analysis/form.ir.json, analysis/discovery/form-map.json.
```

## lovInptip

```text
LOV: INPTIP | rekordcsoport: RG_INPTIP | használja: V_ELEK_ADLAP.UBI_INPTIP_KOD
Eredeti lekérdezés:
SELECT KOD, NEV
FROM ANK_INPTIP
ORDER BY KOD
Oszlopok: KOD -> V_ELEK_ADLAP.UBI_INPTIP_KOD, NEV -> V_ELEK_ADLAP.UBI_INPTIP_KOD_NEV
Szűrés a begépelt szövegre: KOD LIKE 'szöveg%' (kis- és nagybetűtől függetlenül)
Generált művelet: kész, csak a modulszintű ellenőrzésre vár (MODULE_REVIEWED, lásd az osztály elején); addig HTTP 501.
```

## commitForm

```text
Forms COMMIT_FORM sorrend: AIT
Blokkonként: DELETE (PRE-DELETE, ON-CHECK-DELETE-MASTER, ON-DELETE/DELETE, POST-DELETE), majd INSERT/UPDATE rekordonként a saját triggereivel.
```

