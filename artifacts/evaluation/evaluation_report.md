# GroundTruth Epistemic Evaluation Report (Phase 2)

**Run Date:** 2026-11-22 14:13:44 UTC  
**Execution Mode:** `both`  
**Total Evaluations:** 28 | **Passed:** 28 | **Failed:** 0  

## Summary Table

| Scenario ID | Mode | Prompt | Expected Status | Actual Status | Result |
| ----------- | ---- | ------ | --------------- | ------------- | ------ |
| `scenario_01_map_clear_vs_cart_blocked` | `evidence_only` | "Is the front route clear to move forward?" | `verified` | `verified` | **PASS** |
| `scenario_01_map_clear_vs_cart_blocked` | `evidence_only` | "Why was route_A superseded?" | `audit_retrieved` | `audit_retrieved` | **PASS** |
| `scenario_01_map_clear_vs_cart_blocked` | `evidence_only` | "Is the front route clear to move forward?" | `verified` | `verified` | **PASS** |
| `scenario_01_map_clear_vs_cart_blocked` | `world_rerun` | "Is the front route clear to move forward?" | `verified` | `verified` | **PASS** |
| `scenario_01_map_clear_vs_cart_blocked` | `world_rerun` | "Why was route_A superseded?" | `audit_retrieved` | `audit_retrieved` | **PASS** |
| `scenario_01_map_clear_vs_cart_blocked` | `world_rerun` | "Is the front route clear to move forward?" | `verified` | `verified` | **PASS** |
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
| `scenario_08_sensor_outage_and_recovery` | `world_rerun` | "Where is parcel_01 now?" | `uncertain` | `uncertain` | **PASS** |
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
- **Expected Outcome:** `{"status": "verified", "location": null, "revisions_count": 1, "active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "blocked"}]}`
- **Actual Outcome:** `{"status": "verified", "answer": "Route is blocked as recorded. The live LiDAR confirms the stored claim.", "revisions_count": 1}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `1 revisions`
- **Passed:** Yes

### 2. Map-clear vs cart-blocked route, followed by clearance (`scenario_01_map_clear_vs_cart_blocked`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Epistemic audit query retrieves revision rationale, predecessor fact, and policy rule.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Why was route_A superseded?"
- **Expected Outcome:** `{"status": "audit_retrieved", "location": null, "revisions_count": 1, "active_facts": []}`
- **Actual Outcome:** `{"status": "audit_retrieved", "answer": "Audit history: route_A status_is changed from clear (source: static_map) to blocked (source: lidar_scan_01). Reason: Front LiDAR at sim_time 0ms observed route_A is blocked (nearest obstacle: 20.0 cm) Policy: lidar_observation_route_status.", "revisions_count": 1}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `1 revisions`
- **Passed:** Yes

