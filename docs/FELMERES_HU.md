# Felmérés – mi tiltja a generált végpontokat

A felmérés sok formot generál egyszerre, majd egy megosztható riportban összesíti, miért maradnak
tiltva (HTTP 501, kommentben hagyott SQL) a DPS-végpontok. A riport alapján látszik, melyik hiányzó
fordítási szabály szabadítaná fel a legtöbb végpontot.

## Futtatás a webes felületen

1. A „Forrás” részben kapcsold be a **Felmérés** kapcsolót.
2. Húzd be a formokat (.fmb vagy Forms2XML .xml), akár egyet, akár százat.
3. Ha a formok OLB-re hivatkoznak, add hozzá az OLB-exportokat (`…_olb.xml`). Ha van, a `schema.json`-t
   is megadhatod, de nem kötelező.
4. Indítsd el a „Felmérés indítása” gombbal.

Felmérés módban:

- nem kell AWU_AZON;
- a több ablakos formok nem állnak meg kérdéssel, az összes ablak elkészül;
- több lehetséges főablak esetén az első lesz a fő képernyő.

A futás a Feladatok fülön, a tömeges futtatások között követhető. A végén a „Mi tiltja a végpontokat”
táblázat mutatja a legfontosabb okokat. A „Felmérés” sor gombjai letöltik a riportot:

- **Markdown:** `FELMERES_HU.md`, ez olvasható és megosztható.
- **JSON:** `felmeres.json`, a teljes adat.
- **Nevekkel:** a valódi nevekkel készült változat. Ez csak helyi használatra való, ne oszd meg.

Az „Összesítő ZIP” is tartalmazza a felmérés két fájlját.

## Futtatás parancssorból

```
python -m frm_forms survey forms/ --out felmeres
python -m frm_forms survey forms/ --out felmeres --olb kozos_olb.xml --schema schema.json
python -m frm_forms survey forms/ --out felmeres --pld konyvtarak/ANKLIB.pld   # csatolt könyvtárakkal
python -m frm_forms survey --out felmeres --report-only --names   # meglévő kimenetből, nevekkel
```

A kimenet ugyanaz, mint a weben: `FELMERES_HU.md` és `felmeres.json`, mellettük a szokásos
`PORTFOLIO_HU.md`, a formonkénti almappákban pedig a generált modulok.

A felmérés a `backend_live` beállítással fut, ahogy a web is. Így a riport azt mutatja, mit nem tud
lefordítani a generátor, nem az ellenőrzési szabályokat: nincs MODULE_REVIEWED-kapu, és az írás csak
akkor tiltott, ha a `schema.json` kifejezetten tiltja.

## Mi van a riportban

- **Végpontok:** műveletenként az összes, az engedélyezett, a kapcsolóra váró és a tiltott végpontok
  száma.
- **Okok:** a tiltott végpontok okai hibakóddal és üzenetsablonnal. Az **egyedüli ok** oszlop
  megmutatja, hány végpontot csak az adott ok tilt, vagyis hány nyílik meg, ha ezt az egy okot
  megszüntetjük. Az első okokhoz kódpéldák tartoznak.
- **Eltérések a Forms-működéstől** (4.14): ami elkészül és működik, de nem pontosan úgy, mint a
  Formsban. Ezek nem tiltanak végpontot, ezért az okok között nem jelennek meg. A riport mindegyiket
  megszámolja (a 0-s sorokat is), és megadja, mit csinál most a webes modul, és mi lenne a javítás:

  | Eltérés | Most a webes modulban |
  |---|---|
  | Többsoros, írható adatbázis-blokk | blokkonként egy aktuális rekord szerkeszthető, a táblázat sorai csak megjelennek |
  | Szerveroldali mezővalidáció (WHEN-VALIDATE-ITEM, POST-CHANGE) | mentéskor fut, nem a mező elhagyásakor |
  | Szerveroldali rekordvalidáció (WHEN-VALIDATE-RECORD) | mentéskor fut, nem a rekord elhagyásakor |
  | Saját logikájú adat-billentyű (KEY-COMMIT, KEY-EXEQRY, KEY-CREREC, KEY-DELREC …) | az eszköztár gombja az alapműveletet hívja, a trigger logikája nem fut |
  | Saját logikájú billentyű-trigger webes megfelelővel (KEY-NEXT-ITEM, KEY-Fn, KEY-LISTVAL, KEY-CLRBLK …) | nem fut |
  | A Forms-felület billentyűi (KEY-HELP, KEY-ENTQRY, KEY-EXIT, KEY-CLRFRM, KEY-OTHERS …) | nem fut; többnyire nincs teendő, a súgót és a kilépést a host alkalmazás adja |
  | Mezőesemény, amely nem fut (vezérlőblokk WHEN-VALIDATE-ITEM / POST-CHANGE, WHEN-*-CHANGED) | nincs végpont, amely futtatná |
  | Képernyőesemény, amely nem fut (WHEN-NEW-BLOCK/RECORD/ITEM-INSTANCE, WHEN-WINDOW-* …) | a képernyő váz (4.27): a mezőállapot-szabályok sem jönnek át |
  | Képernyőlépés a kód közepén (EXECUTE_QUERY, CLEAR_BLOCK …) | 4.15-től képernyőpont; ami így sem követhető (ciklus, átnyúló helyi változó …), az kézi feladat |
  | Saját hiba- és üzenetkezelés (ON-ERROR, ON-MESSAGE) | nem fut |
  | POST-QUERY többsoros blokkon | működik, de soronként egy adatbázis-hívás |

  Az a billentyű-trigger, amely csak a billentyű saját műveletét végzi (például `KEY-NXTBLK`:
  `NEXT_BLOCK;`), nem számít eltérésnek. A keretrendszeri (`qms$…`) triggereket sem számolja.
  A webes felületen az „Eltérések a Forms-működéstől” panel mutatja a talált sorokat.
