# Changelog

## 1.0.3

### Opgelost
- Lampen in een Hue-ruimte of -zone, en lampen die via een Hue-scène aangaan, werden nog als gewoonte gezien terwijl een automation ze schakelt. De app kijkt nu door groepen en Hue-ruimtes heen tot op de losse lamp.
- Meldt een lamp zijn nieuwe stand zonder afzender, vlak nadat een automation liep, dan telt dat niet meer als iets wat je zelf deed.

## 1.0.2

### Opgelost
- Lampen die al door een automation worden geschakeld, werden toch als gewoonte voorgesteld. Dat gebeurde als de automation een hele ruimte, een apparaat, een label, een scène, een groep of een script bedient in plaats van de lamp zelf, en bij lampen die hun nieuwe stand later doorgeven (zoals Hue). De app herkent nu wat je automations echt bedienen, ook na een vertraging in de automation.
- Het aantal dagen klopte niet altijd ("10 van de 9 dagen").

## 1.0.1

### Verbeterd
- Het lettertype wordt nu met de app meegeleverd. De pagina laadt daardoor niets meer van internet; alles draait lokaal.

## 1.0.0

### Nieuw
- Eerste versie. Stelt automations voor op basis van je eigen huis: 15 soorten, verdeeld over gewoontes, apparaten, energie en veiligheid.
- Per suggestie zie je waarom hij wordt voorgesteld, wat er gemaakt wordt in gewone taal, en kun je een paar dingen aanpassen. Met één knop staat de automation in Home Assistant, met het label Suggesties.
- Bij Gemaakt kijkt de app mee: hoe vaak een automation liep en of je ingreep. Grijp je vaker in, dan stelt de app een aanpassing voor.
- Alles wordt lokaal berekend uit je eigen historie. Er gaat niets naar internet.
