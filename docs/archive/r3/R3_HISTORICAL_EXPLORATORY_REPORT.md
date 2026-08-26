# R3.H Historical Exploratory Report

This is machine-readably labelled historical, MIDPOINT-only authenticated evidence. It is not executable evidence or a recommendation.

## Machine-readable report identity

```json
{
  "canonical_report_contract": "qtrad-r3-historical-exploratory-report-v2",
  "canonical_report_semantic_identity": "ac43c8f474652e43e4994131ea8fa56e99799992607e3ff46439d65b3c4a16fc",
  "configuration_semantic_identity": "eb69a3b1e7fb2e4dd1585169856c71f6a5b3e833f503b250704a6e32e8d950b1",
  "evidence_class": "HISTORICAL_EXPLORATORY",
  "markdown_contract": "qtrad-r3-historical-exploratory-markdown-v1",
  "no_post_result_expansion": true,
  "price_basis": "MIDPOINT_OHLC",
  "schema_version": 1,
  "source_class": "IBKR_HISTORICAL_RESEARCH",
  "stage": "R3.H"
}
```

## Terminal authority and consumed child identities

```json
{
  "authentication_performed": true,
  "forecast_coverage": {
    "all_roles_absent_target_count": 5215,
    "all_roles_present_target_count": 202709,
    "contract": "qtrad-r3-historical-forecast-coverage-receipt-v1",
    "decision_groups": {
      "all_roles_present": 28528,
      "shared_forecast_universe_excluded": 1406,
      "target_incomplete_excluded": 6765
    },
    "eligible_target_count": 207924,
    "excluded_target_counts": {
      "shared_forecast_universe": 2806,
      "target_incomplete": 2409
    },
    "exclusion_digests": {
      "shared_forecast_universe": "149337089debae348d0c78ff4658a5a5a275014ba238240d2523120ba8a5c85c",
      "target_incomplete": "c5f353bb2893c5ea1de0f46f848bea025aec942972e752c03fabbbc3193ee4b3"
    },
    "partial_role_target_count": 0,
    "policy": "EXCLUDE_SHARED_ALL_ROLE_ABSENT_COMPLETE_GROUPS_REJECT_PARTIAL_ROLE_COVERAGE",
    "state": "ASSESSED"
  },
  "identities": {
    "consumed_marker_id": "c9bf58cebaa51435369704e629fc9df65b05020bf4354c0541ae17726a802446",
    "g2_manifest_id": "1159b5f068882acf9a106e67dafc67dc4d976e04f13355b886fd5fdd7138047f",
    "local_forecast_dataset_id": "8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9",
    "outcome_evidence_manifest_id": "1159b5f068882acf9a106e67dafc67dc4d976e04f13355b886fd5fdd7138047f",
    "pooled_forecast_dataset_id": "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b",
    "selection_manifest_id": "15483908d45455ae7ccc5f8d1a3fdcd19b3226308b3a4c6afda067daedb627dc",
    "terminal_approval_sha256": "278830aed74a28c9d8ca79b3695b652137e131609e40b530e019f048063bcaa6",
    "terminal_report_sha256": "4fdc08e2e37135200f974f14ba28669e73aa4f57f51860731741f6a2ddea2b30",
    "zero_forecast_dataset_id": "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223"
  },
  "outcome_decode_performed": true,
  "paths": {
    "consumed": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/consumed.json",
    "local_forecast": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/forecasts/8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9.json",
    "outcome_evidence": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/outcome-evidence.json",
    "pooled_forecast": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/forecasts/d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b.json",
    "selection": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/selection.json",
    "terminal_approval": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z-authority/r2-scientific-report-review.json",
    "terminal_report": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/r2-scientific-report.md",
    "zero_forecast": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/forecasts/93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223.json"
  },
  "role_bindings": {
    "LOCAL_RIDGE": {
      "config_id": "7fad71b132e9ef29fa1d18c9d6c3a2f729f56191d6ec6ddeff767171393f27e8",
      "dataset_id": "8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9",
      "wrapper_sha256": "a11e7096d25cb0ffcda3c4c0bd2efd5d6fde51c50ff8607598b957176992fd0a"
    },
    "POOLED_LOCAL_RIDGE": {
      "config_id": "05e4767b32e5a59b6510eee10f9308c40cbaa18199bcf95a7bf5e61a1636fe28",
      "dataset_id": "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b",
      "wrapper_sha256": "e973e855ab2d62585cd8b809d9a57e74f6fc5b0908b292c08b7ad42ba16df6b6"
    },
    "ZERO_RETURN": {
      "config_id": "6ea3c2aff09d5dae7d30d8cc7eb7883382bfb2ce7a3b51cf5f80bb1d69604f4b",
      "dataset_id": "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223",
      "wrapper_sha256": "bfba06f10de85ad356bfc587d2010544a3f3959d13204f987f22773e916cd72d"
    }
  },
  "terminal_authentication": {
    "approval_path": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z-authority/r2-scientific-report-review.json",
    "approval_sha256": "278830aed74a28c9d8ca79b3695b652137e131609e40b530e019f048063bcaa6",
    "contract": "qtrad-r2-decision-grade-report-review-v1",
    "report_byte_size": 13008,
    "report_path": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/r2-scientific-report.md",
    "report_sha256": "4fdc08e2e37135200f974f14ba28669e73aa4f57f51860731741f6a2ddea2b30",
    "state": "FINAL_AUTHENTICATED",
    "verdict": "APPROVED"
  }
}
```

## Physical closure, execution, and resource provenance

```json
{
  "code_provenance": {
    "application_contract": "qtrad-r3-historical-exploratory-implementation-v2",
    "module_sha256": "80a7437bac6fbe017158362dd0cfd3896c2ef6137a2e721615e4c833d79f20db",
    "python_version": "3.13.14"
  },
  "graph_execution_receipts": {
    "controls": [
      {
        "execution_receipt": {
          "enabled": true,
          "id": "local_non_graph",
          "kind": "non_graph_local",
          "role_binding": {
            "config_id": "7fad71b132e9ef29fa1d18c9d6c3a2f729f56191d6ec6ddeff767171393f27e8",
            "dataset_id": "8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9",
            "wrapper_sha256": "a11e7096d25cb0ffcda3c4c0bd2efd5d6fde51c50ff8607598b957176992fd0a"
          }
        },
        "id": "local_non_graph"
      },
      {
        "execution_receipt": {
          "enabled": true,
          "id": "pooled_non_graph",
          "kind": "non_graph_pooled",
          "role_binding": {
            "config_id": "05e4767b32e5a59b6510eee10f9308c40cbaa18199bcf95a7bf5e61a1636fe28",
            "dataset_id": "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b",
            "wrapper_sha256": "e973e855ab2d62585cd8b809d9a57e74f6fc5b0908b292c08b7ad42ba16df6b6"
          }
        },
        "id": "pooled_non_graph"
      },
      {
        "execution_receipt": {
          "enabled": true,
          "id": "fixed_graph",
          "kind": "fixed_graph"
        },
        "id": "fixed_graph"
      },
      {
        "execution_receipt": {
          "enabled": true,
          "id": "shuffled_graph",
          "kind": "shuffled_graph"
        },
        "id": "shuffled_graph"
      }
    ],
    "tiny_graph": {
      "execution_receipt": {
        "enabled": true,
        "family": "gnn",
        "hidden_units": 4,
        "id": "tiny_learned_graph",
        "layers": 1
      },
      "id": "tiny_learned_graph"
    }
  },
  "retained_parent_paths": {
    "consumed": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/consumed.json",
    "local_forecast": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/forecasts/8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9.json",
    "outcome_evidence": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/outcome-evidence.json",
    "pooled_forecast": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/forecasts/d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b.json",
    "selection": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/selection.json",
    "terminal_approval": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z-authority/r2-scientific-report-review.json",
    "terminal_report": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/r2-scientific-report.md",
    "zero_forecast": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/forecasts/93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223.json"
  },
  "retained_role_bindings": {
    "LOCAL_RIDGE": {
      "config_id": "7fad71b132e9ef29fa1d18c9d6c3a2f729f56191d6ec6ddeff767171393f27e8",
      "dataset_id": "8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9",
      "wrapper_sha256": "a11e7096d25cb0ffcda3c4c0bd2efd5d6fde51c50ff8607598b957176992fd0a"
    },
    "POOLED_LOCAL_RIDGE": {
      "config_id": "05e4767b32e5a59b6510eee10f9308c40cbaa18199bcf95a7bf5e61a1636fe28",
      "dataset_id": "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b",
      "wrapper_sha256": "e973e855ab2d62585cd8b809d9a57e74f6fc5b0908b292c08b7ad42ba16df6b6"
    },
    "ZERO_RETURN": {
      "config_id": "6ea3c2aff09d5dae7d30d8cc7eb7883382bfb2ce7a3b51cf5f80bb1d69604f4b",
      "dataset_id": "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223",
      "wrapper_sha256": "bfba06f10de85ad356bfc587d2010544a3f3959d13204f987f22773e916cd72d"
    }
  },
  "statistical_execution_receipts": {
    "candidates": [
      {
        "execution_receipt": {
          "degree": 1,
          "enabled": true,
          "family": "ridge",
          "id": "linear_ridge"
        },
        "id": "linear_ridge"
      },
      {
        "execution_receipt": {
          "degree": 0,
          "enabled": true,
          "family": "constant_zero",
          "id": "linear_zero_return"
        },
        "id": "linear_zero_return"
      },
      {
        "execution_receipt": {
          "degree": 1,
          "enabled": true,
          "family": "bounded_huber",
          "id": "nonlinear_huber"
        },
        "id": "nonlinear_huber"
      }
    ],
    "simple_controls": [
      {
        "execution_receipt": {
          "candidate_id": "linear_zero_return",
          "fit_policy": "none",
          "id": "zero_return",
          "kind": "constant_zero",
          "role_binding": {
            "config_id": "6ea3c2aff09d5dae7d30d8cc7eb7883382bfb2ce7a3b51cf5f80bb1d69604f4b",
            "dataset_id": "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223",
            "wrapper_sha256": "bfba06f10de85ad356bfc587d2010544a3f3959d13204f987f22773e916cd72d"
          }
        },
        "id": "zero_return"
      },
      {
        "execution_receipt": {
          "candidate_id": "linear_ridge",
          "fit_policy": "chronological_oof",
          "id": "local_ridge",
          "kind": "local_ridge",
          "role_binding": {
            "config_id": "7fad71b132e9ef29fa1d18c9d6c3a2f729f56191d6ec6ddeff767171393f27e8",
            "dataset_id": "8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9",
            "wrapper_sha256": "a11e7096d25cb0ffcda3c4c0bd2efd5d6fde51c50ff8607598b957176992fd0a"
          }
        },
        "id": "local_ridge"
      },
      {
        "execution_receipt": {
          "candidate_id": "linear_ridge",
          "fit_policy": "chronological_oof",
          "id": "pooled_local_ridge",
          "kind": "pooled_ridge",
          "role_binding": {
            "config_id": "05e4767b32e5a59b6510eee10f9308c40cbaa18199bcf95a7bf5e61a1636fe28",
            "dataset_id": "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b",
            "wrapper_sha256": "e973e855ab2d62585cd8b809d9a57e74f6fc5b0908b292c08b7ad42ba16df6b6"
          }
        },
        "id": "pooled_local_ridge"
      }
    ]
  },
  "work_measurement": {
    "elapsed_seconds": 0.00406252,
    "memory_mb": 592.5625
  }
}
```

## Frozen configuration and code identity

```json
{
  "code_provenance": {
    "application_contract": "qtrad-r3-historical-exploratory-implementation-v2",
    "module_sha256": "80a7437bac6fbe017158362dd0cfd3896c2ef6137a2e721615e4c833d79f20db",
    "python_version": "3.13.14"
  },
  "configuration_semantic_identity": "eb69a3b1e7fb2e4dd1585169856c71f6a5b3e833f503b250704a6e32e8d950b1",
  "report_contract": "qtrad-r3-historical-exploratory-report-v2"
}
```

## Loader, selection, resources, and work counts

