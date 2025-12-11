window.dashExtensions = Object.assign({}, window.dashExtensions, {
    default: {
        function0: function(feature, latlng, context) {
                const p = feature.properties || {};
                const airTemp = (p.air_temperature !== undefined && p.air_temperature !== null) ? p.air_temperature : "?";
                const windSpeed = (p.wind_speed !== undefined && p.wind_speed !== null) ? p.wind_speed : "?";
                const relHum = (p.relative_humidity !== undefined && p.relative_humidity !== null) ? p.relative_humidity : "?";

                var popup = "<b>Weather station</b><br>" +
                    "Temp: " + airTemp + " °C<br>" +
                    "Wind: " + windSpeed + " m/s<br>" +
                    "Humidity: " + relHum + " %";

                return L.circleMarker(latlng, {
                    radius: 6,
                    fillColor: "orange",
                    color: "black",
                    weight: 1,
                    opacity: 1,
                    fillOpacity: 0.85
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
            const last_update = p.date_time_utc || "Unknown";


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
                "Last update (UTC): " + last_update;

            const marker = L.marker(latlng, {
                icon: icon
            });
            marker.bindPopup(popup);
            return marker;
        }

    }
});