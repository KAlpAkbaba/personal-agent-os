"""Live weather (docs/DECISIONS.md ADR-0091): a real provider call against a
``app.location.service.LocationContext`` — never weather from model memory, never a
guessed location. See ``app.weather.providers`` for the provider interface and
``app.weather.service.WeatherService`` for the resolver-then-provider path."""
