# 4.12.1 – PMD metódusnevek és CommonMigrateTools

## Metódusnevek

A célminta: `^[a-z][a-z0-9][a-zA-Z0-9_]*$`. A második karakter ezért
nem lehet nagybetű vagy aláhúzás; a hagyományos camelCase önmagában nem elég.

| Korábbi alak | Új alak |
|---|---|
| `vElekAdlapUbiIlimbiKod2Action116a87a8` | `onVElekAdlapUbiIlimbiKod2` |
| `b_validateRules` | `validateRules_b` |
| `vElekAdlap_map` | `map_vElekAdlap` |

A gombtriggereknél az `on` előtag biztosítja a két kisbetűs kezdést. A blokk és a
mező neve megmarad, az általános `Action` + hash-utótag eltűnik a Java metódusnévből.
Csak normalizálási ütközésnél kerül sorszám a végére. A generátor előbb lefoglalja
a természetes neveket, így egy eleve számmal végződő mező neve sem ütközik az
utólag számozott nevekkel. Az XML-elemek sorrendje és a feltöltött fájl átnevezése
nem változtatja meg a kiosztást.

A privát blokksegédeknél a művelet neve kerül előre, a blokk neve az aláhúzás mögé.
A Java hívások és a `this::…` metódusreferenciák együtt változnak. Az átírás nem
érinti a Java szövegliterálokat, a SQL/PLSQL-t vagy a kommenteket.

A CL, DPS és WBS ugyanazokat a metódusneveket használja. Az akcióazonosítók és a
HTTP-útvonalak megmaradnak: a céges profil korábbi hash-t tartalmazó útvonalértéke
az `api_name` mezőben külön él a Java metódusnévtől. A generált Java konstansok
azonosítói az új metódusneveket követik, a hívó rétegek ezekkel együtt készülnek.

## CommonMigrateTools importhelye

Az Angular migrátor `angular-frontend/migrator-app.ts` fájljában a „Célkörnyezet
és Java” panel új mezője: **CommonMigrateTools Java package**.

Példa: `hu.ceg.common.cl`. Ez Java-csomagnév, ezért az osztálynevet, az `import`
szót és a mappaútvonalat nem kell megadni. A CLI/szerver configban ugyanaz a
korábban is létező kulcs használható:

```json
{
  "common_migrate_tools_package": "hu.ceg.common.cl"
}
```

A generált fájlban:

```java
package hu.ceg.common.cl;
```

A DPS ServiceImpl-ben a ténylegesen használt beágyazott segédek importja például:

```java
import hu.ceg.common.cl.CommonMigrateTools.DbCalls;
import hu.ceg.common.cl.CommonMigrateTools.SqlValues;
```

Üres értéknél marad a `<java_package>.cl` alapértelmezés. A beállítás átmegy az
API-n, bekerül a mentett feladatba és az effektív konfigurációba. A böngészős
beállításmentés, az újrapróbálás és a tömeges generálás is megőrzi. A hibás
csomagneveket és a foglalt szavakat a CLI/API elutasítja.

A csomag továbbra is tartalmaz egy `backend/CL/CommonMigrateTools.java` fájlt.
Ezt egyszer kell a közös CL-projekt megfelelő package-ébe másolni, nem minden
modul alá. A segédosztály implementációja és `VERSION = "2"` értéke változatlan.

## Frissítés és ellenőrzés

A Python generátort és az Angular migrátor komponensét együtt frissítsd.
Korábbi generált modult érdemes új célmappába generálni, majd átvenni az új
CL/DPS/WBS fájlokat. A kézi kódban használt régi metódus-/konstansneveket is
illeszteni kell. A `--regenerate` továbbra is megőrzi a CREATE_ONCE
ServiceImpl/ControllerImpl fájlokat: a friss változatot és az eltérést az
`analysis/backend-regeneration` mappába teszi kézi összefésüléshez.

A regressziós ellenőrzések lefedik a megadott PMD-névmintát, a névütközéseket,
a HTTP-útvonalak megőrzését, a két backendprofilt és a webes opció továbbítását.
A Java 11 fordítási teszt két generált modult fordít egyetlen, egyedileg beállított
csomagban lévő segédosztállyal, tesztbeli Spring/céges helyettesítőkkel.
Ez nem helyettesíti a saját projekt PMD-szabálykészletével végzett host buildet.

Ellenőrzési eredmény: mind a 12 új teszt sikeres. A teljes futás 392 tesztesetet
tartalmazott, 29 kihagyással; 34 sikertelen ellenőrzés és 46 hiba ugyanúgy jelen
van, mint az eredeti feltöltésben, az érintett tesztesetek listája nem változott.
Ezek főként a csomagból hiányzó `examples` bemenetekhez kötődnek.
A teljes Angular hostbuild és a saját PMD-szabálykészlet itt nem futott.
