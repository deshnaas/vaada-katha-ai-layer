"""Service-level tests with MOCK embeddings (deterministic synthetic speakers). They prove gating, storage, throttling and
quality handling, NOT biometric accuracy. Real-model checks live in test_vauth_real_model.py."""
import logging
import os

import numpy as np
import pytest
from cryptography.fernet import Fernet

from vauth_synth import (Clock, FakeAntiSpoof, FakeDirectory, FakeEmbedder, RaisingEmbedder, enrollment_set, speaker_wav,
                         speech, wav_bytes)
from voice_auth import audio as A
from voice_auth import calibration as C
from voice_auth.config import VoiceAuthConfig
from voice_auth.service import (INCONCLUSIVE, REJECTED, UNAVAILABLE, VERIFIED, NullAntiSpoof, VoiceAuthService)
from voice_auth.store import StoreCorrupt, StoreUnavailable, Voiceprint, VoiceprintStore

THRESHOLD = 0.9          # valid ONLY for the mock embedder above (genuine >= 0.997, impostor <= 0.70)


def make(tmp_path, *, directory=None, embedder=None, detector=None, key=True, **cfg):
    clock = Clock()
    k = Fernet.generate_key().decode()
    base = dict(mode="enforce", threshold=THRESHOLD, key=k if key else None, voiceprint_dir=str(tmp_path / "vp"))
    base.update(cfg)
    config = VoiceAuthConfig(**base)
    store = VoiceprintStore(config.voiceprint_dir, config.key) if key else None
    svc = VoiceAuthService(config, embedder or FakeEmbedder(), store, directory or FakeDirectory(), detector, clock=clock)
    return svc, clock


def enrolled(tmp_path, account="1", speaker=1, **kw):
    svc, clock = make(tmp_path, **kw)
    r = svc.enroll(account, enrollment_set(speaker))
    assert r.status == "enrolled", r
    return svc, clock


# ====================================================================== 1. enrollment with valid audio
def test_enrollment_with_valid_audio(tmp_path):
    svc, _ = make(tmp_path)
    r = svc.enroll("1", enrollment_set(1, n=4))
    assert (r.status, r.sample_count) == ("enrolled", 4)
    assert svc.profile_status("1")["enrolled"] is True and svc.profile_status("1")["sample_count"] == 4


# ====================================================================== 2. insufficient audio
def test_too_few_samples_is_rejected(tmp_path):
    svc, _ = make(tmp_path)
    assert svc.enroll("1", enrollment_set(1, n=2)).reason == "insufficient_samples"
    assert svc.enroll("1", []).reason == "insufficient_samples"
    assert svc.profile_status("1")["enrolled"] is False


def test_too_many_samples_is_rejected(tmp_path):
    svc, _ = make(tmp_path, max_enroll_samples=4)
    assert svc.enroll("1", enrollment_set(1, n=5)).reason == "too_many_samples"


@pytest.mark.parametrize("make_bad,reason", [
    (lambda: speaker_wav(1, 9, seconds=1.0), "too_short"),
    (lambda: wav_bytes(np.zeros(16000 * 4, dtype=np.float32)), "too_quiet"),
    (lambda: wav_bytes(speech(1, 9, amp=0.00005, noise=0.00001)), "too_quiet"),
    (lambda: wav_bytes(np.clip(speech(1, 9) * 8, -1, 1)), "clipped"),
    (lambda: wav_bytes(np.random.default_rng(0).normal(0, 0.1, 16000 * 4).astype(np.float32) * 0 + 0.05), "insufficient_speech"),
    (lambda: speaker_wav(1, 9, seconds=40.0), "too_long"),
])
def test_unsuitable_enrollment_audio_is_rejected_and_nothing_is_stored(tmp_path, make_bad, reason):
    svc, _ = make(tmp_path)
    r = svc.enroll("1", enrollment_set(1, n=2) + [make_bad()])
    assert r.status == "rejected" and r.reason.endswith(reason), r
    assert svc.profile_status("1")["enrolled"] is False


