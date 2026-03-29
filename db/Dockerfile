FROM postgres:latest

# Optional initialization scripts for first-time container bootstrap.
# Existing project migration flow can still be used for upgrades.
COPY db/migrations /docker-entrypoint-initdb.d
