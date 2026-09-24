"""Deterministic unit coverage for the Auryel Control MFA primitives."""

import admin_control


def test_rfc_totp_and_replay_guard():
    secret = "JBSWY3DPEHPK3PXP"
    now = 1_111_111_111
    step = now // admin_control.MFA_STEP_SECONDS
    code = admin_control._totp_value(secret, step)
    assert admin_control._verify_totp(secret, code, now=now) == step
    assert admin_control._verify_totp(secret, code, now=now, last_step=step) is None


def test_secret_is_encrypted_and_recovery_values_are_hashed():
    secret = "JBSWY3DPEHPK3PXP"
    fernet = admin_control._mfa_fernet("test-secret")
    ciphertext = fernet.encrypt(secret.encode()).decode()
    assert ciphertext != secret
    assert fernet.decrypt(ciphertext.encode()).decode() == secret
    codes, hashes = admin_control._recovery_codes("test-secret")
    assert len(codes) == 10
    assert len(set(codes)) == 10
    assert all(len(value) == 64 for value in hashes)
    assert all(code not in hashes for code in codes)


if __name__ == "__main__":
    test_rfc_totp_and_replay_guard()
    test_secret_is_encrypted_and_recovery_values_are_hashed()
    print("admin MFA tests: PASS")
