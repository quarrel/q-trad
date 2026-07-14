#!/usr/bin/env bash
set -euo pipefail

umask 077

if (($# != 3)); then
  printf 'usage: %s AUTOMATIC_EVIDENCE_JSON OPERATOR_REVIEW_JSON OUTPUT_JSON\n' "${0##*/}" >&2
  exit 64
fi

readonly automatic_evidence=$1
readonly operator_review=$2
readonly output=$3
readonly maximum_automatic_bytes=16777216
readonly maximum_review_bytes=1048576

[[ -f "$automatic_evidence" && ! -L "$automatic_evidence" ]]
[[ -f "$operator_review" && ! -L "$operator_review" ]]
[[ -d "$(dirname "$output")" && ! -L "$(dirname "$output")" ]]
[[ ! -L "$output" && ! -e "$output" ]]
(( $(wc -c < "$automatic_evidence") <= maximum_automatic_bytes ))
(( $(wc -c < "$operator_review") <= maximum_review_bytes ))
jq -e 'type == "object"' "$automatic_evidence" > /dev/null
jq -e 'type == "object"' "$operator_review" > /dev/null

sha256_canonical_without() {
  local path=$1
  local field=$2
  local canonical
  canonical="$(jq -cS "del(.$field)" "$path")"
  printf '%s' "$canonical" | sha256sum | cut -d ' ' -f 1
}

utc_epoch() {
  local value=$1
  local epoch
  local normalised
  epoch="$(date --date="$value" +%s)"
  normalised="$(date --utc --date="@$epoch" +%Y-%m-%dT%H:%M:%SZ)"
  [[ "$normalised" == "$value" ]]
  printf '%s\n' "$epoch"
}

recorded_evidence_sha="$(jq -er '.evidence_sha256' "$automatic_evidence")"
calculated_evidence_sha="$(
  sha256_canonical_without "$automatic_evidence" evidence_sha256
)"
readonly recorded_evidence_sha calculated_evidence_sha
[[ "$recorded_evidence_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$recorded_evidence_sha" == "$calculated_evidence_sha" ]]

jq -e '
  .schema == "qtrad-capture-qualification-v1"
  and .automatic_checks_passed == true
  and ([.automatic_checks[]] | all)
  and .qualification_decision == "PENDING_OPERATOR_REVIEW"
  and (.candidate_start | type == "string")
  and (.not_before_end | type == "string")
  and (.generated_at | type == "string")
  and (.candidate_gaps | type == "array")
  and (.release | type == "object")
  and .operator_reviews == {
    candidate_gap_classification:
      (if (.candidate_gaps | length) == 0 then "NOT_REQUIRED" else "REQUIRED" end),
    container_log_history:"REQUIRED",
    monitoring_history:"REQUIRED",
    active_market_representativeness:"REQUIRED"
  }
' "$automatic_evidence" > /dev/null

candidate_start="$(jq -er '.candidate_start' "$automatic_evidence")"
not_before_end="$(jq -er '.not_before_end' "$automatic_evidence")"
generated_at="$(jq -er '.generated_at' "$automatic_evidence")"
candidate_start_epoch="$(utc_epoch "$candidate_start")"
not_before_end_epoch="$(utc_epoch "$not_before_end")"
generated_at_epoch="$(utc_epoch "$generated_at")"
readonly candidate_start not_before_end generated_at
readonly candidate_start_epoch not_before_end_epoch generated_at_epoch
((generated_at_epoch >= not_before_end_epoch))

jq -e --arg evidence_sha "$recorded_evidence_sha" '
  def bounded_text($maximum):
    type == "string" and length > 0 and length <= $maximum;
  def evidence_refs:
    type == "array" and length > 0 and length <= 16
    and all(.[]; bounded_text(512)) and (unique | length) == length;
  def decision:
    . == "PASS" or . == "FAIL";
  type == "object"
  and (keys == ["qualification_evidence_sha256", "reviewed_at", "reviewer", "reviews", "schema"])
  and .schema == "qtrad-capture-qualification-review-v1"
  and .qualification_evidence_sha256 == $evidence_sha
  and (.reviewed_at | type == "string")
  and (.reviewer | bounded_text(128))
  and (.reviews | type == "object")
  and (.reviews | keys == [
    "active_market_representativeness", "candidate_gap_classification",
    "container_log_history", "monitoring_history"
  ])
  and (.reviews.candidate_gap_classification
    | type == "object" and (keys == ["decision", "gaps", "notes"])
    and (.decision == "PASS" or .decision == "FAIL" or .decision == "NOT_REQUIRED")
    and (.notes | bounded_text(2000))
    and (.gaps | type == "array" and length <= 100
      and all(.[];
        type == "object" and (keys == ["classification", "gap_id", "rationale"])
        and (.gap_id | bounded_text(128))
        and (.classification | IN(
          "EXPECTED_MARKET_CLOSURE", "EXPLAINED_PROVIDER_MAINTENANCE",
          "EXPLAINED_LIFECYCLE_EVENT", "UNEXPLAINED"
        ))
        and (.rationale | bounded_text(2000)))
      and ([.[].gap_id] | unique | length) == length))
  and (.reviews.container_log_history
    | type == "object" and (keys == ["decision", "evidence_refs", "notes", "window_end", "window_start"])
    and (.decision | decision) and (.evidence_refs | evidence_refs)
    and (.notes | bounded_text(2000))
    and (.window_start | type == "string") and (.window_end | type == "string"))
  and (.reviews.monitoring_history
    | type == "object" and (keys == ["decision", "evidence_refs", "notes", "window_end", "window_start"])
    and (.decision | decision) and (.evidence_refs | evidence_refs)
    and (.notes | bounded_text(2000))
    and (.window_start | type == "string") and (.window_end | type == "string"))
  and (.reviews.active_market_representativeness
    | type == "object" and (keys == ["decision", "evidence_refs", "notes"])
    and (.decision | decision) and (.evidence_refs | evidence_refs)
    and (.notes | bounded_text(2000)))
' "$operator_review" > /dev/null

reviewed_at="$(jq -er '.reviewed_at' "$operator_review")"
reviewed_at_epoch="$(utc_epoch "$reviewed_at")"
readonly reviewed_at reviewed_at_epoch
((reviewed_at_epoch >= generated_at_epoch))

for review_name in container_log_history monitoring_history; do
  window_start="$(jq -er --arg name "$review_name" '.reviews[$name].window_start' "$operator_review")"
  window_end="$(jq -er --arg name "$review_name" '.reviews[$name].window_end' "$operator_review")"
  window_start_epoch="$(utc_epoch "$window_start")"
  window_end_epoch="$(utc_epoch "$window_end")"
  ((window_start_epoch <= candidate_start_epoch))
  ((window_end_epoch >= generated_at_epoch))
done

evidence_gap_ids="$(jq -cS '[.candidate_gaps[].gap_id] | sort' "$automatic_evidence")"
review_gap_ids="$(
  jq -cS '[.reviews.candidate_gap_classification.gaps[].gap_id] | sort' "$operator_review"
)"
readonly evidence_gap_ids review_gap_ids
[[ "$(jq -r 'unique | length == length' <<< "$evidence_gap_ids")" == true ]]
[[ "$evidence_gap_ids" == "$review_gap_ids" ]]

