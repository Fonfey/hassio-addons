# Changelog

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