```json
{
  "loader_contract": {
    "child_wrappers": {
      "consumed": {
        "contract": "qtrad-r2-holdout-consumed-v1",
        "identity": "c9bf58cebaa51435369704e629fc9df65b05020bf4354c0541ae17726a802446",
        "identity_field": "marker_id",
        "required_keys": [
          "contract",
          "schema_version",
          "selection_manifest_id",
          "seal_id",
          "opened_marker_id",
          "consumed_at",
          "consumed_by",
          "evaluation_id",
          "outcome_accessed",
          "state",
          "marker_id"
        ],
        "sha256": "da69bbd8cee38cbd9f0df63e7c0c33f6268ed189dc81fce75c7bd5176bfc708f"
      },
      "local_forecast": {
        "contract": "qtrad-r2-holdout-forecast-v1",
        "identity": "8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9",
        "identity_field": "dataset_id",
        "manifest_relative_path": "forecasts/8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9.json",
        "partition_fields": [
          "rows"
        ],
        "partition_mapping_fields": [],
        "partition_row_field": "rows",
        "physical_required_keys": [
          "configuration_id",
          "contract",
          "dataset_id",
          "evidence_class",
          "expected_opportunity_ids",
          "feature_dataset_id",
          "final_fit_id",
          "final_fit_ids",
          "header_sha256",
          "holdout_outcomes_accessed",
          "holdout_scope",
          "identity_field",
          "opportunity_target_ids",
          "partition_fields",
          "partition_mapping_fields",
          "partition_row_field",
          "parts",
          "row_count",
          "schema_version",
          "selection_manifest_id",
          "source_class",
          "storage"
        ],
        "required_keys": [
          "contract",
          "schema_version",
          "selection_manifest_id",
          "feature_dataset_id",
          "configuration_id",
          "final_fit_id",
          "final_fit_ids",
          "rows",
          "expected_opportunity_ids",
          "opportunity_target_ids",
          "source_class",
          "evidence_class",
          "holdout_scope",
          "holdout_outcomes_accessed",
          "dataset_id"
        ],
        "sha256": "a11e7096d25cb0ffcda3c4c0bd2efd5d6fde51c50ff8607598b957176992fd0a"
      },
      "outcome_evidence": {
        "contract": "qtrad-r2-holdout-outcome-evidence-v1",
        "identity": "480ef61aec7daff49eadbec7d5ec6dc7c7f6f702c92b6a0bdbc9a7c05a342f8a",
        "identity_field": "outcome_evidence_id",
        "manifest_relative_path": "outcome-evidence.json",
        "partition_fields": [
          "expected_target_ids",
          "source_row_ids",
          "outcomes"
        ],
        "partition_mapping_fields": [],
        "partition_row_field": "outcomes",
        "physical_required_keys": [
          "contract",
          "schema_version",
          "selection_manifest_id",
          "seal_id",
          "opened_marker_id",
          "experiment_configuration_id",
          "foundation_bundle_id",
          "feature_dataset_id",
          "target_dataset_id",
          "holdout_range",
          "source_class",
          "evidence_class",
          "holdout_scope",
          "outcome_evidence_id",
          "partition_row_field",
          "partition_fields",
          "partition_mapping_fields",
          "header_sha256",
          "storage",
          "identity_field",
          "row_count",
          "parts"
        ],
        "required_keys": [
          "contract",
          "schema_version",
          "selection_manifest_id",
          "seal_id",
          "opened_marker_id",
          "experiment_configuration_id",
          "foundation_bundle_id",
          "feature_dataset_id",
          "target_dataset_id",
          "holdout_range",
          "expected_target_ids",
          "source_row_ids",
          "outcomes",
          "source_class",
          "evidence_class",
          "holdout_scope",
          "outcome_evidence_id"
        ],
        "sha256": "44be69c09433f4e237eb78535a2e0ba0cab6de67c96d5b79e6e1d69df28f13b1"
      },
      "pooled_forecast": {
        "contract": "qtrad-r2-holdout-forecast-v1",
        "identity": "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b",
        "identity_field": "dataset_id",
        "manifest_relative_path": "forecasts/d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b.json",
        "partition_fields": [
          "rows"
        ],
        "partition_mapping_fields": [],
        "partition_row_field": "rows",
        "physical_required_keys": [
          "configuration_id",
          "contract",
          "dataset_id",
          "evidence_class",
          "expected_opportunity_ids",
          "feature_dataset_id",
          "final_fit_id",
          "final_fit_ids",
          "header_sha256",
          "holdout_outcomes_accessed",
          "holdout_scope",
          "identity_field",
          "opportunity_target_ids",
          "partition_fields",
          "partition_mapping_fields",
          "partition_row_field",
          "parts",
          "row_count",
          "schema_version",
          "selection_manifest_id",
          "source_class",
          "storage"
        ],
        "required_keys": [
          "contract",
          "schema_version",
          "selection_manifest_id",
          "feature_dataset_id",
          "configuration_id",
          "final_fit_id",
          "final_fit_ids",
          "rows",
          "expected_opportunity_ids",
          "opportunity_target_ids",
          "source_class",
          "evidence_class",
          "holdout_scope",
          "holdout_outcomes_accessed",
          "dataset_id"
        ],
        "sha256": "e973e855ab2d62585cd8b809d9a57e74f6fc5b0908b292c08b7ad42ba16df6b6"
      },
      "selection": {
        "contract": "qtrad-r2-selection-v4",
        "identity": "15483908d45455ae7ccc5f8d1a3fdcd19b3226308b3a4c6afda067daedb627dc",
        "identity_field": "manifest_id",
        "required_keys": [
          "contract",
          "schema_version",
          "experiment_configuration_id",
          "foundation_bundle_id",
          "oof_id",
          "evaluation_report_id",
          "prior_selection_manifest_id",
          "source_class",
          "evidence_class",
          "holdout_scope",
          "evaluated_configuration_ids",
          "selected_configuration_ids",
          "control_configuration_ids",
          "holdout_configuration_ids",
          "comparator_families",
          "configuration_registry",
          "metric_policy",
          "threshold_policy",
          "evaluation_policy",
          "final_fitting_policy",
          "questions",
          "holdout_range",
          "experiment_count",
          "runtime_identities",
          "frozen_metadata",
          "frozen_at",
          "frozen_by",
          "state",
          "holdout_outcomes_accessed",
          "manifest_id"
        ],
        "sha256": "7f1020f422b01a64a47439342d6b2301be6aeb16e934daab11e2598935de3a53"
      },
      "zero_forecast": {
        "contract": "qtrad-r2-holdout-forecast-v1",
        "identity": "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223",
        "identity_field": "dataset_id",
        "manifest_relative_path": "forecasts/93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223.json",
        "partition_fields": [
          "rows"
        ],
        "partition_mapping_fields": [],
        "partition_row_field": "rows",
        "physical_required_keys": [
          "configuration_id",
          "contract",
          "dataset_id",
          "evidence_class",
          "expected_opportunity_ids",
          "feature_dataset_id",
          "final_fit_id",
          "final_fit_ids",
          "header_sha256",
          "holdout_outcomes_accessed",
          "holdout_scope",
          "identity_field",
          "opportunity_target_ids",
          "partition_fields",
          "partition_mapping_fields",
          "partition_row_field",
          "parts",
          "row_count",
          "schema_version",
          "selection_manifest_id",
          "source_class",
          "storage"
        ],
        "required_keys": [
          "contract",
          "schema_version",
          "selection_manifest_id",
          "feature_dataset_id",
          "configuration_id",
          "final_fit_id",
          "final_fit_ids",
          "rows",
          "expected_opportunity_ids",
          "opportunity_target_ids",
          "source_class",
          "evidence_class",
          "holdout_scope",
          "holdout_outcomes_accessed",
          "dataset_id"
        ],
        "sha256": "bfba06f10de85ad356bfc587d2010544a3f3959d13204f987f22773e916cd72d"
      }
    },
    "decode_policy": "stream all authenticated forecast/outcome parts one at a time after terminal authority; never replay ancestry",
    "decoder_limits": {
      "max_nested_depth": 8,
      "max_part_rows": 1000000,
      "max_row_bytes": 16384,
      "max_selected_rows": 64
    },
    "field_mappings": {
      "asset": "asset",
      "available_at": "available_at",
      "decision_time": "decision_time",
      "dependency_end": "dependency_end",
      "dependency_start": "dependency_start",
      "feature_value": "feature_value",
      "group": "group",
      "horizon_minutes": "horizon_minutes",
      "period": "period",
      "prediction": "prediction",
      "realised_return": "realised_return",
      "target_available_at": "target_available_at",
      "target_id": "target_id"
    },
    "identity_bindings": {
      "config_ids": {
        "LOCAL_RIDGE": "7fad71b132e9ef29fa1d18c9d6c3a2f729f56191d6ec6ddeff767171393f27e8",
        "POOLED_LOCAL_RIDGE": "05e4767b32e5a59b6510eee10f9308c40cbaa18199bcf95a7bf5e61a1636fe28",
        "ZERO_RETURN": "6ea3c2aff09d5dae7d30d8cc7eb7883382bfb2ce7a3b51cf5f80bb1d69604f4b"
      },
      "consumed_marker_id": "c9bf58cebaa51435369704e629fc9df65b05020bf4354c0541ae17726a802446",
      "dataset_ids": {
        "LOCAL_RIDGE": "8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9",
        "POOLED_LOCAL_RIDGE": "d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b",
        "ZERO_RETURN": "93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223"
      },
      "g2_manifest_id": "1159b5f068882acf9a106e67dafc67dc4d976e04f13355b886fd5fdd7138047f",
      "selection_manifest_id": "15483908d45455ae7ccc5f8d1a3fdcd19b3226308b3a4c6afda067daedb627dc",
      "wrapper_sha256s": {
        "POOLED_LOCAL_RIDGE": "e973e855ab2d62585cd8b809d9a57e74f6fc5b0908b292c08b7ad42ba16df6b6",
        "ZERO_RETURN": "bfba06f10de85ad356bfc587d2010544a3f3959d13204f987f22773e916cd72d"
      }
    },
    "locators": {
      "consumed": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/consumed.json",
      "local_forecast": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/forecasts/8a4fe578512816dc41e644ffd8a69e462440429eff5f76fb9bee5f477f36b4a9.json",
      "outcome_evidence": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/outcome-evidence.json",
      "pooled_forecast": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/forecasts/d2d07d4059ca989a97a2e24f663f28949515592fcaffbae7ea7b0da0ca8b6f6b.json",
      "selection": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/selection.json",
      "zero_forecast": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation/forecasts/93eb9453c269a438eaf2b1a149653449d526bcde3354a7892859097c05ec5223.json"
    },
    "manifest_contract": "qtrad-r2-g2-terminal-children-v1",
    "manifest_root": "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/g2-preparation",
    "required_children": [
      "selection",
      "consumed",
      "local_forecast",
      "pooled_forecast",
      "zero_forecast",
      "outcome_evidence"
    ],
    "required_columns": [
      "decision_time",
      "asset",
      "target_id",
      "group",
      "horizon_minutes",
      "period",
      "prediction",
      "realised_return",
      "available_at",
      "target_available_at",
      "dependency_start",
      "dependency_end",
      "feature_value"
    ],
    "selection_policy": {
      "analysis_row_bound": 18,
      "causal_row_predicate": "decision_time < evaluation_time AND target_available_at <= evaluation_time AND dependency_end < evaluation_time",
      "forecast_coverage_policy": "EXCLUDE_SHARED_ALL_ROLE_ABSENT_COMPLETE_GROUPS_REJECT_PARTIAL_ROLE_COVERAGE",
      "join_fields": [
        "decision_time",
        "target_id",
        "asset",
        "group",
        "horizon_minutes",
        "period"
      ],
      "n_complete_decision_groups": 3,
      "outcome_blind": true,
      "reject_duplicate": true,
      "reject_incomplete": true,
      "required_target_ids": [
        "fx:aud-usd",
        "fx:eur-usd",
        "index:australia-200",
        "index:us-500",
        "commodity:spot-gold",
        "commodity:us-crude"
      ],
      "temporal_selection_algorithm": "LEXICOGRAPHIC_EARLIEST_INCREASING_THREE_COMPLETE_GROUPS_WITH_ROW_CAUSAL_TRAINING"
    },
    "streaming_policy": {
      "expected_largest_part_bytes": 4198824,
      "expected_source_part_bytes": 373175647,
      "expected_source_parts": 150,
      "expected_source_rows": 1216254,
      "hash_consumed_parts": true,
      "max_consumed_parts": 150,
      "max_source_bytes": 2147483648,
      "max_source_rows": 3376258,
      "parts_first": true,
      "stop_after_selected_groups": false
    },
    "target_source": {
      "authorised_families": {
        "opportunities": {
          "part_count": 2,
          "row_count": 207924
        },
        "targets": {
          "part_count": 9,
          "row_count": 1058629
        }
      },
      "availability_evidence_id": "cc8f9ab805ec1f2e0b26bfd132c1209c8f80ac331f8b996d3d8799776b6d5c69",
      "causal_metadata_dataset_id": "0f23d7b17629e50d7ee921edf0dbb910d2e42b03b0ba06c39f12286065fc16fd",
      "causal_panel_dataset_id": "bb757d25b4e922740905dbab929f7a50492f61f3d60537e023d6a8143040918f",
      "closure_id": "216848d5446882763799870051b460e17aba2149cf90a47d361958e8da51c526",
      "combined_inventory": {
        "byte_count": 712575890,
        "largest_part_bytes": 67108825,
        "part_count": 11,
        "row_count": 1266553
      },
      "contract": "qtrad-r2-holdout-target-source-v1",
      "forbidden_families": [
        "pre_holdout_target_parts"
      ],
      "foundation_configuration_id": "c45c2a8be643771bb1940a35d34a990c8b5976e56b551142e376634de57bb9b6",
      "hard_limits": {
        "max_bytes": 2147483648,
        "max_part_bytes": 536870912,
        "max_rows": 3376258
      },
      "observation_dataset_id": "ae6a07f5a7201a184e7d506f2d8f4fd2a27d77045fdf00d383daabe904e9ef41",
      "schema_version": 1,
      "source_id": "b2c3442578bcc65a4b3ee573d34cef474f0dfb09cbdd563bacb1a7740a449994",
      "source_target_dataset_id": "2a09e6146e6feaa1e707f245c8585949fdc15a3a92828f37e1a9e93866de8e5f",
      "storage": "qtrad-r2-holdout-target-source-bounded-parts-v1",
      "target_index_dataset_id": "822c4d2b873d0b704481077ef3fb1cddff25deabe574d3177caa9c5a5e45504f",
      "wrapper_sha256": "672206c558f7fd7db01f7f493f583b30d8944268ffaefa1df314f1f6151a0140"
    }
  },
  "observation_contract": {
    "durable_output": "create-only operator-selected R3.H report path",
    "event_aware": true,
    "resource_limits": {
      "max_elapsed_seconds": 120,
      "max_memory_mb": 1024
    },
    "stop_conditions": [
      "no silent retry after identity, chronology, purge, embargo or decoder failure",
      "no provider/authentication call from fixture runner",
      "preserve failed checkpoint and report exact exit state"
    ]
  },
  "scale_projection": {
    "decoder_limits": {
      "max_nested_depth": 8,
      "max_part_rows": 1000000,
      "max_row_bytes": 16384,
      "max_selected_rows": 64
    },
    "fixture_row_count": 18,
    "group_count": 3,
    "projected_elapsed_seconds": 60,
    "projected_peak_memory_mb": 512,
    "resource_envelope_rationale": {
      "additional_authorized_work": [
        "selected target/opportunity/forecast replays",
        "outcome decode",
        "bounded historical analysis"
      ],
      "envelope": {
        "max_elapsed_seconds": 120,
        "max_memory_mb": 1024
      },
      "first_pass_observation": {
        "elapsed_seconds": 49.23,
        "maximum_rss_kib": 519188
      },
      "projected_first_pass": {
        "max_elapsed_seconds": 60,
        "max_memory_mb": 512
      }
    },
    "retained_row_count": 1216254,
    "selection": {
      "analysis_row_bound": 18,
      "join_fields": [
        "decision_time",
        "target_id",
        "asset",
        "group",
        "horizon_minutes",
        "period"
      ],
      "n_complete_decision_groups": 3,
      "outcome_blind": true,
      "required_target_count": 6
    },
    "source_scan": {
      "bytes": 373175647,
      "parts": 150,
      "rows": 1216254
    },
    "stop_conditions": [
      "stop before retained execution if any child shape exceeds frozen row/column contract",
      "stop if measured memory or elapsed time reaches its frozen cap",
      "stop on missing maturity/dependency metadata or identity mismatch",
      "stop on incomplete or duplicate canonical join identity"
    ],
    "streaming_policy": {
      "hash_consumed_parts": true,
      "parts_first": true,
      "peak_memory_margin_mb": 512,
      "stop_after_selected_groups": false
    },
    "target_count": 6
  },
  "selection": {
    "algorithm": "LEXICOGRAPHIC_EARLIEST_INCREASING_THREE_COMPLETE_GROUPS_WITH_ROW_CAUSAL_TRAINING",
    "causal_predicate": "decision_time < evaluation_time AND target_available_at <= evaluation_time AND dependency_end < evaluation_time",
    "causal_training_count": 6,
    "complete_groups": 3,
    "first_admissible_evaluation_time": "2026-06-26T14:26:00+00:00",
    "outcome_blind": true,
    "selected_bytes": 8997,
    "selected_decision_times": [
      "2026-06-26T14:06:00+00:00",
      "2026-06-26T14:26:00+00:00",
      "2026-06-26T14:27:00+00:00"
    ],
    "selected_parts": 247,
    "selected_rows": 18,
    "source_bytes": 2344087054,
    "source_parts": 247,
    "source_rows": 4357487,
    "stop_reason": "FULL_SCAN_REQUIRED_NO_ORDER_PROOF",
    "stop_state": "SCANNED_ALL_PARTS_REQUIRED_NO_ORDER_PROOF",
    "target_count": 6
  },
  "work": {
    "candidate_count": 3,
    "fit_count": 3,
    "fit_executions": {
      "linear_ridge": 0,
      "linear_zero_return": 0,
      "nonlinear_huber": 1,
      "pooled_local_ridge": 1,
      "tiny_learned_graph": 1
    },
    "graph_control_count": 4,
    "graph_fit_count": 1,
    "limits": {
      "max_candidates": 3,
      "max_elapsed_seconds": 120,
      "max_fits": 4,
      "max_memory_mb": 1024,
      "max_rows": 64
    },
    "rows": 18,
    "within_hard_limits": true
  }
}
```

## Economic break-even and turnover sensitivity

