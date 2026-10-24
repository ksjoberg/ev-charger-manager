# EV Charger Manager

A Home Assistant custom integration that controls an EV charge box by adjusting its allowed charge current based on one of three selectable strategies: charge as fast as possible, use only excess solar power, or charge a fixed amount per day at the lowest cost using solar forecasts and Nordpool spot prices.

## Features

- **Three charge modes** selectable from a standard HA `select` entity:
  - **ASAP** – always charges at the configured maximum current
  - **Solar excess** – sets the current proportional to estimated (or measured) solar surplus; pauses when there is insufficient excess
  - **Fixed daily amount** – plans and executes the cheapest way to charge a configured number of kWh per day, combining solar forecast, import/export prices and household base load
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
| Applied charge current | `sensor` | Current being written to the charger |
| Estimated solar power | `sensor` | Estimated PV output in kW |
| Current spot price | `sensor` | Price read from the Nordpool sensor |
| Charge decision reason | `sensor` | Human-readable explanation of the last decision |
| Fixed daily amount | `number` | kWh to charge per daily window (Fixed daily amount mode) |
| Daily window start hour | `number` | Local hour when the daily window starts; the amount is due before the next one |
| Defer to tomorrow: minimum saving | `number` | How much cheaper (%) tomorrow must be before today's charging is deferred; 100 % disables |
| Opportunistic import price threshold | `number` | All-in import price that qualifies for extra charging in Fixed Daily Amount mode |
| Opportunistic extra energy | `number` | Additional kWh allowed per daily window when the threshold is met; 0 disables |
| Charge session plan | `sensor` | Summary of the planned charging blocks; full per-slot schedule in attributes |
| Energy charged in daily window | `sensor` | kWh delivered since the window started |
| Daily amount remaining | `sensor` | kWh still planned in this window (capped by kWh to target SoC) |
| Planned charging cost | `sensor` | Estimated cost of the remaining plan |

## Requirements

- Home Assistant 2026.3.2 or newer
- A charger that exposes its allowed charge current as a HA `number` entity (e.g. via the Easee, OCPP, or similar integration)
- For Solar excess mode: a `weather` entity at or near the installation site
- For Fixed daily amount mode: a Nordpool (or compatible) spot price sensor

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

### Fixed daily amount
Charges a fixed number of kWh (the **Fixed daily amount** number entity) within each daily window at the lowest estimated cost. The window starts at the **Daily window start hour** (default 00:00, i.e. the calendar day) and the amount is due before the next window starts. The amount is capped by the kWh still needed to reach the EV's target SoC when the EV battery entities are configured.

**Cost model.** The window is split into the price slots of the import price sensor (15 min or 1 h). For each slot:

- solar surplus = forecast PV production − household base load
- charging power covered by the surplus costs the **export price** (the revenue lost by not exporting it)
- charging power beyond the surplus costs the **import price**

Self-consuming solar is therefore always preferred within a slot, but solar is exported when importing in a later slot is cheaper, e.g. when the midday export price is above the night-time all-in import price.

**Planning.** The charger can only run between the minimum and maximum current, but it can be paused for part of a slot. The planner builds each slot's cost curve (the convex hull of the cost over "off" and `min`–`max` current) and fills the required kWh with the cheapest marginal kWh across all slots. This greedy fill is cost-optimal for this model. Slots past the published prices use the price from 24 h earlier until real prices arrive.

**Execution.** The plan is rebuilt every 5 minutes and at every quarter-hour for the kWh still missing. For the current slot, the measured surplus (from the grid and PV sensors, when configured) replaces the forecast. The current slot's planned energy is converted to a charge current; when that is below the minimum current, the slot is charged at the minimum current and stopped once its share is delivered.

**Opportunistic charging.** In Fixed Daily Amount mode only, the **Opportunistic import price threshold** and **Opportunistic extra energy** number entities allow extra charging when the current all-in import price is at or below the threshold. The extra allowance is cumulative within one daily window, on top of the normal daily amount, and defaults to 0 kWh. The EV target SoC remains a hard cap. Forecast slot capacity with a marginal cost below the current import price is reserved first, so only the portion not covered by cheaper forecast solar is charged opportunistically now. The configured import-price units apply to the threshold.

**Deferring to tomorrow.** Next-day prices are published in the early afternoon (around 13:00–13:30 CET). Once the next window's prices are known, the planner first reserves the next window's own daily amount. It then checks whether that window's remaining capacity can supply some of today's kWh at least the configured saving cheaper (default 50 %). For example, at 50 % a kWh costing 2.00 today is moved only if it costs at most 1.00 tomorrow. Today's most expensive kWh are given up first, so a cheap solar slot today can be kept while an expensive evening slot is dropped. Deferred energy is added to the next window's amount. Only published prices are used for this, and energy that doesn't fit today at all is never deferred.

**Delivered energy** is read from the optional *Charger energy meter* sensor (lifetime or per-session counters both work). Without a meter, the commanded current is integrated over time, which overestimates if the car does not draw the full allowed current. The count is persisted across restarts.
