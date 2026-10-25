# Backend bekötési terv

A forrás és a generált kód alapján készült. A hiányzó runtime-viselkedés nem válik automatikusan kész megoldássá.

## Lekérdezések

| Blokk | Metódus | HTTP útvonal | Futás |
|---|---|---|---|
| B | listB | GET /api/forms/dbcall/b/getdata | engedélyezett |

## Konkrét teendők

| Objektum | Ok | Folytatás |
|---|---|---|

A teljes, géppel is feldolgozható terv: [backend-handoff.json](analysis/backend-handoff.json).
Kiinduló schema (minden írás tiltva): [backend-schema.example.json](analysis/backend-schema.example.json). Ellenőrizd adatbázis-metaadatokkal; hiányzó kulcsot nem talál ki.
Triggerek és hívásláncok: [form-map.json](analysis/discovery/form-map.json). Az AI külön javaslat, nem módosít tiltást vagy kódot.
