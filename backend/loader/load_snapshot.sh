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
set -eu

SNAPSHOTS=/snapshots
TARGET=${TARGET_DATABASE:-taxonomy_latest}
PROTECTED=${PROTECTED_DATABASE:-greygigz_beta}

available() {
    echo "available snapshots:" >&2
    for dir in "$SNAPSHOTS"/*/; do
        [ -f "${dir}enriched.yaml" ] && echo "  $(basename "$dir")" >&2
    done
    return 0
}

if [ $# -ne 1 ]; then
    echo "usage: docker compose --profile tools run --rm loader <snapshot>" >&2
    available
    exit 2
fi
NAME=$1
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
if ! printf '%s' "$TARGET" | grep -Eq '^[A-Za-z0-9_]+$'; then
    echo "invalid TARGET_DATABASE '$TARGET': use letters, digits and underscores" >&2
    exit 2
fi

# The first `paths:` block of enriched.yaml, as `integrate` writes it (yaml.safe_dump):
#   - name: enriched
#     paths:
#     - baseline/services.sql
ORDER=$(mktemp)
awk -v q="'" '/^  paths:/ { inblock = 1; next }
     inblock && /^  - / { sub(/^  - /, ""); gsub("^[\"" q "]|[\"" q "]$", ""); print; next }
     inblock { exit }' "$DIR/enriched.yaml" > "$ORDER"
if [ ! -s "$ORDER" ]; then
    echo "$DIR/enriched.yaml lists no snapshot files; is it an integrate snapshot?" >&2
    exit 1
fi
if [ "$(tail -n 1 "$ORDER")" != "additions.sql" ]; then
    echo "$DIR/enriched.yaml does not end with additions.sql; refusing an unexpected file list" >&2
    exit 1
fi
while IFS= read -r file; do
    if [ ! -f "$DIR/$file" ]; then
        echo "$DIR/enriched.yaml lists $file, which is missing" >&2
        exit 1
    fi
done < "$ORDER"

sql() { mysql -h "${MYSQL_HOST:-db}" -uroot --default-character-set=utf8mb4 "$@"; }

echo "rebuilding '$TARGET' from snapshot '$NAME'"
sql -e "DROP DATABASE IF EXISTS \`$TARGET\`; CREATE DATABASE \`$TARGET\` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"
while IFS= read -r file; do
    if ! sql "$TARGET" < "$DIR/$file"; then
        echo "FAILED loading $file; '$TARGET' is now partial, fix the cause and rerun" >&2
        exit 1
    fi
    echo "  loaded $file"
done < "$ORDER"
rm -f "$ORDER"

# Record which stage this database holds, for anyone browsing it in Adminer.
sql "$TARGET" -e "CREATE TABLE _snapshot (name VARCHAR(255) NOT NULL, loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);
                  INSERT INTO _snapshot (name) VALUES ('$NAME');"
sql -t "$TARGET" -e "SELECT 'services' AS \`table\`, COUNT(*) AS \`rows\` FROM services
                     UNION ALL SELECT 'categories', COUNT(*) FROM categories
                     UNION ALL SELECT 'specialities', COUNT(*) FROM specialities
                     UNION ALL SELECT 'tags', COUNT(*) FROM tags
                     UNION ALL SELECT 'speciality_tags', COUNT(*) FROM speciality_tags;"
echo "'$TARGET' now holds snapshot '$NAME'"