```json
{
  "all_in_cost_sensitivity": [
    {
      "break_even_cost": 5.8401438e-05,
      "cost": 0.0,
      "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
      "net_mean": 2.935e-09,
      "unit": "fraction_of_notional"
    },
    {
      "break_even_cost": 5.8401438e-05,
      "cost": 0.0005,
      "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
      "net_mean": -2.2192e-08,
      "unit": "fraction_of_notional"
    },
    {
      "break_even_cost": 5.8401438e-05,
      "cost": 0.001,
      "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
      "net_mean": -4.7319e-08,
      "unit": "fraction_of_notional"
    },
    {
      "break_even_cost": 5.8401438e-05,
      "cost": 0.002,
      "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
      "net_mean": -9.7573e-08,
      "unit": "fraction_of_notional"
    }
  ],
  "asset": {
    "commodity:spot-gold": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": 0.001550509656,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 8.6472e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.001550509656,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 5.8587e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.001550509656,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 3.0702e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.001550509656,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.5068e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "break_even_cost": 0.001550509656,
      "gross_mean": 8.6472e-08,
      "gross_total": 2.59415e-07,
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 2.98861e-07,
          "target_id": "commodity:spot-gold",
          "target_position": 6.3883617e-05,
          "target_position_change": 6.3883617e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.775e-08,
          "target_id": "commodity:spot-gold",
          "target_position": -6.798524e-06,
          "target_position_change": -7.0682141e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -5.7196e-08,
          "target_id": "commodity:spot-gold",
          "target_position": 2.5945221e-05,
          "target_position_change": 3.2743745e-05
        }
      ],
      "turnover": 0.000167309503
    },
    "commodity:us-crude": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": -0.001003509266,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.32533e-07,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.001003509266,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.98568e-07,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.001003509266,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.64603e-07,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.001003509266,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -3.96673e-07,
          "unit": "fraction_of_notional"
        }
      ],
      "break_even_cost": -0.001003509266,
      "gross_mean": -1.32533e-07,
      "gross_total": -3.976e-07,
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 4.1008e-08,
          "target_id": "commodity:us-crude",
          "target_position": -9.4476664e-05,
          "target_position_change": -9.4476664e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -3.58016e-07,
          "target_id": "commodity:us-crude",
          "target_position": 0.000123479741,
          "target_position_change": 0.000217956405
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -8.0592e-08,
          "target_id": "commodity:us-crude",
          "target_position": 3.9703215e-05,
          "target_position_change": -8.3776526e-05
        }
      ],
      "turnover": 0.000396209595
    },
    "fx:aud-usd": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": 0.000469296764,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 6.667e-09,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000469296764,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -4.36e-10,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000469296764,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -7.539e-09,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000469296764,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.1745e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "break_even_cost": 0.000469296764,
      "gross_mean": 6.667e-09,
      "gross_total": 2e-08,
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -2.6153e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.58259e-05,
          "target_position_change": -2.58259e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.674e-08,
          "target_id": "fx:aud-usd",
          "target_position": -3.5894947e-05,
          "target_position_change": -1.0069047e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.9413e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.9172937e-05,
          "target_position_change": 6.72201e-06
        }
      ],
      "turnover": 4.2616957e-05
    },
    "fx:eur-usd": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": -2.9918097e-05,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -4.04e-10,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -2.9918097e-05,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -7.15e-09,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -2.9918097e-05,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.3896e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -2.9918097e-05,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.7388e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "break_even_cost": -2.9918097e-05,
      "gross_mean": -4.04e-10,
      "gross_total": -1.211e-09,
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -7.939e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.159062e-05,
          "target_position_change": -2.159062e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 4.892e-09,
          "target_id": "fx:eur-usd",
          "target_position": -6.420369e-06,
          "target_position_change": 1.5170251e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.836e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.704066e-06,
          "target_position_change": 3.716303e-06
        }
      ],
      "turnover": 4.0477174e-05
    },
    "index:australia-200": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": 0.000190766717,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 1.0517e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000190766717,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.7048e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000190766717,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -4.4613e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000190766717,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -9.9743e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "break_even_cost": 0.000190766717,
      "gross_mean": 1.0517e-08,
      "gross_total": 3.1551e-08,
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.96802e-07,
          "target_id": "index:australia-200",
          "target_position": -0.00011327098,
          "target_position_change": -0.00011327098
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.74201e-07,
          "target_id": "index:australia-200",
          "target_position": -0.000122301435,
          "target_position_change": -9.030455e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 5.4152e-08,
          "target_id": "index:australia-200",
          "target_position": -7.9212384e-05,
          "target_position_change": 4.3089051e-05
        }
      ],
      "turnover": 0.000165390486
    },
    "index:us-500": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": 0.001519753375,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 4.6891e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.001519753375,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 3.1464e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.001519753375,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 1.6037e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.001519753375,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.4818e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "break_even_cost": 0.001519753375,
      "gross_mean": 4.6891e-08,
      "gross_total": 1.40673e-07,
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.81446e-07,
          "target_id": "index:us-500",
          "target_position": -8.414435e-05,
          "target_position_change": -8.414435e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.15805e-07,
          "target_id": "index:us-500",
          "target_position": -8.1357702e-05,
          "target_position_change": 2.786648e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.06314e-07,
          "target_id": "index:us-500",
          "target_position": -7.5725655e-05,
          "target_position_change": 5.632047e-06
        }
      ],
      "turnover": 9.2563045e-05
    }
  },
  "break_even_cost": 5.8401438e-05,
  "configurations": {
    "fixed_graph": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": -0.000129042317,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -3.457e-09,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000129042317,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.6853e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000129042317,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -3.0249e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000129042317,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -5.704e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "asset": {
        "commodity:spot-gold": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001624187963,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.2882e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001624187963,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.224e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001624187963,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.01598e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001624187963,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.40314e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001624187963,
          "gross_mean": -6.2882e-08,
          "gross_total": -1.88646e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -3.17472e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -6.7861703e-05,
              "target_position_change": -6.7861703e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 6.3964e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -2.4498942e-05,
              "target_position_change": 4.3362761e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 6.4862e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -2.9422365e-05,
              "target_position_change": -4.923423e-06
            }
          ],
          "turnover": 0.000116147887
        },
        "commodity:us-crude": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.003301577629,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 7.5865e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.003301577629,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 6.4376e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.003301577629,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 5.2887e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.003301577629,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 2.9908e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.003301577629,
          "gross_mean": 7.5865e-08,
          "gross_total": 2.27595e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.5708e-08,
              "target_id": "commodity:us-crude",
              "target_position": -3.6189647e-05,
              "target_position_change": -3.6189647e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.46578e-07,
              "target_id": "commodity:us-crude",
              "target_position": -5.0554595e-05,
              "target_position_change": -1.4364948e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 6.5309e-08,
              "target_id": "commodity:us-crude",
              "target_position": -3.2173964e-05,
              "target_position_change": 1.8380631e-05
            }
          ],
          "turnover": 6.8935226e-05
        },
        "fx:aud-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000299530214,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.131e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000299530214,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.1705e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000299530214,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.5278e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000299530214,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.2425e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000299530214,
          "gross_mean": -8.131e-09,
          "gross_total": -2.4394e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -5.0552e-08,
              "target_id": "fx:aud-usd",
              "target_position": -4.99198e-05,
              "target_position_change": -4.99198e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.3915e-08,
              "target_id": "fx:aud-usd",
              "target_position": -1.8679658e-05,
              "target_position_change": 3.1240142e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.2243e-08,
              "target_id": "fx:aud-usd",
              "target_position": -1.8398734e-05,
              "target_position_change": 2.80924e-07
            }
          ],
          "turnover": 8.1440866e-05
        },
        "fx:eur-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000207332353,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 5.38e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000207332353,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.594e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000207332353,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.0567e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000207332353,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.6514e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000207332353,
          "gross_mean": 5.38e-09,
          "gross_total": 1.6139e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.8668e-08,
              "target_id": "fx:eur-usd",
              "target_position": -5.0766856e-05,
              "target_position_change": -5.0766856e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.8724e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.4574573e-05,
              "target_position_change": 2.6192283e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.6083e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.3692508e-05,
              "target_position_change": 8.82065e-07
            }
          ],
          "turnover": 7.7841204e-05
        },
        "index:australia-200": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000690064899,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.6206e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000690064899,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.7949e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000690064899,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.9692e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000690064899,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.3177e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000690064899,
          "gross_mean": -1.6206e-08,
          "gross_total": -4.8619e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -5.6347e-08,
              "target_id": "index:australia-200",
              "target_position": -3.2430784e-05,
              "target_position_change": -3.2430784e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.992e-09,
              "target_id": "index:australia-200",
              "target_position": -1.39836e-06,
              "target_position_change": 3.1032424e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.736e-09,
              "target_id": "index:australia-200",
              "target_position": -8.390844e-06,
              "target_position_change": -6.992484e-06
            }
          ],
          "turnover": 7.0455692e-05
        },
        "index:us-500": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000657109935,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.4768e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000657109935,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.6006e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000657109935,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.7243e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000657109935,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.9718e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000657109935,
          "gross_mean": -1.4768e-08,
          "gross_total": -4.4305e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -8.2494e-08,
              "target_id": "index:us-500",
              "target_position": -3.825611e-05,
              "target_position_change": -3.825611e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.543e-08,
              "target_id": "index:us-500",
              "target_position": -9.587107e-06,
              "target_position_change": 2.8669003e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.2759e-08,
              "target_id": "index:us-500",
              "target_position": -9.08819e-06,
              "target_position_change": 4.98917e-07
            }
          ],
          "turnover": 6.742403e-05
        }
      },
      "break_even_cost": -0.000129042317,
      "gross_mean": -3.457e-09,
      "gross_total": -6.223e-08,
      "horizon": {
        "15": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000129042317,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.457e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000129042317,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.6853e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000129042317,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.0249e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000129042317,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.704e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000129042317,
          "gross_mean": -3.457e-09,
          "gross_total": -6.223e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -3.17472e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -6.7861703e-05,
              "target_position_change": -6.7861703e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.5708e-08,
              "target_id": "commodity:us-crude",
              "target_position": -3.6189647e-05,
              "target_position_change": -3.6189647e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -5.0552e-08,
              "target_id": "fx:aud-usd",
              "target_position": -4.99198e-05,
              "target_position_change": -4.99198e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.8668e-08,
              "target_id": "fx:eur-usd",
              "target_position": -5.0766856e-05,
              "target_position_change": -5.0766856e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -5.6347e-08,
              "target_id": "index:australia-200",
              "target_position": -3.2430784e-05,
              "target_position_change": -3.2430784e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -8.2494e-08,
              "target_id": "index:us-500",
              "target_position": -3.825611e-05,
              "target_position_change": -3.825611e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 6.3964e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -2.4498942e-05,
              "target_position_change": 4.3362761e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.46578e-07,
              "target_id": "commodity:us-crude",
              "target_position": -5.0554595e-05,
              "target_position_change": -1.4364948e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.3915e-08,
              "target_id": "fx:aud-usd",
              "target_position": -1.8679658e-05,
              "target_position_change": 3.1240142e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.8724e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.4574573e-05,
              "target_position_change": 2.6192283e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.992e-09,
              "target_id": "index:australia-200",
              "target_position": -1.39836e-06,
              "target_position_change": 3.1032424e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.543e-08,
              "target_id": "index:us-500",
              "target_position": -9.587107e-06,
              "target_position_change": 2.8669003e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 6.4862e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -2.9422365e-05,
              "target_position_change": -4.923423e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 6.5309e-08,
              "target_id": "commodity:us-crude",
              "target_position": -3.2173964e-05,
              "target_position_change": 1.8380631e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.2243e-08,
              "target_id": "fx:aud-usd",
              "target_position": -1.8398734e-05,
              "target_position_change": 2.80924e-07
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.6083e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.3692508e-05,
              "target_position_change": 8.82065e-07
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.736e-09,
              "target_id": "index:australia-200",
              "target_position": -8.390844e-06,
              "target_position_change": -6.992484e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.2759e-08,
              "target_id": "index:us-500",
              "target_position": -9.08819e-06,
              "target_position_change": 4.98917e-07
            }
          ],
          "turnover": 0.000482244905
        }
      },
      "period": {
        "period-0": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001851049052,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.4971e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001851049052,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.07923e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001851049052,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.30875e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001851049052,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.76779e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001851049052,
          "gross_mean": -8.4971e-08,
          "gross_total": -5.09825e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -3.17472e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -6.7861703e-05,
              "target_position_change": -6.7861703e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.5708e-08,
              "target_id": "commodity:us-crude",
              "target_position": -3.6189647e-05,
              "target_position_change": -3.6189647e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -5.0552e-08,
              "target_id": "fx:aud-usd",
              "target_position": -4.99198e-05,
              "target_position_change": -4.99198e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.8668e-08,
              "target_id": "fx:eur-usd",
              "target_position": -5.0766856e-05,
              "target_position_change": -5.0766856e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -5.6347e-08,
              "target_id": "index:australia-200",
              "target_position": -3.2430784e-05,
              "target_position_change": -3.2430784e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -8.2494e-08,
              "target_id": "index:us-500",
              "target_position": -3.825611e-05,
              "target_position_change": -3.825611e-05
            }
          ],
          "turnover": 0.0002754249
        },
        "period-1": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.001547527075,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 4.51e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001547527075,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 3.0529e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001547527075,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.5957e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001547527075,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.3187e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.001547527075,
          "gross_mean": 4.51e-08,
          "gross_total": 2.70603e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 6.3964e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -2.4498942e-05,
              "target_position_change": 4.3362761e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.46578e-07,
              "target_id": "commodity:us-crude",
              "target_position": -5.0554595e-05,
              "target_position_change": -1.4364948e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.3915e-08,
              "target_id": "fx:aud-usd",
              "target_position": -1.8679658e-05,
              "target_position_change": 3.1240142e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.8724e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.4574573e-05,
              "target_position_change": 2.6192283e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.992e-09,
              "target_id": "index:australia-200",
              "target_position": -1.39836e-06,
              "target_position_change": 3.1032424e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.543e-08,
              "target_id": "index:us-500",
              "target_position": -9.587107e-06,
              "target_position_change": 2.8669003e-05
            }
          ],
          "turnover": 0.000174861561
        },
        "period-2": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.005538192035,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 2.9499e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.005538192035,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 2.6835e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.005538192035,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 2.4172e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.005538192035,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.8846e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.005538192035,
          "gross_mean": 2.9499e-08,
          "gross_total": 1.76992e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 6.4862e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -2.9422365e-05,
              "target_position_change": -4.923423e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 6.5309e-08,
              "target_id": "commodity:us-crude",
              "target_position": -3.2173964e-05,
              "target_position_change": 1.8380631e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.2243e-08,
              "target_id": "fx:aud-usd",
              "target_position": -1.8398734e-05,
              "target_position_change": 2.80924e-07
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.6083e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.3692508e-05,
              "target_position_change": 8.82065e-07
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.736e-09,
              "target_id": "index:australia-200",
              "target_position": -8.390844e-06,
              "target_position_change": -6.992484e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.2759e-08,
              "target_id": "index:us-500",
              "target_position": -9.08819e-06,
              "target_position_change": 4.98917e-07
            }
          ],
          "turnover": 3.1958444e-05
        }
      },
      "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -3.17472e-07,
          "target_id": "commodity:spot-gold",
          "target_position": -6.7861703e-05,
          "target_position_change": -6.7861703e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 1.5708e-08,
          "target_id": "commodity:us-crude",
          "target_position": -3.6189647e-05,
          "target_position_change": -3.6189647e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -5.0552e-08,
          "target_id": "fx:aud-usd",
          "target_position": -4.99198e-05,
          "target_position_change": -4.99198e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.8668e-08,
          "target_id": "fx:eur-usd",
          "target_position": -5.0766856e-05,
          "target_position_change": -5.0766856e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -5.6347e-08,
          "target_id": "index:australia-200",
          "target_position": -3.2430784e-05,
          "target_position_change": -3.2430784e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -8.2494e-08,
          "target_id": "index:us-500",
          "target_position": -3.825611e-05,
          "target_position_change": -3.825611e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 6.3964e-08,
          "target_id": "commodity:spot-gold",
          "target_position": -2.4498942e-05,
          "target_position_change": 4.3362761e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.46578e-07,
          "target_id": "commodity:us-crude",
          "target_position": -5.0554595e-05,
          "target_position_change": -1.4364948e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.3915e-08,
          "target_id": "fx:aud-usd",
          "target_position": -1.8679658e-05,
          "target_position_change": 3.1240142e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.8724e-08,
          "target_id": "fx:eur-usd",
          "target_position": -2.4574573e-05,
          "target_position_change": 2.6192283e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.992e-09,
          "target_id": "index:australia-200",
          "target_position": -1.39836e-06,
          "target_position_change": 3.1032424e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.543e-08,
          "target_id": "index:us-500",
          "target_position": -9.587107e-06,
          "target_position_change": 2.8669003e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 6.4862e-08,
          "target_id": "commodity:spot-gold",
          "target_position": -2.9422365e-05,
          "target_position_change": -4.923423e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 6.5309e-08,
          "target_id": "commodity:us-crude",
          "target_position": -3.2173964e-05,
          "target_position_change": 1.8380631e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.2243e-08,
          "target_id": "fx:aud-usd",
          "target_position": -1.8398734e-05,
          "target_position_change": 2.80924e-07
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.6083e-08,
          "target_id": "fx:eur-usd",
          "target_position": -2.3692508e-05,
          "target_position_change": 8.82065e-07
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 5.736e-09,
          "target_id": "index:australia-200",
          "target_position": -8.390844e-06,
          "target_position_change": -6.992484e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.2759e-08,
          "target_id": "index:us-500",
          "target_position": -9.08819e-06,
          "target_position_change": 4.98917e-07
        }
      ],
      "trace_id": "fixed_graph",
      "turnover": 0.000482244905
    },
    "linear_ridge": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 2.935e-09,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.2192e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -4.7319e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -9.7573e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "asset": {
        "commodity:spot-gold": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 8.6472e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 5.8587e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 3.0702e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.5068e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.001550509656,
          "gross_mean": 8.6472e-08,
          "gross_total": 2.59415e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 2.98861e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.775e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -5.7196e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            }
          ],
          "turnover": 0.000167309503
        },
        "commodity:us-crude": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.32533e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.98568e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.64603e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.96673e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001003509266,
          "gross_mean": -1.32533e-07,
          "gross_total": -3.976e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.1008e-08,
              "target_id": "commodity:us-crude",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -3.58016e-07,
              "target_id": "commodity:us-crude",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.0592e-08,
              "target_id": "commodity:us-crude",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            }
          ],
          "turnover": 0.000396209595
        },
        "fx:aud-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 6.667e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.36e-10,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.539e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.1745e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000469296764,
          "gross_mean": 6.667e-09,
          "gross_total": 2e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.6153e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.674e-08,
              "target_id": "fx:aud-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9413e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            }
          ],
          "turnover": 4.2616957e-05
        },
        "fx:eur-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.04e-10,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.15e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.3896e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.7388e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -2.9918097e-05,
          "gross_mean": -4.04e-10,
          "gross_total": -1.211e-09,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -7.939e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.892e-09,
              "target_id": "fx:eur-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.836e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            }
          ],
          "turnover": 4.0477174e-05
        },
        "index:australia-200": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.0517e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.7048e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.4613e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -9.9743e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000190766717,
          "gross_mean": 1.0517e-08,
          "gross_total": 3.1551e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.96802e-07,
              "target_id": "index:australia-200",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.74201e-07,
              "target_id": "index:australia-200",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.4152e-08,
              "target_id": "index:australia-200",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            }
          ],
          "turnover": 0.000165390486
        },
        "index:us-500": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 4.6891e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 3.1464e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.6037e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.4818e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.001519753375,
          "gross_mean": 4.6891e-08,
          "gross_total": 1.40673e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.81446e-07,
              "target_id": "index:us-500",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.15805e-07,
              "target_id": "index:us-500",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.06314e-07,
              "target_id": "index:us-500",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            }
          ],
          "turnover": 9.2563045e-05
        }
      },
      "break_even_cost": 5.8401438e-05,
      "gross_mean": 2.935e-09,
      "gross_total": 5.2828e-08,
      "horizon": {
        "15": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 2.935e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.2192e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.7319e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -9.7573e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 5.8401438e-05,
          "gross_mean": 2.935e-09,
          "gross_total": 5.2828e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 2.98861e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.1008e-08,
              "target_id": "commodity:us-crude",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.6153e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -7.939e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.96802e-07,
              "target_id": "index:australia-200",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.81446e-07,
              "target_id": "index:us-500",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.775e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -3.58016e-07,
              "target_id": "commodity:us-crude",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.674e-08,
              "target_id": "fx:aud-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.892e-09,
              "target_id": "fx:eur-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.74201e-07,
              "target_id": "index:australia-200",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.15805e-07,
              "target_id": "index:us-500",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -5.7196e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.0592e-08,
              "target_id": "commodity:us-crude",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9413e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.836e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.4152e-08,
              "target_id": "index:australia-200",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.06314e-07,
              "target_id": "index:us-500",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            }
          ],
          "turnover": 0.00090456676
        }
      },
      "period": {
        "period-0": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.2078e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.5678e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.9277e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.46476e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000179743091,
          "gross_mean": -1.2078e-08,
          "gross_total": -7.2471e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 2.98861e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.1008e-08,
              "target_id": "commodity:us-crude",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.6153e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -7.939e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.96802e-07,
              "target_id": "index:australia-200",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.81446e-07,
              "target_id": "index:us-500",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            }
          ],
          "turnover": 0.000403192131
        },
        "period-1": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.3562e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.3579e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.072e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -9.5003e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.00024984115,
          "gross_mean": 1.3562e-08,
          "gross_total": 8.1372e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.775e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -3.58016e-07,
              "target_id": "commodity:us-crude",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.674e-08,
              "target_id": "fx:aud-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.892e-09,
              "target_id": "fx:eur-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.74201e-07,
              "target_id": "index:australia-200",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.15805e-07,
              "target_id": "index:us-500",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            }
          ],
          "turnover": 0.000325694947
        },
        "period-2": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 7.321e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.319e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.1959e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.1239e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000250040298,
          "gross_mean": 7.321e-09,
          "gross_total": 4.3927e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -5.7196e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.0592e-08,
              "target_id": "commodity:us-crude",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9413e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.836e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.4152e-08,
              "target_id": "index:australia-200",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.06314e-07,
              "target_id": "index:us-500",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            }
          ],
          "turnover": 0.000175679682
        }
      },
      "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 2.98861e-07,
          "target_id": "commodity:spot-gold",
          "target_position": 6.3883617e-05,
          "target_position_change": 6.3883617e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 4.1008e-08,
          "target_id": "commodity:us-crude",
          "target_position": -9.4476664e-05,
          "target_position_change": -9.4476664e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -2.6153e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.58259e-05,
          "target_position_change": -2.58259e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -7.939e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.159062e-05,
          "target_position_change": -2.159062e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.96802e-07,
          "target_id": "index:australia-200",
          "target_position": -0.00011327098,
          "target_position_change": -0.00011327098
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.81446e-07,
          "target_id": "index:us-500",
          "target_position": -8.414435e-05,
          "target_position_change": -8.414435e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.775e-08,
          "target_id": "commodity:spot-gold",
          "target_position": -6.798524e-06,
          "target_position_change": -7.0682141e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -3.58016e-07,
          "target_id": "commodity:us-crude",
          "target_position": 0.000123479741,
          "target_position_change": 0.000217956405
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.674e-08,
          "target_id": "fx:aud-usd",
          "target_position": -3.5894947e-05,
          "target_position_change": -1.0069047e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 4.892e-09,
          "target_id": "fx:eur-usd",
          "target_position": -6.420369e-06,
          "target_position_change": 1.5170251e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.74201e-07,
          "target_id": "index:australia-200",
          "target_position": -0.000122301435,
          "target_position_change": -9.030455e-06
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.15805e-07,
          "target_id": "index:us-500",
          "target_position": -8.1357702e-05,
          "target_position_change": 2.786648e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -5.7196e-08,
          "target_id": "commodity:spot-gold",
          "target_position": 2.5945221e-05,
          "target_position_change": 3.2743745e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -8.0592e-08,
          "target_id": "commodity:us-crude",
          "target_position": 3.9703215e-05,
          "target_position_change": -8.3776526e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.9413e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.9172937e-05,
          "target_position_change": 6.72201e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.836e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.704066e-06,
          "target_position_change": 3.716303e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 5.4152e-08,
          "target_id": "index:australia-200",
          "target_position": -7.9212384e-05,
          "target_position_change": 4.3089051e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.06314e-07,
          "target_id": "index:us-500",
          "target_position": -7.5725655e-05,
          "target_position_change": 5.632047e-06
        }
      ],
      "trace_id": "linear_ridge",
      "turnover": 0.00090456676
    },
    "linear_zero_return": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": null,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 0.0,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": null,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 0.0,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": null,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 0.0,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": null,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 0.0,
          "unit": "fraction_of_notional"
        }
      ],
      "asset": {
        "commodity:spot-gold": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "commodity:us-crude": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "fx:aud-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "fx:eur-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "index:australia-200": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "index:us-500": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        }
      },
      "break_even_cost": null,
      "gross_mean": 0.0,
      "gross_total": 0.0,
      "horizon": {
        "15": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        }
      },
      "period": {
        "period-0": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "period-1": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "period-2": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        }
      },
      "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "commodity:spot-gold",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:us-crude",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "fx:aud-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "fx:eur-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "index:australia-200",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "index:us-500",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:spot-gold",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:us-crude",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "fx:aud-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "fx:eur-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "index:australia-200",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "index:us-500",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:spot-gold",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:us-crude",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "fx:aud-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "fx:eur-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "index:australia-200",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "index:us-500",
          "target_position": 0.0,
          "target_position_change": 0.0
        }
      ],
      "trace_id": "linear_zero_return",
      "turnover": 0.0
    },
    "local_non_graph": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 2.935e-09,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.2192e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -4.7319e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -9.7573e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "asset": {
        "commodity:spot-gold": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 8.6472e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 5.8587e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 3.0702e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.5068e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.001550509656,
          "gross_mean": 8.6472e-08,
          "gross_total": 2.59415e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 2.98861e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.775e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -5.7196e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            }
          ],
          "turnover": 0.000167309503
        },
        "commodity:us-crude": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.32533e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.98568e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.64603e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.96673e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001003509266,
          "gross_mean": -1.32533e-07,
          "gross_total": -3.976e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.1008e-08,
              "target_id": "commodity:us-crude",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -3.58016e-07,
              "target_id": "commodity:us-crude",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.0592e-08,
              "target_id": "commodity:us-crude",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            }
          ],
          "turnover": 0.000396209595
        },
        "fx:aud-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 6.667e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.36e-10,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.539e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.1745e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000469296764,
          "gross_mean": 6.667e-09,
          "gross_total": 2e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.6153e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.674e-08,
              "target_id": "fx:aud-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9413e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            }
          ],
          "turnover": 4.2616957e-05
        },
        "fx:eur-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.04e-10,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.15e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.3896e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.7388e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -2.9918097e-05,
          "gross_mean": -4.04e-10,
          "gross_total": -1.211e-09,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -7.939e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.892e-09,
              "target_id": "fx:eur-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.836e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            }
          ],
          "turnover": 4.0477174e-05
        },
        "index:australia-200": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.0517e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.7048e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.4613e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -9.9743e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000190766717,
          "gross_mean": 1.0517e-08,
          "gross_total": 3.1551e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.96802e-07,
              "target_id": "index:australia-200",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.74201e-07,
              "target_id": "index:australia-200",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.4152e-08,
              "target_id": "index:australia-200",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            }
          ],
          "turnover": 0.000165390486
        },
        "index:us-500": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 4.6891e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 3.1464e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.6037e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.4818e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.001519753375,
          "gross_mean": 4.6891e-08,
          "gross_total": 1.40673e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.81446e-07,
              "target_id": "index:us-500",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.15805e-07,
              "target_id": "index:us-500",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.06314e-07,
              "target_id": "index:us-500",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            }
          ],
          "turnover": 9.2563045e-05
        }
      },
      "break_even_cost": 5.8401438e-05,
      "gross_mean": 2.935e-09,
      "gross_total": 5.2828e-08,
      "horizon": {
        "15": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 2.935e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.2192e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.7319e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -9.7573e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 5.8401438e-05,
          "gross_mean": 2.935e-09,
          "gross_total": 5.2828e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 2.98861e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.1008e-08,
              "target_id": "commodity:us-crude",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.6153e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -7.939e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.96802e-07,
              "target_id": "index:australia-200",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.81446e-07,
              "target_id": "index:us-500",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.775e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -3.58016e-07,
              "target_id": "commodity:us-crude",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.674e-08,
              "target_id": "fx:aud-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.892e-09,
              "target_id": "fx:eur-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.74201e-07,
              "target_id": "index:australia-200",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.15805e-07,
              "target_id": "index:us-500",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -5.7196e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.0592e-08,
              "target_id": "commodity:us-crude",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9413e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.836e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.4152e-08,
              "target_id": "index:australia-200",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.06314e-07,
              "target_id": "index:us-500",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            }
          ],
          "turnover": 0.00090456676
        }
      },
      "period": {
        "period-0": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.2078e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.5678e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.9277e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.46476e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000179743091,
          "gross_mean": -1.2078e-08,
          "gross_total": -7.2471e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 2.98861e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.1008e-08,
              "target_id": "commodity:us-crude",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.6153e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -7.939e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.96802e-07,
              "target_id": "index:australia-200",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.81446e-07,
              "target_id": "index:us-500",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            }
          ],
          "turnover": 0.000403192131
        },
        "period-1": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.3562e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.3579e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.072e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -9.5003e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.00024984115,
          "gross_mean": 1.3562e-08,
          "gross_total": 8.1372e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.775e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -3.58016e-07,
              "target_id": "commodity:us-crude",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.674e-08,
              "target_id": "fx:aud-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.892e-09,
              "target_id": "fx:eur-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.74201e-07,
              "target_id": "index:australia-200",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.15805e-07,
              "target_id": "index:us-500",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            }
          ],
          "turnover": 0.000325694947
        },
        "period-2": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 7.321e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.319e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.1959e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.1239e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000250040298,
          "gross_mean": 7.321e-09,
          "gross_total": 4.3927e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -5.7196e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.0592e-08,
              "target_id": "commodity:us-crude",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9413e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.836e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.4152e-08,
              "target_id": "index:australia-200",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.06314e-07,
              "target_id": "index:us-500",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            }
          ],
          "turnover": 0.000175679682
        }
      },
      "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 2.98861e-07,
          "target_id": "commodity:spot-gold",
          "target_position": 6.3883617e-05,
          "target_position_change": 6.3883617e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 4.1008e-08,
          "target_id": "commodity:us-crude",
          "target_position": -9.4476664e-05,
          "target_position_change": -9.4476664e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -2.6153e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.58259e-05,
          "target_position_change": -2.58259e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -7.939e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.159062e-05,
          "target_position_change": -2.159062e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.96802e-07,
          "target_id": "index:australia-200",
          "target_position": -0.00011327098,
          "target_position_change": -0.00011327098
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.81446e-07,
          "target_id": "index:us-500",
          "target_position": -8.414435e-05,
          "target_position_change": -8.414435e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.775e-08,
          "target_id": "commodity:spot-gold",
          "target_position": -6.798524e-06,
          "target_position_change": -7.0682141e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -3.58016e-07,
          "target_id": "commodity:us-crude",
          "target_position": 0.000123479741,
          "target_position_change": 0.000217956405
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.674e-08,
          "target_id": "fx:aud-usd",
          "target_position": -3.5894947e-05,
          "target_position_change": -1.0069047e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 4.892e-09,
          "target_id": "fx:eur-usd",
          "target_position": -6.420369e-06,
          "target_position_change": 1.5170251e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.74201e-07,
          "target_id": "index:australia-200",
          "target_position": -0.000122301435,
          "target_position_change": -9.030455e-06
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.15805e-07,
          "target_id": "index:us-500",
          "target_position": -8.1357702e-05,
          "target_position_change": 2.786648e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -5.7196e-08,
          "target_id": "commodity:spot-gold",
          "target_position": 2.5945221e-05,
          "target_position_change": 3.2743745e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -8.0592e-08,
          "target_id": "commodity:us-crude",
          "target_position": 3.9703215e-05,
          "target_position_change": -8.3776526e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.9413e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.9172937e-05,
          "target_position_change": 6.72201e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.836e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.704066e-06,
          "target_position_change": 3.716303e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 5.4152e-08,
          "target_id": "index:australia-200",
          "target_position": -7.9212384e-05,
          "target_position_change": 4.3089051e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.06314e-07,
          "target_id": "index:us-500",
          "target_position": -7.5725655e-05,
          "target_position_change": 5.632047e-06
        }
      ],
      "trace_id": "local_non_graph",
      "turnover": 0.00090456676
    },
    "local_ridge": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 2.935e-09,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.2192e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -4.7319e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -9.7573e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "asset": {
        "commodity:spot-gold": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 8.6472e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 5.8587e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 3.0702e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001550509656,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.5068e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.001550509656,
          "gross_mean": 8.6472e-08,
          "gross_total": 2.59415e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 2.98861e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.775e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -5.7196e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            }
          ],
          "turnover": 0.000167309503
        },
        "commodity:us-crude": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.32533e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.98568e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.64603e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001003509266,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.96673e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001003509266,
          "gross_mean": -1.32533e-07,
          "gross_total": -3.976e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.1008e-08,
              "target_id": "commodity:us-crude",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -3.58016e-07,
              "target_id": "commodity:us-crude",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.0592e-08,
              "target_id": "commodity:us-crude",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            }
          ],
          "turnover": 0.000396209595
        },
        "fx:aud-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 6.667e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.36e-10,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.539e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000469296764,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.1745e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000469296764,
          "gross_mean": 6.667e-09,
          "gross_total": 2e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.6153e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.674e-08,
              "target_id": "fx:aud-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9413e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            }
          ],
          "turnover": 4.2616957e-05
        },
        "fx:eur-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.04e-10,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.15e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.3896e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -2.9918097e-05,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.7388e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -2.9918097e-05,
          "gross_mean": -4.04e-10,
          "gross_total": -1.211e-09,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -7.939e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.892e-09,
              "target_id": "fx:eur-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.836e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            }
          ],
          "turnover": 4.0477174e-05
        },
        "index:australia-200": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.0517e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.7048e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.4613e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000190766717,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -9.9743e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000190766717,
          "gross_mean": 1.0517e-08,
          "gross_total": 3.1551e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.96802e-07,
              "target_id": "index:australia-200",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.74201e-07,
              "target_id": "index:australia-200",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.4152e-08,
              "target_id": "index:australia-200",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            }
          ],
          "turnover": 0.000165390486
        },
        "index:us-500": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 4.6891e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 3.1464e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.6037e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.001519753375,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.4818e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.001519753375,
          "gross_mean": 4.6891e-08,
          "gross_total": 1.40673e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.81446e-07,
              "target_id": "index:us-500",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.15805e-07,
              "target_id": "index:us-500",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.06314e-07,
              "target_id": "index:us-500",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            }
          ],
          "turnover": 9.2563045e-05
        }
      },
      "break_even_cost": 5.8401438e-05,
      "gross_mean": 2.935e-09,
      "gross_total": 5.2828e-08,
      "horizon": {
        "15": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 2.935e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.2192e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.7319e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 5.8401438e-05,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -9.7573e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 5.8401438e-05,
          "gross_mean": 2.935e-09,
          "gross_total": 5.2828e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 2.98861e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.1008e-08,
              "target_id": "commodity:us-crude",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.6153e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -7.939e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.96802e-07,
              "target_id": "index:australia-200",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.81446e-07,
              "target_id": "index:us-500",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.775e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -3.58016e-07,
              "target_id": "commodity:us-crude",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.674e-08,
              "target_id": "fx:aud-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.892e-09,
              "target_id": "fx:eur-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.74201e-07,
              "target_id": "index:australia-200",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.15805e-07,
              "target_id": "index:us-500",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -5.7196e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.0592e-08,
              "target_id": "commodity:us-crude",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9413e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.836e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.4152e-08,
              "target_id": "index:australia-200",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.06314e-07,
              "target_id": "index:us-500",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            }
          ],
          "turnover": 0.00090456676
        }
      },
      "period": {
        "period-0": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.2078e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.5678e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.9277e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000179743091,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.46476e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000179743091,
          "gross_mean": -1.2078e-08,
          "gross_total": -7.2471e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 2.98861e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.1008e-08,
              "target_id": "commodity:us-crude",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.6153e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -7.939e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.96802e-07,
              "target_id": "index:australia-200",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.81446e-07,
              "target_id": "index:us-500",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            }
          ],
          "turnover": 0.000403192131
        },
        "period-1": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.3562e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.3579e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.072e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00024984115,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -9.5003e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.00024984115,
          "gross_mean": 1.3562e-08,
          "gross_total": 8.1372e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.775e-08,
              "target_id": "commodity:spot-gold",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -3.58016e-07,
              "target_id": "commodity:us-crude",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.674e-08,
              "target_id": "fx:aud-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.892e-09,
              "target_id": "fx:eur-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.74201e-07,
              "target_id": "index:australia-200",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.15805e-07,
              "target_id": "index:us-500",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            }
          ],
          "turnover": 0.000325694947
        },
        "period-2": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 7.321e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.319e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.1959e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000250040298,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.1239e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000250040298,
          "gross_mean": 7.321e-09,
          "gross_total": 4.3927e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -5.7196e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.0592e-08,
              "target_id": "commodity:us-crude",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9413e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.836e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 5.4152e-08,
              "target_id": "index:australia-200",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.06314e-07,
              "target_id": "index:us-500",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            }
          ],
          "turnover": 0.000175679682
        }
      },
      "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 2.98861e-07,
          "target_id": "commodity:spot-gold",
          "target_position": 6.3883617e-05,
          "target_position_change": 6.3883617e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 4.1008e-08,
          "target_id": "commodity:us-crude",
          "target_position": -9.4476664e-05,
          "target_position_change": -9.4476664e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -2.6153e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.58259e-05,
          "target_position_change": -2.58259e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -7.939e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.159062e-05,
          "target_position_change": -2.159062e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.96802e-07,
          "target_id": "index:australia-200",
          "target_position": -0.00011327098,
          "target_position_change": -0.00011327098
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.81446e-07,
          "target_id": "index:us-500",
          "target_position": -8.414435e-05,
          "target_position_change": -8.414435e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.775e-08,
          "target_id": "commodity:spot-gold",
          "target_position": -6.798524e-06,
          "target_position_change": -7.0682141e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -3.58016e-07,
          "target_id": "commodity:us-crude",
          "target_position": 0.000123479741,
          "target_position_change": 0.000217956405
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.674e-08,
          "target_id": "fx:aud-usd",
          "target_position": -3.5894947e-05,
          "target_position_change": -1.0069047e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 4.892e-09,
          "target_id": "fx:eur-usd",
          "target_position": -6.420369e-06,
          "target_position_change": 1.5170251e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.74201e-07,
          "target_id": "index:australia-200",
          "target_position": -0.000122301435,
          "target_position_change": -9.030455e-06
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.15805e-07,
          "target_id": "index:us-500",
          "target_position": -8.1357702e-05,
          "target_position_change": 2.786648e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -5.7196e-08,
          "target_id": "commodity:spot-gold",
          "target_position": 2.5945221e-05,
          "target_position_change": 3.2743745e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -8.0592e-08,
          "target_id": "commodity:us-crude",
          "target_position": 3.9703215e-05,
          "target_position_change": -8.3776526e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.9413e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.9172937e-05,
          "target_position_change": 6.72201e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.836e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.704066e-06,
          "target_position_change": 3.716303e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 5.4152e-08,
          "target_id": "index:australia-200",
          "target_position": -7.9212384e-05,
          "target_position_change": 4.3089051e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.06314e-07,
          "target_id": "index:us-500",
          "target_position": -7.5725655e-05,
          "target_position_change": 5.632047e-06
        }
      ],
      "trace_id": "local_ridge",
      "turnover": 0.00090456676
    },
    "nonlinear_huber": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": -0.002819827256,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.349411e-06,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.002819827256,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.765999e-06,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.002819827256,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -3.182587e-06,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.002819827256,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -4.015762e-06,
          "unit": "fraction_of_notional"
        }
      ],
      "asset": {
        "commodity:spot-gold": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.00429736671,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.024657e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.00429736671,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.492927e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.00429736671,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.961197e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.00429736671,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.897738e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.00429736671,
          "gross_mean": -4.024657e-06,
          "gross_total": -1.2073971e-05,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -5.880162e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.002252161542,
              "target_position_change": 0.002252161542
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -6.193809e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.002809620825,
              "target_position_change": 0.000557459283
            }
          ],
          "turnover": 0.002809620825
        },
        "commodity:us-crude": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.003245915993,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.379767e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.003245915993,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.362504e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.003245915993,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.345242e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.003245915993,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.0310717e-05,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.003245915993,
          "gross_mean": -6.379767e-06,
          "gross_total": -1.91393e-05,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.2960685e-05,
              "target_id": "commodity:us-crude",
              "target_position": 0.00447013718,
              "target_position_change": 0.00447013718
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -6.178615e-06,
              "target_id": "commodity:us-crude",
              "target_position": 0.003043849425,
              "target_position_change": -0.001426287755
            }
          ],
          "turnover": 0.005896424935
        },
        "fx:aud-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001364841015,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.51315e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001364841015,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.163188e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001364841015,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.475061e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001364841015,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.098807e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001364841015,
          "gross_mean": -8.51315e-07,
          "gross_total": -2.553944e-06,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.308734e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001756797587,
              "target_position_change": 0.001756797587
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.24521e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001871239193,
              "target_position_change": 0.000114441606
            }
          ],
          "turnover": 0.001871239193
        },
        "fx:eur-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001419990338,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.099011e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001419990338,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.485989e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001419990338,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.872967e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001419990338,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.646924e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001419990338,
          "gross_mean": -1.099011e-06,
          "gross_total": -3.297032e-06,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.72086e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.002258599595,
              "target_position_change": 0.002258599595
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.576172e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.00232186932,
              "target_position_change": 6.3269725e-05
            }
          ],
          "turnover": 0.00232186932
        },
        "index:australia-200": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001082904957,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.67943e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001082904957,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.3783e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001082904957,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.07717e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001082904957,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.047491e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001082904957,
          "gross_mean": -3.67943e-07,
          "gross_total": -1.103829e-06,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.06988e-07,
              "target_id": "index:australia-200",
              "target_position": 0.000285734965,
              "target_position_change": 0.000285734965
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -6.96841e-07,
              "target_id": "index:australia-200",
              "target_position": 0.001019322142,
              "target_position_change": 0.000733587177
            }
          ],
          "turnover": 0.001019322142
        },
        "index:us-500": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.003820698469,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.373775e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.003820698469,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.553555e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.003820698469,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.733336e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.003820698469,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.092897e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.003820698469,
          "gross_mean": -1.373775e-06,
          "gross_total": -4.121324e-06,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -2.60692e-06,
              "target_id": "index:us-500",
              "target_position": 0.000982798306,
              "target_position_change": 0.000982798306
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.514404e-06,
              "target_id": "index:us-500",
              "target_position": 0.001078683396,
              "target_position_change": 9.588509e-05
            }
          ],
          "turnover": 0.001078683396
        }
      },
      "break_even_cost": -0.002819827256,
      "gross_mean": -2.349411e-06,
      "gross_total": -4.22894e-05,
      "horizon": {
        "15": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.002819827256,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.349411e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.002819827256,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.765999e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.002819827256,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.182587e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.002819827256,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.015762e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.002819827256,
          "gross_mean": -2.349411e-06,
          "gross_total": -4.22894e-05,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -5.880162e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.002252161542,
              "target_position_change": 0.002252161542
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.2960685e-05,
              "target_id": "commodity:us-crude",
              "target_position": 0.00447013718,
              "target_position_change": 0.00447013718
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.308734e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001756797587,
              "target_position_change": 0.001756797587
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.72086e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.002258599595,
              "target_position_change": 0.002258599595
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.06988e-07,
              "target_id": "index:australia-200",
              "target_position": 0.000285734965,
              "target_position_change": 0.000285734965
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -2.60692e-06,
              "target_id": "index:us-500",
              "target_position": 0.000982798306,
              "target_position_change": 0.000982798306
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -6.193809e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.002809620825,
              "target_position_change": 0.000557459283
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -6.178615e-06,
              "target_id": "commodity:us-crude",
              "target_position": 0.003043849425,
              "target_position_change": -0.001426287755
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.24521e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001871239193,
              "target_position_change": 0.000114441606
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.576172e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.00232186932,
              "target_position_change": 6.3269725e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -6.96841e-07,
              "target_id": "index:australia-200",
              "target_position": 0.001019322142,
              "target_position_change": 0.000733587177
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.514404e-06,
              "target_id": "index:us-500",
              "target_position": 0.001078683396,
              "target_position_change": 9.588509e-05
            }
          ],
          "turnover": 0.014997159811
        }
      },
      "period": {
        "period-0": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "period-1": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.002072619857,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.147392e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.002072619857,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.147911e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.002072619857,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.14843e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.002072619857,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.149468e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.002072619857,
          "gross_mean": -4.147392e-06,
          "gross_total": -2.4884349e-05,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -5.880162e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.002252161542,
              "target_position_change": 0.002252161542
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.2960685e-05,
              "target_id": "commodity:us-crude",
              "target_position": 0.00447013718,
              "target_position_change": 0.00447013718
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.308734e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001756797587,
              "target_position_change": 0.001756797587
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.72086e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.002258599595,
              "target_position_change": 0.002258599595
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.06988e-07,
              "target_id": "index:australia-200",
              "target_position": 0.000285734965,
              "target_position_change": 0.000285734965
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -2.60692e-06,
              "target_id": "index:us-500",
              "target_position": 0.000982798306,
              "target_position_change": 0.000982798306
            }
          ],
          "turnover": 0.012006229175
        },
        "period-2": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.005819276044,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.900842e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.005819276044,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.150086e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.005819276044,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.39933e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.005819276044,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.897819e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.005819276044,
          "gross_mean": -2.900842e-06,
          "gross_total": -1.7405051e-05,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -6.193809e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.002809620825,
              "target_position_change": 0.000557459283
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -6.178615e-06,
              "target_id": "commodity:us-crude",
              "target_position": 0.003043849425,
              "target_position_change": -0.001426287755
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.24521e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001871239193,
              "target_position_change": 0.000114441606
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.576172e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.00232186932,
              "target_position_change": 6.3269725e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -6.96841e-07,
              "target_id": "index:australia-200",
              "target_position": 0.001019322142,
              "target_position_change": 0.000733587177
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.514404e-06,
              "target_id": "index:us-500",
              "target_position": 0.001078683396,
              "target_position_change": 9.588509e-05
            }
          ],
          "turnover": 0.002990930636
        }
      },
      "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "commodity:spot-gold",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:us-crude",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "fx:aud-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "fx:eur-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "index:australia-200",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "index:us-500",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -5.880162e-06,
          "target_id": "commodity:spot-gold",
          "target_position": 0.002252161542,
          "target_position_change": 0.002252161542
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -1.2960685e-05,
          "target_id": "commodity:us-crude",
          "target_position": 0.00447013718,
          "target_position_change": 0.00447013718
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -1.308734e-06,
          "target_id": "fx:aud-usd",
          "target_position": 0.001756797587,
          "target_position_change": 0.001756797587
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -1.72086e-06,
          "target_id": "fx:eur-usd",
          "target_position": 0.002258599595,
          "target_position_change": 0.002258599595
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -4.06988e-07,
          "target_id": "index:australia-200",
          "target_position": 0.000285734965,
          "target_position_change": 0.000285734965
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -2.60692e-06,
          "target_id": "index:us-500",
          "target_position": 0.000982798306,
          "target_position_change": 0.000982798306
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -6.193809e-06,
          "target_id": "commodity:spot-gold",
          "target_position": 0.002809620825,
          "target_position_change": 0.000557459283
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -6.178615e-06,
          "target_id": "commodity:us-crude",
          "target_position": 0.003043849425,
          "target_position_change": -0.001426287755
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -1.24521e-06,
          "target_id": "fx:aud-usd",
          "target_position": 0.001871239193,
          "target_position_change": 0.000114441606
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -1.576172e-06,
          "target_id": "fx:eur-usd",
          "target_position": 0.00232186932,
          "target_position_change": 6.3269725e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -6.96841e-07,
          "target_id": "index:australia-200",
          "target_position": 0.001019322142,
          "target_position_change": 0.000733587177
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -1.514404e-06,
          "target_id": "index:us-500",
          "target_position": 0.001078683396,
          "target_position_change": 9.588509e-05
        }
      ],
      "trace_id": "nonlinear_huber",
      "turnover": 0.014997159811
    },
    "pooled_local_ridge": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": -0.000291708681,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.4704e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000291708681,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -3.9907e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000291708681,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -6.511e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000291708681,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.15516e-07,
          "unit": "fraction_of_notional"
        }
      ],
      "asset": {
        "commodity:spot-gold": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001574120518,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.1251e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001574120518,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.4353e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001574120518,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.7456e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001574120518,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -9.3662e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001574120518,
          "gross_mean": -4.1251e-08,
          "gross_total": -1.23752e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.9981e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 4.271108e-06,
              "target_position_change": 4.271108e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 3.997e-09,
              "target_id": "commodity:spot-gold",
              "target_position": -1.530712e-06,
              "target_position_change": -5.80182e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.4773e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.7012958e-05,
              "target_position_change": 6.854367e-05
            }
          ],
          "turnover": 7.8616598e-05
        },
        "commodity:us-crude": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.00032687685,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 3.9978e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00032687685,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.1174e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00032687685,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.2326e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00032687685,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.0463e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.00032687685,
          "gross_mean": 3.9978e-08,
          "gross_total": 1.19935e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 6.2309e-08,
              "target_id": "commodity:us-crude",
              "target_position": -0.000143549699,
              "target_position_change": -0.000143549699
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.3489e-08,
              "target_id": "commodity:us-crude",
              "target_position": 1.4999489e-05,
              "target_position_change": 0.000158549188
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.01115e-07,
              "target_id": "commodity:us-crude",
              "target_position": -4.9813505e-05,
              "target_position_change": -6.4812994e-05
            }
          ],
          "turnover": 0.000366911881
        },
        "fx:aud-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001121478571,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.1017e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001121478571,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.5929e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001121478571,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.0841e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001121478571,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.0665e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001121478571,
          "gross_mean": -1.1017e-08,
          "gross_total": -3.3052e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.504e-09,
              "target_id": "fx:aud-usd",
              "target_position": -1.485118e-06,
              "target_position_change": -1.485118e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.3913e-08,
              "target_id": "fx:aud-usd",
              "target_position": 1.8675784e-05,
              "target_position_change": 2.0160902e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.7635e-08,
              "target_id": "fx:aud-usd",
              "target_position": 2.6501571e-05,
              "target_position_change": 7.825787e-06
            }
          ],
          "turnover": 2.9471807e-05
        },
        "fx:eur-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000310753715,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.008e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000310753715,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.8285e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000310753715,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.9561e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000310753715,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.2114e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000310753715,
          "gross_mean": -7.008e-09,
          "gross_total": -2.1025e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.0749e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.9230617e-05,
              "target_position_change": -2.9230617e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -5.938e-09,
              "target_id": "fx:eur-usd",
              "target_position": 7.79337e-06,
              "target_position_change": 3.7023987e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -4.338e-09,
              "target_id": "fx:eur-usd",
              "target_position": 6.389893e-06,
              "target_position_change": -1.403477e-06
            }
          ],
          "turnover": 6.7658081e-05
        },
        "index:australia-200": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000618054369,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.4355e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000618054369,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.4057e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000618054369,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.376e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000618054369,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.03165e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000618054369,
          "gross_mean": -2.4355e-08,
          "gross_total": -7.3064e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -6.281e-08,
              "target_id": "index:australia-200",
              "target_position": -3.6151017e-05,
              "target_position_change": -3.6151017e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.1134e-08,
              "target_id": "index:australia-200",
              "target_position": -1.483772e-05,
              "target_position_change": 2.1313297e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.1388e-08,
              "target_id": "index:australia-200",
              "target_position": 4.5914103e-05,
              "target_position_change": 6.0751823e-05
            }
          ],
          "turnover": 0.000118216137
        },
        "index:us-500": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000542582217,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.4571e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000542582217,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.5644e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000542582217,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.26717e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000542582217,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.08863e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000542582217,
          "gross_mean": -4.4571e-08,
          "gross_total": -1.33713e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.99986e-07,
              "target_id": "index:us-500",
              "target_position": -9.274179e-05,
              "target_position_change": -9.274179e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.5185e-07,
              "target_id": "index:us-500",
              "target_position": -5.7246782e-05,
              "target_position_change": 3.5495008e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.5577e-08,
              "target_id": "index:us-500",
              "target_position": 6.0954648e-05,
              "target_position_change": 0.00011820143
            }
          ],
          "turnover": 0.000246438228
        }
      },
      "break_even_cost": -0.000291708681,
      "gross_mean": -1.4704e-08,
      "gross_total": -2.64671e-07,
      "horizon": {
        "15": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000291708681,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.4704e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000291708681,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.9907e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000291708681,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.511e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000291708681,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.15516e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000291708681,
          "gross_mean": -1.4704e-08,
          "gross_total": -2.64671e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.9981e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 4.271108e-06,
              "target_position_change": 4.271108e-06
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 6.2309e-08,
              "target_id": "commodity:us-crude",
              "target_position": -0.000143549699,
              "target_position_change": -0.000143549699
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.504e-09,
              "target_id": "fx:aud-usd",
              "target_position": -1.485118e-06,
              "target_position_change": -1.485118e-06
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.0749e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.9230617e-05,
              "target_position_change": -2.9230617e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -6.281e-08,
              "target_id": "index:australia-200",
              "target_position": -3.6151017e-05,
              "target_position_change": -3.6151017e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.99986e-07,
              "target_id": "index:us-500",
              "target_position": -9.274179e-05,
              "target_position_change": -9.274179e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 3.997e-09,
              "target_id": "commodity:spot-gold",
              "target_position": -1.530712e-06,
              "target_position_change": -5.80182e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.3489e-08,
              "target_id": "commodity:us-crude",
              "target_position": 1.4999489e-05,
              "target_position_change": 0.000158549188
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.3913e-08,
              "target_id": "fx:aud-usd",
              "target_position": 1.8675784e-05,
              "target_position_change": 2.0160902e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -5.938e-09,
              "target_id": "fx:eur-usd",
              "target_position": 7.79337e-06,
              "target_position_change": 3.7023987e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.1134e-08,
              "target_id": "index:australia-200",
              "target_position": -1.483772e-05,
              "target_position_change": 2.1313297e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.5185e-07,
              "target_id": "index:us-500",
              "target_position": -5.7246782e-05,
              "target_position_change": 3.5495008e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.4773e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.7012958e-05,
              "target_position_change": 6.854367e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.01115e-07,
              "target_id": "commodity:us-crude",
              "target_position": -4.9813505e-05,
              "target_position_change": -6.4812994e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.7635e-08,
              "target_id": "fx:aud-usd",
              "target_position": 2.6501571e-05,
              "target_position_change": 7.825787e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -4.338e-09,
              "target_id": "fx:eur-usd",
              "target_position": 6.389893e-06,
              "target_position_change": -1.403477e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.1388e-08,
              "target_id": "index:australia-200",
              "target_position": 4.5914103e-05,
              "target_position_change": 6.0751823e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.5577e-08,
              "target_id": "index:us-500",
              "target_position": 6.0954648e-05,
              "target_position_change": 0.00011820143
            }
          ],
          "turnover": 0.000907312732
        }
      },
      "period": {
        "period-0": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000627002596,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.2126e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000627002596,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.7746e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000627002596,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.3365e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000627002596,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.34603e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000627002596,
          "gross_mean": -3.2126e-08,
          "gross_total": -1.92759e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.9981e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 4.271108e-06,
              "target_position_change": 4.271108e-06
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 6.2309e-08,
              "target_id": "commodity:us-crude",
              "target_position": -0.000143549699,
              "target_position_change": -0.000143549699
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.504e-09,
              "target_id": "fx:aud-usd",
              "target_position": -1.485118e-06,
              "target_position_change": -1.485118e-06
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.0749e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.9230617e-05,
              "target_position_change": -2.9230617e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -6.281e-08,
              "target_id": "index:australia-200",
              "target_position": -3.6151017e-05,
              "target_position_change": -3.6151017e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.99986e-07,
              "target_id": "index:us-500",
              "target_position": -9.274179e-05,
              "target_position_change": -9.274179e-05
            }
          ],
          "turnover": 0.000307429349
        },
        "period-1": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000408275075,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.894e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000408275075,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.255e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000408275075,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.7451e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000408275075,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.3841e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000408275075,
          "gross_mean": 1.894e-08,
          "gross_total": 1.13641e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 3.997e-09,
              "target_id": "commodity:spot-gold",
              "target_position": -1.530712e-06,
              "target_position_change": -5.80182e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.3489e-08,
              "target_id": "commodity:us-crude",
              "target_position": 1.4999489e-05,
              "target_position_change": 0.000158549188
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.3913e-08,
              "target_id": "fx:aud-usd",
              "target_position": 1.8675784e-05,
              "target_position_change": 2.0160902e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -5.938e-09,
              "target_id": "fx:eur-usd",
              "target_position": 7.79337e-06,
              "target_position_change": 3.7023987e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.1134e-08,
              "target_id": "index:australia-200",
              "target_position": -1.483772e-05,
              "target_position_change": 2.1313297e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.5185e-07,
              "target_id": "index:us-500",
              "target_position": -5.7246782e-05,
              "target_position_change": 3.5495008e-05
            }
          ],
          "turnover": 0.000278344202
        },
        "period-2": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000577077417,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.0926e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000577077417,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.772e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000577077417,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.4515e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000577077417,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.38105e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000577077417,
          "gross_mean": -3.0926e-08,
          "gross_total": -1.85553e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.4773e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.7012958e-05,
              "target_position_change": 6.854367e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.01115e-07,
              "target_id": "commodity:us-crude",
              "target_position": -4.9813505e-05,
              "target_position_change": -6.4812994e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.7635e-08,
              "target_id": "fx:aud-usd",
              "target_position": 2.6501571e-05,
              "target_position_change": 7.825787e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -4.338e-09,
              "target_id": "fx:eur-usd",
              "target_position": 6.389893e-06,
              "target_position_change": -1.403477e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.1388e-08,
              "target_id": "index:australia-200",
              "target_position": 4.5914103e-05,
              "target_position_change": 6.0751823e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.5577e-08,
              "target_id": "index:us-500",
              "target_position": 6.0954648e-05,
              "target_position_change": 0.00011820143
            }
          ],
          "turnover": 0.000321539181
        }
      },
      "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 1.9981e-08,
          "target_id": "commodity:spot-gold",
          "target_position": 4.271108e-06,
          "target_position_change": 4.271108e-06
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 6.2309e-08,
          "target_id": "commodity:us-crude",
          "target_position": -0.000143549699,
          "target_position_change": -0.000143549699
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.504e-09,
          "target_id": "fx:aud-usd",
          "target_position": -1.485118e-06,
          "target_position_change": -1.485118e-06
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.0749e-08,
          "target_id": "fx:eur-usd",
          "target_position": -2.9230617e-05,
          "target_position_change": -2.9230617e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -6.281e-08,
          "target_id": "index:australia-200",
          "target_position": -3.6151017e-05,
          "target_position_change": -3.6151017e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.99986e-07,
          "target_id": "index:us-500",
          "target_position": -9.274179e-05,
          "target_position_change": -9.274179e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 3.997e-09,
          "target_id": "commodity:spot-gold",
          "target_position": -1.530712e-06,
          "target_position_change": -5.80182e-06
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -4.3489e-08,
          "target_id": "commodity:us-crude",
          "target_position": 1.4999489e-05,
          "target_position_change": 0.000158549188
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -1.3913e-08,
          "target_id": "fx:aud-usd",
          "target_position": 1.8675784e-05,
          "target_position_change": 2.0160902e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -5.938e-09,
          "target_id": "fx:eur-usd",
          "target_position": 7.79337e-06,
          "target_position_change": 3.7023987e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.1134e-08,
          "target_id": "index:australia-200",
          "target_position": -1.483772e-05,
          "target_position_change": 2.1313297e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.5185e-07,
          "target_id": "index:us-500",
          "target_position": -5.7246782e-05,
          "target_position_change": 3.5495008e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -1.4773e-07,
          "target_id": "commodity:spot-gold",
          "target_position": 6.7012958e-05,
          "target_position_change": 6.854367e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.01115e-07,
          "target_id": "commodity:us-crude",
          "target_position": -4.9813505e-05,
          "target_position_change": -6.4812994e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -1.7635e-08,
          "target_id": "fx:aud-usd",
          "target_position": 2.6501571e-05,
          "target_position_change": 7.825787e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -4.338e-09,
          "target_id": "fx:eur-usd",
          "target_position": 6.389893e-06,
          "target_position_change": -1.403477e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -3.1388e-08,
          "target_id": "index:australia-200",
          "target_position": 4.5914103e-05,
          "target_position_change": 6.0751823e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -8.5577e-08,
          "target_id": "index:us-500",
          "target_position": 6.0954648e-05,
          "target_position_change": 0.00011820143
        }
      ],
      "trace_id": "pooled_local_ridge",
      "turnover": 0.000907312732
    },
    "pooled_non_graph": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": -0.000291708681,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.4704e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000291708681,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -3.9907e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000291708681,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -6.511e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000291708681,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.15516e-07,
          "unit": "fraction_of_notional"
        }
      ],
      "asset": {
        "commodity:spot-gold": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001574120518,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.1251e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001574120518,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.4353e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001574120518,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.7456e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001574120518,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -9.3662e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001574120518,
          "gross_mean": -4.1251e-08,
          "gross_total": -1.23752e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.9981e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 4.271108e-06,
              "target_position_change": 4.271108e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 3.997e-09,
              "target_id": "commodity:spot-gold",
              "target_position": -1.530712e-06,
              "target_position_change": -5.80182e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.4773e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.7012958e-05,
              "target_position_change": 6.854367e-05
            }
          ],
          "turnover": 7.8616598e-05
        },
        "commodity:us-crude": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.00032687685,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 3.9978e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00032687685,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.1174e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00032687685,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.2326e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00032687685,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.0463e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.00032687685,
          "gross_mean": 3.9978e-08,
          "gross_total": 1.19935e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 6.2309e-08,
              "target_id": "commodity:us-crude",
              "target_position": -0.000143549699,
              "target_position_change": -0.000143549699
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.3489e-08,
              "target_id": "commodity:us-crude",
              "target_position": 1.4999489e-05,
              "target_position_change": 0.000158549188
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.01115e-07,
              "target_id": "commodity:us-crude",
              "target_position": -4.9813505e-05,
              "target_position_change": -6.4812994e-05
            }
          ],
          "turnover": 0.000366911881
        },
        "fx:aud-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001121478571,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.1017e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001121478571,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.5929e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001121478571,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.0841e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001121478571,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.0665e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001121478571,
          "gross_mean": -1.1017e-08,
          "gross_total": -3.3052e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.504e-09,
              "target_id": "fx:aud-usd",
              "target_position": -1.485118e-06,
              "target_position_change": -1.485118e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.3913e-08,
              "target_id": "fx:aud-usd",
              "target_position": 1.8675784e-05,
              "target_position_change": 2.0160902e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.7635e-08,
              "target_id": "fx:aud-usd",
              "target_position": 2.6501571e-05,
              "target_position_change": 7.825787e-06
            }
          ],
          "turnover": 2.9471807e-05
        },
        "fx:eur-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000310753715,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.008e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000310753715,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.8285e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000310753715,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.9561e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000310753715,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.2114e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000310753715,
          "gross_mean": -7.008e-09,
          "gross_total": -2.1025e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.0749e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.9230617e-05,
              "target_position_change": -2.9230617e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -5.938e-09,
              "target_id": "fx:eur-usd",
              "target_position": 7.79337e-06,
              "target_position_change": 3.7023987e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -4.338e-09,
              "target_id": "fx:eur-usd",
              "target_position": 6.389893e-06,
              "target_position_change": -1.403477e-06
            }
          ],
          "turnover": 6.7658081e-05
        },
        "index:australia-200": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000618054369,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.4355e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000618054369,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.4057e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000618054369,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.376e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000618054369,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.03165e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000618054369,
          "gross_mean": -2.4355e-08,
          "gross_total": -7.3064e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -6.281e-08,
              "target_id": "index:australia-200",
              "target_position": -3.6151017e-05,
              "target_position_change": -3.6151017e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.1134e-08,
              "target_id": "index:australia-200",
              "target_position": -1.483772e-05,
              "target_position_change": 2.1313297e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.1388e-08,
              "target_id": "index:australia-200",
              "target_position": 4.5914103e-05,
              "target_position_change": 6.0751823e-05
            }
          ],
          "turnover": 0.000118216137
        },
        "index:us-500": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000542582217,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.4571e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000542582217,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.5644e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000542582217,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.26717e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000542582217,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.08863e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000542582217,
          "gross_mean": -4.4571e-08,
          "gross_total": -1.33713e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.99986e-07,
              "target_id": "index:us-500",
              "target_position": -9.274179e-05,
              "target_position_change": -9.274179e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.5185e-07,
              "target_id": "index:us-500",
              "target_position": -5.7246782e-05,
              "target_position_change": 3.5495008e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.5577e-08,
              "target_id": "index:us-500",
              "target_position": 6.0954648e-05,
              "target_position_change": 0.00011820143
            }
          ],
          "turnover": 0.000246438228
        }
      },
      "break_even_cost": -0.000291708681,
      "gross_mean": -1.4704e-08,
      "gross_total": -2.64671e-07,
      "horizon": {
        "15": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000291708681,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.4704e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000291708681,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.9907e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000291708681,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.511e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000291708681,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.15516e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000291708681,
          "gross_mean": -1.4704e-08,
          "gross_total": -2.64671e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.9981e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 4.271108e-06,
              "target_position_change": 4.271108e-06
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 6.2309e-08,
              "target_id": "commodity:us-crude",
              "target_position": -0.000143549699,
              "target_position_change": -0.000143549699
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.504e-09,
              "target_id": "fx:aud-usd",
              "target_position": -1.485118e-06,
              "target_position_change": -1.485118e-06
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.0749e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.9230617e-05,
              "target_position_change": -2.9230617e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -6.281e-08,
              "target_id": "index:australia-200",
              "target_position": -3.6151017e-05,
              "target_position_change": -3.6151017e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.99986e-07,
              "target_id": "index:us-500",
              "target_position": -9.274179e-05,
              "target_position_change": -9.274179e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 3.997e-09,
              "target_id": "commodity:spot-gold",
              "target_position": -1.530712e-06,
              "target_position_change": -5.80182e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.3489e-08,
              "target_id": "commodity:us-crude",
              "target_position": 1.4999489e-05,
              "target_position_change": 0.000158549188
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.3913e-08,
              "target_id": "fx:aud-usd",
              "target_position": 1.8675784e-05,
              "target_position_change": 2.0160902e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -5.938e-09,
              "target_id": "fx:eur-usd",
              "target_position": 7.79337e-06,
              "target_position_change": 3.7023987e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.1134e-08,
              "target_id": "index:australia-200",
              "target_position": -1.483772e-05,
              "target_position_change": 2.1313297e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.5185e-07,
              "target_id": "index:us-500",
              "target_position": -5.7246782e-05,
              "target_position_change": 3.5495008e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.4773e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.7012958e-05,
              "target_position_change": 6.854367e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.01115e-07,
              "target_id": "commodity:us-crude",
              "target_position": -4.9813505e-05,
              "target_position_change": -6.4812994e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.7635e-08,
              "target_id": "fx:aud-usd",
              "target_position": 2.6501571e-05,
              "target_position_change": 7.825787e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -4.338e-09,
              "target_id": "fx:eur-usd",
              "target_position": 6.389893e-06,
              "target_position_change": -1.403477e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.1388e-08,
              "target_id": "index:australia-200",
              "target_position": 4.5914103e-05,
              "target_position_change": 6.0751823e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.5577e-08,
              "target_id": "index:us-500",
              "target_position": 6.0954648e-05,
              "target_position_change": 0.00011820143
            }
          ],
          "turnover": 0.000907312732
        }
      },
      "period": {
        "period-0": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000627002596,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.2126e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000627002596,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.7746e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000627002596,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.3365e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000627002596,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.34603e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000627002596,
          "gross_mean": -3.2126e-08,
          "gross_total": -1.92759e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.9981e-08,
              "target_id": "commodity:spot-gold",
              "target_position": 4.271108e-06,
              "target_position_change": 4.271108e-06
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 6.2309e-08,
              "target_id": "commodity:us-crude",
              "target_position": -0.000143549699,
              "target_position_change": -0.000143549699
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.504e-09,
              "target_id": "fx:aud-usd",
              "target_position": -1.485118e-06,
              "target_position_change": -1.485118e-06
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.0749e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.9230617e-05,
              "target_position_change": -2.9230617e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -6.281e-08,
              "target_id": "index:australia-200",
              "target_position": -3.6151017e-05,
              "target_position_change": -3.6151017e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.99986e-07,
              "target_id": "index:us-500",
              "target_position": -9.274179e-05,
              "target_position_change": -9.274179e-05
            }
          ],
          "turnover": 0.000307429349
        },
        "period-1": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000408275075,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.894e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000408275075,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.255e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000408275075,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.7451e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000408275075,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.3841e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000408275075,
          "gross_mean": 1.894e-08,
          "gross_total": 1.13641e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 3.997e-09,
              "target_id": "commodity:spot-gold",
              "target_position": -1.530712e-06,
              "target_position_change": -5.80182e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.3489e-08,
              "target_id": "commodity:us-crude",
              "target_position": 1.4999489e-05,
              "target_position_change": 0.000158549188
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.3913e-08,
              "target_id": "fx:aud-usd",
              "target_position": 1.8675784e-05,
              "target_position_change": 2.0160902e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -5.938e-09,
              "target_id": "fx:eur-usd",
              "target_position": 7.79337e-06,
              "target_position_change": 3.7023987e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.1134e-08,
              "target_id": "index:australia-200",
              "target_position": -1.483772e-05,
              "target_position_change": 2.1313297e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.5185e-07,
              "target_id": "index:us-500",
              "target_position": -5.7246782e-05,
              "target_position_change": 3.5495008e-05
            }
          ],
          "turnover": 0.000278344202
        },
        "period-2": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000577077417,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.0926e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000577077417,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.772e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000577077417,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.4515e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000577077417,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.38105e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000577077417,
          "gross_mean": -3.0926e-08,
          "gross_total": -1.85553e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.4773e-07,
              "target_id": "commodity:spot-gold",
              "target_position": 6.7012958e-05,
              "target_position_change": 6.854367e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.01115e-07,
              "target_id": "commodity:us-crude",
              "target_position": -4.9813505e-05,
              "target_position_change": -6.4812994e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.7635e-08,
              "target_id": "fx:aud-usd",
              "target_position": 2.6501571e-05,
              "target_position_change": 7.825787e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -4.338e-09,
              "target_id": "fx:eur-usd",
              "target_position": 6.389893e-06,
              "target_position_change": -1.403477e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.1388e-08,
              "target_id": "index:australia-200",
              "target_position": 4.5914103e-05,
              "target_position_change": 6.0751823e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -8.5577e-08,
              "target_id": "index:us-500",
              "target_position": 6.0954648e-05,
              "target_position_change": 0.00011820143
            }
          ],
          "turnover": 0.000321539181
        }
      },
      "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 1.9981e-08,
          "target_id": "commodity:spot-gold",
          "target_position": 4.271108e-06,
          "target_position_change": 4.271108e-06
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 6.2309e-08,
          "target_id": "commodity:us-crude",
          "target_position": -0.000143549699,
          "target_position_change": -0.000143549699
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.504e-09,
          "target_id": "fx:aud-usd",
          "target_position": -1.485118e-06,
          "target_position_change": -1.485118e-06
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.0749e-08,
          "target_id": "fx:eur-usd",
          "target_position": -2.9230617e-05,
          "target_position_change": -2.9230617e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -6.281e-08,
          "target_id": "index:australia-200",
          "target_position": -3.6151017e-05,
          "target_position_change": -3.6151017e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.99986e-07,
          "target_id": "index:us-500",
          "target_position": -9.274179e-05,
          "target_position_change": -9.274179e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 3.997e-09,
          "target_id": "commodity:spot-gold",
          "target_position": -1.530712e-06,
          "target_position_change": -5.80182e-06
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -4.3489e-08,
          "target_id": "commodity:us-crude",
          "target_position": 1.4999489e-05,
          "target_position_change": 0.000158549188
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -1.3913e-08,
          "target_id": "fx:aud-usd",
          "target_position": 1.8675784e-05,
          "target_position_change": 2.0160902e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -5.938e-09,
          "target_id": "fx:eur-usd",
          "target_position": 7.79337e-06,
          "target_position_change": 3.7023987e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.1134e-08,
          "target_id": "index:australia-200",
          "target_position": -1.483772e-05,
          "target_position_change": 2.1313297e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.5185e-07,
          "target_id": "index:us-500",
          "target_position": -5.7246782e-05,
          "target_position_change": 3.5495008e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -1.4773e-07,
          "target_id": "commodity:spot-gold",
          "target_position": 6.7012958e-05,
          "target_position_change": 6.854367e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.01115e-07,
          "target_id": "commodity:us-crude",
          "target_position": -4.9813505e-05,
          "target_position_change": -6.4812994e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -1.7635e-08,
          "target_id": "fx:aud-usd",
          "target_position": 2.6501571e-05,
          "target_position_change": 7.825787e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -4.338e-09,
          "target_id": "fx:eur-usd",
          "target_position": 6.389893e-06,
          "target_position_change": -1.403477e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -3.1388e-08,
          "target_id": "index:australia-200",
          "target_position": 4.5914103e-05,
          "target_position_change": 6.0751823e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -8.5577e-08,
          "target_id": "index:us-500",
          "target_position": 6.0954648e-05,
          "target_position_change": 0.00011820143
        }
      ],
      "trace_id": "pooled_non_graph",
      "turnover": 0.000907312732
    },
    "shuffled_graph": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": 0.000359106718,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 1.8046e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000359106718,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -7.08e-09,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000359106718,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -3.2207e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000359106718,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -8.2461e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "asset": {
        "commodity:spot-gold": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000154392069,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.764e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000154392069,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.0191e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000154392069,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.5618e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000154392069,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.6472e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000154392069,
          "gross_mean": -4.764e-09,
          "gross_total": -1.4291e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -3.93645e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.12417e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.66937e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            }
          ],
          "turnover": 9.2563045e-05
        },
        "commodity:us-crude": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.003413479298,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.88186e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.003413479298,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.60621e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.003413479298,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.33056e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.003413479298,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 7.7925e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.003413479298,
          "gross_mean": 1.88186e-07,
          "gross_total": 5.64557e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.9166e-08,
              "target_id": "commodity:us-crude",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 3.546e-07,
              "target_id": "commodity:us-crude",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.60791e-07,
              "target_id": "commodity:us-crude",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            }
          ],
          "turnover": 0.000165390486
        },
        "fx:aud-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.00037754612,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -5.094e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.00037754612,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.184e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.00037754612,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.8586e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.00037754612,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.2079e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.00037754612,
          "gross_mean": -5.094e-09,
          "gross_total": -1.5282e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.1864e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.783e-09,
              "target_id": "fx:aud-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.799e-09,
              "target_id": "fx:aud-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            }
          ],
          "turnover": 4.0477174e-05
        },
        "fx:eur-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000883591947,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.2552e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000883591947,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 5.449e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000883591947,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.654e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000883591947,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.5859e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000883591947,
          "gross_mean": 1.2552e-08,
          "gross_total": 3.7656e-08,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -9.497e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.7349e-08,
              "target_id": "fx:eur-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9804e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            }
          ],
          "turnover": 4.2616957e-05
        },
        "index:australia-200": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000926703958,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.2239e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000926703958,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.88425e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000926703958,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.5446e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000926703958,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.86529e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000926703958,
          "gross_mean": -1.2239e-07,
          "gross_total": -3.67169e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.64148e-07,
              "target_id": "index:australia-200",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.75879e-07,
              "target_id": "index:australia-200",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -2.7142e-08,
              "target_id": "index:australia-200",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            }
          ],
          "turnover": 0.000396209595
        },
        "index:us-500": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000713438256,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 3.9788e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000713438256,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.1903e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000713438256,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.5982e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000713438256,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.1751e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000713438256,
          "gross_mean": 3.9788e-08,
          "gross_total": 1.19365e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.37757e-07,
              "target_id": "index:us-500",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.8033e-08,
              "target_id": "index:us-500",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.6425e-08,
              "target_id": "index:us-500",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            }
          ],
          "turnover": 0.000167309503
        }
      },
      "break_even_cost": 0.000359106718,
      "gross_mean": 1.8046e-08,
      "gross_total": 3.24836e-07,
      "horizon": {
        "15": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.000359106718,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.8046e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000359106718,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.08e-09,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000359106718,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.2207e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.000359106718,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -8.2461e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.000359106718,
          "gross_mean": 1.8046e-08,
          "gross_total": 3.24836e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -3.93645e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.9166e-08,
              "target_id": "commodity:us-crude",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.1864e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -9.497e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.64148e-07,
              "target_id": "index:australia-200",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.37757e-07,
              "target_id": "index:us-500",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.12417e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 3.546e-07,
              "target_id": "commodity:us-crude",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.783e-09,
              "target_id": "fx:aud-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.7349e-08,
              "target_id": "fx:eur-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.75879e-07,
              "target_id": "index:australia-200",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.8033e-08,
              "target_id": "index:us-500",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.66937e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.60791e-07,
              "target_id": "commodity:us-crude",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.799e-09,
              "target_id": "fx:aud-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9804e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -2.7142e-08,
              "target_id": "index:australia-200",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.6425e-08,
              "target_id": "index:us-500",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            }
          ],
          "turnover": 0.00090456676
        }
      },
      "period": {
        "period-0": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.000997616196,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.7038e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000997616196,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.00638e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000997616196,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.34237e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.000997616196,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.01436e-07,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.000997616196,
          "gross_mean": -6.7038e-08,
          "gross_total": -4.02231e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -3.93645e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -8.414435e-05,
              "target_position_change": -8.414435e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 4.9166e-08,
              "target_id": "commodity:us-crude",
              "target_position": -0.00011327098,
              "target_position_change": -0.00011327098
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -2.1864e-08,
              "target_id": "fx:aud-usd",
              "target_position": -2.159062e-05,
              "target_position_change": -2.159062e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -9.497e-09,
              "target_id": "fx:eur-usd",
              "target_position": -2.58259e-05,
              "target_position_change": -2.58259e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -1.64148e-07,
              "target_id": "index:australia-200",
              "target_position": -9.4476664e-05,
              "target_position_change": -9.4476664e-05
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 1.37757e-07,
              "target_id": "index:us-500",
              "target_position": 6.3883617e-05,
              "target_position_change": 6.3883617e-05
            }
          ],
          "turnover": 0.000403192131
        },
        "period-1": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.00135495808,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 7.355e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00135495808,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 4.6409e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00135495808,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.9268e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00135495808,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.5014e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.00135495808,
          "gross_mean": 7.355e-08,
          "gross_total": 4.41303e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.12417e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -8.1357702e-05,
              "target_position_change": 2.786648e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 3.546e-07,
              "target_id": "commodity:us-crude",
              "target_position": -0.000122301435,
              "target_position_change": -9.030455e-06
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 4.783e-09,
              "target_id": "fx:aud-usd",
              "target_position": -6.420369e-06,
              "target_position_change": 1.5170251e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 2.7349e-08,
              "target_id": "fx:eur-usd",
              "target_position": -3.5894947e-05,
              "target_position_change": -1.0069047e-05
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.75879e-07,
              "target_id": "index:australia-200",
              "target_position": 0.000123479741,
              "target_position_change": 0.000217956405
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": 1.8033e-08,
              "target_id": "index:us-500",
              "target_position": -6.798524e-06,
              "target_position_change": -7.0682141e-05
            }
          ],
          "turnover": 0.000325694947
        },
        "period-2": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": 0.00162661952,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 4.7627e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00162661952,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 3.2987e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00162661952,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 1.8347e-08,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": 0.00162661952,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.0933e-08,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": 0.00162661952,
          "gross_mean": 4.7627e-08,
          "gross_total": 2.85764e-07,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.66937e-07,
              "target_id": "commodity:spot-gold",
              "target_position": -7.5725655e-05,
              "target_position_change": 5.632047e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.60791e-07,
              "target_id": "commodity:us-crude",
              "target_position": -7.9212384e-05,
              "target_position_change": 4.3089051e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.799e-09,
              "target_id": "fx:aud-usd",
              "target_position": -2.704066e-06,
              "target_position_change": 3.716303e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": 1.9804e-08,
              "target_id": "fx:eur-usd",
              "target_position": -2.9172937e-05,
              "target_position_change": 6.72201e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -2.7142e-08,
              "target_id": "index:australia-200",
              "target_position": 3.9703215e-05,
              "target_position_change": -8.3776526e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.6425e-08,
              "target_id": "index:us-500",
              "target_position": 2.5945221e-05,
              "target_position_change": 3.2743745e-05
            }
          ],
          "turnover": 0.000175679682
        }
      },
      "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -3.93645e-07,
          "target_id": "commodity:spot-gold",
          "target_position": -8.414435e-05,
          "target_position_change": -8.414435e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 4.9166e-08,
          "target_id": "commodity:us-crude",
          "target_position": -0.00011327098,
          "target_position_change": -0.00011327098
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -2.1864e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.159062e-05,
          "target_position_change": -2.159062e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -9.497e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.58259e-05,
          "target_position_change": -2.58259e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.64148e-07,
          "target_id": "index:australia-200",
          "target_position": -9.4476664e-05,
          "target_position_change": -9.4476664e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 1.37757e-07,
          "target_id": "index:us-500",
          "target_position": 6.3883617e-05,
          "target_position_change": 6.3883617e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.12417e-07,
          "target_id": "commodity:spot-gold",
          "target_position": -8.1357702e-05,
          "target_position_change": 2.786648e-06
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 3.546e-07,
          "target_id": "commodity:us-crude",
          "target_position": -0.000122301435,
          "target_position_change": -9.030455e-06
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 4.783e-09,
          "target_id": "fx:aud-usd",
          "target_position": -6.420369e-06,
          "target_position_change": 1.5170251e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.7349e-08,
          "target_id": "fx:eur-usd",
          "target_position": -3.5894947e-05,
          "target_position_change": -1.0069047e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -1.75879e-07,
          "target_id": "index:australia-200",
          "target_position": 0.000123479741,
          "target_position_change": 0.000217956405
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.8033e-08,
          "target_id": "index:us-500",
          "target_position": -6.798524e-06,
          "target_position_change": -7.0682141e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.66937e-07,
          "target_id": "commodity:spot-gold",
          "target_position": -7.5725655e-05,
          "target_position_change": 5.632047e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.60791e-07,
          "target_id": "commodity:us-crude",
          "target_position": -7.9212384e-05,
          "target_position_change": 4.3089051e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.799e-09,
          "target_id": "fx:aud-usd",
          "target_position": -2.704066e-06,
          "target_position_change": 3.716303e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.9804e-08,
          "target_id": "fx:eur-usd",
          "target_position": -2.9172937e-05,
          "target_position_change": 6.72201e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -2.7142e-08,
          "target_id": "index:australia-200",
          "target_position": 3.9703215e-05,
          "target_position_change": -8.3776526e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -3.6425e-08,
          "target_id": "index:us-500",
          "target_position": 2.5945221e-05,
          "target_position_change": 3.2743745e-05
        }
      ],
      "trace_id": "shuffled_graph",
      "turnover": 0.00090456676
    },
    "tiny_learned_graph": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": -0.003122809604,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.655358e-06,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.003122809604,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.920401e-06,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.003122809604,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.185444e-06,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.003122809604,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.715529e-06,
          "unit": "fraction_of_notional"
        }
      ],
      "asset": {
        "commodity:spot-gold": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.004808082271,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.552719e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.004808082271,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.81818e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.004808082271,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.083641e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.004808082271,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.614564e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.004808082271,
          "gross_mean": -2.552719e-06,
          "gross_total": -7.658156e-06,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.146901e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.00158830503,
              "target_position_change": 0.00158830503
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.511255e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.001592767255,
              "target_position_change": 4.462225e-06
            }
          ],
          "turnover": 0.001592767255
        },
        "commodity:us-crude": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.004878304089,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.631674e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.004878304089,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.901406e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.004878304089,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.171139e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.004878304089,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.710603e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.004878304089,
          "gross_mean": -2.631674e-06,
          "gross_total": -7.895021e-06,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.658003e-06,
              "target_id": "commodity:us-crude",
              "target_position": 0.001606543988,
              "target_position_change": 0.001606543988
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.237018e-06,
              "target_id": "commodity:us-crude",
              "target_position": 0.001594693375,
              "target_position_change": -1.1850613e-05
            }
          ],
          "turnover": 0.001618394601
        },
        "fx:aud-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001410016084,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.44982e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001410016084,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.009157e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001410016084,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.273333e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001410016084,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.801683e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001410016084,
          "gross_mean": -7.44982e-07,
          "gross_total": -2.234947e-06,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.18018e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001584231531,
              "target_position_change": 0.001584231531
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.054767e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001585050713,
              "target_position_change": 8.19182e-07
            }
          ],
          "turnover": 0.001585050713
        },
        "fx:eur-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.00144056135,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -7.629e-07,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.00144056135,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.027693e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.00144056135,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.292486e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.00144056135,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.822071e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.00144056135,
          "gross_mean": -7.629e-07,
          "gross_total": -2.288701e-06,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.210193e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.001588357972,
              "target_position_change": 0.001588357972
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.078508e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.001588756355,
              "target_position_change": 3.98383e-07
            }
          ],
          "turnover": 0.001588756355
        },
        "index:australia-200": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.002102652713,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.106027e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.002102652713,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.369035e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.002102652713,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.632042e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.002102652713,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.158057e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.002102652713,
          "gross_mean": -1.106027e-06,
          "gross_total": -3.318081e-06,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -2.239279e-06,
              "target_id": "index:australia-200",
              "target_position": 0.001572134622,
              "target_position_change": 0.001572134622
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.078802e-06,
              "target_id": "index:australia-200",
              "target_position": 0.00157804519,
              "target_position_change": 5.910568e-06
            }
          ],
          "turnover": 0.00157804519
        },
        "index:us-500": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.004055365744,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.133843e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.004055365744,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.396932e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.004055365744,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.660021e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.004055365744,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.186199e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.004055365744,
          "gross_mean": -2.133843e-06,
          "gross_total": -6.40153e-06,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.185368e-06,
              "target_id": "index:us-500",
              "target_position": 0.001577866745,
              "target_position_change": 0.001577866745
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -2.216162e-06,
              "target_id": "index:us-500",
              "target_position": 0.001578533332,
              "target_position_change": 6.66587e-07
            }
          ],
          "turnover": 0.001578533332
        }
      },
      "break_even_cost": -0.003122809604,
      "gross_mean": -1.655358e-06,
      "gross_total": -2.9796436e-05,
      "horizon": {
        "15": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.003122809604,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.655358e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.003122809604,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -1.920401e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.003122809604,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.185444e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.003122809604,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.715529e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.003122809604,
          "gross_mean": -1.655358e-06,
          "gross_total": -2.9796436e-05,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.146901e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.00158830503,
              "target_position_change": 0.00158830503
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.658003e-06,
              "target_id": "commodity:us-crude",
              "target_position": 0.001606543988,
              "target_position_change": 0.001606543988
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.18018e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001584231531,
              "target_position_change": 0.001584231531
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.210193e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.001588357972,
              "target_position_change": 0.001588357972
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -2.239279e-06,
              "target_id": "index:australia-200",
              "target_position": 0.001572134622,
              "target_position_change": 0.001572134622
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.185368e-06,
              "target_id": "index:us-500",
              "target_position": 0.001577866745,
              "target_position_change": 0.001577866745
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.511255e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.001592767255,
              "target_position_change": 4.462225e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.237018e-06,
              "target_id": "commodity:us-crude",
              "target_position": 0.001594693375,
              "target_position_change": -1.1850613e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.054767e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001585050713,
              "target_position_change": 8.19182e-07
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.078508e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.001588756355,
              "target_position_change": 3.98383e-07
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.078802e-06,
              "target_id": "index:australia-200",
              "target_position": 0.00157804519,
              "target_position_change": 5.910568e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -2.216162e-06,
              "target_id": "index:us-500",
              "target_position": 0.001578533332,
              "target_position_change": 6.66587e-07
            }
          ],
          "turnover": 0.009541547446
        }
      },
      "period": {
        "period-0": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "period-1": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.001851330211,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.936654e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001851330211,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -3.729774e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001851330211,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -4.522894e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.001851330211,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -6.109134e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.001851330211,
          "gross_mean": -2.936654e-06,
          "gross_total": -1.7619924e-05,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.146901e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.00158830503,
              "target_position_change": 0.00158830503
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.658003e-06,
              "target_id": "commodity:us-crude",
              "target_position": 0.001606543988,
              "target_position_change": 0.001606543988
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.18018e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001584231531,
              "target_position_change": 0.001584231531
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -1.210193e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.001588357972,
              "target_position_change": 0.001588357972
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -2.239279e-06,
              "target_id": "index:australia-200",
              "target_position": 0.001572134622,
              "target_position_change": 0.001572134622
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -4.185368e-06,
              "target_id": "index:us-500",
              "target_position": 0.001577866745,
              "target_position_change": 0.001577866745
            }
          ],
          "turnover": 0.009517439888
        },
        "period-2": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": -0.505091058995,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.029419e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.505091058995,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.031428e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.505091058995,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.033437e-06,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": -0.505091058995,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": -2.037455e-06,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": -0.505091058995,
          "gross_mean": -2.029419e-06,
          "gross_total": -1.2176512e-05,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.511255e-06,
              "target_id": "commodity:spot-gold",
              "target_position": 0.001592767255,
              "target_position_change": 4.462225e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -3.237018e-06,
              "target_id": "commodity:us-crude",
              "target_position": 0.001594693375,
              "target_position_change": -1.1850613e-05
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.054767e-06,
              "target_id": "fx:aud-usd",
              "target_position": 0.001585050713,
              "target_position_change": 8.19182e-07
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.078508e-06,
              "target_id": "fx:eur-usd",
              "target_position": 0.001588756355,
              "target_position_change": 3.98383e-07
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -1.078802e-06,
              "target_id": "index:australia-200",
              "target_position": 0.00157804519,
              "target_position_change": 5.910568e-06
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -2.216162e-06,
              "target_id": "index:us-500",
              "target_position": 0.001578533332,
              "target_position_change": 6.66587e-07
            }
          ],
          "turnover": 2.4107558e-05
        }
      },
      "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "commodity:spot-gold",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:us-crude",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "fx:aud-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "fx:eur-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "index:australia-200",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "index:us-500",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -4.146901e-06,
          "target_id": "commodity:spot-gold",
          "target_position": 0.00158830503,
          "target_position_change": 0.00158830503
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -4.658003e-06,
          "target_id": "commodity:us-crude",
          "target_position": 0.001606543988,
          "target_position_change": 0.001606543988
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -1.18018e-06,
          "target_id": "fx:aud-usd",
          "target_position": 0.001584231531,
          "target_position_change": 0.001584231531
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -1.210193e-06,
          "target_id": "fx:eur-usd",
          "target_position": 0.001588357972,
          "target_position_change": 0.001588357972
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -2.239279e-06,
          "target_id": "index:australia-200",
          "target_position": 0.001572134622,
          "target_position_change": 0.001572134622
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -4.185368e-06,
          "target_id": "index:us-500",
          "target_position": 0.001577866745,
          "target_position_change": 0.001577866745
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -3.511255e-06,
          "target_id": "commodity:spot-gold",
          "target_position": 0.001592767255,
          "target_position_change": 4.462225e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -3.237018e-06,
          "target_id": "commodity:us-crude",
          "target_position": 0.001594693375,
          "target_position_change": -1.1850613e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -1.054767e-06,
          "target_id": "fx:aud-usd",
          "target_position": 0.001585050713,
          "target_position_change": 8.19182e-07
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -1.078508e-06,
          "target_id": "fx:eur-usd",
          "target_position": 0.001588756355,
          "target_position_change": 3.98383e-07
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -1.078802e-06,
          "target_id": "index:australia-200",
          "target_position": 0.00157804519,
          "target_position_change": 5.910568e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -2.216162e-06,
          "target_id": "index:us-500",
          "target_position": 0.001578533332,
          "target_position_change": 6.66587e-07
        }
      ],
      "trace_id": "tiny_learned_graph",
      "turnover": 0.009541547446
    },
    "zero_return": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": null,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 0.0,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": null,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 0.0,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": null,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 0.0,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": null,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 0.0,
          "unit": "fraction_of_notional"
        }
      ],
      "asset": {
        "commodity:spot-gold": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "commodity:us-crude": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "fx:aud-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "fx:eur-usd": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "index:australia-200": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "index:us-500": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        }
      },
      "break_even_cost": null,
      "gross_mean": 0.0,
      "gross_total": 0.0,
      "horizon": {
        "15": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        }
      },
      "period": {
        "period-0": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:06:00+00:00",
              "realised_gross": 0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "period-1": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:26:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        },
        "period-2": {
          "all_in_cost_sensitivity": [
            {
              "break_even_cost": null,
              "cost": 0.0,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.0005,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.001,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            },
            {
              "break_even_cost": null,
              "cost": 0.002,
              "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
              "net_mean": 0.0,
              "unit": "fraction_of_notional"
            }
          ],
          "break_even_cost": null,
          "gross_mean": 0.0,
          "gross_total": 0.0,
          "position_trace": [
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:spot-gold",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "commodity:us-crude",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:aud-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "fx:eur-usd",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:australia-200",
              "target_position": 0.0,
              "target_position_change": 0.0
            },
            {
              "decision_time": "2026-06-26T14:27:00+00:00",
              "realised_gross": -0.0,
              "target_id": "index:us-500",
              "target_position": 0.0,
              "target_position_change": 0.0
            }
          ],
          "turnover": 0.0
        }
      },
      "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "commodity:spot-gold",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:us-crude",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "fx:aud-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "fx:eur-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "index:australia-200",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 0.0,
          "target_id": "index:us-500",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:spot-gold",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:us-crude",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "fx:aud-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "fx:eur-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "index:australia-200",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -0.0,
          "target_id": "index:us-500",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:spot-gold",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "commodity:us-crude",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "fx:aud-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "fx:eur-usd",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "index:australia-200",
          "target_position": 0.0,
          "target_position_change": 0.0
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -0.0,
          "target_id": "index:us-500",
          "target_position": 0.0,
          "target_position_change": 0.0
        }
      ],
      "trace_id": "zero_return",
      "turnover": 0.0
    }
  },
  "gross_mean": 2.935e-09,
  "gross_total": 5.2828e-08,
  "horizon": {
    "15": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 2.935e-09,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.2192e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -4.7319e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 5.8401438e-05,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -9.7573e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "break_even_cost": 5.8401438e-05,
      "gross_mean": 2.935e-09,
      "gross_total": 5.2828e-08,
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 2.98861e-07,
          "target_id": "commodity:spot-gold",
          "target_position": 6.3883617e-05,
          "target_position_change": 6.3883617e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 4.1008e-08,
          "target_id": "commodity:us-crude",
          "target_position": -9.4476664e-05,
          "target_position_change": -9.4476664e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -2.6153e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.58259e-05,
          "target_position_change": -2.58259e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -7.939e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.159062e-05,
          "target_position_change": -2.159062e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.96802e-07,
          "target_id": "index:australia-200",
          "target_position": -0.00011327098,
          "target_position_change": -0.00011327098
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.81446e-07,
          "target_id": "index:us-500",
          "target_position": -8.414435e-05,
          "target_position_change": -8.414435e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.775e-08,
          "target_id": "commodity:spot-gold",
          "target_position": -6.798524e-06,
          "target_position_change": -7.0682141e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -3.58016e-07,
          "target_id": "commodity:us-crude",
          "target_position": 0.000123479741,
          "target_position_change": 0.000217956405
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.674e-08,
          "target_id": "fx:aud-usd",
          "target_position": -3.5894947e-05,
          "target_position_change": -1.0069047e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 4.892e-09,
          "target_id": "fx:eur-usd",
          "target_position": -6.420369e-06,
          "target_position_change": 1.5170251e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.74201e-07,
          "target_id": "index:australia-200",
          "target_position": -0.000122301435,
          "target_position_change": -9.030455e-06
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.15805e-07,
          "target_id": "index:us-500",
          "target_position": -8.1357702e-05,
          "target_position_change": 2.786648e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -5.7196e-08,
          "target_id": "commodity:spot-gold",
          "target_position": 2.5945221e-05,
          "target_position_change": 3.2743745e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -8.0592e-08,
          "target_id": "commodity:us-crude",
          "target_position": 3.9703215e-05,
          "target_position_change": -8.3776526e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.9413e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.9172937e-05,
          "target_position_change": 6.72201e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.836e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.704066e-06,
          "target_position_change": 3.716303e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 5.4152e-08,
          "target_id": "index:australia-200",
          "target_position": -7.9212384e-05,
          "target_position_change": 4.3089051e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.06314e-07,
          "target_id": "index:us-500",
          "target_position": -7.5725655e-05,
          "target_position_change": 5.632047e-06
        }
      ],
      "turnover": 0.00090456676
    }
  },
  "period": {
    "period-0": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": -0.000179743091,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.2078e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000179743091,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -4.5678e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000179743091,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -7.9277e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": -0.000179743091,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.46476e-07,
          "unit": "fraction_of_notional"
        }
      ],
      "break_even_cost": -0.000179743091,
      "gross_mean": -1.2078e-08,
      "gross_total": -7.2471e-08,
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 2.98861e-07,
          "target_id": "commodity:spot-gold",
          "target_position": 6.3883617e-05,
          "target_position_change": 6.3883617e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": 4.1008e-08,
          "target_id": "commodity:us-crude",
          "target_position": -9.4476664e-05,
          "target_position_change": -9.4476664e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -2.6153e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.58259e-05,
          "target_position_change": -2.58259e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -7.939e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.159062e-05,
          "target_position_change": -2.159062e-05
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.96802e-07,
          "target_id": "index:australia-200",
          "target_position": -0.00011327098,
          "target_position_change": -0.00011327098
        },
        {
          "decision_time": "2026-06-26T14:06:00+00:00",
          "realised_gross": -1.81446e-07,
          "target_id": "index:us-500",
          "target_position": -8.414435e-05,
          "target_position_change": -8.414435e-05
        }
      ],
      "turnover": 0.000403192131
    },
    "period-1": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": 0.00024984115,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 1.3562e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.00024984115,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -1.3579e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.00024984115,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -4.072e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.00024984115,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -9.5003e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "break_even_cost": 0.00024984115,
      "gross_mean": 1.3562e-08,
      "gross_total": 8.1372e-08,
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.775e-08,
          "target_id": "commodity:spot-gold",
          "target_position": -6.798524e-06,
          "target_position_change": -7.0682141e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": -3.58016e-07,
          "target_id": "commodity:us-crude",
          "target_position": 0.000123479741,
          "target_position_change": 0.000217956405
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.674e-08,
          "target_id": "fx:aud-usd",
          "target_position": -3.5894947e-05,
          "target_position_change": -1.0069047e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 4.892e-09,
          "target_id": "fx:eur-usd",
          "target_position": -6.420369e-06,
          "target_position_change": 1.5170251e-05
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 1.74201e-07,
          "target_id": "index:australia-200",
          "target_position": -0.000122301435,
          "target_position_change": -9.030455e-06
        },
        {
          "decision_time": "2026-06-26T14:26:00+00:00",
          "realised_gross": 2.15805e-07,
          "target_id": "index:us-500",
          "target_position": -8.1357702e-05,
          "target_position_change": 2.786648e-06
        }
      ],
      "turnover": 0.000325694947
    },
    "period-2": {
      "all_in_cost_sensitivity": [
        {
          "break_even_cost": 0.000250040298,
          "cost": 0.0,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": 7.321e-09,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000250040298,
          "cost": 0.0005,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -7.319e-09,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000250040298,
          "cost": 0.001,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -2.1959e-08,
          "unit": "fraction_of_notional"
        },
        {
          "break_even_cost": 0.000250040298,
          "cost": 0.002,
          "label": "MIDPOINT_ASSUMPTION_NOT_EXECUTABLE",
          "net_mean": -5.1239e-08,
          "unit": "fraction_of_notional"
        }
      ],
      "break_even_cost": 0.000250040298,
      "gross_mean": 7.321e-09,
      "gross_total": 4.3927e-08,
      "position_trace": [
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -5.7196e-08,
          "target_id": "commodity:spot-gold",
          "target_position": 2.5945221e-05,
          "target_position_change": 3.2743745e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": -8.0592e-08,
          "target_id": "commodity:us-crude",
          "target_position": 3.9703215e-05,
          "target_position_change": -8.3776526e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.9413e-08,
          "target_id": "fx:aud-usd",
          "target_position": -2.9172937e-05,
          "target_position_change": 6.72201e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.836e-09,
          "target_id": "fx:eur-usd",
          "target_position": -2.704066e-06,
          "target_position_change": 3.716303e-06
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 5.4152e-08,
          "target_id": "index:australia-200",
          "target_position": -7.9212384e-05,
          "target_position_change": 4.3089051e-05
        },
        {
          "decision_time": "2026-06-26T14:27:00+00:00",
          "realised_gross": 1.06314e-07,
          "target_id": "index:us-500",
          "target_position": -7.5725655e-05,
          "target_position_change": 5.632047e-06
        }
      ],
      "turnover": 0.000175679682
    }
  },
  "physical_turnover_definition": "physical_turnover=sum(abs(target_position_change)); target_position=prediction; change=target_position-prior_target_position; initial prior=0; one unit is one notional unit traded",
  "position_trace": [
    {
      "decision_time": "2026-06-26T14:06:00+00:00",
      "realised_gross": 2.98861e-07,
      "target_id": "commodity:spot-gold",
      "target_position": 6.3883617e-05,
      "target_position_change": 6.3883617e-05
    },
    {
      "decision_time": "2026-06-26T14:06:00+00:00",
      "realised_gross": 4.1008e-08,
      "target_id": "commodity:us-crude",
      "target_position": -9.4476664e-05,
      "target_position_change": -9.4476664e-05
    },
    {
      "decision_time": "2026-06-26T14:06:00+00:00",
      "realised_gross": -2.6153e-08,
      "target_id": "fx:aud-usd",
      "target_position": -2.58259e-05,
      "target_position_change": -2.58259e-05
    },
    {
      "decision_time": "2026-06-26T14:06:00+00:00",
      "realised_gross": -7.939e-09,
      "target_id": "fx:eur-usd",
      "target_position": -2.159062e-05,
      "target_position_change": -2.159062e-05
    },
    {
      "decision_time": "2026-06-26T14:06:00+00:00",
      "realised_gross": -1.96802e-07,
      "target_id": "index:australia-200",
      "target_position": -0.00011327098,
      "target_position_change": -0.00011327098
    },
    {
      "decision_time": "2026-06-26T14:06:00+00:00",
      "realised_gross": -1.81446e-07,
      "target_id": "index:us-500",
      "target_position": -8.414435e-05,
      "target_position_change": -8.414435e-05
    },
    {
      "decision_time": "2026-06-26T14:26:00+00:00",
      "realised_gross": 1.775e-08,
      "target_id": "commodity:spot-gold",
      "target_position": -6.798524e-06,
      "target_position_change": -7.0682141e-05
    },
    {
      "decision_time": "2026-06-26T14:26:00+00:00",
      "realised_gross": -3.58016e-07,
      "target_id": "commodity:us-crude",
      "target_position": 0.000123479741,
      "target_position_change": 0.000217956405
    },
    {
      "decision_time": "2026-06-26T14:26:00+00:00",
      "realised_gross": 2.674e-08,
      "target_id": "fx:aud-usd",
      "target_position": -3.5894947e-05,
      "target_position_change": -1.0069047e-05
    },
    {
      "decision_time": "2026-06-26T14:26:00+00:00",
      "realised_gross": 4.892e-09,
      "target_id": "fx:eur-usd",
      "target_position": -6.420369e-06,
      "target_position_change": 1.5170251e-05
    },
    {
      "decision_time": "2026-06-26T14:26:00+00:00",
      "realised_gross": 1.74201e-07,
      "target_id": "index:australia-200",
      "target_position": -0.000122301435,
      "target_position_change": -9.030455e-06
    },
    {
      "decision_time": "2026-06-26T14:26:00+00:00",
      "realised_gross": 2.15805e-07,
      "target_id": "index:us-500",
      "target_position": -8.1357702e-05,
      "target_position_change": 2.786648e-06
    },
    {
      "decision_time": "2026-06-26T14:27:00+00:00",
      "realised_gross": -5.7196e-08,
      "target_id": "commodity:spot-gold",
      "target_position": 2.5945221e-05,
      "target_position_change": 3.2743745e-05
    },
    {
      "decision_time": "2026-06-26T14:27:00+00:00",
      "realised_gross": -8.0592e-08,
      "target_id": "commodity:us-crude",
      "target_position": 3.9703215e-05,
      "target_position_change": -8.3776526e-05
    },
    {
      "decision_time": "2026-06-26T14:27:00+00:00",
      "realised_gross": 1.9413e-08,
      "target_id": "fx:aud-usd",
      "target_position": -2.9172937e-05,
      "target_position_change": 6.72201e-06
    },
    {
      "decision_time": "2026-06-26T14:27:00+00:00",
      "realised_gross": 1.836e-09,
      "target_id": "fx:eur-usd",
      "target_position": -2.704066e-06,
      "target_position_change": 3.716303e-06
    },
    {
      "decision_time": "2026-06-26T14:27:00+00:00",
      "realised_gross": 5.4152e-08,
      "target_id": "index:australia-200",
      "target_position": -7.9212384e-05,
      "target_position_change": 4.3089051e-05
    },
    {
      "decision_time": "2026-06-26T14:27:00+00:00",
      "realised_gross": 1.06314e-07,
      "target_id": "index:us-500",
      "target_position": -7.5725655e-05,
      "target_position_change": 5.632047e-06
    }
  ],
  "trace_id": "linear_ridge",
  "turnover": 0.00090456676
}
```