### 3. Map-clear vs cart-blocked route, followed by clearance (`scenario_01_map_clear_vs_cart_blocked`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Fresh LiDAR observation confirms obstacle is cleared; route status conflict is resolved to clear with successor belief revision.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Is the front route clear to move forward?"
- **Expected Outcome:** `{"status": "verified", "location": null, "revisions_count": 2, "active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "clear"}]}`
- **Actual Outcome:** `{"status": "verified", "answer": "Yes, the route is clear. The stored route claim and live LiDAR agree.", "revisions_count": 2}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `2 revisions`
- **Passed:** Yes

### 4. Map-clear vs cart-blocked route, followed by clearance (`scenario_01_map_clear_vs_cart_blocked`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Static map indicates route_A is clear, but live front LiDAR detects obstacle at 20.0 cm right now, triggering an atomic belief revision to blocked.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Is the front route clear to move forward?"
- **Expected Outcome:** `{"status": "verified", "location": null, "revisions_count": 1, "active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "blocked"}]}`
- **Actual Outcome:** `{"status": "verified", "answer": "Route is blocked as recorded. The live LiDAR confirms the stored claim.", "revisions_count": 1}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `1 revisions`
- **Passed:** Yes

### 5. Map-clear vs cart-blocked route, followed by clearance (`scenario_01_map_clear_vs_cart_blocked`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Epistemic audit query retrieves revision rationale, predecessor fact, and policy rule.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Why was route_A superseded?"
- **Expected Outcome:** `{"status": "audit_retrieved", "location": null, "revisions_count": 1, "active_facts": []}`
- **Actual Outcome:** `{"status": "audit_retrieved", "answer": "Audit history: route_A status_is changed from clear (source: static_map) to blocked (source: lidar_scan). Reason: Front LiDAR at sim_time 0ms observed route_A is blocked (nearest obstacle: 20.0 cm) Policy: lidar_observation_route_status.", "revisions_count": 1}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `1 revisions`
- **Passed:** Yes

### 6. Map-clear vs cart-blocked route, followed by clearance (`scenario_01_map_clear_vs_cart_blocked`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Fresh LiDAR observation confirms obstacle is cleared; route status conflict is resolved to clear with successor belief revision.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Is the front route clear to move forward?"
- **Expected Outcome:** `{"status": "verified", "location": null, "revisions_count": 2, "active_facts": [{"subject": "route_A", "predicate": "status_is", "object": "clear"}]}`
- **Actual Outcome:** `{"status": "verified", "answer": "Yes, the route is clear. The stored route claim and live LiDAR agree.", "revisions_count": 2}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `2 revisions`
- **Passed:** Yes

### 7. Visible parcel relocation between known stations (`scenario_02_visible_parcel_relocation`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Current belief is updated to inspection_station based on fresh positive sample at 2000 ms.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "verified", "location": "inspection_station", "revisions_count": 1, "active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}]}`
- **Actual Outcome:** `{"status": "verified", "answer": "parcel_01 was observed at x=150 cm, y=390 cm in warehouse by robot_1 at simulation time 2000 ms (sample ID: 20000000-0000-0000-0000-000000000022).", "revisions_count": 1}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `1 revisions`
- **Passed:** Yes

### 8. Visible parcel relocation between known stations (`scenario_02_visible_parcel_relocation`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Movement reported between packing_station and inspection_station with exact timestamps, accompanied by the mandatory transit caveat.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Has parcel_01 moved?"
- **Expected Outcome:** `{"status": "verified", "location": null, "revisions_count": null, "active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}]}`
- **Actual Outcome:** `{"status": "verified", "answer": "Observed transition for parcel_01: sighted at warehouse at 1000 ms (sample ID: 20000000-0000-0000-0000-000000000021), subsequently sighted at warehouse at 2000 ms (sample ID: 20000000-0000-0000-0000-000000000022). Revision record: Observed parcel_01 at (150.0, 390.0) in inspection_station, revising previous location packing_station (Policy: camera_observation_revision).. Note: Two sightings at distinct locations demonstrate that a transition occurred, but do not prove continuous path, exact transit duration, or physical relocation cause.", "revisions_count": 1}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `1 revisions`
- **Passed:** Yes

### 9. Visible parcel relocation between known stations (`scenario_02_visible_parcel_relocation`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Current belief is updated to inspection_station based on fresh positive sample at 2000 ms.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "verified", "location": "inspection_station", "revisions_count": 1, "active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}]}`
- **Actual Outcome:** `{"status": "verified", "answer": "parcel_01 was observed at x=150 cm, y=390 cm in warehouse by robot_1 at simulation time 2000 ms (sample ID: 20000000-0000-0000-0000-000000000022).", "revisions_count": 1}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `1 revisions`
- **Passed:** Yes

### 10. Visible parcel relocation between known stations (`scenario_02_visible_parcel_relocation`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Movement reported between packing_station and inspection_station with exact timestamps, accompanied by the mandatory transit caveat.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Has parcel_01 moved?"
- **Expected Outcome:** `{"status": "verified", "location": null, "revisions_count": null, "active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}]}`
- **Actual Outcome:** `{"status": "verified", "answer": "Observed transition for parcel_01: sighted at warehouse at 1000 ms (sample ID: 20000000-0000-0000-0000-000000000021), subsequently sighted at warehouse at 2000 ms (sample ID: 20000000-0000-0000-0000-000000000022). Revision record: Observed parcel_01 at (150.0, 390.0) in inspection_station, revising previous location packing_station (Policy: camera_observation_revision).. Note: Two sightings at distinct locations demonstrate that a transition occurred, but do not prove continuous path, exact transit duration, or physical relocation cause.", "revisions_count": 1}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `1 revisions`
- **Passed:** Yes

### 11. Parcel moves outside view (`scenario_03_parcel_moves_outside_view`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Broad camera scan did not detect parcel_01. Location cannot be verified now; last confirmed sighting at packing_station is retained without guessing.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "uncertain", "location": "packing_station", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "Current location of parcel_01 cannot be freshly verified because a subsequent camera scan at simulation time 3000 ms did not detect it in warehouse. Last confirmed sighting was at simulation time 1000 ms at x=80 cm, y=350 cm in warehouse by robot_1 (sample ID: 30000000-0000-0000-0000-000000000031).", "revisions_count": 0}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 12. Parcel moves outside view (`scenario_03_parcel_moves_outside_view`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Broad camera scan did not detect parcel_01. Location cannot be verified now; last confirmed sighting at packing_station is retained without guessing.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "uncertain", "location": "packing_station", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "Current location of parcel_01 cannot be freshly verified because a subsequent camera scan at simulation time 3000 ms did not detect it in warehouse. Last confirmed sighting was at simulation time 1000 ms at x=80 cm, y=350 cm in warehouse by robot_1 (sample ID: 30000000-0000-0000-0000-000000000031).", "revisions_count": 0}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 13. Shelf occlusion and later reacquisition (`scenario_04_shelf_occlusion_and_reacquisition`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Targeted camera query reported target_not_visible due to shelf occlusion. Current belief is uncertain, but earlier sighting at packing_station is preserved.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "uncertain", "location": "packing_station", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "Current location of parcel_01 cannot be freshly verified because the camera reported an outage at simulation time 2000 ms (target_not_visible). Last confirmed sighting was at simulation time 1000 ms at x=80 cm, y=350 cm in warehouse by robot_1 (sample ID: 40000000-0000-0000-0000-000000000041).", "revisions_count": 0}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `3 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 14. Shelf occlusion and later reacquisition (`scenario_04_shelf_occlusion_and_reacquisition`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Reacquisition after occlusion restores verified current status.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "verified", "location": "packing_station", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "verified", "answer": "parcel_01 was observed at x=80 cm, y=350 cm in warehouse by robot_1 at simulation time 3000 ms (sample ID: 40000000-0000-0000-0000-000000000043).", "revisions_count": 0}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `3 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 15. Shelf occlusion and later reacquisition (`scenario_04_shelf_occlusion_and_reacquisition`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Targeted camera query reported target_not_visible due to shelf occlusion. Current belief is uncertain, but earlier sighting at packing_station is preserved.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "uncertain", "location": "packing_station", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "Current location of parcel_01 cannot be freshly verified because the camera reported an outage at simulation time 2000 ms (target_not_visible). Last confirmed sighting was at simulation time 1000 ms at x=80 cm, y=350 cm in warehouse by robot_1 (sample ID: 40000000-0000-0000-0000-000000000041).", "revisions_count": 0}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `3 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 16. Shelf occlusion and later reacquisition (`scenario_04_shelf_occlusion_and_reacquisition`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Reacquisition after occlusion restores verified current status.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "verified", "location": "packing_station", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "verified", "answer": "parcel_01 was observed at x=80 cm, y=350 cm in warehouse by robot_1 at simulation time 3000 ms (sample ID: 40000000-0000-0000-0000-000000000043).", "revisions_count": 0}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `3 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 17. Two similar parcels / ambiguous mention (`scenario_05_two_similar_parcels`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Query ambiguously references 'the box' when multiple distinct objects (box_01, box_02) match. The agent halts and refuses to merge identities.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "What is the color of the box?"
- **Expected Outcome:** `{"status": "ambiguous", "location": null, "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "ambiguous", "answer": "Ambiguous entity mention in query. Candidates found: [box_01, box_02]. Please clarify which entity you mean.", "revisions_count": 0}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `1 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 18. Two similar parcels / ambiguous mention (`scenario_05_two_similar_parcels`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Query ambiguously references 'the box' when multiple distinct objects (box_01, box_02) match. The agent halts and refuses to merge identities.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "What is the color of the box?"
- **Expected Outcome:** `{"status": "ambiguous", "location": null, "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "ambiguous", "answer": "Ambiguous entity mention in query. Candidates found: [box_01, box_02]. Please clarify which entity you mean.", "revisions_count": 0}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `1 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 19. User expectation vs lighting perception vs maintenance history (`scenario_06_color_perspectives`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Distinguishes user expectation (red), live egocentric perception under yellow lighting (brown), and maintenance history (blue) without conflating them.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Can you see the red box? What color do different perspectives think it is?"
- **Expected Outcome:** `{"status": "perspectives_tracked", "location": null, "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "perspectives_tracked", "answer": "The available evidence gives three distinct perspectives:\n\n- **Your expectation:** Red (user expects a red target, as stated in the question).\n- **Current camera view:** Brown (currently sensed by the camera).\n- **Historical record:** Blue (latest logged color claim from 'bot_02', recorded 2026-11-22T14:13:44.000971+00:00).\n\nThe historical record is a past claim; it does not by itself establish the object's current color.", "revisions_count": 0}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `1 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 20. User expectation vs lighting perception vs maintenance history (`scenario_06_color_perspectives`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Distinguishes user expectation (red), live egocentric perception under yellow lighting (brown), and maintenance history (blue) without conflating them.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Can you see the red box? What color do different perspectives think it is?"
- **Expected Outcome:** `{"status": "perspectives_tracked", "location": null, "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "perspectives_tracked", "answer": "The available evidence gives three distinct perspectives:\n\n- **Your expectation:** Red (user expects a red target, as stated in the question).\n- **Current camera view:** Brown (currently sensed by the camera under yellow lighting).\n- **Historical record:** Blue (latest logged color claim from 'bot_02', recorded 2026-11-22T14:13:44.009116+00:00).\n\nThe historical record is a past claim; it does not by itself establish the object's current color.", "revisions_count": 0}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `1 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 21. Wrong-room/frame sample rejected (`scenario_07_wrong_context_sample`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Observation captured in mismatched room_999 cannot update warehouse beliefs. Stored warehouse belief at packing_station is preserved without corruption.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "What was recorded about parcel_01?"
- **Expected Outcome:** `{"status": "verified", "location": "packing_station", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "verified", "answer": "Retrieved stored facts from memory: parcel_01 located_at packing_station (source: warehouse_manifest).", "revisions_count": 0}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `1 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 22. Wrong-room/frame sample rejected (`scenario_07_wrong_context_sample`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Observation captured in mismatched room_999 cannot update warehouse beliefs. Stored warehouse belief at packing_station is preserved without corruption.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "What was recorded about parcel_01?"
- **Expected Outcome:** `{"status": "verified", "location": "packing_station", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "verified", "answer": "Retrieved stored facts from memory: parcel_01 located_at packing_station (source: warehouse_manifest).", "revisions_count": 0}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `1 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 23. Sensor outage and recovery (`scenario_08_sensor_outage_and_recovery`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Current camera verification is unavailable due to outage at 2000 ms. Agent reports uncertain status while retaining last confirmed sighting at 1000 ms.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "uncertain", "location": "packing_station", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "Current location of parcel_01 cannot be freshly verified because the camera reported an outage at simulation time 2000 ms (sensor_unavailable). Last confirmed sighting was at simulation time 1000 ms at x=80 cm, y=350 cm in warehouse by robot_1 (sample ID: 80000000-0000-0000-0000-000000000081).", "revisions_count": 0}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 24. Sensor outage and recovery (`scenario_08_sensor_outage_and_recovery`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Current camera verification is unavailable due to outage at 2000 ms. Agent reports uncertain status while retaining last confirmed sighting at 1000 ms.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "uncertain", "location": "packing_station", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "uncertain", "answer": "Current location of parcel_01 cannot be freshly verified because the camera reported an outage at simulation time 2000 ms (sensor_unavailable). Last confirmed sighting was at simulation time 1000 ms at x=80 cm, y=350 cm in warehouse by robot_1 (sample ID: 80000000-0000-0000-0000-000000000081).", "revisions_count": 0}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 25. Delayed older observation (`scenario_09_delayed_older_observation`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Delayed older observation from t=500 ms does not overwrite newer active belief from t=2000 ms. Active belief remains verified at inspection_station.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "verified", "location": "inspection_station", "revisions_count": null, "active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}]}`
- **Actual Outcome:** `{"status": "verified", "answer": "parcel_01 was observed at x=150 cm, y=390 cm in warehouse by robot_1 at simulation time 2000 ms (sample ID: 90000000-0000-0000-0000-000000000091).", "revisions_count": 0}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 26. Delayed older observation (`scenario_09_delayed_older_observation`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Delayed older observation from t=500 ms does not overwrite newer active belief from t=2000 ms. Active belief remains verified at inspection_station.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Where is parcel_01 now?"
- **Expected Outcome:** `{"status": "verified", "location": "inspection_station", "revisions_count": null, "active_facts": [{"subject": "parcel_01", "predicate": "located_at", "object": "inspection_station"}]}`
- **Actual Outcome:** `{"status": "verified", "answer": "parcel_01 was observed at x=150 cm, y=390 cm in warehouse by robot_1 at simulation time 2000 ms (sample ID: 90000000-0000-0000-0000-000000000091).", "revisions_count": 0}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `0 revisions`
- **Passed:** Yes

### 27. Movement between samples with strict anti-interpolation (`scenario_10_hidden_movement_between_scans`)
- **Mode:** `evidence_only`
- **Conflict / Uncertainty Rationale:** Movement occurred wholly between scans. Agent adheres to strict anti-interpolation policy: reports latest applicable observation at t=0 ms and explicitly refuses to interpolate an intermediate physical trajectory.
- **Setup:** Evidence-only isolated replay over serialized bundle samples.
- **Prompt:** "Where was parcel_01 at 500 ms?"
- **Expected Outcome:** `{"status": "verified", "location": "warehouse", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 500 ms, the latest applicable observation of parcel_01 was captured at 0 ms (age: 500 ms) at x=80 cm, y=350 cm in warehouse by robot_1 (sample ID: a0000000-0000-0000-0000-0000000000a1). Note: No intermediate position is interpolated at 500 ms.", "revisions_count": 1}`
- **Policy Applied:** `temporal_evidence_evaluation`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `1 revisions`
- **Passed:** Yes

### 28. Movement between samples with strict anti-interpolation (`scenario_10_hidden_movement_between_scans`)
- **Mode:** `world_rerun`
- **Conflict / Uncertainty Rationale:** Movement occurred wholly between scans. Agent adheres to strict anti-interpolation policy: reports latest applicable observation at t=0 ms and explicitly refuses to interpolate an intermediate physical trajectory.
- **Setup:** SimulationEngine advanced with fixed ticks; real WarehouseSensors sampled and ingested.
- **Prompt:** "Where was parcel_01 at 500 ms?"
- **Expected Outcome:** `{"status": "verified", "location": "warehouse", "revisions_count": null, "active_facts": []}`
- **Actual Outcome:** `{"status": "verified", "answer": "At simulation time 500 ms, the latest applicable observation of parcel_01 was captured at 0 ms (age: 500 ms) at x=80 cm, y=350 cm in warehouse by robot_1 (sample ID: a0000000-0000-0000-0000-0000000000a1). Note: No intermediate position is interpolated at 500 ms.", "revisions_count": 1}`
- **Policy Applied:** `simulation_world_rerun`
- **Sample IDs Ingested:** `2 samples`
- **Revisions Recorded:** `1 revisions`
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
