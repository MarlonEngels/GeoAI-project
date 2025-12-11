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

    }
});