# ====================================================================== 3. corrupted / wrong-format audio
@pytest.mark.parametrize("bad", [
    b"", b"x" * 10, b"not a wav at all" * 50, b"RIFF" + b"\x00" * 200,
    speaker_wav(1, 1)[:-3001],                                                  # truncated: header lies about length
])
def test_corrupted_enrollment_audio_is_rejected(tmp_path, bad):
    svc, _ = make(tmp_path)
    r = svc.enroll("1", enrollment_set(1, n=2) + [bad])
    assert r.status == "rejected" and r.reason.startswith("sample_3_")
    assert svc.profile_status("1")["enrolled"] is False


@pytest.mark.parametrize("kw", [dict(rate=8000), dict(channels=2), dict(width=1)])
def test_only_pcm16_mono_16k_wav_is_accepted(tmp_path, kw):
    svc, _ = make(tmp_path)
    x = speech(1, 7)
    bad = wav_bytes(np.tile(x[:, None], (1, 2)).ravel() if kw.get("channels") == 2 else x, **kw)
    r = svc.enroll("1", enrollment_set(1, n=2) + [bad])
    assert r.reason == "sample_3_unsupported_format"


def test_samples_from_different_speakers_are_not_accepted_as_one_person(tmp_path):
    svc, _ = make(tmp_path)
    mixed = [speaker_wav(1, 0), speaker_wav(1, 1), speaker_wav(3, 2)]
    assert svc.enroll("1", mixed).reason == "inconsistent_samples"
    assert svc.profile_status("1")["enrolled"] is False


# ====================================================================== 4. unknown customer / identity directory
def test_enrollment_for_an_unknown_customer_is_rejected(tmp_path):
    svc, _ = make(tmp_path, directory=FakeDirectory(known=("1",)))
    r = svc.enroll("99", enrollment_set(1))
    assert (r.status, r.reason) == ("rejected", "unknown_account")


def test_enrollment_fails_closed_when_the_identity_directory_cannot_answer(tmp_path):
    svc, _ = make(tmp_path, directory=FakeDirectory(unavailable=True))
    assert svc.enroll("1", enrollment_set(1)).status == "unavailable"


def test_enrollment_requires_an_identity_directory(tmp_path):
    config = VoiceAuthConfig(mode="enforce", threshold=THRESHOLD, key=Fernet.generate_key().decode(),
                             voiceprint_dir=str(tmp_path / "vp"))
    svc = VoiceAuthService(config, FakeEmbedder(), VoiceprintStore(config.voiceprint_dir, config.key), None)
    assert svc.enroll("1", enrollment_set(1)).reason == "identity_directory_not_configured"


@pytest.mark.parametrize("bad_id", ["", "a b", "../x", "x" * 65, "é"])
def test_invalid_account_ids_are_rejected(tmp_path, bad_id):
    svc, _ = make(tmp_path)
    assert svc.enroll(bad_id, enrollment_set(1)).reason == "invalid_account_id"
    assert svc.verify(bad_id, "s1", speaker_wav(1, 5)).status == REJECTED


# ====================================================================== 5/6. matching and non-matching speaker
def test_registered_speaker_is_verified_with_a_token_bound_to_the_session(tmp_path):
    svc, _ = enrolled(tmp_path)
    r = svc.verify("1", "sess1", speaker_wav(1, 21))
    assert r.status == VERIFIED and r.token and r.expires_in_s == 300
    assert svc.authorize(r.token, "sess1") == "1"
    assert svc.authorize(r.token, "other-session") is None                      # bound to the session


def test_non_matching_speaker_is_rejected_without_a_token(tmp_path):
    svc, _ = enrolled(tmp_path)
    r = svc.verify("1", "sess1", speaker_wav(3, 21))
    assert (r.status, r.reason, r.token) == (REJECTED, "no_match", None)