gap_count="$(jq -r '.candidate_gaps | length' "$automatic_evidence")"
gap_decision="$(jq -r '.reviews.candidate_gap_classification.decision' "$operator_review")"
unexplained_gap_count="$(
  jq -r '[.reviews.candidate_gap_classification.gaps[]
    | select(.classification == "UNEXPLAINED")] | length' "$operator_review"
)"
readonly gap_count gap_decision unexplained_gap_count
if ((gap_count == 0)); then
  [[ "$gap_decision" == "NOT_REQUIRED" ]]
else
  [[ "$gap_decision" != "NOT_REQUIRED" ]]
  if [[ "$gap_decision" == PASS ]]; then
    ((unexplained_gap_count == 0))
  fi
fi

operator_reviews_passed="$(
  jq -r '
    (.reviews.candidate_gap_classification.decision | IN("PASS", "NOT_REQUIRED"))
    and .reviews.container_log_history.decision == "PASS"
    and .reviews.monitoring_history.decision == "PASS"
    and .reviews.active_market_representativeness.decision == "PASS"
  ' "$operator_review"
)"
readonly operator_reviews_passed
qualification_decision=FAIL
if [[ "$operator_reviews_passed" == true ]]; then
  qualification_decision=PASS
fi
review_canonical="$(jq -cS . "$operator_review")"
review_sha="$(printf '%s' "$review_canonical" | sha256sum | cut -d ' ' -f 1)"
finaliser_tool_sha="$(sha256sum "${BASH_SOURCE[0]}" | cut -d ' ' -f 1)"
readonly qualification_decision review_canonical review_sha finaliser_tool_sha

final_identity="$(
  jq -cS -n \
    --arg schema qtrad-capture-qualification-final-v1 \
    --arg finalised_at "$reviewed_at" \
    --arg evidence_sha256 "$recorded_evidence_sha" \
    --arg operator_review_sha256 "$review_sha" \
    --arg finaliser_tool_sha256 "$finaliser_tool_sha" \
    --arg candidate_start "$candidate_start" \
    --arg not_before_end "$not_before_end" \
    --arg generated_at "$generated_at" \
    --arg qualification_decision "$qualification_decision" \
    --argjson operator_reviews_passed "$operator_reviews_passed" \
    --slurpfile release "$automatic_evidence" \
    --slurpfile review "$operator_review" \
    '{schema:$schema, finalised_at:$finalised_at,
      qualification_evidence_sha256:$evidence_sha256,
      operator_review_sha256:$operator_review_sha256,
      finaliser_tool_sha256:$finaliser_tool_sha256,
      reviewer:$review[0].reviewer,
      candidate_start:$candidate_start, not_before_end:$not_before_end,
      automatic_evidence_generated_at:$generated_at,
      release:$release[0].release,
      automatic_checks_passed:true,
      operator_reviews:$review[0].reviews,
      operator_reviews_passed:$operator_reviews_passed,
      qualification_decision:$qualification_decision}'
)"
readonly final_identity
final_evidence_sha="$(printf '%s' "$final_identity" | sha256sum | cut -d ' ' -f 1)"
readonly final_evidence_sha

temporary_output="$(mktemp "$(dirname "$output")/.qualification-final.XXXXXX")"
cleanup() {
  rm -f "$temporary_output"
}
trap cleanup EXIT
jq --arg final_evidence_sha256 "$final_evidence_sha" \
  '. + {final_evidence_sha256:$final_evidence_sha256}' \
  <<< "$final_identity" > "$temporary_output"
ln "$temporary_output" "$output"
rm "$temporary_output"
trap - EXIT
printf '%s\n' "$output"

[[ "$qualification_decision" == PASS ]]
