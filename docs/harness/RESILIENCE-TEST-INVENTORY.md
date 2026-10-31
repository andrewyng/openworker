# Executed resilience test inventory

Generated from the successful October 30, 2026 local qualification. See the
[reviewer guide](RESILIENCE-TEST-CASES.md) for what each section establishes and why.
These are exact executed test names, not a count of unique behaviors: the Linux
rerun deliberately overlaps the native fast level. All entries below passed.

## runtime-unit

<details>
<summary>72 passing cases</summary>

- `budget::tests::reserve_summary_inside_total_and_charge_unknown`
- `coding::web::tests::extracts_without_active_content`
- `chat_completions::tests::truncation_duplicate_calls_and_cross_provider_history_fail_closed`
- `chat_completions::tests::accounting_tail_does_not_replace_done_or_allow_later_events`
- `budget::tests::override_step_stop_and_persist`
- `coding::web::tests::rejects_private_and_credential_urls`
- `platform::runner_tests::runner_validation_uses_execution_location`
- `chat_completions::tests::accounting_tail_rejects_choice_level_errors`
- `chat_completions::tests::accounting_tail_rejects_conflicts_and_new_content`
- `model_api::tests::validates_endpoints_without_echoing_secrets`
- `chat_completions::tests::roundtrip_tools_and_images`
- `chat_completions::tests::openai_empty_choices_usage_tail_remains_supported`
- `chat_completions::tests::openrouter_usage_tail_preserves_text_and_accounting`
- `chat_completions::tests::openrouter_usage_tail_preserves_fragmented_tool_call_once`
- `chat_completions::tests::fragmented_call_requires_complete_stream`
- `provider_auth::tests::refreshes_before_expiry_and_rejects_too_short_callback_token`
- `provider_auth::tests::expiry_and_refresh_failure_fail_closed_without_secret_diagnostics`
- `environment_openshell::tests::provider_identity_and_immutable_image_are_pinned`
- `environment_openshell::tests::resource_limits_are_in_profile_identity_and_verified_on_attach`
- `environment_openshell::tests::altered_authority_and_unready_policy_are_rejected`
- `provider_auth::tests::proactive_rotation_wins_over_pending_refresh`
- `runtime::budget_tests::resilience_stream_barrier_is_cancellable_before_any_completion`
- `provider_auth::tests::persisted_auth_mode_never_falls_back_to_anonymous_after_restart`
- `provider_auth::tests::single_flight_initial_and_rejected_refresh`
- `runtime::budget_tests::submission_id_binds_the_limit_override`
- `runtime::budget_tests::resilience_incomplete_stream_never_commits_tool_and_keeps_budget`
- `runtime::m2_tests::cancellation_drops_native_summarizer_without_late_commit`
- `runtime::budget_tests::incomplete_usage_cannot_refund_a_reserved_output_allowance`
- `runtime::m2_tests::compaction_event_failure_rolls_back_checkpoint`
- `runtime::budget_tests::output_exhaustion_reserves_a_summary_within_the_total`
- `runtime::budget_tests::step_exhaustion_rejects_queued_effect_and_gets_one_summary`
- `runtime::m2_tests::compaction_is_outbound_only_and_survives_restart`
- `runtime::budget_tests::exhausted_compaction_finishes_instead_of_parking_another_question`
- `runtime::m2_tests::completed_turn_cleanup_cannot_accept_and_strand_the_next_submission`
- `runtime::m2_tests::compaction_retry_and_pending_approval_survive_the_same_restart_scenario`
- `runtime::m2_tests::output_limit_continuation_is_bounded_and_not_a_user_instruction`
- `runtime::m2_tests::retry_backoff_is_cancellable_without_another_request`
- `runtime::m2_tests::safety_block_is_durable_and_never_retried`
- `runtime::m2_tests::repeated_transient_failure_stops_after_three_attempts`
- `runtime::m2_tests::stale_summary_cannot_replace_current_history_view`
- `runtime::planning_tests::failed_event_commit_rolls_back_plan_result_and_queue_and_emits_nothing`
- `runtime::planning_tests::validation_covers_bounds_status_ids_and_stale_revision`
- `runtime::planning_tests::stale_update_returns_tool_error_without_mutating_plan_or_stopping_turn`
- `runtime::planning_tests::cancellation_stale_queue_and_disabled_coworker_cannot_update`
- `runtime::budget_tests::invalid_arguments_are_corrected_before_any_execution`
- `runtime::m2_tests::summary_failures_retry_once_then_park_or_trim`
- `runtime::policy_tests::application_tool_policy_cannot_answer_a_user_question`
- `runtime::planning_tests::compaction_restart_replay_and_later_turn_preserve_plan_identity`
- `runtime::policy_tests::cancelled_review_cannot_publish_a_decision`
- `runtime::budget_tests::repeated_invalid_arguments_stop_with_summary`
- `runtime::policy_tests::managed_agent_without_reviewer_does_not_reuse_agent_model`
- `runtime::policy_tests::queued_model_readiness_requires_the_pinned_credential_without_calling_provider`
- `runtime::policy_tests::reviewer_context_excludes_agent_history_and_skipped_answers`
- `runtime::policy_tests::revocation_after_tool_start_is_not_rollback`
- `runtime::planning_tests::drive_updates_plan_without_approval_but_actual_actions_still_ask`
- `runtime::policy_tests::revoked_grant_cannot_cross_execution_checkpoint_or_reuse_old_approval_id`
- `runtime::skills_tests::session_selection_is_durable_scoped_and_cannot_enable_unlisted_skills`
- `sandbox::wire_tests::v1_exec_has_explicit_named_workspace_and_no_login_shell`
- `tool_images::tests::pixels_are_ephemeral_scoped_and_latest_only`
- `runtime::skills_tests::compaction_and_restart_keep_skill_identity_and_allow_explicit_reload`
- `runtime::m2_tests::retries_keep_partial_attempts_separate_and_do_not_touch_tools_or_history`
- `runtime::m2_tests::segment_checkpoint_preserves_history_and_rolls_back_failed_event`
- `runtime::policy_tests::session_grant_skips_reviewer_but_not_hard_checks_or_policy_identity`
- `runtime::policy_tests::hard_path_denial_precedes_all_modes_and_reviewer`
- `runtime::policy_tests::modes_are_independent_of_question_skipping_and_human_presence`
- `runtime::budget_tests::rejected_calls_and_budget_stops_export_content_free_events`
- `runtime::skills_tests::skill_loading_obeys_modes_and_bodies_do_not_reach_reviewer`
- `runtime::policy_tests::question_skip_is_durable_and_is_never_a_human_answer`
- `runtime::policy_tests::reviewer_verdict_and_failure_matrix`
- `runtime::budget_tests::a_turn_can_finish_after_more_than_twenty_model_calls`
- `runtime::m2_tests::image_consent_is_rechecked_on_each_provider_retry_without_journaling_pixels`
- `runtime::m2_tests::live_tool_access_reaches_model_and_refreshes_on_retry_without_entering_history`