def test_result_objects_never_carry_scores_or_embeddings(tmp_path):
    svc, _ = enrolled(tmp_path)
    for r in (svc.verify("1", "s", speaker_wav(1, 22)), svc.verify("1", "s", speaker_wav(3, 22))):
        assert set(vars(r)) == {"status", "reason", "token", "expires_in_s", "retry_after_s"}
    assert set(svc.profile_status("1")) <= {"enrolled", "readable", "enrolled_at", "sample_count", "model_id"}


def test_speaking_the_account_name_is_not_authentication(tmp_path):
    """The service only ever sees audio: a transcript naming the account has no way to authenticate anyone."""
    svc, _ = enrolled(tmp_path)
    assert svc.verify("1", "s", speaker_wav(2, 23)).status == REJECTED


# ====================================================================== 7. poor quality / insufficient speech
@pytest.mark.parametrize("wav,reason", [
    (lambda: speaker_wav(1, 30, seconds=0.5), "too_short"),
    (lambda: wav_bytes(np.zeros(16000 * 3, dtype=np.float32)), "too_quiet"),
    (lambda: wav_bytes(np.clip(speech(1, 30) * 8, -1, 1)), "clipped"),
    (lambda: b"garbage" * 40, "not_a_wav"),
    (lambda: speaker_wav(1, 30, seconds=40.0), "too_long"),
])
def test_poor_audio_is_inconclusive_and_never_a_match_or_a_failure(tmp_path, wav, reason):
    svc, _ = enrolled(tmp_path)
    for _ in range(5):                                                          # would lock the account if it counted
        r = svc.verify("1", "s", wav())
        assert (r.status, r.reason, r.token) == (INCONCLUSIVE, reason, None)
    assert svc.verify("1", "s", speaker_wav(1, 31)).status == VERIFIED          # not locked out


# ====================================================================== 8. missing profile / not configured (fail closed)
def test_missing_enrollment_cannot_authenticate(tmp_path):
    svc, _ = make(tmp_path)
    r = svc.verify("1", "s", speaker_wav(1, 40))
    assert (r.status, r.reason, r.token) == (UNAVAILABLE, "not_enrolled", None)


def test_verification_is_unavailable_without_a_configured_threshold(tmp_path):
    svc, _ = make(tmp_path, threshold=None)
    assert svc.verify("1", "s", speaker_wav(1, 41)).reason == "threshold_not_configured"
    assert svc.enroll("1", enrollment_set(1)).reason == "threshold_not_configured"      # there is no silent default


def test_no_key_means_no_storage_and_no_verification(tmp_path):
    svc, _ = make(tmp_path, key=False)
    assert svc.verify("1", "s", speaker_wav(1, 42)).reason == "voiceprint_store_not_configured"
    assert svc.enroll("1", enrollment_set(1)).reason == "voiceprint_store_not_configured"
    with pytest.raises(StoreUnavailable):
        VoiceprintStore(str(tmp_path / "x"), None)
    with pytest.raises(StoreUnavailable):
        VoiceprintStore(str(tmp_path / "x"), "not-a-fernet-key")


def test_model_failure_is_unavailable_not_a_verdict(tmp_path):
    svc, _ = enrolled(tmp_path)
    svc._embedder = RaisingEmbedder()
    r = svc.verify("1", "s", speaker_wav(1, 43))
    assert r.status == UNAVAILABLE and r.token is None


def test_profile_from_a_different_model_requires_reenrollment(tmp_path):
    svc, _ = enrolled(tmp_path)
    e = FakeEmbedder()
    e.model_id = "fake:other"
    svc._embedder = e
    assert svc.verify("1", "s", speaker_wav(1, 44)).reason == "profile_model_mismatch"


# ====================================================================== 9. expiry and re-authentication
def test_authentication_expires(tmp_path):
    svc, clock = enrolled(tmp_path, session_ttl_s=60)
    tok = svc.verify("1", "s", speaker_wav(1, 50)).token
    clock.now += 59
    assert svc.authorize(tok, "s") == "1" and svc.seconds_left(tok, "s") == 1
    clock.now += 2
    assert svc.authorize(tok, "s") is None
    assert svc.verify("1", "s", speaker_wav(1, 51)).status == VERIFIED          # re-authentication works


