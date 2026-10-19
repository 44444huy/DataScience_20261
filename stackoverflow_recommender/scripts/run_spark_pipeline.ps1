$ErrorActionPreference = "Stop"

$catalog = if ($env:POLARIS_CATALOG) { $env:POLARIS_CATALOG } else { "stackoverflow" }
$clientId = if ($env:POLARIS_ROOT_CLIENT_ID) { $env:POLARIS_ROOT_CLIENT_ID } else { "root" }
$clientSecret = if ($env:POLARIS_ROOT_CLIENT_SECRET) { $env:POLARIS_ROOT_CLIENT_SECRET } else { "s3cr3t" }
$credential = "${clientId}:${clientSecret}"

docker compose -f docker-compose.lakehouse.yml up -d --wait minio polaris polaris-setup spark

docker compose -f docker-compose.lakehouse.yml exec spark /opt/spark/bin/spark-submit `
  --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.12.0,org.apache.iceberg:iceberg-aws-bundle:1.12.0 `
  --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions `
  --conf spark.sql.catalog.polaris=org.apache.iceberg.spark.SparkCatalog `
  --conf spark.sql.catalog.polaris.type=rest `
  --conf spark.sql.catalog.polaris.warehouse=$catalog `
  --conf spark.sql.catalog.polaris.uri=http://polaris:8181/api/catalog `
  --conf spark.sql.catalog.polaris.credential=$credential `
  --conf spark.sql.catalog.polaris.scope=PRINCIPAL_ROLE:ALL `
  --conf spark.sql.catalog.polaris.rest.auth.type=oauth2 `
  --conf spark.sql.catalog.polaris.oauth2-server-uri=http://polaris:8181/api/catalog/v1/oauth/tokens `
  --conf spark.sql.catalog.polaris.header.X-Iceberg-Access-Delegation=vended-credentials `
  --conf spark.sql.defaultCatalog=polaris `
  --conf spark.driver.memory=2g `
  --conf spark.sql.shuffle.partitions=8 `
  --conf spark.jars.ivy=/tmp/.ivy2 `
  /workspace/lakehouse/jobs/clean_stackoverflow.py
