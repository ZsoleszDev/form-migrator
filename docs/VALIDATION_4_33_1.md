# Ellenőrzés — 4.33.1

## Célzott Python-tesztek

```text
PYTHONPATH=<requirements-test target>:tests:. python3 -m unittest \
  test_backend_assistant test_backend_assistant_web -v

Ran 22 tests in 34.055s
OK
```

Kihagyott teszt nincs. Új esetek: az Ollama HTTP 404 válaszának JSON error
üzenete megmarad; nem JSON proxyválasznál a HTTP-státusz és reason megmarad;
nincs automatikus újrakérés. Üres TimeoutError esetén a háttérfeladat API-ja,
a visszatöltött legutóbbi hiba és a Python-napló is tartalmaz okot.
A korábbi javaslatátvételi, cache-, Java- és háttérfutási regressziók sikeresek.

## Valódi böngésző, Angular fejlesztői build

Sikeres `npm run build --workspace web-ui -- --configuration=development`.
A reaktív mező disabled figyelmeztetését fejlesztői módban ellenőriztük,
mivel a production build önmagában nem bizonyítja e figyelmeztetés hiányát.

A `tests/unified_assistant_browser.cjs` teljes folyamata valódi Chromium,
HTTP API, migrációs worker és Ollama-tesztkiszolgáló mellett sikeres:

- CSV fájl és beillesztés, hibás CSV változatlan vázlattal, PK és séma mentése;
- HTTP 422 mezőhiba olvasható a gomb melletti visszajelzésben és a konzolban;
- a hiba az éppen látható képernyőrészben, a kattintott gomb mellett jelent meg;
- a kérés előtti kontextusellenőrzés hibájával lezárt háttérfutás oka látható;
- a hiba oldalfrissítés után is visszatöltődik; a helyes beállítással újraindítás sikeres;
- az aktív AI-futás alatt a modulválasztó ténylegesen letiltott;
- a konzolban nincs disabled + reactive-form figyelmeztetés és nincs futásidejű kivétel;
- folytatás, entity/repository/DTO/service javaslat, beillesztés, friss ZIP,
  valamint külön frontend origin használata is sikeres.

A próba az Angular fő komponens korábbi NG8102 figyelmeztetéseit nem módosította.
A csomag a normál production buildet tartalmazza.

## Határ

A felhasználó eredeti AI-leállásának konkrét oka a kapott figyelmeztetésből
nem állapítható meg. A javítás a biztosan hibás mezőletiltást és a hibaüzenetek
láthatóságát kezeli; nem igazol kapcsolatot a felhasználó belső Ollama-gépével.
Nem futott valódi Qwen-inferencia, Oracle vagy teljes céges alkalmazás.
