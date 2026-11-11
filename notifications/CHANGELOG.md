# Changelog

## 1.1.4

- After an update, the sidebar button now refreshes by itself, so a new name or icon shows right away. You no longer have to switch "Show in sidebar" off and on.
- Na een update werkt de knop in de zijbalk zichzelf bij, zodat een nieuwe naam of een nieuw icoon meteen zichtbaar is. Je hoeft "Toon in zijbalk" niet meer uit en aan te zetten.

## 1.1.3

- Safer file handling: files are only opened by names from a fixed list or from the app's own data, never from the text of a request. Found by code scanning; the existing checks already blocked misuse.
- Veiliger omgaan met bestanden: bestanden worden alleen geopend met namen uit een vaste lijst of uit de eigen gegevens van de app, nooit met de tekst uit een verzoek. Gevonden door code scanning; de bestaande controles hielden misbruik al tegen.

## 1.1.2

- The sidebar icon now matches the app icon: the bell.
- Het icoon in de zijbalk past nu bij het app-icoon: de bel.

## 1.1.1

- The code now lives in the store repository [Fonfey/hassio-addons](https://github.com/Fonfey/hassio-addons), and the image is built from there. Nothing changes in how the app works.
- De code staat nu in de store-repository [Fonfey/hassio-addons](https://github.com/Fonfey/hassio-addons) en het image wordt daar gebouwd. Aan de werking van de app verandert niets.

## 1.1.0

### New / Nieuw
- The app is now available in English and follows the language of your Home Assistant: Dutch if Home Assistant is set to Dutch, English otherwise. The app is now called Notifications.
- De app is nu ook in het Engels en volgt de taal van je Home Assistant: Nederlands als Home Assistant Nederlands is, anders Engels. De app heet nu Notifications.

## 1.0.1

### Verbeterd
- Het lettertype wordt nu met de app meegeleverd. De pagina laadt daardoor niets meer van internet; alles draait lokaal.

## 1.0.0

### Nieuw
- Eerste versie. Houdt bij welke meldingen Home Assistant verstuurt, naar telefoons, tablets, meldingsentiteiten en de zijbalk.
- Per melding: wanneer, naar wie, door welke automation of welk script, welke knoppen erbij zaten en wat ermee gebeurde (knop gekozen, weggeveegd op Android of mislukt).
- Overzicht van de automations die de meeste meldingen sturen, met een waarschuwing als er veel vlak na elkaar komen, en van meldingen die niet aankwamen.
