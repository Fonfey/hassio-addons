# Suggesties

Weet je niet goed wat je allemaal kunt automatiseren? Suggesties kijkt naar je eigen huis en stelt kant-en-klare automations voor. Jij kiest wat er gemaakt wordt. Je hoeft geen YAML te schrijven en geen blueprints te zoeken.

## Hoe het werkt

De app kijkt elke nacht (en als je op **Opnieuw zoeken** drukt) naar:

- **Wat je met de hand doet**, uit het logboek en de historie van Home Assistant. Zet je een lamp elke avond rond zonsondergang zelf aan, dan herkent de app dat.
- **Welke apparaten je hebt** en in welke ruimte ze zitten. Een raamsensor en een thermostaat in dezelfde ruimte, een watersensor of een stekker met verbruiksmeting op de wasmachine leveren meteen een suggestie op.
- **Je bestaande automations.** Doet een automation al hetzelfde, dan stelt de app het niet voor.

Wat telt als "met de hand"? Alleen wat een persoon in huis doet: via een schakelaar, de app of de spraakassistent. Niet wat een automation, script, scène, blueprint of groep doet, ook niet als die een hele ruimte, een Hue- of Zigbee-groep of een label bedient. Ook niet wat Node-RED, AppDaemon of een andere koppeling met een eigen gebruiker doet. Gebeurt iets elke dag op precies dezelfde minuut zonder afzender, dan gaat de app uit van een schema buiten Home Assistant (bijvoorbeeld in de Hue-app of een Shelly) en stelt hij het niet voor. Twee lampen die altijd binnen een paar seconden samen schakelen, zijn al gekoppeld en worden niet voorgesteld.

Alles wordt in je eigen Home Assistant berekend. Er gaat niets naar internet en er is geen AI voor nodig.

## Wat je ziet

- **Voorstellen**: per suggestie waarom hij wordt voorgesteld (bijvoorbeeld de tijden waarop je de lamp zelf aanzette), wat er gemaakt wordt in gewone taal (Als, En, Dan) en een paar instellingen. Druk op **Automation maken** en hij staat meteen aan. Kies **Niet nu** om hem een week weg te leggen, of **Nooit meer voorstellen**.
- **Gemaakt**: de automations die je via de app hebt gemaakt, hoe vaak ze liepen en of je ingreep. Zet je iets steeds meteen weer terug, dan stelt de app een aanpassing voor, zoals een kwartier later. De app verandert nooit zelf iets zonder dat jij op de knop drukt.

## Wat de app herkent

| Groep | Suggesties |
| --- | --- |
| Gewoontes | Vaste tijd of zonsondergang, twee lampen die altijd samen gaan, licht bij beweging, aan bij thuiskomst, vaste thermostaattijden |
| Apparaten | Melding als de wasmachine, droger of vaatwasser klaar is, verwarming lager bij een open raam, vriezer of koelkast te warm |
| Energie | Lampen uit als iedereen weg is, tv uit als niemand thuis is, lampen die 's nachts aanblijven |
| Veiligheid | Melding bij lekkage, alarm bij rook, deur te lang open, deur open terwijl niemand thuis is |

Voor gewoontes heeft de app ongeveer een week historie nodig. Home Assistant bewaart standaard 10 dagen. Suggesties op basis van je apparaten werken meteen. De app bedient nooit zelf sloten, alarm of garagedeuren; daar komen alleen meldingen voor.

## Wat er in Home Assistant gebeurt

- De app maakt gewone automations met het label **Suggesties**. Je vindt ze bij Instellingen, Automations en scènes, en je kunt ze daar zelf aanpassen of verwijderen.
- Voor de klaar-melding van een wasmachine, droger of vaatwasser maakt de app een hulpschakelaar (bijvoorbeeld *Wasmachine bezig*) die bijhoudt of er een programma draait.
- Meldingen gaan naar de telefoons met de Home Assistant-app; per suggestie kies je naar welke. Meldingen over lekkage, rook en een deur die opengaat terwijl niemand thuis is, komen ook door als de telefoon op stil staat.
- Heb je de Tijdmachine geïnstalleerd, dan bewaart die elke versie, ook na een aanpassing die de app voorstelt.

## Instellingen

- **Historie bekijken (dagen)**: standaard 10. Meer heeft alleen zin als je recorder langer bewaart.
- **Toegestane beheerders (optioneel)**: alleen beheerders zien de knop in de zijbalk; vul gebruikersnamen in als niet elke beheerder Suggesties mag zien.