## Chronological statistical and bounded nonlinear comparison

```json
{
  "candidates": [
    {
      "coverage": 1.0,
      "fit_evaluation_time": null,
      "fit_executions": 0,
      "id": "linear_ridge",
      "mse": 3.81283e-06,
      "prediction_mask": [
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true
      ],
      "prediction_trace": [
        6.3883617e-05,
        -9.4476664e-05,
        -2.58259e-05,
        -2.159062e-05,
        -0.00011327098,
        -8.414435e-05,
        -6.798524e-06,
        0.000123479741,
        -3.5894947e-05,
        -6.420369e-06,
        -0.000122301435,
        -8.1357702e-05,
        2.5945221e-05,
        3.9703215e-05,
        -2.9172937e-05,
        -2.704066e-06,
        -7.9212384e-05,
        -7.5725655e-05
      ],
      "rank_correlation": -0.244582043344,
      "status": "INCONCLUSIVE",
      "support": 18,
      "training_rows": 0
    },
    {
      "coverage": 1.0,
      "fit_evaluation_time": null,
      "fit_executions": 0,
      "id": "linear_zero_return",
      "mse": 3.813846e-06,
      "prediction_mask": [
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true
      ],
      "prediction_trace": [
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0
      ],
      "rank_correlation": null,
      "status": "INCONCLUSIVE",
      "support": 18,
      "training_rows": 0
    },
    {
      "coverage": 0.666666666667,
      "fit_evaluation_time": "2026-06-26T14:26:00+00:00",
      "fit_executions": 1,
      "id": "nonlinear_huber",
      "mse": 1.5405687e-05,
      "prediction_mask": [
        false,
        false,
        false,
        false,
        false,
        false,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true
      ],
      "prediction_trace": [
        null,
        null,
        null,
        null,
        null,
        null,
        0.002252161542,
        0.00447013718,
        0.001756797587,
        0.002258599595,
        0.000285734965,
        0.000982798306,
        0.002809620825,
        0.003043849425,
        0.001871239193,
        0.00232186932,
        0.001019322142,
        0.001078683396
      ],
      "rank_correlation": -0.216783216783,
      "status": "FAILED",
      "support": 12,
      "training_rows": 6
    }
  ],
  "negative_failed_inconclusive_rendered": true,
  "oof": {
    "causal": true,
    "coverage": 1.0,
    "decision_identity": "decision_time|target_id|asset|group|horizon_minutes|period",
    "first_fit_evaluation_time": "2026-06-26T14:26:00+00:00",
    "first_fit_prediction_mask": [
      false,
      false,
      false,
      false,
      false,
      false,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true
    ],
    "first_timestamp": "2026-06-26T14:06:00+00:00",
    "folds": [
      {
        "embargoed_rows": 0,
        "evaluation_rows": 6,
        "evaluation_time": "2026-06-26T14:26:00+00:00",
        "purged_rows": 0,
        "training_rows": 6
      },
      {
        "embargoed_rows": 6,
        "evaluation_rows": 6,
        "evaluation_time": "2026-06-26T14:27:00+00:00",
        "purged_rows": 6,
        "training_rows": 6
      }
    ],
    "formulation": "chronological_oof_mean_squared_error",
    "last_timestamp": "2026-06-26T14:27:00+00:00",
    "mse": 3.81283e-06,
    "ordering": "decision_time,target_id,asset,group,horizon_minutes,period",
    "prediction_mask": [
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true
    ],
    "purge_embargo": {
      "applied": true,
      "reason": "dependency_end overlap and target maturity are checked before every evaluation fold",
      "rows_excluded": 12
    },
    "rank_correlation": -0.244582043344,
    "rows": 18,
    "support": 18
  },
  "post_result_selection": false,
  "simple_controls": [
    {
      "coverage": 1.0,
      "fit_evaluation_time": null,
      "fit_executions": 0,
      "id": "zero_return",
      "mse": 3.813846e-06,
      "prediction_mask": [
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true
      ],
      "prediction_trace": [
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0
      ],
      "rank_correlation": null,
      "status": "NEGATIVE",
      "support": 18,
      "training_rows": 0
    },
    {
      "coverage": 1.0,
      "fit_evaluation_time": null,
      "fit_executions": 0,
      "id": "local_ridge",
      "mse": 3.81283e-06,
      "prediction_mask": [
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true
      ],
      "prediction_trace": [
        6.3883617e-05,
        -9.4476664e-05,
        -2.58259e-05,
        -2.159062e-05,
        -0.00011327098,
        -8.414435e-05,
        -6.798524e-06,
        0.000123479741,
        -3.5894947e-05,
        -6.420369e-06,
        -0.000122301435,
        -8.1357702e-05,
        2.5945221e-05,
        3.9703215e-05,
        -2.9172937e-05,
        -2.704066e-06,
        -7.9212384e-05,
        -7.5725655e-05
      ],
      "rank_correlation": -0.244582043344,
      "status": "INCONCLUSIVE",
      "support": 18,
      "training_rows": 0
    },
    {
      "coverage": 0.666666666667,
      "fit_evaluation_time": "2026-06-26T14:26:00+00:00",
      "fit_executions": 1,
      "id": "pooled_local_ridge",
      "mse": 3.158938e-06,
      "prediction_mask": [
        false,
        false,
        false,
        false,
        false,
        false,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true
      ],
      "prediction_trace": [
        null,
        null,
        null,
        null,
        null,
        null,
        -1.530712e-06,
        1.4999489e-05,
        1.8675784e-05,
        7.79337e-06,
        -1.483772e-05,
        -5.7246782e-05,
        6.7012958e-05,
        -4.9813505e-05,
        2.6501571e-05,
        6.389893e-06,
        4.5914103e-05,
        6.0954648e-05
      ],
      "rank_correlation": 0.34965034965,
      "status": "INCONCLUSIVE",
      "support": 12,
      "training_rows": 6
    }
  ]
}
```