def test_logout_and_tokens_are_opaque(tmp_path):
    svc, _ = enrolled(tmp_path)
    tok = svc.verify("1", "s", speaker_wav(1, 52)).token
    assert len(tok) >= 40 and svc.authorize(tok + "x", "s") is None and svc.authorize("", "s") is None
    assert svc.authorize("authenticated=true", "s") is None
    assert svc.logout(tok) is True and svc.authorize(tok, "s") is None and svc.logout(tok) is False


def test_session_table_keeps_only_token_hashes(tmp_path):
    svc, _ = enrolled(tmp_path)
    tok = svc.verify("1", "s", speaker_wav(1, 53)).token
    assert tok not in svc._sessions and all(tok not in k for k in svc._sessions)


# ====================================================================== 10. retry limit / lockout
def test_repeated_failures_lock_the_account_even_for_the_real_speaker(tmp_path):
    svc, clock = enrolled(tmp_path, max_failures=3, lockout_s=120, failure_window_s=300)
    assert svc.verify("1", "s", speaker_wav(3, 60)).reason == "no_match"
    assert svc.verify("1", "s", speaker_wav(3, 61)).reason == "no_match"
    third = svc.verify("1", "s", speaker_wav(3, 62))
    assert (third.status, third.reason, third.retry_after_s) == (REJECTED, "locked", 120)
    blocked = svc.verify("1", "s", speaker_wav(1, 63))                          # the genuine speaker is blocked too
    assert (blocked.status, blocked.reason, blocked.token) == (REJECTED, "locked", None) and blocked.retry_after_s > 0
    clock.now += 121
    assert svc.verify("1", "s", speaker_wav(1, 64)).status == VERIFIED          # safe recovery: wait, then retry


def test_old_failures_age_out_of_the_window(tmp_path):
    svc, clock = enrolled(tmp_path, max_failures=3, failure_window_s=100)
    svc.verify("1", "s", speaker_wav(3, 70)); svc.verify("1", "s", speaker_wav(3, 71))
    clock.now += 101
    assert svc.verify("1", "s", speaker_wav(3, 72)).reason == "no_match"        # not locked: earlier failures expired


def test_success_resets_the_failure_counter(tmp_path):
    svc, _ = enrolled(tmp_path, max_failures=3)
    svc.verify("1", "s", speaker_wav(3, 80)); svc.verify("1", "s", speaker_wav(3, 81))
    assert svc.verify("1", "s", speaker_wav(1, 82)).status == VERIFIED
    assert svc.verify("1", "s", speaker_wav(3, 83)).reason == "no_match"


def test_lockout_is_per_account(tmp_path):
    svc, _ = make(tmp_path, max_failures=2)
    svc.enroll("1", enrollment_set(1)); svc.enroll("2", enrollment_set(2))
    svc.verify("1", "s", speaker_wav(3, 90)); svc.verify("1", "s", speaker_wav(3, 91))
    assert svc.verify("1", "s", speaker_wav(1, 92)).reason == "locked"
    assert svc.verify("2", "s", speaker_wav(2, 92)).status == VERIFIED


# ====================================================================== 14. revocation and re-enrollment
def test_revoked_enrollment_cannot_authenticate_and_ends_sessions(tmp_path):
    svc, _ = enrolled(tmp_path)
    tok = svc.verify("1", "s", speaker_wav(1, 100)).token
    assert svc.revoke("1") is True
    assert svc.authorize(tok, "s") is None                                      # existing login is ended too
    r = svc.verify("1", "s", speaker_wav(1, 101))
    assert (r.status, r.reason, r.token) == (UNAVAILABLE, "not_enrolled", None)
    assert svc.revoke("1") is False                                             # idempotent