- **Indítási végpont** (4.15): ha a PRE-FORM / WHEN-NEW-FORM-INSTANCE kódjából nem készülhet indítási
  végpont, az tiltott „gomb / indítás” végpontként számít, `STARTUP` kóddal és az okkal (korábban a riport
  ezt nem mutatta). A trigger-táblában is ez az ok szerepel.
- **Átültetendő triggerek:** eseményenként és okonként, a bennük lévő Forms-hívásokkal és
  SQL-szerkezetekkel.
- **Szerkezetek:** hány végpontot tiltó trigger tartalmazza az egyes elemeket (pl. `SET_BLOCK_PROPERTY`,
  `GO_BLOCK`, `:SYSTEM.*`, `SELECT … INTO`, csomaghívás).
- **Lekérdezések és LOV-ok:** a tiltott olvasások WHERE feltételei és a hibás rekordcsoport-lekérdezések.
- **Adatforrások, hibakódok, sikertelen formok.**

## Mit takar el a megosztható változat

A kódpéldák szerkezete megmarad. Ami helyettesítőre cserélődik:

- **Nevek:** táblák, oszlopok, rutinok, blokkok, mezők, például `N1`, `:B1.I2`, `:GLOBAL.G1`,
  `:PARAMETER.P1`.
- **Szöveges literálok:** `'…'`. Ha a szövegben SQL van, például egy `DEFAULT_WHERE` értéke, annak
  csak a szerkezete marad.
- **Négy- vagy többjegyű számok:** `N`.
- **Kommentek:** kimaradnak.

Ami megmarad:

- **Nyelvi elemek:** a kulcsszavak, az Oracle-függvények, a Forms beépített hívásai és
  tulajdonságnevei.
- **A `:SYSTEM.*` változók.**

A formokat `F1`, `F2` stb. jelöli.

A hibák feltárásához a `FELMERES_HU.md` elküldése általában elég. Ha a JSON-t is csatolod, minden sor
és példa látszik.

**Ne küldd el:**

- a formonkénti almappákat: ezekben a teljes generált kód van, valódi nevekkel;
- a `PORTFOLIO_HU.md` és `portfolio.json` fájlt: ezekben formnevek vannak (az „Összesítő ZIP” ezeket is
  tartalmazza, ezért abból csak a két felmérésfájlt add tovább);
- a `--names` kapcsolóval vagy a „Nevekkel” gombbal készült változatot.

**Küldés előtt fusd át.** A hibaüzenetek szövegéből a nagybetűs neveket, a számokat, a kötött
változókat és az idézett szövegeket kiveszi a riport. Egy kisbetűs név vagy egy érték elvben mégis
átcsúszhat, ezért érdemes rákeresni a céges előtagokra és táblanév-töredékekre:

```
grep -i -n "ank_\|cegnev\|rendeles" FELMERES_HU.md
```

## Mi nem derül ki a felmérésből

- **A valódi adatbázis:** hogy a továbbított PL/SQL és SQL lefordul-e a sémán. Ezt a `verify-db` mutatja
  meg ([JAVITASOK_4_14_HU.md](JAVITASOK_4_14_HU.md)); a riportja valódi neveket tartalmaz.
- **A host-környezet:** hogy a generált Java és TypeScript lefordul-e a céges CL-lel és az Angular-projekttel.
- **A képernyő elrendezése**, vizuálisan.
- **A névtelenítéssel elveszett részletek:** például az, hogy egy `N3.N4` hívás céges keretrendszer-rutin
  vagy üzleti rutin. Ha egy ilyen gyakori, a keretrendszer előtagja felvehető a katalógusba (`framework_catalog`),
  és onnantól megnevezve marad.
