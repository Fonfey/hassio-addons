# Changelog

## 1.0.8

### Verbeterd
- Zonder voorstellen staat er alleen nog de uitleg, zonder lege kaart en filter ernaast.

## 1.0.7

### Verbeterd
- Geen voorstellen? Dan zie je nu wat de app heeft bekeken: hoeveel automations, hoeveel lampen en schakelaars die al bedienen, en hoe vaak er iets met de hand werd geschakeld. In de log van de app staat per stap hoeveel er overbleef.

## 1.0.6

### Opgelost
- Het vangnet voor lampen die hun stand zonder afzender melden was te streng. Bij een huis met automations die de hele dag door lopen (bijvoorbeeld op stroomverbruik), viel daardoor alles weg. Het vangnet geldt nu alleen nog voor automations waarvan de app niet weet wat ze bedienen, en die niet de hele dag door lopen.

## 1.0.5

### Verbeterd
- Automations op basis van een blueprint, YAML-automations en -scripts: de app vraagt Home Assistant zelf wat ze bedienen, zodat die apparaten niet als gewoonte worden voorgesteld.
- Alleen gebruikers die bij een persoon horen tellen als "met de hand". Wat Node-RED, AppDaemon of een andere koppeling met een eigen gebruiker doet, telt niet meer.
- Iets wat elke dag op precies dezelfde minuut gebeurt zonder afzender, is een schema buiten Home Assistant (bijvoorbeeld de Hue-app, een Shelly of een timer) en wordt niet meer voorgesteld.
- Twee lampen die steeds binnen een paar seconden samen schakelen, zijn al gekoppeld (scène, knop of relais) en worden niet meer voorgesteld om samen te schakelen.

## 1.0.4

### Opgelost
- Lampen in een Zigbee-groep worden nu ook als groep herkend. Lampen die in dezelfde groep zitten, of een groep en een lamp daarin, worden niet meer voorgesteld om samen te schakelen: dat doen ze al.
- Alleen echte overgangen tellen nog, van aan naar uit en andersom. Een schakelaar die na een verbroken verbinding opnieuw uit meldt, telde eerst als schakelen.
- Het label Suggesties werd niet gezet. Automations die al gemaakt zijn krijgen het label alsnog.

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