def test_reenrollment_needs_the_explicit_replace_flag_and_ends_old_sessions(tmp_path):
    svc, _ = enrolled(tmp_path, speaker=1)
    tok = svc.verify("1", "s", speaker_wav(1, 110)).token
    assert svc.enroll("1", enrollment_set(2)).reason == "already_enrolled"
    assert svc.verify("1", "s", speaker_wav(1, 111)).status == VERIFIED          # old profile untouched by the refusal
    assert svc.enroll("1", enrollment_set(2), replace=True).status == "enrolled"
    assert svc.authorize(tok, "s") is None
    assert svc.verify("1", "s", speaker_wav(1, 112)).status == REJECTED          # the old voice no longer works
    assert svc.verify("1", "s", speaker_wav(2, 112)).status == VERIFIED


# ====================================================================== 15. anti-spoofing: separate, limited, fails closed
def test_antispoof_required_without_a_detector_fails_closed(tmp_path):
    svc, _ = enrolled(tmp_path, antispoof="required")
    r = svc.verify("1", "s", speaker_wav(1, 120))
    assert (r.status, r.reason, r.token) == (UNAVAILABLE, "antispoof_unavailable", None)
    assert isinstance(svc._antispoof, NullAntiSpoof) and NullAntiSpoof().assess(None) == "unavailable"


def test_antispoof_off_is_the_documented_no_defence_default(tmp_path):
    svc, _ = enrolled(tmp_path)
    assert svc.config.antispoof == "off" and svc.verify("1", "s", speaker_wav(1, 121)).status == VERIFIED


def test_a_detector_verdict_is_independent_of_the_speaker_match(tmp_path):
    spoof, real, unk = FakeAntiSpoof("spoof"), FakeAntiSpoof("bona_fide"), FakeAntiSpoof("unavailable")
    for det, expect in ((spoof, "spoof_suspected"), (real, None), (unk, "antispoof_unavailable")):
        svc, _ = make(tmp_path / det.verdict, detector=det, antispoof="required")
        assert svc.enroll("1", enrollment_set(1)).status == "enrolled"
        r = svc.verify("1", "s", speaker_wav(1, 122))                           # the voice itself matches in every case
        if expect:
            assert r.status != VERIFIED and r.reason == expect and r.token is None
        else:
            assert r.status == VERIFIED
        assert det.calls == 1


def test_a_spoof_verdict_counts_as_a_failed_attempt(tmp_path):
    det = FakeAntiSpoof("spoof")
    svc, _ = make(tmp_path, detector=det, antispoof="required", max_failures=2)
    svc.enroll("1", enrollment_set(1))
    assert svc.verify("1", "s", speaker_wav(1, 123)).reason == "spoof_suspected"
    assert svc.verify("1", "s", speaker_wav(1, 124)).reason == "locked"


# ====================================================================== storage protection
def test_voiceprint_is_encrypted_at_rest_and_holds_no_audio(tmp_path):
    svc, _ = enrolled(tmp_path, account="1")
    files = sorted(os.listdir(tmp_path / "vp"))
    assert files and all(f.endswith(".vp") for f in files)                       # no .wav, no temp files left behind
    blob = (tmp_path / "vp" / files[0]).read_bytes()
    for needle in (b"embeddings", b'"account_id"', b"enrolled_at", b"fake:bands", b"RIFF"):
        assert needle not in blob
    assert "1" not in files[0].replace(".vp", "") or len(files[0]) > 20          # file name is an HMAC, not the account id
    assert len(blob) > 200


def test_wrong_key_or_tampering_fails_closed(tmp_path):
    svc, _ = enrolled(tmp_path)
    path = next((tmp_path / "vp").glob("*.vp"))
    good = path.read_bytes()
    path.write_bytes(good[:-5] + bytes([good[-5] ^ 1]) + good[-4:])              # flip one bit
    r = svc.verify("1", "s", speaker_wav(1, 130))
    assert (r.status, r.reason, r.token) == (UNAVAILABLE, "profile_unreadable", None)
    assert svc.profile_status("1") == {"enrolled": True, "readable": False}
    path.write_bytes(good)
    other = VoiceprintStore(str(tmp_path / "vp"), Fernet.generate_key().decode())    # a different key
    assert other.load("1") is None                       # file names are HMAC(key, id): another key cannot even locate it