</details>

## runtime-contracts

<details>
<summary>23 passing cases</summary>

- `policy_is_fail_closed`
- `native_mcp_file_tools`
- `bundle_identity_and_exact_routing`
- `tool_path_rejects_escape_and_symlinks`
- `exclusive_state_lock_and_cursor_validation`
- `submissions_are_idempotent_and_conflicts_rejected`
- `history_pages_advance_across_private_items_without_disclosing_them`
- `cancelled_compaction_answer_cannot_carry_into_a_new_turn`
- `schema_and_call_validation`
- `old_submission_returns_original_turn_without_changing_active_turn`
- `schema_checks_nested_constraints_and_refuses_external_references`
- `transcript_excerpts_are_scoped_bounded_and_preserve_unicode`
- `incompatible_stores_are_rejected_without_changes`
- `resilience_recovery_distinguishes_unstarted_and_already_completed_calls`
- `resilience_recovery_preserves_budget_cursor_and_reports_unknown_exactly_once`
- `replay_pages_bound_bytes_and_keep_cursor_continuity`
- `recovery_preserves_pending_identity_but_never_replays_executing`
- `history_is_append_only_and_streaming_does_not_rewrite_sessions`
- `authentication_refresh_records_both_http_attempts_and_missing_usage_is_unknown`
- `anthropic_partial_usage_survives_truncation_without_authorizing_output`
- `all_real_model_purposes_keep_usage_once_and_share_turn`
- `responses_failed_request_keeps_reported_cost_and_tool_first_output`
- `cancellation_keeps_partial_usage_and_interrupted_outcome`

