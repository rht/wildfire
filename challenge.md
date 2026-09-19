# Challenge details
Spain just had a record-breaking wildfire year. We're drowning in data - satellites, cameras, weather stations - but the hard part is pulling accurate, actionable signal out of it in real time. Build something that does exactly that.

Pick one of four tracks:

1. Early detection: Train a model that accurately detects wildfires from cameras, satellites, or other feeds, so firefighters are alerted as early as possible.
2. Monitoring active fires: Draw real-time perimeters of active fires from satellite data, determine the direction they're spreading, and simulate their movement.
3. Prediction: Identify where and when fire risk is highest by analyzing weather data for high-risk conditions and days.
4. Values at risk: Build an agentic system that identifies the infrastructure, people, and assets in danger when a fire breaks out, and helps make evacuation calls (which hospital, which school, etc.).

What a strong submission looks like
1. Works on real data from the provided resources.
2. Produces results in, or near, real time.
3. Clearly shows how a firefighter or emergency coordinator would actually use it.

Judging criteria
1. Using AI to actually help first responders do their jobs better.
2. Technical implementation and accuracy on real data.
3. Creative use of the provided datasets and APIs.
4. Demo quality.

# Datasets

Deepfire API
[Unified API for hotspots, clusters, fire spread](https://docs.deepfire.co/api/hotspots)

Satellite data
[Satellite data from MTG, taking photos of Spain every 10 minutes](https://datalsasaf.lsasvcs.ipma.pt/PRODUCTS/MTG/MTFRPPixel/)

Smoke detection
[Dataset for smoke detection from cameras](https://huggingface.co/datasets/pyronear/pyro-sdis)

Weather model
[Google WeatherNext](https://deepmind.google/science/weathernext/)

Fire spread model
[ELMFIRE](https://elmfire.io/)

Catalan public datasets
[Generalitat de Catalunya geographic information (Department of Interior)](https://interior.gencat.cat/ca/serveis/informacio-geografica/)