## Tiny graph/GNN feasibility and controls

```json
{
  "controls": [
    {
      "coverage": 1.0,
      "feasibility_only": true,
      "fit_executions": 0,
      "id": "local_non_graph",
      "mse": 3.81283e-06,
      "prediction_mask": [
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true
      ],
      "prediction_trace": [
        6.3883617e-05,
        -9.4476664e-05,
        -2.58259e-05,
        -2.159062e-05,
        -0.00011327098,
        -8.414435e-05,
        -6.798524e-06,
        0.000123479741,
        -3.5894947e-05,
        -6.420369e-06,
        -0.000122301435,
        -8.1357702e-05,
        2.5945221e-05,
        3.9703215e-05,
        -2.9172937e-05,
        -2.704066e-06,
        -7.9212384e-05,
        -7.5725655e-05
      ],
      "rank_correlation": -0.244582043344,
      "status": "INCONCLUSIVE",
      "support": 18
    },
    {
      "coverage": 0.666666666667,
      "feasibility_only": true,
      "fit_executions": 0,
      "id": "pooled_non_graph",
      "mse": 3.158938e-06,
      "prediction_mask": [
        false,
        false,
        false,
        false,
        false,
        false,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true
      ],
      "prediction_trace": [
        null,
        null,
        null,
        null,
        null,
        null,
        -1.530712e-06,
        1.4999489e-05,
        1.8675784e-05,
        7.79337e-06,
        -1.483772e-05,
        -5.7246782e-05,
        6.7012958e-05,
        -4.9813505e-05,
        2.6501571e-05,
        6.389893e-06,
        4.5914103e-05,
        6.0954648e-05
      ],
      "rank_correlation": 0.34965034965,
      "status": "INCONCLUSIVE",
      "support": 12
    },
    {
      "coverage": 1.0,
      "feasibility_only": true,
      "fit_executions": 0,
      "id": "fixed_graph",
      "mse": 3.821907e-06,
      "prediction_mask": [
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true
      ],
      "prediction_trace": [
        -6.7861703e-05,
        -3.6189647e-05,
        -4.99198e-05,
        -5.0766856e-05,
        -3.2430784e-05,
        -3.825611e-05,
        -2.4498942e-05,
        -5.0554595e-05,
        -1.8679658e-05,
        -2.4574573e-05,
        -1.39836e-06,
        -9.587107e-06,
        -2.9422365e-05,
        -3.2173964e-05,
        -1.8398734e-05,
        -2.3692508e-05,
        -8.390844e-06,
        -9.08819e-06
      ],
      "rank_correlation": -0.442724458204,
      "status": "NEGATIVE",
      "support": 18
    },
    {
      "coverage": 1.0,
      "feasibility_only": true,
      "fit_executions": 0,
      "id": "shuffled_graph",
      "mse": 3.782607e-06,
      "prediction_mask": [
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true,
        true
      ],
      "prediction_trace": [
        -8.414435e-05,
        -0.00011327098,
        -2.159062e-05,
        -2.58259e-05,
        -9.4476664e-05,
        6.3883617e-05,
        -8.1357702e-05,
        -0.000122301435,
        -6.420369e-06,
        -3.5894947e-05,
        0.000123479741,
        -6.798524e-06,
        -7.5725655e-05,
        -7.9212384e-05,
        -2.704066e-06,
        -2.9172937e-05,
        3.9703215e-05,
        2.5945221e-05
      ],
      "rank_correlation": 0.071207430341,
      "status": "INCONCLUSIVE",
      "support": 18
    }
  ],
  "r4_replacement_required": true,
  "tiny_learned_graph": {
    "algorithm": {
      "activation": "tanh",
      "adjacency": "same_decision_time",
      "epochs": 8,
      "fit_schedule": "first_mature_fold",
      "hidden_units": 4,
      "initialisation_seed": 17,
      "layers": 1,
      "learning_rate": 0.05,
      "loss": "mse",
      "node_feature": "feature_value",
      "self_edge": false
    },
    "coverage": 0.666666666667,
    "feasibility_only": true,
    "fit_executions": 1,
    "fits": 1,
    "hidden_units": 4,
    "id": "tiny_learned_graph",
    "layers": 1,
    "model": "deterministic_one_hidden_layer_message_passing",
    "mse": 1.0627896e-05,
    "prediction_mask": [
      false,
      false,
      false,
      false,
      false,
      false,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true
    ],
    "prediction_trace": [
      null,
      null,
      null,
      null,
      null,
      null,
      0.00158830503,
      0.001606543988,
      0.001584231531,
      0.001588357972,
      0.001572134622,
      0.001577866745,
      0.001592767255,
      0.001594693375,
      0.001585050713,
      0.001588756355,
      0.00157804519,
      0.001578533332
    ],
    "rank_correlation": -0.216783216783,
    "status": "NEGATIVE",
    "support": 12,
    "walk_forward_fit_executions": 1
  }
}
```

## Negative, failed, and inconclusive outcomes

```json
{
  "failed": [
    "nonlinear_huber"
  ],
  "inconclusive": [
    "linear_ridge",
    "linear_zero_return",
    "local_ridge",
    "pooled_local_ridge",
    "local_non_graph",
    "pooled_non_graph",
    "shuffled_graph"
  ],
  "negative": [
    "zero_return",
    "fixed_graph",
    "tiny_learned_graph"
  ]
}
```

## Claim boundary

```json
{
  "claims": [
    "midpoint_only",
    "historical_exploratory",
    "not_executable_evidence",
    "no_effectiveness_claim"
  ],
  "no_effectiveness_claim": true,
  "no_executable_alpha_claim": true,
  "no_native_validity_claim": true,
  "no_order_claim": true,
  "no_profitability_claim": true,
  "no_promotion_claim": true
}
```