</details>

## recording-and-export

<details>
<summary>36 passing cases</summary>

- `union_is_not_accumulation_known_zero_is_not_missing_and_gaps_are_explicit`
- `unrelated_policy_work_is_parallel_and_missing_or_cyclic_parents_are_explicit`
- `truncation_orphan_requests_clock_anomalies_and_overflow_never_fake_completeness`
- `groups_are_bounded_but_overall_usage_still_counts_the_whole_scanned_page`
- `summaries_reconcile_retries_usage_containers_parallel_time_and_waits`
- `corrupted_row_cannot_return_another_sessions_payload`
- `sqlite_scoped_pages_watermark_retention_and_validation`
- `projection_enforces_its_own_row_budget_and_preserves_zero_duration`
- `postgres_connection_validation`
- `validation_rejects_content_overflow_inconsistent_usage_and_unknown_fields`
- `sqlite_rejects_symlink_file_without_modifying_its_target`
- `unsupported_sqlite_schema_is_not_reset`
- `sqlite_contract_restart_and_retention_isolation`
- `typed_purpose_and_reported_breakdowns_survive_conversion_without_inference`
- `real_instrumentation_reopens_metadata_only_and_keeps_export_off`
- `local_commit_does_not_wait_for_a_stalled_vendor_exporter`
- `blocked_writer_has_bounded_queue_and_shutdown_without_blocking_producer`
- `invalid_scope_is_rejected_before_queue`
- `otlp::tests::acknowledgements_are_typed_bounded_and_never_echo_receiver_text`
- `shared_registry_has_unique_typed_privacy_and_dimension_contracts`
- `content_off_generations_have_usage_identity_and_links_without_names_or_parent_rollups`
- `observation_snapshot_is_enqueued_or_dropped_as_one_unit`
- `disabled_and_abandoned_spans_are_truthful`
- `durable_turn_identity_links_segments_without_counting_pause_as_execution`
- `hierarchy_is_task_local_and_only_hashes_cross_the_boundary`
- `shared_registry_bounds_names_and_content_and_reserves_typed_fields`
- `full_queue_and_blocked_exporter_never_block_producers_or_shutdown`
- `deployment_configuration_is_backward_compatible_and_bounded`
- `redaction_masks_common_credentials`
- `content_off_by_default_and_header_rules_enforced`
- `authenticated_base_path_traces_only_with_opt_in_content`
- `ambient_headers_resources_and_proxy_are_not_inherited`
- `all_signals_have_valid_otlp_and_no_payload_or_metric_identifiers`
- `rejection_redirect_partial_malformed_and_large_acknowledgements_are_not_success_or_retried`
- `collector_outage_and_bad_endpoints`
- `slow_collector_cannot_extend_shutdown_budget`

</details>

## postgres-submissions

<details>
<summary>17 passing cases</summary>

- `concurrent_creation_is_once_unknown_is_not_permission_to_recreate`
- `delete_racing_session_creation_never_deletes_a_successfully_bound_live_session`
- `exclusive_and_app_owned_environments_are_not_silently_shared_or_destroyed`
- `resilience_recovered_runtime_shutdown_retires_old_attachments_without_deleting_shared_environment`
- `shared_sessions_and_pending_connections_block_delete_until_explicit_release`
- `takeover_keeps_binding_and_fences_old_attachment_writes`
- `competing_payloads_and_capacity_are_serialized_and_session_scoped`
- `concurrent_api_nodes_create_one_receipt_and_one_turn`
- `deleted_receipts_do_not_turn_a_duplicate_into_new_work`
- `fifo_capacity_and_interleaved_ingress_do_not_invalidate_worker_state`
- `lost_receipts_reconnect_and_takeover_preserve_queue_without_replay`
- `receipt_event_audit_and_queue_position_roll_back_together`
- `resilience_database_actor_recovery_events_keep_the_explicit_turn`
- `resilience_service_result_preserves_interruption_for_current_and_older_runs`
- `turn_limits_survive_queue_and_bind_idempotency`
- `twelve_producers_respect_queue_bound_and_retries_remain_available_when_full`
- `validation_lifecycle_and_old_schema_are_explicit`

