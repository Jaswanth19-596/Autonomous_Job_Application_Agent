# RideWise

RideWise is a full-stack, historical taxi-demand forecasting application that groups NYC pickup locations into 30 regions, predicts demand for the next 15-minute historical time bucket, and presents results through a React dashboard backed by FastAPI and MLflow-managed XGBoost models.

Github: https://github.com/Jaswanth19-596/RideWise

## Verified project snapshot

| Area | Verified facts |
| --- | --- |
| Problem | The project forecasts taxi pickups by geographic region to help drivers compare expected demand across locations. |
| Data scope | The ingestion stage reads January, February, and March 2016 NYC Yellow Taxi CSV files. There is no live-data integration in the repository. |
| User experience | A React dashboard displays region selection, all-region or nearest-region views, a Leaflet map, a Recharts bar chart, summary cards, and a detail table. |
| Prediction API | FastAPI exposes region metadata, all-region predictions, and predictions for the eight nearest regions. |
| Model serving | The backend loads the MLflow model alias `ridewise.production.xgboost@champion`, downloads its run artifacts, transforms data with a saved encoder, and returns predicted pickups plus rush-hour/weekend flags. |
| Training | A DVC-defined pipeline ingests and filters trips, clusters pickup locations with `MiniBatchKMeans`, creates time-series features, trains XGBoost, logs artifacts/metrics to Databricks MLflow, and registers a development model. |
| Automation | GitHub Actions defines CI for DVC pull, tests, and model promotion, plus CD for Docker builds, ECR pushes, S3 deployment packages, and CodeDeploy. |
| Runtime/deployment | Dockerfiles exist for the FastAPI backend and React frontend; the deployment script starts both containers on one EC2 host and uses AWS SSM parameters for Databricks credentials. |

## Safe technical description

The system separates a React presentation layer from a FastAPI prediction service. The frontend calls `/api/regions`, `/api/predict-all`, and `/api/predict/{region_id}`; the service returns a list of predicted pickups and feature flags. The neighborhood endpoint sorts static region coordinates using Haversine distance and returns the nearest eight regions.

The model workflow starts from three historical taxi CSVs. It filters geographic, fare, and trip-distance outliers; fits a `StandardScaler` and `MiniBatchKMeans` model with 30 clusters; aggregates pickups into 15-minute windows; and creates calendar, lag, rolling-window, and regional historical features. Training one-hot encodes region IDs, fits an `XGBRegressor`, logs the model and artifacts to Databricks MLflow, and assigns `latest` to the development model. A promotion script copies that model to the production registry and assigns the `champion` alias.

At serving time, the application maps the current clock time to a March 2016 test-data timestamp and rounds forward to the next 15-minute bucket. This is a historical-time simulation—not real-time demand forecasting.

## Quantitative facts and permitted phrasing

| Verified fact | Safe wording | Do not turn it into |
| --- | --- | --- |
| 3 historical monthly CSV inputs | “Processed three months of 2016 NYC Yellow Taxi trip data.” | “Processed millions of trips” or a dataset size; no row count is verified here. |
| 30 KMeans regions | “Clustered pickup locations into 30 demand regions.” | A claim that 30 was optimal or that clustering improved accuracy. |
| 15-minute resampling and serving buckets | “Produced 15-minute regional demand forecasts.” | “Delivered real-time 15-minute forecasts.” |
| 8 lags | “Engineered eight lag features per region.” | A claim that every feature materially improved the model. |
| 4 rolling windows: 4, 8, 12, and 16 | “Added rolling mean and standard-deviation features across four lookback windows.” | A measured reduction in error. |
| 8 nearest regions per request | “Returned predictions for up to eight nearest regions using Haversine distance.” | A routing/optimization or travel-time feature. |
| MAPE `< 0.3` test assertion | “Implemented a test gate requiring MAPE below 0.30 on the stored test dataset.” | “Achieved MAPE below 0.30” or “30% accuracy”; the repository does not include a passing test result or run metric. |
| Region IDs 0–29 | “Served predictions across 30 predefined regions.” | Coverage of all NYC neighborhoods, drivers, or riders. |

## Technical decisions and rationale supported by the repository

