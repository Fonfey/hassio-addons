# Changelog

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
