# Egyszerű migrálási munkamenet — 4.34

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
Az egyszerű részeket a determinisztikus generátor készíti el.

## 2. A megmaradt trigger javásítása

A kész modulnál kattints a **Java asszisztens** gombra, majd válaszd ki a végpontot.

- **Adatbázistáblák:** a rendszer felismeri a trigger tábláit. Importáld az oszlopokat
  CSV-ből, jelöld a tényleges elsődleges kulcsot (PK), és ments.
- **Trigger javásítása:** kattints a gombra. Az AI csak a kiválasztott DPS-metódust kapja
  (a kikommentezett Oracle-triggerrel) és a benne használt táblák rövid leírását.
  Az AI átírja a triggert a meglévő DPS ServiceImpl metódusához, az adatbázis-műveleteket
  pedig saját, entity-alapú repository metódusokba teszi.
- **AI-kommunikáció:** itt látszik a modellnek elküldött kérés és a modell nyers válasza
  (az elutasított is), és hogy a migrátor mit vett át belőle. Ugyanez újrafordítás nélkül:
  `http://localhost:8000/ai-log`.

A meglévő Controller és végpont megmarad. A repository Spring-bekötése,
a JPA entityk és a szükséges kiegészítő DTO is együtt készül.
Képernyőértékek bekötésével az alapfolyamatban nem kell külön foglalkoznod.

## 3. Beillesztés és próba

Nézd át a visszakapott service-metódust és a repositoryt.
A **Beillesztés a fájlokba** gomb után töltsd le a **Friss modul ZIP** csomagot.

A céges projektbe másold be a friss DPS-/CL-fájlokat, fordítsd le és hasonlítsd
össze a működést az eredeti Forms-triggervel, a hibás és NULL adatoknál is.
A célprojektben JPA/Hibernate szükséges az entityk és repositoryk futtatásához.

Ha az AI hiányzó külső rutint vagy céges API-t kér, azt a **Kiegészítő forrás**
részben add meg, majd indítsd újra a javásítást. Egyébként nincs további lépés.
