# UniFi Dashboard

[English](#english) · [Nederlands](#nederlands)

## English

A live overview of your UniFi network: network map, internet traffic, what is connected to every switch port, Wi-Fi radios and clients.

The dashboard follows the language of your Home Assistant: Dutch if Home Assistant is set to Dutch, English otherwise.

### Setting up

1. Open UniFi Network (your console's IP address in a browser), go to **Settings → Control Plane → Integrations** and click **Create API Key**. Copy the key right away; it is shown only once.
2. On the **Configuration** tab of this app, fill in:
   - `unifi_host`: IP address or hostname of your UniFi console (for example `192.168.1.1`)
   - `unifi_api_key`: the API key
   - `unifi_site`: optional, the name of the site (empty means the first site)
   - `refresh_seconds`: how often the data is refreshed
3. Start the app and open **UniFi** in the sidebar.

Without an API key the app runs in demo mode with sample data.

### What you see

Click a device or client to open its own page.

- **Overview**: the network map, from the internet via the gateway to switches and access points, and the internet traffic over 30 minutes, 6 hours or 24 hours. The history is kept after a restart.
- **Devices**: switch ports as a front panel (speed and PoE), Wi-Fi radios per access point, uptime, firmware, processor and memory. The page of a switch shows what is connected to each port.
- **Clients**: search, filter by Wi-Fi or cable, by network and by the device they are connected to, with the port number for wired clients. Per client you see the Wi-Fi network (SSID), VLAN, signal in dBm, band and channel. New clients from the past hour get a label.
- **Insights**: a network check (weak signal, poor Wi-Fi experience, channel interference, offline access points, backups and firmware), data usage per app over the last 24 hours, and flows (allowed, blocked and suspicious).
- **Log**: the system log of your console.

The page of the gateway also shows storage and the latest backups. Insights, the log and backups use parts of the API that Ubiquiti does not officially document; if your console does not provide something, you will see it under **Insights → Technical check**.

The app gets which device is on which port from the classic Network API, using the same API key. If your console does not allow this, you still see the ports, just without names.

The pictures of your devices come from Ubiquiti's public product catalogue (static.ui.com). The app fetches them once and stores them; without internet you see simple icons.

### Who may see the dashboard

Only Home Assistant administrators see the sidebar button. If you have several administrators and not all of them may see the dashboard, enter the usernames that may under **Allowed admins** on the **Configuration** tab. Leave the list empty to give all administrators access. The bottom of the dashboard shows "Signed in as" with your exact username.

## Nederlands

Live overzicht van je UniFi-netwerk: netwerkkaart, internetverkeer, wat er op elke switchpoort zit, wifi-radio's en clients.

Het dashboard volgt de taal van je Home Assistant: Nederlands als Home Assistant Nederlands is, anders Engels.

### Instellen

1. Open UniFi Network (het IP-adres van je console in de browser), ga naar **Instellingen → Control Plane → Integraties** en klik op **Create API Key**. Kopieer de key direct, je ziet hem maar één keer.
2. Vul op het tabblad **Configuratie** van deze app in:
   - `unifi_host`: IP-adres of hostnaam van je UniFi-console (bijv. `192.168.1.1`)
   - `unifi_api_key`: de API-key
   - `unifi_site`: optioneel, naam van de site (leeg = eerste site)
   - `refresh_seconds`: hoe vaak de gegevens ververst worden
3. Start de app en open **UniFi** in de zijbalk.

Zonder API-key draait de app in demomodus met voorbeeldgegevens.

### Wat je ziet

Klik op een apparaat of client om de eigen pagina te openen.

- **Overzicht**: de netwerkkaart, van internet via de gateway naar switches en toegangspunten, en het internetverkeer over 30 minuten, 6 uur of 24 uur. De geschiedenis blijft bewaard na een herstart.
- **Apparaten**: switchpoorten als frontpaneel (snelheid en PoE), wifi-radio's per toegangspunt, uptime, firmware, processor en geheugen. De pagina van een switch laat per poort zien wat erop is aangesloten.
- **Clients**: zoeken, filteren op wifi of kabel, op netwerk en op het apparaat waarmee ze verbonden zijn, met het poortnummer bij bekabelde clients. Per client zie je het wifi-netwerk (SSID), de VLAN, het signaal in dBm, de band en het kanaal. Nieuwe clients van het afgelopen uur krijgen een label.
- **Inzichten**: een netwerkcheck (zwak signaal, slechte wifi-ervaring, storing op kanalen, offline toegangspunten, back-ups en firmware), dataverbruik per app over de afgelopen 24 uur, en flows (toegestaan, geblokkeerd en verdacht).
- **Logboek**: het systeemlogboek van je console.

Op de pagina van de gateway staan ook de opslag en de laatste back-ups. Inzichten, het logboek en de back-ups gebruiken delen van de API die Ubiquiti niet officieel documenteert; geeft je console iets niet door, dan zie je dat bij **Inzichten → Technische controle**.

Welk apparaat op welke poort zit, haalt de app op met dezelfde API-key uit de klassieke Network-API. Staat je console dat niet toe, dan zie je de poorten nog wel, alleen zonder namen.

De afbeeldingen van je apparaten komen uit de openbare productcatalogus van Ubiquiti (static.ui.com). De app haalt ze één keer op en bewaart ze; zonder internet zie je eenvoudige iconen.

### Wie mag het dashboard zien

Alleen beheerders van Home Assistant zien de knop in de zijbalk. Heb je meerdere beheerders en mag niet iedereen het dashboard zien, vul dan op het tabblad **Configuratie** bij **Toegestane beheerders** de gebruikersnamen in die wel mogen. Laat je de lijst leeg, dan hebben alle beheerders toegang. Onderaan het dashboard staat "Ingelogd als" met je exacte gebruikersnaam.