def test_store_does_not_expose_embeddings_in_repr_or_config_secrets(tmp_path):
    svc, _ = enrolled(tmp_path)
    vp = svc._store.load("1")
    assert "array" not in repr(vp) and "embeddings" not in repr(vp)
    cfg = svc.config
    assert cfg.key not in repr(cfg) and "admin" not in repr(cfg).lower().replace("admin_token=none", "")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not meaningful on Windows")
def test_voiceprint_files_are_owner_only(tmp_path):
    enrolled(tmp_path)
    path = next((tmp_path / "vp").glob("*.vp"))
    assert (path.stat().st_mode & 0o077) == 0


def test_atomic_replace_leaves_no_partial_files(tmp_path):
    svc, _ = enrolled(tmp_path)
    svc.enroll("1", enrollment_set(2), replace=True)
    assert [f for f in os.listdir(tmp_path / "vp") if not f.endswith(".vp") or f.startswith(".tmp")] == []
    assert svc._store.count() == 1


def test_gitignore_blocks_biometric_data():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[3] / ".gitignore").read_text()
    for pattern in (".voiceprints/", "*.vp", ".speaker_model/"):
        assert pattern in text


# ====================================================================== logging: no biometric or credential data
def test_logs_contain_no_tokens_scores_embeddings_or_account_ids(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    svc, _ = make(tmp_path, max_failures=2, directory=FakeDirectory(known=("12345",)))
    assert svc.enroll("12345", enrollment_set(1)).status == "enrolled"
    ok = svc.verify("12345", "sess-abc", speaker_wav(1, 140))
    assert ok.status == VERIFIED and ok.token
    svc.verify("12345", "sess-abc", speaker_wav(3, 141)); svc.verify("12345", "sess-abc", speaker_wav(3, 142))
    svc.revoke("12345")
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert ok.token not in blob and "12345" not in blob and "sess-abc" not in blob
    for word in ("score", "cosine", "embedding", "0.99", "RIFF"):
        assert word not in blob


# ====================================================================== configuration
def test_config_validation_and_env_parsing(tmp_path):
    with pytest.raises(ValueError):
        VoiceAuthConfig(mode="maybe")
    with pytest.raises(ValueError):
        VoiceAuthConfig(threshold=1.5)
    with pytest.raises(ValueError):
        VoiceAuthConfig(max_failures=0)
    cfg = VoiceAuthConfig.from_env({"VAADA_VOICE_AUTH_MODE": "ENFORCE", "VAADA_SPEAKER_THRESHOLD": "0.42",
                                    "VAADA_AUTH_SESSION_TTL_S": "90", "VAADA_VOICEPRINT_KEY": "k"})
    assert (cfg.mode, cfg.threshold, cfg.session_ttl_s) == ("enforce", 0.42, 90.0)
    assert VoiceAuthConfig.from_env({}).mode == "off" and VoiceAuthConfig.from_env({}).threshold is None
    with pytest.raises(ValueError):
        VoiceAuthConfig.from_env({"VAADA_SPEAKER_THRESHOLD": "high"})


# ====================================================================== calibration helper (threshold methodology)
def test_calibration_helpers():
    genuine = [0.8, 0.85, 0.9, 0.95, 0.7]
    impostor = [0.1, 0.2, 0.3, 0.75, 0.4]
    op = C.threshold_for_far(genuine, impostor, target_far=0.0)
    assert op.far == 0.0 and op.threshold > 0.75 and 0.0 < op.frr < 1.0
    eer = C.equal_error_rate(genuine, impostor)
    assert abs(eer.far - eer.frr) <= 0.2
    with pytest.raises(ValueError):
        C.rates([], [0.1], 0.5)


def test_audio_decoder_rejects_oversize():
    with pytest.raises(A.AudioRejected) as e:
        A.decode_wav(speaker_wav(1, 1), max_bytes=1000)
    assert e.value.reason == "too_large"
