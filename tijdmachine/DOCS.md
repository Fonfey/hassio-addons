# Tijdmachine

Home Assistant bewaart geen eerdere versies van wat je in de editor maakt. Sla je per ongeluk iets verkeerds op of verwijder je een automation, dan is de oude versie weg, tenzij je een hele back-up terugzet. De Tijdmachine bewaart bij elke wijziging een versie, zodat je één onderdeel kunt terugzetten zonder de rest aan te raken.

## Wat wordt bewaard

| Onderdeel | Terugzetten |
| --- | --- |
| Automations | Ja, ook als kopie en ook na verwijderen |
| Scripts | Ja, ook als kopie en ook na verwijderen |
| Scènes | Ja, ook als kopie en ook na verwijderen |
| Dashboards | Ja, in zijn geheel. Na verwijderen: maak het dashboard eerst opnieuw aan met dezelfde URL |
| Helpers (schakelaars, getallen, keuzelijsten, tekst, datum en tijd, knoppen, tellers, timers, schema's) | Ja, ook als kopie en ook na verwijderen |

Alleen wat je in Home Assistant zelf maakt, wordt bewaard. Onderdelen uit YAML-bestanden of pakketten beheer je in die bestanden, dus die slaat de Tijdmachine over.

## Zo werkt het

- Kies links een onderdeel. Je ziet alle bewaarde versies, met wanneer en wat er veranderde.
- Kies een versie en je ziet het verschil met nu: rood is wat er was, groen wat er nu staat.
- **Deze versie terugzetten** zet het onderdeel terug, na bevestiging. Je huidige versie blijft bewaard, dus je kunt altijd terug.
- **Als kopie terugzetten** maakt een nieuw onderdeel met de oude inhoud, naast het origineel. Een kopie van een automation staat meteen uit, zodat hij niet dubbel draait; zet hem aan in Home Assistant als je hem wilt gebruiken.
- **Verwijderen** haalt een automation, script, scène of helper uit Home Assistant, na bevestiging. De versies blijven in de Tijdmachine, dus je kunt hem altijd terugzetten.
- **Definitief verwijderen** wist ook de bewaarde versies. Dat kan bij een kopie (die wordt dan meteen ook uit Home Assistant gehaald) en bij een onderdeel dat al verwijderd is. Dit kan niet ongedaan worden.

De Tijdmachine merkt een wijziging meteen als Home Assistant die meldt, en controleert daarnaast zelf elke paar minuten. De eerste versie van elk onderdeel is de versie op het moment dat je de app installeert.

Verwijder je een automation in de app **Onderhoud**, dan brengt de knop **Terugzetten in de Tijdmachine** je hier meteen naar die automation.

## Instellingen

- **Versies bewaren (dagen)**: standaard 30.
- **Altijd bewaren per onderdeel**: standaard de laatste 5, ook als ze ouder zijn.
- **Controleren elke (minuten)**: standaard 5.
- **Toegestane beheerders (optioneel)**: alleen beheerders zien de knop in de zijbalk; vul gebruikersnamen in als niet elke beheerder de Tijdmachine mag zien.

De versies staan in de opslag van de app en gaan dus mee in de back-ups van Home Assistant.
