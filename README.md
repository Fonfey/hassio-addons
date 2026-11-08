# Fonfey's Home Assistant addons

[English](#english) · [Nederlands](#nederlands)

## English

Store repository for Home Assistant. Each app has its own folder with the code, the documentation and the changelog, and is delivered as a ready-made image. Every app follows the language of your Home Assistant: Dutch if Home Assistant is set to Dutch, English otherwise.

### The apps

#### <img src="unifi_dashboard/icon.png" width="32" height="32" align="absmiddle" alt=""> UniFi Dashboard

A live overview of your UniFi network: network map, internet traffic up to 24 hours back, what is connected to every switch port, Wi-Fi radios and clients with SSID, VLAN and signal. With insights (network check, data usage per app, flows), the system log, storage and backups, and the pictures from UniFi. [Documentation](unifi_dashboard/DOCS.md) · code in [`unifi_dashboard/`](unifi_dashboard)

![UniFi Dashboard: overview with network map and internet traffic (sample data)](docs/unifi-overview-en.png)
*Overview: the network map with your provider and the internet traffic. Click Internet, a device or a client for details.*

![UniFi Dashboard: page of a switch showing what is connected to each port (sample data)](docs/unifi-switch-en.png)
*The page of a switch: what is connected to each port.*

![UniFi Dashboard: page of a client with network, VLAN and connection (sample data)](docs/unifi-client-en.png)
*The page of a client: network, VLAN and via which device and port.*

![UniFi Dashboard: insights with network check and data usage (sample data)](docs/unifi-insights-en.png)
*Insights: network check, data usage per app and flows.*

With your own console you see the real pictures and icons from UniFi; the screenshots use sample data.

#### <img src="adguard_dns/icon.png" width="32" height="32" align="absmiddle" alt=""> AdGuard DNS Dashboard

Statistics of your AdGuard DNS account, query log, blocking and unblocking, blocklists and server settings. [Documentation](adguard_dns/DOCS.md) · code in [`adguard_dns/`](adguard_dns)

![AdGuard DNS Dashboard: overview of requests and blocks (sample data)](docs/adguard-dns-dashboard-en.png)
*AdGuard DNS Dashboard with sample data: the overview with requests, blocks, servers and devices.*

#### <img src="maintenance/icon.png" width="32" height="32" align="absmiddle" alt=""> Maintenance

One list of what needs attention in your Home Assistant: backups, offline devices, batteries, sensors without data, automations that work against each other or never run, and what you can clean up. If you delete an automation and have Time Machine, you restore it with one button. [Documentation](maintenance/DOCS.md) · code in [`maintenance/`](maintenance)

![Maintenance: list of items in order of importance (sample data)](docs/maintenance-en.png)
*Maintenance with sample data: the items in order of importance, from Important via Check to Clean up.*

#### <img src="time_machine/icon.png" width="32" height="32" align="absmiddle" alt=""> Time Machine

Keeps a version of your automations, scripts, scenes, dashboards and helpers on every change. Per item you see what changed, line by line, and restore a single item, even if you deleted it, for example from Maintenance. Versions you no longer need can be discarded one by one. [Documentation](time_machine/DOCS.md) · code in [`time_machine/`](time_machine)

![Time Machine: versions of an automation and the difference with now (sample data)](docs/time-machine-en.png)
*Time Machine with sample data: the items on the left, the versions of an automation and what changed on the right.*

#### <img src="notifications/icon.png" width="32" height="32" align="absmiddle" alt=""> Notifications

See which notifications Home Assistant sends: when, to whom, from which automation, and whether a button was pressed, the notification was dismissed or sending failed. [Documentation](notifications/DOCS.md) · code in [`notifications/`](notifications)

![Notifications: list of sent notifications and an overview per automation (sample data)](docs/notifications-en.png)
*Notifications with sample data: the list, the details and the automations that send the most notifications.*

### Installing in Home Assistant

1. Go to **Settings → Apps → App Store**.
2. Click **⋮ → Repositories** in the top right and add this URL:
   `https://github.com/Fonfey/hassio-addons`
3. The apps now appear under **Fonfey's Home Assistant addons**. Install what you want and start the app. UniFi and AdGuard ask for an API key on the **Configuration** tab.

The apps are delivered as ready-made images on `ghcr.io/fonfey`. You do not need a token or extra login.

Maintenance, Time Machine and Notifications run completely locally: they only talk to your own Home Assistant and load nothing from the internet, not even fonts. The UniFi Dashboard also talks to your own UniFi console, and the AdGuard DNS Dashboard to the AdGuard DNS service.

### For developers

Every app folder contains everything for that app: `config.yaml`, the code, the `Dockerfile`, `DOCS.md`, `CHANGELOG.md` and the translations. When a folder changes on `main`, GitHub Actions builds the image for amd64 and aarch64 and publishes it on `ghcr.io/fonfey`. For a new version, raise `version` in `config.yaml` and add it to `CHANGELOG.md`; Home Assistant then offers the update.

Most apps also run without Home Assistant, with sample data: for example `python3 maintenance/maintenance.py --demo`. Add `?lang=en` or `?lang=nl` to the address to choose a language.

Licensed under the [MIT license](LICENSE). The bundled IBM Plex fonts are under the SIL Open Font License (see `fonts/OFL.txt` in the app folders).

## Nederlands

Store-repository voor Home Assistant. Elke app heeft een eigen map met de code, de documentatie en de changelog, en wordt als kant-en-klaar image geleverd. Elke app volgt de taal van je Home Assistant: Nederlands als Home Assistant Nederlands is, anders Engels.

### De apps

#### <img src="unifi_dashboard/icon.png" width="32" height="32" align="absmiddle" alt=""> UniFi Dashboard

Live overzicht van je UniFi-netwerk: netwerkkaart, internetverkeer tot 24 uur terug, wat er op elke switchpoort zit, wifi-radio's en clients met SSID, VLAN en signaal. Met inzichten (netwerkcheck, dataverbruik per app, flows), het systeemlogboek, opslag en back-ups, en de afbeeldingen uit UniFi. [Documentatie](unifi_dashboard/DOCS.md) · code in [`unifi_dashboard/`](unifi_dashboard)

![UniFi Dashboard: overzicht met netwerkkaart en internetverkeer (voorbeeldgegevens)](docs/unifi-overview-nl.png)
*Overzicht: de netwerkkaart met je provider en het internetverkeer. Klik op Internet, een apparaat of een client voor de details.*

![UniFi Dashboard: pagina van een switch met per poort wat erop is aangesloten (voorbeeldgegevens)](docs/unifi-switch-nl.png)
*De pagina van een switch: per poort wat erop zit.*

![UniFi Dashboard: pagina van een client met wifi-netwerk, signaal, VLAN en verbinding (voorbeeldgegevens)](docs/unifi-client-nl.png)
*De pagina van een client: wifi-netwerk, signaal in dBm, band, VLAN en via welk apparaat en welke poort.*

![UniFi Dashboard: inzichten met netwerkcheck, dataverbruik, flows en apps (voorbeeldgegevens)](docs/unifi-insights-nl.png)
*Inzichten: netwerkcheck, dataverbruik per app en flows.*

Met je eigen console zie je de echte afbeeldingen en iconen uit UniFi; de screenshots gebruiken voorbeeldgegevens.

#### <img src="adguard_dns/icon.png" width="32" height="32" align="absmiddle" alt=""> AdGuard DNS Dashboard

Statistieken van je AdGuard DNS-account, querylog, blokkeren en deblokkeren, blokkeerlijsten en serverinstellingen. [Documentatie](adguard_dns/DOCS.md) · code in [`adguard_dns/`](adguard_dns)

![AdGuard DNS Dashboard: overzicht van verzoeken en blokkades (voorbeeldgegevens)](docs/adguard-dns-dashboard-nl.png)
*AdGuard DNS Dashboard met voorbeeldgegevens: het overzicht met verzoeken, blokkades, servers en apparaten.*

#### <img src="maintenance/icon.png" width="32" height="32" align="absmiddle" alt=""> Maintenance (Onderhoud)

Eén lijst met wat aandacht nodig heeft in je Home Assistant: back-ups, offline apparaten, batterijen, sensoren zonder data, automations die elkaar tegenwerken of nooit draaien, en wat je kunt opruimen. Verwijder je een automation en heb je de Tijdmachine, dan zet je hem met één knop terug. [Documentatie](maintenance/DOCS.md) · code in [`maintenance/`](maintenance)

![Onderhoud: lijst met punten op volgorde van belang (voorbeeldgegevens)](docs/maintenance-nl.png)
*Onderhoud met voorbeeldgegevens: de punten op volgorde van belang, van Belangrijk via Nalopen naar Opruimen.*

#### <img src="time_machine/icon.png" width="32" height="32" align="absmiddle" alt=""> Time Machine (Tijdmachine)

Bewaart bij elke wijziging een versie van je automations, scripts, scènes, dashboards en helpers. Je ziet per onderdeel wat er veranderde, regel voor regel, en zet één onderdeel terug, ook als je het hebt verwijderd, bijvoorbeeld vanuit Onderhoud. Versies die je niet meer nodig hebt, gooi je een voor een weg. [Documentatie](time_machine/DOCS.md) · code in [`time_machine/`](time_machine)

![Tijdmachine: versies van een automation en het verschil met nu (voorbeeldgegevens)](docs/time-machine-nl.png)
*Tijdmachine met voorbeeldgegevens: links de onderdelen, rechts de versies van een automation en wat er veranderde.*

#### <img src="notifications/icon.png" width="32" height="32" align="absmiddle" alt=""> Notifications (Meldingen)

Zie welke meldingen Home Assistant verstuurt: wanneer, naar wie, door welke automation, en of er op een knop is gedrukt, de melding is weggeveegd of het versturen mislukte. [Documentatie](notifications/DOCS.md) · code in [`notifications/`](notifications)

![Meldingen: lijst met verstuurde meldingen en een overzicht per automation (voorbeeldgegevens)](docs/notifications-nl.png)
*Meldingen met voorbeeldgegevens: de lijst, de details en de automations die de meeste meldingen sturen.*

### Installeren in Home Assistant

1. Ga naar **Instellingen → Apps → App Store**.
2. Klik rechtsboven op **⋮ → Repositories** en voeg deze URL toe:
   `https://github.com/Fonfey/hassio-addons`
3. De apps staan nu onder **Fonfey's Home Assistant addons**. Installeer wat je wilt en start de app. UniFi en AdGuard vragen op het tabblad **Configuratie** om een API-key.

De apps worden geleverd als kant-en-klare images op `ghcr.io/fonfey`. Je hebt geen token of extra login nodig.

Onderhoud, Tijdmachine en Meldingen draaien helemaal lokaal: ze praten alleen met je eigen Home Assistant en laden niets van internet, ook geen lettertypen. Het UniFi Dashboard praat daarnaast met je eigen UniFi-console, en het AdGuard DNS Dashboard met de AdGuard DNS-dienst.

### Voor ontwikkelaars

Elke app-map bevat alles van die app: `config.yaml`, de code, de `Dockerfile`, `DOCS.md`, `CHANGELOG.md` en de vertalingen. Verandert een map op `main`, dan bouwt GitHub Actions het image voor amd64 en aarch64 en zet het op `ghcr.io/fonfey`. Voor een nieuwe versie verhoog je `version` in `config.yaml` en vul je `CHANGELOG.md` aan; Home Assistant biedt dan de update aan.

De meeste apps draaien ook zonder Home Assistant, met voorbeeldgegevens: bijvoorbeeld `python3 maintenance/maintenance.py --demo`. Zet `?lang=en` of `?lang=nl` achter het adres om een taal te kiezen.

Valt onder de [MIT-licentie](LICENSE). De meegeleverde IBM Plex-lettertypen vallen onder de SIL Open Font License (zie `fonts/OFL.txt` in de app-mappen).
