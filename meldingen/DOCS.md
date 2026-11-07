# Meldingen

Krijg je te veel meldingen, of juist een melding niet? Home Assistant houdt niet bij welke meldingen het heeft verstuurd. Deze app wel: vanaf de installatie zie je elke melding, met wie hem kreeg, welke automation hem stuurde en wat ermee gebeurde.

## Wat je ziet

- **De lijst**: elke melding met tijd, titel, tekst, ontvanger en de automation of het script dat hem stuurde. Filter op ontvanger, op tekst of op wat ermee gebeurde.
- **Details**: klik op een melding voor de knoppen die erbij zaten, het resultaat en een knop naar de trace van de automation.
- **Meeste meldingen**: welke automations de meeste meldingen sturen. Komen er veel vlak na elkaar, dan zie je een waarschuwing.
- **Kwamen niet aan**: meldingen naar een meldingsdienst die niet (meer) bestaat, bijvoorbeeld een oude telefoon, met de automation die hem nog gebruikt.

| Resultaat | Betekenis |
| --- | --- |
| Verstuurd | Home Assistant heeft de melding verstuurd. |
| Knop gekozen | Op de telefoon is op een knop in de melding gedrukt. |
| Weggeveegd | De melding is op een Android-telefoon weggeveegd. |
| Mislukt | Home Assistant kon de melding niet versturen. |

Of een melding is gelezen, weet Home Assistant niet. De app verstuurt zelf niets; hij kijkt alleen mee.

## Instellingen

- **Meldingen bewaren (dagen)**: standaard 30.
- **Toegestane beheerders (optioneel)**: alleen beheerders zien de knop in de zijbalk; vul gebruikersnamen in als niet elke beheerder Meldingen mag zien.

## Lokaal

De app draait helemaal lokaal: hij praat alleen met je eigen Home Assistant en laadt niets van internet, ook geen lettertypen.
