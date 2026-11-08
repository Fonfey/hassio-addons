# Fonfey's Home Assistant addons

![Home Assistant apps: AdGuard DNS Dashboard, Maintenance, Notifications, Time Machine and UniFi Dashboard](docs/social-preview.png)

🇳🇱 [Lees in het Nederlands](README.nl.md)

Store repository for Home Assistant. Each app has its own folder with the code, the documentation and the changelog, and is delivered as a ready-made image. Every app follows the language of your Home Assistant: Dutch if Home Assistant is set to Dutch, English otherwise.

## The apps

### <img src="adguard_dns/icon.png" width="32" height="32" align="absmiddle" alt=""> AdGuard DNS Dashboard

Statistics of your AdGuard DNS account, query log, blocking and unblocking, blocklists and server settings. [Documentation](adguard_dns/DOCS.md) · code in [`adguard_dns/`](adguard_dns)

**Note:** this app is for **AdGuard DNS**, the cloud DNS service run by AdGuard (adguard-dns.io). It does not work with **AdGuard Home**, the free software you install and run yourself; for AdGuard Home, use the official AdGuard Home integration in Home Assistant.

![AdGuard DNS Dashboard: overview of requests and blocks (sample data)](docs/adguard-dns-dashboard-en.png)
*AdGuard DNS Dashboard with sample data: the overview with requests, blocks, servers and devices.*

### <img src="maintenance/icon.png" width="32" height="32" align="absmiddle" alt=""> Maintenance

One list of what needs attention in your Home Assistant: backups, offline devices, batteries, sensors without data, automations that work against each other or never run, and what you can clean up. If you delete an automation and have Time Machine, you restore it with one button. [Documentation](maintenance/DOCS.md) · code in [`maintenance/`](maintenance)

![Maintenance: list of items in order of importance (sample data)](docs/maintenance-en.png)
*Maintenance with sample data: the items in order of importance, from Important via Check to Clean up.*

### <img src="notifications/icon.png" width="32" height="32" align="absmiddle" alt=""> Notifications

See which notifications Home Assistant sends: when, to whom, from which automation, and whether a button was pressed, the notification was dismissed or sending failed. [Documentation](notifications/DOCS.md) · code in [`notifications/`](notifications)

![Notifications: list of sent notifications and an overview per automation (sample data)](docs/notifications-en.png)
*Notifications with sample data: the list, the details and the automations that send the most notifications.*

### <img src="time_machine/icon.png" width="32" height="32" align="absmiddle" alt=""> Time Machine

Keeps a version of your automations, scripts, scenes, dashboards and helpers on every change. Per item you see what changed, line by line, and restore a single item, even if you deleted it, for example from Maintenance. Versions you no longer need can be discarded one by one. [Documentation](time_machine/DOCS.md) · code in [`time_machine/`](time_machine)

![Time Machine: versions of an automation and the difference with now (sample data)](docs/time-machine-en.png)
*Time Machine with sample data: the items on the left, the versions of an automation and what changed on the right.*

### <img src="unifi_dashboard/icon.png" width="32" height="32" align="absmiddle" alt=""> UniFi Dashboard

A live overview of your UniFi network: network map, internet traffic up to 24 hours back, what is connected to every switch port, Wi-Fi radios and clients with SSID, VLAN and signal. With insights (network check, data usage per app, flows), the system log, storage and backups, and the pictures from UniFi. [Documentation](unifi_dashboard/DOCS.md) · code in [`unifi_dashboard/`](unifi_dashboard)

![UniFi Dashboard: overview with network map and internet traffic (sample data)](docs/unifi-overview-en.png)
*Overview: the network map with your provider and the internet traffic. Click Internet, a device or a client for details.*

With your own console you see the real pictures and icons from UniFi; the screenshot uses sample data.

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