| Decision | Evidence-backed rationale | Careful framing |
| --- | --- | --- |
| Historical simulation instead of live inference | The code reads static 2016 files and maps the current clock to March 2016. | “Built a historical-demand simulation” is safe; “integrated live mobility data” is not. |
| Regional clustering | Raw pickup latitude/longitude values are grouped by `MiniBatchKMeans` before aggregation. | Describe it as a way to create region-level demand features; do not claim geographic accuracy validation. |
| 15-minute time windows | The preprocessing stage resamples pickup counts at `15min`; serving advances to the next 15-minute bucket. | Appropriate for short-horizon demand comparison. |
| Feature-rich tabular model | Training combines calendar, lag, rolling, and regional historical statistics, then one-hot encodes the region for XGBoost. | State the implemented feature design; do not claim feature-importance results. |
| MLflow registry aliases | Training assigns `latest` to a development model; promotion assigns `champion` to a copied production model. | “Implemented alias-based model promotion” is safe. |
| Separate frontend and API | React calls FastAPI endpoints through Axios; the API owns model loading and response shaping. | “Built a full-stack separation of concerns” is safe. |
| CI/CD and containers | Workflows build Docker images and invoke ECR/S3/CodeDeploy; scripts start the containers. | “Defined an AWS CI/CD deployment pipeline” is safe; do not claim a currently running or highly available production service without external proof. |

## Technology inventory

- Frontend: React 19, Axios, React Leaflet/Leaflet, Recharts, Create React App.
- Backend: Python, FastAPI, Pydantic Settings, Uvicorn, pandas, NumPy, joblib.
- ML/data: Dask, pandas, scikit-learn (`StandardScaler`, `MiniBatchKMeans`, `OneHotEncoder`), XGBoost, DVC, MLflow/Databricks.
- Delivery: Docker, GitHub Actions, AWS ECR, S3, CodeDeploy, EC2, AWS SSM Parameter Store, Nginx image.

## Reusable content by output type

### Résumé bullets

Use an action verb only if the applicant confirms authorship or ownership. When that context is supplied, build bullets from one implementation fact plus one verified technical detail; do not add an unverified outcome.

Safe building blocks:

- Built a React and FastAPI taxi-demand forecasting application that visualizes predictions across 30 clustered NYC regions.
- Engineered a DVC-defined ML workflow that transforms three months of historical taxi trips into 15-minute regional features, including eight lags and four rolling windows.
- Integrated Databricks MLflow model registration and alias-based promotion from `ridewise.development.xgboost@latest` to `ridewise.production.xgboost@champion`.
- Implemented all-region and nearest-region FastAPI prediction endpoints, using Haversine distance to return up to eight nearby demand estimates.
- Defined Docker and GitHub Actions delivery workflows for ECR, S3, and AWS CodeDeploy.

Allowed qualification when needed: “project code defines,” “implemented in the repository,” “for a historical simulation,” or “with a test gate requiring MAPE < 0.30.”


## Evidence ledger

| Claim area | Primary source |
| --- | --- |
| Historical inputs and filtering | `ridewise/data_ingestion.py` lines 30–64; `params.yaml` lines 1–20 |
| 30-region clustering and 15-minute aggregation | `ridewise/data_preprocessing.py` lines 29–90; `params.yaml` lines 13–16 |
| Feature set and chronological split | `ridewise/feature_engineering.py` lines 22–122 |
| Training, MLflow logging, development alias | `ridewise/model_training.py` lines 41–135 |
| Production promotion alias | `ridewise/promote_model.py` lines 14–25; `backend/config.py` lines 9–25 |
| Historical serving behavior and fallback mode | `backend/classes/predictor.py` lines 28–123 |
| API shape and eight-neighbor behavior | `backend/routers/predictions.py` lines 24–128; `backend/routers/regions.py` lines 5–75 |
| Frontend API routes and dashboard behavior | `frontend/src/services/api.js` lines 1–19; `frontend/src/App.js` lines 9–240 |
| Test threshold and feature/data checks | `tests/test_data.py` lines 33–111 |
| CI/CD and EC2 deployment configuration | `.github/workflows/ci.yaml` lines 1–50; `.github/workflows/cd.yaml` lines 1–59; `deploy/scripts/start_application.sh` lines 1–47 |
| Production proxy caveat | `frontend/nginx.conf.backup` lines 1–34; `frontend/Dockerfile` lines 1–21 |


