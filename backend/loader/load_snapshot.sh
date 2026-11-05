#!/bin/sh
# Rebuild a MySQL database from one taxonomy_pipeline snapshot.
#
# Usage (from backend/):
#   docker compose --profile tools run --rm loader <snapshot>
#   docker compose --profile tools run --rm -e TARGET_DATABASE=<name> loader <snapshot>
#
# <snapshot> is a folder under taxonomy_pipeline/integrations/ (mounted at
# /snapshots). Each snapshot is self-contained: baseline/ holds the original
# dumps plus every earlier round's additions.sql, and its own additions.sql
# holds that round's rows, in the order its enriched.yaml lists them.
#
# The target database is dropped and rebuilt from those files rather than
# updated in place, so it always equals exactly one snapshot: applying a round
# twice or out of order can't happen, and going back a stage is just loading an
# older snapshot. The original database (greygigz_beta) is never a valid target.
#
# The whole rebuild runs in one MySQL session holding a named lock on the
# target, so two loaders started at once can't interleave their drops and
# imports; the second one stops before touching anything.
set -eu

SNAPSHOTS=${SNAPSHOTS_DIR:-/snapshots}
TARGET=${TARGET_DATABASE:-taxonomy_latest}
PROTECTED=${PROTECTED_DATABASE:-greygigz_beta}
CONCEPTS="services categories specialities tags speciality_tags"

available() { # helper function to find which snapshot folders contain enriched.yaml
    echo "available snapshots:" >&2
    for dir in "$SNAPSHOTS"/*/; do
        [ -f "${dir}enriched.yaml" ] && echo "  $(basename "$dir")" >&2
    done
    return 0
}

identifier() {  # returns true only if name is letters / digits / underscores
    printf '%s' "$1" | grep -Eq '^[A-Za-z0-9_]+$'
}

if [ $# -ne 1 ]; then
    echo "usage: docker compose --profile tools run --rm loader <snapshot>" >&2
    available
    exit 2
fi
NAME=$1
# integrate names folders freely (--out-dir); only plain names reach the SQL below.
if ! printf '%s' "$NAME" | grep -Eq '^[A-Za-z0-9._-]+$'; then
    echo "unsupported snapshot name '$NAME': use letters, digits, '.', '_' and '-'" >&2
    exit 2
fi
DIR="$SNAPSHOTS/$NAME"
if [ ! -f "$DIR/enriched.yaml" ]; then
    echo "no snapshot '$NAME' (expected $DIR/enriched.yaml)" >&2
    available
    exit 2
fi

case "$TARGET" in
    "$PROTECTED"|mysql|sys|information_schema|performance_schema)
        echo "refusing to rebuild '$TARGET': it is a protected database" >&2
        exit 2 ;;
esac
if ! identifier "$TARGET"; then
    echo "invalid TARGET_DATABASE '$TARGET': use letters, digits and underscores" >&2
    exit 2
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# The first dataset of enriched.yaml, as `integrate` writes it (yaml.safe_dump):
#   - name: enriched
#     paths:
#     - baseline/services.sql
#     table_map:          (only when the snapshot's tables are renamed)
#       tags: sf_tags
awk -v q="'" '/^  paths:/ { inblock = 1; next }
     inblock && /^  - / { sub(/^  - /, ""); gsub("^[\"" q "]|[\"" q "]$", ""); print; next }
     inblock { exit }' "$DIR/enriched.yaml" > "$WORK/order"
awk '/^- / && seen++ { exit }
     /^  table_map:/ { inblock = 1; next }
     inblock && /^    [a-z_]+: / { sub(/^    /, ""); sub(/: */, " "); print; next }
     inblock { exit }' "$DIR/enriched.yaml" > "$WORK/table_map"
if [ ! -s "$WORK/order" ]; then
    echo "$DIR/enriched.yaml lists no snapshot files; is it an integrate snapshot?" >&2
    exit 1
fi
if [ "$(tail -n 1 "$WORK/order")" != "additions.sql" ]; then
    echo "$DIR/enriched.yaml does not end with additions.sql; refusing an unexpected file list" >&2
    exit 1
fi
while IFS= read -r file; do
    # Each path is echoed inside a SQL string as a progress line; only plain names get there.
    if ! printf '%s' "$file" | grep -Eq '^[A-Za-z0-9._/-]+$'; then
        echo "$DIR/enriched.yaml lists unsupported file name '$file': use letters, digits, '.', '_', '-' and '/'" >&2
        exit 1
    fi
    if [ ! -f "$DIR/$file" ]; then
        echo "$DIR/enriched.yaml lists $file, which is missing" >&2
        exit 1
    fi
done < "$WORK/order"

# Row counts per concept, read from the snapshot's own table names (its table_map).
for concept in $CONCEPTS; do
    mapped=$(awk -v c="$concept" '$1 == c { print $2 }' "$WORK/table_map")
    table=${mapped:-$concept}
    if ! identifier "$table"; then
        echo "$DIR/enriched.yaml maps $concept to unsupported table name '$table'" >&2
        exit 1
    fi
    printf "SELECT CONCAT(RPAD('%s', 18, ' '), COUNT(*)) FROM \`%s\`;\n" "$concept" "$table"
done > "$WORK/counts.sql"

# One session: take the lock (GET_LOCK returns 0 when another session holds it, and
# the IF then runs a two-row subquery, which fails with ERROR 1242 and stops the
# client), rebuild, label, count. A file failing stops the session the same way.
# Build one full SQL script
SESSION="$WORK/session.sql"
{
    printf "DO IF(GET_LOCK('taxonomy_loader.%s', 0) = 1, 0, (SELECT 1 UNION ALL SELECT 2));\n" "$TARGET"
    printf 'DROP DATABASE IF EXISTS `%s`;\n' "$TARGET"
    printf 'CREATE DATABASE `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;\n' "$TARGET"
    printf 'USE `%s`;\n' "$TARGET"
    while IFS= read -r file; do
        cat "$DIR/$file"
        printf "\nSELECT '  loaded %s';\n" "$file"
    done < "$WORK/order"
    # Record which stage this database holds, for anyone browsing it in Adminer.
    printf 'CREATE TABLE _snapshot (name VARCHAR(255) NOT NULL, loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);\n'
    printf "INSERT INTO _snapshot (name) VALUES ('%s');\n" "$NAME"
    cat "$WORK/counts.sql"
} > "$SESSION"

# run the session sql script and output if there are any failures
echo "rebuilding '$TARGET' from snapshot '$NAME'"
if ! mysql -h "${MYSQL_HOST:-db}" -uroot --default-character-set=utf8mb4 -N -B < "$SESSION" 2> "$WORK/error"; then
    if grep -q "ERROR 1242" "$WORK/error" && grep -q "at line 1:" "$WORK/error"; then
        echo "another loader is rebuilding '$TARGET' right now; nothing was changed, try again when it finishes" >&2
    else
        cat "$WORK/error" >&2
        echo "FAILED after the last 'loaded' line above; '$TARGET' is now partial, fix the cause and rerun" >&2
    fi
    exit 1
fi
echo "'$TARGET' now holds snapshot '$NAME'"
