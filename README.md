# Jeroen's Home Assistant apps

Store-repository voor Home Assistant. Hier staan de app-beschrijvingen; de code staat in de eigen repository van elke app en wordt als kant-en-klaar image geleverd.

| App | Wat het doet | Code |
|---|---|---|
| [UniFi Dashboard](unifi_dashboard/DOCS.md) | Live overzicht van je UniFi-netwerk: netwerkkaart, internetverkeer, switchpoorten, wifi-radio's en clients. | [HomeAssistantUnifi](https://github.com/Fonfey/HomeAssistantUnifi) |
| [AdGuard DNS Dashboard](adguard_dns/DOCS.md) | Statistieken van je AdGuard DNS-account, querylog, blokkeren en deblokkeren, blokkeerlijsten en serverinstellingen. | [HomeAssistantAdguard](https://github.com/Fonfey/HomeAssistantAdguard) |

![UniFi Dashboard](docs/unifi.png)

![AdGuard DNS Dashboard](docs/adguard.png)

## Installeren in Home Assistant

1. Ga naar **Instellingen → Apps → App Store**.
2. Klik rechtsboven op **⋮ → Repositories** en voeg deze repository toe.
   Omdat de repository privé is, zet je er een GitHub-token in en eindig je de URL op `.git`:
   `https://GEBRUIKERSNAAM:TOKEN@github.com/Fonfey/hassio-addons.git`
   Gebruik een fine-grained token met alleen **Contents: Read-only** voor deze repository.
3. Beide apps staan nu onder **Jeroen's Home Assistant apps**. Installeer wat je wilt, vul op het tabblad **Configuratie** je API-key in en start de app.

De images staan op `ghcr.io/fonfey`. Zijn die privé, voeg dan in Home Assistant bij **App Store → ⋮ → Registers** een login toe voor `ghcr.io` met een GitHub-token (classic) met alleen het recht `read:packages`.
