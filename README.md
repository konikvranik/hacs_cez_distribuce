# ČEZ Distribuce – Home Assistant custom komponenta ⚡️

[![Release](https://img.shields.io/github/v/release/konikvranik/hacs_cez_distribuce)](https://github.com/konikvranik/hacs_cez_distribuce/releases)
[![Validate](https://github.com/konikvranik/hacs_cez_distribuce/actions/workflows/validate.yml/badge.svg)](https://github.com/konikvranik/hacs_cez_distribuce/actions/workflows/validate.yml)
[![Release on tag](https://github.com/konikvranik/hacs_cez_distribuce/actions/workflows/release_on_tag.yml/badge.svg)](https://github.com/konikvranik/hacs_cez_distribuce/actions/workflows/release_on_tag.yml)

Tato integrace se připojí k portálu ČEZ Distribuce a zveřejní do Home Assistanta:

- 🗓️ kalendář HDO se zobrazením aktuálního signálu a spínacích oken
- 🚧 kalendář plánovaných odstávek
- 📈 senzory s posledními stavy VT, NT a součtem (pro reporty/energie)

Integrace používá klienta `custom_components.cez_distribuce.rest_client.rest_client.CezDistribuceRestClient` a vyžaduje přístupové údaje k portálu.

⚠️ Upozornění: Jedná se o neoficiální integraci. Používejte na vlastní riziko.

## 🛠️ Instalace

1. Zkopírujte celý adresář `custom_components/cez_distribuce` do adresáře `custom_components` ve vaší instalaci Home Assistanta.
2. Restartujte Home Assistant.

## ⚙️ Konfigurace (configuration.yaml)

```yaml
cez_distribuce:
  username: "VASE_UZIVATELSKE_JMENO"
  password: "VASE_HESLO"
  # volitelně intervaly aktualizace (v minutách):
  # scan_interval_signals: 1440
  # scan_interval_outages: 1440
  # scan_interval_readings: 5

# Volitelně můžete povolit platformy také přes discovery, integrace je načte automaticky:
# calendar:
#   - platform: cez_distribuce
# sensor:
#   - platform: cez_distribuce
```

Po restartu integrace vytvoří:

- Kalendář "HDO – aktuální signál" – zobrazuje aktivní signál HDO a nadcházející intervaly podle dat HDO.
- Kalendář "ČEZ Odstávky" – obsahuje plánované odstávky pro vaše OM/EAN.
- Senzory:
  - "Poslední stav VT" (kWh)
  - "Poslední stav NT" (kWh)
  - "Poslední stav celkem" (kWh)
  - "Týden odečtu" – číslo týdne plánovaného odečtu (atributy: interval, sazba, distributor)
  - "Sazba distribuce" – aktuální sazba (např. `D57D`), atributy: hlavní jistič (A), počet fází, napěťová hladina
  - "HDO – rozpis" – čas posledního úspěšného stažení rozpisu HDO, atribut `signals` = rozpis z portálu (jen OM s HDO)
- Binární senzory:
  - "ČEZ Odstávka právě probíhá" – `on`, pokud právě teď běží odstávka (doplnění kalendáře plánovaných odstávek)
  - "ČEZ Smlouva aktivní" – `on`, pokud je distribuční smlouva ve stavu AKTIV

Poznámky:
- Pro každé dostupné odběrné místo (OM) s EAN integrace vytvoří samostatné entity (kalendáře, senzory i binární senzory).
- Nízký tarif je odvozen ze seznamu časů v odpovědi "Signály". Pokud portál HDO pro vaše OM není dostupný, senzor zůstane vypnutý a kalendář bude prázdný.
- Odstávky se načítají podle EAN vašeho OM.
- Senzory "Týden odečtu", "Sazba distribuce" a binární senzory čerpají data z detailu odběrného místa, který se načítá jednou při startu. Restartujte Home Assistant (nebo znovu načtěte integraci), aby se tyto hodnoty aktualizovaly — na rozdíl od odečtů a HDO se nemění průběžně.

## 🧩 Konfigurace přes UI (Config Flow)

Integraci lze přidat také přes Nastavení → Zařízení a služby → Přidat integraci → „ČEZ Distribuce“. Budete vyzváni k zadání uživatelského jména a hesla do portálu ČEZ Distribuce. Při ověřování přihlášení se integrace pokusí připojit a načíst základní informace. Pokud přihlášení selže, uvidíte chybu:
- „invalid_auth“ – nesprávné jméno nebo heslo
- „cannot_connect“ – služba je nedostupná nebo došlo k jiné chybě připojení

## ✅ Testy

Repozitář obsahuje tři vrstvy testů:

1. **Offline unit testy** (`tests/test_rest_client.py`, `tests/test_calendar.py`, `tests/test_cezhub.py`) – testují parsování odpovědí, retry/logiku REST klienta a vlastnosti entit. Používají `requests_mock`, nikdy nevolají reálný portál.
2. **HA-style testy** (`tests/test_config_flow.py`, `tests/test_init.py`) – běží proti reálné instanci Home Assistanta přes `pytest-homeassistant-custom-component` (config flow, setup/unload entry, vytváření entit). Klient je mockovaný.
3. **Integrační testy** (`tests/test_integration_live.py`, označené `@pytest.mark.integration`) – volají skutečné metody proti portálu ČEZ Distribuce. **Vyžadují** přihlašovací údaje; bez nich se automaticky přeskočí (skip), takže sada zůstává zelená i v CI bez secrets. Údaje se načítají v tomto pořadí:
   - env proměnné `CEZ_USERNAME` a `CEZ_PASSWORD` (mají přednost – vhodné pro CI),
   - nebo soubor `.env.local` v kořeni repozitáře (je v `.gitignore`, bezpečné pro lokální vývoj).

### Instalace závislostí

```bash
python -m pip install -r requirements_test.txt
```

> Pozn.: `requirements_test.txt` instaluje `pytest-homeassistant-custom-component==0.13.346`, což je pinovaný na `homeassistant==2026.7.2`. Při upgrade HA je třeba zvolit odpovídající verzi tohoto balíčku.

### Přihlašovací údaje pro integrační testy

Založte v kořeni repozitáře soubor `.env.local` (je v `.gitignore`, nikdy se necommitne):

```
CEZ_USERNAME=vas@email.cz
CEZ_PASSWORD=vaso-heslo
```

> ⚠️ **Portál ČEZ nyní vyžaduje e-mail jako přihlašovací jméno.** `CEZ_USERNAME` proto musí být plný e-mail (např. `petr@vranik.name`), nikoli staré uživatelské jméno. Jinak přihlášení selže s chybou *invalid credentials*.

Případně údaje předejte přes env proměnné (mají přednost před `.env.local`):

```bash
export CEZ_USERNAME=vas@email.cz
export CEZ_PASSWORD=vaso-heslo
```

### Spuštění

```bash
# pouze offline testy (výchozí pro CI):
pytest -q -m "not integration"

# kompletní sada (integration se skipne bez údajů):
pytest -q

# integrační testy proti reálnému portálu (vypíše strukturu odpovědí):
pytest -q -m integration -s
```

> Pozn.: Offline a HA-style testy neprovádějí žádné reálné přihlášení. Integrační testy zasahují na reálný portál ČEZ Distribuce – spouštějte je jen s vlastním účtem a na vlastní zodpovědnost.


## 📚 Přehled projektu

Tato neoficiální integrace pro Home Assistant přináší do vašeho chytrého domu informace z portálu ČEZ Distribuce. Zaměřuje se na praktické funkce pro automatizace:
- dostupnost nízkého tarifu (NT, HDO) pro řízení spotřebičů,
- kalendář HDO pro přehled spínacích oken,
- kalendář plánovaných odstávek,
- poslední stavové hodnoty elektroměru (VT/NT/celkem) pro reporty a energetické přehledy.

Architektura integrace:
- REST klient pro přihlášení a čtení dat z webového portálu ČEZ Distribuce (synchronní volání přes requests),
- CezHub, který zajišťuje přihlášení, jednorázové načtení statických dat a periodické aktualizace vybraných částí,
- platformy Home Assistanta: calendar a sensor.

Upozornění: Integrace není oficiální. Portál se může měnit a způsobit dočasné výpadky funkčnosti. Používejte na vlastní riziko.

## 📋 Požadavky
- Home Assistant (doporučeno 2023.0+).
- Aktivní účet k portálu ČEZ Distribuce s přístupem k vašim OM/EAN.
- Stabilní internetové připojení ze strany Home Assistanta.

## 🛒 Instalace přes HACS
Je-li ve vašem HA nainstalován HACS:
1. V HACS otevřete Integrations → menu vpravo nahoře → Custom repositories.
2. Přidejte URL tohoto repozitáře jako „Integration“.
3. Vyhledejte „ČEZ Distribuce“ a nainstalujte.
4. Restartujte Home Assistant.
5. Přidejte integraci přes UI (viz níže).

## 📦 Manuální instalace (shrnutí)
- Zkopírujte adresář `custom_components/cez_distribuce` do stejnojmenné složky ve vaší instalaci Home Assistanta.
- Restartujte Home Assistant.

## 🧩 Konfigurace přes UI (Config Flow) – podrobněji
Integraci přidejte v Nastavení → Zařízení a služby → Přidat integraci → „ČEZ Distribuce“. Zadejte uživatelské jméno a heslo do portálu ČEZ Distribuce. Při ověření přihlášení se vyzkouší přístup k portálu.
- Chyba „invalid_auth“ značí chybné údaje.
- Chyba „cannot_connect“ značí problém s dostupností služby či připojením.

Poznámka k intervalům aktualizace: Při přidání přes UI se nastaví výchozí intervaly a v současné verzi je nelze v UI měnit. Pro vlastní intervaly použijte konfiguraci v configuration.yaml (viz níže). Výchozí hodnoty jsou:
- signály (HDO): 1440 min
- odstávky: 1440 min
- odečty: 5 min

## ⚙️ Konfigurace v configuration.yaml (alternativa)
Základní příklad je uveden výše v této stránce. Volitelné klíče pro intervaly:
- `scan_interval_signals` – v minutách
- `scan_interval_outages` – v minutách
- `scan_interval_readings` – v minutách

## 🧩 Vytvářené entity – detail
- Kalendář „HDO – aktuální signál“
  - Události s obdobími dle platných HDO signálů pro vaše OM (EAN).
- Kalendář „ČEZ Odstávky“
  - Naplánované odstávky dle EAN vašeho OM.
- Senzory odečtů:
  - Poslední stav VT (kWh), Poslední stav NT (kWh), Poslední stav celkem (kWh).
  - Atributy: `serial` (sériové číslo elektroměru), `date` (datum odečtu), `reason` (důvod), `datum_posledniho_odectu`.
- „Týden odečtu“
  - Stav = číslo týdne plánovaného odečtu distribucí. Atributy: `interval` (např. `23 (01.06.2026 - 07.06.2026)`), `sazba`, `distributor`, `cislo_smlouvy`.
  - Užitečné pro automatizaci „připomenout odečet“ v daném týdnu.
- „Sazba distribuce“
  - Stav = aktuální distribuční sazba (např. `D57D`). Atributy: `hlavni_jistic` (A), `pocet_fazi`, `typ_mereni`, `typ_odberu`, `platnost_od`, `napetova_hladina`, `jistic_charakteristika`.
  - Užitečné při plánování instalace (EV, přímotopy, fotovoltaika) — ukazuje rezervu jističe.
- „HDO – rozpis“ (jen OM s HDO)
  - Stav = čas posledního úspěšného stažení rozpisu (`device_class: timestamp`). Při chybě zůstává poslední dobrý rozpis a čas se neposune, takže zastaralá data se dají snadno poznat.
  - Atribut `signals` = rozpis z portálu na následující dny: seznam položek `datum`, `den`, `signal`, `casy` (např. `00:00-10:00; 11:00-12:00`). Hodí se pro vlastní šablony, např. tarif po hodinách pro graf cen.
- Binární senzory:
  - „ČEZ Odstávka právě probíhá“ (`device_class: problem`) – `on`, pokud `probihajiciOdstavka`. Komplement kalendáře (ten ukazuje budoucnost, tento senzor reálný stav „teď“).
  - „ČEZ Smlouva aktivní“ – `on`, pokud `stavSmlouvyDistribuce == "AKTIV"`. Atribut `stav_text` (např. „Aktivní“).

Poznámky a omezení:
- Pro každé dostupné odběrné místo (OM) s EAN integrace vytvoří samostatné kalendáře, senzory i binární senzory. Pokud OM nemá dostupný EAN, může být přeskočeno.
- Pokud pro OM nejsou dostupné HDO signály, kalendář HDO bude prázdný.
- V případě dočasného výpadku portálu se integrace snaží použít poslední známá data.
- Senzory „Týden odečtu“, „Sazba distribuce“ a oba binární senzory se neaktualizují průběžně (čerpají z detailu OM načteného při startu). Pro obnovu restartujte HA nebo znovu načtěte integraci.

## 💡 Příklady použití

Pozn.: Názvy a entity_id kalendářů se mohou v instalaci lišit (např. podle EAN nebo aliasu). Přesný entity_id zjistíte v UI Home Assistanta na kartě entity.

1) Zapnutí bojleru na začátku okna HDO:

```yaml
alias: Zapnout bojler při startu HDO
trigger:
  - platform: calendar
    entity_id: calendar.hdo_aktualni_signal
    event: start
action:
  - service: switch.turn_on
    target:
      entity_id: switch.bojler
```

2) Vypnutí bojleru na konci okna HDO:

```yaml
alias: Vypnout bojler při konci HDO
trigger:
  - platform: calendar
    entity_id: calendar.hdo_aktualni_signal
    event: end
action:
  - service: switch.turn_off
    target:
      entity_id: switch.bojler
```

3) Zobrazení příštího okna z kalendáře v šabloně:

```yaml
sensor:
  - platform: template
    sensors:
      pristi_hdo_okno:
        friendly_name: "Příští HDO okno"
        value_template: >-
          {{ state_attr('calendar.hdo_aktualni_signal','start_time') }} – {{ state_attr('calendar.hdo_aktualni_signal','end_time') }}
```

## 🧰 Řešení problémů
- Ověřte, že se přihlásíte do portálu ČEZ Distribuce se stejnými údaji v běžném prohlížeči.
- V Nastavení → Systém → Záznamy zapněte ladicí logování pro komponentu, např. v configuration.yaml:

```yaml
logger:
  default: warning
  logs:
    custom_components.cez_distribuce: debug
```

- Dočasné chyby portálu mohou vést k „cannot_connect“. Zkuste to znovu později.
- Po změně hesla odeberte a znovu přidejte integraci.

## 🔒 Soukromí a zabezpečení
- Přihlašovací údaje jsou uloženy v bezpečném úložišti Home Assistanta a používány pouze pro načítání údajů z portálu.
- Integrace neodesílá data třetím stranám mimo ČEZ Distribuce.

## 🧑‍💻 Vývoj a přispívání
- Testy spustíte lokálně pomocí `pytest -q` (viz kapitola Testy výše). Pro CI stačí `pytest -q -m "not integration"`.
- Pull Requesty s vylepšeními a opravami jsou vítány. Prosíme o stručný popis změn a doplnění testů, pokud je to možné.

## 🐛 Nahlášení chyb a návrhy
- Otevírejte prosím issues v GitHub repozitáři s co nejpřesnějším popisem problému, výpisem z logu a verzí HA.

## 📄 Licence
Distribuováno pod licencí uvedenou v souboru LICENSE tohoto repozitáře.

## 🙏 Poděkování
Díky komunitě Home Assistant a projektu HACS za nástroje a inspiraci.