</details>

## postgres-recording-outage

<details>
<summary>1 passing cases</summary>

- `independent_recording_targets_outage_and_actual_service_migration`

</details>

## postgres-authorized-recording

<details>
<summary>1 passing cases</summary>

- `authorized_search_timeline_summaries_revocation_and_separate_database_outage`

</details>

## Linux runtime rerun (77 unit + 18 core; overlaps the native fast level)

<details>
<summary>95 passing cases</summary>

- `budget::tests::override_step_stop_and_persist`
- `budget::tests::reserve_summary_inside_total_and_charge_unknown`
- `chat_completions::tests::accounting_tail_does_not_replace_done_or_allow_later_events`
- `chat_completions::tests::accounting_tail_rejects_choice_level_errors`
- `chat_completions::tests::accounting_tail_rejects_conflicts_and_new_content`
- `chat_completions::tests::openai_empty_choices_usage_tail_remains_supported`
- `chat_completions::tests::fragmented_call_requires_complete_stream`
- `chat_completions::tests::openrouter_usage_tail_preserves_fragmented_tool_call_once`
- `chat_completions::tests::roundtrip_tools_and_images`
- `chat_completions::tests::openrouter_usage_tail_preserves_text_and_accounting`
- `chat_completions::tests::truncation_duplicate_calls_and_cross_provider_history_fail_closed`
- `coding::processes::resilience_tests::resilience_deadline_kills_descendants_without_a_status_request`
- `coding::processes::resilience_tests::resilience_manager_reaps_owned_children_when_group_signals_are_denied`
- `coding::processes::resilience_tests::resilience_stopping_one_workspace_does_not_stop_the_other`
- `coding::web::tests::extracts_without_active_content`
- `coding::web::tests::rejects_private_and_credential_urls`
- `direct::linux::token_check_distinguishes_exit_race_from_live_ownership_failure`
- `environment_openshell::tests::altered_authority_and_unready_policy_are_rejected`
- `environment_openshell::tests::provider_identity_and_immutable_image_are_pinned`
- `environment_openshell::tests::resource_limits_are_in_profile_identity_and_verified_on_attach`
- `model_api::tests::validates_endpoints_without_echoing_secrets`
- `platform::runner_tests::runner_validation_uses_execution_location`
- `provider_auth::tests::expiry_and_refresh_failure_fail_closed_without_secret_diagnostics`
- `provider_auth::tests::persisted_auth_mode_never_falls_back_to_anonymous_after_restart`
- `provider_auth::tests::proactive_rotation_wins_over_pending_refresh`
- `provider_auth::tests::refreshes_before_expiry_and_rejects_too_short_callback_token`
- `provider_auth::tests::single_flight_initial_and_rejected_refresh`
- `coding::processes::resilience_tests::resilience_shutdown_stops_group_and_retains_bounded_logs`
- `runtime::budget_tests::exhausted_compaction_finishes_instead_of_parking_another_question`
- `runtime::budget_tests::incomplete_usage_cannot_refund_a_reserved_output_allowance`
- `runtime::budget_tests::invalid_arguments_are_corrected_before_any_execution`
- `runtime::budget_tests::output_exhaustion_reserves_a_summary_within_the_total`
- `runtime::budget_tests::a_turn_can_finish_after_more_than_twenty_model_calls`
- `runtime::budget_tests::repeated_invalid_arguments_stop_with_summary`
- `runtime::budget_tests::resilience_incomplete_stream_never_commits_tool_and_keeps_budget`
- `runtime::budget_tests::resilience_stream_barrier_is_cancellable_before_any_completion`
- `runtime::budget_tests::rejected_calls_and_budget_stops_export_content_free_events`
- `runtime::budget_tests::step_exhaustion_rejects_queued_effect_and_gets_one_summary`
- `runtime::budget_tests::submission_id_binds_the_limit_override`
- `runtime::m2_tests::cancellation_drops_native_summarizer_without_late_commit`
- `runtime::m2_tests::compaction_event_failure_rolls_back_checkpoint`
- `runtime::m2_tests::compaction_is_outbound_only_and_survives_restart`
- `runtime::m2_tests::completed_turn_cleanup_cannot_accept_and_strand_the_next_submission`
- `runtime::m2_tests::compaction_retry_and_pending_approval_survive_the_same_restart_scenario`
- `runtime::m2_tests::live_tool_access_reaches_model_and_refreshes_on_retry_without_entering_history`
- `runtime::m2_tests::output_limit_continuation_is_bounded_and_not_a_user_instruction`
- `runtime::m2_tests::repeated_transient_failure_stops_after_three_attempts`
- `runtime::m2_tests::image_consent_is_rechecked_on_each_provider_retry_without_journaling_pixels`
- `runtime::m2_tests::retry_backoff_is_cancellable_without_another_request`
- `runtime::m2_tests::safety_block_is_durable_and_never_retried`
- `runtime::m2_tests::retries_keep_partial_attempts_separate_and_do_not_touch_tools_or_history`
- `runtime::m2_tests::stale_summary_cannot_replace_current_history_view`
- `runtime::m2_tests::segment_checkpoint_preserves_history_and_rolls_back_failed_event`
- `runtime::planning_tests::cancellation_stale_queue_and_disabled_coworker_cannot_update`
- `runtime::m2_tests::summary_failures_retry_once_then_park_or_trim`
- `runtime::planning_tests::compaction_restart_replay_and_later_turn_preserve_plan_identity`
- `runtime::planning_tests::failed_event_commit_rolls_back_plan_result_and_queue_and_emits_nothing`
- `runtime::planning_tests::drive_updates_plan_without_approval_but_actual_actions_still_ask`
- `runtime::planning_tests::validation_covers_bounds_status_ids_and_stale_revision`
- `runtime::planning_tests::stale_update_returns_tool_error_without_mutating_plan_or_stopping_turn`
- `runtime::policy_tests::application_tool_policy_cannot_answer_a_user_question`
- `runtime::policy_tests::cancelled_review_cannot_publish_a_decision`
- `runtime::policy_tests::managed_agent_without_reviewer_does_not_reuse_agent_model`
- `runtime::policy_tests::hard_path_denial_precedes_all_modes_and_reviewer`
- `runtime::policy_tests::modes_are_independent_of_question_skipping_and_human_presence`
- `runtime::policy_tests::queued_model_readiness_requires_the_pinned_credential_without_calling_provider`
- `runtime::policy_tests::reviewer_context_excludes_agent_history_and_skipped_answers`
- `runtime::policy_tests::question_skip_is_durable_and_is_never_a_human_answer`
- `runtime::policy_tests::revocation_after_tool_start_is_not_rollback`
- `runtime::policy_tests::revoked_grant_cannot_cross_execution_checkpoint_or_reuse_old_approval_id`
- `runtime::policy_tests::reviewer_verdict_and_failure_matrix`
- `runtime::policy_tests::session_grant_skips_reviewer_but_not_hard_checks_or_policy_identity`
- `runtime::skills_tests::compaction_and_restart_keep_skill_identity_and_allow_explicit_reload`
- `runtime::skills_tests::session_selection_is_durable_scoped_and_cannot_enable_unlisted_skills`
- `sandbox::wire_tests::v1_exec_has_explicit_named_workspace_and_no_login_shell`
- `tool_images::tests::pixels_are_ephemeral_scoped_and_latest_only`
- `runtime::skills_tests::skill_loading_obeys_modes_and_bodies_do_not_reach_reviewer`
- `bundle_identity_and_exact_routing`
- `exclusive_state_lock_and_cursor_validation`
- `cancelled_compaction_answer_cannot_carry_into_a_new_turn`
- `history_pages_advance_across_private_items_without_disclosing_them`
- `incompatible_stores_are_rejected_without_changes`
- `native_mcp_file_tools`
- `old_submission_returns_original_turn_without_changing_active_turn`
- `policy_is_fail_closed`
- `history_is_append_only_and_streaming_does_not_rewrite_sessions`
- `recovery_preserves_pending_identity_but_never_replays_executing`
- `replay_pages_bound_bytes_and_keep_cursor_continuity`
- `resilience_recovery_preserves_budget_cursor_and_reports_unknown_exactly_once`
- `resilience_recovery_distinguishes_unstarted_and_already_completed_calls`
- `schema_and_call_validation`
- `schema_checks_nested_constraints_and_refuses_external_references`
- `tool_path_rejects_escape_and_symlinks`
- `submissions_are_idempotent_and_conflicts_rejected`
- `transcript_excerpts_are_scoped_bounded_and_preserve_unicode`

