# Prototype Digital Twin Architecture Diagram

```UML

 Browser (User)
 ══════════════
       │
       │  HTTP :8050
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Dash Application                             │
│                        (Python / Docker)                            │
│                                                                     │
│  ┌──────────────┐   ┌────────────────────┐   ┌──────────────────┐  │
│  │   app.py     │──▶│  map_callbacks.py  │──▶│  map_view.py     │  │
│  │  (entry pt)  │   │  (all callbacks)   │   │  (Dash layout)   │  │
│  └──────────────┘   └────────┬───────────┘   └──────────────────┘  │
│                              │                                      │
│           ┌──────────────────┼──────────────────┐                  │
│           ▼                  ▼                   ▼                  │
│  ┌────────────────┐ ┌───────────────┐  ┌─────────────────────┐     │
│  │  ais_api.py    │ │ weather_api.py│  │  visir_api.py       │     │
│  │  ais_hist_api  │ │               │  │  (HTTP client)      │     │
│  └───────┬────────┘ └──────┬────────┘  └──────────┬──────────┘     │
│          │                 │                       │                │
│          │                 │           ┌───────────┴──────────┐     │
│          │                 │           ▼                      ▼     │
│          │                 │  ┌────────────────┐  ┌──────────────┐  │
│          │                 │  │ route_job.py   │  │ namelist_    │  │
│          │                 │  │ (pipeline +    │  │ writer.py    │  │
│          │                 │  │  progress)     │  ├──────────────┤  │
│          │                 │  └────────────────┘  │ env_data_    │  │
│          │                 │                      │ downloader.py│  │
│          │                 │                      └──────┬───────┘  │
└──────────┼─────────────────┼─────────────────────────────┼──────────┘
           │                 │                             │
           │                 │           ┌─────────────────┤
           │                 │           │  HTTP :5050     │
           │                 │           ▼                 │
           │                 │  ┌─────────────────────┐   │
           │                 │  │   VISIR-2 Service    │   │
           │                 │  │  (Flask / conda env) │   │
           │                 │  │                      │   │
           │                 │  │  visir_runner.py     │   │
           │                 │  │       │              │   │
           │                 │  │       ▼              │   │
           │                 │  │  MAIN_Campi.py       │   │
           │                 │  │  MAIN_Tracce.py      │   │
           │                 │  │       │              │   │
           │                 │  │       ▼              │   │
           │                 │  │  __namelist/ (YAMLs) │   │
           │                 │  │  __data/    (NetCDF) │   │
           │                 │  │  __product/ (CSVs)   │   │
           │                 │  └─────────────────────┘   │
           │                 │                             │
    ═══════╪═════════════════╪═════════════════════════════╪═══════════
     E X T E R N A L    A P I s                           │
           │                 │                             │
           ▼                 ▼                             ▼
  ┌────────────────┐ ┌───────────────┐  ┌──────────────────────────┐
  │ Kystdatahuset  │ │  MET.no API   │  │  Copernicus Marine       │
  │ AIS API        │ │  (weather     │  │  (wave + current NetCDF) │
  │                │ │   forecast)   │  │                          │
  │ - Real-time    │ │               │  │  Dataset:                │
  │   positions    │ │  Location-    │  │  wam-arctic-1hr3km-be    │
  │ - Historical   │ │  forecast/2.0 │  │                          │
  │   tracks       │ │               │  │  Variables: VHM0, VMDR,  │
  └────────────────┘ └───────────────┘  │  Current, Currentdir     │
                                        └──────────────────────────┘
```

## Data Flow for Route Computation Pipeline

```UML

User submits route form
        │
        ▼
  ┌─────────────────────────────────────────────────┐
  │  route_job.run_pipeline()  (background thread)  │
  │                                                 │
  │  1. params ──> namelist_writer creates YAMLs    │
  │                (_a_Grafi, _b_Campi, _d_Tracce)  │
  │                                                 │
  │  2. data ────> env_data_downloader fetches      │
  │                NetCDF from Copernicus Marine    │
  │                                                 │
  │  3. fields ──> POST /run-campi to VISIR-2       │
  │                (processes env data onto edges)  │
  │                                                 │
  │  4. routes ──> POST /run to VISIR-2             │
  │                (MAIN_Tracce computes routes)    │
  │                                                 │
  │  5. viz ─────> Build GeoJSON + summary for map  │
  └─────────────────────────────────────────────────┘
        │
        ▼
  Map displays 3 route options:
    - Shortest distance (blue)
    - Fastest time (red)
    - Lowest CO2 (green)
```

## Docker Compose Topology

```UML

┌─────────────────────────────────────────────────────┐
│                  Docker Compose                     │
│                                                     │
│  ┌──────────────────┐      ┌──────────────────────┐ │
│  │  app             │      │  visir               │ │
│  │  (Dash, Python)  │────> │  (Flask, conda)      │ │
│  │  port 8050       │ HTTP │  port 5050           │ │
│  │                  │      │                      │ │
│  │  depends_on:     │      │  healthcheck:        │ │
│  │    visir(healthy)│      │    GET /health       │ │
│  └────────┬─────────┘      └──────────┬───────────┘ │
│           │    Shared volumes:        │             │
│           │    __namelist/ <─────────>│             │
│           │    __data/envFields/ <──> │             │
│           └───────────────────────────┘             │
└─────────────────────────────────────────────────────┘
```

## Map Layers (dash-leaflet)

| Layer                  | Source                       | Update                                |
| ---------------------- | ---------------------------- | ------------------------------------- |
| AIS vessel positions   | Kystdatahuset real-time API  | 30s interval                          |
| Weather stations       | MET.no locationforecast/2.0  | 30s interval + click-to-place         |
| AIS density heatmap    | Kystdatahuset historical API | On-demand (draw polygon + date range) |
| VISIR-2 routes         | VISIR-2 service              | On-demand (route form)                |
| Shoreline / Bathymetry | Pre-loaded GeoJSON           | Toggle                                |
