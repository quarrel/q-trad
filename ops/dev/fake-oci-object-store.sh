#!/usr/bin/env bash
set -euo pipefail

readonly root="${QTRAD_FAKE_OBJECT_DIR:?QTRAD_FAKE_OBJECT_DIR is required}"
readonly listed_object="${QTRAD_FAKE_OBJECT_NAME:?QTRAD_FAKE_OBJECT_NAME is required}"
args=" $* "
if [[ "$args" == *" os object list "* ]]; then
  jq -n --arg name "$listed_object" \
    '{data:[{name:$name,"time-created":"2026-07-20T00:00:00Z",size:1,etag:"ci"}]}'
  exit 0
fi

name=''
destination=''
while (($#)); do
  case "$1" in
    --name) name=$2; shift 2 ;;
    --file) destination=$2; shift 2 ;;
    *) shift ;;
  esac
done
[[ -n "$name" && -n "$destination" ]]
cp "$root/$name" "$destination"
