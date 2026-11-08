# Fonfey's Home Assistant addons

![Home Assistant-apps: AdGuard DNS Dashboard, Maintenance, Notifications, Time Machine en UniFi Dashboard](docs/social-preview.png)

🇬🇧 [Read in English](README.md)

Store-repository voor Home Assistant. Elke app heeft een eigen map met de code, de documentatie en de changelog, en wordt als kant-en-klaar image geleverd. Elke app volgt de taal van je Home Assistant: Nederlands als Home Assistant Nederlands is, anders Engels.

## De apps

### <img src="adguard_dns/icon.png" width="32" height="32" align="absmiddle" alt=""> AdGuard DNS Dashboard

Statistieken van je AdGuard DNS-account, querylog, blokkeren en deblokkeren, blokkeerlijsten en serverinstellingen. [Documentatie](adguard_dns/DOCS.md) · code in [`adguard_dns/`](adguard_dns)

**Let op:** deze app is voor **AdGuard DNS**, de DNS-dienst in de cloud van AdGuard (adguard-dns.io). Hij werkt niet met **AdGuard Home**, de gratis software die je zelf installeert en draait; gebruik voor AdGuard Home de officiële AdGuard Home-integratie van Home Assistant.

![AdGuard DNS Dashboard: overzicht van verzoeken en blokkades (voorbeeldgegevens)](docs/adguard-dns-dashboard-nl.png)
*AdGuard DNS Dashboard met voorbeeldgegevens: het overzicht met verzoeken, blokkades, servers en apparaten.*

### <img src="maintenance/icon.png" width="32" height="32" align="absmiddle" alt=""> Maintenance (Onderhoud)

Eén lijst met wat aandacht nodig heeft in je Home Assistant: back-ups, offline apparaten, batterijen, sensoren zonder data, automations die elkaar tegenwerken of nooit draaien, en wat je kunt opruimen. Verwijder je een automation en heb je de Tijdmachine, dan zet je hem met één knop terug. [Documentatie](maintenance/DOCS.md) · code in [`maintenance/`](maintenance)

![Onderhoud: lijst met punten op volgorde van belang (voorbeeldgegevens)](docs/maintenance-nl.png)
*Onderhoud met voorbeeldgegevens: de punten op volgorde van belang, van Belangrijk via Nalopen naar Opruimen.*

### <img src="notifications/icon.png" width="32" height="32" align="absmiddle" alt=""> Notifications (Meldingen)

Zie welke meldingen Home Assistant verstuurt: wanneer, naar wie, door welke automation, en of er op een knop is gedrukt, de melding is weggeveegd of het versturen mislukte. [Documentatie](notifications/DOCS.md) · code in [`notifications/`](notifications)

![Meldingen: lijst met verstuurde meldingen en een overzicht per automation (voorbeeldgegevens)](docs/notifications-nl.png)
*Meldingen met voorbeeldgegevens: de lijst, de details en de automations die de meeste meldingen sturen.*

### <img src="time_machine/icon.png" width="32" height="32" align="absmiddle" alt=""> Time Machine (Tijdmachine)

Bewaart bij elke wijziging een versie van je automations, scripts, scènes, dashboards en helpers. Je ziet per onderdeel wat er veranderde, regel voor regel, en zet één onderdeel terug, ook als je het hebt verwijderd, bijvoorbeeld vanuit Onderhoud. Versies die je niet meer nodig hebt, gooi je een voor een weg. [Documentatie](time_machine/DOCS.md) · code in [`time_machine/`](time_machine)

![Tijdmachine: versies van een automation en het verschil met nu (voorbeeldgegevens)](docs/time-machine-nl.png)
*Tijdmachine met voorbeeldgegevens: links de onderdelen, rechts de versies van een automation en wat er veranderde.*

### <img src="unifi_dashboard/icon.png" width="32" height="32" align="absmiddle" alt=""> UniFi Dashboard

Live overzicht van je UniFi-netwerk: netwerkkaart, internetverkeer tot 24 uur terug, wat er op elke switchpoort zit, wifi-radio's en clients met SSID, VLAN en signaal. Met inzichten (netwerkcheck, dataverbruik per app, flows), het systeemlogboek, opslag en back-ups, en de afbeeldingen uit UniFi. [Documentatie](unifi_dashboard/DOCS.md) · code in [`unifi_dashboard/`](unifi_dashboard)

![UniFi Dashboard: overzicht met netwerkkaart en internetverkeer (voorbeeldgegevens)](docs/unifi-overview-nl.png)
*Overzicht: de netwerkkaart met je provider en het internetverkeer. Klik op Internet, een apparaat of een client voor de details.*

Met je eigen console zie je de echte afbeeldingen en iconen uit UniFi; de screenshot gebruikt voorbeeldgegevens.

## Installeren in Home Assistant

1. Ga naar **Instellingen → Apps → App Store**.
2. Klik rechtsboven op **⋮ → Repositories** en voeg deze URL toe:
   `https://github.com/Fonfey/hassio-addons`
3. De apps staan nu onder **Fonfey's Home Assistant addons**. Installeer wat je wilt en start de app. UniFi en AdGuard vragen op het tabblad **Configuratie** om een API-key.

De apps worden geleverd als kant-en-klare images op `ghcr.io/fonfey`. Je hebt geen token of extra login nodig.

Onderhoud, Tijdmachine en Meldingen draaien helemaal lokaal: ze praten alleen met je eigen Home Assistant en laden niets van internet, ook geen lettertypen. Het UniFi Dashboard praat daarnaast met je eigen UniFi-console, en het AdGuard DNS Dashboard met de AdGuard DNS-dienst.

## Voor ontwikkelaars

Elke app-map bevat alles van die app: `config.yaml`, de code, de `Dockerfile`, `DOCS.md`, `CHANGELOG.md` en de vertalingen. Verandert een map op `main`, dan bouwt GitHub Actions het image voor amd64 en aarch64 en zet het op `ghcr.io/fonfey`. Voor een nieuwe versie verhoog je `version` in `config.yaml` en vul je `CHANGELOG.md` aan; Home Assistant biedt dan de update aan.

De meeste apps draaien ook zonder Home Assistant, met voorbeeldgegevens: bijvoorbeeld `python3 maintenance/maintenance.py --demo`. Zet `?lang=en` of `?lang=nl` achter het adres om een taal te kiezen.

Valt onder de [MIT-licentie](LICENSE). De meegeleverde IBM Plex-lettertypen vallen onder de SIL Open Font License (zie `fonts/OFL.txt` in de app-mappen).
