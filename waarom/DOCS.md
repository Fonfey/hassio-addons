# Waarom

Waarom ging de airco om zes uur uit? Wie deed de lamp aan? Home Assistant onthoudt bij elke wijziging wat de aanleiding was, maar dat zoek je normaal bij elkaar in het logboek en de traces. Waarom zet het per apparaat in één tijdlijn.

## Zo werkt het

1. Zoek een apparaat op naam, of kies er een bij **Onlangs geschakeld**.
2. Kies **Vandaag**, **Gisteren** of **7 dagen**.
3. Elke regel laat zien wat er veranderde en door wie of wat. Klik op een regel voor de aanleiding en, bij een automation of script, een knop naar de trace.

| Soort | Betekenis |
| --- | --- |
| Automation | Een automation schakelde het apparaat. Je ziet welke en wat de trigger was. |
| Script | Een script schakelde het apparaat. |
| Persoon | Iemand deed het in Home Assistant, bijvoorbeeld op een dashboard of in de app. |
| Integratie | Een integratie of dienst gaf de opdracht, zonder automation of persoon ertussen. |
| Ander apparaat | De wijziging volgde op een wijziging van een ander apparaat of een andere entiteit. |
| Apparaat zelf | Niet via Home Assistant: een fysieke schakelaar, een afstandsbediening, de app van de fabrikant, of het apparaat dat opnieuw verbinding maakte. |

Het logboek bewaart wijzigingen van de status, zoals aan, uit of een andere stand. Een andere temperatuur of helderheid zonder statuswijziging staat er niet in. Hoe ver je terug kunt kijken, hangt af van hoe lang Home Assistant je geschiedenis bewaart (standaard 10 dagen).

## Instellingen

- **Toegestane beheerders (optioneel)**: alleen beheerders zien de knop in de zijbalk; vul gebruikersnamen in als niet elke beheerder Waarom mag zien.

Wil je weten of twee automations elkaar tegenwerken? Dat controleert de app Onderhoud.
