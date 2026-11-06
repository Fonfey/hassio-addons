# Onderhoud

Eén overzicht van wat aandacht nodig heeft in je Home Assistant, op volgorde van belang. Wat in orde is, staat op één groene regel; de rest staat in een korte lijst die je kunt openklappen, oplossen of negeren.

## Wat Onderhoud controleert

| Controle | Waar het in de lijst komt |
| --- | --- |
| Back-ups: geen back-ups, de laatste is ouder dan twee dagen, of ze staan alleen op Home Assistant zelf | Belangrijk |
| Automations die niet laden | Belangrijk |
| Reparaties die Home Assistant meldt | Belangrijk of Nalopen |
| Updates | Nalopen |
| Offline apparaten: alle entiteiten zijn onbeschikbaar | Nalopen |
| Batterijen onder de ingestelde grens (telefoons en thuisbatterijen tellen niet mee) | Nalopen |
| Apps die vanzelf zouden starten maar uit staan, met een knop om ze te starten | Nalopen |
| Automations die hetzelfde apparaat aansturen | Nalopen |
| Apparaten en entiteiten die nergens meer worden aangemaakt | Opruimen |
| Automations die aan staan maar nooit zijn gestart, of al lang uit staan | Opruimen |

Onderhoud verandert niets vanzelf. Klik op een naam in een punt om dat apparaat, die automation of die entiteit in Home Assistant te openen. Direct iets uitvoeren kan alleen na bevestiging: een app **Starten**, of per regel **Verwijderen** bij entiteiten die nergens meer worden aangemaakt en automations die al lang uit staan.

**Negeren** kan per regel of voor een heel punt. Een genegeerde regel blijft weg; komt er een nieuw geval bij, bijvoorbeeld een extra offline apparaat, dan zie je dat wel.

## Sensor

Onderhoud maakt de sensor `sensor.onderhoud_punten` met het aantal open punten en de attributen `belangrijk`, `nalopen`, `opruimen` en `punten`. Gebruik hem voor een melding of een tegel op je dashboard. De sensor wordt na elke controle bijgewerkt, ook na een herstart van Home Assistant zodra de volgende controle draait.

## Instellingen

- **Controleren elke (minuten)**: standaard 60.
- **Batterij bijna leeg onder (%)**: standaard 20.
- **Automation lang uit na (dagen)**: standaard 30.
- **Toegestane beheerders (optioneel)**: alleen beheerders zien de knop in de zijbalk; vul gebruikersnamen in als niet elke beheerder Onderhoud mag zien.

Onderhoud heeft geen API-key nodig: de app krijgt toegang via Home Assistant zelf.
