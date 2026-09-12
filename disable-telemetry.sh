#!/usr/bin/env bash
set -euo pipefail

# Telemetry env keys: "KEY=VALUE" (all values are JSON strings)
ENTRIES=(
    "DISABLE_TELEMETRY=1"
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"
    "DISABLE_ERROR_REPORTING=1"
    "DISABLE_FEEDBACK_COMMAND=1"
    "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1"
    "OTEL_METRICS_EXPORTER=none"
    "OTEL_LOGS_EXPORTER=none"
    "OTEL_TRACES_EXPORTER=none"
    "OTEL_LOG_ASSISTANT_RESPONSES=0"
    "OTEL_LOG_USER_PROMPTS=0"
    "OTEL_LOG_TOOL_CONTENT=0"
    "OTEL_LOG_TOOL_DETAILS=0"
)

# Inner env body (last line without trailing comma)
build_env_body() {
    local i last=$((${#ENTRIES[@]} - 1))
    for i in "${!ENTRIES[@]}"; do
        local key="${ENTRIES[$i]%%=*}" val="${ENTRIES[$i]#*=}"
        if [[ $i -eq $last ]]; then
            printf '    "%s": "%s"\n' "$key" "$val"
        else
            printf '    "%s": "%s",\n' "$key" "$val"
        fi
    done
}

ENV_BODY="$(build_env_body)"

# Canonical full file
write_canonical() {
    {
        echo "{"
        echo '  "env": {'
        echo "$ENV_BODY"
        echo "  },"
        echo '  "skipWebFetchPreflight": true'
        echo "}"
    } > "$1"
}

# Top-level env block to insert after the document's opening brace
ENV_BLOCK="$(printf '  "env": {\n%s\n  },' "$ENV_BODY")"

insert_after_first_brace() {
    # $1 file, $2 text to insert after the first line containing {
    local file="$1" text="$2" tmp="$1.ins.$$"
    awk -v ins="$text" '
        !done && /\{/ { print; print ins; done=1; next }
        { print }
    ' "$file" > "$tmp"
    mv "$tmp" "$file"
}

apply_file() {
    local file="$1"
    mkdir -p "$(dirname "$file")"

    # missing or empty -> canonical
    if [[ ! -s "$file" ]]; then
        write_canonical "$file"
        echo "    [created]   $file"
        return
    fi

    local tmp="$file.work.$$"
    cp "$file" "$tmp"
    local changed=0

    # --- env section ---
    if ! grep -Eq '"env"[[:space:]]*:' "$tmp"; then
        # no env at all -> insert full block at top level
        insert_after_first_brace "$tmp" "$ENV_BLOCK"
        changed=1
    elif grep -Eq '"env"[[:space:]]*:[[:space:]]*\{[[:space:]]*\}' "$tmp"; then
        # empty env "{}" -> expand it, preserving trailing comma if present
        local exp="$tmp.exp.$$"
        awk -v body="$ENV_BODY" '
            !done && /"env"[[:space:]]*:[[:space:]]*\{[[:space:]]*\}/ {
                match($0, /^[[:space:]]*/); indent=substr($0,1,RLENGTH)
                comma = ($0 ~ /\}[[:space:]]*,[[:space:]]*$/) ? "," : ""
                print indent "\"env\": {"
                print body
                print indent "}" comma
                done=1; next
            }
            { print }
        ' "$tmp" > "$exp"
        mv "$exp" "$tmp"
        changed=1
    else
        # env has content -> add only missing keys after the env open line
        local missing="" pair key val
        for pair in "${ENTRIES[@]}"; do
            key="${pair%%=*}"; val="${pair#*=}"
            if ! grep -Eq "\"$key\"[[:space:]]*:" "$tmp"; then
                missing+="    \"$key\": \"$val\","$'\n'
            fi
        done
        if [[ -n "$missing" ]]; then
            missing="${missing%$'\n'}"
            local ins="$tmp.ins.$$"
            awk -v ins="$missing" '
                !done && /"env"[[:space:]]*:[[:space:]]*\{[[:space:]]*$/ {
                    print; print ins; done=1; next
                }
                { print }
            ' "$tmp" > "$ins"
            mv "$ins" "$tmp"
            changed=1
        fi
    fi

    # --- skipWebFetchPreflight ---
    if ! grep -q '"skipWebFetchPreflight"' "$tmp"; then
        insert_after_first_brace "$tmp" '  "skipWebFetchPreflight": true,'
        changed=1
    fi

    if [[ $changed -eq 1 ]]; then
        mv "$tmp" "$file"
        echo "    [merged]    $file"
    else
        rm -f "$tmp"
        echo "    [unchanged] $file"
    fi
}

# Root ~/.claude - both settings.json and settings.local.json
echo "Root: $HOME/.claude"
apply_file "$HOME/.claude/settings.json"
apply_file "$HOME/.claude/settings.local.json"

# Interactive scan for project dirs (.local only)
total=0
while true; do
    echo ""
    read -rp "Scan directory for .claude folders? (path or Enter to skip): " extra
    [[ -z "$extra" ]] && break

    if [[ ! -d "$extra" ]]; then
        echo "  Not a directory: $extra"
        continue
    fi

    count=0
    echo "  Scanning: $extra"
    while IFS= read -r found; do
        echo "  Found: $found"
        apply_file "$found/settings.local.json"
        count=$((count + 1))
    done < <(find "$extra" -type d -name ".claude")

    if [[ $count -eq 0 ]]; then
        echo "  No .claude folders found"
    else
        echo "  Applied to $count folder(s)"
        total=$((total + count))
    fi
done

echo ""
echo "Done. Project folders updated: $total"
