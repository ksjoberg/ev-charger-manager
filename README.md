# EV Charger Manager

A Home Assistant custom integration that controls an EV charge box by adjusting its allowed charge current based on one of three selectable strategies: charge as fast as possible, use only excess solar power, or minimize electricity cost using Nordpool spot prices.

## Features

- **Three charge modes** selectable from a standard HA `select` entity:
  - **ASAP** – always charges at the configured maximum current
  - **Solar excess** – sets the current proportional to estimated (or measured) solar surplus; pauses when there is insufficient excess
  - **Minimize cost** – charges only during the cheapest *N* hours of the day based on Nordpool spot prices; pauses during expensive hours
- **Solar estimation** from sun geometry (latitude/longitude) and weather conditions — no extra hardware required. When a grid-power sensor is connected, measured solar export is used instead of the estimate.
- **Nordpool compatibility** — works with both the unofficial [Nordpool HACS integration](https://github.com/custom-components/nordpool) and the official Nordpool integration; any sensor whose state is the current spot price per kWh is accepted.
- **Runtime-adjustable current limits** via `number` entities — no need to re-run the config flow to change the operating range.
- All decisions update every 5 minutes. Changing the charge mode triggers an immediate update.

## Entities created

| Entity | Type | Description |
|---|---|---|
| Charge mode | `select` | Active charging strategy |
| Minimum charge current | `number` | Lower current bound (A) |
| Maximum charge current | `number` | Upper current bound (A) |
| Cheap hours needed | `number` | Target cheap-hour count for Minimize Cost mode |
| Applied charge current | `sensor` | Current being written to the charger |
| Estimated solar power | `sensor` | Estimated PV output in kW |
| Current spot price | `sensor` | Price read from the Nordpool sensor |
| Charge decision reason | `sensor` | Human-readable explanation of the last decision |

## Requirements

- Home Assistant 2026.3.2 or newer
- A charger that exposes its allowed charge current as a HA `number` entity (e.g. via the Easee, OCPP, or similar integration)
- For Solar excess mode: a `weather` entity at or near the installation site
- For Minimize Cost mode: a Nordpool (or compatible) spot price sensor

## Installation via HACS

1. In Home Assistant, go to **HACS → Integrations**.
2. Click the three-dot menu in the top-right corner and choose **Custom repositories**.
3. Paste the URL of this repository, select category **Integration**, and click **Add**.
4. Search for **EV Charger Manager** in the HACS integration list and click **Download**.
5. Restart Home Assistant.

## Configuration

After installation, add the integration through **Settings → Devices & Services → Add Integration → EV Charger Manager**.

The setup wizard has three steps:

### Step 1 — Charger

| Field | Description |
|---|---|
| Charger current entity | The `number` entity that sets the allowed charge current on the charge box |
| Minimum charge current | Lowest current the charger accepts (typically 6 A per IEC 61851) |
| Maximum charge current | Highest current the charger is rated for |
| Number of phases | 1 for single-phase, 3 for three-phase |
| Phase voltage | Nominal voltage (230 V in Europe) |

### Step 2 — Solar / PV

| Field | Description |
|---|---|
| PV system peak power | Nameplate capacity in kWp; set to 0 to disable solar estimation |
| Weather entity | Used to derive cloud-cover attenuation of the solar estimate |
| Grid power sensor *(optional)* | Sensor reporting grid power in kW; positive = exporting to grid. When provided, the measured export is used directly in Solar excess mode instead of the estimate. |

### Step 3 — Pricing

| Field | Description |
|---|---|
| Nordpool / spot price sensor *(optional)* | Any sensor whose `state` is the current spot price per kWh and whose attributes contain an array of today's (and optionally tomorrow's) hourly prices |

All settings can be changed later without removing the integration via **Settings → Devices & Services → EV Charger Manager → Configure**. The minimum and maximum charge current can also be changed at any time directly through their `number` entities on the device page.

## Charge modes in detail

### ASAP
Writes `max_current` to the charger unconditionally. Use this when you want the car charged as quickly as possible regardless of cost or solar output.

### Solar excess
Estimates available solar power from:
1. Sun elevation angle (calculated from site latitude, longitude, and current time)
2. Weather condition (cloud-cover attenuation)

Available current = `solar_power_kW × 1000 / (phases × voltage)`

If a grid-power sensor is configured, the measured grid export replaces the solar estimate. Charging pauses when the available current falls below `min_current`.

### Minimize cost
Collects hourly prices from today and tomorrow (when available). Charges at `max_current` during the *N* cheapest hours and pauses during the rest, where *N* is the **Cheap hours needed** number entity. If no price data is available, falls back to charging at `max_current`.
