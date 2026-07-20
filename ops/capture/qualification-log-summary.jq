def records:
  split("\n")
  | map(select(length > 0))
  | map(
      capture("^(?<docker_timestamp>[^ ]+) (?<payload>.*)$")
      | .docker_timestamp as $docker_timestamp
      | (.payload | fromjson)
      | . + {docker_timestamp:$docker_timestamp}
    );

def event_summary($records; $name):
  [$records[] | select(.event == $name)] as $matches
  | {
      event:$name,
      count:($matches | length),
      first_timestamp:($matches[0].docker_timestamp // null),
      last_timestamp:($matches[-1].docker_timestamp // null)
    };

records as $records
| [
    "ig_heartbeat_subscription_established",
    "ig_heartbeat_frequency",
    "ig_heartbeat_subscription_ended",
    "ig_heartbeat_subscription_error",
    "ig_heartbeat_invalid",
    "ig_stream_status",
    "ig_stream_stale",
    "ig_stream_retry_watchdog_expired",
    "ig_subscription_error",
    "ig_lightstreamer_updates_lost",
    "ig_stream_server_error",
    "ig_queue_saturated",
    "ig_reconnect_retry",
    "ig_reconnect_cooldown",
    "ig_reconnect_exhausted"
  ] as $tracked
| [
    "ig_heartbeat_subscription_ended",
    "ig_heartbeat_subscription_error",
    "ig_heartbeat_invalid",
    "ig_stream_retry_watchdog_expired",
    "ig_subscription_error",
    "ig_lightstreamer_updates_lost",
    "ig_stream_server_error",
    "ig_queue_saturated",
    "ig_reconnect_exhausted"
  ] as $adverse
| {
    schema:"qtrad-capture-lifecycle-summary-v1",
    parsed_records:($records | length),
    tracked_events:[$tracked[] as $event | event_summary($records; $event)],
    stream_statuses:([
      $records[] | select(.event == "ig_stream_status") | .status
    ] | group_by(.) | map({status:.[0],count:length})),
    adverse_event_count:([$records[] | select(.event as $event | $adverse | index($event))] | length)
  }
