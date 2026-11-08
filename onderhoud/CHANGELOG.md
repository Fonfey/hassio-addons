# Changelog

## 1.4.0

### New / Nieuw
- The app is now available in English and follows the language of your Home Assistant: Dutch if Home Assistant is set to Dutch, English otherwise. The app is now called Maintenance. The sensor `sensor.onderhoud_punten` keeps its name and attributes, so existing automations keep working; the item titles in it follow the Home Assistant language.
- De app is nu ook in het Engels en volgt de taal van je Home Assistant: Nederlands als Home Assistant Nederlands is, anders Engels. De app heet nu Maintenance. De sensor `sensor.onderhoud_punten` houdt zijn naam en attributen, dus bestaande automations blijven werken; de titels erin volgen de taal van Home Assistant.

## 1.3.2

### Nieuw
- Na het verwijderen van een automation verschijnt de knop **Terugzetten in de Tijdmachine**, als je die app hebt. Die opent de Tijdmachine meteen bij de verwijderde automation. Bij het bevestigen staat erbij of je het kunt terugzetten.
- Heb je de Tijdmachine niet, dan staat er één keer een tip onderaan. Die kun je verbergen.

## 1.3.1

### Verbeterd
- Het lettertype wordt nu met de app meegeleverd. De pagina laadt daardoor niets meer van internet; alles draait lokaal.

## 1.3.0

### Verbeterd
- **Automations die elkaar tegenwerken**: Onderhoud kijkt nu ook in het logboek van de afgelopen 7 dagen of twee automations hetzelfde apparaat echt kort na elkaar de andere kant op zetten, bijvoorbeeld de airco om 06:00 aan en om 06:30 weer uit. Je ziet hoe vaak het gebeurde en wanneer, met een link naar de geschiedenis. Automations die hetzelfde apparaat aansturen maar elkaar niet tegenwerkten, blijven als aandachtspunt in de lijst.

## 1.2.0

### Nieuw
- **Controle na het opstarten**: zodra Home Assistant (opnieuw) is opgestart, controleert Onderhoud vanzelf. Het wacht twee minuten, zodat apparaten eerst weer verbinding hebben. Ook `sensor.onderhoud_punten` staat daardoor direct na een herstart weer goed.
- **Sensoren die al lang geen data geven**: geen statistieken meer, een waarde die dagen stilstaat, een meter die niet meer optelt, of al lang onbeschikbaar. Hoe lang is in te stellen met **Sensor zonder data na (dagen)**, standaard 7.
- **Statistieken met een probleem**, dezelfde die Home Assistant toont bij Ontwikkelhulpmiddelen, Statistieken. Statistieken van sensoren die niet meer bestaan kun je per regel verwijderen, na bevestiging.

## 1.1.1

### Verbeterd
- Onderaan staat nu een uitklapbare lijst **Genegeerd**: per regel zie je wat je hebt genegeerd en bij welk punt het hoort, met een eigen knop **Terugzetten**. **Alles terugzetten** blijft ook mogelijk.
- Genegeerde regels die inmiddels zijn opgelost, tellen niet meer mee.

## 1.1.0

### Nieuw
- Elke regel in een punt is nu los te bedienen. Klik op de naam om dat apparaat, die automation of de geschiedenis van die entiteit in Home Assistant te openen.
- **Negeren per regel**: houd je één sensor of apparaat bewust, negeer dan alleen die regel. De teller loopt mee terug.
- **Verwijderen per regel**, na bevestiging: entiteiten die nergens meer worden aangemaakt en automations die al lang uit staan.
- **Alles negeren** bij een punt negeert alleen de huidige regels; komt er later iets nieuws bij, dan zie je dat wel.

## 1.0.1

### Opgelost
- De app toonde voorbeeldgegevens in plaats van je eigen Home Assistant, omdat hij de toegangssleutel van Home Assistant niet vond. Hij leest die nu ook op de plek waar de app-omgeving hem bewaart.
- Lukt het toch niet om Home Assistant te bereiken, dan zie je een duidelijke foutmelding in plaats van voorbeeldgegevens.
- Lijsten tonen nu altijd alle onderdelen, ook bij veel updates, reparaties of oude entiteiten.

## 1.0.0

- Eerste versie van Onderhoud.
- Controleert back-ups, reparaties, updates, offline apparaten, batterijen, gestopte apps, automations (laden niet, nooit gestart, lang uit, sturen hetzelfde apparaat) en verweesde apparaten en entiteiten.
- Eén lijst op volgorde van belang, met uitleg, details en een knop naar de juiste plek in Home Assistant.
- Negeren wordt onthouden; een punt komt terug als er iets verandert.
- Sensor `sensor.onderhoud_punten` voor meldingen en dashboards.
