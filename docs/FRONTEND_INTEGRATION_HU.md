# Saját Angular frontend bekötése – 4.7.1

A migrátor helyi Python API-ja a saját alkalmazás `http://localhost:4200/migrator` oldaláról is hívható. Alapból engedélyezett a localhost/127.0.0.1 4200 és 4201 origin, a backend saját portja, valamint az `Authorization`, `Content-Type` és `X-Frm-Client` fejléc. A CORS middleware ezen felül a szokásos egyszerű fejléceket, például az Accept fejlécet is kezeli.

Az Angular interceptor által hozzáadott `Authorization: Bearer …` nem okoz többé tiltott-fejléc hibát. A Python API a tokent nem ellenőrzi, nem használja jogosultságként és nem menti a generálási feladatba. Ez továbbra is helyi, egyfelhasználós migrátor; nem céges bejelentkezési végpont. A szerver továbbra is a 127.0.0.1 címen figyel.

## Indítás

A friss csomag könyvtárában állítsd le a régi backendfolyamatot, majd indítsd el:

```powershell
.\.venv\Scripts\python.exe -m frm_forms.web --port 8000
```

Frontend: `http://localhost:4200/migrator`. Migrátor API URL: `http://localhost:8000/api`.

Ezekhez az alapértékekhez nem kell Python-kódot módosítani. Más frontend és további céges fejléc a backend termináljában állítható be, indítás előtt:

```powershell
$env:FRM_CORS_ORIGINS = "http://localhost:4300,https://frontend.example"
$env:FRM_CORS_HEADERS = "X-Request-Id,X-Company-Id"
.\.venv\Scripts\python.exe -m frm_forms.web --port 8000
```

Az értékek példák: csak a ténylegesen használt origineket/fejlécneveket add meg. A listák bővítik az alapértékeket. Originben nincs `/migrator`, záró `/`, query vagy wildcard; fejlécbe csak név kerül, tokenérték nem. Hibás beállításnál a szerver induláskor hibát jelez. Ezek a szerver környezeti változói, nem a generátor `--config` JSON kulcsai.

Ha a saját HttpClient/interceptor `withCredentials: true` beállítással küld kérést, a backendben ezt külön engedélyezd:

```powershell
$env:FRM_CORS_ALLOW_CREDENTIALS = "true"
```

Alapértéke false. A kézzel hozzáadott Bearer fejléc miatt önmagában nem kell bekapcsolni. Ez a kapcsoló nem hoz létre sessiont és nem végez cookie- vagy tokenellenőrzést.

## A frontend kérései

A POST és DELETE műveletekhez add hozzá a `X-Frm-Client: local-ui` fejlécet. Ez explicit migrátorkérés-jelölés; a Bearer fejléc nem helyettesíti.

Példa a meglévő Angular service-ben (`http` egy HttpClient, `file` egy File):

```typescript
const api = 'http://localhost:8000/api';
const headers = { 'X-Frm-Client': 'local-ui' };

const body = new FormData();
body.append('file', file, file.name);
body.append('options', JSON.stringify({ ai_mode: 'assist', ollama_url: 'http://192.168.1.50:11434', ollama_model: 'SAJAT_MODELLNEV' }));

return this.http.post<{ id: string }>(`${api}/jobs`, body, { headers });
```

A visszaadott Observable-ra a hívó feliratkozik. A meglévő interceptor továbbra is hozzáadhatja az Authorization fejlécet. FormData esetén ne állíts be kézzel JSON vagy multipart Content-Type fejlécet: a böngésző állítsa elő a multipart boundary-t. Ha a globális interceptor minden kérésre JSON Content-Type-ot erőltet, a FormData kéréseket ki kell venni ebből az ágból.

Állapotlekérdezés: GET `/api/jobs/{id}`. Letöltés: GET `/api/jobs/{id}/download?kind=all` (vagy frontend/backend), Angularban `responseType: 'blob'`. A Content-Disposition fejléc olvasható a fájlnévhez. Törlés: DELETE `/api/jobs/{id}`, a fenti X-Frm-Client fejléccel.

A migrátor sima JSON objektumokat ad vissza, nem vállalati ResponseDto burkolót; a hibák jellemzően a `detail` mezőben vannak. A ZIP-válasz bináris. Ha a saját globális interceptor automatikusan kibont vagy átír minden választ, a migrátor API-t ehhez külön kell igazítani. A saját frontend forrása nem része ennek a javításnak; az itt megadott szerződés alapján illeszthető be a meglévő service-be.

Az Angular viselkedése: [HttpClient kérések, FormData, fejlécek és bináris válaszok](https://angular.dev/guide/http/making-requests). A backend CORS paraméterei: [FastAPI middleware](https://fastapi.tiangolo.com/reference/middleware/).

## Diagnosztika

Induláskor a backend kiírja az aktív origineket, fejlécneveket és a credentials kapcsolót. A `http://localhost:8000/api/health` verziója 4.7.1 legyen. A `http://localhost:8000/api/defaults` az aktív `cors_origins`, `cors_headers`, `cors_allow_credentials` és `client_contract` mezőket adja vissza.

Elutasított OPTIONS kérésnél a konzolban és a HTTP-válaszban megjelenik az ok: `Disallowed CORS origin`, `headers`, `method`, illetve az alkalmazott middleware-verzióban `private-network` is lehet. A diagnosztika a middleware okát írja ki; Authorization értéket nem naplóz.

PowerShell-próba a böngészőn kívül:

```powershell
curl.exe -i -X OPTIONS "http://localhost:8000/api/health" -H "Origin: http://localhost:4200" -H "Access-Control-Request-Method: GET" -H "Access-Control-Request-Headers: authorization"
```

Elvárt: HTTP 200, Access-Control-Allow-Origin: http://localhost:4200, és az engedélyezett fejlécek között authorization. Ha a böngésző más egyedi fejléceket is kér, azok neveit a Network panel `Access-Control-Request-Headers` mezője mutatja. Az ismeretlen origin/metódus/fejléc továbbra is elutasított marad, nincs általános `*` feloldás.

## Ellenőrzés

A `tests/test_frontend_integration.py` ellenőrzi a három induló GET végpont preflightját és tényleges hívását Bearer fejléccel, az egyedi környezeti beállításokat, a credentials módot, a hibák diagnosztikáját és a teljes feltöltés–generálás–backend ZIP–törlés folyamatot. A feltöltési jelölés és az idegen origin elleni védelem megmarad. A teszt szintetikus tokent használ, és ellenőrzi, hogy nem kerül a feladat fájljaiba.

A generálómotor nem változott ebben a javításban; a CL/DPS/WBS szerkezet továbbra is 4/5/5 Java-fájl. A korábbi 4.7.0-s példakimenetek megmaradtak. A kész Studio frontend nem igényel új buildet, a céges frontend bekötéséhez a fenti service-módosítás szükséges.
