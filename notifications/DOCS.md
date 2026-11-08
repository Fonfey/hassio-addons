# Notifications

[English](#english) · [Nederlands](#nederlands)

## English

Getting too many notifications, or missing one? Home Assistant does not keep track of the notifications it sends. This app does: from the moment you install it, you see every notification, who received it, which automation sent it and what happened to it.

The app follows the language of your Home Assistant: Dutch if Home Assistant is set to Dutch, English otherwise.

### What you see

- **The list**: every notification with time, title, text, recipient and the automation or script that sent it. Filter by recipient, by text or by what happened.
- **Details**: click a notification to see its buttons, the result and a link to the automation's trace.
- **Most notifications**: which automations send the most notifications. If many arrive in quick succession, you get a warning.
- **Did not arrive**: notifications to a notification service that no longer exists, for example an old phone, with the automation that still uses it.

| Result | Meaning |
| --- | --- |
| Sent | Home Assistant sent the notification. |
| Button chosen | A button in the notification was pressed on the phone. |
| Dismissed | The notification was swiped away on an Android phone. |
| Failed | Home Assistant could not send the notification. |

Home Assistant does not know whether a notification was read. The app does not send anything itself; it only watches.

### Settings

- **Keep notifications (days)**: 30 by default.
- **Allowed administrators (optional)**: only administrators see the sidebar item; enter usernames if not every administrator may see Notifications.

### Local

The app runs completely locally: it only talks to your own Home Assistant and loads nothing from the internet, not even fonts.

## Nederlands

Krijg je te veel meldingen, of juist een melding niet? Home Assistant houdt niet bij welke meldingen het heeft verstuurd. Deze app wel: vanaf de installatie zie je elke melding, met wie hem kreeg, welke automation hem stuurde en wat ermee gebeurde.

### Wat je ziet

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

### Instellingen

- **Meldingen bewaren (dagen)**: standaard 30.
- **Toegestane beheerders (optioneel)**: alleen beheerders zien de knop in de zijbalk; vul gebruikersnamen in als niet elke beheerder Meldingen mag zien.

### Lokaal

De app draait helemaal lokaal: hij praat alleen met je eigen Home Assistant en laadt niets van internet, ook geen lettertypen.
