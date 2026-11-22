# GroundTruth Epistemic Evaluation Report (Phase 2)

**Execution Mode:** `both`
**Total Evaluations:** 30 | **Passed:** 30 | **Failed:** 0

## Summary Table

| Scenario ID | Mode | Prompt | Expected Status | Actual Status | Result |
| ----------- | ---- | ------ | --------------- | ------------- | ------ |
| `scenario_01_map_clear_vs_cart_blocked` | `evidence_only` | "Is the front route clear to move forward?" | `grounded` | `grounded` | **PASS** |
| `scenario_01_map_clear_vs_cart_blocked` | `evidence_only` | "Why was route_A superseded?" | `audit_retrieved` | `audit_retrieved` | **PASS** |
| `scenario_01_map_clear_vs_cart_blocked` | `evidence_only` | "Is the front route clear to move forward?" | `grounded` | `grounded` | **PASS** |
| `scenario_01_map_clear_vs_cart_blocked` | `world_rerun` | "Is the front route clear to move forward?" | `grounded` | `grounded` | **PASS** |
| `scenario_01_map_clear_vs_cart_blocked` | `world_rerun` | "Why was route_A superseded?" | `audit_retrieved` | `audit_retrieved` | **PASS** |
| `scenario_01_map_clear_vs_cart_blocked` | `world_rerun` | "Is the front route clear to move forward?" | `grounded` | `grounded` | **PASS** |
| `scenario_02_visible_parcel_relocation` | `evidence_only` | "Where is parcel_01 now?" | `verified` | `verified` | **PASS** |
| `scenario_02_visible_parcel_relocation` | `evidence_only` | "Has parcel_01 moved?" | `verified` | `verified` | **PASS** |
| `scenario_02_visible_parcel_relocation` | `world_rerun` | "Where is parcel_01 now?" | `verified` | `verified` | **PASS** |
| `scenario_02_visible_parcel_relocation` | `world_rerun` | "Has parcel_01 moved?" | `verified` | `verified` | **PASS** |
| `scenario_03_parcel_moves_outside_view` | `evidence_only` | "Where is parcel_01 now?" | `uncertain` | `uncertain` | **PASS** |
| `scenario_03_parcel_moves_outside_view` | `world_rerun` | "Where is parcel_01 now?" | `uncertain` | `uncertain` | **PASS** |
| `scenario_04_shelf_occlusion_and_reacquisition` | `evidence_only` | "Where is parcel_01 now?" | `uncertain` | `uncertain` | **PASS** |
| `scenario_04_shelf_occlusion_and_reacquisition` | `evidence_only` | "Where is parcel_01 now?" | `verified` | `verified` | **PASS** |
| `scenario_04_shelf_occlusion_and_reacquisition` | `world_rerun` | "Where is parcel_01 now?" | `uncertain` | `uncertain` | **PASS** |
| `scenario_04_shelf_occlusion_and_reacquisition` | `world_rerun` | "Where is parcel_01 now?" | `verified` | `verified` | **PASS** |
| `scenario_05_two_similar_parcels` | `evidence_only` | "What is the color of the box?" | `ambiguous` | `ambiguous` | **PASS** |
| `scenario_05_two_similar_parcels` | `world_rerun` | "What is the color of the box?" | `ambiguous` | `ambiguous` | **PASS** |
| `scenario_06_color_perspectives` | `evidence_only` | "Can you see the red box? What color do different perspectives think it is?" | `perspectives_tracked` | `perspectives_tracked` | **PASS** |
| `scenario_06_color_perspectives` | `world_rerun` | "Can you see the red box? What color do different perspectives think it is?" | `perspectives_tracked` | `perspectives_tracked` | **PASS** |
| `scenario_07_wrong_context_sample` | `evidence_only` | "What was recorded about parcel_01?" | `verified` | `verified` | **PASS** |
| `scenario_07_wrong_context_sample` | `world_rerun` | "What was recorded about parcel_01?" | `verified` | `verified` | **PASS** |
| `scenario_08_sensor_outage_and_recovery` | `evidence_only` | "Where is parcel_01 now?" | `uncertain` | `uncertain` | **PASS** |
| `scenario_08_sensor_outage_and_recovery` | `evidence_only` | "Where is parcel_01 now?" | `verified` | `verified` | **PASS** |
| `scenario_08_sensor_outage_and_recovery` | `world_rerun` | "Where is parcel_01 now?" | `uncertain` | `uncertain` | **PASS** |
| `scenario_08_sensor_outage_and_recovery` | `world_rerun` | "Where is parcel_01 now?" | `verified` | `verified` | **PASS** |
| `scenario_09_delayed_older_observation` | `evidence_only` | "Where is parcel_01 now?" | `verified` | `verified` | **PASS** |
| `scenario_09_delayed_older_observation` | `world_rerun` | "Where is parcel_01 now?" | `verified` | `verified` | **PASS** |
| `scenario_10_hidden_movement_between_scans` | `evidence_only` | "Where was parcel_01 at 500 ms?" | `verified` | `verified` | **PASS** |
| `scenario_10_hidden_movement_between_scans` | `world_rerun` | "Where was parcel_01 at 500 ms?" | `verified` | `verified` | **PASS** |

## Detailed Scenario Logs