</details>

## Isolated PostgreSQL production-worker faults

<details>
<summary>15 passing cases</summary>

- `api_accepts_while_worker_is_dead_and_replacement_consumes_fifo`
- `concurrency::eight_sessions_four_slots_database_loss_is_scoped_and_not_replayed`
- `concurrency::eight_sessions_four_slots_process_death_preserves_fifo_and_siblings`
- `concurrency::parked_approvals_do_not_consume_slots_or_share_session_grants`
- `draining::database_loss_during_drain_interrupts_without_waiting_for_drain_deadline`
- `draining::drain_deadline_interrupts_without_replaying_an_uncertain_effect`
- `draining::drain_finishes_current_turn_renews_lease_and_leaves_followups_for_replacement`
- `draining::draining_preserves_approval_and_defers_resume_but_honors_active_cancellation`
- `production_worker_loses_database_and_replacement_recovers_unknown_once`
- `production_worker_parks_approval_reclaims_for_answer_and_keeps_session_grant`
- `production_worker_plans_survive_replacement_and_cross_api_approval`
- `production_workers_bound_capacity_renew_and_shutdown_without_replaying_effects`
- `real_worker_deaths_preserve_approval_then_deliver_unknown_without_replaying_write`
- `remote_controls_survive_crash_grant_session_access_and_cancel_unknown_effect`
- `resilience_streaming_worker_death_preserves_budget_and_cursor_without_tool_replay`

