# Egyszerű migrálási munkamenet — 4.35

## 1. Alapgenerálás

A PC-n indítsd a Python migrátort:

```powershell
python -m frm_forms.web --port 8000
```

Nyisd meg a http://localhost:8000 címet.
A közös AI-beállításoknál az Ollamát futtató laptop belső címét add meg,
például `http://192.168.1.50:11434`, és a betöltött modell pontos nevét. Mentsd az AI-kapcsolatot: a program ellenőrzi
a szervert és a modellt. A webes AI-segítség automatikusan aktív; külön kapcsoló nincs.

Generáld le az FMB/XML modult a szokásos beállításokkal.
A determinisztikus generátor a frontendet, a backend keretét (végpontok, DTO-k, controller, `log1x`, tranzakció),
a táblák JPA entityjét és üres repositoryját készíti el. A DPS ServiceImpl metódusait az AI írja: mindegyikben ott
van, amiből dolgozik (az eredeti Forms-kód, illetve az `AI-feladat` megjegyzés az SQL-lel és a lépésekkel).

## 2. A metódusok javásítása

A migrálás végén az AI-munkasor magától elindul a modul összes metódusával (adatvégpontok, LOV-ok, gombok,
rekordtriggerek). Kézzel: a kész modulnál kattints a **Java asszisztens** gombra, majd válaszd ki a végpontot.

- **Adatbázistáblák:** a rendszer felismeri a kód tábláit. Importáld az oszlopokat CSV-ből, jelöld a tényleges
  elsődleges kulcsot (PK), és ments: ebből készül a tábla JPA entityje és üres repositoryja. Az AI-feladatokat ez
  nem tartja vissza. Az elsődleges kulcs nélküli blokk táblája minden végpontnál megjelenik („Elsődleges kulcs kell
  a(z) … blokk adatvégpontjaihoz”): a blokk csak kulccsal kap entityt és adatvégpontot. Mentés után futtasd újra a
  migrálást (**Újrafuttatás**).
- **Trigger javásítása:** kattints a gombra. Az AI csak a kiválasztott DPS-metódust kapja, a megjegyzéseivel együtt.
  Mindent a service-ben ír meg: az eredeti Oracle SQL-t az `entityManager` natív lekérdezéseként futtatja, ugyanazzal
  a működéssel, mint a Forms. Repositoryt nem ír.
- **AI-kommunikáció:** itt látszik a modellnek elküldött kérés és a modell nyers válasza
  (az elutasított is), és hogy a migrátor mit vett át belőle. Ugyanez újrafordítás nélkül:
  `http://localhost:8000/ai-log`.

A meglévő Controller és végpont megmarad.
Képernyőértékek bekötésével az alapfolyamatban nem kell külön foglalkoznod.

## 3. Beillesztés és próba

Nézd át a visszakapott service-metódust (az automatikus beillesztés be is teszi).
A **Beillesztés a fájlokba** gomb után töltsd le a **Friss modul ZIP** csomagot.

A céges projektbe másold be a friss DPS-/CL-fájlokat, fordítsd le és hasonlítsd
össze a működést az eredeti Forms-triggerrel, a hibás és NULL adatoknál is.
A célprojektben JPA/Hibernate szükséges (`EntityManager`, entityk). A lekérdezéseket a fejlesztő később áthelyezheti a
tábla repositoryjába (`@Query(value = "...", nativeQuery = true)`), ha úgy látja jónak.

Ha az AI hiányzó külső rutint vagy céges API-t kér, azt a **Kiegészítő forrás**
részben add meg, majd indítsd újra a javásítást. Egyébként nincs további lépés.
