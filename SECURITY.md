# Security policy

🇳🇱 Nederlands staat onderaan.

## Supported versions

Only the latest version of each app is supported. Update the app in Home Assistant to get the newest version.

## What to report

These apps run inside Home Assistant, and some can change things: Maintenance can delete entities, automations and statistics after confirmation, Time Machine can restore items, and the AdGuard DNS Dashboard can block and unblock domains. Please report, for example:

- a way to reach an app or its actions without being a Home Assistant administrator, or as an administrator who is not on the app's list of allowed users;
- an action that changes or deletes something other than what was confirmed;
- an API key, token or other private data that ends up in logs, in the page or anywhere outside Home Assistant;
- a way for an app to load or send data to the internet when it should run locally (Maintenance, Time Machine and Notifications).

Ordinary bugs without a security risk can be reported as an [issue](../../issues).

## How to report

**Do not report security problems in a public issue.** Use **Report a vulnerability** on the [Security tab](../../security/advisories/new) of this repository. Only the maintainer can see your report. Mention which app and version it is about, how to reproduce the problem and what the possible impact is.

## What to expect

This is a hobby project maintained in spare time. You will get a first response within about a week. Confirmed problems are fixed as soon as possible and disclosed afterwards. Let me know if you want to be credited.

---

## Beveiligingsbeleid (Nederlands)

Alleen de nieuwste versie van elke app wordt ondersteund. Meld ook problemen waarbij een app iets anders verwijdert of wijzigt dan je hebt bevestigd, waarbij iemand zonder toestemming bij een app kan, of waarbij een API-key of andere privégegevens buiten Home Assistant terechtkomen.

**Meld beveiligingsproblemen niet in een openbaar issue.** Gebruik **Report a vulnerability** op het [tabblad Security](../../security/advisories/new). Je krijgt binnen ongeveer een week een reactie; dit is een hobbyproject dat in vrije tijd wordt onderhouden.
