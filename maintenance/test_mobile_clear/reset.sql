-- One-time operator-authorized mobile reset. No application imports or sends.
-- Values are supplied through psql quoted variables, never string interpolation.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout = '20s';
SET LOCAL lock_timeout = '5s';
SELECT set_config('btx.reset_uid', :'target_uid', true) AS configured_uid,
       set_config('btx.reset_username', :'target_username', true) AS configured_username,
       set_config('btx.reset_actor', :'actor_id', true) AS configured_actor,
       set_config('btx.reset_mode', :'reset_mode', true) AS configured_mode
\gset
DO $reset$
DECLARE
    target BIGINT := current_setting('btx.reset_uid')::BIGINT;
    expected_name TEXT := lower(current_setting('btx.reset_username'));
    actor BIGINT := current_setting('btx.reset_actor')::BIGINT;
    mode TEXT := current_setting('btx.reset_mode');
    action_key CONSTANT TEXT := 'test_mobile_clear_20260922_user_request_v1';
    pinned BIGINT;
    matches INTEGER;
    matched BIGINT;
    profile_count INTEGER := 0;
    verification_count INTEGER := 0;
    pilot_count INTEGER := 0;
    history_before JSONB;
    history_after JSONB;
BEGIN
    IF target <= 0 OR actor <= 0 OR expected_name <> 'mohit_97saxena' OR mode NOT IN ('preview','apply') THEN
        RAISE EXCEPTION 'Invalid reset scope';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('betroxy:' || action_key, 0));
    SELECT telegram_user_id INTO pinned FROM public.btx_crm_test_identity WHERE slot=1;
    IF pinned IS DISTINCT FROM target THEN
        RAISE EXCEPTION 'Requested identity does not match the pinned BETROXY test account';
    END IF;
    SELECT count(*), min(uid) INTO matches, matched FROM (
        SELECT telegram_user_id AS uid FROM public.referrals
        WHERE lower(ltrim(telegram_username, '@'))=expected_name
        UNION
        SELECT telegram_user_id AS uid FROM public.intelligence_leads
        WHERE lower(ltrim(telegram_username, '@'))=expected_name
    ) AS identities;
    IF matches <> 1 OR matched IS DISTINCT FROM target THEN
        RAISE EXCEPTION 'Test username lookup absent, ambiguous, or mismatched';
    END IF;
    IF EXISTS (SELECT 1 FROM public.btx_crm_audit WHERE telegram_user_id=target AND action=action_key) THEN
        PERFORM set_config('btx.reset_outcome','already_applied_no_repeat',false);
        RETURN;
    END IF;
    IF mode='preview' THEN
        PERFORM set_config('btx.reset_outcome','identity_verified_preview_no_changes',false);
        RETURN;
    END IF;

    -- Lock only this test identity's active contact/verification records.
    PERFORM 1 FROM public.user_contact_profiles WHERE telegram_user_id=target FOR UPDATE;
    PERFORM 1 FROM public.v110_mobile_verifications WHERE telegram_user_id=target FOR UPDATE;
    PERFORM 1 FROM public.btx_crm_verification_tests WHERE telegram_user_id=target FOR UPDATE;
    SELECT jsonb_build_object(
        'referrals',(SELECT count(*) FROM public.referrals WHERE telegram_user_id=target),
        'daily_entries',(SELECT count(*) FROM public.v110_quiz_entries WHERE telegram_user_id=target),
        'weekly_entries',(SELECT count(*) FROM public.mega_quiz_entries WHERE telegram_user_id=target),
        'rewards',(SELECT count(*) FROM public.reward_awards WHERE telegram_user_id=target)
    ) INTO history_before;

    -- Keep the profile and historical consent timestamp; clear active phone only.
    UPDATE public.user_contact_profiles
       SET mobile_number=NULL, mobile_source=NULL, mobile_prompted_at=NULL,
           mobile_removed_at=NOW(), updated_at=NOW()
     WHERE telegram_user_id=target;
    GET DIAGNOSTICS profile_count=ROW_COUNT;
    DELETE FROM public.v110_mobile_verifications WHERE telegram_user_id=target;
    GET DIAGNOSTICS verification_count=ROW_COUNT;
    DELETE FROM public.btx_crm_verification_tests WHERE telegram_user_id=target;
    GET DIAGNOSTICS pilot_count=ROW_COUNT;
    IF profile_count>1 OR verification_count>1 OR pilot_count>1 THEN
        RAISE EXCEPTION 'Unexpected record count; rolling back';
    END IF;
    IF EXISTS (SELECT 1 FROM public.user_contact_profiles WHERE telegram_user_id=target AND NULLIF(trim(mobile_number),'') IS NOT NULL)
       OR EXISTS (SELECT 1 FROM public.v110_mobile_verifications WHERE telegram_user_id=target)
       OR EXISTS (SELECT 1 FROM public.btx_crm_verification_tests WHERE telegram_user_id=target) THEN
        RAISE EXCEPTION 'Reset verification failed; rolling back';
    END IF;
    SELECT jsonb_build_object(
        'referrals',(SELECT count(*) FROM public.referrals WHERE telegram_user_id=target),
        'daily_entries',(SELECT count(*) FROM public.v110_quiz_entries WHERE telegram_user_id=target),
        'weekly_entries',(SELECT count(*) FROM public.mega_quiz_entries WHERE telegram_user_id=target),
        'rewards',(SELECT count(*) FROM public.reward_awards WHERE telegram_user_id=target)
    ) INTO history_after;
    IF history_before IS DISTINCT FROM history_after THEN
        RAISE EXCEPTION 'History changed concurrently; rolling back reset';
    END IF;
    INSERT INTO public.btx_crm_audit(telegram_user_id,actor_user_id,action,detail)
    VALUES(target,actor,action_key,jsonb_build_object(
        'requested_for_testing',true,'profile_rows_cleared',profile_count,
        'verification_rows_cleared',verification_count,'pilot_rows_cleared',pilot_count,
        'history_counts',history_after,'consents_preserved',true,
        'other_users_modified',false,'phone_values_logged',false
    )::TEXT);
    PERFORM set_config('btx.reset_outcome','applied_and_committed',false);
END
$reset$;
COMMIT;
-- Post-commit readback: no mobile numbers, credentials or voucher values.
SELECT 'BTX_TEST_MOBILE_RESET_REPORT ' || jsonb_build_object(
    'outcome',current_setting('btx.reset_outcome',true),
    'username',:'target_username','uid',:'target_uid'::BIGINT,
    'mobile_present',EXISTS(SELECT 1 FROM public.user_contact_profiles WHERE telegram_user_id=:'target_uid'::BIGINT AND NULLIF(trim(mobile_number),'') IS NOT NULL),
    'verification_present',EXISTS(SELECT 1 FROM public.v110_mobile_verifications WHERE telegram_user_id=:'target_uid'::BIGINT),
    'pilot_verification_present',EXISTS(SELECT 1 FROM public.btx_crm_verification_tests WHERE telegram_user_id=:'target_uid'::BIGINT),
    'reset_audit_present',EXISTS(SELECT 1 FROM public.btx_crm_audit WHERE telegram_user_id=:'target_uid'::BIGINT AND action='test_mobile_clear_20260922_user_request_v1')
)::TEXT;