</details>

## JavaScript safety, cleanup and management presentation

<details>
<summary>9 passing cases</summary>

- `Docker absence accepts CLI casing, but daemon/permission failures are not deletion`
- `fault injection rejects foreign, live, remote and unconfirmed deployments`
- `reconnect oracle detects cursor duplicates, reordering and lost-outcome replays`
- `cleanup accepts API pods that already disappeared without kubectl wait on an empty selector`
- `cleanup waits for terminating API pods and does not treat API failure as deletion`
- `cleanup stops at its deadline while an API pod remains`
- `recording proxy accepts encoded literal search and exact bounded read routes only`
- `presentation keeps missing, zero and partial counters distinct`
- `timeline retains parallel intervals and unknown duration markers`

</details>

## Kubernetes and OpenShell scenario groups

<details>
<summary>9 passing cases</summary>

- `two sessions share environment with separate files, handles and ports`
- `worker killed during streaming: budgets and cursor survive, no partial tool accepted`
- `worker killed after real tool effect: unknown outcome persisted once, explicit read-back, no replay`
- `cross-API cancellation stops turn and preserves environment-owned servers`
- `deadline kills descendant without status polling; stopping coder leaves verifier healthy`
- `budget exhaustion refuses queued file effect and records the stop`
- `management summaries agree with timeline and actual OTLP collector by span/trace; content redacted`
- `browser finds exact run and renders grouped usage and timeline without page errors`
- `environment deletion removes remaining server and original pod; other sandboxes retained`

</details>

