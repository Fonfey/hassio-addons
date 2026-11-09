# Fonfey's Home Assistant addons

![Home Assistant apps: AdGuard DNS Dashboard, Maintenance, Notifications, Time Machine and UniFi Dashboard](docs/social-preview.png)

🇳🇱 [Lees in het Nederlands](README.nl.md)

Store repository for Home Assistant. Each app has its own folder with the code, the documentation and the changelog, and is delivered as a ready-made image. Every app follows the language of your Home Assistant: Dutch if Home Assistant is set to Dutch, English otherwise.

## The apps

### <img src="adguard_dns/icon.png" width="32" height="32" align="absmiddle" alt=""> AdGuard DNS Dashboard

Statistics of your AdGuard DNS account, query log, blocking and unblocking, blocklists and server settings. [Documentation](adguard_dns/DOCS.md) · code in [`adguard_dns/`](adguard_dns)

**Note:** this app is for **AdGuard DNS**, the cloud DNS service run by AdGuard (adguard-dns.io). It does not work with **AdGuard Home**, the free software you install and run yourself; for AdGuard Home, use the official AdGuard Home integration in Home Assistant.

<img width="1046" height="571" alt="image" src="https://github.com/user-attachments/assets/58497767-21b4-4cfc-80bc-e3d82154c687" />
<img width="1055" height="523" alt="image" src="https://github.com/user-attachments/assets/d1961a7f-eace-49da-932c-1fa25f783c41" />
<img width="1055" height="427" alt="image" src="https://github.com/user-attachments/assets/1acc6074-78a6-4a11-9cbf-0dfd472e78c3" />
*AdGuard DNS Dashboard with the overview with requests, blocks, servers and devices.*

### <img src="maintenance/icon.png" width="32" height="32" align="absmiddle" alt=""> Maintenance

One list of what needs attention in your Home Assistant: backups, offline devices, batteries, sensors without data, automations that work against each other or never run, and what you can clean up. If you delete an automation and have Time Machine, you restore it with one button. [Documentation](maintenance/DOCS.md) · code in [`maintenance/`](maintenance)

<img width="687" height="305" alt="image" src="https://github.com/user-attachments/assets/9baff171-ecbf-4d3b-87ca-642462cbe611" />
<img width="683" height="203" alt="image" src="https://github.com/user-attachments/assets/724ccec7-1a21-4e40-9dea-f4507989f30f" />
<img width="688" height="186" alt="image" src="https://github.com/user-attachments/assets/64129f2a-5f82-4792-84da-93304948bf65" />



*Maintenance with sample data: the items in order of importance, from Important via Check to Clean up.*

### <img src="notifications/icon.png" width="32" height="32" align="absmiddle" alt=""> Notifications

See which notifications Home Assistant sends: when, to whom, from which automation, and whether a button was pressed, the notification was dismissed or sending failed. [Documentation](notifications/DOCS.md) · code in [`notifications/`](notifications)

<img width="1025" height="766" alt="image" src="https://github.com/user-attachments/assets/3e75aba5-4dbb-4605-b845-4954acce1208" />
*Notifications with sample data: the list, the details and the automations that send the most notifications.*

### <img src="time_machine/icon.png" width="32" height="32" align="absmiddle" alt=""> Time Machine

Keeps a version of your automations, scripts, scenes, dashboards and helpers on every change. Per item you see what changed, line by line, and restore a single item, even if you deleted it, for example from Maintenance. Versions you no longer need can be discarded one by one. [Documentation](time_machine/DOCS.md) · code in [`time_machine/`](time_machine)

<img width="1027" height="658" alt="image" src="https://github.com/user-attachments/assets/41731250-8312-4e27-9dbd-17b6f7492365" />
<img width="1026" height="815" alt="image" src="https://github.com/user-attachments/assets/63412184-3b3c-4395-bec0-b4802771b156" />
*Time Machine with sample data: the items on the left, the versions of an automation and what changed on the right.*

### <img src="unifi_dashboard/icon.png" width="32" height="32" align="absmiddle" alt=""> UniFi Dashboard

A live overview of your UniFi network: network map, internet traffic up to 24 hours back, what is connected to every switch port, Wi-Fi radios and clients with SSID, VLAN and signal. With insights (network check, data usage per app, flows), the system log, storage and backups, and the pictures from UniFi. [Documentation](unifi_dashboard/DOCS.md) · code in [`unifi_dashboard/`](unifi_dashboard)

<img width="1172" height="683" alt="image" src="https://github.com/user-attachments/assets/9ac1a019-38e6-48f7-ae77-9ffd643171b6" />
<img width="1166" height="649" alt="image" src="https://github.com/user-attachments/assets/524ed98d-fccf-428f-9e7e-051b553013a6" />
<img width="850" height="269" alt="image" src="https://github.com/user-attachments/assets/a19149cf-6ea2-4647-9c4d-b858c9925f44" />

*Overview: the network map with your provider and the internet traffic. Click Internet, a device or a client for details.*

## Installing in Home Assistant

1. Go to **Settings → Apps → App Store**.
2. Click **⋮ → Repositories** in the top right and add this URL:
   `https://github.com/Fonfey/hassio-addons`
3. The apps now appear under **Fonfey's Home Assistant addons**. Install what you want and start the app. UniFi and AdGuard ask for an API key on the **Configuration** tab.

The apps are delivered as ready-made images on `ghcr.io/fonfey`. You do not need a token or extra login.

Maintenance, Time Machine and Notifications run completely locally: they only talk to your own Home Assistant and load nothing from the internet, not even fonts. The UniFi Dashboard also talks to your own UniFi console, and the AdGuard DNS Dashboard to the AdGuard DNS service.

## For developers

Every app folder contains everything for that app: `config.yaml`, the code, the `Dockerfile`, `DOCS.md`, `CHANGELOG.md` and the translations. When a folder changes on `main`, GitHub Actions builds the image for amd64 and aarch64 and publishes it on `ghcr.io/fonfey`. For a new version, raise `version` in `config.yaml` and add it to `CHANGELOG.md`; Home Assistant then offers the update.

Most apps also run without Home Assistant, with sample data: for example `python3 maintenance/maintenance.py --demo`. Add `?lang=en` or `?lang=nl` to the address to choose a language.

Licensed under the [MIT license](LICENSE). The bundled IBM Plex fonts are under the SIL Open Font License (see `fonts/OFL.txt` in the app folders).
