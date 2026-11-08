# Maintenance

[English](#english) · [Nederlands](#nederlands)

## English

One overview of what needs attention in your Home Assistant, in order of importance. What is fine is shown on one green line; the rest is in a short list that you can expand, fix or ignore.

The app follows the language of your Home Assistant: Dutch if Home Assistant is set to Dutch, English otherwise.

### What Maintenance checks

| Check | Where it appears in the list |
| --- | --- |
| Backups: no backups, the latest is older than two days, or they are only stored on Home Assistant itself | Important |
| Automations that do not load | Important |
| Repairs reported by Home Assistant | Important or Check |
| Updates | Check |
| Offline devices: all entities are unavailable | Check |
| Batteries below the set threshold (phones and home batteries are not counted) | Check |
| Apps that should start by themselves but are stopped, with a button to start them | Check |
| Automations that control the same device, and whether they actually worked against each other in the last 7 days (switching it the other way within 90 minutes) | Check |
| Sensors that have not given data for a long time: no statistics, a value stuck for days, a meter that no longer counts up, or unavailable for a long time | Check |
| Statistics with a problem, such as statistics of sensors that no longer exist | Clean up |
| Devices and entities that are no longer provided | Clean up |
| Automations that are on but have never run, or have been off for a long time | Clean up |

Maintenance does not change anything by itself. Click a name in an item to open that device, automation or entity in Home Assistant. Carrying something out directly is only possible after confirmation: **Start** an app, or per row **Delete** for entities that are no longer provided, automations that have been off for a long time and statistics of sensors that no longer exist.

If you also have the **Time Machine** app, you can restore an automation you delete here. After deleting, the **Restore in Time Machine** button appears at the bottom; it opens Time Machine straight at that automation. Without Time Machine, a tip appears once at the bottom, which you can hide.

**Ignore** works per row or for a whole item. An ignored row stays away; if a new case comes up, for example an extra offline device, you will see it.

### After a restart

Maintenance checks by itself as soon as Home Assistant has (re)started. It then waits two minutes, so devices can reconnect first and do not wrongly show up as offline.

### Sensor

Maintenance creates the sensor `sensor.onderhoud_punten` with the number of open items and the attributes `belangrijk` (important), `nalopen` (check), `opruimen` (clean up) and `punten` (the item titles, in the language of Home Assistant). Use it for a notification or a tile on your dashboard. The sensor is updated after every check, also right after a Home Assistant restart. The entity and attribute names stay the same in every language, so your automations keep working.

### Settings

- **Check every (minutes)**: 60 by default.
- **Battery low below (%)**: 20 by default.
- **Automation long off after (days)**: 30 by default.
- **Sensor without data after (days)**: 7 by default.
- **Allowed administrators (optional)**: only administrators see the sidebar item; enter usernames if not every administrator may see Maintenance.

### Local

The app runs completely locally: it only talks to your own Home Assistant and loads nothing from the internet, not even fonts.

## Nederlands

De app volgt de taal van je Home Assistant: Nederlands als Home Assistant Nederlands is, anders Engels. In de store heet de app Maintenance.

Eén overzicht van wat aandacht nodig heeft in je Home Assistant, op volgorde van belang. Wat in orde is, staat op één groene regel; de rest staat in een korte lijst die je kunt openklappen, oplossen of negeren.

### Wat Onderhoud controleert

| Controle | Waar het in de lijst komt |
| --- | --- |
| Back-ups: geen back-ups, de laatste is ouder dan twee dagen, of ze staan alleen op Home Assistant zelf | Belangrijk |
| Automations die niet laden | Belangrijk |
| Reparaties die Home Assistant meldt | Belangrijk of Nalopen |
| Updates | Nalopen |
| Offline apparaten: alle entiteiten zijn onbeschikbaar | Nalopen |
| Batterijen onder de ingestelde grens (telefoons en thuisbatterijen tellen niet mee) | Nalopen |
| Apps die vanzelf zouden starten maar uit staan, met een knop om ze te starten | Nalopen |
| Automations die hetzelfde apparaat aansturen, en of ze elkaar de afgelopen 7 dagen echt tegenwerkten (binnen 90 minuten de andere kant op) | Nalopen |
| Sensoren die al lang geen data geven: geen statistieken, een waarde die dagen stilstaat, een teller die niet meer optelt, of al lang onbeschikbaar | Nalopen |
| Statistieken met een probleem, zoals statistieken van sensoren die niet meer bestaan | Opruimen |
| Apparaten en entiteiten die nergens meer worden aangemaakt | Opruimen |
| Automations die aan staan maar nooit zijn gestart, of al lang uit staan | Opruimen |

Onderhoud verandert niets vanzelf. Klik op een naam in een punt om dat apparaat, die automation of die entiteit in Home Assistant te openen. Direct iets uitvoeren kan alleen na bevestiging: een app **Starten**, of per regel **Verwijderen** bij entiteiten die nergens meer worden aangemaakt, automations die al lang uit staan en statistieken van sensoren die niet meer bestaan.

Heb je ook de app **Time Machine** (Tijdmachine), dan kun je een automation die je hier verwijdert terugzetten. Na het verwijderen verschijnt onderin de knop **Terugzetten in de Tijdmachine**; die opent de Tijdmachine meteen bij die automation. Heb je de Tijdmachine niet, dan staat er één keer een tip onderaan, die je kunt verbergen.

**Negeren** kan per regel of voor een heel punt. Een genegeerde regel blijft weg; komt er een nieuw geval bij, bijvoorbeeld een extra offline apparaat, dan zie je dat wel.

### Na een herstart

Onderhoud controleert vanzelf zodra Home Assistant (opnieuw) is opgestart. Het wacht dan twee minuten, zodat apparaten eerst weer verbinding kunnen maken en niet onterecht als offline in de lijst komen.

### Sensor

Onderhoud maakt de sensor `sensor.onderhoud_punten` met het aantal open punten en de attributen `belangrijk`, `nalopen`, `opruimen` en `punten`. Gebruik hem voor een melding of een tegel op je dashboard. De sensor wordt na elke controle bijgewerkt, ook meteen na een herstart van Home Assistant.

### Instellingen

- **Controleren elke (minuten)**: standaard 60.
- **Batterij bijna leeg onder (%)**: standaard 20.
- **Automation lang uit na (dagen)**: standaard 30.
- **Sensor zonder data na (dagen)**: standaard 7.
- **Toegestane beheerders (optioneel)**: alleen beheerders zien de knop in de zijbalk; vul gebruikersnamen in als niet elke beheerder Onderhoud mag zien.

### Lokaal

De app draait helemaal lokaal: hij praat alleen met je eigen Home Assistant en laadt niets van internet, ook geen lettertypen.