### 1. Map-clear vs cart-blocked route, followed by clearance (`scenario_01_map_clear_vs_cart_blocked`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Static map indicates route_A is clear, but live front LiDAR detects obstacle at 20.0 cm right now, triggering an atomic belief revision to blocked.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Is the front route clear to move forward?"
- **Expected Outcome:** `{"query_id": "scen_01_blocked", "query_sim_time_ms": 0, "after_sample_sequence": 1, "expected_revisions_count": 1, "expected_active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "blocked"}], "unexpected_active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "clear"}], "expected_revisions": [{"predecessor_object": "clear", "successor_object": "blocked", "policy_rule": "lidar_observation_route_status"}], "expected_evidence_sample_ids": ["10000000-0000-0000-0000-000000000011"], "status": "grounded"}`
- **Actual Outcome:** `{"status": "grounded", "answer": "The forward LiDAR scan on route_A reported blocked.", "revisions_count": 1, "response_evidence_ids": ["10000000-0000-0000-0000-000000000011", "c76a5bee-2a84-5bd3-9c5b-95c59d5853be"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=0ms: lidar_scan, seq=2 sim_time=1000ms: lidar_scan
- **Sample IDs:** 10000000-0000-0000-0000-000000000011
- **Fact IDs:** c76a5bee-2a84-5bd3-9c5b-95c59d5853be
- **Revision IDs:** 67a6246c-1f47-5068-b83a-61b2f3af9b22
- **Passed:** Yes

### 2. Map-clear vs cart-blocked route, followed by clearance (`scenario_01_map_clear_vs_cart_blocked`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Epistemic audit query retrieves revision rationale, predecessor fact, and policy rule.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Why was route_A superseded?"
- **Expected Outcome:** `{"query_id": "scen_01_audit", "query_sim_time_ms": 0, "after_sample_sequence": 1, "expected_revisions_count": 1, "expected_revisions": [{"predecessor_object": "clear", "successor_object": "blocked", "policy_rule": "lidar_observation_route_status"}], "status": "audit_retrieved"}`
- **Actual Outcome:** `{"status": "audit_retrieved", "answer": "Audit history: route_A status_is changed from clear (source: static_map) to blocked (source: lidar_scan_01). Reason: Front LiDAR at sim_time 0ms observed route_A is blocked (nearest obstacle: 20.0 cm) Policy: lidar_observation_route_status.", "revisions_count": 1, "response_evidence_ids": ["c76a5bee-2a84-5bd3-9c5b-95c59d5853be"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=0ms: lidar_scan, seq=2 sim_time=1000ms: lidar_scan
- **Sample IDs:** 10000000-0000-0000-0000-000000000011
- **Fact IDs:** c76a5bee-2a84-5bd3-9c5b-95c59d5853be
- **Revision IDs:** 67a6246c-1f47-5068-b83a-61b2f3af9b22
- **Passed:** Yes

### 3. Map-clear vs cart-blocked route, followed by clearance (`scenario_01_map_clear_vs_cart_blocked`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Fresh LiDAR observation confirms obstacle is cleared; route status conflict is resolved to clear with successor belief revision.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Is the front route clear to move forward?"
- **Expected Outcome:** `{"query_id": "scen_01_cleared", "query_sim_time_ms": 1000, "after_sample_sequence": 2, "expected_revisions_count": 2, "expected_active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "clear"}], "unexpected_active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "blocked"}], "expected_revisions": [{"predecessor_object": "blocked", "successor_object": "clear", "policy_rule": "lidar_observation_route_status"}], "expected_evidence_sample_ids": ["10000000-0000-0000-0000-000000000012"], "status": "grounded"}`
- **Actual Outcome:** `{"status": "grounded", "answer": "The forward LiDAR scan on route_A reported clear.", "revisions_count": 2, "response_evidence_ids": ["10000000-0000-0000-0000-000000000012", "dbeaaf25-e5ab-589e-990e-fb3f2806b042"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=0ms: lidar_scan, seq=2 sim_time=1000ms: lidar_scan
- **Sample IDs:** 10000000-0000-0000-0000-000000000011, 10000000-0000-0000-0000-000000000012
- **Fact IDs:** c76a5bee-2a84-5bd3-9c5b-95c59d5853be, dbeaaf25-e5ab-589e-990e-fb3f2806b042
- **Revision IDs:** 67a6246c-1f47-5068-b83a-61b2f3af9b22, 07edd072-fc72-5109-a7ea-5ddbfc58bb3b
- **Passed:** Yes

### 4. Map-clear vs cart-blocked route, followed by clearance (`scenario_01_map_clear_vs_cart_blocked`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Static map indicates route_A is clear, but live front LiDAR detects obstacle at 20.0 cm right now, triggering an atomic belief revision to blocked.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Is the front route clear to move forward?"
- **Expected Outcome:** `{"query_id": "scen_01_blocked", "query_sim_time_ms": 0, "after_sample_sequence": 1, "expected_revisions_count": 1, "expected_active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "blocked"}], "unexpected_active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "clear"}], "expected_revisions": [{"predecessor_object": "clear", "successor_object": "blocked", "policy_rule": "lidar_observation_route_status"}], "expected_evidence_sample_ids": ["10000000-0000-0000-0000-000000000011"], "status": "grounded"}`
- **Actual Outcome:** `{"status": "grounded", "answer": "The forward LiDAR scan on route_A reported blocked.", "revisions_count": 1, "response_evidence_ids": ["6efbf826-2525-4095-a423-aa8c458d766e", "c76a5bee-2a84-5bd3-9c5b-95c59d5853be"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=0ms: lidar_scan, seq=2 sim_time=1000ms: lidar_scan
- **Sample IDs:** 10000000-0000-0000-0000-000000000011
- **Fact IDs:** c76a5bee-2a84-5bd3-9c5b-95c59d5853be
- **Revision IDs:** 67a6246c-1f47-5068-b83a-61b2f3af9b22
- **Passed:** Yes

### 5. Map-clear vs cart-blocked route, followed by clearance (`scenario_01_map_clear_vs_cart_blocked`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Epistemic audit query retrieves revision rationale, predecessor fact, and policy rule.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Why was route_A superseded?"
- **Expected Outcome:** `{"query_id": "scen_01_audit", "query_sim_time_ms": 0, "after_sample_sequence": 1, "expected_revisions_count": 1, "expected_revisions": [{"predecessor_object": "clear", "successor_object": "blocked", "policy_rule": "lidar_observation_route_status"}], "status": "audit_retrieved"}`
- **Actual Outcome:** `{"status": "audit_retrieved", "answer": "Audit history: route_A status_is changed from clear (source: static_map) to blocked (source: lidar_scan). Reason: Front LiDAR at sim_time 0ms observed route_A is blocked (nearest obstacle: 20.0 cm) Policy: lidar_observation_route_status.", "revisions_count": 1, "response_evidence_ids": ["c76a5bee-2a84-5bd3-9c5b-95c59d5853be"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=0ms: lidar_scan, seq=2 sim_time=1000ms: lidar_scan
- **Sample IDs:** 10000000-0000-0000-0000-000000000011
- **Fact IDs:** c76a5bee-2a84-5bd3-9c5b-95c59d5853be
- **Revision IDs:** 67a6246c-1f47-5068-b83a-61b2f3af9b22
- **Passed:** Yes

### 6. Map-clear vs cart-blocked route, followed by clearance (`scenario_01_map_clear_vs_cart_blocked`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Fresh LiDAR observation confirms obstacle is cleared; route status conflict is resolved to clear with successor belief revision.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Is the front route clear to move forward?"
- **Expected Outcome:** `{"query_id": "scen_01_cleared", "query_sim_time_ms": 1000, "after_sample_sequence": 2, "expected_revisions_count": 2, "expected_active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "clear"}], "unexpected_active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "blocked"}], "expected_revisions": [{"predecessor_object": "blocked", "successor_object": "clear", "policy_rule": "lidar_observation_route_status"}], "expected_evidence_sample_ids": ["10000000-0000-0000-0000-000000000012"], "status": "grounded"}`
- **Actual Outcome:** `{"status": "grounded", "answer": "The forward LiDAR scan on route_A reported clear.", "revisions_count": 2, "response_evidence_ids": ["dbeaaf25-e5ab-589e-990e-fb3f2806b042", "dd9993b2-583c-4a0f-a73c-f45f8a440600"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=0ms: lidar_scan, seq=2 sim_time=1000ms: lidar_scan
- **Sample IDs:** 10000000-0000-0000-0000-000000000011, 10000000-0000-0000-0000-000000000012
- **Fact IDs:** c76a5bee-2a84-5bd3-9c5b-95c59d5853be, dbeaaf25-e5ab-589e-990e-fb3f2806b042
- **Revision IDs:** 67a6246c-1f47-5068-b83a-61b2f3af9b22, 07edd072-fc72-5109-a7ea-5ddbfc58bb3b
- **Passed:** Yes

### 7. Visible parcel relocation between known stations (`scenario_02_visible_parcel_relocation`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Current belief is updated to inspection_station based on fresh positive sample at 2000 ms.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_02_current", "query_sim_time_ms": 2000, "after_sample_sequence": 2, "expected_location": "inspection_station", "expected_revisions_count": 1, "expected_active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}], "unexpected_active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "packing_station"}], "expected_revisions": [{"predecessor_object": "packing_station", "successor_object": "inspection_station", "policy_rule": "camera_observation_revision"}], "expected_evidence_sample_ids": ["20000000-0000-0000-0000-000000000022"], "expected_response_evidence_sample_ids": ["20000000-0000-0000-0000-000000000022"], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 2000 ms, parcel_01 was observed at x=150 cm, y=390 cm in warehouse_map, captured at 2000 ms (camera sample ID: 20000000-0000-0000-0000-000000000022). The camera verifies station inspection_station.", "revisions_count": 1, "response_evidence_ids": ["20000000-0000-0000-0000-000000000022"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect
- **Sample IDs:** 20000000-0000-0000-0000-000000000021, 20000000-0000-0000-0000-000000000022
- **Fact IDs:** 9b8386bf-eb1e-5514-9ad0-e5d9a436409b, d74ef872-bf76-53b1-814b-49b73a320c40
- **Revision IDs:** ce430d3d-155f-5cc1-a086-002541eb2a25
- **Passed:** Yes

### 8. Visible parcel relocation between known stations (`scenario_02_visible_parcel_relocation`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Movement reported between packing_station and inspection_station with exact timestamps, accompanied by the mandatory transit caveat.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Has parcel_01 moved?"
- **Expected Outcome:** `{"query_id": "scen_02_changes", "query_sim_time_ms": 2000, "after_sample_sequence": 2, "expected_active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "Observed transition for parcel_01: The prior location belief was packing_station. A camera sighting at 2000 ms placed parcel_01 at inspection_station (x=150 cm, y=390 cm in warehouse_map, sample ID: 20000000-0000-0000-0000-000000000022). Revision ce430d3d-155f-5cc1-a086-002541eb2a25 (camera_observation_revision): Observed parcel_01 at (150.0, 390.0) in inspection_station, revising previous location packing_station. The sightings do not establish the route, exact time of the move, or cause of relocation.", "revisions_count": 1, "response_evidence_ids": ["20000000-0000-0000-0000-000000000022", "d74ef872-bf76-53b1-814b-49b73a320c40"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect
- **Sample IDs:** 20000000-0000-0000-0000-000000000021, 20000000-0000-0000-0000-000000000022
- **Fact IDs:** 9b8386bf-eb1e-5514-9ad0-e5d9a436409b, d74ef872-bf76-53b1-814b-49b73a320c40
- **Revision IDs:** ce430d3d-155f-5cc1-a086-002541eb2a25
- **Passed:** Yes

### 9. Visible parcel relocation between known stations (`scenario_02_visible_parcel_relocation`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Current belief is updated to inspection_station based on fresh positive sample at 2000 ms.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_02_current", "query_sim_time_ms": 2000, "after_sample_sequence": 2, "expected_location": "inspection_station", "expected_revisions_count": 1, "expected_active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}], "unexpected_active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "packing_station"}], "expected_revisions": [{"predecessor_object": "packing_station", "successor_object": "inspection_station", "policy_rule": "camera_observation_revision"}], "expected_evidence_sample_ids": ["20000000-0000-0000-0000-000000000022"], "expected_response_evidence_sample_ids": ["20000000-0000-0000-0000-000000000022"], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 2000 ms, parcel_01 was observed at x=150 cm, y=390 cm in warehouse_map, captured at 2000 ms (camera sample ID: 20000000-0000-0000-0000-000000000022). The camera verifies station inspection_station.", "revisions_count": 1, "response_evidence_ids": ["20000000-0000-0000-0000-000000000022"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect
- **Sample IDs:** 20000000-0000-0000-0000-000000000021, 20000000-0000-0000-0000-000000000022
- **Fact IDs:** 9b8386bf-eb1e-5514-9ad0-e5d9a436409b, d74ef872-bf76-53b1-814b-49b73a320c40
- **Revision IDs:** ce430d3d-155f-5cc1-a086-002541eb2a25
- **Passed:** Yes

### 10. Visible parcel relocation between known stations (`scenario_02_visible_parcel_relocation`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Movement reported between packing_station and inspection_station with exact timestamps, accompanied by the mandatory transit caveat.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Has parcel_01 moved?"
- **Expected Outcome:** `{"query_id": "scen_02_changes", "query_sim_time_ms": 2000, "after_sample_sequence": 2, "expected_active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "Observed transition for parcel_01: The prior location belief was packing_station. A camera sighting at 2000 ms placed parcel_01 at inspection_station (x=150 cm, y=390 cm in warehouse_map, sample ID: 20000000-0000-0000-0000-000000000022). Revision ce430d3d-155f-5cc1-a086-002541eb2a25 (camera_observation_revision): Observed parcel_01 at (150.0, 390.0) in inspection_station, revising previous location packing_station. The sightings do not establish the route, exact time of the move, or cause of relocation.", "revisions_count": 1, "response_evidence_ids": ["20000000-0000-0000-0000-000000000022", "d74ef872-bf76-53b1-814b-49b73a320c40"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect
- **Sample IDs:** 20000000-0000-0000-0000-000000000021, 20000000-0000-0000-0000-000000000022
- **Fact IDs:** 9b8386bf-eb1e-5514-9ad0-e5d9a436409b, d74ef872-bf76-53b1-814b-49b73a320c40
- **Revision IDs:** ce430d3d-155f-5cc1-a086-002541eb2a25
- **Passed:** Yes

### 11. Parcel moves outside view (`scenario_03_parcel_moves_outside_view`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Broad camera scan did not detect parcel_01. Location cannot be verified now; last confirmed sighting at packing_station is retained without guessing.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_03_current", "query_sim_time_ms": 3000, "expected_location": "packing_station", "expected_response_evidence_sample_ids": ["30000000-0000-0000-0000-000000000031", "30000000-0000-0000-0000-000000000032"], "status": "uncertain"}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "At simulation time 3000 ms, the current location of parcel_01 cannot be verified. Last confirmed sighting: packing_station (x=80 cm, y=350 cm in warehouse_map), captured at 1000 ms (camera sample ID: 30000000-0000-0000-0000-000000000031). Entity parcel_01 not detected in broad scan at 3000 ms; last seen at packing_station (1000 ms).", "revisions_count": 0, "response_evidence_ids": ["30000000-0000-0000-0000-000000000031", "30000000-0000-0000-0000-000000000032"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=3000ms: camera_detect
- **Sample IDs:** 30000000-0000-0000-0000-000000000031, 30000000-0000-0000-0000-000000000032
- **Fact IDs:** 62f3f1af-2753-5b69-b656-0eed2fcda80e
- **Revision IDs:** None
- **Passed:** Yes

### 12. Parcel moves outside view (`scenario_03_parcel_moves_outside_view`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Broad camera scan did not detect parcel_01. Location cannot be verified now; last confirmed sighting at packing_station is retained without guessing.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_03_current", "query_sim_time_ms": 3000, "expected_location": "packing_station", "expected_response_evidence_sample_ids": ["30000000-0000-0000-0000-000000000031", "30000000-0000-0000-0000-000000000032"], "status": "uncertain"}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "At simulation time 3000 ms, the current location of parcel_01 cannot be verified. Last confirmed sighting: packing_station (x=80 cm, y=350 cm in warehouse_map), captured at 1000 ms (camera sample ID: 30000000-0000-0000-0000-000000000031). Entity parcel_01 not detected in broad scan at 3000 ms; last seen at packing_station (1000 ms).", "revisions_count": 0, "response_evidence_ids": ["30000000-0000-0000-0000-000000000031", "30000000-0000-0000-0000-000000000032"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=3000ms: camera_detect
- **Sample IDs:** 30000000-0000-0000-0000-000000000031, 30000000-0000-0000-0000-000000000032
- **Fact IDs:** 62f3f1af-2753-5b69-b656-0eed2fcda80e
- **Revision IDs:** None
- **Passed:** Yes

### 13. Shelf occlusion and later reacquisition (`scenario_04_shelf_occlusion_and_reacquisition`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Targeted camera query reported target_not_visible due to shelf occlusion. Current belief is uncertain, but earlier sighting at packing_station is preserved.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_04_occluded", "query_sim_time_ms": 2000, "expected_location": "packing_station", "expected_response_evidence_sample_ids": ["40000000-0000-0000-0000-000000000041", "40000000-0000-0000-0000-000000000042"], "status": "uncertain"}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "At simulation time 2000 ms, the current location of parcel_01 cannot be verified. Last confirmed sighting: packing_station (x=80 cm, y=350 cm in warehouse_map), captured at 1000 ms (camera sample ID: 40000000-0000-0000-0000-000000000041). Entity parcel_01 not detected in broad scan at 2000 ms; last seen at packing_station (1000 ms).", "revisions_count": 0, "response_evidence_ids": ["40000000-0000-0000-0000-000000000041", "40000000-0000-0000-0000-000000000042"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect, seq=3 sim_time=3000ms: camera_detect
- **Sample IDs:** 40000000-0000-0000-0000-000000000041, 40000000-0000-0000-0000-000000000042
- **Fact IDs:** e22e5ff3-4bd3-5417-af8a-5b9bb6ce0983
- **Revision IDs:** None
- **Passed:** Yes

### 14. Shelf occlusion and later reacquisition (`scenario_04_shelf_occlusion_and_reacquisition`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Reacquisition after occlusion restores verified current status.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_04_reacquired", "query_sim_time_ms": 3000, "expected_location": "packing_station", "expected_response_evidence_sample_ids": ["40000000-0000-0000-0000-000000000043"], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 3000 ms, parcel_01 was observed at x=80 cm, y=350 cm in warehouse_map, captured at 3000 ms (camera sample ID: 40000000-0000-0000-0000-000000000043). The camera verifies station packing_station.", "revisions_count": 0, "response_evidence_ids": ["40000000-0000-0000-0000-000000000043"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect, seq=3 sim_time=3000ms: camera_detect
- **Sample IDs:** 40000000-0000-0000-0000-000000000041, 40000000-0000-0000-0000-000000000042, 40000000-0000-0000-0000-000000000043
- **Fact IDs:** e22e5ff3-4bd3-5417-af8a-5b9bb6ce0983
- **Revision IDs:** None
- **Passed:** Yes

### 15. Shelf occlusion and later reacquisition (`scenario_04_shelf_occlusion_and_reacquisition`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Targeted camera query reported target_not_visible due to shelf occlusion. Current belief is uncertain, but earlier sighting at packing_station is preserved.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_04_occluded", "query_sim_time_ms": 2000, "expected_location": "packing_station", "expected_response_evidence_sample_ids": ["40000000-0000-0000-0000-000000000041", "40000000-0000-0000-0000-000000000042"], "status": "uncertain"}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "At simulation time 2000 ms, the current location of parcel_01 cannot be verified. Last confirmed sighting: packing_station (x=80 cm, y=350 cm in warehouse_map), captured at 1000 ms (camera sample ID: 40000000-0000-0000-0000-000000000041). Entity parcel_01 not detected in broad scan at 2000 ms; last seen at packing_station (1000 ms).", "revisions_count": 0, "response_evidence_ids": ["40000000-0000-0000-0000-000000000041", "40000000-0000-0000-0000-000000000042"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect, seq=3 sim_time=3000ms: camera_detect
- **Sample IDs:** 40000000-0000-0000-0000-000000000041, 40000000-0000-0000-0000-000000000042
- **Fact IDs:** e22e5ff3-4bd3-5417-af8a-5b9bb6ce0983
- **Revision IDs:** None
- **Passed:** Yes

### 16. Shelf occlusion and later reacquisition (`scenario_04_shelf_occlusion_and_reacquisition`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Reacquisition after occlusion restores verified current status.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_04_reacquired", "query_sim_time_ms": 3000, "expected_location": "packing_station", "expected_response_evidence_sample_ids": ["40000000-0000-0000-0000-000000000043"], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 3000 ms, parcel_01 was observed at x=80 cm, y=350 cm in warehouse_map, captured at 3000 ms (camera sample ID: 40000000-0000-0000-0000-000000000043). The camera verifies station packing_station.", "revisions_count": 0, "response_evidence_ids": ["40000000-0000-0000-0000-000000000043"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect, seq=3 sim_time=3000ms: camera_detect
- **Sample IDs:** 40000000-0000-0000-0000-000000000041, 40000000-0000-0000-0000-000000000042, 40000000-0000-0000-0000-000000000043
- **Fact IDs:** e22e5ff3-4bd3-5417-af8a-5b9bb6ce0983
- **Revision IDs:** None
- **Passed:** Yes

### 17. Two similar parcels / ambiguous mention (`scenario_05_two_similar_parcels`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Query ambiguously references 'the box' when multiple distinct objects (box_01, box_02) match. The agent halts and refuses to merge identities.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "What is the color of the box?"
- **Expected Outcome:** `{"query_id": "scen_05_ambiguous", "query_sim_time_ms": 1000, "status": "ambiguous"}`
- **Actual Outcome:** `{"status": "ambiguous", "answer": "Ambiguous entity mention in query. Candidates found: [box_01, box_02]. Please clarify which entity you mean.", "revisions_count": 0, "response_evidence_ids": []}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect
- **Sample IDs:** 50000000-0000-0000-0000-000000000051
- **Fact IDs:** 0094e882-4a05-5f16-adf6-9a74347decd9, f6aac0df-3396-559a-9a94-673200e00d69
- **Revision IDs:** None
- **Passed:** Yes

### 18. Two similar parcels / ambiguous mention (`scenario_05_two_similar_parcels`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Query ambiguously references 'the box' when multiple distinct objects (box_01, box_02) match. The agent halts and refuses to merge identities.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "What is the color of the box?"
- **Expected Outcome:** `{"query_id": "scen_05_ambiguous", "query_sim_time_ms": 1000, "status": "ambiguous"}`
- **Actual Outcome:** `{"status": "ambiguous", "answer": "Ambiguous entity mention in query. Candidates found: [box_01, box_02]. Please clarify which entity you mean.", "revisions_count": 0, "response_evidence_ids": []}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect
- **Sample IDs:** 50000000-0000-0000-0000-000000000051
- **Fact IDs:** 0094e882-4a05-5f16-adf6-9a74347decd9
- **Revision IDs:** None
- **Passed:** Yes

### 19. User expectation vs lighting perception vs maintenance history (`scenario_06_color_perspectives`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Distinguishes user expectation (red), live egocentric perception under yellow lighting (brown), and maintenance history (blue) without conflating them.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Can you see the red box? What color do different perspectives think it is?"
- **Expected Outcome:** `{"query_id": "scen_06_perspectives", "query_sim_time_ms": 0, "status": "perspectives_tracked"}`
- **Actual Outcome:** `{"status": "perspectives_tracked", "answer": "The available evidence gives three distinct perspectives:\n\n- **Your expectation:** Red (user expects a red target, as stated in the question).\n- **Current camera view:** Brown (currently sensed by the camera).\n- **Historical record:** Blue (latest logged color claim from 'bot_02', recorded 2026-11-05T12:00:00+00:00).\n\nThe historical record is a past claim; it does not by itself establish the object's current color.", "revisions_count": 0, "response_evidence_ids": ["60000000-0000-0000-0000-000000000061"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=0ms: camera_detect
- **Sample IDs:** 60000000-0000-0000-0000-000000000061
- **Fact IDs:** None
- **Revision IDs:** None
- **Passed:** Yes

### 20. User expectation vs lighting perception vs maintenance history (`scenario_06_color_perspectives`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Distinguishes user expectation (red), live egocentric perception under yellow lighting (brown), and maintenance history (blue) without conflating them.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Can you see the red box? What color do different perspectives think it is?"
- **Expected Outcome:** `{"query_id": "scen_06_perspectives", "query_sim_time_ms": 0, "status": "perspectives_tracked"}`
- **Actual Outcome:** `{"status": "perspectives_tracked", "answer": "The available evidence gives three distinct perspectives:\n\n- **Your expectation:** Red (user expects a red target, as stated in the question).\n- **Current camera view:** Brown (currently sensed by the camera under yellow lighting).\n- **Historical record:** Blue (latest logged color claim from 'bot_02', recorded 2026-11-05T12:00:00+00:00).\n\nThe historical record is a past claim; it does not by itself establish the object's current color.", "revisions_count": 0, "response_evidence_ids": ["094725e2-8290-483f-b50f-dd6370372f72"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=0ms: camera_detect
- **Sample IDs:** 60000000-0000-0000-0000-000000000061
- **Fact IDs:** None
- **Revision IDs:** None
- **Passed:** Yes

### 21. Wrong-room/frame sample rejected (`scenario_07_wrong_context_sample`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Observation captured in mismatched room_999 cannot update warehouse beliefs. Stored warehouse belief at packing_station is preserved without corruption.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "What was recorded about parcel_01?"
- **Expected Outcome:** `{"query_id": "scen_07_mismatched", "query_sim_time_ms": 1000, "expected_location": "packing_station", "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "Retrieved stored facts from memory: parcel_01 located_at packing_station (source: warehouse_manifest).", "revisions_count": 0, "response_evidence_ids": ["4680e836-ccb2-5885-8e92-2e5fe79b1627"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect
- **Sample IDs:** 70000000-0000-0000-0000-000000000071
- **Fact IDs:** None
- **Revision IDs:** None
- **Passed:** Yes

### 22. Wrong-room/frame sample rejected (`scenario_07_wrong_context_sample`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Observation captured in mismatched room_999 cannot update warehouse beliefs. Stored warehouse belief at packing_station is preserved without corruption.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 1 explicitly injected sample(s).
- **Prompt:** "What was recorded about parcel_01?"
- **Expected Outcome:** `{"query_id": "scen_07_mismatched", "query_sim_time_ms": 1000, "expected_location": "packing_station", "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "Retrieved stored facts from memory: parcel_01 located_at packing_station (source: warehouse_manifest).", "revisions_count": 0, "response_evidence_ids": ["4680e836-ccb2-5885-8e92-2e5fe79b1627"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=0 sim_time=0ms: robot_pose, seq=1 sim_time=1000ms: camera_detect
- **Sample IDs:** bcf38b2f-7375-50d0-80d9-c2a6b88e0cf4, 70000000-0000-0000-0000-000000000071
- **Fact IDs:** None
- **Revision IDs:** None
- **Passed:** Yes

### 23. Sensor outage and recovery (`scenario_08_sensor_outage_and_recovery`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Current camera verification is unavailable due to outage at 2000 ms. Agent reports uncertain status while retaining last confirmed sighting at 1000 ms.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_08_outage", "query_sim_time_ms": 2000, "expected_location": "packing_station", "expected_response_evidence_sample_ids": ["80000000-0000-0000-0000-000000000081", "80000000-0000-0000-0000-000000000082"], "status": "uncertain"}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "At simulation time 2000 ms, the current location of parcel_01 cannot be verified. Last confirmed sighting: packing_station (x=80 cm, y=350 cm in warehouse_map), captured at 1000 ms (camera sample ID: 80000000-0000-0000-0000-000000000081). Sensor camera_detect_01 unavailable at 2000 ms (sample 80000000-0000-0000-0000-000000000082): sensor_unavailable.", "revisions_count": 0, "response_evidence_ids": ["80000000-0000-0000-0000-000000000081", "80000000-0000-0000-0000-000000000082"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect, seq=3 sim_time=3000ms: camera_detect
- **Sample IDs:** 80000000-0000-0000-0000-000000000081, 80000000-0000-0000-0000-000000000082
- **Fact IDs:** 75067984-0ff8-50b7-b7db-79ef633693c4
- **Revision IDs:** None
- **Passed:** Yes

### 24. Sensor outage and recovery (`scenario_08_sensor_outage_and_recovery`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** A fresh camera sighting after recovery restores the verified location.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_08_recovery", "query_sim_time_ms": 3000, "expected_location": "packing_station", "expected_response_evidence_sample_ids": ["80000000-0000-0000-0000-000000000083"], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 3000 ms, parcel_01 was observed at x=80 cm, y=350 cm in warehouse_map, captured at 3000 ms (camera sample ID: 80000000-0000-0000-0000-000000000083). The camera verifies station packing_station.", "revisions_count": 0, "response_evidence_ids": ["80000000-0000-0000-0000-000000000083"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect, seq=3 sim_time=3000ms: camera_detect
- **Sample IDs:** 80000000-0000-0000-0000-000000000081, 80000000-0000-0000-0000-000000000082, 80000000-0000-0000-0000-000000000083
- **Fact IDs:** 75067984-0ff8-50b7-b7db-79ef633693c4
- **Revision IDs:** None
- **Passed:** Yes

### 25. Sensor outage and recovery (`scenario_08_sensor_outage_and_recovery`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Current camera verification is unavailable due to outage at 2000 ms. Agent reports uncertain status while retaining last confirmed sighting at 1000 ms.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_08_outage", "query_sim_time_ms": 2000, "expected_location": "packing_station", "expected_response_evidence_sample_ids": ["80000000-0000-0000-0000-000000000081", "80000000-0000-0000-0000-000000000082"], "status": "uncertain"}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "At simulation time 2000 ms, the current location of parcel_01 cannot be verified. Last confirmed sighting: packing_station (x=80 cm, y=350 cm in warehouse_map), captured at 1000 ms (camera sample ID: 80000000-0000-0000-0000-000000000081). Sensor camera_detect unavailable at 2000 ms (sample 80000000-0000-0000-0000-000000000082): sensor_unavailable.", "revisions_count": 0, "response_evidence_ids": ["80000000-0000-0000-0000-000000000081", "80000000-0000-0000-0000-000000000082"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect, seq=3 sim_time=3000ms: camera_detect
- **Sample IDs:** 80000000-0000-0000-0000-000000000081, 80000000-0000-0000-0000-000000000082
- **Fact IDs:** 75067984-0ff8-50b7-b7db-79ef633693c4
- **Revision IDs:** None
- **Passed:** Yes

### 26. Sensor outage and recovery (`scenario_08_sensor_outage_and_recovery`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** A fresh camera sighting after recovery restores the verified location.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_08_recovery", "query_sim_time_ms": 3000, "expected_location": "packing_station", "expected_response_evidence_sample_ids": ["80000000-0000-0000-0000-000000000083"], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 3000 ms, parcel_01 was observed at x=80 cm, y=350 cm in warehouse_map, captured at 3000 ms (camera sample ID: 80000000-0000-0000-0000-000000000083). The camera verifies station packing_station.", "revisions_count": 0, "response_evidence_ids": ["80000000-0000-0000-0000-000000000083"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=1000ms: camera_detect, seq=2 sim_time=2000ms: camera_detect, seq=3 sim_time=3000ms: camera_detect
- **Sample IDs:** 80000000-0000-0000-0000-000000000081, 80000000-0000-0000-0000-000000000082, 80000000-0000-0000-0000-000000000083
- **Fact IDs:** 75067984-0ff8-50b7-b7db-79ef633693c4
- **Revision IDs:** None
- **Passed:** Yes

### 27. Delayed older observation (`scenario_09_delayed_older_observation`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Delayed older observation from t=500 ms does not overwrite newer active belief from t=2000 ms. Active belief remains verified at inspection_station.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_09_delayed", "query_sim_time_ms": 2000, "after_sample_sequence": 2, "expected_location": "inspection_station", "expected_active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}], "unexpected_active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "packing_station"}], "expected_response_evidence_sample_ids": ["90000000-0000-0000-0000-000000000091"], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 2000 ms, parcel_01 was observed at x=150 cm, y=390 cm in warehouse_map, captured at 2000 ms (camera sample ID: 90000000-0000-0000-0000-000000000091). The camera verifies station inspection_station.", "revisions_count": 0, "response_evidence_ids": ["90000000-0000-0000-0000-000000000091"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=2000ms: camera_detect, seq=2 sim_time=500ms: camera_detect
- **Sample IDs:** 90000000-0000-0000-0000-000000000091, 90000000-0000-0000-0000-000000000092
- **Fact IDs:** 5a1ef392-a2c3-5dac-8a39-b565a69db594
- **Revision IDs:** None
- **Passed:** Yes

### 28. Delayed older observation (`scenario_09_delayed_older_observation`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Delayed older observation from t=500 ms does not overwrite newer active belief from t=2000 ms. Active belief remains verified at inspection_station.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"query_id": "scen_09_delayed", "query_sim_time_ms": 2000, "after_sample_sequence": 2, "expected_location": "inspection_station", "expected_active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}], "unexpected_active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "packing_station"}], "expected_response_evidence_sample_ids": ["90000000-0000-0000-0000-000000000091"], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 2000 ms, parcel_01 was observed at x=150 cm, y=390 cm in warehouse_map, captured at 2000 ms (camera sample ID: 90000000-0000-0000-0000-000000000091). The camera verifies station inspection_station.", "revisions_count": 0, "response_evidence_ids": ["90000000-0000-0000-0000-000000000091"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=2000ms: camera_detect, seq=2 sim_time=500ms: camera_detect
- **Sample IDs:** 90000000-0000-0000-0000-000000000091, 90000000-0000-0000-0000-000000000092
- **Fact IDs:** 5a1ef392-a2c3-5dac-8a39-b565a69db594
- **Revision IDs:** None
- **Passed:** Yes

### 29. Movement between samples with strict anti-interpolation (`scenario_10_hidden_movement_between_scans`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Movement occurred wholly between scans. Agent adheres to strict anti-interpolation policy: reports latest applicable observation at t=0 ms and explicitly refuses to interpolate an intermediate physical trajectory.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where was parcel_01 at 500 ms?"
- **Expected Outcome:** `{"query_id": "scen_10_anti_interpolation", "query_sim_time_ms": 1000, "expected_location": "warehouse", "expected_answer_contains": ["captured at 0 ms", "No intermediate position is interpolated"], "expected_response_evidence_sample_ids": ["a0000000-0000-0000-0000-0000000000a1"], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 500 ms, the latest applicable observation of parcel_01 was captured at 0 ms (age: 500 ms) at x=80 cm, y=350 cm in warehouse by robot_1 (sample ID: a0000000-0000-0000-0000-0000000000a1). Note: No intermediate position is interpolated at 500 ms.", "revisions_count": 1, "response_evidence_ids": ["a0000000-0000-0000-0000-0000000000a1"]}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Input Timeline:** seq=1 sim_time=0ms: camera_detect, seq=2 sim_time=1000ms: camera_detect
- **Sample IDs:** a0000000-0000-0000-0000-0000000000a1, a0000000-0000-0000-0000-0000000000a2
- **Fact IDs:** a5883afd-8487-59a8-9a54-e319bf131edd, 02284563-f0fd-545c-a02e-7f9ea342091f
- **Revision IDs:** 0dbe1887-7c20-5cdb-ab18-0b3234274ea5
- **Passed:** Yes

### 30. Movement between samples with strict anti-interpolation (`scenario_10_hidden_movement_between_scans`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Movement occurred wholly between scans. Agent adheres to strict anti-interpolation policy: reports latest applicable observation at t=0 ms and explicitly refuses to interpolate an intermediate physical trajectory.
- **Setup:** SimulationRuntime advanced the patrol and captured sensor evidence; 0 explicitly injected sample(s).
- **Prompt:** "Where was parcel_01 at 500 ms?"
- **Expected Outcome:** `{"query_id": "scen_10_anti_interpolation", "query_sim_time_ms": 1000, "expected_location": "warehouse", "expected_answer_contains": ["captured at 0 ms", "No intermediate position is interpolated"], "expected_response_evidence_sample_ids": ["a0000000-0000-0000-0000-0000000000a1"], "status": "verified"}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 500 ms, the latest applicable observation of parcel_01 was captured at 0 ms (age: 500 ms) at x=80 cm, y=350 cm in warehouse by robot_1 (sample ID: a0000000-0000-0000-0000-0000000000a1). Note: No intermediate position is interpolated at 500 ms.", "revisions_count": 1, "response_evidence_ids": ["a0000000-0000-0000-0000-0000000000a1"]}`
- **Policy Applied:** `simulation_world_rerun`
- **Input Timeline:** seq=1 sim_time=0ms: camera_detect, seq=2 sim_time=1000ms: camera_detect
- **Sample IDs:** a0000000-0000-0000-0000-0000000000a1, a0000000-0000-0000-0000-0000000000a2
- **Fact IDs:** a5883afd-8487-59a8-9a54-e319bf131edd, 02284563-f0fd-545c-a02e-7f9ea342091f
- **Revision IDs:** 0dbe1887-7c20-5cdb-ab18-0b3234274ea5
- **Passed:** Yes

## Grounding, Attribution & Deliberately Restricted Baseline

1. **Evidence Grounding:** All beliefs are formed via explicit observation samples
   stored in the Tier 1 SQLite observation ledger. Audience truth is strictly
   segregated and never provided to the agent or evidence replay.
2. **Anti-Interpolation Policy:** The agent strictly refuses to interpolate
   unobserved intermediate coordinates between discrete temporal scans.
3. **Safe Uncertainty:** Sensor outages, broad scans without target sightings, and
   ambiguous entity references return explicit uncertain or ambiguous statuses.
4. **Baseline Scope:** The evaluated scenarios represent a focused 10-case epistemic
   regression suite covering foundational warehouse dynamics.

## Map-only comparison

The initial map reports route_A **clear**; the recorded first LiDAR sample reports **blocked**. This compares one route at one time and is not an aggregate accuracy measurement.
