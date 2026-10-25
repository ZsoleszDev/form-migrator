# A triggerhez szükséges értékek forrása — 4.32

Ez a korábbi **Frontend–backend bemenetek** panel új neve. Az eredeti Formsban a
trigger elérte például a `:BLOKK.MEZO`, `:GLOBAL.UGYFEL_ID` és `:PARAMETER.CEG`
értékeket. A weben ezeknek a Java felé menő kérésben vagy hiteles szerveroldali
forrásban kell megjelenniük. A panelben azt választod ki, honnan származzanak.
Ez a táblaszerkezetek megadásától külön feladat: itt konkrét futásidejű értékekről van szó.

Példa: a trigger `:GLOBAL.UGYFEL_ID` értékét a képernyő `UGYFEL.ID` mezője tartalmazza.
Válaszd a **Képernyőmező** forrást, majd az `UGYFEL.ID` mezőt, és ments.
Ezzel a migrátor valóban átírja a generált Angular-kérést. Ha az azonosítót a
modult megnyitó másik oldal adja át, a **Modulparaméter** a megfelelő lehetőség.
A **Bekötve** állapotú sorokhoz nincs külön teendő.

A Java asszisztens fülének panelje azt mutatja, honnan kapja az értékét
a generált kérés egy mezője. A statikus bekötés nem bizonyítja, hogy a felhasználó
futás közben valóban kitöltötte az értéket; a backend validációja továbbra is szükséges.

## Használat

1. Generálj **képernyőt** a migrátor aktuális verziójával, majd nyisd meg a Java asszisztenst.
2. Az **A triggerhez szükséges értékek forrása** panelen keresd meg a végpontot és a Forms-nevet,
   például `PARAMETER.UGYFEL_KOD` vagy `HIDDEN.ID`.
3. Válassz forrást, majd használd a **Bekötések mentése a kódba** gombot.
4. A friss frontend- vagy teljes ZIP-et töltsd le. A bekötés mentéséhez nem kell AI.

| Forrás | Mit változtat meg? | Mi marad kézi feladat? |
|---|---|---|
| Generált alapérték | Visszaállítja az eredeti mezőolvasást vagy a hiányzó `null` értéket. | A hiányzó forrás pótlása. |
| Képernyőmező | A kiválasztott FormGroup-mezőt vagy táblázat kiválasztott sorának oszlopát küldi. | A mező üzleti jelentésének és típusának ellenőrzése. |
| Modulparaméter | A komponens `migrationParameters` inputjának megnevezett kulcsát küldi. | A szülő komponensből át kell adni az értéket. |
| Kézi / szerveroldali | Megőrzi a jelenlegi generált kifejezést, és eltárolja a megjegyzést. | A tényleges Java-/Angular-kód megírása. Ettől még nem lesz kész a bekötés. |

A modulparaméter például így adható át a szülő komponens sablonjában
(a tényleges generált selectort használd):

```html
<app-ugyfel [migrationParameters]="{ ugyfelKod: kivalasztottUgyfelKod }" />
```

A paramétertérkép típusa `Readonly<Record<string, string | null>>`. Pontos NUMBER
értéket szövegként adj át, hogy ne veszítsen számjegyeket a JavaScript `number` miatt.
A szöveges akció-DTO-khoz a generátor a Forms checkbox-kódot és a helyi
`YYYY-MM-DDTHH:mm:ss.SSS` dátumot használja. Objektumot nem alakít csendben
`[object Object]` szöveggé. Egyedi összetett LOV-értékhez kézi skalárkiválasztás kell.

## Mit jelentenek az állapotok?

- **Bekötve:** van generált értékkifejezés a kéréshez.
- **Hiányzó érték:** jelenleg `null` kerül a kérésbe.
- **Szülőoldali input szükséges:** a kérés a modulparamétert olvassa, a szülőoldali átadás még ellenőrzendő.
- **Kézi feladat:** van fejlesztői megjegyzés, de nincs hozzá automatikus implementáció.
- **Hívó kód szükséges:** a végpont/bemenet ismert, de a képernyő nem épít hozzá megfelelő hívást.
- **Bemenetirány ellenőrzendő:** kézi triggerből/helyi programegységből felismert statikus hivatkozásjelölt.
- **Indítás időzítése kézi feladat:** a kérés a konstruktorban fut; az Angular-input és FormGroup még nem kész.
- **Kód kézzel módosult / Szerződés megváltozott:** a szerkesztett képernyőt kézzel kell egyeztetni, nincs automatikus mentés.

`SYSTEM.*` értékhez nem választható böngészős forrás. A felhasználó és tenant
hiteles azonosítása a szerver feladata; egy `GLOBAL.*` vagy modulparaméter csak
kliensadat. A Java lokális fejlesztői bemeneteihez a meglévő `java-variables.json`
vagy kézi ServiceImpl-módosítás használható; ezt a panel külön jelzi.

## Milyen kódot ellenőriz?

A generált akciók `blocks` és `parameters` értékeit, a képernyőhöz tartozó
keresési feltételeket és a generált LOV-végpontok bemeneteit. Kézi akcióknál
az elérhető helyi programegységek statikus bindhivatkozásait is összegyűjti.

Ez nem teljes PL/SQL adatfolyam-elemzés. SQL `INTO`, ismeretlen rutinok IN/OUT
irányai, dinamikus `NAME_IN`, dinamikus SQL és a frontend nélküli végpontok külön
ellenőrzést igényelnek. A mentés `inserted/updated/deleted` rekordlistáinak
összeállítását és az aszinkron lépések sorrendezését ez a kiadás nem automatizálja.

## Mentés és újragenerálás

- A mentés csak a generált bemeneti kifejezések jelölt helyeit módosítja.
  Az eredetiről másolat készül az `analysis/input-binding-originals` alatt.
- Kézzel szerkesztett komponens vagy elavult szerkesztési revízió esetén a mentés megáll.
- A konfiguráció exportálható `input-bindings.config.json` néven. CLI-generálásnál
  `--config input-bindings.config.json` használható; ha már van konfigurációd,
  annak `screen_input_bindings` kulcsa alá másold a bekötési részt.
- A webes **Újrafuttatás** átviszi a mentett bekötéseket az új modulba.
- A CLI `--regenerate` megőrzi a meglévő komponenst és a hozzá tartozó leltárt.
  Ha a friss bemeneti szerződés eltér, kézi egyeztetést jelez; a friss követelmények
  az `analysis/input-bindings.json` `fresh_requirements` részében találhatók.
- A bekötés változása érvényteleníti a korábbi Java-javaslat kontextuslenyomatát.
  Az új javaslat megkapja a mentett forrásokat és a még nyitott feladatokat.

4.29-ben vagy korábban generált képernyőhöz új generálás szükséges a bemeneti
szerkesztő használatához. A kézzel módosított régi képernyőt előbb őrizd meg,
és a friss generálást külön mappában hasonlítsd össze vele.
