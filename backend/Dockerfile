FROM mysql:8.0

# MySQL runs these files once, in lexical order, when the data directory is new.
COPY sql-files-nus/services.sql /docker-entrypoint-initdb.d/01-services.sql
COPY sql-files-nus/categories.sql /docker-entrypoint-initdb.d/02-categories.sql
COPY sql-files-nus/specialities.sql /docker-entrypoint-initdb.d/03-specialities.sql
COPY sql-files-nus/tags.sql /docker-entrypoint-initdb.d/04-tags.sql
COPY sql-files-nus/speciality_tags.sql /docker-entrypoint-initdb.d/05-speciality-tags.sql
