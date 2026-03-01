window.dashExtensions = Object.assign({}, window.dashExtensions, {
    default: {
        function0: function(feature, latlng, context) {
                const p = feature.properties || {};

                const airTemp = (p.air_temperature !== undefined && p.air_temperature !== null) ? p.air_temperature : "?";
                const windSpeed = (p.wind_speed !== undefined && p.wind_speed !== null) ? p.wind_speed : "?";
                const relHum = (p.relative_humidity !== undefined && p.relative_humidity !== null) ? p.relative_humidity : "?";
                const airPressure = (p.air_pressure_at_sea_level !== undefined && p.air_pressure_at_sea_level !== null) ? p.air_pressure_at_sea_level : "?";
                const cloudAreaFraction = (p.cloud_area_fraction !== undefined && p.cloud_area_fraction !== null) ? p.cloud_area_fraction : "?";

                const windFromDirDeg =
                    (p.wind_from_direction !== undefined && p.wind_from_direction !== null) ?
                    p.wind_from_direction :
                    null;

                function degToCompass(deg) {
                    if (deg === null || isNaN(deg)) return "?";

                    const directions = [
                        "N", "NNE", "NE", "ENE",
                        "E", "ESE", "SE", "SSE",
                        "S", "SSW", "SW", "WSW",
                        "W", "WNW", "NW", "NNW"
                    ];

                    const normalized = ((deg % 360) + 360) % 360;
                    const index = Math.round(normalized / 22.5) % 16;

                    return directions[index];
                }

                const windFromDirText = degToCompass(windFromDirDeg);
                const windFromDirDisplay =
                    windFromDirDeg !== null ?
                    windFromDirText + " (" + Math.round(windFromDirDeg) + "&#176;)" :
                    "?";
                const weatherId = p.weather_id || "";

                var popup = "<b>Weather station</b><br>" +
                    "Temp: " + airTemp + " &#176;C<br>" +
                    "Wind: " + windSpeed + " m/s<br>" +
                    "Humidity: " + relHum + " %<br>" +
                    "Pressure: " + airPressure + " hPa<br>" +
                    "Cloud cover: " + cloudAreaFraction + " %<br>" +
                    "Wind direction: " + windFromDirDisplay +
                    "<br><button type='button' class='weather-remove-btn' data-weather-id='" + weatherId + "'" +
                    " style='margin-top:6px;padding:2px 6px;cursor:pointer;' " +
                    "onclick='event.stopPropagation();'>" +
                    "Remove</button>";

                return L.circleMarker(latlng, {
                    radius: 6,
                    fillColor: "red",
                    color: "black",
                    weight: 1,
                    opacity: 1,
                    fillOpacity: 0.85,
                    bubblingMouseEvents: false
                }).bindPopup(popup);
            }

            ,
        function1: function(feature, latlng, context) {
                const p = feature.properties || {};

                const name = p.ship_name || "Unknown vessel";
                const mmsi = p.mmsi || "?";
                const speed = (p.speed !== undefined && p.speed !== null) ? p.speed : "?";
                const cog = (p.cog !== undefined && p.cog !== null) ? p.cog : "?";
                const heading = (p.true_heading !== undefined && p.true_heading !== null) ?
                    p.true_heading :
                    (p.cog || 0);
                const destination = p.destination || "Unknown";
                const ais_class = p.ais_class || "Unknown";
                const last_update = (() => {
                    const s = p.date_time_utc;
                    if (!s) return "Unknown";

                    // Accept: "YYYY-MM-DDTHH:MM:SS", "YYYY-MM-DD HH:MM:SS", with/without trailing Z
                    const m = String(s).trim().match(
                        /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z)?$/
                    );
                    if (!m) return String(s); // fallback: show raw value

                    const year = Number(m[1]);
                    const mon = Number(m[2]);
                    const day = Number(m[3]);
                    const hour = Number(m[4]);
                    const min = Number(m[5]);
                    const sec = Number(m[6]);

                    // Build a UTC timestamp explicitly, then add +1h for CET
                    const ms = Date.UTC(year, mon - 1, day, hour, min, sec) + 60 * 60 * 1000;
                    const d = new Date(ms);

                    const pad = (x) => String(x).padStart(2, "0");
                    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
                        `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} CET`;
                })();

                const arrow = "&#129033;";

                const iconHtml =
                    '<div style="transform: rotate(' + heading + 'deg);' +
                    'transform-origin: center center;' +
                    'font-size: 18px;' +
                    'color: blue;' +
                    'line-height: 18px;">' +
                    arrow +
                    '</div>';

                const icon = L.divIcon({
                    html: iconHtml,
                    className: "",
                    iconSize: [18, 18],
                    iconAnchor: [9, 9]
                });

                var popup = "<b>Vessel details</b><br>" +
                    "Name: " + name + "<br>" +
                    "MMSI: " + mmsi + "<br>" +
                    "Speed: " + speed + " kn<br>" +
                    "COG: " + cog + "&#176;<br>" +
                    "Destination: " + destination + "<br>" +
                    "Heading: " + heading + "&#176;<br>" +
                    "AIS Class: " + ais_class + "<br>" +
                    "Last update: " + last_update;

                const marker = L.marker(latlng, {
                    icon: icon
                });
                marker.bindPopup(popup);
                return marker;
            }

            ,
        function2: function(feature, context) {
            const c = (feature.properties && feature.properties.count) ? feature.properties.count : 0;

            const h = context.hideout || {};
            const t1 = h.t1 ?? 1;
            const t2 = h.t2 ?? 2;
            const t3 = h.t3 ?? 3;

            let fill = "green";
            if (c >= t3) fill = "red";
            else if (c >= t2) fill = "orange";
            else if (c >= t1) fill = "yellow";

            return {
                color: "black",
                weight: 0.5,
                fillColor: fill,
                fillOpacity: 0.45
            };
        }

    }
});